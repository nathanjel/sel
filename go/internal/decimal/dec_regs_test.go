package decimal

import (
	"math/big"
	"math/rand"
	"testing"
)

// Item 1 lets a math plan keep its intermediates in registers it reuses. Add, Sub
// and Mul -- what every caller outside a plan uses -- must still never write an
// operand or a shared constant, and must return a magnitude no operand shares.
func TestOperationsNeverWriteTheirOperands(t *testing.T) {
	rng := rand.New(rand.NewSource(20261001))
	fail := func(code, msg string, pos Pos) { t.Fatalf("%s: %s", code, msg) }
	singletons := append([]*big.Int{zeroBig, oneBig, twoBig, tenBig}, pow10List[:]...)
	before := make([]string, len(singletons))
	for i, s := range singletons {
		before[i] = s.String()
	}
	random := func() *Dec {
		n := new(big.Int).Rand(rng, new(big.Int).Lsh(big.NewInt(1), uint(1+rng.Intn(600))))
		return Make(rng.Intn(2) == 0, n, int32(rng.Intn(40)))
	}
	for i := 0; i < 3000; i++ {
		a, b := random(), random()
		sa, sb := a.Digits.String(), b.Digits.String()
		for _, r := range []*Dec{
			Add(a, b, Pos{}, fail), Sub(a, b, Pos{}, fail), Mul(a, b, Pos{}, fail),
			Add(a, a, Pos{}, fail), Sub(a, a, Pos{}, fail), Mul(a, a, Pos{}, fail),
		} {
			if r.Digits == a.Digits || r.Digits == b.Digits {
				t.Fatal("a result shares an operand's magnitude")
			}
		}
		if a.Digits.String() != sa || b.Digits.String() != sb {
			t.Fatalf("an operand changed: %s, %s", sa, sb)
		}
	}
	for i, s := range singletons {
		if s.String() != before[i] {
			t.Fatalf("shared constant %d changed: %s", i, s)
		}
	}
}
