// Precedence-climbing parser for SEL. See spec/grammar.md and spec/SPEC.md §5.

package sel

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/decimal"
)

// Binding power levels (spec/SPEC.md §5). Higher binds tighter.
const (
	BPSeq      = 1  // ;
	BPList     = 2  // ,
	BPAssign   = 3  // = += -= *= /= %= &= (right associative)
	BPOr       = 4
	BPXor      = 5
	BPAnd      = 6
	BPNot      = 7  // prefix
	BPCompare  = 8  // non-associative
	BPCoalesce = 9  // ?? ??? (right associative)
	BPBOr      = 10
	BPBXor     = 11
	BPBAnd     = 12
	BPConcat   = 13 // &
	BPAdd      = 14 // + -
	BPMul      = 15 // * / %
	BPNeg      = 16 // prefix
)

type infixEntry struct {
	bp    int
	assoc byte // 'L', 'R', 'N'
}

var infixOps = map[string]infixEntry{
	"??":  {BPCoalesce, 'R'},
	"???": {BPCoalesce, 'R'},
	"&":   {BPConcat, 'L'},
	"+":   {BPAdd, 'L'},
	"-":   {BPAdd, 'L'},
	"*":   {BPMul, 'L'},
	"/":   {BPMul, 'L'},
	"%":   {BPMul, 'L'},
	"=":   {BPAssign, 'R'},
	"+=":  {BPAssign, 'R'},
	"-=":  {BPAssign, 'R'},
	"*=":  {BPAssign, 'R'},
	"/=":  {BPAssign, 'R'},
	"%=":  {BPAssign, 'R'},
	"&=":  {BPAssign, 'R'},
	"==":  {BPCompare, 'N'},
	"!=":  {BPCompare, 'N'},
	"<":   {BPCompare, 'N'},
	"<=":  {BPCompare, 'N'},
	">":   {BPCompare, 'N'},
	">=":  {BPCompare, 'N'},
	"$==": {BPCompare, 'N'},
	"$!=": {BPCompare, 'N'},
	"$<":  {BPCompare, 'N'},
	"$<=": {BPCompare, 'N'},
	"$>":  {BPCompare, 'N'},
	"$>=": {BPCompare, 'N'},
}

var infixWords = map[string]infixEntry{
	"OR":   {BPOr, 'L'},
	"XOR":  {BPXor, 'L'},
	"AND":  {BPAnd, 'L'},
	"BOR":  {BPBOr, 'L'},
	"BXOR": {BPBXor, 'L'},
	"BAND": {BPBAnd, 'L'},
	"EQL":  {BPCompare, 'N'},
	"IN":   {BPCompare, 'N'},
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
	return UniqueRecordShape(keys)
}

type Parser struct {
	toks  []Token
	i     int
	depth int
}

func NewParser(tokens []Token) *Parser {
	return &Parser{
		toks:  tokens,
		i:     0,
		depth: 0,
	}
}

func (p *Parser) infixEntry(t Token) (int, byte, bool) {
	if t.Type == TokenOp {
		if e, ok := infixOps[t.Value]; ok {
			return e.bp, e.assoc, true
		}
	}
	if t.Type == TokenIdent {
		if e, ok := infixWords[t.Value]; ok {
			return e.bp, e.assoc, true
		}
	}
	return 0, 0, false
}

func (p *Parser) peek() Token {
	if p.i < len(p.toks) {
		return p.toks[p.i]
	}
	return Token{Type: TokenEOF}
}

func (p *Parser) peekAhead(offset int) Token {
	idx := p.i + offset
	if idx < len(p.toks) {
		return p.toks[idx]
	}
	return Token{Type: TokenEOF}
}

func (p *Parser) next() Token {
	t := p.peek()
	if p.i < len(p.toks) {
		p.i++
	}
	return t
}

func (p *Parser) atOp(v string) bool {
	t := p.peek()
	return t.Type == TokenOp && t.Value == v
}

func (p *Parser) atEOF() bool {
	return p.peek().Type == TokenEOF
}

func (p *Parser) expectOp(v string) Token {
	if !p.atOp(v) {
		t := p.peek()
		fail("E_SYNTAX", fmt.Sprintf("expected %q, got %s", v, describe(t)), t.Pos)
	}
	return p.next()
}

func (p *Parser) enter(pos Pos) {
	p.depth++
	if p.depth > MAX_DEPTH {
		fail("E_DEPTH", "expression nested too deeply", pos)
	}
}

