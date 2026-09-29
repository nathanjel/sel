// The portable regex subset as a parser (SPEC §7.8): pattern text in, a tree out,
// every rejection decided here and identical on every host. The tree is what the
// RE2 source is generated from and what later static analyses walk; nothing in
// this file lets a pattern through that another host's engine would read
// differently.

package sel

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/limits"
)

const (
	maxQuantifier   = 65535
	maxRegexDepth   = 200
	maxRegexGroups  = limits.MAX_REGEX_GROUPS
	maxRegexPattern = limits.MAX_REGEX_PATTERN
	regexSat        = int64(1) << 40
)

type reKind int

const (
	reEmpty reKind = iota
	reLit          // one literal character (text is its portable source)
	reAny          // .
	reClass        // [...] or an expanded escape such as \d
	reBOL          // ^
	reEOL          // $
	reCat
	reAlt
	reGroup // capturing (idx > 0) or (?: ) (idx == 0)
	reRep
)

type reNode struct {
	kind reKind
	text string    // portable source of a leaf; the quantifier text of a reRep
	subs []*reNode // reCat / reAlt members; reGroup and reRep have one
	idx  int       // capture index of a capturing group
	lo   int       // reRep bounds; hi < 0 is unbounded
	hi   int
	lazy bool

	nullable bool
	minLen   int64
	maxLen   int64
	hasCap   bool

	// The character set of a leaf, for the ambiguity analysis (§7.8). A bracket
	// class keeps its members here and its negation in neg, because `i` folds the
	// members first and negates after.
	set     []rng
	bracket bool
	neg     bool
}

func badRegex(message, pattern string, at int, pos Pos) {
	fail("E_REGEX_SYNTAX", fmt.Sprintf("%s (at offset %d of /%s/)", message, at, clipPattern(pattern)), pos)
}

func clipPattern(p string) string {
	if len(p) > 80 {
		return p[:80] + "…"
	}
	return p
}

var expandOutside = map[rune]string{
	'd': "[0-9]", 'D': "[^0-9]",
	'w': "[0-9A-Za-z_]", 'W': "[^0-9A-Za-z_]",
	's': "[ \\t\\n\\r\\f\\x0b]", 'S': "[^ \\t\\n\\r\\f\\x0b]",
}

var expandInside = map[rune]string{
	'd': "0-9", 'w': "0-9A-Za-z_", 's': " \\t\\n\\r\\f\\x0b",
}

var controlEscapes = map[rune]bool{'n': true, 'r': true, 't': true, 'f': true}

var syntaxChars = map[rune]bool{
	'^': true, '$': true, '\\': true, '.': true, '*': true, '+': true, '?': true,
	'(': true, ')': true, '[': true, ']': true, '{': true, '}': true, '|': true, '/': true,
}

func rejectEscape(e rune, pattern string, at int, pos Pos) {
	switch {
	case e == 'b' || e == 'B':
		badRegex(fmt.Sprintf("\\%c is not portable — word boundaries depend on the engine's idea of a word character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])", e), pattern, at, pos)
	case e == 'v':
		badRegex("\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B", pattern, at, pos)
	case e >= '0' && e <= '9':
		badRegex("backreferences are not portable", pattern, at, pos)
	case e == 'p' || e == 'P':
		badRegex("\\p{...} is not portable", pattern, at, pos)
	case e == 'A' || e == 'z' || e == 'Z' || e == 'G' || e == 'K':
		badRegex(fmt.Sprintf("\\%c is not portable — use ^ and $", e), pattern, at, pos)
	}
	badRegex(fmt.Sprintf("unsupported escape \\%c", e), pattern, at, pos)
}

type reParser struct {
	src     []rune
	pattern string
	pos     Pos
	i       int
	depth   int
	groups  int // groups opened so far, capturing or not
	caps    int // capture groups opened so far
}

