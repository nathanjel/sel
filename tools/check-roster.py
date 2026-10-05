#!/usr/bin/env python3
"""Fails on a stale host roster anywhere in the tracked tree.

SEL grew from two hosts to seven, and every step left sentences behind that
counted the hosts of the day: "all five", "the other four hosts", "both hosts",
a contributor map with five columns, package descriptions naming five
languages. Each was true when written and none of them failed anything when
it stopped being true, so they accumulated (a review found 188 of them). The
cure is count-free wording -- "every host", "the other hosts" -- and this
check keeps it that way:

  1. A COUNTED ROSTER is refused in every tracked text file: "all five", "the
     other four hosts", "the three implementations", "both hosts", "five
     languages", "implemented five times", "a fifth implementation". Counts of
     other things (dialects, servers, drivers, regex engines, lanes) are not
     rosters and are not matched. History may still be told ("three hosts
     answered where two died"); it just cannot use these phrases.
  2. A HOST LIST that misses a host is refused in the documentation and the
     package manifests: a Markdown paragraph or table row that names JS, PHP,
     Python, C++ and Lisp but not Go and Rust.

Out of scope: CHANGELOG.md (history by definition), the shared test data
(.selt, .sqlt, .selo, sql/oracle/, JSON data, whose notes record what hosts
did at the time), and the files generated from them (the map and case
renderings), which follow their sources.

    python3 tools/check-roster.py            # the whole tree
    python3 tools/check-roster.py FILE...    # only these files
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COUNT = r'(?:two|three|four|five|six)'
ROSTER_NOUNS = (r'(?:hosts?|implementations?|languages|parsers|lexers|readers|renderings|cores|'
                r'runners|evaluators|optimi[sz]ers|planners|translators|ports|hosts\'|host APIs)')
COUNTED = [
    # "all five hosts", "the other four implementations", "the five readers"
    # ("the two hosts whose engine follows Perl" names a subset, not a roster)
    re.compile(rf'\b(?:all|the other|the|other) (?:three|four|five|six) {ROSTER_NOUNS}\b', re.I),
    # "four hosts", "five languages" -- not two/three, which history uses
    re.compile(rf'\b(?:four|five|six) (?:hosts|languages|implementations)\b', re.I),
    # "all five", "the other four" standing alone ("all four" counts flags,
    # steps and places too often to be refused)
    re.compile(r'\ball (?:five|six)\b(?! (?:dialects?|servers|databases|SQL dialects|of the dialects|forms|kinds|steps|lanes|places|cases|layers|targets|scenarios)\b)', re.I),
    re.compile(r'\b(?:the )?other (?:four|five|six)\b(?! (?:dialects?|servers|databases|forms|kinds|lanes|places|cases)\b)', re.I),
    re.compile(r'\bboth hosts\b', re.I),
    re.compile(r'\b(?:fifth|sixth|seventh) (?:implementation|host)\b', re.I),
    re.compile(rf'\bimplemented \**{COUNT} times\b', re.I),
]

HOSTS_OLD = {
    'JS': re.compile(r'\b(?:JS|JavaScript)\b'),
    'PHP': re.compile(r'\bPHP\b'),
    'Python': re.compile(r'\bPython\b'),
    'C++': re.compile(r'C\+\+'),
    'Lisp': re.compile(r'\bLisp\b'),
}
HOSTS_NEW = {
    'Go': re.compile(r'\bGo\b'),
    'Rust': re.compile(r'\bRust\b'),
}

EXCLUDED_SUFFIXES = ('.selt', '.sqlt', '.selo', '.json', '.sel', '.svg', '.png', '.ico', '.lock', '.sum')
EXCLUDED_PREFIXES = ('sql/oracle/', 'docs/interim/', 'cpp/third_party/srell/srell', 'tools/scale-test/')
EXCLUDED_FILES = {
    'CHANGELOG.md', 'tools/check-roster.py',
    # renderings of the dialect map and the sqlt cases: they follow sql/dialects/*.json and sql/cases/
    'js/src/sql/_map.mjs', 'python/sel/sql/_map.py', 'php/src/Sql/MapData.php', 'cpp/sel_sql_map_data.cpp',
    'lisp/src/sql/map-data.lisp', 'go/sel/sql/map_data_gen.go', 'rust/src/sql/map_data.rs',
    'js/bin/map-replay.mjs', 'python/bin/map_replay.py', 'php/bin/MapReplay.php', 'cpp/bin/map_replay.cpp',
    'cpp/bin/map_replay.hpp', 'lisp/bin/map-replay.lisp', 'go/bin/sqlreplay/replay_data_gen.go',
    'rust/dev/src/bin/map_replay_data.rs',
    'js/bin/case-data.mjs', 'python/bin/case_data.py', 'php/bin/CaseData.php', 'cpp/bin/case_data.cpp',
    'lisp/bin/case-data.lisp',
}
MANIFESTS = {'package.json', 'composer.json', 'pyproject.toml', 'rust/Cargo.toml', 'cpp/conanfile.py',
             'cpp/vcpkg.json', 'lisp/sel-lang.asd'}


def in_scope(path):
    if path in EXCLUDED_FILES or path.startswith(EXCLUDED_PREFIXES):
        return False
    if path.endswith(EXCLUDED_SUFFIXES) and path not in MANIFESTS:
        return False
    return True


def paragraphs(lines):
    """Markdown blocks outside code fences: (first line number, [lines]); a
    table row is a block of its own, so a header row is judged by itself."""
    block, start, fenced = [], 0, False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith('```'):
            fenced = not fenced
            if block:
                yield start, block
            block = []
            continue
        if fenced:
            continue
        if line.lstrip().startswith('|'):
            if block:
                yield start, block
            block = []
            yield n, [line]
            continue
        if not line.strip():
            if block:
                yield start, block
            block = []
            continue
        if not block:
            start = n
        block.append(line)
    if block:
        yield start, block


def check(path, text):
    problems = []
    lines = text.split('\n')
    for n, line in enumerate(lines, 1):
        for rx in COUNTED:
            m = rx.search(line)
            if m:
                problems.append(f'{path}:{n}: counted roster "{m.group(0)}" -- say "every host" / "the other hosts"')
                break
    if path.endswith('.md') or path in MANIFESTS:
        blocks = paragraphs(lines) if path.endswith('.md') else [(1, lines)]
        for n, block in blocks:
            body = ' '.join(block)
            if all(rx.search(body) for rx in HOSTS_OLD.values()):
                # A host's own README names itself "this module" / "this crate".
                missing = [h for h, rx in HOSTS_NEW.items()
                           if not rx.search(body) and not path.startswith(h.lower() + '/')]
                if missing:
                    problems.append(f'{path}:{n}: a host list without {" and ".join(missing)}')
    return problems


def main(argv):
    if argv:
        paths = argv
    else:
        out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
        paths = [p for p in out.stdout.decode('utf-8').split('\0') if p]
    problems = []
    checked = 0
    for path in paths:
        if not in_scope(path):
            continue
        try:
            text = (ROOT / path).read_text(encoding='utf-8')
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        checked += 1
        problems += check(path, text)
    for p in problems:
        print(p)
    if problems:
        print(f'roster: {len(problems)} stale host count(s) or list(s) in {checked} files', file=sys.stderr)
        return 1
    print(f'roster: {checked} files name every host or none')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
