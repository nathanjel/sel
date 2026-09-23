// The SEL value. One class, used by the interpreter and by host code alike —
// there is deliberately no second representation of state. See spec/SPEC.md §3.

import { fail, MAX_DEPTH } from './errors.mjs';
import * as D from './decimal.mjs';
import { encodeUtf8, decodeUtf8, bytesToHex, bytesEqual, toCodePoints } from './utf8.mjs';

export const NONE = 'NONE';
export const TEXT = 'TEXT';
export const BIN = 'BIN';
export const BOOL = 'BOOL';

// Records and lists are hot values in the in-memory relational lane.  Keep the
// schema separate from the row so rows with the same keys share one Map and
// field reads become a single slot lookup.  The ordinary Map representation is
// retained for irregular/mutated values, because insertion order is part of
// SEL's value semantics.
export class RecordShape {
  constructor(keys) {
    this.keys = Object.freeze([...keys]);
    this.keyMap = new Map(this.keys.map((key, i) => [key, i]));
    this.size = this.keys.length;
  }

  // Declared in sel.d.ts and kept for readers of it; alias plans moved to a
  // bounded module-level cache (builtins/structure.mjs) and nothing writes
  // here. Built on first read so a shape carries no per-instance Map.
  /** @deprecated always empty; removed in the next minor release */
  get aliasCache() {
    let cache = LEGACY_ALIAS_CACHES.get(this);
    if (!cache) LEGACY_ALIAS_CACHES.set(this, cache = new Map());
    return cache;
  }
}

const LEGACY_ALIAS_CACHES = new WeakMap();

const SHAPES = new Map();
const SHAPE_CACHE_ENTRIES = 256;
const SHAPE_CACHE_MAX_KEYS = 256;
const SHAPE_CACHE_MAX_CHARS = 16384;

// The one shape object for these keys (interned while the cache holds it), so
// rows built by different calls share their shape and shape-keyed caches hit.
export function internRecordShape(keys) { return recordShape(keys); }

function recordShape(keys) {
  // Keys are arbitrary SEL text.  A delimiter-joined signature would alias
  // distinct schemas when a key itself contains that delimiter.
  const signature = JSON.stringify(keys);
  let shape = SHAPES.get(signature);
  if (!shape) {
    shape = new RecordShape(keys);
    if (keys.length <= SHAPE_CACHE_MAX_KEYS &&
        keys.reduce((n, key) => n + key.length, 0) <= SHAPE_CACHE_MAX_CHARS) {
      if (SHAPES.size >= SHAPE_CACHE_ENTRIES) SHAPES.clear();
      SHAPES.set(signature, shape);
    }
  }
  return shape;
}

const LIST_KEY = /^[1-9][0-9]{0,8}$/;

function listIndex(key, length) {
  if (typeof key !== 'string' || !LIST_KEY.test(key)) return -1;
  const n = Number(key);
  return n <= length ? n - 1 : -1;
}

// The shared half of fromEntries and fromEntriesPreserveDuplicates: split the
// pairs into key and value arrays and, when every key is distinct, the packed
// record they shape. Null means a duplicate key, and the two callers then
// diverge on purpose -- ordinary records overwrite, joined rows keep the
// ordered duplicates -- so that fallback is theirs, not this helper's. Measured
// against the inlined loop the call boundary costs nothing (WL-001 SEL-0016).
function shapedFromUniqueEntries(entries) {
  const keys = new Array(entries.length);
  const values = new Array(entries.length);
  const seen = new Set();
  for (let i = 0; i < entries.length; i++) {
    const [key, value] = entries[i];
    keys[i] = key;
    values[i] = value;
    if (seen.has(key)) return null;
    seen.add(key);
  }
  return Value.shapedOwned(keys, values);
}

export class Value {
  constructor(kind, scalar, isList = false) {
    this.kind = kind;
    this._scalar = scalar;
    this.children = null;   // Map<string, Value>, created on demand
    this._entries = null;   // ordered duplicate-preserving fallback (join only)
    this.isList = isList;
    this.shape = null;      // shared RecordShape for flat records
    this.storage = null;    // flat array for records and lists
    this._decimal = null;   // parsed decimal cache for numeric TEXT values
  }

