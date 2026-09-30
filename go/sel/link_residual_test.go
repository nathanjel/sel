package sel

import (
	"fmt"
	"strings"
	"sync"
	"testing"
)

// GO-P1: `equality AND residual` hashes on the equality. The reference for every
// case is the same join with the equality hidden inside IF(…), which makes the
// leftmost conjunct a call and so keeps the nested loop: the answers (values, key
// order, and the ERROR raised) must be identical.
func hideEq(eq string) string { return "IF(" + eq + ", TRUE, FALSE)" }

func dumpCode(dump string) string {
	if i := strings.Index(dump, "@"); strings.HasPrefix(dump, "!") && i > 0 {
		return dump[:i]
	}
	return dump
}

func linkCtxFrom(left, right []string) func() *Value {
	mk := func(rows []string, tag string) *Value {
		items := make([]*Value, len(rows))
		for i, r := range rows {
			v, err := Eval("RECORD("+r+")", NewNone())
			if err != nil {
				panic(err)
			}
			items[i] = v
			_ = tag
		}
		return NewListOwned(items)
	}
	return func() *Value { return ctxWith("L", mk(left, "l"), "R", mk(right, "r")) }
}

func TestLinkEqualityAndResidualMatchesTheNestedLoop(t *testing.T) {
	type data struct {
		name        string
		left, right []string
	}
	good := data{"good",
		[]string{`"a", 1, "c", 1`, `"a", 2, "c", 5`, `"a", 1, "c", 9`, `"a", "2.0", "c", 3`, `"a", 3, "c", 4`},
		[]string{`"a", 1, "c", 2`, `"a", 1, "c", 10`, `"a", 2, "c", 4`, `"a", "02", "c", 6`, `"a", 9, "c", 1`}}
	dups := data{"dups",
		[]string{`"a", "x", "c", 1`, `"a", "x", "c", 2`, `"a", "y", "c", 3`},
		[]string{`"a", "x", "c", 5`, `"a", "x", "c", 0`, `"a", "x", "c", 1`, `"a", "z", "c", 2`}}
	nullKey := data{"null",
		[]string{`"a", 1, "c", 1`, `"a", NULL, "c", 2`},
		[]string{`"a", 1, "c", 2`, `"a", NULL, "c", 3`}}
	badKey := data{"bad",
		[]string{`"a", 1, "c", 1`, `"a", "abc", "c", 2`},
		[]string{`"a", 1, "c", 2`, `"a", 2, "c", 3`}}
	badRight := data{"badright",
		[]string{`"a", 1, "c", 1`, `"a", 2, "c", 2`},
		[]string{`"a", 1, "c", 2`, `"a", "zz", "c", 3`}}
	missing := data{"missing",
		[]string{`"a", 1, "c", 1`, `"c", 2`},
		[]string{`"a", 1, "c", 2`, `"a", 2, "c", 3`}}
	residualBad := data{"residualbad",
		[]string{`"a", 1, "c", 1`, `"a", 1, "c", "oops"`},
		[]string{`"a", 1, "c", 2`, `"a", 1, "c", 3`}}
	empty := data{"emptyright", []string{`"a", 1, "c", 1`}, nil}
	all := []data{good, dups, nullKey, badKey, badRight, missing, residualBad, empty}

	type pred struct{ eq, rest string }
	preds := []pred{
		{`l["a"] == r["a"]`, `l["c"] < r["c"]`},
		{`l["a"] == r["a"]`, `l["c"] < r["c"] AND r["c"] > 1`},
		{`r["a"] == l["a"]`, `l["c"] <= r["c"]`},
		{`l["a"] $== r["a"]`, `l["c"] != r["c"]`},
		{`l["a"] == r["a"]`, `l["c"] == r["c"]`},
		{`l["a"] == r["a"]`, `TRUE`},
		{`l["a"] == r["a"]`, `l["c"] + 0`}, // a non-BOOL residual raises E_NOT_BOOL
		{`l["a"] == r["a"]`, `FALSE`},
		{`l["a"] == r["a"]`, `(l["c"] > 0 AND r["c"] > 0)`},
	}
	for _, d := range all {
		for _, p := range preds {
			for _, form := range []string{"LINK", "LINK_LEFT"} {
				mk := func(eq string) string {
					return fmt.Sprintf(`JOIN(MAP(%s(L, R, l, r, %s AND %s), _["l"]["c"] & ":" & _["r"]["c"]), ",")`, form, eq, p.rest)
				}
				got := runOnce(mk(p.eq), linkCtxFrom(d.left, d.right)())
				want := runOnce(mk(hideEq(p.eq)), linkCtxFrom(d.left, d.right)())
				if dumpCode(got) != dumpCode(want) {
					t.Errorf("%s %s %s AND %s:\n fast   %s\n nested %s", d.name, form, p.eq, p.rest, got, want)
				}
			}
		}
	}
}

