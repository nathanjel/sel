// Stage 1: turn a small, well-behaved class of SEL programs into one expression,
// and refuse the rest.
//
// SEL is an expression language, but it has assignment and `;`, and SQL has
// neither. This is the restrictive step: a helper variable is inlined as the
// expression it held, and anything that cannot be is refused with a position.

import { MAX_DEPTH } from '../eval.mjs';
import * as constants from './constants.mjs';
import { refuse } from './errors.mjs';

// A keyed list, built by indexed assignment and by nothing else.
//
// Distinct from a `list` node because it must NOT be flattened: assignment stores
// a list as a child rather than contributing its children (spec §5.9 applies to
// `,` and not to `=`), so `R[1] = (1, 2); R[2] = (3, 4)` is two pairs and not
// four scalars, and this is the only node that can say so. It carries the real
// keys too, so `R["a"] = 1` gives `_K` of `"a"`.
//
// A class of its own rather than a field added to a node: the parser never
// produces one, the evaluator has never seen one, and this is the SQL layer's
// node, not the language's. It duck-types as a node — `.t` and `.pos` — which is
// all any consumer here reads.
export class CList {
  constructor(pos, entries = null) {
    this.t = 'clist';
    this.pos = pos;
    this.entries = entries ?? [];
  }
}

export function run(ast, constNames = null, ctx = null) {
  const stmts = ast.t === 'seq' ? [...ast.items] : [ast];
  const result = stmts.pop();
  // A Map: the names come from the program, and `{}` answers for every
  // Object.prototype name, so `constructor = 1; constructor` would inline a
  // function.
  const defs = new Map();

  for (const s of stmts) record(s, defs, constNames ?? new Map(), ctx);
  return substitute(result, defs, []);
}

// Fold one leading statement into `defs`, or refuse it.
function record(s, defs, constNames, ctx) {
  if (s.t !== 'assign') {
    refuse('E_SQL_ASSIGN',
      'only assignments may come before the result expression; this computes a '
      + 'value nothing reads, which SQL has nowhere to put', s.pos);
  }
  if (s.op !== '=') {
    refuse('E_SQL_ASSIGN',
      `${s.op} reads its own target before writing it, and SQL has nowhere to put `
      + 'the write; use = and a fresh name', s.pos);
  }

  // The target is a bare name, or a name indexed by constant keys.
  const keys = [];
  let t = s.target;
  while (t.t === 'index') {
    const k = constantKey(t.idx);
    if (k === null) {
      refuse('E_SQL_ASSIGN',
        'an assignment target may only be indexed by a constant here, because the '
        + 'shape has to be known before the query runs', t.idx.pos);
    }
    keys.unshift(k);
    t = t.obj;
  }
  if (t.t !== 'var') refuse('E_SQL_ASSIGN', 'assignment target is not a variable', s.pos);
  const name = t.name;

  const value = substitute(s.value, defs, []);

  // Validated here, and only here, because after this the subtree may be gone: a
  // definition nothing reads is dropped, so `A = 1 / 0; TRUE` translated to
  // `TRUE` and every server answered TRUE where SEL raises E_DIV_ZERO. An indexed
  // assignment builds a `clist`, which the constant test refuses to walk and
  // COUNT/HAS never render, so `R[1] = 1 / 0; COUNT(R)` was `1`. §11.4's fourth
  // bullet says a constant subtree is checked wherever it appears; these were the
  // two places it did not appear by the time anything looked.
  if (constants.isConstant(value, constNames)) constants.validate(value, ctx);

  if (keys.length === 0) {
    if (defs.has(name)) {
      refuse('E_SQL_ASSIGN',
        `${name} is assigned more than once; SQL has no notion of a variable `
        + 'changing, so each name may be written once', s.pos);
    }
    defs.set(name, value);
    return;
  }

  if (keys.length > 1) {
    refuse('E_SQL_ASSIGN',
      'only one level of indexed assignment can be folded into a list here', s.pos);
  }
  const key = keys[0];
  if (!defs.has(name)) defs.set(name, new CList(s.pos));
  if (defs.get(name).t !== 'clist') {
    refuse('E_SQL_ASSIGN',
      `${name} is assigned both as a whole and by index; use one or the other`, s.pos);
  }
  for (const [existing] of defs.get(name).entries) {
    if (existing === key) {
      refuse('E_SQL_ASSIGN', `${name}[${key}] is assigned more than once`, s.pos);
    }
  }
  defs.get(name).entries.push([key, value]);
}

// The literal key an index expression names, or null when it is not one.
function constantKey(idx) {
  if (idx.t === 'num' || idx.t === 'text') return String(idx.v);
  return null;
}

