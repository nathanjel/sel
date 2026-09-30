#!/usr/bin/env python3
"""Reference implementation of the SPEC §7.8 regex validator: the portable-subset
parser, the structural rules (P1-P4 and the size caps) and the exponential-
ambiguity rule. Standalone -- it imports nothing from sel/ -- and it is the
oracle the six hosts' validators are written against: the conformance cases in
conformance/28b-regex-ambiguity.selt were checked against it before they were
pinned, and `--self-check` re-checks them.

    tools/regex-ambiguity-ref.py --self-check     run the built-in lists
    tools/regex-ambiguity-ref.py [-i] PATTERN     accept / reject one pattern
    tools/regex-ambiguity-ref.py --cases FILE     check every RMATCH literal in a
                                                  .selt against its expectation

validate(pattern, ignore_case) returns None when the pattern is in the subset and
raises Reject(reason) when it is not. Reasons are diagnostics only; conformance
asserts the code E_REGEX_SYNTAX and nothing else.

The rule, in one paragraph (spec/SPEC.md §7.8 has the normative text). The pattern
is parsed to a tree and turned into a *position automaton* (Glushkov): one state
per letter, edges from "last" positions to "first" positions, character classes as
sorted ranges. Counted repeats with a maximum up to UNROLL are unrolled; larger or
unbounded ones become one copy with wrap-around edges, counts ignored. A pattern is
rejected when a backtracking engine can be made to take exponential time on it:
the same follow edge generated twice, two different paths from a state back to
itself on one word (EDA), a nullable choice inside a loop, or an ambiguity budget
above AMB_MAX; and when the analysis itself would exceed its closed-form caps.
Everything else, polynomial ambiguity included, is accepted on purpose.
"""
import sys

sys.setrecursionlimit(20000)   # the parser recurses per group: 200 levels x 5 frames

MAX_CP = 0x10FFFF
MAX_DEPTH = 200            # spec/limits.json MAX_DEPTH: group nesting
MAX_PATTERN = 65535        # MAX_REGEX_PATTERN, code points
MAX_GROUPS = 1000          # MAX_REGEX_GROUPS
MAX_BOUND = 65535          # {n} bounds
UNROLL = 8
P_MAX = 1 << 17            # positions
E_MAX = 1 << 18            # follow edges
D_MAX = 1 << 21            # sum over edges of range count at the target
Q_MAX = 1 << 20            # pair-graph work
AMB_MAX = 16
SAT = 1 << 40


class Reject(Exception):
    pass


class BadArg(Exception):
    """`i` on a pattern with a non-ASCII literal: E_BAD_ARG, not E_REGEX_SYNTAX."""


# ---------------------------------------------------------------- range sets
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


DIGIT = ((0x30, 0x39),)
WORD = norm([(0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A)])
SPACE = norm([(0x09, 0x0D), (0x20, 0x20)])          # [ \t\n\r\f\x0b]
ANY = ((0, MAX_CP),)
ESC_SET = {'d': DIGIT, 'w': WORD, 's': SPACE}
CTRL = {'n': 10, 'r': 13, 't': 9, 'f': 12}


# ------------------------------------------------------------------- the tree
class N:
    __slots__ = ('k', 'a', 'lo', 'hi', 'cap', 'nullable', 'minlen', 'maxlen')

    def __init__(self, k, a=None, lo=0, hi=0, cap=False):
        self.k, self.a, self.lo, self.hi, self.cap = k, a, lo, hi, cap
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


