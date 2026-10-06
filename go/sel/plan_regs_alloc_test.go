package sel

import "testing"

// What one evaluation of a math plan allocates, warm, on operands of at
// most 60 digits (math/big takes no pooled Karatsuba stack at these sizes, so the
// counts are deterministic, -race included). Lower these when the code gets
// cheaper; never raise them.
const planAllocVars = `ZR = 123456789012345678901234567890123456789012345.1234567890; ` +
	`ZI = -98765432109876543210987654321098765432109876.5432109876; ` +
	`CR = -0.5612345678; CI = 0.2666666667; ` +
	`X = 12345678901234567890123.25; Y = -9876543210987654321.5; Z = 42.125; ` +
	`SR = 0.1234567890123456789012345678901234567890123456789; SI = -0.9876543210987654321098765432109876543210987654321`

func planAllocations(t *testing.T, expr string) float64 {
	t.Helper()
	root := NewNone()
	if _, err := MustCompile(planAllocVars).Run(root); err != nil {
		t.Fatal(err)
	}
	node := MustCompile(expr).PhysicalAST()
	if node.mathPlan == nil {
		t.Fatalf("%s: no plan", expr)
	}
	ctx := newContext(root)
	evalNode(node, ctx) // warm: registers and caches
	return testing.AllocsPerRun(50, func() { evalNode(node, ctx) })
}

func TestMathPlanAllocationBudgets(t *testing.T) {
	for _, c := range []struct {
		expr   string
		budget float64
	}{
		{"X * Y + Z", 4},
		{"ZR * ZR - ZI * ZI + CR", 4},
		{"2.0 * ZR * ZI + CI", 4},
		{"ZR * ZR + ZI * ZI", 4},
		{"X + 1", 4},
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
	if got := testing.AllocsPerRun(50, func() { prog.Run(root) }); got != 7 {
		t.Errorf("one Run of X * Y + Z: %v allocations, budget 7", got)
	}
}

// exprAllocations is planAllocations for any expression, planned or not.
func exprAllocations(t *testing.T, expr string) float64 {
	t.Helper()
	root := NewNone()
	if _, err := MustCompile(planAllocVars).Run(root); err != nil {
		t.Fatal(err)
	}
	node := MustCompile(expr).PhysicalAST()
	ctx := newContext(root)
	evalNode(node, ctx) // warm
	return testing.AllocsPerRun(50, func() { evalNode(node, ctx) })
}

// Mandelbrot's escape test while z is still small: the bit lengths cannot
// order the sum against 4.0, so the comparison brings 4.0 to the sum's scale
// (98 fractional digits), a copy of the sum's size. The literal 4.0 itself
// costs nothing: an operator reads the value its node keeps (operand).
func TestScaledComparisonAllocationBudget(t *testing.T) {
	if got := exprAllocations(t, "SR * SR + SI * SI > 4.0"); got != 7 {
		t.Errorf("SR * SR + SI * SI > 4.0: %v allocations, budget 7", got)
	}
}
