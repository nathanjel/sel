// Dialect lookup, and the runtime registration an application extends the map
// with.
//
// The generated table in `_map.mjs` is already flattened, so a shipped lookup is
// a property access; the overlay written here is what re-introduces the extends
// chain, and it is the only thing that does.
//
// **There is no trace facility here, deliberately.** PHP's `Map::traceOn` exists
// for two checks — the oracle's per-entry coverage gate and sqlt's caveat pins —
// and both ask about `sql/dialects/*.json` and `sql/cases/*.sqlt`, which are
// shared data that PHP already measures. Running them a second time here would
// measure the same thing twice.

import { asciiUpper } from '../lexer.mjs';
import { DIALECTS, RULES } from './_map.mjs';
import { refuse } from './errors.mjs';

export const SECTIONS = ['ops', 'funcs', 'skel'];

// Sentinel for "no dialect in the chain mentioned this key".
export const MISSING = '\0missing';

// Dialects declared at run time, and runtime entries consulted before the
// generated table: dialect -> section -> key -> entry | builder.
//
// Maps, not objects, for the reason the parser's operator tables are Maps: the
// keys are caller-supplied text, and `{}` answers for every Object.prototype
// name. A dialect called `toString` must not already exist.
const extra = new Map();
const overlay = new Map();

// Dialects whose numericGuard has been checked against their ISNUM. Dropped
// whole whenever any dialect's ISNUM is (re)defined -- define() allows
// redefinition and the last writer wins, so a memo taken before an ISNUM changed
// would vouch for a pairing that no longer exists, and a redefinition against a
// BASE reaches every dialect that inherits from it. Registration is a start-up
// activity; throwing the whole set away costs one comparison per dialect
// afterwards.
const guardChecked = new Set();

// Object.hasOwn throughout, never `in` and never a truthiness test: the
// generated tables are plain objects parsed from JSON, so `'constructor' in d`
// is true for every one of them.
const has = (obj, key) => obj != null && Object.hasOwn(obj, key);

// --- registration ------------------------------------------------------------

// Declare a dialect.
//
// The usual reason is an older or newer server than the shipped map assumes,
// which needs no special code because a version is only another link in the
// chain:
//
//     map.defineDialect('mariadb-11.8', { extends: 'mariadb', version: '11.8' });
//
// Throws Error, not SqlError: a malformed registration is a mistake in the
// application's startup, and `tryTranslate` must not swallow it.
// Every key defineDialect() accepts. sql/MAP.md §3 is the normative list.
const DIALECT_KEYS = ['extends', 'version', 'target', 'lexical'];

