// Does SEL itself accept this expression?
//
// The translator's job is to answer "can this rule be pushed into that database".
// It was answering that question without ever asking a prior one: is the rule
// *valid*. `LEFT("abc", -1)` translates cleanly into every dialect here, and SEL
// raises E_RANGE for it while MariaDB answers `''`, PostgreSQL answers `'ab'` and
// SQLite answers `'abc'`. Three databases, three answers, none of them SEL's —
// from a translation that reported success.
//
// That is the core promise inverted. So where an expression's arguments are all
// literals, the values SEL would reject are known *here*, and this asks SEL.
//
// It is validation, not constant folding. The value is computed and thrown away;
// the translation that follows is byte-identical to the one that would have been
// emitted without this check. Folding would have been the tempting second step
// and would have blinded the oracle: an expression replaced by its answer no
// longer exercises the database's version of the operation, which is the only
// thing sql/oracle/ exists to compare.
//
// What it cannot do is check a value it does not have. `LEFT(col, -1)` is exactly
// as wrong and passes, because `col` is a column and its value is not knowable at
// translation time. See docs/internals/sql-translation.md §11.4.

import { SelError, MAX_DEPTH } from '../errors.mjs';
import { Context, evalNode } from '../eval.mjs';
import { Value } from '../value.mjs';
import { format as formatDecimal } from '../decimal.mjs';
import { refuse } from './errors.mjs';
import { asciiUpper } from '../lexer.mjs';
import { bindingForm } from '../registry.mjs';

// The static walks below stop with their conservative answer at this depth. They
// recurse over a tree already capped at MAX_DEPTH, and stop short of it to leave
// headroom for the caller's frames.
const WALK_DEPTH = MAX_DEPTH - 20;

// Whether the node is an IF or COND whose every result is a text literal -- or,
// in turn, such a conditional (SEL-0057). SQL's CASE returns the literal it
// chose byte for byte, so its identity is SEL's; a number it can re-spell
// (MariaDB types `CASE ... THEN 1 ELSE 1.0` as DECIMAL(2,1)), and a column or a
// computation is not a literal at all. A two-argument IF's otherwise is "" (§7.2),
// a text literal too.
function textLiteralResults(node, depth = 0) {
  if (!node || depth >= WALK_DEPTH || node.t !== 'call' || !['IF', 'COND'].includes(node.name)) return false;
  const args = node.args;
  let results;
  if (node.name === 'IF') {
    results = args.slice(1);
  } else {
    if (args.length < 3 || args.length % 2 === 0) return false;
    results = [...args.filter((_, i) => i % 2 === 1), args[args.length - 1]];
  }
  return results.every((r) => !!r && (r.t === 'text' || textLiteralResults(r, depth + 1)));
}

function identityProjection(node, depth = 0) {
  if (!node || depth >= WALK_DEPTH) return false;
  if (['var', 'num', 'text', 'bool', 'null'].includes(node.t)) return true;
  if (node.t === 'index') return identityProjection(node.obj, depth + 1) && identityProjection(node.idx, depth + 1);
  if (node.t === 'call') {
    if (['COUNT', 'LEN', 'BLEN'].includes(node.name)) return true;
    // One spelling per value (§7.6): whatever computed the argument, the
    // canonical form's identity is its value's, which SQL computes exactly
    // wherever the translation does not declare otherwise (a caveat).
    if (node.name === 'CANON') return true;
    if (textLiteralResults(node, depth)) return true;
    if (node.name === 'RECORD') return node.args.length % 2 === 0
      && node.args.every((n, i) => i % 2 === 0 || identityProjection(n, depth + 1));
  }
  return false;
}

function identityInputs(n, depth = 0) {
  if (!n || depth >= WALK_DEPTH) return true;
  if (['num', 'text', 'bool', 'null'].includes(n.t)) return new Set();
  if (n.t === 'var') return n.name === '_K' ? new Set() : true;
  if (n.t === 'index') return n.idx.t !== 'text' ? true : n.obj.t === 'var' ? new Set([n.idx.v]) : identityInputs(n.obj, depth + 1);
  if (n.t === 'call' && ['COUNT', 'LEN', 'BLEN'].includes(n.name)) return new Set();
  // The canonical form does not depend on how its argument is spelled, so no
  // field it reads needs its identity kept upstream.
  if (n.t === 'call' && n.name === 'CANON') return new Set();
  // The literal a conditional chose does not depend on how any field it tests
  // is spelled.
  if (textLiteralResults(n, depth)) return new Set();
  if (n.t === 'list' || (n.t === 'call' && ['LIST', 'RECORD'].includes(n.name))) {
    const items = n.t === 'list' ? n.items : n.name === 'RECORD' ? n.args.filter((_, i) => i % 2) : n.args;
    const out = new Set();
    for (const item of items) {
      const fields = identityInputs(item, depth + 1);
      if (fields === true) return true;
      for (const f of fields) out.add(f);
    }
    return out;
  }
  return true;
}

