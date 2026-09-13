<?php
// SQL-prefix planning with an in-memory SEL continuation.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Optimizer;
use Sel\Program;
use Sel\SelError;
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
        [$constNames, $constContext] = Constants::scope($catalog);
        $normalized = Normalise::run($program->ast, $constNames, $constContext);
        $optimized = Optimizer::optimize($normalized, false, $options);
        $unwound = Optimizer::unwindPipeline($optimized);
        $source = $unwound['source'];
        $steps = $unwound['steps'];

        if ($steps === [] || ($source['t'] ?? null) !== 'var'
            || !$catalog->has($source['name'])
            || ($catalog->get($source['name'], $source['pos'])['kind'] ?? null) !== 'relation') {
            return new HybridPlan([
                'dialect' => $dialect,
                'pureMemory' => true,
                'continuationProgram' => $program,
                'sourceTables' => self::sourceTables($program->ast, $catalog),
            ]);
        }

        $fullAst = Optimizer::buildPipeline($source, $steps);
        $fullSql = self::tryStatement($fullAst, $dialect, $catalog, $options);
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

        return new HybridPlan([
            'dialect' => $dialect,
            'pureMemory' => true,
            'continuationProgram' => $program,
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

    /** @param array<string,mixed> $ast @return list<string> */
    private static function sourceTables(array $ast, Bindings $bindings): array
    {
        $out = [];
        $seen = [];
        $visit = function (?array $node) use (&$visit, &$out, &$seen, $bindings): void {
            if ($node === null) return;
            if (($node['t'] ?? null) === 'var' && $bindings->has($node['name'])) {
                $binding = $bindings->get($node['name'], $node['pos'] ?? null);
                if (($binding['kind'] ?? null) === 'relation' && !isset($seen[$node['name']])) {
                    $seen[$node['name']] = true;
                    $out[] = $node['name'];
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

    private static function containsUnsupportedSql(?array $node, string $dialect): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'call') {
            $special = ['IF' => true, 'COND' => true, 'COALESCE' => true, 'COUNT' => true,
                        'SUM' => true, 'AVG' => true, 'MIN' => true, 'MAX' => true,
                        'RECORD' => true, 'LIST' => true, 'LAZY_RECORD' => true];
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
    private static function fieldReferences(?array $node, string $binder = '_'): array
    {
        $wanted = array_map('strtoupper', [$binder, '_', '_1', '_2']);
        $out = [];
        $seen = [];
        $visit = function (?array $item) use (&$visit, &$out, &$seen, $wanted): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text'
                && in_array(strtoupper($item['obj']['name']), $wanted, true)) {
                $key = (string) $item['idx']['v'];
                $upper = strtoupper($key);
                if (!isset($seen[$upper])) {
                    $seen[$upper] = true;
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
        if ($mapIndex === null) return null;
        $details = self::mapRecordDetails($steps[$mapIndex]);
        if ($details === null) return null;
        $pushable = [];
        $custom = [];
        foreach ($details['pairs'] as $pair) {
            if (self::containsUnsupportedSql($pair['value'], $dialect)) $custom[] = $pair;
            else $pushable[] = $pair;
        }
        if ($custom === [] || $pushable === []) return null;

        $downstream = [];
        foreach (array_slice($steps, $mapIndex + 1) as $step) {
            foreach (self::fieldReferences($step, '_') as $field) $downstream[] = strtoupper($field);
        }
        foreach ($custom as $pair) if (in_array(strtoupper((string) $pair['key']['v']), $downstream, true)) return null;

        $projected = array_map(static fn (array $pair): string => strtoupper((string) $pair['key']['v']), $pushable);
        $dependencies = [];
        foreach ($custom as $pair) foreach (self::fieldReferences($pair['value'], $details['binder']) as $field) {
            if (!in_array(strtoupper($field), $projected, true) && !in_array(strtoupper($field), array_map('strtoupper', $dependencies), true)) {
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
        $input = ['t' => 'var', 'name' => '_INPUT', 'pos' => $steps[$mapIndex]['pos']];
        $continuationMap = $steps[$mapIndex];
        $continuationMap['args'] = $details['explicit']
            ? [$input, $continuationMap['args'][1], $details['body']]
            : [$input, $details['body']];
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
