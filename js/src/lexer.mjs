// Tokeniser. See spec/grammar.md.
//
// The source is held as an array of single-code-point strings, so every offset,
// line and column in an error is a code point index. PHP does the same, which is
// what keeps reported positions identical across hosts.
//
// String interpolation is resolved here and nowhere else: a literal containing
// {…} is emitted as the token stream of a parenthesised `&` chain, so the parser
// never learns that interpolation exists.

import { fail } from './errors.mjs';
import { toCodePoints, fromCodePoints, SOURCE } from './utf8.mjs';

export const OPERATORS = [
  '???', '??',
  '$==', '$!=', '$<=', '$>=',
  '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
  '.>',
  '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
];

export const RESERVED = new Set([
  'TRUE', 'FALSE', 'NULL', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
]);

const SIMPLE_ESCAPES = {
  '\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', '{': '{', '}': '}',
};

const isDigit = (c) => c >= '0' && c <= '9';
const isAlpha = (c) => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c === '_';
const isIdent = (c) => isAlpha(c) || isDigit(c);
const isSpace = (c) => c === ' ' || c === '\t' || c === '\r' || c === '\n';

// The kinds of work lexRange keeps on its explicit stack.
const T_RANGE = 0, T_PART = 1, T_CLOSE = 2, T_END = 3;

class Lexer {
  constructor(source) {
    // Splitting on code points also validates the source: a lone surrogate here
    // is E_UTF8 rather than a silently mangled token.
    this.chars = toCodePoints(source, SOURCE).map((c) => fromCodePoints([c]));
    this.n = this.chars.length;
    // braceEnds[i] is the index just past the '}' matching the '{' at i, once
    // some scan has established it (0 = not yet). See matchBrace.
    this.braceEnds = new Int32Array(this.n);
    this.lineStarts = [0];
    for (let i = 0; i < this.n; i++) {
      if (this.chars[i] === '\n') this.lineStarts.push(i + 1);
    }
  }

