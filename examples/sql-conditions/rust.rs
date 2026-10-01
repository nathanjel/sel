// SQL conditions -- one rule as a WHERE clause, from Rust.
//
//   tools/check-usage.sh sql-conditions        (starts the databases for you)
//
// A rule written for the application can filter rows where they live. Part 1 is
// the naive integration: the host knows nothing about the schema except that a
// variable is a column of the same name. Part 2 describes the schema -- types,
// a list of columns, a related table, a parameter -- and gets SQL that is both
// tighter and able to say more. Either way a rule SQL cannot express is refused
// whole, and runs in memory instead; every answer below is checked against the
// in-memory one.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release -p sel-lang-dev --features usage --example sql-conditions` in
// rust/ (tools/check-usage.sh does it in its image), which includes
// examples/lib/db.rs and its PostgreSQL, MariaDB and SQLite drivers.

#[path = "../lib/db.rs"]
mod db;

use std::collections::HashMap;
use std::error::Error;

use sel_lang::sql::{translate, try_translate, Binding, Bindings, FieldEntry, Mode, Options, SqlKind};
use sel_lang::{compile, Pos, Program, SelError, Value};

// A column the table has, read by name (SELECT * brought them all).
fn field(row: &Value, name: &str) -> Value {
    row.get(name).unwrap_or_else(|| panic!("the row has no column {name}"))
}

fn ids(records: &Value) -> Result<String, SelError> {
    let ids = records
        .values()
        .iter()
        .map(|record| field(record, "id").as_text(Pos::default()))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(if ids.is_empty() { "(none)".to_string() } else { ids.join(", ") })
}

// translate() says why try_translate() returned nothing.
fn refusal(rule: &Program, dialect: &str, bindings: &Bindings) -> String {
    match translate(rule, dialect, Some(bindings), Options::default()) {
        Ok(_) => "translated".to_string(),
        Err(e) => e.code,
    }
}

// A row as a rule's context: SEL names are upper case, columns are not (both are
// ASCII, which is all to_ascii_uppercase touches).
fn context_of(row: &Value) -> Result<Value, SelError> {
    let ctx = Value::none();
    for entry in row.entries() {
        ctx.set(&entry.key.to_ascii_uppercase(), entry.val, Pos::default())?;
    }
    Ok(ctx)
}

// The records a rule accepts, run in memory; keys kept.
fn run_in_memory(
    rule: &mut Program,
    rows: &Value,
    context: impl Fn(&Value) -> Result<Value, SelError>,
) -> Result<Value, SelError> {
    let kept = Value::none();
    for entry in rows.entries() {
        if rule.run(Some(context(&entry.val)?))?.as_bool(Pos::default())? {
            kept.set(&entry.key, entry.val, Pos::default())?;
        }
    }
    Ok(kept)
}

const RULES: &[&str] = &[
    r#"COUNTRY $== "PL" AND TIER $!= "standard""#,
    r#"COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000"#,
    r#"RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)"#,
    r#"IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL)"#,
    r#"ANY(SPLIT(NAME, " "), LEN(_) > 9)"#,
];

