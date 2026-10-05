package sql

// Round-3 SQL workloads (GO-P28), docs/interim/2026-09-29/worklist/performance/go.md.
// TestLiteralWorkloadChecksums pins a digest of every rendered output and plan, recorded at
// the baseline, so a speedup that changes a byte fails a test.

import (
	"fmt"
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

func constChain(n int) string { return "AMT > " + strings.Repeat("1+", n) + "1" }

func sumLiterals(n int) string {
	items := make([]string, n)
	for i := range items {
		items[i] = fmt.Sprint(i)
	}
	return "SUM((" + strings.Join(items, ", ") + "), _ + 1) > 0"
}

func BenchmarkP28TextLiteral(b *testing.B) {
	for _, d := range []string{"mariadb", "postgresql", "sqlite"} {
		b.Run("short/"+d, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				for j := 0; j < 10000; j++ {
					_ = textLiteral(d, "hello")
				}
			}
		})
		b.Run("quoted/"+d, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				for j := 0; j < 10000; j++ {
					_ = textLiteral(d, `it's a "x" \ y`)
				}
			}
		})
	}
	big := strings.Repeat("abcdefghij'klm\\no", 65000) // ~1.1 MB with escapes
	for _, d := range []string{"mariadb", "postgresql"} {
		b.Run("megabyte/"+d, func(b *testing.B) {
			b.ReportAllocs()
			b.SetBytes(int64(len(big)))
			for i := 0; i < b.N; i++ {
				_ = textLiteral(d, big)
			}
		})
	}
}

func BenchmarkP28ConstantChains(b *testing.B) {
	for _, n := range []int{50, 100, 195} {
		prog := sel.MustCompile(constChain(n))
		b.Run(fmt.Sprintf("const_chain/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := Translate(prog, "mariadb", perfBindings(), Options{}); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
	col := sel.MustCompile("AMT > " + strings.Repeat("A+", 195) + "1")
	b.Run("column_chain_ctl/n=195", func(b *testing.B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			if _, err := Translate(col, "mariadb", perfBindings(), Options{}); err != nil {
				b.Fatal(err)
			}
		}
	})
	for _, n := range []int{500, 1000, 2000} {
		prog := sel.MustCompile(sumLiterals(n))
		b.Run(fmt.Sprintf("sum_literals/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				if _, err := Translate(prog, "mariadb", perfBindings(), Options{}); err != nil {
					b.Fatal(err)
				}
			}
		})
	}
}

// tailPipeline: a pipeline whose translatable prefix is short (the MAP reading
// SMALL is not), followed by `tail` steps that stay in memory.
func tailPipeline(tail int) string {
	s := `ORDERS .> FILTER(_["id"] > 2) .> MAP(_["id"] + COUNT(SMALL))`
	for i := 0; i < tail; i++ {
		if i%2 == 0 {
			s += ` .> FILTER(_ > 0)`
		} else {
			s += ` .> MAP(_ + 1)`
		}
	}
	return s
}

func helperChain(k int) string {
	var b strings.Builder
	b.WriteString(`H0 = SMALL; `)
	for i := 1; i <= k; i++ {
		fmt.Fprintf(&b, "H%d = H%d; ", i, i-1)
	}
	fmt.Fprintf(&b, `ORDERS .> FILTER(_["id"] > COUNT(H%d)) .> MAP(_["id"] + COUNT(SMALL))`, k)
	return b.String()
}

func BenchmarkP28PlanHybrid(b *testing.B) {
	bind := ordersAndCustomers()
	for _, n := range []int{5, 20, 40, 80} {
		prog := sel.MustCompile(tailPipeline(n))
		b.Run(fmt.Sprintf("tail_steps/n=%d", n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				_ = PlanHybrid(prog, "mariadb", bind, Options{})
			}
		})
	}
	for _, k := range []int{5, 20, 60} {
		prog := sel.MustCompile(helperChain(k))
		b.Run(fmt.Sprintf("helper_chain/k=%d", k), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				_ = PlanHybrid(prog, "mariadb", bind, Options{})
			}
		})
	}
}

