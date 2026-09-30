// The same report with no database at all -- generated data, in memory, from Rust.
//
//   bash rust/build.sh && rust/build/example-memory-complex     (from the repository root)
//
// examples/sql-complex loads the support desk from PostgreSQL. Here the same
// rows come from examples/lib/tickets-generate.sel -- a SEL program that builds
// them deterministically, and the source the PostgreSQL seed was rendered from
// -- and the same report runs over them. Nothing below opens a connection:
// the plan for MariaDB is computed from the bindings alone, and it says what it
// said for PostgreSQL, that none of this report is SQL's to answer.
//
// The files beside this one print byte-identical output.
//
// render.rs is the driver-free half of examples/lib/db.rs, so this example
// needs the `sql` feature and no database client.

#[path = "../lib/render.rs"]
mod render;

use std::collections::HashMap;
use std::error::Error;
use std::fs;

use sel_lang::sql::{plan_hybrid, Binding, Bindings, FieldEntry, Options, SqlKind};
use sel_lang::{compile, evaluate};

use render::render;

fn read(name: &str) -> Result<String, Box<dyn Error>> {
    let path = format!("examples/lib/{name}");
    fs::read_to_string(&path).map_err(|e| format!("cannot read {path}: {e}").into())
}

// Planning needs the schema, not a server: the bindings sql-complex describes
// PostgreSQL with, asked about MariaDB this time.
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
    // EXAMPLE-BEGIN generate
    let data = evaluate(&read("tickets-generate.sel")?, None)?;
    println!("1. generated in memory");
    for name in data.keys() {
        let rows = data.get(&name).map_or(0, |table| table.size());
        println!("   {name:<10}  {rows:>3} rows");
    }

    let mut report = compile(&read("tickets-report.sel")?)?;
    println!("2. the report");
    println!("{}", render(&report.run(Some(data))?, "   | ")?);
    // EXAMPLE-END generate

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
    let plan = plan_hybrid(&report, "mariadb", Some(&schema), Options::default());
    println!("3. planned for MariaDB, without connecting");
    println!("   plan        {}", if plan.pure_memory { "pure_memory" } else { "pushed down" });
    println!("   reads       {}", plan.source_tables.join(", "));
    Ok(())
}
