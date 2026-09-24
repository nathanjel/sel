<?php
// Scripting an application's behaviour — functions the host provides, from PHP.
//
//   php examples/scripting/php.php
//
// SEL has no way to reach the world on its own, and that is the point: the
// application decides what a script may touch by registering functions. Here
// the warehouse gets four — STOCK and WEIGHT to read the catalogue, RESERVE to
// take stock, NOTIFY to queue a message — and fulfil.sel, a file the warehouse
// team owns, decides per order whether to ship, how, and whom to tell. The
// application stays the same when the policy changes.
//
// A registered function is strict: its arguments arrive evaluated, left to
// right, through the same typed readers the builtins use, so a script passing
// the wrong kind gets the usual error at the usual position.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Args;
use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$inventory = ['LAMP-01' => 4, 'DESK-02' => 1, 'CHAIR-03' => 6];
$weights = ['LAMP-01' => '1.6', 'DESK-02' => '28.0', 'CHAIR-03' => '7.5'];
$outbox = [];

// EXAMPLE-BEGIN register
Sel::registerFunction('STOCK', 1, 1, function (Args $args) use (&$inventory): Value {
    return Value::int($inventory[$args->text(0)] ?? 0);
});

Sel::registerFunction('RESERVE', 2, 2, function (Args $args) use (&$inventory): Value {
    [$sku, $qty] = [$args->text(0), $args->nonNegInt(1)];
    if (($inventory[$sku] ?? 0) < $qty) {
        return Value::bool(false);
    }
    $inventory[$sku] -= $qty;
    return Value::bool(true);
});

Sel::registerFunction('WEIGHT', 1, 1, function (Args $args) use ($weights): Value {
    return Value::text($weights[$args->text(0)] ?? '0');
});

Sel::registerFunction('NOTIFY', 2, 2, function (Args $args) use (&$outbox): Value {
    $outbox[] = "{$args->text(0)}: {$args->text(1)}";
    return Value::bool(true);
});
// EXAMPLE-END register

// EXAMPLE-BEGIN run
$fulfil = Sel::compile(file_get_contents(__DIR__ . '/fulfil.sel'));   // after registering: names resolve now

echo '1. the script reads ', implode(', ', $fulfil->dependencies()), "\n";
echo "2. orders\n";
$orders = [
    ['ORDER' => 'A-1', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => '2'],
                                                      ['sku' => 'CHAIR-03', 'qty' => '1']]],
    ['ORDER' => 'A-2', 'COUNTRY' => 'DE', 'ITEMS' => [['sku' => 'DESK-02', 'qty' => '1'],
                                                      ['sku' => 'CHAIR-03', 'qty' => '2']]],
    ['ORDER' => 'A-3', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => '3']]],
    ['ORDER' => 'A-4', 'COUNTRY' => 'PL', 'ITEMS' => [['sku' => 'LAMP-01', 'qty' => 'two']]],
];
foreach ($orders as $order) {
    try {
        $decision = $fulfil->run(Value::fromNative($order))->asText();
    } catch (SelError $e) {
        $decision = "{$e->code} at {$e->line}:{$e->col}";
    }
    echo "   {$order['ORDER']}  $decision\n";
}
// EXAMPLE-END run

echo "3. outbox\n";
foreach ($outbox as $message) {
    echo '   ', $message, "\n";
}
echo "4. stock left\n";
ksort($inventory, SORT_STRING);
foreach ($inventory as $sku => $left) {
    printf("   %-9s %d\n", $sku, $left);
}
