#!/usr/bin/env node
// Generates each host's SEL->SQL dialect map from sql/dialects/*.json.
//
//     node tools/gen-sql-map.mjs            write the generated files
//     node tools/gen-sql-map.mjs --check    validate and diff, write nothing
//
// The generated files ARE committed, unlike dist/. They are source as far as
// each host is concerned — Packagist and PyPI ship them, and a clone must not
// need Node to get a working library. Staleness is caught by --check, which
// tools/check-sql-map.sh runs from tools/check.sh; a generated file in version
// control goes stale only if nothing checks it.
//
// Everything sql/MAP.md §7 promises is enforced here, so no host ever has to
// defend against a malformed map. A validation failure is a non-zero exit and a
// message naming the file, the section and the key.

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, basename } from 'node:path';

// Importing the JS host populates the function table as a side effect, which is
// what lets an entry's `arity` be checked against SEL's own declared arity
// rather than against a second copy of it that could drift.
import '../js/src/sel.mjs';
import { lookup as selLookup, names as selNames } from '../js/src/registry.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIALECT_DIR = resolve(ROOT, 'sql/dialects');
const MAX_CHAIN = 8;

// ---------------------------------------------------------------------------
// What the map is not allowed to contain.

// Stage 2 lowers these; they are not template-shaped and never reach `funcs`.
const LOWERED = new Set([
  'IF', 'COND', 'ABORT', 'COUNT', 'INDEXES', 'HAS',
  'ALL', 'ANY', 'MAP', 'FILTER', 'SUM', 'JOIN',
]);

// sql/MAP.md §4.6. Closed, so Fragment::$caveats is something an application
// can branch on rather than a bag of prose.
const CAVEATS = new Set([
  'unicode-case', 'division-scale', 'numeric-scale', 'scale-limit', 'decimal-float',
  'rounding-mode',
  'modulo-integer',
  'power-float', 'text-collation', 'regex-engine', 'concat-null',
  'trim-charset', 'length-units', 'input-laxity',
]);

const RET_KINDS = new Set(['NUM', 'TEXT', 'BOOL', 'BIN', 'UNKNOWN']);

// An operator's argument count. `funcs` gets this from the SEL function table,
// which is the whole reason this file imports the JS host; `ops` has no such
// table to borrow, so without this an entry could reference {3} of a binary
// operator and nothing would object.
const OP_ARITY = {
  NEG: 1, NOT: 1,
  // IN's list variant sees a flattened vector — the needle at {0} and every
  // element after it — so it is the one operator with no upper bound.
  IN: Infinity,
};
const opArity = (key) => OP_ARITY[key] ?? 2;

const LEXICAL_KEYS = [
  'identQuote', 'identEscape', 'textQuote', 'textEscape', 'true', 'false',
  'binaryLiteral', 'numericLiteral', 'textCollate', 'textCast', 'numericCast',
  'binaryCast', 'isTrue', 'isNotTrue', 'placeholder',
];
// Substitutable in a template. textEscape is an object and binaryLiteral is
// filled by the renderer, so neither is spliceable.
const LEXICAL_TEMPLATE_KEYS = LEXICAL_KEYS.filter(
  (k) => k !== 'textEscape' && k !== 'binaryLiteral',
);

// sql/MAP.md §4.3. A variants object may use only the names its family defines.
const VARIANT_FAMILIES = {
  '==': ['num', 'coerce'], '!=': ['num', 'coerce'], '<': ['num', 'coerce'],
  '<=': ['num', 'coerce'], '>': ['num', 'coerce'], '>=': ['num', 'coerce'],
  '$==': ['text'], '$!=': ['text'], '$<': ['text'],
  '$<=': ['text'], '$>': ['text'], '$>=': ['text'],
  EQL: ['text'],
  IN: ['scalar'],
  '&': ['text', 'bin'],
};

// sql/MAP.md §5. Named placeholders, so a typo cannot survive as literal text.
const SKEL_SLOTS = {
  case: ['branches', 'else'],
  caseBranch: ['cond', 'then'],
  all: ['from', 'corr', 'body'],
  any: ['from', 'corr', 'body'],
  sum: ['from', 'corr', 'body'],
  count: ['from', 'corr'],
  join: ['from', 'corr', 'body', 'sep'],
  inRelation: ['needle', 'from', 'corr', 'body'],
};

// The type each lexical key must have. sql/MAP.md §3.
//
// Emitted, and read at run time by every host's Map::define, because a lexical
// value with the wrong type is not a style problem: `textEscape` given as a
// STRING made both hosts skip escaping entirely and emit 'it's' unquoted, which
// is an injection, and `true` given as a JSON boolean rendered as `1` on one
// host and `True` on the other. Neither was noticed because nothing checked.
//
// `null` is a WITHDRAWAL wherever it appears, and is therefore always allowed --
// sql/MAP.md §3 says a null binaryLiteral refuses BIN literals. The hosts look
// lexical keys up by PRESENCE for the same reason.
const LEXICAL_TYPES = {
  identQuote: 'string', identEscape: 'string', textQuote: 'string',
  textEscape: 'map', true: 'string', false: 'string',
  binaryLiteral: 'string', numericLiteral: 'string', textCollate: 'string',
  textCast: 'string', numericCast: 'string', binaryCast: 'string',
  isTrue: 'string', isNotTrue: 'string', placeholder: 'string',
};

