use std::sync::Arc;

use crate::ast::{Node, NodeType};
use crate::dec::{dec_format, dec_parse};
use crate::limits::MAX_DEPTH;
use crate::manifest::{lookup_builtin, Entry as BuiltinEntry};
use crate::shape::{unique_record_shape, RecordShape};
use crate::utf8::{Pos, SelError};

const OPERATORS: &[&str] = &[
    "???", "??",
    "$==", "$!=", "$<=", "$>=",
    "$<", "$>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "&=",
    ".>",
    "+", "-", "*", "/", "%", "&", "=", "<", ">", "(", ")", "[", "]", ",", ";",
];

const RESERVED: &[&str] = &[
    "TRUE", "FALSE", "NULL",
    "AND", "OR", "NOT", "XOR",
    "EQL", "IN", "BAND", "BOR", "BXOR",
];

// Binding powers start at 3: a sequence (`;`) and a list (`,`) are parsed by
// their own functions, not through this table.
const BP_ASSIGN: u8 = 3;
const BP_OR: u8 = 4;
const BP_XOR: u8 = 5;
const BP_AND: u8 = 6;
const BP_NOT: u8 = 7;
const BP_COMPARE: u8 = 8;
const BP_COALESCE: u8 = 9;
const BP_BOR: u8 = 10;
const BP_BXOR: u8 = 11;
const BP_BAND: u8 = 12;
const BP_CONCAT: u8 = 13;
const BP_ADD: u8 = 14;
const BP_MUL: u8 = 15;
const BP_NEG: u8 = 16;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Assoc {
    Left,
    Right,
    NonAssoc,
}

#[derive(Clone, Copy, Debug)]
struct InfixEntry {
    bp: u8,
    assoc: Assoc,
}

fn get_infix_op(op: &str) -> Option<InfixEntry> {
    match op {
        "??" | "???" => Some(InfixEntry { bp: BP_COALESCE, assoc: Assoc::Right }),
        "&" => Some(InfixEntry { bp: BP_CONCAT, assoc: Assoc::Left }),
        "+" | "-" => Some(InfixEntry { bp: BP_ADD, assoc: Assoc::Left }),
        "*" | "/" | "%" => Some(InfixEntry { bp: BP_MUL, assoc: Assoc::Left }),
        "=" | "+=" | "-=" | "*=" | "/=" | "%=" | "&=" => Some(InfixEntry { bp: BP_ASSIGN, assoc: Assoc::Right }),
        "==" | "!=" | "<" | "<=" | ">" | ">="
        | "$==" | "$!=" | "$<" | "$<=" | "$>" | "$>=" => Some(InfixEntry { bp: BP_COMPARE, assoc: Assoc::NonAssoc }),
        _ => None,
    }
}

fn get_infix_word(word: &str) -> Option<InfixEntry> {
    match word {
        "OR" => Some(InfixEntry { bp: BP_OR, assoc: Assoc::Left }),
        "XOR" => Some(InfixEntry { bp: BP_XOR, assoc: Assoc::Left }),
        "AND" => Some(InfixEntry { bp: BP_AND, assoc: Assoc::Left }),
        "BOR" => Some(InfixEntry { bp: BP_BOR, assoc: Assoc::Left }),
        "BXOR" => Some(InfixEntry { bp: BP_BXOR, assoc: Assoc::Left }),
        "BAND" => Some(InfixEntry { bp: BP_BAND, assoc: Assoc::Left }),
        "EQL" | "IN" => Some(InfixEntry { bp: BP_COMPARE, assoc: Assoc::NonAssoc }),
        _ => None,
    }
}

fn is_assign_op(op: &str) -> bool {
    matches!(op, "=" | "+=" | "-=" | "*=" | "/=" | "%=" | "&=")
}

