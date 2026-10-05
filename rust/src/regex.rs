// Portable regex subset validator (Spec §7.8) and compiler for Rust.

use crate::regex_counter::CounterRegex;
use crate::utf8::{Pos, SelError};
use regex::Regex;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex, OnceLock};

const MAX_QUANTIFIER: usize = 65535;

fn bad_regex(message: &str, pattern: &str, at: usize, pos: Pos) -> SelError {
    SelError::new(
        "E_REGEX_SYNTAX",
        format!("{} (at offset {} of /{}/)", message, at, pattern),
        pos,
    )
}

fn reject_escape(e: char, pattern: &str, at: usize, pos: Pos) -> SelError {
    if e == 'b' || e == 'B' {
        return bad_regex(
            &format!("\\{} is not portable — word boundaries depend on the engine's idea of a word character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])", e),
            pattern,
            at,
            pos,
        );
    }
    if e == 'v' {
        return bad_regex(
            "\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B",
            pattern,
            at,
            pos,
        );
    }
    if e.is_ascii_digit() {
        return bad_regex("backreferences are not portable", pattern, at, pos);
    }
    if e == 'p' || e == 'P' {
        return bad_regex("\\p{...} is not portable", pattern, at, pos);
    }
    if e == 'A' || e == 'z' || e == 'Z' || e == 'G' || e == 'K' {
        return bad_regex(
            &format!("\\{} is not portable — use ^ and $", e),
            pattern,
            at,
            pos,
        );
    }
    bad_regex(&format!("unsupported escape \\{}", e), pattern, at, pos)
}

pub fn validate_pattern(pattern: &str, pos: Pos) -> Result<String, SelError> {
    validate_pattern_with_case(pattern, pos, false)
}

pub fn validate_pattern_with_case(
    pattern: &str,
    pos: Pos,
    ignore_case: bool,
) -> Result<String, SelError> {
    let portable = validate_pattern_impl(pattern, pos, false)?;
    let analysis = validate_pattern_impl(pattern, pos, true)?;
    crate::regex_ambiguity::analyse(&analysis, ignore_case)
        .map_err(|reason| bad_regex(reason, pattern, 0, pos))?;
    Ok(portable)
}

