<?php
// Text built-ins. Everything counts code points — never bytes — so positions and
// lengths agree with the JS host on astral characters. Positions are 1-based and
// 0 means "not found" (§7.5).

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Registry;
use Sel\Utf8;
use Sel\Value;

use function Sel\fail;

final class Text
{
    /**
     * @param list<string> $hay
     * @param list<string> $needle
     */
    private static function indexOfCp(array $hay, array $needle, int $from): int
    {
        $n = count($needle);
        if ($n === 0) {
            return -1;
        }
        $limit = count($hay) - $n;
        for ($i = $from; $i <= $limit; $i++) {
            $ok = true;
            for ($j = 0; $j < $n; $j++) {
                if ($hay[$i + $j] !== $needle[$j]) {
                    $ok = false;
                    break;
                }
            }
            if ($ok) {
                return $i;
            }
        }
        return -1;
    }

    /** @param list<string> $chars */
    private static function join(array $chars): string
    {
        return implode('', $chars);
    }

    public static function register(): void
    {
        Registry::define(['name' => 'LEN', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int(count(Utf8::chars($a->text(0))))]);

        Registry::define(['name' => 'LEFT', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::text(
                self::join(array_slice(Utf8::chars($a->text(0)), 0, $a->nonNegInt(1))),
            )]);

        Registry::define(['name' => 'RIGHT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $c = Utf8::chars($a->text(0));
                $n = $a->nonNegInt(1);
                return Value::text(self::join(array_slice($c, max(0, count($c) - $n))));
            }]);