fn is_reserved(word: &str) -> bool {
    RESERVED.contains(&word)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TokenType {
    Num,
    Text,
    Ident,
    Op,
    Eof,
}

#[derive(Clone, Debug)]
pub struct Token {
    pub t: TokenType,
    pub val: String,
    pub pos: Pos,
}

pub struct Lexer {
    chars: Vec<char>,
    n: usize,
    line_starts: Vec<usize>,
    // brace_ends[i]: index just past the '}' matching the '{' at i, once some
    // scan has established it (0 = not yet). See match_brace.
    brace_ends: std::cell::RefCell<Vec<usize>>,
}

impl Lexer {
    pub fn new(source: &str) -> Result<Self, SelError> {
        let chars: Vec<char> = source.chars().collect();
        let mut line_starts = vec![0];
        for (i, &ch) in chars.iter().enumerate() {
            if ch == '\n' {
                line_starts.push(i + 1);
            }
        }
        let n = chars.len();
        Ok(Self {
            chars,
            n,
            line_starts,
            brace_ends: std::cell::RefCell::new(vec![0; n]),
        })
    }

    pub fn pos_at(&self, offset: usize) -> Pos {
        let mut lo = 0;
        let mut hi = self.line_starts.len() - 1;
        while lo < hi {
            let mid = (lo + hi + 1) / 2;
            if self.line_starts[mid] <= offset {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        Pos {
            line: lo + 1,
            col: offset - self.line_starts[lo] + 1,
            offset,
        }
    }

    pub fn tokenize(&self) -> Result<Vec<Token>, SelError> {
        let mut out = Vec::with_capacity(32);
        self.lex_range(0, self.n, &mut out)?;
        out.push(Token {
            t: TokenType::Eof,
            val: String::new(),
            pos: self.pos_at(self.n),
        });
        Ok(out)
    }

    // Lexes chars[frm, to) into `out`. Interpolation nests without bound, so
    // this is a loop over an explicit stack of tasks rather than a recursion: a
    // literal pushes what it still has to emit (its parts, each interior range,
    // the closers) and the loop pops them in source order. Nothing here can
    // reach the host's own stack, however deep the braces go.
    //
    // `bals` holds one stack of open parentheses/brackets per interpolation
    // body; a task refers to its body's stack by index (None at top level).
    fn lex_range(&self, frm: usize, to: usize, out: &mut Vec<Token>) -> Result<(), SelError> {
        let mut bals: Vec<Vec<char>> = Vec::new();
        let mut stack = vec![LexTask::Range { i: frm, to, bal: None }];
        while let Some(task) = stack.pop() {
            match task {
                LexTask::Range { i, to, bal } => self.lex_tokens(i, to, out, &mut stack, &mut bals, bal)?,
                LexTask::Part { part, index, pos } => {
                    if index > 0 {
                        out.push(Token { t: TokenType::Op, val: "&".to_string(), pos });
                    }
                    if !part.is_expr {
                        out.push(Token { t: TokenType::Text, val: part.text, pos });
                        continue;
                    }
                    let mark = out.len();
                    bals.push(Vec::new());
                    let bal = bals.len() - 1;
                    out.push(Token { t: TokenType::Op, val: "(".to_string(), pos: self.pos_at(part.frm) });
                    stack.push(LexTask::Close { mark, frm: part.frm, to: part.to, bal });
                    stack.push(LexTask::Range { i: part.frm, to: part.to, bal: Some(bal) });
                }
                LexTask::Close { mark, frm, to, bal } => {
                    // An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
                    if out.len() == mark + 1 {
                        return Err(SelError::syntax("empty interpolation {}", self.pos_at(frm)));
                    }
                    // ... and one whose parentheses do not close inside the braces.
                    if let Some(open) = bals[bal].last() {
                        return Err(SelError::syntax(
                            format!("unclosed {} in interpolation", open),
                            self.pos_at(to),
                        ));
                    }
                    out.push(Token { t: TokenType::Op, val: ")".to_string(), pos: self.pos_at(to) });
                }
                LexTask::End { pos } => out.push(Token { t: TokenType::Op, val: ")".to_string(), pos }),
            }
        }
        Ok(())
    }

    // The flat part of lex_range. A quoted literal with parts ends the run: the
    // tasks it pushes come first, and the rest of the range resumes after them.
    //
    // A body is spliced into the surrounding tokens as `( body )`, so a body
    // that closes what it never opened, or leaves something open, would change
    // the meaning of the text around it; each body balances inside its braces.
    fn lex_tokens(
        &self,
        frm: usize,
        to: usize,
        out: &mut Vec<Token>,
        stack: &mut Vec<LexTask>,
        bals: &mut [Vec<char>],
        bal: Option<usize>,
    ) -> Result<(), SelError> {
        let mut i = frm;
        while i < to {
            let c = self.chars[i];

            if crate::utf8::is_sel_space(c) {
                i += 1;
                continue;
            }

            if c == '#' {
                while i < to && self.chars[i] != '\n' {
                    i += 1;
                }
                continue;
            }

            let pos = self.pos_at(i);

            if c.is_ascii_digit() {
                let mut j = i;
                while j < to && self.chars[j].is_ascii_digit() {
                    j += 1;
                }
                // Only consume the dot when a digit follows, so `1.` is not a number
                if j + 1 < to && self.chars[j] == '.' && self.chars[j + 1].is_ascii_digit() {
                    j += 1;
                    while j < to && self.chars[j].is_ascii_digit() {
                        j += 1;
                    }
                }
                let val: String = self.chars[i..j].iter().collect();
                out.push(Token {
                    t: TokenType::Num,
                    val,
                    pos,
                });
                i = j;
                continue;
            }

            if c.is_ascii_alphabetic() || c == '_' {
                let mut j = i;
                while j < to && (self.chars[j].is_ascii_alphanumeric() || self.chars[j] == '_') {
                    j += 1;
                }
                let word: String = self.chars[i..j].iter().collect();
                out.push(Token {
                    t: TokenType::Ident,
                    val: word.to_ascii_uppercase(),
                    pos,
                });
                i = j;
                continue;
            }

            if c == '"' {
                let (mut parts, next) = self.scan_quoted(i, to)?;
                if parts.len() == 1 {
                    out.push(Token { t: TokenType::Text, val: parts.pop().unwrap().text, pos });
                    i = next;
                    continue;
                }
                // `( "seg" & expr & "seg" )`: the opener now, the rest as
                // tasks, the remainder of this range underneath them.
                out.push(Token { t: TokenType::Op, val: "(".to_string(), pos });
                stack.push(LexTask::Range { i: next, to, bal });
                stack.push(LexTask::End { pos });
                for (index, part) in parts.into_iter().enumerate().rev() {
                    stack.push(LexTask::Part { part, index, pos });
                }
                return Ok(());
            }

            if c == '\'' {
                i = self.lex_raw(i, to, out)?;
                continue;
            }

            if let Some(op) = self.match_operator(i, to) {
                if let Some(b) = bal {
                    let open = &mut bals[b];
                    if op == "(" || op == "[" {
                        open.push(op.chars().next().unwrap());
                    } else if op == ")" || op == "]" {
                        let is_paren = op == ")";
                        if open.is_empty() || (open.last() == Some(&'(')) != is_paren {
                            return Err(SelError::syntax(
                                format!("unbalanced {} in interpolation", op),
                                pos,
                            ));
                        }
                        open.pop();
                    }
                }
                out.push(Token {
                    t: TokenType::Op,
                    val: op.to_string(),
                    pos,
                });
                i += op.chars().count();
                continue;
            }

            return Err(SelError::syntax(format!("unexpected character {:?}", c), pos));
        }
        Ok(())
    }

    fn match_operator(&self, i: usize, to: usize) -> Option<&'static str> {
        // Every operator is ASCII, so its bytes are its characters.
        OPERATORS.iter().copied().find(|op| {
            i + op.len() <= to && op.bytes().enumerate().all(|(k, b)| self.chars[i + k] == b as char)
        })
    }

    fn lex_raw(&self, start: usize, to: usize, out: &mut Vec<Token>) -> Result<usize, SelError> {
        let pos = self.pos_at(start);
        let mut i = start + 1;
        let mut buf = String::new();
        while i < to {
            let c = self.chars[i];
            if c == '\'' {
                if i + 1 < to && self.chars[i + 1] == '\'' {
                    buf.push('\'');
                    i += 2;
                    continue;
                }
                out.push(Token {
                    t: TokenType::Text,
                    val: buf,
                    pos,
                });
                return Ok(i + 1);
            }
            buf.push(c);
            i += 1;
        }
        Err(SelError::new("E_UNTERMINATED", "unterminated raw text literal", pos))
    }

    // Reads a quoted literal into its parts and the index just past its
    // closing quote, emitting nothing. Every `{...}` is only located here (by
    // match_brace); its interior is lexed later as a task of its own.
    fn scan_quoted(&self, start: usize, to: usize) -> Result<(Vec<TextPart>, usize), SelError> {
        let pos = self.pos_at(start);
        let mut parts: Vec<TextPart> = Vec::new();
        let mut buf = String::new();
        let mut i = start + 1;

        while i < to {
            let c = self.chars[i];

            if c == '"' {
                parts.push(TextPart { is_expr: false, text: buf, frm: 0, to: 0 });
                return Ok((parts, i + 1));
            }

            if c == '\\' {
                let (esc_text, next_idx) = self.read_escape(i, to)?;
                buf.push_str(&esc_text);
                i = next_idx;
                continue;
            }

            if c == '{' {
                let close_idx = self.match_brace(i, to)? - 1; // index of matching '}'
                parts.push(TextPart { is_expr: false, text: std::mem::take(&mut buf), frm: 0, to: 0 });
                parts.push(TextPart { is_expr: true, text: String::new(), frm: i + 1, to: close_idx });
                i = close_idx + 1;
                continue;
            }

            buf.push(c);
            i += 1;
        }
        Err(SelError::new("E_UNTERMINATED", "unterminated text literal", pos))
    }

    fn read_escape(&self, i: usize, to: usize) -> Result<(String, usize), SelError> {
        let pos = self.pos_at(i);
        if i + 1 >= to {
            return Err(SelError::new("E_UNTERMINATED", "text literal ends in a backslash", pos));
        }
        let e = self.chars[i + 1];

        match e {
            '\\' => Ok(("\\".to_string(), i + 2)),
            '"' => Ok(("\"".to_string(), i + 2)),
            'n' => Ok(("\n".to_string(), i + 2)),
            't' => Ok(("\t".to_string(), i + 2)),
            'r' => Ok(("\r".to_string(), i + 2)),
            '{' => Ok(("{".to_string(), i + 2)),
            '}' => Ok(("}".to_string(), i + 2)),
            'u' => {
                if i + 2 >= to || self.chars[i + 2] != '{' {
                    return Err(SelError::new("E_ESCAPE", "\\u must be followed by {", pos));
                }
                let mut j = i + 3;
                let mut hex_runes = Vec::new();
                while j < to && self.chars[j] != '}' {
                    hex_runes.push(self.chars[j]);
                    j += 1;
                }
                if j >= to {
                    return Err(SelError::new("E_UNTERMINATED", "unterminated \\u{...} escape", pos));
                }
                let hex_str: String = hex_runes.into_iter().collect();
                if hex_str.is_empty() || hex_str.len() > 6 || !hex_str.chars().all(|c| c.is_ascii_hexdigit()) {
                    return Err(SelError::new("E_ESCAPE", format!("bad \\u{{{}}} escape", hex_str), pos));
                }
                let cp = u32::from_str_radix(&hex_str, 16).map_err(|_| {
                    SelError::new("E_ESCAPE", format!("bad \\u{{{}}} escape", hex_str), pos)
                })?;
                if cp > 0x10FFFF || (0xD800..=0xDFFF).contains(&cp) {
                    return Err(SelError::range(
                        format!("code point U+{} is not encodable", hex_str.to_ascii_uppercase()),
                        pos,
                    ));
                }
                let ch = char::from_u32(cp).ok_or_else(|| {
                    SelError::range(
                        format!("code point U+{} is not encodable", hex_str.to_ascii_uppercase()),
                        pos,
                    )
                })?;
                Ok((ch.to_string(), j + 1))
            }
            _ => Err(SelError::new("E_ESCAPE", format!("unknown escape \\{}", e), pos)),
        }
    }

    // Returns the index just past the '}' matching the '{' at i. Nested
    // literals are skipped so that a brace inside a string inside an
    // interpolation does not close it.
    //
    // One pass with an explicit stack of what is open (a brace, a string), and
    // every brace it closes is remembered in brace_ends: a literal nested d deep
    // is located by its parent and again while each ancestor's interior is
    // lexed, and without the memo each of those passes re-reads everything
    // below it. If anything is unterminated the innermost open construct is the
    // one reported.
    fn match_brace(&self, i: usize, to: usize) -> Result<usize, SelError> {
        let known = self.brace_ends.borrow()[i];
        if known != 0 {
            return Ok(known);
        }
        // (is_string, start, brace depth)
        let mut open: Vec<(bool, usize, usize)> = vec![(false, i, 0)];
        let mut j = i;
        loop {
            let top = open.len() - 1;
            let (is_str, at, _) = open[top];
            if j >= to {
                let msg = if is_str { "unterminated text literal" } else { "unterminated { in text literal" };
                return Err(SelError::new("E_UNTERMINATED", msg, self.pos_at(at)));
            }
            let c = self.chars[j];
            if is_str {
                match c {
                    '\\' => j += 2,
                    '"' => {
                        open.pop();
                        j += 1;
                    }
                    '{' => open.push((false, j, 0)),
                    _ => j += 1,
                }
                continue;
            }
            match c {
                '"' => {
                    open.push((true, j, 0));
                    j += 1;
                }
                '\'' => j = self.skip_raw(j, to)?,
                '{' => {
                    open[top].2 += 1;
                    j += 1;
                }
                '}' => {
                    open[top].2 -= 1;
                    j += 1;
                    if open[top].2 == 0 {
                        self.brace_ends.borrow_mut()[at] = j;
                        open.pop();
                        if open.is_empty() {
                            return Ok(j);
                        }
                    }
                }
                '#' => {
                    while j < to && self.chars[j] != '\n' {
                        j += 1;
                    }
                }
                _ => j += 1,
            }
        }
    }

    fn skip_raw(&self, j: usize, to: usize) -> Result<usize, SelError> {
        let pos = self.pos_at(j);
        let mut k = j + 1;
        while k < to {
            if self.chars[k] == '\'' {
                if k + 1 < to && self.chars[k + 1] == '\'' {
                    k += 2;
                    continue;
                }
                return Ok(k + 1);
            }
            k += 1;
        }
        Err(SelError::new("E_UNTERMINATED", "unterminated raw text literal", pos))
    }
}

enum LexTask {
    Range { i: usize, to: usize, bal: Option<usize> },
    Part { part: TextPart, index: usize, pos: Pos },
    Close { mark: usize, frm: usize, to: usize, bal: usize },
    End { pos: Pos },
}

struct TextPart {
    is_expr: bool,
    text: String,
    frm: usize,
    to: usize,
}

pub struct Parser {
    toks: Vec<Token>,
    i: usize,
    depth: usize,
}

impl Parser {
    pub fn new(toks: Vec<Token>) -> Self {
        Self {
            toks,
            i: 0,
            depth: 0,
        }
    }

    fn peek(&self) -> &Token {
        if self.i < self.toks.len() {
            &self.toks[self.i]
        } else {
            &self.toks[self.toks.len() - 1]
        }
    }

    fn peek_ahead(&self, offset: usize) -> &Token {
        let idx = self.i + offset;
        if idx < self.toks.len() {
            &self.toks[idx]
        } else {
            &self.toks[self.toks.len() - 1]
        }
    }

    fn next(&mut self) -> Token {
        let t = self.peek().clone();
        if self.i < self.toks.len() {
            self.i += 1;
        }
        t
    }

    fn at_op(&self, v: &str) -> bool {
        let t = self.peek();
        t.t == TokenType::Op && t.val == v
    }

    fn at_eof(&self) -> bool {
        self.peek().t == TokenType::Eof
    }

    fn expect_op(&mut self, v: &str) -> Result<Token, SelError> {
        if !self.at_op(v) {
            let t = self.peek();
            return Err(SelError::syntax(
                format!("expected {:?}, got {}", v, describe_token(t)),
                t.pos,
            ));
        }
        Ok(self.next())
    }

    fn enter(&mut self, pos: Pos) -> Result<(), SelError> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            return Err(SelError::depth("expression nested too deeply", pos));
        }
        Ok(())
    }

    fn leave(&mut self) {
        if self.depth > 0 {
            self.depth -= 1;
        }
    }

    pub fn parse_program(&mut self) -> Result<Node, SelError> {
        let node = self.sequence()?;
        if !self.at_eof() {
            let t = self.peek();
            return Err(SelError::syntax(
                format!("unexpected {}", describe_token(t)),
                t.pos,
            ));
        }
        Ok(*node)
    }

    // The recursive descent below passes nodes boxed. A `Node` is over 200
    // bytes, and every level of nesting runs through several of these frames:
    // moving nodes by value made the parser need ~1 MiB of stack in release
    // (and over 2 MiB in a debug build) to reach MAX_DEPTH.

    fn sequence(&mut self) -> PResult {
        let start = self.peek().pos;
        self.enter(start)?;
        let first = self.list()?;
        let mut rest: Vec<Node> = Vec::new();
        while self.at_op(";") {
            self.next();
            if self.at_eof() || self.at_op(")") || self.at_op("]") {
                break;
            }
            let item = self.list()?;
            push_unboxed(&mut rest, item);
        }
        self.leave();
        if rest.is_empty() {
            return Ok(first);
        }
        Ok(collect_node(NodeType::Seq, first, rest))
    }

    fn list(&mut self) -> PResult {
        let first = self.term(BP_ASSIGN)?;
        let mut rest: Vec<Node> = Vec::new();
        while self.at_op(",") {
            self.next();
            let item = self.term(BP_ASSIGN)?;
            push_unboxed(&mut rest, item);
        }
        if rest.is_empty() {
            return Ok(first);
        }
        Ok(collect_node(NodeType::List, first, rest))
    }

    fn infix_entry(t: &Token) -> Option<InfixEntry> {
        if t.t == TokenType::Op {
            return get_infix_op(&t.val);
        }
        if t.t == TokenType::Ident {
            return get_infix_word(&t.val);
        }
        None
    }

    fn term(&mut self, min_bp: u8) -> PResult {
        let mut left = self.prefix(min_bp)?;

        loop {
            let (bp, assoc) = match Self::infix_entry(self.peek()) {
                Some(e) if e.bp >= min_bp => (e.bp, e.assoc),
                _ => return Ok(left),
            };

            let t = self.next();

            if is_assign_op(&t.val) {
                check_target(&left, &t)?;
                self.enter(t.pos)?;
                let value = self.term(bp)?;
                self.leave();
                left = binary_node(NodeType::Assign, left.pos, t.val, left, value);
                continue;
            }

            if assoc == Assoc::Right {
                self.enter(t.pos)?;
                let right = self.term(bp)?;
                self.leave();
                left = binary_node(NodeType::Bin, t.pos, t.val, left, right);
                continue;
            }

            if assoc == Assoc::NonAssoc {
                let right = self.term(bp + 1)?;
                let after = self.peek();
                if let Some(after_entry) = Self::infix_entry(after) {
                    if after_entry.assoc == Assoc::NonAssoc {
                        return Err(SelError::syntax(
                            format!(
                                "comparison operators do not chain \u{2014} parenthesise, as in (a {} b) AND (b {} c)",
                                t.val, after.val
                            ),
                            after.pos,
                        ));
                    }
                }
                left = binary_node(NodeType::Bin, t.pos, t.val, left, right);
                continue;
            }

            let right = self.term(bp + 1)?;
            left = binary_node(NodeType::Bin, t.pos, t.val, left, right);
        }
    }

    fn prefix(&mut self, min_bp: u8) -> PResult {
        let t = self.peek();

        if t.t == TokenType::Ident && t.val == "NOT" && min_bp <= BP_NOT {
            let op_tok = self.next();
            self.enter(op_tok.pos)?;
            let x = self.term(BP_NOT)?;
            self.leave();
            return Ok(unary_node(op_tok.pos, "NOT", x));
        }

        if t.t == TokenType::Op && t.val == "-" && min_bp <= BP_NEG {
            let op_tok = self.next();
            self.enter(op_tok.pos)?;
            let x = self.term(BP_NEG)?;
            self.leave();
            return Ok(unary_node(op_tok.pos, "NEG", x));
        }

        self.postfix()
    }

    fn postfix(&mut self) -> PResult {
        let mut node = self.primary()?;
        while self.at_op("[") || self.at_op(".>") {
            if self.at_op("[") {
                let br = self.next();
                self.enter(br.pos)?;
                let idx = self.sequence()?;
                self.expect_op("]")?;
                self.leave();
                node = binary_node(NodeType::Index, br.pos, String::new(), node, idx);
            } else {
                self.next(); // consume '.>'
                node = self.pipe_step(node)?;
            }
        }
        Ok(node)
    }

    fn pipe_step(&mut self, left: PNode) -> PResult {
        let t = self.peek();
        if t.t != TokenType::Ident || t.val == "TRUE" || t.val == "FALSE" || t.val == "NULL" {
            return Err(SelError::syntax(
                "right-hand side of .> must be a function call or function name",
                t.pos,
            ));
        }
        let name_tok = self.next();
        let mut args = Vec::new();
        if self.at_op("(") {
            self.next();
            if self.at_op(")") {
                self.next();
            } else {
                let inner = self.sequence()?;
                self.expect_op(")")?;
                args = call_arguments(inner);
            }
        }

        let spec = lookup_any_function(&name_tok.val).ok_or_else(|| {
            SelError::new(
                "E_UNKNOWN_FUNC",
                format!("unknown function {}", name_tok.val),
                name_tok.pos,
            )
        })?;

        let mut has_placeholder = false;
        if !spec.binds() && args.len() >= spec.min() {
            for arg in &mut args {
                if arg.t == NodeType::Var && arg.s == "_" && !arg.grouped {
                    *arg = (*left).clone();
                    has_placeholder = true;
                }
            }
        }

        if !has_placeholder {
            insert_unboxed(&mut args, 0, left);
        }

        finish_call(name_tok, &spec, args)
    }

    fn primary(&mut self) -> PResult {
        let pos = self.peek().pos;
        self.enter(pos)?;
        let res = self.primary_inner()?;
        self.leave();
        Ok(res)
    }

    fn primary_inner(&mut self) -> PResult {
        let t = self.peek();
        let pos = t.pos;
        if t.t == TokenType::Num {
            let t = self.next();
            let parsed = dec_parse(&t.val, t.pos)?;
            let mut n = new_node(NodeType::Num, pos);
            n.s = dec_format(&parsed);
            n.dec = Some(parsed);
            return Ok(n);
        }

        if t.t == TokenType::Text {
            let t = self.next();
            let mut n = new_node(NodeType::Text, pos);
            n.s = t.val;
            return Ok(n);
        }

        if t.t == TokenType::Ident {
            if t.val == "TRUE" || t.val == "FALSE" {
                let t = self.next();
                let mut n = new_node(NodeType::Bool, pos);
                n.b = t.val == "TRUE";
                return Ok(n);
            }
            if t.val == "NULL" {
                self.next();
                return Ok(new_node(NodeType::Null, pos));
            }
            let after = self.peek_ahead(1);
            if after.t == TokenType::Op && after.val == "(" {
                return self.call();
            }
            if is_reserved(&t.val) {
                return Err(SelError::new(
                    "E_RESERVED",
                    format!("{} is a reserved word and cannot be a variable", t.val),
                    pos,
                ));
            }
            let t = self.next();
            let mut n = new_node(NodeType::Var, pos);
            n.s = t.val;
            return Ok(n);
        }

        if t.t == TokenType::Op && t.val == "(" {
            self.next();
            if self.at_op(")") {
                return Err(SelError::syntax("empty parentheses", pos));
            }
            let mut inner = self.sequence()?;
            self.expect_op(")")?;
            inner.grouped = true;
            return Ok(inner);
        }

        Err(SelError::syntax(format!("unexpected {}", describe_token(t)), pos))
    }

    fn call(&mut self) -> PResult {
        let name_tok = self.next();
        self.expect_op("(")?;
        let mut args = Vec::new();
        if self.at_op(")") {
            self.next();
        } else {
            let inner = self.sequence()?;
            self.expect_op(")")?;
            args = call_arguments(inner);
        }

        let spec = lookup_any_function(&name_tok.val).ok_or_else(|| {
            SelError::new(
                "E_UNKNOWN_FUNC",
                format!("unknown function {}", name_tok.val),
                name_tok.pos,
            )
        })?;

        finish_call(name_tok, &spec, args)
    }
}