fn validate_pattern_impl(pattern: &str, pos: Pos, rust_classes: bool) -> Result<String, SelError> {
    if pattern.chars().count() > crate::limits::MAX_REGEX_PATTERN {
        return Err(bad_regex(
            "pattern length exceeds the limit",
            pattern,
            0,
            pos,
        ));
    }
    let bytes = pattern.as_bytes();
    let n = bytes.len();
    let mut out: Vec<u8> = Vec::with_capacity(n * 2);
    let mut i = 0;

    while i < n {
        let c = bytes[i] as char;

        if c == '\\' {
            if i + 1 >= n {
                return Err(bad_regex("trailing backslash", pattern, i, pos));
            }
            let e = bytes[i + 1] as char;
            match e {
                'd' => {
                    out.extend_from_slice(b"[0-9]");
                    i += 2;
                    continue;
                }
                'D' => {
                    out.extend_from_slice(b"[^0-9]");
                    i += 2;
                    continue;
                }
                'w' => {
                    out.extend_from_slice(b"[0-9A-Za-z_]");
                    i += 2;
                    continue;
                }
                'W' => {
                    out.extend_from_slice(b"[^0-9A-Za-z_]");
                    i += 2;
                    continue;
                }
                's' => {
                    out.extend_from_slice(b"[ \\t\\n\\r\\f\\x0b]");
                    i += 2;
                    continue;
                }
                'S' => {
                    out.extend_from_slice(b"[^ \\t\\n\\r\\f\\x0b]");
                    i += 2;
                    continue;
                }
                'n' | 'r' | 't' | 'f' | '^' | '$' | '\\' | '.' | '*' | '+' | '?' | '(' | ')'
                | '[' | ']' | '{' | '}' | '|' | '/' => {
                    out.push(b'\\');
                    out.push(e as u8);
                    i += 2;
                    continue;
                }
                _ => return Err(reject_escape(e, pattern, i, pos)),
            }
        }

        if c == '[' {
            let (text, nxt) = validate_class(pattern, bytes, i, pos, rust_classes)?;
            out.extend_from_slice(text.as_bytes());
            i = nxt;
            continue;
        }

        if c == '(' {
            if i + 1 < n && bytes[i + 1] == b'?' {
                let nxt = if i + 2 < n {
                    bytes[i + 2] as char
                } else {
                    '\0'
                };
                if nxt == ':' {
                    out.extend_from_slice(b"(?:");
                    i += 3;
                    continue;
                }
                let kind = match nxt {
                    '=' | '!' => "lookahead",
                    '<' => "lookbehind and named groups",
                    '>' => "atomic groups",
                    _ => "this group type",
                };
                return Err(bad_regex(
                    &format!("{} is not portable — only (?: ) is", kind),
                    pattern,
                    i,
                    pos,
                ));
            }
            out.push(b'(');
            i += 1;
            continue;
        }

        if c == '{' {
            let brace_end = validate_braces(pattern, bytes, i, pos)?;
            let end = after_quantifier(pattern, bytes, brace_end, pos)?;
            out.extend_from_slice(&bytes[i..end]);
            i = end;
            continue;
        }

        if c == '*' || c == '+' || c == '?' {
            let end = after_quantifier(pattern, bytes, i + 1, pos)?;
            out.extend_from_slice(&bytes[i..end]);
            i = end;
            continue;
        }

        if c == '}' {
            return Err(bad_regex("unmatched } — escape it as \\}", pattern, i, pos));
        }
        if c == ']' {
            return Err(bad_regex("unmatched ] — escape it as \\]", pattern, i, pos));
        }

        out.push(bytes[i]);
        i += 1;
    }

    let normalized =
        String::from_utf8(out).map_err(|e| SelError::new("E_UTF8", e.to_string(), pos))?;
    StructureParser {
        src: &normalized,
        i: 0,
        groups: 0,
        pos,
    }
    .parse()?;
    Ok(normalized)
}

fn after_quantifier(pattern: &str, bytes: &[u8], i: usize, pos: Pos) -> Result<usize, SelError> {
    if i < bytes.len() && bytes[i] == b'+' {
        return Err(bad_regex(
            "possessive quantifiers are not portable",
            pattern,
            i,
            pos,
        ));
    }
    if i < bytes.len() && bytes[i] == b'?' {
        return Ok(i + 1);
    }
    Ok(i)
}

fn validate_braces(pattern: &str, bytes: &[u8], start: usize, pos: Pos) -> Result<usize, SelError> {
    let mut i = start + 1;
    let lo_start = i;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i == lo_start {
        return Err(bad_regex(
            "{ must begin a quantifier such as {2,4} — escape it as \\{",
            pattern,
            start,
            pos,
        ));
    }
    let lo: usize = pattern[lo_start..i].parse().unwrap_or(MAX_QUANTIFIER + 1);
    let mut has_hi = false;
    let mut hi = 0;
    if i < bytes.len() && bytes[i] == b',' {
        i += 1;
        let hi_start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        if i > hi_start {
            has_hi = true;
            hi = pattern[hi_start..i].parse().unwrap_or(MAX_QUANTIFIER + 1);
        }
    }
    if i >= bytes.len() || bytes[i] != b'}' {
        return Err(bad_regex("malformed quantifier", pattern, start, pos));
    }
    if lo > MAX_QUANTIFIER || (has_hi && hi > MAX_QUANTIFIER) {
        return Err(bad_regex(
            &format!("quantifier bound exceeds the maximum of {}", MAX_QUANTIFIER),
            pattern,
            start,
            pos,
        ));
    }
    if has_hi && hi < lo {
        return Err(bad_regex(
            &format!(
                "quantifier {{{},{}}} is empty — the upper bound is below the lower one",
                lo, hi
            ),
            pattern,
            start,
            pos,
        ));
    }
    Ok(i + 1)
}

