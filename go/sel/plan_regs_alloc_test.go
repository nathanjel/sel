package sel

import "testing"

// Item 1: what one evaluation of a math plan allocates, warm, on operands of at
// most 60 digits (math/big takes no pooled Karatsuba stack at these sizes, so the
// counts are deterministic, -race included). Lower these when the code gets
// cheaper; never raise them.
const planAllocVars = `ZR = 123456789012345678901234567890123456789012345.1234567890; ` +
	`ZI = -98765432109876543210987654321098765432109876.5432109876; ` +
	`CR = -0.5612345678; CI = 0.2666666667; ` +
	`X = 12345678901234567890123.25; Y = -9876543210987654321.5; Z = 42.125`

func planAllocations(t *testing.T, expr string) float64 {
	t.Helper()
	root := NewNone()
	if _, err := MustCompile(planAllocVars).Run(root); err != nil {
		t.Fatal(err)
	}
	node := MustCompile(expr).PhysicalAST()
	if node.MathPlan == nil {
		t.Fatalf("%s: no plan", expr)
	}
	ctx := NewContext(root)
	EvalNode(node, ctx) // warm: registers and caches
	return testing.AllocsPerRun(50, func() { EvalNode(node, ctx) })
}

func TestMathPlanAllocationBudgets(t *testing.T) {
	for _, c := range []struct {
		expr   string
		budget float64
	}{
		{"X * Y + Z", 7},
		{"ZR * ZR - ZI * ZI + CR", 16},
		{"2.0 * ZR * ZI + CI", 12},
		{"ZR * ZR + ZI * ZI", 10},
		{"X + 1", 6},
	} {
		if got := planAllocations(t, c.expr); got != c.budget {
			t.Errorf("%s: %v allocations, budget %v", c.expr, got, c.budget)
		}
	}
}

// One Run of a small program: a Context per Run must not cost more than the
// registers it saves.
func TestOneRunOfASmallPlanAllocationBudget(t *testing.T) {
	root := NewNone()
	if _, err := MustCompile(planAllocVars).Run(root); err != nil {
		t.Fatal(err)
	}
	prog := MustCompile("X * Y + Z")
	if _, err := prog.Run(root); err != nil {
		t.Fatal(err)
	}
	if got := testing.AllocsPerRun(50, func() { prog.Run(root) }); got != 8 {
		t.Errorf("one Run of X * Y + Z: %v allocations, budget 8", got)
	}
}
