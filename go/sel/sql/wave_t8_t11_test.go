package sql

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/nathanjel/sel/go/sel"
)

// Unit tests for the T08-T11 wave (SQL scope, kinds, rendering, hybrid parity).
// The .sqlt corpus pins the bytes; these pin the mechanisms behind them, in the
// Go host's own API, so a regression names the rule and not just a golden.

func colNum(name string) *Binding {
	return ColumnBinding(name, "t", KindNum, false, false, false, "", "", false)
}

func colUnknown(name string) *Binding {
	return ColumnBinding(name, "t", KindUnknown, false, false, false, "", "", false)
}

func expr(t *testing.T, dialect, src string, b map[string]*Binding) string {
	t.Helper()
	f, err := Translate(sel.MustCompile(src), dialect, NewBindings(b), Options{})
	if err != nil {
		t.Fatalf("%s: %v", src, err)
	}
	return f.AsValue(ModeInline)
}

func stmt(t *testing.T, dialect, src string, b map[string]*Binding) string {
	t.Helper()
	f, err := TranslateStatement(sel.MustCompile(src), dialect, NewBindings(b), Options{})
	if err != nil {
		t.Fatalf("%s: %v", src, err)
	}
	return f.AsStatement(ModeInline)
}

func refusal(t *testing.T, dialect, src string, b map[string]*Binding, statement bool) string {
	t.Helper()
	var err error
	if statement {
		_, err = TranslateStatement(sel.MustCompile(src), dialect, NewBindings(b), Options{})
	} else {
		_, err = Translate(sel.MustCompile(src), dialect, NewBindings(b), Options{})
	}
	if err == nil {
		t.Fatalf("%s: translated, want a refusal", src)
	}
	return err.Error()
}

func wantCode(t *testing.T, got, code string) {
	t.Helper()
	if !strings.Contains(got, code) {
		t.Fatalf("want %s, got %q", code, got)
	}
}

// --- T08: lexical scope -------------------------------------------------------

func TestInnerBinderDoesNotCaptureAHelpersFreeNames(t *testing.T) {
	b := map[string]*Binding{"Y": colNum("y")}
	captured := expr(t, "mariadb", "X = Y + 1; ALL((1,2,3), Y, Y > X)", b)
	renamed := expr(t, "mariadb", "X = Y + 1; ALL((1,2,3), Z, Z > X)", b)
	if captured != renamed {
		t.Fatalf("a binder named like a helper's free name captured it:\n%s\n%s", captured, renamed)
	}
}

func TestStaticListElementsAreReadWhereTheListIsWritten(t *testing.T) {
	got := expr(t, "mariadb", `ANY((0,0), ALL((_K, 5), I, I > 1))`, nil)
	want := expr(t, "mariadb", `ALL(("1", 5), I, I > 1) OR ALL(("2", 5), I, I > 1)`, nil)
	if got != want {
		t.Fatalf("the inner list's _K was the inner key:\n%s\n%s", got, want)
	}
	// The same name on the two sides of the comma: the list's X is the column.
	b := map[string]*Binding{"X": colNum("x")}
	if a, c := expr(t, "mariadb", "ANY((X, 2), X, X > 1)", b), expr(t, "mariadb", "ANY((X, 2), P, P > 1)", b); a != c {
		t.Fatalf("an element named like the binder was captured:\n%s\n%s", a, c)
	}
}

func TestNestedDefaultBindersAreDistinct(t *testing.T) {
	b := map[string]*Binding{"A": colNum("a")}
	got := expr(t, "mariadb", "ANY((A, 2), ALL((_, 5), _ > 0))", b)
	want := expr(t, "mariadb", "ANY((A, 2), P, ALL((P, 5), Q, Q > 0))", b)
	if got != want {
		t.Fatalf("nested default binders collided:\n%s\n%s", got, want)
	}
}

