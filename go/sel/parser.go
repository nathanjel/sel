// Precedence-climbing parser for SEL. See spec/grammar.md and spec/SPEC.md §5.

package sel

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/vocab"
)

// Binding power levels (spec/SPEC.md §5). Higher binds tighter.
const (
	bpSeq      = 1 // ;
	bpList     = 2 // ,
	bpAssign   = 3 // = += -= *= /= %= &= (right associative)
	bpOr       = 4
	bpXor      = 5
	bpAnd      = 6
	bpNot      = 7 // prefix
	bpCompare  = 8 // non-associative
	bpCoalesce = 9 // ?? ??? (right associative)
	bpBOr      = 10
	bpBXor     = 11
	bpBAnd     = 12
	bpConcat   = 13 // &
	bpAdd      = 14 // + -
	bpMul      = 15 // * / %
	bpNeg      = 16 // prefix
)

type infixEntry struct {
	bp    int
	assoc byte // 'L', 'R', 'N'
}

var infixOps = map[string]infixEntry{
	"??":  {bpCoalesce, 'R'},
	"???": {bpCoalesce, 'R'},
	"&":   {bpConcat, 'L'},
	"+":   {bpAdd, 'L'},
	"-":   {bpAdd, 'L'},
	"*":   {bpMul, 'L'},
	"/":   {bpMul, 'L'},
	"%":   {bpMul, 'L'},
	"=":   {bpAssign, 'R'},
	"+=":  {bpAssign, 'R'},
	"-=":  {bpAssign, 'R'},
	"*=":  {bpAssign, 'R'},
	"/=":  {bpAssign, 'R'},
	"%=":  {bpAssign, 'R'},
	"&=":  {bpAssign, 'R'},
	"==":  {bpCompare, 'N'},
	"!=":  {bpCompare, 'N'},
	"<":   {bpCompare, 'N'},
	"<=":  {bpCompare, 'N'},
	">":   {bpCompare, 'N'},
	">=":  {bpCompare, 'N'},
	"$==": {bpCompare, 'N'},
	"$!=": {bpCompare, 'N'},
	"$<":  {bpCompare, 'N'},
	"$<=": {bpCompare, 'N'},
	"$>":  {bpCompare, 'N'},
	"$>=": {bpCompare, 'N'},
}

var infixWords = map[string]infixEntry{
	"OR":   {bpOr, 'L'},
	"XOR":  {bpXor, 'L'},
	"AND":  {bpAnd, 'L'},
	"BOR":  {bpBOr, 'L'},
	"BXOR": {bpBXor, 'L'},
	"BAND": {bpBAnd, 'L'},
	"EQL":  {bpCompare, 'N'},
	"IN":   {bpCompare, 'N'},
}

var assignOps = map[string]struct{}{
	"=": {}, "+=": {}, "-=": {}, "*=": {}, "/=": {}, "%=": {}, "&=": {},
}

func prepareRecordShape(name string, args []*Node) *RecordShape {
	if name != "RECORD" || len(args) == 0 || len(args)%2 != 0 {
		return nil
	}
	keys := make([]string, 0, len(args)/2)
	for i := 0; i < len(args); i += 2 {
		if args[i].T != NodeText {
			return nil
		}
		keys = append(keys, args[i].S)
	}
	return uniqueRecordShape(keys)
}

type parser struct {
	toks  []token
	i     int
	depth int
}

func newParser(tokens []token) *parser {
	return &parser{
		toks:  tokens,
		i:     0,
		depth: 0,
	}
}

func (p *parser) infixEntry(t token) (int, byte, bool) {
	if t.Type == tokenOp {
		if e, ok := infixOps[t.Value]; ok {
			return e.bp, e.assoc, true
		}
	}
	if t.Type == tokenIdent {
		if e, ok := infixWords[t.Value]; ok {
			return e.bp, e.assoc, true
		}
	}
	return 0, 0, false
}

