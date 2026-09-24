#!/usr/bin/env python3
"""The mutation runner. See tools/mutate-sql.sh for what it is for."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

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
    # And the other three dynamic hosts' replays, for the reason the sqlt
    # runners above are all here: one host's registration API is not the others.
    # Until these were listed, PHP was the only host whose defineDialect and
    # define() were mutation-tested at all -- a mutation to js/src/sql/map.mjs's
    # registration path survived every check and was reported `skipped`, which
    # reads like "needs a database" and meant "nothing here looks". The C++
    # replay is not here: it needs the build, which is the expensive check at
    # the bottom, and a C++-local registration mutation reaches it there.
    ('sql map replay (js)',     ['node', 'js/bin/sqlreplay.mjs']),
    ('sql map replay (python)', ['python3', 'python/bin/sqlreplay']),
    ('sql map replay (lisp)',   ['lisp/bin/sqlreplay']),
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
    # Same build, so the marginal cost is one link, and it is the only thing
    # that watches the C++ registration path.
    ('sql map replay (cpp)',
     ['sh', '-c', 'make -s -C cpp build/sqlreplay && cpp/build/sqlreplay']),
    ('oracle rows',       ['php', 'php/bin/sqlo', 'rows']),
    # The SQL API contract, pinned. A mutation that drops a public flag --
    # Fragment.canonical, lost by every host's top-level translate at once
    # (SEL-0058) -- changes no emitted SQL, so nothing above can see it.
    # C++ builds its probe first -- and conformance, which is what the roster
    # checks C++ is present by: the mutated copy has no build output, and a
    # host the check cannot run is a host it silently leaves out.
    ('sql api',           ['sh', '-c', 'make -s -C cpp build/conformance build/sqlapi && tools/check-sqlapi.sh']),
]
NEEDS_DB = {'oracle coverage', 'oracle expressions', 'oracle rows'}

# Keep the mutation runner aligned with tools/impls.sh.  The main check driver
# has always supported SEL_IMPLS, but this script historically ran every host's
# SQL checks regardless of that selection and could therefore start a C++ build
# during a JS-only validation.  Shared dialect-map mutations are relevant to
# every selected SQL host; host-local mutations are only relevant when that host
# is in scope.
CHECK_IMPLS = {
    'sqlt': {'php'},
    'sqlt (python)': {'python'},
    'sqlt (js)': {'js'},
    'sqlt (lisp)': {'lisp'},
    'sqlt (lisp, base 16)': {'lisp'},
    'lisp unit': {'lisp'},
    'sql map replay': {'php'},
    'sql map replay (js)': {'js'},
    'sql map replay (python)': {'python'},
    'sql map replay (lisp)': {'lisp'},
    'sqldoc': {'php'},
    'oracle coverage': {'php'},
    'oracle expressions': {'php'},
    'sqlt (cpp)': {'cpp'},
    'sql map replay (cpp)': {'cpp'},
    'oracle rows': {'php'},
    'sql api': {'js', 'php', 'python', 'cpp', 'lisp'},
}

HOST_ROOTS = {
    'php': 'php/',
    'python': 'python/',
    'js': 'js/',
    'lisp': 'lisp/',
    'cpp': 'cpp/',
}


def selected_impls():
    raw = os.environ.get('SEL_IMPLS')
    if raw is None or not raw.strip():
        return set(HOST_ROOTS)
    return set(raw.split())


def selected_checks(impls):
    return [check for check in CHECKS
            if CHECK_IMPLS.get(check[0], set()) & impls]


def mutation_in_scope(file, impls):
    if file.startswith('sql/'):
        return True
    return any(impl in impls and file.startswith(root)
               for impl, root in HOST_ROOTS.items())


# Every check runs under one of the SEL_JOBS slots of tools/impls.sh (and a php
# check under one of the SEL_PHP_JOBS slots as well), so the mutations graded
# side by side below share the gate's bound rather than adding their own. The
# registry is sourced from the ORIGINAL tree: it only defines functions and the
# slot directory, and the command still runs in the mutated copy.
SLOT = ['bash', '-c',
        '. "$0/tools/impls.sh" || exit 2; '
        'if [ "$1" = php ]; then shift; sel_slot sel_php "$@"; else sel_slot "$@"; fi',
        ROOT]


def jobs():
    raw = os.environ.get('SEL_JOBS', '')
    if raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, ((os.cpu_count() or 2) + 1) // 2)


# Checks that are not leaves: they start several leaves of their own, each
# through a slot (tools/check-sqlapi.sh runs every host's probe under
# sel_slot). Run inside a slot as well, a child can block on the very slot its
# parent holds -- _sel_sem waits on one chosen at random -- and wait forever:
# the first run with the SQL API check in this list hung for hours on its
# unmutated baseline.
NOT_LEAF = {'sql api'}


def run(cmd, cwd, label=None):
    # PYTHONPATH points at the mutated copy, not at the source tree, or the
    # Python runner would import the unmutated package and report every
    # mutation caught for the wrong reason -- the exact failure this tool
    # exists to catch, committed by the tool itself.
    env = dict(os.environ, PYTHONPATH=os.path.join(cwd, 'python'))
    prefix = [] if label in NOT_LEAF else SLOT
    return subprocess.run(prefix + list(cmd), cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def main(argv):
    table = json.load(open(TABLE, encoding='utf-8'))
    filters = argv[1:]
    impls = selected_impls()
    checks = selected_checks(impls)
    if not checks:
        print('no SQL mutation checks selected for SEL_IMPLS=' + ' '.join(sorted(impls)))
        return 0
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
    pool = ThreadPoolExecutor(max_workers=jobs())
    live = [(label, cmd) for label, cmd in checks if label not in NEEDS_DB or any_db]
    baseline = list(pool.map(lambda lc: (lc[0], run(lc[1], ROOT, lc[0])), live))
    for label, rc in baseline:
        if rc != 0:
            print(f'BASELINE {label} fails on the unmutated tree; every mutation '
                  'would be reported as caught by it. Fix that first.')
            return 2

    caught, holes, errors, skipped = 0, [], [], 0
    lock = threading.Lock()
    # Plain mkdtemp: it already honours TMPDIR, and passing dir= explicitly only
    # removed tempfile's fallback -- a TMPDIR naming somewhere that does not
    # exist raised FileNotFoundError where the default form quietly uses /tmp.
    # Set TMPDIR to move this off a tmpfs; on a lot of Linux installs /tmp is
    # one, which makes every copy below resident memory rather than disk.
    work = tempfile.mkdtemp(prefix='mutate-sql.')

    def grade(m):
        # One mutation: its own copy of the tree, the mutation applied, the
        # checks run in order until one fails. Returns (name, verdict, detail).
        name = m['name']
        tree = os.path.join(work, name)
        # The whole tree, minus git and build output: the map generator
        # resolves its paths from its own location, so running the ORIGINAL
        # generator would regenerate the ORIGINAL map and the copy would
        # never see the mutation. That is not hypothetical -- it is what the
        # first version of this script did, and it scored three mutations as
        # holes that the suite catches immediately.
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
            '.git', 'node_modules', 'build', 'dist', '.venv*', '__pycache__'))
        try:
            # Each mutation gets its OWN tree at its own path, which is what
            # keeps the Lisp lane honest as well as the C++ one: ASDF keys its
            # fasl cache by absolute source path, so a fresh path recompiles.
            # Mutating in place instead would be a coin toss -- CL's
            # file-write-date has one-second resolution and ASDF treats a fasl
            # whose second matches the source as current, so a restore landing
            # inside the same second leaves the previous compile in play.
            path = os.path.join(tree, m['file'])
            text = open(path, encoding='utf-8').read()
            n = text.count(m['from'])
            if n != 1:
                return name, 'error', (f'{name}: pattern occurs {n} times in {m["file"]}, '
                                       'expected exactly 1 — the mutation is stale')
            open(path, 'w', encoding='utf-8').write(text.replace(m['from'], m['to'], 1))

            # Proof it landed. A silent no-op would be scored by whatever the
            # checks say about unmutated code, which is this tool's own failure
            # mode and the reason it exists.
            if open(path, encoding='utf-8').read() == text:
                return name, 'error', f'{name}: {m["file"]} is unchanged after mutation'

            if m['file'].startswith('sql/dialects/'):
                before = [open(os.path.join(tree, g), encoding='utf-8').read()
                          for g in GENERATED]
                if run(['node', 'tools/gen-sql-map.mjs'], tree) != 0:
                    return name, 'error', f'{name}: the mutated map would not regenerate'
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
                    return name, 'error', (f'{name}: {m["file"]} changed but the generated '
                                           'map did not — the mutation is a no-op')

            for label, cmd in live:
                if run(cmd, tree, label) != 0:
                    return name, 'caught', label
            return name, 'survived', None
        finally:
            # Freed here rather than at the end of the run: every tree used to
            # stay alive for the whole run, so the space held grew with the
            # corpus for no reason -- nothing reads a tree once its mutation has
            # been graded.
            shutil.rmtree(tree, ignore_errors=True)

    selected = [m for m in table['mutations']
                if (not filters or any(f in m['name'] for f in filters))
                and mutation_in_scope(m['file'], impls)]
    try:
        # SEL_JOBS mutations side by side; each check inside takes a slot of
        # the same bound, so the load never exceeds it. Verdicts print as
        # they arrive, so the order varies from run to run; the counts do not.
        for name, verdict, detail in pool.map(grade, selected):
            with lock:
                if verdict == 'error':
                    errors.append(detail)
                elif verdict == 'caught':
                    print(f'caught  {name:<34} by {detail}', flush=True)
                    caught += 1
                elif missing:
                    print(f'skipped {name:<34} (survived; no DSN for ' + ', '.join(missing) + ')',
                          flush=True)
                    skipped += 1
                else:
                    print(f'HOLE    {name:<34} survived every check', flush=True)
                    holes.append(name)
    finally:
        pool.shutdown(wait=True)
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
