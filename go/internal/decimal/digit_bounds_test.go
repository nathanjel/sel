package decimal

import (
	"math/big"
	"math/rand"
	"strings"
	"testing"
)

// GO-P21: Guard / TrimScale / IsInteger / Cmp without powers of ten, strings or
// scaled copies. Each replaced routine has its old behaviour kept here as the
// reference, and the tests compare the two on random and boundary inputs.

// refNumDigits is the old Guard's digit count (a power-of-ten probe loop).
func refNumDigits(n *big.Int) int {
	if n.Sign() == 0 {
		return 1
	}
	return len(n.String())
}

func refGuardFails(d *Dec) bool {
	if int(d.Scale) > MAX_FRAC_DIGITS {
		return true
	}
	return refNumDigits(d.Digits)-int(d.Scale) > MAX_INT_DIGITS
}

func guardFails(d *Dec) (failed bool) {
	defer func() {
		if recover() != nil {
			failed = true
		}
	}()
	Guard(d, Pos{}, fail0)
	return false
}

func TestDigitBoundsBracketTheDigitCount(t *testing.T) {
	rnd := rand.New(rand.NewSource(21))
	check := func(n *big.Int) {
		if n.Sign() == 0 {
			return
		}
		lo, hi := digitBounds(n.BitLen())
		d := refNumDigits(n)
		if d < lo || d > hi {
			t.Fatalf("bit length %d: digits %d outside [%d, %d]", n.BitLen(), d, lo, hi)
		}
	}
	for k := 0; k <= 3000; k++ { // every decade boundary, both sides
		p := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(k)), nil)
		check(p)
		check(new(big.Int).Sub(p, big.NewInt(1)))
		check(new(big.Int).Add(p, big.NewInt(1)))
	}
	for k := 1; k <= 3000; k++ { // every binary boundary
		p := new(big.Int).Lsh(big.NewInt(1), uint(k))
		check(p)
		check(new(big.Int).Sub(p, big.NewInt(1)))
	}
	for i := 0; i < 2000; i++ {
		check(new(big.Int).Rand(rnd, new(big.Int).Lsh(big.NewInt(1), uint(1+rnd.Intn(5000)))))
	}
	// the cap region: the two bounds must bracket the exact count for huge values
	for _, k := range []int{MAX_INT_DIGITS - 1, MAX_INT_DIGITS, MAX_INT_DIGITS + 1} {
		p := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(k)), nil)
		check(p)
		check(new(big.Int).Sub(p, big.NewInt(1)))
	}
}

func TestGuardMatchesTheDigitCountAroundTheCap(t *testing.T) {
	p := func(k int) *big.Int { return new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(k)), nil) }
	cases := []struct {
		name  string
		n     *big.Int
		scale int32
	}{}
	for _, scale := range []int32{0, 1, 7, 1000, 123456} {
		thr := MAX_INT_DIGITS + int(scale)
		base := p(thr)
		one := big.NewInt(1)
		cases = append(cases,
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"just below", new(big.Int).Sub(base, one), scale},
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"exactly 10^thr", base, scale},
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"10^thr + 1", new(big.Int).Add(base, one), scale},
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"thr-1 digits", p(thr - 1), scale},
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"far above", p(thr + 50), scale},
			struct {
				name  string
				n     *big.Int
				scale int32
			}{"small", big.NewInt(12345), scale},
		)
	}
	for _, c := range cases {
		d := &Dec{Digits: c.n, Scale: c.scale}
		if want, got := refGuardFails(d), guardFails(d); want != got {
			t.Errorf("%s scale %d (%d digits): reference fails=%v, Guard fails=%v", c.name, c.scale, refNumDigits(c.n), want, got)
		}
	}
}

