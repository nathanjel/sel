// SQL API parity probe: the planner's contract through every host's own SQL binding.

package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

var probes harness.Probes

func say(name, value string) { probes.Say(name, value) }

var b = harness.Bool

func attempt(fn func()) string {
	var res string
	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sql.SqlError); ok {
					res = "SqlError " + se.Code
				} else if se, ok := r.(*sel.SelError); ok {
					res = "SqlError " + se.Code
				} else {
					res = "refused"
				}
			}
		}()
		fn()
		res = "accepted"
	}()
	return res
}

func main() {
	bindings := sql.NewBindings(map[string]*sql.Binding{
		"ORDERS": sql.RelationBinding("orders", "o", map[string]*sql.Binding{
			"ID":          sql.ColumnBinding("id", "o", sql.KindNum, false, false, false, "", "", false),
			"CUSTOMER_ID": sql.ColumnBinding("customer_id", "o", sql.KindNum, false, false, false, "", "", false),
			"AMOUNT":      sql.ColumnBinding("amount", "o", sql.KindNum, false, false, false, "", "", false),
			"NAME":        sql.ColumnBinding("name", "o", sql.KindText, false, false, false, "", "", false),
		}, "", "", "", false),
		"CUSTOMERS": sql.RelationBinding("customers", "c", map[string]*sql.Binding{
			"ID":   sql.ColumnBinding("id", "c", sql.KindNum, false, false, false, "", "", false),
			"NAME": sql.ColumnBinding("name", "c", sql.KindText, false, false, false, "", "", false),
		}, "", "", "", false),
	})

	probe := func(label, source string) {
		program := sel.MustCompile(source)
		plan := sql.PlanHybrid(program, "mariadb", bindings, sql.Options{})
		kind := "hybrid"
		if plan.PureSql {
			kind = "pure_sql"
		} else if plan.PureMemory {
			kind = "pure_memory"
		}
		say("plan."+label+".kind", kind)
		d := plan.Dialect
		if d == "" {
			d = "-"
		}
		say("plan."+label+".dialect", d)
		stmt := "-"
		if plan.SqlStatement != nil {
			stmt = plan.SqlStatement.AsStatement(sql.ModeInline)
		}
		say("plan."+label+".statement", stmt)
		say("plan."+label+".prefix.present", b(plan.SqlPrefixAst != nil))
		say("plan."+label+".continuation.present", b(plan.ContinuationAst != nil))
		deps := "-"
		if plan.ContinuationProgram != nil {
			depsList := plan.ContinuationProgram.Dependencies()
			if len(depsList) > 0 {
				deps = strings.Join(depsList, " ")
			}
		}
		say("plan."+label+".continuation.deps", deps)
		say("plan."+label+".source.var", plan.ContinuationSourceVar)
		tables := "-"
		if len(plan.SourceTables) > 0 {
			tables = strings.Join(plan.SourceTables, ",")
		}
		say("plan."+label+".tables", tables)
		sm := "-"
		if plan.SelectedMember != nil {
			sm = "present"
		}
		say("plan."+label+".selected.member", sm)
	}

	probe("sql", "ORDERS .> FILTER(_[\"AMOUNT\"] > 10) .> MAP(RECORD(\"id\", _[\"ID\"], \"amount\", _[\"AMOUNT\"]))")
	probe("hybrid", "ORDERS .> SORT_BY(_[\"AMOUNT\"]) .> FILTER(_K > 1)")
	probe("memory", "A += 1; ORDERS .> TAKE(1)")

	fragmentProbe := func(label, dialect, source string) {
		f := sql.MustTranslate(sel.MustCompile(source), dialect, bindings, sql.Options{})
		say("fragment."+label+".kind", f.Kind.String())
		say("fragment."+label+".canonical", b(f.Canonical))
		caveats := "-"
		if len(f.Caveats) > 0 {
			caveats = strings.Join(f.Caveats, ",")
		}
		say("fragment."+label+".caveats", caveats)
	}

	fragmentProbe("canon.postgresql", "postgresql", "CANON(1.50)")
	fragmentProbe("canon.mariadb", "mariadb", "CANON(1.50)")
	fragmentProbe("canon.sqlite", "sqlite", "CANON(1.50)")
	fragmentProbe("abs.postgresql", "postgresql", "ABS(1.50)")

	host := sql.NewBindings(map[string]*sql.Binding{
		"T": sql.ColumnBinding("title", "t", sql.KindText, false, false, false, "", "", false),
	})

	localSlug := func(a *sel.Args) *sel.Value {
		return sel.NewText("local:" + a.Text(0))
	}

	say("host.spell.before-register", attempt(func() {
		sql.Define("postgresql", "funcs", "HSLUG", map[string]interface{}{
			"tpl": "slug({0})",
			"ret": "TEXT",
		})
	}))

	sel.RegisterFunction("HSLUG", 1, 1, localSlug)
	sql.Define("postgresql", "funcs", "HSLUG", map[string]interface{}{
		"tpl":  "slug({0})",
		"ret":  "TEXT",
		"args": []string{"TEXT"},
	})

	spelled := sql.MustTranslate(sel.MustCompile("HSLUG(T) $== \"x\""), "postgresql", host, sql.Options{})
	say("host.spell.condition", spelled.AsCondition(sql.ModeInline))
	hostCaveats := "-"
	if len(spelled.Caveats) > 0 {
		hostCaveats = strings.Join(spelled.Caveats, ",")
	}
	say("host.spell.caveats", hostCaveats)

	say("host.spell.strict", attempt(func() {
		sql.MustTranslate(sel.MustCompile("HSLUG(T)"), "postgresql", host, sql.Options{Strict: true})
	}))

	say("host.spell.other-dialect", attempt(func() {
		sql.MustTranslate(sel.MustCompile("HSLUG(T)"), "mariadb", host, sql.Options{})
	}))

	sel.RegisterFunction("HHAS", 2, 2, func(a *sel.Args) *sel.Value {
		return sel.NewBool(false)
	})
	sql.Define("postgresql", "funcs", "HHAS", map[string]interface{}{
		"tpl":  "({1} = ANY(ARRAY[{0}]))",
		"ret":  "BOOL",
		"args": []string{"LIST", "TEXT"},
	})

	listed := sql.MustTranslate(sel.MustCompile("HHAS((\"a\", \"b\"), \"c\")"), "postgresql", host, sql.Options{})
	say("host.spell.list.params", listed.AsCondition(sql.ModeParams))
	var bound []string
	for _, v := range listed.Bindings() {
		bound = append(bound, v.Dump())
	}
	say("host.spell.list.bound", strings.Join(bound, ","))

	sel.RegisterFunction("HWRAP", 1, 1, func(a *sel.Args) *sel.Value {
		return a.Val(0).Clone()
	})
	sql.DefineBuilder("postgresql", "funcs", "HWRAP", func(emit *sql.Emit, args []*sql.Fragment, pos sel.Pos) *sql.Fragment {
		parts := []sql.Part{{Sql: "wrap("}}
		parts = append(parts, args[0].Parts...)
		parts = append(parts, sql.Part{Sql: ")"})
		return sql.NewFragment(parts, sql.KindText, emit.Dialect(), nil, nil, nil)
	})
	say("host.spell.builder", sql.MustTranslate(sel.MustCompile("HWRAP(T)"), "postgresql", host, sql.Options{}).AsValue(sql.ModeInline))

	sel.RegisterFunction("HSLUG", 1, 2, localSlug)
	say("host.spell.reregistered-arity", attempt(func() {
		sql.MustTranslate(sel.MustCompile("HSLUG(T)"), "postgresql", host, sql.Options{})
	}))

	sel.RegisterFunction("HSLUG", 1, 1, localSlug)
	say("host.spell.arity-restored", attempt(func() {
		sql.MustTranslate(sel.MustCompile("HSLUG(T)"), "postgresql", host, sql.Options{})
	}))

	sql.Reset()
	say("host.spell.after-reset", attempt(func() {
		sql.MustTranslate(sel.MustCompile("HSLUG(T)"), "postgresql", host, sql.Options{})
	}))

	say("host.spell.after-reset.local", sel.MustEval("HSLUG(\"A\")", nil).AsText(sel.Pos{}))

	// --- rendering and registration state ---------------------------------------
	// The questions a snapshot of ONE translation cannot ask: what an unknown
	// render mode does when there is nothing to bind, and whether a refused
	// dialect stays refused. refuses collapses the host's own failure classes,
	// which differ, to the one thing the contract says: the call did not produce
	// SQL. Here a mode is an int, so an unknown NAME is refused where a name
	// becomes a mode (ModeFromName).
	refuses := func(fn func()) (res string) {
		defer func() {
			if r := recover(); r != nil {
				res = "refused"
			}
		}()
		fn()
		return "accepted"
	}
	one := sql.NewBindings(map[string]*sql.Binding{
		"C": sql.ColumnBinding("c", "t", sql.KindNum, false, false, false, "", "", false),
	})
	zero := sql.MustTranslate(sel.MustCompile("C > C"), "mariadb", one, sql.Options{})
	onelit := sql.MustTranslate(sel.MustCompile("C > 1"), "mariadb", one, sql.Options{})
	say("render.mode.valid.zero-slots", refuses(func() { zero.AsValue(sql.ModeFromName("params")) }))
	say("render.mode.bogus.zero-slots", refuses(func() { zero.AsValue(sql.ModeFromName("bogus")) }))
	say("render.mode.bogus.zero-slots.condition", refuses(func() { zero.AsCondition(sql.ModeFromName("bogus")) }))
	say("render.mode.bogus.with-slot", refuses(func() {
		sql.MustTranslate(sel.MustCompile("C > \"x\""), "mariadb", one, sql.Options{}).AsValue(sql.ModeFromName("bogus"))
	}))
	say("render.mode.bogus.literal-number", refuses(func() { onelit.AsValue(sql.ModeFromName("bogus")) }))
	sql.DefineDialect("probe-badguard", map[string]interface{}{
		"extends": "postgresql",
		"version": "16",
		"lexical": map[string]interface{}{
			"numericGuard": "CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END",
		},
	})
	named := sql.NewBindings(map[string]*sql.Binding{
		"N": sql.ColumnBinding("n", "t", sql.KindText, false, false, false, "", "", false),
	})
	for k := 1; k <= 3; k++ {
		say(fmt.Sprintf("guard.reuse.%d", k), refuses(func() {
			sql.MustTranslate(sel.MustCompile("N + 1"), "probe-badguard", named, sql.Options{})
		}))
	}
	sql.Reset()
	say("guard.reuse.after-reset", refuses(func() {
		sql.MustTranslate(sel.MustCompile("N + 1"), "probe-badguard", named, sql.Options{})
	}))

	os.Stdout.WriteString(probes.Text())
}
