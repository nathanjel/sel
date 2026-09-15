<?php
// SQL-prefix planning with an in-memory SEL continuation.
//
// The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at the
// tree the translator will see, `sourceTables` names PHYSICAL sources (a
// relation's `from`, or a relation query's text verbatim), a program stage 1
// refuses is a pure-memory plan rather than an exception, and `$options` is
// one array that reaches both the logical optimiser and the translator.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Optimizer;
use Sel\Program;
use Sel\Value;

final class HybridPlan
{
    public ?string $dialect;
    public ?Fragment $sqlStatement;
    /** @var array<string,mixed>|null */
    public ?array $sqlPrefixAst;
    /** @var array<string,mixed>|null */
    public ?array $continuationAst;
    public ?Program $continuationProgram;
    public string $continuationSourceVar;
    public bool $pureSql;
    public bool $pureMemory;
    /** @var list<string> */
    public array $sourceTables;

    // Snake-case aliases mirror the cross-host planner contract. They are
    // values, rather than magic accessors, so a caller can serialize a plan
    // without knowing which host produced it.
    public ?Fragment $sql_query;
    /** @var array<string,mixed>|null */
    public ?array $sql_prefix_ast;
    /** @var array<string,mixed>|null */
    public ?array $continuation_ast;
    public ?Program $continuation_program;
    public string $continuation_source_var;
    public bool $pure_sql;
    public bool $pure_memory;
    public bool $is_hybrid;
    /** @var list<string> */
    public array $source_tables;

    /** @param array<string,mixed> $spec */
    public function __construct(array $spec = [])
    {
        $this->dialect = $spec['dialect'] ?? null;
        $this->sqlStatement = $spec['sqlStatement'] ?? null;
        $this->sqlPrefixAst = $spec['sqlPrefixAst'] ?? null;
        $this->continuationAst = $spec['continuationAst'] ?? null;
        $this->continuationProgram = $spec['continuationProgram'] ?? null;
        $this->continuationSourceVar = $spec['continuationSourceVar'] ?? '_INPUT';
        $this->pureSql = (bool) ($spec['pureSql'] ?? false);
        $this->pureMemory = (bool) ($spec['pureMemory'] ?? false);
        $this->sourceTables = array_values($spec['sourceTables'] ?? []);
        $this->sql_query = $this->sqlStatement;
        $this->sql_prefix_ast = $this->sqlPrefixAst;
        $this->continuation_ast = $this->continuationAst;
        $this->continuation_program = $this->continuationProgram;
        $this->continuation_source_var = $this->continuationSourceVar;
        $this->pure_sql = $this->pureSql;
        $this->pure_memory = $this->pureMemory;
        $this->is_hybrid = !$this->pureSql && !$this->pureMemory;
        $this->source_tables = $this->sourceTables;
    }

    public function isHybrid(): bool
    {
        return !$this->pureSql && !$this->pureMemory;
    }

    public function is_hybrid(): bool
    {
        return $this->isHybrid();
    }

    public function sql_query(): ?Fragment
    {
        return $this->sqlStatement;
    }

    /** @return list<string> */
    public function source_tables(): array
    {
        return $this->sourceTables;
    }
}

