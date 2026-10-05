package sel

import (
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/internal/utf8"
)

// GO-P6: posAt answers from a cursor on the previous line before it searches; the
// answer must be the naive line/column for every offset in any order.
func TestPosAtCursorMatchesTheNaiveCount(t *testing.T) {
	src := "ab\n\n  cdé\n😀x\n" + strings.Repeat("line of text\n", 50) + "tail"
	l := newLexer(src)
	runes := []rune(src)
	naive := func(off int) Pos {
		line, col := 1, 1
		for i := 0; i < off; i++ {
			if runes[i] == '\n' {
				line++
				col = 1
			} else {
				col++
			}
		}
		return Pos{Line: line, Col: col, Offset: off}
	}
	s := lcg(5)
	order := []int{}
	for i := 0; i <= len(runes); i++ {
		order = append(order, i)
	}
	for i := len(runes); i >= 0; i-- {
		order = append(order, i)
	}
	for i := 0; i < 800; i++ {
		order = append(order, int(s.next()%uint64(len(runes)+1)))
	}
	for _, off := range order {
		if got, want := l.posAt(off), naive(off); got != want {
			t.Fatalf("posAt(%d) = %+v, want %+v", off, got, want)
		}
	}
}

func TestAsciiUpperKeepsAnUpperCaseStringAndUpperCasesTheRest(t *testing.T) {
	for in, want := range map[string]string{
		"": "", "ABC_1": "ABC_1", "abc": "ABC", "aBc_9z": "ABC_9Z", "é": "é", "éa": "éA", "Zażółć": "ZAżółć",
	} {
		if got := utf8.AsciiUpper(in); got != want {
			t.Errorf("AsciiUpper(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestTokenizeUpperCasesIdentifiersAndKeepsOperators(t *testing.T) {
	toks := tokenize("abc_1 += Foo??bar ?? x<=y $== z .> f")
	var got []string
	for _, tk := range toks {
		got = append(got, tk.Value)
	}
	if want := "ABC_1 += FOO ?? BAR ?? X <= Y $== Z .> F "; strings.Join(got, " ") != want {
		t.Errorf("tokens %q, want %q", strings.Join(got, " "), want)
	}
}
