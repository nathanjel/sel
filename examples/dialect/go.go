// Adding a SQL flavour — teaching the translator about your database, from Go.
//
//   make -C go examples && go/build/example-dialect
//
// The shipped map covers four targets over two bases. A deployment is rarely
// exactly one of them: a driver wants numbered placeholders, a function is
// spelled differently, an extension is not installed. A dialect is registered
// rather than forked, so what you write is only the difference.
//
// The files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is the shape of a registration. The map's
// functions sit in package sql beside Translate, and a dialect or an entry is
// the same JSON the dynamic hosts write as a literal, spelled as a
// map[string]any. The registration calls return nothing: a spec the map will
// not accept panics, where the other hosts throw.

package main

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

// A column binding with no flags, collation or prefilter.
func col(column, table string, kind sql.SqlKind) *sql.Binding {
	return sql.ColumnBinding(column, table, kind, false, false, false, "", "", false)
}

// check stops the example on an error a well-formed program never has.
func check(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	rule := sel.MustCompile(`NAME $== "ok" AND TOTAL > 10.00`)
	bindings := sql.NewBindings(map[string]*sql.Binding{
		"NAME":  col("name", "t", sql.KindText),
		"TOTAL": col("total", "t", sql.KindNum),
	})
	sqlIn := func(dialect string) string {
		frag, err := sql.Translate(rule, dialect, bindings, sql.Options{})
		check(err)
		return frag.AsCondition(sql.ModeParams)
	}

	// 1 — what ships ---------------------------------------------------------------

	fmt.Println("1. what ships")
	fmt.Println("   targets      =>", strings.Join(sql.Dialects(), " "))
	fmt.Println("   postgresql   =>", strings.Join(sql.Chain("postgresql"), " -> "))

	// 2 — a flavour of your own -------------------------------------------------------
	// `extends` is the whole mechanism: the new dialect answers for what it declares
	// and defers upward for everything else. Two-phase lookup -- the whole overlay
	// chain, then the whole shipped chain -- so an override never half-applies.
	//
	// A spec that leaves `extends` out is refused, as it is in every dynamic host;
	// a dialect with no parent says "extends": nil, as ansi does.

	fmt.Println("2. a flavour of your own")
	// EXAMPLE-BEGIN flavour
	sql.DefineDialect("pg-libpq", map[string]any{
		"extends": "postgresql",
		"version": "15",
		"target":  true,                                  // a base is not a target; this is a server
		"lexical": map[string]any{"placeholder": "${n}"}, // libpq numbers its parameters
	})
	fmt.Println("   targets      =>", strings.Join(sql.Dialects(), " "))
	fmt.Println("   chain        =>", strings.Join(sql.Chain("pg-libpq"), " -> "))
	fmt.Println("   base         =>", sqlIn("postgresql"))
	fmt.Println("   pg-libpq     =>", sqlIn("pg-libpq"))
	// EXAMPLE-END flavour

	// 3 — spelling one function differently ---------------------------------------------
	// {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
	// while every position SEL reports is one-based -- these are template holes, not
	// SEL positions. The entry also says what it returns, because the translator
	// infers kinds and will not guess.

	fmt.Println("3. one function, respelled")
	// EXAMPLE-BEGIN respell
	sql.Define("pg-libpq", "funcs", "UPPER", map[string]any{"tpl": `UPPER({0} COLLATE "C")`, "ret": "TEXT"})
	upper, err := sql.Translate(sel.MustCompile("UPPER(NAME)"), "pg-libpq", bindings, sql.Options{})
	check(err)
	fmt.Println("   upper        =>", upper.AsValue(sql.ModeInline))
	// EXAMPLE-END respell

	// 4 — withdrawing what a deployment does not have ------------------------------------
	// A nil entry withdraws it. This is not the same as leaving it unmapped: it is
	// the map saying "not here", and the rule is refused rather than emitted against
	// a function the server does not have.

	fmt.Println("4. withdrawing an entry")
	// EXAMPLE-BEGIN withdraw
	sql.Define("pg-libpq", "funcs", "RMATCH", nil)
	re := sel.MustCompile("RMATCH('^a', NAME)")
	for _, dialect := range []string{"postgresql", "pg-libpq"} {
		answer := "translated"
		if sql.TryTranslate(re, dialect, bindings, sql.Options{}) == nil {
			answer = "refused"
		}
		fmt.Printf("   %-12s => %s\n", dialect, answer)
	}
	// EXAMPLE-END withdraw

	// 5 — a builder, for what a template cannot say -----------------------------------------
	// The escape hatch. It receives the emitter and the already-rendered arguments,
	// and returns a fragment, so it can do what no string with holes in it can. The
	// third argument is the call site; a builder has to take it even when it has
	// no use for it, and the blank identifier says so.
	//
	// Splice the argument's Parts rather than its rendered SQL. A part list is
	// strings alternating with parameter slots, so splicing keeps a bound value
	// bound; flattening it to a string first would inline whatever the argument
	// carried and quietly turn a prepared statement back into concatenation.
	//
	// Slot numbers are absolute for the whole translation, so a spliced part is
	// copied verbatim and never renumbered.

	fmt.Println("5. a builder")
	// EXAMPLE-BEGIN builder
	sql.DefineBuilder("pg-libpq", "funcs", "LEN", func(emit *sql.Emit, args []*sql.Fragment, _ sel.Pos) *sql.Fragment {
		parts := []sql.Part{{Sql: "length("}}
		parts = append(parts, args[0].Parts...)
		parts = append(parts, sql.Part{Sql: ")"})
		return sql.NewFragment(parts, sql.KindNum, emit.Dialect(), nil, nil, nil)
	})
	length, err := sql.Translate(sel.MustCompile("LEN(NAME)"), "pg-libpq", bindings, sql.Options{})
	check(err)
	fmt.Println("   len          =>", length.AsValue(sql.ModeInline))
	// EXAMPLE-END builder

	// 6 — putting it back ---------------------------------------------------------------------
	// Reset() drops every registration and leaves the shipped map. Worth knowing in
	// a test suite: a registration that leaks into the next test is a test that
	// passes for the wrong reason.

	fmt.Println("6. reset")
	sql.Reset()
	fmt.Println("   targets      =>", strings.Join(sql.Dialects(), " "))
	gone := "gone"
	if sql.Exists("pg-libpq") {
		gone = "still there"
	}
	fmt.Println("   pg-libpq     =>", gone)
}
