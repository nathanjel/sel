<?php
// Plain usage — calling SEL from PHP.
//
//   php examples/plain/php.php
//
// The four files beside this one do the same thing through their own host API
// and print byte-identical output; tools/check-examples.sh diffs them. That is
// the point of the example as much as the code is: the differences you see
// between these files are the languages', never SEL's.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

// 1 — evaluate something ------------------------------------------------------

echo "1. one-off\n";
echo '   2.50 + 2.50 => ', Sel::evaluate('2.50 + 2.50')->asText(), "\n";

// 2 — compile once, run per request -------------------------------------------
// Parsing is cheap but not free, and a Program is immutable and reusable. In a
// web app you compile each rule once at boot and keep it.

echo "2. compile once, run many\n";
$rule = Sel::compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
foreach ([['QTY' => '3', 'PRICE' => '19.99'], ['QTY' => '1', 'PRICE' => '5.00']] as $row) {
    $ctx = Value::fromNative($row + ['LIMIT' => '50.00']);
    printf("   QTY=%s PRICE=%s => %s\n", $row['QTY'], $row['PRICE'], $rule->run($ctx)->asText());
}

// 3 — building a context -------------------------------------------------------
// Pass money as *strings*. A PHP float has already lost the exactness SEL exists
// to preserve, and fromNative refuses one rather than pretend otherwise.

echo "3. structured context\n";
$order = Value::fromNative([
    'CUSTOMER' => 'Zażółć',
    'ITEMS' => [                                  // a packed array is a 1-based list
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
    ],
]);
echo '   first SKU => ', Sel::compile('ITEMS[1]["SKU"]')->run($order)->asText(), "\n";
echo '   total     => ', Sel::compile('SUM(ITEMS, _["QTY"] * _["PRICE"])')->run($order)->asText(), "\n";
echo '   0.10+0.20 => ', Sel::evaluate('0.10 + 0.20')->asText(), "\n";

// 4 — reading results back ------------------------------------------------------
// A result is a Value: a scalar, children, both or neither.

echo "4. reading results\n";
$v = Sel::evaluate('SPLIT("a,b,c", ",")');
echo '   size   => ', $v->size(), "\n";
echo '   keys   => ', implode(',', $v->keys()), "\n";
echo '   [2]    => ', $v->get('2')->asText(), "\n";
echo '   scalar => ', $v->asText(), "\n";               // scalar context: first child
// A host bool prints differently in all five languages (true/1/True/T), and
// this file's output has to be byte-identical to its four siblings, so say it
// in SEL's own spelling rather than the host's.
echo '   bool   => ', Sel::evaluate('1 < 2')->asBool() ? 'TRUE' : 'FALSE', "\n";

// 5 — the context is mutated, so rules hand values back ------------------------

echo "5. variables the rule set\n";
$ctx = Value::fromNative(['QTY' => '3', 'PRICE' => '19.99']);
Sel::compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT')->run($ctx);
foreach (['NET', 'VAT', 'GROSS'] as $name) {
    printf("   %-5s => %s\n", $name, $ctx->get($name)->asText());
}

// 6 — errors --------------------------------------------------------------------
// Every failure carries a stable code and the position of the node that actually
// failed. Assert on ->code, never on the message.

echo "6. errors\n";
foreach (['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")'] as $src) {
    try {
        Sel::evaluate($src);
        printf("   %-17s => no error\n", $src);
    } catch (SelError $e) {
        printf("   %-17s => %s at %d:%d\n", $src, $e->code, $e->line, $e->col);
    }
}

// 7 — which fields does this rule read? -----------------------------------------
// Found statically, without running it. This is how a frontend knows which
// inputs should re-trigger which rule.

echo "7. dependencies\n";
echo '   ', implode(' ', Sel::compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""')
    ->dependencies()), "\n";
