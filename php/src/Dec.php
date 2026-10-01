<?php
// Exact decimal arithmetic on digit strings. See spec/SPEC.md §4.
//
// Checked native mantissas accelerate small arithmetic. GMP is optional;
// digit-string arithmetic preserves exact results without extensions.
//
// A decimal is ['neg' => bool, 'digits' => string, 'scale' => int], meaning
// (neg ? -1 : 1) * digits / 10^scale.
// Additional native/nativeDigits/nativeNeg fields cache checked conversion;
// callers may still supply the original three-field descriptors.
//
// With ext-gmp, a result too big for a native int is lazy (item 1, 2026-10-01):
// 'digits' is null and 'gmp' holds the magnitude, and the digits are written only
// when text is asked for -- format(), digits(), eager(). Every function here takes
// either form. Value::asDecimal() and Args::dec() hand a host today's array, and
// checked() accepts the lazy one back.

declare(strict_types=1);

namespace Sel;

require_once __DIR__ . '/Limits.php';   // the caps below are defined from it

final class Dec
{
    public const DIV_SCALE = Limits::DIV_SCALE;   // spec/limits.json
    /** @var list<int>|null */
    private static ?array $nativePow10 = null;

    private static ?bool $hasGmp = null;
    /** @var list<int> */
    private static array $pow10Int = [
        1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000
    ];

    /**
     * A digit string as a GMP number, in base 10. `gmp_add("010", ...)` on a
     * bare string detects the base from the prefix, so "010" is octal 8 and "0x1"
     * is hex: harmless for the canonical digits every SEL number carries, wrong
     * for the un-normalised descriptor a host can hand in (PHP-C41).
     */
    private static function gmpInt(string $digits): \GMP
    {
        self::$conversions++;
        return gmp_init($digits, 10);
    }

    /** A GMP magnitude's digits; the other half of the conversion count. */
    private static function gmpStr(\GMP $g): string
    {
        self::$conversions++;
        return gmp_strval($g);
    }

    /**
     * Test hook (item 1): base-10 conversions between digit strings and GMP so
     * far, both ways. A test resets it and reads how many a computation cost.
     */
    public static int $conversions = 0;

    /**
     * Item 1: when true (and ext-gmp is there), a result too big for a native int
     * keeps its magnitude as GMP and writes its digits only when text is asked
     * for. Public so a test can hold both forms to the same answers.
     */
    public static bool $lazyDigits = false;

    /** @var array<int, \GMP> */
    private static array $pow10Gmp = [];
    private static int $pow10GmpDigits = 0;
    private static ?\GMP $int64MinMagnitude = null;

    private static function lazy(): bool
    {
        return self::$lazyDigits && self::hasGmp();
    }

    /** 10^k as GMP, cached up to about four million digits in all (item 1). */
    private static function pow10Gmp(int $k): \GMP
    {
        if (isset(self::$pow10Gmp[$k])) {
            return self::$pow10Gmp[$k];
        }
        $p = gmp_pow(10, $k);
        if (self::$pow10GmpDigits + $k > 4194304) {
            self::$pow10Gmp = [];
            self::$pow10GmpDigits = 0;
        }
        self::$pow10GmpDigits += $k;
        return self::$pow10Gmp[$k] = $p;
    }

    /**
     * $d's magnitude as GMP: a lazy value's own, a native one without a digit
     * string, anything else parsed (item 1).
     */
    private static function gmpOf(array $d): \GMP
    {
        if ($d['digits'] === null) {
            return $d['gmp'];
        }
        $n = self::intMantissa($d);
        if ($n !== null && $n !== PHP_INT_MIN) {
            return gmp_init($n < 0 ? -$n : $n);
        }
        return self::gmpInt($d['digits']);
    }

    /**
     * A result from a GMP magnitude: today's array when it fits a native int (so
     * the arrays PHP-P13/P15 hold identical still are), else the lazy form --
     * digits null, the magnitude kept as GMP until text is asked for (item 1). A
     * lazy value is never zero and never fits a native int.
     */
    private static function fromGmp(bool $neg, \GMP $mag, int $scale): array
    {
        if (gmp_cmp($mag, PHP_INT_MAX) <= 0) {
            $v = gmp_intval($mag);
            return self::make($neg, (string) $v, $scale, $neg ? -$v : $v);
        }
        self::$int64MinMagnitude ??= gmp_init('9223372036854775808', 10);
        if ($neg && gmp_cmp($mag, self::$int64MinMagnitude) === 0) {
            return self::make(true, '9223372036854775808', $scale);
        }
        return ['neg' => $neg, 'digits' => null, 'scale' => $scale,
                'native' => null, 'nativeDigits' => null, 'nativeNeg' => $neg, 'gmp' => $mag];
    }

    /** The digits of $d, written from its GMP magnitude when it is lazy (item 1). */
    public static function digits(array $d): string
    {
        return $d['digits'] ?? self::gmpStr($d['gmp']);
    }

    /** $d with its digits written out: today's array, never lazy (item 1). */
    public static function eager(array $d): array
    {
        if ($d['digits'] !== null) {
            return $d;
        }
        return self::make($d['neg'], self::gmpStr($d['gmp']), $d['scale']);
    }

    /** Whether two decimals have the same digits, whatever their forms. */
    public static function sameDigits(array $a, array $b): bool
    {
        if ($a['digits'] !== null && $b['digits'] !== null) {
            return $a['digits'] === $b['digits'];
        }
        if ($a['digits'] === null && $b['digits'] === null) {
            return gmp_cmp($a['gmp'], $b['gmp']) === 0;
        }
        return self::digits($a) === self::digits($b);
    }

    /**
     * Whether a positive GMP magnitude has more than MAX_INT_DIGITS + $scale
     * digits, that is reaches 10^L. Its bit length decides, by gmp_scan1, unless
     * it lies within a few bits of 10^L; only then is 10^L built and compared
     * (item 1). 3.321928 < log2(10) < 3.321929.
     */
    private static function exceedsIntDigits(\GMP $g, int $scale): bool
    {
        $L = self::MAX_INT_DIGITS + $scale;
        if (gmp_scan1($g, intdiv($L * 3321928, 1000000)) === -1) {
            return false;   // below 2^floor(L * 3.321928), which is at most 10^L
        }
        if (gmp_scan1($g, intdiv($L * 3321929, 1000000) + 1) !== -1) {
            return true;    // at least 2^(floor(L * 3.321929) + 1), which is past 10^L
        }
        return gmp_cmp($g, self::pow10Gmp($L)) >= 0;
    }

