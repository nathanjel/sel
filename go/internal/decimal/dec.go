// Exact decimal arithmetic. See spec/SPEC.md §4.

package decimal

import (
	"fmt"
	"math"
	"math/big"
	"strings"
	"sync"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
)

type Pos = utf8.Pos
type FailFunc = utf8.FailFunc

const (
	DIV_SCALE       = limits.DIV_SCALE
	MAX_INT_DIGITS  = limits.MAX_INT_DIGITS
	MAX_FRAC_DIGITS = limits.MAX_FRAC_DIGITS
	MAX_INT_BITS    = 3321929
)

type Dec struct {
	Neg    bool
	Digits *big.Int
	Scale  int32
}

var (
	zeroBig   = big.NewInt(0)
	oneBig    = big.NewInt(1)
	twoBig    = big.NewInt(2)
	tenBig    = big.NewInt(10)
	Zero      = Make(false, zeroBig, 0)
	pow10List [19]*big.Int
)

func init() {
	p := big.NewInt(1)
	for i := 0; i <= 18; i++ {
		pow10List[i] = new(big.Int).Set(p)
		p.Mul(p, tenBig)
	}
}

func Make(neg bool, digits *big.Int, scale int32) *Dec {
	d := new(big.Int)
	if digits != nil {
		d.Set(digits)
	}
	actualNeg := neg && d.Sign() != 0
	return &Dec{
		Neg:    actualNeg,
		Digits: d,
		Scale:  scale,
	}
}

var (
	pow10Mu          sync.RWMutex
	pow10Cache       = make(map[int]*big.Int)
	pow10Weight      = 0
	pow10CacheMaxExp = 1000000
	pow10CacheDigits = 1048576
	pow10CacheMax    = 64
)

func Pow10(k int) *big.Int {
	if k >= 0 && k <= 18 {
		return pow10List[k]
	}
	pow10Mu.RLock()
	cached, ok := pow10Cache[k]
	pow10Mu.RUnlock()
	if ok {
		return cached
	}
	v := new(big.Int).Exp(tenBig, big.NewInt(int64(k)), nil)
	if k >= 0 && k <= pow10CacheMaxExp {
		pow10Mu.Lock()
		defer pow10Mu.Unlock()
		// Another evaluator may have filled this entry while we computed it.
		if cached, ok := pow10Cache[k]; ok {
			return cached
		}
		if len(pow10Cache) >= pow10CacheMax || pow10Weight+k > pow10CacheDigits {
			pow10Cache = make(map[int]*big.Int)
			pow10Weight = 0
		}
		pow10Cache[k] = v
		pow10Weight += k
	}
	return v
}

func numDigits(n *big.Int) int {
	if n.Sign() == 0 {
		return 1
	}
	bitLen := n.BitLen()
	d := (bitLen*30103)/100000 + 1
	for n.Cmp(Pow10(d-1)) < 0 {
		d--
	}
	return d
}

func Guard(d *Dec, pos Pos, fail FailFunc) *Dec {
	if int(d.Scale) > MAX_FRAC_DIGITS {
		fail("E_RANGE", fmt.Sprintf("number has more than %d fractional digits", MAX_FRAC_DIGITS), pos)
	}
	if d.Digits.BitLen() >= MAX_INT_BITS {
		if numDigits(d.Digits)-int(d.Scale) > MAX_INT_DIGITS {
			fail("E_RANGE", fmt.Sprintf("number has more than %d integer digits", MAX_INT_DIGITS), pos)
		}
	}
	return d
}

func Parse(text string, pos Pos, fail FailFunc) *Dec {
	if len(text) == 0 {
		return nil
	}
	s := text
	neg := false
	if s[0] == '-' {
		neg = true
		s = s[1:]
	}
	if len(s) == 0 {
		return nil
	}
	dot := -1
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c >= '0' && c <= '9' {
			continue
		}
		if c == '.' {
			if dot >= 0 {
				return nil
			}
			dot = i
			continue
		}
		return nil
	}
	var intPart, fracPart string
	if dot < 0 {
		intPart = s
		fracPart = ""
	} else {
		intPart = s[:dot]
		fracPart = s[dot+1:]
		if len(fracPart) == 0 {
			return nil
		}
	}
	if len(intPart) == 0 {
		return nil
	}
	stripped := strings.TrimLeft(intPart+fracPart, "0")
	if len(stripped) == 0 {
		stripped = "0"
	}
	if len(fracPart) > MAX_FRAC_DIGITS {
		fail("E_RANGE", fmt.Sprintf("number has more than %d fractional digits", MAX_FRAC_DIGITS), pos)
	}
	if len(stripped)-len(fracPart) > MAX_INT_DIGITS {
		fail("E_RANGE", fmt.Sprintf("number has more than %d integer digits", MAX_INT_DIGITS), pos)
	}
	digits := new(big.Int)
	digits.SetString(stripped, 10)
	return Make(neg, digits, int32(len(fracPart)))
}

