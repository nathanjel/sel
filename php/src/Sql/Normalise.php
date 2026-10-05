<?php
// Stage 1: turn a small, well-behaved class of SEL programs into one
// expression, and refuse the rest.
//
// SEL is an expression language, but it has assignment and `;`, and SQL has
// neither. This is the restrictive step: a helper variable is inlined as the
// expression it held, and anything that cannot be is refused with a position.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Context;
use Sel\Limits;
use Sel\Registry;

final class Normalise
{
    /**
     * What a subtree costs once every inlined helper read is expanded, counted as
     * the walk goes (sql/errors.md E_SQL_SIZE). A helper read is the helper's whole
     * expression again, so `A1 = A0 + A0; A2 = A1 + A1; ...` is linear to evaluate
     * and 2^n to render -- and to WALK, so it has to be counted here, before
     * anything (the constant test, the validator) visits the expansion. PHP arrays
     * share the subtree, which is why stage 1 itself stays cheap.
     */
    private static int $size = 0;
    /** Whether an over-limit count refuses now (the result) or is only recorded (a definition). */
    private static bool $refusing = true;
    /** @var array<string,int> expanded size of each definition, by name */
    private static array $defSizes = [];
    /** @var array<string,bool> whether each definition is a constant expression, by name */
    private static array $defConst = [];

    private static function charge(int $nodes): void
    {
        // Saturating: a chain of a hundred doublings must not overflow the counter.
        self::$size = min(self::$size + $nodes, 1 << 40);
        if (self::$refusing && self::$size > Limits::MAX_SQL_NODES) {
            refuse('E_SQL_SIZE',
                'the expression this rule would translate to has more than '
                . Limits::MAX_SQL_NODES . ' nodes once its helpers are expanded, '
                . 'though SEL evaluates it in linear time; it is evaluated the ordinary way');
        }
    }

    /**
     * @param array<string,mixed> $ast
     * @param array<string,bool>  $constNames value-binding names, Constants::scope
     * @return array<string,mixed>
     */
    public static function run(array $ast, array $constNames = [],
                               ?Context $ctx = null): array
    {
        $stmts = $ast['t'] === 'seq' ? $ast['items'] : [$ast];
        $result = array_pop($stmts);
        $defs = [];                                  // NAME => node

        // Counting starts where the evaluator's count would stand: the `;`
        // sequence costs a level and each assignment it inlines one more
        // (spec §6.4), so `X = <199 terms>; X` -- E_DEPTH in the evaluator --
        // is refused here too, although the chain alone would translate.
        // Stage 1 removes both wrappers before either guard looks, which is
        // why they must be charged up front.
        $base = $ast['t'] === 'seq' ? 1 : 0;
        self::$defSizes = [];
        self::$defConst = [];
        foreach ($stmts as $s) {
            self::record($s, $defs, $constNames, $ctx, $base + 1);
        }
        // Only what is READ is translated: a definition nothing reads is dropped, so
        // its own size is not the rule's. The result's count is the rule's.
        self::$size = 0;
        self::$refusing = true;
        return self::substitute($result, $defs, [], $base);
    }

