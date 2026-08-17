#!/usr/bin/env php
<?php
// API parity probe — PHP. See tools/api.mjs for what this is and why.
// The four drivers must stay in the same order with the same probe names; the
// diff between their reports is the whole mechanism.

declare(strict_types=1);
require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$out = [];
$n = 0;
function say(string $name, string $value): void
{
    global $out, $n;
    $out[] = sprintf('%02d %s = %s', ++$n, $name, $value);
}
$b = fn (bool $x): string => $x ? 'true' : 'false';

// --- kind constants and predicates
say('kind.const.none', Value::NONE);
say('kind.const.text', Value::TEXT);
say('kind.const.bin', Value::BIN);
say('kind.const.bool', Value::BOOL);
say('kind.static.bool', Value::BOOL);
say('kind.of.text', Sel::evaluate('"x"')->kind);
say('kind.of.bool', Sel::evaluate('TRUE')->kind);
say('kind.of.none', Sel::evaluate('(1,2)')->kind);
say('pred.isText', $b(Sel::evaluate('"x"')->isText()));
say('pred.isBool', $b(Sel::evaluate('TRUE')->isBool()));
say('pred.isNone', $b(Sel::evaluate('(1,2)')->isNone()));
say('pred.isBin', $b(Sel::evaluate('TO_UTF8("x")')->isBin()));
say('pred.isText.on.bool', $b(Sel::evaluate('TRUE')->isText()));

// --- constructors
say('ctor.text', Value::text('hi')->dump());
say('ctor.bool', Value::bool(true)->dump());
say('ctor.none', Value::none()->dump());
say('ctor.num.canonicalises', Value::num('007')->dump());
say('ctor.int', Value::int(-3)->dump());
say('ctor.list', Value::list([Value::text('a'), Value::text('b')])->dump());

// --- children, and the ordering rules
$v = Value::none();
$v->set('b', Value::text('1'));
$v->set('a', Value::text('2'));
say('children.size', (string) $v->size());
say('children.size.is.callable', $b(is_callable([$v, 'size'])));
say('children.keys', implode(',', $v->keys()));
$v->set('b', Value::text('9'));
say('children.reassign.keeps.position', implode(',', $v->keys()));
say('children.reassign.no.growth', (string) $v->size());
say('children.has', $b($v->has('a')));
say('children.has.missing', $b($v->has('zz')));
say('children.get', $v->get('b')->dump());

// --- scalar context
say('scalar.asText', Sel::evaluate('"héllo"')->asText());
say('scalar.asBool', $b(Sel::evaluate('TRUE')->asBool()));
say('scalar.takes.first.child', Sel::evaluate('(7,8)')->asText());
say('scalar.looksNumeric', $b(Sel::evaluate('"2.50"')->looksNumeric()));
say('scalar.looksNumeric.no', $b(Sel::evaluate('"x"')->looksNumeric()));

// --- equality and dump
say('eql.same', $b(Value::text('5')->eql(Value::text('5'))));
say('eql.not.normalised', $b(Value::text('5.00')->eql(Value::text('5'))));
say('dump.tree', Sel::evaluate('A=1; A[2]="x"; A')->dump());

// --- programs
$p = Sel::compile('IF(A > B, A, C)');
say('program.dependencies', implode(' ', $p->dependencies()));
say('program.deps.excludes.assigned', implode(' ', Sel::compile('X = 1; X + Y')->dependencies()));
say('program.deps.excludes.binder', implode(' ', Sel::compile('ALL(I, IT, IT > 0)')->dependencies()));
$ctx = Value::none();
$ctx->set('TOTAL', Value::num('59.97'));
say('program.run.reads.context', Sel::evaluate('TOTAL > 10.00', $ctx)->dump());
Sel::evaluate('SEEN = TOTAL * 2', $ctx);
say('program.run.mutates.context', $ctx->get('SEEN')->asText());
say('registry.count', (string) count(Sel::functionNames()));
say('registry.sorted.first', Sel::functionNames()[0]);

// --- errors
try {
    Sel::evaluate("1 +\n  X");
} catch (SelError $e) {
    say('error.code', $e->code);
    say('error.line', (string) $e->line);
    say('error.col', (string) $e->col);
    say('error.isSelError', $b($e instanceof SelError));
}
try {
    Sel::compile('NOPE(1)');
} catch (SelError $e) {
    say('error.compile.unknown.func', $e->code);
}
try {
    Value::num('x');
} catch (SelError $e) {
    say('error.host.badnum', $e->code);
}

echo implode("\n", $out), "\n";
