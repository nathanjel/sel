<?php
// Recursive descent, one function per precedence level, mirroring spec/grammar.md
// and js/src/parser.mjs so the two can be read side by side.

declare(strict_types=1);

namespace Sel;

final class Parser
{
    private const MAX_DEPTH = 200;

    private const ASSIGN_OPS = ['=', '+=', '-=', '*=', '/=', '%=', '&='];
    private const COMPARE_OPS = [
        '==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
    ];
    private const COMPARE_WORDS = ['EQL', 'IN'];

    /** @var list<array<string,mixed>> */
    private array $toks;
    private int $i = 0;
    private int $depth = 0;

    /** @param list<array<string,mixed>> $tokens */
    public function __construct(array $tokens)
    {
        $this->toks = $tokens;
    }

    /** @return array<string,mixed> */
    private function peek(): array
    {
        return $this->toks[$this->i];
    }

    /** @return array<string,mixed> */
    private function next(): array
    {
        return $this->toks[$this->i++];
    }

    private function atOp(string $v): bool
    {
        $t = $this->peek();
        return $t['type'] === 'op' && $t['value'] === $v;
    }

    private function atWord(string $v): bool
    {
        $t = $this->peek();
        return $t['type'] === 'ident' && $t['value'] === $v;
    }

    private function atEof(): bool
    {
        return $this->peek()['type'] === 'eof';
    }

    /** @return array<string,mixed> */
    private function expectOp(string $v): array
    {
        if (!$this->atOp($v)) {
            $t = $this->peek();
            fail('E_SYNTAX', 'expected ' . json_encode($v) . ', got ' . self::describe($t), $t);
        }
        return $this->next();
    }

    /** @param array<string,mixed> $pos */
    private function enter(array $pos): void
    {
        if (++$this->depth > self::MAX_DEPTH) {
            fail('E_DEPTH', 'expression nested too deeply', $pos);
        }
    }

    private function leave(): void
    {
        $this->depth--;
    }

    // --- entry --------------------------------------------------------------

    /** @return array<string,mixed> */
    public function parseProgram(): array
    {
        $node = $this->parseSequence();
        if (!$this->atEof()) {
            $t = $this->peek();
            fail('E_SYNTAX', 'unexpected ' . self::describe($t), $t);
        }
        return $node;
    }

    /** @return array<string,mixed> */
    private function parseSequence(): array
    {
        $start = $this->peek();
        $this->enter($start);
        $items = [$this->parseList()];
        while ($this->atOp(';')) {
            $this->next();
            // A trailing ';' before a closer or end of input is permitted.
            if ($this->atEof() || $this->atOp(')') || $this->atOp(']')) {
                break;
            }
            $items[] = $this->parseList();
        }
        $this->leave();
        return count($items) === 1
            ? $items[0]
            : ['t' => 'seq', 'items' => $items, 'pos' => $items[0]['pos']];
    }

    /** @return array<string,mixed> */
    private function parseList(): array
    {
        $items = [$this->parseAssignment()];
        while ($this->atOp(',')) {
            $this->next();
            $items[] = $this->parseAssignment();
        }
        return count($items) === 1
            ? $items[0]
            : ['t' => 'list', 'items' => $items, 'pos' => $items[0]['pos']];
    }

    /** @return array<string,mixed> */
    private function parseAssignment(): array
    {
        $left = $this->parseOr();
        $t = $this->peek();
        if ($t['type'] === 'op' && in_array($t['value'], self::ASSIGN_OPS, true)) {
            $this->next();
            self::checkTarget($left, $t);
            $value = $this->parseAssignment();
            return [
                't' => 'assign', 'op' => $t['value'], 'target' => $left,
                'value' => $value, 'pos' => $left['pos'],
            ];
        }
        return $left;
    }

    /** @return array<string,mixed> */
    private function parseOr(): array
    {
        return $this->parseWordBinary('OR', fn () => $this->parseXor());
    }

    /** @return array<string,mixed> */
    private function parseXor(): array
    {
        return $this->parseWordBinary('XOR', fn () => $this->parseAnd());
    }

    /** @return array<string,mixed> */
    private function parseAnd(): array
    {
        return $this->parseWordBinary('AND', fn () => $this->parseNot());
    }

