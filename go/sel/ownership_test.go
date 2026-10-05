package sel

import (
	"errors"
	"strings"
	"testing"
)

func evalCtx(t *testing.T, src string, ctx *Value) *Value {
	t.Helper()
	v, err := Eval(src, ctx)
	if err != nil {
		t.Fatalf("Eval(%q): %v", src, err)
	}
	return v
}

func codeOf(err error) (string, Pos) {
	var se *SelError
	if errors.As(err, &se) {
		return se.Code, se.Pos
	}
	return "", Pos{}
}

// TAKE and DROP returned a slice of the source list's backing array, so
// replacing an element of the source afterwards changed the "new" list.
func TestTakeAndDropDoNotShareTheSourceBackingArray(t *testing.T) {
	src := NewList([]*Value{NewText("1"), NewText("2"), NewText("3")})
	ctx := NewNone()
	ctx.Set("A", src)
	for _, program := range []string{"TAKE(A, 2)", "DROP(A, 1)", "TAKE(A, 3)", "DROP(A, 0)"} {
		out := evalCtx(t, program, ctx)
		before := out.Dump()
		// Replace every element of the source in place.
		for i := range src.storage {
			src.storage[i] = NewText("changed")
		}
		if got := out.Dump(); got != before {
			t.Errorf("%s changed when the source's storage was rewritten: %s -> %s", program, before, got)
		}
		src.storage = []*Value{NewText("1"), NewText("2"), NewText("3")}
	}
}

// The results that are containers of their own must not alias the source's
// backing array either: DISTINCT, DEDUPE, SORT, TOP, TAKE, DROP, MAP, FILTER,
// LIST, `,`. Rewriting the source after the fact must leave the result alone.
func TestCollectionResultsAreDetachedFromTheSource(t *testing.T) {
	programs := []string{
		"DISTINCT(A)", "DEDUPE(A)", "SORT(A)", "SORT_DESC(A)", "SORT_BY(A, _)", "TOP(A, 2)",
		"TAKE(A, 2)", "DROP(A, 1)", "MAP(A, _)", "FILTER(A, TRUE)", "LIST(A)", "(A, 9)",
	}
	for _, program := range programs {
		src := NewList([]*Value{NewText("1"), NewText("2"), NewText("3")})
		ctx := NewNone()
		ctx.Set("A", src)
		out := evalCtx(t, program, ctx)
		before := out.Dump()
		for i := range src.storage {
			src.storage[i] = NewText("changed")
		}
		if got := out.Dump(); got != before {
			t.Errorf("%s changed when the source's storage was rewritten: %s -> %s", program, before, got)
		}
	}
}

// SPEC §3.4: what the aggregates, LIST, RECORD and `,` collect is a copy, so an
// assignment through the source afterwards is not visible in the result.
func TestCollectorsCopyWhatTheyCollect(t *testing.T) {
	for _, program := range []string{
		`X = LIST(RECORD("k",1)); R = MAP(X, _); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = FILTER(X, TRUE); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = SORT(X); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = SORT_BY(X, 1); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = TOP(X, 1); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = TOP_BY(X, 1, 1); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = BUCKET(X, 1, _); X[1]["k"] = 9; R[1][1]["k"]`,
		`X = LIST(RECORD("k",1)); R = BUCKET(X, 1); X[1]["k"] = 9; R["1"][1]["k"]`,
		`X = LIST(RECORD("k",1)); R = LIST(X[1]); X[1]["k"] = 9; R[1]["k"]`,
		`X = LIST(RECORD("k",1)); R = RECORD("r", X[1]); X[1]["k"] = 9; R["r"]["k"]`,
	} {
		if got := evalCtx(t, program, nil).Dump(); got != `t"1"` {
			t.Errorf("%s = %s, want t\"1\" (a copy)", program, got)
		}
	}
}

// SPEC §6.4: an assigned value counts from where it is stored, and the error is
// at the assignment target, not later at 0:0 when something walks the value.
func TestAssignedValueDepthCountsThePath(t *testing.T) {
	chain := func(n int) string { return "A" + strings.Repeat("[1]", n) }
	prog := chain(150) + " = 1; " + "B" + strings.Repeat("[1]", 50) + " = A; 7"
	_, err := Eval(prog, nil)
	code, pos := codeOf(err)
	if code != "E_DEPTH" || pos.Line == 0 {
		t.Errorf("path 50 + value 151: got %q at %+v, want E_DEPTH with a position", code, pos)
	}
	ok := chain(150) + " = 1; " + "B" + strings.Repeat("[1]", 49) + " = A; 7"
	if _, err := Eval(ok, nil); err != nil {
		t.Errorf("path 49 + value 151 = 200 levels is legal, got %v", err)
	}
}

// SPEC §3.4, §6.4: a constructor that builds a value past the cap fails at the
// constructing node.
func TestConstructorDepthIsReportedAtTheConstructor(t *testing.T) {
	deep := "A" + strings.Repeat("[1]", 199) + " = 1; "
	for _, tail := range []string{"LIST(A); 7", `RECORD("k", A); 7`} {
		_, err := Eval(deep+tail, nil)
		code, pos := codeOf(err)
		if code != "E_DEPTH" || pos.Line != 1 || pos.Col != len(deep)+1 {
			t.Errorf("%s: got %q at %+v, want E_DEPTH at the call (col %d)", tail, code, pos, len(deep)+1)
		}
	}
	if _, err := Eval("A"+strings.Repeat("[1]", 198)+" = 1; LIST(A); 7", nil); err != nil {
		t.Errorf("LIST of a 199-level value is 200 levels and legal, got %v", err)
	}
}

// Through the language: CEIL/FLOOR carry past the integer-digit cap.
func TestCeilFloorCarryIsE_RANGEAtTheCall(t *testing.T) {
	for _, src := range []string{
		`CEIL(REPEAT("9", 1000000) & ".5")`,
		`FLOOR("-" & REPEAT("9", 1000000) & ".5")`,
	} {
		_, err := Eval(src, nil)
		code, pos := codeOf(err)
		if code != "E_RANGE" || pos.Line != 1 || pos.Col != 1 {
			t.Errorf("%s: got %q at %+v, want E_RANGE at 1:1", src, code, pos)
		}
	}
}

// The public keyed-list constructor refuses a mismatch instead of truncating.
func TestNewListWithKeysMismatchIsBadArg(t *testing.T) {
	defer func() {
		r := recover()
		se, ok := r.(*SelError)
		if !ok || se.Code != "E_BAD_ARG" {
			t.Fatalf("got %v, want a SelError E_BAD_ARG", r)
		}
	}()
	NewListWithKeys([]*Value{NewText("1")}, nil)
}
