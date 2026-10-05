#!/usr/bin/env php
<?php
// PHP performance benchmarks for worklist wave PHP-P1..P11 (docs/interim/2026-09-29/worklist/performance/php.md).
//
//   php [-d memory_limit=-1] tools/perf/php/bench.php [--gmp=on|off] [--reps=N] [--json] [case-substring ...]
//
// Every case prints: name, n, median CPU ms (user+sys, getrusage) over reps, CPU peak delta (MB) and a SEMANTIC CHECKSUM of the result
// (md5 of the dump/text), so a speed-up that changes an answer shows as a checksum change between two runs.
// Sizes come in n / 2n / 4n so growth is visible (x2 = linear, x4 = quadratic). Fixed seeds: no randomness
// except mt_srand(20260930). A case whose single run exceeds --cap seconds is cut off at that size.
declare(strict_types=1);
require_once __DIR__ . '/../../../php/src/Sql/bootstrap.php';

use Sel\Dec;
use Sel\Lexer;
use Sel\Sel;
use Sel\Value;
use Sel\Sql\Binding;
use Sel\Sql\Sql;

$opts = ['gmp' => 'on', 'reps' => 5, 'json' => false, 'cap' => 25.0];
$filters = [];
foreach (array_slice($argv, 1) as $a) {
    if (preg_match('/^--gmp=(on|off)$/', $a, $m)) $opts['gmp'] = $m[1];
    elseif (preg_match('/^--reps=(\d+)$/', $a, $m)) $opts['reps'] = (int) $m[1];
    elseif (preg_match('/^--cap=([\d.]+)$/', $a, $m)) $opts['cap'] = (float) $m[1];
    elseif ($a === '--json') $opts['json'] = true;
    else $filters[] = $a;
}
Dec::testHooks(['gmp' => $opts['gmp'] === 'on' ? null : false]);
mt_srand(20260930);

function lcheck(mixed $v): string
{
    if ($v instanceof Value) return md5($v->dump());
    if (is_string($v)) return md5($v);
    return md5(json_encode($v));
}

$rows = [];
/** CPU milliseconds (user+system) of this process: steadier than wall time while other jobs share the box. */
function cpums(): float
{
    $u = getrusage();
    return ($u['ru_utime.tv_sec'] + $u['ru_stime.tv_sec']) * 1000.0 + ($u['ru_utime.tv_usec'] + $u['ru_stime.tv_usec']) / 1000.0;
}
function bench(string $name, array $sizes, callable $make, ?int $reps = null): void
{
    global $opts, $filters, $rows;
    if ($filters) {
        $hit = false;
        foreach ($filters as $f) if (str_contains($name, $f)) $hit = true;
        if (!$hit) return;
    }
    $reps ??= $opts['reps'];
    foreach ($sizes as $n) {
        $run = $make($n);              // setup is not timed
        $t0 = cpums();
        $result = $run();              // warm-up / cold call, also the checksum
        $cold = cpums() - $t0;
        $sum = lcheck($result);
        $samples = [$cold];
        if ($cold / 1000 < $opts['cap']) {
            $r = $cold > 2000 ? 1 : $reps;
            $samples = [];
            for ($i = 0; $i < $r; $i++) {
                $t0 = cpums();
                $run();
                $samples[] = cpums() - $t0;
            }
        }
        sort($samples);
        $med = $samples[intdiv(count($samples), 2)];
        $rows[] = ['case' => $name, 'n' => $n, 'cold_ms' => round($cold, 3), 'median_ms' => round($med, 3),
            'min_ms' => round($samples[0], 3), 'max_ms' => round(end($samples), 3), 'reps' => count($samples), 'sum' => substr($sum, 0, 10)];
        if (!$opts['json']) fprintf(STDERR, "%-34s n=%-9d median %10.3f ms  (min %.3f max %.3f, %d reps)  sum %s\n",
            $name, $n, $med, $samples[0], end($samples), count($samples), substr($sum, 0, 10));
        if ($cold / 1000 >= $opts['cap']) break;     // do not grow past the cap
    }
}
function mem(string $name, callable $fn): void
{
    global $opts, $filters, $rows;
    if ($filters) {
        $hit = false;
        foreach ($filters as $f) if (str_contains($name, $f)) $hit = true;
        if (!$hit) return;
    }
    gc_collect_cycles();
    $base = memory_get_usage();
    $keep = $fn();
    $used = memory_get_usage() - $base;
    $rows[] = ['case' => $name, 'n' => 0, 'bytes' => $used, 'info' => is_array($keep) ? (string) ($keep['info'] ?? '') : ''];
    if (!$opts['json']) fprintf(STDERR, "%-34s retained %12d bytes %s\n", $name, $used, is_array($keep) ? ($keep['info'] ?? '') : '');
}
function ev(string $src, array $ctx = []): callable
{
    $p = Sel::compile($src);
    return fn () => $p->run(Value::fromNative($ctx));
}
$ascii = fn (int $n) => str_repeat('abcdefghij', intdiv($n, 10) + 1);
$utf8 = function (int $n): string { $s = str_repeat("aé漢😀", intdiv($n, 4) + 1); return $s; };

