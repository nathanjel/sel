// Tokeniser. See spec/grammar.md.

package sel

import (
	"fmt"
	"regexp"
	"strconv"
	gout "unicode/utf8"

	"github.com/nathanjel/sel/go/internal/utf8"
)

var operators = []string{
	"???", "??",
	"$==", "$!=", "$<=", "$>=",
	"$<", "$>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "&=",
	".>",
	"+", "-", "*", "/", "%", "&", "=", "<", ">", "(", ")", "[", "]", ",", ";",
}

var reserved = map[string]struct{}{
	"TRUE": {}, "FALSE": {}, "NULL": {},
	"AND": {}, "OR": {}, "NOT": {}, "XOR": {},
	"EQL": {}, "IN": {}, "BAND": {}, "BOR": {}, "BXOR": {},
}

var simpleEscapes = map[rune]rune{
	'\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', '{': '{', '}': '}',
}

var hexRegex = regexp.MustCompile(`^[0-9a-fA-F]+$`)

func isDigit(c rune) bool {
	return '0' <= c && c <= '9'
}

func isAlpha(c rune) bool {
	return ('A' <= c && c <= 'Z') || ('a' <= c && c <= 'z') || c == '_'
}

func isIdent(c rune) bool {
	return isAlpha(c) || isDigit(c)
}

func isSpace(c rune) bool {
	return c == ' ' || c == '\t' || c == '\r' || c == '\n'
}

type tokenType string

const (
	tokenNum   tokenType = "num"
	tokenText  tokenType = "text"
	tokenIdent tokenType = "ident"
	tokenOp    tokenType = "op"
	tokenEOF   tokenType = "eof"
)

type token struct {
	Type  tokenType
	Value string
	Pos   Pos
}

const maxTokenPrealloc = 1 << 17

type lexer struct {
	chars      []rune
	n          int
	lineStarts []int
	// braceEnds[i] is the index just past the '}' matching the '{' at i, once
	// some scan has established it (0 = not yet). See matchBrace.
	braceEnds []int
	// lineCursor is the line index of the last posAt answer.
	lineCursor int
}

// The kinds of work lexRange keeps on its explicit stack.
const (
	taskRange = iota
	taskPart
	taskClose
	taskEnd
)

type lexTask struct {
	kind  int
	i, to int      // taskRange
	part  textPart // taskPart, taskClose
	index int      // taskPart
	mark  int      // taskClose
	pos   Pos      // taskPart, taskEnd
	bal   *balance // taskRange, taskClose: nil at the top level
}

// The parentheses and brackets open so far in one interpolation body. A body is
// spliced into the surrounding tokens as `( body )`, so one that closes what it
// never opened, or leaves something open, would change the meaning of the text
// around it; each body has to balance inside its own braces. Only the body's
// direct tokens count, not those emitted by literals nested inside it.
type balance struct {
	open []rune // '(' or '['
}

// sourceRunes splits the source into code points, and reports invalid UTF-8 at
// the first invalid unit (SPEC §2): its position counts the code points before
// it, with LF the only line end, like every other position. Go's decoder is
// strict — a bad start byte, a truncated or overlong sequence, an encoded
// surrogate and anything above U+10FFFF are all RuneError of width 1 — and a
// genuine U+FFFD is width 3, so the two cannot be confused.
func sourceRunes(source string) []rune {
	runes := make([]rune, 0, len(source))
	line, lineStart := 1, 0
	for i := 0; i < len(source); {
		r, size := gout.DecodeRuneInString(source[i:])
		if r == gout.RuneError && size == 1 {
			off := len(runes)
			fail("E_UTF8", fmt.Sprintf("invalid UTF-8 at byte %d", i),
				Pos{Line: line, Col: off - lineStart + 1, Offset: off})
		}
		if r == '\n' {
			line++
			lineStart = len(runes) + 1
		}
		runes = append(runes, r)
		i += size
	}
	return runes
}

func newLexer(source string) *lexer {
	chars := sourceRunes(source)
	lineStarts := []int{0}
	for i, ch := range chars {
		if ch == '\n' {
			lineStarts = append(lineStarts, i+1)
		}
	}
	return &lexer{
		chars:      chars,
		n:          len(chars),
		lineStarts: lineStarts,
		braceEnds:  make([]int, len(chars)),
	}
}