func TestAFilterBinderIsLocalToItsPredicate(t *testing.T) {
	cols := map[string]*Binding{"V": ColumnsBinding([]*Binding{colNum("a"), colNum("b")})}
	wantCode(t, refusal(t, "mariadb", "ANY(FILTER(V, a, a > 1), q, a < 9)", cols, false), "E_SQL_UNBOUND")
	wantCode(t, refusal(t, "mariadb", "ALL(FILTER((1,2,3), x, y > 0), y, y < 9)", nil, false), "E_SQL_UNBOUND")
	wantCode(t, refusal(t, "mariadb", "ANY(FILTER(FILTER(V, a, b > 1), b, a < 9), q, q > 0)", cols, false), "E_SQL_UNBOUND")
	// The control: each name where it is bound.
	got := expr(t, "mariadb", "ANY(FILTER(V, w, w > 1), q, q > 0)", cols)
	if !strings.Contains(got, "> 1") || !strings.Contains(got, "> 0") {
		t.Fatalf("control did not translate: %s", got)
	}
}

func TestABinderShadowsAValueBindingForConstantFolding(t *testing.T) {
	b := map[string]*Binding{
		"V": ValueBinding(sel.NewText("abc"), nil),
		"A": colUnknown("a"),
	}
	// V is the element here, not the value binding "abc": the program is fine.
	got := expr(t, "mariadb", "ALL((A, 2), V, V + 1 > 0)", b)
	if !strings.Contains(got, "REGEXP") {
		t.Fatalf("the column element was not guarded: %s", got)
	}
}

func TestReadingAHelperSnapshotsItsList(t *testing.T) {
	b := map[string]*Binding{"A": colNum("a"), "B": colNum("b")}
	got := expr(t, "mariadb", "R[1] = A; X = R; R[2] = B; SUM(X, _ + 1)", b)
	if strings.Contains(got, "`b`") {
		t.Fatalf("a write to R after X = R reached X: %s", got)
	}
	if n := expr(t, "mariadb", "R[1] = 5; X = R; R[2] = 6; COUNT(X)", nil); n != "1" {
		t.Fatalf("COUNT(X) = %s, want 1", n)
	}
}

func TestANonNameBinderIsRefusedAtTheBinder(t *testing.T) {
	col := map[string]*Binding{"A": colNum("a")}
	wantCode(t, refusal(t, "mariadb", `BUCKET("x", A[1], "y", 1)`, col, false), "E_SQL_SHAPE")
	items := map[string]*Binding{"ITEMS": RelationBinding("items", "i", map[string]*Binding{
		"DEPT": ColumnBinding("dept", "i", KindText, false, false, false, "", "", false),
		"QTY":  colNum("qty"),
	}, "", "", "", false)}
	wantCode(t, refusal(t, "mariadb", `ITEMS .> MAP(BUCKET("x", _["DEPT"], "x", _["QTY"]))`, items, true), "E_SQL_SHAPE")
}

func TestABucketSumBodyKeepsItsLiterals(t *testing.T) {
	items := map[string]*Binding{"ITEMS": RelationBinding("items", "i", map[string]*Binding{
		"DEPT": ColumnBinding("dept", "i", KindText, false, false, false, "", "", false),
		"QTY":  ColumnBinding("qty", "i", KindNum, false, false, false, "", "", false),
	}, "", "", "", false)}
	got := stmt(t, "mariadb", `ITEMS .> BUCKET(_["DEPT"], RECORD("s", SUM(_, _["QTY"] * 2 + 5)))`, items)
	if !strings.Contains(got, "* 2) + 5)") {
		t.Fatalf("the literals of a BUCKET SUM body were lost: %s", got)
	}
}

func TestSizeBudgetRefusesAHugeExpansionQuicklyAndAcceptsBelowIt(t *testing.T) {
	doubling := func(k int, base string) string {
		var b strings.Builder
		fmt.Fprintf(&b, "X0 = %s; ", base)
		for i := 1; i <= k; i++ {
			fmt.Fprintf(&b, "X%d = X%d + X%d; ", i, i-1, i-1)
		}
		fmt.Fprintf(&b, "X%d > 0", k)
		return b.String()
	}
	col := map[string]*Binding{"N": colNum("n")}
	for _, base := range []string{"N", "1"} {
		start := time.Now()
		wantCode(t, refusal(t, "mariadb", doubling(40, base), col, false), "E_SQL_SIZE")
		if d := time.Since(start); d > 5*time.Second {
			t.Fatalf("refusing a 2^40 expansion over %q took %v; the walk must stop at the budget", base, d)
		}
	}
	if got := expr(t, "mariadb", doubling(4, "N"), col); !strings.Contains(got, "`t`.`n`") {
		t.Fatalf("a small doubling chain did not translate: %s", got)
	}
}

