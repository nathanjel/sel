<?php
// Tokeniser. See spec/grammar.md. Ported from js/src/lexer.mjs.
//
// The source is held as an array of single-code-point strings, so every offset,
// line and column in an error is a code point index — the same number the JS host
// reports for the same source.
//
// String interpolation is resolved here and nowhere else: a literal containing
// {…} is emitted as the token stream of a parenthesised `&` chain, so the parser
// never learns that interpolation exists.

declare(strict_types=1);

namespace Sel;

final class Lexer
{
    /** Longest first: `$<=` must not lex as `$<` then `=`. */
    public const OPERATORS = [
        '$==', '$!=', '$<=', '$>=',
        '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
        '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
    ];

    public const RESERVED = [
        'TRUE', 'FALSE', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
    ];

    private const SIMPLE_ESCAPES = [
        '\\' => '\\', '"' => '"', 'n' => "\n", 't' => "\t", 'r' => "\r",
        '{' => '{', '}' => '}',
    ];

    /** @var list<string> */
    private array $chars;
    private int $n;
    /** @var list<int> */
    private array $lineStarts;

    public function __construct(string $source)
    {
        // Validating here means a malformed source is E_UTF8 rather than a
        // silently mangled token.
        Utf8::validate($source);
        $this->chars = Utf8::chars($source);
        $this->n = count($this->chars);
        $this->lineStarts = [0];
        for ($i = 0; $i < $this->n; $i++) {
            if ($this->chars[$i] === "\n") {
                $this->lineStarts[] = $i + 1;
            }
        }
    }

    /** @return array{line:int,col:int,offset:int} */
    private function posAt(int $offset): array
    {
        $lo = 0;
        $hi = count($this->lineStarts) - 1;
        while ($lo < $hi) {
            $mid = intdiv($lo + $hi + 1, 2);
            if ($this->lineStarts[$mid] <= $offset) {
                $lo = $mid;
            } else {
                $hi = $mid - 1;
            }
        }
        return [
            'line' => $lo + 1,
            'col' => $offset - $this->lineStarts[$lo] + 1,
            'offset' => $offset,
        ];
    }

    private function slice(int $from, int $to): string
    {
        return implode('', array_slice($this->chars, $from, $to - $from));
    }

    private static function isDigit(string $c): bool
    {
        return $c >= '0' && $c <= '9';
    }

    private static function isAlpha(string $c): bool
    {
        return ($c >= 'A' && $c <= 'Z') || ($c >= 'a' && $c <= 'z') || $c === '_';
    }

    private static function isIdent(string $c): bool
    {
        return self::isAlpha($c) || self::isDigit($c);
    }

    private static function isSpace(string $c): bool
    {
        return $c === ' ' || $c === "\t" || $c === "\r" || $c === "\n";
    }

    /** @return list<array<string,mixed>> */
    public function tokenize(): array
    {
        $out = [];
        $this->lexRange(0, $this->n, $out);
        $out[] = ['type' => 'eof', 'value' => ''] + $this->posAt($this->n);
        return $out;
    }

    /** @param list<array<string,mixed>> $out */
    private function lexRange(int $from, int $to, array &$out): void
    {
        $i = $from;
        while ($i < $to) {
            $c = $this->chars[$i];

            if (self::isSpace($c)) {
                $i++;
                continue;
            }

            if ($c === '#') {
                while ($i < $to && $this->chars[$i] !== "\n") {
                    $i++;
                }
                continue;
            }

            $pos = $this->posAt($i);

            if (self::isDigit($c)) {
                $j = $i;
                while ($j < $to && self::isDigit($this->chars[$j])) {
                    $j++;
                }
                // Only consume the dot when a digit follows, so `1.` is not a number.
                if ($j + 1 < $to && $this->chars[$j] === '.' && self::isDigit($this->chars[$j + 1])) {
                    $j++;
                    while ($j < $to && self::isDigit($this->chars[$j])) {
                        $j++;
                    }
                }
                $out[] = ['type' => 'num', 'value' => $this->slice($i, $j)] + $pos;
                $i = $j;
                continue;
            }

            if (self::isAlpha($c)) {
                $j = $i;
                while ($j < $to && self::isIdent($this->chars[$j])) {
                    $j++;
                }
                $out[] = ['type' => 'ident', 'value' => strtoupper($this->slice($i, $j))] + $pos;
                $i = $j;
                continue;
            }

            if ($c === '"') {
                $i = $this->lexQuoted($i, $to, $out);
                continue;
            }
            if ($c === "'") {
                $i = $this->lexRaw($i, $to, $out);
                continue;
            }

            $op = $this->matchOperator($i, $to);
            if ($op !== null) {
                $out[] = ['type' => 'op', 'value' => $op] + $pos;
                $i += strlen($op);
                continue;
            }

            fail('E_SYNTAX', 'unexpected character ' . json_encode($c), $pos);
        }
    }