/**
 * The vocabulary, emitted as data.
 *
 * Everything above is what this file checks the shipped map against. Nothing
 * checked a map entry registered at RUN time, so `Map::define` accepted an entry
 * with no `ret`, a `tpl` that was a JSON list, an `arity` of strings, a `since`
 * of "abc" and a caveat somebody invented -- and the two hosts then improvised
 * differently over each one, because improvising is what code does when it has
 * no rule. Every one of those is an entry this file would have rejected.
 *
 * So the lists are emitted rather than retyped in six languages. The LOGIC is
 * necessarily per host, because the translator is; the VOCABULARY is data, and
 * data is generated.
 */
function buildRules(dialects) {
  const ops = new Set();
  for (const d of Object.values(dialects)) {
    for (const k of Object.keys(d.ops ?? {})) ops.add(k);
  }
  const funcs = {};
  for (const name of selNames()) {
    if (LOWERED.has(name)) continue;          // stage 2 lowers these
    const spec = selLookup(name);
    funcs[name] = [spec.min, spec.max === Infinity ? null : spec.max];
  }
  const opArityOut = {};
  for (const k of [...ops].sort()) {
    const a = opArity(k);
    opArityOut[k] = a === Infinity ? [1, null] : [a, a];
  }
  return {
    sections: ['ops', 'funcs', 'skel'],
    caveats: [...CAVEATS].sort(),
    retKinds: [...RET_KINDS].sort(),
    opArity: opArityOut,
    funcArity: funcs,
    variants: VARIANT_FAMILIES,
    skelSlots: SKEL_SLOTS,
    lexicalTypes: LEXICAL_TYPES,
    templateKeys: LEXICAL_TEMPLATE_KEYS,
  };
}

// ---------------------------------------------------------------------------

const errors = [];
const fail = (where, msg) => errors.push(`${where}: ${msg}`);

const isObject = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const isRefusal = (x) => x === null || typeof x === 'string';

/**
 * A reason that looks like a template. Since §2 makes a string a refusal, a
 * template accidentally written in the string position would silently become
 * one — the rule would refuse instead of translating, and nothing would say so.
 * Any {slot} in a reason is that slip, because prose has no reason to hold one.
 */
function reasonLooksLikeTemplate(reason) {
  return slots(reason).some((s) => s.raw !== null && /^([0-9]+:?|\*|[a-z][A-Za-z]*)$/.test(s.raw));
}

/** Every {…} in a template, ignoring the {{ }} escapes. */
function slots(tpl) {
  const out = [];
  for (let i = 0; i < tpl.length; i++) {
    if (tpl[i] === '{' && tpl[i + 1] === '{') { i++; continue; }
    if (tpl[i] === '}' && tpl[i + 1] === '}') { i++; continue; }
    if (tpl[i] !== '{') continue;
    const end = tpl.indexOf('}', i);
    if (end < 0) { out.push({ raw: null, at: i }); break; }
    out.push({ raw: tpl.slice(i + 1, end), at: i });
    i = end;
  }
  return out;
}

// --- load ------------------------------------------------------------------

function load() {
  const docs = new Map();
  for (const f of readdirSync(DIALECT_DIR).sort()) {
    if (!f.endsWith('.json')) continue;
    const name = basename(f, '.json');
    let doc;
    try {
      doc = JSON.parse(readFileSync(resolve(DIALECT_DIR, f), 'utf8'));
    } catch (e) {
      fail(f, `not valid JSON — ${e.message}`);
      continue;
    }
    if (doc.dialect !== name) {
      fail(f, `declares dialect "${doc.dialect}" but the file is named "${name}.json"`);
    }
    docs.set(name, doc);
  }
  return docs;
}

/** Self first, then extends, … up to ansi. */
function chainOf(docs, name) {
  const chain = [];
  const seen = new Set();
  let cur = name;
  while (cur !== undefined) {
    if (seen.has(cur)) { fail(name, `extends cycle at "${cur}"`); return chain; }
    seen.add(cur);
    const doc = docs.get(cur);
    if (!doc) { fail(name, `extends "${cur}", which does not exist`); return chain; }
    chain.push(doc);
    if (chain.length > MAX_CHAIN) { fail(name, `chain deeper than ${MAX_CHAIN}`); return chain; }
    cur = doc.extends;
  }
  if (chain[chain.length - 1].dialect !== 'ansi') {
    fail(name, 'chain does not reach ansi');
  }
  return chain;
}

const DOTTED = /^[0-9]+(\.[0-9]+)*$/;

// --- flatten ---------------------------------------------------------------

function flatten(docs, name) {
  const chain = chainOf(docs, name);
  const merge = (section) => {
    const out = {};
    for (let i = chain.length - 1; i >= 0; i--) {              // base first
      Object.assign(out, chain[i][section] || {});
    }
    return out;
  };
  const self = chain[0];
  return {
    dialect: name,
    extends: self.extends ?? null,
    version: self.version ?? '0',
    target: self.target === true,
    lexical: merge('lexical'),
    ops: merge('ops'),
    funcs: merge('funcs'),
    skel: merge('skel'),
  };
}

