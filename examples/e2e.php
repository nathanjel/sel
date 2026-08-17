#!/usr/bin/env php
<?php
// Drives examples/order-validation.sel through the PHP host API and prints a
// canonical report. examples/e2e.mjs does the same through the JS host API, and
// tools/e2e.sh diffs the two.

declare(strict_types=1);

require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$source = file_get_contents(__DIR__ . '/order-validation.sel');
$program = Sel::compile($source);

// Prices are strings, not PHP floats: 0.1 + 0.2 must not become
// 0.30000000000004 on one side of the wire and 0.30 on the other.
$scenarios = [
    'valid order' => [
        'CUSTOMER' => 'Zażółć Gęślą',
        'POSTCODE' => '31-874',
        'CREDIT_LIMIT' => '1000.00',
        'ITEMS' => [
            ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
            ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
        ],
    ],
    'blank customer' => [
        'CUSTOMER' => '   ', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '1000.00',
        'ITEMS' => [['SKU' => 'AB-1234', 'QTY' => '1', 'PRICE' => '1.00']],
    ],
    'bad postcode' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '318744', 'CREDIT_LIMIT' => '1000.00',
        'ITEMS' => [['SKU' => 'AB-1234', 'QTY' => '1', 'PRICE' => '1.00']],
    ],
    'no lines' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '1000.00', 'ITEMS' => [],
    ],
    'zero quantity' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '1000.00',
        'ITEMS' => [
            ['SKU' => 'AB-1234', 'QTY' => '1', 'PRICE' => '1.00'],
            ['SKU' => 'CD-5678', 'QTY' => '0', 'PRICE' => '2.00'],
        ],
    ],
    'malformed sku' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '1000.00',
        'ITEMS' => [['SKU' => 'oops', 'QTY' => '1', 'PRICE' => '1.00']],
    ],
    'over credit limit' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '10.00',
        'ITEMS' => [['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99']],
    ],
    'exact-cent arithmetic' => [
        'CUSTOMER' => 'Anna', 'POSTCODE' => '31-874', 'CREDIT_LIMIT' => '0.30',
        'ITEMS' => [
            ['SKU' => 'AB-1234', 'QTY' => '1', 'PRICE' => '0.10'],
            ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '0.20'],
        ],
    ],
];

$lines = ['dependencies: ' . implode(' ', $program->dependencies())];
foreach ($scenarios as $name => $data) {
    try {
        $result = $program->run(Value::fromNative($data))->dump();
    } catch (SelError $e) {
        $result = "!{$e->code}@{$e->line}:{$e->col}";
    } catch (\Throwable $e) {
        $result = '!HOST ' . $e->getMessage();
    }
    $lines[] = str_pad($name, 24) . ' ' . $result;
}
echo implode("\n", $lines), "\n";
