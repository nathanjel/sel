#!/usr/bin/env python3
"""The mutation runner. See tools/mutate-sql.sh for what it is for."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(ROOT, 'sql', 'mutations.json')

# In order, cheapest first. The first one that fails is the one reported: it is
# the answer to "what would have told you", and the cheapest such answer is the
# useful one.
CHECKS = [
    ('sqlt',              ['php', 'php/bin/sqlt']),
    ('sqldoc',            ['php', 'php/bin/sqldoc']),
    ('oracle coverage',   ['php', 'php/bin/sqlo', 'coverage']),
    ('oracle expressions',['php', 'php/bin/sqlo', 'expressions']),
    ('oracle rows',       ['php', 'php/bin/sqlo', 'rows']),
]
NEEDS_DB = {'oracle coverage', 'oracle expressions', 'oracle rows'}


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def main(argv):
    table = json.load(open(TABLE, encoding='utf-8'))
    filters = argv[1:]
    have_db = bool(os.environ.get('SEL_SQL_MARIADB_DSN'))

    caught, holes, errors, skipped = 0, [], [], 0
    work = tempfile.mkdtemp(prefix='mutate-sql.')
    try:
        for m in table['mutations']:
            name = m['name']
            if filters and not any(f in name for f in filters):
                continue

            tree = os.path.join(work, name)
            # The whole tree, minus git and build output: the map generator
            # resolves its paths from its own location, so running the ORIGINAL
            # generator would regenerate the ORIGINAL map and the copy would
            # never see the mutation. That is not hypothetical -- it is what the
            # first version of this script did, and it scored three mutations as
            # holes that the suite catches immediately.
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
                '.git', 'node_modules', 'build', 'dist', '.venv*', '__pycache__'))

            path = os.path.join(tree, m['file'])
            text = open(path, encoding='utf-8').read()
            n = text.count(m['from'])
            if n != 1:
                errors.append(f'{name}: pattern occurs {n} times in {m["file"]}, '
                              'expected exactly 1 — the mutation is stale')
                continue
            open(path, 'w', encoding='utf-8').write(text.replace(m['from'], m['to'], 1))

            # Proof it landed. A silent no-op would be scored by whatever the
            # checks say about unmutated code, which is this tool's own failure
            # mode and the reason it exists.
            if open(path, encoding='utf-8').read() == text:
                errors.append(f'{name}: {m["file"]} is unchanged after mutation')
                continue

            if m['file'].startswith('sql/dialects/'):
                if run(['node', 'tools/gen-sql-map.mjs'], tree) != 0:
                    errors.append(f'{name}: the mutated map would not regenerate')
                    continue

            by = None
            for label, cmd in CHECKS:
                if label in NEEDS_DB and not have_db:
                    continue
                if run(cmd, tree) != 0:
                    by = label
                    break

            if by:
                print(f'caught  {name:<34} by {by}')
                caught += 1
            elif not have_db:
                print(f'skipped {name:<34} (survived sqlt and sqldoc; no database to ask)')
                skipped += 1
            else:
                print(f'HOLE    {name:<34} survived every check')
                holes.append(name)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    print(f'{caught} caught, {len(holes)} survived, {skipped} skipped, '
          f'{len(errors)} suite errors')
    for e in errors:
        print('SUITE ERROR ' + e)
    for h in holes:
        print(f'HOLE {h} — nothing here would tell you this had happened')
    return 0 if not holes and not errors else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