fn validate_class(
    pattern: &str,
    bytes: &[u8],
    start: usize,
    pos: Pos,
    rust_classes: bool,
) -> Result<(String, usize), SelError> {
    fn item(
        pattern: &str,
        i: &mut usize,
        pos: Pos,
        rust_classes: bool,
    ) -> Result<(String, Option<char>), SelError> {
        let bytes = pattern.as_bytes();
        let at = *i;
        let c = pattern[*i..].chars().next().unwrap();
        *i += c.len_utf8();
        // A `[` followed by `:`, `.` or `=` inside a class is refused, closed or
        // not (SPEC 7.8): the engines read the POSIX forms `[:alpha:]`, `[.x.]`,
        // `[=x=]` differently, and an unfinished one too.
        if c == '[' && *i < bytes.len() && matches!(bytes[*i], b':' | b'.' | b'=') {
            return Err(bad_regex(
                "POSIX bracket forms are not portable",
                pattern,
                at,
                pos,
            ));
        }
        if c == '\\' {
            let e = pattern[*i..]
                .chars()
                .next()
                .ok_or_else(|| bad_regex("trailing backslash", pattern, at, pos))?;
            *i += e.len_utf8();
            let expanded = match e {
                'd' => Some("0-9"),
                'w' => Some("0-9A-Za-z_"),
                's' => Some(" \\t\\n\\r\\f\\x0b"),
                _ => None,
            };
            if let Some(text) = expanded {
                return Ok((text.into(), None));
            }
            let value = match e {
                'n' => '\n',
                'r' => '\r',
                't' => '\t',
                'f' => '\u{c}',
                '-' | '^' | '$' | '\\' | '.' | '*' | '+' | '?' | '(' | ')' | '[' | ']' | '{'
                | '}' | '|' | '/' => e,
                _ => return Err(reject_escape(e, pattern, at, pos)),
            };
            return Ok((
                if rust_classes {
                    format!("\\x{{{:x}}}", value as u32)
                } else {
                    format!("\\{}", e)
                },
                Some(value),
            ));
        }
        // Escape every literal member: Rust additionally recognizes nested
        // classes and &&, --, ~~ set operations, which SEL does not.
        Ok((
            if rust_classes {
                format!("\\x{{{:x}}}", c as u32)
            } else {
                c.to_string()
            },
            Some(c),
        ))
    }
    let mut i = start + 1;
    let mut out = String::from("[");
    if bytes.get(i) == Some(&b'^') {
        out.push('^');
        i += 1;
    }
    let mut count = 0;
    while i < bytes.len() && bytes[i] != b']' {
        let (text, lo) = item(pattern, &mut i, pos, rust_classes)?;
        out.push_str(&text);
        count += 1;
        if bytes.get(i) == Some(&b'-') && bytes.get(i + 1).is_some_and(|&b| b != b']') {
            let dash = i;
            i += 1;
            let (text, hi) = item(pattern, &mut i, pos, rust_classes)?;
            match (lo, hi) {
                (Some(lo), Some(hi)) if lo <= hi => {}
                _ => {
                    return Err(bad_regex(
                        "invalid character class range",
                        pattern,
                        dash,
                        pos,
                    ))
                }
            }
            out.push('-');
            out.push_str(&text);
        }
    }
    if count == 0 || i == bytes.len() {
        return Err(bad_regex(
            "empty or unterminated character class",
            pattern,
            start,
            pos,
        ));
    }
    out.push(']');
    Ok((out, i + 1))
}

