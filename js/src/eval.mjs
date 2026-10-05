// The evaluator, and the argument framework built-ins are written against.
//
// Nothing here catches a SelError. An error surfaces from the innermost node
// that failed, carrying that node's position, and no layer rewrites it.

import { fail, MAX_DEPTH } from './errors.mjs';
import * as D from './decimal.mjs';
import { Value, NONE, TEXT, BOOL } from './value.mjs';
import { bytesCompare, compareText, encodeUtf8 } from './utf8.mjs';
import { cpLength, checkText, checkCollection, MAX_TEXT_LEN, checkSizedInt, MAX_SCALE, MAX_POWER } from './budget.mjs';
import { OpCode } from './math_plan.mjs';

// Exported so the SQL translator can say "as deep as the evaluator counts"
// rather than repeating 200, the same way python/sel/sql does.
// Re-exported so that sel.mjs, which asks for the same cap on the static walk,
// keeps naming the module that owns the depth rather than the one that owns
// the number.
export { MAX_DEPTH };

export class Context {
  constructor(root) {
    this.root = root || Value.none();
    this.frames = [];     // aggregate binders: Map<name, Value>
    this.depth = 0;
    // A FILTER whose source is a LINK hands the join its conjuncts here, for
    // the join to test on the rows it joins where that is provably the same as
    // filtering the joined rows (builtins/structure.mjs, doLink; SEL-0052,
    // SEL-0054).
    this.joinPrefilter = null;
    // What a join below reported after applying them: which conjuncts every
    // row that came up has passed, and whether any row was kept on an error
    // -- in which case the join above applies them all again.
    this.joinPrefilterReport = null;
  }

  lookup(name) {
    for (let i = this.frames.length - 1; i >= 0; i--) {
      const v = this.frames[i].get(name);
      if (v !== undefined) return v;
    }
    const v = this.root.get(name);
    return v === undefined ? undefined : v;
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
    this.recordShape = node.recordShape ?? null;
    this.name = node.name;
    this.pos = node.pos;
    this.ctx = ctx;
    this._vals = new Array(node.args.length);
  }

  count() { return this.nodes.length; }

  // An argument the call does not have. A host function is handed these
  // accessors and may ask for any index (spec/SPEC.md 8.1): the answer is
  // E_BAD_ARG at the call, never a TypeError from reading past the array.
  at(i) {
    const n = this.nodes[i];
    if (n === undefined) {
      fail('E_BAD_ARG', `${this.name}: no argument ${Number.isInteger(i) ? i + 1 : String(i)} (the call has ${this.nodes.length})`, this.pos);
    }
    return n;
  }
  node(i) { return this.at(i); }
  posOf(i) { return this.at(i).pos; }

  val(i) {
    if (this._vals[i] === undefined) this._vals[i] = evalNode(this.at(i), this.ctx);
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
    return D.truncToNumber(d);
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
    const n = this.at(i);
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
    if (node.mathPlan) return evalMathPlan(node.mathPlan, ctx);
    return evalDispatch(node, ctx);
  } finally {
    ctx.depth--;
  }
}

const DEC_NEG_ONE = { neg: true, digits: 1n, scale: 0 };
const DEC_ZERO = { neg: false, digits: 0n, scale: 0 };
const DEC_ONE = { neg: false, digits: 1n, scale: 0 };

// A slot holds either a decimal (an operation's result, a constant) or the VALUE
// a load produced. The operation that consumes it coerces it, at the position of
// the node it came from -- after every operand has been evaluated, and left
// before right, which is the order the plain tree uses (SPEC 6.2). Coercing at
// load time instead made the plan report E_NOT_NUM for `"abc" + 1/0` where the
// plain tree reports E_DIV_ZERO, and read a variable at load time as a snapshot
// where the plain tree reads it when the operator runs (SPEC 3.4).
const asNum = (x, pos) => (x instanceof Value ? x.asDecimal(pos) : x);