func (p *parser) peek() token {
	if p.i < len(p.toks) {
		return p.toks[p.i]
	}
	return token{Type: tokenEOF}
}

func (p *parser) peekAhead(offset int) token {
	idx := p.i + offset
	if idx < len(p.toks) {
		return p.toks[idx]
	}
	return token{Type: tokenEOF}
}

func (p *parser) next() token {
	t := p.peek()
	if p.i < len(p.toks) {
		p.i++
	}
	return t
}

func (p *parser) atOp(v string) bool {
	t := p.peek()
	return t.Type == tokenOp && t.Value == v
}

func (p *parser) atEOF() bool {
	return p.peek().Type == tokenEOF
}

func (p *parser) expectOp(v string) token {
	if !p.atOp(v) {
		t := p.peek()
		fail("E_SYNTAX", "expected "+quoteText(v)+", got "+describe(t), t.Pos)
	}
	return p.next()
}

func (p *parser) enter(pos Pos) {
	p.depth++
	if p.depth > maxDepth {
		fail("E_DEPTH", "expression nested too deeply", pos)
	}
}

func (p *parser) leave() {
	p.depth--
}

func (p *parser) ParseProgram() *Node {
	node := p.parseSequence()
	if !p.atEOF() {
		t := p.peek()
		fail("E_SYNTAX", fmt.Sprintf("unexpected %s", describe(t)), t.Pos)
	}
	return node
}

func (p *parser) parseSequence() *Node {
	start := p.peek()
	p.enter(start.Pos)
	defer p.leave()

	items := []*Node{p.parseList()}
	for p.atOp(";") {
		p.next()
		if p.atEOF() || p.atOp(")") || p.atOp("]") {
			break
		}
		items = append(items, p.parseList())
	}
	if len(items) == 1 {
		return items[0]
	}
	n := NewNode(NodeSeq, items[0].Pos)
	n.Items = items
	return n
}

func (p *parser) parseList() *Node {
	items := []*Node{p.parseTerm(bpAssign)}
	for p.atOp(",") {
		p.next()
		items = append(items, p.parseTerm(bpAssign))
	}
	if len(items) == 1 {
		return items[0]
	}
	n := NewNode(NodeList, items[0].Pos)
	n.Items = items
	return n
}

func (p *parser) parseTerm(minBp int) *Node {
	left := p.parsePrefix(minBp)

	for {
		t := p.peek()
		bp, assoc, ok := p.infixEntry(t)
		if !ok || bp < minBp {
			return left
		}

		p.next()

		if _, isAssign := assignOps[t.Value]; isAssign {
			checkTarget(left, t)
			p.enter(t.Pos)
			value := p.parseTerm(bp)
			p.leave()
			n := NewNode(NodeAssign, left.Pos)
			n.S = t.Value
			n.L = left
			n.R = value
			left = n
			continue
		}

		if assoc == 'R' {
			// Counted like an assignment (SPEC §6.4): `??` and `???` are the
			// other right-associative operators.
			p.enter(t.Pos)
			right := p.parseTerm(bp)
			p.leave()
			n := NewNode(NodeBin, t.Pos)
			n.S = t.Value
			n.L = left
			n.R = right
			left = n
			continue
		}

		if assoc == 'N' {
			right := p.parseTerm(bp + 1)
			after := p.peek()
			_, afterAssoc, afterOk := p.infixEntry(after)
			if afterOk && afterAssoc == 'N' {
				fail("E_SYNTAX",
					fmt.Sprintf("comparison operators do not chain — parenthesise, as in (a %s b) AND (b %s c)",
						t.Value, after.Value),
					after.Pos)
			}
			n := NewNode(NodeBin, t.Pos)
			n.S = t.Value
			n.L = left
			n.R = right
			left = n
			continue
		}

		right := p.parseTerm(bp + 1)
		n := NewNode(NodeBin, t.Pos)
		n.S = t.Value
		n.L = left
		n.R = right
		left = n
	}
}

