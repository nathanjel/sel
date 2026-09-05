<?php
// Stage 1: turn a small, well-behaved class of SEL programs into one
// expression, and refuse the rest.
//
// SEL is an expression language, but it has assignment and `;`, and SQL has
// neither. This is the restrictive step: a helper variable is inlined as the
// expression it held, and anything that cannot be is refused with a position.

declare(strict_types=1);

namespace Sel\Sql;

final class Normalise
{
    /**
     * @param array<string,mixed> $ast
     * @return array<string,mixed>
     */
    /**
     * @param array<string,mixed> $ast
     * @param array<string,bool>  $constNames value-binding names, Constants::scope
     * @return array<string,mixed>
     */
    public static function run(array $ast, array $constNames = [],
                               ?\Sel\Context $ctx = null): array
    {
        $stmts = $ast['t'] === 'seq' ? $ast['items'] : [$ast];
        $result = array_pop($stmts);
        $defs = [];                                  // NAME => node

        foreach ($stmts as $s) {
            self::record($s, $defs, $constNames, $ctx);
        }
        return self::substitute($result, $defs, []);
    }

    /**
     * Fold one leading statement into $defs, or refuse it.
     *
     * @param array<string,mixed> $s
     * @param array<string, array<string,mixed>> $defs
     * @param array<string,bool> $constNames
     */
    private static function record(array $s, array &$defs, array $constNames = [],
                                   ?\Sel\Context $ctx = null): void
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

        $value = self::substitute($s['value'], $defs, []);

        // Validated here, and only here, because after this the subtree may be
        // gone: a definition nothing reads is dropped, so `A = 1 / 0; TRUE`
        // translated to `TRUE` and every server answered TRUE where SEL raises
        // E_DIV_ZERO. An indexed assignment builds a `clist`, which the
        // constant test refuses to walk and COUNT/HAS never render, so
        // `R[1] = 1 / 0; COUNT(R)` was `1`. §11.4's fourth bullet says a
        // constant subtree is checked wherever it appears; these were the two
        // places it did not appear by the time anything looked.
        if (Constants::isConstant($value, $constNames)) {
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
    private static function substitute(array $node, array $defs, array $bound): array
    {
        switch ($node['t']) {
            case 'var':
                if (in_array($node['name'], $bound, true)) {
                    return $node;
                }
                return $defs[$node['name']] ?? $node;

            case 'num':
            case 'text':
            case 'bool':
                return $node;

            case 'assign':
                refuse('E_SQL_ASSIGN',
                    'an assignment here would have to happen while the query runs, '
                    . 'and a SQL expression cannot assign', $node['pos']);
                // no break — refuse() never returns

            case 'seq':
                refuse('E_SQL_ASSIGN',
                    'a sequence here would evaluate and discard a value, which a '
                    . 'SQL expression cannot do', $node['pos']);
                // no break

            case 'un':
                $node['x'] = self::substitute($node['x'], $defs, $bound);
                return $node;

            case 'bin':
                $node['l'] = self::substitute($node['l'], $defs, $bound);
                $node['r'] = self::substitute($node['r'], $defs, $bound);
                return $node;

            case 'index':
                $node['obj'] = self::substitute($node['obj'], $defs, $bound);
                $node['idx'] = self::substitute($node['idx'], $defs, $bound);
                return $node;

            case 'list':
                $node['items'] = self::flatten($node['items'], $defs, $bound);
                return $node;

            case 'clist':
                foreach ($node['entries'] as $i => [$k, $v]) {
                    $node['entries'][$i] = [$k, self::substitute($v, $defs, $bound)];
                }
                return $node;

            case 'call':
                $inner = $bound;
                if (!empty($node['spec']['binds'])) {
                    $n = count($node['args']);
                    $inner[] = '_K';
                    $inner[] = $n === 3 && $node['args'][1]['t'] === 'var'
                        ? $node['args'][1]['name'] : '_';
                }
                foreach ($node['args'] as $i => $arg) {
                    // An aggregate's binder argument is a name, not a read of one.
                    if (!empty($node['spec']['binds']) && $i === 1
                        && count($node['args']) === 3 && $arg['t'] === 'var') {
                        continue;
                    }
                    $node['args'][$i] = self::substitute($arg, $defs, $i === 0 ? $bound : $inner);
                }
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
     * What `clist` protects is not this. It is that rule 5 in the class comment
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
    private static function flatten(array $items, array $defs, array $bound): array
    {
        $out = [];
        foreach ($items as $item) {
            $s = self::substitute($item, $defs, $bound);
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
