// The SEL value. One class, used by the interpreter and by host code alike —
// there is deliberately no second representation of state. See spec/SPEC.md §3.

import { fail } from './errors.mjs';
import * as D from './decimal.mjs';
import { encodeUtf8, decodeUtf8, bytesToHex, bytesEqual, toCodePoints } from './utf8.mjs';

export const NONE = 'NONE';
export const TEXT = 'TEXT';
export const BIN = 'BIN';
export const BOOL = 'BOOL';

export class Value {
  constructor(kind, scalar) {
    this.kind = kind;
    this.scalar = scalar;
    this.children = null;   // Map<string, Value>, created on demand
  }

  // The kind constants, mirrored as statics so `Value.BOOL` works the way
  // `Value::BOOL` does in PHP. They are also exported from sel.mjs — without
  // that, a consumer of the published package had no route to them at all and
  // had to hardcode the string 'BOOL'.
  static get NONE() { return NONE; }
  static get TEXT() { return TEXT; }
  static get BIN() { return BIN; }
  static get BOOL() { return BOOL; }

  // Kind predicates. The recommended way to branch on kind in every host,
  // because it is the one spelling that reads the same in all four: the kind
  // *values* are a string here, a class constant in PHP, an enum in C++ and a
  // keyword in Lisp, so only a predicate can be documented uniformly.
  // These test the value's own kind and do not apply scalar context.
  isNone() { return this.kind === NONE; }
  isText() { return this.kind === TEXT; }
  isBin() { return this.kind === BIN; }
  isBool() { return this.kind === BOOL; }

  static none() { return new Value(NONE, null); }
  static text(s) { return new Value(TEXT, s); }
  static bin(b) { return new Value(BIN, b instanceof Uint8Array ? b : Uint8Array.from(b)); }
  static bool(b) { return new Value(BOOL, !!b); }
  // A string is canonicalised and validated: "007" becomes "7", and anything
  // that is not a number is E_NOT_NUM here rather than a TEXT value that fails
  // later somewhere else. Internal callers pass a decimal record, not a string.
  static num(d) {
    if (typeof d !== 'string') return new Value(TEXT, D.format(d));
    const parsed = D.parse(d);
    if (parsed === null) fail('E_NOT_NUM', `not a number: ${JSON.stringify(d)}`, null);
    return new Value(TEXT, D.format(parsed));
  }
  static int(n) { return new Value(TEXT, D.format(D.fromInt(n))); }

  // Builds a list keyed "1".."n". Used by `,` and by list-returning built-ins.
  static list(values) {
    const v = Value.none();
    values.forEach((x, i) => v.set(String(i + 1), x));
    return v;
  }

  // --- children -------------------------------------------------------------

  // A method, not a getter, so it reads the same as $v->size(), v.size() and
  // (sel:value-size v) in the other three hosts. tools/check-api.sh keeps it
  // that way.
  size() { return this.children ? this.children.size : 0; }
  has(key) { return this.children ? this.children.has(key) : false; }
  get(key) { return this.children ? this.children.get(key) : undefined; }
  keys() { return this.children ? Array.from(this.children.keys()) : []; }
  values() { return this.children ? Array.from(this.children.values()) : []; }
  entries() { return this.children ? Array.from(this.children.entries()) : []; }

  // Re-assigning an existing key keeps its original position — Map does this.
  set(key, value) {
    if (!this.children) this.children = new Map();
    this.children.set(key, value);
    return this;
  }

  delete(key) {
    if (this.children) this.children.delete(key);
    return this;
  }

  // --- scalar context (§3.2) ------------------------------------------------

  // The value that supplies the scalar: itself, or its first child, recursively.
  scalarSource(pos) {
    let v = this;
    let guard = 0;
    while (v.kind === NONE) {
      if (!v.children || v.children.size === 0) {
        fail('E_NO_SCALAR', 'value has no scalar and no children', pos);
      }
      v = v.children.values().next().value;
      if (++guard > 1000) fail('E_DEPTH', 'scalar context nested too deeply', pos);
    }
    return v;
  }

  asText(pos) {
    const v = this.scalarSource(pos);
    if (v.kind === TEXT) return v.scalar;
    if (v.kind === BIN) fail('E_NOT_TEXT', 'expected text, got binary (use FROM_UTF8)', pos);
    fail('E_NOT_TEXT', 'expected text, got boolean', pos);
  }

  asBytes(pos) {
    const v = this.scalarSource(pos);
    if (v.kind === BIN) return v.scalar;
    if (v.kind === TEXT) return encodeUtf8(v.scalar, pos);
    fail('E_NOT_BIN', 'expected binary or text, got boolean', pos);
  }

