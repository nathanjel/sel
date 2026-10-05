<?php
declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Dec;
use Sel\Limits;
use Sel\Registry;
use Sel\Value;

use function Sel\fail;

/**
 * @phpstan-import-type Decimal from Dec
 */
final class Number
{
    /**
     * ROUND's scale and POWER's exponent, checked once for the builtin and the
     * math plan (Evaluator::runMathPlan) alike: a whole number (E_NOT_INT), not
     * negative, and within its spec/SPEC.md §6.4 cap (Limits::MAX_ROUND_SCALE,
     * Limits::MAX_POWER_EXPONENT) — a size argument beyond these exhausts memory
     * instead of failing as a rule error. The messages are Args::nonNegInt's.
     *
     * @param Decimal $d
     * @param array<string,mixed>|null $pos
     */
    public static function sizedArg(array $d, string $fn, int $argNo, int $limit, string $what, ?array $pos): int
    {
        if (!Dec::isInteger($d)) {
            fail('E_NOT_INT', "{$fn} argument {$argNo} must be a whole number", $pos);
        }
        $n = Dec::toInt($d);
        if ($n < 0) {
            fail('E_RANGE', "{$fn} argument {$argNo} must not be negative", $pos);
        }
        if ($n > $limit) {
            fail('E_RANGE', "{$what} {$n} exceeds the maximum of {$limit}", $pos);
        }
        return $n;
    }

    private static function sized(Args $a, int $i, int $limit, string $what): int
    {
        return self::sizedArg($a->dec($i), $a->name, $i + 1, $limit, $what, $a->posOf($i));
    }

    public static function register(): void
    {
        Registry::define(['name' => 'ABS', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::abs($a->dec(0)))]);
        Registry::define(['name' => 'SIGN', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int(Dec::sign($a->dec(0)))]);
        Registry::define(['name' => 'CEIL', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::ceil($a->dec(0), $a->pos))]);
        Registry::define(['name' => 'FLOOR', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::floor($a->dec(0), $a->pos))]);
        Registry::define(['name' => 'TRUNC', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::trunc($a->dec(0)))]);
        Registry::define(['name' => 'CANON', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::trimScale($a->dec(0)))]);

        Registry::define(['name' => 'ROUND', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::round($a->dec(0), self::sized($a, 1, Limits::MAX_ROUND_SCALE, 'ROUND scale'), $a->pos))]);

        Registry::define(['name' => 'POWER', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::numTrusted(Dec::power($a->dec(0), self::sized($a, 1, Limits::MAX_POWER_EXPONENT, 'POWER exponent'), $a->pos))]);

        Registry::define(['name' => 'MIN', 'min' => 1, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $best = $a->dec(0);
                for ($i = 1, $n = $a->count(); $i < $n; $i++) {
                    $d = $a->dec($i);
                    if (Dec::cmp($d, $best) < 0) {
                        $best = $d;
                    }
                }
                return Value::numTrusted($best);
            }]);

        Registry::define(['name' => 'MAX', 'min' => 1, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $best = $a->dec(0);
                for ($i = 1, $n = $a->count(); $i < $n; $i++) {
                    $d = $a->dec($i);
                    if (Dec::cmp($d, $best) > 0) {
                        $best = $d;
                    }
                }
                return Value::numTrusted($best);
            }]);

        // The non-throwing probe. Every other numeric path raises E_NOT_NUM.
        Registry::define(['name' => 'ISNUM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->looksNumeric())]);
    }
}
