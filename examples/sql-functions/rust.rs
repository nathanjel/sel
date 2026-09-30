// The application's own functions, in memory and in PostgreSQL -- Rust.
//
//   tools/check-usage.sh sql-functions         (starts the databases for you)
//
// The application registers five functions of its own. Each has a local
// implementation -- the code register_function runs -- and four also get a SQL
// spelling for PostgreSQL, which the application promises computes the same
// thing (spec §8.1, sql/MAP.md §4.7):
//
//   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
//   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
//   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
//   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
//   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
//   WORDS(title)                returns a list               -> no spelling: stays in memory
//
// Every pipeline prints its plan and its rows, and whether those rows are the rows
// the same program computes in memory -- which is how the example checks that the
// two implementations of each function agree on this data.
//
// The files beside this one print byte-identical output.
//
// Built by `cargo build --release --features usage --example sql-functions` in
// rust/ (tools/check-usage.sh does it in its image), which includes
// examples/lib/db.rs and its PostgreSQL, MariaDB and SQLite drivers. A spelling
// is registered with sql::define, whose entry is the dialect map's own JSON
// (sql/MAP.md), written here with serde_json::json!.
//
// A host function must be Send + Sync, and a Program is neither (it caches as it
// runs), so the two that run SEL keep their compiled Program per thread.

#[path = "../lib/db.rs"]
mod db;

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs;

use serde_json::json;
use sel_lang::sql::{
    define, execute_hybrid, plan_hybrid, translate, Binding, Bindings, FieldEntry, Mode, Options, SqlKind,
};
use sel_lang::{compile, register_function, Args, Pos, Program, Value};

