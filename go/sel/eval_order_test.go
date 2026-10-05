package sel

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/internal/decimal"
)

// ---- plain tree versus optimised tree -------------------------------------

type selCase struct {
	file, name, setup, source string
}

func loadConformanceCases(t *testing.T) []selCase {
	t.Helper()
	files, err := filepath.Glob("../../conformance/*.selt")
	if err != nil || len(files) == 0 {
		t.Skip("conformance suite not found next to the module")
	}
	sort.Strings(files)
	var cases []selCase
	for _, f := range files {
		raw, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var cur *selCase
		section := ""
		var buf []string
		flush := func() {
			if cur == nil {
				return
			}
			text := strings.Join(buf, "\n")
			switch section {
			case "setup":
				cur.setup = text
			case "source":
				cur.source = text
			}
			buf = nil
		}
		for _, line := range strings.Split(string(raw), "\n") {
			switch {
			case strings.HasPrefix(line, "### name: "):
				flush()
				if cur != nil && cur.source != "" {
					cases = append(cases, *cur)
				}
				cur = &selCase{file: filepath.Base(f), name: strings.TrimPrefix(line, "### name: ")}
				section = ""
			case strings.HasPrefix(line, "--- "):
				flush()
				section = strings.TrimPrefix(line, "--- ")
			case line == "===":
				flush()
				if cur != nil && cur.source != "" {
					cases = append(cases, *cur)
				}
				cur = nil
				section = ""
			default:
				if section == "setup" || section == "source" {
					buf = append(buf, line)
				}
			}
		}
	}
	return cases
}

func outcome(fn func() *Value) (out string) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SelError); ok {
				out = fmt.Sprintf("!%s@%d:%d", se.Code, se.Pos.Line, se.Pos.Col)
				return
			}
			out = fmt.Sprintf("HOST PANIC: %v", r)
		}
	}()
	return fn().Dump()
}

func freshRoot(c selCase) (*Value, bool) {
	root := NewNone()
	if c.setup != "" {
		p, err := Compile(c.setup)
		if err != nil {
			return nil, false
		}
		if _, err := p.Run(root); err != nil {
			return nil, false
		}
	}
	return root, true
}

// Every conformance program must give the same value, or the same error at the
// same position, when evaluated as written, optimised, and optimised again on
// the same Program (SPEC §6.2: an optimisation is invisible).
func TestPlainAndOptimisedEvaluationAgree(t *testing.T) {
	skip := map[string]bool{ // sources far too large to run three times in a unit test
		"lim.eval-depth": true,
	}
	for _, c := range loadConformanceCases(t) {
		// The 24-* decimal cases run million-digit operands; the conformance
		// suite and the decimal oracle already grade them.
		if skip[c.name] || len(c.source) > 20000 || strings.HasPrefix(c.file, "24-") ||
			strings.Contains(c.source, "000000") {
			continue
		}
		prog, err := Compile(c.source)
		if err != nil {
			continue // a compile-time expectation; nothing to evaluate
		}
		root1, ok := freshRoot(c)
		if !ok {
			continue
		}
		plain := outcome(func() *Value { return evalNode(prog.AST(), newContext(root1)) })
		// The final context is part of the observable result.
		plainCtx := outcome(func() *Value { return root1 })
		for round := 1; round <= 2; round++ {
			root2, _ := freshRoot(c)
			opt := outcome(func() *Value {
				v, err := prog.Run(root2)
				if err != nil {
					se := err.(*SelError)
					panic(se)
				}
				return v
			})
			if opt != plain {
				t.Errorf("%s/%s (run %d): plain %s, optimised %s", c.file, c.name, round, clip(plain), clip(opt))
				break
			}
			if got := outcome(func() *Value { return root2 }); got != plainCtx {
				t.Errorf("%s/%s (run %d): final context differs: plain %s, optimised %s", c.file, c.name, round, clip(plainCtx), clip(got))
				break
			}
		}
	}
}

func clip(s string) string {
	if len(s) > 120 {
		return s[:120] + "…"
	}
	return s
}

