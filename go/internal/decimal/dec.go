// Package decimal is SEL's exact decimal arithmetic (spec/SPEC.md §4): a
// sign, a big.Int magnitude and a scale, with the digit caps of §6.4.
package decimal

import (
	"fmt"
	"math"
	"math/big"
	"math/bits"
	"strings"
	"sync"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
)

type Pos = utf8.Pos
type FailFunc = utf8.FailFunc

const (
	DIV_SCALE       = limits.DIV_SCALE
	MAX_INT_DIGITS  = limits.MAX_INT_DIGITS
	MAX_FRAC_DIGITS = limits.MAX_FRAC_DIGITS
	// MAX_INT_BITS is ⌈MAX_INT_DIGITS·log2 10⌉: a magnitude of fewer bits is
	// below 2^(MAX_INT_BITS-1) < 10^MAX_INT_DIGITS, so Guard counts digits only
	// from this bit length up. Derived in integer arithmetic between two bounds
	// on log2 10 (log2Of10Lo/Hi over 10^9); D·log2 10 is irrational, so its
	// ceiling is its floor plus one, and the floor is exact when both bounds
	// agree, which the array length below asserts at compile time.
	MAX_INT_BITS = MAX_INT_DIGITS*log2Of10Hi/log2Of10Den + 1
)

// log2 10 = 3.3219280948…, bracketed to nine decimals.
const (
	log2Of10Lo  = 3321928094
	log2Of10Hi  = 3321928095
	log2Of10Den = 1000000000
)

// A compile-time assertion: the array length is negative, and the package does
// not compile, unless the lower bound gives the same MAX_INT_BITS.
var _ [1 - 2*(MAX_INT_BITS-(MAX_INT_DIGITS*log2Of10Lo/log2Of10Den+1))*(MAX_INT_BITS-(MAX_INT_DIGITS*log2Of10Lo/log2Of10Den+1))]struct{}

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
	pow10List [19]*big.Int
	pow10U64  [19]uint64
)

func init() {
	p := big.NewInt(1)
	for i := 0; i <= 18; i++ {
		pow10List[i] = new(big.Int).Set(p)
		pow10U64[i] = p.Uint64()
		p.Mul(p, tenBig)
	}
}

