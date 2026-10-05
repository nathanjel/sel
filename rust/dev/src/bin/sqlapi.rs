// SQL API parity probe: the planner's contract through every host's own SQL
// binding. tools/check-sqlapi.sh diffs the reports of the hosts that carry the
// SQL layer (the JS bundles do not; they print nothing and are left out).

use std::sync::Arc;

use sel_lang::sql::{
    define, define_builder, define_dialect, plan_hybrid, reset, translate, Binding, Bindings,
    BuilderFn, Emit, FieldEntry, Fragment, HybridPlan, Mode, Options, Part, SqlError, SqlKind,
};
use sel_lang::{compile, evaluate, register_function, Pos, SelError, Value};
use sel_lang_dev::{b, say};

fn attempt(f: impl FnOnce()) -> String {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
    match result {
        Ok(()) => "accepted".to_string(),
        Err(e) => {
            if let Some(sql_err) = e.downcast_ref::<SqlError>() {
                return format!("SqlError {}", sql_err.code);
            }
            if let Some(sel_err) = e.downcast_ref::<SelError>() {
                return format!("SqlError {}", sel_err.code);
            }
            "refused".to_string()
        }
    }
}

fn refuses(f: impl FnOnce()) -> String {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(f));
    match result {
        Ok(()) => "accepted".to_string(),
        Err(_) => "refused".to_string(),
    }
}

fn mode_named(name: &str) -> Mode {
    Mode::from_name(name).unwrap_or_else(|error| std::panic::panic_any(error))
}