// ---- math plans --------------------------------------------------------------

func TestMathPlanOfManyArgumentsIsNotPlannedNotWrapped(t *testing.T) {
	// 33,000 arguments need more than 16 bits of scratchpad slots. The plan used
	// to wrap around and index outside the scratchpad (a Go runtime panic).
	var sb strings.Builder
	for _, name := range []string{"MAX", "MIN"} {
		sb.Reset()
		sb.WriteString(name + "(")
		for i := 1; i <= 33000; i++ {
			if i > 1 {
				sb.WriteString(",")
			}
			fmt.Fprintf(&sb, "%d", i)
		}
		sb.WriteString(")")
		want := "33000"
		if name == "MIN" {
			want = "1"
		}
		v, err := Eval(sb.String(), NewNone())
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if got := v.Scalar(); got != want {
			t.Errorf("%s of 1..33000 = %s, want %s", name, got, want)
		}
	}
}

func TestMathPlanCoercesAfterEveryOperandIsEvaluated(t *testing.T) {
	for _, c := range []struct {
		src, code string
		col       int
	}{
		{`X = "abc"; X + MISSING`, "E_UNDEF_VAR", 10},
		{`"abc" + 1/0`, "E_DIV_ZERO", 10},
		{`MAX(TRUE, U)`, "E_UNDEF_VAR", 11},
	} {
		_, err := Eval(c.src, NewNone())
		code, pos := codeOf(err)
		if code != c.code {
			t.Errorf("%s: got %s at %d:%d, want %s", c.src, code, pos.Line, pos.Col, c.code)
		}
	}
	// A later operand's side effect on an earlier operand's value is seen.
	v, err := Eval(`A = LIST(1,2); A + LEN((A[1] = 10))`, NewNone())
	if err != nil || v.Scalar() != "12" {
		t.Errorf("operand snapshot: %v %v", v, err)
	}
}

func TestPropagatedOperandIsCoercedBeforeTheNextOperandIsEvaluated(t *testing.T) {
	// `x + 0` is copy-propagated to `x`; the plain tree coerces x there, before
	// the right operand of the enclosing `*` is even looked up.
	_, err := Eval(`X = "abc"; (X + 0) * MISSING`, NewNone())
	if code, _ := codeOf(err); code != "E_NOT_NUM" {
		t.Errorf("got %s, want E_NOT_NUM", code)
	}
}

func TestRoundAndPowerCoerceTheFirstArgumentFirst(t *testing.T) {
	for _, src := range []string{
		`Y = "1"; ROUND(NULL, IF(Y == "1", "z", 0))`,
		`Y = "1"; POWER(NULL, IF(Y == "1", 1.5, 0))`,
	} {
		_, err := Eval(src, NewNone())
		if code, _ := codeOf(err); code != "E_NULL" {
			t.Errorf("%s: got %s, want E_NULL", src, code)
		}
	}
}

// ---- optimizer rewrites ------------------------------------------------------

func TestFusedStagesKeepTheOuterPosition(t *testing.T) {
	for _, c := range []struct {
		src string
		col int
	}{
		{`NOT TAKE(TAKE(LIST(1), 3), 2)`, 5},
		{`NOT DROP(DROP(LIST(1),1),1)`, 5},
		{`NOT FILTER(LIST(1), TRUE)`, 5},
		{`X = LIST(1); NOT TAKE(SORT(X), 1)`, 18},
	} {
		prog, err := Compile(c.src)
		if err != nil {
			t.Fatal(err)
		}
		_, err = prog.Run(NewNone())
		_, pos := codeOf(err)
		if pos.Col != c.col {
			t.Errorf("%s: error at column %d, want %d", c.src, pos.Col, c.col)
		}
	}
}

