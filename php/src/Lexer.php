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
        '???', '??',
        '$==', '$!=', '$<=', '$>=',
        '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
        '.>',
        '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
    ];

    public const RESERVED = [
        'TRUE', 'FALSE', 'NULL', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
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
    /**
     * braceEnds[i]: the index just past the '}' matching the '{' at i, once some
     * scan has established it. See matchBrace.
     *
     * @var array<int,int>
     */
    private array $braceEnds = [];

    // The kinds of work lexRange keeps on its explicit stack.
    private const T_RANGE = 0;
    private const T_PART = 1;
    private const T_CLOSE = 2;
    private const T_END = 3;

    public function __construct(string $source)
    {
        // Validating here means a malformed source is E_UTF8 rather than a
        // silently mangled token.
        // The position is the first invalid unit's, counted in code points of
        // the valid prefix (SPEC §2).
        // PCRE's strict UTF-8 check is exactly "valid UTF-8" (the same strictness
        // as the hand-written codec: overlongs, surrogates and > U+10FFFF are
        // refused), so the byte loop only runs for a source that is about to be
        // refused and has to say where.
        if (preg_match('//u', $source) !== 1) {
            $bad = Utf8::firstInvalid($source);
            if ($bad !== null) {
                fail('E_UTF8', $bad[1], Utf8::positionAtByte($source, $bad[0]));
            }
        }
        $this->chars = Utf8::chars($source);
        $this->n = count($this->chars);
        // Line starts: one C-level scan for the newlines instead of a PHP loop
        // over every character.
        $this->lineStarts = [0];
        foreach (array_keys($this->chars, "\n", true) as $i) {
            $this->lineStarts[] = $i + 1;
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

    /**
     * Lexes chars[from, to) into $out. Interpolation nests without bound, so this
     * is a loop over an explicit stack of tasks rather than a recursion: a
     * literal pushes what it still has to emit (its parts, each interior range,
     * the closers) and the loop pops them in source order.
     *
     * @param list<array<string,mixed>> $out
     */
    private function lexRange(int $from, int $to, array &$out): void
    {
        $stack = [['k' => self::T_RANGE, 'i' => $from, 'to' => $to, 'bal' => null]];
        while ($stack !== []) {
            $task = array_pop($stack);
            switch ($task['k']) {
                case self::T_RANGE:
                    $this->lexTokens($task['i'], $task['to'], $out, $stack, $task['bal']);
                    break;
                case self::T_PART:
                    $this->emitPart($task, $out, $stack);
                    break;
                case self::T_CLOSE:
                    // An interpolation that lexed to nothing: `{}`, `{ }`.
                    if (count($out) === $task['mark'] + 1) {
                        fail('E_SYNTAX', 'empty interpolation {}', $this->posAt($task['part']['from']));
                    }
                    // ... and one whose parentheses do not close inside the braces.
                    if ($task['bal']->s !== []) {
                        fail('E_SYNTAX', 'unclosed ' . end($task['bal']->s) . ' in interpolation', $this->posAt($task['part']['to']));
                    }
                    $out[] = ['type' => 'op', 'value' => ')'] + $this->posAt($task['part']['to']);
                    break;
                case self::T_END:
                    $out[] = ['type' => 'op', 'value' => ')'] + $task['pos'];
                    break;
            }
        }
    }

    /**
     * The flat part of lexRange. A quoted literal with parts ends the run: the
     * tasks it pushes come first, and the rest of the range resumes after them.
     *
     * $bal is the stack (in ->s) of parentheses and brackets open so far in an
     * interpolation body, null at the top level where the parser does the
     * balancing. A body is spliced into the surrounding tokens as `( body )`, so
     * one that closes what it never opened, or leaves something open, would change
     * the meaning of the text around it: each body balances inside its own braces.
     *
     * @param list<array<string,mixed>> $out
     * @param list<array<string,mixed>> $stack
     */
    private function lexTokens(int $from, int $to, array &$out, array &$stack, ?\stdClass $bal): void
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
                $out[] = ['type' => 'ident', 'value' => Utf8::upper($this->slice($i, $j))] + $pos;
                $i = $j;
                continue;
            }

            if ($c === '"') {
                [$parts, $next] = $this->scanQuoted($i, $to);
                if (count($parts) === 1) {
                    $out[] = ['type' => 'text', 'value' => $parts[0]['value']] + $pos;
                    $i = $next;
                    continue;
                }
                // `( "seg" & expr & "seg" )`: the opener now, the rest as tasks,
                // the remainder of this range underneath them.
                $out[] = ['type' => 'op', 'value' => '('] + $pos;
                $stack[] = ['k' => self::T_RANGE, 'i' => $next, 'to' => $to, 'bal' => $bal];
                $stack[] = ['k' => self::T_END, 'pos' => $pos];
                for ($k = count($parts) - 1; $k >= 0; $k--) {
                    $stack[] = ['k' => self::T_PART, 'part' => $parts[$k], 'index' => $k, 'pos' => $pos];
                }
                return;
            }
            if ($c === "'") {
                $i = $this->lexRaw($i, $to, $out);
                continue;
            }

            $op = $this->matchOperator($i, $to);
            if ($op !== null) {
                if ($bal !== null) {
                    if ($op === '(' || $op === '[') {
                        $bal->s[] = $op;
                    } elseif ($op === ')' || $op === ']') {
                        $open = array_pop($bal->s);
                        if ($open === null || ($open === '(') !== ($op === ')')) {
                            fail('E_SYNTAX', "unbalanced $op in interpolation", $pos);
                        }
                    }
                }
                $out[] = ['type' => 'op', 'value' => $op] + $pos;
                $i += strlen($op);
                continue;
            }

            fail('E_SYNTAX', 'unexpected character ' . json_encode($c), $pos);
        }
    }

    /**
     * One part of an interpolated literal: the `&` before it, then either its
     * text or `( interior )`, the interior being a range of its own.
     *
     * @param array<string,mixed> $task
     * @param list<array<string,mixed>> $out
     * @param list<array<string,mixed>> $stack
     */
    private function emitPart(array $task, array &$out, array &$stack): void
    {
        $part = $task['part'];
        $pos = $task['pos'];
        if ($task['index'] > 0) {
            $out[] = ['type' => 'op', 'value' => '&'] + $pos;
        }
        if ($part['kind'] === 'text') {
            $out[] = ['type' => 'text', 'value' => $part['value']] + $pos;
            return;
        }
        $mark = count($out);
        $bal = new \stdClass();
        $bal->s = [];
        $out[] = ['type' => 'op', 'value' => '('] + $this->posAt($part['from']);
        $stack[] = ['k' => self::T_CLOSE, 'mark' => $mark, 'part' => $part, 'bal' => $bal];
        $stack[] = ['k' => self::T_RANGE, 'i' => $part['from'], 'to' => $part['to'], 'bal' => $bal];
    }

    /**
     * The operators by first character, each list in OPERATORS order (longest
     * first) so the first match is the same one a scan of the whole table finds;
     * built once. A token starting with anything else cannot be an operator
     * (it used to cost 31 comparisons per operator token).
     *
     * @var array<string,list<string>>|null
     */
    private static ?array $operatorsByFirst = null;

    private function matchOperator(int $i, int $to): ?string
    {
        $byFirst = self::$operatorsByFirst;
        if ($byFirst === null) {
            $byFirst = [];
            foreach (self::OPERATORS as $op) {
                $byFirst[$op[0]][] = $op;
            }
            self::$operatorsByFirst = $byFirst;
        }
        foreach ($byFirst[$this->chars[$i]] ?? [] as $op) {
            $len = strlen($op);
            if ($i + $len > $to) {
                continue;
            }
            $ok = true;
            for ($k = 1; $k < $len; $k++) {
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

    /**
     * Reads a quoted literal into its parts and the index just past its closing
     * quote, emitting nothing. Every `{...}` is skipped by matchBrace, so the
     * interior is not read here, only located.
     *
     * @return array{0:list<array<string,mixed>>,1:int}
     */
    private function scanQuoted(int $start, int $to): array
    {
        $pos = $this->posAt($start);
        $parts = [];
        $buf = '';
        $i = $start + 1;

        while ($i < $to) {
            $c = $this->chars[$i];

            if ($c === '"') {
                $parts[] = ['kind' => 'text', 'value' => $buf];
                return [$parts, $i + 1];
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
            // /D for the reason in Dec.php: a trailing newline would otherwise
            // satisfy `$` and `\u{41<newline>}` would be accepted here alone.
            if ($hex === '' || strlen($hex) > 6 || preg_match('/^[0-9a-fA-F]+$/D', $hex) !== 1) {
                fail('E_ESCAPE', "bad \\u{{$hex}} escape", $pos);
            }
            $cp = (int) hexdec($hex);
            if ($cp > 0x10ffff || ($cp >= 0xd800 && $cp <= 0xdfff)) {
                fail('E_RANGE', 'code point U+' . Utf8::upper($hex) . ' is not encodable', $pos);
            }
            return [Utf8::chr($cp), $j + 1];
        }

        fail('E_ESCAPE', "unknown escape \\{$e}", $pos);
    }

    /**
     * Returns the index just past the matching '}'. Nested literals are skipped
     * so that a brace inside a string inside an interpolation does not close it.
     *
     * One pass with an explicit stack of what is open (a brace, a string), not a
     * recursion through the strings, and every brace it closes is remembered in
     * braceEnds. The second half is what keeps the lexer linear: a literal nested
     * d deep is located by its parent and again by each of its own ancestors'
     * interiors being lexed, and without the memo each of those locate-passes
     * re-read everything below it. If anything is unterminated the innermost open
     * construct is the one reported, which is where the recursion used to fail.
     */
    private function matchBrace(int $i, int $to): int
    {
        if (isset($this->braceEnds[$i])) {
            return $this->braceEnds[$i];
        }
        // Parallel stacks: whether each open construct is a string, where it
        // began, and (for a brace) how many braces it has open.
        $isStr = [false];
        $at = [$i];
        $depth = [0];
        $top = 0;
        $j = $i;
        for (;;) {
            if ($j >= $to) {
                fail(
                    'E_UNTERMINATED',
                    $isStr[$top] ? 'unterminated text literal' : 'unterminated { in text literal',
                    $this->posAt($at[$top])
                );
            }
            $c = $this->chars[$j];
            if ($isStr[$top]) {
                if ($c === '\\') {
                    $j += 2;
                    continue;
                }
                if ($c === '"') {
                    array_pop($isStr);
                    array_pop($at);
                    array_pop($depth);
                    $top--;
                    $j++;
                    continue;
                }
                if ($c === '{') {
                    $isStr[] = false;
                    $at[] = $j;
                    $depth[] = 0;
                    $top++;
                    continue;
                }
                $j++;
                continue;
            }
            if ($c === '"') {
                $isStr[] = true;
                $at[] = $j;
                $depth[] = 0;
                $top++;
                $j++;
                continue;
            }
            if ($c === "'") {
                $j = $this->skipRaw($j, $to);
                continue;
            }
            if ($c === '{') {
                $depth[$top]++;
                $j++;
                continue;
            }
            if ($c === '}') {
                $depth[$top]--;
                $j++;
                if ($depth[$top] === 0) {
                    $this->braceEnds[$at[$top]] = $j;
                    array_pop($isStr);
                    array_pop($at);
                    array_pop($depth);
                    $top--;
                    if ($top < 0) {
                        return $j;
                    }
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

    /** @return list<array<string,mixed>> */
    public static function tokenizeSource(string $source): array
    {
        return (new self($source))->tokenize();
    }
}
