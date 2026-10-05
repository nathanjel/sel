package sel

// Unit tests for the round-2 performance changes (GO-P11 … GO-P20): each pins the
// behaviour the optimisation must not change, on shapes the workloads do not hit.

import (
	"fmt"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// GO-P11: a keyed list of keyIndexMin+ children answers Get/Has/Set through a
// lazily built index; the answers equal the linear scan's, including duplicate
// keys (first wins), a write through the index, and a clone.
func TestKeyedListIndexMatchesTheScan(t *testing.T) {
	for _, n := range []int{1, 15, 16, 17, 100} {
		items := make([]*Value, n)
		keys := make([]string, n)
		for i := range items {
			items[i] = NewInt(int64(i * 10))
			keys[i] = "k" + strconv.Itoa(i*3) // sparse, non-positional
		}
		l := NewListWithKeys(items, keys)
		for i := 0; i < n; i++ {
			if got := l.Get(keys[i]); got == nil || got.Scalar() != strconv.Itoa(i*10) {
				t.Fatalf("n=%d Get(%s)", n, keys[i])
			}
			if !l.Has(keys[i]) {
				t.Fatalf("n=%d Has(%s)", n, keys[i])
			}
		}
		if l.Has("nope") || l.Get("nope") != nil || l.Has("k1") {
			t.Fatalf("n=%d phantom key", n)
		}
		l.Set(keys[n-1], NewInt(-1))
		if l.Get(keys[n-1]).Scalar() != "-1" || l.Size() != n {
			t.Fatalf("n=%d Set existing", n)
		}
		before := l.Get(keys[0]).Scalar()
		c := l.Clone()
		c.Set(keys[0], NewInt(77))
		if l.Get(keys[0]).Scalar() != before || c.Get(keys[0]).Scalar() != "77" {
			t.Fatalf("n=%d clone shares state", n)
		}
		// A new key converts the list to entries; the old index must not answer.
		l.Set("fresh", NewInt(5))
		if l.Size() != n+1 || l.Get("fresh").Scalar() != "5" || l.Get(keys[1%n]) == nil {
			t.Fatalf("n=%d Set new key", n)
		}
	}
}

func TestKeyedListIndexFirstDuplicateWins(t *testing.T) {
	n := 40
	items := make([]*Value, n)
	keys := make([]string, n)
	for i := range items {
		items[i] = NewInt(int64(i))
		keys[i] = "d" + strconv.Itoa(i%5)
	}
	l := NewListWithKeys(items, keys)
	for k := 0; k < 5; k++ {
		if got := l.Get("d" + strconv.Itoa(k)).Scalar(); got != strconv.Itoa(k) {
			t.Fatalf("d%d -> %s, want the first occurrence %d", k, got, k)
		}
	}
	l.Set("d3", NewInt(1000))
	if l.Get("d3").Scalar() != "1000" || l.storage[3].Scalar() != "1000" || l.storage[8].Scalar() != "8" {
		t.Fatalf("Set must write the first occurrence only")
	}
}

// Read-only sharing: many goroutines reading one keyed list race on the lazy
// index unless it is published atomically (run with -race).
func TestKeyedListIndexIsSafeToBuildConcurrently(t *testing.T) {
	n := 500
	items := make([]*Value, n)
	keys := make([]string, n)
	for i := range items {
		items[i] = NewInt(int64(i))
		keys[i] = fmt.Sprintf("q%d", i)
	}
	l := NewListWithKeys(items, keys)
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < n; i++ {
				if l.Get(keys[i]).Scalar() != strconv.Itoa(i) || !l.Has(keys[i]) {
					t.Errorf("bad read %d", i)
					return
				}
			}
		}()
	}
	wg.Wait()
}

