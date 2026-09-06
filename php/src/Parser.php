<?php
// Precedence climbing, mirroring js/src/parser.mjs and python/sel/parser.py so
// the three can be read side by side. See docs/PARSER-MIGRATION.md.
//
// This file used to transcribe spec/grammar.md one function per production,
// seventeen deep, with an `fn () => ...` closure at each of the nine binary
// helpers. That cost 35 stack frames per level of parenthesis nesting, and
// E_DEPTH does not trip until 100 nested parens -- invisible here, fatal on the
// Python host, whose default recursion limit is 1000. The ladder below is that
// chain, as data.
//
// The depth arithmetic is unchanged and is not free to change: conformance/
// 10-limits.selt pins two increments per paren (parseSequence + parsePrimary),
// E_DEPTH at 1:101 for 100 parens, 1:200 for a `-` chain and 1:797 for a NOT
// chain. Prefix operators are counted only when actually consumed.

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

    // spec/SPEC.md §5, as a table. Higher binds tighter. The gaps are the levels
    // that are not infix: 16 is postfix/primary, 15 is unary minus, 7 is NOT.
    private const BP_SEQ = 1;       // ;
    private const BP_LIST = 2;      // ,
    private const BP_ASSIGN = 3;    // = += -= *= /= %= &=   (right associative)
    private const BP_OR = 4;
    private const BP_XOR = 5;
    private const BP_AND = 6;
    private const BP_NOT = 7;       // prefix
    private const BP_COMPARE = 8;   // non-associative
    private const BP_BOR = 9;
    private const BP_BXOR = 10;
    private const BP_BAND = 11;
    private const BP_CONCAT = 12;   // &
    private const BP_ADD = 13;      // + -
    private const BP_MUL = 14;      // * / %
    private const BP_NEG = 15;      // prefix

    // BP_SEQ and BP_LIST are deliberately unused: `;` and `,` build N-ary nodes,
    // so they stay hand-written loops in parseSequence/parseList rather than
    // table rows. They are declared anyway so the ladder above reads as
    // spec/SPEC.md §5 does, with no silent gap at the loose end.

    /** @var array<string, array{int, string}>|null */
    private static ?array $infixOps = null;

    /** @var array<string, array{int, string}>|null */
    private static ?array $infixWords = null;

    /**
     * Symbol operator -> [binding power, associativity]. Built once from the
     * lists above rather than written out a second time: PHP has no loop in a
     * constant expression, and the assignment and comparison operators are
     * already named there — two copies would be two places to forget one.
     *
     * @return array<string, array{int, string}>
     */
    private static function infixOps(): array
    {
        if (self::$infixOps === null) {
            $t = [
                '&' => [self::BP_CONCAT, 'L'],
                '+' => [self::BP_ADD, 'L'], '-' => [self::BP_ADD, 'L'],
                '*' => [self::BP_MUL, 'L'], '/' => [self::BP_MUL, 'L'], '%' => [self::BP_MUL, 'L'],
            ];
            foreach (self::ASSIGN_OPS as $op) {
                $t[$op] = [self::BP_ASSIGN, 'R'];
            }
            foreach (self::COMPARE_OPS as $op) {
                $t[$op] = [self::BP_COMPARE, 'N'];
            }
            self::$infixOps = $t;
        }
        return self::$infixOps;
    }

    /**
     * Word operator -> [binding power, associativity]. Word operators lex as
     * identifiers and symbol operators as `op` tokens, so they are two tables
     * sharing one set of binding powers, built the same way from the same lists.
     *
     * @return array<string, array{int, string}>
     */
    private static function infixWords(): array
    {
        if (self::$infixWords === null) {
            $t = [
                'OR' => [self::BP_OR, 'L'], 'XOR' => [self::BP_XOR, 'L'], 'AND' => [self::BP_AND, 'L'],
                'BOR' => [self::BP_BOR, 'L'], 'BXOR' => [self::BP_BXOR, 'L'], 'BAND' => [self::BP_BAND, 'L'],
            ];
            foreach (self::COMPARE_WORDS as $w) {
                $t[$w] = [self::BP_COMPARE, 'N'];
            }
            self::$infixWords = $t;
        }
        return self::$infixWords;
    }

    /**
     * The two tables are one lookup. Every question about an operator — what it
     * binds at, how it associates, and whether it may follow a comparison — is
     * answered from here, so adding an operator really is adding a row. Asking a
     * separate list anywhere would put that claim back in doubt.
     *
     * @param array<string,mixed> $t
     * @return array{int, string}|null
     */
    private static function infixEntry(array $t): ?array
    {
        if ($t['type'] === 'op') {
            return self::infixOps()[$t['value']] ?? null;
        }
        if ($t['type'] === 'ident') {
            return self::infixWords()[$t['value']] ?? null;
        }
        return null;
    }

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
        // The try/finally is new. It costs nothing — a failing parse abandons the
        // Parser either way — and the Lisp and Python hosts already protect this
        // counter, so this is the asymmetry docs/PARSER-MIGRATION.md asks the
        // transcribed hosts to converge on rather than a deviation.
        $start = $this->peek();
        $this->enter($start);
        try {
            $items = [$this->parseList()];
            while ($this->atOp(';')) {
                $this->next();
                // A trailing ';' before a closer or end of input is permitted.
                if ($this->atEof() || $this->atOp(')') || $this->atOp(']')) {
                    break;
                }
                $items[] = $this->parseList();
            }
        } finally {
            $this->leave();
        }
        return count($items) === 1
            ? $items[0]
            : ['t' => 'seq', 'items' => $items, 'pos' => $items[0]['pos']];
    }

    /** @return array<string,mixed> */
    private function parseList(): array
    {
        $items = [$this->parseTerm(self::BP_ASSIGN)];
        while ($this->atOp(',')) {
            $this->next();
            $items[] = $this->parseTerm(self::BP_ASSIGN);
        }
        return count($items) === 1
            ? $items[0]
            : ['t' => 'list', 'items' => $items, 'pos' => $items[0]['pos']];
    }

    // --- the precedence-climbing loop ----------------------------------------

    /** @return array<string,mixed> */
    private function parseTerm(int $minBp): array
    {
        $left = $this->parsePrefix($minBp);

        for (;;) {
            $t = $this->peek();
            $entry = self::infixEntry($t);
            if ($entry === null) {
                return $left;
            }
            [$bp, $assoc] = $entry;
            if ($bp < $minBp) {
                return $left;
            }

            $this->next();

            if ($assoc === 'R') {
                // Assignment. The target is validated against the AST shape, not
                // against a value, which is what makes `(A) = 1` a compile error.
                // Parsing the right side at $bp rather than $bp + 1 is what makes
                // it right associative.
                self::checkTarget($left, $t);
                // Counted, for the same reason parsePrefix counts: the right side
                // recurses without passing through parseSequence or parsePrimary,
                // so uncounted a chain of assignments is bounded by nothing but
                // the host's own stack.
                $this->enter($t);
                try {
                    $value = $this->parseTerm($bp);
                    $left = [
                        't' => 'assign', 'op' => $t['value'], 'target' => $left,
                        'value' => $value, 'pos' => $left['pos'],
                    ];
                } finally {
                    $this->leave();
                }
                continue;
            }

            if ($assoc === 'N') {
                $right = $this->parseTerm($bp + 1);
                $after = $this->peek();
                $afterEntry = self::infixEntry($after);
                if ($afterEntry !== null && $afterEntry[1] === 'N') {
                    fail(
                        'E_SYNTAX',
                        "comparison operators do not chain — parenthesise, as in (a {$t['value']} b) AND (b {$after['value']} c)",
                        $after,
                    );
                }
                $left = ['t' => 'bin', 'op' => $t['value'], 'l' => $left, 'r' => $right, 'pos' => $t];
                continue;
            }

            $right = $this->parseTerm($bp + 1);
            $left = ['t' => 'bin', 'op' => $t['value'], 'l' => $left, 'r' => $right, 'pos' => $t];
        }
    }

    /**
     * NOT and unary minus.
     *
     * Each is accepted only where its own binding power reaches: NOT at 7 cannot
     * appear inside a comparison operand (parsed at 9), so `a == NOT b` falls
     * through to parsePrimary, which sees the bare identifier NOT and raises
     * E_RESERVED — the same error the transcribed parser gave, by a different
     * route. Folding these into parsePrimary, which is where textbook precedence
     * climbing puts prefix operators, would make `NOT a == b` parse as
     * `(NOT a) == b` and would break the depth pins at the same time.
     *
     * Counted, and only when actually consumed: a prefix operator recurses
     * without passing through parseSequence or parsePrimary, and uncounted it
     * reached the host’s own stack limit instead of E_DEPTH — that was a
     * segfault in the C++ host, from a rule that is just `-` repeated.
     *
     * @return array<string,mixed>
     */
    private function parsePrefix(int $minBp): array
    {
        $t = $this->peek();

        if ($t['type'] === 'ident' && $t['value'] === 'NOT' && $minBp <= self::BP_NOT) {
            $this->next();
            $this->enter($t);
            try {
                return ['t' => 'un', 'op' => 'NOT', 'x' => $this->parseTerm(self::BP_NOT), 'pos' => $t];
            } finally {
                $this->leave();
            }
        }

        if ($t['type'] === 'op' && $t['value'] === '-' && $minBp <= self::BP_NEG) {
            $this->next();
            $this->enter($t);
            try {
                return ['t' => 'un', 'op' => 'NEG', 'x' => $this->parseTerm(self::BP_NEG), 'pos' => $t];
            } finally {
                $this->leave();
            }
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
                return ['t' => 'num', 'v' => Dec::format(Dec::parse($t['value'], $t)), 'pos' => $t];
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
