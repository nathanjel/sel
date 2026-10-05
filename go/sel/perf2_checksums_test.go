package sel

import (
	"fmt"
	"testing"
)

// Semantic checksums for the round-2 workloads (GO-P11 … GO-P20), recorded at the
// baseline before any round-2 change. A speedup that changes an answer fails here.
var perf2Checksums = map[string]string{
	"p11 get":        "t\"1225396064\"",
	"p11 has":        "t\"1754\"",
	"p11 set":        "t\"856753,718673,615764,885686,753075,493632|1757|2\"",
	"p11 keys":       "t\"3,4,8,10,12,13,14,16,17,18,20,21\"",
	"p11 clone":      "t\"938618348|938618348\"",
	"p12 field":      "t\"20002016734\"",
	"p12 k":          "t\"1,2,3,4,5\"",
	"p12 record":     "t\"20,41,52,93\"",
	"p13 assign":     "t\"2|99\"",
	"p13 alias":      "t\"2|7\"",
	"p13 branch":     "t\"4|8\"",
	"p14 record":     "t\"2/773,4/405,5/411,9/909,0/824\"",
	"p14 link":       "t\"59726\"",
	"p14 link_first": "t\"0:16,0:62,0:63,0:73,0:101\"",
	"p14 link_dup":   "t\"2356\"",
	"p14 dupkeys":    "t\"2\"",
	"p14 select":     "t\"2,0;4,1;5,2\"",
	"p15 find":       "t\"3,0,6,6,200001,200001,8\"",
	"p15 find_edge":  "t\"302000\"",
	"p15 find_miss":  "t\"0,6001,6000\"",
	"p19 regex":      "t\"2571\"",
	"p19 regex_mix":  "t\"112+34,3é 5+6,0none,0\"",
	"p20 trim":       "t\"5,9,10,200000\"",
	"p20 trim_empty": "t\"000\"",
	"p20 code":       "t\"32,97,128512\"",
	"p20 slices":     "t\" aé|😀ü |aé😀ü|😀üaé😀ü|||\"",
	"p20 slices_big": "t\"200002,200002,200000\"",
	"p20 back":       "t\" ü😀éaü\\t  \"",
	"p20 pad":        "t\"😀😀😀😀é|éabab|abc|xyx\"",
}