// ---- P1: text builtins on large subjects (code-point answers) -----------------------------------------------
foreach (['ascii' => $ascii, 'utf8' => $utf8] as $kind => $gen) {
    foreach (['LEN' => 'LEN(S)', 'LEFT3' => 'LEFT(S,3)', 'RIGHT3' => 'RIGHT(S,3)', 'SUBSTR' => 'SUBSTR(S,5,3)',
              'CODE' => 'CODE(S)', 'TRIM' => 'LEN(TRIM(S))', 'UPPER' => 'LEN(UPPER(S))', 'BACKWARDS' => 'LEN(BACKWARDS(S))',
              'PADL' => 'LEN(PADL("x", 1000000, "."))'] as $label => $src) {
        bench("p1.$label.$kind", [250000, 500000, 1000000], function ($n) use ($gen, $src) {
            $s = $gen($n);
            return ev($src, ['S' => $s]);
        });
    }
}
// ---- P2: FIND / REPLACE / SPLIT -----------------------------------------------------------------------------
bench('p2.find.adversarial', [2000, 4000, 8000], fn ($n) => ev('FIND(N,H)', ['N' => str_repeat('a', $n) . 'b', 'H' => str_repeat('a', 2 * $n)]));
bench('p2.find.miss-1M', [250000, 500000, 1000000], fn ($n) => ev('FIND("b",H)', ['H' => str_repeat('a', $n)]));
bench('p2.replace', [75000, 150000, 300000], fn ($n) => ev('LEN(REPLACE("a","bb",H))', ['H' => str_repeat('a', $n)]));
bench('p2.split', [75000, 150000, 300000], fn ($n) => ev('COUNT(SPLIT(H,"a"))', ['H' => str_repeat('a', $n)]));
bench('p2.find.utf8', [100000, 200000, 400000], fn ($n) => ev('FIND("é漢",H)', ['H' => str_repeat('aé', $n) . 'é漢']));
// ---- P3: sorting ---------------------------------------------------------------------------------------------
$ints = function (int $n): array { $a = []; for ($i = 0; $i < $n; $i++) $a[] = mt_rand(0, 1000000); return $a; };
$texts = function (int $n): array { $a = []; for ($i = 0; $i < $n; $i++) $a[] = 'k' . mt_rand(0, 1000000); return $a; };
bench('p3.sort.ints', [12500, 25000, 50000], fn ($n) => ev('SORT(L)', ['L' => $ints($n)]));
bench('p3.sort.text', [12500, 25000, 50000], fn ($n) => ev('SORT(L)', ['L' => $texts($n)]));
bench('p3.sortby.field.ints', [12500, 25000, 50000], fn ($n) => ev('SORT_BY(L, R, R["v"])', ['L' => array_map(fn ($x) => ['v' => $x], $ints($n))]));
bench('p3.sortby.field.text', [12500, 25000, 50000], fn ($n) => ev('SORT_BY(L, R, R["v"])', ['L' => array_map(fn ($x) => ['v' => $x], $texts($n))]));
bench('p3.top.ints', [25000, 50000, 100000], fn ($n) => ev('TOP(L, 10)', ['L' => $ints($n)]));
// ---- P4 / P5: non-GMP division and multiplication ---------------------------------------------------------------
$digits = function (int $n, int $seed): string { $s = (string) (($seed % 9) + 1); for ($i = 1; $i < $n; $i++) $s .= (string) (($i * 7 + $seed) % 10); return $s; };
foreach ([[20, 10], [200, 100], [1000, 500]] as [$a, $b]) {}
bench('p4.div', [500, 1000, 2000, 4000], function ($n) use ($digits) {
    $x = Dec::parse($digits(2 * $n, 3)); $y = Dec::parse($digits($n, 5));
    return fn () => Dec::format(Dec::div($x, $y));
});
bench('p4.div.small-divisor', [20, 40, 80], function ($n) use ($digits) {
    $x = Dec::parse($digits($n, 3)); $y = Dec::parse($digits(intdiv($n, 2), 5));
    return fn () => Dec::format(Dec::div($x, $y));
}, 200);
bench('p5.mul', [5000, 10000, 20000, 40000], function ($n) use ($digits) {
    $x = Dec::parse($digits($n, 7)); $y = Dec::parse($digits($n, 9));
    return fn () => Dec::format(Dec::mul($x, $y));
});
bench('p5.power', [2000, 4000, 8000], fn ($n) => ev('LEN(POWER(99, ' . $n . '))'));
// ---- P6: LINK with an outside variable in the key --------------------------------------------------------------
bench('p6.link.plain', [300, 600, 1200], function ($n) {
    $l = []; $r = [];
    for ($i = 0; $i < $n; $i++) { $l[] = ['id' => $i]; $r[] = ['id' => $i]; }
    return ev('COUNT(LINK(L, R, A, B, A["id"] == B["id"]))', ['L' => $l, 'R' => $r]);
});
bench('p6.link.outside-var', [300, 600, 1200], function ($n) {
    $l = []; $r = [];
    for ($i = 0; $i < $n; $i++) { $l[] = ['id' => $i]; $r[] = ['id' => $i + 1]; }
    return ev('COUNT(LINK(L, R, A, B, A["id"] + K == B["id"]))', ['L' => $l, 'R' => $r, 'K' => 1]);
});
// ---- P8: SQL IN-list fold ---------------------------------------------------------------------------------------
bench('p8.sql.in-list', [500, 1000, 2000, 4000], function ($n) {
    $items = []; for ($i = 1; $i <= $n; $i++) $items[] = '"k' . $i . '"';
    $p = Sel::compile('T IN (' . implode(', ', $items) . ')');
    $b = ['T' => Binding::column('t', 'o', 'TEXT')];
    return fn () => Sql::translate($p, 'mariadb', $b)->asValue();
});
bench('p8.sql.any-list', [500, 1000, 2000, 4000], function ($n) {
    $items = []; for ($i = 1; $i <= $n; $i++) $items[] = $i;
    $p = Sel::compile('ANY((' . implode(', ', $items) . '), X, X == N)');
    $b = ['N' => Binding::column('n', 'o', 'NUM')];
    return fn () => Sql::translate($p, 'mariadb', $b)->asValue();
});
// ---- P9: front end ----------------------------------------------------------------------------------------------
$prog = file_get_contents(__DIR__ . '/../../../examples/lib/tickets-generate.sel');
bench('p9.compile.3KB', [1, 10, 40], function ($k) use ($prog) {
    return function () use ($k, $prog) { $h = ''; for ($i = 0; $i < $k * 20; $i++) $h = serialize(Sel::compile($prog)->dependencies()); return $h; };
});
bench('p9.tokenize.3KB', [1, 10, 40], function ($k) use ($prog) {
    return function () use ($k, $prog) { $c = 0; for ($i = 0; $i < $k * 20; $i++) $c += count(Lexer::tokenizeSource($prog)); return (string) $c; };
});
bench('p9.compile.200KB-expr', [25000, 50000, 100000], fn ($n) => function () use ($n) { $p = Sel::compile('1' . str_repeat('+1', $n)); return (string) strlen($p->source); });
bench('p9.compile.ascii-strings', [250, 500, 1000], fn ($n) => function () use ($n) {
    $s = ''; for ($i = 0; $i < $n; $i++) $s .= 'IF(A == "value ' . $i . '", "x{B}", "y") & ';
    $p = Sel::compile($s . '"end"');
    return (string) strlen($p->source);
});
// ---- P10: tokens / nodes memory ----------------------------------------------------------------------------------
mem('p10.tokens.200k', function () { $src = '1' . str_repeat('+1', 100000); $t = Lexer::tokenizeSource($src); return ['info' => count($t) . ' tokens', 'keep' => $t]; });
mem('p10.parse.50k-ops', function () { $p = Sel::compile('1' . str_repeat('+1', 50000)); return ['info' => 'compiled', 'keep' => $p]; });
// ---- P11: literal evaluation ----------------------------------------------------------------------------------
bench('p11.literals.map', [50000, 100000, 200000], function ($n) { $l = range(1, $n); return ev('COUNT(MAP(L, X, "active"))', ['L' => $l]); });
bench('p11.literals.num', [50000, 100000, 200000], function ($n) { $l = range(1, $n); return ev('COUNT(MAP(L, X, 7))', ['L' => $l]); });
bench('p11.text.literal-eq', [50000, 100000, 200000], function ($n) { $l = array_fill(0, $n, 'active'); return ev('COUNT(FILTER(L, X, X $== "active"))', ['L' => $l]); });
bench('p11.record.set', [20000, 40000, 80000], function ($n) { $l = range(1, $n); return ev('COUNT(MAP(L, X, RECORD("id", X, "status", "active", "kind", "a")))', ['L' => $l]); });


