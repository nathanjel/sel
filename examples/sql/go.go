// SQL-aimed usage — pushing a rule down to the database, from Go.
//
//   make -C go examples && go/build/example-sql
//
// The same rule that validates one order in the application can filter a
// million of them in the database. What makes that safe is that the translation
// refuses rather than guesses: if SQL cannot be made to mean what SEL means, no
// SQL is emitted and the rule stays where it already worked.
//
// The files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is that Go, like C++ and Rust, has no untyped map
// to hand over as bindings: a Bindings is built from a map of typed bindings,
// and TryTranslate answers nil when it declines. sql.ColumnBinding takes every
// column option positionally, so the helper below names the three this file
// uses and passes the defaults. Translate returns its refusal as an error,
// always a *sql.SqlError; rendering a fragment (AsCondition, AsValue) panics
// with one, as a Value's accessors panic with a *sel.SelError.

package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

// A column binding with no flags, collation or prefilter; "" is "no table".
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
	// 1 — a rule, and what the database should call its inputs --------------------
	// Dependencies() says exactly what has to be bound. A name the program reads
	// and the bindings do not describe is a refusal, not a guess.

	fmt.Println("1. a rule pushed down")
	rule := sel.MustCompile(`TOTAL > 100.00 AND STATUS $== "open"`)
	fmt.Println("   needs        =>", strings.Join(rule.Dependencies(), " "))

	bindings := sql.NewBindings(map[string]*sql.Binding{
		"TOTAL":  col("total", "o", sql.KindNum),
		"STATUS": col("status", "o", sql.KindText),
	})
	frag, err := sql.Translate(rule, "mariadb", bindings, sql.Options{})
	check(err)
	// Rendering can refuse — AsCondition rejects a NUM or TEXT fragment rather
	// than letting the database decide what truthiness means.
	fmt.Println("   sql          =>", frag.AsCondition(sql.ModeInline))

	// 2 — the same rule as a prepared statement -------------------------------------
	// ModeInline is for reading and for a query you build once. ModeParams is
	// what you hand a driver: the literals become placeholders and Bindings()
	// gives the values in the order the placeholders appear in the output.

	fmt.Println("2. as parameters")
	fmt.Println("   sql          =>", frag.AsCondition(sql.ModeParams))
	var values []string
	for _, v := range frag.Bindings() {
		values = append(values, v.Dump())
	}
	fmt.Println("   values       =>", strings.Join(values, ", "))

	// 3 — one rule, every dialect ----------------------------------------------------
	// The differences below are the databases', not the rule's. Nothing in the
	// program changed.

	fmt.Println("3. every dialect")
	for _, dialect := range sql.Dialects() {
		f, err := sql.Translate(rule, dialect, bindings, sql.Options{})
		check(err)
		fmt.Printf("   %-12s => %s\n", dialect, f.AsCondition(sql.ModeInline))
	}

	// 4 — a rule over a related table -------------------------------------------------
	// An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
	// which is the shape a database can actually use an index for.

	fmt.Println("4. over a relation")
	lines := sel.MustCompile(`ALL(ITEMS, I, I["QTY"] > 0)`)
	itemBindings := sql.NewBindings(map[string]*sql.Binding{
		"ITEMS": sql.RelationBinding(
			"order_items",
			"oi",
			[]sql.FieldEntry{{Name: "QTY", Binding: col("qty", "", sql.KindNum)}},
			"",                           // no scalar column
			"`oi`.`order_id` = `o`.`id`", // the correlation
			"",
			false,
		),
	})
	relation, err := sql.Translate(lines, "mariadb", itemBindings, sql.Options{})
	check(err)
	fmt.Println("   sql          =>", relation.AsCondition(sql.ModeInline))

	// 5 — refusal is an ordinary answer ------------------------------------------------
	// TryTranslate returns nil so the caller can fall back to the evaluator
	// without inspecting an error. Translate returns the same refusal with the
	// reason written out, which is what you want in a build-time audit of a rule
	// set.

	fmt.Println("5. refusal")
	unbound := sel.MustCompile("MYSTERY > 1")
	// The label is JS's spelling of the method, in every one of the files. The
	// outputs have to be byte-identical, so one host's name for the call is what
	// all of them print; this host's is TryTranslate.
	answer := "translated"
	if sql.TryTranslate(unbound, "mariadb", bindings, sql.Options{}) == nil {
		answer = "null — evaluate it in the host instead"
	}
	fmt.Println("   tryTranslate =>", answer)
	// Translate only ever answers a refusal as a *sql.SqlError. A bug in the
	// translator, or a malformed registration, is a panic and goes on up — the
	// same line JS draws with its `instanceof` rethrow.
	_, err = sql.Translate(unbound, "mariadb", bindings, sql.Options{})
	var refused *sql.SqlError
	if errors.As(err, &refused) {
		fmt.Println("   translate    =>", refused.Code)
	}

	// 6 — what a fragment knows about itself ---------------------------------------------
	// A caveat is the map saying "this dialect's answer may differ from SEL's here".
	// An empty list is the layer promising it does not.

	fmt.Println("6. the fragment")
	fmt.Println("   kind         =>", frag.Kind)
	fmt.Println("   dialect      =>", frag.Dialect)
	exact := "FALSE"
	if frag.IsExact() {
		exact = "TRUE"
	}
	fmt.Println("   exact        =>", exact)
}
