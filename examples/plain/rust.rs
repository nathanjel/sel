// Plain usage — calling SEL from Rust.
//
//   bash rust/build.sh && rust/build/example-plain
//
// The files beside this one do the same thing through their own host API and
// print byte-identical output; tools/check-examples.sh diffs them. That is the
// point of the example as much as the code is: the differences you see between
// these files are the languages', never SEL's.
//
// Rust, like C++, has no fromNative: a context is built child by child. Every
// call that can fail returns Result<_, SelError>, so `?` carries a SEL error to
// main, and a position argument says where a host-built value comes from
// (Pos::default() for "the host").

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

    // 1 — evaluate something ------------------------------------------------------

    println!("1. one-off");
    println!("   2.50 + 2.50 => {}", text(&evaluate("2.50 + 2.50", None)?)?);

    // 2 — compile once, run per request ---------------------------------------------
    // Parsing is cheap but not free, and a Program is reusable.

    println!("2. compile once, run many");
    // EXAMPLE-BEGIN compile
    let mut rule = compile("IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")")?;
    for (qty, price) in [("3", "19.99"), ("1", "5.00")] {
        let ctx = Value::none();
        ctx.set("QTY", val(qty), at)?;
        ctx.set("PRICE", val(price), at)?;
        ctx.set("LIMIT", val("50.00"), at)?;
        println!("   QTY={qty} PRICE={price} => {}", text(&rule.run(Some(ctx))?)?);
    }
    // EXAMPLE-END compile

    // 3 — building a context ----------------------------------------------------------
    // Money is TEXT, never an f64. SEL has no floating point, so the host boundary
    // is where that is said out loud.

    println!("3. structured context");
    // EXAMPLE-BEGIN context
    let order = Value::none();
    order.set("CUSTOMER", val("Zażółć"), at)?;
    let mut items = Vec::new();
    for (sku, qty, price) in [("AB-1234", "3", "19.99"), ("CD-5678", "1", "5.01")] {
        let item = Value::none();
        item.set("SKU", val(sku), at)?;
        item.set("QTY", val(qty), at)?;
        item.set("PRICE", val(price), at)?;
        items.push(item);
    }
    order.set("ITEMS", Value::list(items), at)?; // a list is keyed "1".."n"
    println!("   first SKU => {}", text(&compile("ITEMS[1][\"SKU\"]")?.run(Some(order.clone()))?)?);
    println!("   total     => {}", text(&compile("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])")?.run(Some(order))?)?);
    println!("   0.10+0.20 => {}", text(&evaluate("0.10 + 0.20", None)?)?);
    // EXAMPLE-END context

    // 4 — reading results back ----------------------------------------------------------
    // A result is a Value: a scalar, children, both or neither.

    println!("4. reading results");
    let v = evaluate("SPLIT(\"a,b,c\", \",\")", None)?;
    println!("   size   => {}", v.size());
    println!("   keys   => {}", v.keys().join(","));
    println!("   [2]    => {}", text(&v.get("2").expect("a second element"))?);
    println!("   scalar => {}", text(&v)?); // scalar context: first child
    // A host bool prints differently in every language (true/1/True/T), and this
    // file's output has to be byte-identical to its siblings, so say it in SEL's
    // own spelling rather than the host's.
    let yes = evaluate("1 < 2", None)?.as_bool(at)?;
    println!("   bool   => {}", if yes { "TRUE" } else { "FALSE" });

    // 5 — the context is mutated, so rules hand values back -----------------------------

    println!("5. variables the rule set");
    // EXAMPLE-BEGIN variables
    let ctx = Value::none();
    ctx.set("QTY", val("3"), at)?;
    ctx.set("PRICE", val("19.99"), at)?;
    // A Value is a shared handle: the run assigns into the same record.
    compile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT")?.run(Some(ctx.clone()))?;
    for name in ["NET", "VAT", "GROSS"] {
        println!("   {name:<5} => {}", text(&ctx.get(name).expect("set by the rule"))?);
    }
    // EXAMPLE-END variables

    // 6 — errors ---------------------------------------------------------------------------
    // Every failure is a SelError carrying a stable code and the position of the
    // node that actually failed. Match on the code, never on the message.

    println!("6. errors");
    // EXAMPLE-BEGIN errors
    for src in ["3 + \"A\"", "NOSUCH(1)", "IF(1, \"a\", \"b\")", "ABORT(\"no stock\")"] {
        match evaluate(src, None) {
            Ok(_) => println!("   {src:<17} => no error"),
            Err(e) => println!("   {src:<17} => {} at {}:{}", e.code, e.pos.line, e.pos.col),
        }
    }
    // EXAMPLE-END errors

    // 7 — which fields does this rule read? ------------------------------------------------
    // Found statically, without running it.

    println!("7. dependencies");
    // EXAMPLE-BEGIN dependencies
    let deps = compile("T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"")?.dependencies()?;
    println!("   {}", deps.join(" "));
    // EXAMPLE-END dependencies
    Ok(())
}