func TestGuardBigScaleKeepsItsThresholdCached(t *testing.T) {
	// A value at the cap with scale 1,000,000 needs 10^2,000,000: built once.
	thr := MAX_INT_DIGITS + 1000000
	n := new(big.Int).Sub(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(thr)), nil), big.NewInt(1))
	d := &Dec{Digits: n, Scale: 1000000}
	if guardFails(d) {
		t.Fatal("a value with exactly MAX_INT_DIGITS integer digits must pass")
	}
	first := lastGuard.Load()
	if first == nil || first.k != thr {
		t.Fatalf("the threshold power was not kept (entry %v)", first)
	}
	if guardFails(d) || lastGuard.Load() != first {
		t.Fatal("the second guard must reuse the kept power")
	}
	over := &Dec{Digits: new(big.Int).Add(n, big.NewInt(1)), Scale: 1000000}
	if !guardFails(over) {
		t.Fatal("10^(MAX_INT_DIGITS+scale) has one integer digit too many")
	}
}

// refTrimScale is the old string-based routine.
func refTrimScale(d *Dec) *Dec {
	if d.Digits.Sign() == 0 {
		return Make(false, zeroBig, 0)
	}
	if d.Scale == 0 {
		return d
	}
	s := d.Digits.String()
	trimmed := strings.TrimRight(s, "0")
	zeros := len(s) - len(trimmed)
	if zeros > int(d.Scale) {
		zeros = int(d.Scale)
	}
	if zeros == 0 {
		return d
	}
	digits := new(big.Int)
	digits.SetString(s[:len(s)-zeros], 10)
	return Make(d.Neg, digits, d.Scale-int32(zeros))
}

func sameDec(a, b *Dec) bool {
	return a.Neg == b.Neg && a.Scale == b.Scale && a.Digits.Cmp(b.Digits) == 0
}

func TestTrimScaleMatchesTheStringRoutine(t *testing.T) {
	rnd := rand.New(rand.NewSource(2121))
	for i := 0; i < 4000; i++ {
		// a magnitude above 64 bits (the word-sized path is GO-P9's) with a random
		// number of trailing zeros, sometimes more than the scale
		body := new(big.Int).Rand(rnd, new(big.Int).Lsh(big.NewInt(1), uint(65+rnd.Intn(300))))
		if body.Sign() == 0 {
			body.SetInt64(1)
		}
		zeros := rnd.Intn(120)
		n := new(big.Int).Mul(body, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(zeros)), nil))
		scale := int32(rnd.Intn(150))
		d := &Dec{Neg: rnd.Intn(2) == 0, Digits: n, Scale: scale}
		want, got := refTrimScale(d), TrimScale(d)
		if !sameDec(want, got) {
			t.Fatalf("n=%s scale=%d: reference %s/%d, got %s/%d", n, scale, want.Digits, want.Scale, got.Digits, got.Scale)
		}
	}
	// the scale caps the stripping exactly, and a power of ten strips to 1
	n := new(big.Int).Exp(big.NewInt(10), big.NewInt(400), nil)
	for _, scale := range []int32{1, 2, 3, 31, 32, 33, 399, 400, 401} {
		d := &Dec{Digits: n, Scale: scale}
		if want, got := refTrimScale(d), TrimScale(d); !sameDec(want, got) {
			t.Fatalf("10^400 scale %d: reference scale %d, got scale %d", scale, want.Scale, got.Scale)
		}
	}
}

func refIsInteger(d *Dec) bool {
	if d.Scale == 0 {
		return true
	}
	rem := new(big.Int).Mod(d.Digits, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(d.Scale)), nil))
	return rem.Sign() == 0
}

func TestIsIntegerMatchesTheDivision(t *testing.T) {
	rnd := rand.New(rand.NewSource(212121))
	for i := 0; i < 6000; i++ {
		var n *big.Int
		switch rnd.Intn(3) {
		case 0:
			n = new(big.Int).Rand(rnd, new(big.Int).Lsh(big.NewInt(1), uint(1+rnd.Intn(200))))
		case 1:
			n = new(big.Int).Mul(big.NewInt(int64(1+rnd.Intn(1000))), new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(rnd.Intn(60))), nil))
		default:
			n = big.NewInt(int64(rnd.Intn(100)))
		}
		d := &Dec{Digits: n, Scale: int32(rnd.Intn(70))}
		if want, got := refIsInteger(d), IsInteger(d); want != got {
			t.Fatalf("n=%s scale=%d: reference %v, got %v", n, d.Scale, want, got)
		}
	}
}

