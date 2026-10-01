// Runs a corpus of SEL programs and prints one canonical line each.
// Matches tools/run-batch.mjs and cpp/bin/batch.cpp.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use sel_lang::{compile, Kind, Pos, Value};

fn read_corpus(reader: impl BufRead) -> Vec<String> {
    let mut records: Vec<Vec<String>> = Vec::new();
    let mut cur: Vec<String> = Vec::new();
    let mut started = false;

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.starts_with("### ") {
            if started {
                records.push(std::mem::take(&mut cur));
            }
            started = true;
            continue;
        }
        if started {
            cur.push(line);
        }
    }
    if started {
        records.push(cur);
    }

    let mut out = Vec::with_capacity(records.len());
    for lines in records {
        let mut joined = lines.join("\n");
        if joined.ends_with('\n') {
            joined.pop();
        }
        out.push(joined);
    }
    out
}

fn render(v: &Value) -> String {
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

    let file = match File::open(&path) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("cannot read {}: {}", path, e);
            std::process::exit(2);
        }
    };

    let corpus = read_corpus(BufReader::new(file));
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
