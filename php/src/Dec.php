<?php
// Exact decimal arithmetic on digit strings. See spec/SPEC.md §4.
//
// Ported line for line from js/src/decimal.mjs. PHP has no bigint and BCMath is
// an optional extension, so this is written from scratch — which is also what
// guarantees the two hosts round identically rather than merely similarly.
//
// A decimal is ['neg' => bool, 'digits' => string, 'scale' => int], meaning
// (neg ? -1 : 1) * digits / 10^scale.

declare(strict_types=1);

namespace Sel;

final class Dec
{
    public const DIV_SCALE = 10;

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
        $out = '';
        $i = strlen($a) - 1;
        $j = strlen($b) - 1;
        $carry = 0;
        while ($i >= 0 || $j >= 0 || $carry) {
            $s = ($i >= 0 ? ord($a[$i--]) - 48 : 0) + ($j >= 0 ? ord($b[$j--]) - 48 : 0) + $carry;
            $out = chr(48 + $s % 10) . $out;
            $carry = $s >= 10 ? 1 : 0;
        }
        return $out;
    }

    /** Requires a >= b. */
    private static function subAbs(string $a, string $b): string
    {
        $out = '';
        $i = strlen($a) - 1;
        $j = strlen($b) - 1;
        $borrow = 0;
        while ($i >= 0) {
            $s = (ord($a[$i--]) - 48) - ($j >= 0 ? ord($b[$j--]) - 48 : 0) - $borrow;
            if ($s < 0) {
                $s += 10;
                $borrow = 1;
            } else {
                $borrow = 0;
            }
            $out = chr(48 + $s) . $out;
        }
        return self::strip($out);
    }

    private static function mulAbs(string $a, string $b): string
    {
        if ($a === '0' || $b === '0') {
            return '0';
        }
        $n = strlen($a);
        $m = strlen($b);
        $acc = array_fill(0, $n + $m, 0);
        for ($i = $n - 1; $i >= 0; $i--) {
            $av = ord($a[$i]) - 48;
            if ($av === 0) {
                continue;
            }
            $carry = 0;
            for ($j = $m - 1; $j >= 0; $j--) {
                $t = $acc[$i + $j + 1] + $av * (ord($b[$j]) - 48) + $carry;
                $acc[$i + $j + 1] = $t % 10;
                $carry = intdiv($t, 10);
            }
            $acc[$i] += $carry;
        }
        return self::strip(implode('', $acc));
    }

    /**
     * Schoolbook long division. Trial digits by repeated subtraction — at most
     * nine per output digit, which keeps it obviously correct and trivial to port.
     *
     * @return array{0:string,1:string}|null
     */
    private static function divModAbs(string $a, string $b): ?array
    {
        if ($b === '0') {
            return null;
        }
        if (self::cmpAbs($a, $b) < 0) {
            return ['0', $a];
        }
        $q = '';
        $r = '0';
        $len = strlen($a);
        for ($i = 0; $i < $len; $i++) {
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

    // --- construction -------------------------------------------------------

    /** @return array{neg:bool,digits:string,scale:int} */
    private static function make(bool $neg, string $digits, int $scale): array
    {
        return ['neg' => $digits === '0' ? false : $neg, 'digits' => $digits, 'scale' => $scale];
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
     * @return array{neg:bool,digits:string,scale:int}|null
     */
    public static function parse(string $text): ?array
    {
        if (preg_match('/^-?[0-9]+(\.[0-9]+)?$/', $text) !== 1) {
            return null;
        }
        $neg = $text[0] === '-';
        $body = $neg ? substr($text, 1) : $text;
        $dot = strpos($body, '.');
        if ($dot === false) {
            return self::make($neg, self::strip($body), 0);
        }
        $intPart = substr($body, 0, $dot);
        $fracPart = substr($body, $dot + 1);
        return self::make($neg, self::strip($intPart . $fracPart), strlen($fracPart));
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
        return self::make($n < 0, (string) abs($n), 0);
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
        [, $r] = self::divModAbs($d['digits'], self::pow10($d['scale']));
        return $r === '0';
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
    public static function add(array $a, array $b): array
    {
        [$A, $B, $s] = self::aligned($a, $b);
        if ($a['neg'] === $b['neg']) {
            return self::make($a['neg'], self::addAbs($A, $B), $s);
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
    public static function sub(array $a, array $b): array
    {
        return self::add($a, self::negate($b));
    }

    /**
     * @param array{neg:bool,digits:string,scale:int} $a
     * @param array{neg:bool,digits:string,scale:int} $b
     * @return array{neg:bool,digits:string,scale:int}
     */
    public static function mul(array $a, array $b): array
    {
        return self::make(
            $a['neg'] !== $b['neg'],
            self::mulAbs($a['digits'], $b['digits']),
            $a['scale'] + $b['scale'],
        );
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
            return self::make($neg, $digits, $scale);
        }
        $up = self::cmpAbs(self::addAbs($r, $r), $D) >= 0 ? self::addAbs($q, '1') : $q;
        return self::make($neg, $up, self::DIV_SCALE);
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
    public static function round(array $d, int $n): array
    {
        if ($n >= $d['scale']) {
            return self::make($d['neg'], self::scaleUp($d['digits'], $n - $d['scale']), $n);
        }
        $p = self::pow10($d['scale'] - $n);
        [$q, $r] = self::divModAbs($d['digits'], $p);
        $up = self::cmpAbs(self::addAbs($r, $r), $p) >= 0 ? self::addAbs($q, '1') : $q;
        return self::make($d['neg'], $up, $n);
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
    public static function power(array $a, int $n): array
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
                $result = self::mul($result, $base);
            }
            $e = intdiv($e, 2);
            if ($e > 0) {
                $base = self::mul($base, $base);
            }
        }
        return $result;
    }
}
