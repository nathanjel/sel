<?php
// A ray tracer in SEL, and the host function it needs -- from PHP.
//
//   php examples/raytrace/php.php
//   php examples/raytrace/php.php --ppm 640 360 2 > mark.ppm
//   php examples/raytrace/php.php --bench report.json
//
// raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
// root has no exact decimal result -- so the application gives it one: SQRT(x, n)
// is the square root of x to n fractional digits (10 if n is left out). Like
// `/`, it is exact when it can be: a root with at most n fractional digits comes
// back at its minimal scale, and any other is rounded half away from zero to
// exactly n. It is computed on whole numbers, so every host gives every digit
// the same. For x = m / 10^s, x has an exact root when m, with the scale made
// even, is a perfect square -- which costs what x's size costs, whatever n is.
// Any other root is rounded from
//
//     sqrt(x) * 10^n = sqrt(m * 10^(2n - s))
//
// whose integer square root is the truncated answer; one comparison of whole
// numbers decides the rounding.
//
// With no arguments it prints a few square roots and a small frame; --ppm prints
// a frame of any size as PPM, and --bench times the frame the benchmarks use.
// The files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\Args;
use Sel\Program;
use Sel\Sel;
use Sel\SelError;
use Sel\Value;

const MAX_SCALE = 1000000;   // the cap ROUND's scale has (spec/limits.json)

// EXAMPLE-BEGIN sqrt
// PHP has whole numbers of any size only through ext-gmp, which SEL leaves
// optional, so the integer square root runs one of three ways: on native ints
// when the number fits one, on GMP when it is loaded, and otherwise by the
// schoolbook method on digit strings. A number that is not a native int is a
// canonical digit string (no sign, no leading zeros), the form Args::dec()
// hands a decimal's digits in.

/** floor(sqrt(v)) for 0 <= v < 10^$len: Newton's method, down from 10^ceil($len / 2). */
function isqrt_int(int $v, int $len): int
{
    if ($v < 2) {
        return $v;
    }
    $x = 10 ** intdiv($len + 1, 2);
    for (;;) {
        $y = ($x + intdiv($v, $x)) >> 1;
        if ($y >= $x) {
            return $x;
        }
        $x = $y;
    }
}

/** [r, q - r*r] for r = floor(sqrt(q)), as digit strings. */
function isqrt_rem(string $q): array
{
    static $gmp = null;
    $gmp ??= extension_loaded('gmp');
    if (strlen($q) <= 18) {                             // below 10^18, so below 2^63
        $v = (int) $q;
        $r = isqrt_int($v, strlen($q));
        return [(string) $r, (string) ($v - $r * $r)];
    }
    if ($gmp) {
        [$r, $rem] = gmp_sqrtrem(gmp_init($q, 10));
        return [gmp_strval($r), gmp_strval($rem)];
    }
    return isqrt_rem_schoolbook($q);
}

/**
 * isqrt_rem without GMP: one digit x of the root per pair of digits of q. With r
 * the root of the digits read so far and rem their remainder (at most 2r), the
 * next pair makes rem' = 100 rem + pair, and x is the largest digit with
 * (20r + x) x <= rem'. Native ints carry it while 200r + 99 fits (r below 10^16,
 * q up to 32 digits); past that rem and y = 2r are digit strings, handled in
 * 17-digit chunks. Each root digit costs O(len q), so a root costs O(len(q)^2),
 * where GMP's is quasi-linear: SQRT(2, 1000) takes some 20 ms this way and
 * SQRT(2, 16000) five seconds, so SQRT(2, 1000000) takes hours (GMP: 0.4 s).
 */
