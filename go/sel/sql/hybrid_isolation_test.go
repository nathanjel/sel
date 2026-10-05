package sql

import (
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

func TestHybridIsolation_PureMemoryMutatingCallback(t *testing.T) {
	sel.RegisterFunction("GO_POKE_1", 1, 1, func(args *sel.Args) *sel.Value {
		val := args.Val(0)
		val.Set("k", sel.NewText("9"))
		return val
	})

	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan := PlanHybrid(sel.MustCompile("GO_POKE_1(A)"), "sqlite", NewBindings(nil), Options{})
	if !plan.PureMemory {
		t.Fatalf("expected pure memory plan")
	}

	res, err := ExecuteHybrid(plan, nil, ctx)
	if err != nil {
		t.Fatal(err)
	}
	if res.Get("k").AsText(sel.Pos{}) != "9" {
		t.Fatalf("expected result '9', got %s", res.Get("k").AsText(sel.Pos{}))
	}
	if ctx.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated: expected '1', got %s", ctx.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_CallbackMutatesThenRaises(t *testing.T) {
	sel.RegisterFunction("GO_POKE_ERR", 1, 1, func(args *sel.Args) *sel.Value {
		val := args.Val(0)
		val.Set("k", sel.NewText("99"))
		panic("custom callback panic")
	})

	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan := PlanHybrid(sel.MustCompile("GO_POKE_ERR(A)"), "sqlite", NewBindings(nil), Options{})

	defer func() {
		_ = recover()
		if ctx.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
			t.Fatalf("caller context mutated on error: expected '1', got %s", ctx.Get("A").Get("k").AsText(sel.Pos{}))
		}
	}()

	_, _ = ExecuteHybrid(plan, nil, ctx)
}

func TestHybridIsolation_NestedInAggregateOrLazy(t *testing.T) {
	sel.RegisterFunction("GO_POKE_NESTED", 1, 1, func(args *sel.Args) *sel.Value {
		val := args.Val(0)
		val.Set("k", sel.NewText("999"))
		return val
	})

	// Inside lazy branch IF:
	ctx1 := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan1 := PlanHybrid(sel.MustCompile("IF(TRUE, GO_POKE_NESTED(A), 0)"), "sqlite", NewBindings(nil), Options{})
	res1, err := ExecuteHybrid(plan1, nil, ctx1)
	if err != nil {
		t.Fatal(err)
	}
	if res1.Get("k").AsText(sel.Pos{}) != "999" {
		t.Fatalf("expected '999', got %s", res1.Get("k").AsText(sel.Pos{}))
	}
	if ctx1.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated in IF: expected '1', got %s", ctx1.Get("A").Get("k").AsText(sel.Pos{}))
	}

	// Inside aggregate body MAP:
	ctx2 := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan2 := PlanHybrid(sel.MustCompile("MAP(LIST(1), GO_POKE_NESTED(A))"), "sqlite", NewBindings(nil), Options{})
	res2, err := ExecuteHybrid(plan2, nil, ctx2)
	if err != nil {
		t.Fatal(err)
	}
	if res2.Values()[0].Get("k").AsText(sel.Pos{}) != "999" {
		t.Fatalf("expected '999', got %s", res2.Values()[0].Get("k").AsText(sel.Pos{}))
	}
	if ctx2.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated in MAP: expected '1', got %s", ctx2.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_LowerLevelDefinitionAPI(t *testing.T) {
	sel.Define(&sel.Spec{
		Name: "GO_POKE_LOW",
		Min:  1,
		Max:  1,
		Fn: func(args *sel.Args, ctx *sel.Context) *sel.Value {
			val := args.Val(0)
			val.Set("k", sel.NewText("888"))
			return val
		},
	})

	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan := PlanHybrid(sel.MustCompile("GO_POKE_LOW(A)"), "sqlite", NewBindings(nil), Options{})
	res, err := ExecuteHybrid(plan, nil, ctx)
	if err != nil {
		t.Fatal(err)
	}
	if res.Get("k").AsText(sel.Pos{}) != "888" {
		t.Fatalf("expected '888', got %s", res.Get("k").AsText(sel.Pos{}))
	}
	if ctx.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated via Define: expected '1', got %s", ctx.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_SqlPrefixFollowedByCallback(t *testing.T) {
	sel.RegisterFunction("GO_POKE_SPLIT", 1, 1, func(args *sel.Args) *sel.Value {
		val := args.Val(0)
		val.Set("k", sel.NewText("777"))
		return val
	})

	b := ordersAndCustomers()
	src := `ORDERS .> SORT_BY(_["id"]) .> MAP(GO_POKE_SPLIT(A))`
	plan := PlanHybrid(sel.MustCompile(src), "sqlite", b, Options{})
	if plan.PureMemory || plan.PureSql || plan.SqlStatement == nil {
		t.Fatalf("expected hybrid split plan, got %+v", plan)
	}

	calls := 0
	runner := func(q string, params []*sel.Value) (*sel.Value, error) {
		calls++
		return sel.NewList([]*sel.Value{
			sel.NewRecordFromEntries([]sel.Entry{{Key: "id", Val: sel.NewInt(1)}}),
		}), nil
	}

	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})

	res, err := ExecuteHybrid(plan, runner, ctx)
	if err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("expected runner to be called once, called %d times", calls)
	}
	if res.Values()[0].Get("k").AsText(sel.Pos{}) != "777" {
		t.Fatalf("expected '777', got %s", res.Values()[0].Get("k").AsText(sel.Pos{}))
	}
	if ctx.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated in split plan: expected '1', got %s", ctx.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_SamePlanExecutesTwiceNoMutationLeak(t *testing.T) {
	sel.RegisterFunction("GO_POKE_TWICE", 1, 1, func(args *sel.Args) *sel.Value {
		val := args.Val(0)
		val.Set("k", sel.NewText("555"))
		return val
	})

	plan := PlanHybrid(sel.MustCompile("GO_POKE_TWICE(A)"), "sqlite", NewBindings(nil), Options{})
	ctx1 := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	ctx2 := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})

	_, err := ExecuteHybrid(plan, nil, ctx1)
	if err != nil {
		t.Fatal(err)
	}
	_, err = ExecuteHybrid(plan, nil, ctx2)
	if err != nil {
		t.Fatal(err)
	}

	if ctx1.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("ctx1 mutated: expected '1', got %s", ctx1.Get("A").Get("k").AsText(sel.Pos{}))
	}
	if ctx2.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("ctx2 mutated: expected '1', got %s", ctx2.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_BuiltinReadOnlyContinuationSharesContext(t *testing.T) {
	inner := sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})
	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: inner},
	})
	plan := PlanHybrid(sel.MustCompile("A[\"k\"]"), "sqlite", NewBindings(nil), Options{})
	res, err := ExecuteHybrid(plan, nil, ctx)
	if err != nil {
		t.Fatal(err)
	}
	if res.AsText(sel.Pos{}) != "1" {
		t.Fatalf("expected '1', got %s", res.AsText(sel.Pos{}))
	}
}

