package sel

import (
	"fmt"
	"strings"
	"testing"
)

// Semantic checksums for the round-3 workloads (GO-P22 … GO-P27), recorded at the
// baseline before any round-3 change. A speedup that changes an answer fails here.
var perf3Checksums = map[string]string{
	"p23 replace":              "t\"axx😀bxx||XcXX|aébé\"",
	"p23 replace_empty_needle": "!E_BAD_ARG@1:9",
	"p23 replace_to_empty":     "t\"\"",
	"p23 replace_empty_hay":    "t\"\"",
	"p23 replace_overlap":      "t\"bba\"",
	"p22 nested":               "t\"1280,1040\"",
	"p22 key_outer":            "t\"17567301,1:1,2:3,3:5,4:7\"",
	"p22 key_deep":             "t\"40\"",
	"p22 no_key":               "t\"56,16\"",
	"p22 binder_named_k":       "!E_NO_KEY@1:15",
	"p23 split":                "t\"aBcé😀z|Q|é||😀#5\"",
	"p23 repeat":               "t\"é😀é😀é😀||2000\"",
	"p23 case":                 "t\"ABCé😀Z,Q,é,,😀|abcé😀z,q,é,,😀|STRAßE é|İi\"",
	"p23 b64":                  "t\"AAEC+vv8/f7/QUI=|000102fafbfcfdfeff4142|61|61|\"",
	"p23 b64_bad1":             "!E_BAD_ARG@1:15",
	"p23 b64_bad2":             "!E_BAD_ARG@1:15",
	"p23 b64_bad3":             "!E_BAD_ARG@1:15",
	"p23 btl":                  "t\"0,1,2,250,251,252,253,254,255,65,66||000102fafbfcfdfeff4142\"",
	"p23 bin_and":              "t\"000102fafbfcfdfeff4142\"",
	"p23 bin_xor":              "t\"0000000000000000000000\"",
	"p23 bin_cat":              "!E_NOT_TEXT@1:7",
	"p24 add_one":              "t\"42\"",
	"p24 medium":               "t\"5\"",
	"p24 three_op":             "t\"50\"",
	"p24 pipeline":             "t\"34\"",
	"p24 if_and":               "t\"8\"",
	"p24 err_div":              "!E_DIV_ZERO@1:8",
	"p24 err_undef":            "!E_UNDEF_VAR@1:5",
	"p24 err_coerce":           "!E_DIV_ZERO@1:10",
	"p24 assign":               "t\"8\"",
	"p24 nested":               "t\"55196329\"",
	"p25 poly":                 "t\"2016\"",
	"p25 poly2":                "t\"1,1,2,1,1,2,1,1,2,1,1,2,1,1,2,1,1\"",
	"p26 distinct":             "t\"35,35,5,2995\"",
	"p26 identity":             "t\"5\"",
	"p26 bucket":               "t\"4:608,2:569,0:636,1:583,3:604\"",
	"p27 chain":                "t\"151\"",
	"p27 chain_deep":           "!E_DEPTH@1:5600",
	"p27 literals":             "t\"1126.0\"",
	"p27 literals_deep":        "!E_DEPTH@1:2096",
	"p27 ops":                  "b3578543b544637385454542d33",
	"p27 assign_ops":           "t\"1z\"",
	"p27 cmp_chain_err":        "!E_SYNTAX@1:7",
}

func eachOutcome(src string, ctx *Value) string {
	return outcome(func() *Value { return MustEval(src, ctx) })
}