// Structural facts suffice for the portable loop rules. Keep syntax groups
// intact: whether a capture is optional must be checked before engine rewrites.
#[derive(Clone, Copy, Default)]
struct Facts {
    nullable: bool,
    capture: bool,
    optional_capture: bool,
    anchor: bool,
}
struct StructureParser<'a> {
    src: &'a str,
    i: usize,
    groups: usize,
    pos: Pos,
}
impl StructureParser<'_> {
    fn skip_continuation(&mut self, bytes: &[u8]) {
        while self.i < bytes.len() && bytes[self.i] & 0xC0 == 0x80 {
            self.i += 1;
        }
    }
    fn error(&self) -> SelError {
        bad_regex("non-portable regex structure", self.src, self.i, self.pos)
    }
    fn parse(&mut self) -> Result<(), SelError> {
        self.alt(0)?;
        if self.i != self.src.len() {
            return Err(self.error());
        }
        Ok(())
    }
    fn alt(&mut self, depth: usize) -> Result<Facts, SelError> {
        let mut branches = Vec::new();
        loop {
            let mut cat = Facts {
                nullable: true,
                ..Facts::default()
            };
            while self.i < self.src.len() && !matches!(self.src.as_bytes()[self.i], b'|' | b')') {
                let atom = self.atom(depth)?;
                cat.nullable &= atom.nullable;
                cat.capture |= atom.capture;
                cat.optional_capture |= atom.optional_capture;
            }
            branches.push(cat);
            if self.src.as_bytes().get(self.i) != Some(&b'|') {
                break;
            }
            self.i += 1;
        }
        let mut result = Facts::default();
        for b in &branches {
            result.nullable |= b.nullable;
            result.capture |= b.capture;
            result.optional_capture |= b.optional_capture || (branches.len() > 1 && b.capture);
        }
        Ok(result)
    }
    fn atom(&mut self, depth: usize) -> Result<Facts, SelError> {
        let bytes = self.src.as_bytes();
        let c = bytes[self.i];
        self.i += 1;
        let mut facts = Facts::default();
        match c {
            b'(' => {
                self.groups += 1;
                if depth >= crate::limits::MAX_DEPTH
                    || self.groups > crate::limits::MAX_REGEX_GROUPS
                {
                    return Err(self.error());
                }
                let capture = bytes.get(self.i) != Some(&b'?');
                if !capture {
                    self.i += 2;
                }
                facts = self.alt(depth + 1)?;
                if bytes.get(self.i) != Some(&b')') {
                    return Err(self.error());
                }
                self.i += 1;
                facts.capture |= capture;
            }
            b'[' => {
                while self.i < bytes.len() && bytes[self.i] != b']' {
                    if bytes[self.i] == b'\\' {
                        self.i += 1;
                    }
                    self.i += 1;
                }
                self.i += 1;
            }
            b'\\' => {
                self.i += 1;
                self.skip_continuation(bytes);
            }
            b'^' | b'$' => {
                facts.nullable = true;
                facts.anchor = true;
            }
            b'*' | b'+' | b'?' | b'{' => return Err(self.error()),
            // A literal is one code point: a quantifier after a multi-byte
            // character binds to all of it, not to its last byte.
            _ => self.skip_continuation(bytes),
        }
        if let Some(&q) = bytes.get(self.i) {
            if matches!(q, b'*' | b'+' | b'?' | b'{') {
                if facts.anchor {
                    return Err(self.error());
                }
                let (lo, hi) = if q == b'{' {
                    let end = validate_braces(self.src, bytes, self.i, self.pos)?;
                    let body = &self.src[self.i + 1..end - 1];
                    let mut parts = body.split(',');
                    let lo = parts.next().unwrap().parse::<usize>().unwrap();
                    let hi = parts.next().map_or(lo, |s| s.parse().unwrap_or(usize::MAX));
                    self.i = end;
                    (lo, hi)
                } else {
                    self.i += 1;
                    (
                        usize::from(q == b'+'),
                        if q == b'?' { 1 } else { usize::MAX },
                    )
                };
                if hi > 1 && (facts.nullable || facts.optional_capture) {
                    return Err(self.error());
                }
                facts.nullable |= lo == 0;
                facts.optional_capture |= lo == 0 && facts.capture;
                if bytes.get(self.i) == Some(&b'?') {
                    self.i += 1;
                }
                if bytes
                    .get(self.i)
                    .is_some_and(|b| matches!(b, b'*' | b'+' | b'?' | b'{'))
                {
                    return Err(self.error());
                }
            }
        }
        Ok(facts)
    }
}