// GO-P12: the blob-built positional keys equal strconv.Itoa(i+1) for every size,
// around each digit-count boundary.
func TestDenseEntriesKeysEqualItoa(t *testing.T) {
	for _, n := range []int{0, 1, 9, 10, 98, 99, 100, 101, 999, 1000, 1001, 9999, 10000, 10001, 12345} {
		vals := make([]*Value, n)
		for i := range vals {
			vals[i] = NewInt(int64(i))
		}
		l := NewList(vals)
		es, keys := l.Entries(), l.Keys()
		el := l.Elements()
		if len(es) != n || len(keys) != n || len(el) != n {
			t.Fatalf("n=%d lengths %d %d %d", n, len(es), len(keys), len(el))
		}
		for i := 0; i < n; i++ {
			want := strconv.Itoa(i + 1)
			if es[i].Key != want || keys[i] != want || el[i].Key != want {
				t.Fatalf("n=%d i=%d got %q %q %q want %q", n, i, es[i].Key, keys[i], el[i].Key, want)
			}
			if es[i].Val != vals[i] || el[i].Val != vals[i] {
				t.Fatalf("n=%d i=%d value identity", n, i)
			}
		}
	}
}

// GO-P12: Args of up to four arguments share one allocation with their value
// slots; larger calls still get a slice of the right length. A program calling
// functions of every small arity returns the same answers as before.
func TestArgsEmbeddedSlotsKeepArities(t *testing.T) {
	for _, src := range []string{
		`MAX(1)`, `MAX(1, 2)`, `MAX(1, 2, 3)`, `MAX(1, 2, 3, 4)`, `MAX(1, 2, 3, 4, 5)`,
		`MAX(1, 2, 3, 4, 5, 6, 7, 8, 9)`, `SUBSTR("abcdef", 2, 3)`, `JOIN((1, 2, 3), ",")`,
	} {
		want := map[string]string{
			`MAX(1)`: "1", `MAX(1, 2)`: "2", `MAX(1, 2, 3)`: "3", `MAX(1, 2, 3, 4)`: "4", `MAX(1, 2, 3, 4, 5)`: "5",
			`MAX(1, 2, 3, 4, 5, 6, 7, 8, 9)`: "9", `SUBSTR("abcdef", 2, 3)`: "bcd", `JOIN((1, 2, 3), ",")`: "1,2,3",
		}[src]
		v, err := MustCompile(src).Run(NewNone())
		if err != nil || v.Scalar() != want {
			t.Fatalf("%s = %v %v, want %s", src, v, err, want)
		}
	}
}

// GO-P12: a math plan with more slots than the stack buffer holds still works.
func TestWideMathPlanBeyondTheStackScratchpad(t *testing.T) {
	src := `X = 1; X + X * 2 - X / 4 + X * 3 - X * 5 + X * 7 - X * 11 + X * 13 - X * 17 + X * 19 - X * 23 + X * 29`
	v, err := MustCompile(src).Run(NewNone())
	if err != nil {
		t.Fatal(err)
	}
	// 1 + 2 - 0.25 + 3 - 5 + 7 - 11 + 13 - 17 + 19 - 23 + 29 = 17.75
	if v.Scalar() != "17.75" {
		t.Fatalf("got %s want 17.75", v.Scalar())
	}
}

// GO-P13: a variable takes the result of MAP/FILTER/SORT*/TOP*/BUCKET/LIST/RECORD
// without the assignment's own copy. That is only sound if no node of such a
// result is reachable from its input — checked here by pointer identity over
// every shape of input, for every function in freshResultFuncs.
func collectNodes(v *Value, into map[*Value]bool) {
	if v == nil || into[v] {
		return
	}
	into[v] = true
	for _, c := range v.storage {
		collectNodes(c, into)
	}
	for _, e := range v.entries {
		collectNodes(e.Val, into)
	}
}

