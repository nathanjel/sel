// How an application says where a SEL variable lives in the schema.
//
// Constructed in code, never decoded from a document. That is the whole point:
// this layer used to take a nested map shaped like JSON and validate it by hand,
// and a cross-host review found the hosts disagreeing about what a malformed one
// meant — `from: ["order_items"]` was refused by PHP and spliced into an
// identifier by Python; `items` as an object was accepted by one and refused by
// the other. None of that was a decision anybody made; it was `json_decode`'s
// shape rules on one side and Python's on the other, leaking into the translator.
//
// A typed constructor makes the whole class unrepresentable rather than
// refusable. An application whose bindings come from a schema file generates
// these calls; SEL parses nothing.
//
// **The checks are in the bodies rather than in the annotations, deliberately.**
// PHP would enforce a `string` parameter and refuse a list with a TypeError;
// Python's annotations enforce nothing at run time, JS has no types to declare,
// and Lisp's are advisory. A guarantee written as a signature is a guarantee
// three of the six hosts do not make. Written in the body it is the same refusal,
// with the same code, everywhere — and `SqlError` is the class an application
// catches, where a `TypeError` is not.
//
// See docs/SQL-TRANSLATION.md §5.

import * as D from '../decimal.mjs';
import { asciiUpper } from '../lexer.mjs';
import { Value, quoteDump } from '../value.mjs';
import { SqlError } from './errors.mjs';
import { KINDS as FRAGMENT_KINDS } from './fragment.mjs';

// A validated, normalised binding record.
//
// `spec` is what the translator reads. Nothing outside this module builds one,
// and the factories below are the only way in.
export class Binding {
  constructor(spec) {
    this.spec = spec;
  }

  // --- the four kinds ------------------------------------------------------

  // One column, optionally qualified by a table, optionally typed.
  //
  // `type` is what the kind guards read. Leaving it UNKNOWN is honest and no
  // longer free: an undeclared operand passes the guards that name a kind as
  // impossible — a numeric position then wraps it rather than trusting it, or
  // refuses where the dialect cannot ask — but it is refused wherever a BOOL is
  // required, because nothing in SQL can ask a column whether it is one. A column
  // that a condition or a boolean operator will read has to say BOOL here; see
  // Translator.requireBool and Fragment.asCondition.
  static column(column, table = null, type = 'UNKNOWN', exact = false, sargable = false, guard = false, collation = null) {
    checkName('column', column);
    if (table !== null && table !== undefined) checkName('table', table);
    checkType(type);
    if (collation !== null && collation !== undefined) {
      const [cExact, cSargable] = checkCollation(collation);
      exact = exact || cExact;
      sargable = sargable || cSargable;
    }
    checkBool("a column binding's exact flag", exact);
    checkBool("a column binding's sargable flag", sargable);
    checkBool("a column binding's guard flag", guard);
    return new Binding({
      kind: 'column', column, table: table ?? null, type,
      exact: Boolean(exact), sargable: Boolean(sargable), guard: Boolean(guard)
    });
  }

  // A column expressed as SQL this layer will not read.
  //
  // The one place an application writes SQL here. It is emitted verbatim, so
  // whatever it contains is the application's promise rather than this layer's —
  // which is exactly why it is a named constructor and not a key somebody can
  // leave in a map by accident.
  static raw(sql, type = 'UNKNOWN', exact = false, sargable = false, guard = false, collation = null) {
    checkString('a raw column binding', sql);
    if (sql === '') throw new SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty');
    checkType(type);
    if (collation !== null && collation !== undefined) {
      const [cExact, cSargable] = checkCollation(collation);
      exact = exact || cExact;
      sargable = sargable || cSargable;
    }
    checkBool("a raw column binding's exact flag", exact);
    checkBool("a raw column binding's sargable flag", sargable);
    checkBool("a raw column binding's guard flag", guard);
    return new Binding({
      kind: 'column', raw: sql, type,
      exact: Boolean(exact), sargable: Boolean(sargable), guard: Boolean(guard)
    });
  }