// --- lexical expansion -----------------------------------------------------

/**
 * {key} splices a lexical string; {key:n} wraps argument n in a lexical
 * template. Done here so the shipped map holds finished templates and the hosts
 * expand only what an application registers at run time.
 */
function expandLexical(tpl, lexical, where) {
  let out = '';
  let i = 0;
  while (i < tpl.length) {
    if (tpl[i] === '{' && tpl[i + 1] === '{') { out += '{{'; i += 2; continue; }
    if (tpl[i] === '}' && tpl[i + 1] === '}') { out += '}}'; i += 2; continue; }
    if (tpl[i] !== '{') { out += tpl[i++]; continue; }
    const end = tpl.indexOf('}', i);
    if (end < 0) { out += tpl.slice(i); break; }
    const body = tpl.slice(i + 1, end);
    const [key, arg] = body.includes(':') ? body.split(':', 2) : [body, null];

    if (LEXICAL_TEMPLATE_KEYS.includes(key)) {
      const val = lexical[key];
      if (val === undefined) {
        fail(where, `template uses {${body}} but lexical.${key} does not resolve`);
        out += tpl.slice(i, end + 1);
      } else if (arg === null || arg === '') {
        out += val;
      } else if (!/^[0-9]+$/.test(arg)) {
        fail(where, `{${body}} — the part after the colon must be an argument index`);
      } else {
        // The lexical template's own {0} becomes the caller's argument index.
        out += val.replace(/\{0\}/g, `{${arg}}`);
      }
    } else {
      out += tpl.slice(i, end + 1);           // {0}, {*}, {1:}, skeleton slots
    }
    i = end + 1;
  }
  return out;
}

// --- validation ------------------------------------------------------------

// The one slot grammar, shared with php/src/Sql/Emit.php and
// python/sel/sql/emit.py. Canonical and full-match: `{01}` is not `{1}` and
// `{1\n}` is not a slot at all. It was `/^[0-9]+$/` here and in both hosts,
// which JS reads strictly and PHP does not -- so a template this file refused
// was accepted at run time by Map::define, and the shipped map and a registered
// entry were read by two different grammars. Three digits is far above any
// entry's arity and keeps every host's integer parser out of it.
const SLOT_INDEX = /^(?:0|[1-9][0-9]{0,2})$/;

function checkTemplate(tpl, { where, minArgs, maxArgs, allowNamed }) {
  for (const s of slots(tpl)) {
    if (s.raw === null) { fail(where, 'unclosed { in template'); continue; }
    const raw = s.raw;
    if (raw === '*') continue;
    if (SLOT_INDEX.test(raw)) {
      const n = Number(raw);
      if (maxArgs !== Infinity && n >= maxArgs) {
        fail(where, `template uses {${n}} but the entry takes at most ${maxArgs} argument(s)`);
      }
      continue;
    }
    if (raw.endsWith(':') && SLOT_INDEX.test(raw.slice(0, -1))) continue;
    if (allowNamed && allowNamed.includes(raw)) continue;
    if (raw === 'hex') continue;                       // binaryLiteral
    fail(where, `template uses {${raw}}, which is neither an argument nor a known slot`);
  }
  void minArgs;
}

