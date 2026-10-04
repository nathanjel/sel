package sql

import (
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
