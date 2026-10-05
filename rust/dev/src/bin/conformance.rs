// Conformance test runner for SEL in Rust.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use regex::Regex;
use sel_lang::{compile, Pos, SelError, Value, Kind};
use sel_lang_dev::{read_text, trim_section as trim_ws};

static HEADER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^###\s+name:\s*(\S+)\s*$").unwrap());
static ERROR_EXPECT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(\S+)(?:\s+at\s+(\d+):(\d+))?$").unwrap());

#[derive(Debug, Clone)]
struct TestCase {
    name: String,
    at: String,
    setup: String,
    source: String,
    expect: String,
}

fn parse_selt(text: &str, file: &str) -> Result<Vec<TestCase>, String> {
    let mut cases = Vec::new();
    let mut cur: Option<TestCase> = None;
    let mut section = "";
    let mut setup_lines = Vec::new();
    let mut source_lines = Vec::new();
    let mut expect_lines = Vec::new();

    // Split on LF only: a CR is case content (conformance/README.md), and
    // str::lines() would drop one before every LF.
    for (idx, line) in text.split('\n').enumerate() {
        let at = format!("{}:{}", file, idx + 1);
        if line.starts_with("### ") {
            if let Some(caps) = HEADER.captures(line) {
                if let Some(mut c) = cur.take() {
                    c.setup = trim_ws(&setup_lines.join("\n")).to_string();
                    c.source = trim_ws(&source_lines.join("\n")).to_string();
                    c.expect = trim_ws(&expect_lines.join("\n")).to_string();
                    cases.push(c);
                }
                cur = Some(TestCase {
                    name: caps[1].to_string(),
                    at,
                    setup: String::new(),
                    source: String::new(),
                    expect: String::new(),
                });
                section = "";
                setup_lines.clear();
                source_lines.clear();
                expect_lines.clear();
                continue;
            } else {
                return Err(format!("{}: malformed case header", at));
            }
        }
        if line == "===" {
            if let Some(mut c) = cur.take() {
                c.setup = trim_ws(&setup_lines.join("\n")).to_string();
                c.source = trim_ws(&source_lines.join("\n")).to_string();
                c.expect = trim_ws(&expect_lines.join("\n")).to_string();
                cases.push(c);
            }
            section = "";
            setup_lines.clear();
            source_lines.clear();
            expect_lines.clear();
            continue;
        }
        if line.starts_with("--- ") {
            if cur.is_none() {
                return Err(format!("{}: section outside a case", at));
            }
            let s = trim_ws(&line[4..]);
            if s != "setup" && s != "source" && s != "expect" && s != "note" {
                return Err(format!("{}: unknown section {}", at, s));
            }
            section = match s {
                "setup" => "setup",
                "source" => "source",
                "expect" => "expect",
                _ => "note",
            };
            continue;
        }
        if cur.is_none() || section.is_empty() || section == "note" {
            continue;
        }
        match section {
            "setup" => setup_lines.push(line),
            "source" => source_lines.push(line),
            "expect" => expect_lines.push(line),
            _ => {}
        }
    }
    if let Some(mut c) = cur.take() {
        c.setup = trim_ws(&setup_lines.join("\n")).to_string();
        c.source = trim_ws(&source_lines.join("\n")).to_string();
        c.expect = trim_ws(&expect_lines.join("\n")).to_string();
        cases.push(c);
    }

    for c in &cases {
        if c.source.is_empty() {
            return Err(format!("{}: case {} has no --- source", c.at, c.name));
        }
        if c.expect.is_empty() {
            return Err(format!("{}: case {} has no --- expect", c.at, c.name));
        }
    }
    Ok(cases)
}

