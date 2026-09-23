"""tools/join-filter-oracle/gen.py -- SEL-0052's differential oracle: join-then-filter programs as written vs the
same with every join result bound to a helper variable (no FILTER over a
LINK, so no pre-filter can apply). Emits a corpus of pairs and a sidecar of
sources, one JSON record per line in corpus order, with where each shared
segment starts so an error compares as (segment, offset).

  gen.py COUNT SEED CORPUS SIDECAR [mixed|uniform]

`uniform` gives every row of a relation the same keys (values still vary)."""
import random, sys
count, seed, out_corpus, out_src = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
UNIFORM = len(sys.argv) > 5 and sys.argv[5] == 'uniform'
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
    op = R.choice(['==', '!=', '>', '<', '>=', '$==', '$!=', '$<'])
    left = ref(binder, two)
    if R.random() < 0.15:
        right = ref(binder, two)
    elif op.startswith('$'):
        right = R.choice(['"P"', '"Q"', '"1"', '"y"'])
    else:
        right = R.choice(['0', '1', '2', '3', '5', '"x"' if R.random() < 0.05 else '2'])
    return f'{left} {op} {right}'

def predicate(two):
    binder = '_' if R.random() < 0.8 else 'r'
    body = ' AND '.join(conjunct(binder, two) for _ in range(R.randint(1, 4)))
    return f'{binder}, {body}' if binder != '_' else body

pairs = []
for _ in range(count):
    d = data()
    two = R.random() < 0.6
    link1 = R.choice(['LINK', 'LINK', 'LINK_LEFT'])
    link2 = R.choice(['LINK', 'LINK', 'LINK_LEFT'])
    p1 = '_1["bid"] == _2["id"]'
    p2 = 'L, R, ' + R.choice(['L["cid"] == R["id"]', 'L["A"]["cid"] == R["id"]', 'L["B"]["cid"] == R["id"]', 'L["a"]["cid"] == R["id"]'])
    mid = predicate(False) if (two and R.random() < 0.35) else None
    final = predicate(two)
    tail = R.choice(['', '', ' .> MAP(_K)', ' .> MAP(1)', ' .> TAKE(2)', ' .> MAP(RECORD("s", _["A"]["status"] ?? "-"))'])
    segs = [('data', d), ('link1', f'{link1}(B, {p1})')]
    if two and mid is not None:
        segs.append(('mid', f'FILTER({mid})'))
    if two:
        segs.append(('link2', f'{link2}(C, {p2})'))
    segs.append(('final', f'FILTER({final}){tail}'))
    written = d + f'A .> {link1}(B, {p1})'
    oracle = d + f'J1 = A .> {link1}(B, {p1}); '
    last = 'J1'
    if two:
        if mid is not None:
            written += f' .> FILTER({mid})'
            oracle += f'M = J1 .> FILTER({mid}); '
            last = 'M'
        written += f' .> {link2}(C, {p2})'
        oracle += f'J2 = {last} .> {link2}(C, {p2}); '
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
