package sql

import (
	"reflect"
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// The dialect chain is memoised; the memo must follow every registration
// change and never be corrupted by a caller that modifies what Chain returns.
func TestChainMemoFollowsRegistrationAndIsNotSharedWithCallers(t *testing.T) {
	Reset()
	defer Reset()
	if got := Chain("perf2-none"); len(got) != 0 {
		t.Fatalf("unknown dialect has a chain: %v", got)
	}
	base := Chain("mariadb")
	if len(base) < 2 || base[0] != "mariadb" {
		t.Fatalf("mariadb chain: %v", base)
	}
	base[0] = "corrupted"
	if again := Chain("mariadb"); again[0] != "mariadb" {
		t.Fatalf("a caller modified the memoised chain: %v", again)
	}
	DefineDialect("perf2-d", map[string]interface{}{"extends": "mariadb", "version": "10.5"})
	d := Chain("perf2-d")
	if len(d) != len(base)+1 || d[0] != "perf2-d" || d[1] != "mariadb" {
		t.Fatalf("chain of the new dialect: %v", d)
	}
	Reset()
	if got := Chain("perf2-d"); len(got) != 0 {
		t.Fatalf("a reset dialect still has a chain: %v", got)
	}
	// A refused registration leaves no stale chain behind.
	func() {
		defer func() { _ = recover() }()
		DefineDialect("perf2-bad", map[string]interface{}{"extends": "mariadb", "version": "1",
			"lexical": map[string]interface{}{"textQuote": "'", "identQuote": "'"}})
	}()
	if got := Chain("perf2-bad"); len(got) != 0 {
		t.Fatalf("a refused dialect has a chain: %v", got)
	}
	// Lexical lookups through the memo keep inheriting.
	if Lexical("mariadb", "textQuote") == nil {
		t.Fatal("lexical lookup lost its inheritance")
	}
}

// Fill's capacity hint changes nothing observable.
func TestFillKeepsItsOutputBytes(t *testing.T) {
	src := `(AMT + 1 > 3 AND NAME $== "it's 1" AND LEN(NAME) < 6) AND (AMT + 2 > 3 AND NAME $== "it's 2" AND LEN(NAME) < 7)`
	got := translateRender(t, "mariadb", src)
	for _, want := range []string{"it\\'s 1", "it\\'s 2", "LENGTH("} {
		if !strings.Contains(got, want) && !strings.Contains(got, strings.ReplaceAll(want, `\'`, `''`)) {
			t.Fatalf("missing %q in %s", want, got)
		}
	}
	if got != translateRender(t, "mariadb", src) {
		t.Fatal("two translations of one program differ")
	}
}

// ExecuteHybrid reads the caller's variables without copying the ones the
// continuation cannot write, never mutates the caller's context, and still copies
// what the continuation assigns.
func TestExecuteHybridDoesNotCopyWhatItOnlyReads(t *testing.T) {
	ctx := bigContext(200)
	plan := PlanHybrid(sel.MustCompile(hybridSrc), "mariadb", ordersAndCustomers(), Options{})
	if !plan.IsHybrid {
		t.Fatal("not hybrid")
	}
	rows := orderRows()
	runner := func(string, []*sel.Value) (*sel.Value, error) { return rows, nil }
	before := ctx.Get("BIG")
	res, err := ExecuteHybrid(plan, runner, ctx)
	if err != nil || res.Size() != 100 {
		t.Fatalf("result: %v %v", res, err)
	}
	if ctx.Get("BIG") != before || ctx.Has("_INPUT") {
		t.Fatal("the caller's context was mutated")
	}
	// A continuation that assigns to a caller variable must not leak it back.
	plan2 := PlanHybrid(sel.MustCompile(`SMALL[1] = 99; ORDERS .> FILTER(_["id"] > 2) .> MAP(_["id"] + SMALL[1])`), "mariadb", ordersAndCustomers(), Options{})
	res2, err := ExecuteHybrid(plan2, runner, ctx)
	if err != nil || res2.Size() == 0 {
		t.Fatalf("assigning continuation: %v %v", res2, err)
	}
	if got := ctx.Get("SMALL").Get("1").Scalar(); got != "1" {
		t.Fatalf("the continuation's assignment leaked into the caller: SMALL[1] = %s", got)
	}
	_ = reflect.TypeOf
}

// An IN list translates in time linear in its length. Measured in
// allocations, which do not depend on machine load: 4x the elements must cost far
// less than the 16x a quadratic fold would.
func TestInListTranslationGrowsLinearly(t *testing.T) {
	allocs := func(n int) float64 {
		prog := sel.MustCompile(inList(n))
		return testing.AllocsPerRun(3, func() {
			if _, err := Translate(prog, "mariadb", perfBindings(), Options{}); err != nil {
				t.Fatal(err)
			}
		})
	}
	a1, a4 := allocs(500), allocs(2000)
	if ratio := a4 / a1; ratio > 6 {
		t.Fatalf("allocations grew %.1fx for 4x the elements (%.0f -> %.0f): superlinear", ratio, a1, a4)
	}
}
