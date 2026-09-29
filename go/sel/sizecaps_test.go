package sel

import (
	"strings"
	"testing"
)

// SPEC §6.4: what an operation builds is measured before it is built.
func TestTextAndCollectionCapsRefuseBeforeAllocating(t *testing.T) {
	for _, c := range []struct{ src, want string }{
		{`REPEAT("ab", 10000000000)`, `!E_RANGE@1:1`},
		{`REPEAT("ab", 99999999999999999999999999999999999999999999)`, `!E_RANGE@1:1`},
		{`PADL("7", 318446744073709551616, "0")`, `!E_RANGE@1:1`},
		{`PADR("7", 100000000, "0")`, `!E_RANGE@1:1`},
		{`S = REPEAT("a", 9000000); S & S`, `!E_RANGE@1:29`},
		{`JOIN(SPLIT(REPEAT("a,", 900000), ","), REPEAT("b", 30))`, `!E_RANGE@1:1`},
		{`SPLIT(REPEAT("x,", 1000000), ",")`, `!E_RANGE@1:1`},
		{`A = 1; A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); COUNT(A)`, `!E_RANGE@1:241`},
	} {
		got := dumpOf(c.src)
		if strings.HasPrefix(c.want, "!") && got != c.want {
			t.Errorf("%.60s\n got  %s\n want %s", c.src, got, c.want)
		}
	}
}

func TestEmptyResultIsNeverTooLarge(t *testing.T) {
	expectDump(t, `REPEAT("", 99999999999999999999)`, `t""`)
	expectDump(t, `REPEAT("", 400000000000000000000000000000000000000000000000000000000000)`, `t""`)
	// A count that only clamps is not an error at all.
	expectDump(t, `LEFT("abc", 99999999999999999999)`, `t"abc"`)
	expectDump(t, `REPEAT("ab", 0)`, `t""`)
}

func TestAtTheCapIsLegal(t *testing.T) {
	expectDump(t, `LEN(REPEAT("a", 16777216))`, `t"16777216"`)
	expectDump(t, `LEN(PADL("", 16777216, "xy"))`, `t"16777216"`)
	expectDump(t, `LEN(REPEAT("a", 16777215) & "b")`, `t"16777216"`)
	expectDump(t, `LEN(REPEAT("a", 16777216) & "b")`, `!E_RANGE@1:27`)
}

func TestLtbIsTotalOnEmptyAndScaledIntegers(t *testing.T) {
	expectDump(t, `TO_HEX(LTB(LIST()))`, `t""`)
	expectDump(t, `TO_HEX(LTB(BTL(FROM_HEX("00ff10"))))`, `t"00ff10"`)
	expectDump(t, `TO_HEX(LTB(LIST(65, 1.0, "2.00")))`, `t"410102"`)
	expectDump(t, `LTB(LIST(1.5))`, `!E_NOT_INT@1:5`)
	expectDump(t, `LTB(LIST(256))`, `!E_RANGE@1:5`)
}
