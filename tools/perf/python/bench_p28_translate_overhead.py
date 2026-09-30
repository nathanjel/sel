"""PY-P28: SQL translator per-translation overhead (template re-tokenising, stage 1 copies).
    PYTHONPATH=python python3 tools/perf/python/bench_p28_translate_overhead.py [--profile]"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, checksum
from sel import compile
from sel.sql import Sql
from sel.sql.binding import Binding

B = {
    'TOTAL': Binding.column('total', 'o', 'NUM'),
    'CREDIT': Binding.column('credit', 'o', 'NUM'),
    'NAME': Binding.column('name', 'o', 'TEXT'),
    'CITY': Binding.column('city', 'o', 'TEXT'),
    'NOTE': Binding.column('note', 'o', 'TEXT'),
}
RULE = ('TOTAL > CREDIT AND TOTAL >= 0 AND UPPER(NAME) $== "ACME" AND LEN(CITY) > 2 '
        'AND IF(TOTAL > 100, TOTAL * 2 > CREDIT, TOTAL + 1 < CREDIT) AND LEFT(NAME, 3) $== "ACM" '
        'AND FIND("x", NOTE) > 0 OR TRIM(NOTE) $!= "" AND ABS(TOTAL - CREDIT) < 50 AND LOWER(CITY) $!= "lodz"')
program = compile(RULE)

def translate():
    return [Sql.translate(program, d, B).as_condition() for d in ('postgresql', 'mariadb', 'sqlite')]

def hundred():
    for _ in range(100):
        translate()

report('translate 10-clause rule x3 dialects', translate, reps=7, inner=20)
out = translate()
print('checksum', checksum(out))
if '--profile' in sys.argv:
    import cProfile, pstats
    pr = cProfile.Profile(); pr.enable(); hundred(); pr.disable()
    pstats.Stats(pr).sort_stats('cumtime').print_stats(14)
