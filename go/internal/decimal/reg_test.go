package decimal

import (
	"fmt"
	"math/big"
	"math/rand"
	"strings"
	"testing"
)

// Item 1: AddInto, SubInto and MulInto write a result into a register the caller
// owns and reuses. Against Add, Sub and Mul (the reference: what every caller
// outside a plan still uses) they must give the same sign, scale and magnitude,
// raise the same E_RANGE at the same place, never write an operand they do not
// own, and allocate nothing once their registers are warm.

type rangeError struct{ code string }

func catch(f func()) (err *rangeError) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(*rangeError); ok {
				err = e
				return
			}
			panic(r)
		}
	}()
	f()
	return nil
}

func failRange(code, msg string, pos Pos) { panic(&rangeError{code}) }

// The allocating operations as they were before registers (the P21 style: the
// retired code is the reference the new code is held to).
func refAdd(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	A, B, s := aligned(a, b)
	if a.Neg == b.Neg {
		return Guard(makeOwned(a.Neg, new(big.Int).Add(A, B), s), pos, fail)
	}
	switch A.Cmp(B) {
	case 0:
		return makeOwned(false, new(big.Int), s)
	case 1:
		return makeOwned(a.Neg, new(big.Int).Sub(A, B), s)
	}
	return makeOwned(b.Neg, new(big.Int).Sub(B, A), s)
}

func refSub(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	A, B, s := aligned(a, b)
	if a.Neg != b.Neg {
		return Guard(makeOwned(a.Neg, new(big.Int).Add(A, B), s), pos, fail)
	}
	switch A.Cmp(B) {
	case 0:
		return makeOwned(false, new(big.Int), s)
	case 1:
		return makeOwned(a.Neg, new(big.Int).Sub(A, B), s)
	}
	return makeOwned(!a.Neg, new(big.Int).Sub(B, A), s)
}

func refMul(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	return Guard(makeOwned(a.Neg != b.Neg, new(big.Int).Mul(a.Digits, b.Digits), a.Scale+b.Scale), pos, fail)
}

func randomDec(rng *rand.Rand) *Dec {
	n := 1 + rng.Intn(80)
	if rng.Intn(6) == 0 {
		n = 1 + rng.Intn(4000)
	}
	var sb strings.Builder
	sb.WriteByte(byte('1' + rng.Intn(9)))
	for i := 1; i < n; i++ {
		sb.WriteByte(byte('0' + rng.Intn(10)))
	}
	if rng.Intn(8) == 0 {
		sb.Reset()
		sb.WriteByte('0')
	}
	d, _ := new(big.Int).SetString(sb.String(), 10)
	return Make(rng.Intn(2) == 0, d, int32(rng.Intn(61)))
}

func same(a *Dec, b Num) bool {
	return a.Neg == b.Neg && a.Scale == b.Scale && a.Digits.Cmp(b.Mag) == 0
}

func describe(d *Dec) string { return fmt.Sprintf("%v/%d/%s", d.Neg, d.Scale, d.Digits) }

func TestRegistersAgreeWithTheAllocatingOperations(t *testing.T) {
	rng := rand.New(rand.NewSource(20261001))
	pos := Pos{Line: 1, Col: 7}
	z, w, scratch := new(big.Int), new(big.Int), new(big.Int)
	for i := 0; i < 4000; i++ {
		a, b := randomDec(rng), randomDec(rng)
		for _, op := range []string{"+", "-", "*"} {
			var want *Dec
			if e := catch(func() {
				var now *Dec
				switch op {
				case "+":
					want, now = refAdd(a, b, pos, failRange), Add(a, b, pos, failRange)
				case "-":
					want, now = refSub(a, b, pos, failRange), Sub(a, b, pos, failRange)
				default:
					want, now = refMul(a, b, pos, failRange), Mul(a, b, pos, failRange)
				}
				if !same(want, NumOf(now)) {
					t.Fatalf("%s %s %s: %s, retired code %s", describe(a), op, describe(b), describe(now), describe(want))
				}
			}); e != nil {
				t.Fatalf("reference raised %s on ordinary operands", e.code)
			}
			// Into a stale register (z still holds the previous result), and
			// into a register that holds a's magnitude (the in-place form).
			var got, inPlace Num
			switch op {
			case "+":
				got = AddInto(z, scratch, NumOf(a), NumOf(b), pos, failRange)
				w.Set(a.Digits)
				inPlace = AddInto(w, scratch, Num{Neg: a.Neg, Mag: w, Scale: a.Scale}, NumOf(b), pos, failRange)
			case "-":
				got = SubInto(z, scratch, NumOf(a), NumOf(b), pos, failRange)
				w.Set(b.Digits)
				inPlace = SubInto(w, scratch, NumOf(a), Num{Neg: b.Neg, Mag: w, Scale: b.Scale}, pos, failRange)
			default:
				got = MulInto(z, NumOf(a), NumOf(b), pos, failRange)
				inPlace = MulInto(w, NumOf(a), NumOf(a), pos, failRange) // a square: shared operand
				if sq := refMul(a, a, pos, failRange); !same(sq, inPlace) {
					t.Fatalf("%s squared: register %v, reference %s", describe(a), inPlace, describe(sq))
				}
				inPlace = got
			}
			if !same(want, got) || !same(want, inPlace) {
				t.Fatalf("%s %s %s: register %v / %v, reference %s", describe(a), op, describe(b), got, inPlace, describe(want))
			}
			if got.Mag != z {
				t.Fatalf("the result is not in the register")
			}
		}
	}
}