final class Hybrid
{
    /**
     * Build the maximal SQL prefix. A null SQL fragment is an ordinary
     * non-pushdown result; translator bugs and non-SQL exceptions still escape.
     *
     * @param array<string,Binding>|Bindings $bindings
     * @param array<string,mixed> $options
     */
    public static function plan(Program $program, string $dialect,
                                array|Bindings $bindings = [], array $options = []): HybridPlan
    {
        $catalog = $bindings instanceof Bindings ? $bindings : new Bindings($bindings);
        Map::requireTarget($dialect);
        $catalog->checkAliases();
        // Stage 1 first, exactly as the translator runs it, so the tree unwound
        // below is the one a prefix will be translated from. A program stage 1
        // refuses -- `A += 1; ...`, a bare statement before the result -- is a
        // program no part of which can be pushed down, which is a pure-memory
        // plan and not an exception: "none of it" is one of the planner's
        // answers.
        try {
            [$constNames, $constContext] = Constants::scope($catalog);
            $normalized = Normalise::run($program->ast, $constNames, $constContext);
        } catch (SqlError) {
            return self::pureMemoryPlan($program, $dialect, $catalog);
        }
        $optimized = Optimizer::optimize($normalized, false, $options);
        $unwound = Optimizer::unwindPipeline($optimized);
        $source = $unwound['source'];
        $steps = $unwound['steps'];

        if ($steps === [] || ($source['t'] ?? null) !== 'var'
            || !$catalog->has($source['name'])
            || ($catalog->get($source['name'], $source['pos'])['kind'] ?? null) !== 'relation') {
            return self::pureMemoryPlan($program, $dialect, $catalog);
        }

        // The whole pipeline, unless its rows would be a bucket's keys: the
        // translator renders a bare bucket as its keys, and a plan that pushes
        // the whole of `... .> BUCKET(k)` would hand them back as the answer.
        $fullAst = Optimizer::buildPipeline($source, $steps);
        $fullSql = self::bucketRowsAreKeys($steps) ? null : self::tryStatement($fullAst, $dialect, $catalog, $options);
        if ($fullSql !== null) {
            return new HybridPlan([
                'dialect' => $dialect,
                'sqlStatement' => $fullSql,
                'sqlPrefixAst' => $fullAst,
                'pureSql' => true,
                'sourceTables' => self::sourceTables($fullAst, $catalog),
            ]);
        }

        $fallthrough = self::tryPlanFallthrough($source, $steps, $dialect, $catalog, $options);
        if ($fallthrough !== null) return $fallthrough;

        for ($count = count($steps) - 1; $count >= 1; $count--) {
            $prefixSteps = array_slice($steps, 0, $count);
            if (self::bucketRowsAreKeys($prefixSteps)) continue;
            $prefixAst = Optimizer::buildPipeline($source, $prefixSteps);
            $sql = self::tryStatement($prefixAst, $dialect, $catalog, $options);
            if ($sql === null) continue;
            $remaining = array_slice($steps, $count);
            $input = ['t' => 'var', 'name' => '_INPUT', 'pos' => $remaining[0]['pos']];
            $continuationAst = Optimizer::buildPipeline($input, $remaining);
            return new HybridPlan([
                'dialect' => $dialect,
                'sqlStatement' => $sql,
                'sqlPrefixAst' => $prefixAst,
                'continuationAst' => $continuationAst,
                'continuationProgram' => new Program('', $continuationAst),
                'sourceTables' => self::sourceTables($prefixAst, $catalog),
            ]);
        }

        return self::pureMemoryPlan($program, $dialect, $catalog);
    }

    /**
     * The plan for a program nothing of which reaches the database. The
     * continuation is the program itself, and the AST it exposes is the
     * program's own, so a caller sees the same tree whichever way the plan went.
     */
    private static function pureMemoryPlan(Program $program, string $dialect, Bindings $catalog): HybridPlan
    {
        return new HybridPlan([
            'dialect' => $dialect,
            'pureMemory' => true,
            'continuationProgram' => $program,
            'continuationAst' => $program->ast,
            'sourceTables' => self::sourceTables($program->ast, $catalog),
        ]);
    }

    /** @param array<string,mixed> $ast @param array<string,mixed> $options */
    private static function tryStatement(array $ast, string $dialect, Bindings $catalog, array $options): ?Fragment
    {
        try {
            return Sql::translateStatement(new Program('', $ast), $dialect, $catalog, $options);
        } catch (SqlError) {
            return null;
        }
    }

