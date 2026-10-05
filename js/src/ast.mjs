// The shapes of the parse tree, in one place: which fields of a node are
// child nodes, and the questions about a whole subtree that more than one
// layer asks (does it read a name, can evaluating it write).
//
// Node kinds and their children, in source order:
//   num text bool null var cval    leaves
//   un      x
//   bin     l r
//   index   obj idx
//   assign  target value          (target: a var, or an index chain over one)
//   list seq  items
//   call    args
//   clist   entries' values       (the SQL normaliser's own unrolled list)
//
// Every walk here is iterative: a tree is as deep as its source is long
// (`1+1+1+...`), and spec §6.4 bounds evaluation, not a static walk's stack.
//
// A walker that deliberately leaves something out says so at its call: depth
// counting and the optimiser's rewrite walk pass `target: false` (an
// assignment's target is not evaluated as an expression; its index
// expressions are reached through the evaluator's own path).

import { BUILTIN_MANIFEST } from './_builtin_manifest.mjs';

// The children of `node`, in source order.
export function childNodes(node, target = true) {
  switch (node.t) {
    case 'un': return [node.x];
    case 'bin': return [node.l, node.r];
    case 'index': return [node.obj, node.idx];
    case 'assign': return target ? [node.target, node.value] : [node.value];
    case 'list': case 'seq': return node.items;
    case 'call': return node.args;
    case 'clist': return node.entries.map(([, v]) => v);
    default: return [];
  }
}

// Pushes `node`'s children onto `stack` in reverse source order, so popping
// visits them left to right.
export function pushChildren(stack, node, target = true) {
  const kids = childNodes(node, target);
  for (let i = kids.length - 1; i >= 0; i--) if (kids[i]) stack.push(kids[i]);
}

// Visits every node of the tree, pre-order and left to right; `visit`
// returning false skips that node's children.
export function walkNodes(root, visit, target = true) {
  const stack = root ? [root] : [];
  while (stack.length > 0) {
    const n = stack.pop();
    if (visit(n) !== false) pushChildren(stack, n, target);
  }
}

// Whether `test` holds for some node of the tree (pre-order, left to right,
// stopping at the first).
export function anyNode(root, test, target = true) {
  const stack = root ? [root] : [];
  while (stack.length > 0) {
    const n = stack.pop();
    if (test(n)) return true;
    pushChildren(stack, n, target);
  }
  return false;
}

// The field names read as `binder["field"]` (`_`, `_1` and `_2` too), first
// seen first, each once. Names compare as the lexer wrote them (canonical
// upper case, as the evaluator's frames hold them); field keys exactly, since
// SEL's record keys are case-sensitive. `binder` null counts a read under any
// name -- a later step binds the row however it likes.
export function fieldReads(node, binder = '_') {
  const wanted = binder === null ? null : new Set([binder, '_', '_1', '_2']);
  const refs = [];
  const seen = new Set();
  walkNodes(node, (n) => {
    if (n.t === 'index' && n.obj?.t === 'var' && n.idx?.t === 'text'
        && (wanted === null || wanted.has(n.obj.name)) && !seen.has(n.idx.v)) {
      seen.add(n.idx.v);
      refs.push(n.idx.v);
    }
  });
  return refs;
}

// Whether `node` reads one of `names` as a value -- a field read
// `name["field"]` reads the field, not the whole of `name`.
export function readsName(node, names) {
  const wanted = new Set(names);
  let found = false;
  walkNodes(node, (n) => {
    if (found) return false;
    if (n.t === 'var' && wanted.has(n.name)) { found = true; return false; }
    return !(n.t === 'index' && n.obj?.t === 'var' && n.idx?.t === 'text');
  });
  return found;
}

// A shallow copy of `node` with each child replaced by `fn(child)`.
export function mapChildren(node, fn) {
  const copy = { ...node };
  switch (node.t) {
    case 'un': copy.x = fn(node.x); break;
    case 'bin': copy.l = fn(node.l); copy.r = fn(node.r); break;
    case 'index': copy.obj = fn(node.obj); copy.idx = fn(node.idx); break;
    case 'assign': copy.target = fn(node.target); copy.value = fn(node.value); break;
    case 'list': case 'seq': copy.items = node.items.map(fn); break;
    case 'call': copy.args = node.args.map(fn); break;
    default: break;
  }
  return copy;
}

// Whether a call names an application's code rather than a shipped builtin:
// a host function, or any other function not in spec/builtins.json. Such a
// call is handed values and may do anything with them.
export function callsApplication(node) {
  return node.t === 'call' && !Object.hasOwn(BUILTIN_MANIFEST, node.name);
}

const WRITES = new WeakMap();
// Whether evaluating `node` might write into a value: it holds an assignment
// or calls an application's function. Asked of an aggregate's body once per
// call, so answered once per node.
export function mayWrite(node) {
  if (!node) return false;
  let answer = WRITES.get(node);
  if (answer === undefined) {
    answer = anyNode(node, (n) => n.t === 'assign' || callsApplication(n));
    WRITES.set(node, answer);
  }
  return answer;
}

const READS_KEY = new WeakMap();
// Whether `node` mentions `_K` anywhere (a body that does not is never handed
// the element's key). Answered once per node.
export function mentionsKey(node) {
  if (!node) return false;
  let answer = READS_KEY.get(node);
  if (answer === undefined) {
    answer = anyNode(node, (n) => n.t === 'var' && n.name === '_K');
    READS_KEY.set(node, answer);
  }
  return answer;
}
