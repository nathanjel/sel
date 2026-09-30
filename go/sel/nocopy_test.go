package sel

import (
	"fmt"
	"strings"
	"testing"
)

// GO-REG-1: a FILTER whose parent only reads or itself copies its rows keeps them
// aliased. These tests hold the elision to "cannot be told apart": every program gives
// the same outcome with it on and off, it is declined wherever a value could change
// while the rows are read, and it still refuses the rows the copy would have refused.

func bothWays(t *testing.T, src string, ctx func() *Value) string {
	t.Helper()
	disableNoCopy = true
	off := runOnce(src, ctx())
	disableNoCopy = false
	on := runOnce(src, ctx())
	if on != off {
		t.Errorf("%s\n elided:   %.200s\n copying:  %.200s", src, on, off)
	}
	return on
}

func nestedRows(depth int) *Value {
	v := NewInt(1)
	for i := 1; i < depth; i++ {
		v = NewListOwned([]*Value{v})
	}
	return NewListOwned([]*Value{v})
}

func TestNoCopyGivesTheSameAnswerWithAndWithout(t *testing.T) {
	ctx := func() *Value { return ctxWith("L", joinRows(300, 7, 3), "NUMS", intList(200, 5)) }
	for _, src := range []string{
		`COUNT(FILTER(L, _["a"] > 3))`,
		`SUM(FILTER(L, _["a"] > 3), _["c"])`,
		`ALL(FILTER(L, _["a"] > 3), _["k"] >= 0)`,
		`ANY(FILTER(L, _["a"] > 3), _["c"] > 290)`,
		`JOIN(MAP(FILTER(L, _["a"] > 3), _["c"]), ",")`,
		`JOIN(MAP(FILTER(L, _["a"] > 3), _K), ",")`,
		`COUNT(MAP(FILTER(L, _["a"] > 3), _))`,
		`JOIN(MAP(TAKE(SORT_BY(FILTER(L, _["a"] > 3), _["k"]), 5), _["c"]), ",")`,
		`JOIN(MAP(TOP_BY(FILTER(L, _["a"] > 3), _["k"], 5), _["c"]), ",")`,
		`JOIN(MAP(TOP_BY(FILTER(L, _["a"] > 3), _["k"], 5, "DESC"), _["c"]), ",")`,
		`JOIN(MAP(TOP(FILTER(NUMS, _ > 400000), 7), _), ",")`,
		`JOIN(TOP_DESC(FILTER(NUMS, _ > 400000), 4), ",")`,
		`JOIN(SORT(FILTER(NUMS, _ > 500000)), ",")`,
		`JOIN(SORT_DESC(FILTER(NUMS, _ > 500000)), ",")`,
		`SUM(FILTER(NUMS, _ > 400000), _)`,
		`COUNT(FILTER(FILTER(L, _["a"] > 1), _["a"] < 5))`,
		`COUNT(FILTER(L, FALSE))`,
		`COUNT(FILTER(L, _["nope"] > 1))`,
		`COUNT(FILTER(L, _["a"]))`,
	} {
		bothWays(t, src, ctx)
	}
}

func TestNoCopyIsDeclinedWhereAValueCouldChange(t *testing.T) {
	parse := func(src string) *Node { return MustCompile(src).AST() }
	find := func(n *Node, consumer string) *Node {
		var hit *Node
		var walk func(*Node)
		walk = func(n *Node) {
			if n == nil || hit != nil {
				return
			}
			if n.T == NodeCall && n.S == consumer {
				hit = n
				return
			}
			walk(n.L)
			walk(n.R)
			for _, it := range n.Items {
				walk(it)
			}
		}
		walk(n)
		return hit
	}
	cases := []struct {
		src, consumer string
		want          bool
	}{
		{`COUNT(FILTER(L, _["a"] > 1))`, "COUNT", true},
		{`SUM(FILTER(L, _["a"] > 1), _["c"])`, "SUM", true},
		{`MAP(FILTER(L, _["a"] > 1), _["c"])`, "MAP", true},
		{`TOP_BY(FILTER(L, _["a"] > 1), _["k"], 5)`, "TOP_BY", true},
		{`SORT(FILTER(NUMS, _ > 1))`, "SORT", true},
		// the parent keeps the very rows it is handed
		{`TAKE(FILTER(L, _["a"] > 1), 3)`, "TAKE", false},
		{`DROP(FILTER(L, _["a"] > 1), 3)`, "DROP", false},
		{`DISTINCT(FILTER(L, _["a"] > 1))`, "DISTINCT", false},
		{`LIST(FILTER(L, _["a"] > 1))`, "LIST", false},
		{`LINK(FILTER(L, _["a"] > 1), L, _1["a"] == _2["a"])`, "LINK", false},
		// not a FILTER
		{`COUNT(MAP(L, _))`, "COUNT", false},
		{`COUNT(L)`, "COUNT", false},
		// an assignment anywhere the rows are read
		{`MAP(FILTER(L, (X = 1; _["a"] > 1)), _["c"])`, "MAP", false},
		{`MAP(FILTER(L, _["a"] > 1), (L[1]["a"] = 9; _["c"]))`, "MAP", false},
		{`TOP_BY(FILTER(L, _["a"] > 1), _["k"], (N = 3; N))`, "TOP_BY", false},
		{`COUNT(FILTER(FILTER(L, (Q = 1; TRUE)), _["a"] > 1))`, "COUNT", false},
		// a registered function may mutate what it is handed
		{`SUM(FILTER(L, _["a"] > 1), P3HOST(_))`, "SUM", false},
		{`COUNT(FILTER(L, P3HOST(_) > 1))`, "COUNT", false},
	}
	RegisterFunction("P3HOST", 1, 1, func(a *Args) *Value { return NewInt(1) })
	for _, c := range cases {
		n := find(parse(c.src), c.consumer)
		if n == nil {
			t.Fatalf("no %s call in %s", c.consumer, c.src)
		}
		if got := noCopyAllowed(n.S, n.Items); got != c.want {
			t.Errorf("%s: elision allowed = %v, want %v", c.src, got, c.want)
		}
	}
}