fn unescape(lit: &str, at: &str) -> Result<String, String> {
    if lit.len() < 2 || !lit.starts_with('"') || !lit.ends_with('"') {
        return Err(format!("{}: expected a quoted string, got {}", at, lit));
    }
    let body = &lit[1..lit.len() - 1];
    let bytes = body.as_bytes();
    let mut out: Vec<u8> = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'\\' {
            out.push(bytes[i]);
            i += 1;
            continue;
        }
        i += 1;
        if i >= bytes.len() {
            return Err(format!("{}: trailing backslash", at));
        }
        match bytes[i] {
            b'\\' => out.push(b'\\'),
            b'"' => out.push(b'"'),
            b'n' => out.push(b'\n'),
            b't' => out.push(b'\t'),
            b'r' => out.push(b'\r'),
            b'u' => {
                if i + 5 > bytes.len() {
                    return Err(format!("{}: short unicode escape", at));
                }
                let hex_str = std::str::from_utf8(&bytes[i + 1..i + 5])
                    .map_err(|e| format!("{}: invalid unicode escape: {}", at, e))?;
                let cp = u32::from_str_radix(hex_str, 16)
                    .map_err(|e| format!("{}: invalid unicode escape: {}", at, e))?;
                let ch = char::from_u32(cp)
                    .ok_or_else(|| format!("{}: invalid unicode scalar value: {:x}", at, cp))?;
                let mut buf = [0u8; 4];
                out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
                i += 4;
            }
            e => return Err(format!("{}: bad escape \\{}", at, e as char)),
        }
        i += 1;
    }
    String::from_utf8(out).map_err(|e| format!("{}: invalid utf8: {}", at, e))
}

