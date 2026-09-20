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

declare(strict_types=1);

namespace Sel;

final class Dec
{
    public const DIV_SCALE = 10;
    /** @var list<int>|null */
    private static ?array $nativePow10 = null;

    private static ?bool $hasGmp = null;
    /** @var list<int> */
    private static array $pow10Int = [
        1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000
    ];

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
    public const MAX_INT_DIGITS = 1000000;
    public const MAX_FRAC_DIGITS = 1000000;

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
        return $a === $b ? 0 : ($a < $b ? -1 : 1);
    }

    private static function addAbs(string $a, string $b): string
    {
        if (self::hasGmp()) {
            return gmp_strval(gmp_add($a, $b));
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
            return gmp_strval(gmp_sub($a, $b));
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
            return gmp_strval(gmp_mul($a, $b));
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

        $limbsA = [];
        for ($i = $la; $i > 0; $i -= 7) {
            $start = max(0, $i - 7);
            $limbsA[] = (int) substr($a, $start, $i - $start);
        }
        $limbsB = [];
        for ($j = $lb; $j > 0; $j -= 7) {
            $start = max(0, $j - 7);
            $limbsB[] = (int) substr($b, $start, $j - $start);
        }

        $na = count($limbsA);
        $nb = count($limbsB);
        $acc = array_fill(0, $na + $nb, 0);

        for ($i = 0; $i < $na; $i++) {
            $ai = $limbsA[$i];
            if ($ai === 0) continue;
            for ($j = 0; $j < $nb; $j++) {
                $acc[$i + $j] += $ai * $limbsB[$j];
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

        while (count($acc) > 1 && end($acc) === 0) {
            array_pop($acc);
        }

        $out = (string) array_pop($acc);
        while (!empty($acc)) {
            $out .= sprintf('%07d', array_pop($acc));
        }
        return $out;
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
            [$q, $r] = gmp_div_qr($a, $b);
            return [gmp_strval($q), gmp_strval($r)];
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

        $q = '';
        $r = '0';
        for ($i = 0; $i < $la; $i++) {
            $r = self::strip($r . $a[$i]);
            $k = 0;
            while (self::cmpAbs($r, $b) >= 0) {
                $r = self::subAbs($r, $b);
                $k++;
            }
            $q .= chr(48 + $k);
        }
        return [self::strip($q), $r];
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
        // Negative when the value is below 1: those render as a single "0".
        if (strlen($d['digits']) - $d['scale'] > self::MAX_INT_DIGITS) {
            fail('E_RANGE', 'number has more than ' . self::MAX_INT_DIGITS . ' integer digits', $pos);
        }
        return $d;
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
        if ($d['scale'] === 0) {
            return $sign . $d['digits'];
        }
        $padded = strlen($d['digits']) <= $d['scale']
            ? str_repeat('0', $d['scale'] - strlen($d['digits']) + 1) . $d['digits']
            : $d['digits'];
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
        return self::make(!$d['neg'], $d['digits'], $d['scale']);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function abs(array $d): array
    {
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
        $t = self::trunc($d);
        $v = (int) $t['digits'];
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
        [$A, $B] = self::aligned($a, $b);
        $c = self::cmpAbs($A, $B);
        return $a['neg'] ? -$c : $c;
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
        if (self::isZero($b)) {
            fail('E_DIV_ZERO', 'division by zero', $pos);
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
        [$q] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return self::make($d['neg'], $q, 0);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function floor(array $d): array
    {
        if ($d['scale'] === 0) {
            return $d;
        }
        [$q, $r] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return self::make($d['neg'], $d['neg'] && $r !== '0' ? self::addAbs($q, '1') : $q, 0);
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $d
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function ceil(array $d): array
    {
        if ($d['scale'] === 0) {
            return $d;
        }
        [$q, $r] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return self::make($d['neg'], !$d['neg'] && $r !== '0' ? self::addAbs($q, '1') : $q, 0);
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
