<?php
// Optimised relational and structural built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\Context;
use Sel\Dec;
use Sel\RecordShape;
use Sel\Registry;
use Sel\Value;

use function Sel\fail;

final class Structure
{
    public static function register(): void
    {
        Registry::define(['name' => 'DEDUPE', 'min' => 1, 'max' => 1,
            'fn' => static function (Args $a): Value {
                $value = $a->val(0);
                if ($value->isNull()) {
                    return Value::list([]);
                }
                $buckets = [];
                $out = [];
                self::forEachElement($value, static function (string $key, Value $item) use (&$buckets, &$out): void {
                    $hash = $item->structuralHash();
                    $found = false;
                    foreach ($buckets[$hash] ?? [] as $existing) {
                        if ($item->eql($existing)) {
                            $found = true;
                            break;
                        }
                    }
                    if (!$found) {
                        $buckets[$hash][] = $item;
                        $out[] = $item;
                    }
                });
                return Value::list($out);
            }]);

        Registry::define(['name' => 'TOP', 'min' => 2, 'max' => 4, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doTop($a, $ctx, 'ASC')]);
        Registry::define(['name' => 'TOP_DESC', 'min' => 2, 'max' => 4, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doTop($a, $ctx, 'DESC')]);
        Registry::define(['name' => 'TOP_BY', 'min' => 3, 'max' => 5, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doTop($a, $ctx, null)]);

        Registry::define(['name' => 'BUCKET', 'min' => 2, 'max' => 4, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doBucket($a, $ctx)]);

        // Three or five arguments, refused at compile time like every E_ARITY
        // (spec §7.4); the rule is spec/builtins.json's and the registry installs it.
        Registry::define(['name' => 'LINK', 'min' => 3, 'max' => 5, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doLink($a, $ctx, false)]);
        Registry::define(['name' => 'LINK_LEFT', 'min' => 3, 'max' => 5, 'lazy' => true, 'binds' => true,
            'fn' => static fn (Args $a, Context $ctx): Value => self::doLink($a, $ctx, true)]);
    }

    /** @param callable(string,Value):void $callback */
    private static function forEachElement(Value $value, callable $callback): void
    {
        if ($value->isNull()) return;
        if ($value->isList && $value->storage !== null) {
            if ($value->listKeys !== null) {
                foreach ($value->storage as $i => $item) {
                    $callback($value->listKeys[$i], $item);
                }
            } else {
                foreach ($value->storage as $i => $item) {
                    $callback((string) ($i + 1), $item);
                }
            }
            return;
        }
        if ($value->shape !== null && $value->storage !== null) {
            foreach ($value->shape->keys as $i => $key) {
                $callback($key, $value->storage[$i]);
            }
            return;
        }
        if ($value->size() > 0) {
            foreach ($value->children as $key => $item) {
                $callback((string) $key, $item);
            }
            return;
        }
        if ($value->kind !== Value::NONE) $callback('1', $value);
    }

    private static function firstCollectionItem(Value $value): ?Value
    {
        if ($value->isNull() || ($value->kind === Value::NONE && $value->size() === 0)) {
            return null;
        }
        if ($value->isList && $value->storage !== null && $value->storage !== []) {
            return $value->storage[0];
        }
        if ($value->shape !== null && $value->storage !== null && $value->storage !== []) {
            return $value->storage[0];
        }
        if ($value->size() > 0) {
            foreach ($value->children as $item) return $item;
        }
        return $value;
    }

    /** @param array<string,mixed> $node @param array<string,bool> $allowed */
    private static function exprDependsOnlyOn(?array $node, array $allowed): bool
    {
        if ($node === null) return true;
        return match ($node['t']) {
            'var' => isset($allowed[strtoupper($node['name'])]),
            'index' => self::exprDependsOnlyOn($node['obj'], $allowed)
                && self::exprDependsOnlyOn($node['idx'], $allowed),
            'call' => self::allNodes($node['args'], $allowed),
            'bin' => self::exprDependsOnlyOn($node['l'], $allowed)
                && self::exprDependsOnlyOn($node['r'], $allowed),
            'un' => self::exprDependsOnlyOn($node['x'], $allowed),
            'assign' => self::exprDependsOnlyOn($node['target'], $allowed)
                && self::exprDependsOnlyOn($node['value'], $allowed),
            'seq', 'list' => self::allNodes($node['items'], $allowed),
            default => true,
        };
    }

    /** @param list<array<string,mixed>> $nodes @param array<string,bool> $allowed */
    private static function allNodes(array $nodes, array $allowed): bool
    {
        foreach ($nodes as $node) {
            if (!self::exprDependsOnlyOn($node, $allowed)) return false;
        }
        return true;
    }

