<?php
// UTF-8 codec, hand-written on purpose — no mbstring dependency, and strict
// where PHP's own helpers are lenient. Every length, offset and slice in SEL
// counts code points, and that has to mean the same thing here as in JS.
//
// A PHP string is a byte array, so a SEL TEXT value is simply a string that has
// been checked to be valid UTF-8, and asBytes() on it is a no-op.

declare(strict_types=1);

namespace Sel;

final class Utf8
{
    /**
     * Strict validation: rejects overlong forms, surrogates, values above
     * U+10FFFF and truncated sequences. No replacement characters, ever.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     */
    public static function validate(string $b, ?array $pos = null): void
    {
        $n = strlen($b);
        $i = 0;
        while ($i < $n) {
            $c = ord($b[$i]);
            if ($c < 0x80) {
                $i++;
                continue;
            }
            if ($c >= 0xc2 && $c <= 0xdf) {
                $need = 1; $lo = 0x80; $hi = 0xbf;
            } elseif ($c === 0xe0) {
                $need = 2; $lo = 0xa0; $hi = 0xbf;      // reject overlong 3-byte
            } elseif ($c >= 0xe1 && $c <= 0xec) {
                $need = 2; $lo = 0x80; $hi = 0xbf;
            } elseif ($c === 0xed) {
                $need = 2; $lo = 0x80; $hi = 0x9f;      // reject surrogates
            } elseif ($c >= 0xee && $c <= 0xef) {
                $need = 2; $lo = 0x80; $hi = 0xbf;
            } elseif ($c === 0xf0) {
                $need = 3; $lo = 0x90; $hi = 0xbf;      // reject overlong 4-byte
            } elseif ($c >= 0xf1 && $c <= 0xf3) {
                $need = 3; $lo = 0x80; $hi = 0xbf;
            } elseif ($c === 0xf4) {
                $need = 3; $lo = 0x80; $hi = 0x8f;      // cap at U+10FFFF
            } else {
                fail('E_UTF8', sprintf('invalid start byte 0x%x at byte %d', $c, $i), $pos);
                return;
            }

            if ($i + $need >= $n) {
                fail('E_UTF8', "truncated sequence at byte {$i}", $pos);
            }
            for ($k = 1; $k <= $need; $k++) {
                $cc = ord($b[$i + $k]);
                $min = $k === 1 ? $lo : 0x80;
                $max = $k === 1 ? $hi : 0xbf;
                if ($cc < $min || $cc > $max) {
                    $at = $i + $k;
                    fail('E_UTF8', "invalid continuation byte at byte {$at}", $pos);
                }
            }
            $i += $need + 1;
        }
    }


    /**
     * Splits valid UTF-8 into single-code-point strings. The lexer and the text
     * built-ins work on this representation, which is what makes offsets and
     * lengths agree with the JS host.
     *
     * @return list<string>
     */
    public static function chars(string $s): array
    {
        $out = [];
        $n = strlen($s);
        $i = 0;
        while ($i < $n) {
            $c = ord($s[$i]);
            $len = $c < 0x80 ? 1 : ($c < 0xe0 ? 2 : ($c < 0xf0 ? 3 : 4));
            $out[] = substr($s, $i, $len);
            $i += $len;
        }
        return $out;
    }

    public static function length(string $s): int
    {
        return count(self::chars($s));
    }

    /** @return list<int> */
    public static function codePoints(string $s): array
    {
        $out = [];
        foreach (self::chars($s) as $ch) {
            $out[] = self::ord($ch);
        }
        return $out;
    }

    public static function ord(string $ch): int
    {
        $c = ord($ch[0]);
        if ($c < 0x80) {
            return $c;
        }
        if ($c < 0xe0) {
            return (($c & 0x1f) << 6) | (ord($ch[1]) & 0x3f);
        }
        if ($c < 0xf0) {
            return (($c & 0x0f) << 12) | ((ord($ch[1]) & 0x3f) << 6) | (ord($ch[2]) & 0x3f);
        }
        return (($c & 0x07) << 18) | ((ord($ch[1]) & 0x3f) << 12)
            | ((ord($ch[2]) & 0x3f) << 6) | (ord($ch[3]) & 0x3f);
    }

    public static function chr(int $cp): string
    {
        if ($cp < 0x80) {
            return chr($cp);
        }
        if ($cp < 0x800) {
            return chr(0xc0 | ($cp >> 6)) . chr(0x80 | ($cp & 0x3f));
        }
        if ($cp < 0x10000) {
            return chr(0xe0 | ($cp >> 12))
                . chr(0x80 | (($cp >> 6) & 0x3f))
                . chr(0x80 | ($cp & 0x3f));
        }
        return chr(0xf0 | ($cp >> 18))
            . chr(0x80 | (($cp >> 12) & 0x3f))
            . chr(0x80 | (($cp >> 6) & 0x3f))
            . chr(0x80 | ($cp & 0x3f));
    }


    /** Converts a byte offset, as preg_* reports, into a code point index. */
    public static function cpIndex(string $s, int $byteOffset): int
    {
        $count = 0;
        $i = 0;
        while ($i < $byteOffset) {
            $c = ord($s[$i]);
            $i += $c < 0x80 ? 1 : ($c < 0xe0 ? 2 : ($c < 0xf0 ? 3 : 4));
            $count++;
        }
        return $count;
    }

    public static function toHex(string $bytes): string
    {
        return bin2hex($bytes);
    }
}