func Format(d *Dec) string {
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
		pad := strings.Repeat("0", scale+1-len(s))
		s = pad + s
	}
	dotIdx := len(s) - scale
	return sign + s[:dotIdx] + "." + s[dotIdx:]
}

func TrimScale(d *Dec) *Dec {
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

func FromInt(n int64) *Dec {
	neg := n < 0
	// Negate in uint64: -math.MinInt64 does not fit an int64 and wraps to
	// itself, which used to produce a Dec that formatted as "--9223372036854775808".
	abs := uint64(n)
	if neg {
		abs = -abs
	}
	return Make(neg, new(big.Int).SetUint64(abs), 0)
}

func IsZero(d *Dec) bool {
	return d.Digits.Sign() == 0
}

func Negate(d *Dec) *Dec {
	return Make(!d.Neg, d.Digits, d.Scale)
}

func Abs(d *Dec) *Dec {
	return Make(false, d.Digits, d.Scale)
}

func Sign(d *Dec) int {
	if IsZero(d) {
		return 0
	}
	if d.Neg {
		return -1
	}
	return 1
}

func IsInteger(d *Dec) bool {
	if d.Scale == 0 {
		return true
	}
	rem := new(big.Int).Mod(d.Digits, Pow10(int(d.Scale)))
	return rem.Sign() == 0
}

func ToSafeInt(d *Dec) int64 {
	t := Trunc(d)
	// Saturate before narrowing. Consumers either reject a bounded argument
	// or clamp a count to the available input; wrapping defeats both contracts.
	if !t.Digits.IsInt64() {
		if t.Neg {
			return math.MinInt64
		}
		return math.MaxInt64
	}
	val := t.Digits.Int64()
	if t.Neg {
		return -val
	}
	return val
}

func aligned(a, b *Dec) (*big.Int, *big.Int, int32) {
	if a.Scale == b.Scale {
		return a.Digits, b.Digits, a.Scale
	}
	if a.Scale > b.Scale {
		diff := int(a.Scale - b.Scale)
		scaledB := new(big.Int).Mul(b.Digits, Pow10(diff))
		return a.Digits, scaledB, a.Scale
	}
	diff := int(b.Scale - a.Scale)
	scaledA := new(big.Int).Mul(a.Digits, Pow10(diff))
	return scaledA, b.Digits, b.Scale
}

func Add(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	A, B, s := aligned(a, b)
	if a.Neg == b.Neg {
		sum := new(big.Int).Add(A, B)
		return Guard(Make(a.Neg, sum, s), pos, fail)
	}
	cmp := A.Cmp(B)
	if cmp == 0 {
		return Make(false, zeroBig, s)
	}
	if cmp > 0 {
		diff := new(big.Int).Sub(A, B)
		return Make(a.Neg, diff, s)
	}
	diff := new(big.Int).Sub(B, A)
	return Make(b.Neg, diff, s)
}

func Sub(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	A, B, s := aligned(a, b)
	if a.Neg != b.Neg {
		sum := new(big.Int).Add(A, B)
		return Guard(Make(a.Neg, sum, s), pos, fail)
	}
	cmp := A.Cmp(B)
	if cmp == 0 {
		return Make(false, zeroBig, s)
	}
	if cmp > 0 {
		diff := new(big.Int).Sub(A, B)
		return Make(a.Neg, diff, s)
	}
	diff := new(big.Int).Sub(B, A)
	return Make(!a.Neg, diff, s)
}

func Mul(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	neg := a.Neg != b.Neg
	prod := new(big.Int).Mul(a.Digits, b.Digits)
	return Guard(Make(neg, prod, a.Scale+b.Scale), pos, fail)
}

func Cmp(a, b *Dec) int {
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

func Div(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	if IsZero(b) {
		fail("E_DIV_ZERO", "division by zero", pos)
	}
	N := new(big.Int).Set(a.Digits)
	D := new(big.Int).Set(b.Digits)
	if b.Scale > a.Scale {
		N.Mul(N, Pow10(int(b.Scale-a.Scale)))
	} else if a.Scale > b.Scale {
		D.Mul(D, Pow10(int(a.Scale-b.Scale)))
	}
	scaledN := new(big.Int).Mul(N, Pow10(DIV_SCALE))
	q := new(big.Int)
	r := new(big.Int)
	q.QuoRem(scaledN, D, r)
	neg := a.Neg != b.Neg

	if r.Sign() == 0 {
		digits := q
		scale := DIV_SCALE
		rem := new(big.Int)
		for scale > 0 && digits.Sign() != 0 {
			div10 := new(big.Int)
			div10.QuoRem(digits, tenBig, rem)
			if rem.Sign() != 0 {
				break
			}
			digits = div10
			scale--
		}
		if digits.Sign() == 0 {
			scale = 0
		}
		return Guard(Make(neg, digits, int32(scale)), pos, fail)
	}

	twoR := new(big.Int).Mul(twoBig, r)
	if twoR.Cmp(D) >= 0 {
		q.Add(q, oneBig)
	}
	return Guard(Make(neg, q, DIV_SCALE), pos, fail)
}

func Mod(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	if IsZero(b) {
		fail("E_DIV_ZERO", "modulo by zero", pos)
	}
	A, B, s := aligned(a, b)
	rem := new(big.Int).Rem(A, B)
	return Make(a.Neg, rem, s)
}

func Round(d *Dec, n int, pos Pos, fail FailFunc) *Dec {
	if n >= int(d.Scale) {
		p := Pow10(n - int(d.Scale))
		res := new(big.Int).Mul(d.Digits, p)
		return Guard(Make(d.Neg, res, int32(n)), pos, fail)
	}
	p := Pow10(int(d.Scale) - n)
	q := new(big.Int)
	r := new(big.Int)
	q.QuoRem(d.Digits, p, r)
	twoR := new(big.Int).Mul(twoBig, r)
	if twoR.Cmp(p) >= 0 {
		q.Add(q, oneBig)
	}
	return Guard(Make(d.Neg, q, int32(n)), pos, fail)
}

func Trunc(d *Dec) *Dec {
	if d.Scale == 0 {
		return d
	}
	p := Pow10(int(d.Scale))
	q := new(big.Int).Quo(d.Digits, p)
	return Make(d.Neg, q, 0)
}

// Floor and Ceil can add one to the integer part (a carry out of the last
// digit), so a value already at the integer-digit cap can leave it: they take
// the call's position and fail like Round, and Guard the result.
func Floor(d *Dec, pos Pos, fail FailFunc) *Dec {
	if d.Scale == 0 {
		return d
	}
	p := Pow10(int(d.Scale))
	q := new(big.Int)
	r := new(big.Int)
	q.QuoRem(d.Digits, p, r)
	if d.Neg && r.Sign() != 0 {
		q.Add(q, oneBig)
	}
	return Guard(Make(d.Neg, q, 0), pos, fail)
}

func Ceil(d *Dec, pos Pos, fail FailFunc) *Dec {
	if d.Scale == 0 {
		return d
	}
	p := Pow10(int(d.Scale))
	q := new(big.Int)
	r := new(big.Int)
	q.QuoRem(d.Digits, p, r)
	if !d.Neg && r.Sign() != 0 {
		q.Add(q, oneBig)
	}
	return Guard(Make(d.Neg, q, 0), pos, fail)
}

func Power(a *Dec, n int, pos Pos, fail FailFunc) *Dec {
	result := Make(false, oneBig, 0)
	base := a
	e := n
	for e > 0 {
		if e%2 == 1 {
			result = Mul(result, base, pos, fail)
		}
		e /= 2
		if e > 0 {
			base = Mul(base, base, pos, fail)
		}
	}
	return result
}