function checkEntry(entry, key, section, dialect, lexical) {
  const where = `${dialect}.${section}.${key}`;
  if (isRefusal(entry)) {
    if (entry === null) return;
    if (entry.trim() === '') fail(where, 'refusal reason is empty; use null if there is none');
    else if (reasonLooksLikeTemplate(entry)) {
      fail(where, 'refusal reason contains a {slot} — a template written in the string position is a refusal, not a template');
    }
    return;
  }
  if (!isObject(entry)) { fail(where, 'entry must be an object, a string or null'); return; }

  const hasTpl = 'tpl' in entry;
  const hasVariants = 'variants' in entry;
  if (hasTpl === hasVariants) {
    fail(where, 'an entry needs exactly one of "tpl" and "variants"');
    return;
  }

  if (!entry.ret) fail(where, 'entry has no "ret"');
  else if (!RET_KINDS.has(entry.ret)
           && entry.ret !== '@concat'
           && !/^@unify:[0-9]+(,[0-9]+)*$/.test(entry.ret)) {
    fail(where, `unknown ret "${entry.ret}"`);
  }

  if (entry.caveat !== undefined && !CAVEATS.has(entry.caveat)) {
    fail(where, `caveat "${entry.caveat}" is not on the closed list in sql/MAP.md §4.6`);
  }
  if (entry.since !== undefined && !DOTTED.test(entry.since)) {
    fail(where, `since "${entry.since}" is not dotted-numeric`);
  }

  // Effective arity: the entry's own, narrowed against SEL's for a function.
  let [min, max] = entry.arity ?? [0, Infinity];
  if (entry.arity && (!Array.isArray(entry.arity) || entry.arity.length !== 2)) {
    fail(where, 'arity must be [min, max]');
  }
  if (section === 'ops') {
    const declared = opArity(key);
    if (entry.arity && max > declared) {
      fail(where, `arity [${min}, ${max}] is wider than ${key}'s ${declared} operand(s)`);
    } else if (!entry.arity) {
      [min, max] = [declared === Infinity ? 1 : declared, declared];
    }
  } else if (section === 'funcs') {
    const spec = selLookup(key);
    if (!spec) {
      fail(where, `${key} is not a SEL function`);
    } else if (entry.arity && (min < spec.min || max > spec.max)) {
      fail(where, `arity [${min}, ${max}] is outside SEL's [${spec.min}, ${spec.max}]`);
    } else if (!entry.arity) {
      [min, max] = [spec.min, spec.max];
    }
  }

  const opts = { where, minArgs: min, maxArgs: max };
  if (hasTpl) {
    if (typeof entry.tpl === 'string') {
      // A single template covering more than one argument count silently
      // ignores the arguments it does not name — which is how RMATCH's `i` flag
      // vanished, turning a TRUE into a case-sensitive FALSE with nothing to
      // say so. Either narrow `arity` or supply an arity-keyed `tpl`.
      // `{*}` and `{n:}` are exempt: those forms consume whatever they are given.
      const variadic = /\{(\*|[0-9]+:)\}/.test(entry.tpl);
      if (!variadic && min !== max) {
        fail(where, `one template covers ${min}..${max} arguments; it can only use `
          + `${min}, so the rest would be dropped silently — narrow "arity" or key `
          + '"tpl" by argument count');
      }
      checkTemplate(expandLexical(entry.tpl, lexical, where), opts);
    } else if (isObject(entry.tpl)) {
      // An arity-keyed template maps one argument count each. `*` is the
      // fallback for the counts it does not name, which is what an open-ended
      // arity needs: MIN takes one argument or any number, and only the
      // one-argument case cannot go through LEAST.
      for (const [arity, t] of Object.entries(entry.tpl)) {
        if (arity === '*') {
          checkTemplate(expandLexical(t, lexical, where),
            { ...opts, maxArgs: max, where: `${where}[*]` });
          continue;
        }
        if (!/^[0-9]+$/.test(arity)) { fail(where, `tpl key "${arity}" is not an argument count or "*"`); continue; }
        checkTemplate(expandLexical(t, lexical, where),
          { ...opts, maxArgs: Number(arity), where: `${where}[${arity}]` });
      }
      // Every count the entry accepts must resolve to a template. Without this
      // an arity-keyed entry refuses at run time for a count nobody noticed was
      // missing — the same silent gap a single template covering two counts has,
      // one level up.
      if (entry.tpl['*'] === undefined) {
        if (max === Infinity) {
          fail(where, `arity is [${min}, unbounded] and "tpl" names specific counts `
            + 'with no "*" fallback, so every larger count is unmapped');
        } else {
          const missing = [];
          for (let n = min; n <= max; n++) if (entry.tpl[String(n)] === undefined) missing.push(n);
          if (missing.length) {
            fail(where, `arity is [${min}, ${max}] but "tpl" has no template for `
              + `${missing.join(', ')} argument(s) — add them or add a "*" fallback`);
          }
        }
      }
    } else {
      fail(where, 'tpl must be a string or an object keyed by argument count');
    }
  } else {
    const allowed = VARIANT_FAMILIES[key];
    if (!allowed) fail(where, 'variants used on an entry with no variant family');
    for (const [name, t] of Object.entries(entry.variants)) {
      if (allowed && !allowed.includes(name)) {
        fail(where, `variant "${name}" is not one of ${allowed.join(', ')}`);
      }
      checkTemplate(expandLexical(t, lexical, where), { ...opts, where: `${where}.${name}` });
    }
  }
}

function validate(flat) {
  const { dialect, lexical } = flat;
  if (!DOTTED.test(flat.version)) fail(dialect, `version "${flat.version}" is not dotted-numeric`);

  if (flat.target) {
    for (const k of LEXICAL_KEYS) {
      if (lexical[k] === undefined) fail(dialect, `target dialect does not resolve lexical.${k}`);
    }
  }

  for (const [k, e] of Object.entries(flat.ops)) checkEntry(e, k, 'ops', dialect, lexical);

  for (const [k, e] of Object.entries(flat.funcs)) {
    if (LOWERED.has(k)) {
      fail(`${dialect}.funcs.${k}`, 'is lowered by stage 2 and must not appear in funcs');
      continue;
    }
    checkEntry(e, k, 'funcs', dialect, lexical);
  }

  // A skeleton follows sql/MAP.md §2 like every other entry: an object is
  // support, a string is a refusal carrying its reason. It would have been
  // shorter to spell a skeleton as a bare string, and that is exactly the trap —
  // a refusal is a string too, so neither this validator nor a host could tell
  // "EXISTS (SELECT 1 …)" from "no server spells this the same way".
  for (const [k, v] of Object.entries(flat.skel)) {
    const where = `${dialect}.skel.${k}`;
    if (!SKEL_SLOTS[k]) { fail(where, 'is not a known skeleton'); continue; }
    if (isRefusal(v)) {
      if (v === null) continue;
      if (v.trim() === '') fail(where, 'refusal reason is empty; use null if there is none');
      else if (reasonLooksLikeTemplate(v)) {
        fail(where, 'refusal reason contains a {slot} — a template written in the string position is a refusal, not a template');
      }
      continue;
    }
    if (!isObject(v) || typeof v.tpl !== 'string') {
      fail(where, 'a skeleton is { "tpl": "…" }, a reason string, or null');
      continue;
    }
    checkTemplate(expandLexical(v.tpl, lexical, where),
      { where, minArgs: 0, maxArgs: 0, allowNamed: SKEL_SLOTS[k] });
  }
}