  // An ordered set of columns, iterated by an aggregate and indexed by position:
  // the first is `V[1]`.
  static columns(...items) {
    if (items.length === 0) {
      throw new SqlError('E_SQL_BINDING', 'a columns binding needs at least one column');
    }
    const out = [];
    items.forEach((item, i) => {
      if (!(item instanceof Binding) || item.spec.kind !== 'column') {
        const kind = item instanceof Binding ? item.spec.kind : typeName(item);
        throw new SqlError('E_SQL_BINDING',
          `a columns binding takes column bindings, and item ${i + 1} is a ${kind}`);
      }
      out.push(item.spec);
    });
    return new Binding({ kind: 'columns', items: out });
  }

  // A set of rows, rendered as a correlated subquery.
  //
  // `fields` maps a SEL key to a column binding; the keys are upper-cased here,
  // once, so every consumer looks one up the same way. `scalar` names the field a
  // bare reference means, and only a ONE-field relation may declare it — a wider
  // row is a map in SEL, and a map is not the value of one of its fields.
  //
  // `correlate` is SQL, like `raw()`, and joins the subquery back to the outer
  // row. Without it the subquery is over the whole table, which is legal and
  // occasionally what you want.
  static relation(from, alias = null, fields = null, scalar = null, correlate = null) {
    checkName('from', from);
    if (alias !== null && alias !== undefined) checkName('alias', alias);
    return makeRelation({ kind: 'relation', from }, alias, fields, scalar, correlate);
  }

  // The same, over a query the application writes rather than a table.
  static relationQuery(query, alias = null, fields = null, scalar = null, correlate = null) {
    checkString('a relation query', query);
    if (query === '') throw new SqlError('E_SQL_BINDING', 'a relation query cannot be empty');
    if (alias !== null && alias !== undefined) checkName('alias', alias);
    return makeRelation({ kind: 'relation', from: { raw: query } },
      alias, fields, scalar, correlate);
  }

  // A constant the application supplies, inlined as a literal.
  //
  // Takes a Value, never a native number or string, and that is the fix for the
  // last cross-host divergence here: PHP's `json_decode` turns a 20-digit integer
  // into a float, Python keeps it exact, and JS cannot tell `1.0` from `1`.
  // Asking the caller for a Value moves the decision to the line that knows the
  // answer.
  //
  // `type` is NUM or nothing. It decides whether the value is emitted quoted,
  // which is a question no inspection can settle: SEL numbers ARE text values
  // (spec §4), so `Value.num('5.00')` and `Value.text('5.00')` are one object.
  static value(v, type = null) {
    if (!(v instanceof Value)) {
      throw new SqlError('E_SQL_BINDING',
        `a value binding takes a Value, and this is ${typeName(v)}; build one with `
        + 'Value.num(), .text(), .bool(), .bin() or .list()');
    }
    if (type !== null && type !== undefined && type !== 'NUM') checkType(type);
    if (type === 'NUM') checkNumeric('this value binding', v);
    return new Binding({ kind: 'value', type: type ?? null, value: v });
  }
}

export function typeName(v) {
  if (v === null) return 'null';
  if (v === undefined) return 'undefined';
  if (Array.isArray(v)) return 'list';
  if (typeof v === 'object') return v.constructor ? v.constructor.name : 'object';
  return typeof v;
}

// --- internals ---------------------------------------------------------------

function makeRelation(base, alias, fields, scalar, correlate) {
  const f = fields ?? {};
  if (f === null || typeof f !== 'object' || Array.isArray(f)) {
    throw new SqlError('E_SQL_BINDING',
      'the fields of a relation binding must be a map of name to column binding, '
      + `and this is ${typeName(fields)}`);
  }
  if (scalar !== null && scalar !== undefined) checkString("a relation binding's scalar", scalar);
  if (correlate !== null && correlate !== undefined) {
    checkString("a relation binding's correlate", correlate);
  }
  // Object.create(null), never `{}`: a field named `__proto__` assigned into an
  // ordinary object literal sets the prototype instead of a property, and the
  // field names come from the application.
  const out = Object.create(null);
  const pairs = f instanceof Map ? [...f] : Object.entries(f);
  for (const [name, b] of pairs) {
    if (!(b instanceof Binding) || b.spec.kind !== 'column') {
      throw new SqlError('E_SQL_BINDING',
        `the field ${name} of a relation binding must be a column binding`);
    }
    // asciiUpper, matching PHP's strtoupper: toUpperCase would fold "ß" to "SS"
    // and change the key's length.
    out[asciiUpper(String(name))] = b.spec;
  }
  if (scalar !== null && scalar !== undefined && !Object.hasOwn(out, asciiUpper(scalar))) {
    throw new SqlError('E_SQL_BINDING',
      `a relation binding names ${scalar} as its scalar, which is not one of its fields`);
  }
  const spec = { ...base, alias: alias ?? null, fields: out };
  if (scalar !== null && scalar !== undefined) spec.scalar = scalar;
  if (correlate !== null && correlate !== undefined) spec.correlate = { raw: correlate };
  return new Binding(spec);
}