    /**
     * Test hook: false makes every operation take the pure-PHP digit-string
     * paths on a machine that has ext-gmp; null goes back to asking the runtime.
     * There is no reason to call it outside a test.
     */
    public static function forceGmp(?bool $on): void
    {
        self::$hasGmp = $on === null ? null : ($on && extension_loaded('gmp'));
    }

    /**
     * How many rows of the limb product may add into the accumulators before
     * they are carried. A row adds at most (10^7 - 1)^2 < 10^14 to a slot, and
     * PHP turns an integer overflow into a float, so a slot must never see more
     * than PHP_INT_MAX / 10^14 = 92,233 of them: past that the result was a
     * float and the next intdiv() an uncaught TypeError (PHP-C17). Public so a
     * test can lower it to force the carry every row.
     */
    public static int $mulCarryEvery = 50000;

    /**
     * Test hook: false makes parse() and div() take their general
     * digit-string paths on operands the native fast paths would handle, so a
     * test can hold the two to identical results (PHP-P13, P15). A native-integer mod() was tried and dropped: 5-8% over the
     * general path, below what earns a second code path.
     */
    public static bool $fastPaths = true;

    private static function hasGmp(): bool
    {
        if (self::$hasGmp === null) {
            self::$hasGmp = extension_loaded('gmp');
        }
        return self::$hasGmp;
    }

    // spec/SPEC.md §6.4. These bound the *value*; ROUND's scale cap and POWER's
    // exponent cap bound *arguments*, and an argument cap is not a value cap —
    // POWER's base is unbounded, so nesting one POWER inside another multiplies
    // the exponents and steps straight over the exponent cap. Two independent
    // numbers rather than one shared budget, because ROUND(99.5, 1000000) is
    // 1 000 002 digits and legal under the scale cap: a shared budget would have
    // shrunk what the spec already sanctions.
    public const MAX_INT_DIGITS = Limits::MAX_INT_DIGITS;
    public const MAX_FRAC_DIGITS = Limits::MAX_FRAC_DIGITS;

    // --- digit-string primitives (non-negative, no leading zeros) ------------

    private static function strip(string $s): string
    {
        $t = ltrim($s, '0');
        return $t === '' ? '0' : $t;
    }

    private static function cmpAbs(string $a, string $b): int
    {
        $la = strlen($a);
        $lb = strlen($b);
        if ($la !== $lb) {
            return $la < $lb ? -1 : 1;
        }
        // strcmp, not `<`: same length already, and `<` on two digit strings is a
        // PHP numeric-string comparison (4-5x slower, and only kept exact by
        // PHP's own overflow fallback).
        $c = strcmp($a, $b);
        return $c < 0 ? -1 : ($c > 0 ? 1 : 0);
    }

    private static function addAbs(string $a, string $b): string
    {
        if (self::hasGmp()) {
            return self::gmpStr(gmp_add(self::gmpInt($a), self::gmpInt($b)));
        }

        $la = strlen($a);
        $lb = strlen($b);
        if ($la <= 18 && $lb <= 18) {
            return (string) ((int) $a + (int) $b);
        }

        $out = '';
        $carry = 0;
        $i = $la;
        $j = $lb;
        while ($i > 0 || $j > 0 || $carry > 0) {
            $chunkA = 0;
            if ($i > 0) {
                $startA = max(0, $i - 14);
                $chunkA = (int) substr($a, $startA, $i - $startA);
                $i = $startA;
            }
            $chunkB = 0;
            if ($j > 0) {
                $startB = max(0, $j - 14);
                $chunkB = (int) substr($b, $startB, $j - $startB);
                $j = $startB;
            }
            $sum = $chunkA + $chunkB + $carry;
            if ($sum >= 100000000000000) { // 10^14
                $rem = $sum - 100000000000000;
                $carry = 1;
            } else {
                $rem = $sum;
                $carry = 0;
            }
            if ($i > 0 || $j > 0 || $carry > 0) {
                $out = sprintf('%014d', $rem) . $out;
            } else {
                $out = (string) $rem . $out;
            }
        }
        return $out === '' ? '0' : $out;
    }

    /** Requires a >= b. */
    private static function subAbs(string $a, string $b): string
    {
        if (self::hasGmp()) {
            return self::gmpStr(gmp_sub(self::gmpInt($a), self::gmpInt($b)));
        }

        $la = strlen($a);
        if ($la <= 18) {
            return (string) ((int) $a - (int) $b);
        }

        $out = '';
        $borrow = 0;
        $i = $la;
        $j = strlen($b);
        while ($i > 0) {
            $startA = max(0, $i - 14);
            $chunkA = (int) substr($a, $startA, $i - $startA);
            $i = $startA;

            $chunkB = 0;
            if ($j > 0) {
                $startB = max(0, $j - 14);
                $chunkB = (int) substr($b, $startB, $j - $startB);
                $j = $startB;
            }

            $diff = $chunkA - $chunkB - $borrow;
            if ($diff < 0) {
                $diff += 100000000000000;
                $borrow = 1;
            } else {
                $borrow = 0;
            }
            if ($i > 0) {
                $out = sprintf('%014d', $diff) . $out;
            } else {
                $out = (string) $diff . $out;
            }
        }
        return self::strip($out);
    }

    private static function mulAbs(string $a, string $b): string
    {
        if ($a === '0' || $b === '0') {
            return '0';
        }
        if (self::hasGmp()) {
            return self::gmpStr(gmp_mul(self::gmpInt($a), self::gmpInt($b)));
        }

        $la = strlen($a);
        $lb = strlen($b);
        if ($la + $lb <= 18) {
            return (string) ((int) $a * (int) $b);
        }

        if ($la <= 18 && $lb <= 18) {
            $a0 = $la > 9 ? (int) substr($a, -9) : (int) $a;
            $a1 = $la > 9 ? (int) substr($a, 0, -9) : 0;
            $b0 = $lb > 9 ? (int) substr($b, -9) : (int) $b;
            $b1 = $lb > 9 ? (int) substr($b, 0, -9) : 0;

            $p0 = $a0 * $b0;
            $p1 = $a0 * $b1 + $a1 * $b0;
            $p2 = $a1 * $b1;

            $c0 = $p0 % 1000000000;
            $carry1 = intdiv($p0, 1000000000);
            $p1 += $carry1;
            $c1 = $p1 % 1000000000;
            $carry2 = intdiv($p1, 1000000000);
            $c2 = $p2 + $carry2;

            if ($c2 > 0) {
                return (string) $c2 . sprintf('%09d%09d', $c1, $c0);
            }
            if ($c1 > 0) {
                return (string) $c1 . sprintf('%09d', $c0);
            }
            return (string) $c0;
        }

        return self::fromLimbs(self::mulLimbs(self::toLimbs($a), self::toLimbs($b)));
    }

