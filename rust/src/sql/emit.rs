use std::collections::HashSet;

use crate::dec::{dec_format, dec_parse};
use crate::utf8::Pos;
use crate::value::{Kind, Value};
use crate::sql::errors::{refuse, SqlError};
use crate::sql::map::{check_numeric_guard, lexical};
use crate::sql::types::{Fragment, Mode, Part, SqlKind};

pub fn fill_slot(tpl: &str, slot: &str, value: &str) -> String {
    tpl.replace(slot, value)
}

fn parse_slot_num(s: &str) -> Option<usize> {
    if s == "0" {
        return Some(0);
    }
    crate::utf8::canonical_index(s, 3)
}

pub fn format_literal(dialect: &str, v: Option<&Value>, form: SqlKind, pos: Pos) -> Result<String, SqlError> {
    if form == SqlKind::Bool || v.is_some_and(|val| val.kind() == Kind::Bool) {
        let is_true = v.is_some_and(|val| val.as_bool(pos).unwrap_or(false));
        let key = if is_true { "true" } else { "false" };
        if let Some(lex) = lexical(dialect, key) {
            if let Some(s) = lex.as_str() {
                return Ok(s.to_string());
            }
        }
        return Ok(key.to_string());
    }

    if form == SqlKind::Bin || v.is_some_and(|val| val.kind() == Kind::Bin) {
        let tpl_val = lexical(dialect, "binaryLiteral");
        let tpl = match tpl_val.as_ref().and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s,
            _ => return refuse("E_SQL_UNSUPPORTED", format!("dialect {} has no binary literal syntax", dialect), pos),
        };
        let bytes = v.map_or_else(Vec::new, |val| val.as_bytes(pos).unwrap_or_default());
        let hex_str: String = bytes.iter().map(|b| format!("{:02x}", b)).collect();
        return Ok(fill_slot(tpl, "{hex}", &hex_str));
    }

    let val = match v {
        Some(val) if !val.is_none() => val,
        _ => {
            return refuse(
                "E_SQL_BINDING",
                "a value binding holding no value cannot be a SQL literal; only an aggregate can be given an empty binding",
                pos,
            )
        }
    };

    if form == SqlKind::Num {
        return numeric_literal(dialect, val, pos);
    }

    let text = val.as_text(pos).unwrap_or_default();
    Ok(text_literal(dialect, &text))
}

pub fn numeric_literal(dialect: &str, v: &Value, pos: Pos) -> Result<String, SqlError> {
    let text = v.as_text(pos).unwrap_or_default();
    let d = match dec_parse(&text, pos) {
        Ok(d) => d,
        Err(_) => {
            return refuse(
                "E_SQL_BINDING",
                format!("a value bound as NUM must be a number, and {:?} is not", text),
                pos,
            )
        }
    };
    let n = dec_format(&d);

    if let Some(wrap_val) = lexical(dialect, "numericLiteral") {
        if let Some(wrap) = wrap_val.as_str() {
            if !wrap.is_empty() && wrap != "{0}" {
                return Ok(fill_slot(wrap, "{0}", &n));
            }
        }
    }

    if n.starts_with('-') {
        Ok(format!("({})", n))
    } else {
        Ok(n)
    }
}

pub fn text_literal(dialect: &str, text: &str) -> String {
    let quote = lexical(dialect, "textQuote")
        .and_then(|v| v.as_str().map(String::from))
        .unwrap_or_else(|| "'".to_string());

    let escape_val = lexical(dialect, "textEscape");
    let mut out = text.to_string();

    if let Some(esc) = escape_val {
        if let Some(m) = esc.as_object() {
            let mut keys: Vec<String> = m.keys().cloned().collect();
            // Sort by descending length so multi-character escapes match first
            keys.sort_by_key(|k| std::cmp::Reverse(k.len()));

            let mut buf = String::new();
            let mut i = 0;
            while i < out.len() {
                let rest = &out[i..];
                let mut hit: Option<&String> = None;
                for k in &keys {
                    if !k.is_empty() && rest.starts_with(k) {
                        hit = Some(k);
                        break;
                    }
                }
                if let Some(k) = hit {
                    let repl = m[k].as_str().unwrap_or("");
                    buf.push_str(repl);
                    i += k.len();
                } else {
                    let ch = rest.chars().next().unwrap();
                    buf.push(ch);
                    i += ch.len_utf8();
                }
            }
            out = buf;
        }
    }

    format!("{}{}{}", quote, out, quote)
}

pub fn placeholder(dialect: &str, n: usize) -> String {
    let tpl = lexical(dialect, "placeholder")
        .and_then(|v| v.as_str().map(String::from))
        .unwrap_or_else(|| "?".to_string());

    if tpl.contains("{n}") {
        fill_slot(&tpl, "{n}", &n.to_string())
    } else {
        tpl
    }
}

