<?php
// Control, structure and aggregate built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Ast;
use Sel\Budget;
use Sel\Context;
use Sel\Dec;
use Sel\Limits;
use Sel\Registry;
use Sel\SelError;
use Sel\Utf8;
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
            'fn' => static function (Args $a, Context $ctx): Value {
                $n = $a->count();
                if ($n > Limits::MAX_COLLECTION) Budget::checkCollection($n, $a->pos, 'the list');
                $out = [];
                $hold = $ctx->writeFree;
                for ($i = 0; $i < $n; $i++) {
                    $out[] = $hold ? $a->val($i)->checkDepthBelow(1, $a->pos) : $a->val($i)->copyBelow(1, $a->pos);
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'RECORD', 'min' => 0, 'max' => PHP_INT_MAX,
            'fn' => static function (Args $a, Context $ctx): Value {
                $n = $a->count();
                if ($n === 0) return Value::none();
                if ($n >> 1 > Limits::MAX_COLLECTION) Budget::checkCollection($n >> 1, $a->pos, 'the record');
                // Copied as `,` copies (spec §3.4), unless nothing can write.
                $hold = $ctx->writeFree;
                if ($a->recordShape !== null) {
                    $values = [];
                    for ($i = 1; $i < $n; $i += 2) {
                        $values[] = $hold ? $a->val($i)->checkDepthBelow(1, $a->pos) : $a->val($i)->copyBelow(1, $a->pos);
                    }
                    return Value::fromShape($a->recordShape, $values);
                }
                $keys = [];
                $values = [];
                for ($i = 0; $i < $n; $i += 2) {
                    $keys[] = $a->text($i);
                    $values[] = $hold ? $a->val($i + 1)->checkDepthBelow(1, $a->pos) : $a->val($i + 1)->copyBelow(1, $a->pos);
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
     * The ordering of SORT, SORT_BY, TOP and TOP_BY (spec §7.3), as one
     * comparison: compareKeys over two sortKeys, which is what SORT and
     * Structure::doTop use (they build each key once). Kept for the tests,
     * which hold the keyed form to it.
     */
    public static function compareValues(Value $a, Value $b): int
    {
        return self::compareKeys(self::sortKey($a), self::sortKey($b));
    }

    /**
     * The sort key of a value, classified ONCE: its rank under the
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
        } catch (SelError $e) {
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

    /**
     * The decoded sort form of this SORT* / TOP* call (Registry::sortForm).
     *
     * @return array{binder:?int,key:?int,dir:?int}
     */
    public static function sortFormOf(Args $a): array
    {
        $nodes = [];
        for ($i = 0, $n = $a->count(); $i < $n; $i++) $nodes[] = $a->node($i);
        return Registry::sortForm($a->name, $nodes)
            // The manifest refuses any other count when the program is compiled.
            ?? throw new \LogicException("unreachable: {$a->name} with {$a->count()} arguments");
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
            $hold = $ctx->writeFree;
            $val->forEachElement(static function (string $key, Value $item) use (&$indexed, &$idx, $pos, $hold): void {
                $indexed[] = ['item' => $hold ? $item->checkDepthBelow(1, $pos) : $item->copyBelow(1, $pos), 'sk' => self::sortKey($item), 'idx' => $idx++];
            });
        } else {
            // The form (Registry::sortForm): a text-literal third slot is the
            // direction even where a bare name stands second.
            $form = self::sortFormOf($a);
            $binder = $form['binder'] === null ? '_' : $a->symbol($form['binder']);
            $body = $a->node($form['key']);
            $dir = $form['dir'] === null ? ($forcedDir ?? 'ASC') : Utf8::upper($a->text($form['dir']));
            if ($dir !== 'ASC' && $dir !== 'DESC') {
                fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", $a->posOf($form['dir']));
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
                    $indexed[] = ['item' => $ctx->writeFree ? $item->checkDepthBelow(1, $a->pos) : $item->copyBelow(1, $a->pos), 'sk' => self::sortKey($evalKey), 'idx' => $idx++];
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
     * [member, field] when $conjunct is `IS_NULL(binder["member"]["field"])` --
     * the shipped IS_NULL of a literal field of a literal member of the
     * FILTER's element, the binder exactly as named -- else null. Over a
     * LINK_LEFT, a member that is one of the join's right binder keys is the
     * right row (spec §7.4), and the join may skip building the joined rows of
     * the right rows the conjunct is FALSE on (Structure::rightNullRejects).
     *
     * @param array<string,mixed>|null $conjunct
     * @return array{0:string,1:string}|null
     */
    private static function rightNullTest(?array $conjunct, string $binder): ?array
    {
        if ($conjunct === null || $conjunct['t'] !== 'call' || $conjunct['name'] !== 'IS_NULL'
                || count($conjunct['args']) !== 1 || Registry::mayHaveEffects('IS_NULL')) {
            return null;
        }
        $field = $conjunct['args'][0];
        if ($field === null || $field['t'] !== 'index' || ($field['idx']['t'] ?? null) !== 'text') return null;
        $member = $field['obj'] ?? null;
        if ($member === null || $member['t'] !== 'index' || ($member['idx']['t'] ?? null) !== 'text'
                || ($member['obj']['t'] ?? null) !== 'var' || $member['obj']['name'] !== $binder) {
            return null;
        }
        return [(string) $member['idx']['v'], (string) $field['idx']['v']];
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

    /**
     * Whether evaluating $node always yields a value no other reference holds: a
     * RECORD or LIST call (which copied its own arguments, or holds them where
     * nothing can write) or an operator that builds its result -- arithmetic, a
     * comparison, `&`, the logic and bitwise operators, unary minus and NOT --
     * never `??` or `???`, which hand back an operand. A collector holding such a
     * value needs no copy of it (spec §3.4), only the copy's depth check.
     *
     * @param array<string,mixed>|null $node
     */
    public static function buildsItsResult(?array $node): bool
    {
        if ($node === null) return false;
        return match ($node['t']) {
            'call' => $node['name'] === 'RECORD' || $node['name'] === 'LIST',
            'bin' => $node['op'] !== '??' && $node['op'] !== '???',
            'un' => true,
            default => false,
        };
    }

    /**
     * Whether evaluating $node might write into a value: it holds an assignment or
     * calls an application's function (Registry::mayHaveEffects: anything outside
     * the manifest, however installed). A collector copies an element when it
     * collects it (spec §3.4); while nothing below the body can write, deferring
     * the copy to the end is unobservable, so only a body that might write copies
     * at collection. Iterative: a body can be a flat chain as long as the source.
     *
     * @param array<string,mixed>|null $node
     */
    public static function mayWrite(?array $node): bool
    {
        $stack = [$node];
        while ($stack !== []) {
            $n = array_pop($stack);
            if (!is_array($n)) continue;
            $t = $n['t'] ?? null;
            if ($t === 'assign') return true;
            if ($t === 'call' && Registry::mayHaveEffects((string) ($n['name'] ?? ''))) return true;
            foreach (Ast::children($n) as $child) $stack[] = $child;
        }
        return false;
    }

    /** Does the tree read the variable NAME anywhere? @param array<string,mixed>|null $node */
    public static function containsVar(?array $node, string $name): bool
    {
        if ($node === null) return false;
        if (($node['t'] ?? null) === 'var') {
            return Utf8::casecmp((string) ($node['name'] ?? ''), $name) === 0;
        }
        foreach (Ast::children($node) as $child) {
            if (self::containsVar($child, $name)) return true;
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
                // MAP collects what its body returns (spec §3.4): a copy, unless
                // nothing can write, or the body built the value and nothing else
                // holds it -- then only the copy's depth check is made.
                if ($ctx->writeFree || self::buildsItsResult(self::shape($a)['body'])) {
                    self::walk($a, $ctx, static function (Value $r) use (&$out, $pos): ?Value {
                        $out[] = $r->checkDepthBelow(1, $pos);
                        return null;
                    });
                } else {
                    self::walk($a, $ctx, static function (Value $r) use (&$out, $pos): ?Value {
                        $out[] = $r->copyBelow(1, $pos);
                        return null;
                    });
                }
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
                // An early test holds only while nothing can change what it
                // read before this FILTER reads it (spec §7.4): a predicate
                // that may write -- an assignment, or a call SEL does not
                // ship -- is offered to no join, and the conjuncts handed
                // from above stop here too.
                if ($src !== null && $src['t'] === 'call' && in_array($src['name'], ['LINK', 'LINK_LEFT'], true)
                        && ($ctx->writeFree || !self::mayWrite(self::shape($a)['body']))) {
                    ['binder' => $binder, 'body' => $body] = self::shape($a);
                    $own = Structure::leadingFieldConjuncts($body, $binder);
                    // A first conjunct that is neither a field test nor total
                    // ends every walk before it starts: hand nothing, gather
                    // nothing. A stage is [binder, conjuncts, how many joins
                    // lie between its FILTER and the join testing it].
                    $blocked = $own !== [] && $own[0]['fields'] === null && $own[0]['total'] === null;
                    $stages = $blocked ? [] : [[$binder, $own, 0]];
                    if ($handed !== null && !$blocked) foreach ($handed[0] as $stage) $stages[] = $stage;
                    // A body that reads _K observes the keys itself, whatever
                    // the step after it does: rows dropped below must keep
                    // their positions then.
                    $deep = ($handed === null ? !empty($body['keysUnobserved']) : true) && !self::containsVar($body, '_K');
                    if ($stages !== []) {
                        $ctx->joinPrefilter = [$stages, $deep, $handed === null ? [] : $handed[2], $handed === null ? [] : $handed[3]];
                    } elseif ($handed === null && $src['name'] === 'LINK_LEFT' && $own !== []) {
                        // A predicate that opens with IS_NULL of a right member's
                        // field (S6's unsold products): the join may reject the
                        // right rows it is FALSE on before building their joined
                        // rows. Nothing is reported back -- the null-extended rows
                        // were never tested -- so the whole predicate still runs
                        // over every row the join builds.
                        $test = self::rightNullTest($own[0]['node'], $binder);
                        if ($test !== null) $ctx->joinRightNull = [$test[0], $test[1], $deep];
                    }
                }
                try {
                    $source = $a->val(0);
                } finally {
                    $ctx->joinPrefilter = null;
                    $ctx->joinRightNull = null;
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
                        if (!isset($report[0][$entry['id']])) $rest[] = $entry['node'];
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
                // Nothing in the program can write, or nothing can between here
                // and the next step, which copies what it keeps (`borrowRows`,
                // stamped by the physical optimiser): a kept element is handed
                // on as it is, after the depth check its copy would have made.
                $hold = $ctx->writeFree || !empty(self::shape($a)['body']['borrowRows']);
                self::walk($a, $ctx, static function (Value $r, string|int $key, Value $item, array $body) use (
                    &$storage, &$keys, &$needsCustomKeys, &$expectedIndex, $pos, $hold
                ): ?Value {
                    if ($r->asBool($body['pos'])) {
                        $storage[] = $hold ? $item->checkDepthBelow(1, $pos) : $item->copyBelow(1, $pos);
                        // The key as written, not (int) of it: "1x", "01" and
                        // " 1" all cast to 1.
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
                // A native running total while every step fits; the answer
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
                if ($bytes > Limits::MAX_TEXT_LEN) {
                    $cps = Utf8::length($sep) * max(0, count($parts) - 1);
                    foreach ($parts as $part) $cps += Utf8::length($part);
                    Budget::checkText($cps, $a->pos, 'the JOIN result');
                }
                return Value::text(implode($sep, $parts));
            }]);
    }
}