export function defineDialect(name, spec) {
  if (exists(name)) {
    throw new Error(`SQL dialect ${name} is already defined; a name means one dialect`);
  }
  // The keys a dialect declaration carries, and nothing else. `ops`, `funcs` and
  // `skel` are NOT among them -- they are defined one entry at a time with
  // define() -- and passing them here used to be accepted and silently dropped,
  // which is a registration that looks like it worked.
  const unknown = Object.keys(spec).filter((k) => !DIALECT_KEYS.includes(k)).sort();
  if (unknown.length) {
    throw new Error(`SQL dialect ${name} declares ${unknown.join(', ')}, which a `
      + 'dialect declaration does not carry; ops, funcs and skel entries are '
      + 'defined one at a time with define()');
  }

  // `in`, not `?? null`: a dialect with no parent is a real thing -- `ansi` is
  // one -- but forgetting the key is a typo, and the two must not look alike.
  // Without this a missing `extends` would quietly produce a root that inherits
  // nothing and answers every lookup with MISSING.
  if (!('extends' in spec)) {
    throw new Error(`SQL dialect ${name} must say what it extends; write `
      + 'extends: null for a dialect with no parent, as ansi has');
  }
  const ext = spec.extends;
  if (ext !== null && !exists(ext)) {
    throw new Error(`SQL dialect ${name} extends ${ext}, which does not exist`);
  }

  // Each default is applied when the key is absent OR explicitly null, because
  // PHP's `?? …` is. `??` is that operator, so this is the same rule.
  const or = (key, dflt) => spec[key] ?? dflt;

  // A root inherits nothing, so it has to state its own version.
  let version;
  if (ext === null) {
    if ((spec.version ?? null) === null) {
      throw new Error(`SQL dialect ${name} extends nothing, so it must declare a `
        + 'version; there is none to inherit');
    }
    version = spec.version;
  } else {
    version = or('version', record(ext).version);
  }
  // Dotted-numeric, as sql/MAP.md §4.5 says and nothing cleverer. A live server
  // reports "11.8.8-MariaDB", which is the natural thing to pass and is not a
  // version this map can compare: PHP's intval read it as 11.8.8 by guessing and
  // int() raised a ValueError out of the first translation that had a `since`.
  // Refused here, at the line that wrote it.
  if (typeof version !== 'string' || !DOTTED.test(version)) {
    throw new Error(`SQL dialect ${name} has version ${JSON.stringify(version)}, which `
      + 'is not dotted-numeric; strip any suffix a server reports '
      + '(11.8.8-MariaDB is 11.8.8)');
  }
  const target = or('target', true);
  if (typeof target !== 'boolean') {
    throw new Error(`SQL dialect ${name} has a target that is not a boolean; `
      + 'truthiness differs between hosts and must not decide this');
  }
  const lexical_ = or('lexical', {});
  if (lexical_ === null || typeof lexical_ !== 'object' || Array.isArray(lexical_)) {
    throw new Error(`SQL dialect ${name} has a lexical that is not a map`);
  }
  for (const [k, v] of Object.entries(lexical_)) {
    checkLexical(String(k), v, `SQL dialect ${name}`);
  }

  extra.set(name, { extends: ext, version, target, lexical: lexical_ });
}

// Define or withdraw one entry.
//
// Unlike `registry.define`, redefinition is allowed and the last writer wins: a
// duplicate SEL function is always a bug, while a duplicate SQL entry is usually
// an application deliberately overriding a shipped default for its own schema or
// server build.
//
// Passing a string withdraws the entry and makes the string the reason the
// caller is given; passing null withdraws it without one.
export function define(dialect, section, key, entry_) {
  checkSection(section);
  if (!exists(dialect)) throw new Error(`SQL dialect ${dialect} does not exist`);
  checkKey(section, key);
  checkEntry(section, key, entry_);
  // Only `funcs` keys are SEL function names, which are case-insensitive. `ops`
  // keys are operator tokens and `skel` keys are camel-case names the translator
  // looks up verbatim — upper-casing those stored a registered skeleton under a
  // key nothing ever reads, which made the documented escape hatch silently dead.
  const k = section === 'funcs' ? asciiUpper(key) : key;
  if (!overlay.has(dialect)) overlay.set(dialect, new Map());
  const byDialect = overlay.get(dialect);
  if (!byDialect.has(section)) byDialect.set(section, new Map());
  byDialect.get(section).set(k, entry_);
  // A redefined ISNUM invalidates every memoised guard check: define() lets the
  // last writer win, and a redefinition against a BASE reaches every dialect
  // that inherits from it, so the whole set goes rather than one name.
  if (section === 'funcs' && k === 'ISNUM') guardChecked.clear();
}

// The escape hatch, for what a template cannot say.
//
// A builder receives the already-rendered arguments and returns a Fragment. This
// is the SQL layer's equivalent of the `fn` in `registry.define`.
export function defineBuilder(dialect, section, key, fn) {
  define(dialect, section, key, { builder: fn });
}

// Forget every runtime registration. For tests; nothing else should need it.
export function reset() {
  extra.clear();
  overlay.clear();
  guardChecked.clear();
}

