// Public host interface. See spec/SPEC.md §8.

import './builtins/index.mjs';
import { parse } from './parser.mjs';
import { Context, evalNode, MAX_DEPTH } from './eval.mjs';
import { RecordShape, Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { SelError, fail } from './errors.mjs';
import { names, register, registerFunction, bindingForm } from './registry.mjs';
import { optimizeAstLogical, optimizeAstInMemory } from './optimizer.mjs';

export class Program {
  constructor(source, ast) {
    this.source = source;
    // The parse tree, and the tree every other consumer reads: dependencies(),
    // the SQL translator, the hybrid planner. It is IMMUTABLE once here -- the
    // optimiser and the planner copy on the way down and never write into it
    // (sql/cases/25-hybrid-plans.sqlt asserts so) -- and a caller who builds a
    // Program from an AST of their own is held to the same rule. Reassigning
    // `ast` is fine and drops the cache below; writing into its nodes is not.
    this.ast = ast;
    // The physical tree run() evaluates: `ast` after the in-memory optimiser,
    // built on the first run and kept, because the rewrite and the copy it
    // makes cost more than evaluating a small rule does. Keyed by the identity
    // of `ast` so a reassignment is noticed. Private; SQL translation never
    // sees it, since a physical rewrite (join pushdown) is not
    // something a database can be asked to run.
    this._physical = null;
    this._physicalOf = null;
  }

  // `context` may be a Value, a plain object, or omitted. Returns a Value; the
  // context is mutated in place by any assignments the program performs.
  run(context) {
    const root = context instanceof Value ? context : Value.fromNative(context || {});
    return evalNode(this.physicalAst(), new Context(root));
  }

  // The optimised tree run() evaluates, built once per `ast`.
  physicalAst() {
    if (this._physicalOf !== this.ast) {
      this._physical = optimizeAstInMemory(this.ast);
      this._physicalOf = this.ast;
    }
    return this._physical;
  }

  // Every variable some read of which can happen before the program has
  // DEFINITELY assigned it, in evaluation order (spec/SPEC.md §8), found
  // statically. Only possible because SEL has no dynamic symbol operator; this
  // is what tells a frontend which inputs should re-trigger which rule.
  dependencies() {
    const reads = new Set();
    collect(this.ast, new Set(), new Set(), reads, 1);
    return Array.from(reads).sort();
  }
}

// The static walk of the tree, and the third thing in each host that recurses
// over it. spec/SPEC.md 6.4 caps the other two -- the parser's nesting and the
// evaluator's -- and says why: uncounted recursion over a tree the source can
// make arbitrarily deep reaches the host's own stack limit. This walk was
// uncounted, and `dependencies()` on a flat chain of about 48,000 operators
// raised an uncaught RangeError, which is not a SEL error at all.
//
// The depth rides as a parameter rather than as a counter with a guard, because
// there is nothing to release on the way out -- which is also what lets the
// hosts spell this identically. Capped at the same MAX_DEPTH the evaluator uses
// and tripping at the same node, so a program whose dependencies cannot be
// computed is exactly a program that could not have been evaluated.
//
// FLOW-SENSITIVE (§8): `done` is the set of names definitely assigned so far on
// every path that reaches this node. A read of a name that is neither bound nor
// in `done` is a dependency, whether or not it is later assigned. The walk
// follows evaluation order; a region that may not run (the right side of AND, OR,
// `??`, `???`, the arms of IF and COND, later COALESCE arguments, GET's default,
// an aggregate body) is walked on a copy, and only what every arm assigns joins
// `done` afterwards.
function collect(node, bound, done, reads, depth) {
  if (!node || typeof node !== 'object') return;
  if (depth > MAX_DEPTH) fail('E_DEPTH', 'expression nested too deeply', node.pos);
  const walk = (n, b = bound, d = done) => collect(n, b, d, reads, depth + 1);
  switch (node.t) {
    case 'var':
      if (!bound.has(node.name) && !done.has(node.name)) reads.add(node.name);
      return;

    case 'assign': {
      // Index expressions run first, in order, then the right side; then the
      // store. `A = x` defines A; `A[k] = x` creates A and reads only the index;
      // `A += x` and `A[k] += x` also read their target.
      // In SOURCE order: `A[(K = 1)][K] = B` runs the inner bracket first, so the K of the
      // outer bracket is already assigned. The chain is walked outermost-first, so the keys
      // are collected and then taken from the innermost out.
      let target = node.target;
      const keys = [];
      while (target.t === 'index') {
        keys.push(target.idx);
        target = target.obj;
      }
      for (let i = keys.length - 1; i >= 0; i--) walk(keys[i]);
      // The target of `op=` is read BEFORE the right side runs (SPEC §8): in
      // `A += (A = 1; 2)` the read of A has already failed by the time the right
      // side assigns it.
      if (node.op !== '=') {
        if (!bound.has(target.name) && !done.has(target.name)) reads.add(target.name);
      }
      walk(node.value);
      if (!bound.has(target.name)) done.add(target.name);
      return;
    }

    case 'call': {
      const name = (node.name || '').toUpperCase();
      // Which arguments run inside the binder, and what they see, is decided
      // once, by bindingForm() over the manifest's forms (spec/builtins.md).
      // No form -- a strict function, or a count the evaluator would refuse --
      // and every argument is read where the call stands, in order.
      const form = bindingForm(node.name || '', node.args, node.spec);
      if (!form) {
        if (name === 'IF' || name === 'COND') return collectBranches(node, name, bound, done, reads, depth);
        node.args.forEach((arg, i) => {
          // The first argument always runs; COALESCE's later ones and GET/PATH's
          // default only when the earlier ones leave room for them.
          const optional = (name === 'COALESCE' && i > 0) || ((name === 'GET' || name === 'PATH') && i > 1);
          if (optional) walk(arg, bound, new Set(done)); else walk(arg);
        });
        return;
      }
      let inner = null;
      node.args.forEach((arg, i) => {
        const scope = form.scopes[i];
        if (scope === 'binder') return;
        if (scope === 'inner') {
          if (!inner) {
            inner = new Set(bound);
            for (const b of form.binds) inner.add(b);
          }
          // A body may run once per element, or never: what it assigns is not
          // definite afterwards.
          walk(arg, inner, new Set(done));
        } else {
          walk(arg);
        }
      });
      return;
    }

    case 'seq': case 'list':
      for (const item of node.items) walk(item);
      return;

    case 'index':
      walk(node.obj);
      walk(node.idx);
      return;

    case 'bin': {
      const op = node.op;
      walk(node.l);
      if (op === 'AND' || op === 'OR' || op === '??' || op === '???') {
        walk(node.r, bound, new Set(done));
      } else {
        walk(node.r);
      }
      return;
    }

    case 'un':
      walk(node.x);
      return;
  }
}

// IF and COND: the condition(s) that decide run in order along the path where
// every earlier one was false; each arm runs from the state after its own
// condition; what is definite afterwards is what EVERY outcome assigned (COND's
// last argument is its default, so it always has an outcome; a two-argument IF
// has an empty else).
function collectBranches(node, name, bound, done, reads, depth) {
  const walkIn = (n, d) => collect(n, bound, d, reads, depth + 1);
  const args = node.args;
  const outcomes = [];
  let cur = new Set(done);
  if (name === 'IF') {
    if (args.length > 0) walkIn(args[0], cur);
    for (let i = 1; i < args.length; i++) {
      const arm = new Set(cur);
      walkIn(args[i], arm);
      outcomes.push(arm);
    }
    if (args.length < 3) outcomes.push(new Set(cur));
  } else {
    const pairs = Math.floor(args.length / 2);
    for (let k = 0; k < pairs; k++) {
      walkIn(args[2 * k], cur);
      const arm = new Set(cur);
      walkIn(args[2 * k + 1], arm);
      outcomes.push(arm);
    }
    // COND's count is odd (the manifest's arity rule, at compile time): the
    // last argument is the default.
    walkIn(args[args.length - 1], cur);
    outcomes.push(cur);
  }
  // Definite afterwards: assigned in every outcome.
  if (outcomes.length > 0) {
    for (const n of outcomes[0]) if (outcomes.every((o) => o.has(n))) done.add(n);
  }
}

export function compile(source) {
  // Source is text (SPEC §8): anything else is a misuse of the API, not a syntax
  // error in a program that was never given.
  if (typeof source !== 'string') fail('E_BAD_ARG', 'compile: the source must be a string', null);
  return new Program(source, parse(source));
}

export function evaluate(source, context) {
  return compile(source).run(context);
}

export function functionNames() { return names(); }

// The kind constants travel with the Value class. Leaving them out of this
// re-export meant `import { BOOL } from 'sel-lang'` failed and `Value.BOOL` was
// undefined, so branching on kind required the literal string 'BOOL'.
// Context is deliberately NOT here. It is one evaluation's binder frames and
// recursion depth, spec/SPEC.md §8 does not list it, nothing outside the
// evaluator constructs one, and C++ and Lisp never exposed it. Exporting it in
// three hosts and not the other two was an accident of what was convenient to
// import here.
//
// DEPRECATED, kept for one release and marked so in sel.d.ts: `register` and
// its alias `registerBuiltin` (now the same strict path as registerFunction),
// the optimiser entry points (`optimizeAst` is optimizeAstInMemory under an
// older name) and `RecordShape`, which no public call accepts or returns.
const registerBuiltin = register;
const optimizeAst = optimizeAstInMemory;
export {
  RecordShape, Value, SelError, NONE, TEXT, BIN, BOOL, register, registerBuiltin, registerFunction,
  optimizeAst, optimizeAstLogical, optimizeAstInMemory,
};
