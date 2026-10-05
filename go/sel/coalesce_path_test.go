package sel

import (
	"fmt"
	"testing"
)

// GO-P7: `??` / `???` over a plain path resolves it without raising. The
// reference for each expression is the same path behind IF(TRUE, …), which is not
// a plain path and so takes the raising-and-recovering route.
func TestCoalesceOverAPlainPathMatchesTheRecoverRoute(t *testing.T) {
	ctx := func() *Value {
		inner, _ := Eval(`RECORD("b", 5, "n", NULL, "e", "", "s", "txt", "l", LIST(1, 2), "deep", RECORD("x", RECORD("y", 7)))`, NewNone())
		c := ctxWith("A", inner, "T", NewText("hello"), "N", NewNull(), "E", NewText(""), "L", newListOwned([]*Value{NewInt(1), rec("k", 9)}))
		return c
	}
	paths := []string{
		`A["b"]`, `A["zz"]`, `A["n"]`, `A["e"]`, `A["s"]`, `A["l"]`, `A["l"]["1"]`, `A["l"]["3"]`, `A["deep"]["x"]["y"]`,
		`A["deep"]["x"]["z"]`, `A["deep"]["q"]["y"]`, `A["b"]["c"]`, `A["s"]["c"]`, `A`, `NOPE`, `NOPE["a"]`, `T`, `T["x"]`,
		`N`, `N["a"]`, `E`, `L`, `L["1"]`, `L["2"]["k"]`, `L["2"]["j"]`, `L["9"]["k"]`,
		`A["b"]["c"]["d"]["e"]["f"]["g"]["h"]["i"]["j"]`, // deeper than the fast walk's chain
	}
	for _, p := range paths {
		for _, op := range []string{"??", "???"} {
			direct := fmt.Sprintf(`%s %s "fallback"`, p, op)
			ref := fmt.Sprintf(`IF(TRUE, %s, NULL) %s "fallback"`, p, op)
			if got, want := runOnce(direct, ctx()), runOnce(ref, ctx()); got != want {
				t.Errorf("%s\n got  %s\n want %s", direct, got, want)
			}
		}
	}
	// Inside aggregates, where the binder is a frame variable and the slot cache is hot.
	rows := func() *Value { return ctxWith("R", joinRows(300, 4, 3)) }
	for _, src := range []string{
		`SUM(R, _["a"] ?? 100)`, `SUM(R, _["zz"] ?? 1)`, `COUNT(MAP(R, _["zz"]["y"] ?? _["a"]))`, `SUM(R, (_["nope"] ?? _["c"]) ??? 0)`,
	} {
		ref := fmt.Sprintf("IF(TRUE, 0, 0) + %s", src)
		_ = ref
		if got := runOnce(src, rows()); got[0] == '!' {
			t.Errorf("%s: %s", src, got)
		}
	}
}

// A chain that would exceed the evaluation depth must still raise E_DEPTH at the
// node it always did, not answer from the fast walk: for each start depth the
// evaluator may already be nested to, the walk and the raising route agree on the
// outcome, at and around the cap.
func TestCoalesceKeepsTheDepthCapForAPlainPath(t *testing.T) {
	root := ctxWith("A", rec("x", 5))
	run := func(src string, start int, fast bool) string {
		coalescePathFast = fast
		defer func() { coalescePathFast = true }()
		c := newContext(root)
		c.depth = start
		return outcome(func() *Value { return evalNode(MustCompile(src).AST(), c) })
	}
	for _, src := range []string{`A["x"] ?? 1`, `A["zz"] ?? 1`, `A["x"]["y"] ?? 1`, `A ?? 1`, `A["x"] ??? 1`} {
		seenDepth := false
		for start := maxDepth - 6; start <= maxDepth; start++ {
			got, want := run(src, start, true), run(src, start, false)
			if got != want {
				t.Errorf("%s at start depth %d: walk %s, raising route %s", src, start, got, want)
			}
			seenDepth = seenDepth || len(want) > 8 && want[:8] == "!E_DEPTH"
		}
		if !seenDepth {
			t.Errorf("%s never reached the cap in the range tried", src)
		}
	}
}
