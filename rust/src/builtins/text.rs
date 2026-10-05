use crate::args::Args;
use crate::utf8::{cap_collection, cap_text, is_sel_space, to_code_points, validate_text, SelError};
use crate::value::Value;

fn trim_text(s: &str, left: bool, right: bool) -> String {
    let chars: Vec<char> = s.chars().collect();
    let mut a = 0;
    let mut b = chars.len();
    if left {
        while a < b && is_sel_space(chars[a]) {
            a += 1;
        }
    }
    if right {
        while b > a && is_sel_space(chars[b - 1]) {
            b -= 1;
        }
    }
    chars[a..b].iter().collect()
}

fn pad(args: &mut Args, left: bool) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let width = args.non_neg_int(1)?;
    let fill = args.text(2)?;
    let fill_pos = args.pos_at(2);
    if fill.is_empty() {
        return Err(SelError::new("E_BAD_ARG", "pad fill must not be empty", fill_pos));
    }
    let s_chars: Vec<char> = s.chars().collect();
    if (s_chars.len() as i64) >= width {
        return Ok(Value::text_owned(s));
    }
    cap_text(width as u128, args.pos())?;
    let width_usize = width as usize;
    let fill_chars: Vec<char> = fill.chars().collect();
    let need = width_usize - s_chars.len();
    let mut padding = Vec::with_capacity(need);
    for i in 0..need {
        padding.push(fill_chars[i % fill_chars.len()]);
    }
    let res: String = if left {
        padding.into_iter().chain(s_chars.into_iter()).collect()
    } else {
        s_chars.into_iter().chain(padding.into_iter()).collect()
    };
    Value::text(&res, args.pos())
}

pub fn fn_len(args: &mut Args) -> Result<Value, SelError> {
    let pos = args.pos_at(0);
    let scalar = args.val(0)?.scalar_source(pos)?;
    // A computed number's length follows from its digits and scale; its text
    // is made only if something else asks for it.
    if let Some(len) = scalar.unformatted_number_len() {
        return Ok(Value::int(len as i64));
    }
    let s = scalar.as_text(pos)?;
    let chars = to_code_points(&s, pos)?;
    Ok(Value::int(chars.len() as i64))
}

pub fn fn_left(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let n = args.non_neg_int(1)? as usize;
    let chars: Vec<char> = s.chars().collect();
    let take_n = n.min(chars.len());
    let res: String = chars[..take_n].iter().collect();
    Ok(Value::text_owned(res))
}

pub fn fn_right(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let n = args.non_neg_int(1)? as usize;
    let chars: Vec<char> = s.chars().collect();
    let take_n = n.min(chars.len());
    let start = chars.len() - take_n;
    let res: String = chars[start..].iter().collect();
    Ok(Value::text_owned(res))
}

pub fn fn_substr(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let start = args.int(1)?;
    if start < 1 {
        return Err(SelError::range(
            "SUBSTR start is 1-based and must be at least 1",
            args.pos_at(1),
        ));
    }
    let from = (start - 1) as usize;
    let chars: Vec<char> = s.chars().collect();
    if from >= chars.len() {
        return Ok(Value::text_owned(String::new()));
    }
    if args.count() == 2 {
        let res: String = chars[from..].iter().collect();
        return Ok(Value::text_owned(res));
    }
    let n = args.non_neg_int(2)? as usize;
    let to = (from + n).min(chars.len());
    let res: String = chars[from..to].iter().collect();
    Ok(Value::text_owned(res))
}

pub fn fn_find(args: &mut Args) -> Result<Value, SelError> {
    let needle = args.text(0)?;
    let hay = args.text(1)?;
    if needle.is_empty() {
        return Err(SelError::new("E_BAD_ARG", "FIND needle must not be empty", args.pos_at(0)));
    }
    let mut from = 0;
    if args.count() == 3 {
        let f = args.int(2)?;
        if f < 1 {
            return Err(SelError::range(
                "FIND start is 1-based and must be at least 1",
                args.pos_at(2),
            ));
        }
        from = (f - 1) as usize;
    }
    let needle_chars: Vec<char> = needle.chars().collect();
    let hay_chars: Vec<char> = hay.chars().collect();
    if from > hay_chars.len() || needle_chars.len() > hay_chars.len() {
        return Ok(Value::int(0));
    }
    let max_start = hay_chars.len() - needle_chars.len();
    for i in from..=max_start {
        if hay_chars[i..i + needle_chars.len()] == needle_chars[..] {
            return Ok(Value::int((i + 1) as i64));
        }
    }
    Ok(Value::int(0))
}

