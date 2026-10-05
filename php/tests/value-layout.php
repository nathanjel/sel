#!/usr/bin/env php
<?php
// The PHP Value's physical layout: interned record shapes, packed list and
// record storage, prepared row ingestion, copy isolation, join row shapes, the
// cached decimal behind a public scalar. PHP-only internals, so these stay
// beside the PHP host instead of becoming a cross-language harness contract.
//
//   php php/tests/value-layout.php

declare(strict_types=1);

require_once __DIR__ . '/../src/bootstrap.php';

use Sel\Dec;
use Sel\Sel;
use Sel\Value;

$checks = 0;

/** @param bool $condition */
function check_layout(bool $condition, string $message): void
{
    global $checks;
    $checks++;
    if (!$condition) {
        fwrite(STDERR, "FAIL {$message}\n");
        exit(1);
    }
}

$record = Value::fromEntries([
    ['id', Value::int(1)],
    ['name', Value::text('one')],
]);
$sameShape = Value::fromEntries([
    ['id', Value::int(2)],
    ['name', Value::text('two')],
]);
check_layout($record->shape !== null && $record->shape === $sameShape->shape, 'record shape interning');
check_layout($record->shape?->keyMap['id'] === 0, 'record shape slot map');
check_layout($record->storage !== null && array_is_list($record->storage), 'record storage is packed');
check_layout($record->get('id')?->scalar === '1', 'record direct slot lookup');
check_layout($record->toNative() === ['id' => '1', 'name' => 'one'], 'shaped direct materialization');

$scalarWithChild = Value::int(7);
$scalarWithChild->set('label', Value::text('seven'));
check_layout($scalarWithChild->toNative() === ['_' => '7', 'label' => 'seven'], 'scalar-with-child materialization');
$duplicate = Value::record(['same', 'same'], [Value::text('first'), Value::text('last')]);
check_layout($duplicate->toNative() === ['same' => 'last'], 'fallback duplicate-key materialization');

$list = Value::list([$record, $sameShape]);
check_layout($list->storage !== null && array_is_list($list->storage), 'list storage is packed');
check_layout($list->get('1') === $record && $list->get('2') === $sameShape, 'list direct slot lookup');
check_layout($list->toNative() === [
    ['id' => '1', 'name' => 'one'],
    ['id' => '2', 'name' => 'two'],
], 'packed list direct materialization');
$listFallback = Value::list([Value::text('first')]);
$listFallback->set('2', Value::text('second'));
check_layout($listFallback->toNative() === ['first', 'second'], 'fallback list materialization');
$listSparse = Value::list([Value::text('first')]);
$listSparse->set('extra', Value::text('second'));
check_layout($listSparse->toNative() === [1 => 'first', 'extra' => 'second'], 'sparse list materialization');

$nativeRows = [
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'prepared_name' => 'two'],
];
$preparedRows = Value::fromNativeRows($nativeRows);
$genericRows = Value::fromNative($nativeRows);
check_layout($preparedRows->dump() === $genericRows->dump(), 'prepared ingestion preserves rows');
check_layout($preparedRows->storage[0]->shape === $preparedRows->storage[1]->shape,
    'prepared ingestion reuses homogeneous shape');
$mixedRows = Value::fromNativeRows([
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'other_name' => 'two'],
]);
$mixedGeneric = Value::fromNative([
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'other_name' => 'two'],
]);
check_layout($mixedRows->dump() === $mixedGeneric->dump(), 'heterogeneous ingestion falls back safely');
check_layout($mixedRows->storage[0]->shape !== null && $mixedRows->storage[1]->shape !== null
    && $mixedRows->storage[0]->shape !== $mixedRows->storage[1]->shape,
    'heterogeneous rows retain separate shapes');

$specialKeys = Value::fromNative([
    'b' => Value::text('first'),
    '01' => Value::text('numeric-looking'),
    "nul\0key" => Value::text('embedded-nul'),
    'a' => Value::text('last'),
]);
check_layout($specialKeys->keys() === ['b', '01', "nul\0key", 'a'], 'host key order and spelling');
check_layout($specialKeys->get("nul\0key")?->scalar === 'embedded-nul', 'embedded-NUL key lookup');

$copy = $record->copy();
$copy->set('id', Value::int(99));
check_layout($record->get('id')?->scalar === '1' && $copy->get('id')?->scalar === '99',
    'copy mutation isolation');
$nestedCopy = Value::record(['child'], [Value::record(['value'], [Value::text('original')])])->copy();
$nestedCopy->get('child')->set('value', Value::text('changed'));
check_layout($nestedCopy->get('child')->get('value')?->scalar === 'changed', 'nested copy mutation');
check_layout($record->get('id')?->scalar === '1', 'nested copy leaves source isolated');

