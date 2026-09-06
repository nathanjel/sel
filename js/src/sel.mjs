// Public host interface. See spec/SPEC.md §8.

import './builtins/index.mjs';
import { parse } from './parser.mjs';
import { Context, evalNode, MAX_DEPTH } from './eval.mjs';
import { Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { SelError, fail } from './errors.mjs';
import { names } from './registry.mjs';

export class Program {
  constructor(source, ast) {
    this.source = source;
    this.ast = ast;
  }

  // `context` may be a Value, a plain object, or omitted. Returns a Value; the
  // context is mutated in place by any assignments the program performs.
  run(context) {
    const root = context instanceof Value ? context : Value.fromNative(context || {});
    return evalNode(this.ast, new Context(root));
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
      // An aggregate's three-argument form binds its second argument as a name
      // for the duration of the third.
      if (node.spec && node.spec.binds && node.args.length === 3 && node.args[1].t === 'var') {
        collect(node.args[0], bound, reads, assigned, depth + 1);
        const inner = new Set(bound);
        inner.add(node.args[1].name);
        inner.add('_K');
        collect(node.args[2], inner, reads, assigned, depth + 1);
        return;
      }
      if (node.spec && node.spec.binds && node.args.length === 2) {
        collect(node.args[0], bound, reads, assigned, depth + 1);
        const inner = new Set(bound);
        inner.add('_');
        inner.add('_K');
        collect(node.args[1], inner, reads, assigned, depth + 1);
        return;
      }
      for (const a of node.args) collect(a, bound, reads, assigned, depth + 1);
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
export { Value, SelError, Context, NONE, TEXT, BIN, BOOL };