func TestNoCopyProgramsThatMutateStillSeeTheCopy(t *testing.T) {
	ctx := func() *Value { return ctxWith("L", joinRows(40, 5, 9)) }
	for _, src := range []string{
		// the consumer's body changes a source row after FILTER has kept it
		`JOIN(MAP(FILTER(L, _["a"] > 2), (L[1]["c"] = 12345; _["c"])), ",")`,
		`SUM(FILTER(L, _["a"] > 2), (L[2]["c"] = 7; _["c"]))`,
		// a result that is held must be independent of its source
		`R = FILTER(L, _["a"] > 2); R[1]["c"] = 99999; L[1]["c"] & "|" & R[1]["c"]`,
		`R = TAKE(FILTER(L, _["a"] > 2), 2); R[1]["c"] = 99999; JOIN(MAP(L, _["c"]), ",")`,
		`R = MAP(FILTER(L, _["a"] > 2), _); R[1]["c"] = 99999; JOIN(MAP(L, _["c"]), ",")`,
		`R = TOP_BY(FILTER(L, _["a"] > 2), _["k"], 3); R[1]["c"] = 99999; JOIN(MAP(L, _["c"]), ",")`,
		`R = SORT_BY(FILTER(L, _["a"] > 2), _["k"]); R[1]["c"] = 99999; JOIN(MAP(L, _["c"]), ",")`,
		`R = COUNT(FILTER(L, _["a"] > 2)); L[1]["c"] = 1; R`,
	} {
		bothWays(t, src, ctx)
	}
}

func TestNoCopyStillRefusesTheRowsTheCopyWouldRefuse(t *testing.T) {
	for depth := 195; depth <= 202; depth++ {
		depth := depth
		ctx := func() *Value { return ctxWith("L", nestedRows(depth)) }
		for _, src := range []string{`COUNT(FILTER(L, TRUE))`, `SUM(FILTER(L, TRUE), 1)`, `COUNT(MAP(FILTER(L, TRUE), 1))`} {
			got := bothWays(t, src, ctx)
			wantDeep := depth >= 200
			if strings.HasPrefix(got, "!E_DEPTH") != wantDeep {
				t.Errorf("depth %d %s: %s (E_DEPTH expected: %v)", depth, src, got, wantDeep)
			}
		}
	}
}

func TestNoCopyFlagDoesNotOutliveAnError(t *testing.T) {
	// The FILTER's predicate fails on a caught error; the state handed to the FILTER
	// must be gone, so the later held result is a real copy.
	ctx := func() *Value { return ctxWith("L", joinRows(30, 4, 2)) }
	for _, src := range []string{
		`X = (COUNT(FILTER(L, _["zz"] > 0)) ?? 7); R = FILTER(L, _["a"] > 1); R[1]["c"] = 4242; X & "|" & L[1]["c"] & "|" & R[1]["c"]`,
		`X = (SUM(FILTER(L, _["zz"] > 0), 1) ?? 7); R = TAKE(FILTER(L, _["a"] > 1), 3); R[1]["c"] = 4242; X & "|" & JOIN(MAP(L, _["c"]), ",")`,
	} {
		got := bothWays(t, src, ctx)
		if strings.HasPrefix(got, "!") {
			t.Errorf("%s: %s", src, got)
		}
	}
	_ = fmt.Sprint
}
