#!/usr/bin/env python3
"""The decimal mutation runner. See tools/mutate-decimal.sh for what it is for.

    tools/mutate-decimal.sh [--weak] [--list] [name-substring ...]

Each host gets a copy of the tree (a C++ host several: see SHARDS), built once
and checked once unmutated (the baseline). Then, one mutant at a time in each
copy: the edit applied (its `from` must occur exactly once), what the host needs
rebuilt, the checks in CHECKS order until one fails, and the file restored. The
first failing check is reported as the one that caught it; a mutant every check
passes SURVIVED. Hosts (and shards) run side by side; every check and build is a
leaf under the tools/impls.sh slots.
"""

import fcntl
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# SEL_MUTATE_DECIMAL_TABLE grades a draft table instead (a path).
TABLE = os.environ.get('SEL_MUTATE_DECIMAL_TABLE') or os.path.join(ROOT, 'tools', 'decimal-mutations.json')

# The oracle records every host grades: tools/decimal-oracle.py (Python's
# decimal module, operands of <= 12 integer and <= 6 fractional digits -- the
# only decimal oracle 0.9.2 had) and tools/decimal-oracle-exact.py (exact
# rationals at the widths the cores hold). Fixed counts and seeds, so a verdict
# is reproducible; SEL_MUTATE_DECIMAL_SEED moves both.
NARROW_COUNT = 2000
WIDE_COUNT = 1500
SEED = int(os.environ.get('SEL_MUTATE_DECIMAL_SEED', '20261006'))

HOSTS = ('js', 'php', 'python', 'cpp', 'lisp', 'go', 'rust')
# What each host's checks need on PATH. A host whose toolchain is missing is an
# error, not a skip: narrow SEL_IMPLS to say so.
TOOLCHAIN = {'js': ['node'], 'php': ['php'], 'python': ['python3'], 'cpp': ['make', 'c++'],
             'lisp': ['sbcl'], 'go': ['go'], 'rust': ['cargo']}

# Copies per host. A C++ mutant recompiles sel.cpp twice (the conformance
# library and the whitebox check-decimal, which includes the translation unit),
# about 45 s on an idle box, so C++'s mutants are dealt round-robin over three
# copies; a Lisp mutant that gets as far as conformance 24 and the unit tests
# spends a minute and a half there, so Lisp has two. Every other host rebuilds
# in seconds or not at all. Each copy grades its share one mutant at a time.
# Rust has ONE copy and one target directory per run (its incremental build is
# the shared state).
SHARDS = {'cpp': 3, 'lisp': 2}

# The checks, cheapest first. The loop stops at the first that fails; the order
# decides what a mutant costs, never whether it is caught.
#   kind 'decimal'      impl_decimal <host> <oracle file>
#   kind 'conformance'  impl_conformance <host> conformance/<file>
#   kind 'unit'         the host's numeric unit tests (unit_command below)
CHECKS = [
    ('oracle narrow', 'decimal', 'narrow'),
    ('oracle wide', 'decimal', 'wide'),
    ('conformance 02-numbers', 'conformance', '02-numbers.selt'),
    ('conformance 03-operators', 'conformance', '03-operators.selt'),
    ('conformance 22-canon', 'conformance', '22-canon.selt'),
    ('conformance 32-numeric-plans', 'conformance', '32-numeric-plans.selt'),
    ('conformance 24-decimal-boundaries', 'conformance', '24-decimal-boundaries.selt'),
    ('conformance 10-limits', 'conformance', '10-limits.selt'),
    ('unit', 'unit', None),
]

# --weak: the checks as they stood at 0.9.2, when the C++ multiply rounded both
# operands to 18 fractional digits and every lane was green -- the narrow oracle
# only, and no generated boundary cases (24, 32, both generated from the exact
# oracle after that release) and no unit lane. A red run under --weak is the
# proof that the full set bites where that one did not.
WEAK = {'oracle narrow', 'conformance 02-numbers', 'conformance 03-operators',
        'conformance 22-canon', 'conformance 10-limits'}

# The Lisp tests a decimal mutant can reach, run by name: the whole FiveAM suite
# is a minute, these are seconds.
LISP_TESTS = ['decimal', 'math-plan', 'math-plan-evaluates-then-coerces', 'dec-format-ignores-the-embedders-printer-variables',
              'dec-parse-reads-any-string-as-its-characters', 'make-int-cap-guard-uses-integers-only',
              'ceil-and-floor-carry-past-the-digit-cap-is-positioned', 'ltb-of-empty-and-scaled-integral-values']