fn main() {
    std::panic::set_hook(Box::new(|_| {}));
    let mut out: Vec<String> = Vec::new();
    let mut counter: usize = 0;

    let bindings = {
        let mut m = std::collections::HashMap::new();
        m.insert(
            "ORDERS".to_string(),
            Binding::relation(
                "orders",
                "o",
                vec![
                    FieldEntry::new("ID", Binding::column("id", "o", SqlKind::Num, false, false, false, "", "", false)),
                    FieldEntry::new("CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num, false, false, false, "", "", false)),
                    FieldEntry::new("AMOUNT", Binding::column("amount", "o", SqlKind::Num, false, false, false, "", "", false)),
                    FieldEntry::new("NAME", Binding::column("name", "o", SqlKind::Text, false, false, false, "", "", false)),
                ],
                "",
                "",
                "",
                false,
            ),
        );
        m.insert(
            "CUSTOMERS".to_string(),
            Binding::relation(
                "customers",
                "c",
                vec![
                    FieldEntry::new("ID", Binding::column("id", "c", SqlKind::Num, false, false, false, "", "", false)),
                    FieldEntry::new("NAME", Binding::column("name", "c", SqlKind::Text, false, false, false, "", "", false)),
                ],
                "",
                "",
                "",
                false,
            ),
        );
        Bindings::new(Some(m))
    };

    // --- plan_hybrid probes ---
    let probe = |label: &str, source: &str, counter: &mut usize, out: &mut Vec<String>| {
        let program = compile(source).unwrap_or_else(|e| std::panic::panic_any(e));
        let plan: HybridPlan = plan_hybrid(&program, "mariadb", Some(&bindings), Options::default());

        let kind = if plan.pure_sql {
            "pure_sql"
        } else if plan.pure_memory {
            "pure_memory"
        } else {
            "hybrid"
        };
        say(counter, out, &format!("plan.{}.kind", label), kind);

        let dialect = if plan.dialect.is_empty() { "-" } else { &plan.dialect };
        say(counter, out, &format!("plan.{}.dialect", label), dialect);

        let stmt = match &plan.sql_statement {
            Some(f) => f.as_statement(Mode::Inline).unwrap_or("-".to_string()),
            None => "-".to_string(),
        };
        say(counter, out, &format!("plan.{}.statement", label), &stmt);

        say(counter, out, &format!("plan.{}.prefix.present", label), b(plan.sql_prefix_ast.is_some()));
        say(counter, out, &format!("plan.{}.continuation.present", label), b(plan.continuation_ast.is_some()));

        let deps = match &plan.continuation_program {
            Some(p) => {
                let d = p.dependencies().unwrap_or_else(|e| std::panic::panic_any(e));
                if d.is_empty() { "-".to_string() } else { d.join(" ") }
            }
            None => "-".to_string(),
        };
        say(counter, out, &format!("plan.{}.continuation.deps", label), &deps);

        say(counter, out, &format!("plan.{}.source.var", label), &plan.continuation_source_var);

        let tables = if plan.source_tables.is_empty() {
            "-".to_string()
        } else {
            plan.source_tables.join(",")
        };
        say(counter, out, &format!("plan.{}.tables", label), &tables);

        let sm = if plan.selected_member.is_some() { "present" } else { "-" };
        say(counter, out, &format!("plan.{}.selected.member", label), sm);
    };

    probe("sql", "ORDERS .> FILTER(_[\"AMOUNT\"] > 10) .> MAP(RECORD(\"id\", _[\"ID\"], \"amount\", _[\"AMOUNT\"]))", &mut counter, &mut out);
    probe("hybrid", "ORDERS .> SORT_BY(_[\"AMOUNT\"]) .> FILTER(_K > 1)", &mut counter, &mut out);
    probe("memory", "A += 1; ORDERS .> TAKE(1)", &mut counter, &mut out);

    // --- fragment probes ---
    let fragment_probe = |label: &str, dialect: &str, source: &str, counter: &mut usize, out: &mut Vec<String>| {
        let program = compile(source).unwrap_or_else(|e| std::panic::panic_any(e));
        let f = translate(&program, dialect, Some(&bindings), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
        say(counter, out, &format!("fragment.{}.kind", label), f.kind.as_str());
        say(counter, out, &format!("fragment.{}.canonical", label), b(f.canonical));
        let caveats = if f.caveats.is_empty() { "-".to_string() } else { f.caveats.join(",") };
        say(counter, out, &format!("fragment.{}.caveats", label), &caveats);
    };

    fragment_probe("canon.postgresql", "postgresql", "CANON(1.50)", &mut counter, &mut out);
    fragment_probe("canon.mariadb", "mariadb", "CANON(1.50)", &mut counter, &mut out);
    fragment_probe("canon.sqlite", "sqlite", "CANON(1.50)", &mut counter, &mut out);
    fragment_probe("abs.postgresql", "postgresql", "ABS(1.50)", &mut counter, &mut out);

    // --- host functions with a SQL spelling (spec §8.1, sql/MAP.md §4.7) ---
    let host = {
        let mut m = std::collections::HashMap::new();
        m.insert(
            "T".to_string(),
            Binding::column("title", "t", SqlKind::Text, false, false, false, "", "", false),
        );
        Bindings::new(Some(m))
    };

    let local_slug = |a: &mut sel_lang::Args| -> Result<Value, SelError> {
        Ok(Value::text_owned(format!("local:{}", a.text(0)?)))
    };

    // Before registering HSLUG as a host function, trying to define it should fail
    say(&mut counter, &mut out, "host.spell.before-register", &attempt(|| {
        let spec = serde_json::json!({"tpl": "slug({0})", "ret": "TEXT"});
        define("postgresql", "funcs", "HSLUG", &spec);
    }));

    // Register the host function, then define its SQL spelling
    register_function("HSLUG", 1, 1, local_slug).expect("HSLUG registers");
    {
        let spec = serde_json::json!({"tpl": "slug({0})", "ret": "TEXT", "args": ["TEXT"]});
        define("postgresql", "funcs", "HSLUG", &spec);
    }

    let spelled = translate(&compile("HSLUG(T) $== \"x\"").unwrap_or_else(|e| std::panic::panic_any(e)), "postgresql", Some(&host), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    say(&mut counter, &mut out, "host.spell.condition", &spelled.as_condition(Mode::Inline).unwrap_or_else(|e| std::panic::panic_any(e)));
    let caveats = if spelled.caveats.is_empty() { "-".to_string() } else { spelled.caveats.join(",") };
    say(&mut counter, &mut out, "host.spell.caveats", &caveats);

    // Strict mode should refuse a host function
    say(&mut counter, &mut out, "host.spell.strict", &attempt(|| {
        translate(
            &compile("HSLUG(T)").unwrap_or_else(|e| std::panic::panic_any(e)),
            "postgresql",
            Some(&host),
            Options { strict: true },
        ).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // Other dialect should refuse
    say(&mut counter, &mut out, "host.spell.other-dialect", &attempt(|| {
        translate(
            &compile("HSLUG(T)").unwrap_or_else(|e| std::panic::panic_any(e)),
            "mariadb",
            Some(&host),
            Options::default(),
        ).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // LIST argument
    register_function("HHAS", 2, 2, |_a: &mut sel_lang::Args| -> Result<Value, SelError> {
        Ok(Value::bool(false))
    })
    .expect("HHAS registers");
    {
        let spec = serde_json::json!({"tpl": "({1} = ANY(ARRAY[{0}]))", "ret": "BOOL", "args": ["LIST", "TEXT"]});
        define("postgresql", "funcs", "HHAS", &spec);
    }

    let listed = translate(
        &compile("HHAS((\"a\", \"b\"), \"c\")").unwrap_or_else(|e| std::panic::panic_any(e)),
        "postgresql",
        Some(&host),
        Options::default(),
    ).unwrap_or_else(|e| std::panic::panic_any(e));
    say(&mut counter, &mut out, "host.spell.list.params", &listed.as_condition(Mode::Params).unwrap_or_else(|e| std::panic::panic_any(e)));
    let bound: Vec<String> = listed.bindings().iter().map(|v| v.dump().unwrap_or_default()).collect();
    say(&mut counter, &mut out, "host.spell.list.bound", &bound.join(","));

    // Builder
    register_function("HWRAP", 1, 1, |a: &mut sel_lang::Args| -> Result<Value, SelError> {
        Ok(a.val(0)?.clone())
    })
    .expect("HWRAP registers");
    {
        let builder: BuilderFn = Arc::new(|emit: &Emit, args: &[Fragment], _pos: Pos| -> Result<Fragment, SqlError> {
            let mut parts: Vec<Part> = vec![Part::Sql("wrap(".to_string())];
            for p in &args[0].parts {
                parts.push(p.clone());
            }
            parts.push(Part::Sql(")".to_string()));
            Ok(Fragment::new(
                parts,
                SqlKind::Text,
                emit.dialect(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ))
        });
        define_builder("postgresql", "funcs", "HWRAP", builder);
    }
    say(&mut counter, &mut out, "host.spell.builder",
        &translate(&compile("HWRAP(T)").unwrap_or_else(|e| std::panic::panic_any(e)), "postgresql", Some(&host), Options::default())
            .unwrap_or_else(|e| std::panic::panic_any(e))
            .as_value(Mode::Inline)
            .unwrap_or_else(|e| std::panic::panic_any(e)));

    // Re-register HSLUG with wider arity [1,2] — SQL should refuse because arity mismatch
    register_function("HSLUG", 1, 2, local_slug).expect("HSLUG re-registers");
    say(&mut counter, &mut out, "host.spell.reregistered-arity", &attempt(|| {
        translate(&compile("HSLUG(T)").unwrap_or_else(|e| std::panic::panic_any(e)), "postgresql", Some(&host), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // Restore original arity [1,1]
    register_function("HSLUG", 1, 1, local_slug).expect("HSLUG re-registers");
    say(&mut counter, &mut out, "host.spell.arity-restored", &attempt(|| {
        translate(&compile("HSLUG(T)").unwrap_or_else(|e| std::panic::panic_any(e)), "postgresql", Some(&host), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // Reset SQL map — should remove all host spellings
    reset();
    say(&mut counter, &mut out, "host.spell.after-reset", &attempt(|| {
        translate(&compile("HSLUG(T)").unwrap_or_else(|e| std::panic::panic_any(e)), "postgresql", Some(&host), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // Host function still works after SQL map reset
    say(&mut counter, &mut out, "host.spell.after-reset.local",
        &evaluate("HSLUG(\"A\")", None).unwrap_or_else(|e| std::panic::panic_any(e)).as_text(Pos::default()).unwrap_or_else(|e| std::panic::panic_any(e)));

    // --- rendering and registration state (T10) ---
    let one_bindings = {
        let mut m = std::collections::HashMap::new();
        m.insert(
            "C".to_string(),
            Binding::column("c", "t", SqlKind::Num, false, false, false, "", "", false),
        );
        Bindings::new(Some(m))
    };

    let zero = translate(&compile("C > C").unwrap_or_else(|e| std::panic::panic_any(e)), "mariadb", Some(&one_bindings), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    let onelit = translate(&compile("C > 1").unwrap_or_else(|e| std::panic::panic_any(e)), "mariadb", Some(&one_bindings), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));

    say(&mut counter, &mut out, "render.mode.valid.zero-slots", &refuses(|| {
        zero.as_value(mode_named("params")).unwrap_or_else(|e| std::panic::panic_any(e));
    }));
    say(&mut counter, &mut out, "render.mode.bogus.zero-slots", &refuses(|| {
        zero.as_value(mode_named("bogus")).unwrap_or_else(|e| std::panic::panic_any(e));
    }));
    say(&mut counter, &mut out, "render.mode.bogus.zero-slots.condition", &refuses(|| {
        zero.as_condition(mode_named("bogus")).unwrap_or_else(|e| std::panic::panic_any(e));
    }));
    say(&mut counter, &mut out, "render.mode.bogus.with-slot", &refuses(|| {
        translate(&compile("C > \"x\"").unwrap_or_else(|e| std::panic::panic_any(e)), "mariadb", Some(&one_bindings), Options::default())
            .unwrap_or_else(|e| std::panic::panic_any(e))
            .as_value(mode_named("bogus"))
            .unwrap_or_else(|e| std::panic::panic_any(e));
    }));
    say(&mut counter, &mut out, "render.mode.bogus.literal-number", &refuses(|| {
        onelit.as_value(mode_named("bogus")).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    // Guard reuse
    {
        let spec = serde_json::json!({
            "extends": "postgresql",
            "version": "16",
            "lexical": {
                "numericGuard": "CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END"
            }
        });
        define_dialect("probe-badguard", &spec);
    }

    let named = {
        let mut m = std::collections::HashMap::new();
        m.insert(
            "N".to_string(),
            Binding::column("n", "t", SqlKind::Text, false, false, false, "", "", false),
        );
        Bindings::new(Some(m))
    };

    for k in 1..=3 {
        let label = format!("guard.reuse.{}", k);
        say(&mut counter, &mut out, &label, &refuses(|| {
            translate(&compile("N + 1").unwrap_or_else(|e| std::panic::panic_any(e)), "probe-badguard", Some(&named), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
        }));
    }

    reset();
    say(&mut counter, &mut out, "guard.reuse.after-reset", &refuses(|| {
        translate(&compile("N + 1").unwrap_or_else(|e| std::panic::panic_any(e)), "probe-badguard", Some(&named), Options::default()).unwrap_or_else(|e| std::panic::panic_any(e));
    }));

    println!("{}", out.join("\n"));
}
