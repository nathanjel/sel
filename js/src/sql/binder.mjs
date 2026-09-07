// What an aggregate binder names for the duration of one element.
//
// Three shapes, matching the three iteration shapes of docs/SQL-TRANSLATION.md
// §7, plus one that exists only to carry a refusal — so that `_K` inside a
// relation body fails saying rows have no key, rather than falling through to the
// bindings map and being reported as an unbound variable.

const NODE = 'node';       // an element of a static list: an AST node, re-entered
const COLUMN = 'column';   // one column reference, from a `columns` binding
const ROW = 'row';         // a row of a relation: fields resolve to its columns
const NONE = 'none';       // in scope, but using it is an error with this reason

export class Binder {
  // Mirrored as static properties so `Binder.ROW` works the way `Value.BOOL`
  // does, which is the spelling every host reads the same.
  static NODE = NODE;
  static COLUMN = COLUMN;
  static ROW = ROW;
  static NONE = NONE;

  constructor(shape, payload, reason = null) {
    this.shape = shape;
    this.payload = payload;
    this.reason = reason;
  }

  static node(node) { return new Binder(NODE, node); }

  static column(column) { return new Binder(COLUMN, column); }

  static row(relation) { return new Binder(ROW, relation); }

  static none(reason) { return new Binder(NONE, null, reason); }
}