// Lexical references are validated here (expandLexical reports an unresolvable
// {key} while checking a template) but deliberately NOT baked into the emitted
// map. Baking them in would be one fewer thing for a host to do at render time
// and would quietly break the reason lexical keys exist: `textCollate` is
// stated once and used by thirteen comparison entries, so an application on a
// server with a different binary collation should be able to override that one
// key. With the templates pre-expanded it could not — the key is gone by then,
// and it would have to re-register all thirteen.
//
// The cost is one scan per template fill, which Emit::fill already performs for
// entries registered at run time.

// --- emitters --------------------------------------------------------------

const BANNER = (tool) => [
  `GENERATED by tools/${tool} from sql/dialects/*.json — do not edit.`,
  'Regenerate with: node tools/gen-sql-map.mjs',
  'The format is normative in sql/MAP.md; the design is in docs/SQL-TRANSLATION.md.',
];

const phpStr = (s) => "'" + s.replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";

function phpValue(v, indent) {
  const pad = ' '.repeat(indent);
  if (v === null) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'number') return v === Infinity ? 'PHP_INT_MAX' : String(v);
  if (typeof v === 'string') return phpStr(v);
  if (Array.isArray(v)) {
    return '[' + v.map((x) => phpValue(x, indent)).join(', ') + ']';
  }
  const entries = Object.entries(v);
  if (entries.length === 0) return '[]';
  const body = entries
    .map(([k, x]) => `${pad}    ${phpStr(k)} => ${phpValue(x, indent + 4)},`)
    .join('\n');
  return `[\n${body}\n${pad}]`;
}

function emitPhp(dialects, rules) {
  const body = Object.entries(dialects)
    .map(([name, d]) => `        ${phpStr(name)} => ${phpValue(d, 8)},`)
    .join('\n');
  return `<?php
// ${BANNER('gen-sql-map.mjs').join('\n// ')}

declare(strict_types=1);

namespace Sel\\Sql;

final class MapData
{
    /**
     * Every dialect, with its chain already flattened, so a lookup is a hash
     * access and nothing else. Runtime registration is what re-introduces the
     * chain, and it is the only thing that does.
     *
     * @var array<string, array<string, mixed>>
     */
    public const DIALECTS = [
${body}
    ];

    /**
     * The map's own vocabulary, so Map::define can enforce at run time what
     * tools/gen-sql-map.mjs enforces at generation time.
     *
     * Emitted rather than retyped in each host. Every divergence a cross-host
     * review found in runtime registration -- an entry with no "ret", a "tpl"
     * that was a JSON list, an "arity" of strings, a caveat somebody invented --
     * was an entry the generator would have rejected and the runtime would not,
     * after which the two hosts improvised differently. Improvising is what code
     * does when it has no rule; this is the rule, as data.
     *
     * @var array<string, mixed>
     */
    public const RULES = ${phpValue(rules, 4)};
}
`;
}

function pyValue(v, indent) {
  const pad = ' '.repeat(indent);
  if (v === null) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return v === Infinity ? "float('inf')" : String(v);
  if (typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) return '[' + v.map((x) => pyValue(x, indent)).join(', ') + ']';
  const entries = Object.entries(v);
  if (entries.length === 0) return '{}';
  const inner = entries
    .map(([k, x]) => `${pad}    ${JSON.stringify(k)}: ${pyValue(x, indent + 4)},`)
    .join('\n');
  return `{\n${inner}\n${pad}}`;
}

function emitPython(dialects, rules) {
  const body = Object.entries(dialects)
    .map(([name, d]) => `    ${JSON.stringify(name)}: ${pyValue(d, 4)},`)
    .join('\n');
  return `"""${BANNER('gen-sql-map.mjs').join('\n')}

Every dialect, with its chain already flattened, so a lookup is a dict access
and nothing else. Runtime registration is what re-introduces the chain, and it
is the only thing that does.
"""

from __future__ import annotations

from typing import Any

DIALECTS: dict[str, dict[str, Any]] = {
${body}
}

#: The map's own vocabulary, so map.define can enforce at run time what
#: tools/gen-sql-map.mjs enforces at generation time.
#:
#: Emitted rather than retyped in each host. Every divergence a cross-host review
#: found in runtime registration -- an entry with no "ret", a "tpl" that was a
#: JSON list, an "arity" of strings, a caveat somebody invented -- was an entry
#: the generator would have rejected and the runtime would not, after which the
#: two hosts improvised differently. Improvising is what code does when it has no
#: rule; this is the rule, as data.
RULES: dict[str, Any] = ${pyValue(rules, 0)}
`;
}

