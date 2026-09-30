"""PY-P18: SQL translation of a constant subtree re-evaluated it once per ancestor.
    PYTHONPATH=python python3 tools/perf/python/bench_p18_const_validate.py"""
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from harness import report, growth, checksum
import sel
from sel.sql import Sql, Binding


def prog(depth, n=50000):
    src = 'REPEAT("a", %d)' % n
    for _ in range(depth):
        src = 'UPPER(%s)' % src
    return sel.compile('LEN(%s) > 0' % src)


sums = []
for d in (2, 8, 16):
    p = prog(d)
    report('translate depth %2d (50k-char constant)' % d, lambda p=p: Sql.translate(p, 'mariadb'), reps=3)
    sums.append(Sql.translate(p, 'mariadb').as_value('params'))
CB = {'A': Binding.column('a', 'NUM'), 'B': Binding.column('b', 'NUM')}
# a realistic rule with small constants next to a column (control: must not regress)
ctl = sel.compile('A > 1 + 2 * 3 AND LEN(UPPER("abc" & "def")) == 6 OR B IN (1, 2, 3 + 4)')
report('small constants beside columns (control)', lambda: Sql.translate(ctl, 'mariadb', CB), reps=5, inner=50)
sums.append(Sql.translate(ctl, 'mariadb', CB).as_condition('params'))
print('checksum', checksum(sums))
def make(depth):
    p = prog(depth, 20000)
    return lambda: Sql.translate(p, 'mariadb')
growth('depth (20k-char constant)', make, [4, 8, 16])