// Replace every read of a defined name with the node it was assigned.
//
// The substituted subtree keeps its original `pos`, so an error inside an inlined
// expression still points at where the author wrote it rather than at the place
// it was used.
//
// Every branch that changes a child COPIES the node first. PHP gets this free,
// because its arrays are values and `$node['x'] = …` mutates a copy; a JS node is
// a mutable object shared with the caller's AST, and rewriting one in place would
// leave the program permanently substituted — visible to the next translation of
// the same Program, and to the evaluator.
//
// Bounded at the evaluator's own limit, because stage 1 walks the tree before the
// translator's guard can reach it. Without this the deepest expression the layer
// accepts was decided by the host: PHP recursed as far as it liked and Python
// died of its own stack at around 510 terms, which is an implementation accident
// rather than a decision.
function substitute(node, defs, bound, depth = 0) {
  const d = depth + 1;
  if (d > MAX_DEPTH) {
    refuse('E_SQL_DEPTH',
      `this expression nests deeper than SEL will evaluate (${MAX_DEPTH}), so there `
      + 'is nothing to translate; the evaluator answers E_DEPTH for it', node.pos);
  }
  const t = node.t;
  if (t === 'var') {
    if (bound.includes(node.name)) return node;
    return defs.has(node.name) ? defs.get(node.name) : node;
  }

  if (t === 'num' || t === 'text' || t === 'bool') return node;

  if (t === 'assign') {
    refuse('E_SQL_ASSIGN',
      'an assignment here would have to happen while the query runs, and a SQL '
      + 'expression cannot assign', node.pos);
  }

  if (t === 'seq') {
    refuse('E_SQL_ASSIGN',
      'a sequence here would evaluate and discard a value, which a SQL expression '
      + 'cannot do', node.pos);
  }

  // `{ ...node, … }` is dataclasses.replace: a shallow copy with one field
  // changed, never a mutation of the caller's AST.
  if (t === 'un') return { ...node, x: substitute(node.x, defs, bound, d) };

  if (t === 'bin') {
    return { ...node,
      l: substitute(node.l, defs, bound, d),
      r: substitute(node.r, defs, bound, d) };
  }

  if (t === 'index') {
    return { ...node,
      obj: substitute(node.obj, defs, bound, d),
      idx: substitute(node.idx, defs, bound, d) };
  }

  if (t === 'list') return { ...node, items: flatten(node.items, defs, bound, d) };

  if (t === 'clist') {
    return new CList(node.pos,
      node.entries.map(([k, v]) => [k, substitute(v, defs, bound, d)]));
  }

  if (t === 'call') {
    const inner = [...bound];
    const binds = node.spec != null && node.spec.binds;
    if (binds) {
      const n = node.args.length;
      inner.push('_K');
      inner.push(n === 3 && node.args[1].t === 'var' ? node.args[1].name : '_');
    }
    const args = [];
    node.args.forEach((arg, i) => {
      // An aggregate's binder argument is a name, not a read of one.
      if (binds && i === 1 && node.args.length === 3 && arg.t === 'var') {
        args.push(arg);
        return;
      }
      args.push(substitute(arg, defs, i === 0 ? bound : inner, d));
    });
    return { ...node, args };
  }

  return node;
}

// Build a `,` list, flattening per spec §5.9: an operand with children and no
// scalar of its own contributes each of its children, and the keys are renumbered
// from 1.
//
// Both node kinds contribute, and both contribute exactly one level. Verified
// against the evaluator, which is the arbiter here:
//
//     ((1, 2), 3)                       -> three scalars
//     R[1] = (1, 2); R[2] = (3, 4); (R, 5)
//                                       -> (1,2), (3,4), 5 — three elements,
//                                          two of which are still lists
//
// Parenthesising changes nothing: the `grouped` flag decides whether a call sees
// one argument or several, not whether `,` flattens.
//
// What `clist` protects is not this. It is that indexed assignment builds nesting
// out of `R[1] = …; R[2] = …`, and a `list` node would have been renumbered flat
// by the time an aggregate iterated it. A `clist` reaches an aggregate through a
// bare variable reference, never through a `,`, so the two rules never meet.
function flatten(items, defs, bound, depth = 0) {
  const out = [];
  for (const item of items) {
    const s = substitute(item, defs, bound, depth);
    if (s.t === 'list') { out.push(...s.items); continue; }
    if (s.t === 'clist') { out.push(...s.entries.map(([, v]) => v)); continue; }
    out.push(s);
  }
  return out;
}
