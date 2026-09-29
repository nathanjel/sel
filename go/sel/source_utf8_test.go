package sel

import "testing"

// SPEC §2: invalid UTF-8 in source is E_UTF8 at the first invalid unit, counted
// in code points of the valid prefix, LF the only line end.
func TestInvalidSourceUTF8Position(t *testing.T) {
	tests := []struct {
		name              string
		src               string
		line, col, offset int
	}{
		{"bare byte", "\xff", 1, 1, 0},
		{"inside a literal", "\"a\xffb\"", 1, 3, 2},
		{"second line", "1 +\n \"a\xffb\"", 2, 4, 7},
		{"after a multibyte prefix", "\"\xc5\x82\xff\"", 1, 3, 2},
		{"truncated at end of input", "\"\xe2\x82", 1, 2, 1},
		{"overlong", "\"\xc0\x80\"", 1, 2, 1},
		{"overlong three byte", "\"\xe0\x80\x80\"", 1, 2, 1},
		{"encoded surrogate", "\"\xed\xa0\x80\"", 1, 2, 1},
		{"above U+10FFFF", "\"\xf4\x90\x80\x80\"", 1, 2, 1},
		{"CR is not a line end", "1 +\r\n\xff", 2, 1, 5},
		{"lone CR is not a line end", "1\r\xff", 1, 3, 2},
	}
	for _, tt := range tests {
		_, err := Compile(tt.src)
		se, ok := err.(*SelError)
		if !ok {
			t.Fatalf("%s: want E_UTF8, got %v", tt.name, err)
		}
		if se.Code != "E_UTF8" || se.Line() != tt.line || se.Col() != tt.col || se.Pos.Offset != tt.offset {
			t.Errorf("%s: got %s at %d:%d offset %d, want E_UTF8 at %d:%d offset %d",
				tt.name, se.Code, se.Line(), se.Col(), se.Pos.Offset, tt.line, tt.col, tt.offset)
		}
	}
}

func TestValidMultibyteSourceIsUntouched(t *testing.T) {
	// A real U+FFFD is width 3 and must not be taken for a decoding failure.
	for _, src := range []string{"LEN(\"\xef\xbf\xbd\")", "LEN(\"\xc5\x82\r\n\")"} {
		if _, err := Eval(src, nil); err != nil {
			t.Errorf("%q: %v", src, err)
		}
	}
	v, _ := Eval("LEN(\"a\r\nb\")", nil)
	if v.Dump() != "t\"4\"" {
		t.Errorf("CRLF inside a literal is part of the literal: got %s", v.Dump())
	}
}