// ---- P12: numeric results re-validated (Value::num -> Dec::checked) ---------------------------------------------
bench('p12.arith.map', [25000, 50000, 100000], function ($n) { $l = range(1, $n); return ev('COUNT(MAP(L, X, X * 2 + 1))', ['L' => $l]); });
bench('p12.sum.decimals', [25000, 50000, 100000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = '12.' . ($i % 100); return ev('SUM(L, X, X)', ['L' => $l]); });
bench('p12.abs-max', [25000, 50000, 100000], function ($n) { $l = range(-$n, -1); return ev('COUNT(MAP(L, X, MAX(ABS(X), 3)))', ['L' => $l]); });
// ---- P13: division with small operands --------------------------------------------------------------------------
bench('p13.div.small', [50000, 100000, 200000], function ($n) {
    $x = Dec::parse('123.45'); $y = Dec::parse('67.89');
    return function () use ($n, $x, $y) { $h = ''; for ($i = 0; $i < $n; $i++) $h = Dec::format(Dec::div($x, $y)); return $h; };
});
bench('p13.div.map', [25000, 50000, 100000], function ($n) { $l = range(1, $n); return ev('COUNT(MAP(L, X, X / 7))', ['L' => $l]); });
bench('p13.mod.small', [50000, 100000, 200000], function ($n) {
    $x = Dec::parse('123.45'); $y = Dec::parse('6.789');
    return function () use ($n, $x, $y) { $h = ''; for ($i = 0; $i < $n; $i++) $h = Dec::format(Dec::mod($x, $y)); return $h; };
});
// ---- P14: cmpAbs on digit strings ---------------------------------------------------------------------------------
foreach ([24, 300] as $w) {
    bench("p14.cmp.$w-digits", [50000, 100000, 200000], function ($n) use ($w, $digits) {
        $a = Dec::parse($digits($w, 3)); $b = Dec::parse($digits($w, 4));
        return function () use ($n, $a, $b) { $h = 0; for ($i = 0; $i < $n; $i++) $h += Dec::cmp($a, $b); return (string) $h; };
    });
}
bench('p14.filter.gt-wide', [25000, 50000, 100000], function ($n) use ($digits) {
    $l = []; for ($i = 0; $i < $n; $i++) $l[] = $digits(24, $i);
    return ev('COUNT(FILTER(L, X, X > K))', ['L' => $l, 'K' => $digits(24, 4)]);
});
// ---- P15: Dec::parse ------------------------------------------------------------------------------------------------
bench('p15.parse.short', [100000, 200000, 400000], function ($n) {
    $t = ['123.45', '7', '-0.5', '0', '100', '12.34', '99.99', '-7']; 
    return function () use ($n, $t) { $h = 0; for ($i = 0; $i < $n; $i++) { $d = Dec::parse($t[$i & 7]); $h += $d['scale']; } return (string) $h; };
});
bench('p15.sum.text-numbers', [25000, 50000, 100000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = '12.' . ($i % 100); return ev('SUM(L, X, X)', ['L' => $l]); });
// ---- P16: copy of a keyed list --------------------------------------------------------------------------------------
bench('p16.copy.keyed', [100, 1000, 10000], function ($n) {
    $l = range(1, $n);
    return ev('A = FILTER(L, X, X > 0); B = A; C = A; D = A; E = A; F = A; COUNT(B) + COUNT(F)', ['L' => $l]);
});
bench('p16.copy.plain', [100, 1000, 10000], function ($n) {
    $l = range(1, $n);
    return ev('A = L; B = A; C = A; D = A; E = A; F = A; COUNT(B) + COUNT(F)', ['L' => $l]);
});
bench('p16.copy.micro-keyed', [100, 1000, 10000], function ($n) {
    $l = Value::fromNative(['L' => range(1, $n)]);
    $k = Sel::compile('FILTER(L, X, X > 0)')->run($l);        // a keyed list (FILTER keeps the keys)
    return function () use ($k, $n) { $c = null; for ($i = 0; $i < max(1, intdiv(200000, $n)); $i++) $c = $k->copy(); return (string) $c->size(); };
});
// ---- P17: retained memory of numeric values ---------------------------------------------------------------------------
mem('p17.rows.fromNative', function () { $r = []; for ($i = 0; $i < 50000; $i++) $r[] = ['AMOUNT' => '12.34']; $v = Value::fromNative($r); return ['info' => '50k rows', 'keep' => $v]; });
mem('p17.rows.after-sum', function () {
    $r = []; for ($i = 0; $i < 50000; $i++) $r[] = ['AMOUNT' => '12.34'];
    $ctx = Value::fromNative(['ROWS' => $r]);
    Sel::compile('SUM(ROWS, r, r["AMOUNT"])')->run($ctx);
    return ['info' => '50k rows after SUM', 'keep' => $ctx];
});
// ---- P18: RREPLACE ---------------------------------------------------------------------------------------------------
bench('p18.rreplace.100k', [50000, 100000, 200000], function ($n) { return ev('LEN(RREPLACE("a", "bb", S))', ['S' => str_repeat('xa', $n)]); });
bench('p18.rreplace.groups', [50000, 100000, 200000], function ($n) { return ev('LEN(RREPLACE("(a)(x)", "<$2$1$$>", S))', ['S' => str_repeat('xa', $n)]); });
bench('p18.rreplace.utf8', [25000, 50000, 100000], function ($n) { return ev('LEN(RREPLACE("é", "e$0", S))', ['S' => str_repeat('aé', $n)]); });
// ---- P19: base64 / crc32 ------------------------------------------------------------------------------------------------
bench('p19.encode-base64', [250000, 500000, 1000000], function ($n) { return ev('LEN(ENCODE_BASE64(TO_UTF8(S)))', ['S' => str_repeat('abcdefghij', intdiv($n, 10))]); });
bench('p19.decode-base64', [250000, 500000, 1000000], function ($n) { return ev('BLEN(DECODE_BASE64(S))', ['S' => base64_encode(str_repeat('abcdefghij', intdiv($n, 10)))]); });
bench('p19.crc32', [250000, 500000, 1000000], function ($n) { return ev('CRC32(TO_UTF8(S))', ['S' => str_repeat('abcdefghij', intdiv($n, 10))]); });
// ---- P20: LINK plan compilation per invocation --------------------------------------------------------------------------
bench('p20.link.per-iteration', [100, 200, 400], function ($n) {
    $l = []; $r = []; for ($i = 0; $i < 20; $i++) { $l[] = ['id' => $i, 'v' => 'l' . $i]; $r[] = ['id' => $i, 'w' => 'r' . $i]; }
    return ev('COUNT(MAP(K, X, COUNT(LINK(R, S, A, B, A["id"] == B["id"]))))', ['K' => range(1, $n), 'R' => $l, 'S' => $r]);
});
// ---- P21: nested-loop LINK ------------------------------------------------------------------------------------------------
bench('p21.link.nested-false', [125, 250, 500], function ($n) {
    $l = []; $r = []; for ($i = 0; $i < $n; $i++) { $l[] = ['id' => $i]; $r[] = ['id' => $i]; }
    return ev('COUNT(LINK(R, S, A, B, A["id"] < 0))', ['R' => $l, 'S' => $r]);
});
bench('p21.link.nested-sum', [125, 250, 500], function ($n) {
    $l = []; $r = []; for ($i = 0; $i < $n; $i++) { $l[] = ['id' => $i]; $r[] = ['id' => $i]; }
    return ev('COUNT(LINK(R, S, A, B, A["id"] + B["id"] == 100))', ['R' => $l, 'S' => $r]);
});

