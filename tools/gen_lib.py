"""What the Python generators in tools/ share: the command line and the
write-or-check loop (the Node generators' twin is tools/gen-lib.mjs).

    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_lib
    check = gen_lib.gen_args('gen-x', USAGE)       # first, before any work
    ...
    gen_lib.write_or_check('gen-x', ROOT, [(rel, text), ...], check, 'python3 tools/gen-x.py')

No argument writes; `--check` compares and writes nothing; `--help`/`-h` prints
the usage and exits 0; anything else exits 2 having written nothing. Files are
read and written as UTF-8 with no newline translation, so a CR in a case
survives a regeneration and is not hidden from `--check`.
"""
import os
import sys


def gen_args(name, usage):
    args = sys.argv[1:]
    if '--help' in args or '-h' in args:
        sys.stdout.write(usage if usage.endswith('\n') else usage + '\n')
        sys.exit(0)
    for a in args:
        if a != '--check':
            sys.stderr.write(f'{name}: unexpected argument {a}\n' + (usage if usage.endswith('\n') else usage + '\n'))
            sys.exit(2)
    return '--check' in args


def write_or_check(name, root, outputs, check, rerun):
    """outputs: [(path relative to root, text)]. Exits 1 in check mode when one is stale."""
    stale = unchanged = 0
    for rel, text in outputs:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding='utf-8', newline='') as f:
                have = f.read()
        except FileNotFoundError:
            have = None
        if check:
            if have != text:
                print(f'{name}: {rel} is {"missing" if have is None else "stale"}', file=sys.stderr)
                stale += 1
        elif have == text:
            unchanged += 1
        else:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
            print(f'wrote {rel}')
    if check and stale:
        print(f'\nrun: {rerun}', file=sys.stderr)
        sys.exit(1)
    if not check and unchanged:
        print(f'{unchanged} artifact(s) already current')