# --------------------------------------------------------------------- parser
class Parser:
    def __init__(self, pat, ic):
        self.p, self.i, self.ic = pat, 0, ic
        self.groups = 0
        if len(pat) > MAX_PATTERN:
            raise Reject('pattern longer than MAX_REGEX_PATTERN')

    def peek(self):
        return self.p[self.i] if self.i < len(self.p) else ''

    def parse(self):
        n = self.alt(0)
        if self.i < len(self.p):
            raise Reject('unbalanced )')
        return n

    def alt(self, depth):
        branches = [self.cat(depth)]
        while self.peek() == '|':
            self.i += 1
            branches.append(self.cat(depth))
        return branches[0] if len(branches) == 1 else N('ALT', branches)

    def cat(self, depth):
        items = []
        while self.i < len(self.p) and self.peek() not in '|)':
            items.append(self.quantified(depth))
        if not items:
            return N('EPS')
        return items[0] if len(items) == 1 else N('CAT', items)

    def quantified(self, depth):
        c = self.peek()
        atom_kind, atom = self.atom(depth)
        q = self.quantifier()
        if q is None:
            return atom
        if atom_kind == 'anchor':
            raise Reject('quantified anchor')
        lo, hi = q
        if self.peek() in ('*', '+', '?', '{') and not (self.peek() == '{' and not self.brace_quant_ahead()):
            raise Reject('quantifier on a quantifier')
        if self.peek() == '{':
            raise Reject('bare {')
        if hi is None or hi > 1:
            # P1: a loop over something that can match the empty string
            if atom.nullable:
                raise Reject('nullable loop body')
            # P2: every capture inside a loop must take part in every iteration
            self.mandatory_captures(atom, True)
        return N('REP', atom, lo, hi)

    def mandatory_captures(self, n, mand):
        if n.k == 'GRP':
            if n.cap and not mand:
                raise Reject('optional capture in a loop')
            self.mandatory_captures(n.a, mand)
        elif n.k == 'CAT':
            for x in n.a:
                self.mandatory_captures(x, mand)
        elif n.k == 'ALT':
            for x in n.a:
                self.mandatory_captures(x, False)
        elif n.k == 'REP':
            self.mandatory_captures(n.a, mand and n.lo > 0)

    def brace_quant_ahead(self):
        return self.brace() is not None

    def brace(self):
        """Parse {n} {n,} {n,m} at i without consuming; (lo, hi, end) or None."""
        p, i = self.p, self.i
        if i >= len(p) or p[i] != '{':
            return None
        j = i + 1
        a = j
        while j < len(p) and p[j].isascii() and p[j].isdigit():
            j += 1
        if j == a:
            return None
        lo = int(p[a:j])
        hi = lo
        if j < len(p) and p[j] == ',':
            j += 1
            b = j
            while j < len(p) and p[j].isascii() and p[j].isdigit():
                j += 1
            hi = None if j == b else int(p[b:j])
        if j >= len(p) or p[j] != '}':
            return None
        return lo, hi, j + 1

    def quantifier(self):
        c = self.peek()
        if c == '*':
            self.i += 1
            q = (0, None)
        elif c == '+':
            self.i += 1
            q = (1, None)
        elif c == '?':
            self.i += 1
            q = (0, 1)
        elif c == '{':
            b = self.brace()
            if b is None:
                raise Reject('bare {')
            lo, hi, end = b
            if lo > MAX_BOUND or (hi is not None and hi > MAX_BOUND):
                raise Reject('bound beyond the cap')
            if hi is not None and lo > hi:
                raise Reject('reversed bounds')
            self.i = end
            q = (lo, hi)
        else:
            return None
        if self.peek() == '?':                # lazy: same language, same analysis
            self.i += 1
        return q

    def letter(self, rs):
        rs = norm(rs)
        if self.ic:
            rs = fold(rs)
        return N('LET', rs)

    def lit(self, ch):
        if self.ic and ord(ch) > 127:
            raise BadArg()
        return self.letter([(ord(ch), ord(ch))])

    def atom(self, depth):
        p = self.p
        c = p[self.i]
        if c in '*+?':
            raise Reject('nothing to repeat')
        if c == '{':
            raise Reject('bare {')
        if c in ']}':
            raise Reject('unmatched ] or } -- escape it (every host has always refused these)')
        if c == '^' or c == '$':
            self.i += 1
            return 'anchor', N('EPS')
        if c == '.':
            self.i += 1
            return 'atom', self.letter(ANY)
        if c == '(':
            return 'atom', self.group(depth)
        if c == '[':
            return 'atom', self.klass()
        if c == '\\':
            return 'atom', self.escape()
        self.i += 1
        return 'atom', self.lit(c)

    def group(self, depth):
        p = self.p
        self.i += 1
        cap = True
        if self.peek() == '*':
            raise Reject('PCRE verb')
        if self.peek() == '?':
            if p[self.i:self.i + 2] != '?:':
                raise Reject('(? construct outside the subset')
            self.i += 2
            cap = False
        if depth + 1 > MAX_DEPTH:
            raise Reject('group nesting beyond MAX_DEPTH')
        self.groups += 1
        if self.groups > MAX_GROUPS:
            raise Reject('more than MAX_REGEX_GROUPS groups')
        inner = self.alt(depth + 1)
        if self.peek() != ')':
            raise Reject('unclosed (')
        self.i += 1
        g = N('GRP', inner, cap=cap)
        g.cap = cap
        return g

    def escape(self):
        p = self.p
        if self.i + 1 >= len(p):
            raise Reject('trailing backslash')
        e = p[self.i + 1]
        self.i += 2
        if e in ESC_SET:
            return self.letter(ESC_SET[e])
        if e in 'DWS':
            return self.letter(negate(ESC_SET[e.lower()]))
        if e in CTRL:
            return self.letter([(CTRL[e], CTRL[e])])
        if e.isascii() and e.isalnum():
            raise Reject('unsupported escape')
        return self.lit(e)

    def klass(self):
        p = self.p
        j = self.i + 1
        neg = False
        if j < len(p) and p[j] == '^':
            neg = True
            j += 1
        if j < len(p) and p[j] == ']':
            raise Reject('leading ] in a class')
        rs = []
        first = True
        while True:
            if j >= len(p):
                raise Reject('unclosed [')
            c = p[j]
            if c == ']':
                j += 1
                break
            if c == '[' and j + 1 < len(p) and p[j + 1] in ':.=':
                # A `[` followed by `:`, `.` or `=` inside a class is refused, closed or
                # not (SPEC 7.8).
                raise Reject('POSIX bracket form')
            lo, j, was_escape = self.class_atom(j)
            if lo is None:                    # a class escape: a set, no range
                rs.extend(was_escape)
                if j < len(p) and p[j] == '-' and j + 1 < len(p) and p[j + 1] != ']':
                    raise Reject('class escape before a range hyphen')
                continue
            if j < len(p) and p[j] == '-' and j + 1 < len(p) and p[j + 1] != ']':
                hi, j2, hi_set = self.class_atom(j + 1)
                if hi is None:
                    raise Reject('class escape as a range endpoint')
                if hi < lo:
                    raise Reject('range out of order')
                rs.append((lo, hi))
                j = j2
            else:
                rs.append((lo, lo))
        self.i = j
        rs = norm(rs)
        if self.ic:
            if any(b > 127 for a, b in rs) is False:
                pass
            rs = fold(rs)
        if neg:
            rs = negate(rs)
        return N('LET', rs)

    def class_atom(self, j):
        """(codepoint, next, None) for a member, (None, next, ranges) for \\d \\w \\s."""
        p = self.p
        c = p[j]
        if c == '\\':
            if j + 1 >= len(p):
                raise Reject('trailing backslash')
            e = p[j + 1]
            if e in ESC_SET:
                return None, j + 2, list(ESC_SET[e])
            if e in 'DWS':
                raise Reject('negated escape in a class')
            if e in CTRL:
                return CTRL[e], j + 2, None
            if e.isascii() and e.isalnum():
                raise Reject('unsupported escape')
            return ord(e), j + 2, None
        if self.ic and ord(c) > 127:
            raise BadArg()
        return ord(c), j + 1, None


