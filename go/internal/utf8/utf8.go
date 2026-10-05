// Package utf8 is SEL's UTF-8 codec (spec/SPEC.md §2): validation with SEL's
// diagnostics, code point counts and ASCII case for names.
package utf8

import (
	"bytes"
	"encoding/hex"
	"unicode/utf8"
	"unsafe"
)

type Pos struct {
	Line   int
	Col    int
	Offset int
}

type FailFunc func(code string, msg string, pos Pos)

// ValidateText refuses text that is not well-formed UTF-8 (E_UTF8). Go's
// validator is the standard's: an encoded surrogate, an overlong form or a code
// point past U+10FFFF is ill-formed, so no separate surrogate check is needed.
func ValidateText(s string, pos Pos, fail FailFunc) {
	if !utf8.ValidString(s) {
		fail("E_UTF8", "invalid UTF-8 byte sequence", pos)
	}
}

func BytesToHex(data []byte) string {
	return hex.EncodeToString(data)
}

func BytesCompare(a, b []byte) int {
	return bytes.Compare(a, b)
}

func AsciiUpper(s string) string {
	// Already upper case (every identifier the lexer produces, every Lookup of a
	// built-in by its own name): no copy; otherwise one allocation.
	i := 0
	for ; i < len(s); i++ {
		if 'a' <= s[i] && s[i] <= 'z' {
			break
		}
	}
	if i == len(s) {
		return s
	}
	b := make([]byte, len(s))
	copy(b, s)
	for ; i < len(b); i++ {
		if c := b[i]; 'a' <= c && c <= 'z' {
			b[i] = c - 32
		}
	}
	return unsafe.String(&b[0], len(b)) // b is not touched again
}

func AsciiLower(s string) string {
	// No upper-case ASCII letter: no copy; otherwise one allocation.
	i := 0
	for ; i < len(s); i++ {
		if 'A' <= s[i] && s[i] <= 'Z' {
			break
		}
	}
	if i == len(s) {
		return s
	}
	b := make([]byte, len(s))
	copy(b, s)
	for ; i < len(b); i++ {
		if c := b[i]; 'A' <= c && c <= 'Z' {
			b[i] = c + 32
		}
	}
	return unsafe.String(&b[0], len(b)) // b is not touched again
}