  asBool(pos) {
    const v = this.scalarSource(pos);
    if (v.kind === BOOL) return v.scalar;
    fail('E_NOT_BOOL', 'expected a boolean — SEL has no truthiness', pos);
  }

  asDecimal(pos) {
    const v = this.scalarSource(pos);
    if (v.kind !== TEXT) {
      fail('E_NOT_NUM', `expected a number, got ${v.kind.toLowerCase()}`, pos);
    }
    const d = D.parse(v.scalar);
    if (d === null) fail('E_NOT_NUM', `not a number: ${JSON.stringify(v.scalar)}`, pos);
    return d;
  }

  // Non-throwing probe for ISNUM.
  looksNumeric() {
    if (this.kind === NONE && this.size() === 0) return false;
    let v;
    try { v = this.scalarSource(null); } catch { return false; }
    return v.kind === TEXT && D.parse(v.scalar) !== null;
  }

  // --- copying --------------------------------------------------------------

  // Assignment copies by value: two variables never share structure (§5.7).
  clone() {
    const out = new Value(this.kind, this.kind === BIN ? this.scalar.slice() : this.scalar);
    if (this.children) {
      out.children = new Map();
      for (const [k, v] of this.children) out.children.set(k, v.clone());
    }
    return out;
  }

  // --- structural equality (§5.4) -------------------------------------------

  eql(other) {
    if (this.kind !== other.kind) return false;
    if (this.kind === TEXT || this.kind === BOOL) {
      if (this.scalar !== other.scalar) return false;
    } else if (this.kind === BIN) {
      if (!bytesEqual(this.scalar, other.scalar)) return false;
    }
    if (this.size() !== other.size()) return false;
    if (this.size() === 0) return true;
    const a = this.entries(), b = other.entries();
    for (let i = 0; i < a.length; i++) {
      if (a[i][0] !== b[i][0]) return false;      // key order is normative
      if (!a[i][1].eql(b[i][1])) return false;
    }
    return true;
  }

  // --- canonical dump (conformance/README.md) -------------------------------

  dump() {
    let s;
    switch (this.kind) {
      case NONE: s = '-'; break;
      case TEXT: s = 't' + quoteDump(this.scalar); break;
      case BIN: s = 'b' + bytesToHex(this.scalar); break;
      case BOOL: s = this.scalar ? 'TRUE' : 'FALSE'; break;
    }
    if (this.size() === 0) return s;
    const parts = this.entries().map(([k, v]) => `${quoteDump(k)}=${v.dump()}`);
    return s + '{' + parts.join(', ') + '}';
  }

  // --- host convenience -----------------------------------------------------

  static fromNative(x) {
    if (x === null || x === undefined) return Value.none();
    if (typeof x === 'boolean') return Value.bool(x);
    if (typeof x === 'number') {
      if (!Number.isFinite(x)) throw new TypeError('cannot convert non-finite number to SEL');
      return Value.text(nativeNumberToDecimal(x));
    }
    if (typeof x === 'bigint') return Value.text(x.toString());
    if (typeof x === 'string') return Value.text(x);
    if (x instanceof Uint8Array) return Value.bin(x);
    if (Array.isArray(x)) return Value.list(x.map(Value.fromNative));
    if (x instanceof Value) return x;
    if (typeof x === 'object') {
      const v = Value.none();
      for (const k of Object.keys(x)) v.set(String(k), Value.fromNative(x[k]));
      return v;
    }
    throw new TypeError(`cannot convert ${typeof x} to SEL`);
  }

  toNative() {
    const scalar =
      this.kind === TEXT ? this.scalar :
      this.kind === BIN ? this.scalar :
      this.kind === BOOL ? this.scalar : null;
    if (this.size() === 0) return scalar;
    const obj = {};
    for (const [k, v] of this.children) obj[k] = v.toNative();
    return scalar === null ? obj : { _: scalar, ...obj };
  }
}

// JS numbers are doubles and SEL has none, so the host boundary is where the
// conversion has to be pinned down. Integers pass through exactly; anything with
// a fraction goes via its shortest round-trip form, which is what the author
// literally wrote in source.
function nativeNumberToDecimal(x) {
  const s = String(x);
  if (/^-?\d+$/.test(s)) return s;
  if (/^-?\d+\.\d+$/.test(s)) return s;
  throw new TypeError(`number ${s} has no exact decimal form; pass a string instead`);
}

const DUMP_ESCAPES = { '\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r' };

function quoteDump(s) {
  let out = '"';
  for (const ch of s) {
    if (DUMP_ESCAPES[ch]) out += DUMP_ESCAPES[ch];
    else if (ch.codePointAt(0) < 0x20) out += '\\u' + ch.codePointAt(0).toString(16).padStart(4, '0');
    else out += ch;
  }
  return out + '"';
}

export { quoteDump, toCodePoints, decodeUtf8 };
