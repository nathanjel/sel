// The same report with no database at all -- generated data, in memory, from Go.
//
//   make -C go examples && go/build/example-memory-complex     (from the repository root)
//
// examples/sql-complex loads the support desk from PostgreSQL. Here the same
// rows come from examples/lib/tickets-generate.sel -- a SEL program that builds
// them deterministically, and the source the PostgreSQL seed was rendered from
// -- and the same report runs over them. Nothing below opens a connection:
// the plan for MariaDB is computed from the bindings alone, and it says what it
// said for PostgreSQL, that none of this report is SQL's to answer.
//
// The files beside this one print byte-identical output.
//
// db.Render is the driver-free half of examples/lib/db, so this example builds
// without any database client.

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

// Planning needs the schema, not a server: the bindings sql-complex describes
// PostgreSQL with, asked about MariaDB this time.
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
	// EXAMPLE-BEGIN generate
	data, err := sel.Eval(read("tickets-generate.sel"), nil)
	check(err)
	fmt.Println("1. generated in memory")
	for _, name := range data.Keys() {
		fmt.Printf("   %-10s  %3d rows\n", name, data.Get(name).Size())
	}

	report, err := sel.Compile(read("tickets-report.sel"))
	check(err)
	fmt.Println("2. the report")
	rows, err := report.Run(data)
	check(err)
	text, err := db.Render(rows, "   | ")
	check(err)
	fmt.Println(text)
	// EXAMPLE-END generate

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
	plan := sql.PlanHybrid(report, "mariadb", schema, sql.Options{})
	fmt.Println("3. planned for MariaDB, without connecting")
	shape := "pushed down"
	if plan.PureMemory {
		shape = "pure_memory"
	}
	fmt.Println("   plan       ", shape)
	fmt.Println("   reads      ", strings.Join(plan.SourceTables, ", "))
}