    /** Operand sizes (in limbs) below which schoolbook beats Karatsuba's bookkeeping. */
    public static int $karatsubaFrom = 40;

    /**
     * Product of two limb arrays (base 10^7, least significant first, no leading
     * zero limb): schoolbook below $karatsubaFrom limbs, Karatsuba above it
     * (PHP-P5). Quadratic schoolbook made squaring a 100,000-digit number take
     * 13 s without ext-gmp; three half-size products instead of four make it
     * ~n^1.58. Exact integer arithmetic only.
     *
     * @param list<int> $a @param list<int> $b @return list<int>
     */
    private static function mulLimbs(array $a, array $b): array
    {
        $na = count($a);
        $nb = count($b);
        if ($na < $nb) {
            [$a, $b, $na, $nb] = [$b, $a, $nb, $na];
        }
        if ($nb < self::$karatsubaFrom) {
            return self::mulLimbsSchool($a, $b);
        }
        $h = intdiv($na + 1, 2);
        if ($nb <= $h) {
            // Lopsided: only the long operand is split.
            $p0 = self::mulLimbs(self::trimLimbs(array_slice($a, 0, $h)), $b);
            $p1 = self::mulLimbs(array_slice($a, $h), $b);
            return self::addShifted($p0, $p1, $h);
        }
        $a0 = self::trimLimbs(array_slice($a, 0, $h));
        $a1 = array_slice($a, $h);
        $b0 = self::trimLimbs(array_slice($b, 0, $h));
        $b1 = array_slice($b, $h);
        $z0 = self::mulLimbs($a0, $b0);
        $z2 = self::mulLimbs($a1, $b1);
        $z1 = self::subLimbs(self::subLimbs(self::mulLimbs(self::addLimbs($a0, $a1), self::addLimbs($b0, $b1)), $z0), $z2);
        return self::addShifted(self::addShifted($z0, $z1, $h), $z2, 2 * $h);
    }

    /** @param list<int> $limbs @return list<int> */
    private static function trimLimbs(array $limbs): array
    {
        $n = count($limbs);
        while ($n > 1 && $limbs[$n - 1] === 0) {
            $n--;
        }
        return $n === count($limbs) ? $limbs : array_slice($limbs, 0, $n);
    }

    /** @param list<int> $a @param list<int> $b @return list<int> */
    private static function addLimbs(array $a, array $b): array
    {
        if (count($a) < count($b)) {
            [$a, $b] = [$b, $a];
        }
        $carry = 0;
        $nb = count($b);
        foreach ($a as $i => $x) {
            $t = $x + ($i < $nb ? $b[$i] : 0) + $carry;
            if ($t >= 10000000) {
                $t -= 10000000;
                $carry = 1;
            } else {
                $carry = 0;
            }
            $a[$i] = $t;
        }
        if ($carry) {
            $a[] = 1;
        }
        return $a;
    }

    /** a - b for a >= b. @param list<int> $a @param list<int> $b @return list<int> */
    private static function subLimbs(array $a, array $b): array
    {
        $borrow = 0;
        $nb = count($b);
        foreach ($a as $i => $x) {
            $t = $x - ($i < $nb ? $b[$i] : 0) - $borrow;
            if ($t < 0) {
                $t += 10000000;
                $borrow = 1;
            } else {
                $borrow = 0;
            }
            $a[$i] = $t;
        }
        return self::trimLimbs($a);
    }

    /** x + y * base^shift. @param list<int> $x @param list<int> $y @return list<int> */
    private static function addShifted(array $x, array $y, int $shift): array
    {
        if ($y === [0]) {
            return $x;
        }
        $nx = count($x);
        $ny = count($y);
        $n = max($nx, $ny + $shift);
        $out = $x;
        for ($i = $nx; $i < $n; $i++) {
            $out[$i] = 0;
        }
        $carry = 0;
        for ($i = $shift; $i < $n; $i++) {
            $j = $i - $shift;
            $t = $out[$i] + ($j < $ny ? $y[$j] : 0) + $carry;
            if ($t >= 10000000) {
                $t -= 10000000;
                $carry = 1;
            } else {
                $carry = 0;
            }
            $out[$i] = $t;
            if ($j >= $ny && $carry === 0) {
                break;
            }
        }
        if ($carry) {
            $out[] = 1;
        }
        return self::trimLimbs($out);
    }

    /**
     * The schoolbook product: accumulate limb products, rippling the carries back
     * under 10^7 every $mulCarryEvery rows so no slot overflows a native int.
     *
     * @param list<int> $limbsA @param list<int> $limbsB @return list<int>
     */
    private static function mulLimbsSchool(array $limbsA, array $limbsB): array
    {
        $na = count($limbsA);
        $nb = count($limbsB);
        $acc = array_fill(0, $na + $nb, 0);

        for ($i = 0; $i < $na; $i++) {
            $ai = $limbsA[$i];
            if ($ai === 0) continue;
            for ($j = 0; $j < $nb; $j++) {
                $acc[$i + $j] += $ai * $limbsB[$j];
            }
            if (($i + 1) % self::$mulCarryEvery === 0) {
                // Ripple every slot back under 10^7. A partial sum is below the
                // full product, which fits the na + nb slots, so no carry
                // leaves the array.
                $c = 0;
                for ($k = 0, $m = $na + $nb; $k < $m; $k++) {
                    $t = $acc[$k] + $c;
                    $acc[$k] = $t % 10000000;
                    $c = intdiv($t, 10000000);
                }
            }
        }

        $carry = 0;
        $nc = count($acc);
        for ($k = 0; $k < $nc; $k++) {
            $t = $acc[$k] + $carry;
            $acc[$k] = $t % 10000000;
            $carry = intdiv($t, 10000000);
        }
        while ($carry > 0) {
            $acc[] = $carry % 10000000;
            $carry = intdiv($carry, 10000000);
        }
        return self::trimLimbs($acc);
    }

