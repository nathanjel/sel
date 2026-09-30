// An entity-attribute-value catalogue -- pipelines over EAV rows, from Rust.
//
//   tools/check-usage.sh sql-eav               (starts the databases for you)
//
// entities holds one row per product; attributes holds one (entity, name, value)
// row per property, every value TEXT, whatever it means. That shape is flexible
// to write and awkward to ask: "red or blue, and made of steel" is two EXISTS
// subqueries, and a price is a number only when the text says so. SEL pushes
// down what SQLite can answer exactly (text equality, joins, grouping) and keeps
// the rest -- pivoting attributes into records, comparing text as a number -- in
// memory, where ISNUM can say what SQLite cannot.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release --features usage --example sql-eav` in rust/
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
        ("PRODUCTS".to_string(), relation("entities", "e", &[("id", NUM), ("sku", TEXT), ("kind", TEXT)])),
        ("ATTRS".to_string(), relation("attributes", "a", &[("entity_id", NUM), ("name", TEXT),
                                                             ("value", TEXT)])),
    ])))
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const PIPELINES: &[(&str, &str)] = &[
    ("red or blue, and steel", "red-or-blue-steel.sel"),
    ("products per colour", "products-per-colour.sel"),
    ("priced under 60.00, pivoted", "priced-under-60.sel"),
];

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();
    let schema = schema();
    let mut conn = db::connect("sqlite")?;

    // The same tables in memory, for the comparison at the end of each pipeline.
    let tables = Value::none();
    for (name, table, key) in [("PRODUCTS", "entities", "id"), ("ATTRS", "attributes", "entity_id, name")] {
        tables.set(name, db::query(&mut conn, &format!("SELECT * FROM {table} ORDER BY {key}"), &[])?, at)?;
    }

    for (n, (title, file)) in PIPELINES.iter().enumerate() {
        // EXAMPLE-BEGIN run
        let mut program = compile(&read(&format!("examples/sql-eav/{file}"))?)?;
        let plan = plan_hybrid(&program, "sqlite", Some(&schema), Options::default());
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
