// The SEL->SQL conformance suite for the Rust host.
//
// Run from the repository root:
//
//     cargo run --release --bin sqlt                 every case
//     cargo run --release --bin sqlt bind. agg.      only cases whose name contains one of these
//     cargo run --release --bin sqlt -- --names      what this host loaded, and stop

use std::collections::HashSet;
use sel_lang::compile;
use sel_lang::sql::{
    plan_hybrid, reset, translate, translate_statement, Binding, Bindings, Fragment, HybridPlan,
    Mode, Options, Part, SqlError,
};
use sel_lang::{Program, Value};

#[path = "sqlt/case_data.rs"]
mod case_data;
use case_data::{SqlCase, SQL_CASES};

fn throws_is_known(name: &str) -> bool {
    name == "LogicException"
}

struct Expected {
    code: String,
    has_pos: bool,
    line: usize,
    col: usize,
}

fn parse_expected(s: &str, at: &str) -> Result<Expected, String> {
    let s = s.trim();
    if let Some(sp) = s.find(' ') {
        let code = &s[..sp];
        let pos = s[sp + 1..].trim();
        if let Some(colon) = pos.find(':') {
            let line = pos[..colon]
                .parse::<usize>()
                .map_err(|_| format!("{}: malformed error expectation", at))?;
            let col = pos[colon + 1..]
                .parse::<usize>()
                .map_err(|_| format!("{}: malformed error expectation", at))?;
            Ok(Expected {
                code: code.to_string(),
                has_pos: true,
                line,
                col,
            })
        } else {
            Err(format!("{}: malformed error expectation", at))
        }
    } else {
        Ok(Expected {
            code: s.to_string(),
            has_pos: false,
            line: 0,
            col: 0,
        })
    }
}