func refCmp(a, b *Dec) int {
	if a.Neg != b.Neg {
		if a.Neg {
			return -1
		}
		return 1
	}
	var A, B *big.Int
	if a.Scale == b.Scale {
		A, B = a.Digits, b.Digits
	} else {
		if a.Digits.Sign() == 0 && b.Digits.Sign() == 0 {
			return 0
		}
		A, B, _ = aligned(a, b)
	}
	c := A.Cmp(B)
	if a.Neg {
		return -c
	}
	return c
}

func TestCmpMatchesTheAlignedComparison(t *testing.T) {
	rnd := rand.New(rand.NewSource(21212121))
	mk := func() *Dec {
		var n *big.Int
		switch rnd.Intn(4) {
		case 0:
			n = big.NewInt(0)
		case 1:
			n = new(big.Int).SetUint64(rnd.Uint64() >> uint(rnd.Intn(64)))
		case 2:
			n = new(big.Int).Rand(rnd, new(big.Int).Lsh(big.NewInt(1), uint(1+rnd.Intn(400))))
		default:
			n = new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(rnd.Intn(200))), nil)
			n.Add(n, big.NewInt(int64(rnd.Intn(3)-1)))
			if n.Sign() < 0 {
				n.SetInt64(0)
			}
		}
		return &Dec{Neg: n.Sign() != 0 && rnd.Intn(3) == 0, Digits: n, Scale: int32(rnd.Intn(60))}
	}
	for i := 0; i < 200000; i++ {
		a, b := mk(), mk()
		if want, got := refCmp(a, b), Cmp(a, b); want != got {
			t.Fatalf("%s/%d vs %s/%d (neg %v %v): reference %d, got %d", a.Digits, a.Scale, b.Digits, b.Scale, a.Neg, b.Neg, want, got)
		}
	}
	// the 128-bit path at its edges: 2^64-1 against a product that overflows 64 bits
	max := new(big.Int).SetUint64(^uint64(0))
	for gap := int32(1); gap <= 19; gap++ {
		a := &Dec{Digits: max, Scale: gap}
		b := &Dec{Digits: new(big.Int).SetUint64(^uint64(0) / 7), Scale: 0}
		if want, got := refCmp(a, b), Cmp(a, b); want != got {
			t.Fatalf("gap %d: reference %d, got %d", gap, want, got)
		}
		if want, got := refCmp(b, a), Cmp(b, a); want != got {
			t.Fatalf("gap %d reversed: reference %d, got %d", gap, want, got)
		}
	}
}

// refFormat is the big.Int-string formatting Format had before GO-P27's fast path.
func refFormat(d *Dec) string {
	sign := ""
	if d.Neg {
		sign = "-"
	}
	if d.Scale == 0 {
		return sign + d.Digits.String()
	}
	s := d.Digits.String()
	scale := int(d.Scale)
	if len(s) <= scale {
		s = strings.Repeat("0", scale+1-len(s)) + s
	}
	dot := len(s) - scale
	return sign + s[:dot] + "." + s[dot:]
}

func TestFormatFastPathMatchesTheBigIntRoutine(t *testing.T) {
	rnd := rand.New(rand.NewSource(27))
	vals := []uint64{0, 1, 9, 10, 99, 100, 12345, 999999999, 1 << 32, 1<<63 - 1, 1 << 63, 1<<64 - 1, 10000000000000000000}
	for i := 0; i < 3000; i++ {
		vals = append(vals, rnd.Uint64()>>uint(rnd.Intn(64)))
	}
	for _, v := range vals {
		for _, scale := range []int32{0, 1, 2, 5, 18, 19, 20, 21, 39, 40, 41, 100} {
			for _, neg := range []bool{false, true} {
				d := &Dec{Neg: neg && v != 0, Digits: new(big.Int).SetUint64(v), Scale: scale}
				if want, got := refFormat(d), Format(d); want != got {
					t.Fatalf("%d scale %d neg %v: want %q got %q", v, scale, neg, want, got)
				}
			}
		}
	}
	// a magnitude beyond a word still takes the big path
	d := &Dec{Digits: new(big.Int).Lsh(big.NewInt(1), 70), Scale: 3}
	if want, got := refFormat(d), Format(d); want != got {
		t.Fatalf("2^70 scale 3: want %q got %q", want, got)
	}
}