function isqrt_rem_schoolbook(string $q): array
{
    $len = strlen($q);
    $i = 18 - ($len & 1);                               // a head of 17 or 18 digits leaves pairs
    $head = (int) substr($q, 0, $i);
    $r = isqrt_int($head, $i);
    $rem = $head - $r * $r;
    for (; $i < $len && $r < 10 ** 16; $i += 2) {
        $rem = 100 * $rem + (int) substr($q, $i, 2);
        $y = 20 * $r;
        $x = min(9, intdiv($rem, $y));                  // at or above x, as (y + x) x >= y x
        while (($y + $x) * $x > $rem) {
            $x--;
        }
        $rem -= ($y + $x) * $x;
        $r = 10 * $r + $x;
    }
    [$y, $r, $rem] = [(string) (2 * $r), (string) $r, (string) $rem];
    for (; $i < $len; $i += 2) {
        $rem = ltrim($rem . substr($q, $i, 2), '0') ?: '0';
        // An upper bound on x from the leading digits: rem / 10y < (a + 1) / b, with
        // a and b their leading digits cut at the same place (rem < 100y + 100,
        // so rem has at most one digit more than 10y).
        $extra = strlen($rem) - strlen($y) - 1;
        $x = $extra < 0 ? 0 : min(9, intdiv((int) substr($rem, 0, 17 + $extra) + 1, (int) substr($y, 0, 17)));
        for (; $x > 0; $x--) {
            $t = digits_mul_small($y . $x, $x);         // (10y + x) x
            if (digits_cmp($t, $rem) <= 0) {
                $rem = digits_sub($rem, $t);
                break;
            }
        }
        $r .= $x;
        $y = digits_add_small($y . '0', 2 * $x);        // 2(10r + x) = 10y + 2x
    }
    return [$r, $rem];
}

function digits_cmp(string $a, string $b): int
{
    return strlen($a) <=> strlen($b) ?: strcmp($a, $b) <=> 0;
}

/** $a + $k for 0 <= $k <= 18. */
function digits_add_small(string $a, int $k): string
{
    for ($i = strlen($a) - 1; $k > 0 && $i >= 0; $i--) {
        $d = ord($a[$i]) - 48 + $k;
        $a[$i] = chr(48 + $d % 10);
        $k = intdiv($d, 10);
    }
    return $k > 0 ? $k . $a : $a;
}

/** $a * $k for 0 <= $k <= 9, 17 digits at a time: 9 * 10^17 + 9 fits. */
function digits_mul_small(string $a, int $k): string
{
    $chunks = [];
    $carry = 0;
    for ($i = strlen($a); $i > 0; $i -= 17) {
        $p = (int) substr($a, max(0, $i - 17), min(17, $i)) * $k + $carry;
        $carry = intdiv($p, 10 ** 17);
        $chunks[] = sprintf('%017d', $p % 10 ** 17);
    }
    return ltrim($carry . implode('', array_reverse($chunks)), '0') ?: '0';
}

/** $a - $b for $a >= $b, 17 digits at a time. */
function digits_sub(string $a, string $b): string
{
    $chunks = [];
    $borrow = 0;
    for ($i = strlen($a), $j = strlen($b); $i > 0; $i -= 17, $j -= 17) {
        $d = (int) substr($a, max(0, $i - 17), min(17, $i)) - $borrow
            - ($j > 0 ? (int) substr($b, max(0, $j - 17), min(17, $j)) : 0);
        $borrow = $d < 0 ? 1 : 0;
        $chunks[] = sprintf('%017d', $d + $borrow * 10 ** 17);
    }
    return ltrim(implode('', array_reverse($chunks)), '0') ?: '0';
}

Sel::registerFunction('SQRT', 1, 2, function (Args $args): Value {
    $x = $args->dec(0);
    $n = $args->count() > 1 ? $args->nonNegInt(1) : 10;
    if ($n > MAX_SCALE) {
        throw new SelError('E_RANGE', 'SQRT: scale above 1000000', $args->posOf(1));
    }
    if ($x['neg']) {
        throw new SelError('E_RANGE', 'SQRT of a negative number', $args->posOf(0));
    }
    // An exact root comes from x itself, so it costs what x's digits cost, not
    // n: with x = m / 10^s and s made even, sqrt(x) = sqrt(m) / 10^(s/2), which
    // ends exactly when m is a perfect square.
    [$m, $s] = [$x['digits'], $x['scale']];
    [$m2, $s2] = $s % 2 === 0 ? [$m, $s] : [$m === '0' ? '0' : $m . '0', $s + 1];
    [$r, $rem] = isqrt_rem($m2);
    if ($rem === '0') {                                 // drop the zeros it does not need: count, cut once
        $scale = intdiv($s2, 2);
        $zeros = $r === '0' ? $scale : min($scale, strlen($r) - strlen(rtrim($r, '0')));
        if ($scale - $zeros <= $n) {                    // else more digits than n allows: rounded below
            $r = substr($r, 0, strlen($r) - $zeros) ?: '0';   // '' only for zero
            return Value::num(['neg' => false, 'digits' => $r, 'scale' => $scale - $zeros]);
        }
    }
    // Any other root is rounded to n digits: sqrt(x) * 10^n = sqrt(m * 10^e) with
    // e = 2n - s. When e is negative that is sqrt(q + f / 10^-e), q being m
    // without its last -e digits and f those digits.
    $e = 2 * $n - $s;
    if ($e >= 0) {
        [$q, $f] = [$m === '0' ? '0' : $m . str_repeat('0', $e), ''];
    } elseif (strlen($m) > -$e) {
        [$q, $f] = [substr($m, 0, $e), substr($m, $e)];
    } else {
        [$q, $f] = ['0', str_pad($m, -$e, '0', STR_PAD_LEFT)];
    }
    // q = r^2 + rem with 0 <= rem <= 2r, and the root is at least r + 1/2 (the
    // other hosts' 4v >= (2r + 1)^2 * p) when rem + f / 10^-e >= r + 1/4: when
    // rem > r, or rem = r and f / 10^-e >= 1/4 (f's first two digits, padded, >= 25).
    [$r, $rem] = isqrt_rem($q);
    if (digits_cmp($rem, $r) > 0 || ($rem === $r && strcmp(substr($f . '00', 0, 2), '25') >= 0)) {
        $r = digits_add_small($r, 1);                   // at or past the half: away from zero
    }
    return Value::num(['neg' => false, 'digits' => $r, 'scale' => $n]);
});
// EXAMPLE-END sqrt

