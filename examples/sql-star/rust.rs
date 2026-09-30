// A star schema -- whole pipelines in SQL, and split with memory, from Rust.
//
//   tools/check-usage.sh sql-star              (starts the databases for you)
//
// fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
// The application describes each table once, as a relation binding, and then
// hands SEL whole pipelines. plan_hybrid() decides how much of each one the
// database can answer: all of it (pure_sql), a prefix of it (hybrid, the rest
// runs in memory over the rows the prefix returned), or none of it
// (pure_memory). The answer is checked against a run of the same program over
// the tables loaded into memory.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release --features usage --example sql-star` in rust/
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
        ("SALES".to_string(), relation("fact_sales", "s", &[("sale_id", NUM), ("date_key", NUM),
                                                            ("product_key", NUM), ("store_key", NUM),
                                                            ("qty", NUM), ("revenue", NUM)])),
        ("DATES".to_string(), relation("dim_date", "d", &[("date_key", NUM), ("year", NUM), ("quarter", NUM),
                                                          ("month", NUM), ("month_name", TEXT)])),
        ("STORES".to_string(), relation("dim_store", "t", &[("store_key", NUM), ("city", TEXT),
                                                            ("region", TEXT), ("format", TEXT)])),
        ("PRODUCTS".to_string(), relation("dim_product", "p", &[("product_key", NUM), ("sku", TEXT),
                                                                ("name", TEXT), ("category", TEXT),
                                                                ("brand", TEXT), ("list_price", NUM)])),
    ])))
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const PIPELINES: &[(&str, &str)] = &[
    ("revenue by category, first quarter", "revenue-by-category.sel"),
    ("best-selling product per region, stores only", "best-product-per-region.sel"),
];

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();
    let schema = schema();
    let mut conn = db::connect("postgresql")?;

    // The same tables in memory, for the comparison at the end of each pipeline.
    let tables = Value::none();
    for (name, table, key) in [
        ("SALES", "fact_sales", "sale_id"),
        ("DATES", "dim_date", "date_key"),
        ("STORES", "dim_store", "store_key"),
        ("PRODUCTS", "dim_product", "product_key"),
    ] {
        tables.set(name, db::query(&mut conn, &format!("SELECT * FROM {table} ORDER BY {key}"), &[])?, at)?;
    }

    for (n, (title, file)) in PIPELINES.iter().enumerate() {
        // EXAMPLE-BEGIN run
        let mut program = compile(&read(&format!("examples/sql-star/{file}"))?)?;
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
