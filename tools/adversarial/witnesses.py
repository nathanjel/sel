"""Recheck original F1-F5 witnesses against desired outcomes, not old bugs."""
import json
from pathlib import Path
import tempfile
import uuid

import identity as live
from latest import TOOLS, PG, MARIA, adapter, call, Value, compile, literal
from probe import CASES, fixture, row

live.DATABASE = 'sel_witness_' + uuid.uuid4().hex[:12]


def check(index):
    name, source, rows = CASES[index]
    context = {'R': rows, 'S': [row(1, 'dimension')]}
    expected = compile(source).run(context)
    translations = {host: adapter(host, source) for host in live.HOSTS}
    requests, keys = [], []
    for host, records in translations.items():
        assert len(records) == 6
        for record in records:
            assert 'plan_error' not in record, record
            for mode in ('inline', 'params', 'prefix', 'prefix_params'):
                sql = record.get({'inline': 'sql', 'params': 'params_sql',
                                  'prefix': 'prefix', 'prefix_params': 'prefix_params_sql'}[mode])
                if not sql:
                    continue
                requests.append(dict(dialect=record['d'], fixture=fixture(rows, context['S']), sql=sql,
                                     params=record.get('params', []) if mode == 'params' else
                                     record.get('prefix_params', []) if mode == 'prefix_params' else []))
                keys.append(dict(host=host, d=record['d'], strict=record['strict'], mode=mode))
    results = [dict(**key, **answer) for key, answer in zip(keys, live.database(requests))] if requests else []
    assert len(results) == len(requests)
    for result in results:
        assert 'error' not in result, (name, result)
        if result['mode'] in ('inline', 'params'):
            assert sorted(v.dump() for v in Value.from_native(result['assoc']).values()) == sorted(
                v.dump() for v in expected.values()), (name, result, expected.dump())

    for host, records in translations.items():
        replay = []
        for record in records:
            result = next((r for r in results if r['host'] == host and r['d'] == record['d']
                           and r['strict'] == record['strict'] and r['mode'] == 'prefix_params'), None)
            returned = result['assoc'] if result else []
            if record['plan'] == 'pure_sql' and index in (0, 5):
                # These witnesses assert group membership, not unspecified SQL order.
                positions = {v.dump(): i for i, v in enumerate(expected.values())}
                returned = sorted(returned, key=lambda r: positions[Value.from_native(r).dump()])
            replay.append(literal({'context': context, 'rows': returned, 'error': ''}))
        for result in adapter(host, source, '\n'.join(replay) + '\n'):
            assert result.get('hybrid_value') == expected.dump(), (name, host, result, expected.dump())
    print(f'{name}: {len(results)} live SQL + 36 exact hybrid checks', flush=True)
    return dict(index=index, name=name, translations=translations, results=results)


def main():
    created = []
    output = Path(tempfile.mkdtemp(prefix='sel-witness-evidence.'))
    try:
        call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'CREATE DATABASE ' + live.DATABASE])
        created.append('pg')
        call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'CREATE DATABASE ' + live.DATABASE])
        created.append('maria')
        # Arithmetic/validation contract witnesses are deliberately separate
        # (C1); F6 has the selected-member correctness/scale harness latest.py.
        evidence = [check(i) for i in (0, 1, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 17)]
        (output / 'results.json').write_text(json.dumps(evidence, indent=2))
        print('Evidence:', output, flush=True)
    finally:
        if 'maria' in created:
            call(['docker', 'exec', MARIA, 'mariadb', '-uroot', '-psel_audit', '-e', 'DROP DATABASE ' + live.DATABASE])
        if 'pg' in created:
            call(['docker', 'exec', PG, 'psql', '-U', 'postgres', '-c', 'DROP DATABASE ' + live.DATABASE])


if __name__ == '__main__':
    main()