func TestSortTakeFusesOnlyForALiteralCountOfAtLeastOne(t *testing.T) {
	steps := func(src string) []string {
		prog := MustCompile(src)
		var names []string
		_, ss := UnwindPipeline(prog.PhysicalAST())
		for _, s := range ss {
			names = append(names, s.S)
		}
		return names
	}
	if got := steps(`L .> SORT_BY(_) .> TAKE(2)`); len(got) != 1 || got[0] != "TOP_BY" {
		t.Errorf("literal count: %v", got)
	}
	for _, src := range []string{`L .> SORT_BY(_) .> TAKE(0)`, `L .> SORT_BY(_) .> TAKE(N)`, `L .> SORT_BY(_) .> TAKE(-1)`} {
		if got := steps(src); len(got) != 2 {
			t.Errorf("%s fused: %v", src, got)
		}
	}
}

func TestFilterFusionNeverHidesAnEarlierPredicateError(t *testing.T) {
	// The second predicate is a bare value, which raises E_NOT_BOOL by itself;
	// fused behind the first it would run element by element and fail on the
	// first element, before the first predicate's division by zero.
	_, err := Eval(`LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_K)`, NewNone())
	if code, _ := codeOf(err); code != "E_DIV_ZERO" {
		t.Errorf("got %s, want E_DIV_ZERO", code)
	}
}

func TestOptRenameVarDropsTheStaleMathPlan(t *testing.T) {
	n := NewNode(NodeBin, Pos{Line: 1, Col: 1})
	n.S = "+"
	n.L = NewNode(NodeVar, Pos{Line: 1, Col: 1})
	n.L.S = "A"
	n.R = NewNode(NodeNum, Pos{Line: 1, Col: 5})
	n.mathPlan = &mathPlan{Steps: []mathStep{{Op: "LOAD_VAR", Name: "A"}}}
	cp := optRenameVar(n, "A", "B")
	if cp.mathPlan != nil {
		t.Error("the renamed copy still carries a plan that loads the old binder")
	}
	if n.mathPlan == nil {
		t.Error("the original lost its plan")
	}
}

// ---- recovery ----------------------------------------------------------------

func TestBrokenOptimizerDoesNotPoisonLaterRuns(t *testing.T) {
	// A node the optimizer cannot handle: a FILTER whose predicate is missing.
	bad := NewNode(NodeCall, Pos{Line: 1, Col: 1})
	bad.S = "FILTER"
	bad.Items = []*Node{NewNode(NodeNull, Pos{Line: 1, Col: 1}), nil}
	prog := NewProgram("bad", bad)
	for i := 0; i < 2; i++ {
		func() {
			defer func() {
				if recover() == nil {
					t.Errorf("call %d: PhysicalAST returned normally for a node that cannot be optimised", i)
				}
			}()
			if prog.PhysicalAST() == nil {
				t.Errorf("call %d: PhysicalAST returned a nil tree", i)
			}
		}()
	}
}

func TestJoinPrefilterStateDoesNotOutliveAPanic(t *testing.T) {
	ctx := newContext(NewNone())
	ctx.joinPrefilterReport = &joinReport{}
	// Completed evaluation hands the state on (a LINK sets it for its parent).
	evalNode(NewNode(NodeNull, Pos{Line: 1, Col: 1}), ctx)
	if ctx.joinPrefilterReport == nil {
		t.Fatal("a normal return cleared the join report")
	}
	missing := NewNode(NodeVar, Pos{Line: 1, Col: 1})
	missing.S = "NOPE"
	func() {
		defer func() { _ = recover() }()
		evalNode(missing, ctx)
	}()
	if ctx.joinPrefilterReport != nil || ctx.joinPrefilter != nil {
		t.Error("a caught failure left join prefilter state behind for the next LINK")
	}
}

func TestProbesSwallowOnlySelErrors(t *testing.T) {
	if got := tryDec(func() *decimal.Dec { fail("E_RANGE", "x", Pos{}); return nil }); got != nil {
		t.Error("a SEL error was not swallowed")
	}
	defer func() {
		if r := recover(); r != "boom" {
			t.Errorf("a runtime panic was swallowed or changed: %v", r)
		}
	}()
	tryDec(func() *decimal.Dec { panic("boom") })
}
