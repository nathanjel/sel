"""tools/join-rows-oracle/gen.py COUNT SEED CORPUS EXPECT

Programs that build joined rows over relations of mixed shapes -- missing
fields, NULL, booleans, lists, nested records, names differing only in case --
through LINK and LINK_LEFT, three- and five-argument forms, named and literal
sources, and chains of two; with the rows spec §7.4 says they answer, from
model.py. One program per `### ` record in CORPUS, one expected dump per line
in EXPECT."""
import random, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from model import NULL, MissingKey, PredictedError, dump, source, extend, binder_names, link, get

count, seed, out_corpus, out_expect = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
R = random.Random(seed)

SCALARS = [('t', '1'), ('t', '2'), ('t', '5'), ('t', 'P'), ('t', 'Q'), NULL, ('bool', True),
           ('list', [('t', '1'), ('t', '2')]), ('rec', [('x', ('t', '1'))])]
WEIGHTS = [5, 5, 3, 5, 3, 3, 2, 1, 2]
FIELDS = ['name', 'Name', 'tier', 'amt', 'status', 'sku', 'id', 'Tier']
# Names that differ only outside ASCII (review 2026-09-25 TEST-08): distinct
# names for promotion, which compares ASCII-case-insensitively.
UNICODE_FIELDS = [['ß', 'SS'], ['é', 'É'], ['ſ', 's'], ['ı', 'I']]
# Names a relation binds under, in both cases: a field may shadow a binder key
# (an element that already has the key is not extended with it).
BINDERISH = ['a', 'B', 'b', 'c', 'x']


def value():
    return R.choices(SCALARS, weights=WEIGHTS)[0]


def relation(n, keyfield, keys, pool):
    rows = []
    for _ in range(n):
        fields = [(keyfield, R.choice(keys))]
        for f in pool:
            if R.random() < 0.55 and f not in [k for k, _ in fields]:
                fields.append((f, value()))
        R.shuffle(fields)
        rows.append(('rec', fields))
    return rows


def rel_source(rows):
    return 'LIST(' + ', '.join(source(r) for r in rows) + ')'


def key_reader(path):
    def read(elem):
        v = elem
        for p in path:
            if v is None or v[0] != 'rec':
                return None
            v = get(v, p)
        return v
    return read


# Join keys beyond integers and NULL (review 2026-09-25 TEST-08): keys the
# comparison matches across kinds, and keys it rejects. The model predicts the
# error as well as the rows.
KEYS_EQ = [('t', '1'), ('t', '2'), ('t', '3'), ('t', '1.0'), ('t', 'bad'), ('bool', True)]
KEYS_TXT = [('t', 'a'), ('t', 'b'), ('bin', b'a'), ('bin', b'b'), ('t', '1'), ('bool', True)]
counts = {'unicode-names': 0, 'bin-text-keys': 0, 'rejected-keys': 0, 'values': 0, 'errors': 0}


