// SQL-aimed usage — pushing a rule down to the database, from Rust.
//
//   bash rust/build.sh && rust/build/example-sql
//
// The same rule that validates one order in the application can filter a
// million of them in the database. What makes that safe is that the translation
// refuses rather than guesses: if SQL cannot be made to mean what SEL means, no
// SQL is emitted and the rule stays where it already worked.
//
// The files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is that Rust, like C++, has no untyped map to
// hand over as bindings and no null to return from a call that declines: a
// Bindings is built from a HashMap of typed Binding constructors, and
// try_translate() answers None. Binding::column() takes every column option
// positionally, so the helper below names the three this file uses and passes
// the defaults. There are two error types — compiling fails with a SelError,
// translating and rendering with an SqlError — and main returns either through
// `?`.

use std::collections::HashMap;
use std::error::Error;

use sel_lang::compile;
use sel_lang::sql::{self, Binding, Bindings, FieldEntry, Mode, Options, SqlKind};

// A column binding with no flags, collation or prefilter; "" is "no table".
fn col(column: &str, table: &str, kind: SqlKind) -> Binding {
    Binding::column(column, table, kind, false, false, false, "", "", false)
}

fn bindings_of(entries: Vec<(&str, Binding)>) -> Bindings {
    let map: HashMap<String, Binding> =
        entries.into_iter().map(|(name, b)| (name.to_string(), b)).collect();
    Bindings::new(Some(map))
}

fn main() -> Result<(), Box<dyn Error>> {
    // 1 — a rule, and what the database should call its inputs --------------------
    // dependencies() says exactly what has to be bound. A name the program reads
    // and the bindings do not describe is a refusal, not a guess.

    println!("1. a rule pushed down");
    let rule = compile("TOTAL > 100.00 AND STATUS $== \"open\"")?;
    println!("   needs        => {}", rule.dependencies()?.join(" "));

    let bindings = bindings_of(vec![
        ("TOTAL", col("total", "o", SqlKind::Num)),
        ("STATUS", col("status", "o", SqlKind::Text)),
    ]);
    let frag = sql::translate(&rule, "mariadb", Some(&bindings), Options::default())?;
    // Rendering can refuse — as_condition() rejects a NUM or TEXT fragment rather
    // than letting the database decide what truthiness means. println! evaluates
    // its arguments before it writes a byte, so a `?` among them leaves before
    // the label is printed.
    println!("   sql          => {}", frag.as_condition(Mode::Inline)?);

    // 2 — the same rule as a prepared statement -------------------------------------
    // Mode::Inline is for reading and for a query you build once. Mode::Params is
    // what you hand a driver: the literals become placeholders and bindings()
    // gives the values in the order the placeholders appear in the output.

    println!("2. as parameters");
    println!("   sql          => {}", frag.as_condition(Mode::Params)?);
    let values = frag.bindings().iter().map(|v| v.dump()).collect::<Result<Vec<_>, _>>()?;
    println!("   values       => {}", values.join(", "));

    // 3 — one rule, every dialect ----------------------------------------------------
    // The differences below are the databases', not the rule's. Nothing in the
    // program changed.

    println!("3. every dialect");
    for dialect in sql::dialects() {
        let sql = sql::translate(&rule, &dialect, Some(&bindings), Options::default())?
            .as_condition(Mode::Inline)?;
        println!("   {dialect:<12} => {sql}");
    }

    // 4 — a rule over a related table -------------------------------------------------
    // An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
    // which is the shape a database can actually use an index for.

    println!("4. over a relation");
    let lines = compile("ALL(ITEMS, I, I[\"QTY\"] > 0)")?;
    let item_bindings = bindings_of(vec![(
        "ITEMS",
        Binding::relation(
            "order_items",
            "oi",
            vec![FieldEntry::new("QTY", col("qty", "", SqlKind::Num))],
            "",                              // no scalar column
            "`oi`.`order_id` = `o`.`id`",    // the correlation
            "",
            false,
        ),
    )]);
    let relation_sql = sql::translate(&lines, "mariadb", Some(&item_bindings), Options::default())?
        .as_condition(Mode::Inline)?;
    println!("   sql          => {relation_sql}");

    // 5 — refusal is an ordinary answer ------------------------------------------------
    // try_translate returns None so the caller can fall back to the evaluator
    // without matching on an error. translate() returns the same refusal with the
    // reason written out, which is what you want in a build-time audit of a rule
    // set.

    println!("5. refusal");
    let unbound = compile("MYSTERY > 1")?;
    // The label is JS's spelling of the method, in every one of the files. The
    // outputs have to be byte-identical, so one host's name for the call is what
    // all of them print; this host's is try_translate.
    let answer = match sql::try_translate(&unbound, "mariadb", Some(&bindings), Options::default()) {
        Some(_) => "translated",
        None => "null — evaluate it in the host instead",
    };
    println!("   tryTranslate => {answer}");
    // translate() only ever answers a refusal as Err(SqlError). A bug in the
    // translator, or a malformed registration, is a panic and goes on up — the
    // same line JS draws with its `instanceof` rethrow.
    if let Err(e) = sql::translate(&unbound, "mariadb", Some(&bindings), Options::default()) {
        println!("   translate    => {}", e.code);
    }

    // 6 — what a fragment knows about itself ---------------------------------------------
    // A caveat is the map saying "this dialect's answer may differ from SEL's here".
    // An empty list is the layer promising it does not.

    println!("6. the fragment");
    println!("   kind         => {}", frag.kind);
    println!("   dialect      => {}", frag.dialect);
    println!("   exact        => {}", if frag.caveats.is_empty() { "TRUE" } else { "FALSE" });
    Ok(())
}