func (p *parser) parsePrefix(minBp int) *Node {
	t := p.peek()

	if t.Type == tokenIdent && t.Value == "NOT" && minBp <= bpNot {
		p.next()
		p.enter(t.Pos)
		x := p.parseTerm(bpNot)
		p.leave()
		n := NewNode(NodeUn, t.Pos)
		n.S = "NOT"
		n.L = x
		return n
	}

	if t.Type == tokenOp && t.Value == "-" && minBp <= bpNeg {
		p.next()
		p.enter(t.Pos)
		x := p.parseTerm(bpNeg)
		p.leave()
		n := NewNode(NodeUn, t.Pos)
		n.S = "NEG"
		n.L = x
		return n
	}

	return p.parsePostfix()
}

func (p *parser) parsePostfix() *Node {
	node := p.parsePrimary()
	for p.atOp("[") || p.atOp(".>") {
		if p.atOp("[") {
			br := p.next()
			p.enter(br.Pos)
			idx := p.parseSequence()
			p.expectOp("]")
			p.leave()
			n := NewNode(NodeIndex, br.Pos)
			n.L = node
			n.R = idx
			node = n
		} else {
			p.next() // consume '.>'
			node = p.parsePipeStep(node)
		}
	}
	return node
}

func (p *parser) parsePipeStep(left *Node) *Node {
	t := p.peek()
	if t.Type != tokenIdent || t.Value == "TRUE" || t.Value == "FALSE" || t.Value == "NULL" {
		fail("E_SYNTAX", "right-hand side of .> must be a function call or function name", t.Pos)
	}
	nameTok := p.next()
	var args []*Node
	if p.atOp("(") {
		p.next()
		if p.atOp(")") {
			p.next()
		} else {
			inner := p.parseSequence()
			p.expectOp(")")
			if inner.T == NodeList && !inner.Grouped {
				args = inner.Items
			} else {
				args = []*Node{inner}
			}
		}
	}

	spec := lookup(nameTok.Value)
	if spec == nil {
		fail("E_UNKNOWN_FUNC", fmt.Sprintf("unknown function %s", nameTok.Value), nameTok.Pos)
	}

	hasPlaceholder := false
	if !spec.Binds && len(args) >= spec.Min {
		for i, arg := range args {
			if arg.T == NodeVar && arg.S == "_" && !arg.Grouped {
				args[i] = left
				hasPlaceholder = true
			}
		}
	}

	if !hasPlaceholder {
		args = append([]*Node{left}, args...)
	}

	return finishCall(nameTok, spec, args)
}

func (p *parser) parsePrimary() *Node {
	t := p.peek()
	p.enter(t.Pos)
	defer p.leave()

	if t.Type == tokenNum {
		p.next()
		parsed := decimal.Parse(t.Value, t.Pos, fail)
		n := NewNode(NodeNum, t.Pos)
		n.S = decimal.Format(parsed)
		n.dec = parsed
		return n
	}

	if t.Type == tokenText {
		p.next()
		n := NewNode(NodeText, t.Pos)
		n.S = t.Value
		return n
	}

	if t.Type == tokenIdent {
		if t.Value == "TRUE" || t.Value == "FALSE" {
			p.next()
			n := NewNode(NodeBool, t.Pos)
			n.B = (t.Value == "TRUE")
			return n
		}
		if t.Value == "NULL" {
			p.next()
			return NewNode(NodeNull, t.Pos)
		}
		after := p.peekAhead(1)
		if after.Type == tokenOp && after.Value == "(" {
			return p.parseCall()
		}
		if _, isRes := reserved[t.Value]; isRes {
			fail("E_RESERVED", fmt.Sprintf("%s is a reserved word and cannot be a variable", t.Value), t.Pos)
		}
		p.next()
		n := NewNode(NodeVar, t.Pos)
		n.S = t.Value
		return n
	}

	if t.Type == tokenOp && t.Value == "(" {
		p.next()
		if p.atOp(")") {
			fail("E_SYNTAX", "empty parentheses", t.Pos)
		}
		inner := p.parseSequence()
		p.expectOp(")")
		inner.Grouped = true
		return inner
	}

	fail("E_SYNTAX", fmt.Sprintf("unexpected %s", describe(t)), t.Pos)
	return nil
}

