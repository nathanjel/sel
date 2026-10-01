// A report no database can take a share of -- SQL loads, SEL computes, from Go.
//
//   tools/check-usage.sh sql-complex           (starts the databases for you)
//
// The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
// numbers out of subjects with RGROUPS and searches the event log for each
// ticket's first answer. sql.PlanHybrid finds no step of it PostgreSQL can
// answer, and says so: pure_memory. So the database's job shrinks to handing
// over the tables -- with the SELECTs written by SEL too, from the same bindings
// -- and the report runs in memory over what came back. The last line checks it
// against the report over the data generated in memory (examples/memory-complex),
// which is where the rows in this database came from.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-complex/go.go`
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

func read(name string) string {
	text, err := os.ReadFile("examples/lib/" + name)
	check(err)
	return string(text)
}

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

func main() {
	// EXAMPLE-BEGIN load
	num, txt := sql.KindNum, sql.KindText
	schema := sql.NewBindings(map[string]*sql.Binding{
		"TEAMS":     relation("teams", "g", field{"team_id", num}, field{"team", txt}),
		"CUSTOMERS": relation("customers", "c", field{"customer_id", num}, field{"customer", txt}, field{"plan", txt}),
		"SLA": relation("sla", "s", field{"plan", txt}, field{"priority", txt},
			field{"respond_within", num}, field{"resolve_within", num}),
		"TICKETS": relation("tickets", "t", field{"ticket_id", num}, field{"customer_id", num},
			field{"team_id", num}, field{"priority", txt}, field{"subject", txt},
			field{"opened_at", num}, field{"closed_at", num}),
		"EVENTS": relation("events", "e", field{"event_id", num}, field{"ticket_id", num},
			field{"seq", num}, field{"at", num}, field{"kind", txt}, field{"actor", txt}),
	})
	keys := map[string]string{
		"TEAMS": "team_id", "CUSTOMERS": "customer_id", "SLA": "plan",
		"TICKETS": "ticket_id", "EVENTS": "event_id",
	}

	report, err := sel.Compile(read("tickets-report.sel"))
	check(err)
	plan := sql.PlanHybrid(report, "postgresql", schema, sql.Options{})
	fmt.Println("1. the report, planned for PostgreSQL")
	shape := "pushed down"
	if plan.PureMemory {
		shape = "pure_memory"
	}
	fmt.Println("   plan       ", shape)
	fmt.Println("   reads      ", strings.Join(plan.SourceTables, ", "))

	fmt.Println("2. so SQL only loads the tables it reads")
	conn, err := db.Connect("postgresql")
	check(err)
	defer conn.Close()
	tables := sel.NewNone()
	for _, name := range report.Dependencies() {
		load, err := sel.Compile(fmt.Sprintf(`%s .> SORT_BY(_["%s"])`, name, keys[name]))
		check(err)
		statement, err := sql.TranslateStatement(load, "postgresql", schema, sql.Options{})
		check(err)
		text := statement.AsStatement(sql.ModeInline)
		rows, err := db.Query(conn, text, nil)
		check(err)
		fmt.Printf("   %-10s  %3d rows  %s\n", name, rows.Size(), text)
		tables.Set(name, rows)
	}

	fmt.Println("3. and SEL computes the report over them")
	result, err := report.Run(tables)
	check(err)
	rendered, err := db.Render(result, "   | ")
	check(err)
	fmt.Println(rendered)
	// EXAMPLE-END load

	generated, err := sel.Eval(read("tickets-generate.sel"), nil)
	check(err)
	again, err := report.Run(generated)
	check(err)
	same := "DIFFERENT"
	if again.Dump() == result.Dump() {
		same = "same report"
	}
	fmt.Println("   over the generated rows:", same)
}
