#!/usr/bin/env python3
"""API parity probe — Python.

Every implementation runs the same numbered probes through its *own* binding and
prints one `NN name = value` line each; tools/check-api.sh diffs the reports.
Where the language layer is checked by conformance/, this checks the layer above
it: that the host APIs offer the same operations and give the same answers.

The probe *names* are the contract, not the method spellings — this host is
snake_case, as the C++ one is, while JS and PHP are camelCase and Lisp is
value-text-p. Keep the drivers in the same order with the same names; the diff
is the whole mechanism.
"""

import os
import sys

# Prefer an *installed* sel over the source tree, so the python-wheel
# implementation in tools/impls.sh actually exercises the built package rather
# than silently re-testing python/sel through a path insert. Falls back to the
# source tree when nothing is installed, which is how the plain `python`
# implementation and a bare checkout run.
try:
    import sel as _sel_probe                                    # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from sel import (BIN, BOOL, NONE, TEXT, SelError, Value,      # noqa: E402
                 compile as sel_compile, evaluate, function_names)

out = []
_n = [0]


def say(name, value):
    _n[0] += 1
    out.append(f'{_n[0]:02d} {name} = {value}')


def b(x):
    return 'true' if x else 'false'


# --- kind constants and predicates
say('kind.const.none', NONE)
say('kind.const.text', TEXT)
say('kind.const.bin', BIN)
say('kind.const.bool', BOOL)
say('kind.static.bool', Value.BOOL)
say('kind.of.text', evaluate('"x"').kind)
say('kind.of.bool', evaluate('TRUE').kind)
say('kind.of.none', evaluate('(1,2)').kind)
say('pred.isText', b(evaluate('"x"').is_text()))
say('pred.isBool', b(evaluate('TRUE').is_bool()))
say('pred.isNone', b(evaluate('(1,2)').is_none()))
say('pred.isBin', b(evaluate('TO_UTF8("x")').is_bin()))
say('pred.isText.on.bool', b(evaluate('TRUE').is_text()))

# --- constructors
say('ctor.text', Value.text('hi').dump())
say('ctor.bool', Value.bool(True).dump())
say('ctor.none', Value.none().dump())
say('ctor.num.canonicalises', Value.num('007').dump())
say('ctor.int', Value.int(-3).dump())
say('ctor.list', Value.list([Value.text('a'), Value.text('b')]).dump())

# --- children, and the ordering rules
v = Value.none()
v.set('b', Value.text('1'))
v.set('a', Value.text('2'))
say('children.size', str(v.size()))
# size() is a method in every host, not a property in one of them. This probe is
# the reason it cannot quietly become one again.
say('children.size.is.callable', b(callable(v.size)))
say('children.keys', ','.join(v.keys()))
v.set('b', Value.text('9'))
say('children.reassign.keeps.position', ','.join(v.keys()))
say('children.reassign.no.growth', str(v.size()))
say('children.has', b(v.has('a')))
say('children.has.missing', b(v.has('zz')))
say('children.get', v.get('b').dump())

# --- scalar context
say('scalar.asText', evaluate('"héllo"').as_text())
say('scalar.asBool', b(evaluate('TRUE').as_bool()))
say('scalar.takes.first.child', evaluate('(7,8)').as_text())
say('scalar.looksNumeric', b(evaluate('"2.50"').looks_numeric()))
say('scalar.looksNumeric.no', b(evaluate('"x"').looks_numeric()))

# --- equality and dump
say('eql.same', b(Value.text('5').eql(Value.text('5'))))
say('eql.not.normalised', b(Value.text('5.00').eql(Value.text('5'))))
say('dump.tree', evaluate('A=1; A[2]="x"; A').dump())

# --- programs
p = sel_compile('IF(A > B, A, C)')
say('program.dependencies', ' '.join(p.dependencies()))
say('program.deps.excludes.assigned', ' '.join(sel_compile('X = 1; X + Y').dependencies()))
say('program.deps.excludes.binder', ' '.join(sel_compile('ALL(I, IT, IT > 0)').dependencies()))
ctx = Value.none()
ctx.set('TOTAL', Value.num('59.97'))
say('program.run.reads.context', evaluate('TOTAL > 10.00', ctx).dump())
evaluate('SEEN = TOTAL * 2', ctx)
say('program.run.mutates.context', ctx.get('SEEN').as_text())
say('registry.count', str(len(function_names())))
say('registry.sorted.first', function_names()[0])

# --- errors
try:
    evaluate('1 +\n  X')
except SelError as e:
    say('error.code', e.code)
    say('error.line', str(e.line))
    say('error.col', str(e.col))
    say('error.isSelError', b(isinstance(e, SelError)))
try:
    sel_compile('NOPE(1)')
except SelError as e:
    say('error.compile.unknown.func', e.code)
try:
    Value.num('x')
except SelError as e:
    say('error.host.badnum', e.code)
# Every character is a digit, so this is E_RANGE and not E_NOT_NUM. Value.num is
# public API, so an embedding application can reach the numeral cap without
# compiling a rule at all -- and all six hosts must refuse it the same way.
try:
    Value.num('1' * 2000001)
except SelError as e:
    say('error.host.hugenum', e.code)

sys.stdout.reconfigure(encoding='utf-8', newline='\n')
sys.stdout.write('\n'.join(out) + '\n')