CARGO_ENV = {
    # Semantics unchanged (overflow checks and debug assertions stay off, as
    # in the shipped profile); only how the compiler spends its time. Thin LTO
    # and one codegen unit made every incremental rebuild a whole-crate one.
    'CARGO_PROFILE_RELEASE_LTO': 'off',
    'CARGO_PROFILE_RELEASE_CODEGEN_UNITS': '16',
    'CARGO_PROFILE_RELEASE_INCREMENTAL': 'true',
}
RUST_BINS = ['check-decimal', 'conformance']


def unit_command(host):
    """The host's numeric unit tests, as a shell command run in the copy; None
    where the host has none worth their time here."""
    if host == 'js':
        return 'node tools/check-js-decimal-guard.mjs'
    if host == 'python':
        return ('python3 -c "import pytest" 2>/dev/null || { echo "pytest missing" >&2; exit 3; }; '
                'PYTHONPATH="$PWD/python" python3 -m pytest -q -p no:cacheprovider '
                'python/tests/test_decimal_native.py')
    if host == 'cpp':
        return 'make -s -j2 -C cpp build/unit && cpp/build/unit'
    if host == 'go':
        # The decimal package's tests, and the plan tests that compare planned
        # with as-written arithmetic (registers); not the whole-corpus
        # equivalence test (half a minute) or the allocation budgets.
        return ('cd go && go test -count=1 ./internal/decimal/ && go test -count=1 ./sel '
                "-run 'TestPlan|TestMathPlan|TestWideMathPlan' -skip 'Allocation|Budget'")
    if host == 'rust':
        return 'cd rust && cargo test -q --release -p sel-lang --lib'
    if host == 'lisp':
        names = ' '.join(f'"{t}"' for t in LISP_TESTS)
        return ('sbcl --dynamic-space-size 4GB --noinform --disable-debugger --non-interactive '
                '--load lisp/bin/boot.lisp '
                '--eval \'(let ((*standard-output* (make-broadcast-stream))) '
                '(funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/tests))\' '
                '--eval \'(unless (every (lambda (n) (funcall (find-symbol "RUN!" "FIVEAM") '
                f'(find-symbol (string-upcase n) "SEL-TESTS"))) (quote ({names}))) '
                '(sb-ext:exit :code 1))\'')
    return None


def build_command(host, baseline=False):
    """What the host needs rebuilt after an edit, or None. The C++ unit binary
    is a third compile of sel.cpp, so a mutant builds it only when the unit check
    is reached (unit_command); the baseline builds all three side by side."""
    if host == 'cpp':
        return ('make -s -j3 -C cpp build/check-decimal build/conformance build/unit' if baseline
                else 'make -s -j2 -C cpp build/check-decimal build/conformance')
    if host == 'go':
        return 'cd go && mkdir -p build && go build -o build/ ./bin/check-decimal ./bin/conformance'
    if host == 'rust':
        bins = ' '.join(f'--bin {b}' for b in RUST_BINS)
        return f'cd rust && cargo build -q --release -p sel-lang-dev {bins}'
    return None