pub fn join_fragment(f: &Fragment, mode: Mode) -> Result<String, SqlError> {
    let mut sb = String::new();
    let mut nth = 0;

    for p in &f.parts {
        match p {
            Part::Sql(s) => sb.push_str(s),
            Part::Slot(slot) => {
                let slot_idx = *slot;
                let kind = if slot_idx > 0 && slot_idx - 1 < f.param_kinds.len() {
                    f.param_kinds[slot_idx - 1]
                } else {
                    SqlKind::Text
                };
                let null_val = Value::null();
                let val = if slot_idx > 0 && slot_idx - 1 < f.params.len() {
                    &f.params[slot_idx - 1]
                } else {
                    &null_val
                };

                if mode != Mode::Inline && f.is_inline(slot_idx) {
                    sb.push_str(&format_literal(&f.dialect, Some(val), kind, Pos::default())?);
                    continue;
                }

                nth += 1;
                match mode {
                    Mode::Inline => {
                        sb.push_str(&format_literal(&f.dialect, Some(val), kind, Pos::default())?);
                    }
                    Mode::Params => {
                        sb.push_str(&placeholder(&f.dialect, nth));
                    }
                    Mode::Debug => {
                        sb.push_str(&format!("~{}~", nth));
                    }
                }
            }
        }
    }

    Ok(sb)
}

pub struct Emit {
    dialect: String,
}

impl Emit {
    pub fn new(dialect: impl Into<String>) -> Self {
        Self {
            dialect: dialect.into(),
        }
    }

    pub fn dialect(&self) -> &str {
        &self.dialect
    }

    pub fn lex(&self, key: &str) -> Option<serde_json::Value> {
        lexical(&self.dialect, key)
    }

