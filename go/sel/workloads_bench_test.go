package sel

// Durable benchmark workloads for the Go performance queue (GO-P1 … GO-P10),
// docs/interim/2026-09-29/worklist/performance/go.md. Every workload uses a fixed
// seed and has a semantic checksum (TestWorkloadChecksums) so a speedup that
// changes an answer fails a test, not a review.
//
//	tools/perf/go/bench.sh [pattern]     runs them, median of 5, with allocs
//
// Heavy workloads are written to be run with -benchtime=1x.

import (
	"fmt"
	"strings"
	"testing"
)

// lcg is a fixed-seed generator: the workloads must be identical on every run.
type lcg uint64

func (s *lcg) next() uint64 {
	*s = *s*6364136223846793005 + 1442695040888963407
	return uint64(*s >> 33)
}

func rec(kv ...interface{}) *Value {
	entries := make([]Entry, 0, len(kv)/2)
	for i := 0; i < len(kv); i += 2 {
		var v *Value
		switch x := kv[i+1].(type) {
		case int:
			v = NewInt(int64(x))
		case string:
			v = NewText(x)
		}
		entries = append(entries, Entry{Key: kv[i].(string), Val: v})
	}
	return NewRecordFromEntries(entries)
}

// joinRows: n rows {a: key, c: unique, k: key-ish} with `keys` distinct a values.
func joinRows(n, keys int, seed uint64) *Value {
	s := lcg(seed)
	items := make([]*Value, n)
	for i := range items {
		items[i] = rec("a", int(s.next()%uint64(keys)), "c", i, "k", int(s.next()%1000))
	}
	return newListOwned(items)
}

func intList(n int, seed uint64) *Value {
	s := lcg(seed)
	items := make([]*Value, n)
	for i := range items {
		items[i] = NewInt(int64(s.next() % 1000000))
	}
	return newListOwned(items)
}

// textRows: rows {s: non-numeric text, n: int}.
func textRows(n int, seed uint64) *Value {
	s := lcg(seed)
	items := make([]*Value, n)
	for i := range items {
		items[i] = rec("s", fmt.Sprintf("k%07d", s.next()%10000000), "n", int(s.next()%1000))
	}
	return newListOwned(items)
}

func ctxWith(kv ...interface{}) *Value {
	c := NewNone()
	for i := 0; i < len(kv); i += 2 {
		c.Set(kv[i].(string), kv[i+1].(*Value))
	}
	return c
}

type perfCase struct {
	name string
	src  string
	ctx  func() *Value
}

func runOnce(src string, ctx *Value) string {
	return outcome(func() *Value {
		v, err := MustCompile(src).Run(ctx)
		if err != nil {
			panic(err)
		}
		return v
	})
}

func benchCase(b *testing.B, c perfCase) {
	prog := MustCompile(c.src)
	ctx := c.ctx()
	if _, err := prog.Run(ctx); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := prog.Run(ctx); err != nil {
			b.Fatal(err)
		}
	}
}

func joinCtx(n int) func() *Value {
	return func() *Value { return ctxWith("L", joinRows(n, 10, 1), "R", joinRows(n, 10, 2)) }
}

// --- GO-P1: equality AND residual (n/2n/4n at 10 distinct keys) ---------------

