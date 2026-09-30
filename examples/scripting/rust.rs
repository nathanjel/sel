// Scripting an application's behaviour -- functions the host provides, from Rust.
//
//   bash rust/build.sh && rust/build/example-scripting      (from the repository root)
//
// SEL has no way to reach the world on its own, and that is the point: the
// application decides what a script may touch by registering functions. Here
// the warehouse gets four -- STOCK and WEIGHT to read the catalogue, RESERVE to
// take stock, NOTIFY to queue a message -- and fulfil.sel, a file the warehouse
// team owns, decides per order whether to ship, how, and whom to tell. The
// application stays the same when the policy changes.
//
// A registered function is strict: its arguments arrive evaluated, left to
// right, through the same typed readers the builtins use, so a script passing
// the wrong kind gets the usual error at the usual position.
//
// The files beside this one print byte-identical output.
//
// The visible differences here: Rust, like C++, has no from_native, so each
// order is built into a Value child by child, the way examples/plain/ builds
// one. And the function registry is process-wide and must be Send + Sync, so
// the state the functions share lives in statics behind a Mutex. Reading
// fulfil.sel can fail in a way that is not SEL's, hence main's boxed error.

use std::collections::BTreeMap;
use std::error::Error;
use std::fs;
use std::sync::{LazyLock, Mutex};

use sel_lang::{compile, register_function, Pos, Value};

static INVENTORY: LazyLock<Mutex<BTreeMap<&str, i64>>> =
    LazyLock::new(|| Mutex::new(BTreeMap::from([("LAMP-01", 4), ("DESK-02", 1), ("CHAIR-03", 6)])));
static WEIGHTS: LazyLock<BTreeMap<&str, &str>> =
    LazyLock::new(|| BTreeMap::from([("LAMP-01", "1.6"), ("DESK-02", "28.0"), ("CHAIR-03", "7.5")]));
static OUTBOX: Mutex<Vec<String>> = Mutex::new(Vec::new());

fn val(s: &str) -> Value {
    Value::text_owned(s.to_string())
}

fn main() -> Result<(), Box<dyn Error>> {
    let at = Pos::default();

    // EXAMPLE-BEGIN register
    register_function("STOCK", 1, 1, |args| {
        let sku = args.text(0)?;
        let left = INVENTORY.lock().unwrap().get(sku.as_str()).copied();
        Ok(Value::int(left.unwrap_or(0)))
    })?;

    register_function("RESERVE", 2, 2, |args| {
        let sku = args.text(0)?;
        let qty = args.non_neg_int(1)?;
        let mut inventory = INVENTORY.lock().unwrap();
        match inventory.get_mut(sku.as_str()) {
            Some(left) if *left >= qty => {
                *left -= qty;
                Ok(Value::bool(true))
            }
            _ => Ok(Value::bool(false)),
        }
    })?;

    register_function("WEIGHT", 1, 1, |args| {
        Ok(val(WEIGHTS.get(args.text(0)?.as_str()).copied().unwrap_or("0")))
    })?;

    register_function("NOTIFY", 2, 2, |args| {
        let message = format!("{}: {}", args.text(0)?, args.text(1)?);
        OUTBOX.lock().unwrap().push(message);
        Ok(Value::bool(true))
    })?;
    // EXAMPLE-END register

    // EXAMPLE-BEGIN run
    // After registering: names resolve now.
    let mut fulfil = compile(&fs::read_to_string("examples/scripting/fulfil.sel")?)?;

    println!("1. the script reads {}", fulfil.dependencies()?.join(", "));
    println!("2. orders");
    let orders = [
        ("A-1", "PL", vec![("LAMP-01", "2"), ("CHAIR-03", "1")]),
        ("A-2", "DE", vec![("DESK-02", "1"), ("CHAIR-03", "2")]),
        ("A-3", "PL", vec![("LAMP-01", "3")]),
        ("A-4", "PL", vec![("LAMP-01", "two")]),
    ];
    for (order, country, lines) in orders {
        let ctx = Value::none();
        ctx.set("ORDER", val(order), at)?;
        ctx.set("COUNTRY", val(country), at)?;
        let mut items = Vec::new();
        for (sku, qty) in lines {
            let item = Value::none();
            item.set("sku", val(sku), at)?;
            item.set("qty", val(qty), at)?;
            items.push(item);
        }
        ctx.set("ITEMS", Value::list(items), at)?;
        let decision = match fulfil.run(Some(ctx)).and_then(|v| v.as_text(at)) {
            Ok(decision) => decision,
            Err(e) => format!("{} at {}:{}", e.code, e.pos.line, e.pos.col),
        };
        println!("   {order}  {decision}");
    }
    // EXAMPLE-END run

    println!("3. outbox");
    for message in OUTBOX.lock().unwrap().iter() {
        println!("   {message}");
    }
    println!("4. stock left");
    for (sku, left) in INVENTORY.lock().unwrap().iter() { // a BTreeMap is sorted
        println!("   {sku:<9} {left}");
    }
    Ok(())
}
