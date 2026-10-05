<?php
declare(strict_types=1);
// PHP's own lane: what an application holding the library across requests, queues
// and caches relies on. The contracts every host shares are probed by tools/api.php
// (pinned in tools/api-pins.txt); these are the PHP-shaped ones.
require __DIR__ . '/../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$failures = [];
$checks = 0;
$expect = function (string $name, callable $fn) use (&$failures, &$checks): void {
    $checks++;
    try {
        $r = $fn();
        if ($r !== true) $failures[] = "$name: got " . var_export($r, true);
    } catch (\Throwable $e) {
        $failures[] = "$name: threw " . get_class($e) . ' ' . $e->getMessage();
    }
};
function errorOf(string $source): SelError {
    try {
        Sel::compile($source);
    } catch (SelError $e) {
        return $e;
    }
    throw new RuntimeException("no error for $source");
}

// The Python host's SelError could not be pickled and killed a process pool; PHP's
// equivalent is serialize(): an error that goes through a queue or a session or a cache must
// come back with its code and position, not with uninitialised properties.
$expect('a SelError survives serialize/unserialize', function () {
    $e = errorOf("1 +\n  * 2");
    $c = unserialize(serialize($e));
    return $c instanceof SelError && $c->code === $e->code && $c->line === $e->line
        && $c->col === $e->col && $c->offset === $e->offset && $c->getMessage() === $e->getMessage();
});
$expect('a positionless SelError survives too', function () {
    $c = unserialize(serialize(new SelError('E_BAD_ARG', 'no position')));
    return $c->code === 'E_BAD_ARG' && $c->line === 0 && $c->col === 0 && $c->getMessage() === 'no position';
});
$expect('a runtime SelError survives a JSON-shaped round trip of its public fields', function () {
    try {
        Sel::evaluate('A / 0', ['A' => 1]);
    } catch (SelError $e) {
        $j = json_decode(json_encode(['code' => $e->code, 'line' => $e->line, 'col' => $e->col, 'offset' => $e->offset]), true);
        return $j['code'] === 'E_DIV_ZERO' && $j['line'] === 1 && $j['col'] === 3 && $j['offset'] === 2;
    }
    return false;
});

// Two logical clients in one PHP process (a worker that serves many requests): one keeps
// failing, the other keeps succeeding, over their own contexts. Neither sees the other's
// error, and the shared TRUE/FALSE values are not poisoned by either.
$expect('two interleaved clients do not see each other', function () {
    $failing = Sel::compile('A / B');
    $working = Sel::compile('A / B');
    $f = Value::none(); Sel::compile('A = 1; B = 0; 0')->run($f);
    $w = Value::none(); Sel::compile('A = 6; B = 3; 0')->run($w);
    for ($i = 0; $i < 300; $i++) {
        try {
            $failing->run($f);
            return 'the failing client did not fail';
        } catch (SelError $e) {
            if ($e->code !== 'E_DIV_ZERO') return $e->code;
        }
        if ($working->run($w)->asText() !== '2') return 'the working client saw a wrong answer';
    }
    return true;
});
$expect('the boolean constants are intact after both clients ran', function () {
    $t = Sel::evaluate('TRUE'); $f = Sel::evaluate('FALSE');
    return $t->asBool() === true && $f->asBool() === false;
});

// Reuse after caught errors: nothing (scratch depth, binder frame, join state) is left
// behind for the next run.
$expect('programs run cleanly after a series of failures', function () {
    $bad = ['MAP((1, 2, 3), _ / 0)', 'A = 1; B = 1 / 0', 'SORT_BY((3, 1, 2), _ / 0)',
        'LINK(LIST(RECORD("k", 1)), LIST(RECORD("k", 1)), A, B, A["k"] == B["nope"])'];
    for ($round = 0; $round < 3; $round++) {
        foreach ($bad as $src) {
            try {
                Sel::evaluate($src);
                return "no error: $src";
            } catch (SelError) {
            }
        }
        $v = Sel::evaluate('SUM((1, 2, 3), _ * 2) & "|" & COUNT(SORT_BY((3, 1, 2), -_))');
        if ($v->asText() !== '12|3') return "round $round: " . $v->asText();
    }
    return true;
});

// Locale: strtoupper() and friends follow LC_CTYPE before PHP 8.2. UPPER/LOWER
// and function-name lookup are ASCII-only by decision; a process locale must not change them.
$expect('UPPER, LOWER and function names ignore the process locale', function () {
    $saved = setlocale(LC_ALL, '0');
    $found = false;
    foreach (['tr_TR.UTF-8', 'tr_TR.utf8', 'tr_TR', 'de_DE.UTF-8', 'C.UTF-8'] as $loc) {
        if (@setlocale(LC_ALL, $loc) !== false) { $found = true; break; }
    }
    try {
        $ok = Sel::evaluate('UPPER("iı") & "|" & LOWER("IİK")')->asText() === "Iı|iİk"
            && Sel::evaluate('len("abc")')->asText() === '3'
            && Sel::evaluate('Len("i")')->asText() === '1';
    } finally {
        setlocale(LC_ALL, $saved ?: 'C');
    }
    return $ok;   // when no locale exists the check still runs, in the C locale
});

if ($failures) {
    fwrite(STDERR, 'PHP integration: ' . count($failures) . " contract(s) broken:\n  " . implode("\n  ", $failures) . "\n");
    exit(1);
}
echo "PHP integration: $checks checks passed\n";
