"""tools/join-filter-oracle/gen.py -- SEL-0052's differential oracle: join-then-filter programs as written vs the
same with every join result bound to a helper variable (no FILTER over a
LINK, so no pre-filter can apply). Emits a corpus of pairs and a sidecar of
sources, one JSON record per line in corpus order, with where each shared
segment starts so an error compares as (segment, offset).

  gen.py COUNT SEED CORPUS SIDECAR [mixed|uniform|pipeline|leftnull]

`uniform` gives every row of a relation the same keys (values still vary)."""
import random, sys
count, seed, out_corpus, out_src = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
UNIFORM = len(sys.argv) > 5 and sys.argv[5] == 'uniform'
# `pipeline`: no joins -- sorts, maps, filters and slices.
PIPELINE = len(sys.argv) > 5 and sys.argv[5] == 'pipeline'
# `leftnull`: a LINK_LEFT (or two joins ending in one) under a FILTER that opens
# with IS_NULL of a right member's field, over rows that carry their fields and
# often a NULL id -- so most programs answer, and a host that rejects right rows
# before the join must still answer exactly that.
LEFTNULL = len(sys.argv) > 5 and sys.argv[5] == 'leftnull'
R = random.Random(seed)

def val():
    return R.choices(['0', '1', '2', '3', '5', '8', '"P"', '"Q"', '"y"', 'NULL', 'TRUE'],
                     weights=[6, 6, 6, 6, 5, 4, 6, 5, 2, 1, 1])[0]

def rows(fields, keyfield_vals, n):
    out = []
    if UNIFORM:
        fields = [f for f in fields if f in keyfield_vals or R.random() < 0.8]
    for i in range(n):
        parts = []
        for f in fields:
            if f in keyfield_vals:
                if UNIFORM or R.random() < 0.95:
                    parts.append(f'"{f}", {R.choice(keyfield_vals[f])}')
            elif UNIFORM or R.random() < 0.8:
                parts.append(f'"{f}", {val()}')
        out.append('RECORD(' + ', '.join(parts) + ')')
    return 'LIST(' + ', '.join(out) + ')'

def data():
    ids = ['1', '2', '3', '4']
    a_fields = ['id', 'bid', 'cid', 'status', 'amt'] + (['tier'] if R.random() < 0.3 else [])
    b_fields = ['id', 'cid', 'tier', 'name'] + (['amt'] if R.random() < 0.3 else [])
    c_fields = ['id', 'sku'] + (['amt'] if R.random() < 0.3 else []) + (['status'] if R.random() < 0.2 else [])
    kv = {'id': ids, 'bid': ids + ['"y"'], 'cid': ids}
    A = rows(a_fields, kv, R.randint(2, 6))
    B = rows(b_fields, kv, R.randint(1, 5))
    C = rows(c_fields, kv, R.randint(1, 5))
    return f'A = {A}; B = {B}; C = {C}; '

FIELDS = ['id', 'bid', 'cid', 'status', 'amt', 'tier', 'name', 'sku']

def ref(binder, two):
    r = R.random()
    tables = ['A', 'B'] + (['C'] if two else [])
    if r < 0.55:
        return f'{binder}["{R.choice(FIELDS)}"]'
    t = R.choice(tables + [t.lower() for t in tables])
    return f'{binder}["{t}"]["{R.choice(FIELDS)}"]'

def conjunct(binder, two):
    r = R.random()
    if r < 0.06:
        return R.choice(['TRUE', '_K $!= "0"' if binder == '_' else 'TRUE', 'NOT (' + ref(binder, two) + ' $== "P")'])
    if r < 0.12:
        return f'({ref(binder, two)} ?? 0) > {R.randint(0, 5)}'
    if r < 0.16:
        return f'IS_NULL({ref(binder, two)})'
    op = R.choice(['==', '!=', '>', '<', '>=', '$==', '$!=', '$<'])
    left = ref(binder, two)
    if R.random() < 0.15:
        right = ref(binder, two)
    elif op.startswith('$'):
        right = R.choice(['"P"', '"Q"', '"1"', '"y"'])
    else:
        right = R.choice(['0', '1', '2', '3', '5', '"x"' if R.random() < 0.05 else '2'])
    return f'{left} {op} {right}'

def predicate(two, right=None):
    """RIGHT: the member names of the join's right binder under the FILTER
    (a LINK_LEFT's), so that the predicate sometimes opens with IS_NULL of a
    right member's field -- the shape a host may test on the right rows before
    it builds their joined rows, which must not change what the FILTER answers
    (a matched element whose matches all fail keeps no null-extended row)."""
    binder = '_' if R.random() < 0.8 else 'r'
    conjuncts = [conjunct(binder, two) for _ in range(R.randint(1, 4))]
    if right is not None and R.random() < 0.5:
        member = R.choice(right + ['B', 'C', 'X'])
        field = R.choice(['id', 'id', 'id', 'name', 'sku', 'tier', 'amt'])
        lead = f'IS_NULL({binder}["{member}"]["{field}"])'
        conjuncts = [lead] + (conjuncts[:R.randint(0, 2)] if R.random() < 0.7 else [])
    body = ' AND '.join(conjuncts)
    return f'{binder}, {body}' if binder != '_' else body

