// Everything that turns a value or a template into characters. The one place
// quoting happens, so there is one place to get it right.

import * as D from '../decimal.mjs';
import { Value, quoteDump } from '../value.mjs';
import { bytesToHex } from '../utf8.mjs';
import * as map from './map.mjs';
import { refuse } from './errors.mjs';
// fragment.mjs imports this module back. ESM resolves the cycle because neither
// side touches the other's binding while the modules are still evaluating —
// Fragment is referenced only inside method bodies, which run long afterwards.
// Python needs a function-local import here for the same reason; ESM does not.
import { Fragment } from './fragment.mjs';

// Template slots are 0-based and canonical: {0}, {1}, {0:}. `{01}` and `{1\n}`
// are not slots, and the generator (tools/gen-sql-map.mjs) already refuses both
// — JS's `$` matches only at end of string — so the shipped map and a
// runtime-registered template were being read by two different grammars. A
// full match and a three-digit cap close that: one grammar, and no host's
// integer parser is consulted.
const SLOT = /^(0|[1-9][0-9]{0,2})$/;

// The argument a template slot names, or null when it names none.
function slotIndex(s) {
  return SLOT.test(s) ? Number(s) : null;
}

// Substitute a template slot, the way every other host's string replace already
// works and JS's does not.
//
// `String.prototype.replace(string, string)` gets this wrong TWICE, and both
// mistakes are silent:
//
//   1. It replaces only the FIRST occurrence. Python's str.replace and PHP's
//      str_replace replace every one, so a template naming {0} twice bound the
//      second copy to nothing here and to the operand there.
//   2. The REPLACEMENT string is not literal: $$, $&, $`, $' and $1-$9 are
//      substitution directives even when the pattern is a plain string. The
//      replacement here is rendered SQL carrying caller-supplied identifiers, so
//      a column named `a$'b` spliced the template's own tail back into the
//      output and produced a different query. `replaceAll` does NOT fix this —
//      it is global but still interprets the dollar patterns.
//
// split/join is literal and global, which is exactly str.replace's contract.
// Emit.ident already used this idiom for the same reason; these sites did not.
export function fillSlot(tpl, slot, value) {
  return tpl.split(slot).join(value);
}

// --- literals ----------------------------------------------------------------

// A SEL value as a SQL literal, in the form the caller says it has.
//
// A module function so Fragment can join without holding an emitter, which keeps
// a Fragment a plain data object.
//
// The form is passed in and never inferred, because it cannot be inferred: SEL
// numbers *are* TEXT values (spec §4), so `Value.num('5.00')` and
// `Value.text('5.00')` are the same object and no predicate can tell "the author
// wrote 5.00" from "the author wrote \"5.00\"". Only the AST knows, and it is the
// AST that tells us.
//
// Getting this wrong is not cosmetic. Emitted bare, `"5.00" $== "5"` becomes
// `5.00 = 5`, which the database answers TRUE and SEL answers FALSE.
export function literal(dialect, v, form = 'TEXT', pos = null) {
  if (form === 'BOOL' || v.isBool()) {
    return String(map.lexical(dialect, v.asBool(pos) ? 'true' : 'false'));
  }
  if (form === 'BIN' || v.isBin()) {
    const tpl = map.lexical(dialect, 'binaryLiteral');
    if (typeof tpl !== 'string') {
      refuse('E_SQL_UNSUPPORTED', `dialect ${dialect} has no binary literal syntax`, pos);
    }
    return fillSlot(tpl, '{hex}', bytesToHex(v.asBytes(pos)));
  }
  // A NONE value has no characters, and asking for them raises a SelError —
  // which tryTranslate() does not catch, so a host using the refusal-tolerant
  // API got a fatal out of asValue() rather than null. Reachable from ordinary
  // host data: {"kind": "value", "value": []} is an empty result set. Standing
  // alone the variable is refused as a LIST, but as an operand the result kind
  // comes from the template and the LIST-ness is gone by the time anything looks.
  if (v.isNone()) {
    refuse('E_SQL_BINDING',
      'a value binding holding no value cannot be a SQL literal; only an '
      + 'aggregate can be given an empty binding', pos);
  }
  if (form === 'NUM') return numericLiteral(dialect, v, pos);
  return textLiteral(dialect, v.asText(pos));
}

