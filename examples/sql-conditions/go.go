// SQL conditions -- one rule as a WHERE clause, from Go.
//
//   tools/check-usage.sh sql-conditions        (starts the databases for you)
//
// A rule written for the application can filter rows where they live. Part 1 is
// the naive integration: the host knows nothing about the schema except that a
// variable is a column of the same name. Part 2 describes the schema -- types,
// a list of columns, a related table, a parameter -- and gets SQL that is both
// tighter and able to say more. Either way a rule SQL cannot express is refused
// whole, and runs in memory instead; every answer below is checked against the
// in-memory one.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-conditions/go.go`
// in examples/ (tools/check-usage.sh does it in its image), which brings in
// examples/lib/db and its PostgreSQL, MariaDB and SQLite drivers.

package main

import (
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/examples/lib/db"
	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

var at = sel.Pos{}

func check(err error) {
	if err != nil {
		panic(err)
	}
}

// A column the table has, read by name (SELECT * brought them all).
func field(row *sel.Value, name string) *sel.Value {
	v := row.Get(name)
	if v == nil {
		panic("the row has no column " + name)
	}
	return v
}

func ids(records *sel.Value) string {
	var out []string
	for _, record := range records.Values() {
		out = append(out, field(record, "id").AsText(at))
	}
	if len(out) == 0 {
		return "(none)"
	}
	return strings.Join(out, ", ")
}

// Translate says why TryTranslate returned nothing.
func refusal(rule *sel.Program, dialect string, bindings *sql.Bindings) string {
	_, err := sql.Translate(rule, dialect, bindings, sql.Options{})
	var refused *sql.SqlError
	var failed *sel.SelError
	switch {
	case err == nil:
		return "translated"
	case errors.As(err, &refused):
		return refused.Code
	case errors.As(err, &failed):
		return failed.Code
	}
	return err.Error()
}

// A row as a rule's context: SEL names are upper case, columns are not (both are
// ASCII, where strings.ToUpper changes what to_ascii_uppercase would).
func contextOf(row *sel.Value) *sel.Value {
	ctx := sel.NewNone()
	for _, entry := range row.Entries() {
		ctx.Set(strings.ToUpper(entry.Key), entry.Val)
	}
	return ctx
}

// The records a rule accepts, run in memory; keys kept.
func runInMemory(rule *sel.Program, rows *sel.Value, context func(*sel.Value) *sel.Value) *sel.Value {
	kept := sel.NewNone()
	for _, entry := range rows.Entries() {
		v, err := rule.Run(context(entry.Val))
		check(err)
		if v.AsBool(at) {
			kept.Set(entry.Key, entry.Val)
		}
	}
	return kept
}

var rules = []string{
	`COUNTRY $== "PL" AND TIER $!= "standard"`,
	`COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000`,
	`RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)`,
	`IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL)`,
	`ANY(SPLIT(NAME, " "), LEN(_) > 9)`,
}

// What the rule sees in memory: the same names, as values.
func orderContext(order, items *sel.Value) *sel.Value {
	ctx := sel.NewNone()
	for _, name := range []string{"status", "total", "channel"} {
		ctx.Set(strings.ToUpper(name), field(order, name))
	}
	tags := sel.NewNone()
	for n, name := range []string{"tag1", "tag2", "tag3"} {
		tags.Set(strconv.Itoa(n+1), field(order, name))
	}
	ctx.Set("TAGS", tags)
	id := field(order, "id").AsText(at)
	lines := sel.NewNone()
	for _, item := range items.Values() {
		if field(item, "order_id").AsText(at) == id {
			lines.Set(strconv.Itoa(lines.Size()+1), item)
		}
	}
	ctx.Set("ITEMS", lines)
	ctx.Set("MIN_TOTAL", sel.NewText("100.00"))
	return ctx
}

func yesNo(same bool) string {
	if same {
		return "TRUE"
	}
	return "FALSE"
}

func main() {
	// 1 - naive: a column per variable, nothing else known ---------------------------

	fmt.Println("1. naive bindings")
	for _, dialect := range []string{"sqlite", "mariadb"} {
		conn, err := db.Connect(dialect)
		check(err)
		everyone, err := db.Query(conn, "SELECT * FROM customers ORDER BY id", nil)
		check(err)
		for _, source := range rules {
			// EXAMPLE-BEGIN naive
			rule, err := sel.Compile(source)
			check(err)
			columns := map[string]*sql.Binding{}
			for _, name := range rule.Dependencies() {
				columns[name] = sql.ColumnBinding(strings.ToLower(name), "", sql.KindUnknown,
					false, false, false, "", "", false)
			}
			bindings := sql.NewBindings(columns)
			condition := sql.TryTranslate(rule, dialect, bindings, sql.Options{})
			var rows *sel.Value
			if condition != nil {
				query := "SELECT id FROM customers WHERE " + condition.AsCondition(sql.ModeParams) + " ORDER BY id"
				rows, err = db.Query(conn, query, condition.Bindings())
				check(err)
			} else {
				// refused: the rule stays in the application, over rows it loads
				rows = sel.NewNone()
				for _, row := range everyone.Entries() {
					keep, err := rule.Run(contextOf(row.Val))
					check(err)
					if keep.AsBool(at) {
						rows.Set(row.Key, row.Val)
					}
				}
			}
			// EXAMPLE-END naive
			inMemory := runInMemory(rule, everyone, contextOf)
			fmt.Printf("   %-8s %s\n", dialect, source)
			if condition != nil {
				fmt.Println("             sql   ", condition.AsCondition(sql.ModeInline))
			} else {
				fmt.Printf("             memory (%s)\n", refusal(rule, dialect, bindings))
			}
			fmt.Printf("             rows   %s | same as in memory: %s\n", ids(rows), yesNo(ids(rows) == ids(inMemory)))
		}
		check(conn.Close())
	}

	// 2 - involved: the host describes its schema ------------------------------------

	fmt.Println("2. described bindings")
	conn, err := db.Connect("postgresql")
	check(err)
	defer conn.Close()
	// EXAMPLE-BEGIN involved
	// sql.ColumnBinding's arguments are all positional; these are the four that vary.
	column := func(name, table string, kind sql.SqlKind, exact bool) *sql.Binding {
		return sql.ColumnBinding(name, table, kind, exact, false, false, "", "", false)
	}
	num, txt := sql.KindNum, sql.KindText
	bindings := sql.NewBindings(map[string]*sql.Binding{
		"STATUS":  column("status", "o", txt, true),
		"TOTAL":   column("total", "o", num, false),
		"CHANNEL": column("channel", "o", txt, true),
		"TAGS": sql.ColumnsBinding([]*sql.Binding{column("tag1", "o", txt, false),
			column("tag2", "o", txt, false),
			column("tag3", "o", txt, false)}),
		"ITEMS": sql.RelationBinding("order_items", "i", []sql.FieldEntry{
			{Name: "sku", Binding: column("sku", "i", txt, false)},
			{Name: "qty", Binding: column("qty", "i", num, false)},
			{Name: "price", Binding: column("price", "i", num, false)},
		}, "" /* scalar */, `"i"."order_id" = "o"."id"` /* correlate */, "", false),
		"MIN_TOTAL": sql.ValueBinding(sel.NewText("100.00"), nil),
	})
	// EXAMPLE-END involved

	orders, err := db.Query(conn, "SELECT * FROM orders ORDER BY id", nil)
	check(err)
	items, err := db.Query(conn, "SELECT * FROM order_items ORDER BY order_id, line_no", nil)
	check(err)

	for _, source := range []string{
		`STATUS $== "paid" AND TOTAL >= MIN_TOTAL`,
		`ANY(TAGS, _ $== "gift") AND CHANNEL $== "web"`,
		`COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)`,
		`SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL`,
		`ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")`,
	} {
		// EXAMPLE-BEGIN involved-run
		rule, err := sel.Compile(source)
		check(err)
		condition, err := sql.Translate(rule, "postgresql", bindings, sql.Options{})
		check(err)
		query := "SELECT id FROM orders o WHERE " + condition.AsCondition(sql.ModeParams) + " ORDER BY id"
		rows, err := db.Query(conn, query, condition.Bindings())
		check(err)
		// EXAMPLE-END involved-run
		inMemory := runInMemory(rule, orders, func(order *sel.Value) *sel.Value { return orderContext(order, items) })
		fmt.Println("  ", source)
		fmt.Println("             sql   ", condition.AsCondition(sql.ModeInline))
		if params := condition.Bindings(); len(params) > 0 {
			texts := make([]string, len(params))
			for i, p := range params {
				texts[i] = p.AsText(at)
			}
			fmt.Println("             params", strings.Join(texts, ", "))
		}
		fmt.Printf("             rows   %s | same as in memory: %s\n", ids(rows), yesNo(ids(rows) == ids(inMemory)))
	}
}
