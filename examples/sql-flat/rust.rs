// An unnormalised export -- one wide table, from Rust.
//
//   tools/check-usage.sh sql-flat              (starts the databases for you)
//
// order_export repeats the customer and the product on every line, the way a
// spreadsheet or a nightly dump does, with the inconsistencies that come with
// it: the same person under two spellings of their name and e-mail. The first
// pipeline groups in SQL. The second normalises e-mails and counts distinct
// customers, and the planner keeps the normalised values out of MariaDB's
// hands: its collation would decide which of them are "the same", and SEL's
// identity is exact bytes. The third explodes a `;`-separated column, which no
// SQL step can express, so it runs in memory entirely.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release -p sel-lang-dev --features usage --example sql-flat` in rust/
// (tools/check-usage.sh does it in its image), which includes examples/lib/db.rs
// and its PostgreSQL, MariaDB and SQLite drivers.

#[path = "../lib/db.rs"]
mod db;

use std::collections::HashMap;
use std::error::Error;
use std::fs;

use sel_lang::sql::{execute_hybrid, plan_hybrid, Binding, Bindings, FieldEntry, Mode, Options, SqlKind};
use sel_lang::{compile, Pos, Value};

fn read(path: &str) -> Result<String, Box<dyn Error>> {
    fs::read_to_string(path).map_err(|e| format!("cannot read {path}: {e}").into())
}

// EXAMPLE-BEGIN bindings
fn relation(table: &str, alias: &str, fields: &[(&str, SqlKind)]) -> Binding {
    let columns = fields
        .iter()
        .map(|&(name, kind)| {
            FieldEntry::new(name, Binding::column(name, alias, kind, false, false, false, "", "", false))
        })
        .collect();
    Binding::relation(table, alias, columns, "", "", "", false)
}

fn schema() -> Bindings {
    use SqlKind::{Num as NUM, Text as TEXT};
    Bindings::new(Some(HashMap::from([
        ("EXPORT".to_string(), relation("order_export", "x", &[("line_id", NUM), ("order_no", TEXT),
                                                               ("order_date", TEXT), ("customer_name", TEXT),
                                                               ("customer_email", TEXT), ("customer_city", TEXT),
                                                               ("sku", TEXT), ("product_name", TEXT),
                                                               ("category", TEXT), ("qty", NUM),
                                                               ("unit_price", NUM), ("tags", TEXT)])),
    ])))
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const PIPELINES: &[(&str, &str)] = &[
    ("revenue per city, February and March", "revenue-per-city.sel"),
    ("distinct customers per city, by normalised e-mail", "customers-per-city.sel"),
    ("lines per tag", "lines-per-tag.sel"),
];

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();
    let schema = schema();
    let mut conn = db::connect("mariadb")?;

    // The same tables in memory, for the comparison at the end of each pipeline.
    let tables = Value::none();
    for (name, table, key) in [("EXPORT", "order_export", "line_id")] {
        tables.set(name, db::query(&mut conn, &format!("SELECT * FROM {table} ORDER BY {key}"), &[])?, at)?;
    }

    for (n, (title, file)) in PIPELINES.iter().enumerate() {
        // EXAMPLE-BEGIN run
        let mut program = compile(&read(&format!("examples/sql-flat/{file}"))?)?;
        let plan = plan_hybrid(&program, "mariadb", Some(&schema), Options::default());
        let rows = execute_hybrid(&plan, db::runner(&mut conn), plan.pure_memory.then_some(&tables))?;
        // EXAMPLE-END run
        let kind = if plan.pure_sql { "pure_sql" } else if plan.pure_memory { "pure_memory" } else { "hybrid" };
        println!("{}. {title}", n + 1);
        println!("   plan        {kind}");
        println!("   reads       {}", plan.source_tables.join(", "));
        if let Some(statement) = &plan.sql_statement {
            println!("   sql         {}", statement.as_statement(Mode::Inline)?);
        }
        println!("{}", db::render(&rows, "   | ")?);
        // A Value is a handle: the program gets a copy, so the tables stay as loaded.
        let same = program.run(Some(tables.deep_copy(1, at)?))?.dump()? == rows.dump()?;
        println!("   in memory   {}", if same { "same rows" } else { "DIFFERENT" });
    }
    Ok(())
}
