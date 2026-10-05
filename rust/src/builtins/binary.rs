use crate::args::Args;
use crate::utf8::{cap_collection, cap_text, SelError};
use crate::value::Value;

const B64_ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn fn_blen(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::int(args.bytes(0)?.len() as i64))
}

pub fn fn_to_utf8(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    cap_text(b.len() as u128, args.pos())?;
    Ok(Value::bin_owned(b))
}

pub fn fn_from_utf8(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    let pos = args.pos_at(0);
    // from_utf8 refuses surrogates, overlongs and everything else that is not
    // UTF-8: nothing is left to check once it accepts.
    let s = String::from_utf8(b).map_err(|_| SelError::new("E_UTF8", "invalid UTF-8", pos))?;
    Ok(Value::text_owned(s))
}

pub fn fn_to_hex(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    cap_text((b.len() as u128) * 2, args.pos())?;
    let mut out = String::with_capacity(b.len() * 2);
    for byte in b {
        use std::fmt::Write;
        let _ = write!(out, "{:02x}", byte);
    }
    Ok(Value::text_owned(out))
}

pub fn fn_from_hex(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let pos = args.pos_at(0);
    if s.len() % 2 != 0 {
        return Err(SelError::new("E_BAD_ARG", "FROM_HEX needs an even number of digits", pos));
    }
    let bytes = s.as_bytes();
    for &c in bytes {
        if !(c.is_ascii_hexdigit()) {
            return Err(SelError::new("E_BAD_ARG", format!("FROM_HEX: invalid hex byte in {:?}", s), pos));
        }
    }
    let mut out = Vec::with_capacity(bytes.len() / 2);
    for i in (0..bytes.len()).step_by(2) {
        let h = (bytes[i] as char).to_digit(16).unwrap() as u8;
        let l = (bytes[i + 1] as char).to_digit(16).unwrap() as u8;
        out.push((h << 4) | l);
    }
    Ok(Value::bin_owned(out))
}

pub fn fn_encode_base64(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    cap_text(((b.len() as u128 + 2) / 3) * 4, args.pos())?;
    let mut out = Vec::with_capacity((b.len() + 2) / 3 * 4);
    let mut i = 0;
    while i < b.len() {
        let b0 = b[i] as u32;
        let b1 = if i + 1 < b.len() { b[i + 1] as u32 } else { 0 };
        let b2 = if i + 2 < b.len() { b[i + 2] as u32 } else { 0 };
        let n = (b0 << 16) | (b1 << 8) | b2;

        out.push(B64_ALPHABET[((n >> 18) & 63) as usize]);
        out.push(B64_ALPHABET[((n >> 12) & 63) as usize]);
        if i + 1 < b.len() {
            out.push(B64_ALPHABET[((n >> 6) & 63) as usize]);
        } else {
            out.push(b'=');
        }
        if i + 2 < b.len() {
            out.push(B64_ALPHABET[(n & 63) as usize]);
        } else {
            out.push(b'=');
        }
        i += 3;
    }
    Ok(Value::text_owned(String::from_utf8(out).unwrap()))
}

fn b64_val(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

pub fn fn_decode_base64(args: &mut Args) -> Result<Value, SelError> {
    let s = args.text(0)?;
    let pos = args.pos_at(0);
    if s.len() % 4 != 0 {
        return Err(SelError::new(
            "E_BAD_ARG",
            "DECODE_BASE64 needs a length that is a multiple of 4",
            pos,
        ));
    }
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    let mut i = 0;
    while i < bytes.len() {
        let mut quad = [0u32; 4];
        let mut padding = 0;
        for k in 0..4 {
            let ch = bytes[i + k];
            if ch == b'=' {
                if i + 4 < bytes.len() || k < 2 {
                    return Err(SelError::new("E_BAD_ARG", "misplaced base64 padding", pos));
                }
                padding += 1;
                quad[k] = 0;
                continue;
            }
            if padding > 0 {
                return Err(SelError::new("E_BAD_ARG", "misplaced base64 padding", pos));
            }
            let v = b64_val(ch).ok_or_else(|| {
                SelError::new("E_BAD_ARG", format!("invalid base64 character {:?}", ch as char), pos)
            })?;
            quad[k] = v as u32;
        }
        let n = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3];
        out.push(((n >> 16) & 255) as u8);
        if padding < 2 {
            out.push(((n >> 8) & 255) as u8);
        }
        if padding < 1 {
            out.push((n & 255) as u8);
        }
        i += 4;
    }
    Ok(Value::bin_owned(out))
}

// CRC32 table
fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            let mask = ((crc & 1) as i32).wrapping_neg() as u32;
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

pub fn fn_crc32(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    let crc = crc32_ieee(&b);
    Ok(Value::text_owned(format!("{:08x}", crc)))
}

pub fn fn_btl(args: &mut Args) -> Result<Value, SelError> {
    let b = args.bytes(0)?;
    cap_collection(b.len() as u128, args.pos())?;
    let items = b.into_iter().map(|byte| Value::int(byte as i64)).collect();
    Ok(Value::list(items))
}

pub fn fn_ltb(args: &mut Args) -> Result<Value, SelError> {
    let v = args.val(0)?;
    let pos = args.pos_at(0);
    let items = if v.size() > 0 {
        v.values()
    } else if !(v.is_none() && v.size() == 0) {
        vec![v]
    } else {
        Vec::new()
    };
    cap_text(items.len() as u128, args.pos())?;
    let mut out = Vec::with_capacity(items.len());
    for (i, item) in items.iter().enumerate() {
        let d = item.as_decimal(pos)?;
        if !d.is_integer() {
            return Err(SelError::not_int(
                format!("LTB element {} must be a whole number", i + 1),
                pos,
            ));
        }
        let n = d.to_i64().unwrap_or(-1);
        if !(0..=255).contains(&n) {
            return Err(SelError::range(
                format!("LTB element {} is not a byte value", i + 1),
                pos,
            ));
        }
        out.push(n as u8);
    }
    Ok(Value::bin_owned(out))
}