// posAt resolves a code point offset to line and column. Tokens are asked for in
// nearly increasing order, so the line of the previous answer (and the next few)
// are tried before the binary search.
func (l *lexer) posAt(offset int) Pos {
	ls := l.lineStarts
	cur := l.lineCursor
	if cur >= len(ls) || ls[cur] > offset {
		cur = -1
	} else {
		// Forward within a few lines of the last answer.
		for step := 0; step < 4; step++ {
			if cur+1 >= len(ls) || ls[cur+1] > offset {
				l.lineCursor = cur
				return Pos{Line: cur + 1, Col: offset - ls[cur] + 1, Offset: offset}
			}
			cur++
		}
		cur = -1
	}
	lo, hi := 0, len(ls)-1
	for lo < hi {
		mid := (lo + hi + 1) / 2
		if ls[mid] <= offset {
			lo = mid
		} else {
			hi = mid - 1
		}
	}
	l.lineCursor = lo
	return Pos{Line: lo + 1, Col: offset - ls[lo] + 1, Offset: offset}
}

func (l *lexer) Tokenize() []token {
	// About one token in three characters for ordinary rules; growth past the cap
	// is by append (a source that is mostly text does not get 56 bytes a character).
	est := l.n/3 + 32
	if est > maxTokenPrealloc {
		est = maxTokenPrealloc
	}
	out := make([]token, 0, est)
	l.lexRange(0, l.n, &out)
	out = append(out, token{Type: tokenEOF, Value: "", Pos: l.posAt(l.n)})
	return out
}

// Lexes chars[frm, to) into out. Interpolation nests without bound, so this is
// a loop over an explicit stack of tasks rather than a recursion: a literal
// pushes what it still has to emit (its parts, each interior range, the
// closers) and the loop pops them in source order. Nothing here can therefore
// reach the goroutine stack limit, however deep the braces go.
func (l *lexer) lexRange(frm, to int, out *[]token) {
	stack := []lexTask{{kind: taskRange, i: frm, to: to}}
	for len(stack) > 0 {
		task := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		switch task.kind {
		case taskRange:
			stack = l.lexTokens(task.i, task.to, out, stack, task.bal)
		case taskPart:
			stack = l.emitPart(task, out, stack)
		case taskClose:
			// An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
			if len(*out) == task.mark+1 {
				fail("E_SYNTAX", "empty interpolation {}", l.posAt(task.part.frm))
			}
			// ... and one whose parentheses do not close inside the braces.
			if len(task.bal.open) > 0 {
				fail("E_SYNTAX", fmt.Sprintf("unclosed %c in interpolation", task.bal.open[len(task.bal.open)-1]),
					l.posAt(task.part.to))
			}
			*out = append(*out, token{Type: tokenOp, Value: ")", Pos: l.posAt(task.part.to)})
		case taskEnd:
			*out = append(*out, token{Type: tokenOp, Value: ")", Pos: task.pos})
		}
	}
}