// The JS emitter is the shortest of the three because the data is already
// JSON-shaped and JSON is a subset of JS: there is no quoting dialect to
// translate into, so `JSON.stringify` is the whole of it. Two spaces and sorted
// keys make the diff readable when a dialect document changes; nothing reads
// this file by hand.
function emitJs(dialects, rules) {
  return `// ${BANNER('gen-sql-map.mjs').join('\n// ')}
//
// Every dialect, with its chain already flattened, so a lookup is a property
// access and nothing else. Runtime registration is what re-introduces the
// chain, and it is the only thing that does.

export const DIALECTS = ${JSON.stringify(dialects, null, 2)};

// The map's own vocabulary, so map.define can enforce at run time what
// tools/gen-sql-map.mjs enforces at generation time.
//
// Emitted rather than retyped in each host. Every divergence a cross-host review
// found in runtime registration — an entry with no "ret", a "tpl" that was a
// JSON list, an "arity" of strings, a caveat somebody invented — was an entry
// the generator would have rejected and the runtime would not, after which the
// two hosts improvised differently. Improvising is what code does when it has no
// rule; this is the rule, as data.
export const RULES = ${JSON.stringify(rules, null, 2)};
`;
}

// --- C++ -------------------------------------------------------------------
//
// The other three hosts get a literal of their language's own map type. C++
// gets `constexpr` aggregates over static arrays, which is the same data with
// three properties the others have no need of: it is entirely .rodata, no
// static constructor runs to build it, and a link that never calls
// shipped_map() drops all of it under --gc-sections.
//
// Every initialiser below is DESIGNATED, and C++ requires those in declaration
// order -- so the field order in cpp/sel_sql_map.hpp is this emitter's
// contract. Reordering or renaming a member there fails the static_asserts
// beside the structs, which is the file a reader can act on.

function cppStr(s) {
  let out = '"';
  for (const b of Buffer.from(s, 'utf8')) {
    if (b === 0x5c) out += '\\\\';
    else if (b === 0x22) out += '\\"';
    // Three-digit octal, which -- unlike \x -- cannot run on into the
    // character beside it. No template holds a control byte today; this is here
    // so that one could not silently change its neighbour if one ever did.
    else if (b < 0x20 || b === 0x7f) out += '\\' + b.toString(8).padStart(3, '0');
    else out += String.fromCharCode(b);
  }
  return out + '"';
}

// Indexed, so two dialect names cannot sanitise to the same identifier.
// `mysql-family` and a future `mysql_family` would both be `mysql_family`.
const cppIdent = (name, i) => `d${i}_${name.replace(/[^A-Za-z0-9]/g, '_')}`;

function cppEntry(key, e, prefix, section, index, out) {
  const f = [`.key = ${cppStr(key)}`];
  // A string is a refusal carrying its reason. sql/MAP.md §4.2 -- the reason
  // reaches the caller as the message, exactly as a runtime withdrawal's does.
  if (typeof e === 'string' || e === null) {
    f.push('.kind = EntryKind::Refusal');
    if (e !== null) f.push(`.reason = {.present = true, .text = ${cppStr(e)}}`);
    return `{${f.join(', ')}}`;
  }
  f.push('.kind = EntryKind::Template');
  const body = e.variants ? 'Variants' : (typeof e.tpl === 'object' ? 'ByCount' : 'One');
  if (body === 'One') {
    f.push(`.one = ${cppStr(e.tpl)}`);
  } else {
    const arr = `${prefix}_${section}${index}`;
    const arms = Object.entries(e.variants ?? e.tpl);
    out.push(`constexpr Keyed ${arr}[] = {`);
    for (const [k, v] of arms) {
      out.push(`    {.key = ${cppStr(k)}, .value = ${cppStr(v)}},`);
    }
    out.push('};');
    f.push(`.body = BodyKind::${body}`, `.keyed = ${arr}`);
  }
  // `skel` entries carry no ret: the translator decides what a CASE or a
  // subquery yields, not the map.
  if (e.ret !== undefined) f.push(`.ret = ${cppStr(e.ret)}`);
  if (e.caveat !== undefined) f.push(`.caveat = ${cppStr(e.caveat)}`);
  if (e.since !== undefined) f.push(`.since = ${cppStr(e.since)}`);
  if (e.arity !== undefined) {
    f.push('.has_arity = true', `.arity_min = ${e.arity[0]}`,
           `.arity_max = ${e.arity[1]}`);
  }
  return `{${f.join(', ')}}`;
}