func TestAHelperChainPastTheDepthLimitIsSqlDepthNotSqlInvalid(t *testing.T) {
	var b strings.Builder
	b.WriteString("X0 = 1; ")
	for i := 1; i <= 205; i++ {
		fmt.Fprintf(&b, "X%d = X%d + 1; ", i, i-1)
	}
	b.WriteString("X205 > 0")
	wantCode(t, refusal(t, "mariadb", b.String(), nil, false), "E_SQL_DEPTH")
}

func TestReadingTheBindingBeforeTheHelperThatRebindsIt(t *testing.T) {
	orders := map[string]*Binding{"ORDERS": RelationBinding("orders", "o", map[string]*Binding{"ID": colNum("id")}, "", "", "", false)}
	p := sel.MustCompile("ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)")
	plan := PlanHybrid(p, "mariadb", NewBindings(orders), Options{})
	if !plan.PureSql {
		t.Fatalf("want a pure_sql plan, got %+v", plan)
	}
	if got := plan.SqlStatement.AsStatement(ModeInline); !strings.HasSuffix(got, "LIMIT 3 OFFSET 2") {
		t.Fatalf("the DROP was applied twice: %s", got)
	}
}

// --- T09: kinds ---------------------------------------------------------------

func TestUnknownDoesNotUnifyWithAnyKind(t *testing.T) {
	b := map[string]*Binding{"F": ColumnBinding("f", "t", KindBool, false, false, false, "", "", false), "U": colUnknown("u")}
	got := expr(t, "mariadb", "IF(F, 1, U) > 0", b)
	if !strings.Contains(got, "REGEXP") || strings.Contains(got, "CASE WHEN `t`.`f` THEN CAST") {
		t.Fatalf("the conditional was not guarded as a whole: %s", got)
	}
	wantCode(t, refusal(t, "sqlite", "IF(F, 1, U) > 0", b, false), "E_SQL_UNSUPPORTED")
}

func TestSumOfAnUnknownBodyIsAllOrNothing(t *testing.T) {
	oi := map[string]*Binding{"OI": RelationBinding("oi", "oi", map[string]*Binding{"QTY": colUnknown("qty")}, "", "", "", false)}
	got := expr(t, "mariadb", `SUM(OI, _["QTY"])`, oi)
	for _, want := range []string{"CASE WHEN COUNT(*) = COUNT(CASE WHEN (", "THEN 1 END) THEN COALESCE(SUM(CAST(", "ELSE NULL END"} {
		if !strings.Contains(got, want) {
			t.Fatalf("missing %q in %s", want, got)
		}
	}
	pg := expr(t, "postgresql", `SUM(OI, _["QTY"])`, oi)
	if !strings.Contains(pg, "SUM(CASE WHEN (") {
		t.Fatalf("PostgreSQL must sum the guarded cast: %s", pg)
	}
	wantCode(t, refusal(t, "sqlite", `SUM(OI, _["QTY"])`, oi, false), "E_SQL_UNSUPPORTED")
	// A declared NUM is the caller's promise: no guard.
	num := map[string]*Binding{"OI": RelationBinding("oi", "oi", map[string]*Binding{"QTY": colNum("qty")}, "", "", "", false)}
	if plain := expr(t, "mariadb", `SUM(OI, _["QTY"])`, num); strings.Contains(plain, "COUNT(*)") {
		t.Fatalf("a declared NUM body was guarded: %s", plain)
	}
}

