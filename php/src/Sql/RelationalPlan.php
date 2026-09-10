<?php
// Relational Plan IR for statement compilation.
//
// Represents the structured relational query before emitting dialect SQL.
// Captures sources, projections, filter predicates, orderings, and pagination.

declare(strict_types=1);

namespace Sel\Sql;

final class RelationalPlan
{
    public string $sourceName = '';
    /** @var array<string,mixed> */
    public array $sourceRelation = [];
    /** @var string|array{raw:string} */
    public string|array $sourceTable = '';
    public ?string $sourceAlias = null;
    public ?string $correlate = null;
    public bool $distinct = false;

    /**
     * Column names to project from SELECT_COLS.
     * @var list<string>|null
     */
    public ?array $selectCols = null;

    /**
     * Named projections from MAP(RECORD(...)).
     * @var list<array{alias: string, binder: string, node: array<string,mixed>}>|null
     */
    public ?array $projections = null;

    /**
     * Filter predicates from FILTER(...).
     * @var list<array{binder: string, node: array<string,mixed>, pos: array{line:int,col:int,offset:int}}>
     */
    public array $filters = [];

    /**
     * Order items from SORT / SORT_DESC / SORT_BY.
     * @var list<array{binder: string, node: array<string,mixed>, dir: string, pos: array{line:int,col:int,offset:int}}>
     */
    public array $orderBy = [];

    public ?int $limit = null;
    public ?int $offset = null;
}
