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
        $bad = self::firstInvalid($b);
        if ($bad !== null) {
            fail('E_UTF8', $bad[1], $pos);
        }
    }

    /**
     * The first invalid unit of `$b`: its byte index (the start of the bad
     * sequence) and a message, or null if the string is valid UTF-8.
     *
     * @return array{0:int,1:string}|null
     */
    public static function firstInvalid(string $b): ?array
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
                return [$i, sprintf('invalid start byte 0x%x at byte %d', $c, $i)];
            }

            if ($i + $need >= $n) {
                return [$i, "truncated sequence at byte {$i}"];
            }
            for ($k = 1; $k <= $need; $k++) {
                $cc = ord($b[$i + $k]);
                $min = $k === 1 ? $lo : 0x80;
                $max = $k === 1 ? $hi : 0xbf;
                if ($cc < $min || $cc > $max) {
                    $at = $i + $k;
                    return [$i, "invalid continuation byte at byte {$at}"];
                }
            }
            $i += $need + 1;
        }
        return null;
    }

    /**
     * The position of byte index `$byte` in `$b` as every other position is
     * counted: code points, 1-based line and column, LF the only line end. The
     * prefix before `$byte` must be valid UTF-8 (it is when `$byte` came from
     * firstInvalid).
     *
     * @return array{line:int,col:int,offset:int}
     */
    public static function positionAtByte(string $b, int $byte): array
    {
        $offset = 0;
        $line = 1;
        $lineStart = 0;
        for ($i = 0; $i < $byte; $i++) {
            $c = ord($b[$i]);
            if (($c & 0xc0) === 0x80) {
                continue;               // a continuation byte: same code point
            }
            if ($c === 0x0a) {
                $line++;
                $lineStart = $offset + 1;
            }
            $offset++;
        }
        return ['line' => $line, 'col' => $offset - $lineStart + 1, 'offset' => $offset];
    }


    /**
     * ASCII case folding, the only kind SEL has (UPPER and LOWER are ASCII-only
     * by decision, and identifiers are ASCII by construction). PHP's own
     * strtoupper/strtolower/strcasecmp are locale-dependent before 8.2 —
     * composer.json still allows 8.1 — so an application that called
     * setlocale(LC_CTYPE, 'tr_TR.ISO-8859-9') would have `i` fold to a non-ASCII
     * byte and identifiers stop matching. strtr with a fixed table never asks
     * the locale, and is as fast.
     */
    public static function upper(string $s): string
    {
        return strtr($s, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ');
    }

    public static function lower(string $s): string
    {
        return strtr($s, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz');
    }

    /** ASCII-case-insensitive comparison, negative / zero / positive like strcmp. */
    public static function casecmp(string $a, string $b): int
    {
        return strcmp(self::lower($a), self::lower($b));
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
        // ASCII: one byte per code point, split in C (PHP-P1/P9). The empty
        // guard is for PHP < 8.2, where str_split('') is [''].
        if (!preg_match('/[\x80-\xff]/', $s)) {
            return $s === '' ? [] : str_split($s);
        }
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

    /** Is every byte below 0x80? Then bytes and code points are the same thing. */
    public static function isAscii(string $s): bool
    {
        return !preg_match('/[\x80-\xff]/', $s);
    }

    /**
     * Code points in valid UTF-8, without materialising them: a text at the
     * length cap (spec §6.4) is 16 million of them, and an array of that many
     * one-character strings is gigabytes.
     */
    public static function length(string $s): int
    {
        $n = strlen($s);
        if (self::isAscii($s)) return $n;
        // Every code point has exactly one byte that is not a continuation byte.
        // count_chars() tallies all 256 byte values in one C pass (~1 ms/MB, no
        // allocation beyond a 256-entry table): preg_match_all over a megabyte
        // chunk built a match array per chunk and was ~150x slower (PHP-P1).
        $continuation = 0;
        foreach (count_chars($s, 1) as $byte => $times) {
            if ($byte >= 0x80 && $byte <= 0xbf) $continuation += $times;
        }
        return $n - $continuation;
    }

    /**
     * The byte offset of the start of the last `$cps` code points, clamped to 0:
     * walks back from the end over lead bytes, O(cps) whatever the length.
     */
    public static function retreat(string $s, int $cps): int
    {
        $i = strlen($s);
        while ($cps > 0 && $i > 0) {
            $i--;
            while ($i > 0 && (ord($s[$i]) & 0xc0) === 0x80) $i--;
            $cps--;
        }
        return $i;
    }

    /**
     * The byte offset reached by advancing `$cps` code points from byte `$from`,
     * clamped to the end of the string.
     */
    public static function advance(string $s, int $cps, int $from = 0): int
    {
        $n = strlen($s);
        if ($cps <= 0) return $from;
        if (self::isAscii($from === 0 ? $s : substr($s, $from))) {
            return $cps >= $n - $from ? $n : $from + $cps;
        }
        $i = $from;
        while ($cps > 0 && $i < $n) {
            $c = ord($s[$i]);
            $i += $c < 0x80 ? 1 : ($c < 0xe0 ? 2 : ($c < 0xf0 ? 3 : 4));
            $cps--;
        }
        return min($i, $n);
    }

    /** Substring by code points: `$start` 0-based, `$len` null for the rest. */
    public static function slice(string $s, int $start, ?int $len = null): string
    {
        $from = self::advance($s, $start);
        if ($len === null) return substr($s, $from);
        return substr($s, $from, self::advance($s, $len, $from) - $from);
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
}