        Registry::define(['name' => 'SUBSTR', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $c = Utf8::chars($a->text(0));
                $start = $a->int(1);
                if ($start < 1) {
                    fail('E_RANGE', 'SUBSTR start is 1-based and must be at least 1', $a->posOf(1));
                }
                $from = $start - 1;
                if ($a->count() === 2) {
                    return Value::text(self::join(array_slice($c, $from)));
                }
                return Value::text(self::join(array_slice($c, $from, $a->nonNegInt(2))));
            }]);

        Registry::define(['name' => 'FIND', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $needle = Utf8::chars($a->text(0));
                $hay = Utf8::chars($a->text(1));
                $from = 0;
                if ($a->count() === 3) {
                    $f = $a->int(2);
                    if ($f < 1) {
                        fail('E_RANGE', 'FIND start is 1-based and must be at least 1', $a->posOf(2));
                    }
                    $from = $f - 1;
                }
                if (!$needle) {
                    fail('E_BAD_ARG', 'FIND needle must not be empty', $a->posOf(0));
                }
                return Value::int(self::indexOfCp($hay, $needle, $from) + 1);
            }]);

        Registry::define(['name' => 'REPLACE', 'min' => 3, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $needle = Utf8::chars($a->text(0));
                $repl = $a->text(1);
                $hay = Utf8::chars($a->text(2));
                if (!$needle) {
                    fail('E_BAD_ARG', 'REPLACE needle must not be empty', $a->posOf(0));
                }
                $out = '';
                $i = 0;
                for (;;) {
                    $at = self::indexOfCp($hay, $needle, $i);
                    if ($at < 0) {
                        break;
                    }
                    $out .= self::join(array_slice($hay, $i, $at - $i)) . $repl;
                    $i = $at + count($needle);
                }
                return Value::text($out . self::join(array_slice($hay, $i)));
            }]);

        Registry::define(['name' => 'SPLIT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $hay = Utf8::chars($a->text(0));
                $sep = Utf8::chars($a->text(1));
                if (!$sep) {
                    fail('E_BAD_ARG', 'SPLIT separator must not be empty', $a->posOf(1));
                }
                $parts = [];
                $i = 0;
                for (;;) {
                    $at = self::indexOfCp($hay, $sep, $i);
                    if ($at < 0) {
                        break;
                    }
                    $parts[] = Value::text(self::join(array_slice($hay, $i, $at - $i)));
                    $i = $at + count($sep);
                }
                $parts[] = Value::text(self::join(array_slice($hay, $i)));
                return Value::list($parts);
            }]);

        Registry::define(['name' => 'TRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(self::trim($a->text(0), true, true))]);
        Registry::define(['name' => 'LTRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(self::trim($a->text(0), true, false))]);
        Registry::define(['name' => 'RTRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(self::trim($a->text(0), false, true))]);

        Registry::define(['name' => 'UPPER', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(self::asciiCase($a->text(0), true))]);
        Registry::define(['name' => 'LOWER', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(self::asciiCase($a->text(0), false))]);

        Registry::define(['name' => 'BACKWARDS', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(
                self::join(array_reverse(Utf8::chars($a->text(0)))),
            )]);

        Registry::define(['name' => 'REPEAT', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::text(
                str_repeat($a->text(0), $a->nonNegInt(1)),
            )]);

        Registry::define(['name' => 'PADL', 'min' => 3, 'max' => 3,
            'fn' => static fn (Args $a): Value => self::pad($a, true)]);
        Registry::define(['name' => 'PADR', 'min' => 3, 'max' => 3,
            'fn' => static fn (Args $a): Value => self::pad($a, false)]);

        Registry::define(['name' => 'CHAR', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $n = $a->int(0);
                if ($n < 0 || $n > 0x10ffff || ($n >= 0xd800 && $n <= 0xdfff)) {
                    fail('E_RANGE', "{$n} is not an encodable code point", $a->posOf(0));
                }
                return Value::text(Utf8::chr($n));
            }]);

        Registry::define(['name' => 'CODE', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $c = Utf8::chars($a->text(0));
                if (!$c) {
                    fail('E_RANGE', 'CODE of empty text', $a->posOf(0));
                }
                return Value::int(Utf8::ord($c[0]));
            }]);
    }

    private static function trim(string $s, bool $left, bool $right): string
    {
        $space = [' ', "\t", "\r", "\n"];
        $c = Utf8::chars($s);
        $a = 0;
        $b = count($c);
        if ($left) {
            while ($a < $b && in_array($c[$a], $space, true)) {
                $a++;
            }
        }
        if ($right) {
            while ($b > $a && in_array($c[$b - 1], $space, true)) {
                $b--;
            }
        }
        return self::join(array_slice($c, $a, $b - $a));
    }

    /**
     * ASCII only, deliberately. PHP's strtoupper is byte- and locale-based while
     * JS's toUpperCase applies full Unicode mapping; they cannot be reconciled
     * without shipping a case table, and guessing would break the invariant
     * silently.
     */
    private static function asciiCase(string $s, bool $up): string
    {
        $out = '';
        foreach (Utf8::chars($s) as $ch) {
            if (strlen($ch) === 1) {
                $c = ord($ch);
                if ($up && $c >= 0x61 && $c <= 0x7a) {
                    $out .= chr($c - 32);
                    continue;
                }
                if (!$up && $c >= 0x41 && $c <= 0x5a) {
                    $out .= chr($c + 32);
                    continue;
                }
            }
            $out .= $ch;
        }
        return $out;
    }

    private static function pad(Args $a, bool $left): Value
    {
        $c = Utf8::chars($a->text(0));
        $width = $a->nonNegInt(1);
        $fill = Utf8::chars($a->text(2));
        if (!$fill) {
            fail('E_BAD_ARG', 'pad fill must not be empty', $a->posOf(2));
        }
        if (count($c) >= $width) {
            return Value::text(self::join($c));
        }
        $need = $width - count($c);
        $padding = [];
        while (count($padding) < $need) {
            $padding[] = $fill[count($padding) % count($fill)];
        }
        return Value::text($left
            ? self::join($padding) . self::join($c)
            : self::join($c) . self::join($padding));
    }
}
