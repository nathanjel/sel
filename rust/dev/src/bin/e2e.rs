// Drives examples/order-validation.sel through the Rust host API and prints a
// canonical report. examples/e2e.mjs and cpp/bin/e2e.cpp do the same.

use std::fs;
use sel_lang::{compile, Pos, Value};

struct Item {
    sku: &'static str,
    qty: &'static str,
    price: &'static str,
}

struct Scenario {
    name: &'static str,
    customer: &'static str,
    postcode: &'static str,
    credit_limit: &'static str,
    items: Vec<Item>,
}

fn build(s: &Scenario) -> Value {
    let ctx = Value::none();
    let _ = ctx.set("CUSTOMER", Value::text_owned(s.customer.to_string()), Pos::default());
    let _ = ctx.set("POSTCODE", Value::text_owned(s.postcode.to_string()), Pos::default());
    let _ = ctx.set("CREDIT_LIMIT", Value::text_owned(s.credit_limit.to_string()), Pos::default());

    let mut items = Vec::with_capacity(s.items.len());
    for it in &s.items {
        let item = Value::none();
        let _ = item.set("SKU", Value::text_owned(it.sku.to_string()), Pos::default());
        let _ = item.set("QTY", Value::text_owned(it.qty.to_string()), Pos::default());
        let _ = item.set("PRICE", Value::text_owned(it.price.to_string()), Pos::default());
        items.push(item);
    }
    let _ = ctx.set("ITEMS", Value::list(items), Pos::default());
    ctx
}

fn main() {
    let mut path = "examples/order-validation.sel";
    let data = match fs::read_to_string(path) {
        Ok(d) => d,
        Err(_) => {
            path = "../examples/order-validation.sel";
            match fs::read_to_string(path) {
                Ok(d) => d,
                Err(_) => {
                    eprintln!("run from the repository root: examples/order-validation.sel not found");
                    std::process::exit(2);
                }
            }
        }
    };

    let mut prog = match compile(&data) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("compile error: {:?}", e);
            std::process::exit(1);
        }
    };

    let scenarios = vec![
        Scenario {
            name: "valid order",
            customer: "Zażółć Gęślą",
            postcode: "31-874",
            credit_limit: "1000.00",
            items: vec![
                Item { sku: "AB-1234", qty: "3", price: "19.99" },
                Item { sku: "CD-5678", qty: "1", price: "5.01" },
            ],
        },
        Scenario {
            name: "blank customer",
            customer: "   ",
            postcode: "31-874",
            credit_limit: "1000.00",
            items: vec![Item { sku: "AB-1234", qty: "1", price: "1.00" }],
        },
        Scenario {
            name: "bad postcode",
            customer: "Anna",
            postcode: "318744",
            credit_limit: "1000.00",
            items: vec![Item { sku: "AB-1234", qty: "1", price: "1.00" }],
        },
        Scenario {
            name: "no lines",
            customer: "Anna",
            postcode: "31-874",
            credit_limit: "1000.00",
            items: vec![],
        },
        Scenario {
            name: "zero quantity",
            customer: "Anna",
            postcode: "31-874",
            credit_limit: "1000.00",
            items: vec![
                Item { sku: "AB-1234", qty: "1", price: "1.00" },
                Item { sku: "CD-5678", qty: "0", price: "2.00" },
            ],
        },
        Scenario {
            name: "malformed sku",
            customer: "Anna",
            postcode: "31-874",
            credit_limit: "1000.00",
            items: vec![Item { sku: "oops", qty: "1", price: "1.00" }],
        },
        Scenario {
            name: "over credit limit",
            customer: "Anna",
            postcode: "31-874",
            credit_limit: "10.00",
            items: vec![Item { sku: "AB-1234", qty: "3", price: "19.99" }],
        },
        Scenario {
            name: "exact-cent arithmetic",
            customer: "Anna",
            postcode: "31-874",
            credit_limit: "0.30",
            items: vec![
                Item { sku: "AB-1234", qty: "1", price: "0.10" },
                Item { sku: "CD-5678", qty: "1", price: "0.20" },
            ],
        },
    ];

    let mut out = "dependencies:".to_string();
    if let Ok(deps) = prog.dependencies() {
        for d in deps {
            out.push(' ');
            out.push_str(&d);
        }
    }
    out.push('\n');

    for s in &scenarios {
        let ctx = build(s);
        let result = match prog.run(Some(ctx)) {
            Ok(v) => v.dump().unwrap_or_default(),
            Err(e) => format!("!{}@{}:{}", e.code, e.pos.line, e.pos.col),
        };
        let mut name = s.name.to_string();
        while name.len() < 24 {
            name.push(' ');
        }
        out.push_str(&name);
        out.push(' ');
        out.push_str(&result);
        out.push('\n');
    }

    print!("{}", out);
}
