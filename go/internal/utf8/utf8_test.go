package utf8

import (
	"strings"
	"testing"
)

func diag(data string) (code, msg string) {
	defer func() {
		if r := recover(); r != nil {
			s, ok := r.(string)
			if !ok {
				panic(r)
			}
			parts := strings.SplitN(s, "|", 2)
			code, msg = parts[0], parts[1]
		}
	}()
	DecodeUtf8Diagnostic([]byte(data), Pos{}, func(code, msg string, pos Pos) { panic(code + "|" + msg) })
	return "", ""
}

// The boundary the review flagged as an off-by-one (`i+need >= n`): a sequence
// whose last byte is the last byte of the input is COMPLETE, and one byte short
// of it is truncated. The check is right; these pin it so it stays that way.
func TestDiagnosticSequenceEndingAtEndOfInput(t *testing.T) {
	// A complete 2-, 3- and 4-byte sequence at the very end, after an invalid byte
	// that is what actually gets reported.
	for _, tail := range []string{"\xc5\x82", "\xe2\x82\xac", "\xf0\x9f\x98\x80"} {
		code, msg := diag("\xff" + tail)
		if code != "E_UTF8" || !strings.Contains(msg, "invalid start byte 0xff at byte 0") {
			t.Errorf("%q: got %s %s", tail, code, msg)
		}
		// The complete sequence before the bad byte is not mistaken for truncation.
		code, msg = diag(tail + "\xff")
		if code != "E_UTF8" || !strings.Contains(msg, "invalid start byte 0xff at byte "+itoa(len(tail))) {
			t.Errorf("%q then 0xff: got %s %s", tail, code, msg)
		}
		// One byte short at the end of input is truncation, at the sequence start.
		code, msg = diag("a" + tail[:len(tail)-1])
		if code != "E_UTF8" || !strings.Contains(msg, "truncated sequence at byte 1") {
			t.Errorf("truncated %q: got %s %s", tail, code, msg)
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	s := ""
	for n > 0 {
		s = string(rune('0'+n%10)) + s
		n /= 10
	}
	return s
}

// GO-P23: the ASCII case mappers copy once, and not at all when nothing changes.
func TestAsciiCaseMappersMatchAByteWiseReference(t *testing.T) {
	ref := func(s string, lo, hi byte, d int) string {
		b := []byte(s)
		for i, c := range b {
			if lo <= c && c <= hi {
				b[i] = byte(int(c) + d)
			}
		}
		return string(b)
	}
	for _, s := range []string{"", "a", "A", "abc", "ABC", "aBc", "é😀zZ", "İi", "STRAßE", "x\x00Y", "z{`@[Aa"} {
		if got, want := AsciiUpper(s), ref(s, 'a', 'z', -32); got != want {
			t.Errorf("AsciiUpper(%q) = %q, want %q", s, got, want)
		}
		if got, want := AsciiLower(s), ref(s, 'A', 'Z', 32); got != want {
			t.Errorf("AsciiLower(%q) = %q, want %q", s, got, want)
		}
	}
	if allocs := testing.AllocsPerRun(50, func() { _ = AsciiLower("already lower é") }); allocs != 0 {
		t.Errorf("AsciiLower on text with nothing to change allocated %v times", allocs)
	}
	if allocs := testing.AllocsPerRun(50, func() { _ = AsciiUpper("MIXed Case") }); allocs != 1 {
		t.Errorf("AsciiUpper allocated %v times, want 1", allocs)
	}
}
