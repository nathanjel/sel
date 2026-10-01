// A report no database can take a share of -- SQL loads, SEL computes, from Rust.
//
//   tools/check-usage.sh sql-complex           (starts the databases for you)
//
// The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
// numbers out of subjects with RGROUPS and searches the event log for each
// ticket's first answer. plan_hybrid() finds no step of it PostgreSQL can
// answer, and says so: pure_memory. So the database's job shrinks to handing
// over the tables -- with the SELECTs written by SEL too, from the same bindings
// -- and the report runs in memory over what came back. The last line checks it
// against the report over the data generated in memory (examples/memory-complex),
// which is where the rows in this database came from.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release -p sel-lang-dev --features usage --example sql-complex` in rust/
// (tools/check-usage.sh does it in its image), which includes examples/lib/db.rs
// and its PostgreSQL, MariaDB and SQLite drivers.

#[path = "../lib/db.rs"]
mod db;

use std::collections::HashMap;
use std::error::Error;
use std::fs;

use sel_lang::sql::{plan_hybrid, translate_statement, Binding, Bindings, FieldEntry, Mode, Options, SqlKind};
use sel_lang::{compile, evaluate, Pos, Value};

fn read(name: &str) -> Result<String, Box<dyn Error>> {
    let path = format!("examples/lib/{name}");
    fs::read_to_string(&path).map_err(|e| format!("cannot read {path}: {e}").into())
}

fn relation(table: &str, alias: &str, fields: &[(&str, SqlKind)]) -> Binding {
    let columns = fields
        .iter()
        .map(|&(name, kind)| {
            FieldEntry::new(name, Binding::column(name, alias, kind, false, false, false, "", "", false))
        })
        .collect();
    Binding::relation(table, alias, columns, "", "", "", false)
}

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();
    // EXAMPLE-BEGIN load
    use SqlKind::{Num as NUM, Text as TEXT};
    let schema = Bindings::new(Some(HashMap::from([
        ("TEAMS".to_string(), relation("teams", "g", &[("team_id", NUM), ("team", TEXT)])),
        ("CUSTOMERS".to_string(), relation("customers", "c", &[("customer_id", NUM), ("customer", TEXT),
                                                               ("plan", TEXT)])),
        ("SLA".to_string(), relation("sla", "s", &[("plan", TEXT), ("priority", TEXT),
                                                   ("respond_within", NUM), ("resolve_within", NUM)])),
        ("TICKETS".to_string(), relation("tickets", "t", &[("ticket_id", NUM), ("customer_id", NUM),
                                                           ("team_id", NUM), ("priority", TEXT),
                                                           ("subject", TEXT), ("opened_at", NUM),
                                                           ("closed_at", NUM)])),
        ("EVENTS".to_string(), relation("events", "e", &[("event_id", NUM), ("ticket_id", NUM),
                                                         ("seq", NUM), ("at", NUM), ("kind", TEXT),
                                                         ("actor", TEXT)])),
    ])));
    let keys = HashMap::from([
        ("TEAMS", "team_id"), ("CUSTOMERS", "customer_id"), ("SLA", "plan"),
        ("TICKETS", "ticket_id"), ("EVENTS", "event_id"),
    ]);

    let mut report = compile(&read("tickets-report.sel")?)?;
    let plan = plan_hybrid(&report, "postgresql", Some(&schema), Options::default());
    println!("1. the report, planned for PostgreSQL");
    println!("   plan        {}", if plan.pure_memory { "pure_memory" } else { "pushed down" });
    println!("   reads       {}", plan.source_tables.join(", "));

    println!("2. so SQL only loads the tables it reads");
    let mut conn = db::connect("postgresql")?;
    let tables = Value::none();
    for name in report.dependencies()? {
        let load = compile(&format!("{name} .> SORT_BY(_[\"{}\"])", keys[name.as_str()]))?;
        let sql = translate_statement(&load, "postgresql", Some(&schema), Options::default())?
            .as_statement(Mode::Inline)?;
        let rows = db::query(&mut conn, &sql, &[])?;
        println!("   {name:<10}  {:>3} rows  {sql}", rows.size());
        tables.set(&name, rows, at)?;
    }

    println!("3. and SEL computes the report over them");
    let result = report.run(Some(tables))?;
    println!("{}", db::render(&result, "   | ")?);
    // EXAMPLE-END load

    let generated = evaluate(&read("tickets-generate.sel")?, None)?;
    let same = report.run(Some(generated))?.dump()? == result.dump()?;
    println!("   over the generated rows: {}", if same { "same report" } else { "DIFFERENT" });
    Ok(())
}