    /**
     * @return array{0:string,1:string}|null
     */
    private static function divModAbs(string $a, string $b): ?array
    {
        if ($b === '0') {
            return null;
        }
        $cmp = self::cmpAbs($a, $b);
        if ($cmp < 0) {
            return ['0', $a];
        }
        if ($cmp === 0) {
            return ['1', '0'];
        }

        $lb = strlen($b);
        if ($b[0] === '1' && $lb - 1 === strspn($b, '0', 1)) {
            $k = $lb - 1;
            if ($k === 0) {
                return [$a, '0'];
            }
            $la = strlen($a);
            if ($la <= $k) {
                return ['0', $a];
            }
            $q = substr($a, 0, -$k);
            $r = self::strip(substr($a, -$k));
            return [$q, $r];
        }

        $la = strlen($a);
        if ($la <= 18) {
            $ia = (int) $a;
            $ib = (int) $b;
            return [(string) intdiv($ia, $ib), (string) ($ia % $ib)];
        }

        if (self::hasGmp()) {
            [$q, $r] = gmp_div_qr(self::gmpInt($a), self::gmpInt($b));
            return [self::gmpStr($q), self::gmpStr($r)];
        }

        if ($lb <= 9) {
            $ib = (int) $b;
            $rem = 0;
            $q = '';
            for ($i = 0; $i < $la; $i += 9) {
                $chunkLen = min(9, $la - $i);
                $chunk = (int) substr($a, $i, $chunkLen);
                $curr = $rem * self::$pow10Int[$chunkLen] + $chunk;
                $qChunk = intdiv($curr, $ib);
                $rem = $curr % $ib;
                if ($q !== '' || $qChunk > 0) {
                    $q .= $q === '' ? (string) $qChunk : sprintf("%0{$chunkLen}d", $qChunk);
                }
            }
            return [$q === '' ? '0' : $q, (string) $rem];
        }

        return self::divModLimbs($a, $b);
    }

    /**
     * Digits to base-10^7 limbs, least significant first (the representation
     * mulAbs multiplies in).
     *
     * @return list<int>
     */
    private static function toLimbs(string $digits): array
    {
        $limbs = [];
        for ($i = strlen($digits); $i > 0; $i -= 7) {
            $start = max(0, $i - 7);
            $limbs[] = (int) substr($digits, $start, $i - $start);
        }
        return $limbs;
    }

    /** @param list<int> $limbs most significant limb last; trailing zero limbs are dropped */
    private static function fromLimbs(array $limbs): string
    {
        $i = count($limbs) - 1;
        while ($i > 0 && $limbs[$i] === 0) $i--;
        $out = (string) $limbs[$i];
        for ($i--; $i >= 0; $i--) {
            $out .= sprintf('%07d', $limbs[$i]);
        }
        return $out;
    }

    /**
     * Knuth's algorithm D over base-10^7 limbs (PHP-P4): quotient and remainder
     * of two non-negative digit strings, `$b` longer than 9 digits so it has at
     * least two limbs, `$a` > `$b`. Integer arithmetic only — every product is
     * below 10^14, well inside a native int — and O(la * lb / 49) limb steps
     * where the digit-at-a-time loop it replaces did a string compare and a
     * string subtract per digit, each O(lb), so quadratic with a large constant.
     *
     * @return array{0:string,1:string}
     */
    private static function divModLimbs(string $a, string $b): array
    {
        $base = 10000000;
        $u = self::toLimbs($a);
        $v = self::toLimbs($b);
        $n = count($v);
        $m = count($u) - $n;
        // Normalise so the divisor's top limb is at least base/2.
        $d = intdiv($base, $v[$n - 1] + 1);
        if ($d !== 1) {
            $carry = 0;
            foreach ($u as $i => $x) {
                $t = $x * $d + $carry;
                $u[$i] = $t % $base;
                $carry = intdiv($t, $base);
            }
            $u[] = $carry;
            $carry = 0;
            foreach ($v as $i => $x) {
                $t = $x * $d + $carry;
                $v[$i] = $t % $base;
                $carry = intdiv($t, $base);
            }
        } else {
            $u[] = 0;
        }
        $vTop = $v[$n - 1];
        $vNext = $v[$n - 2];
        $q = array_fill(0, $m + 1, 0);
        for ($j = $m; $j >= 0; $j--) {
            $num = $u[$j + $n] * $base + $u[$j + $n - 1];
            $qhat = intdiv($num, $vTop);
            $rhat = $num % $vTop;
            while ($qhat >= $base || $qhat * $vNext > $rhat * $base + $u[$j + $n - 2]) {
                $qhat--;
                $rhat += $vTop;
                if ($rhat >= $base) break;
            }
            // Multiply and subtract: u[j .. j+n] -= qhat * v.
            $borrow = 0;
            $carry = 0;
            for ($i = 0; $i < $n; $i++) {
                $p = $qhat * $v[$i] + $carry;
                $carry = intdiv($p, $base);
                $t = $u[$i + $j] - ($p % $base) - $borrow;
                if ($t < 0) {
                    $t += $base;
                    $borrow = 1;
                } else {
                    $borrow = 0;
                }
                $u[$i + $j] = $t;
            }
            $t = $u[$j + $n] - $carry - $borrow;
            if ($t < 0) {
                $t += $base;
                $borrow = 1;
            } else {
                $borrow = 0;
            }
            $u[$j + $n] = $t;
            if ($borrow) {
                // qhat was one too large: add the divisor back.
                $qhat--;
                $carry = 0;
                for ($i = 0; $i < $n; $i++) {
                    $t = $u[$i + $j] + $v[$i] + $carry;
                    if ($t >= $base) {
                        $t -= $base;
                        $carry = 1;
                    } else {
                        $carry = 0;
                    }
                    $u[$i + $j] = $t;
                }
                $u[$j + $n] = ($u[$j + $n] + $carry) % $base;
            }
            $q[$j] = $qhat;
        }
        // The remainder is u[0 .. n-1] divided back by the normalisation factor.
        $r = array_slice($u, 0, $n);
        if ($d !== 1) {
            $rem = 0;
            for ($i = $n - 1; $i >= 0; $i--) {
                $t = $rem * $base + $r[$i];
                $r[$i] = intdiv($t, $d);
                $rem = $t % $d;
            }
        }
        return [self::fromLimbs($q), self::fromLimbs($r)];
    }

    private static function scaleUp(string $digits, int $k): string
    {
        if ($k <= 0) {
            return $digits;
        }
        return $digits === '0' ? '0' : $digits . str_repeat('0', $k);
    }

    private static function pow10(int $k): string
    {
        return $k === 0 ? '1' : '1' . str_repeat('0', $k);
    }

    /**
     * Return a native mantissa when it is safe to do so. PHP turns an
     * overflowing integer operation into a float, so the fast path is always
     * checked with is_int() and the digit-string implementation remains the
     * exact fallback at the boundary.
     */
    private static function intMantissa(array $d): ?int
    {
        if ($d['digits'] === null) {
            return null;   // a lazy value never fits a native int
        }
        // Decimal arrays are also accepted from host code. Validate the cache
        // against its source fields so edits to those arrays cannot stale it.
        if (array_key_exists('native', $d)
            && ($d['nativeDigits'] ?? null) === $d['digits']
            && ($d['nativeNeg'] ?? null) === $d['neg']) {
            return $d['native'];
        }
        return self::parseMantissa($d['neg'], $d['digits']);
    }