function checkString(what, v) {
  if (typeof v !== 'string') {
    throw new SqlError('E_SQL_BINDING', `${what} must be a string, and this is ${typeName(v)}`);
  }
}

// An identifier the application supplied has to survive being quoted.
//
// `Emit.ident` doubles the quote character and passes everything else through,
// which is right for every character but two. A NUL terminates the C string libpq
// and sqlite3 are handed, so `a\0b` is malformed SQL on all four servers rather
// than a column nobody has. An empty name quotes to `""`, which PostgreSQL
// rejects and the other three accept — a divergence with no upside.
function checkName(what, v) {
  checkString(`a binding's ${what}`, v);
  if (v === '') throw new SqlError('E_SQL_BINDING', `a binding has an empty ${what} name`);
  if (v.includes('\0')) {
    throw new SqlError('E_SQL_BINDING',
      `a binding has a ${what} name containing a NUL, which no dialect can quote`);
  }
}

function checkType(t) {
  if (typeof t !== 'string' || !FRAGMENT_KINDS.includes(t)) {
    throw new SqlError('E_SQL_BINDING',
      `a binding has type ${t}; use one of ${FRAGMENT_KINDS.join(', ')}`);
  }
}

// Every scalar reachable from a NUM-typed value binding.
function checkNumeric(where, v) {
  if (v.size() > 0) {
    for (const [k, child] of v.entries()) checkNumeric(`${where}["${k}"]`, child);
    return;
  }
  if (v.isNone()) return;
  if (!v.isText() || !v.looksNumeric()) {
    const shown = v.isBool() ? (v.asBool() ? 'TRUE' : 'FALSE') : v.asText();
    throw new SqlError('E_SQL_BINDING',
      `${where} declares type NUM, which asks for it to be emitted unquoted, but `
      + `${quoteDump(shown)} is not a number`);
  }
  // looksNumeric is broader than canonical, and emit's numericLiteral emits
  // decimal.format's output rather than the caller's characters — correct for the
  // AST path, where the lexer has already canonicalised, and wrong here, where the
  // application supplied the string and the evaluator was handed that same
  // string. "007" translated to 7 while SEL kept "007".
  const text = v.asText();
  if (D.format(D.parse(text)) !== text) {
    throw new SqlError('E_SQL_BINDING',
      `${where} declares type NUM and is ${quoteDump(text)}, which is not how SEL `
      + 'writes that number; a NUM binding is emitted unquoted and must already be '
      + 'canonical, so pass it as text or drop the leading zeros');
  }
}

function checkBool(what, v) {
  if (typeof v !== 'boolean') {
    throw new SqlError('E_SQL_BINDING',
      `${what} must be a boolean, and this is ${typeName(v)}`);
  }
}

function checkCollation(c) {
  if (c === null || c === undefined) return [false, false];
  if (typeof c !== 'string') {
    throw new SqlError('E_SQL_BINDING',
      `collation must be a string, and this is ${typeName(c)}`);
  }
  const lower = c.toLowerCase();
  if (lower === 'binary' || lower === 'exact') return [true, false];
  if (lower === 'sargable' || lower === 'prefilter') return [false, true];
  if (lower === 'default' || lower === 'none') return [false, false];
  throw new SqlError('E_SQL_BINDING',
    `unknown collation '${c}'; use 'binary', 'exact', 'sargable', or 'default'`);
}