// The flat part of lexRange. A quoted literal with parts ends the run: the
// tasks it pushes come first, and the rest of the range resumes after them.
//
// bal is the balance of the interpolation body being lexed, nil at the top level
// where the parser does the balancing.
func (l *lexer) lexTokens(frm, to int, out *[]token, stack []lexTask, bal *balance) []lexTask {
	i := frm
	for i < to {
		c := l.chars[i]

		if isSpace(c) {
			i++
			continue
		}

		if c == '#' {
			for i < to && l.chars[i] != '\n' {
				i++
			}
			continue
		}

		pos := l.posAt(i)

		if isDigit(c) {
			j := i
			for j < to && isDigit(l.chars[j]) {
				j++
			}
			// Only consume the dot when a digit follows, so `1.` is not a number.
			if j+1 < to && l.chars[j] == '.' && isDigit(l.chars[j+1]) {
				j++
				for j < to && isDigit(l.chars[j]) {
					j++
				}
			}
			*out = append(*out, token{Type: tokenNum, Value: string(l.chars[i:j]), Pos: pos})
			i = j
			continue
		}

		if isAlpha(c) {
			j := i
			for j < to && isIdent(l.chars[j]) {
				j++
			}
			// An identifier is ASCII by construction (isAlpha, isIdent): upper-case
			// it while copying, in one allocation rather than two.
			word := make([]byte, j-i)
			for k := i; k < j; k++ {
				ch := byte(l.chars[k])
				if 'a' <= ch && ch <= 'z' {
					ch -= 32
				}
				word[k-i] = ch
			}
			*out = append(*out, token{Type: tokenIdent, Value: string(word), Pos: pos})
			i = j
			continue
		}

		if c == '"' {
			parts, next := l.scanQuoted(i, to)
			if len(parts) == 1 {
				*out = append(*out, token{Type: tokenText, Value: parts[0].text, Pos: pos})
				i = next
				continue
			}
			// `( "seg" & expr & "seg" )`: the opener now, the rest as tasks, the
			// remainder of this range underneath them.
			*out = append(*out, token{Type: tokenOp, Value: "(", Pos: pos})
			stack = append(stack, lexTask{kind: taskRange, i: next, to: to, bal: bal})
			stack = append(stack, lexTask{kind: taskEnd, pos: pos})
			for k := len(parts) - 1; k >= 0; k-- {
				stack = append(stack, lexTask{kind: taskPart, part: parts[k], index: k, pos: pos})
			}
			return stack
		}
		if c == '\'' {
			i = l.lexRaw(i, to, out)
			continue
		}

		if op, ok := l.matchOperator(i, to); ok {
			if bal != nil {
				switch op {
				case "(", "[":
					bal.open = append(bal.open, rune(op[0]))
				case ")", "]":
					n := len(bal.open)
					if n == 0 || (bal.open[n-1] == '(') != (op == ")") {
						fail("E_SYNTAX", fmt.Sprintf("unbalanced %s in interpolation", op), pos)
					}
					bal.open = bal.open[:n-1]
				}
			}
			*out = append(*out, token{Type: tokenOp, Value: op, Pos: pos})
			i += len(op)
			continue
		}

		fail("E_SYNTAX", fmt.Sprintf("unexpected character %q", c), pos)
	}
	return stack
}

// One part of an interpolated literal: the `&` before it, then either its text
// or `( interior )`, the interior being a range of its own.
func (l *lexer) emitPart(task lexTask, out *[]token, stack []lexTask) []lexTask {
	part, pos := task.part, task.pos
	if task.index > 0 {
		*out = append(*out, token{Type: tokenOp, Value: "&", Pos: pos})
	}
	if !part.isExpr {
		*out = append(*out, token{Type: tokenText, Value: part.text, Pos: pos})
		return stack
	}
	mark := len(*out)
	bal := &balance{}
	*out = append(*out, token{Type: tokenOp, Value: "(", Pos: l.posAt(part.frm)})
	stack = append(stack, lexTask{kind: taskClose, mark: mark, part: part, bal: bal})
	return append(stack, lexTask{kind: taskRange, i: part.frm, to: part.to, bal: bal})
}

// Every operator is ASCII, so it is compared byte against code point, with no
// []rune(op) conversion per probe.
func (l *lexer) matchOperator(i, to int) (string, bool) {
	for _, op := range operators {
		if i+len(op) > to {
			continue
		}
		match := true
		for k := 0; k < len(op); k++ {
			if l.chars[i+k] != rune(op[k]) {
				match = false
				break
			}
		}
		if match {
			return op, true
		}
	}
	return "", false
}

func (l *lexer) lexRaw(start, to int, out *[]token) int {
	pos := l.posAt(start)
	i := start + 1
	var buf []rune
	for i < to {
		c := l.chars[i]
		if c == '\'' {
			if i+1 < to && l.chars[i+1] == '\'' {
				buf = append(buf, '\'')
				i += 2
				continue
			}
			*out = append(*out, token{Type: tokenText, Value: string(buf), Pos: pos})
			return i + 1
		}
		buf = append(buf, c)
		i++
	}
	fail("E_UNTERMINATED", "unterminated raw text literal", pos)
	return to
}

type textPart struct {
	isExpr bool
	text   string
	frm    int
	to     int
}

// Reads a quoted literal into its parts and the index just past its closing
// quote, emitting nothing. Every `{...}` is skipped by matchBrace, so the
// interior is not read here, only located.
func (l *lexer) scanQuoted(start, to int) ([]textPart, int) {
	pos := l.posAt(start)
	var parts []textPart
	var buf []rune
	i := start + 1

	for i < to {
		c := l.chars[i]

		if c == '"' {
			parts = append(parts, textPart{isExpr: false, text: string(buf)})
			return parts, i + 1
		}

		if c == '\\' {
			text, nextIdx := l.readEscape(i, to)
			buf = append(buf, []rune(text)...)
			i = nextIdx
			continue
		}

		if c == '{' {
			closeIdx := l.matchBrace(i, to) - 1 // index of matching '}'
			parts = append(parts, textPart{isExpr: false, text: string(buf)})
			buf = nil
			parts = append(parts, textPart{isExpr: true, frm: i + 1, to: closeIdx})
			i = closeIdx + 1
			continue
		}

		buf = append(buf, c)
		i++
	}
	fail("E_UNTERMINATED", "unterminated text literal", pos)
	return nil, to
}