function emitCpp(dialects, rules) {
  const out = [];
  const rows = [];

  Object.keys(dialects).forEach((name, i) => {
    const d = dialects[name];
    const p = cppIdent(name, i);
    out.push('', `// --- ${name} ${'-'.repeat(Math.max(3, 72 - name.length))}`);

    const lexRows = [];
    Object.entries(d.lexical).forEach(([k, v], j) => {
      if (v === null) {
        lexRows.push(`    {.key = ${cppStr(k)}, .kind = LexKind::Withdrawn},`);
      } else if (typeof v === 'object') {
        const arr = `${p}_esc${j}`;
        out.push(`constexpr Escape ${arr}[] = {`);
        for (const [from, to] of Object.entries(v)) {
          out.push(`    {.from = ${cppStr(from)}, .to = ${cppStr(to)}},`);
        }
        out.push('};');
        lexRows.push(`    {.key = ${cppStr(k)}, .kind = LexKind::Escapes, ` +
                     `.escapes = ${arr}},`);
      } else {
        lexRows.push(`    {.key = ${cppStr(k)}, .kind = LexKind::Text, ` +
                     `.text = ${cppStr(v)}},`);
      }
    });
    let lexName = '{}';
    if (lexRows.length) {
      lexName = `${p}_lexical`;
      out.push(`constexpr Lexical ${lexName}[] = {`, ...lexRows, '};');
    }

    const secNames = {};
    for (const section of ['ops', 'funcs', 'skel']) {
      const entries = Object.entries(d[section]);
      if (!entries.length) { secNames[section] = null; continue; }
      const entryRows = entries.map(([k, e], j) =>
        '    ' + cppEntry(k, e, p, section, j, out) + ',');
      secNames[section] = `${p}_${section}`;
      out.push(`constexpr Entry ${secNames[section]}[] = {`, ...entryRows, '};');
    }

    const f = [`.name = ${cppStr(name)}`];
    // An empty `extends` is a root, as ansi is.
    if (d.extends !== null) f.push(`.extends = ${cppStr(d.extends)}`);
    f.push(`.version = ${cppStr(d.version)}`, `.target = ${d.target}`);
    if (lexName !== '{}') f.push(`.lexical = ${lexName}`);
    for (const section of ['ops', 'funcs', 'skel']) {
      if (secNames[section]) f.push(`.${section} = ${secNames[section]}`);
    }
    rows.push(`    {${f.join(',\n     ')}},`);
  });

  // --- the vocabulary
  const rule = [];
  const svArray = (id, xs) =>
    rule.push(`constexpr std::string_view ${id}[] = {${xs.map(cppStr).join(', ')}};`);
  svArray('CAVEATS', rules.caveats);
  svArray('RET_KINDS', rules.retKinds);
  svArray('TEMPLATE_KEYS', rules.templateKeys);
  const arity = (id, obj) => {
    rule.push(`constexpr Arity ${id}[] = {`);
    for (const [k, [lo, hi]] of Object.entries(obj)) {
      rule.push(hi === null
        ? `    {.key = ${cppStr(k)}, .min = ${lo}, .unbounded = true},`
        : `    {.key = ${cppStr(k)}, .min = ${lo}, .max = ${hi}},`);
    }
    rule.push('};');
  };
  arity('OP_ARITY', rules.opArity);
  arity('FUNC_ARITY', rules.funcArity);
  const names = (id, obj, prefix) => {
    const rs = [];
    Object.entries(obj).forEach(([k, xs], i) => {
      const arr = `${prefix}${i}`;
      rule.push(`constexpr std::string_view ${arr}[] = {${xs.map(cppStr).join(', ')}};`);
      rs.push(`    {.key = ${cppStr(k)}, .names = ${arr}},`);
    });
    rule.push(`constexpr Names ${id}[] = {`, ...rs, '};');
  };
  names('VARIANTS', rules.variants, 'variant');
  names('SKEL_SLOTS', rules.skelSlots, 'slots');
  rule.push('constexpr LexType LEX_TYPES[] = {');
  for (const [k, t] of Object.entries(rules.lexicalTypes)) {
    rule.push(`    {.key = ${cppStr(k)}, .escapes = ${t === 'map'}},`);
  }
  rule.push('};');

  return `// ${BANNER('gen-sql-map.mjs').join('\n// ')}
//
// Every dialect, with its chain already flattened, so a lookup is a scan of one
// small array and nothing else. Runtime registration is what re-introduces the
// chain, and it is the only thing that does.
//
// Entirely \`constexpr\`: this file contributes .rodata and no static
// constructor, and --gc-sections drops all of it from a link that never calls
// shipped_map(). That is the whole reason the C++ host has a generated source
// file rather than a dialect document it reads at startup -- an application
// deploying SEL ships an executable, not an executable plus a data directory.

#include "sel_sql_map.hpp"

namespace sel::sql {
namespace {
${out.join('\n')}

// --- the map ----------------------------------------------------------------

constexpr Dialect DIALECTS[] = {
${rows.join('\n')}
};

// --- the map's own vocabulary -----------------------------------------------
//
// So that Map::define can enforce at registration time what this generator
// enforces at generation time. Emitted rather than retyped in each host: every
// divergence a cross-host review found in runtime registration -- an entry with
// no "ret", a "tpl" that was a JSON list, an "arity" of strings, a caveat
// somebody invented -- was an entry the generator would have rejected and the
// runtime would not, after which the hosts improvised differently. Improvising
// is what code does when it has no rule; this is the rule, as data.

${rule.join('\n')}

constexpr Rules RULES = {
    .caveats = CAVEATS,
    .ret_kinds = RET_KINDS,
    .template_keys = TEMPLATE_KEYS,
    .op_arity = OP_ARITY,
    .func_arity = FUNC_ARITY,
    .variants = VARIANTS,
    .skel_slots = SKEL_SLOTS,
    .lexical_types = LEX_TYPES,
};

}  // namespace

std::span<const Dialect> shipped_map() { return DIALECTS; }
const Rules& shipped_rules() { return RULES; }

}  // namespace sel::sql
`;
}

