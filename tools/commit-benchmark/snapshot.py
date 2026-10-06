#!/usr/bin/env python3
"""One benchmark snapshot of the current working tree: the six scale scenarios
and Mandelbrot, in every host, one host at a time.

    python3 tools/commit-benchmark/snapshot.py                 # all seven hosts
    python3 tools/commit-benchmark/snapshot.py --lanes cpp,js  # a subset
    python3 tools/commit-benchmark/snapshot.py --compare OLD/summary.json
    python3 tools/commit-benchmark/snapshot.py --summary-only --out DIR [--compare ...]

Each lane runs the host's own scale harness (tools/scale-test/sel_benchmarks.*,
Go's go/build/scale-bench) over the 10x dataset, then the host's Mandelbrot timer
(tools/commit-benchmark/mandelbrot.*), with 2 warmups and 5 measured runs by
default. Lanes run strictly one after another, so a host never competes with
another host for the CPU; run it on an otherwise idle machine. PHP runs with
OPcache and the tracing JIT, as the other harnesses here do.

It measures the binaries already built in this tree and builds nothing: run
`cd cpp && make build/scale-bench`, the mandelbrot compile below, `make -C go`,
`bash rust/build.sh` first. A missing binary is refused with the command that
builds it.

Results go to --out (default tools/commit-benchmark/results/snapshot-<commit>/,
which is ignored): one JSON report and log per lane and per Mandelbrot run, and
summary.json with the median prepared_total_ms per scenario and the median
Mandelbrot frame time per host. --compare prints a second table against an
earlier summary.json: old, new and the change in percent. Every Mandelbrot frame
must be byte-identical across hosts, and every scenario must pass its parity
check against tools/scale-test/benchmark_results.json; a run that does not is
reported under "problems" and exits 1.
"""
import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools/commit-benchmark'
DATASET = ROOT / 'tools/scale-test/dataset-10x.json'
REFERENCE = ROOT / 'tools/scale-test/benchmark_results.json'
LANES = ['cpp', 'rust', 'go', 'js', 'lisp', 'php', 'python']
WORKLOADS = [f'scenario{i}' for i in range(1, 7)] + ['mandelbrot']
PHP = ['php', '-d', 'memory_limit=-1', '-d', 'opcache.enable_cli=1',
       '-d', 'opcache.jit_buffer_size=128M', '-d', 'opcache.jit=1255']
SBCL = ['sbcl', '--dynamic-space-size', '4096', '--noinform', '--disable-debugger', '--non-interactive']
BINARIES = {  # lane -> [(binary, the command that builds it)]
    'cpp': [('cpp/build/scale-bench', 'cd cpp && make build/scale-bench'),
            ('cpp/build/mandelbrot', 'c++ -std=c++23 -O2 -Icpp tools/commit-benchmark/mandelbrot.cpp '
                                     'cpp/build/sel.o -o cpp/build/mandelbrot')],
    'go': [('go/build/scale-bench', 'make -C go'), ('go/build/mandelbrot', 'make -C go')],
    'rust': [('rust/target/release/examples/scale-bench', 'bash rust/build.sh'),
             ('rust/target/release/examples/mandelbrot', 'bash rust/build.sh')],
}


def scale_command(lane, out, runs, warmups):
    args = ['--dataset', str(DATASET), '--reference', str(REFERENCE), '--runs', str(runs),
            '--warmups', str(warmups), '--timing-mode', 'steady-state', '--output', str(out)]
    env = {}
    if lane == 'lisp':
        env = dict(SEL_BENCHMARK_MODE='steady-state', SEL_BENCHMARK_OUTPUT=str(out),
                   SEL_BENCHMARK_REFERENCE=str(REFERENCE), SEL_BENCHMARK_RUNS=str(runs),
                   SEL_BENCHMARK_WARMUPS=str(warmups), SEL_BENCHMARK_TIMING_MODE='steady-state',
                   SEL_DATASET_FILE=str(DATASET))
        return SBCL + ['--load', 'tools/scale-test/sel_benchmarks.lisp'], env
    if lane == 'go':  # Go's harness has no --timing-mode; it is always steady state
        return ['go/build/scale-bench'] + args[:-4] + args[-2:], env
    return {'cpp': ['cpp/build/scale-bench'],
            'rust': ['rust/target/release/examples/scale-bench'],
            'js': ['node', 'tools/scale-test/sel_benchmarks.mjs'],
            'php': PHP + ['tools/scale-test/sel_benchmarks.php'],
            'python': [sys.executable, 'tools/scale-test/sel_benchmarks.py']}[lane] + args, env


def mandelbrot_command(lane, out, runs, warmups):
    env = dict(MANDEL_OUTPUT=str(out), MANDEL_RUNS=str(runs), MANDEL_WARMUPS=str(warmups))
    cmd = {'cpp': ['cpp/build/mandelbrot', str(out)],
           'rust': ['rust/target/release/examples/mandelbrot', str(out)],
           'go': ['go/build/mandelbrot', str(out)],
           'js': ['node', str(TOOL / 'mandelbrot.mjs'), str(out)],
           'lisp': SBCL + ['--load', str(TOOL / 'mandelbrot.lisp')],
           'php': PHP + [str(TOOL / 'mandelbrot.php'), str(out)],
           'python': [sys.executable, str(TOOL / 'mandelbrot.py'), str(out)]}[lane]
    return cmd, env