// The only unquoted output in the layer.
//
// A NUM literal is the one thing emitted without quotes, which makes it the one
// thing that has to be a number. The AST path arrives already parsed, but a
// `value` binding declaring `type: NUM` reaches here straight from host data,
// and `"1 OR 1=1 -- "` would go out verbatim. Fragment's part list keeps a
// literal from being confused with SQL; it cannot keep a literal from BEING SQL.
//
// What is emitted is what the parse recovered — `decimal.format`'s output —
// rather than the text the caller supplied. The two agree for everything the
// parser produces, and the difference is the point: proving a string is a number
// and then emitting a *different* string is a gap, however small, and the gap is
// where "1 OR 1=1" lived. After this the characters that can leave here are
// digits, one `.` and a leading `-`, by construction.
function numericLiteral(dialect, v, pos) {
  const text = v.asText(pos);
  const d = D.parse(text);
  if (d === null) {
    refuse('E_SQL_BINDING',
      `a value bound as NUM must be a number, and ${quoteDump(text)} is not`, pos);
  }
  const n = D.format(d);

  // How the dialect spells a number is the dialect's business, and one of them
  // has to spell it as text. SQLite has no exact decimal: 2.50 is a REAL that
  // prints as 2.5, so `2.50 $== 2.5` would be TRUE there and FALSE in SEL.
  // Quoted, the exact characters survive, and SQLite's dynamic typing reads them
  // as a number wherever a number is wanted. Applied AFTER format, so the
  // digits-by-construction guarantee is unaffected: it decides how to spell a
  // number that has already been proved to be one.
  const wrap = map.lexical(dialect, 'numericLiteral');
  if (typeof wrap === 'string' && wrap !== '{0}') return fillSlot(wrap, '{0}', n);

  // A negative number is parenthesised so that unary minus in front of it cannot
  // produce `--`. MariaDB reads that as double negation and gets the right answer
  // by luck; PostgreSQL and SQLite read it as the start of a line comment and the
  // rest of the expression disappears. Only reachable through a `value` binding,
  // since the parser never produces a signed `num` node.
  return n.startsWith('-') ? `(${n})` : n;
}

export function textLiteral(dialect, text) {
  const quote = String(map.lexical(dialect, 'textQuote'));
  const escape = map.lexical(dialect, 'textEscape');
  let out = text;
  if (escape !== null && typeof escape === 'object' && !Array.isArray(escape)) {
    // Longest first, so a rule for "\\" is applied before one for "\". A single
    // left-to-right pass, never one replace per rule: replacing "'" with "''"
    // and then "\" with "\\" would rewrite the output of the first rule.
    const keys = Object.keys(escape).sort((a, b) => b.length - a.length);
    const buf = [];
    let i = 0;
    while (i < out.length) {
      let hit = null;
      for (const k of keys) {
        if (k && out.startsWith(k, i)) { hit = k; break; }
      }
      if (hit !== null) {
        buf.push(String(escape[hit]));
        i += hit.length;
      } else {
        buf.push(out[i]);
        i += 1;
      }
    }
    out = buf.join('');
  }
  return quote + out + quote;
}

// The params-mode placeholder for slot n, 1-based.
export function placeholder(dialect, n) {
  const tpl = String(map.lexical(dialect, 'placeholder'));
  return tpl.includes('{n}') ? fillSlot(tpl, '{n}', String(n)) : tpl;
}

// The dialect-bound half: identifiers, templates, and the byte-comparison
// operand. The literal functions above are free because Fragment needs them
// without an emitter.
export class Emit {
  constructor(dialect) {
    this._dialect = dialect;
  }

  dialect() { return this._dialect; }

  lex(key) { return map.lexical(this._dialect, key); }