// parseRegex parses a pattern of the portable subset. Rejections are
// E_REGEX_SYNTAX at the pattern's position.
func parseRegex(pattern string, pos Pos) *reNode {
	return parseRegexIC(pattern, false, pos)
}

// parseRegexIC is parseRegex for a pattern that will run with the `i` flag when
// ignoreCase is set: the ambiguity analysis folds case, so its verdict depends
// on the flag.
func parseRegexIC(pattern string, ignoreCase bool, pos Pos) *reNode {
	src := []rune(pattern)
	if len(src) > maxRegexPattern {
		badRegex(fmt.Sprintf("the pattern has %d code points; the limit is %d", len(src), maxRegexPattern), pattern, 0, pos)
	}
	p := &reParser{src: src, pattern: pattern, pos: pos}
	n := p.parseAlt()
	if p.i < len(p.src) {
		// Only an unmatched ) stops parseAlt early.
		badRegex("unmatched ) — escape it as \\)", pattern, p.i, pos)
	}
	analyse(n)
	checkLoops(n, pos)
	checkAmbiguity(n, ignoreCase, pattern, pos)
	return n
}

func (p *reParser) more() bool { return p.i < len(p.src) }

func (p *reParser) parseAlt() *reNode {
	branches := []*reNode{p.parseCat()}
	for p.more() && p.src[p.i] == '|' {
		p.i++
		branches = append(branches, p.parseCat())
	}
	if len(branches) == 1 {
		return branches[0]
	}
	return &reNode{kind: reAlt, subs: branches}
}

func (p *reParser) parseCat() *reNode {
	var items []*reNode
	for p.more() {
		c := p.src[p.i]
		if c == '|' || c == ')' {
			break
		}
		items = append(items, p.parseQuantified())
	}
	switch len(items) {
	case 0:
		return &reNode{kind: reEmpty}
	case 1:
		return items[0]
	}
	return &reNode{kind: reCat, subs: items}
}

func (p *reParser) parseQuantified() *reNode {
	start := p.i
	atom := p.parseAtom()
	if !p.more() {
		return atom
	}
	c := p.src[p.i]
	if c != '*' && c != '+' && c != '?' && c != '{' {
		return atom
	}
	qStart := p.i
	lo, hi := 0, -1
	switch c {
	case '*':
		p.i++
	case '+':
		lo = 1
		p.i++
	case '?':
		hi = 1
		p.i++
	case '{':
		lo, hi = p.parseBraces()
	}
	if atom.kind == reBOL || atom.kind == reEOL {
		badRegex("an anchor cannot be quantified — ^ and $ match a position, and engines disagree on what repeating one means", p.pattern, start, p.pos)
	}
	lazy := false
	if p.more() && p.src[p.i] == '+' {
		badRegex("possessive quantifiers are not portable", p.pattern, p.i, p.pos)
	}
	if p.more() && p.src[p.i] == '?' {
		lazy = true
		p.i++
	}
	if p.more() {
		if n := p.src[p.i]; n == '*' || n == '+' || n == '?' || n == '{' {
			badRegex("nested repetition — a quantifier cannot follow a quantifier", p.pattern, p.i, p.pos)
		}
	}
	return &reNode{kind: reRep, subs: []*reNode{atom}, lo: lo, hi: hi, lazy: lazy, text: string(p.src[qStart:p.i])}
}