func TestLinkResidualThreeArgumentFormAndOrder(t *testing.T) {
	ctx := func() *Value { return ctxWith("L", joinRows(40, 4, 1), "R", joinRows(40, 4, 2)) }
	fast := runOnce(`JOIN(MAP(LINK(L, R, L["a"] == R["a"] AND L["c"] < R["c"]), _["L"]["c"] & ":" & _["R"]["c"]), ",")`, ctx())
	slow := runOnce(`JOIN(MAP(LINK(L, R, IF(L["a"] == R["a"], TRUE, FALSE) AND L["c"] < R["c"]), _["L"]["c"] & ":" & _["R"]["c"]), ",")`, ctx())
	if fast != slow || !strings.HasPrefix(fast, `t"`) || fast == `t""` {
		t.Errorf("three-argument LINK\n fast %.200s\n slow %.200s", fast, slow)
	}
}

// A key expression that assigns or calls a host function may be observed if it is
// evaluated once per row instead of once per pair, so those joins keep the nested loop.
func TestLinkResidualRefusesEffectfulKeys(t *testing.T) {
	calls := 0
	RegisterFunction("PERF_TAP", 1, 1, func(a *Args) *Value {
		calls++
		return a.Val(0)
	})
	ctx := linkCtxFrom([]string{`"a", 1, "c", 1`, `"a", 1, "c", 2`}, []string{`"a", 1, "c", 3`, `"a", 1, "c", 4`})
	got := runOnce(`COUNT(LINK(L, R, l, r, PERF_TAP(l["a"]) == r["a"] AND l["c"] < r["c"]))`, ctx())
	if got != `t"4"` {
		t.Errorf("got %s", got)
	}
	if calls != 4 { // once per pair: the nested loop, not the hash path (which would be 2)
		t.Errorf("a host function in the key ran %d times, want 4 (once per pair)", calls)
	}
}

// GO-P4: hoisting the right alias records is unobservable (a binder cannot be
// assigned, so nothing can write into the alias record; a host function is treated
// as possibly impure and keeps the old per-pair records). The reference is the same predicate behind a host function, which
// is not pure and so keeps the alias record fresh for every pair.
var registerTapOnce sync.Once

func registerTap() {
	registerTapOnce.Do(func() {
		RegisterFunction("PERF_ID", 1, 1, func(a *Args) *Value { return a.Val(0) })
	})
}

func TestLinkNonEquiHoistIsUnobservable(t *testing.T) {
	registerTap()
	ctx := func() *Value { return ctxWith("L", joinRows(30, 5, 1), "R", joinRows(30, 5, 2)) }
	for _, pred := range []string{`X["k"] < Y["k"]`, `TRUE`, `X["a"] != Y["a"] AND X["c"] <= Y["c"]`, `Y["k"] > 500`} {
		for _, form := range []string{"LINK", "LINK_LEFT"} {
			mk := func(p string) string {
				return fmt.Sprintf(`JOIN(MAP(%s(L, R, X, Y, %s), _["X"]["c"] & ":" & _["Y"]["c"]), ",")`, form, p)
			}
			got := runOnce(mk(pred), ctx())
			want := runOnce(mk("PERF_ID("+pred+")"), ctx())
			if got != want || got == `t""` {
				t.Errorf("%s %s\n hoisted %.120s\n fresh   %.120s", form, pred, got, want)
			}
		}
	}
}
