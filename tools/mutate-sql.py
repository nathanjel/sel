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
# All three generated maps, not one. A mutation to sql/dialects/*.json is scored
# a no-op unless it changes what the hosts actually read -- and reading only
# PHP's copy assumed the three emitters cannot differ, which is the assumption
# the emitters exist to be checked against. Any one of them changing is enough
# to prove the mutation landed.
GENERATED = (
    os.path.join('php', 'src', 'Sql', 'MapData.php'),
    os.path.join('python', 'sel', 'sql', '_map.py'),
    os.path.join('js', 'src', 'sql', '_map.mjs'),
)

# In order, cheapest first. The first one that fails is the one reported: it is
# the answer to "what would have told you", and the cheapest such answer is the
# useful one.
CHECKS = [
    ('sqlt',              ['php', 'php/bin/sqlt']),
    # The second host runs the same case files, so a mutation in python/sel/sql
    # is now testable at all. Before this the tool could only mutate PHP and
    # JSON, which meant every Python-side guard was believed rather than
    # checked -- and the cross-host review's whole finding was that the two
    # hosts diverge exactly where nothing was watching. A check that cannot
    # reach half the code under test is docs/history/SQL-TESTING.md's Class G.
    ('sqlt (python)',     ['python3', 'python/bin/sqlt']),
    # And the third host, for the same reason. js/src/sql is 3,300 lines whose
    # host-shaped decisions -- Map where the others use a native ordered map,
    # Object.hasOwn where they use plain membership -- are exactly the kind the
    # other two hosts cannot fail on, so nothing else here can measure them.
    ('sqlt (js)',         ['node', 'js/bin/sqlt.mjs']),
    # And the fifth. Grouped with the other sqlt runners rather than last: a
    # quickload-and-run is cheap next to a C++ build, and Lisp-local mutations
    # are then caught before anything expensive is attempted.
    ('sqlt (lisp)',       ['lisp/bin/sqlt']),
    # The same corpus with the printer set hostile. *PRINT-BASE* belongs to the
    # calling application and this layer may not read it, but a case file has no
    # way to say so -- every runner above uses the standard printer. This costs
    # one more quickload and covers every PRINC-TO-STRING the translator could
    # grow, not just the ones a bespoke test happened to name.
    ('sqlt (lisp, base 16)', ['lisp/bin/sqlt', '--print-base', '16']),
    # The Lisp unit tests. Two of this host's failure modes -- a dialect that is
    # not a string, and a caller's rebound *PRINT-BASE* -- arrive from host code
    # rather than from a case file, so no .sqlt case can state them and none of
    # the runners above can catch a mutation of either. Cheap: the same
    # quickload the line above already pays for, against a suite that runs in a
    # second.
    ('lisp unit',         ['lisp/bin/test']),
    # Cheap, and the only check that asks whether the registration API can
    # express the shipped map. Nothing else covers php/bin/sqlreplay or the
    # defineDialect rules it exercises.
    ('sql map replay',    ['php', 'php/bin/sqlreplay']),
    ('sqldoc',            ['php', 'php/bin/sqldoc']),
    ('oracle coverage',   ['php', 'php/bin/sqlo', 'coverage']),
    ('oracle expressions',['php', 'php/bin/sqlo', 'expressions']),
    # Last, deliberately. The loop breaks on the first check that catches, so a
    # mutation any dynamic host sees never pays for a C++ build; this runs only
    # for the ones nothing else caught, which is exactly the C++-local
    # mutations below. Its own build has to happen in the WORK TREE -- build/ is
    # not copied -- or it would grade the unmutated binary, which is this tool's
    # signature failure mode.
    ('sqlt (cpp)',        ['sh', '-c', 'make -s -C cpp build/sqlt && cpp/build/sqlt']),
    ('oracle rows',       ['php', 'php/bin/sqlo', 'rows']),
]
NEEDS_DB = {'oracle coverage', 'oracle expressions', 'oracle rows'}