func TestJoinTakesText(t *testing.T) {
	b := map[string]*Binding{
		"T": ColumnBinding("t", "", KindText, false, false, false, "", "", false),
		"F": ColumnBinding("f", "", KindBool, false, false, false, "", "", false),
		"X": ColumnBinding("x", "", KindBin, false, false, false, "", "", false),
	}
	for _, src := range []string{`JOIN((T, F), ",")`, `JOIN((F, "a"), "-")`, `JOIN((T, T), F)`, `JOIN((X, T), ",")`} {
		wantCode(t, refusal(t, "mariadb", src, b, false), "E_SQL_SHAPE")
	}
	wantCode(t, refusal(t, "mariadb", `JOIN(FILTER(("a","b"), _ $== "a"), ",")`, b, false), "E_SQL_SHAPE")
}

func TestInOverARelationChecksTheNeedleKind(t *testing.T) {
	b := map[string]*Binding{
		"S": RelationBinding("sk", "s", map[string]*Binding{"SKU": ColumnBinding("sku", "s", KindText, false, false, false, "", "", false)}, "SKU", "", "", false),
		"F": ColumnBinding("f", "", KindBool, false, false, false, "", "", false),
	}
	wantCode(t, refusal(t, "mariadb", "F IN S", b, false), "E_SQL_SHAPE")
	wantCode(t, refusal(t, "mariadb", "TRUE IN S", b, false), "E_SQL_SHAPE")
}

func TestAnExactColumnComparesANumberAsText(t *testing.T) {
	b := map[string]*Binding{"T": ColumnBinding("t", "", KindText, true, false, false, "", "", false)}
	got := expr(t, "mariadb", `T IN ("a", 3)`, b)
	if !strings.Contains(got, "CAST(3 AS CHAR)") {
		t.Fatalf("the numeric item was compared bare: %s", got)
	}
}

func TestStatementCountsAreClampedAndMergedExactly(t *testing.T) {
	items := map[string]*Binding{"ITEMS": RelationBinding("items", "", nil, "", "", "", false)}
	cases := map[string]string{
		`ITEMS .> TAKE(2.0)`:                                        "LIMIT 2",
		`ITEMS .> TAKE(0.0)`:                                        "LIMIT 0",
		`ITEMS .> TAKE(99999999999999999999999)`:                    "LIMIT 9223372036854775807",
		`ITEMS .> TAKE(5) .> TAKE(9223372036854775808)`:             "LIMIT 5",
		`ITEMS .> DROP(9223372036854775806) .> DROP(1)`:             "OFFSET 9223372036854775807",
		`ITEMS .> DROP(9223372036854775807) .> DROP(1)`:             "OFFSET 9223372036854775807",
		`ITEMS .> DROP(4503599627370496) .> DROP(4503599627370496)`: "OFFSET 9007199254740992",
	}
	for src, want := range cases {
		if got := stmt(t, "postgresql", src, items); !strings.Contains(got, want) {
			t.Errorf("%s: %s, want %s", src, got, want)
		}
	}
	wantCode(t, refusal(t, "postgresql", `ITEMS .> TAKE(1.5)`, items, true), "E_NOT_INT")
	wantCode(t, refusal(t, "postgresql", `ITEMS .> TAKE(0 - 1)`, items, true), "E_RANGE")
}

func TestLongUnrollsFoldBalancedAboveTwoFiftySix(t *testing.T) {
	list := func(n int) string {
		parts := make([]string, n)
		for i := range parts {
			parts[i] = "1"
		}
		return "(" + strings.Join(parts, ",") + ")"
	}
	depth := func(s string) int {
		d, max := 0, 0
		for _, c := range s {
			switch c {
			case '(':
				d++
				if d > max {
					max = d
				}
			case ')':
				d--
			}
		}
		return max
	}
	left := expr(t, "mariadb", "ANY("+list(256)+", _ > 0)", nil)
	if depth(left) < 256 {
		t.Fatalf("256 operands must stay a left fold, depth %d", depth(left))
	}
	bal := expr(t, "mariadb", "ANY("+list(600)+", _ > 0)", nil)
	if depth(bal) > 160 { // 300 + 300, each half 150 + 150: a left fold of 150

		t.Fatalf("600 operands must fold balanced, depth %d", depth(bal))
	}
}

// --- T10: rendering, registration, names --------------------------------------

