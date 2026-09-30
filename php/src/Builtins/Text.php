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
    // Every operation here is on BYTES, counting code points only where the answer
    // is a count or an offset (Utf8::advance/length/slice/cpIndex). The text is
    // valid UTF-8, so a byte-level search for a valid needle can only land on a
    // character boundary, and no character is ever a substring of another's
    // encoding. What this replaces split the text into an array of one-character
    // strings first, which for a text at the length cap (spec §6.4:
    // 16 777 216 code points) is gigabytes.

    public static function register(): void
    {
        Registry::define(['name' => 'LEN', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int(Utf8::length($a->text(0)))]);

        Registry::define(['name' => 'LEFT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                $n = $a->nonNegInt(1);
                return Value::textTrusted(substr($s, 0, Utf8::advance($s, $n)));
            }]);

        Registry::define(['name' => 'RIGHT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                $n = $a->nonNegInt(1);
                return Value::textTrusted(substr($s, Utf8::retreat($s, $n)));
            }]);

        Registry::define(['name' => 'SUBSTR', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                $start = $a->int(1);
                if ($start < 1) {
                    fail('E_RANGE', 'SUBSTR start is 1-based and must be at least 1', $a->posOf(1));
                }
                $from = $start - 1;
                if ($a->count() === 2) {
                    return Value::textTrusted(Utf8::slice($s, $from));
                }
                return Value::textTrusted(Utf8::slice($s, $from, $a->nonNegInt(2)));
            }]);

        Registry::define(['name' => 'FIND', 'min' => 2, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $needle = $a->text(0);
                $hay = $a->text(1);
                $from = 0;
                if ($a->count() === 3) {
                    $f = $a->int(2);
                    if ($f < 1) {
                        fail('E_RANGE', 'FIND start is 1-based and must be at least 1', $a->posOf(2));
                    }
                    $from = $f - 1;
                }
                if ($needle === '') {
                    fail('E_BAD_ARG', 'FIND needle must not be empty', $a->posOf(0));
                }
                $offset = Utf8::advance($hay, $from);
                $at = strpos($hay, $needle, $offset);
                return Value::int($at === false ? 0 : Utf8::cpIndex($hay, $at) + 1);
            }]);

        Registry::define(['name' => 'REPLACE', 'min' => 3, 'max' => 3,
            'fn' => static function (Args $a): Value {
                $needle = $a->text(0);
                $repl = $a->text(1);
                $hay = $a->text(2);
                if ($needle === '') {
                    fail('E_BAD_ARG', 'REPLACE needle must not be empty', $a->posOf(0));
                }
                // The result's size is known before it is built: refuse it, at the
                // call, before a host allocates it (spec §6.4).
                $count = substr_count($hay, $needle);
                if ($count > 0) {
                    $bytes = strlen($hay) + $count * (strlen($repl) - strlen($needle));
                    if ($bytes > \Sel\Limits::MAX_TEXT_LEN) {
                        $cps = Utf8::length($hay) + $count * (Utf8::length($repl) - Utf8::length($needle));
                        if ($cps > \Sel\Limits::MAX_TEXT_LEN) {
                            fail('E_RANGE', 'REPLACE result would be longer than ' . \Sel\Limits::MAX_TEXT_LEN, $a->pos);
                        }
                    }
                }
                return Value::textTrusted(str_replace($needle, $repl, $hay));
            }]);

        Registry::define(['name' => 'SPLIT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $hay = $a->text(0);
                $sep = $a->text(1);
                if ($sep === '') {
                    fail('E_BAD_ARG', 'SPLIT separator must not be empty', $a->posOf(1));
                }
                Utf8::checkCount(substr_count($hay, $sep) + 1, $a->pos, 'SPLIT result');
                $parts = [];
                foreach (explode($sep, $hay) as $part) {
                    $parts[] = Value::textTrusted($part);
                }
                return Value::list($parts);
            }]);

        Registry::define(['name' => 'TRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::textTrusted(trim($a->text(0), " \t\r\n"))]);
        Registry::define(['name' => 'LTRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::textTrusted(ltrim($a->text(0), " \t\r\n"))]);
        Registry::define(['name' => 'RTRIM', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::textTrusted(rtrim($a->text(0), " \t\r\n"))]);

        // ASCII only, deliberately. PHP's strtoupper is byte- and locale-based
        // while JS's toUpperCase applies full Unicode mapping; they cannot be
        // reconciled without shipping a case table, and guessing would break the
        // invariant silently. Utf8::upper/lower are the locale-free ASCII rule.
        Registry::define(['name' => 'UPPER', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::textTrusted(Utf8::upper($a->text(0)))]);
        Registry::define(['name' => 'LOWER', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::textTrusted(Utf8::lower($a->text(0)))]);

        Registry::define(['name' => 'BACKWARDS', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                if (Utf8::isAscii($s)) return Value::textTrusted(strrev($s));
                return Value::textTrusted(self::reverseCodePoints($s));
            }]);

        Registry::define(['name' => 'REPEAT', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                $n = $a->nonNegInt(1);
                // An empty text repeated any number of times is empty: only a
                // RESULT over the cap is refused, never a count as such.
                if ($s === '' || $n === 0) return Value::text('');
                if ($n > intdiv(\Sel\Limits::MAX_TEXT_LEN, Utf8::length($s))) {
                    fail('E_RANGE', 'REPEAT result would be longer than ' . \Sel\Limits::MAX_TEXT_LEN, $a->pos);
                }
                return Value::textTrusted(str_repeat($s, $n));
            }]);

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
                $s = $a->text(0);
                if ($s === '') {
                    fail('E_RANGE', 'CODE of empty text', $a->posOf(0));
                }
                return Value::int(Utf8::ord(substr($s, 0, 4)));
            }]);
    }

    /**
     * Valid UTF-8 reversed by code point. With mbstring, through UTF-32: strrev of
     * the little-endian text is the big-endian text of the reversed code points
     * (~25x faster than patching reversed multi-byte sequences with preg, PHP-P1).
     * Without it, the reversed bytes of each character are put back in order by
     * three fixed-length passes (their lead-byte classes are disjoint from each
     * other and from the continuation bytes, so no pass mis-matches what another
     * restored).
     */
    private static function reverseCodePoints(string $s): string
    {
        if (function_exists('mb_convert_encoding')) {
            return mb_convert_encoding(strrev(mb_convert_encoding($s, 'UTF-32LE', 'UTF-8')), 'UTF-8', 'UTF-32BE');
        }
        return preg_replace(
            ['/([\x80-\xbf])([\xc0-\xdf])/', '/([\x80-\xbf])([\x80-\xbf])([\xe0-\xef])/',
             '/([\x80-\xbf])([\x80-\xbf])([\x80-\xbf])([\xf0-\xf7])/'],
            ['$2$1', '$3$2$1', '$4$3$2$1'], strrev($s));
    }

    private static function pad(Args $a, bool $left): Value
    {
        $s = $a->text(0);
        $width = $a->nonNegInt(1);
        $fill = $a->text(2);
        if ($fill === '') {
            fail('E_BAD_ARG', 'pad fill must not be empty', $a->posOf(2));
        }
        $have = Utf8::length($s);
        if ($have >= $width) {
            return Value::textTrusted($s);
        }
        if ($width > \Sel\Limits::MAX_TEXT_LEN) {
            fail('E_RANGE', 'pad result would be longer than ' . \Sel\Limits::MAX_TEXT_LEN, $a->pos);
        }
        $need = $width - $have;
        $fillLen = Utf8::length($fill);
        // Whole copies of the fill, then as much of one more as fits — cut at a
        // code point, never inside one.
        $padding = str_repeat($fill, intdiv($need, $fillLen)) . Utf8::slice($fill, 0, $need % $fillLen);
        return Value::textTrusted($left ? $padding . $s : $s . $padding);
    }
}
