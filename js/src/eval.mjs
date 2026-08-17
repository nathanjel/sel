// The evaluator, and the argument framework built-ins are written against.
//
// Nothing here catches a SelError. An error surfaces from the innermost node
// that failed, carrying that node's position, and no layer rewrites it.

import { fail } from './errors.mjs';
import * as D from './decimal.mjs';
import { Value, NONE, TEXT, BIN, BOOL } from './value.mjs';
import { bytesCompare } from './utf8.mjs';

const MAX_DEPTH = 200;

export class Context {
  constructor(root) {
    this.root = root || Value.none();
    this.frames = [];     // aggregate binders: Map<name, Value>
    this.depth = 0;
  }

  lookup(name) {
    for (let i = this.frames.length - 1; i >= 0; i--) {
      const v = this.frames[i].get(name);
      if (v !== undefined) return v;
    }
    return this.root.get(name);
  }

  isBound(name) {
    for (let i = this.frames.length - 1; i >= 0; i--) {
      if (this.frames[i].has(name)) return true;
    }
    return false;
  }

  pushFrame(map) { this.frames.push(map); }
  popFrame() { this.frames.pop(); }
}

// --- arguments --------------------------------------------------------------

// Wraps the flattened argument vector. Values are evaluated at most once, so a
// built-in body can read the same argument repeatedly without thinking about it,
// and typed accessors report failures against the argument's own position.
export class Args {
  constructor(node, ctx) {
    this.nodes = node.args;
    this.name = node.name;
    this.pos = node.pos;
    this.ctx = ctx;
    this._vals = new Array(node.args.length);
  }

  count() { return this.nodes.length; }
  node(i) { return this.nodes[i]; }
  posOf(i) { return this.nodes[i].pos; }

  val(i) {
    if (this._vals[i] === undefined) this._vals[i] = evalNode(this.nodes[i], this.ctx);
    return this._vals[i];
  }

  // For lazy functions re-evaluating a body node under changed bindings.
  evalNode(node) { return evalNode(node, this.ctx); }

  text(i) { return this.val(i).asText(this.posOf(i)); }
  bytes(i) { return this.val(i).asBytes(this.posOf(i)); }
  bool(i) { return this.val(i).asBool(this.posOf(i)); }
  dec(i) { return this.val(i).asDecimal(this.posOf(i)); }

  // A whole number, returned as a JS number for indexing and counting.
  int(i) {
    const d = this.dec(i);
    if (!D.isInteger(d)) {
      fail('E_NOT_INT', `${this.name} argument ${i + 1} must be a whole number`, this.posOf(i));
    }
    return D.toSafeInt(d);
  }

  nonNegInt(i) {
    const n = this.int(i);
    if (n < 0) {
      fail('E_RANGE', `${this.name} argument ${i + 1} must not be negative`, this.posOf(i));
    }
    return n;
  }

  // Requires the argument to be a bare identifier in the source — the AST shape
  // check that gives aggregates their three-argument binder form.
  symbol(i) {
    const n = this.nodes[i];
    if (n.t !== 'var' || n.grouped) {
      fail('E_EXPECT_SYMBOL', `${this.name} argument ${i + 1} must be a plain name`, n.pos);
    }
    return n.name;
  }
}

// --- evaluation -------------------------------------------------------------

export function evalNode(node, ctx) {
  if (++ctx.depth > MAX_DEPTH) {
    ctx.depth--;
    fail('E_DEPTH', 'evaluation nested too deeply', node.pos);
  }
  try {
    return evalDispatch(node, ctx);
  } finally {
    ctx.depth--;
  }
}

