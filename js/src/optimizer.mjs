// Engine-independent pipeline rewrites shared by the in-memory and SQL lanes.
// The parser AST is treated as immutable by callers: every rewrite starts from
// shallow copies, and nested nodes are copied as they are visited.

import * as D from './decimal.mjs';
import { lookup } from './registry.mjs';
import { MAX_DEPTH } from './errors.mjs';
import { compileMathPlan, isMathOp } from './math_plan.mjs';

const PIPELINE_OPS = new Set([
  'FILTER', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
  'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
  'LINK', 'LINK_LEFT',
]);

function copyNode(node) {
  if (!node || typeof node !== 'object') return node;
  const copy = { ...node };
  if (node.args) copy.args = [...node.args];
  if (node.items) copy.items = [...node.items];
  return copy;
}

function unwindPipeline(node) {
  const steps = [];
  let current = node;
  while (current && current.t === 'call' && PIPELINE_OPS.has(current.name)
         && current.args.length > 0) {
    steps.unshift(current);
    current = current.args[0];
  }
  return { source: current, steps };
}

function buildPipeline(source, steps) {
  let current = source;
  for (const step of steps) {
    const next = copyNode(step);
    next.args = [current, ...step.args.slice(1)];
    current = next;
  }
  return current;
}

function literalBool(value, pos) { return { t: 'bool', v: value, pos }; }
function literalNum(value, pos) { return { t: 'num', v: value, pos }; }

// A fold that replaces a node by one of its children must not move the error
// position an operator over the result reports: spec §6.3 names the node that
// actually failed, and to the operator the operand IS the folded node, not the
// literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
// So a hoisted child is re-stamped with the folded node's position -- which is
// only exact for a leaf literal, the one shape that carries no positions of
// its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
// at its own column), so it is not a literal here.
const LITERAL_TYPES = new Set(['num', 'text', 'bool', 'null']);
function isLiteral(node) { return node != null && LITERAL_TYPES.has(node.t); }
function hoistLiteral(child, pos) { return { ...child, pos }; }

function textCompare(a, b) {
  const aa = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  const n = Math.min(aa.length, bb.length);
  for (let i = 0; i < n; i++) if (aa[i] !== bb[i]) return aa[i] < bb[i] ? -1 : 1;
  return aa.length - bb.length;
}

function fold(node) {
  if (!node) return node;
  if (node.t === 'un' && node.x) {
    if (node.op === 'NOT' && node.x.t === 'bool') return literalBool(!node.x.v, node.pos);
    if (node.op === 'NEG' && node.x.t === 'num') {
      try { return literalNum(D.format(D.negate(D.parse(node.x.v, node.pos))), node.pos); } catch (_) { return node; }
    }
    return node;
  }
  if (node.t === 'bin' && node.l && node.r) {
    if (node.op === 'AND') {
      if (node.l.t === 'bool' && !node.l.v) return literalBool(false, node.pos);
      if (node.l.t === 'bool' && node.r.t === 'bool') return literalBool(node.l.v && node.r.v, node.pos);
    }
    if (node.op === 'OR') {
      if (node.l.t === 'bool' && node.l.v) return literalBool(true, node.pos);
      if (node.l.t === 'bool' && node.r.t === 'bool') return literalBool(node.l.v || node.r.v, node.pos);
    }
    if (node.l.t === 'num' && node.r.t === 'num'
        && ['+', '-', '*', '/', '%'].includes(node.op)) {
      try {
        const left = D.parse(node.l.v, node.pos);
        const right = D.parse(node.r.v, node.pos);
        const result = node.op === '+' ? D.add(left, right, node.pos)
          : node.op === '-' ? D.sub(left, right, node.pos)
            : node.op === '*' ? D.mul(left, right, node.pos)
              : node.op === '/' ? D.div(left, right, node.pos)
                : D.mod(left, right, node.pos);
        return literalNum(D.format(result), node.pos);
      } catch (_) { return node; }
    }
    if (node.l.t === 'num' && node.r.t === 'num'
        && ['==', '!=', '<', '<=', '>', '>='].includes(node.op)) {
      try {
        const c = D.cmp(D.parse(node.l.v, node.pos), D.parse(node.r.v, node.pos));
        const value = node.op === '==' ? c === 0 : node.op === '!=' ? c !== 0
          : node.op === '<' ? c < 0 : node.op === '<=' ? c <= 0
            : node.op === '>' ? c > 0 : c >= 0;
        return literalBool(value, node.pos);
      } catch (_) { return node; }
    }
    if (node.l.t === 'text' && node.r.t === 'text'
        && ['$==', '$!=', '$<', '$<=', '$>', '$>='].includes(node.op)) {
      const c = textCompare(node.l.v, node.r.v);
      const value = node.op === '$==' ? c === 0 : node.op === '$!=' ? c !== 0
        : node.op === '$<' ? c < 0 : node.op === '$<=' ? c <= 0
          : node.op === '$>' ? c > 0 : c >= 0;
      return literalBool(value, node.pos);
    }
    return node;
  }
  if (node.t === 'call' && node.name === 'IF' && node.args.length === 3
      && node.args[0].t === 'bool') {
    const branch = node.args[node.args[0].v ? 1 : 2];
    return isLiteral(branch) ? hoistLiteral(branch, node.pos) : node;
  }
  return node;
}

