// A star schema -- whole pipelines in SQL, and split with memory, from Go.
//
//   tools/check-usage.sh sql-star              (starts the databases for you)
//
// fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
// The application describes each table once, as a relation binding, and then
// hands SEL whole pipelines. sql.PlanHybrid decides how much of each one the
// database can answer: all of it (pure_sql), a prefix of it (hybrid, the rest
// runs in memory over the rows the prefix returned), or none of it
// (pure_memory). The answer is checked against a run of the same program over
// the tables loaded into memory.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-star/go.go`
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
		"SALES": relation("fact_sales", "s", field{"sale_id", num}, field{"date_key", num},
			field{"product_key", num}, field{"store_key", num}, field{"qty", num}, field{"revenue", num}),
		"DATES": relation("dim_date", "d", field{"date_key", num}, field{"year", num}, field{"quarter", num},
			field{"month", num}, field{"month_name", txt}),
		"STORES": relation("dim_store", "t", field{"store_key", num}, field{"city", txt},
			field{"region", txt}, field{"format", txt}),
		"PRODUCTS": relation("dim_product", "p", field{"product_key", num}, field{"sku", txt},
			field{"name", txt}, field{"category", txt}, field{"brand", txt}, field{"list_price", num}),
	})
}

// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: {title, file}.
var pipelines = [][2]string{
	{"revenue by category, first quarter", "revenue-by-category.sel"},
	{"best-selling product per region, stores only", "best-product-per-region.sel"},
}

func main() {
	schema := schema()
	conn, err := db.Connect("postgresql")
	check(err)
	defer conn.Close()

	// The same tables in memory, for the comparison at the end of each pipeline.
	tables := sel.NewNone()
	for _, t := range [][3]string{
		{"SALES", "fact_sales", "sale_id"},
		{"DATES", "dim_date", "date_key"},
		{"STORES", "dim_store", "store_key"},
		{"PRODUCTS", "dim_product", "product_key"},
	} {
		rows, err := db.Query(conn, fmt.Sprintf("SELECT * FROM %s ORDER BY %s", t[1], t[2]), nil)
		check(err)
		tables.Set(t[0], rows)
	}

	for n, p := range pipelines {
		title, file := p[0], p[1]
		// EXAMPLE-BEGIN run
		program, err := sel.Compile(read("examples/sql-star/" + file))
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
