package sel

import (
	"strings"
	"testing"
)

// An operator reads a literal operand as the one value its node keeps
// (operand), not a fresh value per evaluation. These tests hold that to "cannot
// be told apart": the value never reaches anything that could keep or change
// it -- a result, a host function, a collector -- and E_DEPTH is raised where it
// always was.

func TestALiteralOperandCostsNothing(t *testing.T) {
	// The BOOL the comparison yields is the one allocation; the literal is not.
	for _, expr := range []string{`X $== "abc"`, `X < 5`, `"5" < X`, `X EQL 5`} {
		if got := exprAllocations(t, expr); got != 1 {
			t.Errorf("%s: %v allocations, want 1", expr, got)
		}
	}
}

// literals collects the values the literal nodes of a tree keep.
func literals(n *Node, into map[*Value]bool) {
	if n == nil {
		return
	}
	if n.lit != nil {
		into[n.lit] = true
	}
	literals(n.L, into)
	literals(n.R, into)
	for _, item := range n.Items {
		literals(item, into)
	}
}

// heldLiteral reports whether v, or anything inside it, is one of lits.
func heldLiteral(v *Value, lits map[*Value]bool) bool {
	if v == nil {
		return false
	}
	if lits[v] {
		return true
	}
	for _, child := range v.Values() {
		if heldLiteral(child, lits) {
			return true
		}
	}
	return false
}

func TestALiteralsValueNeverLeavesTheOperator(t *testing.T) {
	var handed []*Value
	RegisterFunction("COND_TEST_HOLD", 1, 1, func(a *Args) *Value {
		handed = append(handed, a.Val(0))
		return a.Val(0)
	})
	for _, src := range []string{
		`"abc"`,
		`5`,
		`IF(TRUE, "abc", "x")`,
		`IF(1 == 1, 7, 8)`,
		`IF("a" $== "b", 7, 8)`,
		`COND(1 == 2, "a", 2 == 2, "b", "c")`,
		`NULL ?? "abc"`,
		`"abc" ?? "x"`,
		`LIST("abc", 5)`,
		`RECORD("k", "abc", "n", 5)`,
		`MAP(LIST(1, 2), "abc")`,
		`FILTER(LIST("DE", "PL"), _ $== "DE")`,
		`COND_TEST_HOLD("abc")`,
		`COND_TEST_HOLD(5) + 1`,
		`IF(COND_TEST_HOLD("DE") $== "DE", COND_TEST_HOLD(5), 0)`,
		`MAP(LIST("DE"), COND_TEST_HOLD("DE"))`,
	} {
		prog := MustCompile(src)
		lits := map[*Value]bool{}
		literals(prog.AST(), lits)
		literals(prog.PhysicalAST(), lits)
		handed = handed[:0]
		res, err := prog.Run(NewNone())
		if err != nil {
			t.Fatalf("%s: %v", src, err)
		}
		if heldLiteral(res, lits) {
			t.Errorf("%s: the result holds a literal node's own value", src)
		}
		for _, v := range handed {
			if lits[v] {
				t.Errorf("%s: a host function was handed a literal node's own value", src)
			}
		}
		// Changed through the API, the result is the host's: the next run starts clean.
		before := res.Dump()
		res.Set("changed", NewInt(1))
		again, _ := prog.Run(NewNone())
		if again.Dump() != before {
			t.Errorf("%s: after the host changed a result the next run gives %s, want %s", src, again.Dump(), before)
		}
	}
}

func TestAHostFunctionThatWritesItsArgumentDoesNotReachALiteral(t *testing.T) {
	// COND_TEST_MARK counts the marks on what it is handed: 1 on a fresh value.
	RegisterFunction("COND_TEST_MARK", 1, 1, func(a *Args) *Value {
		v := a.Val(0)
		n := int64(1)
		if prev := v.Get("mark"); prev != nil {
			n += prev.AsDecimal(Pos{}).Digits.Int64()
		}
		v.Set("mark", NewInt(n))
		return NewInt(n)
	})
	src := `COND_TEST_MARK("DE") + COND_TEST_MARK(5) + COUNT(FILTER(LIST("DE"), COND_TEST_MARK("DE") == 1 AND _ $== "DE"))`
	prog := MustCompile(src)
	for run := 1; run <= 3; run++ {
		res, err := prog.Run(NewNone())
		if err != nil {
			t.Fatal(err)
		}
		if got := res.Dump(); got != `t"3"` {
			t.Errorf("run %d: %s = %s, want 3", run, src, got)
		}
	}
}

// chain is head followed by n left-leaning `& "a"` steps: shallow to parse,
// and head sits n levels deeper than it would alone.
func chain(head string, n int) string {
	return head + strings.Repeat(` & "a"`, n)
}

func TestALiteralOperandsDepth(t *testing.T) {
	// The answers are the evaluator's as they were when every literal operand
	// was evaluated as a node of its own (a6ab7f8).
	for _, c := range []struct {
		src  string
		want string
	}{
		// The innermost & is at the cap, so its literal operands are past it:
		// the left one is reported.
		{strings.Repeat(`"a" & `, 200) + `"a"`, "!E_DEPTH@1:1"},
		{strings.Repeat(`"a" & `, 199) + `"a"`, `t"` + strings.Repeat("a", 200) + `"`},
		// A comparison of two literals at the cap, past it, and short of it.
		{chain(`("a" $== "b")`, 199), "!E_DEPTH@1:2"},
		{chain(`(5 < 7)`, 199), "!E_DEPTH@1:2"},
		{chain(`(5 < 7)`, 200), "!E_DEPTH@1:4"},
		{chain(`(5 < 7)`, 198), "!E_NOT_TEXT@1:4"},
		{chain(`("5" < 7.5)`, 199), "!E_DEPTH@1:2"},
	} {
		if got := runOnce(c.src, NewNone()); got != c.want {
			t.Errorf("%.60s…: got %.80s, want %.80s", c.src, got, c.want)
		}
	}
}
