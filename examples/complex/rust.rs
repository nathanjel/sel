// Complex usage — the language's reach, from Rust.
//
//   bash rust/build.sh && rust/build/example-complex
//
// examples/plain/ is the API. This is the language: aggregates, named binders,
// text and regex, structured results, and a rule that refuses. The files beside
// this one print byte-identical output; tools/check-examples.sh diffs them.

use sel_lang::{compile, evaluate, Pos, SelError, Value};

// The scalar-context text of a value (a list reads as its first child).
fn text(v: &Value) -> Result<String, SelError> {
    v.as_text(Pos::default())
}

fn val(s: &str) -> Value {
    Value::text_owned(s.to_string())
}

fn main() -> Result<(), SelError> {
    let at = Pos::default();

    // Rust has no fromNative — there is no native map or array to convert from —
    // so the order is built child by child. Money is TEXT, never an f64.
    let order = Value::none();
    order.set("CUSTOMER", val("Zażółć Gęślą"), at)?;
    order.set("POSTCODE", val("31-874"), at)?;
    order.set("CREDIT_LIMIT", val("100.00"), at)?;
    let mut items = Vec::new();
    for (sku, qty, price) in [("AB-1234", "3", "19.99"), ("CD-5678", "1", "5.01"), ("EF-9012", "2", "0.50")] {
        let item = Value::none();
        item.set("SKU", val(sku), at)?;
        item.set("QTY", val(qty), at)?;
        item.set("PRICE", val(price), at)?;
        items.push(item);
    }
    order.set("ITEMS", Value::list(items), at)?; // a list is keyed "1".."n"

    // A compile error and a run error arrive the same way, as the Err of one
    // Result, so a caller that catches one catches both.
    let ask = |src: &str| -> Result<String, SelError> { text(&compile(src)?.run(Some(order.clone()))?) };

    // 1 — a rule set, not an expression -------------------------------------------
    // `;` separates statements and the last one is the answer. Intermediate names
    // are ordinary variables, so a long rule reads top to bottom.

    println!("1. a rule set");
    println!(
        "   {}",
        ask(&[
            "NET   = SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])",
            "VAT   = ROUND(NET * 0.23, 2)",
            "GROSS = NET + VAT",
            "IF(GROSS > CREDIT_LIMIT, \"refer: \" & GROSS, \"accept: \" & GROSS)",
        ]
        .join("; "))?
    );

    // 2 — aggregates ---------------------------------------------------------------
    // No loops. A body expression is evaluated once per element with `_` bound to
    // the element and `_K` to its key.

    println!("2. aggregates");
    println!("   lines        => {}", ask("COUNT(ITEMS)")?);
    println!("   net          => {}", ask("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])")?);
    println!("   all in stock => {}", ask("IF(ALL(ITEMS, _[\"QTY\"] > 0), \"TRUE\", \"FALSE\")")?);
    println!("   any > 10     => {}", ask("IF(ANY(ITEMS, _[\"PRICE\"] > 10.00), \"TRUE\", \"FALSE\")")?);
    println!("   dearest      => {}", ask("MAX(MAP(ITEMS, _[\"PRICE\"]))")?);

    // 3 — MAP renumbers, FILTER keeps the keys --------------------------------------
    // A filtered list stays addressable the way its source was, which is why the
    // dump below has holes in it. That is the contract, not an accident.

    println!("3. map and filter");
    println!("   skus         => {}", ask("JOIN(MAP(ITEMS, _[\"SKU\"]), \", \")")?);
    println!(
        "   bulk keys    => {}",
        compile("FILTER(ITEMS, _[\"QTY\"] > 1)")?.run(Some(order.clone()))?.keys().join(",")
    );
    println!(
        "   keyed        => {}",
        compile("MAP(FILTER(ITEMS, _[\"QTY\"] > 1), _K & \":\" & _[\"SKU\"])")?
            .run(Some(order.clone()))?
            .dump()?
    );

    // 4 — naming the binder, for nesting ---------------------------------------------
    // `_` is the innermost element. The three-argument form names it instead, which
    // is the only way an outer element stays reachable from an inner body.

    println!("4. named binders");
    println!(
        "   {}",
        text(&evaluate(
            "R[1] = (1, 2); R[2] = (3, 4); \
             IF(ALL(R, ROW, ALL(ROW, _ > 0)), \"all positive\", \"no\")",
            None
        )?)?
    );

    // 5 — text and regex ---------------------------------------------------------------
    // Patterns are a portable subset, checked at compile time: a regex that would
    // mean different things on different hosts is refused rather than guessed at.

    println!("5. text and regex");
    // UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
    // ę below come back unchanged. That is not a shortcoming, it is the only way
    // every host can agree. Measured on a sharp s: JS's toUpperCase, Python's
    // str.upper and Rust's str::to_uppercase all answer SS, PHP's strtoupper
    // answers ß, and C's toupper cannot see it at all. SEL answers ß on every
    // host, because it never asks the host.
    println!("   upper        => {}", ask("UPPER(CUSTOMER)")?);
    println!("   initials     => {}", ask("JOIN(MAP(SPLIT(CUSTOMER, \" \"), LEFT(_, 1)), \".\")")?);
    println!("   postcode     => {}", ask("IF(RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE), \"ok\", \"bad\")")?);
    // RGROUPS puts the WHOLE match at "1", so the first capture is "2".
    println!("   area         => {}", ask("RGROUPS('^([0-9]{2})-', POSTCODE)[\"2\"]")?);
    println!("   padded       => {}", ask("PADL(COUNT(ITEMS), 3, \"0\")")?);

    // 6 — asking whether a key is there --------------------------------------------------

    println!("6. presence");
    println!("   HAS SKU      => {}", ask("IF(HAS(ITEMS[1], \"SKU\"), \"TRUE\", \"FALSE\")")?);
    println!("   HAS NOTE     => {}", ask("IF(HAS(ITEMS[1], \"NOTE\"), \"TRUE\", \"FALSE\")")?);
    let missing = match ask("ITEMS[1][\"NOTE\"]") {
        Ok(found) => found,
        Err(e) => format!("{} at {}:{}", e.code, e.pos.line, e.pos.col),
    };
    println!("   missing      => {missing}");

    // 7 — a rule that refuses -------------------------------------------------------------
    // ABORT is how a rule says "this is not valid", as distinct from "this could not
    // be computed". Both arrive as the same error type, told apart by the code.

    println!("7. business refusal");
    for (label, src) in [
        (
            "under limit",
            "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > CREDIT_LIMIT, \
             ABORT(\"over credit limit\"), \"ok\")",
        ),
        (
            "over limit",
            "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > 50.00, \
             ABORT(\"over credit limit\"), \"ok\")",
        ),
        ("blank name", "IF(TRIM(\"   \") $== \"\", ABORT(\"customer required\"), \"ok\")"),
    ] {
        // The answer is settled before anything is printed: a partly written line
        // cannot be taken back once ABORT has failed through the middle of it.
        let answer = match ask(src) {
            Ok(answer) => answer,
            Err(e) => format!("{}: {}", e.code, e.message),
        };
        println!("   {label:<11} => {answer}");
    }

    // 8 — where a failure actually happened -------------------------------------------------
    // The position is the node that failed, not the statement or the call that
    // contains it. That is what makes a long rule debuggable.

    println!("8. error positions");
    for src in ["1 + ROUND(2 + \"x\", 2)", "SUM(ITEMS, _[\"QTY\"] * _[\"NOPE\"])"] {
        if let Err(e) = compile(src).and_then(|mut program| program.run(Some(order.clone()))) {
            println!("   {} at {}:{}  {src}", e.code, e.pos.line, e.pos.col);
        }
    }
    Ok(())
}
