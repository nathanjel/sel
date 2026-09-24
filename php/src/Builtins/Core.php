<?php
// Control, structure and aggregate built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
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
                $out = [];
                for ($i = 0; $i < $n; $i++) {
                    $out[] = $a->val($i)->copy();
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'RECORD', 'min' => 0, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a): Value {
                $keys = [];
                $values = [];
                $n = $a->count();
                for ($i = 0; $i < $n; $i += 2) {
                    $keys[] = $a->text($i);
                    $values[] = $a->val($i + 1)->copy();
                }
                if ($a->recordShape !== null && $a->recordShape->keys === $keys) {
                    return Value::fromShape($a->recordShape, $values);
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

        Registry::define(['name' => 'DISTINCT', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                $buckets = [];
                $out = [];
                $val->forEachElement(static function (string $key, Value $item) use (&$buckets, &$out): void {
                    $hash = $item->structuralHash();
                    $seen = false;
                    foreach ($buckets[$hash] ?? [] as $existing) {
                        if ($item->eql($existing)) {
                            $seen = true;
                            break;
                        }
                    }
                    if (!$seen) {
                        $buckets[$hash][] = $item;
                        $out[] = $item;
                    }
                });
                return Value::list($out);
            }]);

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
        $aNull = $a->isNull();
        $bNull = $b->isNull();
        if ($aNull && $bNull) return 0;
        if ($aNull) return -1;
        if ($bNull) return 1;

        $aNum = $a->looksNumeric();
        $bNum = $b->looksNumeric();
        if ($aNum && $bNum) {
            return Dec::cmp($a->asDecimal(), $b->asDecimal());
        }

        if ($a->kind === Value::BOOL && $b->kind === Value::BOOL) {
            return ((int) (bool) $a->getScalar()) <=> ((int) (bool) $b->getScalar());
        }

        if (($a->kind === Value::TEXT || $a->kind === Value::BIN) &&
            ($b->kind === Value::TEXT || $b->kind === Value::BIN)) {
            return strcmp($a->asBytes(), $b->asBytes());
        }

        $rank = static function (Value $v): int {
            if ($v->isNull()) return 0;
            if ($v->kind === Value::BOOL) return 1;
            if ($v->looksNumeric()) return 2;
            if ($v->kind === Value::TEXT) return 3;
            if ($v->kind === Value::BIN) return 4;
            return 5;
        };
        return $rank($a) <=> $rank($b);
    }

    private static function doSort(Args $a, Context $ctx, ?string $forcedDir): Value
    {
        $val = $a->val(0);
        if ($val->isNull()) {
            return Value::list([]);
        }
        $count = $a->count();
        if ($count === 1) {
            $dir = $forcedDir ?? 'ASC';
            $indexed = [];
            $idx = 0;
            $val->forEachElement(static function (string $key, Value $item) use (&$indexed, &$idx): void {
                $indexed[] = ['item' => $item, 'key' => $item, 'idx' => $idx++];
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
                    $dir = strtoupper($a->text(2));
                } elseif ($a->isSymbol(1)) {
                    $binder = $a->symbol(1);
                    $body = $a->node(2);
                    $dir = 'ASC';
                } else {
                    $binder = '_';
                    $body = $a->node(1);
                    $dir = strtoupper($a->text(2));
                }
            } else {
                $binder = $a->symbol(1);
                $body = $a->node(2);
                $dir = strtoupper($a->text(3));
            }

            if ($dir !== 'ASC' && $dir !== 'DESC') {
                $posIdx = $count === 4 ? 3 : 2;
                fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", $a->posOf($posIdx));
            }

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
                    $indexed[] = ['item' => $item, 'key' => $evalKey, 'idx' => $idx++];
                });
            } finally {
                $ctx->popFrame();
            }
        }

        usort($indexed, static function (array $x, array $y) use ($dir): int {
            $c = self::compareValues($x['key'], $y['key']);
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
     * A scalar with no children behaves as a one-element list containing itself,
     * consistent with scalar context (§3.2). A NONE with no children is genuinely
     * empty — that is what FILTER returns when nothing matched, and ALL over it
     * must be TRUE rather than a scalar-context failure.
     *
     * @return list<array{0:string,1:Value}>
     */
    private static function elements(Value $value): array
    {
        if ($value->size() > 0) {
            return $value->entries();
        }
        return $value->kind === Value::NONE ? [] : [['1', $value]];
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
            if ($value->isList && $value->storage !== null) {
                if ($value->listKeys !== null) {
                    foreach ($value->storage as $i => $item) {
                        if ($result !== null) break;
                        $key = $value->listKeys[$i];
                        $topFrame[$binder] = $item;
                        if ($needsK) $topFrame['_K'] = Value::text($key);
                        $result = $visit($a->evalNode($body), $key, $item, $body);
                    }
                } else {
                    foreach ($value->storage as $i => $item) {
                        if ($result !== null) break;
                        $key = $needsK ? (string) ($i + 1) : ($i + 1);
                        $topFrame[$binder] = $item;
                        if ($needsK) $topFrame['_K'] = Value::text((string) $key);
                        $result = $visit($a->evalNode($body), $key, $item, $body);
                    }
                }
            } elseif ($value->shape !== null && $value->storage !== null) {
                foreach ($value->shape->keys as $i => $key) {
                    if ($result !== null) break;
                    $item = $value->storage[$i];
                    $topFrame[$binder] = $item;
                    if ($needsK) $topFrame['_K'] = Value::text($key);
                    $result = $visit($a->evalNode($body), $key, $item, $body);
                }
            } elseif ($value->size() > 0) {
                foreach ($value->children as $key => $item) {
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
    private static function containsVar(?array $node, string $name): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'var') {
            return strcasecmp((string) ($node['name'] ?? ''), $name) === 0;
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
                self::walk($a, $ctx, static function (Value $r) use (&$out): ?Value {
                    $out[] = $r;
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
                self::walk($a, $ctx, static function (Value $r, string|int $key, Value $item, array $body) use (
                    &$storage, &$keys, &$needsCustomKeys, &$expectedIndex
                ): ?Value {
                    if ($r->asBool($body['pos'])) {
                        $storage[] = $item;
                        $keyInt = is_int($key) ? $key : (int) $key;
                        if (!$needsCustomKeys && $keyInt !== $expectedIndex) {
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
                $total = Dec::zero();
                self::walk($a, $ctx, static function (Value $r, $k, $i, array $body) use (&$total): ?Value {
                    $total = Dec::add($total, $r->asDecimal($body['pos']), $body['pos']);
                    return null;
                });
                return Value::num($total);
            }]);

        // Strict, not an aggregate: its second argument is a separator, not a body.
        Registry::define(['name' => 'JOIN', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $sep = $a->text(1);
                $parts = [];
                $a->val(0)->forEachElement(static function (string $key, Value $item) use (&$parts, $a, $sep): void {
                    $parts[] = $item->asText($a->posOf(0));
                });
                return Value::text(implode($sep, $parts));
            }]);
    }
}