  get scalar() {
    if (this._scalar === null && this._decimal !== null) {
      this._scalar = D.format(this._decimal);
    }
    return this._scalar;
  }

  set scalar(s) {
    this._scalar = s;
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
  isNone() { this; return this.kind === NONE; }
  isNull() { return this.kind === NONE && this.size() === 0 && !this.isList; }
  isVacuous() {
    if (this.isNull()) return true;
    if (this.kind === NONE && this.size() === 0) return true;
    if (this.kind === TEXT && this.size() === 0) {
      return /^[ \t\r\n]*$/.test(this.scalar);
    }
    return false;
  }
  isText() { this; return this.kind === TEXT; }
  isBin() { this; return this.kind === BIN; }
  isBool() { this; return this.kind === BOOL; }

  static none() { return new Value(NONE, null); }
  static null() { return new Value(NONE, null, false); }
  static text(s) { return new Value(TEXT, s); }
  static bin(b) { return new Value(BIN, b instanceof Uint8Array ? b : Uint8Array.from(b)); }
  static bool(b) { return new Value(BOOL, !!b); }

  static shaped(keys, values) {
    if (keys.length === 0) return Value.none();
    return Value.shapedFromShape(recordShape(keys), values.slice());
  }

  // Internal constructors take ownership of freshly allocated packed arrays.
  // Keeping the public constructors copying preserves their host-facing
  // isolation, while the evaluator and relational built-ins avoid a second
  // array allocation when the destination is already private.
  static shapedOwned(keys, values) {
    return Value.shapedFromShape(recordShape(keys), values);
  }

  static shapedFromShape(shape, values) {
    const v = new Value(NONE, null);
    v.shape = shape;
    v.storage = values;
    return v;
  }

  static fromEntries(entries, isList = false) {
    if (isList) return Value.listOwned(entries.map(([, value]) => value));
    if (entries.length > 0) {
      const shaped = shapedFromUniqueEntries(entries);
      if (shaped) return shaped;
    }
    const v = Value.none();
    for (const [key, value] of entries) v.set(key, value);
    return v;
  }

  // LINK can deliberately expose duplicate aliases when a caller's binder
  // names collide with fields carried by an earlier link.  Ordinary RECORD
  // construction still has last-write-wins semantics through fromEntries;
  // this narrow internal form preserves the Lisp join builder's ordered
  // alist only where the relational operator needs it.
  static fromEntriesPreserveDuplicates(entries) {
    if (entries.length === 0) return Value.none();
    const shaped = shapedFromUniqueEntries(entries);
    if (shaped) return shaped;
    const v = Value.none();
    v._entries = entries;
    return v;
  }

  // A string is canonicalised and validated: "007" becomes "7", and anything
  // that is not a number is E_NOT_NUM here rather than a TEXT value that fails
  // later somewhere else. Internal callers pass a decimal record, not a string.
  static num(d) {
    let parsed = d;
    if (typeof d === 'string') {
      parsed = D.parse(d);
      if (parsed === null) fail('E_NOT_NUM', `not a number: ${JSON.stringify(d)}`, null);
    }
    const v = new Value(TEXT, null);
    v._decimal = parsed;
    return v;
  }
  static int(n) {
    const d = D.fromInt(n);
    const v = new Value(TEXT, null);
    v._decimal = d;
    return v;
  }

  // Builds a list keyed "1".."n". Used by `,` and by list-returning built-ins.
  static list(values) {
    return Value.listOwned(values.slice());
  }

  static listOwned(values) {
    const v = new Value(NONE, null, true);
    v.storage = values;
    return v;
  }

  // --- children -------------------------------------------------------------

  // A method, not a getter, so it reads the same as $v->size(), v.size() and
  // (sel:value-size v) in the other three hosts. tools/check-api.sh keeps it
  // that way.
  size() {
    if (this.storage !== null) return this.storage.length;
    if (this._entries !== null) return this._entries.length;
    return this.children ? this.children.size : 0;
  }

  has(key) {
    if (this.shape) return this.shape.keyMap.has(key);
    if (this.isList && this.storage !== null) return listIndex(key, this.storage.length) >= 0;
    if (this._entries !== null) return this._entries.some(([entryKey]) => entryKey === key);
    return this.children ? this.children.has(key) : false;
  }

  get(key) {
    if (this.shape) {
      const i = this.shape.keyMap.get(key);
      return i === undefined ? undefined : this.storage[i];
    }
    if (this.isList && this.storage !== null) {
      const i = listIndex(key, this.storage.length);
      return i < 0 ? undefined : this.storage[i];
    }
    if (this._entries !== null) {
      for (const [entryKey, value] of this._entries) {
        if (entryKey === key) return value;
      }
      return undefined;
    }
    const value = this.children ? this.children.get(key) : undefined;
    return value === undefined ? undefined : value;
  }

  keys() {
    // RecordShape.keys is frozen, so returning it is safe and avoids a fresh
    // key array on every row inspected by a relational operator.
    if (this.shape) return this.shape.keys;
    if (this.isList && this.storage !== null) {
      return this.storage.map((_, i) => String(i + 1));
    }
    if (this._entries !== null) return this._entries.map(([key]) => key);
    return this.children ? Array.from(this.children.keys()) : [];
  }

  values() {
    if (this.storage !== null) return this.storage.map((value) => value);
    if (this._entries !== null) return this._entries.map(([, value]) => value);
    return this.children ? Array.from(this.children.values(), (value) => value) : [];
  }

  entries() {
    if (this.shape) {
      return this.shape.keys.map((key, i) => [key, this.storage[i]]);
    }
    if (this.isList && this.storage !== null) {
      return this.storage.map((value, i) => [String(i + 1), value]);
    }
    if (this._entries !== null) {
      return this._entries.map(([key, value]) => [key, value]);
    }
    return this.children
      ? Array.from(this.children.entries(), ([key, value]) => [key, value])
      : [];
  }

  // Re-assigning an existing key keeps its original position — Map does this.
  set(key, value) {
    if (this.shape) {
      const index = this.shape.keyMap.get(key);
      if (index !== undefined) {
        this.storage[index] = value;
        return this;
      }
      // A new record field cannot fit the existing shape. Materialise it once
      // and continue with the ordered fallback.
      const entries = this.entries();
      this.shape = null;
      this.storage = null;
      this.children = new Map(entries);
    } else if (this.isList && this.storage !== null) {
      const index = listIndex(key, this.storage.length);
      if (index >= 0) {
        this.storage[index] = value;
        return this;
      }
      // Keep the list marker when an assignment adds a non-positional key;
      // the Lisp value does the same after materialising its backing vector.
      const entries = this.entries();
      this.storage = null;
      this.children = new Map(entries);
    } else if (this._entries !== null) {
      for (const entry of this._entries) {
        if (entry[0] === key) {
          entry[1] = value;
          return this;
        }
      }
      this._entries.push([key, value]);
      return this;
    }
    if (!this.children) this.children = new Map();
    this.children.set(key, value);
    return this;
  }

  // --- scalar context (§3.2) ------------------------------------------------

  scalarSource(pos) {
    if (this.kind !== NONE) return this;
    let v = this;
    let guard = 0;
    while (v.kind === NONE) {
      if (v.isNull()) {
        fail('E_NULL', 'value is NULL', pos);
      }
      if (v.size() === 0) {
        fail('E_NO_SCALAR', 'value has no scalar and no children', pos);
      }
      v = v.children
        ? v.children.values().next().value
        : v._entries !== null
          ? v._entries[0][1]
        : v.storage[0];
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
    if (v._decimal !== null) return v._decimal;
    const d = D.parse(v.scalar, pos);
    if (d === null) fail('E_NOT_NUM', `not a number: ${JSON.stringify(v.scalar)}`, pos);
    v._decimal = d;
    return d;
  }

  // Non-throwing probe for ISNUM.
  looksNumeric() {
    if (this.kind === NONE && this.size() === 0) return false;
    // A well-formed numeral too big to hold raises E_RANGE out of parse. The
    // probe answers no rather than raising, so ISNUM is true exactly when the
    // value can be used as a number — before the cap it said true for a
    // 2 000 000-digit text that then failed on first use.
    try {
      const v = this.scalarSource(null);
      if (v.kind !== TEXT) return false;
      if (v._decimal !== null) return true;
      const d = D.parse(v.scalar);
      if (d !== null) v._decimal = d;
      return d !== null;
    } catch { return false; }
  }

  // --- copying --------------------------------------------------------------

  // Assignment copies by value: two variables never share structure (§5.7).
  //
  // A value's nesting is the third thing spec/SPEC.md §6.4 caps, after the parser's
  // and the evaluator's, and it was the last one left uncounted. clone, eql, dump
  // and the two native conversions each recurse once per level, so a value nested
  // deeply enough reached the host's own stack: an uncaught RangeError here at about
  // four thousand levels, RecursionError on Python at about one thousand, a segfault
  // on C++ at about sixty thousand. Three hosts answered where two died, on the same
  // program.
  //
  // The depth rides as a parameter, as it does in dependencies(): nothing has to be
  // released on the way out, so no guard object is needed and all five hosts spell it
  // the same way. A value of exactly MAX_DEPTH levels is fine; the level past it is
  // refused. `pos` is reported when the caller has one — the evaluator knows which
  // node asked — and is null for a call from host code, the same convention as
  // asText().
  clone(pos = null) { return this.cloneAt(1, pos); }

  cloneAt(depth, pos) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', pos);
    if (this.shape) {
      return Value.shapedFromShape(this.shape,
        this.storage.map((value) => value.cloneAt(depth + 1, pos)));
    }
    if (this.isList && this.storage !== null) {
      return Value.listOwned(this.storage.map((value) => value.cloneAt(depth + 1, pos)));
    }
    if (!this.children && this._entries === null) {
      const out = new Value(this.kind, this.kind === BIN ? (this._scalar ? this._scalar.slice() : null) : this._scalar, this.isList);
      out._decimal = this._decimal;
      return out;
    }
    const out = new Value(this.kind, this.kind === BIN ? (this._scalar ? this._scalar.slice() : null) : this._scalar, this.isList);
    out._decimal = this._decimal;
    if (this._entries !== null) {
      out._entries = this._entries.map(([key, value]) => [key, value.cloneAt(depth + 1, pos)]);
      return out;
    }
    if (this.children) {
      out.children = new Map();
      for (const [k, v] of this.children) out.children.set(k, v.cloneAt(depth + 1, pos));
    }
    return out;
  }

  // --- structural equality (§5.4) -------------------------------------------

  eql(other, pos = null) { return this.eqlAt(other, 1, pos); }

  eqlAt(other, depth, pos) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', pos);
    if (this.kind !== other.kind) return false;
    if (this.kind === TEXT) {
      if (this._scalar === null && other._scalar === null &&
          this._decimal !== null && other._decimal !== null) {
        if (this._decimal.neg !== other._decimal.neg ||
            this._decimal.scale !== other._decimal.scale ||
            this._decimal.digits !== other._decimal.digits) {
          return false;
        }
      } else {
        if (this.scalar !== other.scalar) return false;
      }
    } else if (this.kind === BOOL) {
      if (this.scalar !== other.scalar) return false;
    } else if (this.kind === BIN) {
      if (!bytesEqual(this.scalar, other.scalar)) return false;
    }
    if (this.size() !== other.size()) return false;
    if (this.size() === 0) return true;
    if (this.shape && other.shape && this.shape === other.shape) {
      for (let i = 0; i < this.storage.length; i++) {
        if (!this.storage[i].eqlAt(other.storage[i], depth + 1, pos)) return false;
      }
      return true;
    }
    if (this.isList && other.isList && this.storage !== null && other.storage !== null) {
      for (let i = 0; i < this.storage.length; i++) {
        if (!this.storage[i].eqlAt(other.storage[i], depth + 1, pos)) return false;
      }
      return true;
    }
    const a = this.entries(), b = other.entries();
    for (let i = 0; i < a.length; i++) {
      if (a[i][0] !== b[i][0]) return false;      // key order is normative
      if (!a[i][1].eqlAt(b[i][1], depth + 1, pos)) return false;
    }
    return true;
  }

