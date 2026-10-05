// What an aggregate binder names for the duration of one element.
//
// Three shapes, matching the three iteration shapes of docs/internals/sql-translation.md
// §7, plus one that exists only to carry a refusal — so that `_K` inside a
// relation body fails saying rows have no key, rather than falling through to the
// bindings map and being reported as an unbound variable.

const NODE = 'node';       // an element of a static list: an AST node, re-entered
const COLUMN = 'column';   // one column reference, from a `columns` binding
const ROW = 'row';         // a row of a relation: fields resolve to its columns
const NONE = 'none';       // in scope, but using it is an error with this reason
const KEY = 'key';         // the key of the group being rendered: a group-by entry,
                           // rendered as the GROUP BY expression itself
const GROUP = 'group';     // a bucket's members, inside the bucket's own body: a list
                           // of rows that only COUNT and SUM can read
const PROJECTED = 'projected'; // the record a bucket's projection built, after it:
                           // its fields are the projection's aliases and nothing else

export class Binder {
  // Mirrored as static properties so `Binder.ROW` works the way `Value.BOOL`
  // does, which is the spelling every host reads the same.
  static NODE = NODE;
  static COLUMN = COLUMN;
  static ROW = ROW;
  static NONE = NONE;
  static KEY = KEY;
  static GROUP = GROUP;
  static PROJECTED = PROJECTED;

  constructor(shape, payload, reason = null, scope = null) {
    this.shape = shape;
    this.payload = payload;
    this.reason = reason;
    // For a NODE: how many frames were open where the node was WRITTEN. The node
    // is rendered later, inside frames its own text never saw, and a name in it
    // means what it meant where it was written -- so it is rendered against that
    // many frames and no more (lexical scope; docs/internals/sql-translation.md §7.4).
    // Without it an element `A` was captured by an inner binder that happened to
    // be called A.
    this.scope = scope;
    // For a ROW over joined rows (a pipeline after a LINK, or a side of a LINK's
    // predicate): the row model its fields resolve against, or null.
    this.model = null;
  }

  static node(node, scope = null) { return new Binder(NODE, node, null, scope); }

  static column(column) { return new Binder(COLUMN, column); }

  static row(relation) { return new Binder(ROW, relation); }

  static none(reason) { return new Binder(NONE, null, reason); }

  static key(group) { return new Binder(KEY, group); }

  static group(relation) { return new Binder(GROUP, relation); }

  static projected(relation, projections) { return new Binder(PROJECTED, { relation, projections }); }
}