// Every quoted run, not the first: two genuinely different numeral tests that
// happen to share an earlier literal — a flag, a collation clause — compare
// equal if only the first is read. The generator learned this from a decoy
// that defeated it.
function quotedRuns(tpl) {
  return [...tpl.matchAll(/'([^']*)'/g)].map((m) => m[1]);
}

// sql/MAP.md §7 rule 10, asked at the moment the guard is used.
//
// The generator checks it when the map is built, and for six months that was the
// whole of it: an application registering its own dialect could declare a
// numericGuard that disagreed with its ISNUM, or one that tested nothing, and
// nothing refused it. MAP.md said so and filed it beside a binding declared NUM
// over a column that is not -- the caller's promise.
//
// It does not belong there. A wrong NUM declaration is a claim the caller makes
// about their own data; a wrong numericGuard is a claim about SEL's numeral
// grammar, which the caller has no way to check and every other lexical key fails
// loudly about. This one fails silently: it emits SQL that answers where SEL would
// not, which is the one outcome docs/SQL-KINDS.md exists to rule out. The first
// external user of this layer registered a derived dialect on their first day,
// overriding one lexical key. It was textCollate; it could have been this.
//
// Checked here rather than in defineDialect because registration has no end:
// funcs.ISNUM is defined one entry at a time with define(), so at the moment a
// dialect is declared its ISNUM may not exist yet. By the time a guard is being
// USED, everything either side of the rule is registered.
export function checkNumericGuard(dialect) {
  if (guardChecked.has(dialect)) return;
  guardChecked.add(dialect);
  const guard = lexical(dialect, 'numericGuard');
  if (typeof guard !== 'string') return;
  const isnum = entry(dialect, 'funcs', 'ISNUM');
  const tpl = isnum && typeof isnum === 'object' ? isnum.tpl : null;
  if (typeof tpl !== 'string') {
    throw new Error(`SQL dialect ${dialect} declares a numericGuard but maps no `
      + 'funcs.ISNUM with a template for it to agree with; the two ask the same '
      + 'question and sql/MAP.md §7 rule 10 is that one place defines a thing');
  }
  const want = quotedRuns(tpl);
  if (want.length === 0) {
    throw new Error(`SQL dialect ${dialect} maps a funcs.ISNUM that carries no `
      + 'quoted pattern, so its numericGuard has nothing to agree with');
  }
  const got = new Set(quotedRuns(guard));
  const missing = want.filter((w) => !got.has(w));
  if (missing.length) {
    throw new Error(`SQL dialect ${dialect} declares a numericGuard that does not `
      + `carry ${missing.map((m) => `'${m}'`).join(', ')}, which its funcs.ISNUM `
      + 'tests; they ask the same question, and a guard that asks a different one '
      + 'answers for rows SEL refuses');
  }
}

// --- lookup ------------------------------------------------------------------

export function exists(dialect) {
  return extra.has(dialect) || has(DIALECTS, dialect);
}

function record(dialect) {
  return extra.has(dialect) ? extra.get(dialect) : DIALECTS[dialect];
}

// Every dialect that may be named in a translate() call, sorted.
export function targets() {
  const out = new Set();
  for (const [d, r] of Object.entries(DIALECTS)) if (r.target) out.add(d);
  for (const [d, r] of extra) if (r.target) out.add(d);
  return [...out].sort();
}

// Check a dialect may be translated to.
//
// A base is not a target: `ansi` and `mysql-family` name no server anyone runs,
// and a dialect no database implements is not one a caller should be able to aim
// at.
export function requireTarget(dialect, pos = null) {
  if (!exists(dialect)) {
    refuse('E_SQL_DIALECT',
      `there is no SQL dialect ${dialect}; known targets are ${targets().join(', ')}`,
      pos);
  }
  if (!record(dialect).target) {
    refuse('E_SQL_DIALECT',
      `${dialect} is a base other dialects inherit from, not a server anyone runs; `
      + `translate to one of ${targets().join(', ')}`, pos);
  }
}

