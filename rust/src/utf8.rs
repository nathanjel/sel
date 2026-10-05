// Native UTF-8 conversion with SEL diagnostics on invalid input.

use std::fmt;

/// SEL whitespace (SPEC §2.2): space, TAB, CR and LF, and nothing else. Never
/// `char::is_whitespace` or `str::trim`, which follow Unicode White_Space and
/// would also take NBSP, VT, FF, U+3000 and the rest.
pub(crate) fn is_sel_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}

/// The value of a canonical positive integer key -- ASCII digits, no leading
/// zero, at most `max_digits` of them -- the only spelling that names a list
/// position. Anything else (an empty key, "01", "+1", a non-ASCII digit) is
/// no position at all.
#[inline]
pub(crate) fn canonical_index(key: &str, max_digits: usize) -> Option<usize> {
    let bytes = key.as_bytes();
    if bytes.is_empty() || bytes.len() > max_digits || !(b'1'..=b'9').contains(&bytes[0]) {
        return None;
    }
    let mut val = 0usize;
    for &b in bytes {
        if !b.is_ascii_digit() {
            return None;
        }
        val = val * 10 + (b - b'0') as usize;
    }
    Some(val)
}

/// Bytes as lower-case hex, two digits each: TO_HEX and the `b…` dump.
pub(crate) fn hex_lower(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 15) as usize] as char);
    }
    out
}

/// True when `s` holds nothing but SEL whitespace (the empty text included):
/// the "blank" of `IS_BLANK` and `???`.
pub(crate) fn is_sel_blank(s: &str) -> bool {
    s.bytes().all(|b| matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
}

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

// There is no validate_text: a &str is valid UTF-8 by construction, and so
// holds no surrogate code point -- text arriving as bytes is checked where it
// is decoded (decode_utf8_source, FROM_UTF8), and nowhere else needs to look.

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

