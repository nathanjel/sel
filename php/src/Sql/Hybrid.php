<?php
// SQL-prefix planning with an in-memory SEL continuation.
//
// The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at the
// PIPELINE, whichever helper assignments it is written through (see "helper
// assignments" below -- inlining every helper the way stage 1 does for
// translate() made the continuation report an error at the helper's
// definition where run() reports its use), `sourceTables` names PHYSICAL
// sources (a relation's `from`, or a relation query's text verbatim), a
// program stage 1 refuses is a pure-memory plan rather than an exception, and
// `$options` is one array that reaches both the logical optimiser and the
// translator.

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
    public ?array $selectedMember;
    public ?array $selected_member;

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
        $this->selectedMember = $this->selected_member = $spec['selectedMember'] ?? null;
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
        // Stage 1 first, exactly as the translator runs it, for its verdict. A
        // program stage 1 refuses -- `A += 1; ...`, a bare statement before the
        // result -- is a program no part of which can be pushed down, which is
        // a pure-memory plan and not an exception: "none of it" is one of the
        // planner's answers. Its TREE is not what is planned, though: see
        // "helper assignments" below.
        [$constNames, $constContext] = Constants::scope($catalog);
        try {
            $normalized = Normalise::run($program->ast, $constNames, $constContext);
            $identityBarrier = Constants::identityLossBeforeGrouping($normalized);
        } catch (SqlError) {
            return self::pureMemoryPlan($program, $dialect, $catalog);
        }
        ['leading' => $leading, 'result' => $result] = self::statements($program->ast);
        $literals = self::literalHelpers($leading, $options);
        $defs = self::definitions($leading);
        $isRelation = static fn (?array $node): bool => $node !== null && ($node['t'] ?? null) === 'var'
            && $catalog->has($node['name'])
            && ($catalog->get($node['name'], $node['pos'])['kind'] ?? null) === 'relation';
        $unwound = self::unwindThroughHelpers($result, $defs, $literals);
        if ($unwound['steps'] === [] || !$isRelation($unwound['source'])) {
            return self::pureMemoryPlan($program, $dialect, $catalog);
        }
        $optimized = Optimizer::optimize(
            Optimizer::buildPipeline($unwound['source'], $unwound['steps']), false, $options);
        $unwound = Optimizer::unwindPipeline($optimized);
        $source = $unwound['source'];
        $steps = $unwound['steps'];
        if ($steps === [] || !$isRelation($source)) return self::pureMemoryPlan($program, $dialect, $catalog);

        $helpers = [
            'defs' => $defs,
            'wrap' => static fn (array $node): array => self::withHelpers($leading, $node),
            // The physical sources of a wrapped tree are read off what the
            // translator renders: stage 1's tree, where an assignment a binder
            // shadows is gone.
            'tables' => static fn (array $wrapped): array => self::sourceTables(
                Normalise::run($wrapped, $constNames, $constContext), $catalog),
        ];

        // The whole pipeline, unless its rows would be a bucket's keys: the
        // translator renders a bare bucket as its keys, and a plan that pushes
        // the whole of `... .> BUCKET(k)` would hand them back as the answer.
        $fullAst = $helpers['wrap'](Optimizer::buildPipeline($source, $steps));
        $fullSql = $identityBarrier || self::rowsAreNotTheValue($steps) ? null : self::tryStatement($fullAst, $dialect, $catalog, $options);
        if ($fullSql !== null) {
            return new HybridPlan([
                'dialect' => $dialect,
                'sqlStatement' => $fullSql,
                'sqlPrefixAst' => $fullAst,
                'pureSql' => true,
                'sourceTables' => $helpers['tables']($fullAst),
            ]);
        }

        $latest = self::tryLatestMember($source, $steps, $dialect, $catalog, $options, $helpers);
        if ($latest !== null) return $latest;
        $fallthrough = $identityBarrier ? null : self::tryPlanFallthrough($source, $steps, $dialect, $catalog, $options, $helpers);
        if ($fallthrough !== null) return $fallthrough;

        for ($count = count($steps) - 1; $count >= 1; $count--) {
            $prefixSteps = array_slice($steps, 0, $count);
            if (self::rowsAreNotTheValue($prefixSteps)) continue;
            $prefixAst = $helpers['wrap'](Optimizer::buildPipeline($source, $prefixSteps));
            if ($identityBarrier) {
                try {
                    if (Constants::identityLossBeforeGrouping(Normalise::run($prefixAst, $constNames, $constContext), true)) continue;
                } catch (SqlError) {
                    continue;
                }
            }
            $sql = self::tryStatement($prefixAst, $dialect, $catalog, $options);
            if ($sql === null) continue;
            $remaining = array_slice($steps, $count);
            $input = ['t' => 'var', 'name' => '_INPUT', 'pos' => $remaining[0]['pos']];
            $continuationAst = $helpers['wrap'](Optimizer::buildPipeline($input, $remaining));
            return new HybridPlan([
                'dialect' => $dialect,
                'sqlStatement' => $sql,
                'sqlPrefixAst' => $prefixAst,
                'continuationAst' => $continuationAst,
                'continuationProgram' => new Program('', $continuationAst),
                'sourceTables' => $helpers['tables']($prefixAst),
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
    private static function latestFieldName(?array $n): ?string
    {
        return ($n['t'] ?? null) === 'index' && $n['obj']['t'] === 'var' && $n['obj']['name'] === '_'
            && $n['idx']['t'] === 'text' ? $n['idx']['v'] : null;
    }

    private static function tryLatestMember(array $source, array $steps, string $dialect, Bindings $catalog, array $opts, array $helpers): ?HybridPlan
    {
        if (!in_array($dialect, ['mariadb', 'mysql', 'postgresql', 'sqlite'], true)) return null;
        $rel = $catalog->get($source['name'], $source['pos']);
        $revision = $rel['unique_key'] ?? null;
        $at = null;
        foreach ($steps as $i => $s) if ($s['name'] === 'BUCKET') { $at = $i; break; }
        if ($revision === null || $at === null || !is_string($rel['from']) || ($rel['correlate'] ?? null)) return null;
        $ba = $steps[$at]['args'];
        $partition = in_array(count($ba), [2, 3], true) ? self::latestFieldName($ba[1]) : null;
        $body = count($ba) === 3 ? $ba[2] : null;
        $m = $steps[$at + 1] ?? null;
        if ($body === null && ($m['name'] ?? null) === 'MAP' && count($m['args']) === 2) $body = $m['args'][1];
        $upper = static fn (string $s): string => strtr($s, 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ');
        $pf = $rel['fields'][$upper($partition ?? '')] ?? [];
        $rf = $rel['fields'][$upper($revision)] ?? [];
        if ($partition === null || ($body['t'] ?? null) !== 'call' || $body['name'] !== 'RECORD' || count($body['args']) !== 4
            || !in_array($pf['type'] ?? null, ['NUM', 'TEXT'], true) || ($rf['type'] ?? null) !== 'NUM'
            || ($pf['column'] ?? null) !== $partition || ($rf['column'] ?? null) !== $revision
            || ($pf['raw'] ?? null) || ($rf['raw'] ?? null) || ($rf['guard'] ?? false)) return null;
        $ra = $body['args'];
        if ($ra[0]['t'] !== 'text' || $ra[2]['t'] !== 'text' || $ra[0]['v'] === $ra[2]['v']) return null;
        $top = null; $hasKey = false;
        foreach ([$ra[1], $ra[3]] as $v) {
            if ($v['t'] === 'call' && $v['name'] === 'TOP_BY') $top = $v;
            if ($v['t'] === 'var' && $v['name'] === '_K') $hasKey = true;
        }
        if ($top === null || !$hasKey) return null;
        $ta = $top['args'];
        if (count($ta) !== 4 || $ta[0]['t'] !== 'var' || $ta[0]['name'] !== '_' || self::latestFieldName($ta[1]) !== $revision
            || $ta[2]['t'] !== 'text' || $ta[2]['v'] !== 'DESC' || $ta[3]['t'] !== 'num' || $ta[3]['v'] !== '1') return null;
        foreach (array_slice($steps, 0, $at) as $s) {
            if ($s['name'] === 'FILTER') continue;
            if ($s['name'] !== 'SORT_BY' || !in_array(count($s['args']), [2, 3], true) || self::latestFieldName($s['args'][1]) !== $revision
                || (count($s['args']) === 3 && ($s['args'][2]['t'] !== 'text' || $s['args'][2]['v'] !== 'ASC'))) return null;
        }
        $dummy = ['t' => 'call', 'name' => 'FILTER', 'pos' => $source['pos'], 'args' => [$source, ['t' => 'bool', 'v' => true, 'pos' => $source['pos']]]];
        $prefix = $helpers['wrap'](Optimizer::buildPipeline($source, $at ? array_slice($steps, 0, $at) : [$dummy]));
        $sql = self::tryStatement($prefix, $dialect, $catalog, $opts);
        if ($sql === null) return null;
        try {
            $emit = new Emit($dialect);
            $input = '_sel_input'; $groups = '_sel_latest';
            while ($upper($input) === $upper($rel['from'])) $input .= '_';
            while (in_array($upper($groups), [$upper($rel['from']), $upper($input)], true)) $groups .= '_';
            [$qi, $qg, $qr, $qmax, $qfirst] = array_map(fn ($s) => $emit->ident($s), [$input, $groups, $revision, '_sel_revision', '_sel_first']);
            $key = $emit->textOperand(new Fragment([$emit->ident($partition)], $pf['type'], $dialect))->asValue();
            $parts = ["WITH {$qi} AS (", ...$sql->parts,
                "), {$qg} AS (SELECT MAX({$qr}) AS {$qmax}, MIN({$qr}) AS {$qfirst} FROM {$qi} GROUP BY {$key}) "
                . "SELECT {$qi}.* FROM {$qi} JOIN {$qg} ON {$qi}.{$qr} = {$qg}.{$qmax} ORDER BY {$qg}.{$qfirst} ASC"];
            $continuation = $helpers['wrap'](Optimizer::buildPipeline(['t' => 'var', 'name' => '_INPUT', 'pos' => $steps[$at]['pos']], array_slice($steps, $at)));
            return new HybridPlan(['dialect' => $dialect,
                'sqlStatement' => new Fragment($parts, 'STATEMENT', $dialect, $sql->params, $sql->paramKinds, $sql->caveats),
                'sqlPrefixAst' => $prefix, 'continuationAst' => $continuation, 'continuationProgram' => new Program('', $continuation),
                'sourceTables' => [$rel['from']], 'selectedMember' => ['partition_key' => $partition, 'revision_key' => $revision]]);
        } catch (SqlError $e) { return null; }
    }

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

    /**
     * Whether the SQL rows for this step list are a join's rows without the
     * binders SEL's rows carry. A LINK's row in SEL holds each side under its
     * binders and the promoted fields beside them (spec §7.4); SQL carries the
     * promoted fields alone. A MAP, a SELECT_COLS or a projected BUCKET after the
     * LINK makes the rows exact again -- what they compute is over the promoted
     * fields, or is refused -- so a prefix whose LINK nothing has projected is
     * not a split point and not a full pushdown (finding Y, lanes): its
     * continuation would read `_["C"]` where the database sent nothing.
     *
     * @param list<array<string,mixed>> $steps
     */
    private static function joinRowsLackBinders(array $steps): bool
    {
        $joined = false;
        foreach ($steps as $step) {
            $name = $step['name'] ?? '';
            if ($name === 'LINK' || $name === 'LINK_LEFT') $joined = true;
            elseif ($name === 'MAP' || $name === 'SELECT_COLS' || $name === 'BUCKET') $joined = false;
        }
        return $joined;
    }

    /**
     * The two together: a prefix whose SQL rows are not the value SEL would have
     * produced for it, whatever the translator says about it.
     *
     * @param list<array<string,mixed>> $steps
     */
    private static function rowsAreNotTheValue(array $steps): bool
    {
        return self::bucketRowsAreKeys($steps) || self::joinRowsLackBinders($steps);
    }

    /**
     * `$defs` are the helper definitions: a read of one is as unsupported as
     * its definition, since the translator will inline it.
     *
     * @param array<string,array<string,mixed>>|null $defs @param array<string,bool> $seen
     */
    private static function containsUnsupportedSql(?array $node, string $dialect,
                                                   ?array $defs = null, array $seen = []): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'var' && $defs !== null && isset($defs[$node['name']])
            && !isset($seen[$node['name']])) {
            return self::containsUnsupportedSql($defs[$node['name']], $dialect, $defs,
                $seen + [$node['name'] => true]);
        }
        if (($node['t'] ?? null) === 'call') {
            $special = ['IF' => true, 'COND' => true, 'COALESCE' => true, 'COUNT' => true,
                        'SUM' => true, 'AVG' => true, 'MIN' => true, 'MAX' => true,
                        'RECORD' => true, 'LIST' => true];
            if (!isset($special[$node['name']])) {
                $entry = Map::entry($dialect, 'funcs', strtoupper($node['name']));
                if ($entry === Map::MISSING || $entry === null || is_string($entry)) return true;
            }
            foreach ($node['args'] ?? [] as $item) {
                if (self::containsUnsupportedSql($item, $dialect, $defs, $seen)) return true;
            }
            return false;
        }
        foreach (['args', 'items'] as $key) foreach ($node[$key] ?? [] as $item) {
            if (self::containsUnsupportedSql($item, $dialect, $defs, $seen)) return true;
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($node[$key]) && is_array($node[$key])
                && self::containsUnsupportedSql($node[$key], $dialect, $defs, $seen)) return true;
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
    // FILTER retains ordinal keys that SQL rows plus the local MAP cannot restore.
    private const FALLTHROUGH_DOWNSTREAM = ['SORT_BY', 'TOP_BY', 'TAKE', 'DROP'];

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
            || $body['name'] !== 'RECORD'
            || count($body['args']) % 2 !== 0) return null;
        $pairs = [];
        $seen = [];
        for ($i = 0; $i < count($body['args']); $i += 2) {
            if (($body['args'][$i]['t'] ?? null) !== 'text') return null;
            $key = $body['args'][$i]['v'];
            if (isset($seen[$key])) return null;
            $seen[$key] = true;
            $pairs[] = ['key' => $body['args'][$i], 'value' => $body['args'][$i + 1]];
        }
        return ['explicit' => $explicit, 'binder' => $explicit ? $args[1]['name'] : '_',
                'body' => $body, 'pairs' => $pairs];
    }

    /** @param array{defs:array<string,array<string,mixed>>,wrap:callable,tables:callable} $helpers */
    private static function tryPlanFallthrough(array $source, array $steps, string $dialect,
                                                Bindings $catalog, array $options, array $helpers): ?HybridPlan
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
            if (self::containsUnsupportedSql($pair['value'], $dialect, $helpers['defs'])) {
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
        $rewrittenAst = $helpers['wrap'](Optimizer::buildPipeline($source, $rewrittenSteps));
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
        $continuationAst = $helpers['wrap']($continuationMap);
        return new HybridPlan([
            'dialect' => $dialect,
            'sqlStatement' => $sql,
            'sqlPrefixAst' => $rewrittenAst,
            'continuationAst' => $continuationAst,
            'continuationProgram' => new Program('', $continuationAst),
            'sourceTables' => $helpers['tables']($rewrittenAst),
        ]);
    }

    // --- helper assignments -------------------------------------------------
    //
    // Stage 1 inlines a helper assignment for translate(): `Y = "x"; ... + Y`
    // is rendered as `... + "x"`, the literal keeping its definition-site
    // position, which is right for a refusal message. It is wrong for the
    // memory half of a plan, because that half is a program run() evaluates
    // and §12.1 promises it reports errors where run() would: run() evaluates
    // the READ of Y at the use site and reports `+`'s operand there, and it
    // evaluates the definition once, before the pipeline, not once per row
    // (review 2026-09-15 finding AJ). So the planner does not inline. It plans
    // the program as written, three ways:
    //
    //   * A helper that IS a literal -- after inlining earlier such helpers
    //     and folding, `N = 1 + 1` as much as `N = 2` -- is inlined at its
    //     reads, stamped with the read's position. That is invisible: a leaf
    //     literal cannot fail, and neither can the read, since the definition
    //     exists. It keeps `TAKE(N)` a `LIMIT 2` rather than a helper the SQL
    //     has to carry.
    //   * A helper read as the pipeline's SOURCE is unwound through: `X =
    //     ORDERS .> TAKE(2); X .> MAP(...)` is one pipeline over ORDERS, so
    //     the prefix search sees every step. Only the source position looks
    //     through a helper; a read anywhere else stays a read.
    //   * What is handed to the translator, and what is kept for the
    //     continuation, carries in front of it the assignments it still reads
    //     and the ones those read, in program order, as the program wrote
    //     them. The translator runs its own stage 1 over that seq and inlines;
    //     the continuation evaluates them once, before its steps, as run()
    //     does. An assignment nothing after the split reads is dropped, as
    //     stage 1 drops it for translate() -- the one departure, and the same
    //     one.

    private const LITERAL_TYPES = ['num' => true, 'text' => true, 'bool' => true, 'null' => true];

    /**
     * The leading statements and the result expression of a program.
     *
     * @param array<string,mixed> $ast
     * @return array{leading:list<array<string,mixed>>,result:array<string,mixed>}
     */
    private static function statements(array $ast): array
    {
        if (($ast['t'] ?? null) !== 'seq') return ['leading' => [], 'result' => $ast];
        $items = $ast['items'];
        return ['leading' => array_slice($items, 0, -1), 'result' => $items[count($items) - 1]];
    }

    /**
     * The name a leading statement assigns. Stage 1 has accepted every
     * statement by the time this runs, so each is an assignment whose target
     * is a name, or a name indexed by constants.
     *
     * @param array<string,mixed> $statement
     */
    private static function assignedName(array $statement): string
    {
        $target = $statement['target'];
        while (($target['t'] ?? null) === 'index') $target = $target['obj'];
        return $target['name'];
    }

    /**
     * The whole-name definitions, by name. Stage 1 refuses a name assigned
     * twice, or both whole and by index, so each name here has exactly one.
     *
     * @param list<array<string,mixed>> $leading @return array<string,array<string,mixed>>
     */
    private static function definitions(array $leading): array
    {
        $defs = [];
        foreach ($leading as $s) {
            if (($s['target']['t'] ?? null) === 'var') $defs[$s['target']['name']] = $s['value'];
        }
        return $defs;
    }

    /**
     * `$node` with every read of a literal helper replaced by the literal,
     * stamped with the read's position. Binder scoping is stage 1's: a binder
     * shadows a same-named helper inside its body. PHP arrays are values, so
     * this copies on the way down and never writes into the caller's tree.
     *
     * @param array<string,mixed>|null $node
     * @param array<string,array<string,mixed>> $literals @param list<string> $bound
     */
    private static function inlineLiterals(?array $node, array $literals, array $bound = []): ?array
    {
        if ($node === null) return null;
        $t = $node['t'] ?? null;
        if ($t === 'var') {
            if (in_array($node['name'], $bound, true) || !isset($literals[$node['name']])) return $node;
            $literal = $literals[$node['name']];
            $literal['pos'] = $node['pos'];
            return $literal;
        }
        if (isset(self::LITERAL_TYPES[$t])) return $node;
        if ($t === 'un') {
            $node['x'] = self::inlineLiterals($node['x'], $literals, $bound);
            return $node;
        }
        if ($t === 'bin') {
            $node['l'] = self::inlineLiterals($node['l'], $literals, $bound);
            $node['r'] = self::inlineLiterals($node['r'], $literals, $bound);
            return $node;
        }
        if ($t === 'index') {
            $node['obj'] = self::inlineLiterals($node['obj'], $literals, $bound);
            $node['idx'] = self::inlineLiterals($node['idx'], $literals, $bound);
            return $node;
        }
        if ($t === 'list' || $t === 'seq') {
            $node['items'] = array_map(
                static fn (array $item): array => self::inlineLiterals($item, $literals, $bound), $node['items']);
            return $node;
        }
        if ($t === 'assign') {
            $node['value'] = self::inlineLiterals($node['value'], $literals, $bound);
            return $node;
        }
        if ($t === 'call') {
            $inner = $bound;
            $binds = !empty($node['spec']['binds']);
            $argc = count($node['args']);
            if ($binds) {
                $inner[] = '_K';
                $inner[] = $argc === 3 && Constants::isBinderName($node['args'][1])
                    ? $node['args'][1]['name'] : '_';
            }
            $args = [];
            foreach ($node['args'] as $i => $arg) {
                if ($binds && $i === 1 && $argc === 3 && Constants::isBinderName($arg)) {
                    $args[] = $arg;
                    continue;
                }
                $args[] = self::inlineLiterals($arg, $literals, $i === 0 ? $bound : $inner);
            }
            $node['args'] = $args;
            return $node;
        }
        return $node;
    }

    /**
     * The literal helpers: each whole-name definition, after the earlier
     * literal helpers are inlined into it and it is folded, when what is left
     * is a leaf.
     *
     * @param list<array<string,mixed>> $leading @param array<string,mixed> $options
     * @return array<string,array<string,mixed>>
     */
    private static function literalHelpers(array $leading, array $options): array
    {
        $literals = [];
        foreach ($leading as $s) {
            if (($s['target']['t'] ?? null) !== 'var') continue;
            $folded = Optimizer::optimize(self::inlineLiterals($s['value'], $literals), false, $options);
            if (isset(self::LITERAL_TYPES[$folded['t'] ?? ''])) $literals[$s['target']['name']] = $folded;
        }
        return $literals;
    }

    /**
     * The pipeline the planner probes: the result unwound, and where its
     * source is a helper, that helper's definition unwound in turn.
     *
     * @param array<string,mixed> $result
     * @param array<string,array<string,mixed>> $defs @param array<string,array<string,mixed>> $literals
     * @return array{source:array<string,mixed>,steps:list<array<string,mixed>>}
     */
    private static function unwindThroughHelpers(array $result, array $defs, array $literals): array
    {
        $unwound = Optimizer::unwindPipeline(self::inlineLiterals($result, $literals));
        $source = $unwound['source'];
        $steps = $unwound['steps'];
        $seen = [];
        while (($source['t'] ?? null) === 'var' && isset($defs[$source['name']]) && !isset($seen[$source['name']])) {
            $seen[$source['name']] = true;
            $inner = Optimizer::unwindPipeline(self::inlineLiterals($defs[$source['name']], $literals));
            $source = $inner['source'];
            $steps = array_merge($inner['steps'], $steps);
        }
        return ['source' => $source, 'steps' => $steps];
    }

    /**
     * The names a tree reads, binders included: an over-approximation that
     * can only keep an assignment the tree does not need, never drop one it
     * does.
     *
     * @param array<string,mixed>|null $node @param array<string,bool> $out
     * @return array<string,bool>
     */
    private static function readNames(?array $node, array $out = []): array
    {
        if ($node === null) return $out;
        if (($node['t'] ?? null) === 'var') {
            $out[$node['name']] = true;
            return $out;
        }
        foreach (['args', 'items'] as $key) foreach ($node[$key] ?? [] as $item) $out = self::readNames($item, $out);
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($node[$key]) && is_array($node[$key])) $out = self::readNames($node[$key], $out);
        }
        return $out;
    }

    /**
     * The leading assignments `$node` depends on, in program order: those
     * whose name it reads, and those THEY read, transitively.
     *
     * @param list<array<string,mixed>> $leading @param array<string,mixed> $node
     * @return list<array<string,mixed>>
     */
    private static function referencedAssignments(array $leading, array $node): array
    {
        $needed = self::readNames($node);
        $grew = true;
        while ($grew) {
            $grew = false;
            foreach ($leading as $s) {
                if (!isset($needed[self::assignedName($s)])) continue;
                foreach (self::readNames($s['value']) as $name => $_) {
                    if (!isset($needed[$name])) {
                        $needed[$name] = true;
                        $grew = true;
                    }
                }
            }
        }
        return array_values(array_filter($leading,
            static fn (array $s): bool => isset($needed[self::assignedName($s)])));
    }

    /**
     * `$node` behind the assignments it depends on, as the program wrote them
     * -- a seq the translator's stage 1 inlines and the evaluator runs in
     * order -- or `$node` itself when it depends on none.
     *
     * @param list<array<string,mixed>> $leading @param array<string,mixed> $node
     * @return array<string,mixed>
     */
    private static function withHelpers(array $leading, array $node): array
    {
        $kept = self::referencedAssignments($leading, $node);
        return $kept === [] ? $node
            : ['t' => 'seq', 'items' => array_merge($kept, [$node]), 'pos' => $kept[0]['pos']];
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