type PNode = Box<Node>;
type PResult = Result<PNode, SelError>;

// Node construction lives in these small out-of-line functions so that the
// 200-byte `Node` temporaries are in their frames, not in the recursive
// parse frames above them.
#[inline(never)]
fn new_node(t: NodeType, pos: Pos) -> PNode {
    Box::new(Node::new(t, pos))
}

#[inline(never)]
fn binary_node(t: NodeType, pos: Pos, op: String, l: PNode, r: PNode) -> PNode {
    let mut n = new_node(t, pos);
    n.s = op;
    n.l = Some(l);
    n.r = Some(r);
    n
}

#[inline(never)]
fn unary_node(pos: Pos, op: &str, x: PNode) -> PNode {
    let mut n = new_node(NodeType::Un, pos);
    n.s = op.to_string();
    n.l = Some(x);
    n
}

#[inline(never)]
fn push_unboxed(items: &mut Vec<Node>, node: PNode) {
    items.push(*node);
}

#[inline(never)]
fn insert_unboxed(items: &mut Vec<Node>, at: usize, node: PNode) {
    items.insert(at, *node);
}

// A sequence or list node from its first item (boxed) and the rest, at the
// first item's position.
#[inline(never)]
fn collect_node(t: NodeType, first: PNode, rest: Vec<Node>) -> PNode {
    let mut n = new_node(t, first.pos);
    let mut items = Vec::with_capacity(rest.len() + 1);
    items.push(*first);
    items.extend(rest);
    n.items = items;
    n
}

