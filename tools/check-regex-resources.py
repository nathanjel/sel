#!/usr/bin/env python3
"""Bounded child-process probes for regex resource behaviour.

The conformance suite must never hang or die, so anything that can is run here:
every case is one CLI invocation per host, in its own process, with a wall-time
ceiling and a peak-memory measurement. What is asserted is deliberately weaker
than a correct answer and does NOT depend on a policy the spec has not chosen:

  * the process ends inside the ceiling (no hang),
  * it ends by printing a value or a SEL error (`E_...`) -- never a host
    exception (RangeError, RecursionError, CONTROL-STACK-EXHAUSTED, `terminate
    called`, a Go panic), never a signal,
  * and where the pattern and subject leave no doubt (a plain long match), it
    prints the RIGHT value: a resource failure must not masquerade as FALSE, 0 or
    unchanged text.

  tools/check-regex-resources.py [--ceiling SECONDS] [case-name-substring ...]

SEL_IMPLS narrows the hosts, as everywhere (tools/impls.sh).
"""
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CEILING = 20.0
MEM_CASE_LIMIT_MB = 1024

HOST_FAILURE_MARKERS = (
    'RangeError', 'RecursionError', 'CONTROL-STACK', 'Maximum call stack', 'stack exhausted',
    'Traceback', 'Fatal error', 'FATAL ERROR', 'terminate called', 'panic:', 'goroutine ',
    'Unhandled', 'unhandled condition', 'SIGSEGV', 'Segmentation', 'std::', 'srell',
    'Allowed memory size', 'out of memory', 'OutOfMemory', 'MemoryError',
    'Error:', 'file:///', 'Exception',
)

# name | source | allowed outcomes: exact strings and/or "E_*" for any SEL error
CASES = [
    # --- exponential backtracking: any bounded answer or a SEL refusal
    ('dos.nested-plus', "RMATCH('^(a+)+$', REPEAT('a', 32) & '!')", {'FALSE', 'E_*'}),
    ('dos.overlapping-alt', "RMATCH('^(a|aa)+$', REPEAT('a', 40) & 'b')", {'FALSE', 'E_*'}),
    ('dos.alt-star', "RMATCH('(a|b|ab)*c', REPEAT('ab', 40))", {'FALSE', 'E_*'}),
    ('dos.nested-star-replace', "LEN(RREPLACE('^(a*)*$', 'x', REPEAT('a', 30) & '!'))", {'31', 'E_*'}),
    # --- a long subject that plainly matches must be answered correctly
    ('long.match-100k', "RMATCH('^(?:a|b)*$', REPEAT('a', 100000))", {'TRUE'}),
    ('long.match-captured-100k', "RMATCH('^(a|b)*$', REPEAT('ab', 50000))", {'TRUE'}),
    ('long.find-100k', "RFIND('(?:a|b)*c', REPEAT('a', 100000) & 'c')", {'1'}),
    ('long.replace-100k', "LEN(RREPLACE('^(?:a|b)*$', 'X', REPEAT('a', 100000)))", {'1'}),
    ('long.groups-100k', "COUNT(RGROUPS('^(?:a|b)*$', REPEAT('a', 100000)))", {'1'}),
    ('long.alt-prefix-100k', "RMATCH('^(?:ab|a)*$', REPEAT('a', 100000))", {'TRUE'}),
    # --- multi-megabyte subjects: a value or a SEL error, never a host failure
    ('huge.match-5m', "RMATCH('^(a|b)*$', REPEAT('a', 5000000))", {'TRUE', 'E_*'}),
    # One match over the whole subject, then the empty match abutting it at the end
    # (re.replace.empty-match-abutting-a-match): `xx`.
    ('huge.replace-5m', "LEN(RREPLACE('(a|b)*', 'x', REPEAT('a', 5000000)))", {'2', 'E_*'}),
    ('huge.no-match-3m', "RMATCH('^(a|b)*c$', REPEAT('ab', 1500000))", {'FALSE', 'E_*'}),
    # --- structural extremes in the pattern
    ('deep.groups-1000', "RMATCH(REPEAT('(?:', 1000) & 'a' & REPEAT(')', 1000), 'a')", {'TRUE', 'E_*'}),
    ('deep.groups-50000', "RMATCH(REPEAT('(?:', 50000) & 'a' & REPEAT(')', 50000), 'a')", {'TRUE', 'E_*'}),
    ('deep.capture-counted-20000', "RMATCH('^(a){20000}$', REPEAT('a', 20000))", {'TRUE', 'E_*'}),
    ('deep.nullable-lazy-loop', "RMATCH('(?:a(?:x*)*?)*[!]', 'aa')", {'FALSE', 'E_*'}),
    ('wide.groups-8000', "RMATCH(REPEAT('(a)', 8000), REPEAT('a', 8000))", {'TRUE', 'E_*'}),
    ('wide.program-size', "RMATCH(REPEAT('[^a]{1000}', 10000), 'a')", {'FALSE', 'E_*'}),
    ('wide.product-of-repeats', "RMATCH('(?:a{60000}){60000}', 'a')", {'FALSE', 'E_*'}),
]