  posAt(offset) {
    let lo = 0, hi = this.lineStarts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.lineStarts[mid] <= offset) lo = mid; else hi = mid - 1;
    }
    return { line: lo + 1, col: offset - this.lineStarts[lo] + 1, offset };
  }

  slice(from, to) { return this.chars.slice(from, to).join(''); }

  tokenize() {
    const out = [];
    this.lexRange(0, this.n, out);
    out.push({ type: 'eof', value: '', ...this.posAt(this.n) });
    return out;
  }

  // Lexes chars[from, to) into `out`. Interpolation nests without bound, so this
  // is a loop over an explicit stack of tasks rather than a recursion: a literal
  // pushes what it still has to emit (its parts, each interior range, the
  // closers) and the loop pops them in source order. Nothing here can therefore
  // reach the host's own stack, however deep the braces go.
  lexRange(from, to, out) {
    const stack = [{ k: T_RANGE, i: from, to, bal: null }];
    while (stack.length > 0) {
      const task = stack.pop();
      switch (task.k) {
        case T_RANGE: this.lexTokens(task.i, task.to, out, stack, task.bal); break;
        case T_PART: this.emitPart(task, out, stack); break;
        case T_CLOSE: {
          // An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
          if (out.length === task.mark + 1) {
            fail('E_SYNTAX', 'empty interpolation {}', this.posAt(task.part.from));
          }
          // ... and one whose parentheses do not close inside the braces.
          if (task.bal.length > 0) {
            fail('E_SYNTAX', `unclosed ${task.bal[task.bal.length - 1].op} in interpolation`,
              this.posAt(task.part.to));
          }
          out.push({ type: 'op', value: ')', ...this.posAt(task.part.to) });
          break;
        }
        case T_END: out.push({ type: 'op', value: ')', ...task.pos }); break;
      }
    }
  }

  // The flat part of lexRange. A quoted literal with parts ends the run: the
  // tasks it pushes come first, and the rest of the range resumes after them.
  //
  // `bal` is the stack of parentheses and brackets open so far in an interpolation
  // body (null at the top level, where the parser does the balancing). A body is
  // spliced into the surrounding tokens as `( body )`, so a body that closes what
  // it never opened, or leaves something open, would change the meaning of the
  // text around it; each body has to balance inside its own braces.
  lexTokens(from, to, out, stack, bal) {
    let i = from;
    while (i < to) {
      const c = this.chars[i];

      if (isSpace(c)) { i++; continue; }

      if (c === '#') {
        while (i < to && this.chars[i] !== '\n') i++;
        continue;
      }

      const pos = this.posAt(i);

      if (isDigit(c)) {
        let j = i;
        while (j < to && isDigit(this.chars[j])) j++;
        // Only consume the dot when a digit follows, so `1.` is not a number.
        if (j + 1 < to && this.chars[j] === '.' && isDigit(this.chars[j + 1])) {
          j++;
          while (j < to && isDigit(this.chars[j])) j++;
        }
        out.push({ type: 'num', value: this.slice(i, j), ...pos });
        i = j;
        continue;
      }

      if (isAlpha(c)) {
        let j = i;
        while (j < to && isIdent(this.chars[j])) j++;
        out.push({ type: 'ident', value: this.slice(i, j).toUpperCase(), ...pos });
        i = j;
        continue;
      }

      if (c === '"') {
        const { parts, next } = this.scanQuoted(i, to);
        if (parts.length === 1) {
          out.push({ type: 'text', value: parts[0].value, ...pos });
          i = next;
          continue;
        }
        // `( "seg" & expr & "seg" )`: the opener now, the rest as tasks, the
        // remainder of this range underneath them.
        out.push({ type: 'op', value: '(', ...pos });
        stack.push({ k: T_RANGE, i: next, to, bal });
        stack.push({ k: T_END, pos });
        for (let k = parts.length - 1; k >= 0; k--) {
          stack.push({ k: T_PART, part: parts[k], index: k, pos });
        }
        return;
      }
      if (c === "'") { i = this.lexRaw(i, to, out); continue; }

      const op = this.matchOperator(i, to);
      if (op) {
        if (bal !== null) {
          if (op === '(' || op === '[') {
            bal.push({ op });
          } else if (op === ')' || op === ']') {
            const open = bal.pop();
            if (open === undefined || (open.op === '(') !== (op === ')')) {
              fail('E_SYNTAX', `unbalanced ${op} in interpolation`, pos);
            }
          }
        }
        out.push({ type: 'op', value: op, ...pos });
        i += op.length;
        continue;
      }

      fail('E_SYNTAX', `unexpected character ${JSON.stringify(c)}`, pos);
    }
  }

  // One part of an interpolated literal: the `&` before it, then either its text
  // or `( interior )`, the interior being a range of its own.
  emitPart(task, out, stack) {
    const { part, index, pos } = task;
    if (index > 0) out.push({ type: 'op', value: '&', ...pos });
    if (part.kind === 'text') {
      out.push({ type: 'text', value: part.value, ...pos });
      return;
    }
    const mark = out.length;
    const bal = [];
    out.push({ type: 'op', value: '(', ...this.posAt(part.from) });
    stack.push({ k: T_CLOSE, mark, part, bal });
    stack.push({ k: T_RANGE, i: part.from, to: part.to, bal });
  }

  matchOperator(i, to) {
    for (const op of OPERATORS) {
      if (i + op.length > to) continue;
      let ok = true;
      for (let k = 0; k < op.length; k++) {
        if (this.chars[i + k] !== op[k]) { ok = false; break; }
      }
      if (ok) return op;
    }
    return null;
  }

  // --- text literals --------------------------------------------------------

  // Raw 'literals' take no escapes and no interpolation; '' is one quote. This
  // is the form to use for regex patterns.
  lexRaw(start, to, out) {
    const pos = this.posAt(start);
    let i = start + 1;
    let buf = '';
    while (i < to) {
      const c = this.chars[i];
      if (c === "'") {
        if (i + 1 < to && this.chars[i + 1] === "'") { buf += "'"; i += 2; continue; }
        out.push({ type: 'text', value: buf, ...pos });
        return i + 1;
      }
      buf += c;
      i++;
    }
    fail('E_UNTERMINATED', 'unterminated raw text literal', pos);
  }

  // Reads a quoted literal into its parts and the index just past its closing
  // quote, emitting nothing. Every `{...}` is skipped by matchBrace, so the
  // interior is not read here, only located.
  scanQuoted(start, to) {
    const pos = this.posAt(start);
    const parts = [];
    let buf = '';
    let i = start + 1;

    while (i < to) {
      const c = this.chars[i];

      if (c === '"') {
        parts.push({ kind: 'text', value: buf });
        return { parts, next: i + 1 };
      }

      if (c === '\\') {
        const [text, next] = this.readEscape(i, to);
        buf += text;
        i = next;
        continue;
      }

      if (c === '{') {
        const close = this.matchBrace(i, to) - 1;   // index of the matching '}'
        parts.push({ kind: 'text', value: buf });
        buf = '';
        parts.push({ kind: 'expr', from: i + 1, to: close });
        i = close + 1;
        continue;
      }

      buf += c;
      i++;
    }
    fail('E_UNTERMINATED', 'unterminated text literal', pos);
  }

  readEscape(i, to) {
    const pos = this.posAt(i);
    if (i + 1 >= to) fail('E_UNTERMINATED', 'text literal ends in a backslash', pos);
    const e = this.chars[i + 1];

    if (SIMPLE_ESCAPES[e] !== undefined) return [SIMPLE_ESCAPES[e], i + 2];

    if (e === 'u') {
      if (i + 2 >= to || this.chars[i + 2] !== '{') {
        fail('E_ESCAPE', '\\u must be followed by {', pos);
      }
      let j = i + 3;
      let hex = '';
      while (j < to && this.chars[j] !== '}') { hex += this.chars[j]; j++; }
      if (j >= to) fail('E_UNTERMINATED', 'unterminated \\u{...} escape', pos);
      if (hex.length === 0 || hex.length > 6 || !/^[0-9a-fA-F]+$/.test(hex)) {
        fail('E_ESCAPE', `bad \\u{${hex}} escape`, pos);
      }
      const cp = parseInt(hex, 16);
      if (cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
        fail('E_RANGE', `code point U+${hex.toUpperCase()} is not encodable`, pos);
      }
      return [fromCodePoints([cp]), j + 1];
    }

    fail('E_ESCAPE', `unknown escape \\${e}`, pos);
  }

  // Returns the index just past the matching '}'. Nested literals are skipped so
  // that a brace inside a string inside an interpolation does not close it.
  //
  // One pass with an explicit stack of what is open (a brace, a string), not a
  // recursion through the strings, and every brace it closes is remembered in
  // braceEnds. The second half is what keeps the lexer linear: a literal nested
  // d deep is located by its parent and again by each of its own ancestors'
  // interiors being lexed, and without the memo each of those locate-passes
  // re-read everything below it. If anything is unterminated the innermost open
  // construct is the one reported, which is where the recursion used to fail.
  matchBrace(i, to) {
    if (this.braceEnds[i] !== 0) return this.braceEnds[i];
    const open = [{ str: false, at: i, depth: 0 }];
    let j = i;
    for (;;) {
      const top = open[open.length - 1];
      if (j >= to) {
        fail('E_UNTERMINATED',
          top.str ? 'unterminated text literal' : 'unterminated { in text literal',
          this.posAt(top.at));
      }
      const c = this.chars[j];
      if (top.str) {
        if (c === '\\') { j += 2; continue; }
        if (c === '"') { open.pop(); j++; continue; }
        if (c === '{') { open.push({ str: false, at: j, depth: 0 }); continue; }
        j++;
        continue;
      }
      if (c === '"') { open.push({ str: true, at: j }); j++; continue; }
      if (c === "'") { j = this.skipRaw(j, to); continue; }
      if (c === '{') { top.depth++; j++; continue; }
      if (c === '}') {
        top.depth--; j++;
        if (top.depth === 0) {
          this.braceEnds[top.at] = j;
          open.pop();
          if (open.length === 0) return j;
        }
        continue;
      }
      if (c === '#') { while (j < to && this.chars[j] !== '\n') j++; continue; }
      j++;
    }
  }

  skipRaw(j, to) {
    const pos = this.posAt(j);
    j++;
    while (j < to) {
      if (this.chars[j] === "'") {
        if (j + 1 < to && this.chars[j + 1] === "'") { j += 2; continue; }
        return j + 1;
      }
      j++;
    }
    fail('E_UNTERMINATED', 'unterminated raw text literal', pos);
  }
}

// strtoupper's rule, not toUpperCase's: only a-z move, and every non-ASCII byte
// is left alone. python/sel/lexer.py keeps ascii_upper here for the same reason,
// and the SQL layer needs it for case-insensitive names an application supplies.
//
// This host's own identifiers are ASCII by construction (isAlpha above), so
// tokenize's toUpperCase is equivalent there and is left as it is; the callers
// that need the rule for arbitrary text are the ones that call this.
export function asciiUpper(s) {
  let out = '';
  for (const ch of s) {
    const c = ch.codePointAt(0);
    out += (c >= 0x61 && c <= 0x7a) ? String.fromCharCode(c - 32) : ch;
  }
  return out;
}

export function tokenize(source) {
  return new Lexer(source).tokenize();
}