fn count_slots(s: &str) -> usize {
    let bytes = s.as_bytes();
    let mut n = 0;
    let mut i = 0;
    while i + 2 < bytes.len() {
        if bytes[i] == b'~' {
            let mut j = i + 1;
            while j < bytes.len() && bytes[j].is_ascii_digit() {
                j += 1;
            }
            if j > i + 1 && j < bytes.len() && bytes[j] == b'~' {
                n += 1;
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }
    n
}

fn join_dumps(vs: &[Value]) -> String {
    vs.iter().map(|v| v.dump().unwrap_or_default()).collect::<Vec<_>>().join(", ")
}

fn classify(plan: &HybridPlan) -> &'static str {
    if plan.pure_sql {
        "pure_sql"
    } else if plan.pure_memory {
        "pure_memory"
    } else {
        "hybrid"
    }
}

fn parse_mode(mode_name: &str, at: &str) -> Result<Mode, String> {
    Mode::from_name(mode_name).map_err(|error| format!("{}: {}", at, error))
}

fn run_plan_case(
    c: &SqlCase,
    dialect: &str,
    expect_override: Option<&str>,
) -> (Option<String>, Option<String>) {
    let mode_name = c.mode.unwrap_or("inline");
    let mode = match parse_mode(mode_name, c.at) {
        Ok(m) => m,
        Err(e) => return (None, Some(e)),
    };

    let old_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));

    let mut sql_err: Option<SqlError> = None;
    let mut have_error = false;

    let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if let Some(reg) = c.register_fn {
            reg();
        }
        let binds = if let Some(bfn) = c.bindings_fn {
            Bindings::new(Some(bfn()))
        } else {
            Bindings::default()
        };

        let prog = match compile(c.source) {
            Ok(p) => p,
            Err(e) => return Err(format!("the source did not compile: {}", e)),
        };

        let opts = Options {
            strict: c.strict,
            ..Default::default()
        };

        Ok(plan_hybrid(&prog, dialect, Some(&binds), opts))
    }));

    std::panic::set_hook(old_hook);

    let plan = match res {
        Ok(Ok(p)) => Some(p),
        Ok(Err(compile_err)) => {
            return (Some(compile_err), None);
        }
        Err(payload) => {
            if let Some(se) = payload.downcast_ref::<SqlError>() {
                sql_err = Some(se.clone());
                have_error = true;
                None
            } else if let Some(s) = payload.downcast_ref::<&str>() {
                return (None, Some(format!("{}: unexpected throw: {}", c.at, s)));
            } else if let Some(s) = payload.downcast_ref::<String>() {
                return (None, Some(format!("{}: unexpected throw: {}", c.at, s)));
            } else {
                return (None, Some(format!("{}: unexpected throw: panic", c.at)));
            }
        }
    };

    let want_plan = match c.plan {
        Some(p) => p,
        None => return (Some("missing plan expectation".to_string()), None),
    };

    if want_plan == "refused" {
        if !have_error {
            let got_desc = plan.as_ref().map(classify).unwrap_or("none");
            return (
                Some(format!("expected {}, got a {} plan", c.error.unwrap_or(""), got_desc)),
                None,
            );
        }
        let want_str = c.error.unwrap_or("");
        let want = match parse_expected(want_str, c.at) {
            Ok(w) => w,
            Err(e) => return (None, Some(e)),
        };
        let se = sql_err.as_ref().unwrap();
        if se.code != want.code {
            return (
                Some(format!("expected {}, got {} ({})", want.code, se.code, se.message)),
                None,
            );
        }
        return (None, None);
    }

    if have_error {
        let se = sql_err.as_ref().unwrap();
        return (
            Some(format!("expected a {} plan, got {} ({})", want_plan, se.code, se.message)),
            None,
        );
    }

    let plan = plan.unwrap();
    let got = classify(&plan);
    if got != want_plan {
        return (Some(format!("expected a {} plan, got {}", want_plan, got)), None);
    }

    if c.has_tables {
        let tables_match = plan.source_tables.len() == c.tables.len()
            && plan
                .source_tables
                .iter()
                .zip(c.tables.iter())
                .all(|(a, b)| a == b);
        if !tables_match {
            return (
                Some(format!(
                    "source tables got:  [{}]\n     want: [{}]",
                    plan.source_tables.join(", "),
                    c.tables.join(", ")
                )),
                None,
            );
        }
    }

    if plan.dialect != dialect {
        return (Some(format!("plan.dialect is {}, not {}", plan.dialect, dialect)), None);
    }

    if want_plan == "pure_memory" {
        if plan.sql_statement.is_some() {
            return (Some("a pure-memory plan carries a SQL statement".to_string()), None);
        }
        if plan.continuation_program.is_none() {
            return (Some("a pure-memory plan must run the original program".to_string()), None);
        }
    } else {
        if plan.sql_statement.is_none() {
            return (Some(format!("a {} plan has no SQL statement", want_plan)), None);
        }
        if plan.sql_prefix_ast.is_none() {
            return (Some(format!("a {} plan has no SQL prefix AST", want_plan)), None);
        }
        let sql_frag = plan.sql_statement.as_ref().unwrap();
        let sql_str = match sql_frag.as_statement(mode) {
            Ok(s) => s,
            Err(e) => return (Some(format!("as_statement failed: {}", e)), None),
        };
        let want_expect = expect_override.unwrap_or(c.expect.unwrap_or(""));
        if sql_str != want_expect {
            return (Some(format!("got:  {}\n     want: {}", sql_str, want_expect)), None);
        }
        if want_plan == "pure_sql" {
            if plan.continuation_program.is_some() || plan.continuation_ast.is_some() {
                return (Some("a pure-SQL plan carries a continuation".to_string()), None);
            }
            if plan.is_hybrid {
                return (Some("a pure-SQL plan reports is_hybrid".to_string()), None);
            }
        } else {
            if plan.continuation_program.is_none() || plan.continuation_ast.is_none() {
                return (Some("a hybrid plan has no continuation".to_string()), None);
            }
            if !plan.is_hybrid {
                return (Some("a hybrid plan does not report is_hybrid".to_string()), None);
            }
        }
    }

    (None, None)
}

