// SEL command line: evaluate an expression, a file, or start a REPL. The same
// interface as every host's `sel` (docs/usage/repl.md).
//
//   sel -e 'EXPR'          evaluate and print
//   sel FILE               evaluate a file
//   sel --deps -e 'EXPR'   print the variables the expression reads, one per line
//   sel --functions        list the function table
//   sel                    REPL on stdin, keeping one context across lines
//   sel --help | -h        usage;  sel --version   the package version
//
// Usage errors exit 2 with `sel: ...` on stderr; a file that cannot be read
// exits 1 with `sel: cannot read <path>: ...`; an evaluation error exits 1 with
// `E_CODE at line L column C: message`.

use sel_lang::{compile, decode_utf8_source, function_names, Context, Kind, Pos, SelError, Value};
use std::ffi::OsString;
use std::io::{self, BufRead, IsTerminal, Write};

const USAGE: &str = "\
usage: sel -e EXPR          evaluate EXPR and print the result
       sel FILE             evaluate the program in FILE
       sel --deps -e EXPR   print what EXPR reads, one name per line (also with FILE)
       sel --functions      list the functions
       sel                  read-eval-print loop on stdin
       sel --help | --version
";

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
    eprintln!("{} at line {} column {}: {}", e.code, e.pos.line, e.pos.col, e.message);
}

fn usage_error(message: impl std::fmt::Display) -> ! {
    eprintln!("sel: {}", message);
    std::process::exit(2);
}

/// A REPL line is skipped only if it holds nothing but SEL whitespace (space,
/// TAB, CR, LF); NBSP, VT, U+3000 and the like are program text, and the lexer
/// says so (E_SYNTAX), as on the `-e` path.
fn is_blank(line: &[u8]) -> bool {
    line.iter().all(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
}

fn main() {
    let mut want_deps = false;
    let mut want_functions = false;
    let mut expr: Option<OsString> = None;
    let mut file: Option<OsString> = None;
    let mut args = std::env::args_os().skip(1);
    while let Some(arg) = args.next() {
        let source_given = expr.is_some() || file.is_some();
        match arg.to_str() {
            Some("--deps") => want_deps = true,
            Some("--functions") => want_functions = true,
            Some("-h") | Some("--help") => {
                print!("{}", USAGE);
                return;
            }
            Some("--version") => {
                println!("sel {}", env!("CARGO_PKG_VERSION"));
                return;
            }
            Some("-e") => match args.next() {
                None => usage_error("-e needs an expression"),
                Some(e) if source_given => usage_error(format!("unexpected argument {}", e.to_string_lossy())),
                Some(e) => expr = Some(e),
            },
            _ if arg.as_encoded_bytes().len() > 1 && arg.as_encoded_bytes()[0] == b'-' => {
                usage_error(format!("unknown option {}", arg.to_string_lossy()))
            }
            _ if source_given => usage_error(format!("unexpected argument {}", arg.to_string_lossy())),
            _ => file = Some(arg),
        }
    }

    if want_functions {
        for n in function_names() {
            println!("{}", n);
        }
        return;
    }

    // Bytes in, strictly: an invalid file or argument is E_UTF8 at its first
    // bad byte (SPEC §2), with no replacement and no newline translation.
    let source = if let Some(e) = expr {
        Some(decode_utf8_source(e.as_encoded_bytes()))
    } else if let Some(path) = file {
        let bytes = std::fs::read(&path).unwrap_or_else(|e| {
            eprintln!("sel: cannot read {}: {}", std::path::Path::new(&path).display(), e);
            std::process::exit(1);
        });
        Some(decode_utf8_source(&bytes))
    } else {
        None
    };

    let Some(source) = source else {
        if want_deps {
            usage_error("--deps needs -e EXPR or a FILE");
        }
        repl();
        return;
    };
    let result = source.and_then(|s| compile(&s)).and_then(|mut program| {
        if want_deps {
            // An empty list prints nothing at all, not an empty line.
            for d in program.dependencies()? {
                println!("{}", d);
            }
        } else {
            println!("{}", show(&program.run(None)?));
        }
        Ok(())
    });
    if let Err(e) = result {
        report(&e);
        std::process::exit(1);
    }
}

/// One context for the whole session, so assignments persist. The prompt is
/// written only to a terminal: on a pipe nothing but results and errors is.
fn repl() {
    let interactive = io::stdin().is_terminal();
    let prompt = || {
        if interactive {
            print!("sel> ");
            let _ = io::stdout().flush();
        }
    };
    let mut ctx = Context::new(Value::none());
    let mut stdin = io::stdin().lock();
    let mut line = Vec::new();
    prompt();
    loop {
        line.clear();
        match stdin.read_until(b'\n', &mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        if line.last() == Some(&b'\n') {
            line.pop();
        }
        if !is_blank(&line) {
            match decode_utf8_source(&line).and_then(|s| compile(&s)) {
                Ok(mut prog) => match prog.run_with_context(&mut ctx) {
                    Ok(val) => println!("{}", show(&val)),
                    Err(e) => report(&e),
                },
                Err(e) => report(&e),
            }
        }
        prompt();
    }
    if interactive {
        println!();
    }
}