func planDigest(t testing.TB, dialect, src string) string {
	p := PlanHybrid(sel.MustCompile(src), dialect, ordersAndCustomers(), Options{})
	q := ""
	if p.SqlStatement != nil {
		q = p.SqlStatement.AsStatement(ModeInline)
	}
	return digest(fmt.Sprintf("%v/%v/%v|%s|%s|%s", p.IsHybrid, p.PureSql, p.PureMemory, q, p.ContinuationSourceVar, strings.Join(p.SourceTables, ",")))
}

var perf3SqlChecksums = map[string]string{
	"p28 const_195":      "09df8e94eecdd752/7815",
	"p28 const_50":       "ba560404c6cb928e/315",
	"p28 const_dead":     "3f1df7f7aaeecb01/171",
	"p28 const_div_err":  "4d0898a38b369431/170",
	"p28 const_nested":   "fcfc25b60ee14bdb/185",
	"p28 literals":       "7409a2559f55596d/5571",
	"p28 plan_helpers_5": "12c34f72a83453e9/31",
	"p28 plan_memory":    "3cc57354a6329020/25",
	"p28 plan_pure":      "19115db19c8ec5bd/100",
	"p28 plan_tail_5":    "68d5fcd6f859bfa2/82",
	"p28 plan_tail_pg":   "b740b61047f906c2/82",
	"p28 sum_literals":   "b84f0d3563035ad9/511",
}

func TestLiteralWorkloadChecksums(t *testing.T) {
	texts := []string{"", "hello", `it's`, `a "q" b`, `back\slash`, "nl\nx\ty\r", "é😀ü", "nul\x00x", `'`, `''`, `\'`, strings.Repeat("ab'c\\", 200)}
	var lits []string
	for _, d := range []string{"mariadb", "mysql", "postgresql", "sqlite"} {
		for _, s := range texts {
			lits = append(lits, textLiteral(d, s))
		}
	}
	got := map[string]string{
		"p28 literals":       digest(strings.Join(lits, "\x01")),
		"p28 const_50":       digest(translateRender(t, "mariadb", constChain(50))),
		"p28 const_195":      digest(translateRender(t, "postgresql", constChain(195))),
		"p28 const_nested":   digest(translateRender(t, "sqlite", `AMT > (1 + 2) * (3 - 1) + LEN("abc") AND NAME $== "a" & "b"`)),
		"p28 sum_literals":   digest(translateRender(t, "mariadb", sumLiterals(40))),
		"p28 const_div_err":  digest(errText(t, "mariadb", `AMT > 1 / 0`)),
		"p28 const_dead":     digest(errText(t, "mariadb", `IF(TRUE, AMT, 1 / 0) > 1`)),
		"p28 plan_tail_5":    planDigest(t, "mariadb", tailPipeline(5)),
		"p28 plan_tail_pg":   planDigest(t, "postgresql", tailPipeline(9)),
		"p28 plan_helpers_5": planDigest(t, "mariadb", helperChain(5)),
		"p28 plan_pure":      planDigest(t, "mariadb", `ORDERS .> FILTER(_["id"] > 2) .> MAP(RECORD("x", _["id"])) .> TAKE(3)`),
		"p28 plan_memory":    planDigest(t, "sqlite", `SMALL .> MAP(_ + 1)`),
	}
	for name, g := range got {
		want, ok := perf3SqlChecksums[name]
		if !ok {
			t.Logf("CHECKSUM %q: %q", name, g)
			continue
		}
		if g != want {
			t.Errorf("%s: got %s want %s", name, g, want)
		}
	}
}

// errText renders the refusal (code and message) of a program, or the SQL if it translates.
func errText(t testing.TB, dialect, src string) string {
	f, err := Translate(sel.MustCompile(src), dialect, perfBindings(), Options{})
	if err != nil {
		return "ERR " + err.Error()
	}
	return f.AsValue(ModeInline)
}