# ------------------------------------------------------------------- analysis
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
    nl, f, l = a.walk(tree, False)
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


def validate(pattern, ignore_case=False):
    # The `i` fold is analysed only for an ASCII pattern (SPEC 7.8): a non-ASCII pattern
    # under `i` is refused at run time (E_BAD_ARG) whatever else it holds, so the
    # compile-time analysis must not turn it into a refusal of its own.
    if ignore_case and any(ord(ch) > 127 for ch in pattern):
        ignore_case = False
    tree = Parser(pattern, ignore_case).parse()
    analyse(tree)


# ------------------------------------------------------------------- self-check
MUST_REJECT = r"""(a+)+$
(a|aa)+$
(a|b|ab)*c
(?:a+|b)*(?:a+)*c
(.+)+x
([a-z]+)*$
(\w+\s?)*$
(\w+\s*)*$
([a-zA-Z]+)*\d
(?:[a-z]+|\d+)*$
(\d+\d*)+$
([\w.-]+\.)+$
(x+x+)+y
(?:\d|\d\d)+$
(?:\s|\s\s)+$
(?:.|\n)*x
(?:a|a)*$
(\d{1,3},?)+$
^(([a-z])+.)+[A-Z]([a-z])+$
(?:\s*,\s*)*x
(?:[ab]|[bc])*$
(\s*\w+\s*)*$
(?:x|xx|xxx)+y
^(\w+[-.]?)+@
(?:[\d.]+,?)+$
(a+){2,}$
(?:(?:a|b)+c?)+$
((a+)b?)*$
(?:a+)+?b
(?:a{1,20}){1,20}b
(?:\s*\w+\s*,?)*x
(?:(?:a?|b?)c)*d
(?:(?:a*)?c)*d
^(?:a+){2,}$
^(?:a|b|ab)+$""".split('\n')

# reject with i, accept without
FLAG_PAIRS = [r"(?:a|A)+$", r"(?:[a-z]|[A-Z])+$", r"^[a-z]*(?:[a-c]|[A-C])+$"]

BUDGET = [
    ('(a|a)' * 8 + 'x', True), ('(a|a)' * 9 + 'x', False),
    ('(?:|)' * 16 + 'x', True), ('(?:|)' * 17 + 'x', False),
    ('a?' * 7 + 'b', True), ('a?' * 8 + 'b', False),
    ('(?:a|a){1,8}$', True), ('(?:a|a){1,9}$', False),
]

