"""The exponential-ambiguity rule of the portable regex subset (SPEC 7.8).

Port of the analysis in tools/regex-ambiguity-ref.py, which is the oracle every
host's validator is held to: same counts, same verdicts. The pattern's tree
(built by `regex.parse`) is turned into a Glushkov position automaton -- one state
per letter, character classes as sorted ranges -- and a pattern is refused when a
backtracking engine can be made to take exponential time on it: a follow edge
generated twice, two different paths from a state back to itself on one word
(EDA), a nullable choice inside a loop, or an ambiguity budget above AMB_MAX; and
when the analysis itself would exceed its closed-form caps. Polynomial ambiguity
is accepted on purpose.

`analyse(tree)` raises `Reject` (the caller turns it into E_REGEX_SYNTAX) and is
iterative except for the walk of the tree, whose depth is bounded by the group
nesting cap (MAX_DEPTH); the caller runs it under the recursion budget.
"""

MAX_CP = 0x10FFFF
UNROLL = 8
P_MAX = 1 << 17            # positions
E_MAX = 1 << 18            # follow edges
D_MAX = 1 << 21            # sum over edges of the range count at the target
Q_MAX = 1 << 20            # pair-graph work
AMB_MAX = 16
SAT = 1 << 40


class Reject(Exception):
    pass


def norm(rs):
    rs = sorted(rs)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1] + 1:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return tuple(out)


def negate(rs):
    out, nxt = [], 0
    for a, b in norm(rs):
        if a > nxt:
            out.append((nxt, a - 1))
        nxt = b + 1
    if nxt <= MAX_CP:
        out.append((nxt, MAX_CP))
    return tuple(out)


def fold(rs):
    """`i`: simple case folding restricted to what an ASCII pattern can reach --
    the ASCII case mirror, plus U+212A with k/K and U+017F with s/S."""
    extra = []
    for a, b in rs:
        lo, hi = max(a, 0x41), min(b, 0x5A)
        if lo <= hi:
            extra.append((lo + 32, hi + 32))
        lo, hi = max(a, 0x61), min(b, 0x7A)
        if lo <= hi:
            extra.append((lo - 32, hi - 32))
    allr = norm(list(rs) + extra)

    def has(c):
        return any(a <= c <= b for a, b in allr)
    more = []
    if has(ord('k')) or has(ord('K')):
        more.append((0x212A, 0x212A))
    if has(ord('s')) or has(ord('S')):
        more.append((0x17F, 0x17F))
    return norm(list(allr) + more)


def intersects(x, y):
    i = j = 0
    while i < len(x) and j < len(y):
        if x[i][1] < y[j][0]:
            i += 1
        elif y[j][1] < x[i][0]:
            j += 1
        else:
            return True
    return False


def max_cover(classes):
    """The largest number of the given classes that share one code point."""
    ev = []
    for c in classes:
        for a, b in c:
            ev.append((a, 1))
            ev.append((b + 1, -1))
    ev.sort(key=lambda e: (e[0], e[1]))       # closings before openings at a point
    best = cur = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best



class N:
    __slots__ = ('k', 'a', 'lo', 'hi', 'nullable', 'minlen', 'maxlen')

    def __init__(self, k, a=None, lo=0, hi=0):
        self.k, self.a, self.lo, self.hi = k, a, lo, hi
        if k == 'EPS':
            self.nullable, self.minlen, self.maxlen = True, 0, 0
        elif k == 'LET':
            self.nullable, self.minlen, self.maxlen = False, 1, 1
        elif k == 'CAT':
            self.nullable = all(x.nullable for x in a)
            self.minlen = min(SAT, sum(x.minlen for x in a))
            self.maxlen = min(SAT, sum(x.maxlen for x in a))
        elif k == 'ALT':
            self.nullable = any(x.nullable for x in a)
            self.minlen = min(x.minlen for x in a)
            self.maxlen = max(x.maxlen for x in a)
        elif k == 'GRP':                      # a group: transparent to analysis
            x = a
            self.nullable, self.minlen, self.maxlen = x.nullable, x.minlen, x.maxlen
        elif k == 'REP':
            x = a
            self.nullable = lo == 0 or x.nullable
            self.minlen = min(SAT, lo * x.minlen)
            if hi == 0 or x.maxlen == 0:
                self.maxlen = 0
            elif hi is None:
                self.maxlen = SAT
            else:
                self.maxlen = min(SAT, hi * x.maxlen)
        elif k == 'OPT':                      # internal: optional, no cost of its own
            self.nullable, self.minlen, self.maxlen = True, 0, a.maxlen
        else:
            raise AssertionError(k)



