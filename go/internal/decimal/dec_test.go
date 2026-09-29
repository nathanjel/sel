package decimal

import (
	"math"
	"strings"
	"testing"
)

func mustNotFail(t *testing.T) FailFunc {
	return func(code, msg string, pos Pos) {
		t.Fatalf("unexpected %s at %v: %s", code, pos, msg)
	}
}

// GO-C42: negating math.MinInt64 in int64 wraps to itself, which built a Dec
// whose digits were negative and printed as "--9223372036854775808".
func TestFromIntExtremes(t *testing.T) {
	cases := map[int64]string{
		math.MinInt64: "-9223372036854775808",
		math.MaxInt64: "9223372036854775807",
		-1:            "-1",
		0:             "0",
		1:             "1",
	}
	for n, want := range cases {
		d := FromInt(n)
		if got := Format(d); got != want {
			t.Errorf("Format(FromInt(%d)) = %q, want %q", n, got, want)
		}
		if d.Digits.Sign() < 0 {
			t.Errorf("FromInt(%d) has negative digits", n)
		}
	}
	// And it behaves as the number it is.
	sum := Add(FromInt(math.MinInt64), FromInt(1), Pos{}, mustNotFail(t))
	if got := Format(sum); got != "-9223372036854775807" {
		t.Errorf("MinInt64 + 1 = %q", got)
	}
}

type failure struct {
	code string
	pos  Pos
}

func capture(f func(FailFunc)) (got *failure) {
	defer func() {
		if r := recover(); r != nil {
			if fl, ok := r.(*failure); ok {
				got = fl
				return
			}
			panic(r)
		}
	}()
	f(func(code, msg string, pos Pos) { panic(&failure{code, pos}) })
	return nil
}

// GO-C12: CEIL and FLOOR carry one into the integer part, so a value with the
// maximum number of integer digits leaves the cap. Like ROUND they must fail
// E_RANGE at the call, not return a 1,000,001-digit number.
func TestCeilFloorCarryPastTheDigitCap(t *testing.T) {
	nines := strings.Repeat("9", MAX_INT_DIGITS)
	pos := Pos{Line: 1, Col: 1, Offset: 0}
	up := Parse(nines+".5", pos, mustNotFail(t))
	down := Parse("-"+nines+".5", pos, mustNotFail(t))

	if f := capture(func(fail FailFunc) { Ceil(up, pos, fail) }); f == nil || f.code != "E_RANGE" || f.pos != pos {
		t.Errorf("Ceil of %d nines + .5: got %+v, want E_RANGE at the call", MAX_INT_DIGITS, f)
	}
	if f := capture(func(fail FailFunc) { Floor(down, pos, fail) }); f == nil || f.code != "E_RANGE" || f.pos != pos {
		t.Errorf("Floor of -%d nines - .5: got %+v, want E_RANGE at the call", MAX_INT_DIGITS, f)
	}

	// Controls: the other direction does not carry, one digit fewer does not overflow.
	if got := Format(Floor(up, pos, mustNotFail(t))); got != nines {
		t.Errorf("Floor(up) has the wrong value (len %d)", len(got))
	}
	if got := Format(Ceil(down, pos, mustNotFail(t))); got != "-"+nines {
		t.Errorf("Ceil(down) has the wrong value (len %d)", len(got))
	}
	short := Parse(nines[1:]+".5", pos, mustNotFail(t))
	if got := Format(Ceil(short, pos, mustNotFail(t))); got != "1"+strings.Repeat("0", MAX_INT_DIGITS-1) {
		t.Errorf("Ceil just under the cap carried wrongly")
	}
}
