<?php
declare(strict_types=1);
require __DIR__ . '/../php/src/bootstrap.php';
use Sel\Dec;
use Sel\Value;
use Sel\SelError;
$checks = 0;
function verify(bool $ok, string $label): void {
    global $checks;
    $checks++;
    if (!$ok) throw new RuntimeException($label);
}
$pos = ['line'=>3, 'col'=>7, 'offset'=>12];
function errorAt(callable $call, string $code): void {
    global $pos;
    try { $call(); } catch (SelError $e) {
        verify($e->code === $code && $e->line === $pos['line'] &&
            $e->col === $pos['col'] && $e->offset === $pos['offset'], $code);
        return;
    }
    verify(false, 'missing '.$code);
}
$first = Value::text('first');
$ordinary = Value::none();
for ($i=0; $i<10000; $i++) $ordinary->set('k'.$i, $first);
foreach ([$ordinary, Value::list([$first, Value::text('second')]),
          Value::record(['a','b'], [$first, Value::text('second')]),
          Value::list([Value::list([$first])])] as $v) {
    verify($v->scalarSource() === $first, 'first child identity');
}
errorAt(fn()=>Value::null()->asText($pos), 'E_NULL');
errorAt(fn()=>Value::list([])->asText($pos), 'E_NO_SCALAR');
$deep = $first;
for ($i=0; $i<1001; $i++) $deep = Value::list([$deep]);
errorAt(fn()=>$deep->asText($pos), 'E_DEPTH');
foreach ([
    ['add','9223372036854775807','1','9223372036854775808'],
    ['sub','-9223372036854775808','1','-9223372036854775809'],
    ['mul','-9223372036854775808','-1','9223372036854775808'],
    ['mul','9223372036854775807','2','18446744073709551614'],
    ['add','0.01','1.001','1.011'],
    ['mul','-0.00','2','0.00'],
    ['div','1','8','0.125'],
] as [$op,$a,$b,$want]) {
    verify(Dec::format(Dec::$op(Dec::parse($a),Dec::parse($b))) === $want, "$op $a $b");
}
verify(Dec::format(Dec::fromInt(PHP_INT_MIN)) === (string)PHP_INT_MIN, 'native minimum');
$legacy = ['neg'=>false,'digits'=>'123','scale'=>2];
verify(Dec::format(Dec::add($legacy, Dec::parse('1'))) === '2.23', 'legacy descriptor');
$changed = Dec::parse('1.23'); $changed['digits'] = '456';
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '5.56', 'mutated digits');
$changed['neg'] = true;
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '-3.56', 'mutated sign');
$changed['scale'] = 3;
verify(Dec::format(Dec::add($changed, Dec::parse('1'))) === '0.544', 'mutated scale');
errorAt(fn()=>Dec::div(Dec::fromInt(1),Dec::zero(),$pos), 'E_DIV_ZERO');
errorAt(fn()=>Dec::round(Dec::fromInt(1),Dec::MAX_FRAC_DIGITS+1,$pos), 'E_RANGE');
// Numeric join keys are one key per number whichever path builds them: a
// cached decimal (Value::num) against a text nobody has parsed yet, in a fresh
// process, before and after an equality parses the text, and in both orders.
$join = \Sel\Sel::compile('LINK(L, R, _1["id"] == _2["id"])');
$equal = \Sel\Sel::compile('L[1]["id"] == R[1]["id"]');
foreach ([['1.00','1.00'],['1.50','1.5'],['-0.0','0'],['0','-0'],['7','007'],['0.5','.5'],
          ['123456789012345678901.50','123456789012345678901.5'],
          ['-123456789012345678901','-123456789012345678901.0']] as [$num, $text]) {
    if (Dec::parse($text) === null) continue; // not every spelling above is a SEL number
    foreach ([false, true] as $swap) {
        $ctx = Value::none();
        $a = Value::list([Value::record(['id'], [Value::num($num)])]);
        $b = Value::list([Value::record(['id'], [Value::text($text)])]);
        $ctx->set('L', $swap ? $b : $a);
        $ctx->set('R', $swap ? $a : $b);
        $cold = $join->run($ctx)->size();
        $eq = $equal->run($ctx)->asBool();
        $warm = $join->run($ctx)->size();
        verify($cold === 1 && $eq && $warm === 1, "join key $num/$text cold=$cold warm=$warm");
    }
}

