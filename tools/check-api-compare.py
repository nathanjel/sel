#!/usr/bin/env python3
"""Compare the per-host API reports written by tools/check-api.sh.

usage: check-api-compare.py WORKDIR PINS REF HOST...

WORKDIR/<host>.txt holds `NN name = value` lines. Two things are checked:

  * agreement: for every probe, every host that answers (does not print `n/a (...)`)
    prints the same value as the reference host's answer;
  * pins (tools/api-pins.txt): every answering host prints the pinned value, and
    `n/a` is only printed by the hosts the pin file lists for that probe, always
    with a reason.

Exit status 1 with a diff-style report on any failure, 0 otherwise.
"""
import re
import sys

work, pins_path, ref = sys.argv[1:4]
hosts = sys.argv[4:]

LINE = re.compile(r'^(\d+) (\S+) = ?(.*)$')
NA = re.compile(r'^n/a \(.+\)$')


def load(host):
    rows = {}
    order = []
    for raw in open(f'{work}/{host}.txt', encoding='utf-8').read().split('\n'):
        if not raw:
            continue
        m = LINE.match(raw)
        if not m:
            rows.setdefault('?malformed', []).append(raw)
            continue
        rows[m.group(2)] = (m.group(1), m.group(3))
        order.append(m.group(2))
    return rows, order


reports = {h: load(h) for h in hosts}
pins, na = {}, {}
for raw in open(pins_path, encoding='utf-8'):
    raw = raw.strip()
    if not raw or raw.startswith('#'):
        continue
    if raw.startswith('pin '):
        name, _, value = raw[4:].partition(' = ')
        pins[name.strip()] = value
    elif raw.startswith('na '):
        parts = raw[3:].split()
        na[parts[0]] = set(parts[1:])
    else:
        print(f'api-pins.txt: cannot read: {raw}')
        sys.exit(2)

failures = []
names = reports[ref][1]
for h in hosts:
    if '?malformed' in reports[h][0]:
        failures.append(f'{h}: malformed report lines: {reports[h][0]["?malformed"][:3]}')
    if reports[h][1] != names:
        missing = [n for n in names if n not in reports[h][0]]
        extra = [n for n in reports[h][1] if n not in names]
        failures.append(f'{h}: probes differ from {ref}: missing {missing[:5]} extra {extra[:5]}')

for name in names:
    answers = {}
    for h in hosts:
        if name not in reports[h][0]:
            continue
        value = reports[h][0][name][1]
        if value.startswith('n/a'):
            if not NA.match(value):
                failures.append(f'{name}: {h} prints n/a without a reason: {value!r}')
            elif h not in na.get(name, set()):
                failures.append(f'{name}: {h} may not print n/a (pin file allows: {sorted(na.get(name, set())) or "nobody"})')
            continue
        answers[h] = value
    distinct = {}
    for h, v in answers.items():
        distinct.setdefault(v, []).append(h)
    if len(distinct) > 1:
        detail = '; '.join(f'{v!r} <- {" ".join(hs)}' for v, hs in distinct.items())
        failures.append(f'{name}: hosts disagree: {detail}')
    if name in pins:
        for v, hs in distinct.items():
            if v != pins[name]:
                failures.append(f'{name}: pinned {pins[name]!r}, {" ".join(hs)} print {v!r}')
    for h in na.get(name, set()):
        if h in hosts and h in answers:
            failures.append(f'{name}: {h} is listed as n/a but answers {answers[h]!r}')
    if not answers:
        failures.append(f'{name}: no host answers')

for name in pins:
    if name not in names:
        failures.append(f'api-pins.txt pins an unknown probe: {name}')

if failures:
    print(f'API CHECK FAILED ({len(failures)}):')
    for f in failures:
        print('  ' + f)
    sys.exit(1)
print(f'{len(names)} API probes, {" ".join(hosts)} agree on every one ({len(pins)} pinned)')