export function identityLossBeforeGrouping(node, needed = false) {
  while (node && node.t === 'call' && node.args?.length) {
    if ((needed === true || needed?.size > 0) && (node.name === 'MAP' || (node.name === 'BUCKET' && node.args.length > 2))) {
      const body = node.args.at(-1);
      let values = [body];
      if (needed instanceof Set && body.t === 'call' && body.name === 'RECORD') {
        const found = new Set(); values = [];
        for (let i = 0; i + 1 < body.args.length; i += 2) {
          const k = body.args[i];
          if (k.t === 'text' && needed.has(k.v)) { found.add(k.v); values.push(body.args[i + 1]); }
        }
        if ([...needed].some((k) => !found.has(k))) return true;
      }
      if (!values.every((v) => identityProjection(v))) return true;
      needed = new Set();
      for (const v of values) {
        const fields = identityInputs(v);
        needed = needed === true || fields === true ? true : new Set([...needed, ...fields]);
      }
    }
    if (node.name === 'BUCKET') needed = identityInputs(node.args[node.args.length === 4 ? 2 : 1]);
    if (['DISTINCT', 'DEDUPE'].includes(node.name)) needed = true;
    if (needed instanceof Set && ['LINK', 'LINK_LEFT'].includes(node.name) && node.args[1].t === 'var') {
      const right = node.args.length === 5 ? node.args[3].name : node.args[1].name;
      needed = new Set([...needed].filter((k) => asciiUpper(k) !== right));
    }
    node = node.args[0];
  }
  return false;
}

// Is this node an aggregate's binder NAME, rather than a read of one?
//
// The evaluator's rule, in Args.symbol: a bare `var` node that did NOT come from
// parentheses. Both halves matter and the second was missing here, in every host
// that had this check. `(C)` parses as a `var` node carrying the parser's `grouped` flag --
// which is exactly what makes `(A) = 1` an E_BAD_ASSIGN -- so testing only the
// kind accepted a binder the evaluator refuses with E_EXPECT_SYMBOL, and
// `ALL(V, (C), C > 0)` translated to working SQL for a rule that can never run.
// A translation that is accepted where the language refuses is the one direction
// this layer must never fail in.
export function isBinderName(node) {
  return node.t === 'var' && !node.grouped;
}

// The value bindings, as a name set and an evaluation context.
//
// A `value` binding is a constant the translator *has* — §5.4 calls it "a
// constant supplied at translation time, inlined as a literal", and
// `Translator.#variable` hands it straight to literal(). So `LEFT("abc", X)` with
// X bound to "-1" is exactly as knowable as `LEFT("abc", -1)`, and before this it
// was exactly as wrong: SEL raised E_RANGE and the four servers answered '', '',
// 'ab' and ''. §11.4's headline defect, still open through the documented way a
// host passes a parameter.
//
// Only scalars are lifted. A list-valued binding is what an aggregate iterates
// and its shape is the translator's business, not the evaluator's.
export function scope(bindings) {
  const names = new Map();
  const root = Value.none();
  if (bindings === null || bindings === undefined) return [names, new Context(root)];
  for (const name of bindings.names()) {
    const b = bindings.get(name);
    if (b.kind !== 'value') continue;
    const v = b.value;
    if (!(v instanceof Value) || v.isNone() || v.size() > 0) continue;
    names.set(name, true);
    root.set(name, v);
  }
  return [names, new Context(root)];
}

// Whether every leaf under `n` is a literal.
//
// A binder an aggregate introduces inside `n` counts as bound, so
// `ALL((1, 2), _ > 0)` is constant and `ALL(ITEMS, _ > 0)` is not. That is the
// same rule the evaluator applies, which is what lets the whole node be handed to
// it below.
//
// The answer for a node depends only on the node and on `bound`, and the translator asks
// it again at every level of a nest (is this node constant? is its parent? its
// grandparent?), which made a deep constant expression quadratic. The answers
// are kept per `bound` map, which the translator builds once and never changes.
const CONSTANT_MEMO = new WeakMap();

