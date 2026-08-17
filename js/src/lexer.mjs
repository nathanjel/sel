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
import { toCodePoints, fromCodePoints } from './utf8.mjs';

export const OPERATORS = [
  '$==', '$!=', '$<=', '$>=',
  '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
  '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
];

export const RESERVED = new Set([
  'TRUE', 'FALSE', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
]);

const SIMPLE_ESCAPES = {
  '\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', '{': '{', '}': '}',
};

const isDigit = (c) => c >= '0' && c <= '9';
const isAlpha = (c) => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c === '_';
const isIdent = (c) => isAlpha(c) || isDigit(c);
const isSpace = (c) => c === ' ' || c === '\t' || c === '\r' || c === '\n';

class Lexer {
  constructor(source) {
    // Splitting on code points also validates the source: a lone surrogate here
    // is E_UTF8 rather than a silently mangled token.
    this.chars = toCodePoints(source, null).map((c) => fromCodePoints([c]));
    this.n = this.chars.length;
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

  lexRange(from, to, out) {
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

      if (c === '"') { i = this.lexQuoted(i, to, out); continue; }
      if (c === "'") { i = this.lexRaw(i, to, out); continue; }

      const op = this.matchOperator(i, to);
      if (op) {
        out.push({ type: 'op', value: op, ...pos });
        i += op.length;
        continue;
      }

      fail('E_SYNTAX', `unexpected character ${JSON.stringify(c)}`, pos);
    }
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

  lexQuoted(start, to, out) {
    const pos = this.posAt(start);
    const parts = [];
    let buf = '';
    let i = start + 1;

    while (i < to) {
      const c = this.chars[i];

      if (c === '"') {
        parts.push({ kind: 'text', value: buf });
        this.emitParts(parts, pos, out);
        return i + 1;
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
  matchBrace(i, to) {
    const pos = this.posAt(i);
    let depth = 0;
    let j = i;
    while (j < to) {
      const c = this.chars[j];
      if (c === '"') { j = this.skipQuoted(j, to); continue; }
      if (c === "'") { j = this.skipRaw(j, to); continue; }
      if (c === '{') { depth++; j++; continue; }
      if (c === '}') { depth--; j++; if (depth === 0) return j; continue; }
      if (c === '#') { while (j < to && this.chars[j] !== '\n') j++; continue; }
      j++;
    }
    fail('E_UNTERMINATED', 'unterminated { in text literal', pos);
  }

  skipQuoted(j, to) {
    const pos = this.posAt(j);
    j++;
    while (j < to) {
      const c = this.chars[j];
      if (c === '\\') { j += 2; continue; }
      if (c === '"') return j + 1;
      if (c === '{') { j = this.matchBrace(j, to); continue; }
      j++;
    }
    fail('E_UNTERMINATED', 'unterminated text literal', pos);
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

  // A literal with no interpolation is one token. Otherwise it becomes the
  // tokens of `( "seg" & expr & "seg" )` — empty segments included, so the
  // result always goes through `&` and obeys §5.2.
  emitParts(parts, pos, out) {
    if (parts.length === 1) {
      out.push({ type: 'text', value: parts[0].value, ...pos });
      return;
    }
    out.push({ type: 'op', value: '(', ...pos });
    parts.forEach((part, k) => {
      if (k > 0) out.push({ type: 'op', value: '&', ...pos });
      if (part.kind === 'text') {
        out.push({ type: 'text', value: part.value, ...pos });
      } else {
        const mark = out.length;
        out.push({ type: 'op', value: '(', ...this.posAt(part.from) });
        this.lexRange(part.from, part.to, out);
        if (out.length === mark + 1) {
          fail('E_SYNTAX', 'empty interpolation {}', this.posAt(part.from));
        }
        out.push({ type: 'op', value: ')', ...this.posAt(part.to) });
      }
    });
    out.push({ type: 'op', value: ')', ...pos });
  }
}

export function tokenize(source) {
  return new Lexer(source).tokenize();
}
