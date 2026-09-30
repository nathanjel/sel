// Form validation -- one rule set, the same verdicts in every host, from Rust.
//
//   bash rust/build.sh && rust/build/example-validation
//
// A checkout form's rules, one per field, written in SEL. The server compiles
// them once at start-up, so a rule that does not parse fails the deployment
// rather than a customer. Each rule answers "" when the field is fine and
// ABORT("message") when it is not, so there are two kinds of failure and they
// are told apart by code: E_ABORT is a message for the user, anything else means
// the rule itself is broken and the user should never see it. dependencies()
// tells a browser which rules to re-run when a field changes -- and the browser
// runs the very same rule text, in JavaScript.
//
// The files beside this one print byte-identical output.
//
// The visible difference here is that Rust, like C++, has no from_native: a
// submitted form is a slice of (field, text) pairs, and the context is built
// from it one set() at a time. And Program::run takes `&mut self` -- a compiled
// program keeps caches it fills while it runs -- so validate() borrows the
// rule set mutably.

use std::collections::BTreeMap;

use sel_lang::{compile, Pos, Program, SelError, Value};

// EXAMPLE-BEGIN rules
const RULES: &[(&str, &str)] = &[
    ("name",     r#"IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")"#),
    ("email",    concat!(r#"IF(RMATCH('^[^@ ]+@[^@ ]+\.[a-z]{2,}$', TRIM(EMAIL), "i"), "","#,
                         r#" ABORT("{EMAIL} does not look like an e-mail address"))"#)),
    ("postcode", concat!(r#"COND(COUNTRY $== "PL" AND NOT RMATCH('^\d{2}-\d{3}$', POSTCODE),"#,
                         r#"       ABORT("Polish postcodes look like 00-000"),"#,
                         r#"     COUNTRY $== "DE" AND NOT RMATCH('^\d{5}$', POSTCODE),"#,
                         r#"       ABORT("German postcodes have five digits"),"#,
                         r#"     "")"#)),
    ("quantity", concat!(r#"IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,"#,
                         r#" ABORT("Choose between 1 and {STOCK}"), "")"#)),
    ("total",    concat!(r#"TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);"#,
                         r#" IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")"#)),
];
// EXAMPLE-END rules

type Rules = Vec<(&'static str, Program)>;

// EXAMPLE-BEGIN validate
type Form<'a> = [(&'a str, &'a str)];              // field, what was typed
type Problems = Vec<(&'static str, String)>;       // field, message

fn validate(compiled: &mut Rules, form: &Form<'_>) -> Result<Problems, SelError> {
    let mut problems = Problems::new();
    for (field, rule) in compiled.iter_mut() {
        let ctx = Value::none();
        for &(name, typed) in form {
            ctx.set(name, Value::text_owned(typed.to_string()), Pos::default())?;
        }
        let verdict = match rule.run(Some(ctx)).and_then(|v| v.as_text(Pos::default())) {
            Ok(verdict) => verdict,
            // E_ABORT is the rule speaking to the user; anything else is a
            // broken rule or data it cannot read -- log it, show a generic line.
            Err(e) if e.code == "E_ABORT" => e.message,
            Err(e) => format!("could not be checked ({})", e.code),
        };
        if !verdict.is_empty() {
            problems.push((*field, verdict));
        }
    }
    Ok(problems)
}
// EXAMPLE-END validate

const SUBMISSIONS: &[&Form<'static>] = &[
    &[("NAME", "Anna Nowak"), ("EMAIL", "anna@example.pl"), ("COUNTRY", "PL"), ("POSTCODE", "31-874"),
      ("QTY", "2"), ("STOCK", "5"), ("PRICE", "19.99"), ("DISCOUNT", "0.10"), ("CREDIT_LIMIT", "100.00")],
    &[("NAME", "   "), ("EMAIL", "bruno(at)example.de"), ("COUNTRY", "DE"), ("POSTCODE", "1011"),
      ("QTY", "9"), ("STOCK", "5"), ("PRICE", "19.99"), ("DISCOUNT", "0"), ("CREDIT_LIMIT", "100.00")],
    &[("NAME", "Chloé"), ("EMAIL", "CHLOE@EXAMPLE.FR "), ("COUNTRY", "FR"), ("POSTCODE", "69002"),
      ("QTY", "4"), ("STOCK", "5"), ("PRICE", "29.99"), ("DISCOUNT", "0.05"), ("CREDIT_LIMIT", "100.00")],
    &[("NAME", "Dawid"), ("EMAIL", "dawid@example.pl"), ("COUNTRY", "PL"), ("POSTCODE", "00-950"),
      ("QTY", "1"), ("STOCK", "5"), ("PRICE", "twenty"), ("DISCOUNT", "0"), ("CREDIT_LIMIT", "100.00")],
];

fn main() -> Result<(), SelError> {
    // 1 - compile once, at start-up ----------------------------------------------------

    // EXAMPLE-BEGIN compile
    let mut compiled = Rules::new();
    for &(field, source) in RULES {
        compiled.push((field, compile(source)?));
    }
    // EXAMPLE-END compile
    println!("1. the rule set");
    for (field, rule) in &compiled {
        println!("   {field:<9} reads {}", rule.dependencies()?.join(" "));
    }

    // 2 - what to re-check when a field changes ----------------------------------------

    println!("2. re-check on change");
    let mut watch: BTreeMap<String, Vec<&str>> = BTreeMap::new(); // sorted by name
    for (field, rule) in &compiled {
        for name in rule.dependencies()? {
            watch.entry(name).or_default().push(*field);
        }
    }
    for (name, fields) in &watch {
        println!("   {name:<13} {}", fields.join(", "));
    }

    // 3 - validating submissions -----------------------------------------------------------

    println!("3. submissions");
    for (n, form) in (1..).zip(SUBMISSIONS) {
        let problems = validate(&mut compiled, form)?;
        if problems.is_empty() {
            println!("   #{n} accepted");
        }
        for (field, message) in &problems {
            println!("   #{n} {field:<9} {message}");
        }
    }
    Ok(())
}