    /** @return array<string,mixed> */
    private function parseWordBinary(string $word, callable $sub): array
    {
        $left = $sub();
        while ($this->atWord($word)) {
            $op = $this->next();
            $right = $sub();
            $left = ['t' => 'bin', 'op' => $word, 'l' => $left, 'r' => $right, 'pos' => $op];
        }
        return $left;
    }

    /** @return array<string,mixed> */
    private function parseNot(): array
    {
        if ($this->atWord('NOT')) {
            $op = $this->next();
            return ['t' => 'un', 'op' => 'NOT', 'x' => $this->parseNot(), 'pos' => $op];
        }
        return $this->parseComparison();
    }

    /** Deliberately non-associative: `a < b < c` is a parse error. */
    /** @return array<string,mixed> */
    private function parseComparison(): array
    {
        $left = $this->parseBitOr();
        $t = $this->peek();
        $isOp = $t['type'] === 'op' && in_array($t['value'], self::COMPARE_OPS, true);
        $isWord = $t['type'] === 'ident' && in_array($t['value'], self::COMPARE_WORDS, true);
        if (!$isOp && !$isWord) {
            return $left;
        }

        $this->next();
        $right = $this->parseBitOr();
        $after = $this->peek();
        if (($after['type'] === 'op' && in_array($after['value'], self::COMPARE_OPS, true))
            || ($after['type'] === 'ident' && in_array($after['value'], self::COMPARE_WORDS, true))) {
            fail(
                'E_SYNTAX',
                "comparison operators do not chain — parenthesise, as in (a {$t['value']} b) AND (b {$after['value']} c)",
                $after,
            );
        }
        return ['t' => 'bin', 'op' => $t['value'], 'l' => $left, 'r' => $right, 'pos' => $t];
    }

    /** @return array<string,mixed> */
    private function parseBitOr(): array
    {
        return $this->parseWordBinary('BOR', fn () => $this->parseBitXor());
    }

    /** @return array<string,mixed> */
    private function parseBitXor(): array
    {
        return $this->parseWordBinary('BXOR', fn () => $this->parseBitAnd());
    }

    /** @return array<string,mixed> */
    private function parseBitAnd(): array
    {
        return $this->parseWordBinary('BAND', fn () => $this->parseConcat());
    }

    /** @return array<string,mixed> */
    private function parseConcat(): array
    {
        return $this->parseOpBinary(['&'], fn () => $this->parseAdditive());
    }

    /** @return array<string,mixed> */
    private function parseAdditive(): array
    {
        return $this->parseOpBinary(['+', '-'], fn () => $this->parseMultiplicative());
    }

    /** @return array<string,mixed> */
    private function parseMultiplicative(): array
    {
        return $this->parseOpBinary(['*', '/', '%'], fn () => $this->parseUnary());
    }

    /**
     * @param list<string> $ops
     * @return array<string,mixed>
     */
    private function parseOpBinary(array $ops, callable $sub): array
    {
        $left = $sub();
        for (;;) {
            $t = $this->peek();
            if ($t['type'] !== 'op' || !in_array($t['value'], $ops, true)) {
                return $left;
            }
            $this->next();
            $left = ['t' => 'bin', 'op' => $t['value'], 'l' => $left, 'r' => $sub(), 'pos' => $t];
        }
    }

    /** @return array<string,mixed> */
    private function parseUnary(): array
    {
        if ($this->atOp('-')) {
            $op = $this->next();
            return ['t' => 'un', 'op' => 'NEG', 'x' => $this->parseUnary(), 'pos' => $op];
        }
        return $this->parsePostfix();
    }

    /** @return array<string,mixed> */
    private function parsePostfix(): array
    {
        $node = $this->parsePrimary();
        while ($this->atOp('[')) {
            $br = $this->next();
            $idx = $this->parseSequence();
            $this->expectOp(']');
            $node = ['t' => 'index', 'obj' => $node, 'idx' => $idx, 'pos' => $br];
        }
        return $node;
    }