pub fn fn_replace(args: &mut Args) -> Result<Value, SelError> {
    let needle = args.text(0)?;
    let repl = args.text(1)?;
    let hay = args.text(2)?;
    if needle.is_empty() {
        return Err(SelError::new("E_BAD_ARG", "REPLACE needle must not be empty", args.pos_at(0)));
    }
    let hay_chars: Vec<char> = hay.chars().collect();
    let needle_chars: Vec<char> = needle.chars().collect();
    let repl_chars: Vec<char> = repl.chars().collect();
    let mut matches: usize = 0;
    let mut j = 0;
    while j + needle_chars.len() <= hay_chars.len() {
        if hay_chars[j..j + needle_chars.len()] == needle_chars[..] {
            matches += 1;
            j += needle_chars.len();
        } else {
            j += 1;
        }
    }
    let base = hay_chars.len() - matches * needle_chars.len();
    let total = base as u128 + (matches as u128) * (repl_chars.len() as u128);
    cap_text(total, args.pos())?;
    let res = hay.replace(&needle, &repl);
    Value::text(&res, args.pos())
}

pub fn fn_split(args: &mut Args) -> Result<Value, SelError> {
    let hay = args.text(0)?;
    let sep = args.text(1)?;
    if sep.is_empty() {
        return Err(SelError::new("E_BAD_ARG", "SPLIT separator must not be empty", args.pos_at(1)));
    }
    let mut parts: Vec<Value> = Vec::new();
    let mut i = 0;
    while let Some(pos) = hay[i..].find(&sep) {
        cap_collection((parts.len() + 2) as u128, args.pos())?;
        parts.push(Value::text_owned(hay[i..i + pos].to_string()));
        i += pos + sep.len();
    }
    parts.push(Value::text_owned(hay[i..].to_string()));
    Ok(Value::list_owned(parts))
}

pub fn fn_trim(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    Ok(Value::text_owned(trim_text(&s, true, true)))
}

pub fn fn_ltrim(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    Ok(Value::text_owned(trim_text(&s, true, false)))
}

pub fn fn_rtrim(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    Ok(Value::text_owned(trim_text(&s, false, true)))
}

pub fn fn_upper(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    Ok(Value::text_owned(s.to_ascii_uppercase()))
}

pub fn fn_lower(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    Ok(Value::text_owned(s.to_ascii_lowercase()))
}

pub fn fn_backwards(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let mut chars: Vec<char> = s.chars().collect();
    chars.reverse();
    let res: String = chars.into_iter().collect();
    Ok(Value::text_owned(res))
}

pub fn fn_repeat(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let n = args.non_neg_int(1)?;
    if s.is_empty() || n == 0 {
        return Ok(Value::text_owned(String::new()));
    }
    let char_count = s.chars().count();
    let total = (char_count as u128).saturating_mul(n as u128);
    cap_text(total, args.pos())?;
    let res = s.repeat(n as usize);
    Value::text(&res, args.pos())
}

pub fn fn_padl(args: &mut Args) -> Result<Value, SelError> {
    pad(args, true)
}

pub fn fn_padr(args: &mut Args) -> Result<Value, SelError> {
    pad(args, false)
}

pub fn fn_char(args: &mut Args) -> Result<Value, SelError> {
    let n = args.int(0)?;
    let pos = args.pos_at(0);
    if !(0..=0x10FFFF).contains(&n) || (0xD800..=0xDFFF).contains(&n) {
        return Err(SelError::range(
            format!("{} is not an encodable code point", n),
            pos,
        ));
    }
    let ch = char::from_u32(n as u32).unwrap();
    Ok(Value::text_owned(ch.to_string()))
}

pub fn fn_code(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let pos = args.pos_at(0);
    if s.is_empty() {
        return Err(SelError::range("CODE of empty text", pos));
    }
    let ch = s.chars().next().unwrap();
    Ok(Value::int(ch as u32 as i64))
}