MUST_ACCEPT = r"""(\d+,)+
(?:ab|cd)*
^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$
(\w+\s)*
([a-z]+-)*[a-z]+
(?:a|b)*
a*b*c*
^\d{3}-\d{3}-\d{4}$
^(\d{1,3}\.){3}\d{1,3}$
^[+-]?\d+(?:\.\d+)?$
^"(?:[^"\\]|\\.)*"$
^[a-z0-9]+(?:[-_.][a-z0-9]+)*$
^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$
^(?:[^,]*,)*[^,]*$
^\s*(\w+)\s*=\s*(.*?)\s*$
(?:\r\n|\n)*
^(?:[a-z]+\d+)*$
^[A-Z]{2}\d{2}(?: ?\d{4}){4,7}$
^(?:ab|ac)*$
^(?:ab|a)*$
(?:foo|foobar)*
^(?:\d{3}){1,2}$
(?:a{2}){3}
^(a{300}){300}$
(?:a{60000}){60000}
^[a-z]+(?:[A-Z][a-z]+)*$
^(?:[A-Z][a-z0-9]+)+$
(?:[a-z]|[A-Z])+$
^(?:[0-9]*|[a-z]*)$
^(\d*)?$
(^|[^0-9A-Za-z_])foo($|[^0-9A-Za-z_])
^(?:a+b)+$
^[a-z]+(\.[a-z]+)*$
^(a|b)*$
^(?:a|b)+c?$
^[^<>]*(?:<[^<>]*>[^<>]*)*$
^(.*),(.*),(.*),(.*)$
a*a*$
^a{65535}$
^(?:ab){65535}$
^[a-z]{1,65535}$""".split('\n')

STRUCTURAL_REJECT = ['(a', 'a)', '*a', 'a|*', '^*', 'a{2}*', 'a**', '()*', '(?:', '[a',
                     'a{2}{3}', '(*)', '(a|', '(?=a)', '(?i)a', 'a{2,1}', 'a{65536}']
STRUCTURAL_ACCEPT = ['', 'x{0}', '^$', '(a | a)']


def check(pattern, expect_ok, ic=False):
    try:
        validate(pattern, ic)
        got = True
    except Reject as r:
        got = False
        why = str(r)
    return got == expect_ok


def self_check():
    bad = 0
    rows = []
    for p in MUST_REJECT:
        rows.append((p, False, False))
    for p in FLAG_PAIRS:
        rows.append((p, False, True))
        rows.append((p, True, False))
    for p, ok in BUDGET:
        rows.append((p, ok, False))
    for p in MUST_ACCEPT:
        rows.append((p, True, False))
    for p in STRUCTURAL_REJECT:
        rows.append((p, False, False))
    for p in STRUCTURAL_ACCEPT:
        rows.append((p, True, False))
    for p, ok, ic in rows:
        try:
            validate(p, ic)
            got = True
            why = ''
        except Reject as r:
            got = False
            why = str(r)
        flag = ' (i)' if ic else ''
        shown = p if len(p) <= 60 else p[:57] + '...'
        print(f"{'accept' if got else 'reject'}  {shown}{flag}" + ('' if got else f'   [{why}]'))
        if got != ok:
            bad += 1
            print(f"  ^^^ EXPECTED {'accept' if ok else 'reject'}")
    print(f'{len(rows)} patterns, {bad} disagreement(s)')
    return 1 if bad else 0


def cases_check(path):
    import re
    text = open(path, encoding='utf-8').read()
    bad = n = 0
    for rec in text.split('\n===')[:-1] if False else re.split(r'\n===\n?', text):
        m = re.search(r"--- source\n(?:IF\(FALSE, )?RMATCH\('((?:[^']|'')*)', (?:\"(?:[^\"\\]|\\.)*\")(?:, \"(i)\")?\)", rec)
        e = re.search(r'--- expect\n(.*)', rec)
        if not m or not e:
            continue
        pat = m.group(1).replace("''", "'")
        ic = m.group(2) == 'i'
        want_err = e.group(1).startswith('error E_REGEX_SYNTAX')
        n += 1
        try:
            validate(pat, ic)
            got_err = False
        except Reject:
            got_err = True
        except BadArg:
            continue
        if got_err != want_err:
            bad += 1
            print('DISAGREE', pat[:70], 'want', 'reject' if want_err else 'accept')
    print(f'{n} literal patterns checked, {bad} disagreement(s)')
    return 1 if bad else 0


def main():
    a = sys.argv[1:]
    if a[:1] == ['--self-check']:
        return self_check()
    if a[:1] == ['--cases']:
        return cases_check(a[1])
    ic = False
    if a[:1] == ['-i']:
        ic, a = True, a[1:]
    if len(a) != 1:
        print(__doc__)
        return 2
    try:
        validate(a[0], ic)
        print('accept')
        return 0
    except Reject as r:
        print(f'reject: {r}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
