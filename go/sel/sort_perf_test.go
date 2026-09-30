package sel

import (
	"fmt"
	"testing"
)

func sgn(x int) int {
	switch {
	case x < 0:
		return -1
	case x > 0:
		return 1
	}
	return 0
}

// GO-P2: the keys are classified once; the classified order must be the order
// compareValues defines, for every pair of a list spanning every kind.
func TestClassifiedSortKeyOrderMatchesCompareValues(t *testing.T) {
	var vals []*Value
	vals = append(vals, NewNull(), NewNone(), NewBool(false), NewBool(true), NewBin([]byte{0}), NewBin([]byte("z")), NewBin(nil))
	for _, s := range []string{"", " 2", "1a", "abc", "é", "😀", "Z", "z", "1e3", "0x10", "NaN"} {
		vals = append(vals, NewText(s))
	}
	for _, s := range []string{"0", "-0", "007", "7", "9", "10", "1.5", "1.50", "-1.5", "-10", "0.0000001",
		"999999999999999999", "1000000000000000000", "-999999999999999999", "-1000000000000000000",
		"123456789012345678901234567890", "-123456789012345678901234567890", "9223372036854775807",
		"9223372036854775808", "100000000000000000000.5"} {
		vals = append(vals, NewText(s))
	}
	vals = append(vals, NewInt(5), NewInt(-5), NewInt(0))
	vals = append(vals, rec("a", "x", "b", 2), rec("a", 3, "b", 1), rec("a", "4"), NewListOwned([]*Value{NewInt(9), NewInt(1)}),
		NewListOwned([]*Value{rec("q", "deep")}))
	for i, a := range vals {
		ka := classifySortKey(a)
		for j, b := range vals {
			kb := classifySortKey(b)
			want := sgn(compareValues(a, b))
			if got := sgn(cmpSortKey(&ka, &kb)); got != want {
				t.Errorf("pair %d,%d: classified %d, compareValues %d", i, j, got, want)
			}
		}
	}
}

// GO-P3: a bounded selection returns what the full sort's prefix returns, ties in
// input order, in both directions, for every limit on both sides of the switch.
func TestTopSelectionEqualsTheSortedPrefix(t *testing.T) {
	ctx := func() *Value {
		s := lcg(99)
		items := make([]*Value, 240)
		for i := range items {
			items[i] = rec("n", int(s.next()%12), "id", i, "t", fmt.Sprintf("x%02d", s.next()%30))
		}
		return ctxWith("L", NewListOwned(items), "M", NewListOwned(func() []*Value {
			s := lcg(7)
			out := make([]*Value, 240)
			for i := range out {
				out[i] = NewInt(int64(s.next() % 40))
			}
			return out
		}()))
	}
	for _, k := range []int{0, 1, 2, 5, 29, 30, 31, 100, 239, 240, 241, 1000} {
		for _, dir := range []string{"ASC", "DESC"} {
			top := fmt.Sprintf(`JOIN(MAP(TOP_BY(L, _["n"], %q, %d), _["id"]), ",")`, dir, k)
			ref := fmt.Sprintf(`JOIN(MAP(TAKE(SORT_BY(L, _["n"], %q), %d), _["id"]), ",")`, dir, k)
			if got, want := runOnce(top, ctx()), runOnce(ref, ctx()); got != want {
				t.Errorf("TOP_BY k=%d %s\n got  %.160s\n want %.160s", k, dir, got, want)
			}
		}
		topD := fmt.Sprintf(`JOIN(TOP_DESC(M, %d), ",")`, k)
		refD := fmt.Sprintf(`JOIN(TAKE(SORT_DESC(M), %d), ",")`, k)
		if got, want := runOnce(topD, ctx()), runOnce(refD, ctx()); got != want {
			t.Errorf("TOP_DESC k=%d\n got  %.160s\n want %.160s", k, got, want)
		}
		topA := fmt.Sprintf(`JOIN(TOP(M, %d), ",")`, k)
		refA := fmt.Sprintf(`JOIN(TAKE(SORT(M), %d), ",")`, k)
		if got, want := runOnce(topA, ctx()), runOnce(refA, ctx()); got != want {
			t.Errorf("TOP k=%d\n got  %.160s\n want %.160s", k, got, want)
		}
	}
}

// TOP with n = 0 still evaluates every key (SPEC §7.4), and a bad one raises.
func TestTopZeroStillEvaluatesKeys(t *testing.T) {
	expectDump(t, `TOP(LIST(1, 2), 1 / 0, 0)`, `!E_DIV_ZERO@1:19`)
	expectDump(t, `COUNT(TOP(LIST(3, 1, 2), 0))`, `t"0"`)
}
