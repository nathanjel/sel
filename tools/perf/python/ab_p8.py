import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'python'))
from harness import bench, fmt, checksum, rng
import sel, sel.optimizer as O
real=O._literal_list
r=rng(17)
rows=[{'a': r.randrange(10), 'b': 'x%d' % r.randrange(400)} for _ in range(20000)]
lit=lambda k: ', '.join('"x%d"' % (i*2) for i in range(k))
cases=[('IN ("x..") 3','ROWS .> FILTER(_["b"] IN (%s)) .> COUNT()'%lit(3)),('IN LIST(..) 3','ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()'%lit(3)),('IN LIST(..) 10','ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()'%lit(10)),('IN LIST(..) 200','ROWS .> FILTER(_["b"] IN LIST(%s)) .> COUNT()'%lit(200))]
for name,src in cases:
    res={}; chk={}
    for rnd in range(3):
        for label,fn in (('per-row',lambda n:False),('constant',real)):
            O._literal_list=fn
            p=sel.compile(src); p.physical_ast()
            res.setdefault(label,[]).append(bench(lambda:p.run({'ROWS':rows}),reps=3,warm=1)[1])
            chk[label]=checksum(p.run({'ROWS':rows}).scalar)
    assert chk['per-row']==chk['constant'],chk
    print('%-18s per-row %9s  constant %9s  (%.2fx)  chk %s'%(name,fmt(min(res['per-row'])),fmt(min(res['constant'])),min(res['per-row'])/min(res['constant']),chk['constant']))