    private static function parseMantissa(bool $neg, string $digits): ?int
    {
        $max = (string) PHP_INT_MAX;
        $length = strlen($digits);
        $maxLength = strlen($max);
        if ($length > $maxLength
            || ($length === $maxLength && strcmp($digits, $max) > 0)) {
            // Compare lexically, never via PHP's numeric-string float coercion.
            return $neg && $digits === substr((string) PHP_INT_MIN, 1)
                ? PHP_INT_MIN : null;
        }
        $value = (int) $digits;
        return $neg ? -$value : $value;
    }

    private static function intPow10(int $scale): ?int
    {
        if ($scale < 0) return null;
        if (self::$nativePow10 === null) {
            self::$nativePow10 = [1];
            for ($i = 1; $i <= 18; $i++) {
                $next = self::$nativePow10[$i - 1] * 10;
                if (!is_int($next)) break;
                self::$nativePow10[] = $next;
            }
        }
        return self::$nativePow10[$scale] ?? null;
    }

    /** @return array{0:int,1:int,2:int}|null */
    private static function fastAligned(array $a, array $b): ?array
    {
        $left = self::intMantissa($a);
        $right = self::intMantissa($b);
        if ($left === null || $right === null) return null;
        if ($a['scale'] === $b['scale']) return [$left, $right, $a['scale']];
        $scale = max($a['scale'], $b['scale']);
        $lf = self::intPow10($scale - $a['scale']);
        $rf = self::intPow10($scale - $b['scale']);
        if ($lf === null || $rf === null) return null;
        $left *= $lf;
        $right *= $rf;
        if (!is_int($left) || !is_int($right)) return null;
        return [$left, $right, $scale];
    }

    /** @return array{neg:bool,digits:string,scale:int}|null */
    private static function fromIntFast(int $value, int $scale): ?array
    {
        $neg = $value < 0;
        $text = (string) $value;
        return self::make($neg, $neg ? substr($text, 1) : $text, $scale, $value);
    }

    // --- construction -------------------------------------------------------

    /** @return array{neg:bool,digits:string,scale:int} */
    private static function make(bool $neg, string $digits, int $scale, ?int $native = null): array
    {
        $neg = $digits === '0' ? false : $neg;
        return ['neg' => $neg, 'digits' => $digits, 'scale' => $scale,
                'native' => $native ?? self::parseMantissa($neg, $digits),
                'nativeDigits' => $digits, 'nativeNeg' => $neg];
    }

    /**
     * Refuses a value SEL cannot hold, where it is built rather than where it is
     * rendered. Every operation that can grow a number passes its result through
     * here, so POWER — repeated squaring over mul — trips on an intermediate and
     * the enormous value is never allocated: without that, nesting POWER three
     * deep exhausted PHP's memory before any check could run.
     *
     * @param array{neg:bool,digits:string,scale:int} $d
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array{neg:bool,digits:string,scale:int}
     */
    private static function guard(array $d, ?array $pos): array
    {
        if ($d['scale'] > self::MAX_FRAC_DIGITS) {
            fail('E_RANGE', 'number has more than ' . self::MAX_FRAC_DIGITS . ' fractional digits', $pos);
        }
        if ($d['digits'] === null) {
            if (self::exceedsIntDigits($d['gmp'], $d['scale'])) {
                fail('E_RANGE', 'number has more than ' . self::MAX_INT_DIGITS . ' integer digits', $pos);
            }
            return $d;
        }
        // Negative when the value is below 1: those render as a single "0".
        if (strlen($d['digits']) - $d['scale'] > self::MAX_INT_DIGITS) {
            fail('E_RANGE', 'number has more than ' . self::MAX_INT_DIGITS . ' integer digits', $pos);
        }
        return $d;
    }

    /**
     * A decimal handed in by host code (Value::num with an array): well
     * formed, canonical and within the digit caps (spec §8; review 2026-09-28
     * HOST-13, HOST-14). Leading zeros go and a negative zero loses its sign,
     * as they do through parse(); anything that is not a decimal is E_BAD_ARG.
     *
     * @param mixed $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function checked($d): array
    {
        if (is_array($d) && array_key_exists('digits', $d) && $d['digits'] === null
            && ($d['gmp'] ?? null) instanceof \GMP && is_bool($d['neg'] ?? null) && is_int($d['scale'] ?? null)) {
            $d = self::eager($d);
        }
        if (!is_array($d) || !is_bool($d['neg'] ?? null) || !is_string($d['digits'] ?? null)
            || !is_int($d['scale'] ?? null) || $d['scale'] < 0 || $d['digits'] === ''
            || strspn($d['digits'], '0123456789') !== strlen($d['digits'])) {
            fail('E_BAD_ARG', 'not a decimal: expected [neg => bool, digits => a string of ASCII digits, '
                . 'scale => a non-negative int]', null);
        }
        $digits = ltrim($d['digits'], '0');
        if ($digits === '') $digits = '0';
        // A native cache the caller supplied is trusted only when it still
        // describes these very fields (PHP-C40): edit `neg` or `digits` after
        // parse() and the cache is stale, and the fast paths would read it.
        if ($digits === $d['digits'] && ($digits !== '0' || !$d['neg']) && array_key_exists('native', $d)
            && ($d['nativeDigits'] ?? null) === $d['digits'] && ($d['nativeNeg'] ?? null) === $d['neg']
            && $d['native'] === self::parseMantissa($d['neg'], $d['digits'])) {
            return self::guard($d, null);
        }
        return self::guard(self::make($d['neg'], $digits, $d['scale']), null);
    }

    /** @return array{neg:bool,digits:string,scale:int} */
    public static function zero(): array
    {
        return self::make(false, '0', 0);
    }

