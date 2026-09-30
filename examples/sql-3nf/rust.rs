// A third-normal-form shop -- joins, grouping and a split, from Rust.
//
//   tools/check-usage.sh sql-3nf               (starts the databases for you)
//
// categories, products, customers, orders and order_lines, each fact stored
// once. The first pipeline is SQL from end to end. The second assigns every
// customer to an A/B cohort by CRC32 of their e-mail -- the application's own
// hashing, which PostgreSQL has no spelling for -- so the database joins, filters
// and multiplies, and the cohorts are computed in memory over what it returned.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release --features usage --example sql-3nf` in rust/
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
        ("CUSTOMERS".to_string(), relation("customers", "c", &[("customer_id", NUM), ("name", TEXT),
                                                               ("email", TEXT), ("country", TEXT)])),
        ("ORDERS".to_string(), relation("orders", "o", &[("order_id", NUM), ("customer_id", NUM),
                                                         ("status", TEXT), ("ordered_on", TEXT)])),
        ("LINES".to_string(), relation("order_lines", "l", &[("order_id", NUM), ("line_no", NUM),
                                                             ("product_id", NUM), ("qty", NUM),
                                                             ("unit_price", NUM)])),
        ("PRODUCTS".to_string(), relation("products", "p", &[("product_id", NUM), ("sku", TEXT),
                                                             ("title", TEXT), ("category_id", NUM),
                                                             ("list_price", NUM)])),
    ])))
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const PIPELINES: &[(&str, &str)] = &[
    ("paid revenue per product since March", "revenue-per-product.sel"),
    ("paid revenue per experiment cohort", "revenue-per-cohort.sel"),
];

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();
    let schema = schema();
    let mut conn = db::connect("postgresql")?;

    // The same tables in memory, for the comparison at the end of each pipeline.
    let tables = Value::none();
    for (name, table, key) in [
        ("CUSTOMERS", "customers", "customer_id"),
        ("ORDERS", "orders", "order_id"),
        ("LINES", "order_lines", "order_id, line_no"),
        ("PRODUCTS", "products", "product_id"),
    ] {
        tables.set(name, db::query(&mut conn, &format!("SELECT * FROM {table} ORDER BY {key}"), &[])?, at)?;
    }

    for (n, (title, file)) in PIPELINES.iter().enumerate() {
        // EXAMPLE-BEGIN run
        let mut program = compile(&read(&format!("examples/sql-3nf/{file}"))?)?;
        let plan = plan_hybrid(&program, "postgresql", Some(&schema), Options::default());
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