// Self first, then extends, up to ansi.
export function chain(dialect) {
  const out = [];
  let cur = dialect;
  while (cur != null && exists(cur) && !out.includes(cur)) {
    out.push(cur);
    cur = record(cur).extends ?? null;
  }
  return out;
}

export function version(dialect) {
  return String(record(dialect).version);
}

// A lexical value.
//
// Runtime dialects may override individual keys; otherwise the generated table
// already holds the flattened result.
export function lexical(dialect, key) {
  // Membership, not `!= null`, and for the same reason entry() uses it:
  // sql/MAP.md §3 says a null lexical value is a WITHDRAWAL — "a null
  // binaryLiteral refuses BIN literals" — and a null test reads that as "absent"
  // and walks on to the base, which handed the withdrawn value back. The
  // documented withdrawal was unimplementable, in both hosts.
  for (const d of chain(dialect)) {
    const lx = record(d).lexical ?? {};
    if (has(lx, key)) return lx[key];
  }
  return null;
}

// One entry, or MISSING.
//
// The overlay is consulted first and walks the chain; the generated table does
// not need walking because the generator flattened it.
export function entry(dialect, section, key) {
  checkSection(section);
  const ch = chain(dialect);

  // The whole overlay chain first, and only then the generated table.
  // Interleaving the two per level would look tidier and would be wrong: the
  // generated tables are already flattened, so a generated hit at the leaf would
  // shadow a runtime entry registered against a base, and registering against
  // `ansi` is documented to reach every dialect.
  for (const d of ch) {
    const sec = overlay.get(d)?.get(section);
    if (sec && sec.has(key)) return sec.get(key);
  }
  for (const d of ch) {
    const sec = has(DIALECTS, d) ? DIALECTS[d][section] : undefined;
    if (has(sec, key)) return sec[key];
  }
  return MISSING;
}

const DOTTED = /^[0-9]+(\.[0-9]+)*$/;
const TPL_KEY = /^(0|[1-9][0-9]{0,2})$/;
const UNIFY = /^@unify:[0-9]+(,[0-9]+)*$/;
const SLOT_IN_TPL = /\{([^}]*)\}/g;

// --- registration validation -------------------------------------------------
//
// What tools/gen-sql-map.mjs enforces at generation time, enforced here at
// registration time, against the vocabulary that file EMITS rather than a second
// copy of it. Every one of these refusals closes a place where the hosts
// improvised differently over an entry the generator would never have accepted —
// a JSON list where a template belongs, an arity of strings, a `ret` that was not
// there at all.
//
// Error, not SqlError: a malformed registration is a mistake in the
// application's startup, and tryTranslate() must not swallow it.

// `typeof` is not the name Python's type().__name__ gives, and it does not need
// to be: sql/cases/README.md says messages are never asserted. What matters is
// that a reader can tell what they passed.
function typeName(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'list';
  return typeof v;
}