    pub fn numeric_operand(&self, f: &Fragment, pos: Pos) -> Result<Fragment, SqlError> {
        if f.kind == SqlKind::Num && !f.guard {
            return Ok(f.clone());
        }
        check_numeric_guard(&self.dialect);
        let guard_val = self.lex("numericGuard");
        let guard = match guard_val.as_ref().and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s,
            _ => {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "dialect {} has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric",
                        self.dialect
                    ),
                    pos,
                )
            }
        };

        let parts = self.fill(guard, &[f], pos, None)?;
        Ok(f.rewrapped(parts, SqlKind::Num, &self.dialect))
    }

    pub fn text_operand(&self, f: &Fragment) -> Result<Fragment, SqlError> {
        if f.exact {
            return Ok(f.clone());
        }
        let cast_val = self.lex("textCast");
        let collate_val = self.lex("textCollate");
        let collate = collate_val.as_ref().and_then(|v| v.as_str()).unwrap_or("");

        let mut parts = f.parts.clone();
        if let Some(cast) = cast_val.as_ref().and_then(|v| v.as_str()) {
            if !cast.is_empty() && cast != "{0}" {
                parts = self.fill(cast, &[f], Pos::default(), None)?;
            }
        }
        if !collate.is_empty() {
            parts.push(Part::Sql(collate.to_string()));
        }

        Ok(f.rewrapped(parts, SqlKind::Text, &self.dialect))
    }

    pub fn ident(&self, name: &str) -> String {
        let q = self
            .lex("identQuote")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_else(|| "\"".to_string());
        let esc = self
            .lex("identEscape")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_else(|| "\"\"".to_string());
        format!("{}{}{}", q, name.replace(&q, &esc), q)
    }

    pub fn column(&self, table: &str, column: &str) -> String {
        if table.is_empty() {
            self.ident(column)
        } else {
            format!("{}.{}", self.ident(table), self.ident(column))
        }
    }

    pub fn fill(
        &self,
        tpl: &str,
        args: &[&Fragment],
        pos: Pos,
        expanding: Option<&HashSet<String>>,
    ) -> Result<Vec<Part>, SqlError> {
        let mut parts = Vec::new();

        let push = |parts: &mut Vec<Part>, s: &str| {
            if s.is_empty() {
                return;
            }
            if let Some(last) = parts.last_mut() {
                if let Part::Sql(ref mut sql) = last {
                    sql.push_str(s);
                    return;
                }
            }
            parts.push(Part::Sql(s.to_string()));
        };

        let splice = |parts: &mut Vec<Part>, f: &Fragment| {
            for p in &f.parts {
                match p {
                    Part::Sql(ref s) => push(parts, s),
                    Part::Slot(_) => parts.push(p.clone()),
                }
            }
        };

        let join_sub = |parts: &mut Vec<Part>, subset: &[&Fragment]| {
            let mut first = true;
            for f in subset {
                if !first {
                    push(parts, ", ");
                }
                first = false;
                splice(parts, f);
            }
        };

        let bytes = tpl.as_bytes();
        let n_tpl = bytes.len();
        let mut i = 0;

        while i < n_tpl {
            if bytes[i] == b'{' && i + 1 < n_tpl && bytes[i + 1] == b'{' {
                push(&mut parts, "{");
                i += 2;
                continue;
            }
            if bytes[i] == b'}' && i + 1 < n_tpl && bytes[i + 1] == b'}' {
                push(&mut parts, "}");
                i += 2;
                continue;
            }
            if bytes[i] != b'{' {
                let width = tpl[i..].chars().next().unwrap().len_utf8();
                push(&mut parts, &tpl[i..i + width]);
                i += width;
                continue;
            }

            let end_rel = match bytes[i..].iter().position(|&b| b == b'}') {
                Some(pos) => pos,
                None => {
                    push(&mut parts, std::str::from_utf8(&bytes[i..]).unwrap());
                    break;
                }
            };
            let end = i + end_rel;
            let slot = std::str::from_utf8(&bytes[i + 1..end]).unwrap();
            i = end + 1;

            if slot == "*" {
                join_sub(&mut parts, args);
                continue;
            }

            if let Some(frm_str) = slot.strip_suffix(':') {
                if let Some(frm) = parse_slot_num(frm_str) {
                    if frm < args.len() {
                        join_sub(&mut parts, &args[frm..]);
                    }
                    continue;
                }
            }

            if let Some(k) = parse_slot_num(slot) {
                if k >= args.len() {
                    return refuse(
                        "E_SQL_UNSUPPORTED",
                        format!("the mapping for this expression asks for argument {}, which it was not given", k),
                        pos,
                    );
                }
                splice(&mut parts, args[k]);
                continue;
            }

            let (key, arg_str, has_arg) = match slot.find(':') {
                Some(at) => (&slot[..at], &slot[at + 1..], true),
                None => (slot, "", false),
            };

            let val = self.lex(key);
            let val_str = match val.as_ref().and_then(|v| v.as_str()) {
                Some(s) => s,
                _ => {
                    return refuse(
                        "E_SQL_UNSUPPORTED",
                        format!(
                            "a template used {{{}}}, which is neither an argument nor a lexical entry of dialect {}",
                            slot, self.dialect
                        ),
                        pos,
                    )
                }
            };

            if !has_arg || arg_str.is_empty() {
                push(&mut parts, val_str);
                continue;
            }

            if expanding.is_some_and(|e| e.contains(key)) {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "the {} lexical entry of dialect {} expands into itself, so filling it would never finish",
                        key, self.dialect
                    ),
                    pos,
                );
            }

            // A variadic lexical wrapper applies to each argument separately.
            // Use a one-argument view so large calls need no synthetic numeric
            // slot index; the fragments retain their original parameter slots.
            if arg_str == "*" {
                for (index, arg) in args.iter().enumerate() {
                    if index != 0 { push(&mut parts, ", "); }
                    let wrapped = self.fill(&format!("{{{}:0}}", key), &[*arg], pos, expanding)?;
                    for part in wrapped {
                        match part {
                            Part::Sql(ref text) => push(&mut parts, text),
                            Part::Slot(_) => parts.push(part),
                        }
                    }
                }
                continue;
            }

            let cast_arg = parse_slot_num(arg_str);
            if key == "binaryCast" {
                if let Some(ca) = cast_arg {
                    if ca < args.len() && args[ca].kind == SqlKind::Bin {
                        splice(&mut parts, args[ca]);
                        continue;
                    }
                }
            }

            let mut deeper = expanding.cloned().unwrap_or_default();
            deeper.insert(key.to_string());

            let sub_tpl = fill_slot(val_str, "{0}", &format!("{{{}}}", arg_str));
            let sub_parts = self.fill(&sub_tpl, args, pos, Some(&deeper))?;
            for p in sub_parts {
                match p {
                    Part::Sql(ref s) => push(&mut parts, s),
                    Part::Slot(_) => parts.push(p),
                }
            }
        }

        Ok(parts)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn variadic_lexical_wrappers_preserve_each_argument_and_parameter_slot() {
        let emit = Emit::new("sqlite");
        let pos = Pos::default();
        let fragment = |slot, kind| Fragment::new(
            vec![Part::Slot(slot)], kind, "sqlite", vec![], vec![], vec![]);
        let first = fragment(7, SqlKind::Num);
        let second = fragment(2, SqlKind::Text);
        assert_eq!(emit.fill("max({numericCast:*})", &[&first, &second], pos, None).unwrap(), vec![
            Part::Sql("max(CAST(".into()), Part::Slot(7),
            Part::Sql(" AS NUMERIC), CAST(".into()), Part::Slot(2),
            Part::Sql(" AS NUMERIC))".into()),
        ]);
        assert_eq!(emit.fill("f({numericCast:*})", &[], pos, None).unwrap(),
            vec![Part::Sql("f()".into())]);
        let binary = fragment(4, SqlKind::Bin);
        assert_eq!(emit.fill("{binaryCast:*}", &[&binary, &binary], pos, None).unwrap(),
            vec![Part::Slot(4), Part::Sql(", ".into()), Part::Slot(4)]);
        let many = vec![&first; 1001];
        let parts = emit.fill("{numericCast:*}", &many, pos, None).unwrap();
        assert_eq!(parts.iter().filter(|p| matches!(p, Part::Slot(7))).count(), 1001);
        let expanding = HashSet::from(["numericCast".to_string()]);
        assert_eq!(emit.fill("{numericCast:*}", &[&first], pos, Some(&expanding)).unwrap_err().code,
            "E_SQL_UNSUPPORTED");
    }
}