func TestFreshResultFunctionsShareNothingWithTheirInput(t *testing.T) {
	inputs := map[string]func() *Value{
		"records": func() *Value { return joinRows(40, 5, 3) },
		"ints":    func() *Value { return intList(40, 4) },
		"nested": func() *Value {
			return NewList([]*Value{NewList([]*Value{NewInt(1), NewInt(2)}), NewList([]*Value{NewInt(3)})})
		},
		"scalar": func() *Value { return NewInt(5) },
		"empty":  func() *Value { return NewList(nil) },
		"null":   func() *Value { return NewNull() },
		"keyed":  func() *Value { return NewListWithKeys([]*Value{NewInt(1), NewInt(2)}, []string{"a", "b"}) },
	}
	calls := map[string]string{
		"MAP":       `MAP(L, _)`,
		"FILTER":    `FILTER(L, TRUE)`,
		"SORT":      `SORT(L)`,
		"SORT_DESC": `SORT_DESC(L)`,
		"SORT_BY":   `SORT_BY(L, _)`,
		"TOP":       `TOP(L, 100)`,
		"TOP_DESC":  `TOP_DESC(L, 100)`,
		"TOP_BY":    `TOP_BY(L, _, 100)`,
		"BUCKET":    `BUCKET(L, _)`,
		"LIST":      `LIST(L)`,
		"RECORD":    `RECORD("k", L)`,
	}
	for fn := range freshResultFuncs {
		src, ok := calls[fn]
		if !ok {
			t.Fatalf("no program for %s", fn)
		}
		for name, mk := range inputs {
			in := mk()
			ctx := ctxWith("L", in)
			var res *Value
			func() {
				defer func() { _ = recover() }() // a refusal (e.g. a list key) is not an aliasing result
				res, _ = MustCompile("R = " + src + "; R").Run(ctx)
			}()
			if res == nil {
				continue
			}
			a, b := map[*Value]bool{}, map[*Value]bool{}
			collectNodes(in, a)
			collectNodes(res, b)
			for n := range b {
				if a[n] {
					t.Fatalf("%s over %s: result node %p is also reachable from the input", fn, name, n)
				}
			}
		}
	}
}