$noMatch = Sel::compile(
    'LIST(RECORD("id", 1)) .> LINK(LIST(RECORD("id", 2)), _1["id"] == _2["id"])',
)->run();
check_layout($noMatch->isList && $noMatch->size() === 0, 'inner join no-match result');
$emptyRight = Sel::compile(
    'LIST(RECORD("id", 1)) .> LINK_LEFT(LIST(), _1["id"] == _2["id"])',
)->run();
check_layout($emptyRight->isList && $emptyRight->size() === 1
    && $emptyRight->toNative()[0]['_2'] === null, 'left join empty-right null row');
$heterogeneousJoin = Sel::compile(
    'LIST(RECORD("id", 1, "left", "a"), RECORD("id", 2, "left", "b", "extra", "x"))'
    . ' .> LINK(LIST(RECORD("id", 1, "right", "r"), RECORD("id", 2, "right", "s")), '
    . ' _1["id"] == _2["id"])',
)->run();
$heterogeneousNative = $heterogeneousJoin->toNative();
check_layout($heterogeneousNative[1]['_1']['extra'] === 'x', 'heterogeneous join fallback');
$aliasedJoin = Sel::compile(
    'LINK(LIST(RECORD("id", 1)), LIST(RECORD("id", 1)), LEFT, RIGHT, '
    . 'LEFT["id"] == RIGHT["id"])',
)->run();
check_layout($aliasedJoin->isList && $aliasedJoin->size() === 1, 'case-sensitive alias join');

$number = Value::num('123456789.25');
$number->asDecimal();
$decProperty = new ReflectionProperty(Value::class, 'decVal');
check_layout($decProperty->getValue($number) !== null, 'numeric value caches parsed decimal');
check_layout(Dec::format(Dec::add(Dec::parse((string) PHP_INT_MAX), Dec::fromInt(1)))
    === '9223372036854775808', 'native overflow uses exact decimal fallback');
check_layout(Dec::format(Dec::add(Dec::parse((string) PHP_INT_MIN), Dec::fromInt(-1)))
    === '-9223372036854775809', 'negative native overflow uses exact decimal fallback');

$source = 'LIST(RECORD("id", 1, "l", "a"), RECORD("id", 2, "l", "b"))'
    . ' .> LINK_LEFT(LIST(RECORD("id", 2, "r", "x"), RECORD("id", 3, "r", "y")),'
    . ' _1["id"] == _2["id"])';
$joined = Sel::compile($source)->run();
check_layout($joined->dump() === '-{"1"=-{"_1"=-{"id"=t"1", "l"=t"a"}, "_2"=-{"id"=-, "r"=-}, "l"=t"a"}, '
    . '"2"=-{"_1"=-{"id"=t"2", "l"=t"b"}, "_2"=-{"id"=t"2", "r"=t"x"}, '
    . '"l"=t"b", "r"=t"x"}}', 'join projector preserves Lisp output');
check_layout($joined->storage !== null && array_is_list($joined->storage), 'join result storage is packed');
check_layout($joined->storage[0]->shape !== null && $joined->storage[1]->shape !== null, 'join rows use shared shapes');
check_layout($joined->storage[0]->shape !== $joined->storage[1]->shape, 'left miss keeps null-row shape semantics');

// Explicit internal reads must preserve the public lazy scalar API and writes.
$lazy = Value::num('1.00');
check_layout(isset($lazy->scalar), 'lazy numeric scalar remains publicly set');
check_layout($lazy->getScalar() === '1.00' && $lazy->scalar === '1.00', 'getter/property preserve numeric scale');
$lazy->scalar = '2.50';
check_layout($lazy->decVal === null, 'public scalar write invalidates decimal cache');
check_layout(Dec::format($lazy->asDecimal()) === '2.50', 'decimal reparses public scalar write');
check_layout((new ReflectionProperty(Value::class, 'scalar'))->isPrivate(), 'backing scalar remains private');

foreach (['==', '$=='] as $operator) {
    $initial = $operator === '==' ? '1' : '1.00';
    $changed = $operator === '==' ? '2' : '2.00';
    $key = Value::num($initial);
    $ctx = Value::none();
    $ctx->set('L', Value::list([Value::record(['id'], [$key])]));
    $ctx->set('R', Value::list([Value::record(['id'], [Value::text($initial)])]));
    $join = Sel::compile('LINK(L, R, A, B, A["id"] ' . $operator . ' B["id"])');
    check_layout($join->run($ctx)->size() === 1, 'lazy join key '.$operator);
    $key->scalar = $changed;
    check_layout($join->run($ctx)->size() === 0, 'reused join observes scalar mutation '.$operator);
    $ctx->get('R')->get('1')->get('id')->scalar = $changed;
    check_layout($join->run($ctx)->size() === 1, 'join sees updated matching key '.$operator);
}
check_layout(Sel::compile('SORT(LIST(TRUE, FALSE, TRUE, FALSE))')->run()->toNative()
    === [false, false, true, true], 'explicit getter sorts boolean scalars');

echo "PHP value layout checks: {$checks} passed\n";
