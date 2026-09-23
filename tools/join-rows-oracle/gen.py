"""tools/join-rows-oracle/gen.py COUNT SEED CORPUS EXPECT

Programs that build joined rows over relations of mixed shapes -- missing
fields, NULL, booleans, lists, nested records, names differing only in case --
through LINK and LINK_LEFT, three- and five-argument forms, named and literal
sources, and chains of two; with the rows spec §7.4 says they answer, from
model.py. One program per `### ` record in CORPUS, one expected dump per line
in EXPECT."""
import random, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from model import NULL, MissingKey, dump, source, extend, binder_names, link, get

count, seed, out_corpus, out_expect = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
R = random.Random(seed)

SCALARS = [('t', '1'), ('t', '2'), ('t', '5'), ('t', 'P'), ('t', 'Q'), NULL, ('bool', True),
           ('list', [('t', '1'), ('t', '2')]), ('rec', [('x', ('t', '1'))])]
WEIGHTS = [5, 5, 3, 5, 3, 3, 2, 1, 2]
FIELDS = ['name', 'Name', 'tier', 'amt', 'status', 'sku', 'id', 'Tier']
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


def program():
    ints = [('t', str(i)) for i in (1, 2, 3)]
    keys = ints + [NULL]
    A = relation(R.randint(1, 4), 'k', keys, R.sample(FIELDS, 4) + (R.sample(BINDERISH, 1) if R.random() < 0.2 else []))
    B = relation(R.randint(0, 4), 'id', ints, R.sample(FIELDS, 4) + ['k2'] + (R.sample(BINDERISH, 1) if R.random() < 0.2 else []))
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
        pipe = f'A .> {op1}(B, {ln}, {rn}, {ln}["k"] == {rn}["id"])'
        lnames, rnames = binder_names(ln), binder_names(rn)
        rows = link(A, B, lnames, rnames, key_reader(['k']), key_reader(['id']), lj1)
    elif form == 'literal-right':
        pipe = f'A .> {op1}({rel_source(B)}, _1["k"] == _2["id"])'
        lnames, rnames = binder_names('A'), []
        rows = link(A, B, lnames, rnames, key_reader(['k']), key_reader(['id']), lj1)
    elif form == 'pipeline-left':
        pipe = f'A .> TAKE(9) .> {op1}(B, _1["k"] == _2["id"])'
        lnames, rnames = binder_names('A'), binder_names('B')
        rows = link(A, B, lnames, rnames, key_reader(['k']), key_reader(['id']), lj1)
    else:
        pipe = f'A .> {op1}(B, _1["k"] == _2["id"])'
        lnames, rnames = binder_names('A'), binder_names('B')
        rows = link(A, B, lnames, rnames, key_reader(['k']), key_reader(['id']), lj1)
    if R.random() < 0.4 and form != 'literal-right':
        # a second join, keyed through the first join's right binder
        lj2 = R.random() < 0.35
        op2 = 'LINK_LEFT' if lj2 else 'LINK'
        bname = rnames[0] if rnames else '_2'
        pipe += f' .> {op2}(C, _1["{bname}"]["k2"] == _2["id"])'
        rows = link(rows, C, [], binder_names('C'), key_reader([bname, 'k2']), key_reader(['id']), lj2)
    return data + 'J = ' + pipe + '; J', dump(('list', rows)) if rows else '-'


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
