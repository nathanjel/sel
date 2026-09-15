<?php
// Relational Plan IR for statement compilation.
//
// Represents the structured relational query before emitting dialect SQL.
// Captures sources, projections, filter predicates, orderings, and pagination.

declare(strict_types=1);

namespace Sel\Sql;

final class JoinPlan
{
    /** Public name used by the portable plan contract. */
    public string $kind = 'INNER';
    public string $type = 'INNER';
    public string $sourceName = '';
    /** @var array<string,mixed> */
    public array $sourceRelation = [];
    /** @var string|array{raw:string} */
    public string|array $sourceTable = '';
    public ?string $sourceAlias = null;
    public string $leftBinder = '_1';
    public string $rightBinder = '_2';
    /** @var array<string,mixed>|null */
    public ?array $onPred = null;
    /** @var array{line:int,col:int,offset:int}|null */
    public ?array $pos = null;

    public function getKind(): string
    {
        return $this->kind;
    }

    public function setKind(string $value): void
    {
        $this->kind = $value;
        $this->type = $value;
    }
}

final class RelationalPlan
{
    public string $sourceName = '';
    /** @var array<string,mixed> */
    public array $sourceRelation = [];
    /** @var string|array{raw:string} */
    public string|array $sourceTable = '';
    public ?string $sourceAlias = null;
    public ?RelationalPlan $sourceSubquery = null;
    public ?string $correlate = null;
    /** @var list<JoinPlan> */
    public array $joins = [];
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
     * Group by expressions from BUCKET(...).
     * @var list<array{alias: ?string, binder: string, node: array<string,mixed>, pos: array{line:int,col:int,offset:int}}>|null
     */
    public ?array $groupBy = null;

    /**
     * A BUCKET without a projection leaves the plan 'open': its SQL rows are
     * the group keys, which is not what SEL's buckets are (a map of member
     * rows), so the next MAP is folded into the bucket as its projection --
     * the one SQL shape a bucket has. Any other step first turns it 'sealed':
     * the members are gone for good, and a MAP after that is refused rather
     * than evaluated over rows SEL would have called groups.
     */
    public ?string $bucket = null;

    /**
     * Whether the grouping was written as a bare BUCKET (with or without the
     * MAP that closes it). A bare bucket's key is an index key: SEL refuses a
     * boolean, binary, list or record key, so the translator must too.
     */
    public bool $bareKey = false;

    /**
     * Post-group filter predicates (HAVING) from FILTER(...) after BUCKET.
     * @var list<array{binder: string, node: array<string,mixed>, pos: array{line:int,col:int,offset:int}}>
     */
    public array $having = [];

    /**
     * Order items from SORT / SORT_DESC / SORT_BY.
     * @var list<array{binder: string, node: array<string,mixed>, dir: string, pos: array{line:int,col:int,offset:int}}>
     */
    public array $orderBy = [];

    public ?int $limit = null;
    public ?int $offset = null;
}