function evalMathPlan(plan, ctx) {
  const scratchpad = new Array(plan.scratchpadSize);
  for (let i = 0; i < plan.steps.length; i++) {
    const step = plan.steps[i];
    switch (step.op) {
      case OpCode.LOAD_VAR: {
        const val = ctx.lookup(step.name);
        if (val === undefined) fail('E_UNDEF_VAR', `undefined variable ${step.name}`, step.pos);
        scratchpad[step.dst] = val;
        break;
      }
      case OpCode.LOAD_CONST:
        scratchpad[step.dst] = step.constVal;
        break;
      case OpCode.LOAD_LEAF:
        scratchpad[step.dst] = evalNode(step.leafNode, ctx);
        break;
      case OpCode.ADD: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.add(a, b, step.pos);
        break;
      }
      case OpCode.SUB: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.sub(a, b, step.pos);
        break;
      }
      case OpCode.MUL: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.mul(a, b, step.pos);
        break;
      }
      case OpCode.DIV: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.div(a, b, step.pos);
        break;
      }
      case OpCode.MOD: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.mod(a, b, step.pos);
        break;
      }
      case OpCode.NEG:
        scratchpad[step.dst] = D.negate(asNum(scratchpad[step.src1], step.p1));
        break;
      case OpCode.ABS:
        scratchpad[step.dst] = D.abs(asNum(scratchpad[step.src1], step.p1));
        break;
      case OpCode.COERCE:
        scratchpad[step.dst] = asNum(scratchpad[step.src1], step.p1);
        break;
      case OpCode.SIGN: {
        const s = D.sign(asNum(scratchpad[step.src1], step.p1));
        scratchpad[step.dst] = s < 0 ? DEC_NEG_ONE : (s === 0 ? DEC_ZERO : DEC_ONE);
        break;
      }
      case OpCode.CEIL:
        scratchpad[step.dst] = D.ceil(asNum(scratchpad[step.src1], step.p1), step.pos);
        break;
      case OpCode.FLOOR:
        scratchpad[step.dst] = D.floor(asNum(scratchpad[step.src1], step.p1), step.pos);
        break;
      case OpCode.TRUNC:
        scratchpad[step.dst] = D.trunc(asNum(scratchpad[step.src1], step.p1));
        break;
      case OpCode.ROUND: {
        const x = asNum(scratchpad[step.src1], step.p1);
        const n = checkSizedInt(asNum(scratchpad[step.src2], step.p2), 'ROUND', 2, MAX_SCALE, 'ROUND scale', step.auxPos);
        scratchpad[step.dst] = D.round(x, n, step.pos);
        break;
      }
      case OpCode.POWER: {
        const x = asNum(scratchpad[step.src1], step.p1);
        const n = checkSizedInt(asNum(scratchpad[step.src2], step.p2), 'POWER', 2, MAX_POWER, 'POWER exponent', step.auxPos);
        scratchpad[step.dst] = D.power(x, n, step.pos);
        break;
      }
      case OpCode.MIN: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.cmp(b, a) < 0 ? b : a;
        break;
      }
      case OpCode.MAX: {
        const a = asNum(scratchpad[step.src1], step.p1), b = asNum(scratchpad[step.src2], step.p2);
        scratchpad[step.dst] = D.cmp(b, a) > 0 ? b : a;
        break;
      }
    }
  }
  // A slot a step computed is a decimal the guard has already passed; anything
  // else keeps the checked constructor, and its refusal.
  const out = scratchpad[plan.outputSlot];
  return out instanceof Value ? Value.num(out) : Value.numOwned(out);
}