func (p *Parser) leave() {
	p.depth--
}

func (p *Parser) ParseProgram() *Node {
	node := p.parseSequence()
	if !p.atEOF() {
		t := p.peek()
		fail("E_SYNTAX", fmt.Sprintf("unexpected %s", describe(t)), t.Pos)
	}
	return node
}

func (p *Parser) parseSequence() *Node {
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

func (p *Parser) parseList() *Node {
	items := []*Node{p.parseTerm(BPAssign)}
	for p.atOp(",") {
		p.next()
		items = append(items, p.parseTerm(BPAssign))
	}
	if len(items) == 1 {
		return items[0]
	}
	n := NewNode(NodeList, items[0].Pos)
	n.Items = items
	return n
}

func (p *Parser) parseTerm(minBp int) *Node {
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
			right := p.parseTerm(bp)
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

func (p *Parser) parsePrefix(minBp int) *Node {
	t := p.peek()

	if t.Type == TokenIdent && t.Value == "NOT" && minBp <= BPNot {
		p.next()
		p.enter(t.Pos)
		x := p.parseTerm(BPNot)
		p.leave()
		n := NewNode(NodeUn, t.Pos)
		n.S = "NOT"
		n.L = x
		return n
	}

	if t.Type == TokenOp && t.Value == "-" && minBp <= BPNeg {
		p.next()
		p.enter(t.Pos)
		x := p.parseTerm(BPNeg)
		p.leave()
		n := NewNode(NodeUn, t.Pos)
		n.S = "NEG"
		n.L = x
		return n
	}

	return p.parsePostfix()
}

func (p *Parser) parsePostfix() *Node {
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

func (p *Parser) parsePipeStep(left *Node) *Node {
	t := p.peek()
	if t.Type != TokenIdent || t.Value == "TRUE" || t.Value == "FALSE" || t.Value == "NULL" {
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

	spec := Lookup(nameTok.Value)
	if spec == nil {
		fail("E_UNKNOWN_FUNC", fmt.Sprintf("unknown function %s", nameTok.Value), nameTok.Pos)
	}

	hasPlaceholder := false
	if !spec.Binds && len(args) >= spec.Min {
		for i, arg := range args {
			if arg.T == NodeVar && arg.S == "_" && !arg.Grouped {
				args[i] = left
				hasPlaceholder = true
				break
			}
		}
	}

	if !hasPlaceholder {
		args = append([]*Node{left}, args...)
	}

	return finishCall(nameTok, spec, args)
}

func (p *Parser) parsePrimary() *Node {
	t := p.peek()
	p.enter(t.Pos)
	defer p.leave()

	if t.Type == TokenNum {
		p.next()
		parsed := decimal.Parse(t.Value, t.Pos, fail)
		n := NewNode(NodeNum, t.Pos)
		n.S = decimal.Format(parsed)
		n.Dec = parsed
		return n
	}

	if t.Type == TokenText {
		p.next()
		n := NewNode(NodeText, t.Pos)
		n.S = t.Value
		return n
	}

	if t.Type == TokenIdent {
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
		if after.Type == TokenOp && after.Value == "(" {
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

	if t.Type == TokenOp && t.Value == "(" {
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

func (p *Parser) parseCall() *Node {
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

	spec := Lookup(nameTok.Value)
	if spec == nil {
		fail("E_UNKNOWN_FUNC", fmt.Sprintf("unknown function %s", nameTok.Value), nameTok.Pos)
	}
	return finishCall(nameTok, spec, args)
}

func finishCall(nameTok Token, spec *Spec, args []*Node) *Node {
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
	n := NewNode(NodeCall, nameTok.Pos)
	n.S = spec.Name
	n.Spec = spec
	n.Items = args
	n.Shape = prepareRecordShape(spec.Name, args)
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

func describe(t Token) string {
	if t.Type == TokenEOF {
		return "end of input"
	}
	if t.Type == TokenText {
		return "a text literal"
	}
	if t.Type == TokenNum {
		return fmt.Sprintf("number %s", t.Value)
	}
	return fmt.Sprintf("%q", t.Value)
}

func checkTarget(node *Node, opTok Token) {
	n := node
	for n.T == NodeIndex {
		n = n.L
	}
	if n.T != NodeVar || node.Grouped {
		fail("E_BAD_ASSIGN", fmt.Sprintf("cannot assign with %s to this expression", opTok.Value), node.Pos)
	}
}

func Parse(source string) *Node {
	return NewParser(Tokenize(source)).ParseProgram()
}