def pipeline_pair():
    """Rows, then two to four steps, some of which fail on one row. The oracle
    binds every step's result to a helper variable, so no rewrite can move,
    fuse or skip a step: the as-written form must raise what it raises, keep
    the keys it keeps and answer what it answers -- no optimiser rewrite may
    change any of the three."""
    n = R.randint(2, 5)
    rows = 'LIST(' + ', '.join(f'RECORD("id", {i}, "v", {R.randint(0, 3)}, "s", "{R.choice("PQy")}")'
                               for i in range(1, n + 1)) + ')'
    d = f'ROWS = {rows}; '
    bad = lambda: R.randint(1, n)
    def failing(expr):
        return f'IF(_["id"] == {bad()}, ABORT("x"), {expr})' if R.random() < 0.35 else expr
    def step():
        k = R.randint(0, 10)
        if k == 0: return f'SORT(_, {failing(chr(95) + "[" + chr(34) + "v" + chr(34) + "]")})'
        if k == 1: return f'SORT_BY({failing("_[" + chr(34) + "v" + chr(34) + "]")}{R.choice(["", ", " + chr(34) + "DESC" + chr(34)])})'
        if k == 2: return f'TOP_BY({failing("_[" + chr(34) + "id" + chr(34) + "]")}, {R.randint(1, 3)})'
        if k == 3: return f'MAP(RECORD("id", _["id"], "v", _["v"], "s", _["s"], "w", {R.choice(["1/(_[" + chr(34) + "id" + chr(34) + "] - " + str(bad()) + ")", "_[" + chr(34) + "v" + chr(34) + "] + 1", "_K"])}))'
        if k in (4, 5): return f'FILTER({failing(R.choice(["_[" + chr(34) + "v" + chr(34) + "] > 1", "_[" + chr(34) + "s" + chr(34) + "] $== " + chr(34) + "P" + chr(34), "_K $!= " + chr(34) + "2" + chr(34)]))})'
        if k == 6: return f'TAKE({R.randint(1, 3)})'
        if k == 7: return 'DEDUPE()'
        if k == 8: return f'MAP(_["id"])'
        if k == 9: return f'MAP(_K)'
        return f'FILTER({failing("TRUE")})'
    steps = [step() for _ in range(R.randint(2, 4))]
    segs = [('data', d)] + [(f'step{i}', st) for i, st in enumerate(steps)]
    written = d + 'ROWS' + ''.join(f' .> {st}' for st in steps)
    oracle = d
    last = 'ROWS'
    for i, st in enumerate(steps[:-1]):
        oracle += f'S{i} = {last} .> {st}; '
        last = f'S{i}'
    oracle += f'{last} .> {steps[-1]}'
    return written, oracle, segs

def leftnull_pair():
    def rel(fields, n, ids):
        out = []
        for _ in range(n):
            parts = []
            for f in fields:
                if f == 'id':
                    v = R.choice(ids + ['NULL'] * 2 + (['RECORD()', 'LIST()'] if R.random() < 0.1 else []))
                elif f in ('bid', 'cid', 'aid'):
                    v = R.choice(ids)
                else:
                    v = R.choice(['0', '1', '2', '"P"', '"Q"'])
                if R.random() < 0.97:
                    parts.append(f'"{f}", {v}')
            out.append('RECORD(' + ', '.join(parts) + ')')
        return 'LIST(' + ', '.join(out) + ')'
    ids = ['1', '2', '3', '4']
    d = (f'A = {rel(["id", "bid", "cid", "status"], R.randint(0, 6), ids)}; '
         f'B = {rel(["id", "aid", "tier"], R.randint(0, 6), ids)}; '
         f'C = {rel(["id", "sku", "amt"], R.randint(0, 5), ids)}; ')
    two = R.random() < 0.35
    binder = '_' if R.random() < 0.8 else 'r'
    if two:
        link1 = R.choice(['LINK', 'LINK_LEFT'])
        j1 = f'{link1}(B, _1["bid"] == _2["aid"])'
        # Explicit binders: the helper form's second join has a named left
        # source (J0), which would bind J0 in its rows; the chain's has none.
        j2 = 'LINK_LEFT(C, L, R, L["A"]["cid"] == R["id"])'
        members = ['R', 'r', '_2']
        joins = [('link1', j1), ('link2', j2)]
    else:
        explicit = R.random() < 0.3
        j1 = ('LINK_LEFT(B, L, R, L["bid"] == R["aid"])' if explicit
              else 'LINK_LEFT(B, _1["bid"] == _2["aid"])')
        members = ['R', 'r', '_2'] if explicit else ['B', 'b', '_2']
        joins = [('link1', j1)]
    member = R.choice(members * 4 + ['A', 'X', members[0].lower() + 'x'])
    lead = f'IS_NULL({binder}["{member}"]["{R.choice(["id"] * 5 + ["tier", "sku", "amt", "zz"])}"])'
    rest = []
    for _ in range(R.choice([0, 0, 1, 1, 2])):
        rest.append(R.choice([f'{binder}["status"] $!= "Q"', f'{binder}["A"]["status"] != 2',
                              f'_K $!= "2"' if binder == '_' else 'TRUE', f'{binder}["A"]["id"] > 1',
                              f'({binder}["A"]["status"] ?? 0) < 2', f'NOT IS_NULL({binder}["A"]["id"])']))
    body = ' AND '.join([lead] + rest)
    pred = f'{binder}, {body}' if binder != '_' else body
    tail = R.choice(['', ' .> MAP(_K)', ' .> MAP(_["A"]["id"])', ' .> TAKE(2)', ' .> MAP(RECORD("k", _K, "a", _["A"]["id"]))'])
    segs = [('data', d)] + joins + [('final', f'FILTER({pred}){tail}')]
    written = d + 'A' + ''.join(f' .> {j}' for _, j in joins) + f' .> FILTER({pred}){tail}'
    oracle = d
    last = 'A'
    for i, (_, j) in enumerate(joins):
        oracle += f'J{i} = {last} .> {j}; '
        last = f'J{i}'
    oracle += f'{last} .> FILTER({pred}){tail}'
    return written, oracle, segs