function checkLexical(key, v, where) {
  const types = RULES.lexicalTypes;
  if (!has(types, key)) {
    throw new Error(`${where} sets the unknown lexical key ${key}; known keys are `
      + Object.keys(types).join(', '));
  }
  // null is a WITHDRAWAL everywhere in the map, so it is always allowed —
  // sql/MAP.md §3 says a null binaryLiteral refuses BIN literals, and lexical()
  // looks keys up by presence so that it can.
  if (v === null) return;
  if (types[key] === 'map') {
    // textEscape given as a STRING made both hosts skip escaping entirely and
    // emit 'it's' unquoted. That is an injection, it was in both hosts, and
    // nothing checked.
    if (v === null || typeof v !== 'object' || Array.isArray(v)) {
      throw new Error(`${where} sets ${key} to a ${typeName(v)}; it must be a map of `
        + 'character to replacement');
    }
    for (const [frm, to] of Object.entries(v)) {
      if (frm === '' || typeof to !== 'string') {
        throw new Error(`${where}'s ${key} maps ${JSON.stringify(frm)} to something `
          + 'that is not a string');
      }
    }
    return;
  }
  // Everything else is a string, and is never cast to one: `true` given as a
  // JSON boolean rendered as `1` on the PHP host and `True` on Python's.
  if (typeof v !== 'string') {
    throw new Error(`${where} sets ${key} to a ${typeName(v)}; it must be a string`);
  }
  // A quote character that is not a character cannot quote. Left through, the
  // hosts disagreed about what it meant -- JS's split/join inserts the escape
  // between every character, Python's str.replace also puts one at each end --
  // and both answers are nonsense. textCollate is legitimately empty (ansi and
  // sqlite ship it that way); these two are not.
  if (v === '' && (key === 'identQuote' || key === 'textQuote')) {
    throw new Error(`${where} sets ${key} to the empty string; a quote character `
      + 'that is not a character cannot quote');
  }
}

function checkKey(section, key) {
  if (section === 'ops' && !has(RULES.opArity, key)) {
    throw new Error(`${key} is not a SEL operator, so an ops entry for it would `
      + 'never be looked up');
  }
  // `funcs` keys are SEL function names and case-insensitive; ops and skel keys
  // are looked up verbatim, which is why define() upper-cases only the first.
  // Registering `and` or `Case` used to be silently dead.
  if (section === 'funcs' && !has(RULES.funcArity, asciiUpper(key))) {
    throw new Error(`${key} is not a SEL function this layer maps; the aggregates `
      + 'and IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by stage 2 and never '
      + 'reach the funcs table');
  }
  if (section === 'skel' && !has(RULES.skelSlots, key)) {
    throw new Error(`${key} is not a skeleton; known ones are `
      + Object.keys(RULES.skelSlots).join(', '));
  }
}