func TestKeysAndParsingWorkloadChecksums(t *testing.T) {
	ctxO := func() *Value {
		return ctxWith("O", intList(40, 4), "LN", newListOwned([]*Value{
			rec("q", 1, "p", 2), rec("q", 3, "p", 4), rec("q", 5, "p", 6), rec("q", 7, "p", 8)}))
	}
	dups := func() *Value {
		items := make([]*Value, 3000)
		sd := lcg(77)
		for i := range items {
			items[i] = rec("a", int(sd.next()%7), "k", int(sd.next()%5))
		}
		return ctxWith("L", newListOwned(items), "NUMS", intList(3000, 3))
	}
	txt := func() *Value {
		return ctxWith("T", NewText("aBcé😀z,Q,é,,😀"), "B", NewBin([]byte{0, 1, 2, 250, 251, 252, 253, 254, 255, 65, 66}))
	}
	scalars := func() *Value {
		return ctxWith("X", NewInt(41), "A", NewInt(3), "B", NewInt(10), "C", NewInt(4), "L", intList(50, 3))
	}
	cases := []struct {
		name, src string
		ctx       func() *Value
		oneShot   bool
	}{
		{"p22 nested", `SUM(O, o, SUM(LN, l, l["q"] * 2)) & "," & SUM(O, o, SUM(LN, l, l["q"] + _K))`, ctxO, false},
		{"p22 key_outer", `SUM(O, o, o + _K) & "," & JOIN(MAP(LN, _K & ":" & _["q"]), ",")`, ctxO, false},
		{"p22 key_deep", `COUNT(FILTER(O, o, SUM(LN, l, IF(_K > 1, l["q"], 0)) > 0))`, ctxO, false},
		{"p22 no_key", `SUM(LN, l, l["p"] * 3 - 1) & "," & SUM(LN, _["q"])`, ctxO, false},
		{"p22 binder_named_k", `SUM(LN, _K, _K["q"])`, ctxO, false},
		{"p23 replace", `REPLACE("é", "xx", "aé😀bé") & "|" & REPLACE("a", "", "aaa") & "|" & REPLACE("ab", "X", "abcabab") & "|" & REPLACE("😀", "é", "a😀b😀")`, txt, false},
		{"p23 replace_empty_needle", `REPLACE("", "x", "abc")`, txt, false},
		{"p23 replace_to_empty", `REPLACE("a", "", "aaa")`, txt, false},
		{"p23 replace_empty_hay", `REPLACE("a", "b", "")`, txt, false},
		{"p23 replace_overlap", `REPLACE("aa", "b", "aaaaa")`, txt, false},
		{"p23 split", `JOIN(SPLIT(T, ","), "|") & "#" & COUNT(SPLIT("a,b,,c,", ","))`, txt, false},
		{"p23 repeat", `REPEAT("é😀", 3) & "|" & REPEAT("ab", 0) & "|" & LEN(REPEAT("ab", 1000))`, txt, false},
		{"p23 case", `UPPER(T) & "|" & LOWER(T) & "|" & UPPER("straße é") & "|" & LOWER("İI")`, txt, false},
		{"p23 b64", `ENCODE_BASE64(B) & "|" & TO_HEX(DECODE_BASE64(ENCODE_BASE64(B))) & "|" & TO_HEX(DECODE_BASE64("YQ==")) & "|" & TO_HEX(DECODE_BASE64("YR==")) & "|" & TO_HEX(DECODE_BASE64(""))`, txt, false},
		{"p23 b64_bad1", `DECODE_BASE64("Y!==")`, txt, false},
		{"p23 b64_bad2", `DECODE_BASE64("YQ=")`, txt, false},
		{"p23 b64_bad3", `DECODE_BASE64("Y")`, txt, false},
		{"p23 btl", `JOIN(BTL(B), ",") & "|" & JOIN(BTL(FROM_HEX("")), ",") & "|" & TO_HEX(LTB(BTL(B)))`, txt, false},
		{"p23 bin_and", `TO_HEX(B BAND B)`, txt, false},
		{"p23 bin_xor", `TO_HEX(B BXOR B)`, txt, false},
		{"p23 bin_cat", `LEN(B & B)`, txt, false},
		{"p24 add_one", `X + 1`, scalars, true},
		{"p24 medium", `A * 2 + B % 7 - C`, scalars, true},
		{"p24 three_op", `(A + B) * (C - 1) + (A * C) - (B % 3)`, scalars, true},
		{"p24 pipeline", `L .> FILTER(_ > 400000) .> MAP(_ * 2) .> COUNT()`, scalars, true},
		{"p24 if_and", `IF(A > 2 AND B < 20, C * 2, C)`, scalars, true},
		{"p24 err_div", `A + (B / (C - 4))`, scalars, true},
		{"p24 err_undef", `A + NOPE`, scalars, true},
		{"p24 err_coerce", `"x" + (B / 0)`, scalars, true},
		{"p24 assign", `Z = A + 1; Z = Z * 2; Z + 0`, scalars, true},
		{"p24 nested", `SUM(L, _ * 2) + X`, scalars, true},
		{"p25 poly", `SUM(L, _["a"])`, func() *Value { return ctxWith("L", polymorphicRows(64, 4)) }, false},
		{"p25 poly2", `JOIN(MAP(L, _["b"] ?? "-"), ",")`, func() *Value { return ctxWith("L", polymorphicRows(17, 3)) }, false},
		{"p26 distinct", `COUNT(DISTINCT(L)) & "," & COUNT(DEDUPE(L)) & "," & COUNT(BUCKET(L, _["k"])) & "," & COUNT(DISTINCT(NUMS))`, dups, false},
		{"p26 identity", `COUNT(DISTINCT((1, 1.0, "1", 1.00, TRUE, NULL, LIST(1), LIST(1.0), RECORD("a", 1), RECORD("a", 1.0))))`, dups, false},
		{"p26 bucket", `JOIN(MAP(BUCKET(L, _["k"]), _K & ":" & COUNT(_)), ",")`, dups, false},
		{"p27 chain", `1` + strings.Repeat("+1", 150), nil, false},
		{"p27 chain_deep", `1` + strings.Repeat("+1", 3000), nil, false},
		{"p27 literals", `1` + strings.Repeat(" + 12.5", 90), nil, false},
		{"p27 literals_deep", `1` + strings.Repeat(" + 12.5", 500), nil, false},
		{"p27 ops", `(1 + 2 * 3 - 4 / 2 % 3) & "x" & IF(TRUE AND FALSE OR TRUE XOR FALSE, "T", "F") & (5 BAND 3 BOR 8 BXOR 2) & IF(1 < 2, "T", "F") & IF(2 $< 10, "T", "F") & (NULL ?? 7) & ("" ??? 8) & IF(1 IN (1, 2), "T", "F") & IF((1, 2) EQL (1, 2), "T", "F") & IF(NOT FALSE, "T", "F") & (-3)`, nil, false},
		{"p27 assign_ops", `A = 5; A += 2; A -= 1; A *= 3; A /= 2; A %= 4; A &= "z"; A`, nil, false},
		{"p27 cmp_chain_err", `1 < 2 < 3`, nil, false},
	}
	for _, c := range cases {
		var ctx *Value
		if c.ctx != nil {
			ctx = c.ctx()
		} else {
			ctx = NewNone()
		}
		var got string
		if c.oneShot {
			got = eachOutcome(c.src, ctx)
		} else {
			got = runOnce(c.src, ctx)
		}
		want, ok := perf3Checksums[c.name]
		if !ok {
			t.Logf("CHECKSUM %q: %q", c.name, got)
			continue
		}
		if got != want {
			t.Errorf("%s: %.120s\n got  %.300s\n want %.300s", c.name, c.src, got, want)
		}
	}
	_ = fmt.Sprint
}

