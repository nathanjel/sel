#!/usr/bin/env python3
"""The hybrid-parity corpus (sql/oracle/hybrid.json) through the GO host.

Go has no SQLite driver in its standard library, so this script owns the
database and go/build/hybridparity owns everything else: for each program and
context it asks Go for run()'s answer, asks it to PLAN, executes the plan's
prefix statement itself on a real SQLite (sqlite3), and hands the rows back for
execute_hybrid's continuation. The comparison is tools/check-hybrid-parity.py's:
the value, the error, the caller's context and, for a pure_sql plan, the rows in
order and not their keys.

    python3 tools/check-hybrid-parity-go.py [--verbose] [--application]   # after: make -C go
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORACLE = ROOT / 'sql' / 'oracle'
verbose = '--verbose' in sys.argv
# `--application` (or SEL_HYBRID_APPLICATION=1) adds the corpus's `application`
# section: programs that call the application functions POKE and HOSTF, which
# the host's driver registers. Opt-in until every driver registers them.
APPLICATION = '--application' in sys.argv or os.environ.get('SEL_HYBRID_APPLICATION') == '1'
spec = json.loads((ORACLE / 'hybrid.json').read_text(encoding='utf-8'))
binary = ROOT / 'go' / 'build' / 'hybridparity'
if not binary.exists():
    print('go/build/hybridparity is missing: run make -C go', file=sys.stderr)
    sys.exit(2)

proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def call(req):
    proc.stdin.write(json.dumps(req) + '\n')
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


db = sqlite3.connect(':memory:')
ddl = re.sub(r'(?m)^\s*--.*$', '', (ORACLE / spec['fixture']['sqlite']).read_text(encoding='utf-8'))
for stmt in ddl.split(';'):
    if stmt.strip():
        db.execute(stmt)

call({'op': 'init', 'bindings': spec['bindings']})

base = {}
for name, rel in spec['relations'].items():
    cur = db.execute(rel['query'])
    base[name] = [{c: str(v) for c, v in zip(rel['columns'], row)} for row in cur.fetchall()]


def rows_of(sql, params):
    cur = db.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [{n: (None if v is None else str(v)) for n, v in zip(names, row)} for row in cur.fetchall()]


def data(cs):
    return {**base, **cs['vars']}


def fmt(o):
    return f"{o['code']}@{o.get('line', 0)}:{o.get('col', 0)}" if o['status'] == 'err' else 'a value'


bad = []
ok = skipped = 0
kinds = {'pure_sql': 0, 'hybrid': 0, 'pure_memory': 0}
work = [(c, cs) for c in spec['programs'] for cs in spec['contexts']]
if APPLICATION and 'application' in spec:
    work += [(c, {'name': 'application', 'vars': spec['application']['vars']})
             for c in spec['application']['programs']]
for c, cs in work:
    if True:
        if any(v not in cs['vars'] for v in c.get('requires', [])):
            skipped += 1
            continue
        name = f"{c['name']} [{cs['name']}]"
        direct = call({'op': 'direct', 'sel': c['sel'], 'data': data(cs)})
        exp = c.get('expect', {})
        guard = None
        if 'error' in exp:
            if direct['status'] != 'err' or direct['code'] != exp['error']:
                guard = f"SEL answered {fmt(direct)} where the corpus says {exp['error']}"
        elif direct['status'] != 'ok':
            guard = f'SEL raised {fmt(direct)}'
        else:
            if 'value' in exp:
                # The Go driver answers a value as its keys and its children.
                got = (dict(zip(direct['keys'], direct['rows'])) if isinstance(exp['value'], dict)
                       else direct['rows'])
                if got != exp['value']:
                    guard = f"run() answers {json.dumps(got)}, the corpus says {json.dumps(exp['value'])}"
            for f, want in exp.get('fields', {}).items():
                got = [r.get(f, '<absent>') if isinstance(r, dict) else '<absent>' for r in direct['rows']]
                if got != want:
                    guard = f'run() column {f} is {got}, the corpus says {want}'
            if 'keys' in exp and direct['keys'] != exp['keys']:
                guard = f"run() keys are {direct['keys']}, the corpus says {exp['keys']}"
        if guard:
            bad.append((name, '', 'CORPUS: ' + guard))
            continue
        plan = call({'op': 'plan', 'sel': c['sel']})
        if plan['status'] != 'ok':
            bad.append((name, '', f"plan_hybrid raised {plan['code']}"))
            continue
        kind = plan['kind']
        kinds[kind] += 1
        statement = plan.get('sql', '-')
        prefix_rows = rows_of(plan['sql'], (plan.get('params') or [])) if 'sql' in plan else []
        ex = call({'op': 'exec', 'sel': c['sel'], 'data': data(cs), 'rows': prefix_rows})
        why = None
        if direct['status'] == 'err':
            if ex['status'] != 'err' or (ex['code'], ex.get('line'), ex.get('col')) != (direct['code'], direct.get('line'), direct.get('col')):
                why = f"run() raises {fmt(direct)} and the executed {kind} plan gives {fmt(ex)}"
        elif ex['status'] == 'err':
            why = f'the executed {kind} plan raises {fmt(ex)} where run() answers'
        else:
            want, got = direct['rows'], ex['rows']
            if kind != 'pure_sql':
                want, got = {'keys': direct['keys'], 'rows': want}, {'keys': ex['keys'], 'rows': got}
            if want != got:
                why = f'run()={json.dumps(want)}\n        plan={json.dumps(got)}'
        if not why and ex.get('mutated'):
            why = f"execute_hybrid changed the caller's context ({kind} plan)"
        if why:
            bad.append((name, f'[{kind}] {statement}', why))
            continue
        ok += 1
        if verbose:
            print(f'  ok       {name:<64} {kind}')

for c in spec.get('bounded', []):
    t0 = time.perf_counter()
    call({'op': 'plan', 'sel': c['sel']})
    ms = (time.perf_counter() - t0) * 1000
    if ms > c['max_ms']:
        bad.append((c['name'], '', f"planning took {ms:.0f} ms, the bound is {c['max_ms']}"))
    else:
        ok += 1

proc.stdin.close()
for name, statement, why in bad:
    print(f'DIFFER  {name}\n        {statement}\n        {why}')
print(f"go hybrid parity (sqlite): {ok} agree, {len(bad)} differ (plans: {kinds['pure_sql']} pure_sql, "
      f"{kinds['hybrid']} hybrid, {kinds['pure_memory']} pure_memory; {skipped} variants skipped)")
sys.exit(0 if not bad else 1)