  // --- canonical dump (conformance/README.md) -------------------------------

  dump() { return this.dumpAt(1); }

  dumpAt(depth) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', null);
    let s;
    switch (this.kind) {
      case NONE: s = '-'; break;
      case TEXT: s = 't' + quoteDump(this.scalar); break;
      case BIN: s = 'b' + bytesToHex(this.scalar); break;
      case BOOL: s = this.scalar ? 'TRUE' : 'FALSE'; break;
    }
    if (this.size() === 0) return s;
    const parts = this.entries().map(([k, v]) => `${quoteDump(k)}=${v.dumpAt(depth + 1)}`);
    return s + '{' + parts.join(', ') + '}';
  }

  // --- host convenience -----------------------------------------------------

  static fromNative(x) { return Value.fromNativeAt(x, 1); }

  static fromNativeAt(x, depth) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', null);
    if (x === null || x === undefined) return Value.null();
    if (typeof x === 'boolean') return Value.bool(x);
    if (typeof x === 'number') {
      if (!Number.isFinite(x)) throw new TypeError('cannot convert non-finite number to SEL');
      return Value.text(nativeNumberToDecimal(x));
    }
    if (typeof x === 'bigint') return Value.text(x.toString());
    if (typeof x === 'string') return Value.text(x);
    if (x instanceof Uint8Array) return Value.bin(x);
    if (Array.isArray(x)) return Value.listOwned(x.map((e) => Value.fromNativeAt(e, depth + 1)));
    if (x instanceof Value) return x;
    if (typeof x === 'object') {
      const entries = Object.keys(x).map((key) => [String(key), Value.fromNativeAt(x[key], depth + 1)]);
      return Value.fromEntries(entries);
    }
    throw new TypeError(`cannot convert ${typeof x} to SEL`);
  }

  toNative() { return this.toNativeAt(1); }

  toNativeAt(depth) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', null);
    const scalar =
      this.kind === TEXT ? this.scalar :
      this.kind === BIN ? this.scalar :
      this.kind === BOOL ? this.scalar : null;
    if (this.size() === 0) return scalar;
    const obj = {};
    for (const [k, v] of this.entries()) obj[k] = v.toNativeAt(depth + 1);
    return scalar === null ? obj : { _: scalar, ...obj };
  }
}

