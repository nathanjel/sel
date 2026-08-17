<?php
declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
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
            'fn' => static fn (Args $a): Value => Value::bin($a->bytes(0))]);

        Registry::define(['name' => 'FROM_UTF8', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                Utf8::validate($b, $a->posOf(0));
                return Value::text($b);
            }]);

        Registry::define(['name' => 'TO_HEX', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::text(bin2hex($a->bytes(0)))]);

        Registry::define(['name' => 'FROM_HEX', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $s = $a->text(0);
                if (strlen($s) % 2 !== 0) {
                    fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', $a->posOf(0));
                }
                if ($s !== '' && preg_match('/^[0-9a-fA-F]+$/', $s) !== 1) {
                    fail('E_BAD_ARG', 'FROM_HEX: ' . json_encode($s) . ' is not hex', $a->posOf(0));
                }
                return Value::bin($s === '' ? '' : (string) hex2bin($s));
            }]);

        Registry::define(['name' => 'ENCODE_BASE64', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                $out = '';
                for ($i = 0, $n = strlen($b); $i < $n; $i += 3) {
                    $x = (ord($b[$i]) << 16)
                        | (($i + 1 < $n ? ord($b[$i + 1]) : 0) << 8)
                        | ($i + 2 < $n ? ord($b[$i + 2]) : 0);
                    $out .= self::B64[($x >> 18) & 63] . self::B64[($x >> 12) & 63];
                    $out .= $i + 1 < $n ? self::B64[($x >> 6) & 63] : '=';
                    $out .= $i + 2 < $n ? self::B64[$x & 63] : '=';
                }
                return Value::text($out);
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
                $t = self::crcTable();
                $b = $a->bytes(0);
                $crc = 0xffffffff;
                for ($i = 0, $n = strlen($b); $i < $n; $i++) {
                    $crc = $t[($crc ^ ord($b[$i])) & 255] ^ (($crc >> 8) & 0x00ffffff);
                }
                return Value::text(sprintf('%08x', $crc ^ 0xffffffff));
            }]);

        Registry::define(['name' => 'BTL', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $b = $a->bytes(0);
                $out = [];
                for ($i = 0, $n = strlen($b); $i < $n; $i++) {
                    $out[] = Value::int(ord($b[$i]));
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'LTB', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $v = $a->val(0);
                $items = $v->size() > 0 ? $v->values() : [$v];
                $out = '';
                foreach ($items as $i => $item) {
                    $d = $item->asDecimal($a->posOf(0));
                    $n = (int) $d['digits'];
                    if ($d['scale'] !== 0 || $d['neg'] || $n > 255) {
                        $k = $i + 1;
                        fail('E_RANGE', "LTB element {$k} is not a byte value", $a->posOf(0));
                    }
                    $out .= chr($n);
                }
                return Value::bin($out);
            }]);
    }

    /** @return array<int,int> */
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
