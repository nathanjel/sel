// Public host interface. See spec/SPEC.md §8.

import './builtins/index.mjs';
import { parse } from './parser.mjs';
import { Context, evalNode, MAX_DEPTH } from './eval.mjs';
import { RecordShape, Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { SelError, fail } from './errors.mjs';
import { names, register, registerBuiltin, registerFunction, bindingForm } from './registry.mjs';
import { optimizeAst, optimizeAstLogical, optimizeAstInMemory } from './optimizer.mjs';

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
      this._physical = optimizeAst(this.ast);
      this._physicalOf = this.ast;
    }
    return this._physical;
  }

  // Every variable the program reads without having assigned it first, found
  // statically. Only possible because SEL has no dynamic symbol operator; this
  // is what tells a frontend which inputs should re-trigger which rule.
  dependencies() {
    const reads = new Set();
    const assigned = new Set();
    collect(this.ast, new Set(), reads, assigned, 1);
    return Array.from(reads).filter((n) => !assigned.has(n)).sort();
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
// there is nothing to release on the way out -- which is also what lets the five
// hosts spell this identically. Capped at the same MAX_DEPTH the evaluator uses
// and tripping at the same node, so a program whose dependencies cannot be
// computed is exactly a program that could not have been evaluated.
function collect(node, bound, reads, assigned, depth) {
  if (!node || typeof node !== 'object') return;
  if (depth > MAX_DEPTH) fail('E_DEPTH', 'expression nested too deeply', node.pos);
  switch (node.t) {
    case 'var':
      if (!bound.has(node.name)) reads.add(node.name);
      return;

    case 'assign': {
      let target = node.target;
      while (target.t === 'index') {
        collect(target.idx, bound, reads, assigned, depth + 1);
        target = target.obj;
      }
      // `A = x` defines A; `A[k] = x` and `A += x` also read it.
      if (node.target.t !== 'var' || node.op !== '=') {
        if (!bound.has(target.name)) reads.add(target.name);
      }
      assigned.add(target.name);
      collect(node.value, bound, reads, assigned, depth + 1);
      return;
    }

    case 'call': {
      // Which arguments run inside the binder, and what they see, is decided
      // once, by bindingForm() over the manifest's forms (spec/builtins.md).
      // No form -- a strict function, or a count the evaluator would refuse --
      // and every argument is read where the call stands.
      const form = bindingForm(node.name || '', node.args, node.spec);
      if (!form) {
        for (const a of node.args) collect(a, bound, reads, assigned, depth + 1);
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
          collect(arg, inner, reads, assigned, depth + 1);
        } else {
          collect(arg, bound, reads, assigned, depth + 1);
        }
      });
      return;
    }

    case 'seq': case 'list':
      for (const item of node.items) collect(item, bound, reads, assigned, depth + 1);
      return;

    case 'index':
      collect(node.obj, bound, reads, assigned, depth + 1);
      collect(node.idx, bound, reads, assigned, depth + 1);
      return;

    case 'bin':
      collect(node.l, bound, reads, assigned, depth + 1);
      collect(node.r, bound, reads, assigned, depth + 1);
      return;

    case 'un':
      collect(node.x, bound, reads, assigned, depth + 1);
      return;
  }
}

export function compile(source) {
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
export {
  RecordShape, Value, SelError, NONE, TEXT, BIN, BOOL, register, registerBuiltin, registerFunction,
  optimizeAst, optimizeAstLogical, optimizeAstInMemory,
};
