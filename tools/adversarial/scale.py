"""Measure latest EAV revision per entity against an indexed SQL baseline.

Times are single-run observations, not a statistical benchmark. SQL returns
flat latest rows; the caller can group those into the requested SEL shape.
"""
import json
import sqlite3
import time
from probe import CASES, compile, Value
from run import HERE, call

def main():
    out=[]
    source=CASES[-1][1]
    program=compile(source)
    for n in (1000,10000,100000):
        con=sqlite3.connect(':memory:')
        con.execute('CREATE TABLE r(id INTEGER PRIMARY KEY,cat TEXT,v INTEGER,fk INTEGER)')
        con.executemany('INSERT INTO r VALUES (?, ?, ?, ?)',((i,'weight',i%100,(i-1)%100) for i in range(1,n+1)))
        con.execute('CREATE INDEX entity_revision ON r(fk,id)')
        baseline='SELECT r.* FROM r JOIN (SELECT fk, MAX(id) AS latest FROM r GROUP BY fk) g ON r.id=g.latest'
        start=time.perf_counter();expected=con.execute(baseline).fetchall();sql_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter();rows=[dict(zip(('id','cat','v','fk'),map(str,r))) for r in con.execute('SELECT * FROM r')];fetch_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter();ctx=Value.from_native({'R':rows});ingest_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter();value=program.run(ctx);dump=value.to_native();eval_ms=(time.perf_counter()-start)*1000
        groups=dump.values() if isinstance(dump,dict) else dump
        ids=[]
        for group in groups:
            latest=group['latest'];entries=latest.values() if isinstance(latest,dict) else latest
            ids.extend(int(r['id']) for r in entries)
        assert sorted(ids)==sorted(r[0] for r in expected)
        result=dict(n=n,groups=len(expected),sql_ms=sql_ms,fetch_ms=fetch_ms,ingest_ms=ingest_ms,eval_ms=eval_ms,transfer_amplification=n/len(expected),source=source)
        out.append(result)
        print(json.dumps(result),flush=True)
    (HERE/'scale-results.json').write_text(json.dumps(out,indent=2))

if __name__=='__main__':main()
