package sel

import (
	"fmt"
	"math/rand"
	"strings"
	"testing"
)

// Item 1 (2026-10-01): a math plan may keep its intermediates in registers it
// reuses. What must hold whatever it keeps: a plan's result is its own, a plan
// can be re-entered (a leaf that runs another program), a failure caught inside
// a plan leaves nothing behind, and a planned run equals the run as written --
// on big operands, run after run on the same Program.

const (
	planA = "123456789012345678901234567890123456789012345678901234567890.1234567"
	planB = "-98765432109876543210987654321098765432109876543210987.654"
	planC = "5555555555555555555555555555555555555555555.555555555555"
)

var planSetup = fmt.Sprintf("A = %s; B = %s; C = %s; ", planA, planB, planC)

// asWrittenAndPlanned runs src as written once and planned three times on the
// same Program, and fails unless every outcome (value or error) is the same.
func asWrittenAndPlanned(t *testing.T, src string) string {
	t.Helper()
	prog := MustCompile(src)
	outcome := func(v *Value, err error) string {
		if err != nil {
			se := err.(*SelError)
			return fmt.Sprintf("error %s at %d:%d", se.Code, se.Pos.Line, se.Pos.Col)
		}
		return v.Dump()
	}
	want := outcome(prog.RunAsWritten(NewNone()))
	for run := 1; run <= 3; run++ {
		if got := outcome(prog.Run(NewNone())); got != want {
			t.Fatalf("%s\nrun %d planned: %s\nas written: %s", src, run, clip(got), clip(want))
		}
	}
	return want
}

func TestPlanResultsAreTheirOwnAcrossRuns(t *testing.T) {
	got := asWrittenAndPlanned(t, planSetup+
		`X = A * B + C; Y = C * A - B; Z = B * B + A; W = X * Y - Z; LIST(X, Y, Z, W, A, B, C)`)
	for _, v := range []string{planA, planB, planC} {
		if !strings.Contains(got, `t"`+v+`"`) {
			t.Fatalf("an operand changed: %s not in %s", v, clip(got))
		}
	}
}

func TestPlanReentersThroughAHostFunction(t *testing.T) {
	inner := MustCompile(planSetup + `A * A - B * B + C`)
	RegisterFunction("ITEM1_INNER_PLAN", 0, 0, func(args *Args) *Value {
		v, err := inner.Run(NewNone())
		if err != nil {
			panic(err)
		}
		return v
	})
	asWrittenAndPlanned(t, planSetup+`A * B + ITEM1_INNER_PLAN() * C - A * A`)
	asWrittenAndPlanned(t, planSetup+`L = MAP(LIST(1, 2, 3), K, A * K + ITEM1_INNER_PLAN() - B * K); L`)
}

func TestPlanFailureCaughtInsideAPlanLeavesNothingBehind(t *testing.T) {
	asWrittenAndPlanned(t, planSetup+
		`R = (A * B + LEN(V + V + V) * C) ?? -1; S = A * B + C; T = (B * B - (W * A) * C) ?? 7; LIST(R, S, T, A * B + C)`)
}

func TestPlannedAndAsWrittenAgreeOnBigArithmetic(t *testing.T) {
	rng := rand.New(rand.NewSource(20261001))
	setup := planSetup + `D = -0.00000000000000000000000000000000000071; E = 340282366920938463463374607431768211457; `
	names := []string{"A", "B", "C", "D", "E"}
	var gen func(depth int) string
	gen = func(depth int) string {
		if depth == 0 || rng.Intn(5) == 0 {
			switch rng.Intn(6) {
			case 0:
				return fmt.Sprintf("%d.%d", rng.Intn(1000), rng.Intn(100))
			case 1:
				return fmt.Sprint(rng.Intn(10))
			}
			return names[rng.Intn(len(names))]
		}
		l, r := gen(depth-1), gen(depth-1)
		switch rng.Intn(12) {
		case 0:
			return "-(" + l + ")"
		case 1:
			return "ABS(" + l + " - " + r + ")"
		case 2:
			return "MAX(" + l + ", " + r + ")"
		case 3:
			return "(" + l + " / " + r + ")"
		case 4, 5, 6:
			return "(" + l + " * " + r + ")"
		case 7, 8:
			return "(" + l + " - " + r + ")"
		}
		return "(" + l + " + " + r + ")"
	}
	for i := 0; i < 300; i++ {
		asWrittenAndPlanned(t, setup+"R1 = "+gen(4)+"; R2 = "+gen(3)+"; LIST(R1, R2, R1 * R2 - R1, A, B, D)")
	}
}