    /** @return array{left:array<string,mixed>,right:array<string,mixed>,numeric:bool}|null */
    private static function tryExtractEquiKeys(?array $node, string $b1, string $b2): ?array
    {
        if ($node === null || $node['t'] !== 'bin' || !in_array($node['op'], ['==', '$=='], true)) {
            return null;
        }
        $leftNames = array_fill_keys(array_map('strtoupper', [$b1, strtolower($b1), '_1', '_']), true);
        $rightNames = array_fill_keys(array_map('strtoupper', [$b2, strtolower($b2), '_2']), true);
        if (self::exprDependsOnlyOn($node['l'], $leftNames)
            && self::exprDependsOnlyOn($node['r'], $rightNames)) {
            return ['left' => $node['l'], 'right' => $node['r'], 'numeric' => $node['op'] === '=='];
        }
        if (self::exprDependsOnlyOn($node['r'], $leftNames)
            && self::exprDependsOnlyOn($node['l'], $rightNames)) {
            return ['left' => $node['r'], 'right' => $node['l'], 'numeric' => $node['op'] === '=='];
        }
        return null;
    }

    /** @param array<string,mixed> $node */
    private static function singleRelationName(?array $node): ?string
    {
        if ($node === null) return null;
        if ($node['t'] === 'var') return $node['name'];
        if ($node['t'] === 'call' && $node['args'] !== []
            && !in_array($node['name'], ['LINK', 'LINK_LEFT'], true)) {
            return self::singleRelationName($node['args'][0]);
        }
        return null;
    }

    /**
     * One key per number, as `==` compares it (spec §7.4): trailing fraction
     * zeros and a negative zero are representation, not value. Integers that
     * fit a native int are the int (the text shortcut in canonicalJoinKey
     * agrees), anything else the canonical decimal text.
     *
     * @param array{neg:bool,digits:string,scale:int} $dec
     */
    private static function canonicalDecimalKey(array $dec): int|string
    {
        $scale = $dec['scale'];
        $digits = $dec['digits'];
        $neg = $dec['neg'];
        while ($scale > 0 && str_ends_with($digits, '0')) {
            $digits = substr($digits, 0, -1);
            $scale--;
        }
        if ($scale === 0) {
            if ($digits === '0' || $digits === '') return 0;
            $len = strlen($digits);
            if ($len < 19) {
                $int = (int) $digits;
                return $neg ? -$int : $int;
            }
            return ($neg ? '-' : '') . $digits;
        }
        $sign = $neg ? '-' : '';
        $len = strlen($digits);
        if ($len <= $scale) {
            $padded = str_repeat('0', $scale - $len + 1) . $digits;
            $cut = strlen($padded) - $scale;
            return $sign . substr($padded, 0, $cut) . '.' . substr($padded, $cut);
        }
        $cut = $len - $scale;
        return $sign . substr($digits, 0, $cut) . '.' . substr($digits, $cut);
    }

    private static function canonicalJoinKey(Value $value, bool $numeric): int|string|null
    {
        if ($value->isNull()) return null;
        if ($numeric) {
            $v = $value->kind !== Value::NONE ? $value : null;
            if ($v === null) {
                try {
                    $v = $value->scalarSource();
                } catch (\Throwable) {
                    return null;
                }
            }
            if ($v->kind !== Value::TEXT) {
                return null;
            }
            if ($v->decVal !== null) {
                return self::canonicalDecimalKey($v->decVal);
            }
            $scalar = $v->getScalar();
            if (is_string($scalar)) {
                $len = strlen($scalar);
                if ($len > 0) {
                    $first = $scalar[0];
                    if ($first === '-') {
                        if ($len > 1 && ctype_digit(substr($scalar, 1)) && ($len === 2 || $scalar[1] !== '0')) {
                            if ($len < 20) {
                                return -(int) substr($scalar, 1);
                            }
                            return $scalar;
                        }
                    } elseif (ctype_digit($scalar) && ($len === 1 || $first !== '0')) {
                        if ($len < 19) {
                            return (int) $scalar;
                        }
                        return $scalar;
                    }
                }
            }
            // The same key whichever path built it: a text parsed here and a
            // decimal cached earlier must hash alike, or a join answers
            // differently before and after the text is parsed elsewhere.
            try {
                return self::canonicalDecimalKey($v->asDecimal());
            } catch (\Throwable) {
                return null;
            }
        }
        if ($value->kind === Value::TEXT) {
            // getScalar() formats a cached decimal on its first call, so one
            // read is the whole contract; a null here means no scalar at all.
            $s = $value->getScalar();
            return is_string($s) ? $s : null;
        }
        return null;
    }