// --- the host boundary (spec/SPEC.md §8, review 2026-09-25) ------------------
// Collected, so one run reports every broken contract.
$boundary = [];
$expect = function (string $name, callable $fn) use (&$boundary): void {
    global $checks; $checks++;
    try { $r = $fn(); if ($r !== true) $boundary[] = "$name: got " . var_export($r, true); }
    catch (\Throwable $e) { $boundary[] = "$name: threw " . ($e instanceof SelError ? $e->code : get_class($e)) . ' ' . $e->getMessage(); }
};
$code = function (callable $fn): string {
    try { $fn(); return 'no error'; } catch (SelError $e) { return $e->code; }
};
$run = fn(string $src, array $ctx = []) => \Sel\Sel::compile($src)->run($ctx);
// HOST-01: a scalar with a child named "_" has no native form.
$expect('toNative refuses a scalar with a child named _', fn() =>
    $code(fn() => $run('A = "s"; A["_"] = "c"; A')->toNative()) === 'E_BAD_ARG');
// HOST-05: every text entering is checked, keys included.
$expect('Value::text rejects malformed UTF-8', fn() => $code(fn() => Value::text("\xFF")) === 'E_UTF8');
$expect('fromNative rejects malformed UTF-8', fn() => $code(fn() => Value::fromNative("\xFF")) === 'E_UTF8');
$expect('fromNative rejects a malformed key', fn() => $code(fn() => Value::fromNative(["\xFF" => 'x'])) === 'E_UTF8');
$expect('Value::set rejects a malformed key', fn() => $code(fn() => Value::none()->set("\xC3", Value::text('x'))) === 'E_UTF8');
$expect('a supplementary character is text', fn() => Value::text("\u{1F600}")->dump() === "t\"\u{1F600}\"");
// HOST-08 / HOST-09: toNative and fromNative are inverses, except the one
// spec/SPEC.md §8 names: a record keyed "0" … "n-1" is a PHP list.
foreach (['FILTER(LIST(1,2,3), _ > 1)', 'RECORD("5","a","9","b")', 'FALSE', 'RECORD("a", FALSE)', 'LIST(TRUE, NULL)',
          'RECORD("1x","a","1y","b")'] as $src) {
    $expect("round trip of $src", function () use ($run, $src) {
        $v = $run($src); return Value::fromNative($v->toNative())->dump() === $v->dump();
    });
}
$expect('a record keyed 0, 1 comes back as a list keyed 1, 2 (the named exception)', fn() =>
    Value::fromNative($run('RECORD("0","a","1","b")')->toNative())->dump() === '-{"1"=t"a", "2"=t"b"}');
// SEM-09: fromEntries keeps keys that only look numeric.
$expect('fromEntries keeps "1x", "2" as keys of a list', fn() =>
    Value::fromEntries([['1x', Value::text('a')], ['2', Value::text('b')]], true)->dump() === '-{"1x"=t"a", "2"=t"b"}');
$expect('fromEntries keeps "01" as the first key', fn() =>
    Value::fromEntries([['01', Value::text('a')]], true)->dump() === '-{"01"=t"a"}');
