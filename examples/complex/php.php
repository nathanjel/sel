<?php
// Complex usage — the language's reach, from PHP.
//
//   php examples/complex/php.php
//
// examples/plain/ is the API. This is the language: aggregates, named binders,
// text and regex, structured results, and a rule that refuses. The four files
// beside this one print byte-identical output; tools/check-examples.sh diffs
// them.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$order = Value::fromNative([
    'CUSTOMER' => 'Zażółć Gęślą',
    'POSTCODE' => '31-874',
    'CREDIT_LIMIT' => '100.00',
    'ITEMS' => [                                  // a packed array is a 1-based list
        ['SKU' => 'AB-1234', 'QTY' => '3', 'PRICE' => '19.99'],
        ['SKU' => 'CD-5678', 'QTY' => '1', 'PRICE' => '5.01'],
        ['SKU' => 'EF-9012', 'QTY' => '2', 'PRICE' => '0.50'],
    ],
]);
$ask = fn (string $src): string => Sel::compile($src)->run($order)->asText();

// 1 — a rule set, not an expression -------------------------------------------
// `;` separates statements and the last one is the answer. Intermediate names
// are ordinary variables, so a long rule reads top to bottom.

echo "1. a rule set\n";
echo '   ', Sel::compile(implode('; ', [
    'NET   = SUM(ITEMS, _["QTY"] * _["PRICE"])',
    'VAT   = ROUND(NET * 0.23, 2)',
    'GROSS = NET + VAT',
    'IF(GROSS > CREDIT_LIMIT, "refer: " & GROSS, "accept: " & GROSS)',
]))->run($order)->asText(), "\n";

// 2 — aggregates ---------------------------------------------------------------
// No loops. A body expression is evaluated once per element with `_` bound to
// the element and `_K` to its key.

echo "2. aggregates\n";
printf("   %-12s => %s\n", 'lines', $ask('COUNT(ITEMS)'));
printf("   %-12s => %s\n", 'net', $ask('SUM(ITEMS, _["QTY"] * _["PRICE"])'));
printf("   %-12s => %s\n", 'all in stock', $ask('IF(ALL(ITEMS, _["QTY"] > 0), "TRUE", "FALSE")'));
printf("   %-12s => %s\n", 'any > 10', $ask('IF(ANY(ITEMS, _["PRICE"] > 10.00), "TRUE", "FALSE")'));
printf("   %-12s => %s\n", 'dearest', $ask('MAX(MAP(ITEMS, _["PRICE"]))'));

// 3 — MAP renumbers, FILTER keeps the keys --------------------------------------
// A filtered list stays addressable the way its source was, which is why the
// dump below has holes in it. That is the contract, not an accident.

echo "3. map and filter\n";
printf("   %-12s => %s\n", 'skus', $ask('JOIN(MAP(ITEMS, _["SKU"]), ", ")'));
printf("   %-12s => %s\n", 'bulk keys', implode(',', Sel::compile('FILTER(ITEMS, _["QTY"] > 1)')
    ->run($order)->keys()));
printf("   %-12s => %s\n", 'keyed', Sel::compile('MAP(FILTER(ITEMS, _["QTY"] > 1), _K & ":" & _["SKU"])')
    ->run($order)->dump());

// 4 — naming the binder, for nesting ---------------------------------------------
// `_` is the innermost element. The three-argument form names it instead, which
// is the only way an outer element stays reachable from an inner body.

echo "4. named binders\n";
echo '   ', Sel::evaluate(
    'R[1] = (1, 2); R[2] = (3, 4); IF(ALL(R, ROW, ALL(ROW, _ > 0)), "all positive", "no")',
)->asText(), "\n";

// 5 — text and regex ---------------------------------------------------------------
// Patterns are a portable subset, checked at compile time: a regex that would
// mean different things on different hosts is refused rather than guessed at.

echo "5. text and regex\n";
// UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
// ę below come back unchanged. That is not a shortcoming, it is the only way
// five hosts can agree. Measured on a sharp s: JS's toUpperCase and Python's
// str.upper both answer SS, PHP's strtoupper answers ß, and C's toupper cannot
// see it at all. SEL answers ß on all five, because it never asks the host.
printf("   %-12s => %s\n", 'upper', $ask('UPPER(CUSTOMER)'));
printf("   %-12s => %s\n", 'initials', $ask('JOIN(MAP(SPLIT(CUSTOMER, " "), LEFT(_, 1)), ".")'));
printf("   %-12s => %s\n", 'postcode', $ask('IF(RMATCH(\'^[0-9]{2}-[0-9]{3}$\', POSTCODE), "ok", "bad")'));
// RGROUPS puts the WHOLE match at "1", so the first capture is "2".
printf("   %-12s => %s\n", 'area', $ask('RGROUPS(\'^([0-9]{2})-\', POSTCODE)["2"]'));
printf("   %-12s => %s\n", 'padded', $ask('PADL(COUNT(ITEMS), 3, "0")'));

// 6 — asking whether a key is there --------------------------------------------------

echo "6. presence\n";
printf("   %-12s => %s\n", 'HAS SKU', $ask('IF(HAS(ITEMS[1], "SKU"), "TRUE", "FALSE")'));
printf("   %-12s => %s\n", 'HAS NOTE', $ask('IF(HAS(ITEMS[1], "NOTE"), "TRUE", "FALSE")'));
try {
    $missing = $ask('ITEMS[1]["NOTE"]');
} catch (SelError $e) {
    $missing = "{$e->code} at {$e->line}:{$e->col}";
}
printf("   %-12s => %s\n", 'missing', $missing);

// 7 — a rule that refuses -------------------------------------------------------------
// ABORT is how a rule says "this is not valid", as distinct from "this could not
// be computed". Both arrive as the same error type, told apart by the code.

echo "7. business refusal\n";
foreach ([
    ['under limit', 'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > CREDIT_LIMIT, ABORT("over credit limit"), "ok")'],
    ['over limit', 'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > 50.00, ABORT("over credit limit"), "ok")'],
    ['blank name', 'IF(TRIM("   ") $== "", ABORT("customer required"), "ok")'],
] as [$label, $src]) {
    try {
        printf("   %-11s => %s\n", $label, $ask($src));
    } catch (SelError $e) {
        printf("   %-11s => %s: %s\n", $label, $e->code, $e->getMessage());
    }
}

// 8 — where a failure actually happened -------------------------------------------------
// The position is the node that failed, not the statement or the call that
// contains it. That is what makes a long rule debuggable.

echo "8. error positions\n";
foreach (['1 + ROUND(2 + "x", 2)', 'SUM(ITEMS, _["QTY"] * _["NOPE"])'] as $src) {
    try {
        Sel::compile($src)->run($order);
    } catch (SelError $e) {
        printf("   %s at %d:%d  %s\n", $e->code, $e->line, $e->col, $src);
    }
}
