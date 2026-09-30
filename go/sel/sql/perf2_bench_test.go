package sql

// Round-2 SQL workloads (GO-P16 … GO-P18), docs/interim/2026-09-29/worklist/
// performance/go.md. Fixed inputs; TestPerf2SqlChecksums pins a hash of every
// rendered output so a speedup that changes a byte fails a test.

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

func perfBindings() *Bindings {
	return NewBindings(map[string]*Binding{
		"AMT":  colNum("amt"),
		"NAME": ColumnBinding("name", "t", KindText, false, false, false, "", "", false),
		"A":    colNum("a"),
	})
}

// predicates builds the GO-P16 rule: n conjuncts mixing arithmetic, text
// comparison with a quote in the literal, and LEN.
func predicates(n int) string {
	parts := make([]string, n)
	for i := range parts {
		parts[i] = fmt.Sprintf(`(AMT + %d > 3 AND NAME $== "it's %d" AND LEN(NAME) < %d)`, i, i, i+5)
	}
	return strings.Join(parts, " AND ")
}

func inList(n int) string {
	items := make([]string, n)
	for i := range items {
		items[i] = fmt.Sprint(i)
	}
	return "A IN (" + strings.Join(items, ", ") + ")"
}

func translateRender(b testing.TB, dialect, src string) string {
	f, err := Translate(sel.MustCompile(src), dialect, perfBindings(), Options{})
	if err != nil {
		b.Fatalf("%s: %v", src[:min(len(src), 60)], err)
	}
	return f.AsValue(ModeInline)
}

func BenchmarkP16TranslateRender(b *testing.B) {
	for _, n := range []int{30, 60, 120} {
		src := predicates(n)
		prog := sel.MustCompile(src)
		b.Run(fmt.Sprintf("predicates/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				f, err := Translate(prog, "mariadb", perfBindings(), Options{})
				if err != nil {
					b.Fatal(err)
				}
				_ = f.AsValue(ModeInline)
			}
		})
	}
	b.Run("plan_hybrid_3step", func(b *testing.B) {
		prog := sel.MustCompile(`ORDERS .> FILTER(_["id"] > 2) .> MAP(_["id"] + COUNT(SMALL)) .> TAKE(5)`)
		bind := ordersAndCustomers()
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			_ = PlanHybrid(prog, "mariadb", bind, Options{})
		}
	})
}

func BenchmarkP17InFold(b *testing.B) {
	for _, n := range []int{1000, 4000, 8000} {
		prog := sel.MustCompile(inList(n))
		b.Run(fmt.Sprintf("in_list/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := Translate(prog, "mariadb", perfBindings(), Options{}); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
	for _, n := range []int{1000, 4000} {
		items := make([]string, n)
		for i := range items {
			items[i] = fmt.Sprint(i)
		}
		prog := sel.MustCompile("SUM((" + strings.Join(items, ", ") + "), _ + A)")
		b.Run(fmt.Sprintf("sum_unroll/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := Translate(prog, "mariadb", perfBindings(), Options{}); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

func bigContext(n int) *sel.Value {
	items := make([]*sel.Value, n)
	for i := range items {
		items[i] = sel.NewRecordFromEntries([]sel.Entry{
			{Key: "a", Val: sel.NewInt(int64(i))}, {Key: "b", Val: sel.NewInt(int64(i * 3))}, {Key: "c", Val: sel.NewText("row")},
		})
	}
	ctx := sel.NewNone()
	ctx.Set("BIG", sel.NewListOwned(items))
	ctx.Set("SMALL", sel.NewList([]*sel.Value{sel.NewInt(1), sel.NewInt(2), sel.NewInt(3)}))
	return ctx
}

func orderRows() *sel.Value {
	items := make([]*sel.Value, 100)
	for i := range items {
		items[i] = sel.NewRecordFromEntries([]sel.Entry{{Key: "id", Val: sel.NewInt(int64(i + 3))}})
	}
	return sel.NewListOwned(items)
}

const hybridSrc = `ORDERS .> FILTER(_["id"] > 2) .> MAP(_["id"] + COUNT(SMALL))`

func BenchmarkP18ExecuteHybrid(b *testing.B) {
	for _, n := range []int{12500, 25000, 50000} {
		ctx := bigContext(n)
		plan := PlanHybrid(sel.MustCompile(hybridSrc), "mariadb", ordersAndCustomers(), Options{})
		if !plan.IsHybrid {
			b.Fatal("not a hybrid plan")
		}
		rows := orderRows()
		runner := func(string, []*sel.Value) (*sel.Value, error) { return rows, nil }
		b.Run(fmt.Sprintf("big_ctx/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := ExecuteHybrid(plan, runner, ctx); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

func digest(s string) string {
	h := sha256.Sum256([]byte(s))
	return hex.EncodeToString(h[:8]) + fmt.Sprintf("/%d", len(s))
}

var perf2SqlChecksums = map[string]string{
	"p16 predicates_30":  "7d6fcf1ad20ec957/5308",
	"p16 predicates_pg":  "b9262e881e1853a6/1707",
	"p16 predicates_sql": "ae79cdfb6468b9b8/1995",
	"p16 quote_escape":   "78928c13f7d1901a/136",
	"p17 in_1000":        "9cbd8c6c8635ee16/100884",
	"p17 in_300":         "bc23beba1dc2e913/30184",
	"p17 in_pg":          "caebea3ff98c3b5b/50984",
	"p17 in_small":       "d2350bf7bfe6b602/489",
	"p17 sum_unroll":     "6f3d3b92fe55bf70/176",
	"p18 hybrid_sum":     "100/5550/300",
}

func TestPerf2SqlChecksums(t *testing.T) {
	got := map[string]string{
		"p16 predicates_30":  digest(translateRender(t, "mariadb", predicates(30))),
		"p16 predicates_pg":  digest(translateRender(t, "postgresql", predicates(9))),
		"p16 predicates_sql": digest(translateRender(t, "sqlite", predicates(9))),
		"p16 quote_escape":   digest(translateRender(t, "mariadb", `NAME $== "it's \\ \" \u{1F600}é" AND AMT > 1`)),
		"p17 in_300":         digest(translateRender(t, "mariadb", inList(300))),
		"p17 in_1000":        digest(translateRender(t, "mariadb", inList(1000))),
		"p17 in_small":       digest(translateRender(t, "mariadb", inList(5))),
		"p17 in_pg":          digest(translateRender(t, "postgresql", inList(700))),
		"p17 sum_unroll":     digest(translateRender(t, "mariadb", "SUM((1,2,3,4,5,6,7,8,9,10), _ + A)")),
	}
	plan := PlanHybrid(sel.MustCompile(hybridSrc), "mariadb", ordersAndCustomers(), Options{})
	rows := orderRows()
	ctx := bigContext(300)
	res, err := ExecuteHybrid(plan, func(string, []*sel.Value) (*sel.Value, error) { return rows, nil }, ctx)
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, v := range res.Values() {
		var n int
		fmt.Sscan(v.Scalar(), &n)
		total += n
	}
	got["p18 hybrid_sum"] = fmt.Sprintf("%d/%d/%d", res.Size(), total, ctx.Get("BIG").Size())
	for name, g := range got {
		want, ok := perf2SqlChecksums[name]
		if !ok {
			t.Logf("CHECKSUM %q: %q", name, g)
			continue
		}
		if g != want {
			t.Errorf("%s: got %s want %s", name, g, want)
		}
	}
}