func (l *lexer) readEscape(i, to int) (string, int) {
	pos := l.posAt(i)
	if i+1 >= to {
		fail("E_UNTERMINATED", "text literal ends in a backslash", pos)
	}
	e := l.chars[i+1]

	if esc, ok := simpleEscapes[e]; ok {
		return string(esc), i + 2
	}

	if e == 'u' {
		if i+2 >= to || l.chars[i+2] != '{' {
			fail("E_ESCAPE", "\\u must be followed by {", pos)
		}
		j := i + 3
		var hexRunes []rune
		for j < to && l.chars[j] != '}' {
			hexRunes = append(hexRunes, l.chars[j])
			j++
		}
		if j >= to {
			fail("E_UNTERMINATED", "unterminated \\u{...} escape", pos)
		}
		hexStr := string(hexRunes)
		if len(hexStr) == 0 || len(hexStr) > 6 || !hexRegex.MatchString(hexStr) {
			fail("E_ESCAPE", fmt.Sprintf("bad \\u{%s} escape", hexStr), pos)
		}
		cp, err := strconv.ParseInt(hexStr, 16, 32)
		if err != nil || cp > 0x10FFFF || (0xD800 <= cp && cp <= 0xDFFF) {
			fail("E_RANGE", fmt.Sprintf("code point U+%s is not encodable", utf8.AsciiUpper(hexStr)), pos)
		}
		return string(rune(cp)), j + 1
	}

	fail("E_ESCAPE", fmt.Sprintf("unknown escape \\%c", e), pos)
	return "", to
}

// An open construct while matchBrace scans: a brace (with its nesting count)
// or a string.
type openConstruct struct {
	str   bool
	at    int
	depth int
}

// Returns the index just past the matching '}'. Nested literals are skipped so
// that a brace inside a string inside an interpolation does not close it.
//
// One pass with an explicit stack of what is open (a brace, a string), not a
// recursion through the strings, and every brace it closes is remembered in
// braceEnds. The second half is what keeps the lexer linear: a literal nested
// d deep is located by its parent and again by each of its own ancestors'
// interiors being lexed, and without the memo each of those locate-passes
// re-read everything below it. If anything is unterminated the innermost open
// construct is the one reported, which is where the recursion used to fail.
func (l *lexer) matchBrace(i, to int) int {
	if e := l.braceEnds[i]; e != 0 {
		return e
	}
	open := []openConstruct{{at: i}}
	j := i
	for {
		top := &open[len(open)-1]
		if j >= to {
			msg := "unterminated { in text literal"
			if top.str {
				msg = "unterminated text literal"
			}
			fail("E_UNTERMINATED", msg, l.posAt(top.at))
		}
		c := l.chars[j]
		if top.str {
			switch c {
			case '\\':
				j += 2
			case '"':
				open = open[:len(open)-1]
				j++
			case '{':
				open = append(open, openConstruct{at: j})
			default:
				j++
			}
			continue
		}
		switch c {
		case '"':
			open = append(open, openConstruct{str: true, at: j})
			j++
		case '\'':
			j = l.skipRaw(j, to)
		case '{':
			top.depth++
			j++
		case '}':
			top.depth--
			j++
			if top.depth == 0 {
				l.braceEnds[top.at] = j
				open = open[:len(open)-1]
				if len(open) == 0 {
					return j
				}
			}
		case '#':
			for j < to && l.chars[j] != '\n' {
				j++
			}
		default:
			j++
		}
	}
}

func (l *lexer) skipRaw(j, to int) int {
	pos := l.posAt(j)
	j++
	for j < to {
		if l.chars[j] == '\'' {
			if j+1 < to && l.chars[j+1] == '\'' {
				j += 2
				continue
			}
			return j + 1
		}
		j++
	}
	fail("E_UNTERMINATED", "unterminated raw text literal", pos)
	return to
}

func tokenize(source string) []token {
	return newLexer(source).Tokenize()
}
