// Adding a SQL flavour — teaching the translator about your database, from Rust.
//
//   bash rust/build.sh && rust/build/example-dialect
//
// The shipped map covers four targets over two bases. A deployment is rarely
// exactly one of them: a driver wants numbered placeholders, a function is
// spelled differently, an extension is not installed. A dialect is registered
// rather than forked, so what you write is only the difference.
//
// The files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is the shape of a registration. The map's
// functions sit in sel_lang::sql beside translate(), and a dialect or an entry
// is the same JSON the dynamic hosts write as a literal — a serde_json::Value,
// built with json!, so an application that registers one depends on serde_json
// itself. The registration calls return nothing: a spec the map will not accept
// panics, where the other hosts throw.

use std::collections::HashMap;
use std::error::Error;
use std::sync::Arc;

use sel_lang::sql::{self, Binding, Bindings, BuilderFn, Emit, Fragment, Mode, Options, Part, SqlError, SqlKind};
use sel_lang::{compile, Pos};
use serde_json::json;

// A column binding with no flags, collation or prefilter.
fn col(column: &str, table: &str, kind: SqlKind) -> Binding {
    Binding::column(column, table, kind, false, false, false, "", "", false)
}

fn main() -> Result<(), Box<dyn Error>> {
    let rule = compile("NAME $== \"ok\" AND TOTAL > 10.00")?;
    let bindings = Bindings::new(Some(HashMap::from([
        ("NAME".to_string(), col("name", "t", SqlKind::Text)),
        ("TOTAL".to_string(), col("total", "t", SqlKind::Num)),
    ])));
    let sql_in = |dialect: &str| -> Result<String, SqlError> {
        sql::translate(&rule, dialect, Some(&bindings), Options::default())?.as_condition(Mode::Params)
    };

    // 1 — what ships ---------------------------------------------------------------

    println!("1. what ships");
    println!("   targets      => {}", sql::dialects().join(" "));
    println!("   postgresql   => {}", sql::chain("postgresql").join(" -> "));

    // 2 — a flavour of your own -------------------------------------------------------
    // `extends` is the whole mechanism: the new dialect answers for what it declares
    // and defers upward for everything else. Two-phase lookup -- the whole overlay
    // chain, then the whole shipped chain -- so an override never half-applies.
    //
    // A spec that leaves `extends` out is refused, as it is in every dynamic host;
    // a dialect with no parent says `"extends": null`, as ansi does.

    println!("2. a flavour of your own");
    // EXAMPLE-BEGIN flavour
    sql::define_dialect("pg-libpq", &json!({
        "extends": "postgresql",
        "version": "15",
        "target": true,                         // a base is not a target; this is a server
        "lexical": { "placeholder": "${n}" },   // libpq numbers its parameters
    }));
    println!("   targets      => {}", sql::dialects().join(" "));
    println!("   chain        => {}", sql::chain("pg-libpq").join(" -> "));
    println!("   base         => {}", sql_in("postgresql")?);
    println!("   pg-libpq     => {}", sql_in("pg-libpq")?);
    // EXAMPLE-END flavour

    // 3 — spelling one function differently ---------------------------------------------
    // {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
    // while every position SEL reports is one-based -- these are template holes, not
    // SEL positions. The entry also says what it returns, because the translator
    // infers kinds and will not guess.

    println!("3. one function, respelled");
    // EXAMPLE-BEGIN respell
    sql::define("pg-libpq", "funcs", "UPPER", &json!({ "tpl": "UPPER({0} COLLATE \"C\")", "ret": "TEXT" }));
    let upper = sql::translate(&compile("UPPER(NAME)")?, "pg-libpq", Some(&bindings), Options::default())?;
    println!("   upper        => {}", upper.as_value(Mode::Inline)?);
    // EXAMPLE-END respell

    // 4 — withdrawing what a deployment does not have ------------------------------------
    // A null entry withdraws it. This is not the same as leaving it unmapped: it is
    // the map saying "not here", and the rule is refused rather than emitted against
    // a function the server does not have.

    println!("4. withdrawing an entry");
    // EXAMPLE-BEGIN withdraw
    sql::define("pg-libpq", "funcs", "RMATCH", &serde_json::Value::Null);
    let re = compile("RMATCH('^a', NAME)")?;
    for dialect in ["postgresql", "pg-libpq"] {
        let answer = match sql::try_translate(&re, dialect, Some(&bindings), Options::default()) {
            Some(_) => "translated",
            None => "refused",
        };
        println!("   {dialect:<12} => {answer}");
    }
    // EXAMPLE-END withdraw

    // 5 — a builder, for what a template cannot say -----------------------------------------
    // The escape hatch. It receives the emitter and the already-rendered arguments,
    // and returns a fragment, so it can do what no string with holes in it can. The
    // third argument is the call site; a closure has to take it even when it has
    // no use for it, and the leading underscore says so.
    //
    // Splice the argument's `parts` rather than its rendered SQL. A part list is
    // strings alternating with parameter slots, so splicing keeps a bound value
    // bound; flattening it to a string first would inline whatever the argument
    // carried and quietly turn a prepared statement back into concatenation.
    //
    // Slot numbers are absolute for the whole translation, so a spliced part is
    // copied verbatim and never renumbered.

    println!("5. a builder");
    // EXAMPLE-BEGIN builder
    let len: BuilderFn = Arc::new(|emit: &Emit, args: &[Fragment], _at: Pos| -> Result<Fragment, SqlError> {
        let mut parts = vec![Part::Sql("length(".to_string())];
        parts.extend(args[0].parts.iter().cloned());
        parts.push(Part::Sql(")".to_string()));
        Ok(Fragment::new(parts, SqlKind::Num, emit.dialect(), Vec::new(), Vec::new(), Vec::new()))
    });
    sql::define_builder("pg-libpq", "funcs", "LEN", len);
    let len_sql = sql::translate(&compile("LEN(NAME)")?, "pg-libpq", Some(&bindings), Options::default())?;
    println!("   len          => {}", len_sql.as_value(Mode::Inline)?);
    // EXAMPLE-END builder

    // 6 — putting it back ---------------------------------------------------------------------
    // reset() drops every registration and leaves the shipped map. Worth knowing in
    // a test suite: a registration that leaks into the next test is a test that
    // passes for the wrong reason.

    println!("6. reset");
    sql::reset();
    println!("   targets      => {}", sql::dialects().join(" "));
    println!("   pg-libpq     => {}", if sql::exists("pg-libpq") { "still there" } else { "gone" });
    Ok(())
}
