//! Shared SQL corpus protocol: expression, statement and hybrid-plan lanes.
use sel_lang::sql::{
    self, Binding, Bindings, FieldEntry, Fragment, Mode, Options, SqlError, SqlKind,
};
use sel_lang::{compile, SelError};
use std::collections::HashMap;
use sel_lang_dev::{read_corpus, read_text};
use std::io::{self, Write};

fn bindings() -> Bindings {
    let column =
        |name, table, kind| Binding::column(name, table, kind, false, false, false, "", "", false);
    Bindings::new(Some(HashMap::from([
        (
            "ORDERS".into(),
            Binding::relation(
                "orders",
                "o",
                vec![
                    FieldEntry::new("ID", column("id", "o", SqlKind::Num)),
                    FieldEntry::new("CUSTOMER_ID", column("customer_id", "o", SqlKind::Num)),
                    FieldEntry::new("AMOUNT", column("amount", "", SqlKind::Num)),
                    FieldEntry::new("NAME", column("name", "o", SqlKind::Text)),
                ],
                "",
                "",
                "",
                false,
            ),
        ),
        (
            "CUSTOMERS".into(),
            Binding::relation(
                "customers",
                "c",
                vec![
                    FieldEntry::new("ID", column("id", "c", SqlKind::Num)),
                    FieldEntry::new("NAME", column("name", "c", SqlKind::Text)),
                ],
                "",
                "",
                "",
                false,
            ),
        ),
    ])))
}

fn render(fragment: Fragment) -> Result<String, SqlError> {
    let inline = fragment.as_value(Mode::Inline)?;
    let params = fragment.as_value(Mode::Params)?;
    let values: Vec<_> = fragment
        .bindings()
        .iter()
        .map(|value| {
            value
                .dump()
                .unwrap_or_else(|error| std::panic::panic_any(error))
        })
        .collect();
    Ok(format!("{inline} | {params} | {}", values.join(",")))
}

fn sql_error(error: &SqlError) -> String {
    format!("!{}@{}:{}", error.code, error.pos.line, error.pos.col)
}

fn attempt(f: impl FnOnce() -> Result<String, SqlError>) -> String {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => sql_error(&error),
        Err(error) => {
            if let Some(error) = error.downcast_ref::<SqlError>() {
                sql_error(error)
            } else if let Some(error) = error.downcast_ref::<SelError>() {
                format!("!SEL {}@{}:{}", error.code, error.pos.line, error.pos.col)
            } else {
                let message = error
                    .downcast_ref::<String>()
                    .map(String::as_str)
                    .or_else(|| error.downcast_ref::<&str>().copied())
                    .unwrap_or("panic");
                format!("!HOST {message}")
            }
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    let path = args
        .first()
        .ok_or("usage: sqlfuzz corpus.selc [dialect] [mode]")?;
    let dialect = args
        .get(1)
        .map(|s| s.to_string_lossy())
        .unwrap_or("mariadb".into());
    let mode = args
        .get(2)
        .map(|s| s.to_string_lossy())
        .unwrap_or("all".into());
    let text = read_text(path)?;
    let corpus = read_corpus(&text);
    if corpus.is_empty() {
        return Err(format!("{}: no records", std::path::Path::new(path).display()).into());
    }
    let bindings = bindings();
    let mut out = io::BufWriter::new(io::stdout().lock());
    for source in corpus {
        let program = match compile(&source) {
            Ok(program) => program,
            Err(_) => {
                writeln!(out, "-")?;
                continue;
            }
        };
        let line = if mode == "statement" {
            attempt(|| {
                sql::translate_statement(&program, &dialect, Some(&bindings), Options::default())?
                    .as_statement(Mode::Inline)
            })
        } else {
            let value = attempt(|| {
                render(sql::translate(
                    &program,
                    &dialect,
                    Some(&bindings),
                    Options::default(),
                )?)
            });
            let statement = attempt(|| {
                render(sql::translate_statement(
                    &program,
                    &dialect,
                    Some(&bindings),
                    Options::default(),
                )?)
            });
            let hybrid = attempt(|| {
                let plan =
                    sql::plan_hybrid(&program, &dialect, Some(&bindings), Options::default());
                let kind = if plan.pure_sql {
                    "pure_sql"
                } else if plan.pure_memory {
                    "pure_memory"
                } else {
                    "hybrid"
                };
                match plan.sql_statement {
                    Some(fragment) => {
                        Ok(format!("{kind} {}", fragment.as_statement(Mode::Params)?))
                    }
                    None => Ok(kind.into()),
                }
            });
            format!("{value} || {statement} || {hybrid}")
        };
        writeln!(out, "{}", line.replace('\n', "\\n"))?;
    }
    out.flush()?;
    Ok(())
}

fn main() {
    // Panics caught by attempt belong to the corpus protocol's !HOST lane.
    std::panic::set_hook(Box::new(|_| {}));
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