    /**
     * @param array<string,mixed> $expr
     * @param list<string> $allowedBinders
     */
    private static function compileEquiKeyExtractor(array $expr, array $allowedBinders, bool $numeric, ?Value $sample): ?callable
    {
        $allowed = array_fill_keys(array_map('strtoupper', $allowedBinders), true);

        // Case 1: _['field'] or _2['field'] or BINDER['field']
        if (($expr['t'] ?? null) === 'index'
            && ($expr['obj']['t'] ?? null) === 'var'
            && isset($allowed[strtoupper((string) $expr['obj']['name'])])
            && ($expr['idx']['t'] ?? null) === 'text') {
            $keyName = (string) $expr['idx']['v'];
            $pos = $expr['pos'];
            if ($sample !== null && $sample->shape !== null && isset($sample->shape->keyMap[$keyName])) {
                $slot = $sample->shape->keyMap[$keyName];
                $shape = $sample->shape;
                return static function (Value $row) use ($slot, $shape, $keyName, $numeric, $pos): int|string|null {
                    $val = ($row->shape === $shape && $row->storage !== null)
                        ? ($row->storage[$slot] ?? null)
                        : $row->get($keyName);
                    if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($keyName), $pos);
                    return self::canonicalJoinKey($val, $numeric);
                };
            }
            return static function (Value $row) use ($keyName, $numeric, $pos): int|string|null {
                $val = $row->get($keyName);
                if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($keyName), $pos);
                return self::canonicalJoinKey($val, $numeric);
            };
        }

        // Case 2: _['table']['field']
        if (($expr['t'] ?? null) === 'index'
            && ($expr['obj']['t'] ?? null) === 'index'
            && ($expr['obj']['obj']['t'] ?? null) === 'var'
            && isset($allowed[strtoupper((string) $expr['obj']['obj']['name'])])
            && ($expr['obj']['idx']['t'] ?? null) === 'text'
            && ($expr['idx']['t'] ?? null) === 'text') {
            $tableName = (string) $expr['obj']['idx']['v'];
            $fieldName = (string) $expr['idx']['v'];
            $tablePos = $expr['obj']['pos'];
            $fieldPos = $expr['pos'];
            if ($sample !== null && $sample->shape !== null && isset($sample->shape->keyMap[$tableName])) {
                $tableSlot = $sample->shape->keyMap[$tableName];
                $tableShape = $sample->shape;
                $subSample = $sample->storage[$tableSlot] ?? null;
                if ($subSample instanceof Value && $subSample->shape !== null && isset($subSample->shape->keyMap[$fieldName])) {
                    $fieldSlot = $subSample->shape->keyMap[$fieldName];
                    $subShape = $subSample->shape;
                    return static function (Value $row) use ($tableSlot, $tableShape, $fieldSlot, $subShape, $tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|null {
                        if ($row->shape === $tableShape && $row->storage !== null) {
                            $sub = $row->storage[$tableSlot] ?? null;
                            if ($sub !== null && $sub->shape === $subShape && $sub->storage !== null) {
                                $val = $sub->storage[$fieldSlot] ?? null;
                                if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                                return self::canonicalJoinKey($val, $numeric);
                            }
                        }
                        $sub = $row->get($tableName);
                        if ($sub === null) fail('E_NO_KEY', 'no key ' . json_encode($tableName), $tablePos);
                        $val = $sub->get($fieldName);
                        if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                        return self::canonicalJoinKey($val, $numeric);
                    };
                }
                return static function (Value $row) use ($tableSlot, $tableShape, $tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|null {
                    $sub = ($row->shape === $tableShape && $row->storage !== null)
                        ? ($row->storage[$tableSlot] ?? null)
                        : $row->get($tableName);
                    if ($sub === null) fail('E_NO_KEY', 'no key ' . json_encode($tableName), $tablePos);
                    $val = $sub->get($fieldName);
                    if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                    return self::canonicalJoinKey($val, $numeric);
                };
            }
            return static function (Value $row) use ($tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|null {
                $sub = $row->get($tableName);
                if ($sub === null) fail('E_NO_KEY', 'no key ' . json_encode($tableName), $tablePos);
                $val = $sub->get($fieldName);
                if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                return self::canonicalJoinKey($val, $numeric);
            };
        }

        // Case 3: Var reference, e.g. _
        if (($expr['t'] ?? null) === 'var' && isset($allowed[strtoupper((string) $expr['name'])])) {
            return static fn (Value $row): int|string|null => self::canonicalJoinKey($row, $numeric);
        }

        return null;
    }

    /** `_1` and `_2` name a position, not a relation: an argument with no name is bound bare (spec §7.4). */
    private static function isPositionalBinder(string $name): bool
    {
        return $name === '_1' || $name === '_2';
    }

    private static function compileRowTableAliaser(string $tableName, ?Value $sample): callable
    {
        if ($tableName === '' || self::isPositionalBinder($tableName) || ($sample !== null && $sample->has($tableName))) {
            return static fn (Value $row): Value => $row;
        }
        if ($sample !== null && $sample->shape !== null) {
            $sampleShape = $sample->shape;
            $cached = $sampleShape->alias($tableName);
            $targetShape = $cached['shape'];
            $addLower = $cached['addLower'];
            return static function (Value $row) use ($sampleShape, $targetShape, $addLower, $tableName): Value {
                if ($row->shape === $sampleShape && $row->storage !== null) {
                    $storage = $row->storage;
                    $storage[] = $row;
                    if ($addLower) $storage[] = $row;
                    return Value::fromShape($targetShape, $storage);
                }
                return self::ensureRowTableAlias($row, $tableName);
            };
        }
        return static fn (Value $row): Value => self::ensureRowTableAlias($row, $tableName);
    }

    private static function ensureRowTableAlias(Value $row, string $tableName): Value
    {
        if ($tableName === '' || self::isPositionalBinder($tableName) || $row->has($tableName)) return $row;
        $lower = strtolower($tableName);
        if ($row->shape !== null) {
            // The shape owns the interned aliased shape. Assigning the old
            // packed storage to a local and appending lets PHP's COW make one
            // required copy, while array_slice would eagerly copy it first.
            $cached = $row->shape->alias($tableName);
            $storage = $row->storage ?? [];
            $storage[] = $row;
            if ($cached['addLower']) $storage[] = $row;
            return Value::fromShape($cached['shape'], $storage);
        }
        // The irregular path is cold, but parallel arrays still avoid one
        // temporary two-element array for every existing field.
        $keys = $row->keys();
        $values = $row->values();
        $keys[] = $tableName;
        $values[] = $row;
        if ($lower !== $tableName && !$row->has($lower)) {
            $keys[] = $lower;
            $values[] = $row;
        }
        return Value::record($keys, $values);
    }

    private static function makeNullRecord(?Value $sample, string $tableName): Value
    {
        $keys = [];
        $values = [];
        if ($sample !== null) {
            foreach ($sample->keys() as $key) {
                $keys[] = $key;
                $values[] = Value::none();
            }
        }
        if ($tableName !== '' && !self::isPositionalBinder($tableName)) {
            $keys[] = $tableName;
            $values[] = Value::none();
            $lower = strtolower($tableName);
            if ($lower !== $tableName) {
                $keys[] = $lower;
                $values[] = Value::none();
            }
        }
        return Value::record($keys, $values);
    }

    private static function isNestedRecord(Value $value): bool
    {
        return $value->size() > 0 && !$value->isList;
    }

    private static function makeJoinedRow(
        Value $left,
        ?Value $right,
        string $b1,
        string $b2,
        array $promotedLeft,
        array $promotedRight,
        array $tableLeft,
        ?Value $nullRight,
    ): Value {
        // Each key once, where it first occurred (spec §7.4): a carried or promoted
        // key keeps its first value, a binder key holds the row this LINK bound
        // even where an earlier LINK's `_1` or a relation joined twice carried a
        // record of the same name. That is the row the compiled projector below
        // builds from the shape (binders first, then slots); first-wins for the
        // binders too kept the old `_1` on this path while the projector resolved
        // it to the new row.
        $keys = [];
        $values = [];
        $slot = [];
        $put = static function (string $key, Value $value) use (&$keys, &$values, &$slot): void {
            if (isset($slot[$key])) return;
            $slot[$key] = count($keys);
            $keys[] = $key;
            $values[] = $value;
        };
        $bind = static function (string $key, Value $value) use (&$values, &$slot, $put): void {
            if (isset($slot[$key])) $values[$slot[$key]] = $value;
            else $put($key, $value);
        };

        $leftKeys = $left->keys();
        $leftValues = $left->values();
        foreach ($leftKeys as $i => $key) {
            if (self::isNestedRecord($leftValues[$i])) $put($key, $leftValues[$i]);
        }

        $low1 = strtolower($b1);
        $bind($b1, $left);
        if ($low1 !== $b1) $bind($low1, $left);
        if ($b1 !== '_1') $bind('_1', $left);

        $actualRight = $right ?? $nullRight ?? Value::none();
        $low2 = strtolower($b2);
        $bind($b2, $actualRight);
        if ($low2 !== $b2) $bind($low2, $actualRight);
        if ($b2 !== '_2') $bind('_2', $actualRight);

        foreach ($promotedLeft as $key) {
            $value = $left->get($key);
            if ($value !== null) $put($key, $value);
        }
        if ($right !== null) {
            foreach ($promotedRight as $key) {
                $value = $right->get($key);
                if ($value !== null && !$value->isNull()) $put($key, $value);
            }
        }
        return Value::record($keys, $values);
    }

    private static function makeJoinProjector(
        ?Value $sampleLeft,
        ?Value $sampleRight,
        string $b1,
        string $b2,
        array $promotedLeft,
        array $promotedRight,
        array $tableLeft,
        ?Value $nullRight,
    ): callable {
        $leftShape = $sampleLeft?->shape;
        $rightShape = $sampleRight?->shape;
        $leftAliases = [$b1 => true, strtolower($b1) => true, '_1' => true];
        $rightAliases = [$b2 => true, strtolower($b2) => true, '_2' => true];

        // Compile two plans: matched rows use the real right shape, while a
        // LINK_LEFT miss uses the null-right shape and intentionally omits
        // promoted right columns, just like makeJoinedRow(). The integer action
        // codes keep the hot path free of key membership scans and nested action
        // arrays. A shape mismatch falls back for heterogeneous input rows.
        $compile = static function (?Value $joined, ?RecordShape $sourceLeft, ?RecordShape $sourceRight)
            use ($leftAliases, $rightAliases, $tableLeft, $promotedLeft, $promotedRight): ?array {
            if ($joined === null || $joined->shape === null) return null;
            $leftKeys = array_fill_keys([...$tableLeft, ...$promotedLeft], true);
            $rightKeys = array_fill_keys($promotedRight, true);
            $actions = [];
            $slots = [];
            foreach ($joined->shape->keys as $i => $key) {
                if (isset($leftAliases[$key])) {
                    $actions[$i] = 0; // source left record
                    $slots[$i] = null;
                } elseif (isset($rightAliases[$key])) {
                    $actions[$i] = 1; // source right record
                    $slots[$i] = null;
                } elseif (isset($leftKeys[$key])) {
                    $slot = $sourceLeft?->keyMap[$key] ?? null;
                    $actions[$i] = $slot === null ? 4 : 2; // left slot / lookup
                    $slots[$i] = $slot === null ? $key : $slot;
                } elseif (isset($rightKeys[$key])) {
                    $slot = $sourceRight?->keyMap[$key] ?? null;
                    $actions[$i] = $slot === null ? 5 : 3; // right slot / lookup
                    $slots[$i] = $slot === null ? $key : $slot;
                } else {
                    $actions[$i] = 6; // defensive semantic fallback
                    $slots[$i] = null;
                }
            }
            return [
                'shape' => $joined->shape,
                'leftShape' => $sourceLeft,
                'rightShape' => $sourceRight,
                'actions' => $actions,
                'slots' => $slots,
            ];
        };

        $matchedSample = $sampleLeft !== null && $sampleRight !== null
            ? self::makeJoinedRow($sampleLeft, $sampleRight, $b1, $b2, $promotedLeft, $promotedRight, $tableLeft, $nullRight)
            : null;
        $matchedPlan = $compile($matchedSample, $leftShape, $rightShape);
        $nullSample = $sampleLeft !== null && $nullRight !== null
            ? self::makeJoinedRow($sampleLeft, null, $b1, $b2, $promotedLeft, $promotedRight, $tableLeft, $nullRight)
            : null;
        $nullPlan = $compile($nullSample, $leftShape, $nullRight?->shape);

        $makeBuilder = static function (array $plan): callable {
            $actions = $plan['actions'];
            $slots = $plan['slots'];
            $shape = $plan['shape'];
            return static function (Value $left, Value $right) use ($actions, $slots, $shape): Value {
                $storage = [];
                $leftStorage = $left->storage;
                $rightStorage = $right->storage;
                foreach ($actions as $i => $action) {
                    $slot = $slots[$i];
                    $storage[] = match ($action) {
                        0 => $left,
                        1 => $right,
                        2 => $leftStorage[$slot],
                        3 => $rightStorage[$slot],
                        4 => $left->get($slot) ?? Value::none(),
                        5 => $right->get($slot) ?? Value::none(),
                        default => Value::none(),
                    };
                }
                return Value::fromShape($shape, $storage);
            };
        };

        $matchedBuilder = $matchedPlan !== null ? $makeBuilder($matchedPlan) : null;
        $nullBuilder = $nullPlan !== null ? $makeBuilder($nullPlan) : null;
        $matchedLeftShape = $matchedPlan['leftShape'] ?? null;
        $matchedRightShape = $matchedPlan['rightShape'] ?? null;
        $nullLeftShape = $nullPlan['leftShape'] ?? null;

        if ($matchedPlan === null && $nullPlan === null) {
            return static fn (Value $left, ?Value $right): Value =>
                self::makeJoinedRow($left, $right, $b1, $b2, $promotedLeft, $promotedRight, $tableLeft, $nullRight);
        }

        return static function (Value $left, ?Value $right) use (
            $matchedBuilder, $nullBuilder, $matchedLeftShape, $matchedRightShape, $nullLeftShape,
            $nullRight, $b1, $b2, $promotedLeft, $promotedRight, $tableLeft,
        ): Value {
            if ($right !== null && $matchedBuilder !== null
                && $left->shape === $matchedLeftShape
                && $right->shape === $matchedRightShape) {
                return $matchedBuilder($left, $right);
            }
            if ($right === null && $nullBuilder !== null
                && $left->shape === $nullLeftShape) {
                return $nullBuilder($left, $nullRight ?? Value::none());
            }
            return self::makeJoinedRow($left, $right, $b1, $b2, $promotedLeft, $promotedRight, $tableLeft, $nullRight);
        };
    }

    private static function doLink(Args $a, Context $ctx, bool $leftJoin): Value
    {
        $count = $a->count();
        if ($count !== 3 && $count !== 5) {
            fail('E_ARITY', "{$a->name} takes 3 or 5 arguments, got {$count}", $a->pos);
        }
        $leftValue = $a->val(0);
        $rightValue = $a->val(1);
        if ($count === 3) {
            $b1 = self::singleRelationName($a->node(0)) ?? '_1';
            $b2 = self::singleRelationName($a->node(1)) ?? '_2';
            $predicate = $a->node(2);
        } else {
            $b1 = $a->symbol(2);
            $b2 = $a->symbol(3);
            $predicate = $a->node(4);
        }
        if ($leftValue->isNull()) return Value::list([]);

        $firstLeft = self::firstCollectionItem($leftValue);
        $firstRight = self::firstCollectionItem($rightValue);
        if ($firstLeft === null || $firstRight === null) {
            if (!$leftJoin || $firstLeft === null) return Value::list([]);
        }
        $sampleLeft = $firstLeft === null ? null : self::ensureRowTableAlias($firstLeft, $b1);
        $sampleRight = $firstRight === null ? null : self::ensureRowTableAlias($firstRight, $b2);
        $aliasLeft = self::compileRowTableAliaser($b1, $firstLeft);
        $aliasRight = self::compileRowTableAliaser($b2, $firstRight);
        $nullRight = $leftJoin ? self::makeNullRecord($sampleRight, $b2) : null;
        $leftKeys = $sampleLeft?->keys() ?? [];
        $rightKeys = $sampleRight?->keys() ?? [];
        $rightKeySet = array_fill_keys(array_map('strtoupper', $rightKeys), true);
        $leftKeySet = array_fill_keys(array_map('strtoupper', $leftKeys), true);
        $promotedLeft = [];
        foreach ($leftKeys as $key) {
            $value = $sampleLeft->get($key);
            if ($value !== null && !self::isNestedRecord($value) && !isset($rightKeySet[strtoupper($key)])) {
                $promotedLeft[] = $key;
            }
        }
        $promotedRight = [];
        foreach ($rightKeys as $key) {
            $value = $sampleRight->get($key);
            if ($value !== null && !self::isNestedRecord($value) && !isset($leftKeySet[strtoupper($key)])) {
                $promotedRight[] = $key;
            }
        }
        $tableLeft = [];
        foreach ($leftKeys as $key) {
            $value = $sampleLeft->get($key);
            if ($value !== null && self::isNestedRecord($value)) $tableLeft[] = $key;
        }
        $project = self::makeJoinProjector($sampleLeft, $sampleRight, $b1, $b2,
            $promotedLeft, $promotedRight, $tableLeft, $nullRight);
        $equi = self::tryExtractEquiKeys($predicate, $b1, $b2);
        $output = [];
        $each = static function (Value $value, callable $callback): void {
            if ($value->isList && $value->storage !== null) {
                foreach ($value->storage as $item) $callback($item);
                return;
            }
            if ($value->shape !== null && $value->storage !== null) {
                foreach ($value->storage as $item) $callback($item);
                return;
            }
            if ($value->size() > 0) {
                foreach ($value->children as $item) $callback($item);
                return;
            }
            if ($value->kind !== Value::NONE) $callback($value);
        };

        if ($equi !== null && $sampleRight !== null) {
            $buckets = [];
            $rightAllowed = [$b2, strtolower($b2), '_2'];
            $rightExtractor = self::compileEquiKeyExtractor($equi['right'], $rightAllowed, $equi['numeric'], $sampleRight);
            if ($rightExtractor !== null) {
                if ($rightValue->isList && $rightValue->storage !== null) {
                    foreach ($rightValue->storage as $item) {
                        $row = $aliasRight($item);
                        $key = $rightExtractor($row);
                        if ($key !== null) $buckets[$key][] = $row;
                    }
                } else {
                    $each($rightValue, static function (Value $item) use (&$buckets, $aliasRight, $rightExtractor): void {
                        $row = $aliasRight($item);
                        $key = $rightExtractor($row);
                        if ($key !== null) $buckets[$key][] = $row;
                    });
                }
            } else {
                $b2Lower = strtolower($b2);
                $hasLower2 = $b2Lower !== $b2;
                $frameRight = [$b2 => Value::none(), '_2' => Value::none()];
                if ($hasLower2) $frameRight[$b2Lower] = Value::none();
                $ctx->pushFrame($frameRight);
                try {
                    $each($rightValue, function (Value $item) use (&$buckets, $b2, $b2Lower, $hasLower2, $aliasRight, $equi, $a, $ctx): void {
                        $row = $aliasRight($item);
                        $ctx->setFrameValue($b2, $row);
                        if ($hasLower2) $ctx->setFrameValue($b2Lower, $row);
                        $ctx->setFrameValue('_2', $row);
                        $key = self::canonicalJoinKey($a->evalNode($equi['right']), $equi['numeric']);
                        if ($key !== null) $buckets[$key][] = $row;
                    });
                } finally {
                    $ctx->popFrame();
                }
            }

            $leftAllowed = [$b1, strtolower($b1), '_1', '_'];
            $leftExtractor = self::compileEquiKeyExtractor($equi['left'], $leftAllowed, $equi['numeric'], $sampleLeft);
            if ($leftExtractor !== null) {
                if ($leftValue->isList && $leftValue->storage !== null) {
                    foreach ($leftValue->storage as $item) {
                        $row = $aliasLeft($item);
                        $key = $leftExtractor($row);
                        $matches = $key === null ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            foreach ($matches as $right) $output[] = $project($row, $right);
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                        }
                    }
                } else {
                    $each($leftValue, static function (Value $item) use (&$buckets, &$output, $aliasLeft, $leftExtractor, $leftJoin, $project): void {
                        $row = $aliasLeft($item);
                        $key = $leftExtractor($row);
                        $matches = $key === null ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            foreach ($matches as $right) $output[] = $project($row, $right);
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                        }
                    });
                }
            } else {
                $b1Lower = strtolower($b1);
                $hasLower1 = $b1Lower !== $b1;
                $frameLeft = [$b1 => Value::none(), '_1' => Value::none(), '_' => Value::none()];
                if ($hasLower1) $frameLeft[$b1Lower] = Value::none();
                $ctx->pushFrame($frameLeft);
                try {
                    $each($leftValue, function (Value $item) use (&$buckets, &$output, $b1, $b1Lower, $hasLower1, $aliasLeft, $equi, $a, $leftJoin, $project, $ctx): void {
                        $row = $aliasLeft($item);
                        $ctx->setFrameValue($b1, $row);
                        if ($hasLower1) $ctx->setFrameValue($b1Lower, $row);
                        $ctx->setFrameValue('_1', $row);
                        $ctx->setFrameValue('_', $row);
                        $key = self::canonicalJoinKey($a->evalNode($equi['left']), $equi['numeric']);
                        $matches = $key === null ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            foreach ($matches as $right) $output[] = $project($row, $right);
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                        }
                    });
                } finally {
                    $ctx->popFrame();
                }
            }
        } else {
            $frame = [
                $b1 => Value::none(), strtolower($b1) => Value::none(), '_1' => Value::none(), '_' => Value::none(),
                $b2 => Value::none(), strtolower($b2) => Value::none(), '_2' => Value::none(),
            ];
            $ctx->pushFrame($frame);
            try {
                $each($leftValue, function (Value $leftItem) use (&$frame, &$output, $b1, $b2, $rightValue, $aliasLeft, $aliasRight, $a, $predicate, $leftJoin, $project, $ctx): void {
                    $left = $aliasLeft($leftItem);
                        $frame[$b1] = $left;
                    $frame[strtolower($b1)] = $left;
                        $frame['_1'] = $left;
                        $frame['_'] = $left;
                        $ctx->setFrameValue($b1, $left);
                        $ctx->setFrameValue(strtolower($b1), $left);
                        $ctx->setFrameValue('_1', $left);
                        $ctx->setFrameValue('_', $left);
                    $matched = false;
                    self::forEachElement($rightValue, function (string $rightKey, Value $rightItem) use (
                        &$frame, &$output, &$matched, $b1, $b2, $left, $aliasRight, $a, $predicate, $project, $ctx,
                    ): void {
                        $right = $aliasRight($rightItem);
                        $frame[$b2] = $right;
                        $frame[strtolower($b2)] = $right;
                        $frame['_2'] = $right;
                        $ctx->setFrameValue($b2, $right);
                        $ctx->setFrameValue(strtolower($b2), $right);
                        $ctx->setFrameValue('_2', $right);
                        if ($a->evalNode($predicate)->asBool($predicate['pos'])) {
                            $matched = true;
                            $output[] = $project($left, $right);
                        }
                    });
                    if ($leftJoin && !$matched) $output[] = $project($left, null);
                });
            } finally {
                $ctx->popFrame();
            }
        }
        return Value::list($output);
    }

    private static function doTop(Args $a, Context $ctx, ?string $forcedDir): Value
    {
        $value = $a->val(0);
        $limit = $a->nonNegInt($a->count() - 1);
        if ($limit === 0 || $value->isNull()) return Value::list([]);
        $sortCount = $a->count() - 1;
        $binder = '_';
        $body = null;
        $dir = $forcedDir ?? 'ASC';
        if ($sortCount === 1) {
            $binder = null;
        } elseif ($sortCount === 2) {
            $body = $a->node(1);
        } elseif ($sortCount === 3) {
            if ($forcedDir !== null) {
                $binder = $a->symbol(1);
                $body = $a->node(2);
            } elseif ($a->node(2)['t'] === 'text') {
                $body = $a->node(1);
                $dir = strtoupper($a->text(2));
            } elseif ($a->isSymbol(1)) {
                $binder = $a->symbol(1);
                $body = $a->node(2);
            } else {
                $body = $a->node(1);
                $dir = strtoupper($a->text(2));
            }
        } elseif ($sortCount === 4) {
            $binder = $a->symbol(1);
            $body = $a->node(2);
            $dir = strtoupper($a->text(3));
        } else {
            fail('E_ARITY', "{$a->name} has an invalid sort form", $a->pos);
        }
        if ($dir !== 'ASC' && $dir !== 'DESC') {
            $directionIndex = $sortCount === 4 ? 3 : 2;
            fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", $a->posOf($directionIndex));
        }

        // The one ordering SORT uses too (Core::compareValues); only the
        // bounded selection below is TOP's own.
        $compare = static function (array $left, array $right) use ($dir): int {
            $c = Core::compareValues($left['key'], $right['key']);
            if ($dir === 'DESC') $c = -$c;
            return $c !== 0 ? $c : ($left['idx'] <=> $right['idx']);
        };
        $heap = [];
        $siftUp = function (int $index) use (&$heap, $compare): void {
            while ($index > 0) {
                $parent = intdiv($index - 1, 2);
                if ($compare($heap[$index], $heap[$parent]) <= 0) break;
                [$heap[$index], $heap[$parent]] = [$heap[$parent], $heap[$index]];
                $index = $parent;
            }
        };
        $siftDown = function (int $index) use (&$heap, $compare): void {
            while (true) {
                $left = $index * 2 + 1;
                $right = $left + 1;
                $worst = $index;
                if ($left < count($heap) && $compare($heap[$left], $heap[$worst]) > 0) $worst = $left;
                if ($right < count($heap) && $compare($heap[$right], $heap[$worst]) > 0) $worst = $right;
                if ($worst === $index) return;
                [$heap[$index], $heap[$worst]] = [$heap[$worst], $heap[$index]];
                $index = $worst;
            }
        };
        $index = 0;
        $frame = $binder === null ? null : [$binder => Value::none(), '_K' => Value::none()];
        if ($frame !== null) $ctx->pushFrame($frame);
        try {
            $consume = function (string $key, Value $item) use (
                &$heap, &$index, $limit, $binder, $body, $ctx, $a, $compare, $siftUp, $siftDown, &$frame,
            ): void {
                if ($binder === null) {
                    $candidate = ['item' => $item, 'key' => $item, 'idx' => $index++];
                } else {
                    $frame[$binder] = $item;
                    $frame['_K'] = Value::text($key);
                    $ctx->setFrameValue($binder, $frame[$binder]);
                    $ctx->setFrameValue('_K', $frame['_K']);
                    $candidate = ['item' => $item, 'key' => $a->evalNode($body), 'idx' => $index++];
                }
                if (count($heap) < $limit) {
                    $heap[] = $candidate;
                    $siftUp(count($heap) - 1);
                } elseif ($compare($heap[0], $candidate) > 0) {
                    $heap[0] = $candidate;
                    $siftDown(0);
                }
            };
            if ($value->isList && $value->storage !== null) {
                if ($value->listKeys !== null) {
                    foreach ($value->storage as $i => $item) $consume($value->listKeys[$i], $item);
                } else {
                    foreach ($value->storage as $i => $item) $consume((string) ($i + 1), $item);
                }
            } else {
                self::forEachElement($value, $consume);
            }
        } finally {
            if ($frame !== null) $ctx->popFrame();
        }
        usort($heap, $compare);
        return Value::list(array_map(static fn (array $entry): Value => $entry['item'], $heap));
    }

    /** @param array{line:int,col:int,offset:int}|null $pos */
    private static function bucketKeyText(Value $key, ?array $pos): string
    {
        $v = $key;
        if ($v->kind === Value::NONE) {
            if ($v->isNull()) fail('E_NULL', 'value is NULL', $pos);
            fail('E_NOT_TEXT', 'a bucket key must be text or a number, got a list or record', $pos);
        }
        return $v->asText($pos);
    }

    private static function doBucket(Args $a, Context $ctx): Value
    {
        $value = $a->val(0);
        if ($value->isNull() || $value->size() === 0) return Value::list([]);
        $count = $a->count();
        $binder = '_';
        $keyNode = null;
        $aggregateNode = null;
        if ($count === 2) {
            $keyNode = $a->node(1);
        } elseif ($count === 3) {
            $keyNode = $a->node(1);
            $aggregateNode = $a->node(2);
        } elseif ($count === 4) {
            $binder = $a->symbol(1);
            $keyNode = $a->node(2);
            $aggregateNode = $a->node(3);
        } else {
            fail('E_ARITY', 'BUCKET takes 2, 3 or 4 arguments', $a->pos);
        }

        $groups = [];
        $buckets = [];
        $frame = [$binder => Value::none(), '_K' => Value::none()];
        $ctx->pushFrame($frame);
        try {
            $index = 0;
            self::forEachElement($value, function (string $key, Value $item) use (
                &$index, &$frame, &$groups, &$buckets, $a, $ctx, $keyNode, $aggregateNode, $binder,
            ): void {
                $frame[$binder] = $item;
                $frame['_K'] = Value::text($key === '' ? (string) (++$index) : $key);
                $ctx->setFrameValue($binder, $frame[$binder]);
                $ctx->setFrameValue('_K', $frame['_K']);
                $groupKey = $a->evalNode($keyNode);
                // A bare bucket's key is an index key (spec §3.3): the scalar,
                // verbatim, and refused the way indexing refuses it -- never
                // collapsed onto a string that stands for every list, record or
                // NULL. The projected spelling has no map to key and groups by
                // identity instead.
                $keyString = $aggregateNode === null ? self::bucketKeyText($groupKey, $keyNode['pos']) : '';
                $hash = $groupKey->structuralHash();
                $found = null;
                foreach ($buckets[$hash] ?? [] as $groupIndex) {
                    if ($groups[$groupIndex]['key']->eql($groupKey)) {
                        $found = $groupIndex;
                        break;
                    }
                }
                if ($found !== null) {
                    $groups[$found]['rows'][] = $item;
                    return;
                }
                $groupIndex = count($groups);
                $groups[] = ['key' => $groupKey, 'keyString' => $keyString, 'rows' => [$item]];
                $buckets[$hash][] = $groupIndex;
            });
        } finally {
            $ctx->popFrame();
        }

        if ($aggregateNode === null) {
            $out = Value::none();
            foreach ($groups as $group) {
                $out->set($group['keyString'], Value::list(array_map(static fn (Value $row): Value => $row->copy(), $group['rows'])));
            }
            return $out;
        }
        $out = [];
        $aggregateFrame = [$binder => Value::none(), '_K' => Value::none()];
        $ctx->pushFrame($aggregateFrame);
        try {
            foreach ($groups as $group) {
                $aggregateFrame[$binder] = Value::list($group['rows']);
                $aggregateFrame['_K'] = $group['key'];
                $ctx->setFrameValue($binder, $aggregateFrame[$binder]);
                $ctx->setFrameValue('_K', $aggregateFrame['_K']);
                $out[] = $a->evalNode($aggregateNode);
            }
        } finally {
            $ctx->popFrame();
        }
        return Value::list($out);
    }
}
