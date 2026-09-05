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
import { lookup as selLookup } from '../js/src/registry.mjs';

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
  'unicode-case', 'division-scale', 'numeric-scale', 'decimal-float', 'rounding-mode',
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

function checkTemplate(tpl, { where, minArgs, maxArgs, allowNamed }) {
  for (const s of slots(tpl)) {
    if (s.raw === null) { fail(where, 'unclosed { in template'); continue; }
    const raw = s.raw;
    if (raw === '*') continue;
    if (/^[0-9]+$/.test(raw)) {
      const n = Number(raw);
      if (maxArgs !== Infinity && n >= maxArgs) {
        fail(where, `template uses {${n}} but the entry takes at most ${maxArgs} argument(s)`);
      }
      continue;
    }
    if (/^[0-9]+:$/.test(raw)) continue;
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

function emitPhp(dialects) {
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

function emitPython(dialects) {
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
`;
}

// --- main ------------------------------------------------------------------

const OUTPUTS = [
  ['php/src/Sql/MapData.php', emitPhp],
  ['python/sel/sql/_map.py', emitPython],
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
for (const [rel, emit] of OUTPUTS) {
  const path = resolve(ROOT, rel);
  const text = emit(dialects);
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
