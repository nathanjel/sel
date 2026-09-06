// The result of a translation: a part list, its bound values, and the static
// kind it produces.

import * as emit from './emit.mjs';
import * as map from './map.mjs';
import { refuse } from './errors.mjs';

export const KINDS = ['NUM', 'TEXT', 'BOOL', 'BIN', 'UNKNOWN', 'LIST'];

// A rendered SQL expression.
//
// `parts` alternates finished SQL and parameter slots — a string is SQL, a
// number is the 1-based index of a value in `params`. The renderer never
// concatenates a literal into a string, so `inline` and `params` output are two
// ways of joining one structure rather than two code paths. A part list cannot
// be confused about where a literal ends, whatever the literal contains, and
// that is the class of bug this shape exists to make unreachable.
export class Fragment {
  constructor(parts, kind, dialect, params = null, paramKinds = null, caveats = null) {
    this.parts = parts;
    this.kind = kind;
    this.dialect = dialect;
    this.params = params ?? [];
    // The literal form of each slot: NUM, TEXT, BOOL or BIN, parallel to
    // params. Kept beside the values rather than derived from them because it
    // cannot be derived — see emit.literal.
    this.paramKinds = paramKinds ?? [];
    this.caveats = caveats ?? [];
  }

  // Usable in a select list, GROUP BY, ORDER BY or HAVING. Any kind but LIST,
  // which is not a SQL value at all.
  asValue(mode = 'inline') {
    if (this.kind === 'LIST') {
      refuse('E_SQL_SHAPE',
        'this expression yields a list, and a SQL expression is a scalar');
    }
    return this.#join(mode);
  }

  // Usable as a condition.
  //
  // BOOL as it stands; UNKNOWN wrapped in the dialect's IS TRUE test, since a
  // column of unknown type may be NULL and SEL has no third truth value to give
  // back.
  //
  // A NUM or TEXT fragment is refused rather than accepted. Silently allowing
  // `WHERE o.total` is how a database turns a validation rule into the
  // truthiness test SEL spent its whole design avoiding.
  asCondition(mode = 'inline') {
    if (this.kind === 'BOOL') return this.#join(mode);
    if (this.kind === 'UNKNOWN') {
      const tpl = map.lexical(this.dialect, 'isTrue');
      return tpl.replace('{0}', this.#join(mode));
    }
    refuse('E_SQL_SHAPE',
      `a condition must be BOOL, and this expression is ${this.kind}; `
      + 'SQL has no truthiness and neither does SEL');
  }

  // The bound values for `params` mode, in placeholder order.
  //
  // Derived from the part list rather than returned as stored, because the two
  // orders are not the same. A slot is numbered when it is created, and the
  // template decides where it lands: `FIND(needle, hay)` maps to
  // `INSTR({1}, {0})`, so the second slot created is the first one emitted. A
  // positional `?` carries no number, so a driver binds the first value to the
  // first placeholder — which is right only if this walks the output.
  //
  // A slot appearing more than once yields its value more than once, which is
  // also right: two placeholders need two bindings, even of the same value.
  bindings() {
    return this.parts
      .filter((p) => typeof p !== 'string' && !this.#isInline(p))
      .map((p) => this.params[p - 1]);
  }

  // True for a slot rendered as a literal in every mode, never as a parameter.
  //
  // Three forms qualify, for the same underlying reason: **none carries any
  // character the caller chose**, so there is nothing for a placeholder to
  // protect, and each is damaged by being sent as a string.
  //
  // NUM, because no coercion of a bound string reproduces a bare numeric
  // literal. MariaDB reads `2.50` as DECIMAL with scale 2 and
  // `12345678901234567890.12345` as DECIMAL with 25 digits; a parameter is
  // untyped, and every way of giving it a type picks the wrong one.
  // `CAST(? AS DECIMAL(65,10))` pads the scale, so `TRIM(2.50)` answered
  // "2.5000000000". `(? + 0)` drops the scale and floats above seventeen digits.
  // With neither, `(? = ?)` compares two strings and 2.50 = 2.5 is FALSE. After
  // emit.numericLiteral the characters a NUM literal can contain are digits, one
  // `.` and a leading `-`, by construction.
  //
  // BOOL, because the token is `map.lexical(dialect, 'true'|'false')` — it comes
  // out of the dialect document, not out of a rule. Binding it as a string breaks
  // SQLite outright: `1 = '1'` is **0** there, since INTEGER and TEXT are
  // different storage classes and no affinity applies to a bare parameter, so
  // `TRUE XOR TRUE` answered TRUE in params mode and FALSE inline. Found by the
  // fuzz lane on sqlite's first run.
  //
  // BIN, because a BIN parameter is bytes and a driver sends them through the
  // connection's text encoding: on PostgreSQL 130 of the 256 single-byte values
  // then failed — 129 as `22021 invalid byte sequence for encoding "UTF8"` and
  // 0x00 silently — while the same values inlined through `binaryLiteral` were
  // correct on all four dialects, all 256. A host cannot work around it:
  // bindings() hands back Values, and the cast wrapping the placeholder is what
  // breaks it.
  //
  // Everything else is still bound.
  #isInline(slot) {
    const kind = slot - 1 < this.paramKinds.length ? this.paramKinds[slot - 1] : 'TEXT';
    return kind === 'NUM' || kind === 'BOOL' || kind === 'BIN';
  }

  // True when nothing about this translation is inexact.
  isExact() {
    return this.caveats.length === 0;
  }

  #join(mode) {
    const out = [];
    let nth = 0;                       // position in bindings(), not slot id
    for (const p of this.parts) {
      if (typeof p === 'string') { out.push(p); continue; }
      const kind = p - 1 < this.paramKinds.length ? this.paramKinds[p - 1] : 'TEXT';
      if (mode !== 'inline' && this.#isInline(p)) {
        // Never a placeholder; see #isInline. It does not advance nth either,
        // because it emits no placeholder for a binding to land in.
        out.push(emit.literal(this.dialect, this.params[p - 1], kind));
        continue;
      }
      nth += 1;
      if (mode === 'inline') {
        out.push(emit.literal(this.dialect, this.params[p - 1], kind));
      } else if (mode === 'params') {
        // The ordinal a numbered placeholder carries — PostgreSQL's $n — must
        // agree with bindings(), which walks the output. The slot id would not:
        // it is a creation number, and a reordering template emits creation
        // numbers out of order.
        out.push(emit.placeholder(this.dialect, nth));
      } else if (mode === 'debug') {
        out.push(`~${nth}~`);
      } else {
        throw new Error(`unknown render mode ${mode}; use inline, params or debug`);
      }
    }
    return out.join('');
  }
}
