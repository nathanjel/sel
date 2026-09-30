// Native UTF-8 conversion with SEL diagnostics on invalid input.

use std::fmt;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Pos {
    pub line: usize,
    pub col: usize,
    pub offset: usize,
}

impl Pos {
    pub const fn new(line: usize, col: usize, offset: usize) -> Self {
        Self { line, col, offset }
    }
}

impl fmt::Display for Pos {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.line, self.col)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelError {
    pub code: &'static str,
    pub message: String,
    pub pos: Pos,
}

impl SelError {
    pub fn new(code: &'static str, message: impl Into<String>, pos: Pos) -> Self {
        Self {
            code,
            message: message.into(),
            pos,
        }
    }

    pub fn syntax(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_SYNTAX", message, pos)
    }

    pub fn depth(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_DEPTH", message, pos)
    }

    pub fn null(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NULL", message, pos)
    }

    pub fn not_text(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NOT_TEXT", message, pos)
    }

    pub fn not_bin(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NOT_BIN", message, pos)
    }

    pub fn not_bool(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NOT_BOOL", message, pos)
    }

    pub fn not_num(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NOT_NUM", message, pos)
    }

    pub fn not_int(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NOT_INT", message, pos)
    }

    pub fn range(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_RANGE", message, pos)
    }

    pub fn arity(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_ARITY", message, pos)
    }

    pub fn undef_var(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_UNDEF_VAR", message, pos)
    }

    pub fn no_key(message: impl Into<String>, pos: Pos) -> Self {
        Self::new("E_NO_KEY", message, pos)
    }
}

impl fmt::Display for SelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} at line {}:{}: {}", self.code, self.pos.line, self.pos.col, self.message)
    }
}

impl std::error::Error for SelError {}

pub fn validate_text(s: &str, pos: Pos) -> Result<(), SelError> {
    for c in s.chars() {
        let u = c as u32;
        if (0xD800..=0xDFFF).contains(&u) {
            let which = if u <= 0xDBFF { "high" } else { "low" };
            return Err(SelError::new("E_UTF8", format!("unpaired {} surrogate", which), pos));
        }
    }
    Ok(())
}

pub fn to_code_points(s: &str, pos: Pos) -> Result<Vec<char>, SelError> {
    validate_text(s, pos)?;
    Ok(s.chars().collect())
}

pub fn decode_utf8_source(data: &[u8]) -> Result<String, SelError> {
    let mut cps = Vec::with_capacity(data.len());
    let at = |prefix: &[char]| -> Pos {
        let mut line = 1;
        let mut line_start = 0;
        for (k, &c) in prefix.iter().enumerate() {
            if c == '\n' {
                line += 1;
                line_start = k + 1;
            }
        }
        let col = prefix.len() - line_start + 1;
        Pos::new(line, col, prefix.len())
    };

    let n = data.len();
    let mut i = 0;
    while i < n {
        let b = data[i];
        if b < 0x80 {
            cps.push(b as char);
            i += 1;
            continue;
        }
        let (need, mut cp, lo, hi) = if (0xC2..=0xDF).contains(&b) {
            (1, (b & 0x1F) as u32, 0x80, 0xBF)
        } else if b == 0xE0 {
            (2, 0, 0xA0, 0xBF) // reject overlong 3-byte
        } else if (0xE1..=0xEC).contains(&b) {
            (2, (b & 0x0F) as u32, 0x80, 0xBF)
        } else if b == 0xED {
            (2, 0x0D, 0x80, 0x9F) // reject surrogates
        } else if (0xEE..=0xEF).contains(&b) {
            (2, (b & 0x0F) as u32, 0x80, 0xBF)
        } else if b == 0xF0 {
            (3, 0, 0x90, 0xBF) // reject overlong 4-byte
        } else if (0xF1..=0xF3).contains(&b) {
            (3, (b & 0x07) as u32, 0x80, 0xBF)
        } else if b == 0xF4 {
            (3, 4, 0x80, 0x8F) // cap at U+10FFFF
        } else {
            return Err(SelError::new(
                "E_UTF8",
                format!("invalid start byte 0x{:02x} at byte {}", b, i),
                at(&cps),
            ));
        };

        if i + need >= n {
            return Err(SelError::new(
                "E_UTF8",
                format!("truncated sequence at byte {}", i),
                at(&cps),
            ));
        }
        for k in 1..=need {
            let c = data[i + k];
            let lo_k = if k == 1 { lo } else { 0x80 };
            let hi_k = if k == 1 { hi } else { 0xBF };
            if c < lo_k || c > hi_k {
                return Err(SelError::new(
                    "E_UTF8",
                    format!("invalid continuation byte at byte {}", i + k),
                    at(&cps),
                ));
            }
            cp = (cp << 6) | ((c & 0x3F) as u32);
        }
        if let Some(ch) = char::from_u32(cp) {
            cps.push(ch);
        } else {
            return Err(SelError::new("E_UTF8", "invalid Unicode code point", at(&cps)));
        }
        i += need + 1;
    }
    Ok(cps.into_iter().collect())
}