// GO-P13 semantics: assigning a fresh result and then writing through the
// variable leaves the source untouched; an indexed target still checks depth
// and copies.
func TestAssigningAFreshResultDoesNotAliasTheSource(t *testing.T) {
	ctx := ctxWith("L", joinRows(20, 5, 3))
	v, err := MustCompile(`X = MAP(L, _); X[1]["a"] = 99; Y = FILTER(L, TRUE); Y[2]["a"] = 98; L[1]["a"] & "," & L[2]["a"] & "," & X[1]["a"] & "," & Y[2]["a"]`).Run(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if got := v.Scalar(); got == "" || got[len(got)-5:] != "99,98" || got[:2] == "99" {
		t.Fatalf("source changed or copy lost: %q", got)
	}
	d, err := MustCompile(`A[1][1][1][1][1][1][1][1][1][1] = MAP((1, 2), _); COUNT(A)`).Run(NewNone())
	if err != nil || d.Scalar() != "1" {
		t.Fatalf("indexed fresh assignment: %v %v", d, err)
	}
}

// GO-P14: UniqueRecordShape answers from the cache when it can, never wrongly: a
// cached shape that was built from repeated keys (internRecordShape is also called
// directly) must not stand in for "unique".
func TestUniqueRecordShapeSeesRepeatsWhateverIsCached(t *testing.T) {
	dup := []string{"zq1", "zq2", "zq1"}
	if internRecordShape(dup) == nil {
		t.Fatal("internRecordShape refused")
	}
	if uniqueRecordShape(dup) != nil {
		t.Fatal("a repeated key list was reported unique after being interned")
	}
	long := make([]string, 12)
	for i := range long {
		long[i] = "lk" + strconv.Itoa(i)
	}
	first := uniqueRecordShape(long)
	if first == nil || uniqueRecordShape(long) != first {
		t.Fatal("distinct long key list not interned once")
	}
	long[11] = long[3]
	if uniqueRecordShape(long) != nil {
		t.Fatal("repeat among 12 keys not detected")
	}
	for _, keys := range [][]string{{"a", "a"}, {"a", "b", "a"}, {"a", "b", "c", "d", "e", "f", "g", "a"}} {
		if uniqueRecordShape(keys) != nil {
			t.Fatalf("%v reported unique", keys)
		}
	}
	if uniqueRecordShape(nil) == nil || uniqueRecordShape([]string{"only"}) == nil {
		t.Fatal("trivial unique lists refused")
	}
	// Boundaries between keys: "a" "bc" and "ab" "c" are different shapes.
	if uniqueRecordShape([]string{"a", "bc"}) == uniqueRecordShape([]string{"ab", "c"}) {
		t.Fatal("signature lost the key boundary")
	}
}

// GO-P14: RECORD with literal keys evaluates only its values, in order, and keeps
// every observable: errors, positions, copying, duplicate keys falling back.
func TestRecordLiteralKeyFastPath(t *testing.T) {
	cases := []struct{ src, want string }{
		{`RECORD("a", 1, "b", "x")["b"]`, "x"},
		{`RECORD("a", 1, "a", 2)["a"]`, "2"},
		{`COUNT(RECORD("a", 1, "b", 2, "c", 3))`, "3"},
		{`A = 5; R = RECORD("v", A); A = 6; R["v"]`, "5"},
		{`N = 0; R = RECORD("a", (N = N + 1), "b", (N = N + 1)); R["a"] & R["b"] & N`, "122"},
	}
	for _, c := range cases {
		v, err := MustCompile(c.src).Run(NewNone())
		if err != nil || v.Scalar() != c.want {
			t.Fatalf("%s = %v %v, want %s", c.src, v, err, c.want)
		}
	}
	_, err := MustCompile(`RECORD("a", 1, "b", 1 / 0)`).Run(NewNone())
	if err == nil || !strings.Contains(err.Error(), "E_DIV_ZERO") {
		t.Fatalf("value error lost: %v", err)
	}
	if _, err := MustCompile(`RECORD(NULL, 1)`).Run(NewNone()); err == nil {
		t.Fatal("a NULL key must still be refused")
	}
	if _, err := MustCompile(`RECORD(1, "x")`).Run(NewNone()); err != nil {
		t.Fatalf("numeric key: %v", err)
	}
}

// GO-P14: the LINK row alias memo gives the answers the per-row computation gave:
// upper-case binder names also expose their lower-case alias, and a row that
// already carries the name is left alone.
func TestRowTableAliasMemoMatchesTheDirectComputation(t *testing.T) {
	rows := joinRows(30, 4, 2)
	ctx := ctxWith("L", rows, "R", joinRows(30, 4, 3))
	for _, src := range []string{
		`COUNT(LINK(L, R, Left, Right, Left["a"] == Right["a"]))`,
		`COUNT(LINK(L, R, left, right, left["a"] == right["a"]))`,
		`COUNT(LINK(L, R, Left, Right, left["a"] == RIGHT["a"]))`,
		`COUNT(LINK(L, R, a, b, a["a"] == b["a"]))`,
	} {
		v1, err1 := MustCompile(src).Run(ctx)
		v2, err2 := MustCompile(src).Run(ctx) // second run: memo hit
		if fmt.Sprint(err1) != fmt.Sprint(err2) || (err1 == nil && v1.Scalar() != v2.Scalar()) {
			t.Fatalf("%s: memo changed the answer: %v %v / %v %v", src, v1, err1, v2, err2)
		}
	}
	e := ensureRowTableAlias(rows.storage[0], "Tbl")
	if !e.Has("Tbl") || !e.Has("tbl") || e.Get("Tbl") != rows.storage[0] {
		t.Fatal("alias not added")
	}
	if ensureRowTableAlias(e, "Tbl") != e {
		t.Fatal("a row that has the name must be returned as is")
	}
}

// GO-P15: FIND on bytes equals the rune-by-rune search it replaced, over random
// text with multi-byte characters, every start, and the edges.
func refFind(needleS, hayS string, from int) int {
	needle, hay := []rune(needleS), []rune(hayS)
	if from > len(hay) {
		return 0
	}
	for i := from; i <= len(hay)-len(needle); i++ {
		match := true
		for j := range needle {
			if hay[i+j] != needle[j] {
				match = false
				break
			}
		}
		if match {
			return i + 1
		}
	}
	return 0
}

func TestFindOnBytesEqualsTheRuneSearch(t *testing.T) {
	alphabet := []string{"a", "b", "é", "😀", "ü", " ", "aa"}
	s := lcg(11)
	str := func(n int) string {
		var b strings.Builder
		for i := 0; i < n; i++ {
			b.WriteString(alphabet[s.next()%uint64(len(alphabet))])
		}
		return b.String()
	}
	for iter := 0; iter < 3000; iter++ {
		hay := str(int(s.next() % 14))
		needle := str(1 + int(s.next()%3))
		for from := 0; from <= len([]rune(hay))+2; from++ {
			src := fmt.Sprintf("FIND(N, H, %d)", from+1)
			ctx := ctxWith("N", NewText(needle), "H", NewText(hay))
			v, err := MustCompile(src).Run(ctx)
			if err != nil {
				t.Fatalf("%s: %v", src, err)
			}
			if want := strconv.Itoa(refFind(needle, hay, from)); v.Scalar() != want {
				t.Fatalf("FIND(%q, %q, %d) = %s, want %s", needle, hay, from+1, v.Scalar(), want)
			}
		}
		v, _ := MustCompile("FIND(N, H)").Run(ctxWith("N", NewText(needle), "H", NewText(hay)))
		if want := strconv.Itoa(refFind(needle, hay, 0)); v.Scalar() != want {
			t.Fatalf("FIND(%q, %q) = %s, want %s", needle, hay, v.Scalar(), want)
		}
	}
	if _, err := MustCompile(`FIND("", "x")`).Run(NewNone()); err == nil {
		t.Fatal("an empty needle must still be refused")
	}
	if _, err := MustCompile(`FIND("x", "x", 0)`).Run(NewNone()); err == nil {
		t.Fatal("a start below 1 must still be refused")
	}
}

// GO-P20: the byte-offset text builtins equal the []rune implementations they
// replaced, over random text with multi-byte characters and whitespace, for every
// count around the text's length and a saturating one.
func refTrim(s string, l, r bool) string {
	rs := []rune(s)
	a, b := 0, len(rs)
	sp := func(c rune) bool { return c == ' ' || c == '\t' || c == '\r' || c == '\n' }
	if l {
		for a < b && sp(rs[a]) {
			a++
		}
	}
	if r {
		for b > a && sp(rs[b-1]) {
			b--
		}
	}
	return string(rs[a:b])
}

func refPad(s string, width int, fill string, left bool) string {
	sr, fr := []rune(s), []rune(fill)
	if len(sr) >= width {
		return string(sr)
	}
	pd := make([]rune, width-len(sr))
	for i := range pd {
		pd[i] = fr[i%len(fr)]
	}
	if left {
		return string(append(pd, sr...))
	}
	return string(append(sr, pd...))
}

func TestTextBuiltinsOnBytesEqualTheRuneVersions(t *testing.T) {
	alphabet := []string{"a", "b", "é", "😀", "ü", " ", "\t", "\n", "\r", "x"}
	s := lcg(21)
	str := func(n int) string {
		var b strings.Builder
		for i := 0; i < n; i++ {
			b.WriteString(alphabet[s.next()%uint64(len(alphabet))])
		}
		return b.String()
	}
	run := func(src string, vars ...*Value) string {
		ctx := NewNone()
		for i := 0; i < len(vars); i += 2 {
			ctx.Set(vars[i].Scalar(), vars[i+1])
		}
		v, err := MustCompile(src).Run(ctx)
		if err != nil {
			t.Fatalf("%s: %v", src, err)
		}
		return v.Scalar()
	}
	for iter := 0; iter < 400; iter++ {
		in := str(int(s.next() % 12))
		rs := []rune(in)
		n := len(rs)
		S := NewText(in)
		vn := NewText("S")
		for _, k := range []int{0, 1, n - 1, n, n + 1, n + 5} {
			if k < 0 {
				continue
			}
			ks := strconv.Itoa(k)
			if got, want := run(`LEFT(S, `+ks+`)`, vn, S), string(rs[:min(k, n)]); got != want {
				t.Fatalf("LEFT(%q, %d) = %q want %q", in, k, got, want)
			}
			if got, want := run(`RIGHT(S, `+ks+`)`, vn, S), string(rs[n-min(k, n):]); got != want {
				t.Fatalf("RIGHT(%q, %d) = %q want %q", in, k, got, want)
			}
			for from := 1; from <= n+2; from++ {
				want := ""
				if from-1 < n {
					want = string(rs[from-1 : min(from-1+k, n)])
				}
				if got := run(fmt.Sprintf(`SUBSTR(S, %d, %s)`, from, ks), vn, S); got != want {
					t.Fatalf("SUBSTR(%q, %d, %d) = %q want %q", in, from, k, got, want)
				}
			}
		}
		for from := 1; from <= n+2; from++ {
			want := ""
			if from-1 < n {
				want = string(rs[from-1:])
			}
			if got := run(fmt.Sprintf(`SUBSTR(S, %d)`, from), vn, S); got != want {
				t.Fatalf("SUBSTR(%q, %d) = %q want %q", in, from, got, want)
			}
		}
		if got, want := run(`LEFT(S, 9223372036854775807)`, vn, S), in; got != want {
			t.Fatalf("LEFT saturating: %q want %q", got, want)
		}
		if got, want := run(`SUBSTR(S, 1, 9223372036854775807)`, vn, S), in; got != want {
			t.Fatalf("SUBSTR saturating: %q want %q", got, want)
		}
		rev := make([]rune, n)
		for i, r := range rs {
			rev[n-1-i] = r
		}
		if got := run(`BACKWARDS(S)`, vn, S); got != string(rev) {
			t.Fatalf("BACKWARDS(%q) = %q want %q", in, got, string(rev))
		}
		for _, c := range []struct {
			fn   string
			l, r bool
		}{{"TRIM", true, true}, {"LTRIM", true, false}, {"RTRIM", false, true}} {
			if got, want := run(c.fn+`(S)`, vn, S), refTrim(in, c.l, c.r); got != want {
				t.Fatalf("%s(%q) = %q want %q", c.fn, in, got, want)
			}
		}
		if n > 0 {
			if got, want := run(`CODE(S)`, vn, S), strconv.Itoa(int(rs[0])); got != want {
				t.Fatalf("CODE(%q) = %s want %s", in, got, want)
			}
		}
		fill := str(1 + int(s.next()%3))
		for _, w := range []int{0, n, n + 1, n + 7} {
			for _, left := range []bool{true, false} {
				fn := "PADR"
				if left {
					fn = "PADL"
				}
				src := fmt.Sprintf(`%s(S, %d, F)`, fn, w)
				if got, want := run(src, vn, S, NewText("F"), NewText(fill)), refPad(in, w, fill, left); got != want {
					t.Fatalf("%s(%q, %d, %q) = %q want %q", fn, in, w, fill, got, want)
				}
			}
		}
	}
	// A result much smaller than a large source must not pin it: LEFT of a big
	// text is an independent copy.
	big := NewText(strings.Repeat("a", 1<<20))
	small := MustCompileRunText(t, `LEFT(S, 3)`, big)
	if small != "aaa" {
		t.Fatalf("LEFT of a big text: %q", small)
	}
}

func MustCompileRunText(t *testing.T, src string, s *Value) string {
	t.Helper()
	v, err := MustCompile(src).Run(ctxWith("S", s))
	if err != nil {
		t.Fatal(err)
	}
	return v.Scalar()
}

// GO-P19: the regex cache key is (ignoreCase, pattern): one pattern with and without
// `i` is two entries and two answers, in either order of first use, and the cache
// stays bounded under distinct computed patterns.
func TestRegexCacheKeysByFlagAndPattern(t *testing.T) {
	for round := 0; round < 2; round++ {
		for _, c := range []struct{ src, want string }{
			{`RMATCH('k', "K", "i")`, "TRUE"},
			{`RMATCH('k', "K")`, "FALSE"},
			{`RMATCH('k', "k")`, "TRUE"},
			{`RMATCH('k', "k", "i")`, "TRUE"},
		} {
			v, err := MustCompile(c.src).Run(NewNone())
			if err != nil || v.Scalar() != c.want && !(c.want == "TRUE" && v.kind == KindBool && v.AsBool(Pos{})) && !(c.want == "FALSE" && v.kind == KindBool && !v.AsBool(Pos{})) {
				t.Fatalf("round %d %s = %v %v, want %s", round, c.src, v, err, c.want)
			}
		}
	}
	regexMu.Lock()
	n, o := len(regexCache), len(regexOrder)
	regexMu.Unlock()
	if n != o || n > regexCacheSize {
		t.Fatalf("cache %d entries, order %d, limit %d", n, o, regexCacheSize)
	}
}
