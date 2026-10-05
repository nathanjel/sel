<?php
// The shape of a parser node, for the walks over it: which keys hold child
// nodes. Every walk in the core (the optimizer's, the dependency and
// side-effect classifiers, the join planner's rewrites, the parser's
// dismantling) takes its child keys from here, so a new node field is added
// in one place. A walk that deliberately skips a child says so by its
// arguments: an assignment's `target` is walked iteratively by the
// evaluator, so the depth walks pass $target = false.

declare(strict_types=1);

namespace Sel;

final class Ast
{
    /** The keys holding a list of child nodes. */
    public const CHILD_LISTS = ['args', 'items'];
    /** The keys holding one child node. `pos` holds a token, `spec` a function entry, `v`/`op`/`name` scalars. */
    public const CHILD_NODES = ['l', 'r', 'x', 'obj', 'idx', 'target', 'value'];

    /**
     * The direct children of $node, lists first, then the single children in
     * CHILD_NODES order.
     *
     * @param array<string,mixed> $node
     * @return list<array<string,mixed>>
     */
    public static function children(array $node, bool $target = true): array
    {
        $out = [];
        foreach (self::CHILD_LISTS as $key) {
            foreach ($node[$key] ?? [] as $child) {
                if (is_array($child)) $out[] = $child;
            }
        }
        foreach (self::CHILD_NODES as $key) {
            if (isset($node[$key]) && is_array($node[$key]) && ($target || $key !== 'target')) $out[] = $node[$key];
        }
        return $out;
    }

    /**
     * $node with every child replaced by $f(child): a rebuilt node, the
     * original untouched.
     *
     * @param array<string,mixed> $node
     * @param callable(array<string,mixed>):(array<string,mixed>|null) $f
     * @return array<string,mixed>
     */
    public static function mapChildren(array $node, callable $f): array
    {
        foreach (self::CHILD_LISTS as $key) {
            if (isset($node[$key])) $node[$key] = array_map($f, $node[$key]);
        }
        foreach (self::CHILD_NODES as $key) {
            if (isset($node[$key]) && is_array($node[$key])) $node[$key] = $f($node[$key]);
        }
        return $node;
    }
}