// HOST-07 control: an over-deep host value cannot be hashed any more than dumped.
$deepValue = function (int $levels): Value { $v = Value::text('x'); for ($i = 0; $i < $levels; $i++) $v = Value::list([$v]); return Value::list([$v]); };
foreach (['COUNT(DEDUPE(A))', 'COUNT(DISTINCT(A))', 'COUNT(BUCKET(A, _, COUNT(_)))'] as $src) {
    $expect("$src over a value nested past the cap", fn() => $code(fn() => $run($src, ['A' => $deepValue(250)])) === 'E_DEPTH');
}
// HOST-10 control: a compiled program keeps nothing from one run to the next.
$expect('a compiled program reads the key of each run', function () {
    $p = \Sel\Sel::compile('A[K]'); $A = Value::fromNative(['x' => '1', 'y' => '2']);
    return $p->run(['A' => $A, 'K' => 'x'])->dump() . $p->run(['A' => $A, 'K' => 'y'])->dump() === 't"1"t"2"';
});
// --- every public constructor (spec/SPEC.md §8, review 2026-09-28 HOST-12..20) --
$one = Value::text('1'); $two = Value::text('2');
// HOST-12: keys given side by side are checked like any other text.
foreach ([
    'shaped' => fn() => Value::shaped(["a\xff"], [$one]),
    'record' => fn() => Value::record(["a\xff"], [$one]),
    'fromEntries' => fn() => Value::fromEntries([["a\xff", $one]]),
    'list with keys' => fn() => Value::list([$one], ["a\xff"]),
    'RecordShape::intern' => fn() => Value::fromShape(\Sel\RecordShape::intern(["a\xff"]), [$one]),
    'the first row of fromNativeRows' => fn() => Value::fromNativeRows([["a\xff" => 1]]),
] as $what => $f) {
    $expect("$what rejects an invalid UTF-8 key", fn() => $code($f) === 'E_UTF8');
}
// HOST-13 / HOST-14: the decimal form is a number within the caps, canonical.
$expect('num of a decimal with 1,000,001 fractional digits is E_RANGE', fn() =>
    $code(fn() => Value::num(['neg' => false, 'digits' => '1', 'scale' => 1000001])) === 'E_RANGE');
$expect('num of a decimal with 1,000,001 integer digits is E_RANGE', fn() =>
    $code(fn() => Value::num(['neg' => false, 'digits' => str_repeat('1', 1000001), 'scale' => 0])) === 'E_RANGE');
$expect('num of a decimal canonicalises -0 and leading zeros', fn() =>
    Value::num(['neg' => true, 'digits' => '000', 'scale' => 0])->dump() . Value::num(['neg' => false, 'digits' => '007', 'scale' => 1])->dump() === 't"0"t"0.7"');
foreach ([['neg' => false, 'digits' => 'x', 'scale' => 0], ['neg' => false, 'digits' => '7', 'scale' => -1],
          ['neg' => false, 'digits' => '', 'scale' => 0], ['digits' => '1', 'scale' => 0], 5] as $bad) {
    $expect('num of the malformed decimal ' . json_encode($bad) . ' is E_BAD_ARG', fn() => $code(fn() => Value::num($bad)) === 'E_BAD_ARG');
}
// HOST-17: keys and values pair up.
foreach ([
    'shaped' => fn() => Value::shaped(['a'], [$one, $two]),
    'record' => fn() => Value::record(['a', 'b'], [$one]),
    'fromShape' => fn() => Value::fromShape(\Sel\RecordShape::intern(['a']), []),
    'list with keys' => fn() => Value::list([$one], ['1', '2']),
] as $what => $f) {
    $expect("$what with counts that differ is E_BAD_ARG", fn() => $code($f) === 'E_BAD_ARG');
}
// HOST-18: a repeated key is RECORD's last write in its first position; a list's is refused.
$expect('shaped keeps a repeated key once', fn() =>
    Value::shaped(['a', 'b', 'a'], [$one, $two, $two])->dump() === '-{"a"=t"2", "b"=t"2"}');
$expect('a list with a repeated key is E_BAD_ARG', fn() => $code(fn() => Value::list([$one, $two], ['5', '5'])) === 'E_BAD_ARG');
$expect('a shape with a repeated key is E_BAD_ARG', fn() => $code(fn() => \Sel\RecordShape::intern(['a', 'a'])) === 'E_BAD_ARG');
// HOST-20: a malformed call is E_BAD_ARG, never the host's own exception.
foreach ([
    'fromNative(1.5)' => fn() => Value::fromNative(1.5),
    'fromNative(an object)' => fn() => Value::fromNative(new \stdClass()),
    'list(["1"])' => fn() => Value::list(['1']),
    'record(["a"], ["1"])' => fn() => Value::record(['a'], ['1']),
] as $what => $f) {
    $expect("$what is E_BAD_ARG", fn() => $code($f) === 'E_BAD_ARG');
}
if ($boundary) {
    fwrite(STDERR, 'PHP runtime: ' . count($boundary) . " host-boundary contract(s) broken:\n  " . implode("\n  ", $boundary) . "\n");
    exit(1);
}
echo "PHP runtime: $checks checks passed\n";
