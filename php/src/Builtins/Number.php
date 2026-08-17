<?php
declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Dec;
use Sel\Registry;
use Sel\Value;

use function Sel\fail;

final class Number
{
    // spec/SPEC.md §6.4 — a size argument beyond these exhausts memory instead
    // of failing as a rule error.
    private const MAX_SCALE = 1000000;
    private const MAX_POWER = 100000;

    private static function sized(Args $a, int $i, int $limit, string $what): int
    {
        $n = $a->nonNegInt($i);
        if ($n > $limit) {
            fail('E_RANGE', "{$what} {$n} exceeds the maximum of {$limit}", $a->posOf($i));
        }
        return $n;
    }

    public static function register(): void
    {
        Registry::define(['name' => 'ABS', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::num(Dec::abs($a->dec(0)))]);
        Registry::define(['name' => 'SIGN', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int(Dec::sign($a->dec(0)))]);
        Registry::define(['name' => 'CEIL', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::num(Dec::ceil($a->dec(0)))]);
        Registry::define(['name' => 'FLOOR', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::num(Dec::floor($a->dec(0)))]);
        Registry::define(['name' => 'TRUNC', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::num(Dec::trunc($a->dec(0)))]);

        Registry::define(['name' => 'ROUND', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::num(Dec::round($a->dec(0), self::sized($a, 1, self::MAX_SCALE, 'ROUND scale')))]);

        Registry::define(['name' => 'POWER', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::num(Dec::power($a->dec(0), self::sized($a, 1, self::MAX_POWER, 'POWER exponent')))]);

        Registry::define(['name' => 'MIN', 'min' => 1, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $best = $a->dec(0);
                for ($i = 1, $n = $a->count(); $i < $n; $i++) {
                    $d = $a->dec($i);
                    if (Dec::cmp($d, $best) < 0) {
                        $best = $d;
                    }
                }
                return Value::num($best);
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
                return Value::num($best);
            }]);

        // The non-throwing probe. Every other numeric path raises E_NOT_NUM.
        Registry::define(['name' => 'ISNUM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->looksNumeric())]);
    }
}
