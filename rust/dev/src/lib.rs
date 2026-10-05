//! What the harness binaries share: reading their input files the one way the
//! corpus and case formats define, and the CLI's rendering of a value.
//!
//! Never published; the binaries in `src/bin` link it beside `sel-lang`.

use sel_lang::{Kind, Pos, Value};
use std::path::Path;

/// A runner's input file, read as bytes and decoded as UTF-8 with no newline
/// translation: a CR anywhere, a CR before an LF included, is part of the
/// text. A missing, unreadable or non-UTF-8 file -- or a directory -- is an
/// error reading `cannot read <path>: <reason>`.
pub fn read_text(path: impl AsRef<Path>) -> Result<String, String> {
    let path = path.as_ref();
    let bytes = std::fs::read(path).map_err(|e| format!("cannot read {}: {}", path.display(), e))?;
    String::from_utf8(bytes).map_err(|e| format!("cannot read {}: {}", path.display(), e))
}

/// The records of a batch corpus: a line beginning `### ` starts a record and
/// everything up to the next such line is its source, with exactly one
/// trailing `\n` removed -- never a `\r`, never a second `\n`. Text before the
/// first marker belongs to no record. The same five lines as
/// `tools/run-batch.mjs`.
pub fn read_corpus(text: &str) -> Vec<String> {
    let mut records: Vec<Vec<&str>> = Vec::new();
    for line in text.split('\n') {
        if line.starts_with("### ") {
            records.push(Vec::new());
        } else if let Some(record) = records.last_mut() {
            record.push(line);
        }
    }
    records
        .into_iter()
        .map(|lines| {
            let mut source = lines.join("\n");
            if source.ends_with('\n') {
                source.pop();
            }
            source
        })
        .collect()
}

/// Strips the leading and trailing runs of the four characters a `.selt`
/// section trims -- space, TAB, CR and LF -- and no others
/// (conformance/README.md).
pub fn trim_section(s: &str) -> &str {
    s.trim_matches(|c: char| matches!(c, ' ' | '\t' | '\r' | '\n'))
}

/// The `sel` CLI's rendering of a result: a scalar text bare, a boolean as
/// TRUE/FALSE, BIN as `bin:<hex>`, anything else as its dump.
pub fn render(v: &Value) -> String {
    if v.size() == 0 {
        match v.kind() {
            Kind::Text => return v.scalar(),
            Kind::Bool => {
                return if v.as_bool(Pos::default()).unwrap_or(false) { "TRUE" } else { "FALSE" }.to_string();
            }
            Kind::Bin => {
                let d = v.dump().unwrap_or_default();
                if let Some(hex) = d.strip_prefix('b') {
                    return format!("bin:{}", hex);
                }
            }
            _ => {}
        }
    }
    v.dump().unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corpus_records_keep_their_crs_and_lose_exactly_one_newline() {
        // A CR before an LF is program text, and a last record ending in a
        // blank line keeps that line's newline.
        let corpus = "### a\n1 +\r\n2\n### b\n(1\n\n";
        assert_eq!(read_corpus(corpus), vec!["1 +\r\n2".to_string(), "(1\n".to_string()]);
        // Without the terminating newline nothing more is removed.
        assert_eq!(read_corpus("### a\nLEN(\"a\r\nb\")"), vec!["LEN(\"a\r\nb\")".to_string()]);
        // A lone CR at a line end, and text before the first marker.
        assert_eq!(read_corpus("junk\n### a\n1 ==\r\n### b\n\n"), vec!["1 ==\r".to_string(), "".to_string()]);
        assert!(read_corpus("").is_empty());
    }

    #[test]
    fn section_trim_takes_four_characters_only() {
        assert_eq!(trim_section(" \t\r\n1\r\n \t"), "1");
        assert_eq!(trim_section("\u{A0}1\u{B}"), "\u{A0}1\u{B}");
    }
}
