package sel

import (
	"fmt"
	"strconv"
	"strings"
	"testing"
)

// A FILTER that opens with IS_NULL of a LINK_LEFT's right member lets the join
// skip building the joined rows of the right rows that conjunct is FALSE on
// (rightNullRejects). Every shape must answer -- rows, keys, errors and their
// places -- what the join bound to a helper variable first answers, where
// nothing is handed between the two.

func rowsOf(t *testing.T, list string) *Value {
	t.Helper()
	v, err := Eval(list, nil)
	if err != nil {
		t.Fatalf("%s: %v", list, err)
	}
	return v
}

// bothForms runs src, a pipeline ending in `.> FILTER(…)`, as written and with
// everything before that FILTER bound to J first. An error is told by its code
// and where it is: in the join, or in the FILTER on, as an offset from there.
func bothForms(src string, ctx func() *Value) (string, string) {
	cut := strings.LastIndex(src, " .> FILTER(")
	head, tail := src[:cut], src[cut+len(" .> FILTER("):]
	run := func(prog string, headAt int) string {
		got := runOnce(prog, ctx())
		if !strings.HasPrefix(got, "!") {
			return got
		}
		code, at, _ := strings.Cut(got[1:], "@")
		_, col, _ := strings.Cut(at, ":")
		n, _ := strconv.Atoi(col)
		filterAt := strings.LastIndex(prog, "FILTER("+tail)
		if n-1 >= filterAt {
			return fmt.Sprintf("!%s filter+%d", code, n-1-filterAt)
		}
		return fmt.Sprintf("!%s join+%d", code, n-1-headAt)
	}
	return run(head+" .> FILTER("+tail, 0), run("J = "+head+"; J .> FILTER("+tail, 4)
}

// joinAllocs is what a run of src costs in allocations over P and I of n rows
// each, every pair of them joined: as written, the n*n joined rows cost at
// least one each; with the right rows rejected none of them is built.
func joinAllocs(t *testing.T, src string, n int) float64 {
	t.Helper()
	p := make([]*Value, n)
	i := make([]*Value, n)
	for k := range p {
		p[k] = rec("id", 1, "name", "a")
		i[k] = rec("id", 10+k, "product_id", 1)
	}
	ctx := ctxWith("P", newListOwned(p), "I", newListOwned(i))
	prog := MustCompile(src)
	return testing.AllocsPerRun(2, func() { _, _ = prog.Run(ctx) })
}

const rightNullRows = 60

// rejectionTaken: the run builds well under the n*n joined rows as written.
func rejectionTaken(t *testing.T, src string) bool {
	t.Helper()
	return joinAllocs(t, src, rightNullRows) < rightNullRows*rightNullRows/2
}

func TestLeftJoinRightNullKeepsResultsKeysAndErrors(t *testing.T) {
	ctx := func() *Value {
		return ctxWith(
			"P", rowsOf(t, `LIST(RECORD("id", 1, "name", "a"), RECORD("id", 2, "name", "b"), RECORD("id", 3, "name", 5), RECORD("id", 4, "name", "d"))`),
			"I", rowsOf(t, `LIST(RECORD("id", 10, "product_id", 1), RECORD("id", NULL, "product_id", 1), RECORD("id", 12, "product_id", 3), RECORD("product_id", 9))`))
	}
	const left = `P .> LINK_LEFT(I, _1["id"] == _2["product_id"])`
	for _, src := range []string{
		left + ` .> FILTER(IS_NULL(_["i"]["id"]))`,
		left + ` .> FILTER(IS_NULL(_["I"]["id"])) .> MAP(_K)`,
		left + ` .> FILTER(IS_NULL(_["_2"]["id"]) AND _K $!= "2")`,
		// The FILTER's own _K reads the join's keys, even where the next step
		// renumbers the FILTER's.
		left + ` .> FILTER(IS_NULL(_["i"]["id"]) AND _K $!= "2") .> MAP(_["p"]["id"])`,
		left + ` .> FILTER(IS_NULL(_["i"]["id"]) AND ANY(LIST(_K), _ $== "3")) .> TAKE(5)`,
		left + ` .> FILTER(r, IS_NULL(r["i"]["id"]) AND r["p"]["name"] > 1)`,
		left + ` .> FILTER(IS_NULL(_["i"]["id"]) AND _["p"]["name"] $!= "b") .> MAP(_["p"]["id"])`,
		left + ` .> FILTER(IS_NULL(_["i"]["product_id"]))`,
		left + ` .> FILTER(IS_NULL(_["i"]["sku"]))`,
	} {
		asWritten, throughAVariable := bothForms(src, ctx)
		if asWritten != throughAVariable {
			t.Errorf("%s\n as written         %s\n through a variable %s", src, asWritten, throughAVariable)
		}
		// The path is taken; it rejects rows only where the field is there and
		// not NULL (no item has a sku: every row is the FILTER's to raise on).
		if taken := rejectionTaken(t, src); taken != !strings.Contains(src, "sku") {
			t.Errorf("%s: the joined rows were left unbuilt: %v", src, taken)
		}
	}
	if got := runOnce(left+` .> FILTER(IS_NULL(_["i"]["id"])) .> MAP(_["p"]["id"] & ":" & _K)`, ctx()); got != `-{"1"=t"1:2", "2"=t"2:3", "3"=t"4:5"}` {
		t.Errorf("a matched NULL id, and the unmatched rows, under the keys as written: %s", got)
	}
}

