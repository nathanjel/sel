#!/usr/bin/env python3
"""Plain AST versus optimised execution over the conformance corpus:
the Python twin of tools/check-eval-equivalence.mjs — see that file for the rule.

    PYTHONPATH=$PWD/python python3 tools/check-eval-equivalence.py [file.selt ...]

Every `.selt` source (with its setup), is run
on fresh contexts as `eval_node(program.ast)` (plain) and as `program.run()`
(optimiser and math plans), twice each on one Program; value dump, error code and
position, and the final context dump must agree. Exit status is non-zero on any
difference. A gate lane of tools/check.sh ("Python plain vs optimised").
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'python'))
import sel                                   # noqa: E402
from sel.eval import Context, eval_node      # noqa: E402
from sel._stack import recursion_budget      # noqa: E402

# The conformance runner's own reader, so this lane and the suite cannot read a
# case differently. (python/bin after python/: `sel` stays the source tree's.)
sys.path.insert(1, os.path.join(ROOT, 'python', 'bin'))
from conformance import parse_selt           # noqa: E402


def cases(path):
    with open(path, 'rb') as fh:
        text = fh.read().decode('utf-8')
    return [(c['name'], c['setup'], c['source']) for c in parse_selt(text, path)]


def observe(mode, program, root):
    ctx = Context(root)
    try:
        # The plain path bypasses Program.run, so it gets the entry point's
        # recursion budget explicitly (sel/_stack.py).
        with recursion_budget():
            v = eval_node(program.ast, ctx) if mode == 'plain' else program.run(root)
        answer = v.dump()
    except sel.SelError as e:
        answer = f'!{e.code}@{e.line}:{e.col}'
    try:
        dump = root.dump()
    except sel.SelError as e:
        dump = f'!{e.code}'
    return f'{answer} | ctx={dump}', (ctx.depth if mode == 'plain' else 0)


def run(setup, source, mode):
    def fresh():
        root = sel.Value.from_native({})
        if setup:
            sel.compile(setup).run(root)
        return root
    try:
        program = sel.compile(source)
    except sel.SelError as e:
        at = f'!{e.code}@{e.line}:{e.col} (compile)'
        return (at, 0), (at, 0)
    return observe(mode, program, fresh()), observe(mode, program, fresh())


def main(argv):
    files = argv or sorted(os.path.join(ROOT, 'conformance', f) for f in os.listdir(os.path.join(ROOT, 'conformance'))
                            if f.endswith('.selt'))
    total, bad = 0, []
    for f in files:
        for name, setup, source in cases(f):
            total += 1
            try:
                (p1, d1), (p2, _) = run(setup, source, 'plain')
                (q1, _), (q2, _) = run(setup, source, 'physical')
            except RecursionError:
                bad.append((name, 'host RecursionError', ''))
                continue
            if p1 != p2:
                bad.append((name, 'plain run is not repeatable', f'{p1}\n   vs {p2}'))
            elif p1 != q1:
                bad.append((name, 'physical differs from plain', f'plain={p1[:160]}\n   phys ={q1[:160]}'))
            elif q1 != q2:
                bad.append((name, 'physical run is not repeatable', f'{q1}\n   vs {q2}'))
            if d1:
                bad.append((name, 'plain evaluation left depth counted', str(d1)))
    for name, why, detail in bad:
        print(f'DIFF {name}: {why}\n   {detail}')
    print(f'{total} sources, {len(bad)} differ')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
