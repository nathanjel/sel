#!/usr/bin/env php
<?php
// Focused PHP A5 physical-layout checks. These stay beside the PHP lane so the
// checks do not become another cross-language harness contract.

declare(strict_types=1);

require_once __DIR__ . '/../src/bootstrap.php';

use Sel\Dec;
use Sel\Sel;
use Sel\Value;

$checks = 0;

/** @param bool $condition */
function check_a5(bool $condition, string $message): void
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
check_a5($record->shape !== null && $record->shape === $sameShape->shape, 'record shape interning');
check_a5($record->shape?->keyMap['id'] === 0, 'record shape slot map');
check_a5($record->storage !== null && array_is_list($record->storage), 'record storage is packed');
check_a5($record->get('id')?->scalar === '1', 'record direct slot lookup');
check_a5($record->toNative() === ['id' => '1', 'name' => 'one'], 'shaped direct materialization');

$scalarWithChild = Value::int(7);
$scalarWithChild->set('label', Value::text('seven'));
check_a5($scalarWithChild->toNative() === ['_' => '7', 'label' => 'seven'], 'scalar-with-child materialization');
$lazy = Value::thunk(static fn (): Value => Value::record(
    ['lazy_key'], [Value::text('lazy_value')],
));
check_a5($lazy->toNative() === ['lazy_key' => 'lazy_value'], 'lazy direct materialization');
$duplicate = Value::record(['same', 'same'], [Value::text('first'), Value::text('last')]);
check_a5($duplicate->toNative() === ['same' => 'last'], 'fallback duplicate-key materialization');

$list = Value::list([$record, $sameShape]);
check_a5($list->storage !== null && array_is_list($list->storage), 'list storage is packed');
check_a5($list->get('1') === $record && $list->get('2') === $sameShape, 'list direct slot lookup');
check_a5($list->toNative() === [
    ['id' => '1', 'name' => 'one'],
    ['id' => '2', 'name' => 'two'],
], 'packed list direct materialization');
$listFallback = Value::list([Value::text('first')]);
$listFallback->set('extra', Value::text('second'));
check_a5($listFallback->toNative() === ['first', 'second'], 'fallback list materialization');

$nativeRows = [
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'prepared_name' => 'two'],
];
$preparedRows = Value::fromNativeRows($nativeRows);
$genericRows = Value::fromNative($nativeRows);
check_a5($preparedRows->dump() === $genericRows->dump(), 'prepared ingestion preserves rows');
check_a5($preparedRows->storage[0]->shape === $preparedRows->storage[1]->shape,
    'prepared ingestion reuses homogeneous shape');
$mixedRows = Value::fromNativeRows([
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'other_name' => 'two'],
]);
$mixedGeneric = Value::fromNative([
    ['prepared_id' => 1, 'prepared_name' => 'one'],
    ['prepared_id' => 2, 'other_name' => 'two'],
]);
check_a5($mixedRows->dump() === $mixedGeneric->dump(), 'heterogeneous ingestion falls back safely');
check_a5($mixedRows->storage[0]->shape !== null && $mixedRows->storage[1]->shape !== null
    && $mixedRows->storage[0]->shape !== $mixedRows->storage[1]->shape,
    'heterogeneous rows retain separate shapes');

$specialKeys = Value::fromNative([
    'b' => Value::text('first'),
    '01' => Value::text('numeric-looking'),
    "nul\0key" => Value::text('embedded-nul'),
    'a' => Value::text('last'),
]);
check_a5($specialKeys->keys() === ['b', '01', "nul\0key", 'a'], 'host key order and spelling');
check_a5($specialKeys->get("nul\0key")?->scalar === 'embedded-nul', 'embedded-NUL key lookup');

$copy = $record->copy();
$copy->set('id', Value::int(99));
check_a5($record->get('id')?->scalar === '1' && $copy->get('id')?->scalar === '99',
    'copy mutation isolation');
$nestedCopy = Value::record(['child'], [Value::record(['value'], [Value::text('original')])])->copy();
$nestedCopy->get('child')->set('value', Value::text('changed'));
check_a5($nestedCopy->get('child')->get('value')?->scalar === 'changed', 'nested copy mutation');
check_a5($record->get('id')?->scalar === '1', 'nested copy leaves source isolated');

$noMatch = Sel::compile(
    'LIST(RECORD("id", 1)) .> LINK(LIST(RECORD("id", 2)), _1["id"] == _2["id"])',
)->run();
check_a5($noMatch->isList && $noMatch->size() === 0, 'inner join no-match result');
$emptyRight = Sel::compile(
    'LIST(RECORD("id", 1)) .> LINK_LEFT(LIST(), _1["id"] == _2["id"])',
)->run();
check_a5($emptyRight->isList && $emptyRight->size() === 1
    && $emptyRight->toNative()[0]['_2']['_2'] === null, 'left join empty-right null row');
$heterogeneousJoin = Sel::compile(
    'LIST(RECORD("id", 1, "left", "a"), RECORD("id", 2, "left", "b", "extra", "x"))'
    . ' .> LINK(LIST(RECORD("id", 1, "right", "r"), RECORD("id", 2, "right", "s")), '
    . ' _1["id"] == _2["id"])',
)->run();
$heterogeneousNative = $heterogeneousJoin->toNative();
check_a5($heterogeneousNative[1]['_1']['extra'] === 'x', 'heterogeneous join fallback');
$aliasedJoin = Sel::compile(
    'LINK(LIST(RECORD("id", 1)), LIST(RECORD("id", 1)), LEFT, RIGHT, '
    . 'LEFT["id"] == RIGHT["id"])',
)->run();
check_a5($aliasedJoin->isList && $aliasedJoin->size() === 1, 'case-sensitive alias join');

$number = Value::num('123456789.25');
$number->asDecimal();
$decProperty = new ReflectionProperty(Value::class, 'decVal');
check_a5($decProperty->getValue($number) !== null, 'numeric value caches parsed decimal');
check_a5(Dec::format(Dec::add(Dec::parse((string) PHP_INT_MAX), Dec::fromInt(1)))
    === '9223372036854775808', 'native overflow uses exact decimal fallback');
check_a5(Dec::format(Dec::add(Dec::parse((string) PHP_INT_MIN), Dec::fromInt(-1)))
    === '-9223372036854775809', 'negative native overflow uses exact decimal fallback');

$source = 'LIST(RECORD("id", 1, "l", "a"), RECORD("id", 2, "l", "b"))'
    . ' .> LINK_LEFT(LIST(RECORD("id", 2, "r", "x"), RECORD("id", 3, "r", "y")),'
    . ' _1["id"] == _2["id"])';
$joined = Sel::compile($source)->run();
check_a5($joined->dump() === '-{"1"=-{"_1"=-{"id"=t"1", "l"=t"a"}, "_2"=-{"id"=-, "r"=-, "_2"=-}, "l"=t"a"}, '
    . '"2"=-{"_1"=-{"id"=t"2", "l"=t"b"}, "_2"=-{"id"=t"2", "r"=t"x", "_2"=-{"id"=t"2", "r"=t"x"}}, '
    . '"l"=t"b", "r"=t"x"}}', 'join projector preserves Lisp output');
check_a5($joined->storage !== null && array_is_list($joined->storage), 'join result storage is packed');
check_a5($joined->storage[0]->shape !== null && $joined->storage[1]->shape !== null, 'join rows use shared shapes');
check_a5($joined->storage[0]->shape !== $joined->storage[1]->shape, 'left miss keeps null-row shape semantics');

echo "PHP A5 checks: {$checks} passed\n";
