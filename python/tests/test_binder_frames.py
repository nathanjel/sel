"""A Context counts the names its pushed frames bind, so a name no
frame binds is read from the root without walking them. lookup and is_bound
must answer exactly as a walk of the frames does; no frame may change its names
while it is pushed (the count would go stale); and a body that raises leaves
nothing bound."""
import random
import re
import runpy
import sys
from pathlib import Path

import pytest

from sel import SelError, Value, compile, evaluate, function_names
from sel.eval import Context, eval_node
from sel.registry import lookup

ROOT = Path(__file__).resolve().parents[2]


def load_conformance_runner():
    # The runner imports its sibling `_harness` by bare name, as a script does;
    # its read_text (bytes, no newline translation) is in the returned globals.
    bin_dir = str(ROOT / 'python/bin')
    if bin_dir not in sys.path:
        sys.path.append(bin_dir)
    return runpy.run_path(str(ROOT / 'python/bin/conformance.py'))


NAMES = ('_', '_K', 'X', 'Y', 'R')
CONTEXT = {'L': [3, 1, 2], 'R': [{'k': 1}, {'k': 2}], 'S': [{'k': 2}]}
# Every builtin that pushes a frame, in the forms that bind.
BINDING = [
    'MAP(L, X, X * 2)', 'MAP(L, _ + _K)', 'L .> FILTER(_ > 1)', 'ALL(L, X, X > 0)', 'ANY(L, _ > 1)',
    'SORT(L)', 'L .> SORT_BY(_K, "DESC")', 'L .> SORT_BY(X, X, "ASC") .> TAKE(1)',
    'TOP_BY(L, _, 2)', 'BUCKET(L, _ % 2)', 'BUCKET(L, X, X % 2, COUNT(X))',
    'R .> LINK(S, _1["k"] == _2["k"])', 'R .> LINK(S, r["k"] == s["k"]) .> FILTER(_["R"]["k"] > 0)',
    'LINK(R, S, A, B, A["k"] < B["k"])', 'R .> LINK_LEFT(S, R["k"] == S["k"])',
]

# What can push a frame: a call of a binding function (define(..., binds=True)),
# or a pipeline.
BINDS = re.compile(r'\.>|\b(' + '|'.join(n for n in function_names() if lookup(n).binds) + r')\s*\(', re.I)


def walk(ctx, name):
    for frame in reversed(ctx.frames):
        v = frame.get(name)
        if v is not None:
            return v
    return ctx.root.get(name)


def roots():
    loose = Value.from_native({'R': 1})
    loose.set('X', Value.text('x'))                  # its variables sit in `children`
    return [Value.from_native({'R': 1, 'X': 'x'}),   # a shaped record
            loose, Value.from_native([1, 2]), Value.none()]


def test_lookup_and_is_bound_answer_as_a_walk_of_the_frames():
    rnd = random.Random(20261001)
    for root in roots():
        for _ in range(500):
            ctx = Context(root)
            for _ in range(rnd.randrange(5)):
                names = rnd.sample(NAMES, rnd.randrange(1, 4))
                # A binder holds None before its first element; lookup passes it by.
                ctx.push_frame({n: None if rnd.random() < 0.3 else Value.text(n) for n in names})
            for name in NAMES + ('1', 'missing'):
                assert ctx.lookup(name) is walk(ctx, name), name
                assert ctx.is_bound(name) == any(name in f for f in ctx.frames), name
            while ctx.frames:
                ctx.pop_frame()
            assert not any(ctx.is_bound(n) for n in NAMES)


def test_a_body_that_raises_leaves_nothing_bound():
    for src in ('MAP(L, X, X / 0)', 'L .> SORT_BY(_ / 0, "ASC") .> TAKE(1)', 'BUCKET(L, X, X / 0)',
                'R .> LINK(S, _1["k"] / 0 == 1)', 'MAP(L, X, MAP(L, Y, Y / 0))'):
        ctx = Context(Value.from_native(CONTEXT))
        with pytest.raises(SelError):
            eval_node(compile(src).physical_ast(), ctx)
        assert ctx.frames == []
        assert not any(ctx.is_bound(n) for n in ('X', 'Y', '_', '_K', '_1', '_2', 'R', 'r', 'S', 's'))


def test_no_frame_changes_its_names_while_pushed(monkeypatch):
    pushed, changed = [], []
    push, pop = Context.push_frame, Context.pop_frame

    def recording_push(self, mapping):
        pushed.append(tuple(mapping))
        push(self, mapping)

    def checking_pop(self):
        if tuple(self.frames[-1]) != pushed.pop():
            changed.append(tuple(self.frames[-1]))
        pop(self)

    monkeypatch.setattr(Context, 'push_frame', recording_push)
    monkeypatch.setattr(Context, 'pop_frame', checking_pop)
    for src in BINDING:
        evaluate(src, CONTEXT)
    # And every conformance case that can push one (run_case reports, never raises).
    conformance = load_conformance_runner()
    for path in sorted((ROOT / 'conformance').glob('*.selt')):
        for case in conformance['parse_selt'](conformance['read_text'](str(path)), path.name):
            if BINDS.search(case['source']) or (case['setup'] and BINDS.search(case['setup'])):
                conformance['run_case'](case)
    assert not pushed and not changed, changed[:5]
