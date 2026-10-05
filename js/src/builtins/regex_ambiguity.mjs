// The exponential-ambiguity rule of SPEC §7.8: refuse, at compile time, a pattern
// that a backtracking engine can be made to run in exponential time on. One
// static rule, identical in every host (tools/regex-ambiguity-ref.py is the
// reference and conformance/28b-regex-ambiguity.selt pins it): the pattern tree
// becomes a Glushkov position automaton, and the pattern is refused when the same
// follow edge is generated twice, when two different paths run from a state back
// to itself on one word (EDA), when a nullable choice sits inside a loop, when
// the ambiguity budget is above 16, or when the analysis itself would be too big.
//
// Everything here is a function of the pattern and the `i` flag alone, and every
// walk over the automaton is iterative; the only recursion is over the parse
// tree, which is at most 200 groups deep.

import {
  REGEX_AMBIGUITY_BUDGET, REGEX_ANALYSIS_POSITIONS, REGEX_ANALYSIS_EDGES, REGEX_ANALYSIS_RANGES,
  REGEX_ANALYSIS_PAIR_WORK,
} from '../_limits.mjs';

export const MAX_CP = 0x10ffff;
const UNROLL = 8;
// The closed-form caps of SPEC §7.8 rule 5 and the budget of rule 4, from spec/limits.json.
const P_MAX = REGEX_ANALYSIS_POSITIONS;   // positions
const E_MAX = REGEX_ANALYSIS_EDGES;       // follow edges
const D_MAX = REGEX_ANALYSIS_RANGES;      // sum over edges of the range count at the target
const Q_MAX = REGEX_ANALYSIS_PAIR_WORK;   // pair-graph work
const AMB_MAX = REGEX_AMBIGUITY_BUDGET;
const SAT = 2 ** 40;

class Refuse extends Error {}

// ---------------------------------------------------------------- range sets
// A set of code points is a flat array [lo0, hi0, lo1, hi1, ...], sorted, merged.
export function norm(rs) {
  const pairs = [];
  for (let i = 0; i < rs.length; i += 2) pairs.push([rs[i], rs[i + 1]]);
  pairs.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  const out = [];
  for (const [a, b] of pairs) {
    const n = out.length;
    if (n > 0 && a <= out[n - 1] + 1) {
      if (b > out[n - 1]) out[n - 1] = b;
    } else {
      out.push(a, b);
    }
  }
  return out;
}

export function negate(rs) {
  const out = [];
  let next = 0;
  const n = norm(rs);
  for (let i = 0; i < n.length; i += 2) {
    if (n[i] > next) out.push(next, n[i] - 1);
    next = n[i + 1] + 1;
  }
  if (next <= MAX_CP) out.push(next, MAX_CP);
  return out;
}

const has = (rs, c) => {
  for (let i = 0; i < rs.length; i += 2) if (rs[i] <= c && c <= rs[i + 1]) return true;
  return false;
};

// `i`: simple case folding restricted to what an ASCII pattern can reach -- the
// ASCII case mirror, plus U+212A with k/K and U+017F with s/S.
function fold(rs) {
  const extra = [];
  for (let i = 0; i < rs.length; i += 2) {
    const a = rs[i];
    const b = rs[i + 1];
    let lo = Math.max(a, 0x41);
    let hi = Math.min(b, 0x5a);
    if (lo <= hi) extra.push(lo + 32, hi + 32);
    lo = Math.max(a, 0x61);
    hi = Math.min(b, 0x7a);
    if (lo <= hi) extra.push(lo - 32, hi - 32);
  }
  const all = norm([...rs, ...extra]);
  const more = [];
  if (has(all, 0x6b) || has(all, 0x4b)) more.push(0x212a, 0x212a);
  if (has(all, 0x73) || has(all, 0x53)) more.push(0x17f, 0x17f);
  return norm([...all, ...more]);
}

function intersects(x, y) {
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i + 1] < y[j]) i += 2;
    else if (y[j + 1] < x[i]) j += 2;
    else return true;
  }
  return false;
}

// The largest number of the given sets that share one code point.
function maxCover(classes) {
  const ev = [];
  for (const c of classes) {
    for (let i = 0; i < c.length; i += 2) {
      ev.push([c[i], 1], [c[i + 1] + 1, -1]);
    }
  }
  ev.sort((x, y) => x[0] - y[0] || x[1] - y[1]);   // closings before openings
  let best = 0;
  let cur = 0;
  for (const [, d] of ev) {
    cur += d;
    if (cur > best) best = cur;
  }
  return best;
}

const bitLength = (k) => (k <= 0 ? 0 : 32 - Math.clz32(k));   // k < 2^32