pub fn lower_anchors(src: &str) -> String {
    let mut out = String::with_capacity(src.len());
    let chars: Vec<char> = src.chars().collect();
    let n = chars.len();
    let mut i = 0;
    let mut in_class = false;
    while i < n {
        let c = chars[i];
        if c == '\\' && i + 1 < n {
            out.push(c);
            out.push(chars[i + 1]);
            i += 2;
            continue;
        }
        if in_class {
            if c == ']' {
                in_class = false;
            }
            out.push(c);
            i += 1;
            continue;
        }
        if c == '[' {
            in_class = true;
            out.push(c);
            i += 1;
            if i < n && chars[i] == '^' {
                out.push('^');
                i += 1;
            }
            continue;
        }
        if c == '^' {
            out.push_str("\\A");
            i += 1;
            continue;
        }
        if c == '$' {
            out.push_str("\\z");
            i += 1;
            continue;
        }
        out.push(c);
        i += 1;
    }
    out
}

#[derive(Clone)]
pub struct CompiledRegex {
    pub re: Option<Regex>,
    pub ignore_case: bool,
    fallback: Option<Arc<CounterRegex>>,
}

impl CompiledRegex {
    pub fn captures_len(&self) -> usize {
        self.re.as_ref().map_or_else(
            || {
                self.fallback
                    .as_ref()
                    .expect("compiled regex backend")
                    .captures_len
            },
            Regex::captures_len,
        )
    }
}

#[derive(Default)]
struct RegexCache(VecDeque<(String, bool, CompiledRegex)>);

impl RegexCache {
    fn get(&self, pattern: &str, ignore_case: bool) -> Option<CompiledRegex> {
        self.0
            .iter()
            .find(|(p, i, _)| p == pattern && *i == ignore_case)
            .map(|(_, _, compiled)| compiled.clone())
    }

    fn insert(&mut self, pattern: &str, ignore_case: bool, compiled: CompiledRegex) {
        // Concurrent callers may finish compiling the same pattern. Preserve
        // its original insertion order rather than consuming another slot.
        if self
            .0
            .iter()
            .any(|(p, i, _)| p == pattern && *i == ignore_case)
        {
            return;
        }
        if self.0.len() == 256 {
            self.0.pop_front();
        }
        self.0
            .push_back((pattern.to_owned(), ignore_case, compiled));
    }
}

fn regex_cache() -> &'static Mutex<RegexCache> {
    static CACHE: OnceLock<Mutex<RegexCache>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(RegexCache::default()))
}

