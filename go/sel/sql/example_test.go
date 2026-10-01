package sql_test

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

// A rule as a WHERE condition, with its variables described as columns. In
// params mode every text literal is a placeholder, and Bindings gives the
// values for them, in order.
func ExampleTranslate() {
	column := func(name string, kind sql.SqlKind) *sql.Binding {
		return sql.ColumnBinding(name, "orders", kind, false, false, false, "", "", false)
	}
	bindings := sql.NewBindings(map[string]*sql.Binding{
		"QTY":    column("qty", sql.KindNum),
		"STATUS": column("status", sql.KindText),
	})
	rule := sel.MustCompile(`QTY > 5 AND STATUS $== "open"`)
	fragment, err := sql.Translate(rule, "postgresql", bindings, sql.Options{})
	if err != nil {
		panic(err)
	}
	fmt.Println(fragment.AsCondition(sql.ModeParams))
	for _, v := range fragment.Bindings() {
		fmt.Println(v.AsText(sel.Pos{}))
	}
	// Output:
	// (("orders"."qty" > 5) AND (CAST("orders"."status" AS TEXT) COLLATE "C" = CAST(? AS TEXT) COLLATE "C"))
	// open
}

// A pipeline planned for a database: here every step has an exact SQL form, so
// the whole of it is one statement.
func ExamplePlanHybrid() {
	field := func(name string, kind sql.SqlKind) sql.FieldEntry {
		return sql.FieldEntry{Name: name,
			Binding: sql.ColumnBinding(name, "o", kind, false, false, false, "", "", false)}
	}
	bindings := sql.NewBindings(map[string]*sql.Binding{
		"ORDERS": sql.RelationBinding("orders", "o",
			[]sql.FieldEntry{field("id", sql.KindNum), field("total", sql.KindNum)}, "", "", "", false),
	})
	program := sel.MustCompile(`ORDERS .> FILTER(_["total"] > 100) .> MAP(_["id"])`)
	plan := sql.PlanHybrid(program, "sqlite", bindings, sql.Options{})
	fmt.Println(plan.PureSql, strings.Join(plan.SourceTables, ","))
	fmt.Println(plan.SqlStatement.AsStatement(sql.ModeInline))
	// Output:
	// true orders
	// SELECT "o"."id" FROM "orders" "o" WHERE (CAST("o"."total" AS NUMERIC) > CAST('100' AS NUMERIC))
}
