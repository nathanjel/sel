// Translate a corpus of SEL programs and print one canonical line each.
//
//     go run ./bin/sqlfuzz corpus.selc [dialect] [mode]
//
// The counterpart of js/bin/sqlfuzz.mjs, php/bin/sqlfuzz and python/bin/sqlfuzz.
// The corpus format and the one-line-per-program protocol are specified in tools/README.md.

package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

func escapeNewlines(s string) string {
	var sb strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			sb.WriteString("\\n")
		} else {
			sb.WriteByte(s[i])
		}
	}
	return sb.String()
}

func fuzzBindings() *sql.Bindings {
	orders := sql.RelationBinding("orders", "o", []sql.FieldEntry{
		{Name: "ID", Binding: sql.ColumnBinding("id", "o", sql.KindNum, false, false, false, "", "", false)},
		{Name: "CUSTOMER_ID", Binding: sql.ColumnBinding("customer_id", "o", sql.KindNum, false, false, false, "", "", false)},
		{Name: "AMOUNT", Binding: sql.ColumnBinding("amount", "", sql.KindNum, false, false, false, "", "", false)},
		{Name: "NAME", Binding: sql.ColumnBinding("name", "o", sql.KindText, false, false, false, "", "", false)},
	}, "", "", "", false)
	customers := sql.RelationBinding("customers", "c", []sql.FieldEntry{
		{Name: "ID", Binding: sql.ColumnBinding("id", "c", sql.KindNum, false, false, false, "", "", false)},
		{Name: "NAME", Binding: sql.ColumnBinding("name", "c", sql.KindText, false, false, false, "", "", false)},
	}, "", "", "", false)
	return sql.NewBindings(map[string]*sql.Binding{
		"ORDERS":    orders,
		"CUSTOMERS": customers,
	})
}

func render(f *sql.Fragment) string {
	var binds []string
	for _, v := range f.Bindings() {
		binds = append(binds, v.Dump())
	}
	return f.AsValue(sql.ModeInline) + " | " + f.AsValue(sql.ModeParams) + " | " + strings.Join(binds, ",")
}

func attempt(fn func() string) (res string) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*sql.SqlError); ok {
				res = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
			} else if se, ok := r.(sql.SqlError); ok {
				res = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
			} else if se, ok := r.(*sel.SelError); ok {
				res = fmt.Sprintf("!SEL %s@%d:%d", se.Code, se.Line(), se.Col())
			} else if se, ok := r.(sel.SelError); ok {
				res = fmt.Sprintf("!SEL %s@%d:%d", se.Code, se.Line(), se.Col())
			} else {
				res = fmt.Sprintf("!HOST %v", r)
			}
		}
	}()
	return fn()
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "usage: sqlfuzz corpus.selc [dialect] [mode]\n")
		os.Exit(2)
	}

	path := os.Args[1]
	dialect := "mariadb"
	if len(os.Args) > 2 {
		dialect = os.Args[2]
	}
	mode := "all"
	if len(os.Args) > 3 {
		mode = os.Args[3]
	}

	corpus := harness.SplitCorpus(harness.ReadFile(path))
	bindings := fuzzBindings()

	var out strings.Builder
	for _, src := range corpus {
		program, err := sel.Compile(src)
		if err != nil {
			if _, ok := err.(*sel.SelError); ok {
				out.WriteString("-\n")
			} else {
				out.WriteString(fmt.Sprintf("!HOST %v\n", err))
			}
			continue
		}

		if mode == "statement" {
			line := attempt(func() string {
				return sql.MustTranslateStatement(program, dialect, bindings, sql.Options{}).AsStatement(sql.ModeInline)
			})
			out.WriteString(escapeNewlines(line))
			out.WriteByte('\n')
			continue
		}

		line := attempt(func() string {
			return render(sql.MustTranslate(program, dialect, bindings, sql.Options{}))
		}) + " || " + attempt(func() string {
			return render(sql.MustTranslateStatement(program, dialect, bindings, sql.Options{}))
		}) + " || " + attempt(func() string {
			plan := sql.PlanHybrid(program, dialect, bindings, sql.Options{})
			kind := "hybrid"
			if plan.PureSql {
				kind = "pure_sql"
			} else if plan.PureMemory {
				kind = "pure_memory"
			}
			if plan.SqlStatement != nil {
				return kind + " " + plan.SqlStatement.AsStatement(sql.ModeParams)
			}
			return kind
		})
		out.WriteString(escapeNewlines(line))
		out.WriteByte('\n')
	}

	os.Stdout.WriteString(out.String())
}