function checkEntry(section, key, e) {
  const where = `the ${section} entry for ${key}`;
  // A string is a refusal carrying its reason; null is a refusal without one.
  // Both are entries, and neither has anything else to check.
  if (e === null || typeof e === 'string') return;
  if (typeof e !== 'object' || Array.isArray(e)) {
    throw new Error(`${where} must be a map, a string or null, and is ${typeName(e)}`);
  }
  if ((e.builder ?? null) !== null) {
    if (typeof e.builder !== 'function') {
      throw new Error(`${where} has a builder that is not callable; use `
        + 'map.defineBuilder()');
    }
    return;
  }

  // A skeleton is a template with NAMED slots and no kind: the translator decides
  // what a CASE or a subquery yields, not the map. So it is checked for its slots
  // and nothing else.
  if (section === 'skel') {
    if (typeof e.tpl !== 'string') {
      throw new Error(`${where} needs a tpl that is a string`);
    }
    const allowed = RULES.skelSlots[key];
    for (const m of e.tpl.matchAll(SLOT_IN_TPL)) {
      if (!allowed.includes(m[1])) {
        throw new Error(`${where} uses the slot {${m[1]}}; ${key} has `
          + `${allowed.join(', ')} — a typo would survive as literal text in `
          + 'every query');
      }
    }
    if ((e.caveat ?? null) !== null && !RULES.caveats.includes(e.caveat)) {
      throw new Error(`${where} declares the caveat ${JSON.stringify(e.caveat)}, `
        + 'which is not on the closed list in sql/MAP.md §4.6');
    }
    return;
  }

  if (has(e, 'tpl') === has(e, 'variants')) {
    throw new Error(`${where} needs exactly one of tpl and variants`);
  }
  const ret = e.ret;
  if (typeof ret !== 'string'
      || (!RULES.retKinds.includes(ret) && ret !== '@concat' && !UNIFY.test(ret))) {
    throw new Error(`${where} has ret ${JSON.stringify(ret)}; use one of `
      + `${RULES.retKinds.join(', ')}, @concat or @unify:<n>[,<n>...]`);
  }
  if ((e.caveat ?? null) !== null && !RULES.caveats.includes(e.caveat)) {
    throw new Error(`${where} declares the caveat ${JSON.stringify(e.caveat)}, which `
      + 'is not on the closed list in sql/MAP.md §4.6; a caveat an application '
      + 'cannot branch on is prose');
  }
  const since = e.since ?? null;
  if (since !== null && (typeof since !== 'string' || !DOTTED.test(since))) {
    throw new Error(`${where} has a since that is not dotted-numeric`);
  }
  const arity = e.arity ?? null;
  if (arity !== null) {
    const ok = Array.isArray(arity) && arity.length === 2
      && arity.every((x) => Number.isInteger(x))
      && arity[0] >= 0 && arity[1] >= arity[0];
    if (!ok) {
      throw new Error(`${where} has an arity that is not [min, max] of two integers`);
    }
  }
  if (has(e, 'variants')) {
    const vs = e.variants;
    if (vs === null || typeof vs !== 'object' || Array.isArray(vs)
        || Object.keys(vs).length === 0) {
      throw new Error(`${where} has variants that are not a map`);
    }
    const allowed = has(RULES.variants, key) ? RULES.variants[key] : null;
    if (allowed === null) {
      throw new Error(`${where} uses variants, and ${key} is not a variant family`);
    }
    for (const name of Object.keys(vs)) {
      if (!allowed.includes(name)) {
        throw new Error(`${where} declares the variant ${name}; ${key} has `
          + allowed.join(', '));
      }
    }
  }
  const tpl = e.tpl;
  if (tpl !== null && typeof tpl === 'object') {
    // Every key is an argument COUNT the entry can actually be called with,
    // checked against SEL's own arity narrowed by the entry's.
    //
    // Checking the shape alone is not enough, and PHP is why: a JSON list
    // ["a", "b"] decodes there to an array whose keys are 0 and 1, which are
    // perfectly good count keys, so it is indistinguishable from
    // {"0": "a", "1": "b"} — and it reached the renderer and emitted the literal
    // `b`. Against UPPER's arity of [1, 1] the count 0 is out of range, and the
    // list is refused for the reason it is actually wrong. A list is included
    // here so every host refuses it at the same line.
    const base = section === 'ops' ? RULES.opArity[key] : RULES.funcArity[asciiUpper(key)];
    let [lo, hi] = base;
    if (arity !== null) {
      lo = Math.max(lo, arity[0]);
      hi = hi === null ? arity[1] : Math.min(hi, arity[1]);
    }
    const keys = Array.isArray(tpl) ? tpl.map((_, i) => String(i)) : Object.keys(tpl);
    for (const n of keys) {
      if (n === '*') continue;
      if (!TPL_KEY.test(String(n))) {
        throw new Error(`${where} keys a template by ${JSON.stringify(n)}; an `
          + 'arity-keyed template uses a count or *');
      }
      const c = Number(n);
      if (c < lo || (hi !== null && c > hi)) {
        throw new Error(`${where} keys a template by ${c}, and ${key} takes ${lo} to `
          + `${hi === null ? 'any' : hi} argument(s), so that template could never `
          + 'be chosen');
      }
    }
  }
}

function checkSection(section) {
  if (!SECTIONS.includes(section)) {
    throw new Error(`unknown map section ${section}; use ${SECTIONS.join(', ')}`);
  }
}

// Dotted-numeric, as sql/MAP.md §4.5 specifies and nothing cleverer.
export function versionAtLeast(have, want) {
  const a = have.split('.').map(Number);
  const b = want.split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const x = i < a.length ? a[i] : 0;
    const y = i < b.length ? b[i] : 0;
    if (x !== y) return x > y;
  }
  return true;
}