    private function matchOperator(int $i, int $to): ?string
    {
        foreach (self::OPERATORS as $op) {
            $len = strlen($op);
            if ($i + $len > $to) {
                continue;
            }
            $ok = true;
            for ($k = 0; $k < $len; $k++) {
                if ($this->chars[$i + $k] !== $op[$k]) {
                    $ok = false;
                    break;
                }
            }
            if ($ok) {
                return $op;
            }
        }
        return null;
    }

    // --- text literals ------------------------------------------------------

    /**
     * Raw 'literals' take no escapes and no interpolation; '' is one quote. This
     * is the form to use for regex patterns.
     *
     * @param list<array<string,mixed>> $out
     */
    private function lexRaw(int $start, int $to, array &$out): int
    {
        $pos = $this->posAt($start);
        $i = $start + 1;
        $buf = '';
        while ($i < $to) {
            $c = $this->chars[$i];
            if ($c === "'") {
                if ($i + 1 < $to && $this->chars[$i + 1] === "'") {
                    $buf .= "'";
                    $i += 2;
                    continue;
                }
                $out[] = ['type' => 'text', 'value' => $buf] + $pos;
                return $i + 1;
            }
            $buf .= $c;
            $i++;
        }
        fail('E_UNTERMINATED', 'unterminated raw text literal', $pos);
    }

    /** @param list<array<string,mixed>> $out */
    private function lexQuoted(int $start, int $to, array &$out): int
    {
        $pos = $this->posAt($start);
        $parts = [];
        $buf = '';
        $i = $start + 1;

        while ($i < $to) {
            $c = $this->chars[$i];

            if ($c === '"') {
                $parts[] = ['kind' => 'text', 'value' => $buf];
                $this->emitParts($parts, $pos, $out);
                return $i + 1;
            }

            if ($c === '\\') {
                [$text, $next] = $this->readEscape($i, $to);
                $buf .= $text;
                $i = $next;
                continue;
            }

            if ($c === '{') {
                $close = $this->matchBrace($i, $to) - 1;   // index of matching '}'
                $parts[] = ['kind' => 'text', 'value' => $buf];
                $buf = '';
                $parts[] = ['kind' => 'expr', 'from' => $i + 1, 'to' => $close];
                $i = $close + 1;
                continue;
            }

            $buf .= $c;
            $i++;
        }
        fail('E_UNTERMINATED', 'unterminated text literal', $pos);
    }

    /** @return array{0:string,1:int} */
    private function readEscape(int $i, int $to): array
    {
        $pos = $this->posAt($i);
        if ($i + 1 >= $to) {
            fail('E_UNTERMINATED', 'text literal ends in a backslash', $pos);
        }
        $e = $this->chars[$i + 1];

        if (isset(self::SIMPLE_ESCAPES[$e])) {
            return [self::SIMPLE_ESCAPES[$e], $i + 2];
        }

        if ($e === 'u') {
            if ($i + 2 >= $to || $this->chars[$i + 2] !== '{') {
                fail('E_ESCAPE', '\\u must be followed by {', $pos);
            }
            $j = $i + 3;
            $hex = '';
            while ($j < $to && $this->chars[$j] !== '}') {
                $hex .= $this->chars[$j];
                $j++;
            }
            if ($j >= $to) {
                fail('E_UNTERMINATED', 'unterminated \\u{...} escape', $pos);
            }
            if ($hex === '' || strlen($hex) > 6 || preg_match('/^[0-9a-fA-F]+$/', $hex) !== 1) {
                fail('E_ESCAPE', "bad \\u{{$hex}} escape", $pos);
            }
            $cp = (int) hexdec($hex);
            if ($cp > 0x10ffff || ($cp >= 0xd800 && $cp <= 0xdfff)) {
                fail('E_RANGE', 'code point U+' . strtoupper($hex) . ' is not encodable', $pos);
            }
            return [Utf8::chr($cp), $j + 1];
        }

        fail('E_ESCAPE', "unknown escape \\{$e}", $pos);
    }