class Analysis:
    def __init__(self):
        self.cls = [()]
        self.succ = [[]]
        self.tag = {}
        self.E = self.D = self.amb = 0

    def join(self, L, F, sync):
        self.E += len(L) * len(F)
        self.D += len(L) * sum(len(self.cls[q]) for q in F)
        if self.E > E_MAX or self.D > D_MAX:
            raise Reject('analysis edge cap')
        for a in L:
            for b in F:
                key = (a, b)
                if key in self.tag:
                    if self.tag[key] and sync:
                        continue
                    raise Reject('follow edge generated twice')
                self.tag[key] = sync
                self.succ[a].append(b)

    def eps(self, k, inloop):
        if k > 0:
            if inloop:
                raise Reject('nullable choice inside a loop')
            self.amb += k
            if self.amb > AMB_MAX:
                raise Reject('ambiguity budget')

    def walk(self, n, inloop):
        """-> (nullable, first, last): lists of positions."""
        k = n.k
        if k == 'EPS':
            return True, [], []
        if k == 'LET':
            if len(self.cls) >= P_MAX:
                raise Reject('position cap')
            self.cls.append(n.a)
            self.succ.append([])
            i = len(self.cls) - 1
            return False, [i], [i]
        if k == 'GRP':
            return self.walk(n.a, inloop)
        if k == 'OPT':
            _, f, l = self.walk(n.a, inloop)
            return True, f, l
        if k == 'ALT':
            k_null = sum(1 for b in n.a if b.nullable)
            if k_null >= 2:
                self.eps((k_null - 1).bit_length(), inloop)       # ceil(log2 k)
            f, l = [], []
            for b in n.a:
                _, f2, l2 = self.walk(b, inloop)
                f += f2
                l += l2
            return k_null > 0, f, l
        if k == 'CAT':
            nl, f, l = True, [], []
            for it in n.a:
                n2, f2, l2 = self.walk(it, inloop)
                self.join(l, f2, False)
                if nl:
                    f = f + f2
                l = (l + l2) if n2 else l2
                nl = nl and n2
            return nl, f, l
        if k == 'REP':
            x, lo, hi = n.a, n.lo, n.hi
            if hi == 0:
                return True, [], []
            if hi is not None and hi <= UNROLL:
                if lo == 0 and hi == 1 and x.nullable:
                    self.eps(1, inloop)
                tail = N('EPS')
                for _ in range(hi - lo):
                    tail = N('OPT', N('CAT', [x, tail]))
                seq = N('CAT', [x] * lo + [tail])
                return self.walk(seq, inloop)
            _, f, l = self.walk(x, True)
            self.join(l, f, x.minlen == x.maxlen and 0 < x.maxlen < SAT)
            return lo == 0 or x.nullable, f, l
        raise AssertionError(k)


def scc(succ):
    """Iterative Tarjan: (component id per node, cyclic flag per node)."""
    n = len(succ)
    index = [-1] * n
    low = [0] * n
    on = [False] * n
    comp = [-1] * n
    stack = []
    counter = 0
    ncomp = 0
    for root in range(n):
        if index[root] != -1:
            continue
        work = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on[root] = True
        while work:
            v, ei = work[-1]
            if ei < len(succ[v]):
                work[-1] = (v, ei + 1)
                w = succ[v][ei]
                if index[w] == -1:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on[w] = True
                    work.append((w, 0))
                elif on[w]:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    u = work[-1][0]
                    low[u] = min(low[u], low[v])
                if low[v] == index[v]:
                    while True:
                        w = stack.pop()
                        on[w] = False
                        comp[w] = ncomp
                        if w == v:
                            break
                    ncomp += 1
    size = [0] * ncomp
    for v in range(n):
        size[comp[v]] += 1
    cyc = [size[comp[v]] > 1 or v in succ[v] for v in range(n)]
    return comp, cyc


def analyse(tree):
    a = Analysis()
    _, f, l = a.walk(tree, False)
    a.succ[0] = list(f)
    succ, cls = a.succ, a.cls
    comp, cyc = scc(succ)
    # (d) the budget: choices outside every cycle
    for p in range(len(succ)):
        if not cyc[p] and len(succ[p]) >= 2:
            m = max_cover([cls[q] for q in succ[p]])
            if m >= 2:
                a.amb += (m - 1).bit_length()
                if a.amb > AMB_MAX:
                    raise Reject('ambiguity budget')
    # (b) EDA: two different paths from a state to a state on one word
    seeds = [(q, q) for q in range(len(succ)) if cyc[q] and cls[q]]
    insc = {}

    def d(p):
        if p not in insc:
            insc[p] = sum(1 for q in succ[p] if comp[q] == comp[p])
        return insc[p]
    reach = set(seeds)
    order = list(seeds)
    q_work = 0
    fwd = {}
    while order:
        node = order.pop()
        p, r = node
        q_work += d(p) * d(r)
        if q_work > Q_MAX:
            raise Reject('pair-graph cap')
        outs = []
        for p2 in succ[p]:
            if comp[p2] != comp[p]:
                continue
            for r2 in succ[r]:
                if comp[r2] != comp[p] or not intersects(cls[p2], cls[r2]):
                    continue
                outs.append((p2, r2))
                if (p2, r2) not in reach:
                    reach.add((p2, r2))
                    order.append((p2, r2))
        fwd[node] = outs
    rev = {}
    for u, outs in fwd.items():
        for v in outs:
            rev.setdefault(v, []).append(u)
    back = set(n for n in reach if n[0] == n[1])
    todo = list(back)
    while todo:
        v = todo.pop()
        for u in rev.get(v, ()):
            if u not in back:
                back.add(u)
                todo.append(u)
    for p, r in back:
        if p != r:
            raise Reject('exponential ambiguity (EDA)')


