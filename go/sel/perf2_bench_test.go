package sel

// Round-2 workloads for the Go performance queue (GO-P11 … GO-P20),
// docs/interim/2026-09-29/worklist/performance/go.md. Same conventions as
// perf_bench_test.go: fixed seeds, n/2n/4n, semantic checksums in
// TestPerf2WorkloadChecksums (perf2_checksums_test.go).

import (
	"fmt"
	"strings"
	"testing"
)

func asciiText(n int) *Value { return NewText(strings.Repeat("a", n)) }

// --- GO-P11: keyed lists (FILTER result with drops) ------------------------------

func BenchmarkP11KeyedList(b *testing.B) {
	for _, n := range []int{5000, 10000, 20000} {
		n := n
		for _, r := range []struct{ name, src string }{
			{"get", `K = FILTER(L, _ > 400000); SUM(INDEXES(K), K[_])`},
			{"has", `K = FILTER(L, _ > 400000); COUNT(FILTER(INDEXES(K), HAS(K, _)))`},
			{"dense_ctl", `K = L; SUM(INDEXES(K), K[_])`},
		} {
			c := perfCase{fmt.Sprintf("%s/n=%d", r.name, n), r.src,
				func() *Value { return ctxWith("L", intList(n, 7)) }}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P12: per-element allocation of interpreter loops --------------------------

func BenchmarkP12Alloc(b *testing.B) {
	n := 100000
	ctx := func() *Value { return ctxWith("L", joinRows(n, 10, 5), "NUMS", intList(n, 3)) }
	for _, r := range []struct{ name, src string }{
		{"map_const", `COUNT(MAP(L, 1))`},
		{"map_field", `COUNT(MAP(L, _["a"]))`},
		{"map_cmp", `COUNT(MAP(L, _["a"] > 4))`},
		{"map_mul", `COUNT(MAP(L, _["a"] * 2))`},
		{"map_mul_add", `COUNT(MAP(L, _["a"] * 2 + _["c"]))`},
		{"map_if", `COUNT(MAP(L, IF(_["a"] > 4, 1, 2)))`},
		{"map_record", `COUNT(MAP(L, RECORD("x", 1)))`},
		{"map_identity", `COUNT(MAP(NUMS, _))`},
		{"sort_count", `COUNT(SORT(NUMS))`},
		{"filter", `COUNT(FILTER(L, _["a"] > 4))`},
	} {
		c := perfCase{r.name, r.src, ctx}
		b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
	}
}

// --- GO-P13: assignment of a fresh right-hand side --------------------------------

func BenchmarkP13Assign(b *testing.B) {
	n := 200000
	ctx := func() *Value { return ctxWith("L", joinRows(n, 10, 5)) }
	for _, r := range []struct{ name, src string }{
		{"map_alone", `COUNT(MAP(L, RECORD("x", _["a"], "y", _["c"])))`},
		{"map_assigned", `X = MAP(L, RECORD("x", _["a"], "y", _["c"])); COUNT(X)`},
		{"var_copy", `X = L; COUNT(X)`},
		{"if_branch", `X = IF(TRUE, L, L); COUNT(X)`},
	} {
		c := perfCase{r.name, r.src, ctx}
		b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
	}
}

// --- GO-P14: record construction --------------------------------------------------

func BenchmarkP14Record(b *testing.B) {
	ctxR := func() *Value { return ctxWith("L", joinRows(100000, 10, 5)) }
	b.Run("record_literal_keys", func(b *testing.B) {
		benchCase(b, perfCase{"rl", `COUNT(MAP(L, RECORD("a", _["a"], "b", _["k"])))`, ctxR})
	})
	for _, n := range []int{10000, 20000} {
		n := n
		b.Run(fmt.Sprintf("link_table/n=%d", n), func(b *testing.B) {
			benchCase(b, perfCase{"lt", `COUNT(LINK(L, R, _1["a"] == _2["a"]))`,
				func() *Value { return ctxWith("L", joinRows(n, 1000, 1), "R", joinRows(n, 1000, 2)) }})
		})
	}
	b.Run("select_cols", func(b *testing.B) {
		benchCase(b, perfCase{"sc", `COUNT(SELECT_COLS(L, "a", "c"))`, ctxR})
	})
}

// --- GO-P15: FIND ------------------------------------------------------------------

func BenchmarkP15Find(b *testing.B) {
	for _, n := range []int{5000, 10000, 20000} {
		n := n
		b.Run(fmt.Sprintf("miss_tail/n=%d", n), func(b *testing.B) {
			benchCase(b, perfCase{"f", `FIND(REPEAT("a", ` + fmt.Sprint(n) + `) & "b", REPEAT("a", ` + fmt.Sprint(2*n) + `))`,
				func() *Value { return NewNone() }})
		})
	}
	b.Run("unicode_hit", func(b *testing.B) {
		benchCase(b, perfCase{"f", `FIND("é😀b", S)`,
			func() *Value { return ctxWith("S", NewText(strings.Repeat("aé😀", 200000)+"é😀b")) }})
	})
	b.Run("from_offset", func(b *testing.B) {
		benchCase(b, perfCase{"f", `FIND("zz", S, 500000)`,
			func() *Value { return ctxWith("S", NewText(strings.Repeat("ab", 400000)+"zz")) }})
	})
}

// --- GO-P19: regex per-call overhead ------------------------------------------------

func BenchmarkP19RegexCall(b *testing.B) {
	ctx := func() *Value {
		s := lcg(9)
		items := make([]*Value, 200000)
		for i := range items {
			items[i] = NewText(fmt.Sprintf("%03d-%02d", s.next()%1000, s.next()%100))
		}
		return ctxWith("L", newListOwned(items))
	}
	b.Run("rmatch_map", func(b *testing.B) {
		benchCase(b, perfCase{"r", `COUNT(FILTER(L, RMATCH('^\d{3}-\d{2}$', _)))`, ctx})
	})
	b.Run("filter_empty_ctl", func(b *testing.B) {
		benchCase(b, perfCase{"r", `COUNT(FILTER(L, FALSE))`, ctx})
	})
}

// --- GO-P20: rune round trips --------------------------------------------------------

func BenchmarkP20Text(b *testing.B) {
	for _, n := range []int{2000000, 4000000, 8000000} {
		n := n
		ctx := func() *Value { return ctxWith("S", NewText(" "+strings.Repeat("a", n)+" ")) }
		for _, r := range []struct{ name, src string }{
			{"trim", `LEN(TRIM(S))`},
			{"ltrim", `LEN(LTRIM(S))`},
			{"code", `CODE(S)`},
			{"left", `LEN(LEFT(S, 1000000))`},
			{"right", `LEN(RIGHT(S, 1000))`},
			{"substr", `LEN(SUBSTR(S, 1000, 1000))`},
			{"backwards", `LEN(BACKWARDS(S))`},
			{"padl", `LEN(PADL("7", ` + fmt.Sprint(n) + `, "0"))`},
			{"len_ctl", `LEN(S)`},
		} {
			c := perfCase{fmt.Sprintf("%s/n=%d", r.name, n), r.src, ctx}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// uni: multibyte control for the same builtins (correctness of the fast paths).
func uniText() *Value { return NewText(" " + strings.Repeat("aé😀ü", 50000) + " ") }
