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

type TokenType string

const (
	TokenNum   TokenType = "num"
	TokenText  TokenType = "text"
	TokenIdent TokenType = "ident"
	TokenOp    TokenType = "op"
	TokenEOF   TokenType = "eof"
)

type Token struct {
	Type  TokenType
	Value string
	Pos   Pos
}

type Lexer struct {
	chars      []rune
	n          int
	lineStarts []int
	// braceEnds[i] is the index just past the '}' matching the '{' at i, once
	// some scan has established it (0 = not yet). See matchBrace.
	braceEnds []int
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

func NewLexer(source string) *Lexer {
	chars := sourceRunes(source)
	lineStarts := []int{0}
	for i, ch := range chars {
		if ch == '\n' {
			lineStarts = append(lineStarts, i+1)
		}
	}
	return &Lexer{
		chars:      chars,
		n:          len(chars),
		lineStarts: lineStarts,
		braceEnds:  make([]int, len(chars)),
	}
}

func (l *Lexer) posAt(offset int) Pos {
	lo, hi := 0, len(l.lineStarts)-1
	for lo < hi {
		mid := (lo + hi + 1) / 2
		if l.lineStarts[mid] <= offset {
			lo = mid
		} else {
			hi = mid - 1
		}
	}
	return Pos{
		Line:   lo + 1,
		Col:    offset - l.lineStarts[lo] + 1,
		Offset: offset,
	}
}

func (l *Lexer) Tokenize() []Token {
	out := make([]Token, 0, 32)
	l.lexRange(0, l.n, &out)
	out = append(out, Token{Type: TokenEOF, Value: "", Pos: l.posAt(l.n)})
	return out
}

// Lexes chars[frm, to) into out. Interpolation nests without bound, so this is
// a loop over an explicit stack of tasks rather than a recursion: a literal
// pushes what it still has to emit (its parts, each interior range, the
// closers) and the loop pops them in source order. Nothing here can therefore
// reach the goroutine stack limit, however deep the braces go.
func (l *Lexer) lexRange(frm, to int, out *[]Token) {
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
			*out = append(*out, Token{Type: TokenOp, Value: ")", Pos: l.posAt(task.part.to)})
		case taskEnd:
			*out = append(*out, Token{Type: TokenOp, Value: ")", Pos: task.pos})
		}
	}
}

// The flat part of lexRange. A quoted literal with parts ends the run: the
// tasks it pushes come first, and the rest of the range resumes after them.
//
// bal is the balance of the interpolation body being lexed, nil at the top level
// where the parser does the balancing.
func (l *Lexer) lexTokens(frm, to int, out *[]Token, stack []lexTask, bal *balance) []lexTask {
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
			*out = append(*out, Token{Type: TokenNum, Value: string(l.chars[i:j]), Pos: pos})
			i = j
			continue
		}

		if isAlpha(c) {
			j := i
			for j < to && isIdent(l.chars[j]) {
				j++
			}
			word := string(l.chars[i:j])
			*out = append(*out, Token{Type: TokenIdent, Value: utf8.AsciiUpper(word), Pos: pos})
			i = j
			continue
		}

		if c == '"' {
			parts, next := l.scanQuoted(i, to)
			if len(parts) == 1 {
				*out = append(*out, Token{Type: TokenText, Value: parts[0].text, Pos: pos})
				i = next
				continue
			}
			// `( "seg" & expr & "seg" )`: the opener now, the rest as tasks, the
			// remainder of this range underneath them.
			*out = append(*out, Token{Type: TokenOp, Value: "(", Pos: pos})
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
			*out = append(*out, Token{Type: TokenOp, Value: op, Pos: pos})
			i += len([]rune(op))
			continue
		}

		fail("E_SYNTAX", fmt.Sprintf("unexpected character %q", c), pos)
	}
	return stack
}

// One part of an interpolated literal: the `&` before it, then either its text
// or `( interior )`, the interior being a range of its own.
func (l *Lexer) emitPart(task lexTask, out *[]Token, stack []lexTask) []lexTask {
	part, pos := task.part, task.pos
	if task.index > 0 {
		*out = append(*out, Token{Type: TokenOp, Value: "&", Pos: pos})
	}
	if !part.isExpr {
		*out = append(*out, Token{Type: TokenText, Value: part.text, Pos: pos})
		return stack
	}
	mark := len(*out)
	bal := &balance{}
	*out = append(*out, Token{Type: TokenOp, Value: "(", Pos: l.posAt(part.frm)})
	stack = append(stack, lexTask{kind: taskClose, mark: mark, part: part, bal: bal})
	return append(stack, lexTask{kind: taskRange, i: part.frm, to: part.to, bal: bal})
}

func (l *Lexer) matchOperator(i, to int) (string, bool) {
	for _, op := range operators {
		opRunes := []rune(op)
		if i+len(opRunes) > to {
			continue
		}
		match := true
		for k, r := range opRunes {
			if l.chars[i+k] != r {
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

func (l *Lexer) lexRaw(start, to int, out *[]Token) int {
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
			*out = append(*out, Token{Type: TokenText, Value: string(buf), Pos: pos})
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
func (l *Lexer) scanQuoted(start, to int) ([]textPart, int) {
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

func (l *Lexer) readEscape(i, to int) (string, int) {
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
func (l *Lexer) matchBrace(i, to int) int {
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

func (l *Lexer) skipRaw(j, to int) int {
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

func Tokenize(source string) []Token {
	return NewLexer(source).Tokenize()
}
