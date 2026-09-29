package sel

import (
	"fmt"
	"math"
	"unicode/utf8"

	"github.com/nathanjel/sel/go/internal/limits"
)

// The text and collection caps of SPEC §6.4. An operation whose result can be
// larger than what it was given measures the result FIRST, from the lengths of
// its operands, and refuses with E_RANGE at the node that builds it before any
// of it is allocated. Where the result is empty nothing is too large.

const (
	maxTextLen    = int64(limits.MAX_TEXT_LEN)
	maxCollection = int64(limits.MAX_COLLECTION)
)

// runeLen is the length of s in code points.
func runeLen(s string) int64 {
	return int64(utf8.RuneCountInString(s))
}

// satMul multiplies two non-negative lengths, saturating at MaxInt64.
func satMul(a, b int64) int64 {
	if a == 0 || b == 0 {
		return 0
	}
	if a > math.MaxInt64/b {
		return math.MaxInt64
	}
	return a * b
}

// satAdd adds two non-negative lengths, saturating at MaxInt64.
func satAdd(a, b int64) int64 {
	if a > math.MaxInt64-b {
		return math.MaxInt64
	}
	return a + b
}

// checkTextLen refuses a TEXT or BIN result of n code points or bytes.
func checkTextLen(n int64, what string, pos Pos) {
	if n > maxTextLen {
		fail("E_RANGE", fmt.Sprintf("%s would be longer than the %d-unit text limit", what, maxTextLen), pos)
	}
}

// checkCollection refuses a collection result of n children.
func checkCollection(n int64, what string, pos Pos) {
	if n > maxCollection {
		fail("E_RANGE", fmt.Sprintf("%s would have more than the %d-child collection limit", what, maxCollection), pos)
	}
}