    /**
     * Fold one leading statement into $defs, or refuse it. $depth is where the
     * evaluator's count stands at the assignment's right-hand side.
     *
     * @param array<string,mixed> $s
     * @param array<string, array<string,mixed>> $defs
     * @param array<string,bool> $constNames
     */
    private static function record(array $s, array &$defs, array $constNames,
                                   ?Context $ctx, int $depth): void
    {
        if ($s['t'] !== 'assign') {
            refuse('E_SQL_ASSIGN',
                'only assignments may come before the result expression; this '
                . 'computes a value nothing reads, which SQL has nowhere to put',
                $s['pos']);
        }
        if ($s['op'] !== '=') {
            refuse('E_SQL_ASSIGN',
                "{$s['op']} reads its own target before writing it, and SQL has "
                . 'nowhere to put the write; use = and a fresh name', $s['pos']);
        }

        // The target is a bare name, or a name indexed by constant keys.
        $keys = [];
        $t = $s['target'];
        while ($t['t'] === 'index') {
            $k = self::constantKey($t['idx']);
            if ($k === null) {
                refuse('E_SQL_ASSIGN',
                    'an assignment target may only be indexed by a constant here, '
                    . 'because the shape has to be known before the query runs',
                    $t['idx']['pos']);
            }
            array_unshift($keys, $k);
            $t = $t['obj'];
        }
        if ($t['t'] !== 'var') {
            refuse('E_SQL_ASSIGN', 'assignment target is not a variable', $s['pos']);
        }
        $name = $t['name'];

        // A definition's expanded size is recorded, not refused: it only matters if
        // something reads it (the result's count refuses then), and the constant test
        // below must not walk an expansion past the limit.
        self::$size = 0;
        self::$refusing = false;
        $value = self::substitute($s['value'], $defs, [], $depth);
        self::$refusing = true;
        $valueSize = self::$size;

        // Validated here, and only here, because after this the subtree may be
        // gone: a definition nothing reads is dropped, so `A = 1 / 0; TRUE`
        // translated to `TRUE` and every server answered TRUE where SEL raises
        // E_DIV_ZERO. An indexed assignment builds a `clist`, which the
        // constant test refuses to walk and COUNT/HAS never render, so
        // `R[1] = 1 / 0; COUNT(R)` was `1`. §11.4's fourth bullet says a
        // constant subtree is checked wherever it appears; these were the two
        // places it did not appear by the time anything looked.
        $isConstant = Constants::isConstant($value, $constNames);
        if ($valueSize <= Limits::MAX_SQL_NODES && $isConstant) {
            Constants::validate($value, $ctx);
        }

        if ($keys === []) {
            if (isset($defs[$name])) {
                refuse('E_SQL_ASSIGN',
                    "{$name} is assigned more than once; SQL has no notion of a "
                    . 'variable changing, so each name may be written once',
                    $s['pos']);
            }
            $defs[$name] = $value;
            self::$defSizes[$name] = $valueSize;
            self::$defConst[$name] = $isConstant;
            return;
        }

        // Indexed assignment builds a `clist`: a keyed list node, distinct from
        // `list` because it must NOT be flattened. Assignment stores a list as a
        // child rather than contributing its children (spec §5.9 applies to `,`
        // and not to `=`), so `R[1] = (1, 2); R[2] = (3, 4)` is two pairs and
        // not four scalars — and a `clist` is the only node that can say so. It
        // carries the real keys too, so R["a"] = 1 gives _K of "a".
        if (count($keys) > 1) {
            refuse('E_SQL_ASSIGN',
                'only one level of indexed assignment can be folded into a list here',
                $s['pos']);
        }
        $key = $keys[0];
        if (!isset($defs[$name])) {
            $defs[$name] = ['t' => 'clist', 'entries' => [], 'pos' => $s['pos']];
        }
        if ($defs[$name]['t'] !== 'clist') {
            refuse('E_SQL_ASSIGN',
                "{$name} is assigned both as a whole and by index; use one or the other",
                $s['pos']);
        }
        foreach ($defs[$name]['entries'] as [$existing]) {
            if ($existing === $key) {
                refuse('E_SQL_ASSIGN',
                    "{$name}[{$key}] is assigned more than once", $s['pos']);
            }
        }
        $defs[$name]['entries'][] = [$key, $value];
        self::$defSizes[$name] = (self::$defSizes[$name] ?? 1) + $valueSize;
    }

    /** The literal key an index expression names, or null when it is not one. */
    private static function constantKey(array $idx): ?string
    {
        return match ($idx['t']) {
            'num' => (string) $idx['v'],
            'text' => (string) $idx['v'],
            default => null,
        };
    }

    /**
     * A call's arguments, each passed through `$visit` with the names bound
     * where it runs. Which arguments a binding call runs inside the binder, and
     * what they see (its binders and `_K`), is the manifest's decision
     * (Registry::bindingForm), shared with dependencies(). A binder argument is a
     * name, not a read of one, and stays as written. The one scoping rule for
     * every walk that substitutes by name: this stage and the hybrid planner's
     * literal helpers.
     *
     * @param array<string,mixed> $node a call
     * @param list<string> $bound the names bound around the call
     * @param callable(array<string,mixed>, list<string>): array<string,mixed> $visit
     * @return list<array<string,mixed>>
     */
    public static function scopedArgs(array $node, array $bound, callable $visit): array
    {
        $form = Registry::bindingForm($node['name'], $node['args']);
        $inner = $form === null ? $bound : array_merge($bound, $form['binds']);
        $args = $node['args'];
        foreach ($args as $i => $arg) {
            $scope = $form === null ? 'outer' : $form['scopes'][$i];
            if ($scope !== 'binder') {
                $args[$i] = $visit($arg, $scope === 'inner' ? $inner : $bound);
            }
        }
        return $args;
    }