func (p *parser) parseCall() *Node {
	nameTok := p.next()
	p.expectOp("(")
	var args []*Node
	if p.atOp(")") {
		p.next()
	} else {
		inner := p.parseSequence()
		p.expectOp(")")
		if inner.T == NodeList && !inner.Grouped {
			args = inner.Items
		} else {
			args = []*Node{inner}
		}
	}

	spec := lookup(nameTok.Value)
	if spec == nil {
		fail("E_UNKNOWN_FUNC", fmt.Sprintf("unknown function %s", nameTok.Value), nameTok.Pos)
	}
	return finishCall(nameTok, spec, args)
}

func finishCall(nameTok token, spec *Spec, args []*Node) *Node {
	count := len(args)
	if count < spec.Min || (spec.Max >= 0 && count > spec.Max) {
		fail("E_ARITY", fmt.Sprintf("%s takes %s, got %d", spec.Name, arityText(spec), count), nameTok.Pos)
	}
	if spec.ArityError != nil {
		problem := spec.ArityError(count)
		if problem != "" {
			fail("E_ARITY", problem, nameTok.Pos)
		}
	}
	// A literal pattern is checked when the program is compiled, not when the
	// call runs, so a bad one in a branch that never executes is still refused
	// (SPEC §7.8). A computed pattern is checked when it is used.
	switch spec.Name {
	case "RMATCH", "RFIND", "RGROUPS", "RREPLACE":
		if len(args) > 0 && args[0].T == NodeText {
			// The ambiguity analysis folds case under `i`, so a literal flag is
			// part of what is checked. A non-ASCII pattern under `i` is E_BAD_ARG
			// when the call runs (compileRegex), not a syntax question here.
			flagAt, _ := vocab.RegexFlagsAt(spec.Name)
			ic := len(args) > flagAt && args[flagAt].T == NodeText && strings.Contains(args[flagAt].S, "i")
			if ic {
				for _, r := range args[0].S {
					if r > 0x7F {
						ic = false
						break
					}
				}
			}
			parseRegexIC(args[0].S, ic, args[0].Pos)
		}
	}
	n := NewNode(NodeCall, nameTok.Pos)
	n.S = spec.Name
	n.Spec = spec
	n.Items = args
	n.shape = prepareRecordShape(spec.Name, args)
	return n
}

func arityText(spec *Spec) string {
	if spec.Max < 0 {
		if spec.Min == 1 {
			return "at least 1 argument"
		}
		return fmt.Sprintf("at least %d arguments", spec.Min)
	}
	if spec.Min == spec.Max {
		if spec.Min == 1 {
			return "1 argument"
		}
		return fmt.Sprintf("%d arguments", spec.Min)
	}
	return fmt.Sprintf("%d to %d arguments", spec.Min, spec.Max)
}

func describe(t token) string {
	if t.Type == tokenEOF {
		return "end of input"
	}
	if t.Type == tokenText {
		return "a text literal"
	}
	if t.Type == tokenNum {
		return fmt.Sprintf("number %s", t.Value)
	}
	return quoteText(t.Value)
}

func checkTarget(node *Node, opTok token) {
	n := node
	for n.T == NodeIndex {
		n = n.L
	}
	if n.T != NodeVar || node.Grouped {
		fail("E_BAD_ASSIGN", fmt.Sprintf("cannot assign with %s to this expression", opTok.Value), node.Pos)
	}
}

func parse(source string) *Node {
	return newParser(tokenize(source)).ParseProgram()
}