func TestPerf2WorkloadChecksums(t *testing.T) {
	ctxK := func() *Value { return ctxWith("L", intList(3000, 7)) }
	ctxJ := func() *Value { return ctxWith("L", joinRows(2000, 10, 5), "R", joinRows(300, 10, 6)) }
	strs := func() *Value {
		return ctxWith("S", uniText(), "T", NewText("  \t\n ab  c \r\n "), "E", NewText(""))
	}
	cases := []struct {
		name, src string
		ctx       func() *Value
	}{
		{"p11 get", `K = FILTER(L, _ > 400000); SUM(INDEXES(K), K[_])`, ctxK},
		{"p11 has", `K = FILTER(L, _ > 400000); COUNT(FILTER(INDEXES(K), HAS(K, _)))`, ctxK},
		{"p11 set", `K = FILTER(L, _ > 400000); K[1] = 5; K[7] = 1; K["x"] = 2; JOIN(MAP(TAKE(K, 6), _), ",") & "|" & COUNT(K) & "|" & K["x"]`, ctxK},
		{"p11 keys", `K = FILTER(L, _ > 400000); JOIN(TAKE(INDEXES(K), 12), ",")`, ctxK},
		{"p11 clone", `K = FILTER(L, _ > 600000); J = K; J[2] = 0; SUM(K, _) & "|" & SUM(J, _)`, ctxK},
		{"p12 field", `COUNT(MAP(L, _["a"])) & SUM(L, _["a"] * 2 + _["c"])`, ctxJ},
		{"p12 k", `JOIN(TAKE(MAP(L, _K), 5), ",")`, ctxJ},
		{"p12 record", `JOIN(MAP(TAKE(MAP(L, RECORD("x", _["a"], "y", _["c"])), 4), _["x"] & _["y"]), ",")`, ctxJ},
		{"p13 assign", `X = MAP(L, RECORD("x", _["a"], "y", _["c"])); Y = X; Y[1]["x"] = 99; X[1]["x"] & "|" & Y[1]["x"]`, ctxJ},
		{"p13 alias", `X = L; X[1]["a"] = 7; L[1]["a"] & "|" & X[1]["a"]`, ctxJ},
		{"p13 branch", `X = IF(TRUE, L, L); X[2]["a"] = 8; L[2]["a"] & "|" & X[2]["a"]`, ctxJ},
		{"p14 record", `JOIN(MAP(TAKE(MAP(L, RECORD("a", _["a"], "b", _["k"])), 5), _["a"] & "/" & _["b"]), ",")`, ctxJ},
		{"p14 link", `COUNT(LINK(L, R, _1["a"] == _2["a"]))`, ctxJ},
		{"p14 link_first", `JOIN(MAP(TAKE(LINK(L, R, _1["a"] == _2["a"]), 5), _["L"]["c"] & ":" & _["R"]["c"]), ",")`, ctxJ},
		{"p14 link_dup", `COUNT(LINK(L, L, _1["a"] == _2["a"]))`, func() *Value { return ctxWith("L", joinRows(150, 10, 5)) }},
		{"p14 dupkeys", `RECORD("a", 1, "a", 2)["a"]`, ctxJ},
		{"p14 select", `JOIN(MAP(TAKE(SELECT_COLS(L, "a", "c"), 3), _["a"] & "," & _["c"]), ";")`, ctxJ},
		{"p15 find", `FIND("é😀", S) & "," & FIND("q", S) & "," & FIND("a", S, 5) & "," & FIND("aé", S, 3) & "," & FIND("ü ", S) & "," & FIND("ü", S, 200000) & "," & FIND("😀üaé", S, 6)`, strs},
		{"p15 find_edge", `FIND("c", "abc") & FIND("abcd", "abc") & FIND("aa", "aaa", 2) & FIND("aa", "aaa", 3) & FIND("aa", "aaa", 4) & FIND("é", "", 1)`, strs},
		{"p15 find_miss", `FIND(REPEAT("a", 3000) & "b", REPEAT("a", 6000)) & "," & FIND("b", REPEAT("a", 6000) & "b") & "," & FIND("ab", REPEAT("a", 6000) & "b", 5000)`, strs},
		{"p19 regex", `COUNT(FILTER(L, RMATCH('^[0-9]{3}-[0-9]{2}$', _)))`, func() *Value {
			items := make([]*Value, 3000)
			s := lcg(9)
			for i := range items {
				items[i] = NewText(fmt.Sprintf("%03d-%02d", s.next()%1000, s.next()%100))
				if i%7 == 0 {
					items[i] = NewText("x" + items[i].Scalar())
				}
			}
			return ctxWith("L", newListOwned(items))
		}},
		{"p19 regex_mix", `JOIN(MAP(L, RFIND('(\d+)-(\d+)', _) & RREPLACE('-', "+", _)), ",")`, func() *Value {
			return ctxWith("L", newListOwned([]*Value{NewText("12-34"), NewText("é 5-6"), NewText("none"), NewText("")}))
		}},
		{"p20 trim", `LEN(TRIM(T)) & "," & LEN(LTRIM(T)) & "," & LEN(RTRIM(T)) & "," & LEN(TRIM(S))`, strs},
		{"p20 trim_empty", `LEN(TRIM(E)) & LEN(TRIM(" ")) & LEN(TRIM("\t\r\n"))`, strs},
		{"p20 code", `CODE(S) & "," & CODE(TRIM(S)) & "," & CODE("😀")`, strs},
		{"p20 slices", `LEFT(S, 3) & "|" & RIGHT(S, 3) & "|" & SUBSTR(S, 2, 4) & "|" & SUBSTR(S, 100000, 6) & "|" & LEFT(S, 0) & "|" & RIGHT(S, 0) & "|" & SUBSTR(S, 400000)`, strs},
		{"p20 slices_big", `LEN(LEFT(S, 10000000)) & "," & LEN(RIGHT(S, 10000000)) & "," & LEN(SUBSTR(S, 3, 10000000))`, strs},
		{"p20 back", `LEFT(BACKWARDS(S), 6) & RIGHT(BACKWARDS(T), 3)`, strs},
		{"p20 pad", `PADL("é", 5, "😀") & "|" & PADR("é", 5, "ab") & "|" & PADL("abc", 2, "x") & "|" & PADR("", 3, "xy")`, strs},
	}
	for _, c := range cases {
		got := runOnce(c.src, c.ctx())
		want, ok := perf2Checksums[c.name]
		if !ok {
			t.Logf("CHECKSUM %q: %q", c.name, got)
			continue
		}
		if got != want {
			t.Errorf("%s: %s\n got  %.300s\n want %.300s", c.name, c.src, got, want)
		}
	}
}