    /**
     * Replace every read of a defined name with the node it was assigned.
     *
     * The substituted subtree keeps its original `pos`, so an error inside an
     * inlined expression still points at where the author wrote it rather than
     * at the place it was used.
     *
     * @param array<string,mixed> $node
     * @param array<string, array<string,mixed>> $defs
     * @param list<string> $bound aggregate binders, which shadow a definition
     * @return array<string,mixed>
     */
    private static function substitute(array $node, array $defs, array $bound,
                                       int $depth = 0): array
    {
        // Stage 1 walks the tree before the translator's own guard can, so the
        // bound belongs here too and at the same constant. Without it the
        // deepest expression this layer accepts was decided by the host: PHP
        // recursed as far as it liked and Python died of its own stack at around
        // 510 terms, which is an implementation accident rather than a decision.
        if (++$depth > Limits::MAX_DEPTH) {
            refuse('E_SQL_DEPTH',
                'this expression nests deeper than SEL will evaluate ('
                . Limits::MAX_DEPTH . '), so there is nothing to translate; '
                . 'the evaluator answers E_DEPTH for it', $node['pos']);
        }
        self::charge(1);
        switch ($node['t']) {
            case 'var':
                if (in_array($node['name'], $bound, true)) {
                    return $node;
                }
                if (isset($defs[$node['name']])) {
                    // The node counted above is replaced by the whole definition.
                    self::charge(max(0, (self::$defSizes[$node['name']] ?? 1) - 1));
                    // Marked as inlined: the definition was written at the top of the
                    // rule, outside every binder, so its free names must not be
                    // captured by a binder of the same name where it is used.
                    $inlined = $defs[$node['name']];
                    $inlined['inl'] = true;
                    if (isset(self::$defConst[$node['name']])) {
                        $inlined['k'] = self::$defConst[$node['name']];
                    }
                    return $inlined;
                }
                return $node;

            case 'num':
            case 'text':
            case 'bool':
                return $node;

            case 'assign':
                refuse('E_SQL_ASSIGN',
                    'an assignment here would have to happen while the query runs, '
                    . 'and a SQL expression cannot assign', $node['pos']);

            case 'seq':
                refuse('E_SQL_ASSIGN',
                    'a sequence here would evaluate and discard a value, which a '
                    . 'SQL expression cannot do', $node['pos']);

            case 'un':
                $node['x'] = self::substitute($node['x'], $defs, $bound, $depth);
                return $node;

            case 'bin':
                $node['l'] = self::substitute($node['l'], $defs, $bound, $depth);
                $node['r'] = self::substitute($node['r'], $defs, $bound, $depth);
                return $node;

            case 'index':
                $node['obj'] = self::substitute($node['obj'], $defs, $bound, $depth);
                $node['idx'] = self::substitute($node['idx'], $defs, $bound, $depth);
                return $node;

            case 'list':
                $node['items'] = self::flatten($node['items'], $defs, $bound, $depth);
                return $node;

            case 'clist':
                foreach ($node['entries'] as $i => [$k, $v]) {
                    $node['entries'][$i] = [$k, self::substitute($v, $defs, $bound, $depth)];
                }
                return $node;

            case 'call':
                $node['args'] = self::scopedArgs($node, $bound,
                    static fn (array $arg, array $sees): array => self::substitute($arg, $defs, $sees, $depth));
                return $node;

            default:
                return $node;
        }
    }

    /**
     * Build a `,` list, flattening per spec §5.9: an operand with children and
     * no scalar of its own contributes each of its children, and the keys are
     * renumbered from 1.
     *
     * Both node kinds contribute, and both contribute exactly one level.
     * Verified against the evaluator, which is the arbiter here:
     *
     *     ((1, 2), 3)                       -> three scalars
     *     R[1] = (1, 2); R[2] = (3, 4); (R, 5)
     *                                       -> (1,2), (3,4), 5 — three elements,
     *                                          two of which are still lists
     *
     * Parenthesising changes nothing: the `grouped` flag decides whether a call
     * sees one argument or several, not whether `,` flattens.
     *
     * What `clist` protects is not this. It is that indexed assignment
     * builds nesting out of `R[1] = …; R[2] = …`, and a `list` node would have
     * been renumbered flat by the time an aggregate iterated it. A `clist`
     * reaches an aggregate through a bare variable reference, never through a
     * `,`, so the two rules never meet.
     *
     * @param list<array<string,mixed>> $items
     * @param array<string, array<string,mixed>> $defs
     * @param list<string> $bound
     * @return list<array<string,mixed>>
     */
    private static function flatten(array $items, array $defs, array $bound,
                                    int $depth = 0): array
    {
        $out = [];
        foreach ($items as $item) {
            $s = self::substitute($item, $defs, $bound, $depth);
            if ($s['t'] === 'list') {
                foreach ($s['items'] as $child) {
                    $out[] = $child;
                }
                continue;
            }
            if ($s['t'] === 'clist') {
                foreach ($s['entries'] as [, $v]) {
                    $out[] = $v;
                }
                continue;
            }
            $out[] = $s;
        }
        return $out;
    }
}