fn read(path: &str) -> Result<String, Box<dyn Error>> {
    fs::read_to_string(path).map_err(|e| format!("cannot read {path}: {e}").into())
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
    let mut conn = db::connect("postgresql")?;

    // 1 - the local implementations ------------------------------------------------------
    // What register_function runs: plain code, or -- where exact decimal arithmetic
    // matters -- a SEL expression, so the local answer has SEL's numbers.

    // EXAMPLE-BEGIN local
    fn slug(text: &str) -> String {
        let mut out = String::new();
        let mut dash = false;
        for c in text.chars().map(|c| c.to_ascii_lowercase()) {  // ASCII only, as SQL's
            if c.is_ascii_lowercase() || c.is_ascii_digit() {       // [^a-z0-9]+ sees it
                if dash && !out.is_empty() {
                    out.push('-');
                }
                out.push(c);
                dash = false;
            } else {
                dash = true;
            }
        }
        out
    }

    thread_local! {
        static MARGIN: RefCell<Program> =
            RefCell::new(compile("ROUND((PRICE - COST) * 100 / PRICE, 1)").expect("a valid rule"));
        static SHIPPING: RefCell<Program> = RefCell::new(
            compile("COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00) * IF(COUNTRY $== \"PL\", 1, 2)")
                .expect("a valid rule"),
        );
    }

    let vat_rates = db::query(&mut conn, "SELECT * FROM vat_rates", &[])?;
    let mut rates: HashMap<(String, String), String> = HashMap::new();
    for r in vat_rates.values() {
        let text = |name: &str| r.get(name).expect("a vat_rates column").as_text(at);
        rates.insert((text("country")?, text("category")?), text("rate")?);
    }

    register_function("SLUG", 1, 1, |args: &mut Args| Ok(Value::text_owned(slug(&args.text(0)?))))?;
    register_function("MARGIN_PCT", 2, 2, move |args: &mut Args| {
        let ctx = Value::none();
        ctx.set("PRICE", args.val(0)?, at)?;
        ctx.set("COST", args.val(1)?, at)?;
        MARGIN.with(|program| program.borrow_mut().run(Some(ctx)))
    })?;
    register_function("VAT_RATE", 2, 2, move |args: &mut Args| {
        let (country, category) = (args.text(0)?, args.text(1)?);
        let rate = rates
            .get(&(country.clone(), category))
            .or_else(|| rates.get(&(country, "*".to_string())));
        Ok(Value::text_owned(rate.map_or("0", String::as_str).to_string()))
    })?;
    register_function("SHIPPING_COST", 2, 2, move |args: &mut Args| {
        let ctx = Value::none();
        ctx.set("KG", args.val(0)?, at)?;
        ctx.set("COUNTRY", args.val(1)?, at)?;
        SHIPPING.with(|program| program.borrow_mut().run(Some(ctx)))
    })?;
    register_function("HAS_TAG", 2, 2, move |args: &mut Args| {
        let (tags, tag) = (args.val(0)?, args.text(1)?);
        if tags.size() == 0 {
            return Ok(Value::bool(tags.as_text(at)? == tag)); // a scalar is a list of one
        }
        for v in tags.values() {
            if v.as_text(at)? == tag {
                return Ok(Value::bool(true));
            }
        }
        Ok(Value::bool(false))
    })?;
    register_function("WORDS", 1, 1, |args: &mut Args| {
        let words = slug(&args.text(0)?)
            .split('-')
            .filter(|w| !w.is_empty())
            .map(|w| Value::text_owned(w.to_string()))
            .collect();
        Ok(Value::list(words))
    })?;
    // EXAMPLE-END local

    // 2 - the SQL spellings ------------------------------------------------------------------
    // After the functions: a spelling for a name that is not registered is refused.

    // EXAMPLE-BEGIN spell
    define("postgresql", "funcs", "SLUG",
           &json!({"tpl": "slug({0})", "ret": "TEXT", "args": ["TEXT"]}));
    define("postgresql", "funcs", "MARGIN_PCT",
           &json!({"tpl": "margin_pct({0}, {1})", "ret": "NUM", "args": ["NUM", "NUM"]}));
    define("postgresql", "funcs", "VAT_RATE",
           &json!({"tpl": "vat_rate({0}, {1})", "ret": "NUM", "args": ["TEXT", "TEXT"]}));
    define("postgresql", "funcs", "SHIPPING_COST",
           &json!({"tpl": "shipping_cost({0}, {1})", "ret": "NUM", "args": ["NUM", "TEXT"]}));
    define("postgresql", "funcs", "HAS_TAG",
           &json!({"tpl": "({1} = ANY(ARRAY[{0}]))", "ret": "BOOL", "args": ["LIST", "TEXT"]}));
    // WORDS returns a list: no spelling can say that, so it has none.
    // EXAMPLE-END spell

    use SqlKind::{Num as NUM, Text as TEXT};
    let schema = Bindings::new(Some(HashMap::from([
        ("PRODUCTS".to_string(), relation("products", "p", &[("product_id", NUM), ("title", TEXT),
                                                             ("category", TEXT), ("price", NUM),
                                                             ("cost", NUM), ("weight_kg", NUM),
                                                             ("tag1", TEXT), ("tag2", TEXT), ("tag3", TEXT)])),
        ("ORDERS".to_string(), relation("orders", "o", &[("order_id", NUM), ("country", TEXT)])),
        ("LINES".to_string(), relation("order_lines", "l", &[("order_id", NUM), ("line_no", NUM),
                                                             ("product_id", NUM), ("qty", NUM)])),
    ])));

    let tables = Value::none();
    for (name, table, key) in [
        ("PRODUCTS", "products", "product_id"),
        ("ORDERS", "orders", "order_id"),
        ("LINES", "order_lines", "order_id, line_no"),
    ] {
        tables.set(name, db::query(&mut conn, &format!("SELECT * FROM {table} ORDER BY {key}"), &[])?, at)?;
    }

    // Each pipeline is a .sel file beside this one: (title, file).
    let pipelines = [
        ("gifts with a margin of 40% or more", "gifts-by-margin.sel"),
        ("gross revenue and shipping per country", "gross-per-country.sel"),
        ("words in the titles of the better-margin products", "title-words.sel"),
    ];

    for (n, (title, file)) in pipelines.iter().enumerate() {
        // EXAMPLE-BEGIN run
        let mut program = compile(&read(&format!("examples/sql-functions/{file}"))?)?;
        let plan = plan_hybrid(&program, "postgresql", Some(&schema), Options::default());
        let rows = execute_hybrid(&plan, db::runner(&mut conn), plan.pure_memory.then_some(&tables))?;
        // EXAMPLE-END run
        let kind = if plan.pure_sql { "pure_sql" } else if plan.pure_memory { "pure_memory" } else { "hybrid" };
        println!("{}. {title}", n + 1);
        println!("   plan        {kind}");
        if let Some(statement) = &plan.sql_statement {
            println!("   sql         {}", statement.as_statement(Mode::Inline)?);
            let caveats = &statement.caveats;
            println!("   caveats     {}", if caveats.is_empty() { "(none)".to_string() } else { caveats.join(", ") });
        }
        println!("{}", db::render(&rows, "   | ")?);
        // A Value is a handle: the program gets a copy, so the tables stay as loaded.
        let same = program.run(Some(tables.deep_copy(1, at)?))?.dump()? == rows.dump()?;
        println!("   in memory   {}", if same { "same rows" } else { "DIFFERENT" });
    }

    // 4 - what strict translation says ---------------------------------------------------------
    // A spelling is the application's promise, not this layer's, so strict mode --
    // exact or nothing -- refuses it.

    println!("{}. strict translation", pipelines.len() + 1);
    // EXAMPLE-BEGIN strict
    let rule = compile("SLUG(TITLE) $== \"cast-iron-pan\"")?;
    let title = Bindings::new(Some(HashMap::from([(
        "TITLE".to_string(),
        Binding::column("title", "p", SqlKind::Text, false, false, false, "", "", false),
    )])));
    println!("   caveats     {}", translate(&rule, "postgresql", Some(&title), Options::default())?.caveats.join(", "));
    if let Err(e) = translate(&rule, "postgresql", Some(&title), Options { strict: true }) {
        println!("   strict      {}", e.code);
    }
    // EXAMPLE-END strict
    Ok(())
}