def program():
    ints = [('t', str(i)) for i in (1, 2, 3)]
    op = '=='
    lkeys = ints + [NULL]
    rkeys = ints
    exotic = R.random() < 0.3
    if exotic:
        op = R.choice(['==', '$=='])
        domain = KEYS_EQ if op == '==' else KEYS_TXT
        lkeys = domain + [NULL]
        rkeys = domain + [NULL]
    pool_a = R.sample(FIELDS, 4) + (R.sample(BINDERISH, 1) if R.random() < 0.2 else [])
    pool_b = R.sample(FIELDS, 4) + ['k2'] + (R.sample(BINDERISH, 1) if R.random() < 0.2 else [])
    uni = None
    if R.random() < 0.25:
        uni = R.choice(UNICODE_FIELDS)
        pool_a.append(uni[0])
        pool_b.append(uni[1])
    A = relation(R.randint(1, 4), 'k', lkeys, pool_a)
    B = relation(R.randint(0, 4), 'id', rkeys, pool_b)
    for row in B:
        # a second-join key, always an integer when present
        row[1][:] = [(k, R.choice(ints) if k == 'k2' else v) for k, v in row[1]]
    C = relation(R.randint(0, 3), 'id', ints, R.sample(FIELDS, 3))
    data = f'A = {rel_source(A)}; B = {rel_source(B)}; C = {rel_source(C)}; '
    lj1 = R.random() < 0.35
    op1 = 'LINK_LEFT' if lj1 else 'LINK'
    form = R.choice(['named', 'named', 'five', 'literal-right', 'pipeline-left'])
    if form == 'five':
        ln, rn = R.choice([('L', 'R'), ('o', 'c'), ('Lx', 'Rx')])
        lexpr, rexpr = f'{ln}["k"]', f'{rn}["id"]'
        pipe = f'A .> {op1}(B, {ln}, {rn}, {lexpr} {op} {rexpr})'
        lnames, rnames = binder_names(ln), binder_names(rn)
    elif form == 'literal-right':
        lexpr, rexpr = '_1["k"]', '_2["id"]'
        pipe = f'A .> {op1}({rel_source(B)}, {lexpr} {op} {rexpr})'
        lnames, rnames = binder_names('A'), []
    elif form == 'pipeline-left':
        lexpr, rexpr = '_1["k"]', '_2["id"]'
        pipe = f'A .> TAKE(9) .> {op1}(B, {lexpr} {op} {rexpr})'
        lnames, rnames = binder_names('A'), binder_names('B')
    else:
        lexpr, rexpr = '_1["k"]', '_2["id"]'
        pipe = f'A .> {op1}(B, {lexpr} {op} {rexpr})'
        lnames, rnames = binder_names('A'), binder_names('B')
    head = data + 'J = '
    pred = f'{lexpr} {op} {rexpr}'
    at = len(head) + pipe.index(pred)
    lpos = at + lexpr.index('[') + 1
    rpos = at + len(lexpr) + len(f' {op} ') + rexpr.index('[') + 1
    try:
        rows = link(A, B, lnames, rnames, key_reader(['k']), key_reader(['id']), lj1, op, lpos, rpos)
    except PredictedError as e:
        counts['errors'] += 1
        if exotic:
            counts['rejected-keys'] += 1
        return head + pipe + '; J', f'!{e.code}@1:{e.pos}'
    if R.random() < 0.4 and form != 'literal-right':
        # a second join, keyed through the first join's right binder
        lj2 = R.random() < 0.35
        op2 = 'LINK_LEFT' if lj2 else 'LINK'
        bname = rnames[0] if rnames else '_2'
        pipe += f' .> {op2}(C, _1["{bname}"]["k2"] == _2["id"])'
        rows = link(rows, C, [], binder_names('C'), key_reader([bname, 'k2']), key_reader(['id']), lj2)
    counts['values'] += 1
    if uni and rows:
        counts['unicode-names'] += 1
    if op == '$==' and rows and any(r[1] and any(k == 'k' and v[0] == 'bin' for k, v in (r[1] if r[0] == 'rec' else [])) for r in rows):
        counts['bin-text-keys'] += 1
    return head + pipe + '; J', dump(('list', rows)) if rows else '-'


corpus, expect = [], []
while len(corpus) < count:
    try:
        p, x = program()
    except MissingKey:
        continue
    corpus.append(p)
    expect.append(x)

with open(out_corpus, 'w') as c, open(out_expect, 'w') as e:
    for p, x in zip(corpus, expect):
        c.write('### \n' + p + '\n')
        e.write(x + '\n')

# Every targeted category must occur in every corpus, or the run proves nothing
# about it (review 2026-09-25 TEST-08).
sys.stderr.write('join-rows-oracle: ' + ' '.join(f'{k}={v}' for k, v in counts.items()) + '\n')
missing = [k for k, v in counts.items() if v == 0]
if missing and count >= 200:
    sys.stderr.write(f'join-rows-oracle: no program of category {", ".join(missing)}; raise the count\n')
    sys.exit(1)
