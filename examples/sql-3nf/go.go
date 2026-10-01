// A third-normal-form shop -- joins, grouping and a split, from Go.
//
//   tools/check-usage.sh sql-3nf               (starts the databases for you)
//
// categories, products, customers, orders and order_lines, each fact stored
// once. The first pipeline is SQL from end to end. The second assigns every
// customer to an A/B cohort by CRC32 of their e-mail -- the application's own
// hashing, which PostgreSQL has no spelling for -- so the database joins, filters
// and multiplies, and the cohorts are computed in memory over what it returned.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-3nf/go.go`
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
		"CUSTOMERS": relation("customers", "c", field{"customer_id", num}, field{"name", txt},
			field{"email", txt}, field{"country", txt}),
		"ORDERS": relation("orders", "o", field{"order_id", num}, field{"customer_id", num},
			field{"status", txt}, field{"ordered_on", txt}),
		"LINES": relation("order_lines", "l", field{"order_id", num}, field{"line_no", num},
			field{"product_id", num}, field{"qty", num}, field{"unit_price", num}),
		"PRODUCTS": relation("products", "p", field{"product_id", num}, field{"sku", txt},
			field{"title", txt}, field{"category_id", num}, field{"list_price", num}),
	})
}

// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: {title, file}.
var pipelines = [][2]string{
	{"paid revenue per product since March", "revenue-per-product.sel"},
	{"paid revenue per experiment cohort", "revenue-per-cohort.sel"},
}

func main() {
	schema := schema()
	conn, err := db.Connect("postgresql")
	check(err)
	defer conn.Close()

	// The same tables in memory, for the comparison at the end of each pipeline.
	tables := sel.NewNone()
	for _, t := range [][3]string{
		{"CUSTOMERS", "customers", "customer_id"},
		{"ORDERS", "orders", "order_id"},
		{"LINES", "order_lines", "order_id, line_no"},
		{"PRODUCTS", "products", "product_id"},
	} {
		rows, err := db.Query(conn, fmt.Sprintf("SELECT * FROM %s ORDER BY %s", t[1], t[2]), nil)
		check(err)
		tables.Set(t[0], rows)
	}

	for n, p := range pipelines {
		title, file := p[0], p[1]
		// EXAMPLE-BEGIN run
		program, err := sel.Compile(read("examples/sql-3nf/" + file))
		check(err)
		plan := sql.PlanHybrid(program, "postgresql", schema, sql.Options{})
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