fn json_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            c if (c as u32) < 0x20 => {
                use std::fmt::Write;
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn describe(v: &Value) -> String {
    if v.kind() == Kind::None && v.size() == 0 {
        return "none".to_string();
    }
    if v.size() > 0 {
        return format!("tree {}", v.dump().unwrap_or_default());
    }
    match v.kind() {
        Kind::Text => format!("text {}", json_quote(&v.scalar())),
        Kind::Bin => {
            let d = v.dump().unwrap_or_default();
            format!("bin {}", d.strip_prefix('b').unwrap_or(&d))
        }
        Kind::Bool => format!("bool {}", v.dump().unwrap_or_default()),
        _ => "none".to_string(),
    }
}

fn check_expect(expect: &str, val: Option<&Value>, err: Option<&SelError>, at: &str) -> String {
    let (form, rest) = match expect.find(' ') {
        Some(idx) => (&expect[..idx], expect[idx + 1..].trim()),
        None => (expect, ""),
    };

    if form == "error" {
        if val.is_some() {
            return format!("expected {}, got value {}", expect, describe(val.unwrap()));
        }
        let err = match err {
            Some(e) => e,
            None => return format!("expected {}, got no error and no value", expect),
        };
        let caps = match ERROR_EXPECT.captures(rest) {
            Some(c) => c,
            None => return format!("{}: malformed error expectation", at),
        };
        let expected_code = &caps[1];
        if err.code != expected_code {
            return format!("expected {}, got {} ({})", expected_code, err.code, err.message);
        }
        if let Some(m_line) = caps.get(2) {
            let m_col = caps.get(3).unwrap().as_str();
            let got_at = format!("{}:{}", err.pos.line, err.pos.col);
            let want_at = format!("{}:{}", m_line.as_str(), m_col);
            if got_at != want_at {
                return format!("expected {} at {}, got it at {}", expected_code, want_at, got_at);
            }
        }
        return String::new();
    }

    if let Some(err) = err {
        return format!("expected {}, got {} ({})", expect, err.code, err.message);
    }
    let val = match val {
        Some(v) => v,
        None => return format!("expected {}, got none", expect),
    };

    match form {
        "text" => {
            if val.kind() != Kind::Text || val.size() > 0 || val.is_list() {
                return format!("wanted text, got {}", describe(val));
            }
            let unquoted = match unescape(rest, at) {
                Ok(u) => u,
                Err(e) => return e,
            };
            if val.scalar() != unquoted {
                return format!("got {}", describe(val));
            }
            String::new()
        }
        "num" => {
            if val.kind() != Kind::Text || val.size() > 0 || val.is_list() {
                return format!("wanted a number, got {}", describe(val));
            }
            if val.scalar() != rest {
                return format!("got {}", describe(val));
            }
            String::new()
        }
        "bin" => {
            if val.kind() != Kind::Bin || val.size() > 0 || val.is_list() {
                return format!("wanted binary, got {}", describe(val));
            }
            let d = val.dump().unwrap_or_default();
            if d.strip_prefix('b').unwrap_or(&d) != rest {
                return format!("got {}", describe(val));
            }
            String::new()
        }
        "bool" => {
            if val.kind() != Kind::Bool || val.size() > 0 || val.is_list() {
                return format!("wanted a boolean, got {}", describe(val));
            }
            let got = if val.as_bool(Pos::default()).unwrap_or(false) { "TRUE" } else { "FALSE" };
            if got != rest {
                return format!("got {}", describe(val));
            }
            String::new()
        }
        "none" => {
            if val.kind() == Kind::None && val.size() == 0 {
                return String::new();
            }
            format!("got {}", describe(val))
        }
        "tree" => {
            let d = val.dump().unwrap_or_default();
            if d != rest {
                return format!("got tree {}", d);
            }
            String::new()
        }
        _ => format!("{}: unknown expectation form {}", at, form),
    }
}

enum RunResult {
    Val(Value),
    Err(SelError),
    SuiteError(String),
}

fn run_case(c: &TestCase) -> RunResult {
    let root = Value::none();
    if !c.setup.is_empty() {
        let mut prog = match compile(&c.setup) {
            Ok(p) => p,
            Err(e) => return RunResult::SuiteError(format!("setup compile failed: {}", e)),
        };
        if let Err(e) = prog.run(Some(root.clone())) {
            return RunResult::SuiteError(format!("setup run failed: {}", e));
        }
    }

    let mut prog = match compile(&c.source) {
        Ok(p) => p,
        Err(e) => return RunResult::Err(e),
    };

    match prog.run(Some(root)) {
        Ok(res) => RunResult::Val(res),
        Err(e) => RunResult::Err(e),
    }
}

fn add_path(p: &Path, files: &mut Vec<PathBuf>) {
    if p.is_dir() {
        if let Ok(entries) = fs::read_dir(p) {
            let mut selt_files = Vec::new();
            for entry in entries.flatten() {
                let ep = entry.path();
                if ep.is_file() && ep.extension().and_then(|s| s.to_str()) == Some("selt") {
                    selt_files.push(ep);
                }
            }
            selt_files.sort();
            files.extend(selt_files);
        }
    } else {
        files.push(p.to_path_buf());
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut files = Vec::new();

    if !args.is_empty() {
        for arg in &args {
            let p = Path::new(arg);
            if p.is_dir() {
                add_path(p, &mut files);
            } else if let Err(e) = fs::metadata(p) {
                eprintln!("cannot read {}: {}", arg, e);
                std::process::exit(1);
            } else {
                files.push(p.to_path_buf());
            }
        }
    } else {
        let mut root = PathBuf::from("conformance");
        if !root.exists() {
            root = PathBuf::from("../conformance");
        }
        if !root.exists() {
            root = PathBuf::from("../../conformance");
        }
        add_path(&root, &mut files);
    }

    let mut n_pass = 0;
    struct Failure {
        c: TestCase,
        problem: String,
    }
    let mut failures = Vec::new();
    let mut suite_errors = Vec::new();
    let mut seen: HashMap<String, String> = HashMap::new();

    for path in &files {
        let shortname = path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_string();
        let data = match read_text(path) {
            Ok(d) => d,
            Err(e) => {
                suite_errors.push(e);
                continue;
            }
        };
        let cases = match parse_selt(&data, &shortname) {
            Ok(cs) => cs,
            Err(e) => {
                suite_errors.push(format!("{}: parse error: {}", path.display(), e));
                continue;
            }
        };


        for c in cases {
            if let Some(prev) = seen.get(&c.name) {
                suite_errors.push(format!("{}: duplicate case name {} (also {})", c.at, c.name, prev));
                continue;
            }
            seen.insert(c.name.clone(), c.at.clone());

            let res = run_case(&c);
            let (val, err) = match &res {
                RunResult::Val(v) => (Some(v), None),
                RunResult::Err(e) => (None, Some(e)),
                RunResult::SuiteError(msg) => {
                    suite_errors.push(format!("{}: {}: setup failed: {}", c.at, c.name, msg));
                    continue;
                }
            };

            let problem = check_expect(&c.expect, val, err, &c.at);
            if problem.is_empty() {
                n_pass += 1;
            } else {
                failures.push(Failure { c, problem });
            }
        }
    }

    for f in &failures {
        let indent = "\n             ";
        println!("FAIL {}  ({})", f.c.name, f.c.at);
        println!("     source: {}", f.c.source.replace('\n', indent));
        println!("     want:   {}", f.c.expect);
        println!("     {}", f.problem);
    }
    for e in &suite_errors {
        println!("SUITE {}", e);
    }

    println!("\n{} passed, {} failed, {} suite errors", n_pass, failures.len(), suite_errors.len());
    if n_pass + failures.len() == 0 {
        // A run that executed nothing proves nothing: an empty file or a
        // directory without .selt files is a failure, not a pass.
        eprintln!("no cases were run");
        std::process::exit(1);
    }
    if !failures.is_empty() || !suite_errors.is_empty() {
        std::process::exit(1);
    }
}
