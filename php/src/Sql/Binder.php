<?php
// What an aggregate binder names for the duration of one element.
//
// Three shapes, matching the three iteration shapes of docs/SQL-TRANSLATION.md
// §7, plus one that exists only to carry a refusal — so that `_K` inside a
// relation body fails saying rows have no key, rather than falling through to
// the bindings map and being reported as an unbound variable.

declare(strict_types=1);

namespace Sel\Sql;

final class Binder
{
    /** An element of a static list: an AST node, re-entered by the walk. */
    public const NODE = 'node';
    /** One column reference, from a `columns` binding. */
    public const COLUMN = 'column';
    /** A row of a relation: fields resolve to that relation's columns. */
    public const ROW = 'row';
    /** In scope, but using it is an error with this reason. */
    public const NONE = 'none';
    /** The key of the group being rendered: a group-by entry and the row binder, rendered as the GROUP BY expression itself. */
    public const KEY = 'key';
    /** A bucket's members, inside the bucket's own body: a list of rows that only COUNT and SUM can read. */
    public const GROUP = 'group';
    /** The record a bucket's projection built, after it: its fields are the projection's aliases and nothing else. */
    public const PROJECTED = 'projected';

    public string $shape;
    /** @var array<string,mixed>|null */
    public ?array $payload;
    public ?string $reason;

    /** @param array<string,mixed>|null $payload */
    private function __construct(string $shape, ?array $payload, ?string $reason = null)
    {
        $this->shape = $shape;
        $this->payload = $payload;
        $this->reason = $reason;
    }

    /** @param array<string,mixed> $node */
    public static function node(array $node): self
    {
        return new self(self::NODE, $node);
    }

    /** @param array<string,mixed> $column */
    public static function column(array $column): self
    {
        return new self(self::COLUMN, $column);
    }

    /** @param array<string,mixed> $relation */
    public static function row(array $relation): self
    {
        return new self(self::ROW, $relation);
    }

    public static function none(string $reason): self
    {
        return new self(self::NONE, null, $reason);
    }

    /** @param array<string,mixed> $group */
    public static function key(array $group, Binder $row): self
    {
        return new self(self::KEY, ['group' => $group, 'row' => $row]);
    }

    /** @param array<string,mixed> $relation */
    public static function group(array $relation): self
    {
        return new self(self::GROUP, $relation);
    }

    /** @param array<string,mixed> $relation @param list<array<string,mixed>> $projections */
    public static function projected(array $relation, array $projections): self
    {
        return new self(self::PROJECTED, ['relation' => $relation, 'projections' => $projections]);
    }
}
