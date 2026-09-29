#!/usr/bin/env python3
"""The hybrid-parity corpus (sql/oracle/hybrid.json) through the Python host, on a
real SQLite (sqlite3): the plan's prefix statement is EXECUTED and the answer of
execute_hybrid must be what run() answers, under the contract in that file's note.
tools/check-hybrid-parity.mjs is the JS twin, php/bin/sqlo `hybrid` the PHP one on
all four servers (T11).

    PYTHONPATH=$PWD/python python3 tools/check-hybrid-parity.py [--verbose]
"""
import copy
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import sel
from sel import SelError, Value
from sel.sql import Binding, Sql, SqlError

ORACLE = Path(__file__).resolve().parent.parent / 'sql' / 'oracle'
verbose = '--verbose' in sys.argv
spec = json.loads((ORACLE / 'hybrid.json').read_text(encoding='utf-8'))

db = sqlite3.connect(':memory:')
ddl = re.sub(r'(?m)^\s*--.*$', '', (ORACLE / spec['fixture']['sqlite']).read_text(encoding='utf-8'))
for stmt in ddl.split(';'):
    if stmt.strip():
        db.execute(stmt)

bindings = {}
for name, b in spec['bindings'].items():
    fields = {f: Binding.column(c['column'], c['table'], c['type']) for f, c in b['fields'].items()}
    bindings[name] = Binding.relation(b['from'], b['alias'], fields)

base = {}
for name, rel in spec['relations'].items():
    cur = db.execute(rel['query'])
    base[name] = [{c: str(v) for c, v in zip(rel['columns'], row)} for row in cur.fetchall()]


def runner(sql, bound):
    cur = db.execute(sql, [v.to_native() for v in bound])
    names = [d[0] for d in cur.description]
    return [{n: (None if v is None else str(v)) for n, v in zip(names, row)} for row in cur.fetchall()]


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


def outcome(fn):
    try:
        v = fn()
        return ('ok', v if isinstance(v, Value) else Value.from_native(v))
    except SelError as e:
        return ('err', f'{e.code}@{e.line}:{e.col}')
    except SqlError as e:
        return ('err', f'SQL:{e.code}')
    except Exception as e:                                        # noqa: BLE001
        return ('err', f'HOST:{type(e).__name__}: {str(e)[:160]}')


def column(native, field):
    return [norm(r[field]) if isinstance(r, dict) and field in r else '<absent>' for r in rows(native)]


bad = []
ok = skipped = 0
kinds = {'pure_sql': 0, 'hybrid': 0, 'pure_memory': 0}
for c in spec['programs']:
    for cs in spec['contexts']:
        if any(v not in cs['vars'] for v in c.get('requires', [])):
            skipped += 1
            continue
        name = f"{c['name']} [{cs['name']}]"

        def fresh():
            return {**copy.deepcopy(base), **cs['vars']}
        program = sel.compile(c['sel'])
        direct = outcome(lambda: program.run(fresh()))
        exp = c.get('expect', {})
        guard = None
        if 'error' in exp:
            if direct[0] != 'err' or not direct[1].startswith(exp['error'] + '@'):
                guard = f"SEL answered {direct[1] if direct[0] == 'err' else 'a value'} where the corpus says {exp['error']}"
        elif direct[0] != 'ok':
            guard = f'SEL raised {direct[1]}'
        else:
            for f, want in exp.get('fields', {}).items():
                got = column(direct[1].to_native(), f)
                if got != want:
                    guard = f'run() column {f} is {got}, the corpus says {want}'
            if 'keys' in exp and [str(k) for k in direct[1].keys()] != exp['keys']:
                guard = f"run() keys are {direct[1].keys()}, the corpus says {exp['keys']}"
        if guard:
            bad.append((name, '', 'CORPUS: ' + guard))
            continue
        try:
            plan = Sql.plan_hybrid(program, 'sqlite', bindings)
        except Exception as e:                                    # noqa: BLE001
            bad.append((name, '', f'plan_hybrid raised {type(e).__name__}: {e}'))
            continue
        kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
        kinds[kind] += 1
        statement = plan.sql_statement.as_statement() if plan.sql_statement else '-'
        caller = Value.from_native(fresh())
        before = caller.dump()
        ex = outcome(lambda: Sql.execute_hybrid(plan, runner, caller))
        after = caller.dump()
        why = None
        if direct[0] == 'err':
            if ex != direct:
                why = f"run() raises {direct[1]} and the executed {kind} plan gives {ex[1] if ex[0] == 'err' else 'a value'}"
        elif ex[0] == 'err':
            why = f'the executed {kind} plan raises {ex[1]} where run() answers'
        else:
            want, got = norm(direct[1].to_native()), norm(ex[1].to_native())
            if kind == 'pure_sql':
                want, got = rows(want), rows(got)
            else:
                want = {'keys': [str(k) for k in direct[1].keys()], 'rows': rows(want)}
                got = {'keys': [str(k) for k in ex[1].keys()], 'rows': rows(got)}
            if want != got:
                why = f'run()={json.dumps(want)}\n        plan={json.dumps(got)}'
        if not why and before != after:
            why = f"execute_hybrid changed the caller's context ({kind} plan)"
        if why:
            bad.append((name, f'[{kind}] {statement}', why))
            continue
        ok += 1
        if verbose:
            print(f'  ok       {name:<64} {kind}')

for c in spec.get('bounded', []):
    program = sel.compile(c['sel'])
    t0 = time.perf_counter()
    try:
        Sql.plan_hybrid(program, 'sqlite', bindings)
    except Exception:                                             # noqa: BLE001
        pass                                                      # a refusal is an answer
    ms = (time.perf_counter() - t0) * 1000
    if ms > c['max_ms']:
        bad.append((c['name'], '', f"planning took {ms:.0f} ms, the bound is {c['max_ms']}"))
    else:
        ok += 1

for name, statement, why in bad:
    print(f'DIFFER  {name}\n        {statement}\n        {why}')
print(f"python hybrid parity (sqlite): {ok} agree, {len(bad)} differ (plans: {kinds['pure_sql']} pure_sql, "
      f"{kinds['hybrid']} hybrid, {kinds['pure_memory']} pure_memory; {skipped} variants skipped)")
sys.exit(0 if not bad else 1)