pub fn compile_sel_regex(
    pattern: &str,
    flags: &str,
    flag_pos: Pos,
    pat_pos: Pos,
) -> Result<CompiledRegex, SelError> {
    let mut ignore_case = false;
    for ch in flags.chars() {
        if ch == 'i' {
            ignore_case = true;
            continue;
        }
        // Quoted as a string, as the other hosts' messages quote it ("x").
        let quoted = format!("{:?}", ch.to_string());
        if ch == 'm' || ch == 's' {
            return Err(SelError::new(
                "E_BAD_ARG",
                format!(
                    "flag {} is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject",
                    quoted
                ),
                flag_pos,
            ));
        }
        return Err(SelError::new("E_BAD_ARG", format!("unknown regex flag {}", quoted), flag_pos));
    }

    if ignore_case {
        for r in pattern.chars() {
            if r as u32 > 0x7F {
                return Err(SelError::new(
                    "E_BAD_ARG",
                    "the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript",
                    flag_pos,
                ));
            }
        }
    }

    if let Some(compiled) = regex_cache().lock().unwrap().get(pattern, ignore_case) {
        return Ok(compiled);
    }

    let validated = validate_pattern_impl(pattern, pat_pos, true)?;
    crate::regex_ambiguity::analyse(&validated, ignore_case)
        .map_err(|reason| bad_regex(reason, pattern, 0, pat_pos))?;
    let lowered = lower_anchors(&validated);

    let mut prefix = String::from("(?s)");
    if ignore_case {
        prefix.push_str("(?i)");
    }
    let full_pattern = prefix + &lowered;

    let compiled = match Regex::new(&full_pattern) {
        Ok(re) => CompiledRegex {
            re: Some(re),
            ignore_case,
            fallback: None,
        },
        Err(regex::Error::CompiledTooBig(_)) => {
            let fallback = CounterRegex::new(&full_pattern)
                .map_err(|e| SelError::new("E_REGEX_SYNTAX", e.to_string(), pat_pos))?;
            CompiledRegex {
                re: None,
                ignore_case,
                fallback: Some(Arc::new(fallback)),
            }
        }
        Err(e) => {
            return Err(SelError::new(
                "E_REGEX_SYNTAX",
                format!("{} in /{}/", e, pattern),
                pat_pos,
            ))
        }
    };
    regex_cache()
        .lock()
        .unwrap()
        .insert(pattern, ignore_case, compiled.clone());
    Ok(compiled)
}

pub fn fold_subject(chars: &[char]) -> Vec<char> {
    chars
        .iter()
        .map(|&r| {
            if r == '\u{212A}' {
                'k'
            } else if r == '\u{017F}' {
                's'
            } else {
                r
            }
        })
        .collect()
}

#[derive(Clone, Debug)]
pub struct RegexMatch {
    pub start_cp: usize,
    pub end_cp: usize,
    pub groups: Vec<(Option<usize>, Option<usize>)>,
}

/// Every match of `cr` in `search_chars` (the subject as matched: case-folded
/// under `i`), as code-point spans of the subject.
pub fn find_matches(cr: &CompiledRegex, search_chars: &[char]) -> Vec<RegexMatch> {
    let search_str: String = search_chars.iter().collect();

    // Map byte offsets to code point offsets
    let mut byte_to_cp = Vec::with_capacity(search_str.len() + 1);
    for (cp, (byte_idx, _)) in search_str.char_indices().enumerate() {
        while byte_to_cp.len() < byte_idx {
            byte_to_cp.push(cp.saturating_sub(1));
        }
        byte_to_cp.push(cp);
    }
    while byte_to_cp.len() <= search_str.len() {
        byte_to_cp.push(search_chars.len());
    }

    let mut matches = Vec::new();
    let mut scan = 0;
    while scan <= search_str.len() {
        let groups = if let Some(re) = &cr.re {
            re.captures_at(&search_str, scan).map(|caps| {
                caps.iter()
                    .map(|m| m.map_or((None, None), |m| (Some(m.start()), Some(m.end()))))
                    .collect::<Vec<_>>()
            })
        } else {
            cr.fallback
                .as_ref()
                .expect("compiled regex backend")
                .captures_at(&search_str, scan)
        };
        let Some(groups) = groups else {
            break;
        };
        let (Some(start), Some(end)) = groups[0] else {
            unreachable!("whole regex match")
        };
        let start_cp = byte_to_cp[start];
        let end_cp = byte_to_cp[end];
        // SEL permits an empty match immediately after a nonempty match.
        scan = if start == end {
            end + search_str[end..].chars().next().map_or(1, char::len_utf8)
        } else {
            end
        };
        let groups = groups
            .into_iter()
            .map(|(s, e)| (s.map(|s| byte_to_cp[s]), e.map(|e| byte_to_cp[e])))
            .collect();
        matches.push(RegexMatch {
            start_cp,
            end_cp,
            groups,
        });
    }

    matches
}

