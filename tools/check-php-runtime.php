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
echo "PHP runtime: $checks checks passed\n";
