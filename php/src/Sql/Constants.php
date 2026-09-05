<?php
// Does SEL itself accept this expression?
//
// The translator's job is to answer "can this rule be pushed into that
// database". It was answering that question without ever asking a prior one:
// is the rule *valid*. `LEFT("abc", -1)` translates cleanly into every dialect
// here, and SEL raises E_RANGE for it while MariaDB answers '', PostgreSQL
// answers 'ab' and SQLite answers 'abc'. Three databases, three answers, none
// of them SEL's — from a translation that reported success.
//
// That is the core promise inverted. So where an expression's arguments are all
// literals, the values SEL would reject are known *here*, and this asks SEL.
//
// It is validation, not constant folding. The value is computed and thrown
// away; the translation that follows is byte-identical to the one that would
// have been emitted without this check. Folding would have been the tempting
// second step and would have blinded the oracle: an expression replaced by its
// answer no longer exercises the database's version of the operation, which is
// the only thing sql/oracle/ exists to compare.
//
// What it cannot do is check a value it does not have. `LEFT(col, -1)` is
// exactly as wrong and passes, because `col` is a column and its value is not
// knowable at translation time. Argument validation happens where SEL's does —
// at the value — and this reaches the subset of values that are written down.
// See docs/SQL-TRANSLATION.md §11.4.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Context;
use Sel\Evaluator;
use Sel\SelError;

final class Constants
{
    /**
     * Whether every leaf under $n is a literal.
     *
     * A binder an aggregate introduces inside $n counts as bound, so
     * `ALL((1, 2), _ > 0)` is constant and `ALL(ITEMS, _ > 0)` is not. That is
     * the same rule the evaluator applies, which is what lets the whole node be
     * handed to it below.
     *
     * @param array<string,mixed> $n
     * @param array<string,bool>  $bound binder names in scope, by name
     */
    public static function isConstant(array $n, array $bound = []): bool
    {
        switch ($n['t']) {
            case 'num':
            case 'text':
            case 'bool':
                return true;

            case 'var':
                return isset($bound[$n['name']]);

            case 'un':
                return self::isConstant($n['x'], $bound);

            case 'bin':
                return self::isConstant($n['l'], $bound)
                    && self::isConstant($n['r'], $bound);

            case 'index':
                return self::isConstant($n['obj'], $bound)
                    && self::isConstant($n['idx'], $bound);

            case 'clist':
                // Stage 1 builds this one; the evaluator has never seen it and
                // cannot evaluate it. Nothing containing one is checkable.
                return false;

            case 'list':
                foreach ($n['items'] as $item) {
                    if (!self::isConstant($item, $bound)) {
                        return false;
                    }
                }
                return true;

            case 'call':
                return self::constantCall($n, $bound);
        }

        // assign and seq are gone by now (stage 1), and an unknown node type is
        // not something to guess about: not constant, so nothing is validated
        // and the walk refuses it in the ordinary way.
        return false;
    }

    /**
     * The binding form is the only reason this is not three lines.
     *
     * `MAP(list, X, X + 1)` names its binder in argument 1 and uses it in
     * argument 2; the two-argument form binds `_` implicitly. Neither name is a
     * free variable, so neither disqualifies the call — but the *source* still
     * has to be constant, or the body has nothing to iterate.
     *
     * @param array<string,mixed> $n
     * @param array<string,bool>  $bound
     */
    private static function constantCall(array $n, array $bound): bool
    {
        $args = $n['args'];
        if (!($n['spec']['binds'] ?? false)) {
            foreach ($args as $a) {
                if (!self::isConstant($a, $bound)) {
                    return false;
                }
            }
            return true;
        }

        if (!self::isConstant($args[0], $bound)) {
            return false;
        }
        $inner = $bound;
        $body = 1;
        if (count($args) >= 3) {
            if (($args[1]['t'] ?? null) !== 'var') {
                return false;          // malformed; let the evaluator say so
            }
            $inner[$args[1]['name']] = true;
            $body = 2;
        } else {
            $inner['_'] = true;
        }
        for ($i = $body; $i < count($args); $i++) {
            if (!self::isConstant($args[$i], $inner)) {
                return false;
            }
        }
        return true;
    }

    /**
     * Evaluate $n the way SEL would, and refuse the translation if SEL refuses
     * the expression.
     *
     * Called *after* the node has been translated, not before, so that every
     * refusal the translator already had keeps its own message. `TRUE + 1` is
     * an expression SEL rejects and also a BOOL where a number is required;
     * the second is the more useful sentence and is the one an author reading
     * it can act on, and it is the same sentence `FLAG + 1` gets, where no
     * value is known and only the kind check can fire. This check adds
     * refusals where translation used to *succeed* — which is the whole of the
     * defect it exists for — and changes none of the ones that already
     * existed. ABORT and an unportable regex are refused by name on the way
     * past and never reach here.
     *
     * The position reported is SEL's own — the innermost node that failed, not
     * the outermost one this was called with — because that is the character
     * the author has to change.
     *
     * @param array<string,mixed> $n
     */
    public static function validate(array $n): void
    {
        try {
            Evaluator::evalNode($n, new Context());
        } catch (SelError $e) {
            refuse('E_SQL_INVALID',
                "SEL rejects this expression ({$e->code}: {$e->getMessage()}), so "
                . 'there is nothing to translate; a database would answer '
                . 'something rather than fail',
                $e->line > 0
                    ? ['line' => $e->line, 'col' => $e->col, 'offset' => $e->offset]
                    : $n['pos']);
        }
    }
}
