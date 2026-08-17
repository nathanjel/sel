<?php
// Calling SEL from PHP. Run: php examples/host-php.php

declare(strict_types=1);

require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

// 1 — evaluate something -----------------------------------------------------

echo "1. one-off\n";
echo '   ', Sel::evaluate('2.50 + 2.50')->asText(), "\n";

// 2 — compile once, run per request ------------------------------------------
// Parsing is cheap but not free, and a Program is immutable and reusable. In a
// web app you compile each rule once at boot and keep it.

echo "2. compile once, run many\n";
$rule = Sel::compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');

foreach ([['QTY' => '3', 'PRICE' => '19.99'], ['QTY' => '1', 'PRICE' => '5.00']] as $row) {
    $ctx = Value::fromNative($row + ['LIMIT' => '50.00']);
    echo '   ', $rule->run($ctx)->asText(), "\n";
}

// 3 — building a context ------------------------------------------------------
// fromNative takes scalars, arrays and nested arrays. Pass money as *strings*:
// a PHP float has already lost the exactness SEL exists to preserve, and
// fromNative refuses one rather than pretend otherwise.

echo "3. structured context\n";
$order = Value::fromNative([
    'CUSTOMER' => 'Zażółć',
    'ITEMS' => [                                  // a packed array is a 1-based list
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
    ],
]);
echo '   first SKU: ', Sel::compile('ITEMS[1]["SKU"]')->run($order)->asText(), "\n";
echo '   total:     ', Sel::compile('SUM(ITEMS, _["QTY"] * _["PRICE"])')->run($order)->asText(), "\n";

try {
    Value::fromNative(['PRICE' => 19.99]);
} catch (InvalidArgumentException $e) {
    echo '   float rejected: ', $e->getMessage(), "\n";
}

// 4 — reading results back ----------------------------------------------------
// A result is a Value. Use asText/asBool/asDecimal for scalars, or walk it.

echo "4. reading results\n";
$v = Sel::evaluate('SPLIT("a,b,c", ",")');
echo '   count:   ', $v->size(), "\n";
echo '   keys:    ', implode(',', $v->keys()), "\n";
echo '   [2]:     ', $v->get('2')->asText(), "\n";
echo '   scalar:  ', $v->asText(), "\n";                 // scalar context: first child
echo '   native:  ', json_encode($v->toNative()), "\n";
echo '   bool:    ', var_export(Sel::evaluate('1 < 2')->asBool(), true), "\n";

// 5 — the context is mutated, so rules can hand values back -------------------

echo "5. reading variables the rule set\n";
$ctx = Value::fromNative(['QTY' => '3', 'PRICE' => '19.99']);
Sel::compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT')->run($ctx);
foreach (['NET', 'VAT', 'GROSS'] as $name) {
    echo "   {$name} = ", $ctx->get($name)->asText(), "\n";
}

// 6 — errors ------------------------------------------------------------------
// Everything throws SelError with a stable code and the position of the node
// that actually failed. Assert on ->code, never on the message.

echo "6. errors\n";
foreach (['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")'] as $src) {
    try {
        Sel::evaluate($src);
    } catch (SelError $e) {
        printf("   %-18s %s at %d:%d — %s\n", $src, $e->code, $e->line, $e->col, $e->getMessage());
    }
}

// 7 — which fields does this rule read? ---------------------------------------
// Static, without running it. This is how the frontend knows which inputs
// should re-trigger which rule.

echo "7. dependencies\n";
$p = Sel::compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""');
echo '   ', implode(' ', $p->dependencies()), "\n";