// What the rule sees in memory: the same names, as values.
fn order_context(order: &Value, items: &Value) -> Result<Value, SelError> {
    let at = Pos::default();
    let ctx = Value::none();
    for name in ["status", "total", "channel"] {
        ctx.set(&name.to_ascii_uppercase(), field(order, name), at)?;
    }
    let tags = Value::none();
    for (n, name) in ["tag1", "tag2", "tag3"].into_iter().enumerate() {
        tags.set(&(n + 1).to_string(), field(order, name), at)?;
    }
    ctx.set("TAGS", tags, at)?;
    let id = field(order, "id").as_text(at)?;
    let lines = Value::none();
    for item in items.values() {
        if field(&item, "order_id").as_text(at)? == id {
            lines.set(&(lines.size() + 1).to_string(), item, at)?;
        }
    }
    ctx.set("ITEMS", lines, at)?;
    ctx.set("MIN_TOTAL", Value::text_owned("100.00".to_string()), at)?;
    Ok(ctx)
}

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();

    // 1 - naive: a column per variable, nothing else known ---------------------------

    println!("1. naive bindings");
    for dialect in ["sqlite", "mariadb"] {
        let mut conn = db::connect(dialect)?;
        let everyone = db::query(&mut conn, "SELECT * FROM customers ORDER BY id", &[])?;
        for source in RULES {
            // EXAMPLE-BEGIN naive
            let mut rule = compile(source)?;
            let columns = rule
                .dependencies()?
                .into_iter()
                .map(|name| {
                    let column = name.to_ascii_lowercase();
                    (name, Binding::column(&column, "", SqlKind::Unknown, false, false, false, "", "", false))
                })
                .collect();
            let bindings = Bindings::new(Some(columns));
            let condition = try_translate(&rule, dialect, Some(&bindings), Options::default());
            let rows = if let Some(condition) = &condition {
                let sql = format!("SELECT id FROM customers WHERE {} ORDER BY id", condition.as_condition(Mode::Params)?);
                db::query(&mut conn, &sql, &condition.bindings())?
            } else {
                // refused: the rule stays in the application, over rows it loads
                let rows = Value::none();
                for row in everyone.entries() {
                    if rule.run(Some(context_of(&row.val)?))?.as_bool(at)? {
                        rows.set(&row.key, row.val, at)?;
                    }
                }
                rows
            };
            // EXAMPLE-END naive
            let in_memory = run_in_memory(&mut rule, &everyone, context_of)?;
            println!("   {dialect:<8} {source}");
            match &condition {
                Some(condition) => println!("             sql    {}", condition.as_condition(Mode::Inline)?),
                None => println!("             memory ({})", refusal(&rule, dialect, &bindings)),
            }
            let same = ids(&rows)? == ids(&in_memory)?;
            println!("             rows   {} | same as in memory: {}", ids(&rows)?, if same { "TRUE" } else { "FALSE" });
        }
    }

    // 2 - involved: the host describes its schema ------------------------------------

    println!("2. described bindings");
    let mut conn = db::connect("postgresql")?;
    // EXAMPLE-BEGIN involved
    // Binding::column's arguments are all positional; these are the four that vary.
    let column = |name: &str, table: &str, kind: SqlKind, exact: bool| {
        Binding::column(name, table, kind, exact, false, false, "", "", false)
    };
    use SqlKind::{Num as NUM, Text as TEXT};
    let bindings = Bindings::new(Some(HashMap::from([
        ("STATUS".to_string(), column("status", "o", TEXT, true)),
        ("TOTAL".to_string(), column("total", "o", NUM, false)),
        ("CHANNEL".to_string(), column("channel", "o", TEXT, true)),
        ("TAGS".to_string(), Binding::columns(vec![column("tag1", "o", TEXT, false),
                                                   column("tag2", "o", TEXT, false),
                                                   column("tag3", "o", TEXT, false)])),
        ("ITEMS".to_string(), Binding::relation("order_items", "i", vec![
            FieldEntry::new("sku", column("sku", "i", TEXT, false)),
            FieldEntry::new("qty", column("qty", "i", NUM, false)),
            FieldEntry::new("price", column("price", "i", NUM, false)),
        ], /* scalar */ "", /* correlate */ r#""i"."order_id" = "o"."id""#, "", false)),
        ("MIN_TOTAL".to_string(), Binding::value(Value::text_owned("100.00".to_string()), None)),
    ])));
    // EXAMPLE-END involved

    let orders = db::query(&mut conn, "SELECT * FROM orders ORDER BY id", &[])?;
    let items = db::query(&mut conn, "SELECT * FROM order_items ORDER BY order_id, line_no", &[])?;

    for source in [
        r#"STATUS $== "paid" AND TOTAL >= MIN_TOTAL"#,
        r#"ANY(TAGS, _ $== "gift") AND CHANNEL $== "web""#,
        r#"COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)"#,
        r#"SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL"#,
        r#"ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")"#,
    ] {
        // EXAMPLE-BEGIN involved-run
        let mut rule = compile(source)?;
        let condition = translate(&rule, "postgresql", Some(&bindings), Options::default())?;
        let sql = format!("SELECT id FROM orders o WHERE {} ORDER BY id", condition.as_condition(Mode::Params)?);
        let rows = db::query(&mut conn, &sql, &condition.bindings())?;
        // EXAMPLE-END involved-run
        let in_memory = run_in_memory(&mut rule, &orders, |order| order_context(order, &items))?;
        println!("   {source}");
        println!("             sql    {}", condition.as_condition(Mode::Inline)?);
        let params = condition.bindings();
        if !params.is_empty() {
            let params = params.iter().map(|v| v.as_text(at)).collect::<Result<Vec<_>, _>>()?;
            println!("             params {}", params.join(", "));
        }
        let same = ids(&rows)? == ids(&in_memory)?;
        println!("             rows   {} | same as in memory: {}", ids(&rows)?, if same { "TRUE" } else { "FALSE" });
    }
    Ok(())
}
