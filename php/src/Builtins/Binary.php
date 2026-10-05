<?php
declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Budget;
use Sel\Dec;
use Sel\Registry;
use Sel\Utf8;
use Sel\Value;

use function Sel\fail;

final class Binary
{
    private const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

    /** @var array<int,int>|null */
    private static ?array $crcTable = null;

    public static function register(): void
    {
        Registry::define(['name' => 'BLEN', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int(strlen($a->bytes(0)))]);

        Registry::define(['name' => 'TO_UTF8', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Budget::checkText(strlen($b), $a->pos, 'the TO_UTF8 result');
                return Value::bin($b);
            }]);

        Registry::define(['name' => 'FROM_UTF8', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Utf8::validate($b, $a->posOf(0));
                return Value::text($b);
            }]);

        Registry::define(['name' => 'TO_HEX', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Budget::checkText(2 * strlen($b), $a->pos, 'the TO_HEX result');
                return Value::text(bin2hex($b));
            }]);

        Registry::define(['name' => 'FROM_HEX', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                if (strlen($s) % 2 !== 0) {
                    fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', $a->posOf(0));
                }
                if ($s !== '' && preg_match('/^[0-9a-fA-F]+$/D', $s) !== 1) {
                    fail('E_BAD_ARG', 'FROM_HEX: ' . json_encode($s) . ' is not hex', $a->posOf(0));
                }
                return Value::bin($s === '' ? '' : (string) hex2bin($s));
            }]);

        Registry::define(['name' => 'ENCODE_BASE64', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Budget::checkText(4 * intdiv(strlen($b) + 2, 3), $a->pos, 'the ENCODE_BASE64 result');
                // The standard alphabet with padding: what the loop this replaces wrote.
                return Value::text(base64_encode($b));
            }]);

        // Strict: padding is required and any character outside the alphabet fails.
        Registry::define(['name' => 'DECODE_BASE64', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                $pos = $a->posOf(0);
                $len = strlen($s);
                if ($len % 4 !== 0) {
                    fail('E_BAD_ARG', 'DECODE_BASE64 needs a length that is a multiple of 4', $pos);
                }
                // The strict shape by strspn, then PHP's own decoder (PHP-P19): the
                // standard alphabet, at most two `=` and only at the end, and — like
                // the loop below — non-canonical trailing bits accepted. (A regex with
                // a quantified group runs out of PCRE's JIT stack past ~300 KB.) Anything
                // refused here falls through to the loop, which raises the precise error.
                $pad = $len > 0 && $s[$len - 1] === '=' ? ($s[$len - 2] === '=' ? 2 : 1) : 0;
                if (strspn($s, self::B64, 0, $len - $pad) === $len - $pad) {
                    $decoded = base64_decode($s, true);
                    if ($decoded !== false) {
                        return Value::bin($decoded);
                    }
                }
                $index = array_flip(str_split(self::B64));
                $out = '';
                for ($i = 0; $i < $len; $i += 4) {
                    $quad = [];
                    $padding = 0;
                    for ($k = 0; $k < 4; $k++) {
                        $ch = $s[$i + $k];
                        if ($ch === '=') {
                            if ($i + 4 < $len || $k < 2) {
                                fail('E_BAD_ARG', 'misplaced base64 padding', $pos);
                            }
                            $padding++;
                            $quad[] = 0;
                            continue;
                        }
                        if ($padding > 0) {
                            fail('E_BAD_ARG', 'misplaced base64 padding', $pos);
                        }
                        if (!isset($index[$ch])) {
                            fail('E_BAD_ARG', 'invalid base64 character ' . json_encode($ch), $pos);
                        }
                        $quad[] = $index[$ch];
                    }
                    $x = ($quad[0] << 18) | ($quad[1] << 12) | ($quad[2] << 6) | $quad[3];
                    $out .= chr(($x >> 16) & 255);
                    if ($padding < 2) {
                        $out .= chr(($x >> 8) & 255);
                    }
                    if ($padding < 1) {
                        $out .= chr($x & 255);
                    }
                }
                return Value::bin($out);
            }]);

        // CRC-32/ISO-HDLC: reflected, polynomial 0xEDB88320, init and final xor
        // all ones. Written out rather than delegated to crc32() so the algorithm
        // is visibly the same one the JS host runs.
        Registry::define(['name' => 'CRC32', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                // PHP's crc32() is this very CRC (ISO-HDLC, polynomial 0xEDB88320, init and
                // final xor all ones) as a C loop: 1 MB in 0.1 ms against 80 ms for the
                // PHP table loop it replaces (PHP-P19). crc32Reference() keeps the
                // written-out algorithm for the tests to compare it with.
                return Value::text(sprintf('%08x', crc32($a->bytes(0))));
            }]);

        Registry::define(['name' => 'BTL', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Budget::checkCollection(strlen($b), $a->pos, 'the BTL result');
                $out = [];
                if ($b !== '') {
                    foreach (unpack('C*', $b) as $byte) {
                        $out[] = Value::int($byte);
                    }
                }
                return Value::list($out);
            }]);

        // LTB: a list of byte values, each a whole number 0..255 of any scale
        // (`1.0` and "1.0" are the byte 1), to a BIN. An empty list is the empty
        // BIN; a fractional element is E_NOT_INT and one outside 0..255 E_RANGE
        // (spec §7.7).
        Registry::define(['name' => 'LTB', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $v = $a->val(0);
                if ($v->kind === Value::NONE && $v->size() === 0) {
                    return Value::bin('');
                }
                $items = $v->size() > 0 ? $v->values() : [$v];
                $out = '';
                foreach ($items as $i => $item) {
                    $d = $item->asDecimal($a->posOf(0));
                    if (!Dec::isInteger($d)) {
                        $k = $i + 1;
                        fail('E_NOT_INT', "LTB element {$k} is not a whole number", $a->posOf(0));
                    }
                    $n = Dec::toInt($d);
                    if ($n < 0 || $n > 255) {
                        $k = $i + 1;
                        fail('E_RANGE', "LTB element {$k} is not a byte value", $a->posOf(0));
                    }
                    $out .= chr($n);
                }
                return Value::bin($out);
            }]);
    }

    /** @return array<int,int> */
    /** The table-driven CRC-32/ISO-HDLC, kept as the reference `crc32()` is tested against. */
    public static function crc32Reference(string $b): int
    {
        $t = self::crcTable();
        $crc = 0xffffffff;
        for ($i = 0, $n = strlen($b); $i < $n; $i++) {
            $crc = $t[($crc ^ ord($b[$i])) & 255] ^ (($crc >> 8) & 0x00ffffff);
        }
        return $crc ^ 0xffffffff;
    }

    private static function crcTable(): array
    {
        if (self::$crcTable !== null) {
            return self::$crcTable;
        }
        $t = [];
        for ($i = 0; $i < 256; $i++) {
            $c = $i;
            for ($k = 0; $k < 8; $k++) {
                $c = ($c & 1) ? (0xedb88320 ^ (($c >> 1) & 0x7fffffff)) : (($c >> 1) & 0x7fffffff);
            }
            $t[$i] = $c;
        }
        return self::$crcTable = $t;
    }
}