function evalDispatch(node, ctx) {
  switch (node.t) {
    case 'num': {
      const v = Value.textOwned(node.v);
      if (node.dec) v._decimal = node.dec;
      return v;
    }
    case 'text': return Value.textOwned(node.v);
    // A value already computed (sql/constants.mjs evalConstant): nothing to evaluate.
    case 'cval': return node.v;
    case 'bool': return Value.bool(node.v);
    case 'null': return Value.null();

    case 'var': {
      const v = ctx.lookup(node.name);
      if (v === undefined) fail('E_UNDEF_VAR', `undefined variable ${node.name}`, node.pos);
      return v;
    }

    case 'index': {
      // An index over a bare variable reads the variable in place: the index
      // costs its level and the variable none. Every host reads it that way (the
      // fast path), so the limit in `A["a"] AND A["a"] AND ...` is one level
      // deeper than counting the variable as a node of its own would put it;
      // the boundary is pinned by lim.eval-depth.aggregate-body-*.
      let obj;
      if (node.obj.t === 'var') {
        obj = ctx.lookup(node.obj.name);
        if (obj === undefined) fail('E_UNDEF_VAR', `undefined variable ${node.obj.name}`, node.obj.pos);
      } else {
        obj = evalNode(node.obj, ctx);
      }
      const key = node.idx.t === 'text' ? node.idx.v : evalNode(node.idx, ctx).asText(node.idx.pos);
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
  const out = [];
  for (const item of node.items) {
    const v = evalNode(item, ctx);
    if (v.kind === NONE && v.size() > 0) {
      checkCollection(out.length + v.size(), node.pos, '`,` result');
      for (const child of v.values()) out.push(child.cloneAt(2, node.pos));
    } else {
      checkCollection(out.length + 1, node.pos, '`,` result');
      out.push(v.cloneAt(2, node.pos));
    }
  }
  return Value.listOwned(out);
}

function evalUnary(node, ctx) {
  const v = evalNode(node.x, ctx);
  if (node.op === 'NOT') return Value.bool(!v.asBool(node.x.pos));
  return Value.numOwned(D.negate(v.asDecimal(node.x.pos)));
}

function evalBinary(node, ctx) {
  const op = node.op;

  // Short-circuit before either side is touched (§5.5, §5.6).
  if (op === 'AND' || op === 'OR') {
    const left = evalNode(node.l, ctx).asBool(node.l.pos);
    if (op === 'AND' && !left) return Value.bool(false);
    if (op === 'OR' && left) return Value.bool(true);
    return Value.bool(evalNode(node.r, ctx).asBool(node.r.pos));
  }

  // ?? falls back on NULL, ??? on any vacuous value; both on a missing key
  // or name.
  if (op === '??' || op === '???') {
    let l;
    try {
      l = evalNode(node.l, ctx);
    } catch (e) {
      if (e.code === 'E_NO_KEY' || e.code === 'E_UNDEF_VAR') {
        return evalNode(node.r, ctx);
      }
      throw e;
    }
    if (op === '??' ? l.isNull() : l.isVacuous()) return evalNode(node.r, ctx);
    return l;
  }

  const l = evalNode(node.l, ctx);
  const rn = node.r;
  // A numeric literal on the right of an arithmetic or numeric-comparison operator is
  // read as its decimal: no Value is built for it. What evalNode would have
  // done for it is kept -- the depth check, after the left side ran -- and nothing
  // else can happen to a literal.
  const rdec = (rn.t === 'num' && rn.dec !== undefined && NUMERIC_BINARY.has(op)) ? rn.dec : null;
  let r = null;
  if (rdec !== null) {
    if (ctx.depth >= MAX_DEPTH) fail('E_DEPTH', 'evaluation nested too deeply', rn.pos);
  } else {
    r = evalNode(rn, ctx);
  }
  const lp = node.l.pos, rp = rn.pos;

  switch (op) {
    case '+': return Value.numOwned(D.add(l.asDecimal(lp), rdec ?? r.asDecimal(rp), node.pos));
    case '-': return Value.numOwned(D.sub(l.asDecimal(lp), rdec ?? r.asDecimal(rp), node.pos));
    case '*': return Value.numOwned(D.mul(l.asDecimal(lp), rdec ?? r.asDecimal(rp), node.pos));
    case '/': return Value.numOwned(D.div(l.asDecimal(lp), rdec ?? r.asDecimal(rp), node.pos));
    case '%': return Value.numOwned(D.mod(l.asDecimal(lp), rdec ?? r.asDecimal(rp), node.pos));

    case '&': return concat(l, r, lp, rp, node.pos);

    case '==': case '!=': case '<': case '<=': case '>': case '>=': {
      const c = D.cmp(l.asDecimal(lp), rdec ?? r.asDecimal(rp));
      return Value.bool(compareResult(op, c, node.pos));
    }
    case '$==': case '$!=': case '$<': case '$<=': case '$>': case '$>=': {
      const a = l.asTextOrBytes(lp), b = r.asTextOrBytes(rp);
      let c;
      if (typeof a === 'string' && typeof b === 'string') c = compareText(a, b);
      else c = bytesCompare(typeof a === 'string' ? encodeUtf8(a, lp) : a, typeof b === 'string' ? encodeUtf8(b, rp) : b);
      return Value.bool(compareResult(op.slice(1), c, node.pos));
    }

    case 'EQL': return Value.bool(l.eql(r, node.pos));
    case 'IN': return Value.bool(isIn(l, r));

    case 'XOR': return Value.bool(l.asBool(lp) !== r.asBool(rp));

    case 'BAND': case 'BOR': case 'BXOR':
      return bitwise(op, l.asBytes(lp), r.asBytes(rp), node.pos);
  }
  fail('E_SYNTAX', `unknown operator ${op}`, node.pos);
}

const NUMERIC_BINARY = new Set(['+', '-', '*', '/', '%', '==', '!=', '<', '<=', '>', '>=']);

// Each arithmetic operator's decimal operation: compound assignment and the
// optimiser's constant folding dispatch through it. (evalBinary and the math
// plan keep their own switch, on the hot path, over the same five.)
export const ARITHMETIC = Object.freeze({ '+': D.add, '-': D.sub, '*': D.mul, '/': D.div, '%': D.mod });

// The six comparisons, and nothing else.
//
// This switch had no default, so an operator it did not name fell off the end
// as `undefined` and Value.bool made that FALSE -- a comparison operator added
// to the parser and forgotten here answered FALSE for every pair of operands
// and reported nothing. Unreachable today, since the caller only reaches this
// with the six, and that is the point of saying so out loud rather than
// answering.
export function compareResult(op, c, pos) {
  switch (op) {
    case '==': return c === 0;
    case '!=': return c !== 0;
    case '<': return c < 0;
    case '<=': return c <= 0;
    case '>': return c > 0;
    case '>=': return c >= 0;
  }
  fail('E_SYNTAX', `unknown comparison operator ${op}`, pos);
}

// TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
function concat(l, r, lp, rp, pos) {
  const lv = l.scalarSource(lp), rv = r.scalarSource(rp);
  if (lv.kind === BOOL) fail('E_NOT_TEXT', 'cannot concatenate a boolean', lp);
  if (rv.kind === BOOL) fail('E_NOT_TEXT', 'cannot concatenate a boolean', rp);
  if (lv.kind === TEXT && rv.kind === TEXT) {
    // Code units bound code points, so the exact count is only taken near the cap.
    const units = lv.scalar.length + rv.scalar.length;
    if (units > MAX_TEXT_LEN) checkText(cpLength(lv.scalar) + cpLength(rv.scalar), pos, '& result');
    return Value.textOwned(lv.scalar + rv.scalar);
  }
  const a = l.asBytes(lp), b = r.asBytes(rp);
  checkText(a.length + b.length, pos, '& result');
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return Value.binOwned(out);
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
  return Value.binOwned(out);
}

// --- assignment -------------------------------------------------------------

const COMPOUND = { '+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%', '&=': '&' };

function evalAssign(node, ctx) {
  const path = resolveTarget(node.target, ctx);
  const key = path[path.length - 1];

  let value;
  if (node.op === '=') {
    // The copy is made at the depth it will stand at: the path to the target
    // plus the value's own nesting is what SPEC 6.4 caps, and the error is the
    // assignment target's. `path` counts the variable and every bracket, so the
    // stored value's root is `path.length` levels down.
    value = evalNode(node.value, ctx);
    if (node.value.t === 'list') {
      // A `,` result is a fresh container whose children were copied when it
      // was built, so nothing else can reach it: storing it needs no second
      // copy, only the depth check the copy would have made.
      value.checkDepthAt(path.length, node.target.pos);
    } else {
      value = value.cloneAt(path.length, node.target.pos);
    }
  } else {
    const current = walkCreate(ctx, path, path.length - 1).get(key);
    if (current === undefined) {
      fail('E_UNDEF_VAR', `${node.op} needs an existing target`, node.target.pos);
    }
    const rhs = evalNode(node.value, ctx);
    const binOp = COMPOUND[node.op];
    const tp = node.target.pos, vp = node.value.pos;
    if (binOp === '&') {
      value = concat(current, rhs, tp, vp, node.pos);
    } else {
      const a = current.asDecimal(tp), b = rhs.asDecimal(vp);
      value = Value.numOwned(ARITHMETIC[binOp](a, b, node.pos));
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
  // The chain was walked iteratively, which is why nothing has counted it yet:
  // `A[1][2][3]` is a chain of index nodes, not a nesting of them, so neither the
  // parser's depth nor the evaluator's ever sees it -- and the value it is about
  // to build is one level deeper than the chain is long. Uncounted, that built a
  // value deeper than clone, eql and dump can walk, so the assignment succeeded and
  // reading the result back afterwards failed.
  if (chain.length + 1 > MAX_DEPTH) {
    fail('E_DEPTH', 'value nested too deeply', target.pos);
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
