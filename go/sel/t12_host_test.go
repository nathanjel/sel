package sel

import (
	"reflect"
	"strings"
	"sync"
	"testing"
)

// T12 (Go lane): flow-sensitive dependencies() (SPEC 8), the manifest coverage check at
// load (GO-C41), and the argument reader's refusal of an argument the call does not have
// (SPEC 8.1). The shared contract is also probed on every host by tools/api.* and pinned
// in tools/api-pins.txt.

func TestDependenciesAreFlowSensitive(t *testing.T) {
	cases := []struct {
		src  string
		want []string
	}{
		{`A + 1; A = 2`, []string{"A"}},
		{`X += 1`, []string{"X"}},
		{`A[1] += 1`, []string{"A"}},
		{`A[1] = 2`, []string{}},
		{`A = A + 1`, []string{"A"}},
		{`A = 1; A + B`, []string{"B"}},
		{`IF(X, A = 1, 0); A`, []string{"A", "X"}},
		{`IF(X, A = 1, A = 2); A`, []string{"X"}},
		{`IF(X, A = 1); A`, []string{"A", "X"}},
		{`X AND (A = 1); A`, []string{"A", "X"}},
		{`X OR (A = 1); A`, []string{"A", "X"}},
		{`X ?? (A = 1); A`, []string{"A", "X"}},
		{`X ??? (A = 1); A`, []string{"A", "X"}},
		{`MAP(L, A = _); A`, []string{"A", "L"}},
		{`COND(X, A = 1, Y, A = 2, A = 3); A`, []string{"X", "Y"}},
		{`LEFT("abc", (N = 2)); N`, []string{}},
		// The index of a target runs before the right side; a plain indexed store
		// creates the variable and reads only the index.
		{`A[I] = 1; A`, []string{"I"}},
		{`A[1][I] += J`, []string{"A", "I", "J"}},
		// Inside an aggregate body a name assigned earlier in the same body is not a
		// dependency, and nothing it assigns is definite afterwards.
		{`MAP(L, (T = _; T + 1)); T`, []string{"L", "T"}},
		// Binders are never dependencies.
		{`ALL(L, I, I > 0)`, []string{"L"}},
	}
	for _, c := range cases {
		got := MustCompile(c.src).Dependencies()
		if len(got) == 0 && len(c.want) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: dependencies = %v, want %v", c.src, got, c.want)
		}
	}
}

func TestDependenciesKeepTheDepthCap(t *testing.T) {
	if got := MustCompile("A" + strings.Repeat("+A", 150)).Dependencies(); !reflect.DeepEqual(got, []string{"A"}) {
		t.Fatalf("got %v", got)
	}
	// A program that could not be evaluated for depth cannot have its dependencies
	// computed either: the same error at the same node.
	var got interface{}
	func() {
		defer func() { got = recover() }()
		MustCompile("A" + strings.Repeat("+A", 250)).Dependencies()
	}()
	if se, ok := got.(*SelError); !ok || se.Code != "E_DEPTH" {
		t.Errorf("want an E_DEPTH SelError, got %v", got)
	}
}

// GO-C41: a manifest name no module defined is refused on first use, not found later
// as an unknown-function error at parse time.
func TestManifestCoverageIsCheckedAtLoad(t *testing.T) {
	registryMu.Lock()
	saved := funcTable["ABS"]
	delete(funcTable, "ABS")
	registryMu.Unlock()
	savedMsg := manifestMissing
	manifestOnce = sync.Once{}
	manifestMissing = ""
	restore := func() {
		registryMu.Lock()
		funcTable["ABS"] = saved
		registryMu.Unlock()
		manifestOnce = sync.Once{}
		manifestMissing = savedMsg
		assertManifestCovered()
	}
	defer restore()

	var got interface{}
	func() {
		defer func() { got = recover() }()
		Lookup("MAX")
	}()
	msg, ok := got.(string)
	if !ok || !strings.Contains(msg, "ABS") || !strings.Contains(msg, "no module defines it") {
		t.Fatalf("want a coverage panic naming ABS, got %v", got)
	}
	// It keeps refusing: a swallowed panic must not turn into silent success.
	func() {
		defer func() { got = recover() }()
		Lookup("MAX")
	}()
	if got == nil {
		t.Fatal("the coverage refusal did not persist")
	}
}

// Every reader of an argument the call lacks answers E_BAD_ARG at the call.
func TestEveryArgumentReaderRefusesAMissingArgument(t *testing.T) {
	readers := map[string]func(a *Args){
		"Val":      func(a *Args) { a.Val(3) },
		"Text":     func(a *Args) { a.Text(3) },
		"Bytes":    func(a *Args) { a.Bytes(3) },
		"Bool":     func(a *Args) { a.Bool(3) },
		"Dec":      func(a *Args) { a.Dec(3) },
		"Int":      func(a *Args) { a.Int(3) },
		"NonNeg":   func(a *Args) { a.NonNegInt(3) },
		"Node":     func(a *Args) { a.Node(3) },
		"PosOf":    func(a *Args) { a.PosOf(3) },
		"Symbol":   func(a *Args) { a.Symbol(3) },
		"IsSymbol": func(a *Args) { a.IsSymbol(3) },
		"Negative": func(a *Args) { a.Val(-1) },
	}
	for name, read := range readers {
		read := read
		RegisterFunction("T12_RD_"+strings.ToUpper(name), 0, 1, func(a *Args) *Value {
			read(a)
			return NewNone()
		})
		_, err := Eval("T12_RD_"+strings.ToUpper(name)+"(1)", nil)
		se, ok := err.(*SelError)
		if !ok || se.Code != "E_BAD_ARG" {
			t.Errorf("%s: want SelError E_BAD_ARG, got %T %v", name, err, err)
		}
	}
}
