// Engine-independent pipeline rewrites shared by the in-memory and SQL lanes.
// The parser AST is treated as immutable by callers: every rewrite starts from
// shallow copies, and nested nodes are copied as they are visited.

import * as D from './decimal.mjs';
import { lookup } from './registry.mjs';
import { MAX_DEPTH } from './errors.mjs';

const PIPELINE_OPS = new Set([
  'FILTER', 'GROUP_BY', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
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

function call(name, args, pos) {
  const spec = lookup(name);
  return { t: 'call', name, spec, args, pos };
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
      if (node.l.t === 'bool' && !node.l.v) return node.l;
      if (node.l.t === 'bool' && node.r.t === 'bool') return literalBool(node.l.v && node.r.v, node.pos);
    }
    if (node.op === 'OR') {
      if (node.l.t === 'bool' && node.l.v) return node.l;
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
    return node.args[node.args[0].v ? 1 : 2];
  }
  return node;
}

function walkNode(node, predicate) {
  if (!node) return false;
  if (predicate(node)) return true;
  if (node.args && node.args.some((item) => walkNode(item, predicate))) return true;
  if (node.items && node.items.some((item) => walkNode(item, predicate))) return true;
  if (node.l && walkNode(node.l, predicate)) return true;
  if (node.r && walkNode(node.r, predicate)) return true;
  if (node.x && walkNode(node.x, predicate)) return true;
  if (node.obj && walkNode(node.obj, predicate)) return true;
  if (node.idx && walkNode(node.idx, predicate)) return true;
  if (node.target && walkNode(node.target, predicate)) return true;
  return !!(node.value && walkNode(node.value, predicate));
}

function nodeHasVar(node, name) {
  return walkNode(node, (item) => item.t === 'var' && item.name.toUpperCase() === name.toUpperCase());
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

function mapDetails(step) {
  const args = step.args;
  const explicit = args.length === 3 && args[1].t === 'var' && !args[1].grouped;
  const binder = explicit ? args[1].name : '_';
  const body = explicit ? args[2] : args[1];
  return { binder, body, explicit };
}

function mapPassthroughs(step) {
  const { binder, body } = mapDetails(step);
  if (!body || body.t !== 'call' || (body.name !== 'RECORD' && body.name !== 'LAZY_RECORD')) return [];
  const fields = [];
  for (let i = 0; i + 1 < body.args.length; i += 2) {
    const key = body.args[i], value = body.args[i + 1];
    if (key.t === 'text' && value.t === 'index' && value.obj.t === 'var'
        && value.obj.name.toUpperCase() === binder.toUpperCase() && value.idx.t === 'text'
        && value.idx.v === key.v) fields.push(key.v);
  }
  return fields;
}

function mapHasComputedFields(step) {
  const { body } = mapDetails(step);
  if (!body || body.t !== 'call' || (body.name !== 'RECORD' && body.name !== 'LAZY_RECORD')) return true;
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
    } else if (sortCount === 3 && args[1].t === 'var' && !args[1].grouped) {
      binder = args[1].name;
      key = args[2];
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

function splitAnd(node) {
  return node && node.t === 'bin' && node.op === 'AND'
    ? [...splitAnd(node.l), ...splitAnd(node.r)] : [node];
}

function combineAnd(nodes, pos) {
  if (nodes.length === 0) return null;
  let result = nodes[0];
  for (const node of nodes.slice(1)) result = { t: 'bin', op: 'AND', l: result, r: node, pos: pos || result.pos };
  return result;
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

function logicalSteps(steps, options = {}) {
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
      if (second && second.name === 'TAKE'
          && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(first.name)
          && second.args.length === 2) {
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
        if (details.valid && refs.length > 0 && refs.every((field) => passes.includes(field))) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (second && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(first.name) && second.name === 'FILTER') {
        next.push(second, first);
        i++;
        changed = true;
        continue;
      }
      if (second && first.name === 'SELECT_COLS' && second.name === 'FILTER') {
        const details = filterDetails(second);
        const refs = fieldRefs(details.predicate, details.binder);
        if (details.valid && refs.length > 0 && refs.every((field) => selectFields(first).includes(field))) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (second && first.name === 'MAP'
          && ['TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY'].includes(second.name)
          && mapHasComputedFields(first)) {
        const details = sortDetails(second);
        const refs = details.key ? fieldRefs(details.key, details.binder || '_') : [];
        if (refs.length === 0 || refs.every((field) => mapPassthroughs(first).includes(field))) {
          next.push(second, first);
          i++;
          changed = true;
          continue;
        }
      }
      if (options.fuseFilters !== false && second && first.name === 'FILTER' && second.name === 'FILTER') {
        const left = filterDetails(first), right = filterDetails(second);
        if (!left.valid || !right.valid) {
          next.push(first);
          continue;
        }
        const predicate = right.binder.toUpperCase() === left.binder.toUpperCase()
          ? right.predicate : renameVar(right.predicate, right.binder, left.binder);
        const merged = copyNode(first);
        merged.args = left.explicit
          ? [first.args[0], first.args[1], { t: 'bin', op: 'AND', l: left.predicate, r: predicate, pos: left.predicate.pos }]
          : [first.args[0], { t: 'bin', op: 'AND', l: left.predicate, r: predicate, pos: left.predicate.pos }];
        next.push(merged);
        i++;
        changed = true;
        continue;
      }
      if (second && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(first.name)
          && ['SORT', 'SORT_DESC', 'SORT_BY'].includes(second.name)) {
        next.push(second);
        i++;
        changed = true;
        continue;
      }
      if (second && ['DISTINCT', 'DEDUPE'].includes(first.name)
          && ['DISTINCT', 'DEDUPE'].includes(second.name)) {
        next.push(first);
        i++;
        changed = true;
        continue;
      }
      const firstFilter = first.name === 'FILTER' ? filterDetails(first) : null;
      if (firstFilter?.valid && firstFilter.predicate.t === 'bool' && firstFilter.predicate.v) {
        changed = true;
        continue;
      }
      next.push(first);
    }
    current = next;
  }
  return current;
}

function pushdownJoinFilters(steps) {
  const result = [];
  let changed = false;
  for (let i = 0; i < steps.length; i++) {
    const link = steps[i], filter = steps[i + 1];
    if (!filter || !['LINK', 'LINK_LEFT'].includes(link.name) || filter.name !== 'FILTER') {
      result.push(link);
      continue;
    }
    const leftSource = link.args[0], rightSource = link.args[1];
    const leftBinder = link.args.length === 5 && link.args[2].t === 'var' && !link.args[2].grouped
      ? link.args[2].name : singleRelationName(leftSource) || '_1';
    const rightBinder = link.args.length === 5 && link.args[3].t === 'var' && !link.args[3].grouped
      ? link.args[3].name : singleRelationName(rightSource) || '_2';
    const leftNames = new Set([leftBinder, '_1']
      .map((name) => name.toUpperCase()));
    const rightNames = new Set([rightBinder, '_2']
      .map((name) => name.toUpperCase()));
    for (const name of collectPipelineSourceNames(leftSource)) leftNames.add(name.toUpperCase());
    for (const name of collectPipelineSourceNames(rightSource)) rightNames.add(name.toUpperCase());
    const info = filterDetails(filter);
    if (!info.valid) {
      result.push(link);
      continue;
    }
    const left = [], right = [], remaining = [];
    const classify = (node) => {
      let hasLeft = false, hasRight = false, ambiguous = false, unknown = false;
      const visit = (item) => {
        if (!item) return;
        if (item.t === 'index') {
          if (item.obj?.t === 'index' && item.obj.obj?.t === 'var'
              && item.obj.idx?.t === 'text'
              && [info.binder.toUpperCase(), '_'].includes(item.obj.obj.name.toUpperCase())) {
            const table = item.obj.idx.v.toUpperCase();
            if (leftNames.has(table)) hasLeft = true;
            else if (rightNames.has(table)) hasRight = true;
            else unknown = true;
            return;
          }
          if (item.obj?.t === 'var' && item.idx?.t === 'text') {
            const name = item.obj.name.toUpperCase();
            if (leftNames.has(name)) hasLeft = true;
            else if (rightNames.has(name)) hasRight = true;
            else if (name === info.binder.toUpperCase() || name === '_') ambiguous = true;
            else unknown = true;
            return;
          }
        }
        if (item.t === 'var') {
          const name = item.name.toUpperCase();
          if (name !== info.binder.toUpperCase() && name !== '_') {
            if (leftNames.has(name)) hasLeft = true;
            else if (rightNames.has(name)) hasRight = true;
            else unknown = true;
          }
        }
        if (item.args) item.args.forEach(visit);
        if (item.items) item.items.forEach(visit);
        visit(item.l); visit(item.r); visit(item.x);
        visit(item.obj); visit(item.idx);
        visit(item.target); visit(item.value);
      };
      visit(node);
      return { hasLeft, hasRight, ambiguous, unknown };
    };
    const rewrite = (node, targetNames, binder) => {
      if (!node) return node;
      const copy = copyNode(node);
      if (copy.t === 'index') {
        if (copy.obj?.t === 'index' && copy.obj.obj?.t === 'var'
            && copy.obj.idx?.t === 'text'
            && [binder.toUpperCase(), '_'].includes(copy.obj.obj.name.toUpperCase())
            && targetNames.has(copy.obj.idx.v.toUpperCase())) {
          return { t: 'index', obj: { t: 'var', name: '_', pos: copy.obj.obj.pos },
            idx: copy.idx, pos: copy.pos };
        }
        if (copy.obj?.t === 'var' && copy.idx?.t === 'text'
            && targetNames.has(copy.obj.name.toUpperCase())) {
          return { t: 'index', obj: { t: 'var', name: '_', pos: copy.obj.pos },
            idx: copy.idx, pos: copy.pos };
        }
      }
      if (copy.args) copy.args = copy.args.map((item) => rewrite(item, targetNames, binder));
      if (copy.items) copy.items = copy.items.map((item) => rewrite(item, targetNames, binder));
      if (copy.l) copy.l = rewrite(copy.l, targetNames, binder);
      if (copy.r) copy.r = rewrite(copy.r, targetNames, binder);
      if (copy.x) copy.x = rewrite(copy.x, targetNames, binder);
      if (copy.obj) copy.obj = rewrite(copy.obj, targetNames, binder);
      if (copy.idx) copy.idx = rewrite(copy.idx, targetNames, binder);
      if (copy.target) copy.target = rewrite(copy.target, targetNames, binder);
      if (copy.value) copy.value = rewrite(copy.value, targetNames, binder);
      return copy;
    };
    for (const conjunct of splitAnd(info.predicate)) {
      const affinity = classify(conjunct);
      if (affinity.hasLeft && !affinity.hasRight && !affinity.ambiguous && !affinity.unknown) {
        left.push(rewrite(conjunct, leftNames, info.binder));
      } else if (affinity.hasRight && !affinity.hasLeft && !affinity.ambiguous
          && !affinity.unknown && link.name === 'LINK') {
        right.push(rewrite(conjunct, rightNames, info.binder));
      } else {
        remaining.push(conjunct);
      }
    }
    if (left.length === 0 && right.length === 0) {
      result.push(link);
      continue;
    }
    if (left.length) {
      result.push(call('FILTER', [leftSource, combineAnd(left, filter.pos)], filter.pos));
    }
    let newLink = link;
    if (right.length) {
      newLink = copyNode(link);
      newLink.args = [...link.args];
      newLink.args[1] = call('FILTER', [rightSource, combineAnd(right, filter.pos)], filter.pos);
    }
    result.push(newLink);
    if (remaining.length) {
      const newFilter = copyNode(filter);
      newFilter.args = info.explicit
        ? [newLink, filter.args[1], combineAnd(remaining, filter.pos)]
        : [newLink, combineAnd(remaining, filter.pos)];
      result.push(newFilter);
    }
    changed = true;
    i++;
  }
  return { steps: result, changed };
}

function singleRelationName(node) {
  if (!node) return null;
  if (node.t === 'var') return node.name;
  if (node.t === 'call' && node.args?.length
      && node.name !== 'LINK' && node.name !== 'LINK_LEFT') {
    return singleRelationName(node.args[0]);
  }
  return null;
}

function collectPipelineSourceNames(node) {
  const names = [];
  const seen = new Set();
  const add = (name) => {
    const upper = name.toUpperCase();
    if (!seen.has(upper)) {
      seen.add(upper);
      names.push(name);
    }
  };
  const visit = (item) => {
    if (!item) return;
    if (item.t === 'var') {
      add(item.name);
      return;
    }
    if (item.t !== 'call' || !item.args?.length) return;
    if (item.name === 'LINK' || item.name === 'LINK_LEFT') {
      visit(item.args[0]);
      visit(item.args[1]);
    } else if (PIPELINE_OPS.has(item.name)) {
      visit(item.args[0]);
    }
  };
  visit(node);
  return names;
}

function optimizeTree(node, physical, depth = 1, options = {}) {
  if (!node) return node;
  // The evaluator/SQL normaliser owns the public depth error and its source
  // position.  Stop rewriting at the same boundary, but leave the remaining
  // tree intact so a later walk reports the normative E_DEPTH/E_SQL_DEPTH at
  // the node that actually exceeds the limit.
  if (depth > MAX_DEPTH) return node;
  if (node.t === 'call' && PIPELINE_OPS.has(node.name)) {
    const { source, steps } = unwindPipeline(node);
    const optimizedSource = optimizeTree(source, physical, depth + 1, options);
    const optimizedSteps = steps.map((step) => {
      const copy = copyNode(step);
      copy.args = [copy.args[0], ...copy.args.slice(1).map((item) => optimizeTree(item, physical, depth + 1, options))];
      return copy;
    });
    let finalSteps = logicalSteps(optimizedSteps, options);
    if (physical) {
      while (true) {
        const pushed = pushdownJoinFilters(finalSteps);
        finalSteps = pushed.steps;
        if (!pushed.changed) break;
        finalSteps = logicalSteps(finalSteps, options);
      }
    }
    if (physical) {
      finalSteps = finalSteps.map((step) => {
        const copy = copyNode(step);
        const details = copy.name === 'MAP' ? mapDetails(copy) : null;
        if (details && details.body && details.body.t === 'call'
            && details.body.name === 'RECORD' && details.body.args.length >= 4) {
          const body = copyNode(details.body);
          body.name = 'LAZY_RECORD';
          body.spec = lookup('LAZY_RECORD');
          copy.args = details.explicit
            ? [copy.args[0], copy.args[1], body] : [copy.args[0], body];
        }
        return copy;
      });
    }
    return buildPipeline(optimizedSource, finalSteps);
  }
  const copy = copyNode(node);
  if (copy.args) copy.args = copy.args.map((item) => optimizeTree(item, physical, depth + 1, options));
  if (copy.items) copy.items = copy.items.map((item) => optimizeTree(item, physical, depth + 1, options));
  if (copy.l) copy.l = optimizeTree(copy.l, physical, depth + 1, options);
  if (copy.r) copy.r = optimizeTree(copy.r, physical, depth + 1, options);
  if (copy.x) copy.x = optimizeTree(copy.x, physical, depth + 1, options);
  if (copy.obj) copy.obj = optimizeTree(copy.obj, physical, depth + 1, options);
  if (copy.idx) copy.idx = optimizeTree(copy.idx, physical, depth + 1, options);
  // Assignment targets are walked iteratively by the evaluator and by the
  // dependency scanner; recursively charging them here would reject a valid
  // value-depth boundary before the evaluator can report it at the outermost
  // index, as required by the host-neutrality cases.
  if (copy.t === 'assign') {
    if (copy.value) copy.value = optimizeTree(copy.value, physical, depth + 1, options);
  } else if (copy.target) {
    copy.target = optimizeTree(copy.target, physical, depth + 1, options);
    if (copy.value) copy.value = optimizeTree(copy.value, physical, depth + 1, options);
  } else if (copy.value) {
    copy.value = optimizeTree(copy.value, physical, depth + 1, options);
  }
  // Keep the evaluator's depth guard observable for deliberately deep source
  // expressions. Constant folding is useful for ordinary expressions, but it
  // must not collapse a 5,000-node depth-limit probe into one literal.
  return options.foldConstants === false ? copy : fold(copy);
}

export function optimizeAstLogical(ast, options = {}) { return optimizeTree(ast, false, 1, options); }
export function optimizeAstInMemory(ast) { return optimizeTree(ast, true); }
export function optimizeAst(ast) { return optimizeAstInMemory(ast); }
export { PIPELINE_OPS, unwindPipeline, buildPipeline };
