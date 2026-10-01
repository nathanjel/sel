// The application's own functions, in memory and in PostgreSQL -- Go.
//
//   tools/check-usage.sh sql-functions         (starts the databases for you)
//
// The application registers five functions of its own. Each has a local
// implementation -- the code sel.RegisterFunction runs -- and four also get a
// SQL spelling for PostgreSQL, which the application promises computes the same
// thing (spec §8.1, sql/MAP.md §4.7):
//
//   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
//   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
//   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
//   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
//   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
//   WORDS(title)                returns a list               -> no spelling: stays in memory
//
// Every pipeline prints its plan and its rows, and whether those rows are the rows
// the same program computes in memory -- which is how the example checks that the
// two implementations of each function agree on this data.
//
// The files beside this one print byte-identical output.
//
// Built by `go build -modfile=go.usage.mod -tags usage,libsqlite3 ./sql-functions/go.go`
// in examples/ (tools/check-usage.sh does it in its image), which brings in
// examples/lib/db and its PostgreSQL, MariaDB and SQLite drivers. A spelling is
// registered with sql.Define, whose entry is the dialect map's own JSON shape
// (sql/MAP.md), written here as a map[string]any.
//
// A host function may be called from several goroutines at once. A compiled
// *sel.Program is safe to share between them, so the two that run SEL compile
// theirs once.

package main

