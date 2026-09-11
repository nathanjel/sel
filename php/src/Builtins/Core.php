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
            'arityError' => static fn (int $n): ?string => $n % 2 === 0
                ? 'COND takes condition/result pairs and a final default '
                    . "(an odd number of arguments), got {$n}"
                : null,
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
            'arityError' => static fn (int $count): ?string =>
                $count % 2 !== 0 ? "RECORD takes an even number of arguments (key-value pairs), got {$count}" : null,
            'fn' => static function (Args $a): Value {
                $rec = Value::none();
                $n = $a->count();
                for ($i = 0; $i < $n; $i += 2) {
                    $key = $a->text($i);
                    $val = $a->val($i + 1);
                    $rec->set($key, $val->copy());
                }
                return $rec;
            }]);

        Registry::define(['name' => 'TAKE', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                $count = $a->nonNegInt(1);
                if ($count === 0 || $val->isNull()) {
                    return Value::list([]);
                }
                $entries = self::elements($val);
                $out = [];
                $limit = min($count, count($entries));
                for ($i = 0; $i < $limit; $i++) {
                    $out[] = $entries[$i][1]->copy();
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'DROP', 'min' => 2, 'max' => 2,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                $count = $a->nonNegInt(1);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                $entries = self::elements($val);
                $total = count($entries);
                if ($count >= $total) {
                    return Value::list([]);
                }
                $out = [];
                for ($i = $count; $i < $total; $i++) {
                    $out[] = $entries[$i][1]->copy();
                }
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
                $entries = self::elements($val);
                $out = [];
                foreach ($entries as [, $row]) {
                    $newRow = Value::none();
                    foreach ($cols as $c) {
                        if ($row->has($c)) {
                            $newRow->set($c, $row->get($c)->copy());
                        }
                    }
                    $out[] = $newRow;
                }
                return Value::list($out);
            }]);

        Registry::define(['name' => 'DISTINCT', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $val = $a->val(0);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                $entries = self::elements($val);
                $out = [];
                foreach ($entries as [, $item]) {
                    $seen = false;
                    foreach ($out as $existing) {
                        if ($item->eql($existing)) {
                            $seen = true;
                            break;
                        }
                    }
                    if (!$seen) {
                        $out[] = $item->copy();
                    }
                }
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

        Registry::define(['name' => 'GROUP_BY', 'min' => 2, 'max' => 4, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $val = $a->val(0);
                if ($val->isNull()) {
                    return Value::list([]);
                }
                $entries = self::elements($val);
                if ($entries === []) {
                    return Value::list([]);
                }
                $count = $a->count();
                if ($count === 2) {
                    $binder = '_';
                    $keyNode = $a->node(1);
                    $aggNode = null;
                } elseif ($count === 3) {
                    $binder = '_';
                    $keyNode = $a->node(1);
                    $aggNode = $a->node(2);
                } else {
                    $binder = $a->symbol(1);
                    $keyNode = $a->node(2);
                    $aggNode = $a->node(3);
                }

                $groups = [];
                foreach ($entries as $idx => [, $item]) {
                    $ctx->pushFrame([$binder => $item, '_K' => Value::text((string) ($idx + 1))]);
                    try {
                        $kVal = $a->evalNode($keyNode);
                    } finally {
                        $ctx->popFrame();
                    }

                    $found = -1;
                    foreach ($groups as $gIdx => $g) {
                        if ($g['key']->eql($kVal)) {
                            $found = $gIdx;
                            break;
                        }
                    }
                    if ($found >= 0) {
                        $groups[$found]['rows'][] = $item->copy();
                    } else {
                        $keyStr = match ($kVal->kind) {
                            Value::TEXT => (string) $kVal->scalar,
                            Value::BOOL => $kVal->scalar ? 'TRUE' : 'FALSE',
                            Value::NONE => '',
                            default => $kVal->looksNumeric() ? (string) $kVal->scalar : '',
                        };
                        $groups[] = [
                            'key' => $kVal->copy(),
                            'keyStr' => $keyStr,
                            'rows' => [$item->copy()],
                        ];
                    }
                }

                if ($aggNode === null) {
                    $out = Value::none();
                    foreach ($groups as $g) {
                        $out->set($g['keyStr'], Value::list($g['rows']));
                    }
                    return $out;
                }

                $out = [];
                foreach ($groups as $g) {
                    $ctx->pushFrame([$binder => Value::list($g['rows']), '_K' => $g['key']->copy()]);
                    try {
                        $res = $a->evalNode($aggNode);
                        $out[] = $res->copy();
                    } finally {
                        $ctx->popFrame();
                    }
                }
                return Value::list($out);
            }]);

        self::registerAggregates();
    }

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
            $av = (int) (bool) $a->scalar;
            $bv = (int) (bool) $b->scalar;
            return $av <=> $bv;
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
        $entries = self::elements($val);
        if ($entries === []) {
            return Value::list([]);
        }

        $count = $a->count();
        if ($count === 1) {
            $dir = $forcedDir ?? 'ASC';
            $indexed = [];
            foreach ($entries as $idx => [, $item]) {
                $indexed[] = ['item' => $item, 'key' => $item, 'idx' => $idx];
            }
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
            foreach ($entries as $idx => [$k, $item]) {
                $ctx->pushFrame([$binder => $item, '_K' => Value::text($k)]);
                try {
                    $evalKey = $a->evalNode($body);
                } finally {
                    $ctx->popFrame();
                }
                $indexed[] = ['item' => $item, 'key' => $evalKey, 'idx' => $idx];
            }
        }

        usort($indexed, static function (array $x, array $y) use ($dir): int {
            $c = self::compareValues($x['key'], $y['key']);
            if ($dir === 'DESC') {
                $c = -$c;
            }
            return $c !== 0 ? $c : ($x['idx'] <=> $y['idx']);
        });

        $out = array_map(static fn (array $x) => $x['item']->copy(), $indexed);
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
    private static function walk(Args $a, Context $ctx, callable $visit): ?Value
    {
        ['binder' => $binder, 'body' => $body] = self::shape($a);
        foreach (self::elements($a->val(0)) as [$key, $item]) {
            $ctx->pushFrame([$binder => $item, '_K' => Value::text($key)]);
            try {
                $result = $visit($a->evalNode($body), $key, $item, $body);
            } finally {
                $ctx->popFrame();
            }
            if ($result !== null) {
                return $result;
            }
        }
        return null;
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
                    $out[] = $r->copy();
                    return null;
                });
                return Value::list($out);
            }]);

        // The one aggregate that preserves keys — a filtered list should still be
        // addressable the way the original was.
        Registry::define(['name' => 'FILTER', 'min' => 2, 'max' => 3, 'lazy' => true, 'binds' => true,
            'fn' => static function (Args $a, Context $ctx): Value {
                $out = Value::none();
                $out->isList = true;
                self::walk($a, $ctx, static function (Value $r, string $key, Value $item, array $body) use ($out): ?Value {
                    if ($r->asBool($body['pos'])) {
                        $out->set($key, $item->copy());
                    }
                    return null;
                });
                return $out;
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
                foreach (self::elements($a->val(0)) as [, $item]) {
                    $parts[] = $item->asText($a->posOf(0));
                }
                return Value::text(implode($sep, $parts));
            }]);
    }
}
