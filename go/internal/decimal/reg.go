package decimal

import (
	"fmt"
	"math/big"
)

// Num is a number read in place: a sign, a magnitude and a scale, without a Dec
// header around them. A math plan keeps its intermediate ADD, SUB and MUL
// results as Nums whose magnitudes are registers it owns and reuses (item 1);
// everything else holds Decs, which are never changed once built. A Num's Mag is
// written only by whoever owns it.
type Num struct {
	Mag   *big.Int
	Scale int32
	Neg   bool
}

// NumOf reads d in place: the Num shares d's magnitude, and nothing may write it.
func NumOf(d *Dec) Num { return Num{Neg: d.Neg, Mag: d.Digits, Scale: d.Scale} }

// Dec boxes n into a Dec of its own. The magnitude is copied, so the Dec stays
// what it is whatever later becomes of n's register.
func (n Num) Dec() *Dec {
	return makeOwned(n.Neg, new(big.Int).Set(n.Mag), n.Scale)
}

// guardMag is Guard for a magnitude and scale that are not (yet) a Dec.
func guardMag(mag *big.Int, scale int32, pos Pos, fail FailFunc) {
	if int(scale) > MAX_FRAC_DIGITS {
		fail("E_RANGE", fmt.Sprintf("number has more than %d fractional digits", MAX_FRAC_DIGITS), pos)
	}
	if bl := mag.BitLen(); bl >= MAX_INT_BITS {
		thr := MAX_INT_DIGITS + int(scale)
		lo, hi := digitBounds(bl)
		if lo > thr || (hi > thr && mag.Cmp(guardPow10(thr)) >= 0) {
			fail("E_RANGE", fmt.Sprintf("number has more than %d integer digits", MAX_INT_DIGITS), pos)
		}
	}
}

// alignInto brings a and b to their common scale: the operand with the smaller
// scale is multiplied into scratch (a new big.Int when scratch is nil), the
// other is read in place.
func alignInto(scratch *big.Int, a, b Num) (*big.Int, *big.Int, int32) {
	if a.Scale == b.Scale {
		return a.Mag, b.Mag, a.Scale
	}
	if scratch == nil {
		scratch = new(big.Int)
	}
	if a.Scale > b.Scale {
		return a.Mag, scratch.Mul(b.Mag, Pow10(int(a.Scale-b.Scale))), a.Scale
	}
	return scratch.Mul(a.Mag, Pow10(int(b.Scale-a.Scale))), b.Mag, b.Scale
}

// AddInto sets z to a+b with Add's sign, scale and E_RANGE rules, and returns the
// sum read in place (its Mag is z).
//
// z is the caller's own: it may be a's or b's magnitude (the sum is written over
// it), never one anything else holds. scratch, also the caller's own and none of
// z, a.Mag and b.Mag, takes the operand brought to the common scale; nil
// allocates one when the scales differ.
func AddInto(z, scratch *big.Int, a, b Num, pos Pos, fail FailFunc) Num {
	A, B, s := alignInto(scratch, a, b)
	if a.Neg == b.Neg {
		z.Add(A, B)
		guardMag(z, s, pos, fail)
		return Num{Neg: a.Neg && z.Sign() != 0, Mag: z, Scale: s}
	}
	switch A.Cmp(B) {
	case 0:
		z.SetInt64(0)
		return Num{Mag: z, Scale: s}
	case 1:
		z.Sub(A, B)
		return Num{Neg: a.Neg, Mag: z, Scale: s}
	}
	z.Sub(B, A)
	return Num{Neg: b.Neg, Mag: z, Scale: s}
}

// SubInto sets z to a-b with Sub's rules; z and scratch as for AddInto.
func SubInto(z, scratch *big.Int, a, b Num, pos Pos, fail FailFunc) Num {
	A, B, s := alignInto(scratch, a, b)
	if a.Neg != b.Neg {
		z.Add(A, B)
		guardMag(z, s, pos, fail)
		return Num{Neg: a.Neg && z.Sign() != 0, Mag: z, Scale: s}
	}
	switch A.Cmp(B) {
	case 0:
		z.SetInt64(0)
		return Num{Mag: z, Scale: s}
	case 1:
		z.Sub(A, B)
		return Num{Neg: a.Neg, Mag: z, Scale: s}
	}
	z.Sub(B, A)
	return Num{Neg: !a.Neg, Mag: z, Scale: s}
}

// MulInto sets z to a*b with Mul's scale and E_RANGE rules. z is the caller's
// own and neither operand's magnitude (math/big would allocate around the
// alias); a and b may share one, which is how zr * zr takes the squaring path.
func MulInto(z *big.Int, a, b Num, pos Pos, fail FailFunc) Num {
	z.Mul(a.Mag, b.Mag)
	s := a.Scale + b.Scale
	guardMag(z, s, pos, fail)
	return Num{Neg: a.Neg != b.Neg && z.Sign() != 0, Mag: z, Scale: s}
}

// The plan's fresh results: the same operations, into a new magnitude.

// AddNew is a+b as a Dec of its own; scratch as for AddInto.
func AddNew(scratch *big.Int, a, b Num, pos Pos, fail FailFunc) *Dec {
	r := AddInto(new(big.Int), scratch, a, b, pos, fail)
	return &Dec{Neg: r.Neg, Digits: r.Mag, Scale: r.Scale}
}

// SubNew is a-b as a Dec of its own; scratch as for AddInto.
func SubNew(scratch *big.Int, a, b Num, pos Pos, fail FailFunc) *Dec {
	r := SubInto(new(big.Int), scratch, a, b, pos, fail)
	return &Dec{Neg: r.Neg, Digits: r.Mag, Scale: r.Scale}
}

// MulNew is a*b as a Dec of its own.
func MulNew(a, b Num, pos Pos, fail FailFunc) *Dec {
	r := MulInto(new(big.Int), a, b, pos, fail)
	return &Dec{Neg: r.Neg, Digits: r.Mag, Scale: r.Scale}
}
