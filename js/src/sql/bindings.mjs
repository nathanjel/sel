// Where a SEL variable lives in the schema.
//
// The host answers `dependencies()` with one of these per name; nothing is
// inferred, and a name with no binding is E_SQL_UNBOUND rather than a guess at a
// column.

import { asciiUpper } from '../lexer.mjs';
import { Binding } from './binding.mjs';
import { SqlError, refuse } from './errors.mjs';

export class Bindings {
  // Name -> Binding, and nothing else.
  //
  // The map-of-maps this used to take was a JSON document in all but name, and
  // validating one by hand is where the hosts diverged: PHP's `is_array` cannot
  // tell a JSON object from a JSON array, `isinstance` can, and neither
  // difference was a decision anybody made. A Binding is built by a typed
  // constructor (see binding.mjs), so the malformed shapes are unrepresentable
  // rather than refusable, and this class has nothing left to validate.
  constructor(bindings) {
    // A Map, not an object: the names come from the application, and `{}`
    // answers for every Object.prototype name.
    this._map = new Map();
    const pairs = bindings instanceof Map ? [...bindings] : Object.entries(bindings ?? {});
    for (const [name, b] of pairs) {
      if (!(b instanceof Binding)) {
        throw new SqlError('E_SQL_BINDING',
          `the binding for ${name} is a ${b === null ? 'null' : typeof b}; build one `
          + 'with Binding.column(), .columns(), .relation(), .relationQuery(), '
          + '.raw() or .value()');
      }
      // asciiUpper, not toUpperCase: the latter folds "ß" to "SS" and changes the
      // name's length, where PHP's strtoupper is ASCII-only.
      this._map.set(asciiUpper(String(name)), b.spec);
    }
  }

  has(name) {
    return this._map.has(asciiUpper(name));
  }

  get(name, pos = null) {
    const key = asciiUpper(name);
    if (!this._map.has(key)) {
      const known = [...this._map.keys()].sort();
      const tail = known.length === 0
        ? '; no bindings were given'
        : `; bound names are ${known.join(', ')}`;
      refuse('E_SQL_UNBOUND',
        `${key} is read by this rule but no binding says where it lives${tail}`, pos);
    }
    return this._map.get(key);
  }

  names() {
    return [...this._map.keys()].sort();
  }

  // A relation alias may name only one thing.
  //
  // Two relations sharing an alias in one expression would produce a subquery
  // correlated to the wrong rows, and the host chose the aliases, so the host can
  // fix them.
  checkAliases(pos = null) {
    const seen = new Map();
    for (const [name, b] of this._map) {
      if (b.kind !== 'relation') continue;
      let alias = b.alias ?? null;
      // A raw `from` is a map, and a non-string alias reached a map key in PHP
      // and raised a TypeError — not a SqlError, so tryTranslate() did not catch
      // it and a host using the refusal-tolerant API got a fatal instead of null.
      if (alias !== null && typeof alias !== 'string') {
        refuse('E_SQL_BINDING',
          `the relation binding for ${name} has an alias that is not a string`, pos);
      }
      if (alias === null) {
        const frm = b.from;
        alias = (frm !== null && typeof frm === 'object')
          ? String(frm.raw ?? '') : String(frm);
      }
      if (seen.has(alias)) {
        refuse('E_SQL_BINDING',
          `relations ${seen.get(alias)} and ${name} share the alias ${alias}; give `
          + 'each one its own', pos);
      }
      seen.set(alias, name);
    }
  }
}
