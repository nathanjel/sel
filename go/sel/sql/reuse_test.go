package sql

import (
	"fmt"
	"sync"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// T10 (2026-09-29 review). The questions a snapshot of ONE translation cannot
// ask: does the same program translate to the same bytes every time, is a
// refused dialect refused on every use, is an unknown render mode refused, and
// may several goroutines translate at once (`go test -race`).

func refuses(fn func()) (refused bool) {
	defer func() {
		if r := recover(); r != nil {
			refused = true
		}
	}()
	fn()
	return false
}

func itemsAndTags() *Bindings {
	return NewBindings(map[string]*Binding{
		"ITEMS": RelationBinding("items", "i", map[string]*Binding{
			"PRICE": ColumnBinding("price", "", KindNum, false, false, false, "", "", false),
			"QTY":   ColumnBinding("qty", "", KindNum, false, false, false, "", "", false),
			"NAME":  ColumnBinding("name", "", KindText, false, false, false, "", "", false),
			"SKU":   ColumnBinding("sku", "", KindText, false, false, false, "", "", false),
		}, "", "", "", false),
		"TAGS": RelationBinding("tags", "t", map[string]*Binding{
			"TAG": ColumnBinding("tag", "", KindText, false, false, false, "", "", false),
			"W":   ColumnBinding("w", "", KindNum, false, false, false, "", "", false),
		}, "", "", "", false),
	})
}

// GO-C18: the derived table's select list followed Go's map iteration order, so
// the same program gave a different statement on different runs.
func TestDerivedTableColumnOrderIsStable(t *testing.T) {
	seen := map[string]int{}
	for i := 0; i < 40; i++ {
		p := sel.MustCompile(`ITEMS .> TAKE(5) .> LINK(TAGS, a, b, a["NAME"] $== b["TAG"])`)
		f, err := Translate(p, "mariadb", itemsAndTags(), Options{})
		if err != nil {
			t.Fatal(err)
		}
		seen[f.AsStatement(ModeInline)]++
	}
	if len(seen) != 1 {
		for s, n := range seen {
			t.Logf("%d x %s", n, s)
		}
		t.Fatalf("40 translations of one program gave %d different statements", len(seen))
	}
}

// JS-C24, PHP-C49, PY-C49, CPP-C36, LISP-C42: sql/MAP.md section 7, rule
// 10 at run time -- a numericGuard that lacks ISNUM's numeral pattern is refused
// every time the guard is used, not only the first.
func TestDialectWithABadNumericGuardIsRefusedOnEveryUse(t *testing.T) {
	Reset()
	defer Reset()
	DefineDialect("probe-badguard", map[string]interface{}{
		"extends": "postgresql",
		"version": "16",
		"lexical": map[string]interface{}{
			"numericGuard": "CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END",
		},
	})
	b := NewBindings(map[string]*Binding{
		"N": ColumnBinding("n", "t", KindText, false, false, false, "", "", false),
	})
	for use := 1; use <= 3; use++ {
		if !refuses(func() {
			if _, err := Translate(sel.MustCompile("N + 1"), "probe-badguard", b, Options{}); err != nil {
				panic(err)
			}
		}) {
			t.Fatalf("use %d of a dialect with a bad numericGuard was accepted", use)
		}
	}
}

// An unknown render mode NAME is refused, even for a fragment with no parameter
// slot; it used to become inline silently.
func TestUnknownRenderModeNameIsRefused(t *testing.T) {
	if refuses(func() { ModeFromName("params") }) {
		t.Fatal(`"params" is a mode`)
	}
	if !refuses(func() { ModeFromName("bogus") }) {
		t.Fatal(`"bogus" was accepted as a render mode`)
	}
}

// Translation is safe on several goroutines once the map is registered, each with
// its own program and bindings; every goroutine gets the same bytes. Run with -race.
func TestConcurrentTranslationsAgree(t *testing.T) {
	dialects := []string{"mariadb", "mysql", "postgresql", "sqlite"}
	want := map[string]string{}
	for _, d := range dialects {
		f, err := Translate(sel.MustCompile(`ITEMS .> FILTER(_["PRICE"] > 5) .> MAP(RECORD("n", _["NAME"]))`), d, itemsAndTags(), Options{})
		if err != nil {
			t.Fatal(err)
		}
		want[d] = f.AsStatement(ModeInline)
	}
	var wg sync.WaitGroup
	errs := make(chan string, 64)
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for r := 0; r < 50; r++ {
				for _, d := range dialects {
					f, err := Translate(sel.MustCompile(`ITEMS .> FILTER(_["PRICE"] > 5) .> MAP(RECORD("n", _["NAME"]))`), d, itemsAndTags(), Options{})
					if err != nil {
						errs <- err.Error()
						return
					}
					if got := f.AsStatement(ModeInline); got != want[d] {
						errs <- fmt.Sprintf("%s: %s != %s", d, got, want[d])
						return
					}
				}
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Error(e)
	}
}