function fieldRefs(node, binder = '_') {
  const result = [];
  const visit = (item) => {
    if (!item) return;
    if (item.t === 'index' && item.obj.t === 'var' && item.idx.t === 'text'
        && [binder, '_', '_1', '_2'].some((name) => name.toUpperCase() === item.obj.name.toUpperCase())) {
      result.push(item.idx.v);
    }
    if (item.args) item.args.forEach(visit);
    if (item.items) item.items.forEach(visit);
    if (item.l) visit(item.l);
    if (item.r) visit(item.r);
    if (item.x) visit(item.x);
    if (item.target) visit(item.target);
    if (item.value) visit(item.value);
    if (item.t === 'index') { visit(item.obj); visit(item.idx); }
  };
  visit(node);
  return [...new Set(result)];
}

// Whether `node` reads one of `names` as a variable -- other than as
// `name["field"]`, which is a field read. Case-insensitively, like the
// evaluator's frames.
function readsVar(node, names) {
  const wanted = new Set(names.map((name) => name.toUpperCase()));
  let found = false;
  const visit = (item) => {
    if (!item || found) return;
    if (item.t === 'var' && wanted.has(item.name.toUpperCase())) { found = true; return; }
    if (item.t === 'index' && item.obj.t === 'var' && item.idx.t === 'text') return;
    if (item.args) item.args.forEach(visit);
    if (item.items) item.items.forEach(visit);
    visit(item.l);
    visit(item.r);
    visit(item.x);
    visit(item.obj);
    visit(item.idx);
    visit(item.target);
    visit(item.value);
  };
  visit(node);
  return found;
}

// Whether a body reads the element as a whole (its binder, or any of the
// pipeline's implicit names) or its key `_K`. The field set says what a
// rewrite may rely on; this says when it may not: a body that reads either
// cannot move across a step that changes the rows' shape (MAP, SELECT_COLS)
// or renumbers them (MAP, SELECT_COLS, the sorts).
function readsRowOrKey(node, binder = '_') {
  return readsVar(node, [binder, '_', '_1', '_2', '_K']);
}

// Whether a step's own arguments (not its input) read `_K`: the keys a sort
// renumbers, so such a step keeps its place relative to one.
function stepReadsKey(step) {
  return step.args.slice(1).some((arg) => readsVar(arg, ['_K']));
}

// Whether the step after a FILTER hides where the FILTER ran. FILTER keeps its
// input's keys (spec §7.3) and MAP, SELECT_COLS and the sorts renumber, so a
// FILTER moved in front of one of them carries the source's keys where the
// program as written carried the step's -- visible in the answer, and in any
// later `_K`. Only a following step that renumbers again without reading `_K`
// hides that; the end of the pipeline, or another FILTER, does not.
function keysRenumberedBy(step) {
  return step !== undefined && step.name !== 'FILTER' && !stepReadsKey(step);
}

