// Package utf8 is SEL's UTF-8 codec (spec/SPEC.md §2): validation with SEL's
// diagnostics, code point counts and ASCII case for names.
package utf8

import (
	"bytes"
	"encoding/hex"
	"fmt"
	"unicode/utf8"
	"unsafe"
)

type Pos struct {
	Line   int
	Col    int
	Offset int
}

type FailFunc func(code string, msg string, pos Pos)

func ValidateText(s string, pos Pos, fail FailFunc) {
	for i, r := range s {
		if r == utf8.RuneError {
			// Check if it was an actual U+FFFD or invalid utf-8
			_, size := utf8.DecodeRuneInString(s[i:])
			if size == 1 {
				fail("E_UTF8", "invalid UTF-8 byte sequence", pos)
			}
		}
		if 0xD800 <= r && r <= 0xDFFF {
			which := "high"
			if r > 0xDBFF {
				which = "low"
			}
			fail("E_UTF8", fmt.Sprintf("unpaired %s surrogate", which), pos)
		}
	}
}

func ToCodePoints(s string, pos Pos, fail FailFunc) []rune {
	runes := make([]rune, 0, len(s))
	for i, r := range s {
		if r == utf8.RuneError {
			_, size := utf8.DecodeRuneInString(s[i:])
			if size == 1 {
				fail("E_UTF8", "invalid UTF-8 byte sequence", pos)
			}
		}
		if 0xD800 <= r && r <= 0xDFFF {
			which := "high"
			if r > 0xDBFF {
				which = "low"
			}
			fail("E_UTF8", fmt.Sprintf("unpaired %s surrogate", which), pos)
		}
		runes = append(runes, r)
	}
	return runes
}

func DecodeUtf8(data []byte, pos Pos, fail FailFunc) string {
	if utf8.Valid(data) {
		return string(data)
	}
	DecodeUtf8Diagnostic(data, pos, fail)
	return ""
}

func DecodeUtf8Diagnostic(data []byte, pos Pos, fail FailFunc) {
	n := len(data)
	i := 0
	for i < n {
		b := data[i]
		if b < 0x80 {
			i++
			continue
		}
		var need int
		var lo, hi byte
		if 0xC2 <= b && b <= 0xDF {
			need, lo, hi = 1, 0x80, 0xBF
		} else if b == 0xE0 {
			need, lo, hi = 2, 0xA0, 0xBF // reject overlong 3-byte
		} else if 0xE1 <= b && b <= 0xEC {
			need, lo, hi = 2, 0x80, 0xBF
		} else if b == 0xED {
			need, lo, hi = 2, 0x80, 0x9F // reject surrogates
		} else if 0xEE <= b && b <= 0xEF {
			need, lo, hi = 2, 0x80, 0xBF
		} else if b == 0xF0 {
			need, lo, hi = 3, 0x90, 0xBF // reject overlong 4-byte
		} else if 0xF1 <= b && b <= 0xF3 {
			need, lo, hi = 3, 0x80, 0xBF
		} else if b == 0xF4 {
			need, lo, hi = 3, 0x80, 0x8F // cap at U+10FFFF
		} else {
			fail("E_UTF8", fmt.Sprintf("invalid start byte 0x%x at byte %d", b, i), pos)
			return
		}

		if i+need >= n {
			fail("E_UTF8", fmt.Sprintf("truncated sequence at byte %d", i), pos)
			return
		}
		for k := 1; k <= need; k++ {
			c := data[i+k]
			loK := lo
			if k != 1 {
				loK = 0x80
			}
			hiK := hi
			if k != 1 {
				hiK = 0xBF
			}
			if c < loK || c > hiK {
				fail("E_UTF8", fmt.Sprintf("invalid continuation byte at byte %d", i+k), pos)
				return
			}
		}
		i += need + 1
	}
	fail("E_UTF8", "invalid UTF-8 byte sequence", pos)
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
