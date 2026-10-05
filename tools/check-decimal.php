#!/usr/bin/env php
<?php
// Checks the PHP decimal core against the Python oracle.
//   php tools/check-decimal.php oracle.txt

declare(strict_types=1);

require_once __DIR__ . '/../php/src/SelError.php';
require_once __DIR__ . '/../php/src/Dec.php';

use Sel\Dec;
use Sel\SelError;

// Modes: SEL_PHP_FORCE_GMP=0 takes the pure-PHP paths on a machine that has
// ext-gmp; SEL_PHP_LAZY_OPERANDS=1 turns lazy digits on and hands every operation
// operands an earlier operation produced (adding zero keeps the value and, with
// lazy digits, its GMP form).
$mode = 'php';
if (getenv('SEL_PHP_FORCE_GMP') === '0') {
    Dec::testHooks(['gmp' => false]);
    $mode = 'php (pure PHP)';
}
$lazyOperands = getenv('SEL_PHP_LAZY_OPERANDS') === '1';
if ($lazyOperands) {
    Dec::testHooks(['lazyDigits' => true]);
    $mode = 'php (lazy operands)';
}

$cases = [];
foreach (explode("\n", file_get_contents($argv[1])) as $line) {
    if ($line === '') {
        continue;
    }
    [$op, $a, $b, $want] = explode('|', $line);
    $cases[] = ['op' => $op, 'a' => $a, 'b' => $b, 'want' => $want];
}
$failures = [];
// Counted separately from the displayed list; see check-decimal.mjs.
$mismatches = 0;

foreach ($cases as $c) {
    $a = Dec::parse($c['a']);
    $b = Dec::parse($c['b']);
    if ($lazyOperands) {
        $a = Dec::add($a, Dec::zero());
        if ($c['op'] !== 'round') {
            $b = Dec::add($b, Dec::zero());
        }
    }
    try {
        $got = match ($c['op']) {
            '+' => Dec::format(Dec::add($a, $b)),
            '-' => Dec::format(Dec::sub($a, $b)),
            '*' => Dec::format(Dec::mul($a, $b)),
            '/' => Dec::format(Dec::div($a, $b)),
            '%' => Dec::format(Dec::mod($a, $b)),
            'cmp' => (string) Dec::cmp($a, $b),
            'round' => Dec::format(Dec::round($a, (int) $c['b'])),
            'floor' => Dec::format(Dec::floor($a)),
            'ceil' => Dec::format(Dec::ceil($a)),
            'trunc' => Dec::format(Dec::trunc($a)),
        };
    } catch (SelError $e) {
        $got = 'THREW ' . $e->code;
    }
    if ($got !== $c['want']) {
        $mismatches++;
        if (count($failures) < 20) {
            $failures[] = "{$c['a']} {$c['op']} {$c['b']} => {$got}, oracle says {$c['want']}";
        }
    }
}

$n = count($cases);
echo "{$mode}: {$n} cases, {$mismatches} mismatches\n";
foreach ($failures as $line) {
    echo "  {$line}\n";
}
if ($mismatches > count($failures)) {
    echo '  ... and ', $mismatches - count($failures), " more\n";
}
exit($mismatches > 0 ? 1 : 0);
