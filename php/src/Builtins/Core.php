<?php
// Control, structure and aggregate built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Budget;
use Sel\Context;
use Sel\Dec;
use Sel\Registry;
use Sel\Value;

use function Sel\fail;

final class Core
{
    public static function register(): void
    {
        // The whole of SEL's control flow. Lazy, so only the taken branch is
        // evaluated — exactly the property the AST calling convention provides.
        Registry::define(['name' => 'IF', 'min' => 2, 'max' => 3, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                if ($a->bool(0)) {
                    return $a->val(1);
                }
                return $a->count() === 3 ? $a->val(2) : Value::text('');
            }]);

        // Flat multi-branch selection — sugar for a nested IF ladder, with exactly
        // the same laziness: conditions are evaluated in order, and only the
        // result that matches is evaluated at all.
        //
        // The argument count must be odd: condition/result pairs plus a mandatory
        // default. IF can safely let its two-argument form default to "" because
        // there is one branch and nothing to mis-pair, but with an even count here
        // a single miscounted comma would shift every pair by one and still
        // compile. Requiring the default turns that into a compile-time E_ARITY
        // instead of a wrong answer.
        Registry::define(['name' => 'COND', 'min' => 3, 'max' => PHP_INT_MAX, 'lazy' => true,
            'fn' => static function (Args $a): Value {
                $last = $a->count() - 1;
                for ($i = 0; $i < $last; $i += 2) {
                    if ($a->bool($i)) {
                        return $a->val($i + 1);
                    }
                }
                return $a->val($last);
            }]);

