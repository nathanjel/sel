// Runs a corpus of SEL programs and prints one canonical line each.
// Matches tools/run-batch.mjs and cpp/bin/batch.cpp.

use sel_lang::compile;
use sel_lang_dev::{read_corpus, read_text, render};
use std::io::{self, Write};

fn main() {
    let mut show = false;
    let mut path = String::new();
    for arg in std::env::args().skip(1) {
        if arg == "--show" {
            show = true;
        } else {
            path = arg;
        }
    }

    if path.is_empty() {
        eprintln!("usage: batch [--show] corpus.selc");
        std::process::exit(2);
    }

    let text = match read_text(&path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("{}", e);
            std::process::exit(2);
        }
    };

    let corpus = read_corpus(&text);
    if corpus.is_empty() {
        eprintln!("{}: no records (a record starts at a line beginning `### `)", path);
        std::process::exit(1);
    }
    let mut lines = Vec::with_capacity(corpus.len());

    for src in &corpus {
        match compile(src).and_then(|mut prog| prog.run(None)) {
            Ok(val) => {
                if show {
                    lines.push(render(&val));
                } else {
                    lines.push(val.dump().unwrap_or_default());
                }
            }
            Err(e) => {
                if show {
                    lines.push(format!("!{}", e.code));
                } else {
                    lines.push(format!("!{}@{}:{}", e.code, e.pos.line, e.pos.col));
                }
            }
        }
    }

    let mut stdout = io::stdout().lock();
    for (i, l) in lines.iter().enumerate() {
        if i > 0 {
            let _ = stdout.write_all(b"\n");
        }
        let escaped = l.replace('\n', "\\n");
        let _ = stdout.write_all(escaped.as_bytes());
    }
    let _ = stdout.write_all(b"\n");
}