  // An operand a numeric context will read as a number, made safe to read.
  //
  // SEL raises E_NOT_NUM for text that is not a number, and the server does not:
  // CAST('x' AS DECIMAL) is 0 on MariaDB, MySQL and SQLite, so a rule comparing
  // against 0 matched every row of a text column. Wrapping the operand so a
  // non-number becomes NULL keeps the warrant — NULL is not selected, which is
  // what SEL failing has to look like from SQL.
  //
  // Not applied to a NUM operand: the binding said it is a number, and that
  // declaration is where the promise transfers. It is also the only way to keep
  // the index, since the guard is a function of the column.
  //
  // The pattern is SEL's own numeral grammar and lives in the map beside
  // funcs.ISNUM, which asks the same question; tools/gen-sql-map.mjs requires the
  // two to agree. A dialect that cannot ask it — sqlite has no REGEXP, ansi has
  // no regex — declares no numericGuard, and this refuses rather than emitting
  // something that answers when SEL would not.
  numericOperand(f, pos = null) {
    if (f.kind === 'NUM' && !f.guard) return f;
    map.checkNumericGuard(this._dialect);
    const guard = this.lex('numericGuard');
    if (typeof guard !== 'string') {
      refuse('E_SQL_UNSUPPORTED',
        `dialect ${this._dialect} has no way to ask whether a value is a number, `
        + 'so an operand it has not been told is one cannot be read as one here; '
        + 'declare the binding NUM if the column really is numeric', pos);
    }
    return new Fragment(this.fill(guard, [f], pos), 'NUM', this._dialect);
  }

  // An operand of a byte comparison: cast to a character type, then given the
  // dialect's binary collation.
  //
  // Both halves are needed and neither is enough alone. Without the collation
  // MariaDB's default is case-insensitive, so `"A" $== "a"` is true there and
  // false in SEL. Without the cast the collation does not stop two numeric
  // operands being compared as numbers, so `3.0 EQL 3` is true there and false in
  // SEL — EQL is structural and does not normalise numbers.
  //
  // Applied here rather than in the templates because three places need it — two
  // operand comparisons, IN over a list, and the inRelation skeleton — and only
  // one of those is a two-operand template.
  textOperand(f) {
    if (f.exact) return f;
    const cast = this.lex('textCast');
    const collate = String(this.lex('textCollate') ?? '');
    let parts = f.parts;

    if (typeof cast === 'string' && cast !== '{0}') parts = this.fill(cast, [f]);
    if (collate !== '') parts = [...parts, collate];
    return new Fragment(parts, 'TEXT', this._dialect, f.params, f.paramKinds, f.caveats);
  }

  // --- identifiers ---------------------------------------------------------

  // A table or column name, quoted.
  //
  // The quote character is doubled — or whatever `identEscape` says — inside the
  // name, which is what stops a binding naming a column `a"b` from ending the
  // identifier early.
  ident(name) {
    const q = String(this.lex('identQuote'));
    const e = String(this.lex('identEscape'));
    return q + name.split(q).join(e) + q;
  }

  // `table`.`column`, or just the column when no table was given.
  column(table, column) {
    if (table === null || table === undefined || table === '') return this.ident(column);
    return `${this.ident(table)}.${this.ident(column)}`;
  }

  // --- templates -----------------------------------------------------------