export function isConstant(n, bound = null) {
  const t = n.t;
  if (t === 'num' || t === 'text' || t === 'bool') return true;
  const b = bound ?? new Map();
  if (t === 'var') return b.has(n.name);
  let memo;
  if (bound !== null) {
    memo = CONSTANT_MEMO.get(bound);
    if (memo === undefined) { memo = new WeakMap(); CONSTANT_MEMO.set(bound, memo); }
    const cached = memo.get(n);
    if (cached !== undefined) return cached;
  }
  let r;
  if (t === 'un') r = isConstant(n.x, b);
  else if (t === 'bin') r = isConstant(n.l, b) && isConstant(n.r, b);
  else if (t === 'index') r = isConstant(n.obj, b) && isConstant(n.idx, b);
  else if (t === 'list') r = n.items.every((item) => isConstant(item, b));
  else if (t === 'call') r = constantCall(n, b);
  // clist (stage 1 builds it; the evaluator has never seen it and cannot evaluate it),
  // assign and seq (gone by now), and an unknown node type are not constant, so nothing
  // is validated and the walk refuses them in the ordinary way.
  else r = false;
  if (memo !== undefined) memo.set(n, r);
  return r;
}

// The binding form is the only reason this is not three lines.
//
// `MAP(list, X, X + 1)` names its binder in argument 1 and uses it in argument 2;
// the two-argument form binds `_` implicitly. Neither name is a free variable, so
// neither disqualifies the call — but the *source* still has to be constant, or
// the body has nothing to iterate. Which argument is which is the manifest's
// form (registry.bindingForm), as for stage 1 and the dependency walk: an outer
// argument is constant in `bound`, an inner one with the form's names bound too,
// and a binder slot is a name, never a read.
function constantCall(n, bound) {
  const args = n.args;
  const form = bindingForm(n.name, args, n.spec);
  if (form === null) return args.every((a) => isConstant(a, bound));
  let inner = null;
  for (let i = 0; i < args.length; i++) {
    const scope = form.scopes[i];
    if (scope === 'binder') {
      // Malformed; not constant, and aggShape refuses it for real.
      if (!isBinderName(args[i])) return false;
      continue;
    }
    if (scope === 'inner') {
      if (inner === null) {
        inner = new Map(bound);
        for (const name of form.binds) inner.set(name, true);
      }
      if (!isConstant(args[i], inner)) return false;
    } else if (!isConstant(args[i], bound)) {
      return false;
    }
  }
  return true;
}

// Evaluate a constant node, once.
//
// validate() runs at every constant node on the way up, and each run used to evaluate the
// whole subtree again, so `LEN(REPEAT("x", 20000)) + ... + ...` k levels deep evaluated the
// REPEAT k times over (quadratic). A node that evaluated without error has a value
// that cannot change: constants hold no frame, no assignment and no host state. So the
// value is kept on the translation's context, and the next evaluation up the tree runs on
// a copy of its node in which every already-evaluated descendant is replaced by its value
// (a 'cval' node, which the evaluator returns as is). The error a failing evaluation
// raises, and its position, are the innermost node's, exactly as before: a failing
// descendant was refused when it was validated, before any ancestor is asked.
// The values, per translation context: a side table rather than a field
// written onto the evaluator's Context, which does not declare one.
const CONST_VALUES = new WeakMap();

function evalConstant(n, ctx) {
  const c = ctx ?? new Context();
  let vals = CONST_VALUES.get(c);
  if (vals === undefined) { vals = new WeakMap(); CONST_VALUES.set(c, vals); }
  const hit = vals.get(n);
  if (hit !== undefined) return hit;
  const v = evalNode(frozenChildren(n, vals), c);
  vals.set(n, v);
  return v;
}

function frozen(child, vals) {
  const v = vals.get(child);
  if (v !== undefined) return { t: 'cval', v, pos: child.pos };
  return frozenChildren(child, vals);
}

function frozenChildren(n, vals) {
  switch (n.t) {
    case 'bin': {
      const l = frozen(n.l, vals), r = frozen(n.r, vals);
      return l === n.l && r === n.r ? n : { ...n, l, r };
    }
    case 'un': {
      const x = frozen(n.x, vals);
      return x === n.x ? n : { ...n, x };
    }
    case 'index': {
      const obj = frozen(n.obj, vals), idx = frozen(n.idx, vals);
      return obj === n.obj && idx === n.idx ? n : { ...n, obj, idx };
    }
    case 'list': {
      const items = n.items.map((item) => frozen(item, vals));
      return items.every((item, i) => item === n.items[i]) ? n : { ...n, items };
    }
    case 'call': {
      const args = n.args.map((a) => frozen(a, vals));
      return args.every((a, i) => a === n.args[i]) ? n : { ...n, args };
    }
    default: return n;
  }
}

