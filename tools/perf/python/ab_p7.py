import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..', '..', '..', 'python'))
from harness import bench, fmt
import sel, sel.optimizer as O
CTX = {'A': '3.5', 'B': '4', 'C': '7.25', 'D': True}
M='A * B + C - A * B * C / B'
cases=[('math + IF operand','(%s) + IF(D, 1, 2)'%M),('math*IF*math','(%s) * IF(D, 2, 3) - (A * B + C)'%M),('MAX with IF','MAX(%s, IF(D, 1, 2), A * B * C)'%M),('pure math',M)]
real=O.attach_subplans
for name,src in cases:
    res={}
    for rnd in range(3):
        for label,fn in (('no subplans',lambda n:None),('subplans',real)):
            O.attach_subplans=fn
            p=sel.compile(src); p.physical_ast()
            res.setdefault(label,[]).append(bench(lambda:p.run(dict(CTX)),reps=5,warm=1,inner=2000)[1])
    print('%-20s no-subplans %8s  subplans %8s   (%.2fx)'%(name,fmt(min(res['no subplans'])),fmt(min(res['subplans'])),min(res['no subplans'])/min(res['subplans'])))