// ---- P22: Hybrid::execute copies the caller's context --------------------------------------------------------------
bench('p22.hybrid.execute', [5000, 10000, 20000], function ($n) {
    $b = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
    $plan = Sql::planHybrid(Sel::compile('X = COUNT(T); ORDERS .> TAKE(3) .> MAP(_["ID"] + X)'), 'postgresql', $b);
    $rows = []; for ($i = 0; $i < $n; $i++) $rows[] = ['id' => $i, 'name' => 'n' . $i];
    $ctx = Value::fromNative(['T' => $rows]);
    return function () use ($plan, $ctx) {
        $out = null;
        for ($i = 0; $i < 20; $i++) $out = Sql::executeHybrid($plan, static fn () => [['ID' => 1], ['ID' => 2], ['ID' => 3]], $ctx);
        return $out;
    };
});
// ---- P23 / P24: SQL translation cost per node ------------------------------------------------------------------------
bench('p23.translate.wide-or', [40, 80, 160], function ($n) {
    $terms = []; for ($i = 1; $i <= $n; $i++) $terms[] = 'N > ' . $i . ' AND S $== "k' . $i . '"';
    $p = Sel::compile(implode(' OR ', $terms));
    $b = ['N' => Binding::column('n', 'o', 'NUM'), 'S' => Binding::column('s', 'o', 'TEXT')];
    return fn () => Sql::translate($p, 'mariadb', $b)->asValue();
});
bench('p24.translate.small-x', [500, 1000, 2000], function ($n) {
    $p = Sel::compile('LOWER(TRIM(S)) $== "abc" AND N + 1 > 2');
    $b = ['N' => Binding::column('n', 'o', 'NUM'), 'S' => Binding::column('s', 'o', 'TEXT')];
    return function () use ($p, $b, $n) { $r = null; for ($i = 0; $i < $n; $i++) $r = Sql::translate($p, 'postgresql', $b)->asValue(); return $r; };
});
// ---- P25: structural hash for scalar leaves ---------------------------------------------------------------------
bench('p25.dedupe.text', [50000, 100000, 200000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = 't' . ($i % 40000); return ev('COUNT(DEDUPE(L))', ['L' => $l]); });
bench('p25.distinct.text', [50000, 100000, 200000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = 't' . ($i % 40000); return ev('COUNT(DISTINCT(L))', ['L' => $l]); });
bench('p25.bucket.projected', [50000, 100000, 200000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = 't' . ($i % 4000); return ev('COUNT(BUCKET(L, X, X, COUNT(X)))', ['L' => $l]); });
// ---- P26: TOP ----------------------------------------------------------------------------------------------------------
bench('p26.top.small-limit', [50000, 100000, 200000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = ['v' => ($i * 7919) % 100003]; return ev('COUNT(TOP(R, _["v"], 10))', ['R' => $l]); });
bench('p26.top.limit-over-rows', [12500, 25000, 50000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = ['v' => ($i * 7919) % 100003]; return ev('COUNT(TOP(R, _["v"], 100000))', ['R' => $l]); });
// ---- P27: SUM -----------------------------------------------------------------------------------------------------------
bench('p27.sum.ints', [100000, 200000, 400000], function ($n) { return ev('SUM(L, X, X)', ['L' => range(1, $n)]); });
bench('p27.sum.decimals', [100000, 200000, 400000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = ($i % 1000) . '.' . ($i % 100); return ev('SUM(L, X, X)', ['L' => $l]); });
// ---- P28 (rejected; kept as the reference per-node cost) -----------------------------------------------------------
bench('p28.eval.count-map', [50000, 100000, 200000], function ($n) { return ev('COUNT(MAP(L, X, X))', ['L' => range(1, $n)]); });
// ---- P29: one-shot evaluation, literal folding ------------------------------------------------------------------
bench('p29.evaluate.one-shot', [1000, 2000, 4000], function ($n) {
    return function () use ($n) { $r = null; for ($i = 0; $i < $n; $i++) $r = Sel::evaluate('X * 2 + 1 > 10 AND S $== "a"', ['X' => $i, 'S' => 'a']); return $r; };
});
bench('p29.concat-literals.map', [50000, 100000, 200000], function ($n) { return ev('COUNT(MAP(L, X, "abc" & "def"))', ['L' => range(1, $n)]); });
// ---- P30: small items ----------------------------------------------------------------------------------------------------
bench('p30.regex.distinct-patterns', [2500, 5000, 10000], function ($n) { $l = []; for ($i = 0; $i < $n; $i++) $l[] = 'k' . $i; return ev('COUNT(FILTER(L, X, RMATCH("^" & X, "k1")))', ['L' => $l]); });
bench('p30.regex.long-pattern', [2000, 4000, 8000], function ($n) { return ev('RMATCH(P, S)', ['P' => str_repeat('ab|', $n) . 'c', 'S' => 'c']); });
bench('p30.planhybrid.prefix-search', [10, 40, 80], function ($n) {
    $b = ['ORDERS' => Binding::relation('orders', 'o', ['ID' => Binding::column('id', 'o', 'NUM')])];
    $src = 'ORDERS .> TAKE(100) .> DROP(1) .> FILTER(ABORT("x"))';
    for ($i = 0; $i < $n; $i++) $src .= $i % 2 ? ' .> TAKE(50)' : ' .> DROP(1)';
    $p = Sel::compile($src);
    return function () use ($p, $b) { $plan = Sql::planHybrid($p, 'postgresql', $b); return $plan->pureMemory ? 'mem' : ($plan->pureSql ? 'sql' : 'hybrid:' . substr($plan->sqlStatement->asStatement('params'), 0, 60)); };
});

if ($opts['json']) echo json_encode(['rev' => trim((string) @shell_exec('git rev-parse --short HEAD')), 'php' => PHP_VERSION, 'gmp' => $opts['gmp'], 'rows' => $rows], JSON_PRETTY_PRINT), "\n";