    /**
     * Null when the text is not a number; callers raise E_NOT_NUM with the
     * position of the offending node. No trimming — " 2" is not a number.
     *
     * A well-formed numeral too big to hold is E_RANGE, not null: every
     * character of it is a digit, so "not a number" would be false. Callers that
     * must not raise — ISNUM's probe — catch it and answer no.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array{neg:bool,digits:string,scale:int}|null
     */
    public static function parse(string $text, ?array $pos = null): ?array
    {
        // The D modifier. Without it PCRE's `$` matches both at the end of the
        // subject and immediately before a single trailing newline, so "5\n"
        // parsed as a number here and as E_NOT_NUM everywhere else — and then
        // the newline was carried into the digit string and arithmetic on it
        // produced an out-of-range digit, which formatted as punctuation:
        // `"5\n" + 1` answered `5)`. It is the same PCRE behaviour the regex
        // built-ins already use `D` for, one layer further down.
        $len = strlen($text);
        if ($len <= 18 && self::$fastPaths) {
            // Short numerals (the overwhelming case): strspn validation and the
            // six-key array built directly. Eighteen digits can trip neither
            // digit cap and always fit the native mantissa, so guard() and
            // parseMantissa() are skipped; the arrays are identical, key order
            // included, to the general path below (PHP-P15; tested against it).
            $i = ($text[0] ?? '') === '-' ? 1 : 0;
            $n = strspn($text, '0123456789', $i);
            if ($n === 0) {
                return null;
            }
            $end = $i + $n;
            if ($end === $len) {
                $digits = ltrim(substr($text, $i), '0');
                $scale = 0;
            } elseif ($text[$end] === '.') {
                $m = strspn($text, '0123456789', $end + 1);
                if ($m === 0 || $end + 1 + $m !== $len) {
                    return null;
                }
                $digits = ltrim(substr($text, $i, $n) . substr($text, $end + 1), '0');
                $scale = $m;
            } else {
                return null;
            }
            if ($digits === '') {
                return ['neg' => false, 'digits' => '0', 'scale' => $scale,
                        'native' => 0, 'nativeDigits' => '0', 'nativeNeg' => false];
            }
            $neg = $i === 1;
            $v = (int) $digits;
            return ['neg' => $neg, 'digits' => $digits, 'scale' => $scale,
                    'native' => $neg ? -$v : $v, 'nativeDigits' => $digits, 'nativeNeg' => $neg];
        }
        if (preg_match('/^-?[0-9]+(\.[0-9]+)?$/D', $text) !== 1) {
            return null;
        }
        $neg = $text[0] === '-';
        $body = $neg ? substr($text, 1) : $text;
        $dot = strpos($body, '.');
        if ($dot === false) {
            return self::guard(self::make($neg, self::strip($body), 0), $pos);
        }
        $intPart = substr($body, 0, $dot);
        $fracPart = substr($body, $dot + 1);
        return self::guard(
            self::make($neg, self::strip($intPart . $fracPart), strlen($fracPart)),
            $pos,
        );
    }

    /** @param array{neg:bool,digits:string,scale:int} $d */
    public static function format(array $d): string
    {
        $sign = $d['neg'] ? '-' : '';
        $digits = self::digits($d);
        if ($d['scale'] === 0) {
            return $sign . $digits;
        }
        $padded = strlen($digits) <= $d['scale']
            ? str_repeat('0', $d['scale'] - strlen($digits) + 1) . $digits
            : $digits;
        $cut = strlen($padded) - $d['scale'];
        return $sign . substr($padded, 0, $cut) . '.' . substr($padded, $cut);
    }

    /** @return array{neg:bool,digits:string,scale:int} */
    public static function fromInt(int $n): array
    {
        return self::fromIntFast($n, 0);
    }

