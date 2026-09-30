// SEL command line: evaluate an expression, a file, or start a REPL.
//
//   sel -e 'EXPR'          evaluate and print
//   sel file.sel           evaluate a file
//   sel --deps -e 'EXPR'   print the variables the expression reads
//   sel --functions        list the function table
//   sel                    REPL, keeping one context across lines

use sel_lang::{compile, decode_utf8_source, function_names, Context, Kind, Pos, SelError, Value};
use std::io::{self, BufRead, Write};

fn show(v: &Value) -> String {
    if v.size() == 0 {
        if v.kind() == Kind::Text {
            return v.scalar();
        }
        if v.kind() == Kind::Bool {
            return if v.as_bool(Pos::default()).unwrap_or(false) {
                "TRUE".to_string()
            } else {
                "FALSE".to_string()
            };
        }
        if v.kind() == Kind::Bin {
            let d = v.dump().unwrap_or_default();
            if !d.is_empty() {
                return format!("bin:{}", &d[1..]);
            }
        }
    }
    v.dump().unwrap_or_default()
}

fn report(e: &SelError) {
    eprintln!(
        "{} at line {} column {}: {}",
        e.code, e.pos.line, e.pos.col, e.message
    );
}

fn main() {
    let argl: Vec<std::ffi::OsString> = std::env::args_os().skip(1).collect();

    let mut want_deps = false;
    let mut args = Vec::new();
    for a in argl {
        if a == "--deps" {
            want_deps = true;
        } else {
            args.push(a);
        }
    }

    if !args.is_empty() && args[0] == "--functions" {
        for n in function_names() {
            println!("{}", n);
        }
        return;
    }

    let mut have_source = false;
    let mut source = String::new();
    if args.len() >= 2 && args[0] == "-e" {
        source = match decode_utf8_source(args[1].as_encoded_bytes()) {
            Ok(source) => source,
            Err(error) => {
                report(&error);
                std::process::exit(1);
            }
        };
        have_source = true;
    } else if !args.is_empty() {
        let bytes = match std::fs::read(&args[0]) {
            Ok(b) => b,
            Err(_) => {
                eprintln!("cannot read {}", std::path::Path::new(&args[0]).display());
                std::process::exit(1);
            }
        };
        source = match decode_utf8_source(&bytes) {
            Ok(s) => s,
            Err(e) => {
                report(&e);
                std::process::exit(1);
            }
        };
        have_source = true;
    }

    if have_source {
        match compile(&source) {
            Ok(mut program) => {
                if want_deps {
                    match program.dependencies() {
                        Ok(deps) => {
                            for d in deps {
                                println!("{}", d);
                            }
                        }
                        Err(e) => {
                            report(&e);
                            std::process::exit(1);
                        }
                    }
                } else {
                    match program.run(None) {
                        Ok(val) => println!("{}", show(&val)),
                        Err(e) => {
                            report(&e);
                            std::process::exit(1);
                        }
                    }
                }
            }
            Err(e) => {
                report(&e);
                std::process::exit(1);
            }
        }
        return;
    }

    // REPL: one context for the whole session, so assignments persist.
    let mut ctx = Context::new(Value::none());
    let stdin = io::stdin();
    print!("sel> ");
    let _ = io::stdout().flush();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            print!("sel> ");
            let _ = io::stdout().flush();
            continue;
        }
        match compile(&line) {
            Ok(mut prog) => match prog.run_with_context(&mut ctx) {
                Ok(val) => println!("{}", show(&val)),
                Err(e) => report(&e),
            },
            Err(e) => report(&e),
        }
        print!("sel> ");
        let _ = io::stdout().flush();
    }
    println!();
}