    /** @return array<string,mixed> */
    private function parsePrimary(): array
    {
        $t = $this->peek();
        $this->enter($t);
        try {
            if ($t['type'] === 'num') {
                $this->next();
                // Canonicalised once, here: the literal 007 is the value 7.
                return ['t' => 'num', 'v' => Dec::format(Dec::parse($t['value'])), 'pos' => $t];
            }
            if ($t['type'] === 'text') {
                $this->next();
                return ['t' => 'text', 'v' => $t['value'], 'pos' => $t];
            }

            if ($t['type'] === 'ident') {
                if ($t['value'] === 'TRUE' || $t['value'] === 'FALSE') {
                    $this->next();
                    return ['t' => 'bool', 'v' => $t['value'] === 'TRUE', 'pos' => $t];
                }
                $after = $this->toks[$this->i + 1] ?? null;
                if ($after !== null && $after['type'] === 'op' && $after['value'] === '(') {
                    return $this->parseCall();
                }
                if (in_array($t['value'], Lexer::RESERVED, true)) {
                    fail('E_RESERVED', "{$t['value']} is a reserved word and cannot be a variable", $t);
                }
                $this->next();
                return ['t' => 'var', 'name' => $t['value'], 'pos' => $t];
            }

            if ($t['type'] === 'op' && $t['value'] === '(') {
                $this->next();
                if ($this->atOp(')')) {
                    fail('E_SYNTAX', 'empty parentheses', $t);
                }
                $inner = $this->parseSequence();
                $this->expectOp(')');
                // Marked so that F((1,2)) passes one list rather than two arguments.
                $inner['grouped'] = true;
                return $inner;
            }

            fail('E_SYNTAX', 'unexpected ' . self::describe($t), $t);
        } finally {
            $this->leave();
        }
    }

    /** @return array<string,mixed> */
    private function parseCall(): array
    {
        $nameTok = $this->next();
        $this->expectOp('(');
        if ($this->atOp(')')) {
            $this->next();
            $args = [];
        } else {
            $inner = $this->parseSequence();
            $this->expectOp(')');
            $args = ($inner['t'] === 'list' && empty($inner['grouped'])) ? $inner['items'] : [$inner];
        }

        $spec = Registry::lookup($nameTok['value']);
        if ($spec === null) {
            fail('E_UNKNOWN_FUNC', "unknown function {$nameTok['value']}", $nameTok);
        }
        $count = count($args);
        if ($count < $spec['min'] || $count > $spec['max']) {
            fail('E_ARITY', "{$spec['name']} takes " . self::arityText($spec) . ", got {$count}", $nameTok);
        }
        if ($spec['arityError'] !== null) {
            $problem = ($spec['arityError'])($count);
            if ($problem !== null) {
                fail('E_ARITY', $problem, $nameTok);
            }
        }
        return ['t' => 'call', 'name' => $spec['name'], 'spec' => $spec, 'args' => $args, 'pos' => $nameTok];
    }

    /** @param array<string,mixed> $spec */
    private static function arityText(array $spec): string
    {
        if ($spec['max'] === PHP_INT_MAX) {
            return "at least {$spec['min']} argument" . ($spec['min'] === 1 ? '' : 's');
        }
        if ($spec['min'] === $spec['max']) {
            return "{$spec['min']} argument" . ($spec['min'] === 1 ? '' : 's');
        }
        return "{$spec['min']} to {$spec['max']} arguments";
    }

    /** @param array<string,mixed> $t */
    private static function describe(array $t): string
    {
        if ($t['type'] === 'eof') {
            return 'end of input';
        }
        if ($t['type'] === 'text') {
            return 'a text literal';
        }
        if ($t['type'] === 'num') {
            return "number {$t['value']}";
        }
        return json_encode($t['value']);
    }

    /**
     * The target must be an identifier followed by zero or more index operations.
     *
     * @param array<string,mixed> $node
     * @param array<string,mixed> $opTok
     */
    private static function checkTarget(array $node, array $opTok): void
    {
        $n = $node;
        while ($n['t'] === 'index') {
            $n = $n['obj'];
        }
        if ($n['t'] !== 'var' || !empty($node['grouped'])) {
            fail('E_BAD_ASSIGN', "cannot assign with {$opTok['value']} to this expression", $node['pos']);
        }
    }

    /** @return array<string,mixed> */
    public static function parse(string $source): array
    {
        return (new self(Lexer::tokenizeSource($source)))->parseProgram();
    }
}