// --- main ------------------------------------------------------------------

// The dialect documents as they are WRITTEN -- chain not flattened -- in an
// order where a parent always precedes its children, so they can be replayed
// through defineDialect and define in one pass.
//
// This is harness data, not library data: it goes to each host's bin/ the way
// the generated case table does, because no shipped code reads it. What reads it
// is the replay check, which asserts the property sql/MAP.md §4.5¼ states -- that
// anything the shipped map contains, an application could have registered -- and
// that property is what lets a host emit its map as code rather than carry a
// file to parse.
function rawInOrder(docs) {
  const out = [];
  const done = new Set();
  while (out.length < docs.size) {
    const before = out.length;
    for (const [name, doc] of docs) {
      if (done.has(name)) continue;
      const parent = doc.extends ?? null;
      if (parent === null || done.has(parent)) { out.push(doc); done.add(name); }
    }
    // A cycle would otherwise spin here. validate() rejects one, but a generator
    // that hangs is a worse way to find out than one that says so.
    if (out.length === before) {
      fail('sql/dialects', 'the extends chain has a cycle; cannot order the documents');
      break;
    }
  }
  return out;
}

const REPLAY_NOTE = [
  'The dialect documents as written, parents first, for the replay check.',
  '',
  'Not library data and not shipped: this is the input the generator read, kept',
  'in the harness so the check can register it through the public API and diff',
  'the result against the flattened map beside it. sql/MAP.md §4.5¼ is the rule',
  'it proves -- anything the shipped map contains, an application could have',
  'registered -- and that is what lets a host emit its map as code rather than',
  'ship a file to parse.',
];

function emitReplayPhp(dialects, rules, raw) {
  const body = raw.map((d) => `        ${phpValue(d, 8)},`).join('\n');
  return `<?php
// ${BANNER('gen-sql-map.mjs').join('\n// ')}
//
// ${REPLAY_NOTE.join('\n// ')}

declare(strict_types=1);

namespace Sel\\Sql;

final class MapReplay
{
    /** @return list<array<string,mixed>> */
    public static function raw(): array
    {
        return [
${body}
        ];
    }
}
`;
}

function emitReplayPython(dialects, rules, raw) {
  const q = '"'.repeat(3);
  return `${q}${BANNER('gen-sql-map.mjs').join('\n')}

${REPLAY_NOTE.join('\n')}
${q}

from __future__ import annotations

from typing import Any

RAW: list[dict[str, Any]] = ${pyValue(raw, 0)}
`;
}

function emitReplayJs(dialects, rules, raw) {
  return `// ${BANNER('gen-sql-map.mjs').join('\n// ')}
//
// ${REPLAY_NOTE.join('\n// ')}

export const RAW = ${JSON.stringify(raw, null, 2)};
`;
}

const OUTPUTS = [
  ['php/src/Sql/MapData.php', emitPhp],
  ['python/sel/sql/_map.py', emitPython],
  ['js/src/sql/_map.mjs', emitJs],
  ['cpp/sel_sql_map_data.cpp', emitCpp],
  ['php/bin/MapReplay.php', emitReplayPhp],
  ['python/bin/map_replay.py', emitReplayPython],
  ['js/bin/map-replay.mjs', emitReplayJs],
];

const docs = load();
const dialects = {};
for (const name of [...docs.keys()].sort()) {
  const flat = flatten(docs, name);
  validate(flat);
  dialects[name] = flat;
}

if (errors.length) {
  process.stderr.write('gen-sql-map: the dialect map is not valid\n\n');
  for (const e of errors) process.stderr.write(`  ${e}\n`);
  process.stderr.write(`\n${errors.length} problem(s); see sql/MAP.md\n`);
  process.exit(1);
}

const check = process.argv.includes('--check');
let stale = 0;
const rules = buildRules(dialects);
const raw = rawInOrder(docs);
for (const [rel, emit] of OUTPUTS) {
  const path = resolve(ROOT, rel);
  const text = emit(dialects, rules, raw);
  if (check) {
    let have = null;
    try { have = readFileSync(path, 'utf8'); } catch { /* absent counts as stale */ }
    if (have !== text) { process.stderr.write(`stale: ${rel}\n`); stale++; }
  } else {
    writeFileSync(path, text);
    process.stdout.write(`wrote ${rel}\n`);
  }
}

if (check) {
  if (stale) {
    process.stderr.write('\nrun: node tools/gen-sql-map.mjs\n');
    process.exit(1);
  }
  process.stdout.write(`sql map is current — ${Object.keys(dialects).length} dialect(s)\n`);
}