fn run_case(
    c: &SqlCase,
    dialect: &str,
    expect_override: Option<&str>,
) -> (Option<String>, Option<String>) {
    if dialect.is_empty() {
        return (None, Some(format!("{}: case {} has no --- dialect", c.at, c.name)));
    }
    let as_mode = c.as_mode.unwrap_or("value");
    let mode_name = c.mode.unwrap_or("inline");
    let mode = match parse_mode(mode_name, c.at) {
        Ok(m) => m,
        Err(e) => return (None, Some(e)),
    };

    let mut have_sql = false;
    let mut have_error = false;
    let mut have_thrown = false;
    let mut sql_str = String::new();
    let mut thrown_what = String::new();
    let mut sql_err: Option<SqlError> = None;
    let mut frag: Option<Fragment> = None;
    let mut prog: Option<Program> = None;
    let mut binds = Bindings::default();

    let opts = Options {
        strict: c.strict,
        ..Default::default()
    };

    let old_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));

    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if let Some(rf) = c.register_fn {
            rf();
        }
        let b = if let Some(bf) = c.bindings_fn {
            Bindings::new(Some(bf()))
        } else {
            Bindings::default()
        };
        let p = match compile(c.source) {
            Ok(p) => p,
            Err(e) => {
                return Err(format!("the source did not compile: {}", e));
            }
        };
        let f_res = translate(&p, dialect, Some(&b), opts);
        Ok((b, p, f_res))
    }));

    std::panic::set_hook(old_hook);

    match result {
        Ok(Ok((b, p, f_res))) => {
            binds = b;
            prog = Some(p);
            match f_res {
                Ok(f) => {
                    let s_res = match as_mode {
                        "condition" => f.as_condition(mode),
                        "statement" => f.as_statement(mode),
                        _ => f.as_value(mode),
                    };
                    match s_res {
                        Ok(s) => {
                            sql_str = s;
                            have_sql = true;
                            frag = Some(f);
                        }
                        Err(e) => {
                            sql_err = Some(e);
                            have_error = true;
                        }
                    }
                }
                Err(e) => {
                    sql_err = Some(e);
                    have_error = true;
                }
            }
        }
        Ok(Err(compile_err_msg)) => {
            thrown_what = compile_err_msg;
            have_thrown = true;
        }
        Err(payload) => {
            if let Some(se) = payload.downcast_ref::<SqlError>() {
                sql_err = Some(se.clone());
                have_error = true;
            } else {
                have_thrown = true;
                if let Some(s) = payload.downcast_ref::<&str>() {
                    thrown_what = s.to_string();
                } else if let Some(s) = payload.downcast_ref::<String>() {
                    thrown_what = s.clone();
                } else {
                    thrown_what = "panic".to_string();
                }
            }
        }
    }

    if let Some(throws) = c.throws {
        if !throws_is_known(throws) {
            return (
                None,
                Some(format!("{}: no Rust equivalent is recorded for --- throws {}", c.at, throws)),
            );
        }
        if !have_thrown {
            let got = if have_error {
                format!("{}", sql_err.as_ref().unwrap())
            } else {
                sql_str
            };
            return (Some(format!("expected {}, got {}", throws, got)), None);
        }
        return (None, None);
    }
    if have_thrown {
        if thrown_what.starts_with("the source did not compile: ") {
            return (Some(thrown_what), None);
        }
        return (None, Some(format!("{}: unexpected throw: {}", c.at, thrown_what)));
    }

    if as_mode == "statement" && prog.is_some() {
        let p = prog.as_ref().unwrap();
        let mut twin_has_error = false;
        let mut twin_sql = String::new();
        let mut twin_err: Option<SqlError> = None;
        let mut twin_threw = String::new();

        let old_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let twin_res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            translate_statement(p, dialect, Some(&binds), opts)
        }));
        std::panic::set_hook(old_hook);

        match twin_res {
            Ok(Ok(f)) => match f.as_statement(mode) {
                Ok(s) => twin_sql = s,
                Err(e) => {
                    twin_has_error = true;
                    twin_err = Some(e);
                }
            },
            Ok(Err(e)) => {
                twin_has_error = true;
                twin_err = Some(e);
            }
            Err(payload) => {
                if let Some(se) = payload.downcast_ref::<SqlError>() {
                    twin_has_error = true;
                    twin_err = Some(se.clone());
                } else {
                    twin_threw = if let Some(s) = payload.downcast_ref::<&str>() {
                        s.to_string()
                    } else if let Some(s) = payload.downcast_ref::<String>() {
                        s.clone()
                    } else {
                        "panic".to_string()
                    };
                }
            }
        }

        if !twin_threw.is_empty() {
            return (None, Some(format!("{}: translate_statement threw: {}", c.at, twin_threw)));
        }

        let got_desc = |has: bool, e: Option<&SqlError>| -> String {
            if has {
                let err = e.unwrap();
                format!("{} at {}:{}", err.code, err.pos.line, err.pos.col)
            } else {
                "SQL".to_string()
            }
        };

        if have_error || twin_has_error {
            let se = sql_err.as_ref();
            let te = twin_err.as_ref();
            if !have_error
                || !twin_has_error
                || se.unwrap().code != te.unwrap().code
                || se.unwrap().pos.line != te.unwrap().pos.line
                || se.unwrap().pos.col != te.unwrap().pos.col
            {
                return (
                    Some(format!(
                        "translate() gave {} but translate_statement() gave {}",
                        got_desc(have_error, se),
                        got_desc(twin_has_error, te)
                    )),
                    None,
                );
            }
        } else if twin_sql != sql_str {
            return (
                Some(format!(
                    "translate_statement() disagrees with translate():\n     {}\n     {}",
                    twin_sql, sql_str
                )),
                None,
            );
        }
    }

    if let Some(err_spec) = c.error {
        if !have_error {
            return (Some(format!("expected {}, got {}", err_spec, sql_str)), None);
        }
        let want = match parse_expected(err_spec, c.at) {
            Ok(w) => w,
            Err(e) => return (None, Some(e)),
        };
        let se = sql_err.as_ref().unwrap();
        if se.code != want.code {
            return (Some(format!("expected {}, got {} ({})", want.code, se.code, se.message)), None);
        }
        if want.has_pos {
            let got_pos = format!("{}:{}", se.pos.line, se.pos.col);
            let wanted_pos = format!("{}:{}", want.line, want.col);
            if got_pos != wanted_pos {
                return (Some(format!("expected {} at {}, got it at {}", want.code, wanted_pos, got_pos)), None);
            }
        }
        return (None, None);
    }

    if have_error {
        let se = sql_err.as_ref().unwrap();
        return (Some(format!("expected SQL, got {} ({})", se.code, se.message)), None);
    }

    let want_expect = expect_override.unwrap_or(c.expect.unwrap_or(""));
    if !have_sql || sql_str != want_expect {
        return (Some(format!("got:  {}\n     want: {}", sql_str, want_expect)), None);
    }

    let f = frag.as_ref().unwrap();
    let mut seen = HashSet::new();
    for p in &f.parts {
        if let Part::Slot(slot) = p {
            if *slot < 1 || *slot > f.params.len() {
                return (Some(format!("parameter slot {} has no value in params", slot)), None);
            }
            seen.insert(*slot);
        }
    }

    let mut orphans = Vec::new();
    for i in 1..=f.params.len() {
        if !seen.contains(&i) {
            orphans.push(i.to_string());
        }
    }
    if !orphans.is_empty() {
        return (
            Some(format!(
                "parameter slot(s) [{}] were bound but never emitted — a fragment was rendered and discarded",
                orphans.join(", ")
            )),
            None,
        );
    }

    let debug_render = match as_mode {
        "statement" => f.as_statement(Mode::Debug).unwrap_or_default(),
        "condition" => f.as_condition(Mode::Debug).unwrap_or_default(),
        _ => f.as_value(Mode::Debug).unwrap_or_default(),
    };

    if f.bindings().len() != count_slots(&debug_render) {
        return (Some("bindings() and the emitted placeholders disagree in count".to_string()), None);
    }

    if let Some(want_params) = c.params {
        let got_params = join_dumps(&f.bindings());
        if got_params != want_params {
            return (Some(format!("params got:  {}\n     want: {}", got_params, want_params)), None);
        }
    }

    (None, None)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.len() == 1 && args[0] == "--names" {
        for c in SQL_CASES {
            println!("{}\t{}", c.at, c.name);
        }
        return;
    }

    let mut passed = 0;
    let mut mirrored = 0;
    let mut compile_refused = 0;
    let mut suite_errors = 0;
    let mut failures = Vec::new();

    for c in SQL_CASES {
        if !args.is_empty() {
            let mut wanted = false;
            for f in &args {
                if c.name.contains(f) {
                    wanted = true;
                    break;
                }
            }
            if !wanted {
                continue;
            }
        }

        if c.unrepresentable.is_some() {
            passed += 1;
            compile_refused += 1;
            continue;
        }

        reset();
        sel_lang::builtins::reset_host_functions();

        let (problem, s_err) = if c.plan.is_some() {
            run_plan_case(c, c.dialect, None)
        } else {
            run_case(c, c.dialect, None)
        };

        if let Some(err) = s_err {
            println!("SUITE ERROR {}", err);
            suite_errors += 1;
            continue;
        }
        if let Some(prob) = problem {
            failures.push((c.name, c.at, prob));
            continue;
        }
        passed += 1;

        if c.dialect == "mariadb" && c.register_fn.is_none() {
            reset();
            sel_lang::builtins::reset_host_functions();
            let maria_collation = " COLLATE utf8mb4_nopad_bin";
            let mysql_collation = " COLLATE utf8mb4_0900_bin";
            let rep_expect = c.expect.map(|e| e.replace(maria_collation, mysql_collation));

            let (prob, s_err) = if c.plan.is_some() {
                run_plan_case(c, "mysql", rep_expect.as_deref())
            } else {
                run_case(c, "mysql", rep_expect.as_deref())
            };

            if let Some(err) = s_err {
                println!("SUITE ERROR (mirrored to mysql) {}", err);
                suite_errors += 1;
                continue;
            }
            if let Some(prob) = prob {
                failures.push((
                    c.name,
                    c.at,
                    format!("mirrored to mysql, which must agree with {}: {}", c.dialect, prob),
                ));
            } else {
                mirrored += 1;
            }
        }
    }

    for (name, at, prob) in &failures {
        println!("FAIL {}  ({})\n     {}", name, at, prob);
    }

    println!(
        "\n{} passed ({} also checked against a mirrored dialect, {} refused by the type system), {} failed, {} suite errors",
        passed, mirrored, compile_refused, failures.len(), suite_errors
    );

    if !failures.is_empty() || suite_errors > 0 {
        std::process::exit(1);
    }
}