func TestLeftJoinRightNullDeclinesWhatIsNotTheRightRow(t *testing.T) {
	ctx := func() *Value {
		return ctxWith("P", rowsOf(t, `LIST(RECORD("id", 1), RECORD("id", 2))`),
			"I", rowsOf(t, `LIST(RECORD("id", 10, "product_id", 1))`))
	}
	for _, src := range []string{
		// not a right binder key of this join: the left one, a mixed-case name,
		// a relation name under explicit binders
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["p"]["id"]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["iI"]["id"]))`,
		`LINK_LEFT(P, I, L, R, L["id"] == R["product_id"]) .> FILTER(IS_NULL(_["I"]["id"]))`,
		// binders spelled alike
		`LINK_LEFT(P, I, X, x, X["id"] == x["product_id"]) .> FILTER(IS_NULL(_["x"]["id"]))`,
	} {
		if asWritten, throughAVariable := bothForms(src, ctx); asWritten != throughAVariable {
			t.Errorf("%s\n as written         %s\n through a variable %s", src, asWritten, throughAVariable)
		}
		if rejectionTaken(t, src) {
			t.Errorf("%s: right rows were rejected", src)
		}
	}
}

func TestLeftJoinRightNullIsOfferedOnlyForItsShape(t *testing.T) {
	for _, src := range []string{
		// not IS_NULL first, not a literal member or field, not the FILTER's own element
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(TRUE AND IS_NULL(_["i"]["id"]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(NOT IS_NULL(_["i"]["id"]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_[LOWER("I")]["id"]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"][LOWER("ID")]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(r, IS_NULL(_["i"]["id"]))`,
		// an inner join, and a FILTER handed conjuncts by a join above
		`P .> LINK(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"]))`,
		`P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"]))` +
			` .> LINK(I, L, R, L["p"]["id"] == R["product_id"]) .> FILTER(_["p"]["id"] > 0) .> MAP(1)`,
	} {
		if rejectionTaken(t, src) {
			t.Errorf("%s: right rows were rejected", src)
		}
	}
	// The shape itself, for the contrast.
	if !rejectionTaken(t, `P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"]))`) {
		t.Error("the leading IS_NULL of a right member's field rejected nothing")
	}
}

// The join as written builds every matched row before the FILTER drops it, and
// raises E_RANGE at the join when they are more than MAX_COLLECTION: a row the
// rejection never builds still counts, at the same place.
func TestLeftJoinRightNullHoldsTheRealCollectionLimit(t *testing.T) {
	side := 1
	for side*side < int(maxCollection) {
		side++
	}
	if int64(side*side) != maxCollection {
		t.Fatalf("MAX_COLLECTION %d is no square", maxCollection)
	}
	const src = `P .> LINK_LEFT(I, _1["id"] == _2["k"]) .> FILTER(IS_NULL(_["i"]["id"]))`
	rows := func(n int, kv ...interface{}) *Value {
		items := make([]*Value, n)
		for k := range items {
			items[k] = rec(kv...)
		}
		return newListOwned(items)
	}
	prog := MustCompile(src)
	for _, c := range []struct {
		left int
		want string
	}{
		{side, `-`},
		{side + 1, fmt.Sprintf("!E_RANGE@1:%d", strings.Index(src, "LINK_LEFT")+1)},
	} {
		ctx := ctxWith("P", rows(c.left, "id", 1), "I", rows(side, "id", 5, "k", 1))
		got := outcome(func() *Value {
			v, err := prog.Run(ctx)
			if err != nil {
				panic(err)
			}
			return v
		})
		if got != c.want {
			t.Errorf("%d left rows x %d matches: got %s, want %s", c.left, side, got, c.want)
		}
	}
}