function evalDispatch(node, ctx) {
  switch (node.t) {
    case 'num': return Value.text(node.v);      // canonicalised by the parser
    case 'text': return Value.text(node.v);
    case 'bool': return Value.bool(node.v);

    case 'var': {
      const v = ctx.lookup(node.name);
      if (v === undefined) fail('E_UNDEF_VAR', `undefined variable ${node.name}`, node.pos);
      return v;
    }

    case 'index': {
      const obj = evalNode(node.obj, ctx);
      const key = evalNode(node.idx, ctx).asText(node.idx.pos);
      const child = obj.get(key);
      if (child === undefined) fail('E_NO_KEY', `no key ${JSON.stringify(key)}`, node.pos);
      return child;
    }

    case 'seq': {
      let last;
      for (const item of node.items) last = evalNode(item, ctx);
      return last;
    }

    case 'list': return evalList(node, ctx);
    case 'un': return evalUnary(node, ctx);
    case 'bin': return evalBinary(node, ctx);
    case 'assign': return evalAssign(node, ctx);

    case 'call': {
      const args = new Args(node, ctx);
      if (!node.spec.lazy) {
        // Strict: every argument evaluated once, left to right, before the body.
        for (let i = 0; i < node.args.length; i++) args.val(i);
      }
      return node.spec.fn(args, ctx);
    }
  }
  fail('E_SYNTAX', `cannot evaluate node ${node.t}`, node.pos);
}

// §5.9 — a value with children and no scalar contributes its children's values;
// anything else contributes itself. Keys are always renumbered from 1.
function evalList(node, ctx) {
  const out = Value.none();
  let n = 0;
  for (const item of node.items) {
    const v = evalNode(item, ctx);
    if (v.kind === NONE && v.size() > 0) {
      for (const child of v.values()) out.set(String(++n), child.clone());
    } else {
      out.set(String(++n), v.clone());
    }
  }
  return out;
}

function evalUnary(node, ctx) {
  const v = evalNode(node.x, ctx);
  if (node.op === 'NOT') return Value.bool(!v.asBool(node.x.pos));
  return Value.num(D.negate(v.asDecimal(node.x.pos)));
}

function evalBinary(node, ctx) {
  const op = node.op;

  // Short-circuit before either side is touched (§5.5).
  if (op === 'AND' || op === 'OR') {
    const left = evalNode(node.l, ctx).asBool(node.l.pos);
    if (op === 'AND' && !left) return Value.bool(false);
    if (op === 'OR' && left) return Value.bool(true);
    return Value.bool(evalNode(node.r, ctx).asBool(node.r.pos));
  }

  const l = evalNode(node.l, ctx);
  const r = evalNode(node.r, ctx);
  const lp = node.l.pos, rp = node.r.pos;

  switch (op) {
    case '+': return Value.num(D.add(l.asDecimal(lp), r.asDecimal(rp)));
    case '-': return Value.num(D.sub(l.asDecimal(lp), r.asDecimal(rp)));
    case '*': return Value.num(D.mul(l.asDecimal(lp), r.asDecimal(rp)));
    case '/': return Value.num(D.div(l.asDecimal(lp), r.asDecimal(rp), node.pos));
    case '%': return Value.num(D.mod(l.asDecimal(lp), r.asDecimal(rp), node.pos));

    case '&': return concat(l, r, lp, rp);

    case '==': case '!=': case '<': case '<=': case '>': case '>=': {
      const c = D.cmp(l.asDecimal(lp), r.asDecimal(rp));
      return Value.bool(compareResult(op, c));
    }
    case '$==': case '$!=': case '$<': case '$<=': case '$>': case '$>=': {
      const c = bytesCompare(l.asBytes(lp), r.asBytes(rp));
      return Value.bool(compareResult(op.slice(1), c));
    }

    case 'EQL': return Value.bool(l.eql(r));
    case 'IN': return Value.bool(isIn(l, r));

    case 'XOR': return Value.bool(l.asBool(lp) !== r.asBool(rp));

    case 'BAND': case 'BOR': case 'BXOR':
      return bitwise(op, l.asBytes(lp), r.asBytes(rp), node.pos);
  }
  fail('E_SYNTAX', `unknown operator ${op}`, node.pos);
}

function compareResult(op, c) {
  switch (op) {
    case '==': return c === 0;
    case '!=': return c !== 0;
    case '<': return c < 0;
    case '<=': return c <= 0;
    case '>': return c > 0;
    case '>=': return c >= 0;
  }
}

// TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
function concat(l, r, lp, rp) {
  const lv = l.scalarSource(lp), rv = r.scalarSource(rp);
  if (lv.kind === BOOL) fail('E_NOT_TEXT', 'cannot concatenate a boolean', lp);
  if (rv.kind === BOOL) fail('E_NOT_TEXT', 'cannot concatenate a boolean', rp);
  if (lv.kind === TEXT && rv.kind === TEXT) return Value.text(lv.scalar + rv.scalar);
  const a = l.asBytes(lp), b = r.asBytes(rp);
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return Value.bin(out);
}

function isIn(needle, hay) {
  if (hay.size() === 0) return hay.eql(needle);
  for (const child of hay.values()) if (child.eql(needle)) return true;
  return false;
}

function bitwise(op, a, b, pos) {
  if (a.length !== b.length) {
    fail('E_LEN_MISMATCH', `${op} needs operands of equal length (${a.length} vs ${b.length})`, pos);
  }
  const out = new Uint8Array(a.length);
  for (let i = 0; i < a.length; i++) {
    out[i] = op === 'BAND' ? (a[i] & b[i]) : op === 'BOR' ? (a[i] | b[i]) : (a[i] ^ b[i]);
  }
  return Value.bin(out);
}

// --- assignment -------------------------------------------------------------

const COMPOUND = { '+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%', '&=': '&' };

function evalAssign(node, ctx) {
  const path = resolveTarget(node.target, ctx);
  const key = path[path.length - 1];

  let value;
  if (node.op === '=') {
    value = evalNode(node.value, ctx).clone();
  } else {
    const current = walkCreate(ctx, path, path.length - 1).get(key);
    if (current === undefined) {
      fail('E_UNDEF_VAR', `${node.op} needs an existing target`, node.target.pos);
    }
    const rhs = evalNode(node.value, ctx);
    const binOp = COMPOUND[node.op];
    const tp = node.target.pos, vp = node.value.pos;
    if (binOp === '&') {
      value = concat(current, rhs, tp, vp);
    } else {
      const a = current.asDecimal(tp), b = rhs.asDecimal(vp);
      const r = binOp === '+' ? D.add(a, b)
        : binOp === '-' ? D.sub(a, b)
          : binOp === '*' ? D.mul(a, b)
            : binOp === '/' ? D.div(a, b, node.pos)
              : D.mod(a, b, node.pos);
      value = Value.num(r);
    }
  }

  // Re-derived after the right-hand side ran, which may have replaced or
  // removed any level along the path.
  walkCreate(ctx, path, path.length - 1).set(key, value);
  return value;
}

// Walks from the root along `path`, creating any level that is missing, and
// returns the value at the end. Re-derived rather than remembered — see
// resolveTarget.
function walkCreate(ctx, path, upto) {
  let cur = ctx.root;
  for (let i = 0; i < upto; i++) {
    let next = cur.get(path[i]);
    if (next === undefined) {
      next = Value.none();
      cur.set(path[i], next);
    }
    cur = next;
  }
  return cur;
}

// Walks the target chain and returns the full key path, evaluating each index
// expression exactly once, left to right, and creating each intermediate level
// as it goes — so `A[COUNT(A)] = 1` sees the A the walk just created.
//
// A path rather than a live container reference (§5.7). The right-hand side may
// replace any level the walk just found; the assignment then lands in the tree
// that exists afterwards, rather than in an object that has been detached from
// it and which nothing can ever read.
function resolveTarget(target, ctx) {
  const chain = [];
  let n = target;
  while (n.t === 'index') { chain.unshift(n.idx); n = n.obj; }

  if (ctx.isBound(n.name)) {
    fail('E_BAD_ASSIGN', `${n.name} is an aggregate binder and cannot be assigned`, target.pos);
  }
  const path = [n.name];
  if (chain.length === 0) return path;

  if (ctx.root.get(n.name) === undefined) ctx.root.set(n.name, Value.none());

  for (let i = 0; i < chain.length - 1; i++) {
    const k = evalNode(chain[i], ctx).asText(chain[i].pos);
    const cur = walkCreate(ctx, path, path.length);
    if (cur.get(k) === undefined) cur.set(k, Value.none());
    path.push(k);
  }
  const last = chain[chain.length - 1];
  path.push(evalNode(last, ctx).asText(last.pos));
  return path;
}