# Every command is a leaf under one of tools/impls.sh's SEL_JOBS slots (`job`),
# and a PHP host's under one of its SEL_PHP_JOBS slots as well (`php`, taken
# second, the order every tool takes them in), with SEL_SLOT_HELD naming both so
# the sel_slot / sel_php calls inside (impl_decimal's three PHP modes) run at
# once, as under sel_hold. The slots are the same flock files; only the wait
# differs. sel_slot blocks on ONE slot chosen at random once every slot is
# busy, so a leaf that drew the slot of a half-hour step (the gate's ASan build
# holds one for 30 minutes) waited the half hour while the others came free --
# a C++ baseline here did, in the gate. These are polled instead: every slot,
# without blocking, until one is free.
class Slots:
    def __init__(self):
        out = subprocess.run(['bash', '-c', '. tools/impls.sh >/dev/null 2>&1 || exit 2; '
                              'printf "%s\\n%s\\n%s\\n" "$SEL_JOBS" "$SEL_PHP_JOBS" "$SEL_SLOT_DIR"'],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
        self.count = {'job': int(out[0]), 'php': int(out[1])}
        self.dir = out[2]
        os.makedirs(self.dir, exist_ok=True)

    def take(self, kind):
        n = self.count[kind]
        first = random.randrange(n)
        while True:
            for k in range(n):
                f = open(os.path.join(self.dir, f'{kind}.{(first + k) % n}'), 'a')
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return f
                except OSError:
                    f.close()
            time.sleep(0.2)


def leaf(command):
    return ['bash', '-c', '. tools/impls.sh || exit 2; eval "$0"', command]


class Shard:
    """One copy of the tree and the mutants it grades, one at a time."""

    def __init__(self, host, index, work, oracles, checks, env, slots):
        self.host, self.index, self.slots = host, index, slots
        self.tree = os.path.join(work, f'{host}.{index}')
        self.oracles, self.checks = oracles, checks
        # A fasl cache per shard: the Lisp shards start together on an empty
        # cache, and two SBCLs compiling Quicklisp's dependencies into one
        # directory can read each other's half-written fasl -- a baseline then
        # fails on an unmutated tree.
        self.env = dict(env, XDG_CACHE_HOME=os.path.join(work, f'xdg-cache.{host}.{index}'))
        self.log = os.path.join(work, f'{host}.{index}.log')

    def label(self):
        return self.host if SHARDS.get(self.host, 1) == 1 else f'{self.host}#{self.index}'

    def run(self, command, what):
        kinds = ['job', 'php'] if self.host == 'php' else ['job']
        held = [self.slots.take(kind) for kind in kinds]
        try:
            env = dict(self.env, SEL_SLOT_HELD=(self.env.get('SEL_SLOT_HELD', '') + ' ' + ' '.join(kinds)).strip())
            with open(self.log, 'a', encoding='utf-8') as log:
                log.write(f'\n=== {what}: {command}\n')
                log.flush()
                # close_fds (the default): no child inherits a held slot, so
                # none can outlive the leaf holding it.
                return subprocess.run(leaf(command), cwd=self.tree, env=env,
                                      stdout=log, stderr=subprocess.STDOUT).returncode
        finally:
            for f in reversed(held):
                f.close()

    def check_command(self, kind, arg):
        if kind == 'decimal':
            return f'impl_decimal {self.host} {self.oracles[arg]}'
        if kind == 'conformance':
            return f'impl_conformance {self.host} conformance/{arg}'
        return unit_command(self.host)

    def build(self, baseline=False):
        command = build_command(self.host, baseline)
        if command is None:
            return 0
        rc = self.run(command, 'build')
        if rc == 0 and self.host == 'rust':
            target = self.env['CARGO_TARGET_DIR']
            os.makedirs(os.path.join(self.tree, 'rust', 'build'), exist_ok=True)
            for b in RUST_BINS:
                link = os.path.join(self.tree, 'rust', 'build', b)
                if not os.path.lexists(link):
                    os.symlink(os.path.join(target, 'release', b), link)
        return rc

    def checks_for(self):
        return [(label, kind, arg) for label, kind, arg in self.checks
                if kind != 'unit' or unit_command(self.host) is not None]

    def baseline(self):
        """Every check on the unmutated copy. A check already failing here would
        report every mutant caught -- the verdict this tool exists to distrust."""
        if self.build(baseline=True) != 0:
            return f'{self.label()}: the unmutated copy does not build (log: {self.log})'
        for label, kind, arg in self.checks_for():
            if self.run(self.check_command(kind, arg), f'baseline {label}') != 0:
                return f'{self.label()}: {label} fails on the unmutated tree (log: {self.log})'
        return None

    def write(self, path, text):
        if self.host == 'lisp':
            # ASDF compares whole-second file dates and treats a fasl dated the
            # same second as its source as current: an edit landing in the
            # second the previous compile finished would be graded on the
            # previous fasl. Every write here starts a fresh second.
            time.sleep(1.02 - (time.time() % 1.0))
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)

    def grade(self, m):
        """(verdict, detail): caught/<check>, survived, or error/<why>."""
        path = os.path.join(self.tree, m['file'])
        try:
            with open(path, encoding='utf-8', newline='') as f:
                original = f.read()
        except OSError as e:
            return 'error', f'cannot read {m["file"]}: {e}'
        n = original.count(m['from'])
        if n != 1:
            return 'error', f'`from` occurs {n} times in {m["file"]}, expected exactly 1 -- the mutant is stale'
        mutated = original.replace(m['from'], m['to'], 1)
        if mutated == original:
            return 'error', '`to` equals `from`: the mutant changes nothing'
        self.write(path, mutated)
        try:
            with open(self.log, 'a', encoding='utf-8') as log:
                log.write(f'\n##### mutant {m["name"]}\n')
            if self.build() != 0:
                return 'error', f'the mutant does not build (log: {self.log})'
            for label, kind, arg in self.checks_for():
                if self.run(self.check_command(kind, arg), label) != 0:
                    return 'caught', label
            return 'survived', None
        finally:
            self.write(path, original)
            with open(path, encoding='utf-8', newline='') as f:
                if f.read() != original:
                    raise RuntimeError(f'{path}: not restored')


