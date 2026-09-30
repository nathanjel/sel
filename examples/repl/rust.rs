// A read-eval-print loop -- the whole of it, in Rust.
//
//   bash rust/build.sh
//   rust/build/example-repl
//   rust/build/example-repl < examples/repl/session.txt
//
// One context lives across lines, so a variable assigned on one line is there
// on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
// expression reads, and `:reset` empties the context. Errors print their code
// and position -- the message is human text and may differ between hosts; the
// code and the position may not.
//
// The files beside this one print byte-identical output for the session in
// session.txt.
//
// A SEL failure is printed and the loop goes on; what `?` carries to main is
// only a failure to read standard input, which is not SEL's, hence the boxed
// error type.

use std::error::Error;
use std::io;

use sel_lang::{compile, Pos, SelError, Value};

// EXAMPLE-BEGIN repl
fn show(value: &Value) -> Result<String, SelError> {
    let at = Pos::default();
    if value.is_bool() {
        return Ok(if value.as_bool(at)? { "TRUE" } else { "FALSE" }.to_string());
    }
    if value.is_null() {
        return Ok("NULL".to_string());
    }
    if value.size() > 0 || value.is_bin() {
        return value.dump();
    }
    value.as_text(at)
}

// One line: a command, or an expression run against the context. A SEL failure
// comes back as the Err, to be printed where the line was read.
fn respond(line: &str, context: &mut Value) -> Result<(), SelError> {
    if line == ":reset" {
        *context = Value::none();
    } else if let Some(expr) = line.strip_prefix(":deps ") {
        println!("{}", compile(expr)?.dependencies()?.join(" "));
    } else {
        println!("{}", show(&compile(line)?.run(Some(context.clone()))?)?);
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut context = Value::none();
    for line in io::stdin().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        println!("sel> {line}");
        if let Err(e) = respond(&line, &mut context) {
            println!("{} at {}:{}", e.code, e.pos.line, e.pos.col);
        }
    }
    Ok(())
}
// EXAMPLE-END repl
