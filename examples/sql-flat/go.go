// An unnormalised export -- one wide table, from Go.
//
//   tools/check-usage.sh sql-flat              (starts the databases for you)
//
// order_export repeats the customer and the product on every line, the way a
// spreadsheet or a nightly dump does, with the inconsistencies that come with
// it: the same person under two spellings of their name and e-mail. The first
// pipeline groups in SQL. The second normalises e-mails and counts distinct
// customers, and the planner keeps the normalised values out of MariaDB's
// hands: its collation would decide which of them are "the same", and SEL's
// identity is exact bytes. The third explodes a `;`-separated column, which no
// SQL step can express, so it runs in memory entirely.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-flat/go.go`
// in examples/ (tools/check-usage.sh does it in its image), which brings in
// examples/lib/db and its PostgreSQL, MariaDB and SQLite drivers.

package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/examples/lib/db"
	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func read(path string) string {
	text, err := os.ReadFile(path)
	check(err)
	return string(text)
}

// EXAMPLE-BEGIN bindings
type field struct {
	name string
	kind sql.SqlKind
}

func relation(table, alias string, fields ...field) *sql.Binding {
	columns := make([]sql.FieldEntry, len(fields))
	for i, f := range fields {
		columns[i] = sql.FieldEntry{Name: f.name,
			Binding: sql.ColumnBinding(f.name, alias, f.kind, false, false, false, "", "", false)}
	}
	return sql.RelationBinding(table, alias, columns, "", "", "", false)
}

func schema() *sql.Bindings {
	num, txt := sql.KindNum, sql.KindText
	return sql.NewBindings(map[string]*sql.Binding{
		"EXPORT": relation("order_export", "x", field{"line_id", num}, field{"order_no", txt},
			field{"order_date", txt}, field{"customer_name", txt},
			field{"customer_email", txt}, field{"customer_city", txt},
			field{"sku", txt}, field{"product_name", txt},
			field{"category", txt}, field{"qty", num},
			field{"unit_price", num}, field{"tags", txt}),
	})
}

// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: {title, file}.
var pipelines = [][2]string{
	{"revenue per city, February and March", "revenue-per-city.sel"},
	{"distinct customers per city, by normalised e-mail", "customers-per-city.sel"},
	{"lines per tag", "lines-per-tag.sel"},
}

func main() {
	schema := schema()
	conn, err := db.Connect("mariadb")
	check(err)
	defer conn.Close()

	// The same tables in memory, for the comparison at the end of each pipeline.
	tables := sel.NewNone()
	for _, t := range [][3]string{{"EXPORT", "order_export", "line_id"}} {
		rows, err := db.Query(conn, fmt.Sprintf("SELECT * FROM %s ORDER BY %s", t[1], t[2]), nil)
		check(err)
		tables.Set(t[0], rows)
	}

	for n, p := range pipelines {
		title, file := p[0], p[1]
		// EXAMPLE-BEGIN run
		program, err := sel.Compile(read("examples/sql-flat/" + file))
		check(err)
		plan := sql.PlanHybrid(program, "mariadb", schema, sql.Options{})
		var context *sel.Value
		if plan.PureMemory {
			context = tables
		}
		rows, err := sql.ExecuteHybrid(plan, db.Runner(conn), context)
		check(err)
		// EXAMPLE-END run
		kind := "hybrid"
		if plan.PureSql {
			kind = "pure_sql"
		} else if plan.PureMemory {
			kind = "pure_memory"
		}
		fmt.Printf("%d. %s\n", n+1, title)
		fmt.Println("   plan       ", kind)
		fmt.Println("   reads      ", strings.Join(plan.SourceTables, ", "))
		if plan.SqlStatement != nil {
			fmt.Println("   sql        ", plan.SqlStatement.AsStatement(sql.ModeInline))
		}
		text, err := db.Render(rows, "   | ")
		check(err)
		fmt.Println(text)
		// A *sel.Value is a handle: the program gets a copy, so the tables stay as loaded.
		inMemory, err := program.Run(tables.Clone())
		check(err)
		same := "DIFFERENT"
		if inMemory.Dump() == rows.Dump() {
			same = "same rows"
		}
		fmt.Println("   in memory  ", same)
	}
}
