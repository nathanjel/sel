#!/usr/bin/env python3
"""The hybrid-parity corpus (sql/oracle/hybrid.json) for a host with no database of
its own: the host runs the planner and the evaluator, this script runs the
SQL on a real SQLite, and the answers are held to the contract in the corpus note
(docs/internals/sql-translation.md 12.1). The Python and JS hosts have their own
in-process twins (tools/check-hybrid-parity.py, .mjs) and PHP has `sqlo hybrid`;
this is the same check through a DRIVER -- a child process that speaks JSON lines
(see lisp/bin/hybrid-driver.lisp for the protocol). Lisp is the host held to it
this way; Go has its own driver protocol and script (tools/check-hybrid-parity-
go.py), and C++ and Rust have no hybrid driver yet.

    python3 tools/check-hybrid-parity-driver.py NAME DRIVER [ARGS...] [--verbose] [--application]
"""
import copy
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ORACLE = Path(__file__).resolve().parent.parent / 'sql' / 'oracle'
argv = [a for a in sys.argv[1:] if a not in ('--verbose', '--application')]
verbose = '--verbose' in sys.argv
# `--application` (or SEL_HYBRID_APPLICATION=1) adds the corpus's `application`
# section: programs that call the application functions POKE and HOSTF, which
# the host's driver registers. Opt-in until every driver registers them.
APPLICATION = '--application' in sys.argv or os.environ.get('SEL_HYBRID_APPLICATION') == '1'
NAME, CMD = argv[0], argv[1:]
spec = json.loads((ORACLE / 'hybrid.json').read_text(encoding='utf-8'))

db = sqlite3.connect(':memory:')
ddl = re.sub(r'(?m)^\s*--.*$', '', (ORACLE / spec['fixture']['sqlite']).read_text(encoding='utf-8'))
for stmt in ddl.split(';'):
    if stmt.strip():
        db.execute(stmt)

base = {}
for name, rel in spec['relations'].items():
    cur = db.execute(rel['query'])
    base[name] = [{c: str(v) for c, v in zip(rel['columns'], row)} for row in cur.fetchall()]

proc = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8')


def call(**req):
    proc.stdin.write(json.dumps(req) + '\n')
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError('the driver closed its output')
    return json.loads(line)


call(op='init', bindings=spec['bindings'])


def norm(x):
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, bool):
        return 'TRUE' if x else 'FALSE'
    if x is None:
        return 'NULL'
    return str(x)


def rows(native):
    return list(native.values()) if isinstance(native, dict) else list(native)


def column(native, field):
    return [norm(r[field]) if isinstance(r, dict) and field in r else '<absent>' for r in rows(native)]


def describe(ans):
    return f"{ans['code']}@{ans['line']}:{ans['col']}" if ans['status'] == 'err' else 'a value'


def fresh(cs):
    return {**copy.deepcopy(base), **cs['vars']}


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
        direct = call(op='run', sel=c['sel'], vars=fresh(cs))
        exp = c.get('expect', {})
        guard = None
        if 'error' in exp:
            if direct['status'] != 'err' or not direct['code'].startswith(exp['error']):
                guard = f"SEL answered {describe(direct)} where the corpus says {exp['error']}"
        elif direct['status'] != 'ok':
            guard = f'SEL raised {describe(direct)}'
        else:
            if 'value' in exp and norm(direct['native']) != exp['value']:
                guard = f"run() answers {json.dumps(norm(direct['native']))}, the corpus says {json.dumps(exp['value'])}"
            for f, want in exp.get('fields', {}).items():
                got = column(direct['native'], f)
                if got != want:
                    guard = f'run() column {f} is {got}, the corpus says {want}'
            if 'keys' in exp and [str(k) for k in direct['keys']] != exp['keys']:
                guard = f"run() keys are {direct['keys']}, the corpus says {exp['keys']}"
        if guard:
            bad.append((name, '', 'CORPUS: ' + guard))
            continue
        plan = call(op='plan', sel=c['sel'], dialect='sqlite')
        if 'kind' not in plan:
            bad.append((name, '', f"plan raised {plan.get('code')}"))
            continue
        kind = plan['kind']
        kinds[kind] += 1
        statement = plan['statement']
        db_rows = []
        if kind != 'pure_memory':
            ps = call(op='plan-statement', sel=c['sel'], dialect='sqlite')
            cur = db.execute(ps['sql'], ps['params'])
            names = [d[0] for d in cur.description]
            db_rows = [{n: (None if v is None else str(v)) for n, v in zip(names, row)}
                       for row in cur.fetchall()]
        ex = call(op='execute', sel=c['sel'], dialect='sqlite', vars=fresh(cs), rows=db_rows)
        why = None
        if direct['status'] == 'err':
            if not (ex['status'] == 'err' and (ex['code'], ex['line'], ex['col']) ==
                    (direct['code'], direct['line'], direct['col'])):
                why = f"run() raises {describe(direct)} and the executed {kind} plan gives {describe(ex)}"
        elif ex['status'] == 'err':
            why = f'the executed {kind} plan raises {describe(ex)} where run() answers'
        else:
            want, got = norm(direct['native']), norm(ex['native'])
            if kind == 'pure_sql':
                want, got = rows(want), rows(got)
            else:
                want = {'keys': [str(k) for k in direct['keys']], 'rows': rows(want)}
                got = {'keys': [str(k) for k in ex['keys']], 'rows': rows(got)}
            if want != got:
                why = f'run()={json.dumps(want)}\n        plan={json.dumps(got)}'
        if not why and ex['before'] != ex['after']:
            why = f"execute_hybrid changed the caller's context ({kind} plan)"
        if why:
            bad.append((name, f'[{kind}] {statement}', why))
            continue
        ok += 1
        if verbose:
            print(f'  ok       {name:<64} {kind}')

for c in spec.get('bounded', []):
    t0 = time.perf_counter()
    call(op='plan', sel=c['sel'], dialect='sqlite')
    ms = (time.perf_counter() - t0) * 1000
    if ms > c['max_ms']:
        bad.append((c['name'], '', f"planning took {ms:.0f} ms, the bound is {c['max_ms']}"))
    else:
        ok += 1

proc.stdin.close()
for name, statement, why in bad:
    print(f'DIFFER  {name}\n        {statement}\n        {why}')
print(f"{NAME} hybrid parity (sqlite, driver): {ok} agree, {len(bad)} differ (plans: {kinds['pure_sql']} pure_sql, "
      f"{kinds['hybrid']} hybrid, {kinds['pure_memory']} pure_memory; {skipped} variants skipped)")
sys.exit(0 if not bad else 1)