// Whether the source a pipeline starts from is a list already, so a FILTER
// whose predicate is a constant TRUE over it is the identity. Over a scalar
// it is not: FILTER wraps a scalar into a one-element list (spec §7.3), and
// only a later step, a list literal or a constructor is known not to be one.
function sourceIsList(source) {
  return source.t === 'list'
    || (source.t === 'call' && (source.name === 'LIST' || source.name === 'RECORD'));
}

function mapDetails(step) {
  const args = step.args;
  const explicit = args.length === 3 && args[1].t === 'var' && !args[1].grouped;
  const binder = explicit ? args[1].name : '_';
  const body = explicit ? args[2] : args[1];
  return { binder, body, explicit };
}

function mapPassthroughs(step) {
  const { binder, body } = mapDetails(step);
  if (!body || body.t !== 'call' || body.name !== 'RECORD') return [];
  const fields = [];
  for (let i = 0; i + 1 < body.args.length; i += 2) {
    const key = body.args[i], value = body.args[i + 1];
    if (key.t === 'text' && value.t === 'index' && value.obj.t === 'var'
        && value.obj.name.toUpperCase() === binder.toUpperCase() && value.idx.t === 'text'
        && value.idx.v === key.v) fields.push(key.v);
  }
  return fields;
}

// Whether evaluating NODE for one row can raise -- conservatively: a rewrite
// that moves a FILTER in front of a step, runs a step on fewer rows, or fuses
// two FILTERs changes which rows reach what, so it may only pass over
// expressions that cannot raise on any of them (spec §7.3; review 2026-09-25
// SEM-07/SEM-08). Literals, _K and the binder itself never raise. On the
// logical path the rows are a bound relation's, which always carry their
// typed columns, so a field read through the binder cannot raise either, nor
// a comparison, AND/OR/NOT or + - * over such reads; `/` and `%` (E_DIV_ZERO),
// calls and anything else may. The in-memory path has no schema, and a field
// read there can raise E_NO_KEY.
const SAFE_LOGICAL_OPS = new Set(['==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
  'AND', 'OR', '+', '-', '*']);
function cannotRaise(node, binder, logical) {
  if (!node) return true;
  switch (node.t) {
    case 'num': case 'text': case 'bool': case 'null': return true;
    case 'var': {
      const name = node.name.toUpperCase();
      return name === '_K' || name === binder.toUpperCase();
    }
    case 'index':
      return logical && node.obj && node.obj.t === 'var' && node.obj.name.toUpperCase() === binder.toUpperCase()
        && node.idx && node.idx.t === 'text';
    case 'bin':
      return logical && SAFE_LOGICAL_OPS.has(node.op)
        && cannotRaise(node.l, binder, logical) && cannotRaise(node.r, binder, logical);
    case 'un':
      return logical && node.op === 'NOT' && cannotRaise(node.x, binder, logical);
    default:
      return false;
  }
}

// Every field a MAP computes (or its whole body) cannot raise.
function mapCannotRaise(step, logical) {
  const { binder, body } = mapDetails(step);
  if (body && body.t === 'call' && body.name === 'RECORD') {
    for (let i = 1; i < body.args.length; i += 2) if (!cannotRaise(body.args[i], binder, logical)) return false;
    return body.args.every((arg, i) => i % 2 === 1 || arg.t === 'text');
  }
  return cannotRaise(body, binder, logical);
}

// A FILTER predicate that cannot raise: it has to be boolean-valued as well as
// free of failing reads, because a bare variable, a number or a text is exactly
// what E_NOT_BOOL is raised for (`FILTER(_)` over numbers). Only what is boolean
// by construction qualifies -- a boolean literal, or a comparison / AND / OR /
// NOT over operands that cannot raise (the last only where the caller says the
// data is typed, `logical`).
function predicateCannotRaise(node, binder, logical) {
  if (!node) return false;
  if (node.t === 'bool') return true;
  if (node.t === 'bin' || node.t === 'un') return cannotRaise(node, binder, logical);
  return false;
}

