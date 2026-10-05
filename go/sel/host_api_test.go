package sel

import (
	"sync"
	"testing"

	"github.com/nathanjel/sel/go/internal/manifest"
)

// Host API misuse and process-level guarantees, in this host's own lane. The
// same contracts are probed on every host by tools/api.* (tools/api-pins.txt); the
// Go-only mechanics below have no equivalent elsewhere.

// A nil function is refused when it is registered, as an unusable name or
// arity is, and never lands in the table to panic later inside Run.
func TestRegisterFunctionRefusesNilFunction(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("RegisterFunction accepted a nil function")
		}
		if lookup("T12_NIL_FN") != nil {
			t.Error("a refused registration left an entry in the function table")
		}
	}()
	RegisterFunction("T12_NIL_FN", 0, 1, nil)
}

// Every name in the generated manifest is defined by some module. (JS holds
// this at load, with assertManifestCovered; this is the check the test lane can
// make today.)
func TestEveryManifestBuiltinIsDefined(t *testing.T) {
	for name := range manifest.Builtins {
		if lookup(name) == nil {
			t.Errorf("spec/builtins.json names %s but no module defines it", name)
		}
	}
}

// An argument the call does not have is a SEL error at the call, not an index-out-of-range panic
// (the API probe host.fn.arg.out-of-range).
func TestHostFunctionReadingAMissingArgumentIsASelError(t *testing.T) {
	RegisterFunction("T12_OOB", 1, 2, func(a *Args) *Value {
		if a.Count() > 1 {
			return NewText(a.Text(1))
		}
		return NewText(a.Text(5))
	})
	// A runtime panic must fail this test, not the whole test binary.
	defer func() {
		if r := recover(); r != nil {
			t.Errorf("reading a missing argument panicked: %v", r)
		}
	}()
	_, err := Eval(`T12_OOB("x")`, nil)
	se, ok := err.(*SelError)
	if !ok || se.Code != "E_BAD_ARG" {
		t.Errorf("want SelError E_BAD_ARG, got %T %v", err, err)
	}
}

// Two logical clients in one process: one keeps failing, the other keeps succeeding, over
// their own contexts and one shared compiled program each. Neither may see the other's error,
// scratch state or half-built values.
func TestTwoClientsInOneProcess(t *testing.T) {
	failing := MustCompile(`A / B`)
	working := MustCompile(`A / B`)
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		ctx := NewNone().Set("A", NewInt(1)).Set("B", NewInt(0))
		for i := 0; i < 200; i++ {
			if _, err := failing.Run(ctx); err == nil {
				t.Error("division by zero did not fail")
				return
			} else if se, ok := err.(*SelError); !ok || se.Code != "E_DIV_ZERO" {
				t.Errorf("want E_DIV_ZERO, got %v", err)
				return
			}
		}
	}()
	go func() {
		defer wg.Done()
		ctx := NewNone().Set("A", NewInt(6)).Set("B", NewInt(3))
		for i := 0; i < 200; i++ {
			v, err := working.Run(ctx)
			if err != nil || v.AsText(Pos{}) != "2" {
				t.Errorf("run %d: got %v, %v", i, v, err)
				return
			}
		}
	}()
	wg.Wait()
}

// After a run that failed part way (an error inside an aggregate body, a deep
// value, an assignment that had already happened), the next run on a fresh
// context is unaffected: no scratch depth, binder frame or join state is left behind.
func TestRunAfterCaughtErrorsIsClean(t *testing.T) {
	bad := []string{
		`MAP((1, 2, 3), _ / 0)`,
		`A = 1; B = 1 / 0`,
		`LINK(LIST(RECORD("k", 1)), LIST(RECORD("k", 1)), A, B, A["k"] == B["nope"])`,
		`SORT_BY((3, 1, 2), _ / 0)`,
	}
	for round := 0; round < 3; round++ {
		for _, src := range bad {
			if _, err := Eval(src, nil); err == nil {
				t.Errorf("%s: no error", src)
			}
		}
		v, err := Eval(`SUM((1, 2, 3), _ * 2) & "|" & COUNT(SORT_BY((3, 1, 2), -_))`, nil)
		if err != nil || v.AsText(Pos{}) != "12|3" {
			t.Fatalf("round %d after errors: got %v, %v", round, v, err)
		}
	}
}
