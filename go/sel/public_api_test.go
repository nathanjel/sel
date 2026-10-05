package sel_test

import (
	"math/big"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// panicCode runs f and returns the code of the *sel.SelError it panics with,
// "" when it returns, and fails the test on any other panic: a public
// constructor reports a malformed call as a SelError, never as a runtime error.
func panicCode(t *testing.T, f func()) (code string) {
	t.Helper()
	defer func() {
		if r := recover(); r != nil {
			se, ok := r.(*sel.SelError)
			if !ok {
				t.Fatalf("panicked with %T %v, not a *sel.SelError", r, r)
			}
			code = se.Code
		}
	}()
	f()
	return ""
}

// The constructors outside a program check what they are given.
func TestPublicConstructorsRefuseMalformedInput(t *testing.T) {
	shape := sel.InternRecordShape([]string{"A", "B"})
	for _, c := range []struct {
		name string
		f    func()
		want string
	}{
		{"shaped record, too few values", func() { sel.NewShapedRecord(shape, []*sel.Value{sel.NewText("1")}) }, "E_BAD_ARG"},
		{"shaped record, too many values", func() {
			sel.NewShapedRecord(shape, []*sel.Value{sel.NewText("1"), sel.NewText("2"), sel.NewText("3")})
		}, "E_BAD_ARG"},
		{"shaped record, nil shape", func() { sel.NewShapedRecord(nil, nil) }, "E_BAD_ARG"},
		{"shaped record, nil value", func() { sel.NewShapedRecord(shape, []*sel.Value{sel.NewText("1"), nil}) }, "E_BAD_ARG"},
		{"shape, repeated key", func() { sel.InternRecordShape([]string{"A", "A"}) }, "E_BAD_ARG"},
		{"shape, key not UTF-8", func() { sel.InternRecordShape([]string{"a\xff"}) }, "E_UTF8"},
		{"record from entries, key not UTF-8", func() {
			sel.NewRecordFromEntries([]sel.Entry{{Key: "a\xff", Val: sel.NewText("1")}})
		}, "E_UTF8"},
		{"record from entries, nil value", func() { sel.NewRecordFromEntries([]sel.Entry{{Key: "a"}}) }, "E_BAD_ARG"},
		{"text not UTF-8", func() { sel.NewText("a\xff") }, "E_UTF8"},
		{"decimal, nil digits", func() { sel.NewDecimal(sel.Decimal{}) }, "E_BAD_ARG"},
		{"decimal, negative digits", func() { sel.NewDecimal(sel.Decimal{Digits: big.NewInt(-1)}) }, "E_BAD_ARG"},
		{"decimal, negative scale", func() { sel.NewDecimal(sel.Decimal{Digits: big.NewInt(7), Scale: -1}) }, "E_BAD_ARG"},
		{"decimal, past the fraction cap", func() { sel.NewDecimal(sel.Decimal{Digits: big.NewInt(1), Scale: 1000001}) }, "E_RANGE"},
		{"decimal, past the integer cap", func() {
			sel.NewDecimal(sel.Decimal{Digits: new(big.Int).Exp(big.NewInt(10), big.NewInt(1000000), nil)})
		}, "E_RANGE"},
	} {
		if got := panicCode(t, c.f); got != c.want {
			t.Errorf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

// What the constructors build behaves as the value the same rule would build.
func TestPublicConstructorsBuildOrdinaryValues(t *testing.T) {
	shape := sel.InternRecordShape([]string{"A", "B"})
	if sel.InternRecordShape([]string{"A", "B"}) != shape {
		t.Error("InternRecordShape does not return the interned shape")
	}
	vals := []*sel.Value{sel.NewText("1"), sel.NewText("2")}
	rec := sel.NewShapedRecord(shape, vals)
	vals[0] = sel.NewText("changed") // the record keeps its own slice
	ctx := sel.NewNone()
	ctx.Set("R", rec)
	ctx.Set("D", sel.NewRecordFromEntries([]sel.Entry{{Key: "x", Val: sel.NewText("1")}, {Key: "y", Val: sel.NewText("2")}, {Key: "x", Val: sel.NewText("3")}}))
	ctx.Set("N", sel.NewDecimal(sel.Decimal{Neg: true, Digits: big.NewInt(150), Scale: 2}))
	ctx.Set("Z", sel.NewDecimal(sel.Decimal{Neg: true, Digits: big.NewInt(0), Scale: 1}))
	got, err := sel.MustCompile(`R["A"] & R["B"] & "|" & D["x"] & D["y"] & COUNT(D) & "|" & N & "|" & (N * 2) & "|" & Z`).Run(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if want := `12|322|-1.50|-3.00|0.0`; got.AsText(sel.Pos{}) != want {
		t.Errorf("got %s, want %s", got.AsText(sel.Pos{}), want)
	}
	d := sel.NewText("-0012.340").Decimal(sel.Pos{})
	if !d.Neg || d.Digits.String() != "12340" || d.Scale != 3 {
		t.Errorf("Decimal of -0012.340: %+v", d)
	}
	d.Digits.SetInt64(9) // the caller's own copy
	if sel.NewDecimal(sel.NewText("7.5").Decimal(sel.Pos{})).AsText(sel.Pos{}) != "7.5" {
		t.Error("NewDecimal(Decimal) does not round-trip")
	}
	if code := panicCode(t, func() { sel.NewText("x").Decimal(sel.Pos{}) }); code != "E_NOT_NUM" {
		t.Errorf("Decimal of a non-number: %q", code)
	}
	for v, k := range map[*sel.Value]sel.Kind{sel.NewText("a"): sel.KindText, sel.NewBool(true): sel.KindBool,
		sel.NewBin([]byte{1}): sel.KindBin, sel.NewNone(): sel.KindNone, rec: sel.KindNone} {
		if v.Kind() != k {
			t.Errorf("%s: Kind() = %v, want %v", v.Dump(), v.Kind(), k)
		}
	}
}