function read(string $name): string
{
    return file_get_contents(__DIR__ . '/' . $name);
}

function frame(Program $scene, string $w, string $h, string $ss): string
{
    return $scene->run(Value::fromNative(['W' => $w, 'H' => $h, 'SS' => $ss]))->asText();
}

function main(array $argv): int
{
    $scene = Sel::compile(read('raytrace.sel'));
    if (($argv[0] ?? null) === '--ppm' && count($argv) === 4) {
        echo frame($scene, $argv[1], $argv[2], $argv[3]);
        return 0;
    }
    if (($argv[0] ?? null) === '--bench' && count($argv) === 2) {
        return bench($scene, $argv[1]);
    }
    if ($argv) {
        fwrite(STDERR, "usage: php.php [--ppm W H SS | --bench REPORT.json]\n");
        return 2;
    }

    echo "1. SQRT, the one function the ray tracer needs from the host\n";
    foreach (['SQRT(2)', 'SQRT(2, 40)', 'SQRT(2.25)', 'SQRT(1000000, 3)', 'SQRT(0.000)',
              'SQRT(6.25, 3000)', 'SQRT(0.0025, 1)', 'SQRT(0.0225, 1)', 'SQRT(99.999999, 2)',
              'SQRT(POWER(12345678901234567890, 2))', 'SQRT(POWER(10, 41) + 1, 3)',
              'SQRT(-4)', 'SQRT("four")', 'SQRT(4, -1)', 'SQRT(4, 0.5)', 'SQRT(4, 1000001)'] as $src) {
        try {
            $result = Sel::compile($src)->run(Value::none())->asText();
        } catch (SelError $e) {
            $result = "{$e->code} at {$e->line}:{$e->col}";
        }
        printf("   %-38s => %s\n", $src, $result);
    }

    echo "2. the scene, 64 x 36, one ray per pixel\n";
    $img = frame($scene, '64', '36', '1');
    $crc = Sel::compile('CRC32(IMG)')->run(Value::fromNative(['IMG' => $img]))->asText();
    printf("   %d bytes of PPM, CRC32 %s\n", strlen($img), $crc);
    return 0;
}

// The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
// pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5).
function bench(Program $scene, string $report): int
{
    $warmups = (int) (getenv('RAYTRACE_WARMUPS') === false ? 2 : getenv('RAYTRACE_WARMUPS'));
    $runs = (int) (getenv('RAYTRACE_RUNS') === false ? 5 : getenv('RAYTRACE_RUNS'));
    $crc = Sel::compile('CRC32(IMG)');
    $samples = $outputs = [];
    for ($i = 0; $i < $warmups + $runs; $i++) {
        $context = Value::fromNative(['W' => '64', 'H' => '36', 'SS' => '1']);
        $t = hrtime(true);
        $img = $scene->run($context)->asText();
        $elapsed = (hrtime(true) - $t) / 1e6;
        if ($i >= $warmups) {
            $samples[] = $elapsed;
            $outputs[] = $crc->run(Value::fromNative(['IMG' => $img]))->asText();
        }
    }
    file_put_contents($report, json_encode(['samples_ms' => $samples, 'outputs' => $outputs,
                                            'warmups' => $warmups, 'runs' => $runs]));
    return 0;
}

exit(main(array_slice($argv, 1)));