def run(cmd, cwd):
    # PYTHONPATH points at the mutated copy, not at the source tree, or the
    # Python runner would import the unmutated package and report every
    # mutation caught for the wrong reason -- the exact failure this tool
    # exists to catch, committed by the tool itself.
    env = dict(os.environ, PYTHONPATH=os.path.join(cwd, 'python'))
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def main(argv):
    table = json.load(open(TABLE, encoding='utf-8'))
    filters = argv[1:]
    # Per dialect, not one MariaDB-shaped proxy for all four. With
    # SEL_SQL_SQLITE_DSN unset and MariaDB's set, a mutation the SQLite oracle
    # is the only witness for was reported as a HOLE rather than as skipped --
    # inverting the guard's own promise, and the worse of the two failure modes.
    DIALECTS = ('mariadb', 'mysql', 'postgresql', 'sqlite')
    have_db = {d: bool(os.environ.get(f'SEL_SQL_{d.upper()}_DSN')) for d in DIALECTS}
    any_db = any(have_db.values())
    missing = [d for d in DIALECTS if not have_db[d]]

    # Every mutation is trivially "caught" if a check is already failing on the
    # unmutated tree, and the report reads exactly like a healthy one. That is
    # not hypothetical: sqldoc went red when a skeleton changed and the design
    # document still quoted the old rendering, and the next four mutation runs
    # all reported `caught by sqldoc` for mutations sqldoc cannot see.
    for label, cmd in CHECKS:
        if label in NEEDS_DB and not any_db:
            continue
        if run(cmd, ROOT) != 0:
            print(f'BASELINE {label} fails on the unmutated tree; every mutation '
                  'would be reported as caught by it. Fix that first.')
            return 2

    caught, holes, errors, skipped = 0, [], [], 0
    # Under TMPDIR when the environment sets one, because the default is /tmp and
    # /tmp is a tmpfs on a lot of Linux installs -- which makes every copy below
    # resident memory rather than disk. One tree is only a few megabytes, so this
    # is tidiness rather than a crisis, but a check that quietly grows the
    # machine's memory with the size of its own corpus is the wrong shape.
    work = tempfile.mkdtemp(prefix='mutate-sql.', dir=os.environ.get('TMPDIR') or None)
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
                shutil.rmtree(tree, ignore_errors=True)
                continue
            open(path, 'w', encoding='utf-8').write(text.replace(m['from'], m['to'], 1))

            # Proof it landed. A silent no-op would be scored by whatever the
            # checks say about unmutated code, which is this tool's own failure
            # mode and the reason it exists.
            if open(path, encoding='utf-8').read() == text:
                errors.append(f'{name}: {m["file"]} is unchanged after mutation')
                shutil.rmtree(tree, ignore_errors=True)
                continue

            if m['file'].startswith('sql/dialects/'):
                before = [open(os.path.join(tree, g), encoding='utf-8').read()
                          for g in GENERATED]
                if run(['node', 'tools/gen-sql-map.mjs'], tree) != 0:
                    errors.append(f'{name}: the mutated map would not regenerate')
                    continue
                # Landing in the source is not landing in the artifact. The
                # generator drops `notes` entirely, so a mutation to one is a
                # provable no-op that was scored as a HOLE -- this tool's own
                # failure mode, reported with confidence. 22 of the mutations
                # target sql/dialects/*.json and nothing checked that any of
                # them changed the map the checks actually read. Verified by
                # adding a key the generator ignores and watching this fire.
                after = [open(os.path.join(tree, g), encoding='utf-8').read()
                         for g in GENERATED]
                if after == before:
                    errors.append(f'{name}: {m["file"]} changed but the generated map '
                                  'did not — the mutation is a no-op')
                    continue

            by = None
            for label, cmd in CHECKS:
                if label in NEEDS_DB and not any_db:
                    continue
                if run(cmd, tree) != 0:
                    by = label
                    break

            # Freed here rather than in the finally below: every tree used to
            # stay alive for the whole run, so the space held grew with the
            # corpus for no reason -- nothing reads a tree once its mutation has
            # been graded.
            shutil.rmtree(tree, ignore_errors=True)

            if by:
                print(f'caught  {name:<34} by {by}')
                caught += 1
            elif missing:
                print(f'skipped {name:<34} (survived; no DSN for ' + ', '.join(missing) + ')')
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