func TestSuppliedCorrelateIsParenthesised(t *testing.T) {
	b := map[string]*Binding{
		"OI": RelationBinding("oi", "oi", map[string]*Binding{"QTY": colNum("qty")}, "", "a = o.id OR b = o.id", "", false),
	}
	got := expr(t, "mariadb", `ANY(OI, _["QTY"] > 1)`, b)
	if !strings.Contains(got, "WHERE (a = o.id OR b = o.id) AND") {
		t.Fatalf("the correlate was not parenthesised: %s", got)
	}
	plain := map[string]*Binding{"OI": RelationBinding("oi", "oi", map[string]*Binding{"QTY": colNum("qty")}, "", "", "", false)}
	if got := expr(t, "mariadb", `ANY(OI, _["QTY"] > 1)`, plain); !strings.Contains(got, "WHERE TRUE AND") {
		t.Fatalf("the default correlate must stay bare: %s", got)
	}
}

func TestDialectRegistrationRefusesUnsafeQuoting(t *testing.T) {
	Reset()
	defer Reset()
	bad := map[string]map[string]interface{}{
		"t-noquote":  {"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{"textEscape": map[string]interface{}{"\\": "\\\\"}}},
		"t-empty":    {"extends": "sqlite", "version": "3.48", "lexical": map[string]interface{}{"textEscape": map[string]interface{}{}}},
		"t-noescbs":  {"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{"textEscape": map[string]interface{}{"'": "\\'"}}},
		"t-samequot": {"extends": "ansi", "version": "1", "lexical": map[string]interface{}{"textQuote": "\""}},
	}
	for name, spec := range bad {
		if !refuses(func() { DefineDialect(name, spec) }) {
			t.Errorf("%s: the quoting was accepted", name)
		}
		if Exists(name) {
			t.Errorf("%s: a refused registration left the dialect behind", name)
		}
	}
	ok := map[string]interface{}{"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{"textEscape": map[string]interface{}{"\\": "\\\\", "'": "''"}}}
	DefineDialect("t-ok", ok)
	DefineDialect("t-ok", ok) // the same parent replaces
	if !refuses(func() {
		DefineDialect("t-ok", map[string]interface{}{"extends": "postgresql", "version": "16"})
	}) {
		t.Error("a different parent was accepted for a registered name")
	}
}

func TestBindingNamesAreComparedUpperCased(t *testing.T) {
	if !refuses(func() { NewBindings(map[string]*Binding{"x": colNum("a"), "X": colNum("b")}) }) {
		t.Error("two bindings named x and X were accepted")
	}
	if !refuses(func() {
		RelationBinding("t", "t", map[string]*Binding{"A": colNum("x"), "a": colNum("y")}, "", "", "", false)
	}) {
		t.Error("two fields differing only by case were accepted")
	}
	if !refuses(func() { ColumnBinding("c", "", KindList, false, false, false, "", "", false) }) {
		t.Error("LIST was accepted as a column type")
	}
}

func TestColumnBindingsFoldCollationAndPrefilter(t *testing.T) {
	b := ColumnBinding("c", "t", KindText, false, false, false, "binary", "", false)
	if !b.Column.Exact {
		t.Error(`collation="binary" did not make the column exact`)
	}
	b = ColumnBinding("c", "t", KindText, false, false, false, "sargable", "", false)
	if !b.Column.Sargable {
		t.Error(`collation="sargable" did not make the column sargable`)
	}
	b = ColumnBinding("c", "t", KindText, false, false, false, "", "", true)
	if b.Column.Prefilter != "separate" {
		t.Errorf("splitSargable did not imply the separate prefilter: %q", b.Column.Prefilter)
	}
	if !refuses(func() { ColumnBinding("c", "t", KindText, false, false, false, "bogus", "", false) }) {
		t.Error("an unknown collation was accepted")
	}
	if !refuses(func() { ColumnBinding("c", "t", KindText, false, false, false, "", "bogus", false) }) {
		t.Error("an unknown prefilter was accepted")
	}
}

func TestTemplateFillKeepsBytesAndEmptyTails(t *testing.T) {
	Reset()
	defer Reset()
	DefineDialect("t-fill", map[string]interface{}{"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{"textCollate": ""}})
	Define("t-fill", "funcs", "UPPER", map[string]interface{}{"tpl": "F({0}, 'é ż'{1:})", "ret": "TEXT"})
	b := map[string]*Binding{"NAME": ColumnBinding("name", "o", KindText, false, false, false, "", "", false)}
	got := expr(t, "t-fill", "UPPER(NAME)", b)
	if !strings.Contains(got, "'é ż'") || !strings.HasSuffix(got, ")") {
		t.Fatalf("a non-ASCII template byte was mangled or an empty tail refused: %s", got)
	}
}

func TestAliasesFromProgramTextAreChecked(t *testing.T) {
	items := map[string]*Binding{"ITEMS": RelationBinding("items", "i", map[string]*Binding{"PRICE": colNum("price")}, "", "", "", false)}
	wantCode(t, refusal(t, "mariadb", `ITEMS .> MAP(RECORD("", _["PRICE"]))`, items, true), "E_SQL_UNSUPPORTED")
	wantCode(t, refusal(t, "mariadb", "ITEMS .> MAP(RECORD(\"a\\u{0}b\", _[\"PRICE\"]))", items, true), "E_SQL_UNSUPPORTED")
	long := strings.Repeat("a", 63)
	src := fmt.Sprintf(`ITEMS .> MAP(RECORD("%sX1", _["PRICE"], "%sX2", _["PRICE"]))`, long, long)
	wantCode(t, refusal(t, "postgresql", src, items, true), "E_SQL_UNSUPPORTED")
	// MariaDB does not truncate: the same keys are fine there.
	_ = stmt(t, "mariadb", src, items)
}

func TestANulInAnInlineTextLiteralIsRefused(t *testing.T) {
	b := map[string]*Binding{"S": ColumnBinding("s", "o", KindText, false, false, false, "", "", false)}
	wantCode(t, refusal(t, "mariadb", "S $== \"a\\u{0}b\"", b, false), "E_SQL_UNSUPPORTED")
}

func TestARawFieldDoesNotSurviveASelectOrADerivedTable(t *testing.T) {
	items := map[string]*Binding{"ITEMS": RelationBinding("items", "i", map[string]*Binding{
		"ID":    colNum("id"),
		"TOTAL": RawBinding("i.price * i.qty", KindNum, false, false, false, "", "", false),
	}, "", "", "", false)}
	wantCode(t, refusal(t, "mariadb", `ITEMS .> SELECT_COLS("TOTAL")`, items, true), "E_SQL_SHAPE")
	wantCode(t, refusal(t, "mariadb", `ITEMS .> SORT_BY(_["ID"]) .> TAKE(2) .> FILTER(_["TOTAL"] > 5)`, items, true), "E_SQL_SHAPE")
}

func TestModeParamsLeavesNoOrphanSlotAfterAPrefixCheck(t *testing.T) {
	b := map[string]*Binding{
		"R": RelationBinding("r", "r", map[string]*Binding{"A": colNum("a"), "N": ColumnBinding("n", "r", KindText, false, false, false, "", "", false)}, "", "", "", false),
		"S": RelationBinding("s", "s", map[string]*Binding{"A": colNum("a"), "M": ColumnBinding("m", "s", KindText, false, false, false, "", "", false)}, "", "", "", false),
	}
	f, err := TranslateStatement(sel.MustCompile(`R .> FILTER(_["N"] $== "zz") .> SORT_BY(_["N"]) .> TAKE(3) .> LINK(S, _1["A"] == _2["A"] AND _2["M"] $== "yy") .> MAP(RECORD("n", _["N"], "m", _["M"]))`), "mariadb", NewBindings(b), Options{})
	if err != nil {
		t.Fatal(err)
	}
	stmtText := f.AsStatement(ModeParams)
	if got, slots := len(f.Bindings()), strings.Count(stmtText, "?"); got != slots {
		t.Fatalf("%d values bound for %d placeholders: %s", got, slots, stmtText)
	}
}

// --- T11: hybrid parity -------------------------------------------------------

func ordersAndCustomers() *Bindings {
	return NewBindings(map[string]*Binding{
		"ORDERS": RelationBinding("orders", "o", map[string]*Binding{
			"ID": colNum("id"), "CUSTOMER_ID": colNum("customer_id"), "AMOUNT": colNum("amount"),
			"NAME": ColumnBinding("name", "o", KindText, false, false, false, "", "", false),
		}, "", "", "", false),
		"CUSTOMERS": RelationBinding("customers", "c", map[string]*Binding{
			"ID": colNum("id"), "NAME": ColumnBinding("name", "c", KindText, false, false, false, "", "", false),
		}, "", "", "", false),
	})
}

func plan(t *testing.T, src string) *HybridPlan {
	t.Helper()
	return PlanHybrid(sel.MustCompile(src), "mariadb", ordersAndCustomers(), Options{})
}

func TestASplitAfterAFilterThatTheContinuationCanObserveIsNotASplit(t *testing.T) {
	// The MAP's _K read has no SQL spelling; the rows the continuation would see
	// are renumbered 1..n where run() keeps the FILTER's keys.
	if p := plan(t, `ORDERS .> FILTER(_["id"] > 2) .> MAP(RECORD("i", _["id"], "k", _K))`); !p.PureMemory {
		t.Errorf("a _K read after a FILTER boundary must be pure memory, got %+v", p)
	}
	// An unpushable FILTER after a pushable one ends with keys nobody renumbered.
	if p := plan(t, `ORDERS .> FILTER(_["id"] > 2) .> FILTER(ABORT("x") $== "y")`); !p.PureMemory {
		t.Errorf("a trailing local FILTER after a pushed FILTER must be pure memory, got %+v", p)
	}
}

func TestRowCuttingStepsStayBehindALocalHalfThatCanRaise(t *testing.T) {
	p := plan(t, `ORDERS .> SORT_BY(_["id"]) .> MAP(RECORD("i", _["id"], "z", IF(_["id"] > 4, ABORT("x"), 1))) .> TAKE(2)`)
	if !p.IsHybrid {
		t.Fatalf("want a hybrid plan, got %+v", p)
	}
	sqlText := p.SqlStatement.AsStatement(ModeInline)
	if strings.Contains(sqlText, "LIMIT") {
		t.Errorf("the LIMIT was pushed in front of a local half that can raise: %s", sqlText)
	}
	if !strings.Contains(sql4(p), "TAKE") {
		t.Errorf("the TAKE must remain in the continuation")
	}
}

func sql4(p *HybridPlan) string {
	if p.ContinuationAst == nil {
		return ""
	}
	var names []string
	var walk func(n *sel.Node)
	walk = func(n *sel.Node) {
		if n == nil {
			return
		}
		if n.T == sel.NodeCall {
			names = append(names, n.S)
		}
		walk(n.L)
		walk(n.R)
		for _, c := range n.Items {
			walk(c)
		}
	}
	walk(p.ContinuationAst)
	return strings.Join(names, " ")
}

func TestSplitBeforeALinkNamesTheLeftSideAfterTheRelation(t *testing.T) {
	p := plan(t, `ORDERS .> SORT_BY(_["id"]) .> TAKE(4) .> LINK(CUSTOMERS, _1["customer_id"] == _2["id"] AND COUNT(SPLIT(_1["name"], "a")) > 0)`)
	if !p.IsHybrid {
		t.Fatalf("want a hybrid plan, got %+v", p)
	}
	rows := sel.NewList([]*sel.Value{sel.NewRecordFromEntries([]sel.Entry{
		{Key: "id", Val: sel.NewInt(1)}, {Key: "customer_id", Val: sel.NewInt(7)}, {Key: "name", Val: sel.NewText("banana")},
	})})
	customers := sel.NewList([]*sel.Value{sel.NewRecordFromEntries([]sel.Entry{
		{Key: "id", Val: sel.NewInt(7)}, {Key: "name", Val: sel.NewText("ann")},
	})})
	ctx := sel.NewRecordFromEntries([]sel.Entry{{Key: "CUSTOMERS", Val: customers}})
	out, err := ExecuteHybrid(p, func(q string, params []*sel.Value) (*sel.Value, error) { return rows, nil }, ctx)
	if err != nil {
		t.Fatal(err)
	}
	dump := out.Dump()
	if !strings.Contains(dump, "ORDERS") || strings.Contains(dump, "_INPUT") {
		t.Fatalf("the joined row must carry the left side as ORDERS, got %s", dump)
	}
}

func TestExecuteHybridKeepsTheCallersVariablesAndNeverMutatesThem(t *testing.T) {
	mem := PlanHybrid(sel.MustCompile(`K = 5; MAP((1,2), _ + K)`), "mariadb", ordersAndCustomers(), Options{})
	if !mem.PureMemory {
		t.Fatalf("want pure memory, got %+v", mem)
	}
	ctx := sel.NewRecordFromEntries([]sel.Entry{{Key: "Z", Val: sel.NewInt(1)}})
	if _, err := ExecuteHybrid(mem, nil, ctx); err != nil {
		t.Fatal(err)
	}
	if ctx.Has("K") {
		t.Fatalf("a pure-memory plan mutated the caller's context: %s", ctx.Dump())
	}
	// A hybrid plan: the continuation still reads a caller variable.
	hp := plan(t, `ORDERS .> DROP(1) .> DEDUPE() .> TAKE(4) .> DROP(K)`)
	if !hp.IsHybrid {
		t.Fatalf("want a hybrid plan, got %+v", hp)
	}
	rows := sel.NewList([]*sel.Value{sel.NewInt(1), sel.NewInt(2), sel.NewInt(3)})
	ctx2 := sel.NewRecordFromEntries([]sel.Entry{{Key: "K", Val: sel.NewInt(1)}})
	out, err := ExecuteHybrid(hp, func(q string, params []*sel.Value) (*sel.Value, error) { return rows, nil }, ctx2)
	if err != nil {
		t.Fatalf("the caller's K was thrown away: %v", err)
	}
	if out.Size() != 2 {
		t.Fatalf("DROP(K) with K = 1 over three rows: %s", out.Dump())
	}
	if ctx2.Has("_INPUT") {
		t.Fatalf("the caller's context gained the source variable: %s", ctx2.Dump())
	}
}

func TestSourceTablesIgnoreBindersAndAssignmentTargets(t *testing.T) {
	cases := map[string][]string{
		`MAP(LIST(1, 2), ORDERS, ORDERS + 1)`:          {},
		`ORDERS = 5; ORDERS + 1`:                       {},
		`ORDERS .> FILTER(CUSTOMERS, _ > 1)`:           {"orders"},
		`ORDERS .> MAP(RECORD("n", COUNT(CUSTOMERS)))`: {"orders", "customers"},
	}
	for src, want := range cases {
		p := plan(t, src)
		if fmt.Sprint(p.SourceTables) != fmt.Sprint(want) && !(len(p.SourceTables) == 0 && len(want) == 0) {
			t.Errorf("%s: source tables %v, want %v", src, p.SourceTables, want)
		}
	}
}

func TestALiteralHelperNamedLikeABinderIsNotInlinedIntoTheBinderSlot(t *testing.T) {
	p := plan(t, `N = 5; ORDERS .> FILTER(_["id"] > 1) .> SORT_BY(N, N["id"], "DESC") .> TAKE(2)`)
	if !p.PureSql {
		t.Fatalf("want pure SQL, got %+v", p)
	}
}

func TestThePlannerIsLinearInHelperNesting(t *testing.T) {
	// H1 = H0 + H0 ... read by a MAP next to a pair with no SQL spelling: the
	// unsupported-call walk visited every helper once per read (2^n).
	var b strings.Builder
	b.WriteString("H0 = 1; ")
	for i := 1; i <= 40; i++ {
		fmt.Fprintf(&b, "H%d = H%d + H%d; ", i, i-1, i-1)
	}
	b.WriteString(`ORDERS .> MAP(RECORD("plus", H40 + _["amount"], "tag", ABORT("x")))`)
	start := time.Now()
	_ = plan(t, b.String())
	if d := time.Since(start); d > 10*time.Second {
		t.Fatalf("planning took %v", d)
	}
}

func TestUnknownRenderModeNameIsAlwaysRefused(t *testing.T) {
	if !refuses(func() { ModeFromName("nonsense") }) {
		t.Fatal("an unknown mode name was accepted")
	}
}