// -------------------------------------------------- the tree the walk consumes
// kinds: EPS, LET (a: ranges), CAT (a: items), ALT (a: branches), REP (a, lo, hi
// with hi === null for unbounded), OPT (internal: an optional, no cost of its own).
function mk(k, a, lo = 0, hi = 0) {
  const n = { k, a, lo, hi, nullable: false, minlen: 0, maxlen: 0 };
  switch (k) {
    case 'EPS': n.nullable = true; break;
    case 'LET': n.minlen = 1; n.maxlen = 1; break;
    case 'CAT':
      n.nullable = a.every((x) => x.nullable);
      n.minlen = Math.min(SAT, a.reduce((s, x) => s + x.minlen, 0));
      n.maxlen = Math.min(SAT, a.reduce((s, x) => s + x.maxlen, 0));
      break;
    case 'ALT':
      n.nullable = a.some((x) => x.nullable);
      n.minlen = Math.min(...a.map((x) => x.minlen));
      n.maxlen = Math.max(...a.map((x) => x.maxlen));
      break;
    case 'REP':
      n.nullable = lo === 0 || a.nullable;
      n.minlen = Math.min(SAT, lo * a.minlen);
      if (hi === 0 || a.maxlen === 0) n.maxlen = 0;
      else if (hi === null) n.maxlen = SAT;
      else n.maxlen = Math.min(SAT, hi * a.maxlen);
      break;
    case 'OPT': n.nullable = true; n.maxlen = a.maxlen; break;
    default: throw new Error(k);
  }
  return n;
}

// Converts the validator's parse tree. Groups are transparent.
function convert(node, ic) {
  switch (node.k) {
    case 'atom': {
      if (node.anchor) return mk('EPS');
      const r = node.ranges;
      if (!Array.isArray(r)) return mk('LET', negate(ic ? fold(r.members) : r.members));
      return mk('LET', ic ? fold(r) : r);
    }
    case 'rep':
      return mk('REP', convert(node.node, ic), node.lo, node.hi === Infinity ? null : node.hi);
    default: {
      const branches = node.alts.map((seq) => {
        if (seq.length === 0) return mk('EPS');
        const items = seq.map((x) => convert(x, ic));
        return items.length === 1 ? items[0] : mk('CAT', items);
      });
      return branches.length === 1 ? branches[0] : mk('ALT', branches);
    }
  }
}

// ------------------------------------------------------------------- analysis
class Analysis {
  constructor() {
    this.cls = [[]];
    this.succ = [[]];
    this.tag = new Map();
    this.E = 0;
    this.D = 0;
    this.amb = 0;
  }

  join(L, F, sync) {
    this.E += L.length * F.length;
    let d = 0;
    for (const q of F) d += this.cls[q].length / 2;
    this.D += L.length * d;
    if (this.E > E_MAX || this.D > D_MAX) throw new Refuse('analysis edge cap');
    for (const a of L) {
      for (const b of F) {
        const key = a * (P_MAX + 1) + b;
        if (this.tag.has(key)) {
          if (this.tag.get(key) && sync) continue;
          throw new Refuse('follow edge generated twice');
        }
        this.tag.set(key, sync);
        this.succ[a].push(b);
      }
    }
  }

  eps(k, inloop) {
    if (k > 0) {
      if (inloop) throw new Refuse('nullable choice inside a loop');
      this.amb += k;
      if (this.amb > AMB_MAX) throw new Refuse('ambiguity budget');
    }
  }

  // -> [nullable, first, last]
  walk(n, inloop) {
    switch (n.k) {
      case 'EPS': return [true, [], []];
      case 'LET': {
        if (this.cls.length >= P_MAX) throw new Refuse('position cap');
        this.cls.push(n.a);
        this.succ.push([]);
        const i = this.cls.length - 1;
        return [false, [i], [i]];
      }
      case 'OPT': {
        const [, f, l] = this.walk(n.a, inloop);
        return [true, f, l];
      }
      case 'ALT': {
        const kNull = n.a.reduce((s, b) => s + (b.nullable ? 1 : 0), 0);
        if (kNull >= 2) this.eps(bitLength(kNull - 1), inloop);
        let f = [];
        let l = [];
        for (const b of n.a) {
          const [, f2, l2] = this.walk(b, inloop);
          f = f.concat(f2);
          l = l.concat(l2);
        }
        return [kNull > 0, f, l];
      }
      case 'CAT': {
        let nl = true;
        let f = [];
        let l = [];
        for (const it of n.a) {
          const [n2, f2, l2] = this.walk(it, inloop);
          this.join(l, f2, false);
          if (nl) f = f.concat(f2);
          l = n2 ? l.concat(l2) : l2;
          nl = nl && n2;
        }
        return [nl, f, l];
      }
      case 'REP': {
        const x = n.a;
        const { lo, hi } = n;
        if (hi === 0) return [true, [], []];
        if (hi !== null && hi <= UNROLL) {
          if (lo === 0 && hi === 1 && x.nullable) this.eps(1, inloop);
          let tail = mk('EPS');
          for (let i = 0; i < hi - lo; i++) tail = mk('OPT', mk('CAT', [x, tail]));
          const items = [];
          for (let i = 0; i < lo; i++) items.push(x);
          items.push(tail);
          return this.walk(mk('CAT', items), inloop);
        }
        const [, f, l] = this.walk(x, true);
        this.join(l, f, x.minlen === x.maxlen && x.maxlen > 0 && x.maxlen < SAT);
        return [lo === 0 || x.nullable, f, l];
      }
      default: throw new Error(n.k);
    }
  }
}