func TestHybridIsolation_BuiltinAssignsNestedField(t *testing.T) {
	ctx := sel.NewRecordFromEntries([]sel.Entry{
		{Key: "A", Val: sel.NewRecordFromEntries([]sel.Entry{{Key: "k", Val: sel.NewText("1")}})},
	})
	plan := PlanHybrid(sel.MustCompile("A[\"k\"] = \"99\"; A"), "sqlite", NewBindings(nil), Options{})
	res, err := ExecuteHybrid(plan, nil, ctx)
	if err != nil {
		t.Fatal(err)
	}
	if res.Get("k").AsText(sel.Pos{}) != "99" {
		t.Fatalf("expected '99', got %s", res.Get("k").AsText(sel.Pos{}))
	}
	if ctx.Get("A").Get("k").AsText(sel.Pos{}) != "1" {
		t.Fatalf("caller context mutated on assignment: expected '1', got %s", ctx.Get("A").Get("k").AsText(sel.Pos{}))
	}
}

// A source that the program reassigns and a later step reads back as a value:
// hybrid output equals run() output, and the SQL prefix still takes the DROP once.
// The runner answers the SQL prefix by evaluating SqlPrefixAst in memory, as the
// database would.
func TestHybridRereadOfAReassignedSource(t *testing.T) {
	sel.RegisterFunction("GO_HOSTF_REREAD", 1, 1, func(args *sel.Args) *sel.Value {
		return sel.NewText("x" + args.Val(0).AsText(sel.Pos{}))
	})
	orders := func() *sel.Value {
		rows := make([]*sel.Value, 6)
		for i := range rows {
			rows[i] = sel.NewRecordFromEntries([]sel.Entry{{Key: "id", Val: sel.NewInt(int64(i + 1))}})
		}
		ctx := sel.NewNone()
		ctx.Set("ORDERS", sel.NewList(rows))
		return ctx
	}
	src := `ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3) .> MAP(RECORD("n", COUNT(ORDERS), "x", GO_HOSTF_REREAD(_["id"])))`
	prog := sel.MustCompile(src)
	want, err := prog.Run(orders())
	if err != nil {
		t.Fatal(err)
	}
	plan := PlanHybrid(prog, "sqlite", ordersAndCustomers(), Options{})
	if !plan.IsHybrid || plan.SqlStatement == nil {
		t.Fatalf("expected a hybrid plan, got %+v", plan)
	}
	if got := plan.SqlStatement.AsStatement(ModeInline); !strings.Contains(got, "LIMIT 3 OFFSET 2") {
		t.Errorf("SQL prefix: %s", got)
	}
	t.Logf("SQL %s", plan.SqlStatement.AsStatement(ModeInline))
	runner := func(q string, params []*sel.Value) (*sel.Value, error) {
		return sel.NewProgram("", plan.SqlPrefixAst).Run(orders())
	}
	caller := orders()
	before := caller.Dump()
	got, err := ExecuteHybrid(plan, runner, caller)
	if err != nil {
		t.Fatal(err)
	}
	if got.Dump() != want.Dump() {
		t.Fatalf("hybrid %s, run() %s", got.Dump(), want.Dump())
	}
	// Neither the helper's assignment nor the injected source variable reaches
	// the caller.
	if after := caller.Dump(); after != before {
		t.Fatalf("caller context changed: %s -> %s", before, after)
	}
}