// How deep an expression goes, its root counted as 1, and never more than `cap`
// + 1 (the walk stops there), so it is bounded whatever the source's length.
function boundedDepth(root, cap) {
  let deepest = 0;
  let level = [root];
  while (level.length > 0 && deepest <= cap) {
    deepest++;
    const next = [];
    for (const node of level) {
      if (!node || typeof node !== 'object') continue;
      if (node.args) next.push(...node.args);
      if (node.items) next.push(...node.items);
      for (const key of ['l', 'r', 'x', 'obj', 'idx', 'value']) if (node[key]) next.push(node[key]);
    }
    level = next;
  }
  return deepest;
}

function mapHasComputedFields(step) {
  const { body } = mapDetails(step);
  if (!body || body.t !== 'call' || body.name !== 'RECORD') return true;
  return mapPassthroughs(step).length * 2 !== body.args.length;
}

function filterDetails(step) {
  const args = step.args;
  const explicit = args.length === 3 && args[1].t === 'var' && !args[1].grouped;
  const valid = args.length === 2 || explicit;
  return {
    binder: explicit ? args[1].name : '_',
    predicate: explicit ? args[2] : args[1],
    explicit,
    valid,
  };
}

function sortDetails(step) {
  const args = step.args;
  const count = args.length;
  let binder = '_', key = null;
  if (step.name === 'SORT' || step.name === 'SORT_DESC') {
    if (count === 1) return { binder: null, key: null };
    binder = count === 3 && args[1].t === 'var' && !args[1].grouped ? args[1].name : '_';
    key = count === 3 ? args[2] : args[1];
  } else if (step.name === 'TOP' || step.name === 'TOP_DESC') {
    if (count === 2) return { binder: null, key: null };
    const sortCount = count - 1;
    // TOP(source, key, n) has three arguments and
    // TOP(source, binder, key, n) has four. `sortCount` excludes n, so the
    // explicit-binder form is 3, not 4.
    binder = sortCount === 3 && args[1].t === 'var' && !args[1].grouped ? args[1].name : '_';
    key = sortCount === 3 ? args[2] : args[1];
  } else if (step.name === 'SORT_BY' || step.name === 'TOP_BY') {
    const sortCount = step.name === 'TOP_BY' ? count - 1 : count;
    if (sortCount === 2 || sortCount === 3 && args[2]?.t === 'text') {
      key = args[1];
    } else if (args[1]?.t === 'var' && !args[1].grouped) {
      binder = args[1].name;
      key = args[2];
    }
  }
  return { binder, key };
}

function selectFields(step) {
  const result = [];
  for (const arg of step.args.slice(1)) {
    if (arg.t === 'list') {
      for (const item of arg.items) if (item.t === 'text') result.push(item.v);
    } else if (arg.t === 'text') result.push(arg.v);
  }
  return result;
}

function numericLiteral(node) {
  if (!node || node.t !== 'num') return null;
  try {
    const n = BigInt(node.v);
    return n >= 0n && n <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(n) : null;
  } catch (_) { return null; }
}

function renameVar(node, oldName, newName) {
  if (!node) return node;
  const copy = copyNode(node);
  if (copy.t === 'var' && copy.name.toUpperCase() === oldName.toUpperCase()) copy.name = newName;
  if (copy.args) copy.args = copy.args.map((item) => renameVar(item, oldName, newName));
  if (copy.items) copy.items = copy.items.map((item) => renameVar(item, oldName, newName));
  if (copy.l) copy.l = renameVar(copy.l, oldName, newName);
  if (copy.r) copy.r = renameVar(copy.r, oldName, newName);
  if (copy.x) copy.x = renameVar(copy.x, oldName, newName);
  if (copy.obj) copy.obj = renameVar(copy.obj, oldName, newName);
  if (copy.idx) copy.idx = renameVar(copy.idx, oldName, newName);
  if (copy.target) copy.target = renameVar(copy.target, oldName, newName);
  if (copy.value) copy.value = renameVar(copy.value, oldName, newName);
  return copy;
}