// Structural hashing is only a prefilter: callers must still use eql() inside
// the bucket because collisions are allowed.  It deliberately walks the flat
// storage directly so DEDUPE does not serialize every row just to find a bucket.
export function structuralHash(value, depth = 1) {
  if (depth > MAX_DEPTH) return 0;
  let h = value.kind === TEXT ? 17 : value.kind === BIN ? 31 : value.kind === BOOL ? 47 : 61;
  if (value.kind === TEXT) h = mixHash(h, stringHash(value.scalar));
  else if (value.kind === BOOL) h = mixHash(h, value.scalar ? 12345 : 67890);
  else if (value.kind === BIN) {
    h = mixHash(h, value.scalar.length);
    for (const byte of value.scalar) h = mixHash(h, byte);
  }
  if (value.shape) {
    for (let i = 0; i < value.shape.keys.length; i++) {
      h = mixHash(h, stringHash(value.shape.keys[i]));
      h = mixHash(h, structuralHash(value.storage[i], depth + 1));
    }
  } else if (value.isList && value.storage !== null) {
    for (let i = 0; i < value.storage.length; i++) {
      h = mixHash(h, stringHash(String(i + 1)));
      h = mixHash(h, structuralHash(value.storage[i], depth + 1));
    }
  } else {
    for (const [key, child] of value.entries()) {
      h = mixHash(h, stringHash(key));
      h = mixHash(h, structuralHash(child, depth + 1));
    }
  }
  return h >>> 0;
}

function stringHash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mixHash(a, b) {
  return (Math.imul((a ^ b) >>> 0, 16777619) + 0x9e3779b9) >>> 0;
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