  // Fill a template with already-rendered arguments, producing a part list.
  //
  // Splicing part lists rather than strings is the whole point: an argument
  // carrying parameter slots keeps them. Concatenating the arguments into strings
  // first would work exactly until a literal contained something that looked like
  // a placeholder.
  //
  // Slot numbers are **absolute from the moment the literal is created** — one
  // translation has one parameter vector, held by the Translator, and an
  // intermediate Fragment carries indices into it rather than a vector of its
  // own. So splicing copies slots verbatim and never renumbers. The alternative,
  // every Fragment owning its own params and being renumbered on each splice, is
  // the same information arranged so that one missed renumbering silently binds
  // the wrong value to the wrong placeholder.
  //
  // `{key}` and `{key:n}` lexical forms are expanded here too. The generator has
  // already done that for the shipped map; this is for entries an application
  // registers at run time, which never pass through it.
  // `expanding` is the set of lexical keys this call is already inside. A
  // lexical value may reference another lexical key, and nothing stopped one
  // from referencing itself: a dialect registering
  // `{textCast: 'X({textCast:0})'}` recursed until the host died -- RangeError
  // here, RecursionError on Python, a host crash through the public API either
  // way, which is the failure every other guard in this layer exists to prevent.
  //
  // The cycle is refused rather than a depth capped, because the cycle is the
  // actual mistake and a depth cap would need a number nobody can justify. With
  // cycles refused the chain is bounded by the number of lexical keys, which is
  // fifteen.
  fill(tpl, args, pos = null, expanding = null) {
    const parts = [];

    const push = (s) => {
      if (s === '') return;
      if (parts.length && typeof parts[parts.length - 1] === 'string') {
        parts[parts.length - 1] += s;
      } else {
        parts.push(s);
      }
    };

    const splice = (f) => {
      for (const p of f.parts) {
        if (typeof p === 'string') push(p);
        else parts.push(p);          // absolute already; see the note above
      }
    };

    const join = (subset) => {
      let first = true;
      for (const f of subset) {
        if (!first) push(', ');
        first = false;
        splice(f);
      }
    };

    let i = 0;
    const nTpl = tpl.length;
    while (i < nTpl) {
      if (tpl[i] === '{' && i + 1 < nTpl && tpl[i + 1] === '{') { push('{'); i += 2; continue; }
      if (tpl[i] === '}' && i + 1 < nTpl && tpl[i + 1] === '}') { push('}'); i += 2; continue; }
      if (tpl[i] !== '{') { push(tpl[i]); i += 1; continue; }
      const end = tpl.indexOf('}', i);
      if (end === -1) { push(tpl.slice(i)); break; }
      const slot = tpl.slice(i + 1, end);
      i = end + 1;

      if (slot === '*') { join(args); continue; }
      if (slot.endsWith(':')) {
        const frm = slotIndex(slot.slice(0, -1));
        if (frm !== null) { join(args.slice(frm)); continue; }
      }
      const k = slotIndex(slot);
      if (k !== null) {
        if (k >= args.length) {
          refuse('E_SQL_UNSUPPORTED',
            `the mapping for this expression asks for argument ${k}, which it was `
            + 'not given', pos);
        }
        splice(args[k]);
        continue;
      }
      // A lexical reference, from a runtime-registered template.
      const at = slot.indexOf(':');
      const key = at === -1 ? slot : slot.slice(0, at);
      const arg = at === -1 ? null : slot.slice(at + 1);
      const val = this.lex(key);
      if (typeof val !== 'string') {
        refuse('E_SQL_UNSUPPORTED',
          `a template used {${slot}}, which is neither an argument nor a lexical `
          + `entry of dialect ${this._dialect}`, pos);
      }
      if (arg === null || arg === '') { push(val); continue; }
      // binaryCast converts a TEXT or NUM operand to bytes. An operand that is
      // already BIN needs no conversion, and on PostgreSQL converting it is
      // destructive: text::bytea parses its input as a bytea *literal*, where \\
      // is one backslash and \x41 is a byte, so the round trip changes the bytes
      // or fails the query. Every other cast is idempotent and applied
      // unconditionally; this is the one whose input kind decides whether it
      // means anything.
      if (expanding !== null && expanding.has(key)) {
        refuse('E_SQL_UNSUPPORTED',
          `the ${key} lexical entry of dialect ${this._dialect} expands into `
          + 'itself, so filling it would never finish', pos);
      }
      const castArg = key === 'binaryCast' ? slotIndex(arg) : null;
      if (castArg !== null && castArg < args.length
          && args[castArg] instanceof Fragment && args[castArg].kind === 'BIN') {
        splice(args[castArg]);
        continue;
      }
      const deeper = new Set(expanding ?? []);
      deeper.add(key);
      for (const p of this.fill(fillSlot(val, '{0}', `{${arg}}`), args, pos, deeper)) {
        if (typeof p === 'string') push(p);
        else parts.push(p);
      }
    }
    return parts;
  }
}
