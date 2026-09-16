"""Live F2/F3 identity regressions using latest.py's disposable DB adapters.

Checks exact field values and multiplicity, not unspecified SQL group order.
Never modifies historical audit evidence or pre-existing databases.
"""
import json
from pathlib import Path
import tempfile
import uuid

from latest import TOOLS, PG, MARIA, adapter, call, Value, compile, literal

DATABASE = 'sel_identity_' + uuid.uuid4().hex[:12]
HOSTS = ['python', 'js', 'cpp', 'php', 'lisp', 'python-wheel']


def database(requests):
    return json.loads(call([
        'docker', 'exec', '-i', '-e', 'AUDIT_PG=127.0.0.1',
        '-e', 'AUDIT_MARIA=127.0.0.1', '-e', 'AUDIT_DATABASE=' + DATABASE,
        TOOLS, 'php', 'tools/adversarial/db.php'], data=json.dumps(requests)))


def setup(dialect, collation):
    cat = ('TEXT COLLATE ' + collation if dialect == 'sqlite' else
           'VARCHAR(80) COLLATE utf8mb4_general_ci' if dialect == 'mariadb' else 'TEXT')
    # PostgreSQL's unconstrained NUMERIC preserves input scale. MariaDB has
    # fixed DECIMAL scale; compare with what the database actually stores.
    numeric = 'NUMERIC' if dialect == 'postgresql' else 'DECIMAL(12,2)' if dialect == 'mariadb' else 'TEXT'
    return (f'DROP TABLE IF EXISTS r; CREATE TABLE r(id INTEGER PRIMARY KEY, cat {cat}, v {numeric}, fk INTEGER);'
            "INSERT INTO r VALUES (1,'a','1',1),(2,'A','1.0',1),(3,'a ','1.00',2),"
            "(4,'a','2',2),(5,'A','2.0',3);")


def check(source, collation):
    translations = {host: adapter(host, source) for host in HOSTS}
    # Obtain each backend's stored representation once; equality is to the
    # same rows interpreted by SEL, not to pre-insert decimal spellings.
    dialects = ['sqlite', 'postgresql', 'mariadb']
    inputs = database([dict(dialect=d, fixture=setup(d, collation), sql='SELECT * FROM r ORDER BY id', params=[])
                       for d in dialects])
    expected = {}
    for d, result in zip(dialects, inputs):
        assert 'error' not in result, result
        expected[d] = compile(source).run({'R': result['assoc']})

    requests, keys = [], []
    initialized = set()
    for host, records in translations.items():
        assert len(records) == 6
        for record in records:
            assert record['plan'] == 'pure_sql', (host, source, record)
            for mode in ('inline', 'params'):
                d = record['d']
                requests.append(dict(dialect=d, fixture=setup(d, collation) if d not in initialized else '',
                                     sql=record['sql'] if mode == 'inline' else record['params_sql'],
                                     params=[] if mode == 'inline' else record['params']))
                initialized.add(d)
                keys.append(dict(host=host, d=d, strict=record['strict'], mode=mode))
    results = [dict(**key, **answer) for key, answer in zip(keys, database(requests))]
    assert len(results) == 72
    for result in results:
        assert 'error' not in result, (source, result)
        actual = Value.from_native(result['assoc'])
        assert sorted(v.dump() for v in actual.values()) == sorted(
            v.dump() for v in expected[result['d']].values()), (source, result)

    for host, records in translations.items():
        replay = []
        for record in records:
            result = next(r for r in results if r['host'] == host and r['d'] == record['d']
                          and r['strict'] == record['strict'] and r['mode'] == 'params')
            # Group ordering is not a SQL guarantee. Arrange the already
            # verified records in the evaluator's order for adapter replay.
            positions = {v.dump(): i for i, v in enumerate(expected[record['d']].values())}
            rows = sorted(result['assoc'], key=lambda row: positions[Value.from_native(row).dump()])
            replay.append(literal({'context': {}, 'rows': rows, 'error': ''}))
        for result in adapter(host, source, '\n'.join(replay) + '\n'):
            assert result['hybrid_value'] == expected[result['d']].dump(), (host, source, result)
    print(f'{collation}: {source}: 72 live SQL + 36 exact replay checks', flush=True)
    return dict(source=source, sqlite_collation=collation, results=results)


def main():
    created = []
    output = Path(tempfile.mkdtemp(prefix='sel-identity-evidence.'))
    try:
        call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'CREATE DATABASE ' + DATABASE])
        created.append('pg')
        call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'CREATE DATABASE ' + DATABASE])
        created.append('maria')
        sources = [
            'R .> BUCKET(_["cat"], RECORD("key", _K, "n", COUNT(_)))',
            'R .> MAP(RECORD("key", _["cat"])) .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))',
            'R .> SELECT_COLS("cat") .> TAKE(5) .> BUCKET(_["cat"], RECORD("key", _K, "n", COUNT(_)))',
            'R .> MAP(RECORD("cat", _["cat"])) .> DISTINCT()',
            'R .> FILTER(_["cat"] $== "a") .> MAP(RECORD("cat", _["cat"]))',
            'R .> MAP(RECORD("key", _["v"])) .> BUCKET(_["key"], RECORD("key", _K, "n", COUNT(_)))',
            'R .> SELECT_COLS("v") .> TAKE(5) .> BUCKET(_["v"], RECORD("key", _K, "n", COUNT(_)))',
        ]
        evidence = [check(source, collation) for collation in ['NOCASE', 'RTRIM'] for source in sources]
        (output / 'results.json').write_text(json.dumps(evidence, indent=2))
        print('Evidence:', output, flush=True)
    finally:
        if 'maria' in created:
            call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'DROP DATABASE ' + DATABASE])
        if 'pg' in created:
            call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE ' + DATABASE])


if __name__ == '__main__':
    main()
