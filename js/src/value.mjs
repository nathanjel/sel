// The SEL value. One class, used by the interpreter and by host code alike —
// there is deliberately no second representation of state. See spec/SPEC.md §3.

import { fail, MAX_DEPTH } from './errors.mjs';
import * as D from './decimal.mjs';
import { encodeUtf8, bytesToHex, bytesEqual } from './utf8.mjs';

export const NONE = 'NONE';

// An unpaired surrogate is not text (spec §8): JS strings are UTF-16, so the
// check a UTF-8 host makes on bytes is made here on code units.
const ANY_SURROGATE = /[\uD800-\uDFFF]/;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?:^|[^\uD800-\uDBFF])[\uDC00-\uDFFF]/;
function checkText(s) {
  if (typeof s === 'string' && ANY_SURROGATE.test(s) && LONE_SURROGATE.test(s)) {
    fail('E_UTF8', 'text carries an unpaired surrogate', null);
  }
}

// A constructor called with something it does not take (spec §8): E_BAD_ARG,
// a SelError like every other boundary failure, never the host's own
// exception.
function badArg(message) { fail('E_BAD_ARG', message, null); }

function checkKey(key) {
  if (typeof key !== 'string') badArg(`a key must be a string, not ${typeof key}`);
  checkText(key);
}

function checkValue(v) {
  if (!(v instanceof Value)) badArg(`expected a Value, not ${v === null ? 'null' : typeof v}`);
}

// Keys and values side by side, as entries: the counts match, every key is
// text and every value a Value (spec §8).
function pairUp(keys, values) {
  if (!Array.isArray(keys) || !Array.isArray(values)) badArg('keys and values must be arrays');
  if (keys.length !== values.length) {
    badArg(`${keys.length} key(s) and ${values.length} value(s) do not pair up`);
  }
  return keys.map((key, i) => { checkKey(key); checkValue(values[i]); return [key, values[i]]; });
}

// The decimal form Value.num takes besides a string: well formed, within the
// digit caps, and canonical -- a negative zero loses its sign, as "-0" does
// through D.parse (spec §8).
//
// The Value keeps its own copy: it held the caller's object, so changing `digits`
// or `scale` afterwards changed the Value (spec §8, "the boundary copies"). And
// `neg` has to be a boolean -- a missing or truthy-looking one used to be read as
// whatever `!!` made of it, so `{ digits: 5n, scale: 0 }` was -5 in one place and
// 5 in another.
function checkDecimal(d) {
  if (d === null || typeof d !== 'object' || typeof d.neg !== 'boolean'
      || typeof d.digits !== 'bigint' || d.digits < 0n
      || !Number.isSafeInteger(d.scale) || d.scale < 0) {
    badArg('not a decimal: expected { neg: a boolean, digits: a non-negative bigint, scale: a non-negative integer }');
  }
  D.guard(d, null);
  return { neg: d.digits === 0n ? false : d.neg, digits: d.digits, scale: d.scale };
}

let INT_CAP = null;
// Anything below 10^18 is under the digit cap whatever the cap is (it is at least 18), so the
// first BigInt a program ever passes does not have to build 10^1000000 to be waved through.
const SMALL_INT = 10n ** 18n;
function intCap() { return INT_CAP ??= 10n ** BigInt(D.MAX_INT_DIGITS); }

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
}

const SHAPES = new Map();
const SHAPE_CACHE_ENTRIES = 256;
// Bounds on what the cache holds. The number of shapes is bounded, and so is what
// they weigh in all: a schema of a few hundred columns is ordinary (a joined row has
// twice its sides' columns), and refusing to intern it made every row of a join carry
// a shape of its own, with its own plan -- a 36x cliff at 260 fields. A single
// shape too wide to be a schema at all (more than SHAPE_CACHE_MAX_KEYS keys) is still
// not interned.
const SHAPE_CACHE_MAX_KEYS = 4096;
const SHAPE_CACHE_MAX_CHARS = 262144;
const SHAPE_CACHE_TOTAL_CHARS = 4194304;
let shapeCacheChars = 0;

// The shape last asked for by key list: a run of rows with the same keys matches it by
// length and pointer compares, without building the JSON signature.
let lastKeyedShape = null;

// The one shape object for these keys (interned while the cache holds it), so
// rows built by different calls share their shape and shape-keyed caches hit.
export function recordShape(keys) {
  const last = lastKeyedShape;
  if (last !== null && last.keys.length === keys.length) {
    let i = 0;
    while (i < keys.length && last.keys[i] === keys[i]) i++;
    if (i === keys.length) return last;
  }
  const shape = internedShape(keys);
  lastKeyedShape = shape;
  return shape;
}