        // The one error a rule author raises deliberately.
        Registry::define(['name' => 'ABORT', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                fail('E_ABORT', $a->text(0), $a->posOf(0));
            }]);

        Registry::define(['name' => 'COUNT', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::int($a->val(0)->size())]);

        Registry::define(['name' => 'INDEXES', 'min' => 1, 'max' => 1,
            'fn' => static fn (Args $a): Value => Value::list(
                array_map(static fn (string $k): Value => Value::text($k), $a->val(0)->keys()),
            )]);

        Registry::define(['name' => 'HAS', 'min' => 2, 'max' => 2,
            'fn' => static fn (Args $a): Value => Value::bool($a->val(0)->has($a->text(1)))]);

        Registry::define(['name' => 'LIST', 'min' => 0, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $n = $a->count();
                if ($n > \Sel\Limits::MAX_COLLECTION) Budget::checkCollection($n, $a->pos, 'the list');
                $out = [];
                for ($i = 0; $i < $n; $i++) {
                    $out[] = $a->val($i)->copyBelow(1, $a->pos);
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'RECORD', 'min' => 0, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $n = $a->count();
                if ($n === 0) return Value::none();
                if ($n >> 1 > \Sel\Limits::MAX_COLLECTION) Budget::checkCollection($n >> 1, $a->pos, 'the record');
                if ($a->recordShape !== null) {
                    $values = [];
                    for ($i = 1; $i < $n; $i += 2) {
                        $values[] = $a->val($i)->copyBelow(1, $a->pos);
                    }
                    return Value::fromShape($a->recordShape, $values);
                }
                $keys = [];
                $values = [];
                for ($i = 0; $i < $n; $i += 2) {
                    $keys[] = $a->text($i);
                    $values[] = $a->val($i + 1)->copyBelow(1, $a->pos);
                }
                return Value::record($keys, $values);
            }]);

        Registry::define(['name' => 'TAKE', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                $count = $a->nonNegInt(1);
                if ($count === 0 || $val->isNull()) {
                    return Value::list([]);
                }
                if ($val->isList && $val->storage !== null) {
                    return Value::list(array_slice($val->storage, 0, $count));
                }
                $out = [];
                $seen = 0;
                $val->forEachElement(static function (string $key, Value $item) use (&$out, &$seen, $count): void {
                    if ($seen < $count) $out[] = $item;
                    $seen++;
                });
                return Value::list($out);
            }]);

        Registry::define(['name' => 'DROP', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                $count = $a->nonNegInt(1);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                if ($val->isList && $val->storage !== null) {
                    return Value::list(array_slice($val->storage, $count));
                }
                $out = [];
                $index = 0;
                $val->forEachElement(static function (string $key, Value $item) use (&$out, &$index, $count): void {
                    if ($index++ >= $count) $out[] = $item;
                });
                return Value::list($out);
            }]);

        Registry::define(['name' => 'SELECT_COLS', 'min' => 2, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                $colCount = $a->count();
                $cols = [];
                for ($i = 1; $i < $colCount; $i++) {
                    $cols[] = $a->text($i);
                }
                $out = [];
                $val->forEachElement(static function (string $key, Value $row) use (&$out, $cols): void {
                    $newRow = Value::none();
                    foreach ($cols as $c) {
                        if ($row->has($c)) {
                            $newRow->set($c, $row->get($c));
                        }
                    }
                    $out[] = $newRow;
                });
                return Value::list($out);
            }]);

        Registry::define(['name' => 'DISTINCT', 'min' => 1, 'max' => 1, 'fn' => Structure::distinct(...)]);

        Registry::define(['name' => 'SORT', 'min' => 1, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                return self::doSort($a, $ctx, 'ASC');
            }]);

        Registry::define(['name' => 'SORT_DESC', 'min' => 1, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                return self::doSort($a, $ctx, 'DESC');
            }]);

        Registry::define(['name' => 'SORT_BY', 'min' => 2, 'max' => 4, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                return self::doSort($a, $ctx, null);
            }]);

        self::registerAggregates();
    }

    /**
     * The ordering of SORT, SORT_BY, TOP and TOP_BY (spec §7.4): NULL first,
     * then numbers by value, booleans, text/binary bytewise, then by kind.
     * Structure::doTop shares it — one routine, so the two cannot drift.
     */
    public static function compareValues(Value $a, Value $b): int
    {
        return self::compareKeys(self::sortKey($a), self::sortKey($b));
    }

    /**
     * The sort key of a value, classified ONCE (PHP-P3): its rank under the
     * total order and what is compared inside that rank. A sort builds one per
     * element and compares the keys, instead of re-classifying both operands on
     * every comparison (n log n classifications, each a scalar-context walk and
     * a decimal parse). Comparing two keys is exactly compareValues.
     *
     * @return array{0:int,1:mixed} [rank, payload]: 1 int 0|1, 2 decimal array, 3|4 bytes
     */
    public static function sortKey(Value $v): array
    {
        // One total order (spec §7.3): rank by kind first, then compare inside a
        // rank. NULL < BOOL (FALSE < TRUE) < numeric-looking text and numbers by
        // exact value < every other TEXT bytewise < BIN bytewise. Ranking BEFORE
        // comparing is what makes it transitive: comparing numeric text with other
        // text bytewise while ranking it below them was not (`"10" < "1a"` but
        // `"9" > "1a"` and `"9" < "10"`).
        // A value with children and no scalar of its own is ordered by what scalar
        // context makes of it (§3.2: its first child, recursively): a record sorts
        // by its first field and ranks by that field's kind.
        $leaf = self::sortLeaf($v);
        $rank = self::sortRank($leaf);
        switch ($rank) {
            case 1:
                return [1, (int) (bool) $leaf->getScalar()];
            case 2:
                return [2, $leaf->asDecimal()];
            case 3:
            case 4:
                return [$rank, $leaf->asBytes()];
            default:
                return [$rank, null];
        }
    }

    /** @param array{0:int,1:mixed} $x @param array{0:int,1:mixed} $y */
    public static function compareKeys(array $x, array $y): int
    {
        if ($x[0] !== $y[0]) return $x[0] <=> $y[0];
        switch ($x[0]) {
            case 1:
                return $x[1] <=> $y[1];
            case 2:
                return Dec::cmp($x[1], $y[1]);
            case 3:
            case 4:
                return strcmp($x[1], $y[1]);
            default:
                return 0;
        }
    }

    /** The value scalar context reads; null when the chain ends in nothing (NULL). */
    private static function sortLeaf(Value $v): ?Value
    {
        if ($v->kind !== Value::NONE) return $v;
        try {
            return $v->scalarSource(null);
        } catch (\Sel\SelError $e) {
            return null;
        }
    }

    private static function sortRank(?Value $v): int
    {
        if ($v === null || $v->isNull()) return 0;
        if ($v->kind === Value::BOOL) return 1;
        if ($v->looksNumeric()) return 2;
        if ($v->kind === Value::TEXT) return 3;
        if ($v->kind === Value::BIN) return 4;
        return 5;
    }

    private static function doSort(Args $a, Context $ctx, ?string $forcedDir): Value
    {
        $val = $a->val(0);
        $count = $a->count();
        if ($count === 1) {
            if ($val->isNull()) return Value::list([]);
            $dir = $forcedDir ?? 'ASC';
            $indexed = [];
            $idx = 0;
            $pos = $a->pos;
            $val->forEachElement(static function (string $key, Value $item) use (&$indexed, &$idx, $pos): void {
                $indexed[] = ['item' => $item->copyBelow(1, $pos), 'sk' => self::sortKey($item), 'idx' => $idx++];
            });
        } else {
            if ($count === 2) {
                $binder = '_';
                $body = $a->node(1);
                $dir = $forcedDir ?? 'ASC';
            } elseif ($count === 3) {
                if ($forcedDir !== null) {
                    $binder = $a->symbol(1);
                    $body = $a->node(2);
                    $dir = $forcedDir;
                } elseif ($a->node(2)['t'] === 'text') {
                    $binder = '_';
                    $body = $a->node(1);
                    $dir = \Sel\Utf8::upper($a->text(2));
                } elseif ($a->isSymbol(1)) {
                    $binder = $a->symbol(1);
                    $body = $a->node(2);
                    $dir = 'ASC';
                } else {
                    $binder = '_';
                    $body = $a->node(1);
                    $dir = \Sel\Utf8::upper($a->text(2));
                }
            } else {
                $binder = $a->symbol(1);
                $body = $a->node(2);
                $dir = \Sel\Utf8::upper($a->text(3));
            }

            if ($dir !== 'ASC' && $dir !== 'DESC') {
                $posIdx = $count === 4 ? 3 : 2;
                fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", $a->posOf($posIdx));
            }
            // The direction is an argument like any other (spec §7.4): it is
            // checked above whether or not there is anything to sort.
            if ($val->isNull()) return Value::list([]);

            $indexed = [];
            $idx = 0;
            $needsK = self::containsVar($body, '_K');
            $frame = [$binder => Value::none()];
            if ($needsK) $frame['_K'] = Value::none();
            $ctx->pushFrame($frame);
            try {
                $val->forEachElement(function (string $k, Value $item) use (
                    &$indexed, &$idx, $ctx, $binder, $body, $a, &$frame, $needsK,
                ): void {
                    $frame[$binder] = $item;
                    $ctx->setFrameValue($binder, $item);
                    if ($needsK) {
                        $frame['_K'] = Value::text($k);
                        $ctx->setFrameValue('_K', $frame['_K']);
                    }
                    $evalKey = $a->evalNode($body);
                    $indexed[] = ['item' => $item->copyBelow(1, $a->pos), 'sk' => self::sortKey($evalKey), 'idx' => $idx++];
                });
            } finally {
                $ctx->popFrame();
            }
        }

        usort($indexed, static function (array $x, array $y) use ($dir): int {
            $c = self::compareKeys($x['sk'], $y['sk']);
            if ($dir === 'DESC') {
                $c = -$c;
            }
            return $c !== 0 ? $c : ($x['idx'] <=> $y['idx']);
        });

        $out = array_map(static fn (array $x): Value => $x['item'], $indexed);
        return Value::list($out);
    }

    /**
     * Two-argument form binds `_`; three-argument form takes a bare identifier as
     * the binder, checked by inspecting the AST node the caller handed us.
     *
     * @return array{binder:string, body:array<string,mixed>}
     */
    private static function shape(Args $a): array
    {
        return $a->count() === 3
            ? ['binder' => $a->symbol(1), 'body' => $a->node(2)]
            : ['binder' => '_', 'body' => $a->node(1)];
    }

    /**
     * Runs $visit per element with the binder and _K in scope. Returning a Value
     * from $visit stops the walk and becomes the result.
     */
    private static function walk(Args $a, Context $ctx, callable $visit, ?array $bodyOverride = null): ?Value
    {
        ['binder' => $binder, 'body' => $body] = self::shape($a);
        if ($bodyOverride !== null) $body = $bodyOverride;
        $result = null;
        $value = $a->val(0);
        $needsK = self::containsVar($body, '_K');
        $frame = [$binder => Value::none()];
        if ($needsK) $frame['_K'] = Value::none();
        $ctx->pushFrame($frame);
        $topFrame = &$ctx->frames[count($ctx->frames) - 1];
        try {
            // Snapshot (spec §7.3): every container read below is from a local
            // copy taken before the body first runs, so a body that appends,
            // adds a key or overwrites a later element does not change what is
            // visited. Copy-on-write makes the copies free until a write.
            $listKeys = $value->listKeys;
            $storage = $value->storage;
            $shapeKeys = $value->shape?->keys;
            $children = $value->children;
            if ($value->isList && $storage !== null) {
                if ($listKeys !== null) {
                    foreach ($storage as $i => $item) {
                        if ($result !== null) break;
                        $key = $listKeys[$i];
                        $topFrame[$binder] = $item;
                        if ($needsK) $topFrame['_K'] = Value::text($key);
                        $result = $visit($a->evalNode($body), $key, $item, $body);
                    }
                } elseif (!$needsK) {
                    $key = 1;
                    foreach ($storage as $item) {
                        if ($result !== null) break;
                        $topFrame[$binder] = $item;
                        $result = $visit($a->evalNode($body), $key++, $item, $body);
                    }
                } else {
                    foreach ($storage as $i => $item) {
                        if ($result !== null) break;
                        $key = (string) ($i + 1);
                        $topFrame[$binder] = $item;
                        $topFrame['_K'] = Value::text($key);
                        $result = $visit($a->evalNode($body), $key, $item, $body);
                    }
                }
            } elseif ($shapeKeys !== null && $storage !== null) {
                foreach ($shapeKeys as $i => $key) {
                    if ($result !== null) break;
                    $item = $storage[$i];
                    $topFrame[$binder] = $item;
                    if ($needsK) $topFrame['_K'] = Value::text($key);
                    $result = $visit($a->evalNode($body), $key, $item, $body);
                }
            } elseif ($children !== null && $value->size() > 0) {
                foreach ($children as $key => $item) {
                    if ($result !== null) break;
                    $keyStr = (string) $key;
                    $topFrame[$binder] = $item;
                    if ($needsK) $topFrame['_K'] = Value::text($keyStr);
                    $result = $visit($a->evalNode($body), $keyStr, $item, $body);
                }
            } elseif ($value->kind !== Value::NONE && !$value->isNull()) {
                $topFrame[$binder] = $value;
                if ($needsK) $topFrame['_K'] = Value::text('1');
                $result = $visit($a->evalNode($body), '1', $value, $body);
            }
        } finally {
            $ctx->popFrame();
        }
        return $result;
    }

    /** @param array<string,mixed>|null $node */
    /**
     * Whether evaluating $node might write into a value: it holds an assignment or
     * calls a host function. A collector copies an element when it collects it
     * (spec §3.4); while nothing below the body can write, deferring the copy to
     * the end is unobservable, so only a body that might write copies at
     * collection. Iterative: a body can be a flat chain as long as the source.
     */
    public static function mayWrite(?array $node): bool
    {
        $stack = [$node];
        while ($stack !== []) {
            $n = array_pop($stack);
            if (!is_array($n)) continue;
            $t = $n['t'] ?? null;
            if ($t === 'assign') return true;
            if ($t === 'call' && Registry::isHostFunction((string) ($n['name'] ?? ''))) return true;
            foreach (['args', 'items'] as $key) {
                foreach ($n[$key] ?? [] as $child) $stack[] = $child;
            }
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($n[$key]) && is_array($n[$key])) $stack[] = $n[$key];
            }
        }
        return false;
    }

    public static function containsVar(?array $node, string $name): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'var') {
            return \Sel\Utf8::casecmp((string) ($node['name'] ?? ''), $name) === 0;
        }
        foreach (['args', 'items'] as $key) {
            foreach ($node[$key] ?? [] as $child) {
                if (self::containsVar($child, $name)) return true;
            }
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($node[$key]) && is_array($node[$key]) && self::containsVar($node[$key], $name)) {
                return true;
            }
        }
        return false;
    }

    private static function registerAggregates(): void
    {
        Registry::define(['name' => 'ALL', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $short = self::walk($a, $ctx, static fn (Value $r, $k, $i, array $body): ?Value =>
                    $r->asBool($body['pos']) ? null : Value::bool(false));
                return $short ?? Value::bool(true);
            }]);

        Registry::define(['name' => 'ANY', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $short = self::walk($a, $ctx, static fn (Value $r, $k, $i, array $body): ?Value =>
                    $r->asBool($body['pos']) ? Value::bool(true) : null);
                return $short ?? Value::bool(false);
            }]);

        Registry::define(['name' => 'MAP', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $out = [];
                $pos = $a->pos;
                self::walk($a, $ctx, static function (Value $r) use (&$out, $pos): ?Value {
                    $out[] = $r->copyBelow(1, $pos);
                    return null;
                });
                return Value::list($out);
            }]);

        // The one aggregate that preserves keys — a filtered list should still be
        // addressable the way the original was.
        Registry::define(['name' => 'FILTER', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $storage = [];
                $keys = [];
                $needsCustomKeys = false;
                $expectedIndex = 1;
                // Over a join, the conjuncts are offered to the LINK, which
                // tests what it can on the rows it joins (SEL-0052, SEL-0054):
                // this FILTER's first -- it runs before the FILTER that handed
                // the rest down -- then the handed ones. Deep drops, below the
                // join directly under this FILTER, change its keys, so they
                // are allowed only where nothing observes them
                // (`keysUnobserved`, stamped by the physical optimiser).
                $src = $a->node(0);
                $handed = $ctx->joinPrefilter;
                $ctx->joinPrefilter = null;
                $own = null;
                if ($src !== null && $src['t'] === 'call' && in_array($src['name'], ['LINK', 'LINK_LEFT'], true)) {
                    ['binder' => $binder, 'body' => $body] = self::shape($a);
                    $own = Structure::leadingFieldConjuncts($body, $binder);
                    // A first conjunct that is neither a field test nor total
                    // ends every walk before it starts: hand nothing, gather
                    // nothing. A stage is [binder, conjuncts, how many joins
                    // lie between its FILTER and the join testing it].
                    $blocked = $own !== [] && $own[0]['fields'] === null && $own[0]['total'] === null;
                    $stages = $blocked ? [] : [[$binder, $own, 0]];
                    if ($handed !== null && !$blocked) foreach ($handed[0] as $stage) $stages[] = $stage;
                    $deep = $handed === null ? !empty($body['keysUnobserved']) : true;
                    if ($stages !== []) {
                        $ctx->joinPrefilter = [$stages, $deep, $handed === null ? [] : $handed[2], $handed === null ? [] : $handed[3]];
                    }
                }
                try {
                    $source = $a->val(0);
                } finally {
                    $ctx->joinPrefilter = null;
                }
                // The join's report -- which conjuncts every row that came up
                // has passed, whether a row was kept on an error, and whether
                // any row was dropped -- goes up as it is.
                $report = $ctx->joinPrefilterReport;
                $ctx->joinPrefilterReport = null;
                if ($report !== null && $handed !== null) $ctx->joinPrefilterReport = $report;
                // The conjuncts of this FILTER the join below applied held on
                // every row it built, unless it kept a row on an error: then
                // they are TRUE there, raise nowhere, and only the rest is
                // evaluated, in the source's order (with none left, the
                // join's list is the FILTER's result as it is).
                $bodyOverride = null;
                if ($own !== null && $report !== null && !$report[1]) {
                    $rest = [];
                    foreach ($own as $entry) {
                        if (!isset($report[0][Structure::conjunctId($entry['node'])])) $rest[] = $entry['node'];
                    }
                    if (count($rest) < count($own)) {
                        if ($rest === []) return $source;
                        $bodyOverride = array_shift($rest);
                        foreach ($rest as $node) {
                            $bodyOverride = ['t' => 'bin', 'op' => 'AND', 'l' => $bodyOverride, 'r' => $node, 'pos' => $bodyOverride['pos']];
                        }
                    }
                }
                $pos = $a->pos;
                self::walk($a, $ctx, static function (Value $r, string|int $key, Value $item, array $body) use (
                    &$storage, &$keys, &$needsCustomKeys, &$expectedIndex, $pos
                ): ?Value {
                    if ($r->asBool($body['pos'])) {
                        $storage[] = $item->copyBelow(1, $pos);
                        // The key as written, not (int) of it: "1x", "01" and
                        // " 1" all cast to 1 (review 2026-09-25 SEM-09).
                        $inPlace = is_int($key) ? $key === $expectedIndex : $key === (string) $expectedIndex;
                        if (!$needsCustomKeys && !$inPlace) {
                            $needsCustomKeys = true;
                            for ($j = 0, $n = count($storage) - 1; $j < $n; $j++) {
                                $keys[] = (string) ($j + 1);
                            }
                        }
                        if ($needsCustomKeys) {
                            $keys[] = is_string($key) ? $key : (string) $key;
                        }
                        $expectedIndex++;
                    }
                    return null;
                }, $bodyOverride);
                return Value::list($storage, $needsCustomKeys ? $keys : null);
            }]);

        Registry::define(['name' => 'SUM', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                // A native running total while every step fits (PHP-P27); the answer
                // is the one a chain of Dec::add calls gives.
                $total = ['m' => 0, 's' => 0];
                self::walk($a, $ctx, static function (Value $r, $k, $i, array $body) use (&$total): ?Value {
                    Dec::sumAccumulate($total, $r->asDecimalLazy($body['pos']), $body['pos']);
                    return null;
                });
                return Value::numTrusted(Dec::sumResult($total));
            }]);

        // Strict, not an aggregate: its second argument is a separator, not a body.
        Registry::define(['name' => 'JOIN', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $sep = $a->text(1);
                $parts = [];
                $bytes = 0;
                $a->val(0)->forEachElement(static function (string $key, Value $item) use (&$parts, &$bytes, $a): void {
                    $part = $item->asText($a->posOf(0));
                    $parts[] = $part;
                    $bytes += strlen($part);
                });
                // The result's size is known before it is built: refuse it at the
                // call (spec §6.4). The byte length bounds the code point length.
                $bytes += strlen($sep) * max(0, count($parts) - 1);
                if ($bytes > \Sel\Limits::MAX_TEXT_LEN) {
                    $cps = \Sel\Utf8::length($sep) * max(0, count($parts) - 1);
                    foreach ($parts as $part) $cps += \Sel\Utf8::length($part);
                    Budget::checkText($cps, $a->pos, 'the JOIN result');
                }
                return Value::text(implode($sep, $parts));
            }]);
    }
}