func (p *reParser) parseBraces() (lo, hi int) {
	start := p.i
	i := p.i + 1
	loStart := i
	for i < len(p.src) && p.src[i] >= '0' && p.src[i] <= '9' {
		i++
	}
	if i == loStart {
		badRegex("{ must begin a quantifier such as {2,4} — escape it as \\{", p.pattern, start, p.pos)
	}
	lo = atoiSat(string(p.src[loStart:i]))
	hi = lo
	if i < len(p.src) && p.src[i] == ',' {
		i++
		hiStart := i
		for i < len(p.src) && p.src[i] >= '0' && p.src[i] <= '9' {
			i++
		}
		if i > hiStart {
			hi = atoiSat(string(p.src[hiStart:i]))
		} else {
			hi = -1
		}
	}
	if i >= len(p.src) || p.src[i] != '}' {
		badRegex("malformed quantifier", p.pattern, start, p.pos)
	}
	if lo > maxQuantifier || hi > maxQuantifier {
		badRegex(fmt.Sprintf("quantifier bound exceeds the maximum of %d", maxQuantifier), p.pattern, start, p.pos)
	}
	if hi >= 0 && hi < lo {
		badRegex(fmt.Sprintf("quantifier {%d,%d} is empty — the upper bound is below the lower one", lo, hi), p.pattern, start, p.pos)
	}
	p.i = i + 1
	return lo, hi
}

// atoiSat reads a run of digits, saturating well above the quantifier maximum.
func atoiSat(s string) int {
	if len(s) > 9 {
		return 1 << 30
	}
	n, _ := strconv.Atoi(s)
	return n
}

func (p *reParser) parseAtom() *reNode {
	c := p.src[p.i]
	switch c {
	case '\\':
		return p.parseEscape()
	case '[':
		return p.parseClass()
	case '(':
		return p.parseGroup()
	case '.':
		p.i++
		return &reNode{kind: reAny, text: ".", set: []rng{{0, maxCodePoint}}}
	case '^':
		p.i++
		return &reNode{kind: reBOL, text: "^"}
	case '$':
		p.i++
		return &reNode{kind: reEOL, text: "$"}
	case '*', '+', '?':
		badRegex("nothing to repeat — a quantifier needs something before it", p.pattern, p.i, p.pos)
	case '{':
		badRegex("{ must begin a quantifier such as {2,4} — escape it as \\{", p.pattern, p.i, p.pos)
	case '}':
		badRegex("unmatched } — escape it as \\}", p.pattern, p.i, p.pos)
	case ']':
		badRegex("unmatched ] — escape it as \\]", p.pattern, p.i, p.pos)
	}
	p.i++
	return &reNode{kind: reLit, text: string(c), set: []rng{{c, c}}}
}

func (p *reParser) parseEscape() *reNode {
	at := p.i
	if p.i+1 >= len(p.src) {
		badRegex("trailing backslash", p.pattern, at, p.pos)
	}
	e := p.src[p.i+1]
	p.i += 2
	if exp, ok := expandOutside[e]; ok {
		set := escapeSet(e)
		return &reNode{kind: reClass, text: exp, set: set}
	}
	if controlEscapes[e] || syntaxChars[e] {
		v := escapeValue(e)
		return &reNode{kind: reLit, text: "\\" + string(e), set: []rng{{v, v}}}
	}
	rejectEscape(e, p.pattern, at, p.pos)
	return nil
}

func (p *reParser) parseGroup() *reNode {
	at := p.i
	capture := true
	p.i++
	if p.more() && p.src[p.i] == '*' {
		badRegex("PCRE verbs such as (*FAIL) are not portable", p.pattern, at, p.pos)
	}
	if p.more() && p.src[p.i] == '?' {
		nxt := rune(0)
		if p.i+1 < len(p.src) {
			nxt = p.src[p.i+1]
		}
		if nxt != ':' {
			kind := "this group type"
			switch nxt {
			case '=', '!':
				kind = "lookahead"
			case '<':
				kind = "lookbehind and named groups"
			case '>':
				kind = "atomic groups"
			}
			badRegex(fmt.Sprintf("%s is not portable — only (?: ) is", kind), p.pattern, at, p.pos)
		}
		capture = false
		p.i += 2
	}
	p.groups++
	if p.groups > maxRegexGroups {
		badRegex(fmt.Sprintf("more than %d groups", maxRegexGroups), p.pattern, at, p.pos)
	}
	p.depth++
	if p.depth > maxRegexDepth {
		badRegex(fmt.Sprintf("groups nested deeper than %d", maxRegexDepth), p.pattern, at, p.pos)
	}
	idx := 0
	if capture {
		p.caps++
		idx = p.caps
	}
	inner := p.parseAlt()
	if !p.more() || p.src[p.i] != ')' {
		badRegex("unterminated group", p.pattern, at, p.pos)
	}
	p.i++
	p.depth--
	return &reNode{kind: reGroup, subs: []*reNode{inner}, idx: idx}
}

