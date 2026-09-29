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