// TestStructuralHashIsRepresentationIndependent: the hash reads children in place for
// each representation (shaped record, dense list, keyed list, entry list); equal
// values must hash equal whichever way they were built, and unequal shapes of the
// same keys must not be confused.
func TestStructuralHashIsRepresentationIndependent(t *testing.T) {
	shaped := rec("a", 1, "b", 2)
	built := NewNone()
	built.Set("a", NewInt(1))
	built.Set("b", NewInt(2))
	if shaped.StructuralHash() != built.StructuralHash() {
		t.Error("a shaped record and the same record built key by key hash differently")
	}
	dense := newListOwned([]*Value{NewInt(7), NewInt(8), NewInt(9)})
	keyed := NewListWithKeys([]*Value{NewInt(7), NewInt(8), NewInt(9)}, []string{"1", "2", "3"})
	if dense.StructuralHash() != keyed.StructuralHash() {
		t.Error("a dense list and the same list with explicit keys 1..3 hash differently")
	}
	entries := NewNone()
	entries.Set("1", NewInt(7))
	entries.Set("2", NewInt(8))
	entries.Set("3", NewInt(9))
	if dense.StructuralHash() != entries.StructuralHash() {
		t.Error("a dense list and the same children set as entries hash differently")
	}
	big := make([]*Value, 130)
	for i := range big {
		big[i] = NewInt(int64(i))
	}
	bigKeys := make([]string, len(big))
	for i := range bigKeys {
		bigKeys[i] = fmt.Sprint(i + 1)
	}
	if newListOwned(big).StructuralHash() != NewListWithKeys(big, bigKeys).StructuralHash() {
		t.Error("a dense list past 99 elements hashes differently from its explicit keys")
	}
	if rec("a", 1, "b", 2).StructuralHash() == rec("b", 1, "a", 2).StructuralHash() {
		t.Error("the same values under swapped keys must not collide trivially")
	}
}

// GO-P23: BTL shares one immutable decimal per byte value and one slab of Values;
// elements must still behave as independent values, and base64 keeps its strictness.
func TestBtlElementsStayIndependent(t *testing.T) {
	for src, want := range map[string]string{
		`L = BTL(TO_UTF8("ab")); L[1] = 99; JOIN(L, ",")`:                            "t\"99,98\"",
		`L = BTL(TO_UTF8("ab")); M = L; M[1] = 7; JOIN(L, ",") & "|" & JOIN(M, ",")`: "t\"97,98|7,98\"",
		`SUM(BTL(TO_UTF8("abc")), _)`:                                                "t\"294\"",
	} {
		if got := runOnce(src, NewNone()); got != want {
			t.Errorf("%s\n got  %s\n want %s", src, got, want)
		}
	}
}

