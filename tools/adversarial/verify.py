"""Assert that the recorded evidence supports the reported findings.

These assertions deliberately describe current defects, not desired behavior.
They will need updating when SEL is fixed.
"""
import json
from pathlib import Path

p=Path(__file__).resolve().parent
read=lambda f:json.loads((p/f).read_text())
t=read('translations.json');db=read('database-results.json');local=read('local-results.json');hy=read('all-host-hybrid.json')
assert len(local)==8
for host,rows in local.items():
    assert len(rows)==20,(host,len(rows))
    assert [s.split('@')[0] for s in rows]==[s.split('@')[0] for s in local['python']],host
for host,rows in t.items():
    assert len(rows)==120
    for a,b in zip(rows,t['python']):
        for field in ('sql','params_sql','params','plan','prefix'):
            assert a.get(field)==b.get(field),(host,a['i'],field)
for host in t:
 for strict in (False,True):
  for d in ('sqlite','postgresql','mariadb'):
   for mode in ('inline','params','prefix'):
    get=lambda i:next(r for r in db if all(r[k]==v for k,v in dict(host=host,strict=strict,d=d,mode=mode,i=i).items()))
    assert get(8)['rows']==[['2'],['3']]
    assert get(17)['rows']==[['3']]
    if d=='mariadb':
        assert get(0)['rows']==[['a','2']]
        assert get(1)['rows']==[['1'],['2']]
        if not strict:assert get(14)['rows']==[['1'],['1']]
    if d!='sqlite':
        assert get(6)['rows'][0][1]=='2'
        assert 'error' in get(12)
    plan=next(x for x in t[host] if x['i']==19 and x['d']==d and x['strict']==strict)
    assert plan['plan']=='pure_memory'
for host,rows in hy.items():
 for x in rows:
    if x['i']==13:
        if x['d']=='sqlite' and x['strict']:assert 'hybrid_value' in x
        else:assert x['hybrid_error']=='E_NO_KEY',(host,x)
scale=read('scale-results.json')
assert scale[-1]['transfer_amplification']==1000
assert scale[-1]['groups']==100
print('Evidence verified: eight local variants, all translator variants, live inline/parameter/prefix results, hybrid failures, and scale measurements.')
