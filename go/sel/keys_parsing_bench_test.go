package sel

// Round-3 workloads for the Go performance queue (GO-P21 … GO-P29),
// docs/interim/2026-09-29/worklist/performance/go.md. Same conventions as the earlier
// rounds: fixed seeds, n/2n/4n, semantic checksums in TestKeysAndParsingWorkloadChecksums
// (keys_parsing_checksums_test.go). GO-P21's decimal workloads are in
// go/internal/decimal/digit_bounds_bench_test.go and the SQL ones in go/sel/sql/literals_bench_test.go.

import (
	"fmt"
	"strings"
	"testing"
)

// --- GO-P22: nodeContainsVar on every aggregate call --------------------------------

func BenchmarkP22KeyScan(b *testing.B) {
	bigBody := `SUM(LN, l, ((l["q"] + 1) * 2 - l["p"] + (l["q"] * 3) - (l["p"] + 7) + l["q"] * l["p"] - 11 + (l["p"] * 5) + (l["q"] - 2) * 4))`
	for _, n := range []int{10000, 20000, 40000} {
		ctx := func() *Value {
			return ctxWith("O", intList(n, 4), "LN", newListOwned([]*Value{
				rec("q", 1, "p", 2), rec("q", 3, "p", 4), rec("q", 5, "p", 6), rec("q", 7, "p", 8)}))
		}
		for _, c := range []perfCase{
			{"nested_small_body", `SUM(O, o, SUM(LN, l, l["q"] * 2))`, ctx},
			{"nested_big_body", `SUM(O, o, ` + bigBody + `)`, ctx},
			{"flat_ctl", `SUM(O, o, o * 2)`, ctx},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P23: text/binary builtins ----------------------------------------------------

func binOf(n int) *Value {
	buf := make([]byte, n)
	s := lcg(23)
	for i := range buf {
		buf[i] = byte(s.next())
	}
	return NewBin(buf)
}

func BenchmarkP23TextBinary(b *testing.B) {
	for _, n := range []int{1000000, 2000000, 4000000} {
		ctxT := func() *Value {
			return ctxWith("T", NewText(strings.Repeat("ab,c", n/4)), "B", binOf(n))
		}
		for _, r := range []struct{ name, src string }{
			{"replace", `LEN(REPLACE("a", "xy", T))`},
			{"split", `COUNT(SPLIT(T, ","))`},
			{"repeat", `LEN(REPEAT("ab", ` + fmt.Sprint(n/2) + `))`},
			{"upper", `LEN(UPPER(T))`},
			{"lower", `LEN(LOWER(T))`},
			{"b64_roundtrip", `BLEN(DECODE_BASE64(ENCODE_BASE64(B)))`},
			{"b64_encode", `LEN(ENCODE_BASE64(B))`},
		} {
			c := perfCase{fmt.Sprintf("%s/n=%d", r.name, n), r.src, ctxT}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
	for _, n := range []int{250000, 500000, 1000000} {
		c := perfCase{fmt.Sprintf("btl/n=%d", n), `COUNT(BTL(B))`, func() *Value { return ctxWith("B", binOf(n)) }}
		b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
	}
}

// --- GO-P24: one-shot Eval ------------------------------------------------------------

func BenchmarkP24OneShot(b *testing.B) {
	ctx := ctxWith("X", NewInt(41), "A", NewInt(3), "B", NewInt(10), "C", NewInt(4), "L", intList(20, 3))
	for _, c := range []struct{ name, src string }{
		{"add_one", `X + 1`},
		{"medium", `A * 2 + B % 7 - C`},
		{"three_op", `(A + B) * (C - 1) + (A * C) - (B % 3)`},
		{"pipeline", `L .> FILTER(_ > 400000) .> MAP(_ * 2) .> COUNT()`},
		{"if_and", `IF(A > 2 AND B < 20, C * 2, C)`},
	} {
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := Eval(c.src, ctx); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

// --- GO-P25: polymorphic index sites --------------------------------------------------

func polymorphicRows(n, shapes int) *Value {
	items := make([]*Value, n)
	for i := range items {
		switch i % shapes {
		case 0:
			items[i] = rec("a", i, "b", 1)
		case 1:
			items[i] = rec("b", 1, "a", i)
		case 2:
			items[i] = rec("c", 1, "a", i, "b", 2)
		default:
			items[i] = rec("d", 1, "e", 2, "a", i)
		}
	}
	return newListOwned(items)
}

func BenchmarkP25SlotCache(b *testing.B) {
	for _, n := range []int{100000, 200000, 400000} {
		for _, shapes := range []int{1, 2, 4} {
			c := perfCase{fmt.Sprintf("shapes=%d/n=%d", shapes, n), `SUM(L, _["a"])`,
				func() *Value { return ctxWith("L", polymorphicRows(n, shapes)) }}
			b.Run(c.name, func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P26: DISTINCT/BUCKET hashing ---------------------------------------------------

func BenchmarkP26Hash(b *testing.B) {
	for _, n := range []int{50000, 100000, 200000} {
		ctx := func() *Value { return ctxWith("L", joinRows(n, 10, 5), "NUMS", intList(n, 3)) }
		for _, c := range []perfCase{
			{"distinct_records", `COUNT(DISTINCT(L))`, ctx},
			{"distinct_numbers", `COUNT(DISTINCT(NUMS))`, ctx},
			{"bucket_key", `COUNT(BUCKET(L, _["k"]))`, ctx},
			{"dedupe_records", `COUNT(DEDUPE(L))`, ctx},
		} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) { benchCase(b, c) })
		}
	}
}

// --- GO-P27: parser hot loop and numeric literals ---------------------------------------

func BenchmarkP27Parse(b *testing.B) {
	for _, n := range []int{100000, 200000, 400000} {
		chain := "1" + strings.Repeat("+1", n)
		lits := "1" + strings.Repeat(" + 12.5", n/4)
		for _, c := range []struct{ name, src string }{{"chain", chain}, {"literals", lits}} {
			b.Run(fmt.Sprintf("%s/n=%d", c.name, n), func(b *testing.B) {
				b.ReportAllocs()
				b.SetBytes(int64(len(c.src)))
				for i := 0; i < b.N; i++ {
					if _, err := Compile(c.src); err != nil {
						b.Fatal(err)
					}
				}
			})
		}
	}
}

// BenchmarkP26StructuralHash isolates the hash itself (the DISTINCT/BUCKET workloads
// above are dominated by the mandatory clone of each element).
func BenchmarkP26StructuralHash(b *testing.B) {
	rows := joinRows(100000, 10, 5).Elements()
	dense := newListOwned([]*Value{NewInt(1), NewInt(2), NewInt(3), NewInt(4), NewInt(5), NewInt(6), NewInt(7), NewInt(8)})
	var sink uint64
	b.Run("records_100k", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			for _, e := range rows {
				sink += e.Val.StructuralHash()
			}
		}
	})
	b.Run("dense_list_8", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			for j := 0; j < 100000; j++ {
				sink += dense.StructuralHash()
			}
		}
	})
	_ = sink
}