function logicalSteps(source, steps, options = {}) {
  let current = steps;
  let changed = true;
  while (changed) {
    changed = false;
    const next = [];
    for (let i = 0; i < current.length; i++) {
      const first = current[i], second = current[i + 1];
      if (second && first.name === 'TAKE' && second.name === 'TAKE'
          && first.args.length === 2 && second.args.length === 2) {
        const left = numericLiteral(first.args[1]);
        const right = numericLiteral(second.args[1]);
        if (left !== null && right !== null) {
          const merged = copyNode(first);
          merged.args = [first.args[0], literalNum(String(Math.min(left, right)), second.args[1].pos)];
          next.push(merged);
          i++;
          changed = true;
          continue;
        }
      }
      if (second && first.name === 'DROP' && second.name === 'DROP'
          && first.args.length === 2 && second.args.length === 2) {
        const left = numericLiteral(first.args[1]);
        const right = numericLiteral(second.args[1]);
        if (left !== null && right !== null) {
          const merged = copyNode(first);
          merged.args = [first.args[0], literalNum(String(left + right), second.args[1].pos)];
          next.push(merged);
          i++;
          changed = true;
          continue;
        }
      }
      // Fused only for a numeric literal count of at least 1 (SPEC 6.2): the
      // plain tree evaluates every sort key, and may fail on one, before it looks
      // at the count, and a count that is an expression may have effects the keys
      // would see. A literal 0 is left as SORT then TAKE, which still evaluates
      // the keys of an empty result.
      if (second && second.name === 'TAKE'
          && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(first.name)
          && second.args.length === 2
          && (numericLiteral(second.args[1]) ?? 0) >= 1) {
        const topName = first.name === 'SORT' ? 'TOP' : first.name === 'SORT_DESC' ? 'TOP_DESC' : 'TOP_BY';
        next.push(copyNode({ ...first, name: topName, spec: lookup(topName), args: [...first.args, second.args[1]] }));
        i++;
        changed = true;
        continue;
      }
      if (second && first.name === 'MAP' && second.name === 'FILTER') {
        const passes = mapPassthroughs(first);
        const details = filterDetails(second);
        const refs = fieldRefs(details.predicate, details.binder);
        if (details.valid && refs.length > 0 && refs.every((field) => passes.includes(field))
            && !readsRowOrKey(details.predicate, details.binder)
            && keysRenumberedBy(current[i + 2]) && mapCannotRaise(first, options.logical)) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (second && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(first.name) && second.name === 'FILTER'
          && !stepReadsKey(second) && keysRenumberedBy(current[i + 2])
          && cannotRaise(sortDetails(first).key, sortDetails(first).binder || '_', options.logical)
          && (options.logical || cannotRaise(filterDetails(second).predicate, filterDetails(second).binder, false))) {
        next.push(second, first);
        i++;
        changed = true;
        continue;
      }
      if (second && first.name === 'SELECT_COLS' && second.name === 'FILTER') {
        const details = filterDetails(second);
        const refs = fieldRefs(details.predicate, details.binder);
        if (details.valid && refs.length > 0 && refs.every((field) => selectFields(first).includes(field))
            && !readsRowOrKey(details.predicate, details.binder)
            && keysRenumberedBy(current[i + 2])) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (second && first.name === 'MAP'
          && ['TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY'].includes(second.name)
          && mapHasComputedFields(first)) {
        // Only a key over pass-through fields is the same value before the
        // MAP: a keyless sort compares the MAP's outputs, and a key that
        // reads the whole row or `_K` reads what the MAP changes.
        const details = sortDetails(second);
        const refs = details.key ? fieldRefs(details.key, details.binder || '_') : [];
        if (details.key && refs.length > 0 && refs.every((field) => mapPassthroughs(first).includes(field))
            && !readsRowOrKey(details.key, details.binder || '_')
            && mapCannotRaise(first, options.logical) && cannotRaise(details.key, details.binder || '_', options.logical)) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (options.fuseFilters !== false && second && first.name === 'FILTER' && second.name === 'FILTER') {
        const left = filterDetails(first), right = filterDetails(second);
        // Fused, the second predicate runs on a row before the first has seen
        // the rows after it: only one that cannot raise may be fused.
        if (!left.valid || !right.valid || !predicateCannotRaise(right.predicate, right.binder, options.logical)) {
          next.push(first);
          continue;
        }
        // Fused, the second predicate sits one level deeper than it did: the
        // AND that joins them. A fused pair must spend what the two stages spent
        // (SPEC 6.4), so a predicate that reaches the cap that way stays a
        // second FILTER.
        if (second.stepDepth !== undefined
            && second.stepDepth + boundedDepth(right.predicate, MAX_DEPTH) + 1 > MAX_DEPTH) {
          next.push(first);
          continue;
        }
        const predicate = right.binder.toUpperCase() === left.binder.toUpperCase()
          ? right.predicate : renameVar(right.predicate, right.binder, left.binder);
        const merged = copyNode(first);
        const body = { t: 'bin', op: 'AND', l: left.predicate, r: predicate, pos: left.predicate.pos };
        merged.args = left.explicit
          ? [first.args[0], first.args[1], body]
          : [first.args[0], body];
        next.push(merged);
        i++;
        changed = true;
        continue;
      }
      // No rule drops a sort followed by another sort: the sorts are stable,
      // so the first is the second's tie-breaker (rel.sort.then-sort-keeps-
      // the-tie-order), and a rule that removed it changed the value.
      if (second && ['DISTINCT', 'DEDUPE'].includes(first.name)
          && ['DISTINCT', 'DEDUPE'].includes(second.name)) {
        next.push(first);
        i++;
        changed = true;
        continue;
      }
      const firstFilter = first.name === 'FILTER' ? filterDetails(first) : null;
      if (firstFilter?.valid && firstFilter.predicate.t === 'bool' && firstFilter.predicate.v
          && (next.length > 0 || i > 0 || sourceIsList(source))) {
        changed = true;
        continue;
      }
      next.push(first);
    }
    current = next;
  }
  return current;
}

// The evaluator resolves the three-argument SORT_BY / TOP_BY form by shape
// (spec §7.3): a text literal in the third slot is the direction, otherwise a
// bare name in the second slot is the binder and the third slot is its key.
// A fold that hoists a text literal into that slot -- `IF(TRUE, "DESC",
// "ASC")` -- would change the form, so the slot is walked without folding.
function stepArgOptions(step, index, options) {
  const sortCount = step.name === 'SORT_BY' ? step.args.length
    : step.name === 'TOP_BY' ? step.args.length - 1 : 0;
  if (sortCount === 3 && index === 2 && step.args[1].t === 'var' && !step.args[1].grouped) {
    return { ...options, foldConstants: false };
  }
  return options;
}

function optimizeTree(node, physical, depth = 1, options = {}, inMath = false) {
  if (!node) return node;
  // The evaluator/SQL normaliser owns the public depth error and its source
  // position. optimizeRoot never descends into a tree that reaches the cap;
  // this guard keeps the walk bounded should a rewrite ever deepen one.
  if (depth > MAX_DEPTH) return node;
  if (node.t === 'call' && PIPELINE_OPS.has(node.name)) {
    const { source, steps } = unwindPipeline(node);
    const optimizedSource = optimizeTree(source, physical, depth + 1, options, false);
    const optimizedSteps = steps.map((step, index) => {
      const copy = copyNode(step);
      // Where this step stands in the tree as written, for the rules that would
      // deepen a subtree (FILTER fusion): the outermost step is the node itself.
      copy.stepDepth = depth + (steps.length - 1 - index);
      copy.args = [copy.args[0], ...copy.args.slice(1).map((item, offset) =>
        optimizeTree(item, physical, depth + 1, stepArgOptions(step, offset + 1, options), false))];
      return copy;
    });
    let finalSteps = logicalSteps(optimizedSource, optimizedSteps, { ...options, logical: !physical });
    if (physical) {
      // A tree fact the evaluator's join pre-filter needs (SEL-0050): whether
      // anything can see the keys a FILTER's result carries. A following step
      // that renumbers without reading `_K` hides them (keysRenumberedBy, the
      // same notion the logical rewrites use); the end of the pipeline or
      // another FILTER does not. Stamped on the body node of the physical
      // copy, never on the caller's AST.
      finalSteps.forEach((step, index) => {
        if (step.name === 'FILTER') {
          const body = copyNode(step.args[step.args.length - 1]);
          body.keysUnobserved = keysRenumberedBy(finalSteps[index + 1]);
          step.args[step.args.length - 1] = body;
        }
      });
    }
    // Whatever the rewrites did, the pipeline's outermost node is still the one
    // an operator over it sees, so it keeps the position of the call it replaces
    // (spec 6.3: the node that actually failed). A source that is a bare variable
    // is left alone, being a leaf that can fail at its own column.
    const built = buildPipeline(optimizedSource, finalSteps);
    if (built.pos === node.pos || (finalSteps.length === 0 && built.t === 'var')) return built;
    return { ...built, pos: node.pos };
  }

  const isCurrMath = isMathOp(node);
  const nextInMath = isCurrMath;

  const copy = copyNode(node);
  if (copy.args) copy.args = copy.args.map((item) => optimizeTree(item, physical, depth + 1, options, nextInMath));
  if (copy.items) copy.items = copy.items.map((item) => optimizeTree(item, physical, depth + 1, options, false));
  if (copy.l) copy.l = optimizeTree(copy.l, physical, depth + 1, options, nextInMath);
  if (copy.r) copy.r = optimizeTree(copy.r, physical, depth + 1, options, nextInMath);
  if (copy.x) copy.x = optimizeTree(copy.x, physical, depth + 1, options, nextInMath);
  if (copy.obj) copy.obj = optimizeTree(copy.obj, physical, depth + 1, options, false);
  if (copy.idx) copy.idx = optimizeTree(copy.idx, physical, depth + 1, options, false);
  // An assignment's target (only assign nodes have one) is walked iteratively
  // by the evaluator and by the dependency scanner; recursively charging it
  // here would reject a valid value-depth boundary before the evaluator can
  // report it at the outermost index, as required by the host-neutrality cases.
  if (copy.value) copy.value = optimizeTree(copy.value, physical, depth + 1, options, false);
  // Keep the evaluator's depth guard observable for deliberately deep source
  // expressions. Constant folding is useful for ordinary expressions, but it
  // must not collapse a 5,000-node depth-limit probe into one literal.
  const folded = options.foldConstants === false ? copy : fold(copy);
  if (physical && !inMath && isMathOp(folded)) {
    const plan = compileMathPlan(folded);
    if (plan) folded.mathPlan = plan;
  }
  return folded;
}

// Whether any node of the tree lies past the evaluator's depth cap, counted
// the way the evaluator counts: the root at 1, every child one deeper, an
// assignment's target excluded (the evaluator walks it iteratively). The walk
// stops at the cap, so it is bounded however deep the tree is.
function exceedsDepth(node, depth) {
  if (!node || typeof node !== 'object') return false;
  if (depth > MAX_DEPTH) return true;
  const next = depth + 1;
  if (node.args) for (const item of node.args) if (exceedsDepth(item, next)) return true;
  if (node.items) for (const item of node.items) if (exceedsDepth(item, next)) return true;
  for (const key of ['l', 'r', 'x', 'obj', 'idx']) {
    if (node[key] && exceedsDepth(node[key], next)) return true;
  }
  if (node.value && exceedsDepth(node.value, next)) return true;
  return false;
}

// The evaluator is the depth authority (spec §6.4): a tree that reaches the
// cap is evaluated as written, so it is returned as written. Folding at the
// boundary erased the E_DEPTH the evaluator raises for a chain of 201
// additions (each of them foldable), and a rewrite that lifts a child would
// move it; not rewriting loses nothing, because such a tree either raises or
// keeps its deep part on a branch that is never evaluated.
function optimizeRoot(ast, physical, options) {
  return exceedsDepth(ast, 1) ? ast : optimizeTree(ast, physical, 1, options);
}

export function optimizeAstLogical(ast, options = {}) { return optimizeRoot(ast, false, options); }
export function optimizeAstInMemory(ast) { return optimizeRoot(ast, true, {}); }
export function optimizeAst(ast) { return optimizeAstInMemory(ast); }
export { PIPELINE_OPS, unwindPipeline, buildPipeline };
