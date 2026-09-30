"""In-process A/B of SQL translation: baseline tree vs working tree (alternating, best CPU time).
    python3 tools/perf/python/ab_translate.py BASELINE_DIR WORKING_DIR [ROUNDS]"""
import sys
import time


def load(root):
    sys.path.insert(0, root)
    for k in [k for k in sys.modules if k == 'sel' or k.startswith('sel.')]:
        del sys.modules[k]
    import sel
    from sel.sql import Sql
    from sel.sql.binding import Binding
    sys.path.pop(0)
    return sel, Sql, Binding


RULE = ('TOTAL > CREDIT AND TOTAL >= 0 AND UPPER(NAME) $== "ACME" AND LEN(CITY) > 2 '
        'AND IF(TOTAL > 100, TOTAL * 2 > CREDIT, TOTAL + 1 < CREDIT) AND LEFT(NAME, 3) $== "ACM" '
        'AND FIND("x", NOTE) > 0 OR TRIM(NOTE) $!= "" AND ABS(TOTAL - CREDIT) < 50 AND LOWER(CITY) $!= "lodz"')
rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 9
rigs = []
for root in sys.argv[1:3]:
    sel, Sql, Binding = load(root)
    B = {'TOTAL': Binding.column('total', 'o', 'NUM'), 'CREDIT': Binding.column('credit', 'o', 'NUM'),
         'NAME': Binding.column('name', 'o', 'TEXT'), 'CITY': Binding.column('city', 'o', 'TEXT'),
         'NOTE': Binding.column('note', 'o', 'TEXT')}
    prog = sel.compile(RULE)
    rigs.append((lambda prog=prog, Sql=Sql, B=B: [Sql.translate(prog, d, B).as_condition()
                                                 for d in ('postgresql', 'mariadb', 'sqlite')]))
assert rigs[0]() == rigs[1](), 'outputs differ'
best = [1e9, 1e9]
for _ in range(rounds):
    for k in (0, 1):
        s = time.process_time()
        for _ in range(20):
            rigs[k]()
        best[k] = min(best[k], (time.process_time() - s) / 20)
print('translate x3 dialects: base %.2f ms  new %.2f ms  new/base %.2f' % (best[0] * 1e3, best[1] * 1e3, best[1] / best[0]))
