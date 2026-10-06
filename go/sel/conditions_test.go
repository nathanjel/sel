package sel

import (
	"strings"
	"testing"
)

// A condition is read without the BOOL a comparison or a logical operator
// yields (evalCond). These tests hold that to "cannot be told apart": no value
// per element, the same error and the same E_DEPTH where they always were, and
// the depth back where it was when a condition's error is caught.

func TestConditionsBuildNoValuePerElement(t *testing.T) {
	// Nothing is kept, so a walk costs what it costs whatever the row count;
	// before, every row cost the BOOL its condition yielded, and one more for
	// each comparison under an AND, OR or NOT.
	for _, src := range []string{
		`COUNT(FILTER(L, _["s"] $== "nope"))`,
		`COUNT(FILTER(L, _["n"] > 5000 OR NOT (_["s"] $!= "nope")))`,
		`COUNT(FILTER(L, _["n"] >= 0 AND _["s"] $== "nope"))`,
		`COUNT(FILTER(L, _["n"] EQL -1 OR _["s"] IN "nope"))`,
		`ALL(L, _["n"] < 1000)`,
		`ANY(L, _["s"] $== "nope")`,
	} {
		allocs := func(n int) float64 {
			prog := MustCompile(src)
			root := ctxWith("L", textRows(n, 7))
			if _, err := prog.Run(root); err != nil {
				t.Fatalf("%s: %v", src, err)
			}
			return testing.AllocsPerRun(5, func() { prog.Run(root) })
		}
		// A cache filled on the way (a field slot, a pooled buffer) may cost one
		// run an allocation; a value per row costs 500.
		if small, large := allocs(500), allocs(1000); large > small+2 {
			t.Errorf("%s: %v allocations over 500 rows, %v over 1000; want the same", src, small, large)
		}
	}
}

func TestConditionDepth(t *testing.T) {
	// Each answer is the evaluator's as it was before conditions stopped
	// building values (a6ab7f8): E_DEPTH on the same node, or none, and the
	// same error where a condition is not a BOOL.
	as := func(n int) string { return strings.Repeat("a", n) }
	for _, c := range []struct {
		src  string
		want string
	}{
		// A condition's operator, operands and logic at the cap, inside a FILTER,
		// an ALL, an ANY, an IF and a COND.
		{chain(`COUNT(FILTER(LIST(1,2), 1 == 1 AND 2 == 2))`, 195), `t"2` + as(195) + `"`},
		{chain(`COUNT(FILTER(LIST(1,2), 1 == 1 AND 2 == 2))`, 196), "!E_DEPTH@1:25"},
		{chain(`COUNT(FILTER(LIST(1,2), 1 == 1 AND 2 == 2))`, 197), "!E_DEPTH@1:19"},
		{chain(`COUNT(FILTER(LIST(1,2), 1 == 1 AND 2 == 2))`, 198), "!E_DEPTH@1:14"},
		{chain(`COUNT(FILTER(LIST(1,2), NOT 1 == 2))`, 196), "!E_DEPTH@1:29"},
		{chain(`COUNT(FILTER(LIST(1,2), NOT 1 == 2))`, 197), "!E_DEPTH@1:19"},
		{chain(`ALL(LIST(1,2), 1 <= 2)`, 198), "!E_DEPTH@1:10"},
		{chain(`ANY(LIST(1,2), "b" $< "a" OR 1 == 1)`, 197), "!E_DEPTH@1:16"},
		{chain(`ANY(LIST(1,2), "b" $< "a" OR 1 == 1)`, 198), "!E_DEPTH@1:10"},
		{chain(`IF(1 == 1, "y", "n")`, 197), `t"y` + as(197) + `"`},
		{chain(`IF(1 == 1, "y", "n")`, 198), "!E_DEPTH@1:4"},
		{chain(`IF(1 == 1, "y", "n")`, 199), "!E_DEPTH@1:6"},
		{chain(`COND(1 == 2, "a", 2 == 2, "b", "c")`, 198), "!E_DEPTH@1:6"},
		// A condition that raises inside a FILTER, caught by ??: the depth is
		// back where it was, so the fallback has the whole budget.
		{`FILTER(LIST(RECORD("a",1)), _["missing"] == 1) ?? (` + strings.Repeat(`"a" & `, 198) + `"a")`, `t"` + as(199) + `"`},
		{`FILTER(LIST(RECORD("a",1)), 1 == 1 AND NOT (_["missing"] == 1)) ?? (` + strings.Repeat(`"a" & `, 198) + `"a")`, `t"` + as(199) + `"`},
		{`FILTER(LIST(RECORD("a",1)), 1 == 1 AND NOT (_["missing"] == 1)) ?? (` + strings.Repeat(`"a" & `, 199) + `"a")`, "!E_DEPTH@1:69"},
		// A condition that is not a BOOL, or an operand that is not a number.
		{`COUNT(FILTER(LIST(1,2), _ < "x"))`, "!E_NOT_NUM@1:29"},
		{`COUNT(FILTER(LIST(1,2), 1 + 1))`, "!E_NOT_BOOL@1:27"},
		{`COUNT(FILTER(LIST(1,2), NOT 5))`, "!E_NOT_BOOL@1:29"},
		{`IF("x", 1, 2)`, "!E_NOT_BOOL@1:4"},
		{`TRUE AND 5`, "!E_NOT_BOOL@1:10"},
		{`5 OR TRUE`, "!E_NOT_BOOL@1:1"},
	} {
		if got := runOnce(c.src, NewNone()); got != c.want {
			t.Errorf("%.60s…: got %.80s, want %.80s", c.src, got, c.want)
		}
	}
}
