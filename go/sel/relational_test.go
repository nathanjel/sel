package sel

import "testing"

// dumpOf runs a program in a fresh context and returns its dump, or !CODE@line:col.
func dumpOf(src string) string {
	return outcome(func() *Value {
		v, err := Eval(src, NewNone())
		if err != nil {
			panic(err)
		}
		return v
	})
}

func expectDump(t *testing.T, src, want string) {
	t.Helper()
	if got := dumpOf(src); got != want {
		t.Errorf("%s\n got  %s\n want %s", src, got, want)
	}
}

// SPEC §7.3: NULL < BOOL < numeric-looking text and numbers (by value) < other
// TEXT < BIN, ties in input order.
func TestTotalOrderRanksKindsThenValues(t *testing.T) {
	expectDump(t, `JOIN(SORT(LIST("10", "9", "1a", "", "007", "-0")), ",")`, `t"-0,007,9,10,,1a"`)
	// BIN sorts after every text, by rank and not by bytes.
	expectDump(t, `X = SORT(LIST(FROM_HEX("00"), "z", "0")); X[1] & X[2]`, `t"0z"`)
	expectDump(t, `X = SORT(LIST(FROM_HEX("00"), "z", "0")); TO_HEX(X[3])`, `t"00"`)
	// BOOL and NULL rank below any text.
	expectDump(t, `X = SORT(LIST("a", TRUE, "1")); IF(X[1] EQL TRUE, "y", "n") & X[2] & X[3]`, `t"y1a"`)
	// Descending reverses unequal ranks and keeps ties in input order.
	expectDump(t, `JOIN(SORT_DESC(LIST("1a", "10", "9", "10.0")), ",")`, `t"1a,10,10.0,9"`)
}

func TestScalarIsAOneElementListForTopAndBucket(t *testing.T) {
	for _, src := range []string{
		`COUNT(TOP("s", 1))`, `COUNT(TOP_DESC(5, 3))`, `COUNT(TOP_BY(5, X, X, 3))`,
		`COUNT(BUCKET("abc", _))`, `COUNT(BUCKET("abc", _, COUNT(_)))`,
	} {
		expectDump(t, src, `t"1"`)
	}
	expectDump(t, `BUCKET(5, _, _)["1"][1]`, `t"5"`)
}

// SPEC §7.4: the direction and the count are arguments like any other.
func TestDirectionIsCheckedOnAnEmptyOrNullList(t *testing.T) {
	expectDump(t, `SORT_BY(LIST(), _ + 0, "X")`, `!E_BAD_ARG@1:24`)
	expectDump(t, `SORT_BY(NULL, _ + 0, "X")`, `!E_BAD_ARG@1:22`)
	expectDump(t, `SORT_BY(LIST(), _ + 0, 1)`, `!E_BAD_ARG@1:24`)
	expectDump(t, `COUNT(SORT_BY(LIST(), _ + 0, "DESC"))`, `t"0"`)
	// TOP is TAKE(SORT(...), n): the keys are evaluated whatever n is.
	expectDump(t, `TOP(LIST(1, 2), 1 / 0, 0)`, `!E_DIV_ZERO@1:19`)
}

func TestTwoArgumentBucketGroupsByKeyText(t *testing.T) {
	expectDump(t, `B = BUCKET(LIST("x", "x", "y"), _); COUNT(B["x"]) & "," & COUNT(B["y"])`, `t"2,1"`)
}

// SPEC §7.3: an aggregate visits a snapshot of what it iterates.
func TestEntryBackedRecordIsIteratedFromASnapshot(t *testing.T) {
	expectDump(t, `R = RECORD("a", 1, "b", 2); JOIN(MAP(R, (R["b"] = 99; _)), ",")`, `t"1,2"`)
}

// SPEC §7.4: one name for both binders means the right row shadows the left.
func TestJoinWithOneBinderNameForBothSidesUsesTheRightRow(t *testing.T) {
	expectDump(t, `P = LIST(RECORD("k", 1), RECORD("k", 2)); `+
		`Q = LIST(RECORD("k", 1), RECORD("k", 2)); COUNT(LINK(P, Q, x, x, x["k"] == x["k"]))`, `t"4"`)
}

// Gate triage: a value with children and no scalar sorts by scalar context
// (spec §3.2 / §7.3): a record by its first field, ties in input order.
func TestRecordsSortByTheirFirstFieldAndTiesKeepInputOrder(t *testing.T) {
	rows := `LIST(RECORD("k", 3, "v", "c"), RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"))`
	ties := `LIST(RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"), RECORD("k", 1.0, "v", "c"), RECORD("k", "1", "v", "d"))`
	join := func(e string) string { return `JOIN(MAP(` + e + `, _["v"]), ",")` }
	expectDump(t, join(rows+` .> SORT()`), `t"a,b,c"`)
	expectDump(t, join(rows+` .> SORT_DESC()`), `t"c,b,a"`)
	expectDump(t, join(ties+` .> SORT()`), `t"a,c,d,b"`)
	expectDump(t, join(ties+` .> SORT_DESC()`), `t"b,a,c,d"`)
	expectDump(t, join(ties+` .> TOP_DESC(4)`), `t"b,a,c,d"`)
	expectDump(t, join(`LIST(RECORD("k", "b", "v", "b"), RECORD("k", 5, "v", "n"), RECORD("k", "a", "v", "a")) .> SORT()`), `t"n,a,b"`)
	expectDump(t, join(`LIST(RECORD("k", 5, "v", "n"), RECORD("k", TRUE, "v", "t"), RECORD("k", FALSE, "v", "f")) .> SORT()`), `t"f,t,n"`)
}