// Iterative Tarjan: [component id per node, cyclic flag per node].
function scc(succ) {
  const n = succ.length;
  const index = new Array(n).fill(-1);
  const low = new Array(n).fill(0);
  const on = new Array(n).fill(false);
  const comp = new Array(n).fill(-1);
  const stack = [];
  let counter = 0;
  let ncomp = 0;
  for (let root = 0; root < n; root++) {
    if (index[root] !== -1) continue;
    const work = [[root, 0]];
    index[root] = low[root] = counter++;
    stack.push(root);
    on[root] = true;
    while (work.length > 0) {
      const top = work[work.length - 1];
      const v = top[0];
      const ei = top[1];
      if (ei < succ[v].length) {
        top[1] = ei + 1;
        const w = succ[v][ei];
        if (index[w] === -1) {
          index[w] = low[w] = counter++;
          stack.push(w);
          on[w] = true;
          work.push([w, 0]);
        } else if (on[w]) {
          low[v] = Math.min(low[v], index[w]);
        }
      } else {
        work.pop();
        if (work.length > 0) {
          const u = work[work.length - 1][0];
          low[u] = Math.min(low[u], low[v]);
        }
        if (low[v] === index[v]) {
          for (;;) {
            const w = stack.pop();
            on[w] = false;
            comp[w] = ncomp;
            if (w === v) break;
          }
          ncomp++;
        }
      }
    }
  }
  const size = new Array(ncomp).fill(0);
  for (let v = 0; v < n; v++) size[comp[v]]++;
  const cyc = new Array(n);
  for (let v = 0; v < n; v++) cyc[v] = size[comp[v]] > 1 || succ[v].includes(v);
  return [comp, cyc];
}

function analyseTree(tree) {
  const a = new Analysis();
  const [, f] = a.walk(tree, false);
  a.succ[0] = f.slice();
  const { succ, cls } = a;
  const N = succ.length;
  const [comp, cyc] = scc(succ);

  // (d) the budget: choices outside every cycle
  for (let p = 0; p < N; p++) {
    if (!cyc[p] && succ[p].length >= 2) {
      const m = maxCover(succ[p].map((q) => cls[q]));
      if (m >= 2) {
        a.amb += bitLength(m - 1);
        if (a.amb > AMB_MAX) throw new Refuse('ambiguity budget');
      }
    }
  }

  // (b) EDA: two different paths from a state to a state on one word
  const insc = new Map();
  const d = (p) => {
    let v = insc.get(p);
    if (v === undefined) {
      v = 0;
      for (const q of succ[p]) if (comp[q] === comp[p]) v++;
      insc.set(p, v);
    }
    return v;
  };
  const key = (p, r) => p * N + r;
  const reach = new Set();
  const order = [];
  for (let q = 0; q < N; q++) {
    if (cyc[q] && cls[q].length > 0) {
      reach.add(key(q, q));
      order.push([q, q]);
    }
  }
  let qWork = 0;
  const fwd = new Map();
  while (order.length > 0) {
    const node = order.pop();
    const [p, r] = node;
    qWork += d(p) * d(r);
    if (qWork > Q_MAX) throw new Refuse('pair-graph cap');
    const outs = [];
    for (const p2 of succ[p]) {
      if (comp[p2] !== comp[p]) continue;
      for (const r2 of succ[r]) {
        if (comp[r2] !== comp[p] || !intersects(cls[p2], cls[r2])) continue;
        const k2 = key(p2, r2);
        outs.push(k2);
        if (!reach.has(k2)) {
          reach.add(k2);
          order.push([p2, r2]);
        }
      }
    }
    fwd.set(key(p, r), outs);
  }
  const rev = new Map();
  for (const [u, outs] of fwd) {
    for (const v of outs) {
      let l = rev.get(v);
      if (l === undefined) rev.set(v, l = []);
      l.push(u);
    }
  }
  const back = new Set();
  const todo = [];
  for (const k of reach) {
    if (Math.floor(k / N) === k % N) { back.add(k); todo.push(k); }
  }
  while (todo.length > 0) {
    const v = todo.pop();
    for (const u of rev.get(v) || []) {
      if (!back.has(u)) { back.add(u); todo.push(u); }
    }
  }
  for (const k of back) {
    if (Math.floor(k / N) !== k % N) throw new Refuse('exponential ambiguity (EDA)');
  }
}

// Throws Refuse(reason) when the pattern is refused. `tree` is the validator's
// parse tree with `ranges` on every atom.
export function analyse(tree, ignoreCase) {
  analyseTree(convert(tree, ignoreCase));
}

export { Refuse };
