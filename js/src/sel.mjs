// Public host interface. See spec/SPEC.md §8.

import './builtins/index.mjs';
import { parse } from './parser.mjs';
import { Context, evalNode } from './eval.mjs';
import { Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { SelError } from './errors.mjs';
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
    collect(this.ast, new Set(), reads, assigned);
    return Array.from(reads).filter((n) => !assigned.has(n)).sort();
  }
}

function collect(node, bound, reads, assigned) {
  if (!node || typeof node !== 'object') return;
  switch (node.t) {
    case 'var':
      if (!bound.has(node.name)) reads.add(node.name);
      return;

    case 'assign': {
      let target = node.target;
      while (target.t === 'index') {
        collect(target.idx, bound, reads, assigned);
        target = target.obj;
      }
      // `A = x` defines A; `A[k] = x` and `A += x` also read it.
      if (node.target.t !== 'var' || node.op !== '=') {
        if (!bound.has(target.name)) reads.add(target.name);
      }
      assigned.add(target.name);
      collect(node.value, bound, reads, assigned);
      return;
    }

    case 'call': {
      // An aggregate's three-argument form binds its second argument as a name
      // for the duration of the third.
      if (node.spec && node.spec.binds && node.args.length === 3 && node.args[1].t === 'var') {
        collect(node.args[0], bound, reads, assigned);
        const inner = new Set(bound);
        inner.add(node.args[1].name);
        inner.add('_K');
        collect(node.args[2], inner, reads, assigned);
        return;
      }
      if (node.spec && node.spec.binds && node.args.length === 2) {
        collect(node.args[0], bound, reads, assigned);
        const inner = new Set(bound);
        inner.add('_');
        inner.add('_K');
        collect(node.args[1], inner, reads, assigned);
        return;
      }
      for (const a of node.args) collect(a, bound, reads, assigned);
      return;
    }

    case 'seq': case 'list':
      for (const item of node.items) collect(item, bound, reads, assigned);
      return;

    case 'index':
      collect(node.obj, bound, reads, assigned);
      collect(node.idx, bound, reads, assigned);
      return;

    case 'bin':
      collect(node.l, bound, reads, assigned);
      collect(node.r, bound, reads, assigned);
      return;

    case 'un':
      collect(node.x, bound, reads, assigned);
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