// Evaluate `n` the way SEL would, and refuse the translation if SEL refuses the
// expression.
//
// Called *after* the node has been translated, not before, so that every refusal
// the translator already had keeps its own message. `TRUE + 1` is an expression
// SEL rejects and also a BOOL where a number is required; the second is the more
// useful sentence and is the one an author reading it can act on, and it is the
// same sentence `FLAG + 1` gets, where no value is known and only the kind check
// can fire. This check adds refusals where translation used to *succeed* — which
// is the whole of the defect it exists for — and changes none of the ones that
// already existed. ABORT and an unportable regex are refused by name on the way
// past and never reach here.
//
// The position reported is SEL's own — the innermost node that failed, not the
// outermost one this was called with — because that is the character the author
// has to change.
export function validate(n, ctx = null) {
  try {
    evalConstant(n, ctx);
  } catch (e) {
    refuseAsSel(e, n);
  }
}

// The same question asked of one *operand* rather than a whole expression.
//
// validate() above only fires where the entire node is knowable, and that turned
// out to be the wrong shape: `T + (1 + "x")` refused while `(T + 1) + "x"` — the
// same expression, differently parenthesised — translated, because one column
// anywhere in the node switched the check off. Whether a defect is caught may not
// depend on where the author put brackets.
//
// A constant in a numeric position is knowable on its own, and what it settles
// does not depend on the rest: `"x"` is not a number, so SEL raises E_NOT_NUM
// whatever the column holds. Refusing therefore loses nothing — there is no value
// of the other operand that SEL would have answered.
//
// The test is the constant's VALUE and never a declared kind. A TEXT column
// holding numerals is a legitimate schema and `A == 5` must keep translating,
// because SEL's == compares numerically and a bare `=` between two text columns
// would answer FALSE where SEL answers TRUE. That is pinned as
// op.compare.coerce-variant-when-a-side-is-not, and again as
// const.numeric.a-text-column-still-coerces so this check cannot grow into it.
export function requireNumeric(n, ctx = null) {
  try {
    evalConstant(n, ctx).asDecimal(n.pos);
  } catch (e) {
    refuseAsSel(e, n);
  }
}

// The canonical spelling of a constant that is TEXT holding a number, or null when it
// is anything else (or SEL refuses it: the operand's own translation reports that).
// In arithmetic SEL computes with such a text exactly, and MariaDB and MySQL
// would convert the quoted string to DOUBLE (`'0.1' + '0.2' = 0.3` is false there), so
// the translator spells it as the exact numeric literal it stands for.
export function numericTextConstant(n, ctx = null) {
  let v;
  try {
    v = evalConstant(n, ctx);
  } catch (e) {
    if (e instanceof SelError) return null;
    throw e;
  }
  if (!v.isText() || !v.looksNumeric()) return null;
  return formatDecimal(v.asDecimal(n.pos));
}

// The number of fractional digits of a constant already known to be a number
// (requireNumeric ran first).
export function constantScale(n, ctx = null) {
  return evalConstant(n, ctx).asDecimal(n.pos).scale;
}

// SEL's own refusal, reported as the translator's.
//
// The position is SEL's own — the innermost node that failed, not the outermost
// one this was entered at — because that is the character the author has to
// change. Anything that is not a SelError did not come from SEL and is not this
// layer's to reword, so it leaves the way it arrived.
export function refuseAsSel(e, n) {
  if (!(e instanceof SelError)) throw e;
  // pos in this host IS the token object, so an equivalent literal is what a
  // reconstructed Pos would be elsewhere.
  const pos = e.line > 0 ? { line: e.line, col: e.col, offset: e.offset } : n.pos;
  // Depth is the translation's limit, not SEL judging the expression invalid: a
  // constant chain past the evaluator's cap is the same E_SQL_DEPTH a column
  // chain gets (errors.md), and blaming SEL for evaluating something it
  // evaluates fine misdescribes it.
  if (e.code === 'E_DEPTH') {
    refuse('E_SQL_DEPTH',
      `this expression nests deeper than SEL will evaluate (${e.message}), so there `
      + 'is nothing to translate', pos);
  }
  refuse('E_SQL_INVALID',
    `SEL rejects this expression (${e.code}: ${e.message}), so there is nothing `
    + 'to translate; a database would answer something rather than fail', pos);
}