pub fn expand_repl(
    repl: &str,
    m: &RegexMatch,
    num_groups: usize,
    orig_chars: &[char],
    pos: Pos,
) -> Result<String, SelError> {
    let mut out: Vec<u8> = Vec::with_capacity(repl.len());
    let bytes = repl.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] != b'$' {
            out.push(bytes[i]);
            i += 1;
            continue;
        }
        if i + 1 < bytes.len() {
            let nxt = bytes[i + 1];
            if nxt == b'$' {
                out.push(b'$');
                i += 2;
                continue;
            }
            if nxt.is_ascii_digit() {
                let g = (nxt - b'0') as usize;
                if g >= num_groups {
                    return Err(SelError::new(
                        "E_BAD_ARG",
                        format!(
                            "replacement refers to ${} but the pattern has {} groups",
                            g,
                            num_groups - 1
                        ),
                        pos,
                    ));
                }
                if let Some(&(Some(s), Some(e))) = m.groups.get(g) {
                    let s_str: String = orig_chars[s..e].iter().collect();
                    out.extend_from_slice(s_str.as_bytes());
                }
                i += 2;
                continue;
            }
        }
        out.push(b'$');
        i += 1;
    }
    String::from_utf8(out).map_err(|e| SelError::new("E_UTF8", e.to_string(), pos))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counted_fallback_works_through_replacement_builtin() {
        let mut program =
            crate::compile(r#"RREPLACE("(?:a{60000}){60000}|(b+)", "<$1>", "abbc")"#).unwrap();
        assert_eq!(
            program.run(None).unwrap().as_text(Pos::default()).unwrap(),
            "a<bb>c"
        );
        let mut program =
            crate::compile(r#"RREPLACE("(?:a{60000}){60000}|(b+)", "$2", "abbc")"#).unwrap();
        assert_eq!(program.run(None).unwrap_err().code, "E_BAD_ARG");
    }

    #[test]
    fn oversized_counted_repeat_can_match_its_full_subject() {
        let pos = Pos::default();
        let cr = compile_sel_regex("^(abcdefghij){60000}$", "", pos, pos).unwrap();
        assert!(cr.fallback.is_some());
        let subject: Vec<_> = "abcdefghij".repeat(60000).chars().collect();
        let matches = find_matches(&cr, &subject);
        assert_eq!(matches.len(), 1);
        assert_eq!(
            matches[0].groups,
            vec![(Some(0), Some(600000)), (Some(599990), Some(600000))]
        );
    }

    #[test]
    fn oversized_counted_repeats_preserve_matching_alternatives_and_captures() {
        let pos = Pos::default();
        for (pattern, flags, subject, expected) in [
            ("(?:a{60000}){60000}", "", "a", vec![]),
            ("(?:a{60000}){60000}|(b+)", "", "abb", vec![(1, 3)]),
            ("(?:a{60000}){60000}|(b+?)", "", "abb", vec![(1, 2), (2, 3)]),
            ("(?:a{60000}){60000}|(é)", "", "aé", vec![(1, 2)]),
            ("(?:a{60000}){60000}|(k+)", "i", "aKK", vec![(1, 3)]),
            ("(?:a{60000}){60000}|$", "", "a", vec![(1, 1)]),
        ] {
            let cr = compile_sel_regex(pattern, flags, pos, pos).unwrap();
            assert!(cr.fallback.is_some(), "{pattern}");
            let orig: Vec<_> = subject.chars().collect();
            let search = if cr.ignore_case {
                fold_subject(&orig)
            } else {
                orig.clone()
            };
            let matches = find_matches(&cr, &search);
            assert_eq!(
                matches
                    .iter()
                    .map(|m| (m.start_cp, m.end_cp))
                    .collect::<Vec<_>>(),
                expected
            );
            if pattern.contains("|(") {
                assert_eq!(cr.captures_len(), 2);
                for m in matches {
                    assert_eq!(m.groups[1], m.groups[0]);
                    assert_eq!(
                        expand_repl("$1", &m, cr.captures_len(), &orig, pos).unwrap(),
                        orig[m.start_cp..m.end_cp].iter().collect::<String>()
                    );
                }
            }
        }
    }

    #[test]
    fn regex_cache_is_fifo_bounded_and_flag_sensitive() {
        let compiled = CompiledRegex {
            re: Some(Regex::new("a").unwrap()),
            ignore_case: false,
            fallback: None,
        };
        let mut cache = RegexCache::default();
        cache.insert("a", false, compiled.clone());
        cache.insert("a", true, compiled.clone());
        for n in 0..254 {
            cache.insert(&format!("p{n}"), false, compiled.clone());
        }
        assert_eq!(cache.0.len(), 256);
        assert!(cache.get("a", false).is_some());
        cache.insert("a", false, compiled.clone());
        cache.insert("new", false, compiled);
        assert_eq!(cache.0.len(), 256);
        assert!(cache.get("a", false).is_none());
        assert!(cache.get("a", true).is_some());
    }

    #[test]
    fn cached_patterns_do_not_bypass_flag_validation_or_cache_error_positions() {
        let pos = Pos::default();
        compile_sel_regex("cacheflag", "", pos, pos).unwrap();
        let later = Pos {
            line: 9,
            col: 7,
            ..pos
        };
        let error = compile_sel_regex("cacheflag", "I", later, pos)
            .err()
            .unwrap();
        assert_eq!(error.code, "E_BAD_ARG");
        assert_eq!(error.pos, later);
        for location in [pos, later] {
            let error = compile_sel_regex("(a+)+", "", pos, location).err().unwrap();
            assert_eq!(error.pos.line, location.line);
        }
    }

    #[test]
    fn portable_structure_and_class_controls() {
        let pos = Pos::default();
        for pattern in [
            "a**",
            "a{1}?*",
            "^*",
            "(a?)+",
            "(?:(a)|b)*",
            "[+-\\d]",
            "[a[:digit:]]",
            // POSIX forms are refused unterminated too (SPEC 7.8).
            "[[.]",
            "[x[:y]",
            "[z-a]",
        ] {
            assert!(validate_pattern(pattern, pos).is_err(), "{pattern}");
        }
        for pattern in [
            "(a|b)+", "((a)b)+", "(a*)?", "[\\d-]", "[-\\d]", "[a\\[.]", "[&&]", "[~~]",
        ] {
            assert!(validate_pattern(pattern, pos).is_ok(), "{pattern}");
        }
        // SQL consumes the portable spelling, not Rust's class encoding.
        assert_eq!(validate_pattern("[a-z\\d]", pos).unwrap(), "[a-z0-9]");
    }

    #[test]
    fn empty_matches_advance_by_code_point_and_preserve_anchors() {
        let pos = Pos::default();
        let cr = compile_sel_regex("a*", "", pos, pos).unwrap();
        let chars: Vec<_> = "żaac".chars().collect();
        let spans: Vec<_> = find_matches(&cr, &chars)
            .iter()
            .map(|m| (m.start_cp, m.end_cp))
            .collect();
        assert_eq!(spans, [(0, 0), (1, 3), (3, 3), (4, 4)]);
        let cr = compile_sel_regex("^|$", "", pos, pos).unwrap();
        let spans: Vec<_> = find_matches(&cr, &chars)
            .iter()
            .map(|m| (m.start_cp, m.end_cp))
            .collect();
        assert_eq!(spans, [(0, 0), (4, 4)]);
    }
}