def copy_tree(dst):
    shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(
        '.git', 'node_modules', 'dist', 'target', 'build', '__pycache__', '.venv*',
        '.build-stage*', 'results'))


def main(argv):
    args = argv[1:]
    weak = '--weak' in args
    listing = '--list' in args
    filters = [a for a in args if a not in ('--weak', '--list')]
    for a in filters:
        if a.startswith('-'):
            print(f'mutate-decimal: unknown option {a}', file=sys.stderr)
            return 2

    with open(TABLE, encoding='utf-8') as f:
        table = json.load(f)
    mutants = table['mutations']
    names = [m['name'] for m in mutants]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        print('mutate-decimal: duplicate mutant names: ' + ', '.join(dup), file=sys.stderr)
        return 2
    for m in mutants:
        missing = [k for k in ('name', 'host', 'file', 'from', 'to', 'class', 'note') if k not in m]
        if missing or m['host'] not in HOSTS:
            print(f'mutate-decimal: malformed mutant {m.get("name", m)}: '
                  f'{"missing " + ", ".join(missing) if missing else "unknown host " + m["host"]}',
                  file=sys.stderr)
            return 2

    raw = os.environ.get('SEL_IMPLS', '').split()
    impls = set(raw) if raw else set(HOSTS)
    selected = [m for m in mutants
                if m['host'] in impls and (not filters or any(f in m['name'] for f in filters))]
    if listing:
        for m in selected:
            print(f'{m["host"]:<7} {m["class"]:<22} {m["name"]}')
        print(f'{len(selected)} mutants')
        return 0
    if not selected:
        print('mutate-decimal: no mutant selected' + (f' by {filters}' if filters else '')
              + f' for SEL_IMPLS={" ".join(sorted(impls))}', file=sys.stderr)
        return 2

    hosts_wanted = sorted({m['host'] for m in selected})
    missing = [f'{h} (no {cmd})' for h in hosts_wanted for cmd in TOOLCHAIN[h] if shutil.which(cmd) is None]
    if missing:
        print('mutate-decimal: cannot grade ' + ', '.join(missing) + '; narrow SEL_IMPLS to leave a host out',
              file=sys.stderr)
        return 2

    checks = [c for c in CHECKS if not weak or c[0] in WEAK]
    started = time.time()
    keep = os.environ.get('SEL_MUTATE_DECIMAL_KEEP') == '1'
    # Plain mkdtemp: it honours TMPDIR. One copy of the tree per shard (about
    # 50 MB without build output) plus the C++ objects and one Rust target.
    work = tempfile.mkdtemp(prefix='mutate-decimal.')
    try:
        oracles = {'narrow': os.path.join(work, 'narrow.txt'), 'wide': os.path.join(work, 'wide.txt')}
        with open(oracles['narrow'], 'w') as f:
            subprocess.run([sys.executable, 'tools/decimal-oracle.py', str(NARROW_COUNT), str(SEED)],
                           cwd=ROOT, stdout=f, check=True)
        with open(oracles['wide'], 'w') as f:
            subprocess.run([sys.executable, 'tools/decimal-oracle-exact.py', str(WIDE_COUNT), str(SEED)],
                           cwd=ROOT, stdout=f, check=True)
        for path in oracles.values():
            if os.path.getsize(path) == 0:
                print(f'mutate-decimal: the oracle wrote nothing to {path}', file=sys.stderr)
                return 2

        env = dict(os.environ)
        # No bytecode cache: CPython validates a .pyc by the source's
        # whole-second mtime and its size, so a same-size mutant restored in the
        # second it was compiled would be graded as the mutant again.
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # The Lisp copies compile into caches of their own (one per shard, see
        # Shard), removed with the run, rather than leaving fasl trees under
        # ~/.cache.
        # Go keeps its own build cache: content-addressed, so sharing is safe.
        if 'GOCACHE' not in env:
            gocache = subprocess.run(['go', 'env', 'GOCACHE'], capture_output=True, text=True)
            if gocache.returncode == 0 and gocache.stdout.strip():
                env['GOCACHE'] = gocache.stdout.strip()
        env['CARGO_TARGET_DIR'] = os.path.join(work, 'rust-target')
        env.update(CARGO_ENV)

        slots = Slots()
        hosts = [h for h in HOSTS if any(m['host'] == h for m in selected)]
        shards = []
        for h in hosts:
            count = min(SHARDS.get(h, 1), sum(1 for m in selected if m['host'] == h))
            for i in range(count):
                shards.append(Shard(h, i, work, oracles, checks, env, slots))
        for s in shards:
            copy_tree(s.tree)
        jobs = {s: [] for s in shards}
        for h in hosts:
            mine = [s for s in shards if s.host == h]
            for i, m in enumerate(m for m in selected if m['host'] == h):
                jobs[mine[i % len(mine)]].append(m)

        print(f'mutate-decimal: {len(selected)} mutants, {len(shards)} '
              f'{"copy" if len(shards) == 1 else "copies"} of the tree'
              + (' -- WEAK checks (0.9.2-era: ' + ', '.join(c[0] for c in checks) + ')' if weak else ''),
              flush=True)
        lock = threading.Lock()
        results = []
        problems = []

        def work_shard(s):
            why = s.baseline()
            if why:
                with lock:
                    problems.append('BASELINE ' + why)
                return
            for m in jobs[s]:
                t0 = time.time()
                verdict, detail = s.grade(m)
                took = time.time() - t0
                with lock:
                    results.append((m, verdict, detail))
                    if m.get('equivalent'):
                        if verdict == 'survived':
                            print(f'equivalent {m["name"]:<44} ({m["host"]}): {m["equivalent"]}', flush=True)
                        else:
                            print(f'NOT EQUIVALENT {m["name"]:<40} ({m["host"]}): marked equivalent but '
                                  f'{verdict} {detail or ""}', flush=True)
                    elif verdict == 'caught':
                        print(f'caught   {m["name"]:<44} by {detail} ({m["host"]}, {took:.0f}s)', flush=True)
                    elif verdict == 'survived':
                        print(f'SURVIVED {m["name"]:<44} ({m["host"]}, {took:.0f}s)', flush=True)
                    else:
                        print(f'ERROR    {m["name"]:<44} ({m["host"]}): {detail}', flush=True)

        with ThreadPoolExecutor(max_workers=len(shards)) as pool:
            list(pool.map(work_shard, shards))

        caught = [r for r in results if r[1] == 'caught' and not r[0].get('equivalent')]
        survived = [r for r in results if r[1] == 'survived' and not r[0].get('equivalent')]
        errors = [r for r in results if r[1] == 'error']
        equivalent = [r for r in results if r[0].get('equivalent') and r[1] == 'survived']
        wrongly_equivalent = [r for r in results if r[0].get('equivalent') and r[1] == 'caught']

        print()
        by_check = {}
        for _, _, detail in caught:
            by_check[detail] = by_check.get(detail, 0) + 1
        for label, _, _ in checks:
            if label in by_check:
                print(f'  caught by {label:<36} {by_check[label]}')
        per_host = []
        for h in hosts:
            mine = [r for r in results if r[0]['host'] == h]
            per_host.append(f'{h} {sum(1 for r in mine if r[1] == "caught")}/{len(mine)}')
        print('  per host (caught/graded): ' + ', '.join(per_host))
        for p in problems:
            print(p)
        print(f'{len(caught)} caught, {len(survived)} survived, {len(equivalent)} equivalent, '
              f'{len(errors) + len(wrongly_equivalent)} errors'
              f'{" (WEAK checks)" if weak else ""}; {time.time() - started:.0f}s')
        for m, _, _ in survived:
            print(f'SURVIVED {m["name"]} ({m["host"]}, {m["class"]}): nothing here would tell you this had happened')
        for m, _, detail in errors:
            print(f'ERROR {m["name"]} ({m["host"]}): {detail}')
        for m, _, detail in wrongly_equivalent:
            print(f'ERROR {m["name"]} ({m["host"]}): marked equivalent, caught by {detail}')
        if len(results) != len(selected) and not problems:
            problems.append('internal: not every mutant was graded')
        return 0 if not (survived or errors or wrongly_equivalent or problems) else 1
    finally:
        if keep:
            print(f'(kept {work})')
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
