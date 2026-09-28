// Tokeniser. See spec/grammar.md.

package sel

import (
	"fmt"
	"regexp"
	"strconv"

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
}

func NewLexer(source string) *Lexer {
	chars := utf8.ToCodePoints(source, Pos{}, fail)
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

func (l *Lexer) lexRange(frm, to int, out *[]Token) {
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
			i = l.lexQuoted(i, to, out)
			continue
		}
		if c == '\'' {
			i = l.lexRaw(i, to, out)
			continue
		}

		if op, ok := l.matchOperator(i, to); ok {
			*out = append(*out, Token{Type: TokenOp, Value: op, Pos: pos})
			i += len([]rune(op))
			continue
		}

		fail("E_SYNTAX", fmt.Sprintf("unexpected character %q", c), pos)
	}
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

func (l *Lexer) lexQuoted(start, to int, out *[]Token) int {
	pos := l.posAt(start)
	var parts []textPart
	var buf []rune
	i := start + 1

	for i < to {
		c := l.chars[i]

		if c == '"' {
			parts = append(parts, textPart{isExpr: false, text: string(buf)})
			l.emitParts(parts, pos, out)
			return i + 1
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
	return to
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

func (l *Lexer) matchBrace(i, to int) int {
	pos := l.posAt(i)
	depth := 0
	j := i
	for j < to {
		c := l.chars[j]
		if c == '"' {
			j = l.skipQuoted(j, to)
			continue
		}
		if c == '\'' {
			j = l.skipRaw(j, to)
			continue
		}
		if c == '{' {
			depth++
			j++
			continue
		}
		if c == '}' {
			depth--
			j++
			if depth == 0 {
				return j
			}
			continue
		}
		if c == '#' {
			for j < to && l.chars[j] != '\n' {
				j++
			}
			continue
		}
		j++
	}
	fail("E_UNTERMINATED", "unterminated { in text literal", pos)
	return to
}

func (l *Lexer) skipQuoted(j, to int) int {
	pos := l.posAt(j)
	j++
	for j < to {
		c := l.chars[j]
		if c == '\\' {
			j += 2
			continue
		}
		if c == '"' {
			return j + 1
		}
		if c == '{' {
			j = l.matchBrace(j, to)
			continue
		}
		j++
	}
	fail("E_UNTERMINATED", "unterminated text literal", pos)
	return to
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

func (l *Lexer) emitParts(parts []textPart, pos Pos, out *[]Token) {
	if len(parts) == 1 {
		*out = append(*out, Token{Type: TokenText, Value: parts[0].text, Pos: pos})
		return
	}
	*out = append(*out, Token{Type: TokenOp, Value: "(", Pos: pos})
	for k, part := range parts {
		if k > 0 {
			*out = append(*out, Token{Type: TokenOp, Value: "&", Pos: pos})
		}
		if !part.isExpr {
			*out = append(*out, Token{Type: TokenText, Value: part.text, Pos: pos})
		} else {
			mark := len(*out)
			*out = append(*out, Token{Type: TokenOp, Value: "(", Pos: l.posAt(part.frm)})
			l.lexRange(part.frm, part.to, out)
			if len(*out) == mark+1 {
				fail("E_SYNTAX", "empty interpolation {}", l.posAt(part.frm))
			}
			*out = append(*out, Token{Type: TokenOp, Value: ")", Pos: l.posAt(part.to)})
		}
	}
	*out = append(*out, Token{Type: TokenOp, Value: ")", Pos: pos})
}

func Tokenize(source string) []Token {
	return NewLexer(source).Tokenize()
}