// parseClass reads a character class, expanding \d \w \s and refusing what
// engines read differently: POSIX bracket forms anywhere, an escape class as the
// end of a range, an empty class, a reversed range.
func (p *reParser) parseClass() *reNode {
	start := p.i
	p.i++
	var out strings.Builder
	out.WriteByte('[')
	negated := false
	if p.more() && p.src[p.i] == '^' {
		out.WriteByte('^')
		negated = true
		p.i++
	}
	var members []rng
	type item struct {
		text   string
		ch     rune
		single bool // a plain character (or character escape), not \d \w \s
		set    []rng
	}
	readItem := func() item {
		c := p.src[p.i]
		if c == '[' && p.i+1 < len(p.src) {
			if k := p.src[p.i+1]; k == ':' || k == '.' || k == '=' {
				// A POSIX bracket form when a matching terminator follows before
				// the first closing bracket: [:alpha:], [.x.], [=x=].
				for j := p.i + 2; j < len(p.src); j++ {
					if p.src[j] == ']' {
						break
					}
					if p.src[j] == k && j+1 < len(p.src) && p.src[j+1] == ']' {
						badRegex("POSIX bracket forms such as [[:alpha:]] are not portable", p.pattern, p.i, p.pos)
					}
				}
			}
		}
		if c == '\\' {
			if p.i+1 >= len(p.src) {
				badRegex("trailing backslash in character class", p.pattern, p.i, p.pos)
			}
			e := p.src[p.i+1]
			at := p.i
			p.i += 2
			if exp, ok := expandInside[e]; ok {
				return item{text: exp, set: escapeSet(e)}
			}
			if e == 'D' || e == 'W' || e == 'S' {
				badRegex(fmt.Sprintf("\\%c inside a character class cannot be expressed portably — negate the whole class instead", e), p.pattern, at, p.pos)
			}
			if controlEscapes[e] || syntaxChars[e] || e == '-' {
				return item{text: "\\" + string(e), ch: escapeValue(e), single: true}
			}
			rejectEscape(e, p.pattern, at, p.pos)
		}
		p.i++
		return item{text: string(c), ch: c, single: true}
	}
	count := 0
	for p.i < len(p.src) {
		if p.src[p.i] == ']' {
			if count == 0 {
				badRegex("empty character class — write \\] for a literal bracket", p.pattern, start, p.pos)
			}
			p.i++
			out.WriteByte(']')
			return &reNode{kind: reClass, text: out.String(), set: members, bracket: true, neg: negated}
		}
		count++
		lo := readItem()
		out.WriteString(lo.text)
		if !lo.single {
			members = append(members, lo.set...)
		}
		if p.i+1 < len(p.src) && p.src[p.i] == '-' && p.src[p.i+1] != ']' {
			dash := p.i
			p.i++
			if !lo.single {
				badRegex("a character class escape cannot be the start of a range", p.pattern, dash, p.pos)
			}
			hi := readItem()
			if !hi.single {
				badRegex("a character class escape cannot be the end of a range", p.pattern, dash, p.pos)
			}
			if hi.ch < lo.ch {
				badRegex("character class range is reversed", p.pattern, dash, p.pos)
			}
			out.WriteByte('-')
			out.WriteString(hi.text)
			members = append(members, rng{lo.ch, hi.ch})
		} else if lo.single {
			members = append(members, rng{lo.ch, lo.ch})
		}
	}
	badRegex("unterminated character class", p.pattern, start, p.pos)
	return nil
}

