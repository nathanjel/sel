// Stage 1: turn a small, well-behaved class of SEL programs into one expression,
// and refuse the rest.
//
// SEL is an expression language, but it has assignment and `;`, and SQL has
// neither. This is the restrictive step: a helper variable is inlined as the
// expression it held, and anything that cannot be is refused with a position.

import { MAX_DEPTH } from '../eval.mjs';
import { MAX_SQL_NODES } from '../_limits.mjs';
import { bindingForm } from '../registry.mjs';
import { childNodes as children } from '../ast.mjs';
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
  defs.constNames = constNames ?? new Map();

  // Counting starts where the evaluator's count would stand: the `;` sequence
  // costs a level and each assignment it inlines one more (spec §6.4), so
  // `X = <199 terms>; X` — E_DEPTH in the evaluator — is refused here too,
  // although the chain alone would translate. Stage 1 removes both wrappers
  // before either guard looks, which is why they must be charged up front.
  const base = ast.t === 'seq' ? 1 : 0;
  for (const s of stmts) record(s, defs, constNames ?? new Map(), ctx, base + 1);
  return substitute(result, defs, [], base);
}

// Fold one leading statement into `defs`, or refuse it. `depth` is where the
// evaluator's count stands at the assignment's right-hand side.
function record(s, defs, constNames, ctx, depth = 0) {
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

  const value = substitute(s.value, defs, [], depth);
  // A helper's text is shared where it is read, so `X1 = X0 + X0; X2 = X1 + X1;
  // ...` is small as a graph and exponential as a tree, and the constant test just
  // below walks the tree. Its expanded size is known cheaply, node by node, and a
  // definition already past the translator's budget is refused here rather than
  // after that walk (E_SQL_SIZE; docs/internals/sql-translation.md §7.4).
  expandedSize(value, defs.sizes ??= new Map(), s.pos);

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
    if (node.binding || bound.includes(node.name)) return node;
    if (!defs.has(node.name)) return node;
    const def = defs.get(node.name);
    // A read is a value, not a reference (spec §3.4): a keyed list read here is
    // what it holds NOW, so a later `R[2] = 6` must not reach a `X = R` written
    // before it, nor a write through X reach R. The entries' values are
    // immutable nodes, so copying the list of entries is a copy.
    return def.t === 'clist' ? new CList(def.pos, def.entries.map(([k, v]) => [k, v])) : def;
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
    // Which arguments a binding call runs inside the binder, and what they
    // see, is the manifest's decision (bindingForm), shared with dependencies().
    // A binder argument is a name, not a read of one, and stays as written.
    const form = bindingForm(node.name, node.args, node.spec);
    // A named binder is renamed apart before anything is inlined under it. A
    // definition is written where it is written and read where it is read, and a
    // free name in it means what it meant at the assignment: `X2 = A; ALL((5, 6),
    // A, X2 > 0)` must read the column A in X2, not the element. Renaming the
    // binder, and every read of it in the body that is still its own, leaves no
    // name for the inlined text to be captured by.
    let args = node.args;
    let binds = form ? form.binds : [];
    if (form) {
      const fresh = new Map();
      args = args.map((arg, i) => {
        if (form.scopes[i] !== 'binder' || arg.t !== 'var') return arg;
        // Only when something could be captured: a definition that could be
        // inlined below mentions the name, or the name is a value binding the
        // constant test would otherwise still see under the binder. Anything else
        // is left as written.
        if (!defs.constNames.has(arg.name)
            && ![...defs.values()].some((def) => mentions(def, arg.name))) return arg;
        const apart = `${arg.name}\u0001${defs.freshCounter = (defs.freshCounter ?? 0) + 1}`;
        fresh.set(arg.name, apart);
        return { ...arg, name: apart };
      });
      if (fresh.size > 0) {
        args = args.map((arg, i) => (form.scopes[i] === 'inner'
          ? renameFree(arg, fresh) : arg));
        binds = binds.map((b) => fresh.get(b) ?? b);
      }
    }
    const inner = form ? [...bound, ...binds] : bound;
    const out = args.map((arg, i) => {
      const scope = form ? form.scopes[i] : 'outer';
      if (scope === 'binder') return arg;
      return substitute(arg, defs, scope === 'inner' ? inner : bound, d);
    });
    return { ...node, args: out };
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

// Rename the free reads of each name in `map` (old -> new) inside `node`, leaving
// alone any read that a nested binder of the same name captures.
function renameFree(node, map) {
  const t = node.t;
  if (t === 'var') return map.has(node.name) ? { ...node, name: map.get(node.name) } : node;
  if (t === 'un') return { ...node, x: renameFree(node.x, map) };
  if (t === 'bin') return { ...node, l: renameFree(node.l, map), r: renameFree(node.r, map) };
  if (t === 'index') return { ...node, obj: renameFree(node.obj, map), idx: renameFree(node.idx, map) };
  if (t === 'list') return { ...node, items: node.items.map((x) => renameFree(x, map)) };
  if (t === 'clist') {
    const c = new CList(node.pos, node.entries.map(([k, v]) => [k, renameFree(v, map)]));
    return c;
  }
  if (t === 'call') {
    const form = bindingForm(node.name, node.args, node.spec);
    const shadowed = form ? form.binds : [];
    let innerMap = map;
    if (shadowed.some((b) => map.has(b))) {
      innerMap = new Map([...map].filter(([k]) => !shadowed.includes(k)));
    }
    const args = node.args.map((arg, i) => {
      const scope = form ? form.scopes[i] : 'outer';
      if (scope === 'binder') return arg;
      return renameFree(arg, scope === 'inner' ? innerMap : map);
    });
    return { ...node, args };
  }
  return node;
}

// Does the name occur anywhere in the node (a superset of "occurs free")?
// Iterative, and each node at most once: a definition is shared where it is read.
function mentions(root, name) {
  const seen = new Set();
  const stack = [root];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if (node.t === 'var') { if (node.name === name) return true; continue; }
    stack.push(...children(node));
  }
  return false;
}

// The size of `node` counted as a tree, and its height, from a memo keyed by node
// identity, so a subtree shared n times is measured once. Iterative. Refuses past
// the size budget (E_SQL_SIZE) and past four times the evaluator's depth (E_SQL_DEPTH):
// the translation refuses anything over MAX_DEPTH itself, precisely, when it walks
// the tree -- but stage 1 and the constant test walk it first, recursively, and a
// tree tens of thousands deep would find the host's stack before that walk.
function expandedSize(root, memo, pos) {
  const stack = [[root, false]];
  while (stack.length > 0) {
    const [node, done] = stack.pop();
    if (!node || memo.has(node)) continue;
    const kids = children(node);
    if (!done) {
      stack.push([node, true]);
      for (const k of kids) if (k && !memo.has(k)) stack.push([k, false]);
      continue;
    }
    let size = 1;
    let height = 0;
    for (const k of kids) {
      if (!k) continue;
      const m = memo.get(k) ?? [1, 1];
      size += m[0];
      if (m[1] > height) height = m[1];
    }
    height += 1;
    if (height > 4 * MAX_DEPTH) {
      refuse('E_SQL_DEPTH',
        `this expression nests deeper than SEL will evaluate (${MAX_DEPTH}), so there `
        + 'is nothing to translate; the evaluator answers E_DEPTH for it', pos);
    }
    if (size > MAX_SQL_NODES) {
      refuse('E_SQL_SIZE',
        `this definition expands to more than ${MAX_SQL_NODES} nodes once every read of `
        + 'a helper is counted', pos);
    }
    memo.set(node, [size, height]);
  }
  return memo.get(root)?.[0] ?? 1;
}
