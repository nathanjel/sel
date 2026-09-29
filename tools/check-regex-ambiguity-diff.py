#!/usr/bin/env python3
"""Differential check of a host's regex validator against the reference
(tools/regex-ambiguity-ref.py) on a deterministic corpus of random, mostly
well-formed patterns: every pattern must get the same verdict -- accepted, or
refused with E_REGEX_SYNTAX -- from the host and from the reference.

    tools/check-regex-ambiguity-diff.py [--count N] [--seed S] [--ic] -- DRIVER...

DRIVER is a command that reads one pattern per line on stdin and prints one line
per pattern: `A` (accepted) or `R` (refused with E_REGEX_SYNTAX). With `--ic` the
patterns are checked under the `i` flag (the driver gets `i` as its last
argument). A host driver lives next to that host's own code (python/bin/
regex_verdict.py, ...). `--print` writes the corpus and stops.

The corpus is small-alphabet on purpose: ambiguity needs overlapping classes, and
a random character soup is refused for syntax long before it reaches the rule.
"""
import argparse
import importlib.util
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('regex_ref', os.path.join(HERE, 'regex-ambiguity-ref.py'))
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)

ATOMS = ['a', 'b', 'c', 'A', '.', '[ab]', '[a-c]', '[^a]', '[a-z]', '[A-Z]', r'\d', r'\w', r'\s',
         r'\.', '-', ',', '1', '^', '$', 'k', 's']
QUANTS = ['*', '+', '?', '{2}', '{1,3}', '{2,}', '{0,2}', '{0,9}', '{3,12}', '*?', '+?']


def gen(rng, depth):
    parts = []
    for _ in range(rng.randint(1, 4)):
        r = rng.random()
        if r < 0.55 or depth >= 4:
            atom = rng.choice(ATOMS)
        else:
            alts = [gen(rng, depth + 1) for _ in range(rng.randint(1, 3))]
            atom = ('(?:' if rng.random() < 0.6 else '(') + '|'.join(alts) + ')'
        if rng.random() < 0.45 and atom not in ('^', '$'):
            atom += rng.choice(QUANTS)
        parts.append(atom)
    return ''.join(parts)


def corpus(n, seed):
    rng = random.Random(seed)
    seen, out = set(), []
    while len(out) < n:
        p = gen(rng, 0)
        if rng.random() < 0.05 and len(p) > 2:                  # a little damage
            k = rng.randrange(len(p))
            if p[k] not in '[{\\' and p[k - 1:k] != '\\':    # would leave a bare ] or }: every host refuses it, the reference does not
                p = p[:k] + p[k + 1:]
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def verdict(p, ic):
    try:
        ref.validate(p, ic)
        return 'A'
    except ref.Reject:
        return 'R'
    except ref.BadArg:
        return 'R'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=4000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--ic', action='store_true')
    ap.add_argument('--print', action='store_true', dest='dump')
    ap.add_argument('driver', nargs='*')
    a = ap.parse_args()
    pats = corpus(a.count, a.seed)
    if a.dump:
        print('\n'.join(pats))
        return 0
    if not a.driver:
        ap.error('a DRIVER command is required (after --)')
    cmd = a.driver + (['i'] if a.ic else [])
    out = subprocess.run(cmd, input='\n'.join(pats) + '\n', capture_output=True, text=True, timeout=1800)
    got = out.stdout.split('\n')[:len(pats)]
    if out.returncode != 0 or len(got) != len(pats):
        print('driver failed:', out.returncode, out.stderr[-400:])
        return 2
    bad = 0
    accepted = 0
    for p, g in zip(pats, got):
        want = verdict(p, a.ic)
        accepted += want == 'A'
        if g != want:
            bad += 1
            if bad <= 10:
                print(f'MISMATCH {p!r}: host {g}, reference {want}')
    print(f'{len(pats)} patterns (seed {a.seed}{", i" if a.ic else ""}): {accepted} accepted by the reference, {bad} mismatches')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