# Cache growth: many DISTINCT patterns built from data. Peak RSS is bounded.
MEMORY_CASES = [
    ('cache.20000-unique-patterns',
     "L = SPLIT(REPEAT('x,', 20000), ','); COUNT(FILTER(L, RMATCH('a' & _K, 'a')))",
     {'0', 'E_*'}),
]


def hosts():
    # An explicit SEL_IMPLS is taken as given (no staleness guard): this tool is
    # often pointed at one freshly built host while the rest of the tree is mid-edit.
    if os.environ.get('SEL_IMPLS'):
        return os.environ['SEL_IMPLS'].split()
    env = dict(os.environ)
    out = subprocess.run(['bash', '-c', '. tools/impls.sh; available_impls'], cwd=ROOT,
                         capture_output=True, text=True, env=env).stdout.split()
    return out


WRAPPER = (
    "import resource, subprocess, sys\n"
    "p = subprocess.run(sys.argv[1:])\n"
    "kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss\n"
    "sys.stdout.flush()\n"
    "sys.stderr.write('\\nRSS_KB=%d\\nRC=%d\\n' % (kb, p.returncode))\n"
)


def run(host, src, ceiling):
    """One CLI run in a fresh process group: (outcome, seconds, peak RSS in MB).
    The peak is that of this run alone: a wrapper process reads its own child's
    rusage, so an earlier heavy case cannot leak into a later one."""
    with tempfile.NamedTemporaryFile('w', suffix='.sel', delete=False) as f:
        f.write(src)
        path = f.name
    try:
        inner = '. tools/impls.sh; export SEL_PHP_FLAGS="-d memory_limit=2G"; impl_cli "$0" "$1"'
        cmd = [sys.executable, '-c', WRAPPER, 'bash', '-c', inner, host, path]
        t0 = time.time()
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             preexec_fn=os.setsid)
        try:
            out, _ = p.communicate(timeout=ceiling)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            try:
                p.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass                     # a straggler outside the group; do not wait for it
            return 'TIMEOUT', time.time() - t0, 0
        secs = time.time() - t0
        text = out.decode('utf-8', 'replace')
        rss_mb, rc = 0, 0
        body = []
        for line in text.splitlines():
            if line.startswith('RSS_KB='):
                rss_mb = int(line[7:]) // 1024
            elif line.startswith('RC='):
                rc = int(line[3:])
            else:
                body.append(line)
        text = '\n'.join(body)
        first = next((l for l in body if l.strip()), '').strip()
        if rc < 0:
            return f'SIGNAL {-rc}', secs, rss_mb
        for m in HOST_FAILURE_MARKERS:
            if m in text:
                return f'HOST FAILURE ({m}): {first[:80]}', secs, rss_mb
        if rc >= 128:
            return f'SIGNAL {rc - 128}', secs, rss_mb
        if first.startswith('E_'):
            return first.split(' ')[0], secs, rss_mb
        return first, secs, rss_mb
    finally:
        os.unlink(path)


def allowed(got, ok):
    if got in ok:
        return True
    return 'E_*' in ok and got.startswith('E_')


def main():
    global CEILING
    args = sys.argv[1:]
    if args[:1] == ['--ceiling']:
        CEILING = float(args[1])
        args = args[2:]
    impls = hosts()
    if not impls:
        print('no implementations available', file=sys.stderr)
        return 1
    failures = 0
    for name, src, ok in CASES + MEMORY_CASES:
        if args and not any(a in name for a in args):
            continue
        for host in impls:
            got, secs, rss = run(host, src, CEILING)
            bad = not allowed(got, ok)
            if name.startswith('cache.') and rss > MEM_CASE_LIMIT_MB:
                bad = True
                got += f' (peak RSS {rss} MB > {MEM_CASE_LIMIT_MB})'
            if bad:
                failures += 1
                print(f'FAIL {name:32} {host:14} {secs:5.1f}s  {got}')
    if failures:
        print(f'{failures} regex resource check(s) failed')
        return 1
    print(f'regex resources: every case bounded and honest across: {" ".join(impls)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
