#!/usr/bin/env node
// API parity probe — JavaScript.
//
// Every implementation runs the same numbered probes through its *own* binding
// and prints one `NN name = value` line each; tools/check-api.sh diffs the
// reports. Where the language layer is checked by conformance/, this checks the
// layer above it: that the four host APIs offer the same operations and give the
// same answers.
//
// It exists because nothing did. The conformance suite drives everything through
// compile().run() and compares dump(), and the e2e drivers only ever call
// asText() and dump() — so four hosts could drift arbitrarily in API shape and
// every layer stayed green. That is exactly what happened: the kind constants
// were exported in PHP and unreachable in JS, and a developer found it, not the
// harness.
//
// A probe belongs here when it is part of the host contract in spec/SPEC.md §8.
// Keep the four drivers in the same order with the same names; the diff is the
// whole mechanism.

const { compile, evaluate, Value, SelError, functionNames, NONE, TEXT, BIN, BOOL } =
  await import(process.env.SEL_JS_ENTRY ?? '../js/src/sel.mjs');

const out = [];
let n = 0;
const say = (name, value) => out.push(`${String(++n).padStart(2, '0')} ${name} = ${value}`);
const bool = (b) => (b ? 'true' : 'false');

// --- kind constants and predicates
say('kind.const.none', NONE);
say('kind.const.text', TEXT);
say('kind.const.bin', BIN);
say('kind.const.bool', BOOL);
say('kind.static.bool', Value.BOOL);
say('kind.of.text', evaluate('"x"').kind);
say('kind.of.bool', evaluate('TRUE').kind);
say('kind.of.none', evaluate('(1,2)').kind);
say('pred.isText', bool(evaluate('"x"').isText()));
say('pred.isBool', bool(evaluate('TRUE').isBool()));
say('pred.isNone', bool(evaluate('(1,2)').isNone()));
say('pred.isBin', bool(evaluate('TO_UTF8("x")').isBin()));
say('pred.isText.on.bool', bool(evaluate('TRUE').isText()));

// --- constructors
say('ctor.text', Value.text('hi').dump());
say('ctor.bool', Value.bool(true).dump());
say('ctor.none', Value.none().dump());
say('ctor.num.canonicalises', Value.num('007').dump());
say('ctor.int', Value.int(-3).dump());
say('ctor.list', Value.list([Value.text('a'), Value.text('b')]).dump());

// --- children, and the ordering rules
const v = Value.none();
v.set('b', Value.text('1'));
v.set('a', Value.text('2'));
say('children.size', String(v.size()));
// size() is a method in every host, not a getter in one of them. This probe is
// the reason it cannot quietly become one again.
say('children.size.is.callable', typeof v.size === 'function' ? 'true' : 'false');
say('children.keys', v.keys().join(','));
v.set('b', Value.text('9'));
say('children.reassign.keeps.position', v.keys().join(','));
say('children.reassign.no.growth', String(v.size()));
say('children.has', bool(v.has('a')));
say('children.has.missing', bool(v.has('zz')));
say('children.get', v.get('b').dump());

// --- scalar context
say('scalar.asText', evaluate('"héllo"').asText());
say('scalar.asBool', bool(evaluate('TRUE').asBool()));
say('scalar.takes.first.child', evaluate('(7,8)').asText());
say('scalar.looksNumeric', bool(evaluate('"2.50"').looksNumeric()));
say('scalar.looksNumeric.no', bool(evaluate('"x"').looksNumeric()));

// --- equality and dump
say('eql.same', bool(Value.text('5').eql(Value.text('5'))));
say('eql.not.normalised', bool(Value.text('5.00').eql(Value.text('5'))));
say('dump.tree', evaluate('A=1; A[2]="x"; A').dump());

// --- programs
const p = compile('IF(A > B, A, C)');
say('program.dependencies', p.dependencies().join(' '));
say('program.deps.excludes.assigned', compile('X = 1; X + Y').dependencies().join(' '));
say('program.deps.excludes.binder', compile('ALL(I, IT, IT > 0)').dependencies().join(' '));
const ctx = Value.none();
ctx.set('TOTAL', Value.num('59.97'));
say('program.run.reads.context', evaluate('TOTAL > 10.00', ctx).dump());
evaluate('SEEN = TOTAL * 2', ctx);
say('program.run.mutates.context', ctx.get('SEEN').asText());
say('registry.count', String(functionNames().length));
say('registry.sorted.first', functionNames()[0]);

// --- errors
try {
  evaluate('1 +\n  X');
} catch (e) {
  say('error.code', e.code);
  say('error.line', String(e.line));
  say('error.col', String(e.col));
  say('error.isSelError', bool(e instanceof SelError));
}
try {
  compile('NOPE(1)');
} catch (e) {
  say('error.compile.unknown.func', e.code);
}
try {
  Value.num('x');
} catch (e) {
  say('error.host.badnum', e.code);
}

process.stdout.write(out.join('\n') + '\n');
