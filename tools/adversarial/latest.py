"""F6 live regression: five hosts + fresh wheel, three disposable DB platforms.

Uses existing disposable containers; creates and drops only uniquely named
databases of its own. See README.md for container/build prerequisites. Historical
audit JSON is never overwritten. Successful executions and replays are asserted
against current local SEL, not against old bug expectations.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python'))
from sel import Value, compile
from run import literal

TOOLS = os.getenv('AUDIT_TOOLS', 'sel-fixes-tools-0916')
PG = os.getenv('AUDIT_PG_CONTAINER', 'sel-fixes-pg-0916')
MARIA = os.getenv('AUDIT_MARIA_CONTAINER', 'sel-fixes-maria-0916')
WHEEL = os.getenv('AUDIT_WHEEL', '/tmp/sel-f6-wheel')
DATABASE = 'sel_latest_' + uuid.uuid4().hex[:12]


def call(cmd, *, env=None, data=None):
    p = subprocess.run(cmd, cwd=ROOT, env=env, input=data, capture_output=True, text=True, timeout=240)
    if p.returncode:
        raise RuntimeError(f'{cmd[:5]}: {p.stderr[-5000:]} {p.stdout[-1000:]}')
    return p.stdout


def adapter(host, source, replay=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'python'), AUDIT_QUERY=source, AUDIT_UNIQUE_KEY='1')
    if replay is not None:
        env['AUDIT_REPLAY_DATA'] = replay
    commands = {'python': ['python3', 'tools/adversarial/translate.py'],
                'js': ['node', 'tools/adversarial/translate.mjs'],
                'cpp': ['cpp/build/adversarial'],
                'php': ['php', 'tools/adversarial/translate.php'],
                'lisp': ['sbcl', '--noinform', '--disable-debugger', '--script', 'tools/adversarial/translate.lisp'],
                'python-wheel': ['python3', 'tools/adversarial/translate.py']}
    cmd = commands[host]
    if host in ('php', 'lisp', 'python-wheel'):
        flags = ['-e', 'AUDIT_QUERY', '-e', 'AUDIT_UNIQUE_KEY']
        if replay is not None:
            flags += ['-e', 'AUDIT_REPLAY_DATA']
        if host == 'python-wheel':
            flags += ['-e', 'PYTHONPATH=' + WHEEL]
        cmd = ['docker', 'exec', *flags, TOOLS, *cmd]
    return [json.loads(line) for line in call(cmd, env=env).split('\n') if line]


def fixture(rows):
    sql = ['DROP TABLE IF EXISTS r', 'CREATE TABLE r(id INTEGER PRIMARY KEY,cat VARCHAR(80),v INTEGER,fk INTEGER,payload VARCHAR(80))']
    for start in range(0, len(rows), 1000):
        values = ["(" + ','.join("'" + r[k].replace("'", "''") + "'" for k in ('id', 'cat', 'v', 'fk', 'payload')) + ")" for r in rows[start:start + 1000]]
        sql.append('INSERT INTO r VALUES ' + ','.join(values))
    return ';'.join(sql) + ';'


def check(name, source, rows):
    hosts = ['python', 'js', 'cpp', 'php', 'lisp', 'python-wheel']
    expected = compile(source).run(Value.from_native({'R': rows}))
    translations = {h: adapter(h, source) for h in hosts}
    requests, keys, initialized = [], [], set()
    setup = fixture(rows)
    for host, records in translations.items():
        assert len(records) == 6
        for r in records:
            assert r['plan'] == 'hybrid', (name, host, r)
            assert r['prefix'].startswith('WITH ') and 'MAX(' in r['prefix'], (name, host, r)
            for mode in ('inline', 'params'):
                first = r['d'] not in initialized
                initialized.add(r['d'])
                requests.append(dict(dialect=r['d'], fixture=setup if first else '',
                                     sql=r['prefix'] if mode == 'inline' else r['prefix_params_sql'],
                                     params=[] if mode == 'inline' else r['prefix_params']))
                keys.append(dict(host=host, d=r['d'], strict=r['strict'], mode=mode))
    answers = json.loads(call(['docker', 'exec', '-i', '-e', 'AUDIT_PG=127.0.0.1', '-e', 'AUDIT_MARIA=127.0.0.1',
                               '-e', 'AUDIT_DATABASE=' + DATABASE, TOOLS, 'php', 'tools/adversarial/db.php'], data=json.dumps(requests)))
    results = [dict(**key, **answer) for key, answer in zip(keys, answers)]
    assert len(results) == 72
    for r in results:
        assert 'error' not in r, (name, r)
        assert r['columns'] == ['id', 'cat', 'v', 'fk', 'payload'], (name, r)
        assert len(r['rows']) == expected.size(), (name, r)
    for host in hosts:
        replay = []
        for r in translations[host]:
            answer = next(x for x in results if x['host'] == host and x['d'] == r['d'] and x['strict'] == r['strict'] and x['mode'] == 'params')
            inline = next(x for x in results if x['host'] == host and x['d'] == r['d'] and x['strict'] == r['strict'] and x['mode'] == 'inline')
            assert inline['rows'] == answer['rows']
            # Only selected rows travel to the continuation, not a copy of the
            # full history concealed in its context.
            replay.append(literal({'context': {}, 'rows': answer['assoc'], 'error': ''}))
        for r in adapter(host, source, '\n'.join(replay) + '\n'):
            assert r.get('hybrid_value') == expected.dump(), (name, host, r, expected.dump())
    print(f'{name}: {len(rows)} input -> {expected.size()} selected rows; 72 live SQL + 36 exact hybrid checks', flush=True)
    return {'name': name, 'input_rows': len(rows), 'selected_rows': expected.size(), 'results': results}


def main():
    created = []
    output = Path(tempfile.mkdtemp(prefix='sel-latest-evidence.'))
    try:
        call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'CREATE DATABASE ' + DATABASE])
        created.append('pg')
        call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'CREATE DATABASE ' + DATABASE])
        created.append('maria')
        projection = 'RECORD("entity", _K, "latest", TOP_BY(_, _["id"], "DESC", 1))'
        source = 'R .> SORT_BY(_["id"]) .> BUCKET(_["fk"]) .> MAP(' + projection + ')'
        small = [dict(id=str(i), cat=cat, v=str(i * 10), fk=str(fk), payload='payload ' + str(i))
                 for i, cat, fk in [(1, 'a', 2), (2, 'A', 1), (3, 'a ', 1), (4, 'a', 2), (5, 'A', 3)]]
        evidence = [check('normalized-member', source, small),
                    check('empty', source, []),
                    check('text-identity', source.replace('BUCKET(_["fk"])', 'BUCKET(_["cat"])'), small),
                    check('filtered-projected', 'R .> SORT_BY(_["id"]) .> FILTER(_["cat"] $== "a") .> BUCKET(_["fk"], ' + projection + ')', small)]
        for count in (1000, 100000):
            rows = [dict(id=str(i), cat='weight', v=str(i % 100), fk=str((i - 1) % 100), payload='revision ' + str(i)) for i in range(1, count + 1)]
            evidence.append(check('eav-' + str(count), source, rows))
        (output / 'results.json').write_text(json.dumps(evidence, indent=2))
        print('Evidence:', output, flush=True)
    finally:
        if 'maria' in created:
            call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'DROP DATABASE ' + DATABASE])
        if 'pg' in created:
            call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE ' + DATABASE])


if __name__ == '__main__':
    main()