// The arguments written between a call's parentheses: an ungrouped list is
// the argument list, anything else is one argument.
#[inline(never)]
fn call_arguments(mut inner: PNode) -> Vec<Node> {
    if inner.t == NodeType::List && !inner.grouped {
        std::mem::take(&mut inner.items)
    } else {
        vec![*inner]
    }
}

enum FunctionSpec {
    Builtin(&'static BuiltinEntry),
    Host(Arc<crate::builtins::Spec>),
}

impl FunctionSpec {
    fn name(&self) -> &str {
        match self {
            FunctionSpec::Builtin(b) => b.name,
            FunctionSpec::Host(h) => &h.name,
        }
    }

    fn min(&self) -> usize {
        match self {
            FunctionSpec::Builtin(b) => b.min,
            FunctionSpec::Host(h) => h.min,
        }
    }

    fn max(&self) -> Option<usize> {
        match self {
            FunctionSpec::Builtin(b) => b.max,
            FunctionSpec::Host(h) => h.max,
        }
    }

    fn binds(&self) -> bool {
        match self {
            FunctionSpec::Builtin(b) => b.binds,
            FunctionSpec::Host(h) => h.binds,
        }
    }

    fn check_arity(&self, count: usize, pos: Pos) -> Result<(), SelError> {
        if count < self.min() || (self.max().is_some() && count > self.max().unwrap()) {
            return Err(SelError::arity(
                format!("{} takes {}, got {}", self.name(), self.arity_text(), count),
                pos,
            ));
        }
        if let FunctionSpec::Builtin(b) = self {
            if let Some(err_fn) = b.arity_error {
                if let Some(problem) = err_fn(count) {
                    return Err(SelError::arity(problem, pos));
                }
            }
        }
        Ok(())
    }