    /**
     * Returns the index just past the matching '}'. Nested literals are skipped
     * so that a brace inside a string inside an interpolation does not close it.
     */
    private function matchBrace(int $i, int $to): int
    {
        $pos = $this->posAt($i);
        $depth = 0;
        $j = $i;
        while ($j < $to) {
            $c = $this->chars[$j];
            if ($c === '"') {
                $j = $this->skipQuoted($j, $to);
                continue;
            }
            if ($c === "'") {
                $j = $this->skipRaw($j, $to);
                continue;
            }
            if ($c === '{') {
                $depth++;
                $j++;
                continue;
            }
            if ($c === '}') {
                $depth--;
                $j++;
                if ($depth === 0) {
                    return $j;
                }
                continue;
            }
            if ($c === '#') {
                while ($j < $to && $this->chars[$j] !== "\n") {
                    $j++;
                }
                continue;
            }
            $j++;
        }
        fail('E_UNTERMINATED', 'unterminated { in text literal', $pos);
    }

    private function skipQuoted(int $j, int $to): int
    {
        $pos = $this->posAt($j);
        $j++;
        while ($j < $to) {
            $c = $this->chars[$j];
            if ($c === '\\') {
                $j += 2;
                continue;
            }
            if ($c === '"') {
                return $j + 1;
            }
            if ($c === '{') {
                $j = $this->matchBrace($j, $to);
                continue;
            }
            $j++;
        }
        fail('E_UNTERMINATED', 'unterminated text literal', $pos);
    }

    private function skipRaw(int $j, int $to): int
    {
        $pos = $this->posAt($j);
        $j++;
        while ($j < $to) {
            if ($this->chars[$j] === "'") {
                if ($j + 1 < $to && $this->chars[$j + 1] === "'") {
                    $j += 2;
                    continue;
                }
                return $j + 1;
            }
            $j++;
        }
        fail('E_UNTERMINATED', 'unterminated raw text literal', $pos);
    }

    /**
     * A literal with no interpolation is one token. Otherwise it becomes the
     * tokens of `( "seg" & expr & "seg" )` — empty segments included, so the
     * result always goes through `&` and obeys §5.2.
     *
     * @param list<array<string,mixed>> $parts
     * @param array{line:int,col:int,offset:int} $pos
     * @param list<array<string,mixed>> $out
     */
    private function emitParts(array $parts, array $pos, array &$out): void
    {
        if (count($parts) === 1) {
            $out[] = ['type' => 'text', 'value' => $parts[0]['value']] + $pos;
            return;
        }
        $out[] = ['type' => 'op', 'value' => '('] + $pos;
        foreach ($parts as $k => $part) {
            if ($k > 0) {
                $out[] = ['type' => 'op', 'value' => '&'] + $pos;
            }
            if ($part['kind'] === 'text') {
                $out[] = ['type' => 'text', 'value' => $part['value']] + $pos;
            } else {
                $mark = count($out);
                $out[] = ['type' => 'op', 'value' => '('] + $this->posAt($part['from']);
                $this->lexRange($part['from'], $part['to'], $out);
                if (count($out) === $mark + 1) {
                    fail('E_SYNTAX', 'empty interpolation {}', $this->posAt($part['from']));
                }
                $out[] = ['type' => 'op', 'value' => ')'] + $this->posAt($part['to']);
            }
        }
        $out[] = ['type' => 'op', 'value' => ')'] + $pos;
    }

    /** @return list<array<string,mixed>> */
    public static function tokenizeSource(string $source): array
    {
        return (new self($source))->tokenize();
    }
}