func TestRegistersNeverWriteWhatTheyDoNotOwn(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	pos := Pos{}
	z, scratch := new(big.Int), new(big.Int)
	singletons := []*big.Int{zeroBig, oneBig, twoBig, tenBig}
	singletons = append(singletons, pow10List[:]...)
	before := make([]string, len(singletons))
	for i, s := range singletons {
		before[i] = s.String()
	}
	for i := 0; i < 2000; i++ {
		a, b := randomDec(rng), randomDec(rng)
		sa, sb := describe(a), describe(b)
		AddInto(z, scratch, NumOf(a), NumOf(b), pos, failRange)
		SubInto(z, scratch, NumOf(a), NumOf(b), pos, failRange)
		MulInto(z, NumOf(a), NumOf(b), pos, failRange)
		if describe(a) != sa || describe(b) != sb {
			t.Fatalf("an operand changed: %s %s", sa, sb)
		}
		for _, r := range []*Dec{Add(a, b, pos, failRange), Sub(a, b, pos, failRange), Mul(a, b, pos, failRange)} {
			if r.Digits == a.Digits || r.Digits == b.Digits {
				t.Fatal("an allocating result shares an operand's magnitude")
			}
		}
	}
	for i, s := range singletons {
		if s.String() != before[i] {
			t.Fatalf("shared constant %d changed: %s", i, s)
		}
	}
}

func TestRegistersRaiseWhereTheReferenceRaises(t *testing.T) {
	pos := Pos{Line: 3, Col: 11}
	// Products past the fractional-digit cap, and a sum one digit past the
	// integer-digit cap.
	tiny := Make(false, big.NewInt(1), int32(MAX_FRAC_DIGITS/2+1))
	nines := new(big.Int).Sub(new(big.Int).Exp(big.NewInt(10), big.NewInt(MAX_INT_DIGITS), nil), big.NewInt(1))
	wide := Make(false, nines, 0)
	one := Make(false, big.NewInt(1), 0)
	z, scratch := new(big.Int), new(big.Int)
	cases := []struct {
		ref func()
		reg func()
	}{
		{func() { refMul(tiny, tiny, pos, failRange) }, func() { MulInto(z, NumOf(tiny), NumOf(tiny), pos, failRange) }},
		{func() { refAdd(wide, one, pos, failRange) }, func() { AddInto(z, scratch, NumOf(wide), NumOf(one), pos, failRange) }},
		{func() { refSub(wide, Negate(one), pos, failRange) }, func() { SubInto(z, scratch, NumOf(wide), NumOf(Negate(one)), pos, failRange) }},
	}
	for i, c := range cases {
		re, ge := catch(c.ref), catch(c.reg)
		if re == nil || ge == nil || re.code != ge.code {
			t.Fatalf("case %d: reference %v, register %v", i, re, ge)
		}
	}
	// One digit short of the cap passes both ways.
	if e := catch(func() { AddInto(z, scratch, NumOf(wide), NumOf(Make(false, nil, 0)), pos, failRange) }); e != nil {
		t.Fatalf("at the cap: %v", e)
	}
}

func TestWarmRegistersAllocateNothing(t *testing.T) {
	pos := Pos{}
	a := Make(true, new(big.Int).Lsh(big.NewInt(12345), 300), 7)
	b := Make(false, new(big.Int).Lsh(big.NewInt(98765), 280), 3)
	z, w, scratch := new(big.Int), new(big.Int), new(big.Int)
	run := func() {
		p := MulInto(z, NumOf(a), NumOf(b), pos, failRange)
		q := MulInto(w, NumOf(b), NumOf(b), pos, failRange)
		r := SubInto(z, scratch, p, q, pos, failRange)
		AddInto(z, scratch, r, NumOf(a), pos, failRange)
	}
	run()
	if n := testing.AllocsPerRun(100, run); n != 0 {
		t.Fatalf("%v allocations with warm registers", n)
	}
}
