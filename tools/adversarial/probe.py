"""Adversarial SEL statement probes; uses only disposable sel-audit databases."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python'))
from sel import compile, Value
from sel.sql import Sql, Binding
from sel.eval import eval_node, Context


def bindings():
    return {name.upper(): Binding.relation(name, name, {
        col.upper(): Binding.column(col, name, 'TEXT' if col == 'cat' else 'NUM')
        for col in ('id', 'cat', 'v', 'fk')}) for name in ('r', 's')}


def db(dialect, sql):
    if dialect == 'sqlite':
        con = sqlite3.connect(':memory:')
        statements = sql.split(';')
        for st in statements[:-1]:
            if st.strip(): con.execute(st)
        return con.execute(statements[-1]).fetchall()
    cmd = (['docker', 'exec', '-i', 'sel-audit-pg-0915', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'sel_audit']
           if dialect == 'postgresql' else
           ['docker', 'exec', '-i', 'sel-audit-maria-0915', 'mariadb', '-uroot', '-psel_audit', '-N', '-B', 'sel_audit'])
    p = subprocess.run(cmd, input=sql, text=True, capture_output=True, timeout=30)
    return {'exit': p.returncode, 'rows': p.stdout.strip(), 'error': p.stderr.strip()}


def fixture(rows, others):
    out = []
    for name, data in [('r', rows), ('s', others)]:
        out += [f'DROP TABLE IF EXISTS {name}', f'CREATE TABLE {name}(id INTEGER, cat VARCHAR(80), v DECIMAL(30,10), fk INTEGER)']
        if data:
            vals = ','.join('(' + ','.join("'" + str(v).replace("'", "''") + "'" for v in row.values()) + ')' for row in data)
            out.append(f'INSERT INTO {name} VALUES {vals}')
    return ';'.join(out) + ';'


def row(id, cat='a', v='1', fk='1'):
    return dict(id=str(id), cat=cat, v=str(v), fk=str(fk))


CASES = [
    ('star.trailing-space-group', 'R .> BUCKET(_["cat"], RECORD("cat", _K, "n", COUNT(_)))', [row(1,'a'),row(2,'a ')]),
    ('eav.trailing-space-equality', 'R .> FILTER(_["cat"] $== "a") .> MAP(RECORD("id", _["id"]))', [row(1,'a'),row(2,'a ')]),
    ('star.integer-division', 'R .> MAP(RECORD("ratio", _["id"] / _["fk"]))', [row(1,fk=3)]),
    ('star.decimal-division', 'R .> MAP(RECORD("ratio", _["v"] / _["fk"]))', [row(1,v='1',fk=3)]),
    ('normalized.left-missing', 'R .> LINK_LEFT(S, _1["fk"] == _2["id"]) .> MAP(RECORD("cat", _["S"]["cat"]))', [row(1,fk=99)]),
    ('star.composite-group', 'R .> BUCKET(LIST(_["cat"], _["fk"]), RECORD("n", COUNT(_)))', [row(1),row(2,'b')]),
    ('eav.numeric-text-group', 'R .> BUCKET(_["cat"] + 0, RECORD("key", _K, "n", COUNT(_)))', [row(1,'1'),row(2,'1.0')]),
    ('star.duplicate-projection', 'R .> MAP(RECORD("x", _["id"], "x", _["fk"]))', [row(1,fk=9)]),
    ('normalized.take-drop', 'R .> SORT_BY(_["id"]) .> TAKE(2) .> DROP(1) .> MAP(RECORD("id", _["id"]))', [row(1),row(2),row(3)]),
    ('normalized.double-take', 'R .> SORT_BY(_["id"]) .> TAKE(1) .> TAKE(2) .> MAP(RECORD("id", _["id"]))', [row(1),row(2),row(3)]),
    ('eav.dirty-cast', 'R .> FILTER(_["cat"] + 0 > 0) .> MAP(RECORD("id", _["id"]))', [row(1,'bad'),row(2,'2')]),
    ('star.divide-zero', 'R .> MAP(RECORD("v", _["v"] / _["fk"]))', [row(1,fk=0)]),
    ('normalized.duplicate-filter', 'R .> MAP(RECORD("x", _["id"], "x", _["fk"])) .> FILTER(_["x"] > 5)', [row(1,fk=9)]),
    ('normalized.join-page', 'R .> LINK(S, _1["fk"] == _2["id"]) .> SORT_BY(_["R"]["id"]) .> TAKE(2) .> DROP(1) .> MAP(RECORD("id", _["R"]["id"], "dimension", _["S"]["cat"]))', [row(1),row(2),row(3)]),
    ('eav.attribute-join', 'R .> LINK(S, _1["fk"] == _2["fk"]) .> FILTER(_["R"]["cat"] $== "color" AND _["S"]["cat"] $== "dimension") .> MAP(RECORD("entity", _["R"]["fk"]))', [row(1,'color'),row(2,'color ')]),
    ('star.duplicate-derived', 'R .> MAP(RECORD("x", _["id"], "x", _["fk"])) .> TAKE(1) .> FILTER(_["x"] > 5)', [row(1,fk=9)]),
    ('normalized.drop-drop', 'R .> SORT_BY(_["id"]) .> DROP(1) .> DROP(1) .> MAP(RECORD("id", _["id"]))', [row(1),row(2),row(3)]),
    ('normalized.take-zero', 'R .> SORT_BY(_["id"]) .> TAKE(1) .> DROP(2) .> MAP(RECORD("id", _["id"]))', [row(1),row(2),row(3)]),
    ('eav.local-text-step', 'R .> FILTER(BACKWARDS(_["cat"]) $== "a") .> TAKE(1)', [row(1,'b'),row(2,'a')]),
    ('eav.latest-per-entity', 'R .> BUCKET(_["fk"]) .> MAP(RECORD("entity", _K, "latest", TOP_BY(_, _["id"], "DESC", 1)))', [row(1,'weight',v=60),row(2,'weight',v=65),row(3,'weight',v=70,fk=2)]),
]


def main():
    results=[]
    for name, source, rows in CASES:
        ctx=Value.from_native({'R':rows,'S':[row(1,'dimension')]})
        p=compile(source)
        result={'name':name,'source':source,'rows':rows}
        for mode in ('local','unoptimized'):
            try:
                v=p.run(ctx.clone()) if mode=='local' else eval_node(p.ast,Context(ctx.clone()))
                result[mode]=v.dump()
            except Exception as e: result[mode]=str(e)
        result['dialects']={}
        for dialect in ('sqlite','postgresql','mariadb'):
            d={}
            try:
                plan=Sql.plan_hybrid(p,dialect,bindings())
                d['plan']='pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
                f=Sql.translate_statement(p,dialect,bindings())
                d['sql']=f.as_statement()
                d['params_sql']=f.as_statement('params')
                d['params']=[v.dump() for v in f.bindings()]
                d['caveats']=f.caveats
                d['result']=db(dialect, fixture(rows,[row(1,'dimension')])+d['sql'])
            except Exception as e: d['error']=str(e)
            result['dialects'][dialect]=d
        results.append(result)
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