    /** @param array{neg:bool,digits:string,scale:int} $d */
    public static function isZero(array $d): bool
    {
        return $d['digits'] === '0';
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function negate(array $d): array
    {
        if ($d['digits'] === null) {
            $d['neg'] = $d['nativeNeg'] = !$d['neg'];
            return $d;
        }
        return self::make(!$d['neg'], $d['digits'], $d['scale']);
    }

    /**
     * The value with the fraction's trailing zeros removed (§7.6 CANON): 1.50 is
     * 1.5, 2.000 is 2, 100 stays 100, and zero is 0 with no scale and no sign.
     *
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function trimScale(array $d): array
    {
        $d = self::eager($d);
        if ($d['digits'] === '0') {
            return self::make(false, '0', 0);
        }
        if ($d['scale'] === 0) {
            return $d;
        }
        $len = strlen($d['digits']);
        $tail = substr($d['digits'], max(0, $len - $d['scale']));
        $zeros = strlen($tail) - strlen(rtrim($tail, '0'));
        if ($zeros === 0) {
            return $d;
        }
        return self::make($d['neg'], substr($d['digits'], 0, $len - $zeros), $d['scale'] - $zeros);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function abs(array $d): array
    {
        if ($d['digits'] === null) {
            $d['neg'] = $d['nativeNeg'] = false;
            return $d;
        }
        return self::make(false, $d['digits'], $d['scale']);
    }

    /** @param array{neg:bool,digits:string,scale:int} $d */
    public static function sign(array $d): int
    {
        return self::isZero($d) ? 0 : ($d['neg'] ? -1 : 1);
    }

    /** @param array{neg:bool,digits:string,scale:int} $d */
    public static function isInteger(array $d): bool
    {
        $d = self::eager($d);
        if ($d['scale'] === 0) {
            return true;
        }
        $len = strlen($d['digits']);
        if ($len <= $d['scale']) {
            return $d['digits'] === '0';
        }
        return strspn(substr($d['digits'], $len - $d['scale']), '0') === $d['scale'];
    }

    /** @param array{neg:bool,digits:string,scale:int} $d */
    public static function toInt(array $d): int
    {
        $d = self::eager($d);
        $t = self::trunc($d);
        // Saturating: a value past the machine integer is PHP_INT_MAX (or its
        // negation), never the 0 that (int) gives a string PHP reads as INF.
        $digits = $t['digits'];
        $over = strlen($digits) > 19 || (strlen($digits) === 19 && strcmp($digits, '9223372036854775807') > 0);
        $v = $over ? PHP_INT_MAX : (int) $digits;
        return $t['neg'] ? -$v : $v;
    }

    // --- arithmetic ---------------------------------------------------------

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @return array{0:string,1:string,2:int}
     */
    private static function aligned(array $a, array $b): array
    {
        $s = max($a['scale'], $b['scale']);
        return [
            self::scaleUp($a['digits'], $s - $a['scale']),
            self::scaleUp($b['digits'], $s - $b['scale']),
            $s,
        ];
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function add(array $a, array $b, ?array $pos = null): array
    {
        $fast = self::fastAligned($a, $b);
        if ($fast !== null) {
            [$left, $right, $scale] = $fast;
            $sum = $left + $right;
            if (is_int($sum)) {
                $value = self::fromIntFast($sum, $scale);
                if ($value !== null) return self::guard($value, $pos);
            }
        }
        if (self::lazy()) {
            return self::addGmp($a, $b, $pos);
        }
        $a = self::eager($a);
        $b = self::eager($b);
        [$A, $B, $s] = self::aligned($a, $b);
        if ($a['neg'] === $b['neg']) {
            // Only true addition can grow: a difference is never wider than its
            // operands, and the aligned scale is the larger of two legal ones.
            return self::guard(self::make($a['neg'], self::addAbs($A, $B), $s), $pos);
        }
        $c = self::cmpAbs($A, $B);
        if ($c === 0) {
            return self::make(false, '0', $s);
        }
        return $c > 0
            ? self::make($a['neg'], self::subAbs($A, $B), $s)
            : self::make($b['neg'], self::subAbs($B, $A), $s);
    }

    /** add() in GMP, with the same scale, sign and guard rules (item 1). */
    private static function addGmp(array $a, array $b, ?array $pos): array
    {
        $s = max($a['scale'], $b['scale']);
        $A = self::gmpOf($a);
        $B = self::gmpOf($b);
        if ($a['scale'] < $s) {
            $A = gmp_mul($A, self::pow10Gmp($s - $a['scale']));
        }
        if ($b['scale'] < $s) {
            $B = gmp_mul($B, self::pow10Gmp($s - $b['scale']));
        }
        if ($a['neg'] === $b['neg']) {
            return self::guard(self::fromGmp($a['neg'], gmp_add($A, $B), $s), $pos);
        }
        $c = gmp_cmp($A, $B);
        if ($c === 0) {
            return self::make(false, '0', $s);
        }
        return $c > 0
            ? self::fromGmp($a['neg'], gmp_sub($A, $B), $s)
            : self::fromGmp($b['neg'], gmp_sub($B, $A), $s);
    }

    /**
     * One step of a running total (SUM, PHP-P27). The total stays a native integer
     * mantissa and a scale while every step fits, and becomes a decimal descriptor
     * once one does not (overflow, a scale gap too wide for a native factor, or a
     * mantissa that is not native) -- from there every step is Dec::add. The answer
     * is the one a chain of Dec::add calls gives, without building a six-key
     * descriptor per element. Start with `['m' => 0, 's' => 0]`; finish with
     * sumResult().
     *
     * @param array{m?:int,s?:int,d?:array{neg:bool,digits:string,scale:int}} $acc
     * @param array{neg:bool,digits:string,scale:int} $d
     * @param array{line:int,col:int,offset:int}|null $pos
     */
    public static function sumAccumulate(array &$acc, array $d, ?array $pos = null): void
    {
        if (!isset($acc['d'])) {
            $right = self::intMantissa($d);
            if ($right !== null) {
                $left = $acc['m'];
                $scale = $acc['s'];
                if ($d['scale'] === $scale) {
                    $sum = $left + $right;
                    if (is_int($sum)) {
                        $acc['m'] = $sum;
                        return;
                    }
                } else {
                    $to = max($scale, $d['scale']);
                    $lf = self::intPow10($to - $scale);
                    $rf = self::intPow10($to - $d['scale']);
                    if ($lf !== null && $rf !== null) {
                        $left *= $lf;
                        $right *= $rf;
                        if (is_int($left) && is_int($right)) {
                            $sum = $left + $right;
                            if (is_int($sum)) {
                                $acc['m'] = $sum;
                                $acc['s'] = $to;
                                return;
                            }
                        }
                    }
                }
            }
            $acc = ['d' => self::fromIntFast($acc['m'], $acc['s'])];
        }
        $acc['d'] = self::add($acc['d'], $d, $pos);
    }

    /**
     * @param array{m?:int,s?:int,d?:array{neg:bool,digits:string,scale:int}} $acc
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function sumResult(array $acc): array
    {
        return $acc['d'] ?? self::fromIntFast($acc['m'], $acc['s']);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function sub(array $a, array $b, ?array $pos = null): array
    {
        return self::add($a, self::negate($b), $pos);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function mul(array $a, array $b, ?array $pos = null): array
    {
        $left = self::intMantissa($a);
        $right = self::intMantissa($b);
        if ($left !== null && $right !== null) {
            $product = $left * $right;
            if (is_int($product)) {
                $value = self::fromIntFast($product, $a['scale'] + $b['scale']);
                if ($value !== null) return self::guard($value, $pos);
            }
        }
        if (self::lazy()) {
            return self::guard(self::fromGmp(
                $a['neg'] !== $b['neg'],
                gmp_mul(self::gmpOf($a), self::gmpOf($b)),
                $a['scale'] + $b['scale'],
            ), $pos);
        }
        $a = self::eager($a);
        $b = self::eager($b);
        return self::guard(self::make(
            $a['neg'] !== $b['neg'],
            self::mulAbs($a['digits'], $b['digits']),
            $a['scale'] + $b['scale'],
        ), $pos);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     */
    public static function cmp(array $a, array $b): int
    {
        if ($a['scale'] === 0 && $b['scale'] === 0
            && isset($a['native'], $b['native'])
            && ($a['nativeDigits'] ?? null) === $a['digits']
            && ($b['nativeDigits'] ?? null) === $b['digits']
            && ($a['nativeNeg'] ?? null) === $a['neg']
            && ($b['nativeNeg'] ?? null) === $b['neg']) {
            return $a['native'] <=> $b['native'];
        }
        if (self::isZero($a) && self::isZero($b)) {
            return 0;
        }
        if ($a['neg'] !== $b['neg']) {
            return $a['neg'] ? -1 : 1;
        }
        $fast = self::fastAligned($a, $b);
        if ($fast !== null) {
            $c = $fast[0] <=> $fast[1];
            // fastAligned carries signed mantissas, so PHP's native comparison
            // already has the correct order for the negative branch too.
            return $c;
        }
        if ($a['digits'] === null || $b['digits'] === null) {
            if (self::hasGmp()) {
                $s = max($a['scale'], $b['scale']);
                $A = self::gmpOf($a);
                $B = self::gmpOf($b);
                if ($a['scale'] < $s) {
                    $A = gmp_mul($A, self::pow10Gmp($s - $a['scale']));
                }
                if ($b['scale'] < $s) {
                    $B = gmp_mul($B, self::pow10Gmp($s - $b['scale']));
                }
                $c = gmp_cmp($A, $B) <=> 0;
                return $a['neg'] ? -$c : $c;
            }
            $a = self::eager($a);
            $b = self::eager($b);
        }
        [$A, $B] = self::aligned($a, $b);
        $c = self::cmpAbs($A, $B);
        return $a['neg'] ? -$c : $c;
    }

    /**
     * Division on native mantissas (PHP-P13): the same quotient the digit-string
     * path below computes — scaled to DIV_SCALE digits, exact results cut to their
     * minimal scale, inexact ones rounded half away from zero — done in machine
     * integers when every intermediate provably fits. The guards keep the scaled
     * dividend within PHP_INT_MAX and the divisor within half of it, so `2 * r >= D`
     * cannot overflow; PHP_INT_MIN mantissas and anything wider take the general
     * path. Null means "not applicable". The result is identical, cache keys
     * included, to the general path's (tested against it).
     *
     * @return array{neg:bool,digits:string,scale:int}|null
     */
    private static function fastDiv(array $a, array $b): ?array
    {
        if (!self::$fastPaths) {
            return null;
        }
        $x = self::intMantissa($a);
        $y = self::intMantissa($b);
        if ($x === null || $y === null || $x === PHP_INT_MIN || $y === PHP_INT_MIN) {
            return null;
        }
        $p1 = self::intPow10($b['scale'] + self::DIV_SCALE);
        $p2 = self::intPow10($a['scale']);
        if ($p1 === null || $p2 === null) {
            return null;
        }
        $ax = $x < 0 ? -$x : $x;
        $ay = $y < 0 ? -$y : $y;
        if ($ax > intdiv(PHP_INT_MAX, $p1) || $ay > intdiv(PHP_INT_MAX >> 1, $p2)) {
            return null;
        }
        $N = $ax * $p1;
        $D = $ay * $p2;
        $q = intdiv($N, $D);
        $r = $N - $q * $D;
        $neg = ($x < 0) !== ($y < 0);
        if ($r === 0) {
            if ($q === 0) {
                return self::make(false, '0', 0, 0);
            }
            $scale = self::DIV_SCALE;
            while ($scale > 0 && $q % 10 === 0) {
                $q = intdiv($q, 10);
                $scale--;
            }
            return self::make($neg, (string) $q, $scale, $neg ? -$q : $q);
        }
        if ($r >= $D - $r) {
            $q++;
        }
        return self::make($neg, (string) $q, self::DIV_SCALE, $neg ? -$q : $q);
    }

    /**
     * Exact when the quotient terminates within DIV_SCALE fractional digits (and
     * then reported at its minimal scale); otherwise rounded half away from zero
     * to exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
     *
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function div(array $a, array $b, ?array $pos = null): array
    {
        $a = self::eager($a);
        $b = self::eager($b);
        if (self::isZero($b)) {
            fail('E_DIV_ZERO', 'division by zero', $pos);
        }
        $fast = self::fastDiv($a, $b);
        if ($fast !== null) {
            return $fast;
        }
        $N = self::scaleUp($a['digits'], $b['scale']);
        $D = self::scaleUp($b['digits'], $a['scale']);
        [$q, $r] = self::divModAbs(self::scaleUp($N, self::DIV_SCALE), $D);
        $neg = $a['neg'] !== $b['neg'];

        if ($r === '0') {
            // Exact: drop trailing zeros to reach the minimal scale.
            $digits = $q;
            $scale = self::DIV_SCALE;
            while ($scale > 0 && strlen($digits) > 1 && $digits[strlen($digits) - 1] === '0') {
                $digits = substr($digits, 0, -1);
                $scale--;
            }
            if ($digits === '0') {
                $scale = 0;
            }
            return self::guard(self::make($neg, $digits, $scale), $pos);
        }
        $up = self::cmpAbs(self::addAbs($r, $r), $D) >= 0 ? self::addAbs($q, '1') : $q;
        return self::guard(self::make($neg, $up, self::DIV_SCALE), $pos);
    }

    /**
     * Remainder of truncated division: takes the sign of the dividend.
     *
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function mod(array $a, array $b, ?array $pos = null): array
    {
        $a = self::eager($a);
        $b = self::eager($b);
        if (self::isZero($b)) {
            fail('E_DIV_ZERO', 'modulo by zero', $pos);
        }
        [$A, $B, $s] = self::aligned($a, $b);
        [, $r] = self::divModAbs($A, $B);
        return self::make($a['neg'], $r, $s);
    }

    // --- rounding -----------------------------------------------------------

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function round(array $d, int $n, ?array $pos = null): array
    {
        $d = self::eager($d);
        if ($n >= $d['scale']) {
            return self::guard(self::make($d['neg'], self::scaleUp($d['digits'], $n - $d['scale']), $n), $pos);
        }
        $p = self::pow10($d['scale'] - $n);
        [$q, $r] = self::divModAbs($d['digits'], $p);
        // Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
        $up = self::cmpAbs(self::addAbs($r, $r), $p) >= 0 ? self::addAbs($q, '1') : $q;
        return self::guard(self::make($d['neg'], $up, $n), $pos);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function trunc(array $d): array
    {
        if ($d['scale'] === 0) {
            return $d;
        }
        $d = self::eager($d);
        [$q] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return self::make($d['neg'], $q, 0);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function floor(array $d, ?array $pos = null): array
    {
        if ($d['scale'] === 0) {
            return $d;
        }
        $d = self::eager($d);
        [$q, $r] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        // Rounding away from zero carries: -99.5 floors to -100, a digit wider,
        // and past the digit cap that is E_RANGE at the call, not at 0:0 when
        // the result is next checked.
        return self::guard(self::make($d['neg'], $d['neg'] && $r !== '0' ? self::addAbs($q, '1') : $q, 0), $pos);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function ceil(array $d, ?array $pos = null): array
    {
        if ($d['scale'] === 0) {
            return $d;
        }
        $d = self::eager($d);
        [$q, $r] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return self::guard(self::make($d['neg'], !$d['neg'] && $r !== '0' ? self::addAbs($q, '1') : $q, 0), $pos);
    }

    /**
     * n must be a non-negative integer; the result scale is scale(x) * n, which
     * falls out of repeated multiplication.
     *
     * @param array{neg:bool,digits:string,scale:int} $a
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function power(array $a, int $n, ?array $pos = null): array
    {
        $result = self::make(false, '1', 0);
        $base = $a;
        $e = $n;
        while ($e > 0) {
            // intdiv/% rather than the bit operators: PHP's are 64-bit here,
            // but the same code in JS is 32-bit and silently truncated a large
            // exponent. Keeping the four cores literally the same code is the
            // point — see js/src/decimal.mjs.
            if ($e % 2 === 1) {
                $result = self::mul($result, $base, $pos);
            }
            $e = intdiv($e, 2);
            if ($e > 0) {
                $base = self::mul($base, $base, $pos);
            }
        }
        return $result;
    }
}