    /**
     * The physical source a relation binding reads: its table, or for a
     * relation query the query text exactly as the application wrote it.
     *
     * @param array<string,mixed> $binding
     */
    private static function physicalSource(array $binding): string
    {
        $from = $binding['from'] ?? null;
        return is_array($from) && array_key_exists('raw', $from) ? (string) $from['raw'] : (string) $from;
    }

    /**
     * Every physical source the tree reads, first use first, each once. Keyed
     * by the physical name, so two bindings over one table are one source.
     *
     * @param array<string,mixed> $ast @return list<string>
     */
    private static function sourceTables(array $ast, Bindings $bindings): array
    {
        $out = [];
        $seen = [];
        $visit = function (?array $node) use (&$visit, &$out, &$seen, $bindings): void {
            if ($node === null) return;
            if (($node['t'] ?? null) === 'var' && $bindings->has($node['name'])) {
                $binding = $bindings->get($node['name'], $node['pos'] ?? null);
                if (($binding['kind'] ?? null) === 'relation') {
                    $table = self::physicalSource($binding);
                    if (!isset($seen[$table])) {
                        $seen[$table] = true;
                        $out[] = $table;
                    }
                }
                return;
            }
            foreach (['args', 'items'] as $key) foreach ($node[$key] ?? [] as $item) $visit($item);
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($node[$key]) && is_array($node[$key])) $visit($node[$key]);
            }
        };
        $visit($ast);
        return $out;
    }

    /**
     * Whether the SQL rows for this step list are a bucket's KEYS rather than
     * the value SEL would have produced. A BUCKET without a projection is open:
     * the translator projects its keys, and SEL's value is a map of member
     * rows. The next MAP closes it -- it becomes the bucket's projection, one
     * statement, one value in both lanes -- and a FILTER between them is a
     * HAVING. Any other step seals it: the members are gone, and no
     * continuation can get them back. So a prefix that is open or sealed is
     * not a split point, whatever the translator says about it, and the MAP
     * fall-through must not fire on a MAP that closes one -- its custom half
     * would be evaluated over key rows.
     *
     * @param list<array<string,mixed>> $steps
     */
    private static function bucketRowsAreKeys(array $steps): bool
    {
        $open = false;
        foreach ($steps as $step) {
            $name = $step['name'] ?? '';
            if ($name === 'BUCKET') {
                if ($open) return true;
                $open = count($step['args']) === 2;
            } elseif ($open && $name === 'MAP') {
                $open = false;
            } elseif ($open && $name !== 'FILTER') {
                return true;
            }
        }
        return $open;
    }

    private static function containsUnsupportedSql(?array $node, string $dialect): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'call') {
            $special = ['IF' => true, 'COND' => true, 'COALESCE' => true, 'COUNT' => true,
                        'SUM' => true, 'AVG' => true, 'MIN' => true, 'MAX' => true,
                        'RECORD' => true, 'LIST' => true];
            if (!isset($special[$node['name']])) {
                $entry = Map::entry($dialect, 'funcs', strtoupper($node['name']));
                if ($entry === Map::MISSING || $entry === null || is_string($entry)) return true;
            }
            foreach ($node['args'] ?? [] as $item) if (self::containsUnsupportedSql($item, $dialect)) return true;
            return false;
        }
        foreach (['args', 'items'] as $key) foreach ($node[$key] ?? [] as $item) {
            if (self::containsUnsupportedSql($item, $dialect)) return true;
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($node[$key]) && is_array($node[$key])
                && self::containsUnsupportedSql($node[$key], $dialect)) return true;
        }
        return false;
    }

    /** @return list<string> */
    // The field names read as `binder["field"]` in `$node`, first seen first
    // and compared exactly: SEL's record keys are case-sensitive, so `name`
    // and `Name` are two fields. A null binder means a read under ANY name
    // counts -- a downstream step binds the row however it likes
    // (`SORT_BY(s, s["name"])`).
    private static function fieldReferences(?array $node, ?string $binder = '_'): array
    {
        $wanted = $binder === null ? null : array_map('strtoupper', [$binder, '_', '_1', '_2']);
        $out = [];
        $seen = [];
        $visit = function (?array $item) use (&$visit, &$out, &$seen, $wanted): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text'
                && ($wanted === null || in_array(strtoupper($item['obj']['name']), $wanted, true))) {
                $key = (string) $item['idx']['v'];
                if (!isset($seen[$key])) {
                    $seen[$key] = true;
                    $out[] = $key;
                }
            }
            foreach (['args', 'items'] as $key) foreach ($item[$key] ?? [] as $child) $visit($child);
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($item[$key]) && is_array($item[$key])) $visit($item[$key]);
            }
        };
        $visit($node);
        return $out;
    }

    // The steps the MAP fall-through may push past the MAP. Each keeps the rows
    // as they are -- the same records, fewer or reordered -- so the custom half
    // of the projection still runs over its own input. A step that changes the
    // row shape (MAP, SELECT_COLS, LINK, BUCKET) would put it over something
    // else, and the whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts)
    // would compare the dependency columns SQL carries where SEL compares the
    // custom values.
    private const FALLTHROUGH_DOWNSTREAM = ['FILTER', 'SORT_BY', 'TOP_BY', 'TAKE', 'DROP'];

    /**
     * Whether `$node` reads the row itself -- the binder outside an index with
     * a text key, as in `GET(_, "name")` or `COUNT(_)` -- which no projected
     * column can stand in for.
     *
     * @param array<string,mixed>|null $node
     */
    private static function readsWholeRow(?array $node, string $binder): bool
    {
        $wanted = array_map('strtoupper', [$binder, '_', '_1', '_2']);
        $visit = function (?array $item) use (&$visit, $wanted): bool {
            if ($item === null) return false;
            if (($item['t'] ?? null) === 'var' && in_array(strtoupper($item['name']), $wanted, true)) return true;
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text') {
                // A field read; the object is not a whole-row read.
                return $visit($item['idx']);
            }
            foreach (['args', 'items'] as $key) foreach ($item[$key] ?? [] as $child) if ($visit($child)) return true;
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($item[$key]) && is_array($item[$key]) && $visit($item[$key])) return true;
            }
            return false;
        };
        return $visit($node);
    }

    /**
     * Whether a pushable pair is the plain field read `binder[key]` of its own
     * key, so that a dependency of the same name may share its column.
     *
     * @param array{key:array<string,mixed>,value:array<string,mixed>} $pair
     */
    private static function isOwnFieldRead(array $pair, string $binder): bool
    {
        $value = $pair['value'];
        return ($value['t'] ?? null) === 'index' && ($value['obj']['t'] ?? null) === 'var'
            && ($value['idx']['t'] ?? null) === 'text'
            && strtoupper($value['obj']['name']) === strtoupper($binder)
            && (string) $value['idx']['v'] === (string) $pair['key']['v'];
    }

    /** @return array{explicit:bool,binder:string,body:array<string,mixed>,pairs:list<array{key:array<string,mixed>,value:array<string,mixed>}>}|null */
    private static function mapRecordDetails(array $step): ?array
    {
        $args = $step['args'];
        $explicit = count($args) === 3 && ($args[1]['t'] ?? null) === 'var'
            && !($args[1]['grouped'] ?? false);
        $body = $explicit ? ($args[2] ?? null) : ($args[1] ?? null);
        if ($body === null || ($body['t'] ?? null) !== 'call'
            || !in_array($body['name'], ['RECORD', 'LAZY_RECORD'], true)
            || count($body['args']) % 2 !== 0) return null;
        $pairs = [];
        for ($i = 0; $i < count($body['args']); $i += 2) {
            if (($body['args'][$i]['t'] ?? null) !== 'text') return null;
            $pairs[] = ['key' => $body['args'][$i], 'value' => $body['args'][$i + 1]];
        }
        return ['explicit' => $explicit, 'binder' => $explicit ? $args[1]['name'] : '_',
                'body' => $body, 'pairs' => $pairs];
    }

    private static function tryPlanFallthrough(array $source, array $steps, string $dialect,
                                                Bindings $catalog, array $options): ?HybridPlan
    {
        $mapIndex = null;
        foreach ($steps as $i => $step) if (($step['name'] ?? '') === 'MAP') {
            $mapIndex = $i;
            break;
        }
        if ($mapIndex === null || self::bucketRowsAreKeys(array_slice($steps, 0, $mapIndex))) return null;
        $details = self::mapRecordDetails($steps[$mapIndex]);
        if ($details === null) return null;
        // Pairs are told apart by their INDEX in the record, never by name: keys
        // that differ only by case are two fields to SEL.
        $pushable = [];
        $custom = [];
        $pushableAt = [];
        foreach ($details['pairs'] as $i => $pair) {
            if (self::containsUnsupportedSql($pair['value'], $dialect)) {
                $custom[] = $pair;
            } else {
                $pushable[] = $pair;
                $pushableAt[$i] = true;
            }
        }
        if ($custom === [] || $pushable === []) return null;
        // The custom half runs over the rows the SQL returns; a read of the row
        // itself cannot be served by any column.
        foreach ($custom as $pair) if (self::readsWholeRow($pair['value'], $details['binder'])) return null;

        // Every step after the MAP goes into the SQL, so each must keep the rows
        // as they are, and may read only what SEL's rows have after the MAP: the
        // pushable keys. The custom keys are not in the SQL; a dependency column
        // is in the SQL but not in SEL's row.
        $downstream = array_slice($steps, $mapIndex + 1);
        foreach ($downstream as $step) if (!in_array($step['name'], self::FALLTHROUGH_DOWNSTREAM, true)) return null;
        $projected = array_map(static fn (array $pair): string => (string) $pair['key']['v'], $pushable);
        foreach ($downstream as $step) {
            // args[0] is the step's input -- the pipeline so far -- not its own
            // text; the step binds the row under a name of its own, so any read
            // counts.
            foreach (array_slice($step['args'], 1) as $arg) {
                foreach (self::fieldReferences($arg, null) as $field) {
                    if (!in_array($field, $projected, true)) return null;
                }
            }
        }

        // A dependency may share a projected column only when that column IS
        // the field: `"customer_id", _["amount"]` projects amount under the name
        // the custom half would read customer_id by. Names are compared exactly,
        // as SEL compares them; and a dependency that differs from a projected
        // key only by case is not projected beside it, because SQL aliases are
        // not case-sensitive everywhere.
        $own = [];
        foreach ($pushable as $pair) {
            if (self::isOwnFieldRead($pair, $details['binder'])) $own[] = (string) $pair['key']['v'];
        }
        // "Case" here is ASCII case, as everywhere in SEL -- strtoupper is.
        $projectedFolded = array_map('strtoupper', $projected);
        $dependencies = [];
        $dependenciesFolded = [];
        foreach ($custom as $pair) foreach (self::fieldReferences($pair['value'], $details['binder']) as $field) {
            if (in_array($field, $projected, true)) {
                if (!in_array($field, $own, true)) return null;
            } elseif (in_array(strtoupper($field), $projectedFolded, true)) {
                return null;
            } elseif (!in_array($field, $dependencies, true)) {
                // Two dependencies must not differ only by case either.
                if (in_array(strtoupper($field), $dependenciesFolded, true)) return null;
                $dependenciesFolded[] = strtoupper($field);
                $dependencies[] = $field;
            }
        }
        $rewrittenArgs = [];
        foreach ($pushable as $pair) {
            $rewrittenArgs[] = $pair['key'];
            $rewrittenArgs[] = $pair['value'];
        }
        foreach ($dependencies as $field) {
            $key = ['t' => 'text', 'v' => $field, 'pos' => $steps[$mapIndex]['pos']];
            $obj = ['t' => 'var', 'name' => $details['binder'], 'pos' => $steps[$mapIndex]['pos']];
            $rewrittenArgs[] = $key;
            $rewrittenArgs[] = ['t' => 'index', 'obj' => $obj, 'idx' => $key, 'pos' => $steps[$mapIndex]['pos']];
        }
        $record = $details['body'];
        $record['args'] = $rewrittenArgs;
        $map = $steps[$mapIndex];
        $map['args'] = $details['explicit']
            ? [$map['args'][0], $map['args'][1], $record]
            : [$map['args'][0], $record];
        $rewrittenSteps = array_merge(array_slice($steps, 0, $mapIndex), [$map], array_slice($steps, $mapIndex + 1));
        $rewrittenAst = Optimizer::buildPipeline($source, $rewrittenSteps);
        $sql = self::tryStatement($rewrittenAst, $dialect, $catalog, $options);
        if ($sql === null) return null;
        // The continuation re-applies the projection to the rows that come back:
        // a pushable pair is passed through BY KEY -- the SQL already computed it,
        // under that name -- and a custom pair is evaluated as written, over the
        // dependency columns projected beside it.
        $input = ['t' => 'var', 'name' => '_INPUT', 'pos' => $steps[$mapIndex]['pos']];
        $continuationArgs = [];
        foreach ($details['pairs'] as $i => $pair) {
            $continuationArgs[] = $pair['key'];
            if (isset($pushableAt[$i])) {
                $obj = ['t' => 'var', 'name' => $details['binder'], 'pos' => $pair['value']['pos']];
                $continuationArgs[] = ['t' => 'index', 'obj' => $obj, 'idx' => $pair['key'], 'pos' => $pair['value']['pos']];
            } else {
                $continuationArgs[] = $pair['value'];
            }
        }
        $continuationRecord = $details['body'];
        $continuationRecord['args'] = $continuationArgs;
        $continuationMap = $steps[$mapIndex];
        $continuationMap['args'] = $details['explicit']
            ? [$input, $continuationMap['args'][1], $continuationRecord]
            : [$input, $continuationRecord];
        return new HybridPlan([
            'dialect' => $dialect,
            'sqlStatement' => $sql,
            'sqlPrefixAst' => $rewrittenAst,
            'continuationAst' => $continuationMap,
            'continuationProgram' => new Program('', $continuationMap),
            'sourceTables' => self::sourceTables($rewrittenAst, $catalog),
        ]);
    }

    /**
     * Execute a plan with a callback receiving `(sql, bindings, fragment)`.
     * The callback may return a native row array or a Value.
     *
     * @param callable(string,list<Value>,Fragment):mixed $dbRunner
     * @param Value|array<mixed>|null $context
     */
    public static function execute(HybridPlan $plan, callable $dbRunner, $context = null): mixed
    {
        if ($plan->pureMemory) {
            if ($plan->continuationProgram === null) {
                throw new \LogicException('a pure-memory hybrid plan has no program');
            }
            return $plan->continuationProgram->run($context);
        }
        if ($plan->sqlStatement === null) {
            throw new \LogicException('a SQL hybrid plan has no statement');
        }
        $fragment = $plan->sqlStatement;
        $rows = $dbRunner($fragment->asStatement('params'), $fragment->bindings(), $fragment);
        if ($plan->pureSql) return $rows;
        $root = $context instanceof Value ? $context->copy() : Value::fromNative($context ?? []);
        $root->set($plan->continuationSourceVar,
            $rows instanceof Value ? $rows : Value::fromNative($rows));
        if ($plan->continuationProgram === null) {
            throw new \LogicException('a hybrid plan has no continuation program');
        }
        return $plan->continuationProgram->run($root);
    }
}