import (
	"errors"
	"fmt"
	"os"
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

func read(path string) string {
	text, err := os.ReadFile(path)
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

// EXAMPLE-BEGIN local
func slug(text string) string {
	var out strings.Builder
	dash := false
	for _, c := range text {
		if c >= 'A' && c <= 'Z' { // ASCII only, as SQL's
			c += 'a' - 'A' //         [^a-z0-9]+ sees it
		}
		if (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') {
			if dash && out.Len() > 0 {
				out.WriteByte('-')
			}
			out.WriteRune(c)
			dash = false
		} else {
			dash = true
		}
	}
	return out.String()
}

var (
	margin   = sel.MustCompile("ROUND((PRICE - COST) * 100 / PRICE, 1)")
	shipping = sel.MustCompile(`COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00) * IF(COUNTRY $== "PL", 1, 2)`)
)

// run is a host function's way to evaluate SEL: a failure inside is a
// *sel.SelError, which panicking hands to the run that called the function.
func run(program *sel.Program, ctx *sel.Value) *sel.Value {
	v, err := program.Run(ctx)
	if err != nil {
		panic(err)
	}
	return v
}

func registerFunctions(conn *db.Conn) {
	vatRates, err := db.Query(conn, "SELECT * FROM vat_rates", nil)
	check(err)
	rates := map[[2]string]string{}
	for _, r := range vatRates.Values() {
		rates[[2]string{r.Get("country").AsText(at), r.Get("category").AsText(at)}] = r.Get("rate").AsText(at)
	}

	sel.RegisterFunction("SLUG", 1, 1, func(args *sel.Args) *sel.Value {
		return sel.NewText(slug(args.Text(0)))
	})
	sel.RegisterFunction("MARGIN_PCT", 2, 2, func(args *sel.Args) *sel.Value {
		ctx := sel.NewNone()
		ctx.Set("PRICE", args.Val(0))
		ctx.Set("COST", args.Val(1))
		return run(margin, ctx)
	})
	sel.RegisterFunction("VAT_RATE", 2, 2, func(args *sel.Args) *sel.Value {
		country, category := args.Text(0), args.Text(1)
		rate, ok := rates[[2]string{country, category}]
		if !ok {
			rate, ok = rates[[2]string{country, "*"}]
		}
		if !ok {
			rate = "0"
		}
		return sel.NewText(rate)
	})
	sel.RegisterFunction("SHIPPING_COST", 2, 2, func(args *sel.Args) *sel.Value {
		ctx := sel.NewNone()
		ctx.Set("KG", args.Val(0))
		ctx.Set("COUNTRY", args.Val(1))
		return run(shipping, ctx)
	})
	sel.RegisterFunction("HAS_TAG", 2, 2, func(args *sel.Args) *sel.Value {
		tags, tag := args.Val(0), args.Text(1)
		if tags.Size() == 0 {
			return sel.NewBool(tags.AsText(at) == tag) // a scalar is a list of one
		}
		for _, v := range tags.Values() {
			if v.AsText(at) == tag {
				return sel.NewBool(true)
			}
		}
		return sel.NewBool(false)
	})
	sel.RegisterFunction("WORDS", 1, 1, func(args *sel.Args) *sel.Value {
		var words []*sel.Value
		for _, w := range strings.Split(slug(args.Text(0)), "-") {
			if w != "" {
				words = append(words, sel.NewText(w))
			}
		}
		return sel.NewList(words)
	})
}

// EXAMPLE-END local

func main() {
	conn, err := db.Connect("postgresql")
	check(err)
	defer conn.Close()

	// 1 - the local implementations ------------------------------------------------------
	// What sel.RegisterFunction runs: plain code, or -- where exact decimal
	// arithmetic matters -- a SEL expression, so the local answer has SEL's numbers.

	registerFunctions(conn)

	// 2 - the SQL spellings ------------------------------------------------------------------
	// After the functions: a spelling for a name that is not registered is refused.

	// EXAMPLE-BEGIN spell
	sql.Define("postgresql", "funcs", "SLUG",
		map[string]any{"tpl": "slug({0})", "ret": "TEXT", "args": []string{"TEXT"}})
	sql.Define("postgresql", "funcs", "MARGIN_PCT",
		map[string]any{"tpl": "margin_pct({0}, {1})", "ret": "NUM", "args": []string{"NUM", "NUM"}})
	sql.Define("postgresql", "funcs", "VAT_RATE",
		map[string]any{"tpl": "vat_rate({0}, {1})", "ret": "NUM", "args": []string{"TEXT", "TEXT"}})
	sql.Define("postgresql", "funcs", "SHIPPING_COST",
		map[string]any{"tpl": "shipping_cost({0}, {1})", "ret": "NUM", "args": []string{"NUM", "TEXT"}})
	sql.Define("postgresql", "funcs", "HAS_TAG",
		map[string]any{"tpl": "({1} = ANY(ARRAY[{0}]))", "ret": "BOOL", "args": []string{"LIST", "TEXT"}})
	// WORDS returns a list: no spelling can say that, so it has none.
	// EXAMPLE-END spell

	num, txt := sql.KindNum, sql.KindText
	schema := sql.NewBindings(map[string]*sql.Binding{
		"PRODUCTS": relation("products", "p", field{"product_id", num}, field{"title", txt},
			field{"category", txt}, field{"price", num},
			field{"cost", num}, field{"weight_kg", num},
			field{"tag1", txt}, field{"tag2", txt}, field{"tag3", txt}),
		"ORDERS": relation("orders", "o", field{"order_id", num}, field{"country", txt}),
		"LINES": relation("order_lines", "l", field{"order_id", num}, field{"line_no", num},
			field{"product_id", num}, field{"qty", num}),
	})

	tables := sel.NewNone()
	for _, t := range [][3]string{
		{"PRODUCTS", "products", "product_id"},
		{"ORDERS", "orders", "order_id"},
		{"LINES", "order_lines", "order_id, line_no"},
	} {
		rows, err := db.Query(conn, fmt.Sprintf("SELECT * FROM %s ORDER BY %s", t[1], t[2]), nil)
		check(err)
		tables.Set(t[0], rows)
	}

	// Each pipeline is a .sel file beside this one: {title, file}.
	pipelines := [][2]string{
		{"gifts with a margin of 40% or more", "gifts-by-margin.sel"},
		{"gross revenue and shipping per country", "gross-per-country.sel"},
		{"words in the titles of the better-margin products", "title-words.sel"},
	}

	for n, p := range pipelines {
		title, file := p[0], p[1]
		// EXAMPLE-BEGIN run
		program, err := sel.Compile(read("examples/sql-functions/" + file))
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
		if plan.SqlStatement != nil {
			fmt.Println("   sql        ", plan.SqlStatement.AsStatement(sql.ModeInline))
			caveats := "(none)"
			if len(plan.SqlStatement.Caveats) > 0 {
				caveats = strings.Join(plan.SqlStatement.Caveats, ", ")
			}
			fmt.Println("   caveats    ", caveats)
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

	// 4 - what strict translation says ---------------------------------------------------------
	// A spelling is the application's promise, not this layer's, so strict mode --
	// exact or nothing -- refuses it.

	fmt.Printf("%d. strict translation\n", len(pipelines)+1)
	// EXAMPLE-BEGIN strict
	rule := sel.MustCompile(`SLUG(TITLE) $== "cast-iron-pan"`)
	title := sql.NewBindings(map[string]*sql.Binding{
		"TITLE": sql.ColumnBinding("title", "p", sql.KindText, false, false, false, "", "", false),
	})
	condition, err := sql.Translate(rule, "postgresql", title, sql.Options{})
	check(err)
	fmt.Println("   caveats    ", strings.Join(condition.Caveats, ", "))
	_, err = sql.Translate(rule, "postgresql", title, sql.Options{Strict: true})
	var refused *sql.SqlError
	if errors.As(err, &refused) {
		fmt.Println("   strict     ", refused.Code)
	}
	// EXAMPLE-END strict
}