func BenchmarkP1LinkEqResidual(b *testing.B) {
	for _, n := range []int{375, 750, 1500} {
		for _, r := range []struct{ name, pred string }{
			{"eq", `l["a"] == r["a"]`},
			{"eq_lt", `l["a"] == r["a"] AND l["c"] < r["c"]`},
			{"eq_eq", `l["a"] == r["a"] AND l["c"] == r["c"]`},
		} {
			c := perfCase{fmt.Sprintf("%s/n=%d", r.name, n),
				`COUNT(LINK(L, R, l, r, ` + r.pred + `))`, joinCtx(n)}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P2/P3: sorting and TOP ------------------------------------------------

func BenchmarkP2Sort(b *testing.B) {
	for _, n := range []int{25000, 50000, 100000} {
		for _, c := range []perfCase{
			{"ints", `COUNT(SORT(NUMS))`, func() *Value { return ctxWith("NUMS", intList(n, 3)) }},
			{"text_by", `COUNT(SORT_BY(L, _["s"]))`, func() *Value { return ctxWith("L", textRows(n, 4)) }},
			{"text_by_desc", `COUNT(SORT_BY(L, _["s"], "DESC"))`, func() *Value { return ctxWith("L", textRows(n, 4)) }},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

func BenchmarkP3Top(b *testing.B) {
	for _, n := range []int{25000, 50000, 100000} {
		for _, c := range []perfCase{
			{"top10", `COUNT(TOP(NUMS, 10))`, func() *Value { return ctxWith("NUMS", intList(n, 3)) }},
			{"top_by10", `COUNT(TOP_BY(L, _["n"], 10))`, func() *Value { return ctxWith("L", textRows(n, 4)) }},
			{"top_desc10", `COUNT(TOP_DESC(NUMS, 10))`, func() *Value { return ctxWith("NUMS", intList(n, 3)) }},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P4: non-equi LINK -----------------------------------------------------

func BenchmarkP4LinkNonEqui(b *testing.B) {
	for _, n := range []int{250, 500, 1000} {
		for _, r := range []struct{ name, pred string }{{"lt", `X["k"] < Y["k"]`}, {"true", `TRUE`}} {
			c := perfCase{fmt.Sprintf("%s/n=%d", r.name, n),
				`COUNT(LINK(TAKE(L, ` + fmt.Sprint(n) + `), TAKE(R, ` + fmt.Sprint(n) + `), X, Y, ` + r.pred + `))`, joinCtx(n)}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P5: regex -------------------------------------------------------------

func BenchmarkP5Regex(b *testing.B) {
	for _, reps := range []int{62500, 125000, 250000} {
		sub := strings.Repeat("123-45,", reps)
		for _, c := range []perfCase{
			{"rmatch_dot", `RMATCH('.', S)`, nil},
			{"rfind_late", `RFIND('5,$', S)`, nil},
			{"rgroups", `COUNT(RGROUPS('(\d+)-(\d+)', S))`, nil},
		} {
			c.ctx = func() *Value { return ctxWith("S", NewText(sub)) }
			b.Run(fmt.Sprintf("%s/chars=%d", c.name, len(sub)), func(b *testing.B) { benchCase(b, c) })
		}
	}
	for _, c := range []perfCase{
		{"short_rmatch", `RMATCH('[0-9]+-[0-9]+', "123-45")`, func() *Value { return NewNone() }},
		{"short_rfind", `RFIND('-', "123-45")`, func() *Value { return NewNone() }},
		{"short_rgroups", `COUNT(RGROUPS('(\d+)-(\d+)', "123-45"))`, func() *Value { return NewNone() }},
		{"short_rreplace", `RREPLACE('-', "123-45", "+")`, func() *Value { return NewNone() }},
	} {
		b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
	}
}

// --- GO-P6: front end ---------------------------------------------------------

func bigSource(kb int) string {
	var sb strings.Builder
	sb.WriteString("A = 1;\n")
	i := 0
	for sb.Len() < kb*1024 {
		fmt.Fprintf(&sb, "V%d = (A + %d) * 3 - MAX(A, %d) & \"x%d\"; # note %d\n", i%50, i, i, i, i)
		i++
	}
	sb.WriteString("A")
	return sb.String()
}

func BenchmarkP6Tokenize(b *testing.B) {
	for _, kb := range []int{100, 200, 400} {
		src := bigSource(kb)
		b.Run(fmt.Sprintf("kb=%d", kb), func(b *testing.B) {
			b.ReportAllocs()
			b.SetBytes(int64(len(src)))
			for i := 0; i < b.N; i++ {
				tokenize(src)
			}
		})
	}
}

func BenchmarkP6Compile(b *testing.B) {
	for _, kb := range []int{100, 200, 400} {
		src := bigSource(kb)
		b.Run(fmt.Sprintf("kb=%d", kb), func(b *testing.B) {
			b.ReportAllocs()
			b.SetBytes(int64(len(src)))
			for i := 0; i < b.N; i++ {
				if _, err := Compile(src); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

// --- GO-P7/P8: interpreter hot path over records -------------------------------

func BenchmarkP7P8Interp(b *testing.B) {
	for _, n := range []int{50000, 100000, 200000} {
		ctx := func() *Value { return ctxWith("L", joinRows(n, 10, 5)) }
		for _, c := range []perfCase{
			{"coalesce_miss", `COUNT(MAP(L, _["zz"] ?? 1))`, ctx},
			{"coalesce_hit", `COUNT(MAP(L, _["a"] ?? 1))`, ctx},
			{"filter_and", `COUNT(FILTER(L, _["a"] > 2 AND _["c"] < 1000000 AND _["k"] >= 0))`, ctx},
			{"map_math", `SUM(L, _["a"] * 2 + _["c"] - 1)`, ctx},
			{"map_if", `COUNT(MAP(L, IF(_["a"] > 4, 1, 2)))`, ctx},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P9: small-number arithmetic end to end ---------------------------------

func BenchmarkP9Arith(b *testing.B) {
	for _, n := range []int{50000, 100000, 200000} {
		ctx := func() *Value { return ctxWith("L", intList(n, 6)) }
		for _, c := range []perfCase{
			{"sum", `SUM(L, _)`, ctx},
			{"sum_expr", `SUM(L, _ * 2 + 1)`, ctx},
			{"max_div", `MAX(MAP(L, _ / 7))`, ctx},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P10: large numeral parse ------------------------------------------------

func BenchmarkP10BigNumeral(b *testing.B) {
	for _, digits := range []int{62500, 250000, 999999} {
		src := strings.Repeat("7", digits)
		b.Run(fmt.Sprintf("digits=%d", digits), func(b *testing.B) {
			prog := MustCompile(`LEN(S + 1)`)
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				// A fresh text each time: a parsed value caches its decimal.
				if _, err := prog.Run(ctxWith("S", NewText(src))); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

// --- checksums ------------------------------------------------------------------

// Small-n answers for every workload above, recorded at the baseline (the tree
// before the GO-P wave). A speedup must not change any of them.
func TestWorkloadChecksums(t *testing.T) {
	n := 600
	ctxL := func() *Value { return ctxWith("L", joinRows(n, 10, 1), "R", joinRows(n, 10, 2)) }
	cases := []struct {
		name, src string
		ctx       func() *Value
	}{
		{"p1 eq", `COUNT(LINK(L, R, l, r, l["a"] == r["a"]))`, ctxL},
		{"p1 eq_lt", `COUNT(LINK(L, R, l, r, l["a"] == r["a"] AND l["c"] < r["c"]))`, ctxL},
		{"p1 eq_eq", `COUNT(LINK(L, R, l, r, l["a"] == r["a"] AND l["c"] == r["c"]))`, ctxL},
		{"p1 first", `JOIN(MAP(TAKE(LINK(L, R, l, r, l["a"] == r["a"] AND l["k"] < r["k"]), 25), _["l"]["c"] & ":" & _["r"]["c"]), ",")`, ctxL},
		{"p1 left", `COUNT(LINK_LEFT(L, R, l, r, l["a"] == r["a"] AND l["c"] < 50))`, ctxL},
		{"p2 ints", `JOIN(TAKE(SORT(NUMS), 20), ",")`, func() *Value { return ctxWith("NUMS", intList(5000, 3)) }},
		{"p2 ints_desc", `JOIN(TAKE(SORT_DESC(NUMS), 20), ",")`, func() *Value { return ctxWith("NUMS", intList(5000, 3)) }},
		{"p2 text_by", `JOIN(MAP(TAKE(SORT_BY(L, _["s"]), 20), _["s"]), ",")`, func() *Value { return ctxWith("L", textRows(5000, 4)) }},
		{"p2 text_by_desc", `JOIN(MAP(TAKE(SORT_BY(L, _["s"], "DESC"), 20), _["s"]), ",")`, func() *Value { return ctxWith("L", textRows(5000, 4)) }},
		{"p3 top", `JOIN(TOP(NUMS, 10), ",")`, func() *Value { return ctxWith("NUMS", intList(5000, 3)) }},
		{"p3 top_desc", `JOIN(TOP_DESC(NUMS, 10), ",")`, func() *Value { return ctxWith("NUMS", intList(5000, 3)) }},
		{"p3 top_by", `JOIN(MAP(TOP_BY(L, _["n"], 10), _["s"]), ",")`, func() *Value { return ctxWith("L", textRows(5000, 4)) }},
		{"p3 top_by_ties", `JOIN(MAP(TOP_BY(L, _["a"], 15), _["c"]), ",")`, func() *Value { return ctxWith("L", joinRows(3000, 10, 1)) }},
		{"p4 lt", `COUNT(LINK(TAKE(L, 120), TAKE(R, 120), X, Y, X["k"] < Y["k"]))`, ctxL},
		{"p4 true", `COUNT(LINK(TAKE(L, 60), TAKE(R, 60), X, Y, TRUE))`, ctxL},
		{"p5 rmatch", `RMATCH('.', S)`, func() *Value { return ctxWith("S", NewText(strings.Repeat("123-45,", 50))) }},
		{"p5 rfind", `RFIND('5,$', S)`, func() *Value { return ctxWith("S", NewText(strings.Repeat("123-45,", 50))) }},
		{"p5 rfind_unicode", `RFIND('é+', S)`, func() *Value { return ctxWith("S", NewText("ab😀c"+strings.Repeat("123-45,é", 50))) }},
		{"p5 rfind_none", `RFIND('Z', S)`, func() *Value { return ctxWith("S", NewText("ab😀c"+strings.Repeat("123-45,é", 50))) }},
		{"p5 rgroups", `JOIN(RGROUPS('(\d+)-(\d+)', S), "|")`, func() *Value { return ctxWith("S", NewText("ab 123-45 é 6-7")) }},
		{"p5 rreplace", `RREPLACE('(\d+)-(\d+)', "$2-$1", S)`, func() *Value { return ctxWith("S", NewText("ab 123-45 é 6-7")) }},
		{"p5 rreplace_empty", `RREPLACE('a*', "-", "baac")`, func() *Value { return NewNone() }},
		{"p5 ci", `IF(RMATCH('k', "K", "i"), "y", "n") & IF(RMATCH('s', "ſ", "i"), "y", "n") & IF(RMATCH('[^k]', "K", "i"), "y", "n")`, func() *Value { return NewNone() }},
		{"p7 miss", `COUNT(MAP(L, _["zz"] ?? 1))`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p7 hit", `SUM(L, _["a"] ?? 1)`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p7 nested_miss", `SUM(L, (_["zz"] ?? _["yy"]) ?? _["a"])`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p8 filter_and", `COUNT(FILTER(L, _["a"] > 2 AND _["c"] < 1000000 AND _["k"] >= 0))`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p8 map_math", `SUM(L, _["a"] * 2 + _["c"] - 1)`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p8 map_if", `SUM(L, IF(_["a"] > 4, 1, 2))`, func() *Value { return ctxWith("L", joinRows(2000, 10, 5)) }},
		{"p9 sum", `SUM(L, _)`, func() *Value { return ctxWith("L", intList(3000, 6)) }},
		{"p9 sum_expr", `SUM(L, _ * 2 + 1)`, func() *Value { return ctxWith("L", intList(3000, 6)) }},
		{"p9 max_div", `MAX(MAP(L, _ / 7))`, func() *Value { return ctxWith("L", intList(3000, 6)) }},
		{"p9 neg_abs", `SUM(L, ABS(-_) - _ + 0.5)`, func() *Value { return ctxWith("L", intList(3000, 6)) }},
		{"p10 big", `LEN(S + 1) & "," & RIGHT(S + 1, 5) & "," & LEFT(S * 3, 4)`, func() *Value { return ctxWith("S", NewText(strings.Repeat("7", 12345))) }},
	}
	for _, c := range cases {
		got := runOnce(c.src, c.ctx())
		want, ok := perfChecksums[c.name]
		if !ok {
			t.Logf("CHECKSUM %q: %q", c.name, got)
			continue
		}
		if got != want {
			t.Errorf("%s: %s\n got  %.300s\n want %.300s", c.name, c.src, got, want)
		}
	}
}
