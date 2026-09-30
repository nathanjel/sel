package decimal

import (
	"fmt"
	"math/big"
	"strings"
	"testing"
)

// GO-P9: the word-sized Parse path agrees with the general one on every shape of
// numeral: leading zeros, a fraction, signs, the 18/19-digit boundary, -0.
func TestParseWordPathMatchesTheGeneralPath(t *testing.T) {
	general := func(text string) *Dec {
		neg := strings.HasPrefix(text, "-")
		s := strings.TrimPrefix(text, "-")
		dot := strings.IndexByte(s, '.')
		frac := ""
		if dot >= 0 {
			frac = s[dot+1:]
			s = s[:dot]
		}
		st := strings.TrimLeft(s+frac, "0")
		if st == "" {
			st = "0"
		}
		d := new(big.Int)
		d.SetString(st, 10)
		return Make(neg, d, int32(len(frac)))
	}
	texts := []string{"0", "-0", "00", "000.000", "-0.0", "7", "-7", "007", "0.5", "-0.05", "1.50", "100", "100.00",
		"123456789012345678", "-123456789012345678", "999999999999999999", "1000000000000000000", "1000000000000000001",
		"123456789012345678.9", "0.000000000000000000001", "0.123456789012345678", "0.1234567890123456789",
		"18446744073709551615", "18446744073709551616", "00012345678901234567890123456789012.5",
		strings.Repeat("9", 40), strings.Repeat("0", 30) + "1", "1" + strings.Repeat("0", 39), "-" + strings.Repeat("7", 41)}
	for _, tx := range texts {
		got := Parse(tx, Pos{}, fail0)
		want := general(tx)
		if got.Neg != want.Neg || got.Scale != want.Scale || got.Digits.Cmp(want.Digits) != 0 {
			t.Errorf("Parse(%q) = {%v %s %d}, want {%v %s %d}", tx, got.Neg, got.Digits, got.Scale, want.Neg, want.Digits, want.Scale)
		}
		if Format(got) != Format(want) {
			t.Errorf("Format(Parse(%q)) = %s, want %s", tx, Format(got), Format(want))
		}
	}
	for _, bad := range []string{"", "-", ".5", "5.", "1.2.3", "1e3", "a", "--1"} {
		if Parse(bad, Pos{}, fail0) != nil {
			t.Errorf("Parse(%q) accepted", bad)
		}
	}
}

// TrimScale on a word-sized magnitude equals the decimal-string route.
func TestTrimScaleWordPathMatchesTheStringRoute(t *testing.T) {
	ref := func(d *Dec) string {
		if d.Digits.Sign() == 0 {
			return "0"
		}
		s := d.Digits.String()
		tr := strings.TrimRight(s, "0")
		z := len(s) - len(tr)
		if z > int(d.Scale) {
			z = int(d.Scale)
		}
		ds := new(big.Int)
		ds.SetString(s[:len(s)-z], 10)
		return Format(Make(d.Neg, ds, d.Scale-int32(z)))
	}
	for _, tx := range []string{"0", "0.000", "12.3400", "-12.3400", "100", "100.000", "0.10", "1.0", "-0.50", "5", "123456789012345678.000",
		"18446744073709551615.0", "18446744073709551616.00", "-90000000000000000000.000", "0.0000000000000000001000"} {
		d := Parse(tx, Pos{}, fail0)
		if got, want := Format(TrimScale(d)), ref(d); got != want {
			t.Errorf("TrimScale(%q) = %s, want %s", tx, got, want)
		}
	}
}

// Negate and Abs share the magnitude, and so must never change their operand.
func TestNegateAndAbsLeaveTheOperandAlone(t *testing.T) {
	for _, tx := range []string{"5", "-5", "0", "-0.50", "123456789012345678901234567890.123"} {
		d := Parse(tx, Pos{}, fail0)
		before := Format(d)
		n, a := Negate(d), Abs(d)
		if Format(d) != before {
			t.Errorf("%s changed to %s", before, Format(d))
		}
		wantNeg := fmt.Sprint(before)
		if before == "0" {
			wantNeg = "0"
		} else if strings.HasPrefix(before, "-") {
			wantNeg = before[1:]
		} else {
			wantNeg = "-" + before
		}
		if Format(n) != wantNeg {
			t.Errorf("Negate(%s) = %s, want %s", before, Format(n), wantNeg)
		}
		if want := strings.TrimPrefix(before, "-"); Format(a) != want {
			t.Errorf("Abs(%s) = %s, want %s", before, Format(a), want)
		}
		if n.Neg && IsZero(n) {
			t.Errorf("Negate(%s) made a negative zero", before)
		}
	}
	// Two results of one operation do not share a digit buffer that a later
	// operation on one of them could write through.
	x := Parse("12", Pos{}, fail0)
	s1 := Add(x, x, Pos{}, fail0)
	s2 := Add(x, x, Pos{}, fail0)
	if s1.Digits == s2.Digits || s1.Digits == x.Digits {
		t.Error("results alias a digit buffer")
	}
}

// GO-P10: the divide-and-conquer digit parse equals big.Int.SetString for every
// length around the leaf size and the power-of-two split points, including digit
// strings with zeros at every position a split could fall.
func TestParseDigitsMatchesSetString(t *testing.T) {
	gen := func(n int, pattern string) string {
		var sb strings.Builder
		for sb.Len() < n {
			sb.WriteString(pattern)
		}
		return sb.String()[:n]
	}
	lengths := []int{1, 2, 1023, 1024, 1025, 1026, 2047, 2048, 2049, 3071, 3072, 4096, 4097, 5000, 8193, 12345, 65537, 100000}
	for _, n := range lengths {
		for _, pat := range []string{"7", "1234567890", "9", "10", "0001", "1000000000"} {
			s := strings.TrimLeft(gen(n, pat), "0")
			if s == "" {
				s = "0"
			}
			want := new(big.Int)
			want.SetString(s, 10)
			if got := parseDigits(s); got.Cmp(want) != 0 {
				t.Fatalf("parseDigits(len %d, %q) differs from SetString", n, pat)
			}
		}
	}
	// End to end through Parse, with a fraction and a sign.
	d := Parse("-"+strings.Repeat("7", 5000)+"."+strings.Repeat("3", 3000), Pos{}, fail0)
	want := new(big.Int)
	want.SetString(strings.Repeat("7", 5000)+strings.Repeat("3", 3000), 10)
	if !d.Neg || d.Scale != 3000 || d.Digits.Cmp(want) != 0 {
		t.Error("Parse of a long signed decimal differs")
	}
}
