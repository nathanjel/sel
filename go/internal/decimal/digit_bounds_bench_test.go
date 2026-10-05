package decimal

import (
	"math/big"
	"strings"
	"testing"
)

// Digit-bound workloads (the parity tests are in digit_bounds_test.go).

// bigDec is the repunit 11…1 with the given number of digits (it survives doubling
// without gaining a digit, so a benchmark can add it to itself at the cap).
func bigDec(digits, scale int) *Dec {
	n := new(big.Int).Sub(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(digits)), nil), big.NewInt(1))
	n.Quo(n, big.NewInt(9))
	return &Dec{Digits: n, Scale: int32(scale)}
}

// BenchmarkP21 runs the digit-bound workloads: Guard at the cap with a large scale,
// TrimScale of a million-digit magnitude, and Cmp across scales.
func BenchmarkP21(b *testing.B) {
	atCap := bigDec(MAX_INT_DIGITS+1000000, 1000000) // a legal 2,000,000-digit, scale-1,000,000 value
	one := FromInt(1)
	million := bigDec(1000000, 500000) // odd magnitude: nothing to strip
	evenZeros := &Dec{Digits: new(big.Int).Mul(bigDec(1000000, 0).Digits, new(big.Int).Exp(big.NewInt(10), big.NewInt(1000), nil)), Scale: 5000}
	small1, small2 := mustParse("12345.67"), mustParse("8.9")
	wide1, wide2 := mustParse("1"+strings.Repeat("0", 300)), mustParse("9.5")
	cases := []struct {
		name string
		run  func()
	}{
		{"GuardAtCapBigScale_AddSelf", func() { sinkDec = Add(atCap, atCap, Pos{}, fail0) }},
		{"GuardAtCapBigScale_AddOne", func() { sinkDec = Add(atCap, one, Pos{}, fail0) }},
		{"TrimScale1MDigitsOdd", func() { sinkDec = TrimScale(million) }},
		{"TrimScale1MDigitsZeros", func() { sinkDec = TrimScale(evenZeros) }},
		{"IsInteger1MDigits", func() { _ = IsInteger(million) }},
		{"CmpSmallDiffScale", func() { _ = Cmp(small1, small2) }},
		{"CmpWideVsSmall", func() { _ = Cmp(wide1, wide2) }},
		{"CmpBigScaleGap", func() { _ = Cmp(million, small2) }},
	}
	for _, c := range cases {
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				c.run()
			}
		})
	}
}