    fn arity_text(&self) -> String {
        match self.max() {
            None => {
                if self.min() == 1 {
                    "at least 1 argument".to_string()
                } else {
                    format!("at least {} arguments", self.min())
                }
            }
            Some(max) => {
                if self.min() == max {
                    if self.min() == 1 {
                        "1 argument".to_string()
                    } else {
                        format!("{} arguments", self.min())
                    }
                } else {
                    format!("{} to {} arguments", self.min(), max)
                }
            }
        }
    }
}

fn lookup_any_function(name: &str) -> Option<FunctionSpec> {
    if let Some(b) = lookup_builtin(name) {
        return Some(FunctionSpec::Builtin(b));
    }
    if let Some(h) = crate::builtins::lookup_spec(name) {
        return Some(FunctionSpec::Host(h));
    }
    None
}

fn prepare_record_shape(name: &str, args: &[Node]) -> Option<Arc<RecordShape>> {
    if name != "RECORD" || args.is_empty() || args.len() % 2 != 0 {
        return None;
    }
    let mut keys = Vec::with_capacity(args.len() / 2);
    for i in (0..args.len()).step_by(2) {
        if args[i].t != NodeType::Text {
            return None;
        }
        keys.push(args[i].s.clone());
    }
    unique_record_shape(&keys)
}

#[inline(never)]
fn finish_call(name_tok: Token, spec: &FunctionSpec, args: Vec<Node>) -> PResult {
    spec.check_arity(args.len(), name_tok.pos)?;
    if matches!(spec.name(), "RMATCH" | "RFIND" | "RGROUPS" | "RREPLACE") {
        if let Some(pattern) = args.first().filter(|a| a.t == NodeType::Text) {
            let flag_at = if spec.name() == "RREPLACE" { 3 } else { 2 };
            let ignore_case = pattern.s.is_ascii() && args.get(flag_at)
                .map_or(false, |a| a.t == NodeType::Text && a.s.contains('i'));
            crate::regex::validate_pattern_with_case(&pattern.s, pattern.pos, ignore_case)?;
        }
    }
    let mut n = new_node(NodeType::Call, name_tok.pos);
    n.s = spec.name().to_string();
    n.spec = crate::builtins::lookup_spec(spec.name());
    n.shape = prepare_record_shape(spec.name(), &args);
    n.items = args;
    Ok(n)
}

fn describe_token(t: &Token) -> String {
    match t.t {
        TokenType::Eof => "end of input".to_string(),
        TokenType::Text => "a text literal".to_string(),
        TokenType::Num => format!("number {}", t.val),
        _ => format!("{:?}", t.val),
    }
}

fn check_target(node: &Node, op_tok: &Token) -> Result<(), SelError> {
    let mut n = node;
    while n.t == NodeType::Index {
        if let Some(ref l) = n.l {
            n = l;
        } else {
            break;
        }
    }
    if n.t != NodeType::Var || node.grouped {
        return Err(SelError::new(
            "E_BAD_ASSIGN",
            format!("cannot assign with {} to this expression", op_tok.val),
            node.pos,
        ));
    }
    Ok(())
}

pub fn tokenize(source: &str) -> Result<Vec<Token>, SelError> {
    Lexer::new(source)?.tokenize()
}

pub fn parse(source: &str) -> Result<Node, SelError> {
    Parser::new(tokenize(source)?).parse_program()
}