function internedShape(keys) {
  // Keys are arbitrary SEL text.  A delimiter-joined signature would alias
  // distinct schemas when a key itself contains that delimiter.
  const signature = JSON.stringify(keys);
  let shape = SHAPES.get(signature);
  if (!shape) {
    shape = new RecordShape(keys);
    if (keys.length <= SHAPE_CACHE_MAX_KEYS) {
      const chars = keys.reduce((n, key) => n + key.length, 0);
      if (chars <= SHAPE_CACHE_MAX_CHARS) {
        if (SHAPES.size >= SHAPE_CACHE_ENTRIES || shapeCacheChars + chars > SHAPE_CACHE_TOTAL_CHARS) {
          SHAPES.clear();
          shapeCacheChars = 0;
        }
        SHAPES.set(signature, shape);
        shapeCacheChars += chars;
      }
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

// The shape of the last call, with keys already known to be distinct: rows built one after
// another by the same expression have the same keys in the same order, so comparing the
// keys to this shape (length and one pointer compare each) replaces the duplicate check
// and the JSON signature (2.4 us per row before, 0.6 after).
let lastUniqueShape = null;

// The shared half of fromEntries and fromEntriesPreserveDuplicates: split the
// pairs into key and value arrays and, when every key is distinct, the packed
// record they shape. Null means a duplicate key, and the two callers then
// diverge on purpose -- ordinary records overwrite, joined rows keep the
// ordered duplicates -- so that fallback is theirs, not this helper's. Measured
// against the inlined loop the call boundary costs nothing.
function shapedFromUniqueEntries(entries) {
  const n = entries.length;
  const values = new Array(n);
  const last = lastUniqueShape;
  if (last !== null && last.keys.length === n) {
    let i = 0;
    for (; i < n; i++) {
      const entry = entries[i];
      if (entry[0] !== last.keys[i]) break;
      values[i] = entry[1];
    }
    if (i === n) return Value.shapedFromShape(last, values);
  }
  const keys = new Array(n);
  const seen = new Set();
  for (let i = 0; i < n; i++) {
    const entry = entries[i];
    const key = entry[0];
    keys[i] = key;
    values[i] = entry[1];
    if (seen.has(key)) return null;
    seen.add(key);
  }
  const shape = recordShape(keys);
  lastUniqueShape = shape;
  return Value.shapedFromShape(shape, values);
}

const BYTE_DECIMALS = new Array(256);

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
    // A number's parsed form is a cache of its text: with the text changed, the
    // old digits would keep answering ISNUM, arithmetic and EQL.
    this._decimal = null;
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
  isNull() { return this.kind === NONE && this.size() === 0 && !this.isList; }
  isVacuous() {
    if (this.kind === NONE && this.size() === 0) return true;
    if (this.kind === TEXT && this.size() === 0) {
      return /^[ \t\r\n]*$/.test(this.scalar);
    }
    return false;
  }
  isText() { return this.kind === TEXT; }
  isBin() { return this.kind === BIN; }
  isBool() { return this.kind === BOOL; }

  static none() { return new Value(NONE, null); }
  static null() { return new Value(NONE, null, false); }
  // Host code is the one place bad data can enter (spec §8): every text is
  // checked for unpaired surrogates, and bytes are whole numbers 0..255, copied
  // so the caller's array can change afterwards. binOwned is the builtins' constructor for an array they
  // just made.
  static text(s) {
    if (typeof s !== 'string') badArg(`text must be a string, not ${typeof s}`);
    checkText(s);
    return new Value(TEXT, s);
  }
  // The interpreter's constructor for text it has just made from Values that were
  // already validated (a concatenation, a slice by code points, a join): no check.
  // `Value.text` scans every string for surrogates, and on a long V8 rope that scan
  // flattens the rope, so a loop that appends to one string went quadratic.
  // Never for host input, and never for output of an engine that can split a pair.
  static textOwned(s) { return new Value(TEXT, s); }
  static bin(b) {
    if (!(b instanceof Uint8Array) && !Array.isArray(b)) badArg('bytes must be a Uint8Array or an array of numbers');
    const out = new Uint8Array(b.length);
    if (b instanceof Uint8Array) {
      out.set(b);
    } else {
      for (let i = 0; i < b.length; i++) {
        const x = b[i];
        if (typeof x !== 'number' || !Number.isInteger(x) || x < 0 || x > 255) {
          fail('E_RANGE', `byte ${String(x)} is not a whole number from 0 to 255`, null);
        }
        out[i] = x;
      }
    }
    return new Value(BIN, out);
  }
  static binOwned(b) { return new Value(BIN, b); }
  static bool(b) { return new Value(BOOL, !!b); }

  // Keys and values side by side. A repeated key keeps its first position and
  // takes its last value, as RECORD does (spec §8).
  static shaped(keys, values) {
    return Value.fromEntriesOwned(pairUp(keys, values));
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

  // A list's keys are kept: "1".."n" is a plain list, anything else the list
  // with preserved keys FILTER makes. They must be distinct (spec §8; it used
  // to renumber them).
  static fromEntries(entries, isList = false) {
    if (!Array.isArray(entries)) badArg('entries must be an array of [key, value] pairs');
    const checked = entries.map((entry) => {
      if (!Array.isArray(entry) || entry.length !== 2) badArg('an entry must be a [key, value] pair');
      checkKey(entry[0]);
      checkValue(entry[1]);
      return [entry[0], entry[1]];
    });
    if (!isList) return Value.fromEntriesOwned(checked);
    if (checked.every(([key], i) => key === String(i + 1))) return Value.listOwned(checked.map(([, value]) => value));
    const v = new Value(NONE, null, true);
    v.children = new Map();
    for (const [key, value] of checked) {
      if (v.children.has(key)) badArg(`list key "${key}" is given twice`);
      v.children.set(key, value);
    }
    return v;
  }

  // The builtins' form: entries they built from SEL values, unchecked.
  static fromEntriesOwned(entries) {
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

  // A whole number 0..255 (a byte) without a BigInt: the 256 decimals are built
  // once and shared, the way cloneAt already shares a decimal between copies --
  // nothing writes into a decimal record.
  static byteValue(x) {
    const d = BYTE_DECIMALS[x] || (BYTE_DECIMALS[x] = D.fromInt(x));
    const v = new Value(TEXT, null);
    v._decimal = d;
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
    } else {
      parsed = checkDecimal(d);
    }
    const v = new Value(TEXT, null);
    v._decimal = parsed;
    return v;
  }
  // A number from a decimal the evaluator's own arithmetic just produced:
  // D.add, D.mul and the rest have already run the digit-cap guard, so the checked
  // constructor's second validation and copy are skipped. A host's decimal never
  // comes through here (Value.num checks it). Negative zero is still normalised.
  static numOwned(d) {
    const v = new Value(TEXT, null);
    v._decimal = d.neg && d.digits === 0n ? { neg: false, digits: 0n, scale: d.scale } : d;
    return v;
  }
  static int(n) {
    if (typeof n === 'number' ? !Number.isInteger(n) : typeof n !== 'bigint') {
      badArg(`not a whole number: ${String(n)}`);
    }
    // A native integer obeys the digit cap like the same digits in source
    // (spec §8, §6.4).
    if (typeof n === 'bigint' && (n < 0n ? -n : n) >= SMALL_INT && (n < 0n ? -n : n) >= intCap()) {
      fail('E_RANGE', `number has more than ${D.MAX_INT_DIGITS} integer digits`, null);
    }
    const d = D.fromInt(n);
    const v = new Value(TEXT, null);
    v._decimal = d;
    return v;
  }

  // Builds a list keyed "1".."n". Used by `,` and by list-returning built-ins.
  static list(values) {
    if (!Array.isArray(values)) badArg('a list is built from an array of Values');
    values.forEach(checkValue);
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
    checkText(key);
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

  // What asBytes would encode, without encoding it: the string for TEXT, the bytes for BIN,
  // the same refusal for anything else. For comparisons, which can compare two strings
  // without building either one's UTF-8.
  asTextOrBytes(pos) {
    const v = this.scalarSource(pos);
    if (v.kind === BIN || v.kind === TEXT) return v.scalar;
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

  // The number this value is in scalar context, or null when asDecimal would
  // raise (not a number, over the digit cap, no scalar, BOOL/BIN/NULL). The hot
  // callers -- a join key over a column of text that is mostly not numeric --
  // used to throw and catch a SelError, stack trace included, per row.
  tryDecimal() {
    let v = this;
    if (v.kind === NONE) {
      try { v = this.scalarSource(null); } catch { return null; }
    }
    if (v.kind !== TEXT) return null;
    if (v._decimal !== null) return v._decimal;
    let d;
    try { d = D.parse(v.scalar); } catch { return null; }
    if (d === null) return null;
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

  // A copy of this root for running a program that may assign to the names in
  // `writable`: those entries are deep copies, every other entry is shared with
  // this value. Sound for a program that only ever writes through those names
  // (assignment is the one way a run changes a value), which is what a hybrid
  // continuation gets, without copying an unrelated 200k-row table per run.
  // Anything that is not a plain record root falls back to the full copy.
  shallowRoot(writable) {
    if (this.kind !== NONE || this._entries !== null || (this.isList && this.storage !== null)) return this.clone();
    const out = [];
    for (const [key, value] of this.entries()) out.push([key, writable.has(key) ? value.cloneAt(2, null) : value]);
    return Value.fromEntriesOwned(out);
  }

  // The depth check of cloneAt without the copy, for a value that is already
  // exclusively the caller's (a fresh `,` result): same error, same node, no
  // allocation. Recursion is bounded by MAX_DEPTH because the check fails first.
  checkDepthAt(depth, pos) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', pos);
    const next = depth + 1;
    // A childless value (almost every element of a wide list) is settled by the
    // one comparison below; only a container is entered.
    if (this.shape || (this.isList && this.storage !== null)) {
      const items = this.storage;
      for (let i = 0; i < items.length; i++) {
        const v = items[i];
        if (v.storage === null && v._entries === null && (v.children === null || v.children === undefined || v.children.size === 0)) {
          if (next > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', pos);
        } else v.checkDepthAt(next, pos);
      }
      return;
    }
    if (this._entries !== null) {
      for (const entry of this._entries) entry[1].checkDepthAt(next, pos);
      return;
    }
    if (this.children) for (const v of this.children.values()) v.checkDepthAt(next, pos);
  }

  cloneAt(depth, pos) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', pos);
    if (this.shape) {
      return Value.shapedFromShape(this.shape,
        this.storage.map((value) => value.cloneAt(depth + 1, pos)));
    }
    if (this.isList && this.storage !== null) {
      return Value.listOwned(this.storage.map((value) => value.cloneAt(depth + 1, pos)));
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
    if (typeof x === 'number') return Value.text(nativeNumberToDecimal(x));
    // Through Value.int, which holds the integer digit cap: the text of the
    // bigint skipped it.
    if (typeof x === 'bigint') return Value.int(x);
    if (typeof x === 'string') return Value.text(x);
    if (x instanceof Uint8Array) return Value.bin(x);
    // Array.from, not x.map: map keeps a sparse array's holes, and a list with
    // empty slots in its storage fails on first use with a TypeError. A hole
    // reads as undefined, which is NULL, as an explicit undefined does.
    if (Array.isArray(x)) return Value.listOwned(Array.from(x, (e) => Value.fromNativeAt(e, depth + 1)));
    if (x instanceof Value) return x;
    if (typeof x === 'object') {
      // A plain object is a record. Anything else with own enumerable keys or
      // none -- Date, Map, Set, ArrayBuffer, a typed array, a class instance --
      // has no conversion (spec §8), and reading its keys made it a record that
      // was empty (so NULL) or keyed "0", "1", ....
      const proto = Object.getPrototypeOf(x);
      if (proto !== Object.prototype && proto !== null) {
        badArg(`cannot convert ${Object.prototype.toString.call(x)} to SEL`);
      }
      const entries = Object.keys(x).map((key) => { checkText(key); return [String(key), Value.fromNativeAt(x[key], depth + 1)]; });
      return Value.fromEntriesOwned(entries);
    }
    badArg(`cannot convert ${typeof x} to SEL`);
  }

  toNative() { return this.toNativeAt(1); }

  toNativeAt(depth) {
    if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', null);
    const scalar =
      this.kind === TEXT ? this.scalar :
      this.kind === BIN ? this.scalar :
      this.kind === BOOL ? this.scalar : null;
    if (this.size() === 0) return scalar === null || this.kind !== BIN ? scalar : scalar.slice();
    // Every key an own property, "__proto__" included: `obj[k] = v` would set
    // the prototype instead.
    //
    // A JS object enumerates array-index keys ("0", "2", "10") first and in
    // ascending order whatever order they were added in, so a record whose keys
    // are not already in that order cannot come back from fromNative as it went:
    // such a value has no native form (spec §8, "every v toNative accepts"), and
    // raising is honest where reordering would silently break the inverse. Its
    // entries() keep the order.
    const obj = {};
    let lastIndex = -1;
    let sawOther = false;
    for (const [k, v] of this.entries()) {
      const at = arrayIndex(k);
      if (at >= 0) {
        if (sawOther || at <= lastIndex) {
          fail('E_BAD_ARG', 'a record whose position-like keys are not first and ascending has no native form; JS objects reorder them (use entries())', null);
        }
        lastIndex = at;
      } else {
        sawOther = true;
      }
      const child = v.toNativeAt(depth + 1);
      if (k === '__proto__') Object.defineProperty(obj, k, { value: child, enumerable: true, writable: true, configurable: true });
      else obj[k] = child;
    }
    if (scalar === null) return obj;
    // A value's own scalar travels under "_"; with a child of that name too,
    // one of them would be lost (spec §8).
    if (Object.hasOwn(obj, '_')) fail('E_BAD_ARG', 'a value with both a scalar and a child named "_" has no native form', null);
    return { _: this.kind === BIN ? scalar.slice() : scalar, ...obj };
  }
}

// The hash of the key a packed list's element i has ("1", "2", ...), so a packed list
// hashes like its keyed twin without building the key text per element per call.
const LIST_KEY_HASHES = [];
function listKeyHash(i) {
  if (i >= 65536) return stringHash(String(i + 1));
  let h = LIST_KEY_HASHES[i];
  if (h === undefined) h = LIST_KEY_HASHES[i] = stringHash(String(i + 1));
  return h;
}

// Structural hashing is only a prefilter: callers must still use eql() inside
// the bucket because collisions are allowed.  It deliberately walks the flat
// storage directly so DEDUPE does not serialize every row just to find a bucket.
export function structuralHash(value, depth = 1) {
  // A value nested past the cap cannot be hashed any more than dumped (spec
  // §6.4): answering 0 let DEDUPE pass one it could not compare.
  if (depth > MAX_DEPTH) fail('E_DEPTH', 'value nested too deeply', null);
  let h = value.kind === TEXT ? 17 : value.kind === BIN ? 31 : value.kind === BOOL ? 47 : 61;
  if (value.kind === TEXT) h = mixHash(h, stringHash(value.scalar));
  else if (value.kind === BOOL) h = mixHash(h, value.scalar ? 12345 : 67890);
  else if (value.kind === BIN) {
    h = mixHash(h, value.scalar.length);
    const bytes = value.scalar;
    for (let i = 0; i < bytes.length; i++) h = mixHash(h, bytes[i]);
  }
  if (value.shape) {
    for (let i = 0; i < value.shape.keys.length; i++) {
      h = mixHash(h, stringHash(value.shape.keys[i]));
      h = mixHash(h, structuralHash(value.storage[i], depth + 1));
    }
  } else if (value.isList && value.storage !== null) {
    for (let i = 0; i < value.storage.length; i++) {
      h = mixHash(h, listKeyHash(i));
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

// Per process, so a hash cannot be aimed at in advance: FNV-1a is invertible, and
// a few hundred kilobytes of chosen strings shared one hash and made every
// DEDUPE/BUCKET insert scan the whole bucket. Callers only ever use the
// hash to pick a bucket and still compare with eql, so a per-run value changes
// no answer -- and a value with no children never reaches the hash at all
// (scalarKey below).
const HASH_SEED = (Math.random() * 0x100000000) >>> 0;

// An exact identity key for a value with no children -- what eql compares: the
// kind and the scalar. Null for a value with children, which is hashed.
export function scalarKey(value) {
  if (value.size() !== 0) return null;
  switch (value.kind) {
    case TEXT: return 't' + value.scalar;
    case BOOL: return value.scalar ? 'T' : 'F';
    case BIN: {
      let key = 'b';
      const bytes = value.scalar;
      for (let i = 0; i < bytes.length; i++) key += String.fromCharCode(bytes[i]);
      return key;
    }
    default: return 'n';
  }
}

function stringHash(s) {
  let h = (2166136261 ^ HASH_SEED) >>> 0;
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
// conversion has to be pinned down. Only whole numbers: spec §8 lists a float among the things a constructor does
// not take, and PHP and Python refuse one. JS cannot tell `3` from `3.0`, so a
// whole-valued double is accepted; a fraction is not, because 0.1 + 0.2 is
// 0.30000000000000004 and turning that into a decimal is the silent guess §8
// exists to prevent. Pass a string ("0.1") or a decimal record for a fraction.
function nativeNumberToDecimal(x) {
  if (!Number.isFinite(x)) badArg('a non-finite number has no SEL value');
  const s = String(x);
  if (/^-?\d+$/.test(s)) return s;
  badArg(`number ${s} is a float, which has no exact decimal form; pass a string instead`);
}

// The value of a key JS treats as an array index (canonical decimal, below 2^32 - 1),
// or -1.
function arrayIndex(key) {
  if (!/^(0|[1-9][0-9]{0,9})$/.test(key)) return -1;
  const n = Number(key);
  return n < 4294967295 ? n : -1;
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

export { quoteDump };