func TestBase64DecodeTableIsStrict(t *testing.T) {
	for src, want := range map[string]string{
		`TO_HEX(DECODE_BASE64("AAEC+vv8/f7/QUI="))`: "t\"000102fafbfcfdfeff4142\"",
		`DECODE_BASE64("AA-A")`:                     "!E_BAD_ARG@1:15",
		`DECODE_BASE64("AAéA")`:                     "!E_BAD_ARG@1:15",
		`DECODE_BASE64("AA=A")`:                     "!E_BAD_ARG@1:15",
		`DECODE_BASE64("A===")`:                     "!E_BAD_ARG@1:15",
		`DECODE_BASE64("AAA")`:                      "!E_BAD_ARG@1:15",
	} {
		if got := runOnce(src, NewNone()); got != want {
			t.Errorf("%s\n got  %s\n want %s", src, got, want)
		}
	}
}

// GO-P24: a one-shot Eval that skips the planner gives the answer (or the error code
// and position) of the planned run, for programs on both sides of plansPay.
func TestEvalMatchesThePlannedRun(t *testing.T) {
	ctx := func() *Value { return ctxWith("A", NewInt(7), "B", NewInt(3), "L", intList(20, 3)) }
	for _, src := range []string{
		`A + 1`, `A * 2 + B % 7 - 3`, `IF(A > B, A & "x", B)`, `A > 1 AND B < 9`, `1 / (A - 7)`, `A + "x"`,
		`X = A * 2; X + B`, `A ?? B`, `A / 0 ??? 5`, `COUNT(L)`, `SUM(L, _ * 2)`, `L .> FILTER(_ > 3) .> MAP(_ * 2)`,
		`(1 + 2) * (3 + 4) - 5 / 2`, `A[1]`, `NOSUCH + 1`,
	} {
		got := outcome(func() *Value { return MustEval(src, ctx()) })
		want := outcome(func() *Value {
			prog, err := Compile(src)
			if err != nil {
				panic(err)
			}
			v, err := prog.Run(ctx())
			if err != nil {
				panic(err)
			}
			return v
		})
		if got != want {
			t.Errorf("%s\n Eval:        %.160s\n Compile+Run: %.160s", src, got, want)
		}
	}
}

// GO-P25: a site that meets more shapes than the miss limit stops storing, and every
// row still resolves to its own value.
func TestSlotCacheStopsStoringWhenPolymorphic(t *testing.T) {
	shapes := [][]string{{"a", "x"}, {"x", "a"}, {"a"}, {"y", "a"}, {"a", "y", "x"}, {"z", "a"}, {"q", "a"}, {"a", "q"}, {"r", "a"}, {"a", "r"}, {"s", "a"}, {"a", "s"}}
	rows := make([]*Value, 0, 600)
	want := int64(0)
	for i := 0; i < 600; i++ {
		keys := shapes[i%len(shapes)]
		kv := make([]interface{}, 0, 2*len(keys))
		for _, k := range keys {
			kv = append(kv, k, i)
		}
		rows = append(rows, rec(kv...))
		want += int64(i)
	}
	prog := MustCompile(`SUM(L, _["a"])`)
	for round := 0; round < 3; round++ {
		v, err := prog.Run(ctxWith("L", newListOwned(rows)))
		if err != nil {
			t.Fatal(err)
		}
		if got := v.Scalar(); got != fmt.Sprint(want) {
			t.Fatalf("round %d: SUM = %s, want %d", round, got, want)
		}
	}
	var find func(n *Node) *Node
	find = func(n *Node) *Node {
		if n == nil {
			return nil
		}
		if n.slotCache != nil {
			return n
		}
		for _, c := range []*Node{n.L, n.R} {
			if f := find(c); f != nil {
				return f
			}
		}
		for _, c := range n.Items {
			if f := find(c); f != nil {
				return f
			}
		}
		return nil
	}
	site := find(prog.PhysicalAST())
	if site == nil {
		t.Skip("no slot-cache site found in the physical tree")
	}
	if c := site.slotCache.Load(); c == nil || c.Misses > slotCacheMissLimit {
		t.Errorf("site cache = %+v, want at most %d recorded misses", c, slotCacheMissLimit)
	}
}