def run(cmd, env, log):
    started = time.time()
    print(time.strftime('%H:%M:%S'), 'start', log.stem, flush=True)
    with log.open('w') as stream:
        rc = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ, PYTHONHASHSEED='0', **env),
                            stdout=stream, stderr=subprocess.STDOUT).returncode
    print(f'{log.stem} exit {rc} ({time.time() - started:.0f}s)', flush=True)
    return rc


def summarize(out_dir, lanes):
    reference = {r['id']: r for r in json.loads(REFERENCE.read_text())}
    table, problems, frames = {}, [], {}
    for lane in lanes:
        path = out_dir / f'{lane}.json'
        if path.exists():
            report = json.loads(path.read_text())
            scenarios = report if isinstance(report, list) else report['scenarios']
            if isinstance(report, dict) and report.get('passed') is False:
                problems.append(f'{lane}: report passed=false')
            for s in scenarios:
                ok = s.get('passed', s.get('parity', {}).get('passed'))
                if not ok:
                    problems.append(f"{lane}/{s['id']}: parity failed")
                if s.get('in_memory_rows') not in (None, reference[s['id']]['in_memory_rows']):
                    problems.append(f"{lane}/{s['id']}: rows differ from the reference")
                table.setdefault(lane, {})[s['id']] = statistics.median(
                    x['prepared_total_ms'] for x in s['samples'])
        path = out_dir / f'{lane}-mandelbrot.json'
        if path.exists():
            report = json.loads(path.read_text())
            table.setdefault(lane, {})['mandelbrot'] = statistics.median(report['samples_ms'])
            for text in report['outputs']:
                frames.setdefault(hashlib.sha256(text.encode()).hexdigest()[:12], set()).add(lane)
    if len(frames) > 1:
        problems.append('Mandelbrot frames differ: ' + ', '.join(f'{h}: {sorted(v)}' for h, v in frames.items()))
    return table, problems


def print_table(title, lanes, cell, width=10):
    print(title)
    print(f"{'':12}" + ''.join(f'{lane:>{width}}' for lane in lanes))
    for w in WORKLOADS:
        print(f'{w:12}' + ''.join(f'{cell(lane, w):>{width}}' for lane in lanes))


def main():
    parser = argparse.ArgumentParser(description='One benchmark snapshot of this tree, every host in turn.')
    parser.add_argument('--lanes', default=','.join(LANES))
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--out', type=Path, default=None)
    parser.add_argument('--compare', type=Path, default=None, help="an earlier run's summary.json")
    parser.add_argument('--summary-only', action='store_true', help='summarize --out without running')
    a = parser.parse_args()
    lanes = [lane for lane in a.lanes.split(',') if lane]
    unknown = [lane for lane in lanes if lane not in LANES]
    if unknown:
        parser.error(f'unknown lane(s) {unknown}; known: {LANES}')
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
                            capture_output=True, text=True).stdout.strip() or 'unknown'
    out_dir = a.out or TOOL / 'results' / f'snapshot-{commit}'
    out_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    if not a.summary_only:
        missing = [(b, how) for lane in lanes for b, how in BINARIES.get(lane, []) if not (ROOT / b).exists()]
        if missing:
            sys.exit('missing binaries; build them first:\n' + '\n'.join(f'  {b}: {how}' for b, how in missing))
        for lane in lanes:
            cmd, env = scale_command(lane, out_dir / f'{lane}.json', a.runs, a.warmups)
            if run(cmd, env, out_dir / f'{lane}.log'):
                failed.append(lane)
            cmd, env = mandelbrot_command(lane, out_dir / f'{lane}-mandelbrot.json', a.runs, a.warmups)
            if run(cmd, env, out_dir / f'{lane}-mandelbrot.log'):
                failed.append(f'{lane}-mandelbrot')

    table, problems = summarize(out_dir, lanes)
    problems += [f'{label}: non-zero exit (see its log)' for label in failed]
    (out_dir / 'summary.json').write_text(json.dumps(
        {'commit': commit, 'runs': a.runs, 'warmups': a.warmups, 'median_ms': table, 'problems': problems},
        indent=2) + '\n')
    print_table(f'median ms at {commit} ({a.warmups} warmups, {a.runs} runs)', lanes,
                lambda lane, w: f'{table[lane][w]:.1f}' if w in table.get(lane, {}) else '-')
    if a.compare:
        old = json.loads(a.compare.read_text())['median_ms']

        def cell(lane, w):
            if w not in old.get(lane, {}) or w not in table.get(lane, {}):
                return '-'
            o, n = old[lane][w], table[lane][w]
            return f'{o:.1f}->{n:.1f} {(n / o - 1) * 100:+.0f}%'
        print()
        print_table(f'against {a.compare} (old -> new, change)', lanes, cell, width=24)
    print('problems:', problems or 'none')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