pairs = []
for _ in range(count if PIPELINE else 0):
    pairs.append(pipeline_pair())
for _ in range(count if LEFTNULL else 0):
    pairs.append(leftnull_pair())
for _ in range(0 if (PIPELINE or LEFTNULL) else count):
    d = data()
    two = R.random() < 0.6
    link1 = R.choice(['LINK', 'LINK', 'LINK_LEFT'])
    link2 = R.choice(['LINK', 'LINK', 'LINK_LEFT'])
    # Sources that raise (an undefined name), now and then: a host that evaluates
    # the right source of a join before its left one -- which the join pre-filter
    # does for a FILTER handed down through a LINK -- reports the wrong error when
    # both raise (SPEC 7.4: written order). The oracle form evaluates in written
    # order, so the two forms disagree on such a program until the host is right.
    LA = 'NOPE_A' if R.random() < 0.10 else 'A'
    RB = 'NOPE_B' if R.random() < 0.06 else 'B'
    RC = 'NOPE_C' if R.random() < 0.10 else 'C'
    p1 = '_1["bid"] == _2["id"]'
    p2 = 'L, R, ' + R.choice(['L["cid"] == R["id"]', 'L["A"]["cid"] == R["id"]', 'L["B"]["cid"] == R["id"]', 'L["a"]["cid"] == R["id"]'])
    mid = predicate(False, ['B', 'b', '_2'] if link1 == 'LINK_LEFT' else None) if (two and R.random() < 0.35) else None
    final = predicate(two, (['R', 'r', '_2'] if two else ['B', 'b', '_2']) if (link2 if two else link1) == 'LINK_LEFT' else None)
    tail = R.choice(['', '', ' .> MAP(_K)', ' .> MAP(1)', ' .> TAKE(2)', ' .> MAP(RECORD("s", _["A"]["status"] ?? "-"))'])
    segs = [('data', d), ('link1', f'{link1}({RB}, {p1})')]
    if two and mid is not None:
        segs.append(('mid', f'FILTER({mid})'))
    if two:
        segs.append(('link2', f'{link2}({RC}, {p2})'))
    segs.append(('final', f'FILTER({final}){tail}'))
    written = d + f'{LA} .> {link1}({RB}, {p1})'
    oracle = d + f'J1 = {LA} .> {link1}({RB}, {p1}); '
    last = 'J1'
    if two:
        if mid is not None:
            written += f' .> FILTER({mid})'
            oracle += f'M = J1 .> FILTER({mid}); '
            last = 'M'
        written += f' .> {link2}({RC}, {p2})'
        oracle += f'J2 = {last} .> {link2}({RC}, {p2}); '
        last = 'J2'
    written += f' .> FILTER({final}){tail}'
    oracle += f'{last} .> FILTER({final}){tail}'
    pairs.append((written, oracle, segs))

import json
with open(out_corpus, 'w') as c, open(out_src, 'w') as s:
    for w, o, segs in pairs:
        for p in (w, o):
            c.write('### \n' + p + '\n')
            # Where each shared segment starts in this program, so an error
            # position compares as (segment, offset) across the two forms.
            spans, at = [], 0
            for name, text in segs:
                start = p.index(text, at)
                spans.append((name, start, len(text)))
                at = start + len(text)
            s.write(json.dumps({'src': p, 'spans': spans}) + '\n')