func escapeValue(e rune) rune {
	switch e {
	case 'n':
		return '\n'
	case 'r':
		return '\r'
	case 't':
		return '\t'
	case 'f':
		return '\f'
	}
	return e
}

// ---- analyses on the tree ---------------------------------------------------

func satAddLen(a, b int64) int64 {
	if a+b > regexSat {
		return regexSat
	}
	return a + b
}

func satMulLen(a, b int64) int64 {
	if a == 0 || b == 0 {
		return 0
	}
	if a > regexSat/b {
		return regexSat
	}
	return a * b
}

// analyse fills nullable, minLen and hasCap bottom-up.
func analyse(n *reNode) {
	for _, s := range n.subs {
		analyse(s)
	}
	switch n.kind {
	case reEmpty, reBOL, reEOL:
		n.nullable = true
	case reLit, reAny, reClass:
		n.minLen, n.maxLen = 1, 1
	case reCat:
		n.nullable = true
		for _, s := range n.subs {
			n.nullable = n.nullable && s.nullable
			n.minLen = satAddLen(n.minLen, s.minLen)
			n.maxLen = satAddLen(n.maxLen, s.maxLen)
			n.hasCap = n.hasCap || s.hasCap
		}
	case reAlt:
		n.minLen = regexSat
		for _, s := range n.subs {
			n.nullable = n.nullable || s.nullable
			if s.minLen < n.minLen {
				n.minLen = s.minLen
			}
			if s.maxLen > n.maxLen {
				n.maxLen = s.maxLen
			}
			n.hasCap = n.hasCap || s.hasCap
		}
	case reGroup:
		c := n.subs[0]
		n.nullable, n.minLen, n.maxLen = c.nullable, c.minLen, c.maxLen
		n.hasCap = c.hasCap || n.idx > 0
	case reRep:
		c := n.subs[0]
		n.nullable = n.lo == 0 || c.nullable
		n.minLen = satMulLen(c.minLen, int64(n.lo))
		switch {
		case n.hi == 0 || c.maxLen == 0:
			n.maxLen = 0
		case n.hi < 0:
			n.maxLen = regexSat
		default:
			n.maxLen = satMulLen(c.maxLen, int64(n.hi))
		}
		n.hasCap = c.hasCap
	}
}

// isLoop reports a quantifier that can iterate more than once.
func isLoop(n *reNode) bool { return n.kind == reRep && (n.hi < 0 || n.hi > 1) }

// checkLoops applies the two loop rules of §7.8: a quantified group's body must
// not be able to match the empty text, and a capture inside a loop must take part
// in every iteration. Both are properties of the pattern as a whole, so they are
// reported at the pattern's position.
func checkLoops(n *reNode, pos Pos) {
	if isLoop(n) {
		body := n.subs[0]
		if body.nullable {
			fail("E_REGEX_SYNTAX", "a quantified group whose body can match the empty text is not portable — engines disagree on what the empty iteration captures and consumes", pos)
		}
		checkOptionalCaptures(body, pos)
	}
	for _, s := range n.subs {
		checkLoops(s, pos)
	}
}

// checkOptionalCaptures refuses, inside a loop body, a capture that some
// iteration can skip: one under an alternation, or under a quantifier that can
// iterate zero times.
func checkOptionalCaptures(n *reNode, pos Pos) {
	switch n.kind {
	case reGroup:
		checkOptionalCaptures(n.subs[0], pos)
	case reCat:
		for _, s := range n.subs {
			checkOptionalCaptures(s, pos)
		}
	case reAlt:
		for _, s := range n.subs {
			if s.hasCap {
				fail("E_REGEX_SYNTAX", "a capture inside a loop must take part in every iteration — one under an alternation may not", pos)
			}
		}
	case reRep:
		if n.subs[0].hasCap && n.lo == 0 {
			fail("E_REGEX_SYNTAX", "a capture inside a loop must take part in every iteration — one under ?, * or {0,n} may not", pos)
		}
		checkOptionalCaptures(n.subs[0], pos)
	}
}
