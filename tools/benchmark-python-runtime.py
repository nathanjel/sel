#!/usr/bin/env python3
"""Focused runtime benchmark; select a checkout with PYTHONPATH=... .

Compile/parse and context construction are outside timed regions. Results are
microseconds per call (median of five repeats), not whole-application speedups.
"""
import json
import platform
import statistics
import sys
import timeit

from sel import Value, compile
from sel import decimal as D
from sel.eval import Context, eval_node, _eval_math_plan
from sel.math_plan import compile_math_plan
from sel.utf8 import decode_utf8

results = {}

def measure(name, fn, count=20000):
    fn()
    samples = [v * 1e6 / count for v in timeit.repeat(fn, number=count, repeat=5)]
    results[name] = {'median_us': statistics.median(samples), 'samples_us': samples}

for label, text in [('ascii', 'hello world ' * 1000),
                    ('unicode', 'Zażółć gęślą jaźń ' * 1000)]:
    data = text.encode()
    assert decode_utf8(data) == text
    measure('decode.' + label, lambda: decode_utf8(data), 50)

pairs = [('small', '123.45', '67.89'),
         ('mixed_sign', '-123.45', '67.89'),
         ('negative', '-123.45', '-67.89'),
         ('unequal_scale', '123.45', '0.6789'),
         ('mixed_scale', '-123.45', '0.6789'),
         ('large', '123456789012345678901234567890.12', '-98765432109876543210.9876')]
for label, a, b in pairs:
    a, b = D.parse(a), D.parse(b)
    for name in ['add', 'sub', 'mul', 'div', 'cmp']:
        op = getattr(D, name)
        measure('decimal.' + label + '.' + name, lambda: op(a, b))
measure('decimal.construct', lambda: D.make(False, 12345, 2))

root = Value.from_native({'A': '123.45', 'B': '67.89', 'C': '2.50'})
for label, source in [('simple', 'A * B'),
                      ('chain', '(A + B) * C - A / C'),
                      ('builtins', 'ROUND(ABS(A - B) / C, 2)'),
                      ('compare', 'A > B')]:
    program = compile(source)
    tree = program.ast
    context = Context(root)
    plan = compile_math_plan(tree)
    expected = eval_node(tree, context).dump()
    measure('ast.' + label, lambda: eval_node(tree, context), 5000)
    if plan:
        tree.math_plan = plan
        assert eval_node(tree, context).dump() == expected
        measure('plan.' + label, lambda: eval_node(tree, context), 5000)
    program.run(root)
    measure('program.' + label, lambda: program.run(root), 5000)
# Experimental fusion lower bound for exactly A * B. This is a benchmark
# probe, not a production evaluator: it keeps both variable checks and source
# positions, but has no opcode loop or scratchpad. Compare with plan.simple.
from sel.errors import fail
simple = compile('A * B').ast
fusion_context = Context(root)
def fused_multiply():
    left = fusion_context.lookup('A')
    if left is None:
        fail('E_UNDEF_VAR', 'undefined variable A', simple.l.pos)
    left_dec = left.as_decimal(simple.l.pos)
    right = fusion_context.lookup('B')
    if right is None:
        fail('E_UNDEF_VAR', 'undefined variable B', simple.r.pos)
    right_dec = right.as_decimal(simple.r.pos)
    return Value.num(D.mul(left_dec, right_dec, simple.pos))
assert fused_multiply().dump() == eval_node(simple, fusion_context).dump()
measure('pilot.fused_multiply', fused_multiply, 5000)
simple_plan = compile_math_plan(simple)
measure('pilot.plan_multiply', lambda: _eval_math_plan(simple_plan, fusion_context), 5000)

print(json.dumps({'python': sys.version, 'platform': platform.platform(),
                  'decimal_bytes': sys.getsizeof(D.parse('1.23')),
                  'results': results}, indent=2))