// makeOwned is Make for digits the caller has just computed and will not touch
// again (a Dec is immutable, so nothing else does either): it keeps the big.Int
// instead of copying it, which was two allocations on every operation.
func makeOwned(neg bool, digits *big.Int, scale int32) *Dec {
	return &Dec{Neg: neg && digits.Sign() != 0, Digits: digits, Scale: scale}
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

// log10Of2Q32 is floor(log10(2)·2^32): the bit length of a magnitude brackets its
// digit count without touching a power of ten.
const log10Of2Q32 = 1292913986

// digitBounds returns lo ≤ digits(n) ≤ hi for a positive n of the given bit length.
// 2^(b-1) ≤ n < 2^b, so digits lies between floor((b-1)·log10 2)+1 and
// floor(b·log10 2)+1; the truncated constant can only underestimate the floors, so
// hi carries one more unit of slack and lo stays a valid lower bound.
func digitBounds(bitLen int) (lo, hi int) {
	lo = int((uint64(bitLen-1)*log10Of2Q32)>>32) + 1
	hi = int((uint64(bitLen)*log10Of2Q32)>>32) + 2
	return lo, hi
}

// guardEntry and lastGuard hold the one power of ten Guard compares against when a
// value sits at the integer-digit cap with a large scale (the threshold is then up
// to 2,000,000 digits, beyond what the shared Pow10 cache keeps). One entry, about
// 0.8 MB at most, replaced when the threshold changes.
type guardEntry struct {
	k int
	v *big.Int
}

var lastGuard atomic.Pointer[guardEntry]

func guardPow10(k int) *big.Int {
	if k <= pow10CacheMaxExp {
		return Pow10(k)
	}
	if e := lastGuard.Load(); e != nil && e.k == k {
		return e.v
	}
	v := new(big.Int).Exp(tenBig, big.NewInt(int64(k)), nil)
	lastGuard.Store(&guardEntry{k: k, v: v})
	return v
}

// Guard refuses a magnitude with more than MAX_INT_DIGITS integer digits: that is
// digits(n) > MAX_INT_DIGITS + scale, i.e. n ≥ 10^(MAX_INT_DIGITS+scale). The bit
// length decides every value that is not within a digit or two of the threshold;
// only those pay for a comparison with the power of ten, which is cached.
func Guard(d *Dec, pos Pos, fail FailFunc) *Dec {
	guardMag(d.Digits, d.Scale, pos, fail)
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
	// Up to 18 significant digits fit a uint64 and need no string surgery
	// (the concatenation, the TrimLeft and big.Int.SetString each cost an
	// allocation or a scan). Anything longer takes the general route below.
	if len(intPart)+len(fracPart) <= 40 {
		var u uint64
		sig := 0
		for i := 0; i < len(intPart); i++ {
			if c := intPart[i]; u != 0 || c != '0' {
				u = u*10 + uint64(c-'0')
				sig++
			}
		}
		for i := 0; i < len(fracPart); i++ {
			if c := fracPart[i]; u != 0 || c != '0' {
				u = u*10 + uint64(c-'0')
				sig++
			}
		}
		if sig <= 18 {
			return makeOwned(neg, new(big.Int).SetUint64(u), int32(len(fracPart)))
		}
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
	return makeOwned(neg, parseDigits(stripped), int32(len(fracPart)))
}

// parseLeaf is the size below which big.Int.SetString (quadratic in the digit
// count) is fastest; above it parseDigits halves.
const parseLeaf = 1024

// parseDigits reads a string of decimal digits as a big.Int. SetString is
// quadratic, which made a million-digit numeral cost seconds; this
// splits at the largest parseLeaf·2^j below the length, parses the two halves and
// combines them as hi·10^k + lo, so the multiplications (Karatsuba) dominate and
// the powers of ten come from the Pow10 cache, few and regular.
func parseDigits(s string) *big.Int {
	if len(s) <= parseLeaf {
		d := new(big.Int)
		d.SetString(s, 10)
		return d
	}
	k := parseLeaf
	for k*2 < len(s) {
		k *= 2
	}
	hi := parseDigits(s[:len(s)-k])
	lo := parseDigits(s[len(s)-k:])
	hi.Mul(hi, Pow10(k))
	return hi.Add(hi, lo)
}

// maxFastScale bounds the fraction width Format handles in its stack buffer.
const maxFastScale = 40

func Format(d *Dec) string {
	// A word-sized magnitude with a modest scale is rendered in one stack
	// buffer, with no big.Int decimal conversion (nat.itoa) and no intermediate
	// strings. Numeric literals and every number-to-text conversion take this path.
	if d.Scale <= maxFastScale && d.Digits.IsUint64() {
		var buf [1 + 20 + 1 + maxFastScale + 1]byte
		return string(appendFormatU64(buf[:0], d.Neg, d.Digits.Uint64(), int(d.Scale)))
	}
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

// appendFormatU64 appends sign, the digits of m with the decimal point `scale`
// places from the right, padded with leading zeros so at least one digit precedes it.
func appendFormatU64(dst []byte, neg bool, m uint64, scale int) []byte {
	if neg {
		dst = append(dst, '-')
	}
	var tmp [20]byte
	i := len(tmp)
	for {
		i--
		tmp[i] = byte('0' + m%10)
		m /= 10
		if m == 0 {
			break
		}
	}
	digits := tmp[i:]
	if scale == 0 {
		return append(dst, digits...)
	}
	if len(digits) <= scale {
		dst = append(dst, '0', '.')
		for k := scale - len(digits); k > 0; k-- {
			dst = append(dst, '0')
		}
		return append(dst, digits...)
	}
	dst = append(dst, digits[:len(digits)-scale]...)
	dst = append(dst, '.')
	return append(dst, digits[len(digits)-scale:]...)
}

func TrimScale(d *Dec) *Dec {
	if d.Digits.Sign() == 0 {
		return Make(false, zeroBig, 0)
	}
	if d.Scale == 0 {
		return d
	}
	if d.Digits.IsUint64() {
		// Strip trailing zeros of a word-sized magnitude arithmetically,
		// with no decimal string to build, trim and parse back.
		m := d.Digits.Uint64()
		zeros := int32(0)
		for zeros < d.Scale && m%10 == 0 {
			m /= 10
			zeros++
		}
		if zeros == 0 {
			return d
		}
		return makeOwned(d.Neg, new(big.Int).SetUint64(m), d.Scale-zeros)
	}
	// A magnitude of any other size is stripped without a decimal string.
	// A trailing zero needs the magnitude even, which one bit answers; only an even
	// one pays a linear pass for the remainder mod 10, and only one that really ends
	// in zeros pays for the divisions below.
	n := d.Digits
	if n.Bit(0) != 0 || new(big.Int).Rem(n, tenBig).Sign() != 0 {
		return d
	}
	// Binary lifting over the zero count: the set of z with 10^z | n is downward
	// closed, so testing 10^step from the largest power of two down finds the
	// maximal z ≤ scale exactly.
	q := new(big.Int).Set(n)
	rem := new(big.Int)
	zeros := 0
	maxZ := int(d.Scale)
	step := 1
	for step*2 <= maxZ {
		step *= 2
	}
	for ; step >= 1; step >>= 1 {
		if zeros+step > maxZ {
			continue
		}
		next, r := new(big.Int), rem
		next.QuoRem(q, Pow10(step), r)
		if r.Sign() == 0 {
			q = next
			zeros += step
		}
	}
	if zeros == 0 {
		return d
	}
	return makeOwned(d.Neg, q, d.Scale-int32(zeros))
}

func FromInt(n int64) *Dec {
	neg := n < 0
	// Negate in uint64: -math.MinInt64 does not fit an int64 and wraps to
	// itself, which used to produce a Dec that formatted as "--9223372036854775808".
	abs := uint64(n)
	if neg {
		abs = -abs
	}
	return makeOwned(neg, new(big.Int).SetUint64(abs), 0)
}

// byteDecs holds the 256 decimals 0..255. A Dec is never changed after it is
// built, so every byte-valued result (BTL, a code unit) may share one.
var byteDecs = func() (t [256]*Dec) {
	for i := range t {
		t[i] = FromInt(int64(i))
	}
	return
}()

// FromByte returns the shared immutable decimal for b.
func FromByte(b byte) *Dec { return byteDecs[b] }

func IsZero(d *Dec) bool {
	return d.Digits.Sign() == 0
}

// Negate and Abs share the magnitude: a Dec is never changed after it is built,
// so a sign flip needs a new header and nothing more.
func Negate(d *Dec) *Dec {
	return &Dec{Neg: !d.Neg && d.Digits.Sign() != 0, Digits: d.Digits, Scale: d.Scale}
}

func Abs(d *Dec) *Dec {
	return &Dec{Neg: false, Digits: d.Digits, Scale: d.Scale}
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
	if d.Scale == 0 || d.Digits.Sign() == 0 {
		return true
	}
	// An integer needs 10^scale to divide the magnitude. An odd magnitude
	// cannot be, and neither can one with no more digits than the scale (it is
	// nonzero and smaller than 10^scale); both are decided without a division.
	if d.Digits.Bit(0) != 0 {
		return false
	}
	if _, hi := digitBounds(d.Digits.BitLen()); hi <= int(d.Scale) {
		return false
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

// Add returns a+b as a Dec of its own (as Sub and Mul do); the arithmetic, and its sign,
// scale and E_RANGE rules, is AddInto's, SubInto's and MulInto's (reg.go), which
// a math plan also uses to keep intermediates in registers.
func Add(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	return AddNew(nil, NumOf(a), NumOf(b), pos, fail)
}

func Sub(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	return SubNew(nil, NumOf(a), NumOf(b), pos, fail)
}

func Mul(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	return MulNew(NumOf(a), NumOf(b), pos, fail)
}

func Cmp(a, b *Dec) int {
	if a.Neg != b.Neg {
		if a.Neg {
			return -1
		}
		return 1
	}
	var c int
	if a.Scale == b.Scale {
		c = a.Digits.Cmp(b.Digits)
	} else {
		c = cmpScaled(a, b)
	}
	if a.Neg {
		return -c
	}
	return c
}

// cmpScaled orders the magnitudes of two values with different scales. It
// builds no scaled copy when it can avoid it: a zero decides by itself; two
// word-sized magnitudes with a gap of at most 18 are compared as 128-bit products;
// and two magnitudes whose integer-part sizes are apart by more than the bit-length
// bracket allows are ordered by those sizes. Anything else aligns, as before.
func cmpScaled(a, b *Dec) int {
	az, bz := a.Digits.Sign() == 0, b.Digits.Sign() == 0
	if az || bz {
		switch {
		case az && bz:
			return 0
		case az:
			return -1
		}
		return 1
	}
	if a.Digits.IsUint64() && b.Digits.IsUint64() {
		x, y := a.Digits.Uint64(), b.Digits.Uint64()
		if a.Scale > b.Scale && a.Scale-b.Scale <= 18 {
			hi, lo := bits.Mul64(y, pow10U64[a.Scale-b.Scale])
			return cmp128(0, x, hi, lo)
		}
		if b.Scale > a.Scale && b.Scale-a.Scale <= 18 {
			hi, lo := bits.Mul64(x, pow10U64[b.Scale-a.Scale])
			return cmp128(hi, lo, 0, y)
		}
	}
	loA, hiA := digitBounds(a.Digits.BitLen())
	loB, hiB := digitBounds(b.Digits.BitLen())
	if hiA-int(a.Scale) < loB-int(b.Scale) {
		return -1
	}
	if hiB-int(b.Scale) < loA-int(a.Scale) {
		return 1
	}
	A, B, _ := aligned(a, b)
	return A.Cmp(B)
}

func cmp128(ah, al, bh, bl uint64) int {
	switch {
	case ah != bh:
		if ah < bh {
			return -1
		}
		return 1
	case al < bl:
		return -1
	case al > bl:
		return 1
	}
	return 0
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
		return Guard(makeOwned(neg, digits, int32(scale)), pos, fail)
	}

	twoR := new(big.Int).Mul(twoBig, r)
	if twoR.Cmp(D) >= 0 {
		q.Add(q, oneBig)
	}
	return Guard(makeOwned(neg, q, DIV_SCALE), pos, fail)
}

func Mod(a, b *Dec, pos Pos, fail FailFunc) *Dec {
	if IsZero(b) {
		fail("E_DIV_ZERO", "modulo by zero", pos)
	}
	A, B, s := aligned(a, b)
	rem := new(big.Int).Rem(A, B)
	return makeOwned(a.Neg, rem, s)
}

func Round(d *Dec, n int, pos Pos, fail FailFunc) *Dec {
	if n >= int(d.Scale) {
		p := Pow10(n - int(d.Scale))
		res := new(big.Int).Mul(d.Digits, p)
		return Guard(makeOwned(d.Neg, res, int32(n)), pos, fail)
	}
	p := Pow10(int(d.Scale) - n)
	q := new(big.Int)
	r := new(big.Int)
	q.QuoRem(d.Digits, p, r)
	twoR := new(big.Int).Mul(twoBig, r)
	if twoR.Cmp(p) >= 0 {
		q.Add(q, oneBig)
	}
	return Guard(makeOwned(d.Neg, q, int32(n)), pos, fail)
}

func Trunc(d *Dec) *Dec {
	if d.Scale == 0 {
		return d
	}
	p := Pow10(int(d.Scale))
	q := new(big.Int).Quo(d.Digits, p)
	return makeOwned(d.Neg, q, 0)
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
	return Guard(makeOwned(d.Neg, q, 0), pos, fail)
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
	return Guard(makeOwned(d.Neg, q, 0), pos, fail)
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
