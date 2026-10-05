//! How `sel` prints a result (docs/usage/repl.md): a scalar text bare, a
//! boolean as TRUE or FALSE, a binary as `bin:<hex>`, anything else as its dump.
//!
//! A module of the `sel` binary, not of the library. The unpublished harness
//! crate (`dev/src/lib.rs`) compiles this same file for `batch --show`, so a
//! documentation example pasted into the CLI prints exactly what the
//! documentation claims.

use sel_lang::{Kind, Pos, Value};

pub fn show(v: &Value) -> String {
    if v.size() == 0 {
        match v.kind() {
            Kind::Text => return v.scalar(),
            Kind::Bool => {
                return if v.as_bool(Pos::default()).unwrap_or(false) { "TRUE" } else { "FALSE" }.to_string();
            }
            Kind::Bin => {
                let d = v.dump().unwrap_or_default();
                if !d.is_empty() {
                    return format!("bin:{}", &d[1..]);
                }
            }
            _ => {}
        }
    }
    v.dump().unwrap_or_default()
}