pub fn decode_utf8(data: &[u8], pos: Pos) -> Result<String, SelError> {
    match std::str::from_utf8(data) {
        Ok(s) => {
            validate_text(s, pos)?;
            Ok(s.to_string())
        }
        Err(_) => Err(decode_utf8_diagnostic(data, pos)),
    }
}

pub fn decode_utf8_diagnostic(data: &[u8], pos: Pos) -> SelError {
    let n = data.len();
    let mut i = 0;
    while i < n {
        let b = data[i];
        if b < 0x80 {
            i += 1;
            continue;
        }
        let (need, lo, hi) = if (0xC2..=0xDF).contains(&b) {
            (1, 0x80, 0xBF)
        } else if b == 0xE0 {
            (2, 0xA0, 0xBF) // reject overlong 3-byte
        } else if (0xE1..=0xEC).contains(&b) {
            (2, 0x80, 0xBF)
        } else if b == 0xED {
            (2, 0x80, 0x9F) // reject surrogates
        } else if (0xEE..=0xEF).contains(&b) {
            (2, 0x80, 0xBF)
        } else if b == 0xF0 {
            (3, 0x90, 0xBF) // reject overlong 4-byte
        } else if (0xF1..=0xF3).contains(&b) {
            (3, 0x80, 0xBF)
        } else if b == 0xF4 {
            (3, 0x80, 0x8F) // cap at U+10FFFF
        } else {
            return SelError::new(
                "E_UTF8",
                format!("invalid start byte 0x{:x} at byte {}", b, i),
                pos,
            );
        };

        if i + need >= n {
            return SelError::new(
                "E_UTF8",
                format!("truncated sequence at byte {}", i),
                pos,
            );
        }
        for k in 1..=need {
            let c = data[i + k];
            let lo_k = if k == 1 { lo } else { 0x80 };
            let hi_k = if k == 1 { hi } else { 0xBF };
            if c < lo_k || c > hi_k {
                return SelError::new(
                    "E_UTF8",
                    format!("invalid continuation byte at byte {}", i + k),
                    pos,
                );
            }
        }
        i += need + 1;
    }
    SelError::new("E_UTF8", "invalid UTF-8 byte sequence", pos)
}

pub fn bytes_to_hex(data: &[u8]) -> String {
    let mut s = String::with_capacity(data.len() * 2);
    for &b in data {
        use std::fmt::Write;
        write!(&mut s, "{:02x}", b).unwrap();
    }
    s
}

pub fn cap_text(n: u128, pos: Pos) -> Result<(), SelError> {
    if n > crate::limits::MAX_TEXT_LEN as u128 {
        Err(SelError::range(
            format!("result would be longer than {} units", crate::limits::MAX_TEXT_LEN),
            pos,
        ))
    } else {
        Ok(())
    }
}

pub fn cap_collection(n: u128, pos: Pos) -> Result<(), SelError> {
    if n > crate::limits::MAX_COLLECTION as u128 {
        Err(SelError::range(
            format!("collection would have more than {} children", crate::limits::MAX_COLLECTION),
            pos,
        ))
    } else {
        Ok(())
    }
}

