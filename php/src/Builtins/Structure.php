<?php
// Optimised relational and structural built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\BuiltinManifest;
use Sel\SelError;
use Sel\Context;
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
                $value->forEachElement(static function (string $key, Value $item) use (&$buckets, &$out): void {
                    $hash = $item->structuralHash();
                    // One item per hash is stored as the item itself and becomes a
                    // list only when a second, unequal one collides (PHP-P25).
                    $slot = $buckets[$hash] ?? null;
                    if ($slot === null) {
                        $buckets[$hash] = $item;
                        $out[] = $item;
                        return;
                    }
                    if ($slot instanceof Value) {
                        if (!$item->eql($slot)) {
                            $buckets[$hash] = [$slot, $item];
                            $out[] = $item;
                        }
                        return;
                    }
                    foreach ($slot as $existing) {
                        if ($item->eql($existing)) {
                            return;
                        }
                    }
                    $buckets[$hash][] = $item;
                    $out[] = $item;
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

    private static function firstCollectionItem(Value $value): ?Value
    {
        if ($value->kind === Value::NONE && $value->size() === 0) {
            return null;
        }
        if ($value->isList && $value->storage !== null && $value->storage !== []) {
            return $value->storage[0];
        }
        if ($value->shape !== null && $value->storage !== null && $value->storage !== []) {
            return $value->storage[0];
        }
        if ($value->size() > 0 && $value->children !== null) {
            foreach ($value->children as $item) return $item;
        }
        return $value;
    }

    /**
     * Does the expression read nothing of the LINK but the binder names in `$allowed`?
     *
     * `$forbidden` is the other side's binder names. When it is given, a variable
     * that is neither allowed nor forbidden is a constant of the join — a name
     * from the enclosing scope, whose value cannot change while the join runs,
     * because an assignment in the key is refused below — and may appear in a key
     * (PHP-P6): `A["id"] + K == B["id"]` hashes instead of falling to the nested
     * loop, which was ~1000x slower at 600 rows. Names starting with `_` (`_`,
     * `_1`, `_2`, `_K`) are the language's own and stay refused. With `$forbidden`
     * null only binder names qualify, which is what an assignment target needs.
     *
     * @param array<string,mixed> $node @param array<string,bool> $allowed @param array<string,bool>|null $forbidden
     */
    private static function exprDependsOnlyOn(?array $node, array $allowed, ?array $forbidden = null): bool
    {
        if ($node === null) return true;
        return match ($node['t']) {
            'var' => self::varAllowed($node['name'], $allowed, $forbidden),
            'index' => self::exprDependsOnlyOn($node['obj'], $allowed, $forbidden)
                && self::exprDependsOnlyOn($node['idx'], $allowed, $forbidden),
            'call' => self::allNodes($node['args'], $allowed, $forbidden),
            'bin' => self::exprDependsOnlyOn($node['l'], $allowed, $forbidden)
                && self::exprDependsOnlyOn($node['r'], $allowed, $forbidden),
            'un' => self::exprDependsOnlyOn($node['x'], $allowed, $forbidden),
            // A target must be one of the binders even when outside names are
            // welcome elsewhere: an assignment to an outer variable would make it
            // change under the join.
            'assign' => self::exprDependsOnlyOn($node['target'], $allowed)
                && self::exprDependsOnlyOn($node['value'], $allowed, $forbidden),
            'seq', 'list' => self::allNodes($node['items'], $allowed, $forbidden),
            default => true,
        };
    }

    /** @param array<string,bool> $allowed @param array<string,bool>|null $forbidden */
    private static function varAllowed(string $name, array $allowed, ?array $forbidden): bool
    {
        $upper = \Sel\Utf8::upper($name);
        if (isset($allowed[$upper])) return true;
        if ($forbidden === null || isset($forbidden[$upper])) return false;
        return !str_starts_with($name, '_');
    }

    /** @param list<array<string,mixed>> $nodes @param array<string,bool> $allowed @param array<string,bool>|null $forbidden */
    private static function allNodes(array $nodes, array $allowed, ?array $forbidden = null): bool
    {
        foreach ($nodes as $node) {
            if (!self::exprDependsOnlyOn($node, $allowed, $forbidden)) return false;
        }
        return true;
    }

    /** @return array{left:array<string,mixed>,right:array<string,mixed>,numeric:bool}|null */
    private static function tryExtractEquiKeys(?array $node, string $b1, string $b2): ?array
    {
        if ($node === null || $node['t'] !== 'bin' || !in_array($node['op'], ['==', '$=='], true)) {
            return null;
        }
        $leftNames = array_fill_keys(array_map([\Sel\Utf8::class, 'upper'], [$b1, \Sel\Utf8::lower($b1), '_1', '_']), true);
        $rightNames = array_fill_keys(array_map([\Sel\Utf8::class, 'upper'], [$b2, \Sel\Utf8::lower($b2), '_2']), true);
        // Binders spelled alike: the right one shadows the left (spec §7.4), so a
        // comparison reading that name reads ONE element per pair. Splitting it
        // into a left key and a right key would give each side its own element
        // and answer a different question, so such a comparison is not an equi
        // join and the general path decides it.
        if (array_intersect_key($leftNames, $rightNames) !== []) return null;
        if (self::exprDependsOnlyOn($node['l'], $leftNames, $rightNames)
            && self::exprDependsOnlyOn($node['r'], $rightNames, $leftNames)) {
            return ['left' => $node['l'], 'right' => $node['r'], 'numeric' => $node['op'] === '==', 'swapped' => false];
        }
        if (self::exprDependsOnlyOn($node['r'], $leftNames, $rightNames)
            && self::exprDependsOnlyOn($node['l'], $rightNames, $leftNames)) {
            return ['left' => $node['r'], 'right' => $node['l'], 'numeric' => $node['op'] === '==', 'swapped' => true];
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

    private static function canonicalJoinKey(Value $value, bool $numeric): int|string|array|null
    {
        if ($value->isNull()) return null;
        if ($numeric) {
            $v = $value->kind !== Value::NONE ? $value : null;
            if ($v === null) {
                try {
                    $v = $value->scalarSource();
                } catch (SelError) {
                    return ['bad' => $value];
                }
            }
            if ($v->kind !== Value::TEXT) {
                return ['bad' => $value];
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
                            // Past the digit cap the comparison itself raises
                            // E_RANGE (spec §6.4), so the key is a rejected one
                            // and the caller raises it at the operand.
                            if ($len - 1 > \Sel\Limits::MAX_INT_DIGITS) return ['bad' => $value];
                            return $scalar;
                        }
                    } elseif (ctype_digit($scalar) && ($len === 1 || $first !== '0')) {
                        if ($len < 19) {
                            return (int) $scalar;
                        }
                        if ($len > \Sel\Limits::MAX_INT_DIGITS) return ['bad' => $value];
                        return $scalar;
                    }
                }
            }
            // The same key whichever path built it: a text parsed here and a
            // decimal cached earlier must hash alike, or a join answers
            // differently before and after the text is parsed elsewhere.
            try {
                return self::canonicalDecimalKey($v->asDecimal());
            } catch (SelError) {
                return ['bad' => $value];
            }
        }
        // `$==` compares bytes (asBytes, as the evaluator does): a BIN meets the
        // TEXT of its bytes and a list its scalar; the prefix keeps a byte key
        // from being read as an integer array key.
        try {
            return 'b' . $value->asBytes();
        } catch (SelError) {
            return ['bad' => $value];
        }
    }

    /**
     * Record a right key: bucket a good one, and remember what the left keys
     * must be checked against -- whether any key is live (not NULL), the first
     * live key if it was rejected, and the first rejected one (spec §7.4:
     * pairs match "as the comparison would compare them"; review 2026-09-25
     * SEM-06).
     *
     * @param array<int|string, list<Value>> $buckets
     * @param array{live:bool, liveBad:?Value, bad:?Value} $facts
     */
    private static function bucketJoinKey(array &$buckets, array &$facts, int|string|array|null $key, Value $row): void
    {
        if ($key === null) return;
        if (is_array($key)) {
            if (!$facts['live']) $facts['liveBad'] = $key['bad'];
            $facts['bad'] ??= $key['bad'];
        } else {
            $buckets[$key][] = $row;
        }
        $facts['live'] = true;
    }

    /**
     * A left key meets the right keys pair by pair, in order, as the
     * comparison would: a rejected left key raises against the first live
     * right key, a good one against the first rejected right key -- the
     * operator's left operand coerced first. NULLs are never compared.
     *
     * @param array<string,mixed> $equi
     * @param array{live:bool, liveBad:?Value, bad:?Value} $facts
     */
    private static function checkJoinPair(array $equi, int|string|array|null $key, array $facts): void
    {
        if ($key === null || !$facts['live']) return;
        if (is_array($key)) {
            if ($equi['swapped'] && $facts['liveBad'] !== null) self::coerceJoinOperand($equi, $facts['liveBad'], $equi['right']);
            self::coerceJoinOperand($equi, $key['bad'], $equi['left']);
        }
        if ($facts['bad'] !== null) self::coerceJoinOperand($equi, $facts['bad'], $equi['right']);
    }

    /** @param array<string,mixed> $equi @param array<string,mixed> $node */
    private static function coerceJoinOperand(array $equi, Value $value, array $node): void
    {
        if ($equi['numeric']) $value->asDecimal($node['pos']);
        else $value->asBytes($node['pos']);
        throw new \LogicException('a rejected join key did not raise');
    }


    /**
     * @param array<string,mixed> $expr
     * @param list<string> $allowedBinders
     */
    private static function compileEquiKeyExtractor(array $expr, array $allowedBinders, bool $numeric, ?Value $sample): ?callable
    {
        $allowed = array_fill_keys(array_map([\Sel\Utf8::class, 'upper'], $allowedBinders), true);

        // Case 1: _['field'] or _2['field'] or BINDER['field']
        if (($expr['t'] ?? null) === 'index'
            && ($expr['obj']['t'] ?? null) === 'var'
            && isset($allowed[\Sel\Utf8::upper((string) $expr['obj']['name'])])
            && ($expr['idx']['t'] ?? null) === 'text') {
            $keyName = (string) $expr['idx']['v'];
            $pos = $expr['pos'];
            if ($sample !== null && $sample->shape !== null && isset($sample->shape->keyMap[$keyName])) {
                $slot = $sample->shape->keyMap[$keyName];
                $shape = $sample->shape;
                return static function (Value $row) use ($slot, $shape, $keyName, $numeric, $pos): int|string|array|null {
                    $val = ($row->shape === $shape && $row->storage !== null)
                        ? ($row->storage[$slot] ?? null)
                        : $row->get($keyName);
                    if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($keyName), $pos);
                    return self::canonicalJoinKey($val, $numeric);
                };
            }
            return static function (Value $row) use ($keyName, $numeric, $pos): int|string|array|null {
                $val = $row->get($keyName);
                if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($keyName), $pos);
                return self::canonicalJoinKey($val, $numeric);
            };
        }

        // Case 2: _['table']['field']
        if (($expr['t'] ?? null) === 'index'
            && ($expr['obj']['t'] ?? null) === 'index'
            && ($expr['obj']['obj']['t'] ?? null) === 'var'
            && isset($allowed[\Sel\Utf8::upper((string) $expr['obj']['obj']['name'])])
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
                    return static function (Value $row) use ($tableSlot, $tableShape, $fieldSlot, $subShape, $tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|array|null {
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
                return static function (Value $row) use ($tableSlot, $tableShape, $tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|array|null {
                    $sub = ($row->shape === $tableShape && $row->storage !== null)
                        ? ($row->storage[$tableSlot] ?? null)
                        : $row->get($tableName);
                    if ($sub === null) fail('E_NO_KEY', 'no key ' . json_encode($tableName), $tablePos);
                    $val = $sub->get($fieldName);
                    if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                    return self::canonicalJoinKey($val, $numeric);
                };
            }
            return static function (Value $row) use ($tableName, $fieldName, $numeric, $tablePos, $fieldPos): int|string|array|null {
                $sub = $row->get($tableName);
                if ($sub === null) fail('E_NO_KEY', 'no key ' . json_encode($tableName), $tablePos);
                $val = $sub->get($fieldName);
                if ($val === null) fail('E_NO_KEY', 'no key ' . json_encode($fieldName), $fieldPos);
                return self::canonicalJoinKey($val, $numeric);
            };
        }

        // Case 3: Var reference, e.g. _
        if (($expr['t'] ?? null) === 'var' && isset($allowed[\Sel\Utf8::upper((string) $expr['name'])])) {
            return static fn (Value $row): int|string|array|null => self::canonicalJoinKey($row, $numeric);
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
        if ($tableName === '' || self::isPositionalBinder($tableName)) {
            return static fn (Value $row): Value => $row;
        }
        // Each element is extended unless IT has the key (spec §7.4, pair by
        // pair); the shaped fast path serves rows of the sample's shape only.
        if ($sample !== null && $sample->shape !== null && !$sample->has($tableName)) {
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
        $lower = \Sel\Utf8::lower($tableName);
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

    /**
     * An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the first
     * right element as bound -- SAMPLE, already extended with the name -- every
     * field NULL; with no right elements, just the name keys; with no name
     * either, NULL.
     */
    private static function makeNullRecord(?Value $sample, string $tableName): Value
    {
        $keys = [];
        $values = [];
        $seen = [];
        if ($sample !== null) {
            foreach ($sample->keys() as $key) {
                $key = (string) $key;
                $keys[] = $key;
                $values[] = Value::none();
                $seen[$key] = true;
            }
        }
        if ($sample === null && $tableName !== '' && !self::isPositionalBinder($tableName)) {
            foreach ([$tableName, \Sel\Utf8::lower($tableName)] as $name) {
                if (isset($seen[$name])) continue;
                $keys[] = $name;
                $values[] = Value::none();
                $seen[$name] = true;
            }
        }
        return Value::record($keys, $values);
    }

    private static function isNestedRecord(Value $value): bool
    {
        return $value->size() > 0 && !$value->isList;
    }

    // A field's category for the joined row (spec §7.4): a nested record (a
    // record with a field) is carried, anything else is a scalar field -- and a
    // right scalar that is NULL is not promoted.
    private const SCALAR = 0;
    private const NULL_FIELD = 1;
    private const NESTED = 2;

    private static function category(Value $value): int
    {
        if ($value->kind !== Value::NONE || $value->isList) return self::SCALAR;
        return $value->size() > 0 ? self::NESTED : self::NULL_FIELD;
    }

    /** @return list<string> */
    private static function binderKeys(string $name, string $positional): array
    {
        $keys = [$name];
        $lower = \Sel\Utf8::lower($name);
        if ($lower !== $name) $keys[] = $lower;
        if ($name !== $positional) $keys[] = $positional;
        return $keys;
    }

    /**
     * One joined row, from THIS pair's two elements (spec §7.4): the nested
     * records the left element carries, the left binders, the right binders,
     * then the left element's scalar fields whose names (ASCII-case-
     * insensitively) are not the right element's, then the right element's
     * non-NULL scalar fields whose names are not the left's -- each key once,
     * where it first occurred, a binder key holding the row this LINK bound.
     * RIGHT is null for an unmatched LINK_LEFT row, whose right element is
     * NULLRIGHT and promotes nothing.
     */
    private static function makeJoinedRow(Value $left, ?Value $right, string $b1, string $b2, ?Value $nullRight): Value
    {
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
        $leftKeys = array_map('strval', $left->keys());
        $leftValues = $left->values();
        foreach ($leftKeys as $i => $key) {
            if (self::category($leftValues[$i]) === self::NESTED) $put($key, $leftValues[$i]);
        }
        foreach (self::binderKeys($b1, '_1') as $name) $bind($name, $left);
        $rside = $right ?? $nullRight ?? Value::none();
        foreach (self::binderKeys($b2, '_2') as $name) $bind($name, $rside);
        $rightKeys = ($rside->size() > 0 && !$rside->isList) ? array_map('strval', $rside->keys()) : [];
        $rightValues = $rightKeys === [] ? [] : $rside->values();
        $rightNames = [];
        foreach ($rightKeys as $key) $rightNames[\Sel\Utf8::upper($key)] = true;
        foreach ($leftKeys as $i => $key) {
            if (self::category($leftValues[$i]) !== self::NESTED && !isset($rightNames[\Sel\Utf8::upper($key)])) {
                $put($key, $leftValues[$i]);
            }
        }
        if ($right !== null) {
            $leftNames = [];
            foreach ($leftKeys as $key) $leftNames[\Sel\Utf8::upper($key)] = true;
            foreach ($rightKeys as $i => $key) {
                if (self::category($rightValues[$i]) === self::SCALAR && !isset($leftNames[\Sel\Utf8::upper($key)])) {
                    $put($key, $rightValues[$i]);
                }
            }
        }
        return Value::record($keys, $values);
    }

    /**
     * The joined row of a pair as a plan over the two elements' storage, for
     * every pair whose elements have these shapes and field categories: the
     * output shape and, per output key, [op, slot] -- 0 left slot, 1 right
     * slot, 2 the left element, 3 the right one. makeJoinedRow is the rule;
     * this is it, compiled.
     * @return array{shape: RecordShape, ops: list<int>, slots: list<int>}
     */
    private static function rowPlan(Value $left, Value $rside, bool $matched, string $b1, string $b2): array
    {
        $keys = [];
        $ops = [];
        $slots = [];
        $at = [];
        $put = static function (string $key, int $op, int $slot) use (&$keys, &$ops, &$slots, &$at): void {
            if (isset($at[$key])) return;
            $at[$key] = count($keys);
            $keys[] = $key;
            $ops[] = $op;
            $slots[] = $slot;
        };
        $bind = static function (string $key, int $op) use (&$ops, &$slots, &$at, $put): void {
            if (isset($at[$key])) { $ops[$at[$key]] = $op; $slots[$at[$key]] = -1; }
            else $put($key, $op, -1);
        };
        $lkeys = array_map('strval', $left->shape->keys);
        $lcat = array_map([self::class, 'category'], $left->storage);
        foreach ($lkeys as $i => $key) if ($lcat[$i] === self::NESTED) $put($key, 0, $i);
        foreach (self::binderKeys($b1, '_1') as $name) $bind($name, 2);
        foreach (self::binderKeys($b2, '_2') as $name) $bind($name, 3);
        $rkeys = $rside->shape !== null ? array_map('strval', $rside->shape->keys) : [];
        $rightNames = [];
        foreach ($rkeys as $key) $rightNames[\Sel\Utf8::upper($key)] = true;
        foreach ($lkeys as $i => $key) {
            if ($lcat[$i] !== self::NESTED && !isset($rightNames[\Sel\Utf8::upper($key)])) $put($key, 0, $i);
        }
        if ($matched) {
            $rcat = array_map([self::class, 'category'], $rside->storage);
            $leftNames = [];
            foreach ($lkeys as $key) $leftNames[\Sel\Utf8::upper($key)] = true;
            foreach ($rkeys as $i => $key) {
                if ($rcat[$i] === self::SCALAR && !isset($leftNames[\Sel\Utf8::upper($key)])) $put($key, 1, $i);
            }
        }
        return ['shape' => RecordShape::intern($keys), 'ops' => $ops, 'slots' => $slots];
    }

    // Only two facts about a field decide a joined row (spec §7.4): on the
    // left, whether it is a nested record (carried first) or not (promoted,
    // NULL or not); on the right, whether it is a non-NULL scalar (promoted).
    private static function leftNested(Value $v): bool
    {
        return $v->kind === Value::NONE && !$v->isList && $v->size() > 0;
    }

    /**
     * project(left, right): makeJoinedRow, through compiled plans. For each
     * pair of shapes the plans built so far are tried in turn; each checks the
     * two facts it assumed of the fields it reads -- every left field, and the
     * right fields whose names the left does not have -- and a pair none fits
     * gets its own plan from the rule (rowPlan). A left row's matches come one
     * after another, so the plan whose left checks it passed is tried first
     * without repeating them.
     */
    /**
     * @param array<string,mixed> $plan
     * @return array{build:\Closure, many:\Closure}
     */
    private static function compileSpecializedJoinProjector(
        array $plan,
        RecordShape $shape,
        RecordShape $rshape
    ): array {
        // The generated code is a function of (ops, slots, rkept, lnested) alone —
        // the shapes are bound at call time — so the compiled factory is cached
        // across LINK invocations (PHP-P20): an eval() costs ~60 us and this ran
        // for every invocation and shape pair. Bounded, and dropped wholesale when
        // full, like RecordShape's own cache; the key holds integers only, and so
        // does the code (no injection surface).
        $cacheKey = json_encode([$plan['ops'], $plan['slots'], $plan['rkept'], $plan['lnested']]);
        $factory = self::$joinFactories[$cacheKey] ?? null;
        if ($factory !== null) {
            return $factory($shape, $rshape);
        }
        $ops = $plan['ops'];
        $slots = $plan['slots'];
        $rkept = $plan['rkept'];
        $lnested = $plan['lnested'];
        $lrest = $lnested;
        foreach ($ops as $i => $op) {
            if ($op === 0) {
                if ($lnested[$slots[$i]]) $ops[$i] = 4;
                unset($lrest[$slots[$i]]);
            }
        }

        $elements = [];
        foreach ($ops as $i => $op) {
            $slot = $slots[$i];
            if ($op === 0 || $op === 4) {
                $elements[] = "\$ls[{$slot}]";
            } elseif ($op === 1) {
                $elements[] = "\$rs[{$slot}]";
            } elseif ($op === 2) {
                $elements[] = "\$left";
            } else {
                $elements[] = "\$rside";
            }
        }
        $elementsStr = implode(', ', $elements);

        $lchecks = [];
        foreach ($ops as $i => $op) {
            $slot = $slots[$i];
            if ($op === 0) {
                $lchecks[] = "if (\$ls[{$slot}]->kind === 'NONE' && !\$ls[{$slot}]->isList && \$ls[{$slot}]->size() > 0) return false;";
            } elseif ($op === 4) {
                $lchecks[] = "if (\$ls[{$slot}]->kind !== 'NONE' || \$ls[{$slot}]->isList || \$ls[{$slot}]->size() === 0) return false;";
            }
        }
        foreach ($lrest as $slot => $nested) {
            if ($nested) {
                $lchecks[] = "if (\$ls[{$slot}]->kind !== 'NONE' || \$ls[{$slot}]->isList || \$ls[{$slot}]->size() === 0) return false;";
            } else {
                $lchecks[] = "if (\$ls[{$slot}]->kind === 'NONE' && !\$ls[{$slot}]->isList && \$ls[{$slot}]->size() > 0) return false;";
            }
        }
        $lchecksStr = $lchecks === [] ? '' : implode("\n            ", $lchecks);
        $lchecksBuildStr = $lchecks === [] ? '' : str_replace('return false;', 'return null;', $lchecksStr);

        $rchecks = [];
        $rguards = [];
        foreach ($ops as $i => $op) {
            if ($op === 1) {
                $slot = $slots[$i];
                $rchecks[] = "if (\$rs[{$slot}]->kind === 'NONE' && !\$rs[{$slot}]->isList) return null;";
                $rguards[] = "(\$rs[{$slot}]->kind !== 'NONE' || \$rs[{$slot}]->isList)";
            }
        }
        foreach ($rkept as $rk) {
            $rchecks[] = "if (\$rs[{$rk}]->kind !== 'NONE' || \$rs[{$rk}]->isList) return null;";
            $rguards[] = "(\$rs[{$rk}]->kind === 'NONE' && !\$rs[{$rk}]->isList)";
        }
        $rchecksStr = $rchecks === [] ? '' : implode("\n        ", $rchecks);
        $rguardCond = $rguards === [] ? 'true' : implode(' && ', $rguards);

        $code = "return [
            'checkLeft' => static function (\\Sel\\Value \$left): bool {
                \$ls = \$left->storage;
                {$lchecksStr}
                return true;
            },
            'build' => static function (\\Sel\\Value \$left, \\Sel\\Value \$rside, bool \$checkLeft) use (\$shape): ?\\Sel\\Value {
                \$ls = \$left->storage;
                if (\$checkLeft) {
                    {$lchecksBuildStr}
                }
                \$rs = \$rside->storage;
                {$rchecksStr}
                return \\Sel\\Value::fromShape(\$shape, [{$elementsStr}]);
            },
            'many' => static function (\\Sel\\Value \$left, array \$rights, array &\$output, callable \$project) use (\$shape, \$rshape): void {
                \$ls = \$left->storage;
                foreach (\$rights as \$rside) {
                    if (\$rside->shape === \$rshape) {
                        \$rs = \$rside->storage;
                        if ({$rguardCond}) {
                            \$output[] = \\Sel\\Value::fromShape(\$shape, [{$elementsStr}]);
                            continue;
                        }
                    }
                    \$output[] = \$project(\$left, \$rside);
                }
            }
        ];";

        if (count(self::$joinFactories) >= self::JOIN_FACTORY_CAP) {
            self::$joinFactories = [];
        }
        $factory = eval('return static function (\Sel\RecordShape $shape, \Sel\RecordShape $rshape): array { ' . $code . ' };');
        self::$joinFactories[$cacheKey] = $factory;
        return $factory($shape, $rshape);
    }

    private const JOIN_FACTORY_CAP = 512;
    /** @var array<string,\Closure> */
    private static array $joinFactories = [];

    private static function makeJoinProjector(string $b1, string $b2, ?Value $nullRight): JoinProjector
    {
        $plans = [];
        $lastLeft = $lastBuild = $lastMany = $lastCheckLeft = $pairL = $pairR = $pairKey = null;
        $pairMatched = false;

        $project = static function (Value $left, ?Value $right) use (
            &$plans, &$lastLeft, &$lastBuild, &$lastMany, &$lastCheckLeft, &$pairL, &$pairR, &$pairKey, &$pairMatched, $b1, $b2, $nullRight
        ): Value {
            $rside = $right ?? $nullRight;
            if ($left->shape === null || $left->storage === null || $rside === null
                    || $rside->shape === null || $rside->storage === null) {
                return self::makeJoinedRow($left, $right, $b1, $b2, $nullRight);
            }
            $matched = $right !== null;
            if ($pairL === $left->shape && $pairR === $rside->shape && $pairMatched === $matched) {
                $row = ($lastBuild['build'])($left, $rside, $lastLeft !== $left);
                if ($row !== null) {
                    if ($lastLeft !== $left) $lastLeft = $left;
                    return $row;
                }
                $key = $pairKey;
            } else {
                $key = spl_object_id($left->shape) . ':' . spl_object_id($rside->shape) . ':' . ($matched ? 1 : 0);
            }
            foreach ($plans[$key] ?? [] as $entry) {
                $row = ($entry['build'])($left, $rside, true);
                if ($row !== null) {
                    $lastLeft = $left; $lastBuild = $entry;
                    if ($matched) {
                        $lastMany = $entry['many'];
                        $lastCheckLeft = $entry['checkLeft'];
                    }
                    $pairL = $left->shape; $pairR = $rside->shape; $pairMatched = $matched; $pairKey = $key;
                    return $row;
                }
            }
            $plan = self::rowPlan($left, $rside, $matched, $b1, $b2);
            $plan['lnested'] = array_map([self::class, 'leftNested'], $left->storage);
            $leftNames = [];
            foreach ($left->shape->keys as $k) $leftNames[\Sel\Utf8::upper((string) $k)] = true;
            $binderNames = array_flip([...self::binderKeys($b1, '_1'), ...self::binderKeys($b2, '_2')]);
            $kept = [];
            if ($matched) {
                foreach ($rside->shape->keys as $i => $k) {
                    $k = (string) $k;
                    if (!isset($leftNames[\Sel\Utf8::upper($k)]) && !isset($binderNames[$k])
                            && $rside->storage[$i]->kind === Value::NONE && !$rside->storage[$i]->isList) {
                        $kept[] = $i;
                    }
                }
            }
            $plan['rkept'] = $kept;
            $entry = self::compileSpecializedJoinProjector($plan, $plan['shape'], $rside->shape);
            $plans[$key][] = $entry;
            $lastLeft = $left; $lastBuild = $entry;
            if ($matched) {
                $lastMany = $entry['many'];
                $lastCheckLeft = $entry['checkLeft'];
            }
            $pairL = $left->shape; $pairR = $rside->shape; $pairMatched = $matched; $pairKey = $key;
            return ($entry['build'])($left, $rside, false);
        };

        $manyBatch = static function (Value $left, array $rights, array &$output) use (
            &$lastMany, &$lastCheckLeft, &$pairL, &$pairR, &$pairMatched, $project
        ): void {
            if (empty($rights)) return;
            $r0 = $rights[0];
            if ($pairMatched && $pairL === $left->shape && $pairR === $r0->shape && $lastMany !== null && ($lastCheckLeft)($left)) {
                ($lastMany)($left, $rights, $output, $project);
                return;
            }
            $output[] = $project($left, $r0);
            $count = count($rights);
            if ($count === 1) return;
            if ($pairMatched && $pairL === $left->shape && $lastMany !== null && ($lastCheckLeft)($left)) {
                ($lastMany)($left, array_slice($rights, 1), $output, $project);
                return;
            }
            for ($i = 1; $i < $count; $i++) {
                $output[] = $project($left, $rights[$i]);
            }
        };

        return new JoinProjector($project, $manyBatch);
    }

    // --- the join pre-filter (SEL-0049, SEL-0050, SEL-0052) -----------------
    //
    // A FILTER over a LINK hands the join its conjuncts (leadingFieldConjuncts,
    // called from Core's FILTER); the join pre-applies to its left rows those
    // whose fields no right side carries, so the joined rows they would have
    // produced -- all dropped by the same conjunct -- are never built. Decided
    // here from the rows, at run time, so the physical tree stays a function
    // of the AST.

    private const TEXT_COMPARE = ['$==' => true, '$!=' => true, '$<' => true, '$<=' => true, '$>' => true, '$>=' => true];
    private const NUM_COMPARE = ['==' => true, '!=' => true, '<' => true, '<=' => true, '>' => true, '>=' => true];

    /**
     * Every AND-conjunct of a FILTER body, in order, as
     * ['node' => ..., 'fields' => set|null, 'total' => reqs|null, 'binder' => ...]
     * for a LINK to pre-apply to its left rows. `fields`: the upper-cased
     * fields of the row the conjunct reads (a nested `_["orders"]["year"]`
     * reads ORDERS), or null when it reads anything else. `total`: for a
     * comparison between literals and bare field reads, the [name, kind]
     * requirements under which it cannot raise; null when not provable.
     * @return list<array{node: array, fields: array<string,true>|null, total: list<array{0:string,1:string}>|null, binder: string}>
     */
    public static function leadingFieldConjuncts(array $body, string $binder): array
    {
        $conjuncts = [];
        $node = $body;
        while ($node !== null && $node['t'] === 'bin' && $node['op'] === 'AND') {
            $conjuncts[] = $node['r'];
            $node = $node['l'];
        }
        $conjuncts[] = $node;
        $conjuncts = array_reverse($conjuncts);
        // The element is the binder, exactly as named (names are canonical):
        // under an explicit binder `_` is not the element.
        $isRowVar = static fn (?array $n): bool => $n !== null && $n['t'] === 'var' && $n['name'] === $binder;
        $bareRead = static fn (?array $n): bool => $n !== null && $n['t'] === 'index' && $isRowVar($n['obj'] ?? null)
            && isset($n['idx']) && $n['idx']['t'] === 'text';
        $out = [];
        foreach ($conjuncts as $c) {
            $fields = [];
            $readsOnlyFields = static function (?array $n) use (&$readsOnlyFields, &$fields, $bareRead): bool {
                if ($n === null) return true;
                if ($n['t'] === 'index') {
                    if ($bareRead($n)) { $fields[\Sel\Utf8::upper($n['idx']['v'])] = true; return true; }
                    if (isset($n['obj']) && $n['obj']['t'] === 'index') return $readsOnlyFields($n['obj']) && $readsOnlyFields($n['idx'] ?? null);
                    return false;
                }
                if ($n['t'] === 'num' || $n['t'] === 'text' || $n['t'] === 'bool') return true;
                if ($n['t'] === 'bin') return $readsOnlyFields($n['l']) && $readsOnlyFields($n['r']);
                if ($n['t'] === 'un') return $readsOnlyFields($n['x']);
                return false;
            };
            $ok = $readsOnlyFields($c) && $fields !== [];
            $total = null;
            if ($c['t'] === 'bin' && (isset(self::TEXT_COMPARE[$c['op']]) || isset(self::NUM_COMPARE[$c['op']]))) {
                $kind = isset(self::TEXT_COMPARE[$c['op']]) ? 'TEXT' : 'NUM';
                $total = [];
                foreach ([$c['l'], $c['r']] as $operand) {
                    $lit = $operand['t'] === 'num' ? 'NUM' : ($operand['t'] === 'text' ? 'TEXT' : null);
                    if ($lit !== null) {
                        if ($kind === 'NUM' && $lit !== 'NUM') { $total = null; break; }
                        continue;
                    }
                    if (!$bareRead($operand)) { $total = null; break; }
                    $total[] = [$operand['idx']['v'], $kind];
                }
            }
            $out[] = ['node' => $c, 'fields' => $ok ? $fields : null, 'total' => $total, 'binder' => $binder];
        }
        return $out;
    }

    /** Whether evaluating NODE can be observed only through its value (no assignment, sequence, host function or ABORT), so it may run out of order. */
    private static function pureSource(?array $node): bool
    {
        if ($node === null) return true;
        switch ($node['t']) {
            case 'var': case 'num': case 'text': case 'bool': return true;
            case 'index': return self::pureSource($node['obj'] ?? null) && self::pureSource($node['idx'] ?? null);
            case 'bin': return self::pureSource($node['l']) && self::pureSource($node['r']);
            case 'un': return self::pureSource($node['x']);
            case 'list':
                foreach ($node['items'] as $item) if (!self::pureSource($item)) return false;
                return true;
            case 'call':
                if (!isset(BuiltinManifest::BUILTINS[$node['name']]) || $node['name'] === 'ABORT') return false;
                foreach ($node['args'] as $arg) if (!self::pureSource($arg)) return false;
                return true;
            default: return false;
        }
    }

    /**
     * The conjuncts a join may test before it joins, in stage order, and where
     * the walk stopped. One whose fields are all owned by the left rows is
     * applied to them; one that reads only through this join's right binder
     * ($rightHere, `_["products"]["is_active"]`) is applied to the right rows;
     * one that reads a field of some right side ends the walk (AND
     * short-circuits left to right) UNLESS it is total here, in which case it
     * is passed over for the join above; one that reads anything but fields
     * ends the walk too. A deferral relies on no lower relation carrying the
     * field; the join that has those rows repeats the walk with them. Each
     * stage is judged against the joins between its FILTER and this join (its
     * third member, the count of them): a FILTER in the middle of a chain
     * reads rows no join above it has touched.
     * @return array{0: list<array{0: array, 1: array, 2: bool}>, 1: array{0:int,1:int}|null}
     */
    private static function stageWalk(array $stages, callable $ownedHere, callable $totalHere, ?callable $rightHere = null): array
    {
        $applied = [];
        foreach ($stages as $si => $stage) {
            foreach ($stage[1] as $ci => $c) {
                if ($c['fields'] !== null && $ownedHere($c['fields'], $stage)) { $applied[] = [$c, $stage, false]; continue; }
                if ($c['fields'] !== null && $rightHere !== null && $rightHere($c['fields'], $stage)) { $applied[] = [$c, $stage, true]; continue; }
                if ($c['total'] !== null && $totalHere($c['total'], $stage)) continue;
                return [$applied, [$si, $ci]];
            }
        }
        return [$applied, null];
    }

    /** A key naming one conjunct node of the tree: arrays have no identity, and the position and shape of a node do. */
    public static function conjunctId(array $node): string
    {
        return json_encode($node['pos']) . '|' . ($node['op'] ?? $node['t']) . '|' . md5((string) json_encode($node, JSON_PARTIAL_OUTPUT_ON_ERROR));
    }

    private static function truncateStages(array $stages, ?array $stop): array
    {
        if ($stop === null) return $stages;
        [$si, $ci] = $stop;
        $out = array_slice($stages, 0, $si);
        if ($ci) $out[] = [$stages[$si][0], array_slice($stages[$si][1], 0, $ci), $stages[$si][2]];
        return $out;
    }

    /** NODE with every `_["orders"]` -- a read through the left binder's own name (NAMES, upper-cased) -- replaced by `_`. */
    /**
     * Whether $node reads the element bound to $binders only as `r["f"]`
     * with f, upper-cased, not $avoid: then it reads the same on the element
     * as it arrives and on the element extended with its relation's name
     * (ensureRowTableAlias adds only that name), and may run before the
     * extension is made.
     */
    private static function rawSafe(?array $node, array $binders, string $avoid): bool
    {
        if ($node === null) return true;
        if ($node['t'] === 'index' && isset($node['obj']) && $node['obj']['t'] === 'var' && isset($binders[$node['obj']['name']])) {
            return isset($node['idx']) && $node['idx']['t'] === 'text' && \Sel\Utf8::upper($node['idx']['v']) !== $avoid;
        }
        if ($node['t'] === 'var' && isset($binders[$node['name']])) return false;
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $k) {
            if (isset($node[$k]) && is_array($node[$k]) && !self::rawSafe($node[$k], $binders, $avoid)) return false;
        }
        foreach (['args', 'items'] as $k) {
            if (isset($node[$k])) foreach ($node[$k] as $child) if (!self::rawSafe($child, $binders, $avoid)) return false;
        }
        return true;
    }

    private static function readSelf(?array $node, array $names, string $binder): ?array
    {
        if ($node === null) return null;
        if ($node['t'] === 'index' && isset($node['obj']) && $node['obj']['t'] === 'var' && $node['obj']['name'] === $binder
                && isset($node['idx']) && $node['idx']['t'] === 'text' && isset($names[\Sel\Utf8::upper($node['idx']['v'])])) {
            return ['t' => 'var', 'name' => $binder, 'pos' => $node['pos']];
        }
        $copy = $node;
        unset($copy['mathPlan'], $copy['recordShape']);   // a plan of the original reads the original
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $slot) {
            if (isset($node[$slot])) $copy[$slot] = self::readSelf($node[$slot], $names, $binder);
        }
        if (isset($node['args'])) $copy['args'] = array_map(static fn ($a) => self::readSelf($a, $names, $binder), $node['args']);
        if (isset($node['items'])) $copy['items'] = array_map(static fn ($a) => self::readSelf($a, $names, $binder), $node['items']);
        return $copy;
    }

    /** @return array<string,true> the upper-cased keys of every row (a shape's keys read once), plus the names the row is bound under. */
    private static function rowKeys(Value $value, array $bound = []): array
    {
        $keys = [];
        foreach ($bound as $b) $keys[\Sel\Utf8::upper($b)] = true;
        $shapes = [];
        self::forEachRow($value, static function (Value $row) use (&$keys, &$shapes): void {
            if ($row->shape !== null) {
                $id = spl_object_id($row->shape);
                if (isset($shapes[$id])) return;
                $shapes[$id] = true;
                foreach ($row->shape->keys as $k) $keys[\Sel\Utf8::upper($k)] = true;
            } else {
                foreach ($row->keys() as $k) $keys[\Sel\Utf8::upper($k)] = true;
            }
        });
        return $keys;
    }

    private static function forEachRow(Value $value, callable $callback): void
    {
        if ($value->storage !== null && ($value->isList || $value->shape !== null)) {
            foreach ($value->storage as $item) $callback($item);
            return;
        }
        if ($value->size() > 0 && $value->children !== null) {
            foreach ($value->children as $item) $callback($item);
            return;
        }
        if ($value->kind !== Value::NONE) $callback($value);
    }

    /** Whether every row carries the field NAME (as written) as text (a number is text), or, for kind NUM, as a number. */
    public static function rowFactOf(Value $value, string $name, string $kind): bool
    {
        return self::rowFact($value, $name, $kind);
    }

    private static function rowFact(Value $value, string $name, string $kind): bool
    {
        $ok = true;
        $rows = 0;
        self::forEachRow($value, static function (Value $row) use (&$ok, &$rows, $name, $kind): void {
            $rows++;
            if (!$ok) return;
            $v = $row->get($name);
            if ($kind === 'PRESENT') { if ($v === null) $ok = false; return; }
            if ($kind === 'ANY') { if ($v === null || $v->isNull() || self::isNestedRecord($v)) $ok = false; return; }
            if ($v === null || $v->kind !== Value::TEXT) { $ok = false; return; }
            if ($kind === 'NUM') {
                try { $v->asDecimal(); } catch (SelError $e) { $ok = false; }
            }
        });
        return $ok && ($kind !== 'PRESENT' || $rows > 0);
    }

    /**
     * Whether every handed-down join key -- the left key of each join above this
     * one that handed its conjuncts down -- cannot raise on a joined row built
     * from a left row dropped here (a row dropped below never reaches those
     * joins, so an E_NO_KEY there would be lost). Canonical keys never raise,
     * only the reads do, so presence suffices: `r["m"]["f"]` needs f on every
     * row of m's relation; `r["f"]` needs f carried, non-null, by every row of
     * the one side below the join that has it. Anything else is not proved.
     * @param list<array{key: array, rowNames: array<string,true>, outer: int}> $obligations
     * @param list<JoinSideFacts> $above
     */
    private static function keysSafe(array $obligations, JoinSideFacts $left, JoinSideFacts $right, array $above): bool
    {
        foreach ($obligations as $ob) {
            $below = array_slice($above, 0, count($above) - $ob['outer']);
            $key = $ob['key'];
            if (($key['t'] ?? null) !== 'index' || !isset($key['idx'], $key['obj']) || $key['idx']['t'] !== 'text') return false;
            $field = $key['idx']['v'];
            $obj = $key['obj'];
            if ($obj['t'] === 'index' && isset($obj['obj'], $obj['idx']) && $obj['obj']['t'] === 'var'
                    && isset($ob['rowNames'][$obj['obj']['name']]) && $obj['idx']['t'] === 'text') {
                $member = $obj['idx']['v'];
                if (isset($left->names[$member])) { if (!$left->present($field)) return false; continue; }
                $side = null;
                foreach ([$right, ...$below] as $candidate) if (isset($candidate->names[$member])) { $side = $candidate; break; }
                if ($side !== null) { if (!$side->present($field)) return false; continue; }
                $ok = true;
                self::forEachRow($left->value, static function (Value $row) use (&$ok, $member, $field): void {
                    if (!$ok) return;
                    $inner = $row->get($member);
                    if ($inner === null || $inner->get($field) === null) $ok = false;
                });
                if (!$ok) return false;
                continue;
            }
            if ($obj['t'] === 'var' && isset($ob['rowNames'][$obj['name']])) {
                if (!self::totality([[$field, 'ANY']], $left, $right, $below)) return false;
                continue;
            }
            return false;
        }
        return true;
    }

    /**
     * Whether every [field, kind] requirement is met over the joined rows of this
     * join: exactly one side -- the left rows, this right side, or a right side
     * above -- carries the field at all, and that side carries it on every row
     * with the kind (a field two sides carry is promoted from neither, spec §7.4).
     * @param list<JoinSideFacts> $above
     */
    private static function totality(array $reqs, ?JoinSideFacts $left, JoinSideFacts $right, array $above): bool
    {
        foreach ($reqs as [$name, $kind]) {
            $key = \Sel\Utf8::upper($name);
            $owners = [];
            foreach ([$left, $right, ...$above] as $side) {
                if ($side !== null && isset($side->keys[$key])) $owners[] = $side;
            }
            if (count($owners) !== 1 || !$owners[0]->total($name, $kind)) return false;
        }
        return true;
    }

    private static function doLink(Args $a, Context $ctx, bool $leftJoin): Value
    {
        // Taken before anything else is evaluated, so a LINK nested in this
        // one's sources cannot pick it up by accident; it is handed down on
        // purpose below.
        $prefilter = $ctx->joinPrefilter;
        $ctx->joinPrefilter = null;
        $count = $a->count();
        if ($count !== 3 && $count !== 5) {
            fail('E_ARITY', "{$a->name} takes 3 or 5 arguments, got {$count}", $a->pos);
        }
        $leftNode = $a->node(0);
        $rightNode = $a->node(1);
        [$stages, $deep, $above, $obligations] = $prefilter ?? [[], false, [], []];
        $jb1 = $count === 5 ? $a->symbol(2) : (self::singleRelationName($a->node(0)) ?? '_1');
        $jb2 = $count === 5 ? $a->symbol(3) : (self::singleRelationName($a->node(1)) ?? '_2');
        $jequi = self::tryExtractEquiKeys($a->node($count === 5 ? 4 : 2), $jb1, $jb2);
        // The upper-cased keys of the joins between a stage's FILTER and this
        // join, per count of them.
        $aboveKeysCache = [];
        $aboveKeys = static function (array $stage) use (&$aboveKeysCache, $above): array {
            $n = $stage[2];
            if (!isset($aboveKeysCache[$n])) {
                $keys = [];
                foreach (array_slice($above, 0, $n) as $side) foreach ($side->keys as $k => $_) $keys[$k] = true;
                $aboveKeysCache[$n] = $keys;
            }
            return $aboveKeysCache[$n];
        };
        // The keys a side contributes to the joined row include the names its
        // row is bound under: `_["products"]` after LINK(PRODUCTS, ...) is the
        // right row, not a field of the left ones.
        $b2Names = $count === 5 ? [$a->symbol(3), '_2'] : [self::singleRelationName($rightNode) ?? '_2', '_2'];
        $b1Names = $count === 5 ? [$a->symbol(2), '_1'] : [self::singleRelationName($leftNode) ?? '_1', '_1'];
        $rightSide = null;
        // With conjuncts to pre-apply and a left source that is itself a join,
        // the right source is evaluated first -- unobservable when both
        // sources are pure -- so that the conjuncts still askable of the rows
        // below can travel down to the join below, and from there to the base
        // rows, where dropping a row saves every join above it.
        if ($deep && $stages !== [] && $jequi !== null && $leftNode !== null && $leftNode['t'] === 'call'
                && in_array($leftNode['name'], ['LINK', 'LINK_LEFT', 'FILTER'], true)
                && self::pureSource($leftNode) && self::pureSource($rightNode)) {
            try {
                $rightValue = $a->val(1);
            } catch (\Sel\SelError $e) {
                // The right source went first for the prefilter's sake, which is only
                // unobservable while neither source raises (SPEC 7.4: as written, the
                // left source runs first). The left source is pure too: run it now with
                // nothing handed down. If it raises, ITS error is the one as written;
                // if it does not, the right source's error stands.
                $a->val(0);
                throw $e;
            }
            $rightSide = new JoinSideFacts($rightValue, self::rowKeys($rightValue, $b2Names), $leftJoin, $b2Names);
            $ownedBelow = static function (array $fields, array $stage) use ($rightSide, $aboveKeys): bool {
                $upper = $aboveKeys($stage);
                foreach ($fields as $f => $_) if (isset($rightSide->keys[$f]) || isset($upper[$f])) return false;
                return true;
            };
            $totalBelow = static fn (array $reqs, array $stage): bool => self::totality($reqs, null, $rightSide, array_slice($above, 0, $stage[2]));
            [, $stop] = self::stageWalk($stages, $ownedBelow, $totalBelow);
            // Below this join, every stage has one more join above it: this one.
            $handed = array_map(static fn (array $stage): array => [$stage[0], $stage[1], $stage[2] + 1],
                self::truncateStages($stages, $stop));
            if ($handed !== []) {
                // This join computes its left key on every row it receives; a
                // row dropped below never arrives, so the key goes down as an
                // obligation for the join that drops to prove (keysSafe).
                $ownKey = ['key' => $jequi['left'], 'rowNames' => [$jb1 => true, \Sel\Utf8::lower($jb1) => true, '_1' => true, '_' => true],
                           'outer' => count($above) + 1];
                $ctx->joinPrefilter = [$handed, true, [$rightSide, ...$above], [$ownKey, ...$obligations]];
            }
            try {
                $leftValue = $a->val(0);
            } finally {
                $ctx->joinPrefilter = null;
            }
        } else {
            $leftValue = $a->val(0);
            $rightValue = $a->val(1);
        }
        // The join below, if it applied some of these conjuncts, says which
        // ones every row that came up has passed; those are skipped here
        // unless a row was kept on an error below.
        $below = $ctx->joinPrefilterReport;
        $ctx->joinPrefilterReport = null;
        // Drops below that left this join no left rows: as written it may have
        // had some, and then it computes every right key (and raises where one
        // cannot be) before it finds that no row survives. Only the rows as
        // written can say, so the left side -- pure, or nothing was handed
        // down -- is evaluated again without them, and this join runs as
        // written.
        if ($below !== null && $below[2] && self::firstCollectionItem($leftValue) === null) {
            $leftValue = $a->evalNode($a->node(0));
            $ctx->joinPrefilterReport = null;
            $below = null;
        }
        $appliedBelow = ($below !== null && !$below[1]) ? $below[0] : [];
        $dropped = $below !== null && $below[2];
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
        // Every row is built from its own pair (spec §7.4): nothing is decided
        // from a first element except the shape of LINK_LEFT's null record.
        $nullRight = $leftJoin ? self::makeNullRecord($sampleRight, $b2) : null;
        if ($nullRight !== null && $nullRight->isNull()) $nullRight = null;
        $project = self::makeJoinProjector($b1, $b2, $nullRight);
        $project->limitAt($a->pos);
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
            if ($value->size() > 0 && $value->children !== null) {
                foreach ($value->children as $item) $callback($item);
                return;
            }
            if ($value->kind !== Value::NONE) $callback($value);
        };

        if ($equi !== null && $sampleRight !== null) {
            $buckets = [];
            $facts = ['live' => false, 'liveBad' => null, 'bad' => null];
            $rightAllowed = [$b2, \Sel\Utf8::lower($b2), '_2'];
            $rightExtractor = self::compileEquiKeyExtractor($equi['right'], $rightAllowed, $equi['numeric'], $sampleRight);
            if ($rightExtractor !== null) {
                if ($rightValue->isList && $rightValue->storage !== null) {
                    foreach ($rightValue->storage as $item) {
                        $row = $aliasRight($item);
                        self::bucketJoinKey($buckets, $facts, $rightExtractor($row), $row);
                    }
                } else {
                    $each($rightValue, static function (Value $item) use (&$buckets, &$facts, $aliasRight, $rightExtractor): void {
                        $row = $aliasRight($item);
                        self::bucketJoinKey($buckets, $facts, $rightExtractor($row), $row);
                    });
                }
            } else {
                $b2Lower = \Sel\Utf8::lower($b2);
                $hasLower2 = $b2Lower !== $b2;
                $frameRight = [$b2 => Value::none(), '_2' => Value::none()];
                if ($hasLower2) $frameRight[$b2Lower] = Value::none();
                $ctx->pushFrame($frameRight);
                try {
                    $each($rightValue, function (Value $item) use (&$buckets, &$facts, $b2, $b2Lower, $hasLower2, $aliasRight, $equi, $a, $ctx): void {
                        $row = $aliasRight($item);
                        $ctx->setFrameValue($b2, $row);
                        if ($hasLower2) $ctx->setFrameValue($b2Lower, $row);
                        $ctx->setFrameValue('_2', $row);
                        self::bucketJoinKey($buckets, $facts, self::canonicalJoinKey($a->evalNode($equi['right']), $equi['numeric']), $row);
                    });
                } finally {
                    $ctx->popFrame();
                }
            }

            // The pre-filter, decided from the rows themselves (stageWalk). On
            // a left row a conjunct evaluates FALSE the row is dropped -- the
            // joined rows it would have produced would all have been dropped
            // by the same conjunct; likewise a right row, whose joined rows
            // are then not built. On an error the row is KEPT: the full
            // predicate runs over the joined rows afterwards and raises there.
            $prefix = [];
            $rightPrefix = [];
            // How many left conjuncts come before the first right one: with a
            // right row kept on an error, a later left conjunct may not drop a
            // left row -- the joined row would have raised in the right
            // conjunct first.
            $leftBeforeRight = -1;
            $binders = [];
            $appliedIds = [];
            $errored = false;
            // A read through this join's right binder is the right element in
            // every joined row -- the binder is bound last (spec §7.4) --
            // unless the left binder has the same name, or a join above
            // rebinds it.
            $rightNames = static fn (array $stage): array => $stage[2] === 0
                ? [\Sel\Utf8::upper($b2) => true, '_2' => true] : [\Sel\Utf8::upper($b2) => true];
            if ($prefilter !== null) {
                if ($rightSide === null) {
                    $rightSide = new JoinSideFacts($rightValue, self::rowKeys($rightValue, $b2Names), $leftJoin, $b2Names);
                }
                foreach ($stages as $stage) $binders[] = $stage[0];
                $leftSide = new JoinSideFacts($leftValue, self::rowKeys($leftValue, $b1Names), false, $b1Names);
                // A handed-down join key that could raise on a dropped row,
                // and nothing is dropped.
                $safe = $obligations === [] || self::keysSafe($obligations, $leftSide, $rightSide, $above);
                // A joined row carries a left element's field exactly as the
                // element does whenever no right element has the name (§7.4,
                // pair by pair); no row depends on another.
                $ownedHere = static function (array $fields, array $stage) use ($rightSide, $aboveKeys): bool {
                    $upper = $aboveKeys($stage);
                    foreach ($fields as $f => $_) {
                        if (isset($rightSide->keys[$f]) || isset($upper[$f])) return false;
                    }
                    return true;
                };
                $totalHere = static fn (array $reqs, array $stage): bool => self::totality($reqs, $leftSide, $rightSide, array_slice($above, 0, $stage[2]));
                $rightHere = (!$leftJoin && \Sel\Utf8::upper($b1) !== \Sel\Utf8::upper($b2))
                    ? static function (array $fields, array $stage) use ($rightNames, $aboveKeys): bool {
                        $names = $rightNames($stage);
                        $upper = $aboveKeys($stage);
                        foreach ($fields as $f => $_) if (!isset($names[$f]) || isset($upper[$f])) return false;
                        return true;
                    }
                    : null;
                $selfNames = [\Sel\Utf8::upper($b1) => true, '_1' => true];
                [$applied, ] = $safe ? self::stageWalk($stages, $ownedHere, $totalHere, $rightHere) : [[], null];
                foreach ($applied as [$c, $stage, $right]) {
                    $key = self::conjunctId($c['node']);
                    $appliedIds[$key] = true;
                    if (isset($appliedBelow[$key])) continue;
                    if ($right) {
                        if ($leftBeforeRight < 0) $leftBeforeRight = count($prefix);
                        $rightPrefix[] = self::readSelf($c['node'], $rightNames($stage), $c['binder']);
                        continue;
                    }
                    $node = $c['node'];
                    foreach ($c['fields'] as $f => $_) {
                        if (isset($selfNames[$f]) && !isset($leftSide->first[$f])) { $node = self::readSelf($node, $selfNames, $c['binder']); break; }
                    }
                    $prefix[] = $node;
                }
            }
            // 0: keep the row; 1: drop it; 2: keep it, a conjunct raised on it.
            $verdict = static function (array $conjuncts, Value $row) use (&$binders, &$errored, $a, $ctx): int {
                foreach ($binders as $binder) $ctx->setFrameValue($binder, $row);
                foreach ($conjuncts as $conjunct) {
                    try {
                        $keep = $a->evalNode($conjunct)->asBool($conjunct['pos']);
                    } catch (SelError $e) {
                        $errored = true;
                        return 2;
                    }
                    if (!$keep) return 1;
                }
                return 0;
            };
            // The right rows the right conjuncts reject, once each, after
            // every right key was computed. They stay in their buckets: a
            // left row still counts them towards the numbering, and one kept
            // on an error joins them.
            $rejected = null;
            if ($rightPrefix !== []) {
                $rejected = [];
                $frame = [];
                foreach ($binders as $binder) $frame[$binder] = Value::none();
                $before = $errored;
                $errored = false;
                $ctx->pushFrame($frame);
                try {
                    foreach ($buckets as $bucket) {
                        foreach ($bucket as $right) {
                            if ($verdict($rightPrefix, $right) === 1) $rejected[spl_object_id($right)] = true;
                        }
                    }
                } finally {
                    $ctx->popFrame();
                }
                if ($errored) $prefix = array_slice($prefix, 0, $leftBeforeRight);
                $errored = $errored || $before;
            }

            $leftAllowed = [$b1, \Sel\Utf8::lower($b1), '_1', '_'];
            $leftExtractor = self::compileEquiKeyExtractor($equi['left'], $leftAllowed, $equi['numeric'], $sampleLeft);
            if ($prefix !== [] || $rejected !== null) {
                // A FILTER keeps its input's keys, so the rows dropped here
                // still count towards the numbering of the rows kept (the
                // matches say how many joined rows a dropped row stood for),
                // unless nothing observes it (deep). When the join key is a
                // literal field of the row, a row that HAS it may be rejected
                // before its key is computed.
                $keys = $deep ? null : [];
                $position = 1;
                $fastField = null;
                $el = $equi['left'];
                if ($prefix !== [] && $deep && $el['t'] === 'index' && isset($el['obj']) && $el['obj']['t'] === 'var'
                        && isset($el['idx']) && $el['idx']['t'] === 'text'
                        && in_array(\Sel\Utf8::upper($el['obj']['name']), [\Sel\Utf8::upper($b1), '_1', '_'], true)) {
                    $fastField = $el['idx']['v'];
                }
                $b1Lower = \Sel\Utf8::lower($b1);
                $hasLower1 = $b1Lower !== $b1;
                $frameLeft = [$b1 => Value::none(), '_1' => Value::none(), '_' => Value::none()];
                if ($hasLower1) $frameLeft[$b1Lower] = Value::none();
                foreach ($binders as $binder) $frameLeft[$binder] ??= Value::none();
                // A prefix that reads the left element only through fields
                // other than its relation's name is asked of the element as
                // it arrives, before it is extended: a row it drops is never
                // extended.
                $raw = $prefix !== [] && ($fastField === null || \Sel\Utf8::upper($fastField) !== \Sel\Utf8::upper($b1));
                if ($raw) {
                    $binderSet = array_fill_keys($binders, true);
                    foreach ($prefix as $conjunct) {
                        if (!self::rawSafe($conjunct, $binderSet, \Sel\Utf8::upper($b1))) { $raw = false; break; }
                    }
                }
                // The rows in order, walked in place: this loop runs once per
                // left row and is the join's hot path.
                if ($leftValue->isList && $leftValue->storage !== null) {
                    $items = $leftValue->storage;
                } else {
                    $items = [];
                    $each($leftValue, static function (Value $item) use (&$items): void { $items[] = $item; });
                }
                $ctx->pushFrame($frameLeft);
                $top = &$ctx->frames[count($ctx->frames) - 1];
                try {
                    foreach ($items as $item) {
                        $asked = -1;
                        if ($raw) {
                            foreach ($binders as $binder) $top[$binder] = $item;
                            $asked = 0;
                            foreach ($prefix as $conjunct) {
                                try {
                                    $keep = $a->evalNode($conjunct)->asBool($conjunct['pos']);
                                } catch (SelError $e) {
                                    $errored = true;
                                    $asked = 2;
                                    break;
                                }
                                if (!$keep) { $asked = 1; break; }
                            }
                            // A dropped row whose key is a field it has
                            // cannot raise in the key.
                            if ($asked === 1 && $fastField !== null && $item->get($fastField) !== null) {
                                // Dropped before its key was computed -- but the key is
                                // this very field, and a rejected one still raises.
                                self::checkJoinPair($equi, self::canonicalJoinKey($item->get($fastField), $equi['numeric']), $facts);
                                $dropped = true;
                                continue;
                            }
                        }
                        $row = $aliasLeft($item);
                        if ($asked < 0 && $fastField !== null && $row->get($fastField) !== null) {
                            $asked = $verdict($prefix, $row);
                            if ($asked === 1) {
                                self::checkJoinPair($equi, self::canonicalJoinKey($row->get($fastField), $equi['numeric']), $facts);
                                $dropped = true;
                                continue;
                            }
                        }
                        if ($leftExtractor !== null) {
                            $key = $leftExtractor($row);
                        } else {
                            $top[$b1] = $row;
                            if ($hasLower1) $top[$b1Lower] = $row;
                            $top['_1'] = $row;
                            $top['_'] = $row;
                            $key = self::canonicalJoinKey($a->evalNode($equi['left']), $equi['numeric']);
                        }
                        self::checkJoinPair($equi, $key, $facts);
                        $matches = $key === null || is_array($key) ? null : ($buckets[$key] ?? null);
                        if ($asked < 0) $asked = $prefix !== [] ? $verdict($prefix, $row) : 0;
                        if ($asked === 1) {
                            $dropped = true;
                            if (!$deep) $position += $matches !== null ? count($matches) : ($leftJoin ? 1 : 0);
                            continue;
                        }
                        if ($matches !== null) {
                            // A left row kept on an error meets every right
                            // row: its joined rows raise in the FILTER, in
                            // order, where they would have.
                            $skip = ($rejected !== null && $asked === 0) ? $rejected : null;
                            if ($skip === null && $keys === null) {
                                $project->many($row, $matches, $output);
                                $position += count($matches);
                            } else {
                                foreach ($matches as $right) {
                                    if ($skip !== null && isset($skip[spl_object_id($right)])) {
                                        $dropped = true;
                                        $position++;
                                        continue;
                                    }
                                    $output[] = $project($row, $right);
                                    if ($keys !== null) $keys[] = (string) $position;
                                    $position++;
                                }
                            }
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                            if ($keys !== null) $keys[] = (string) $position;
                            $position++;
                        }
                    }
                } finally {
                    unset($top);
                    $ctx->popFrame();
                }
                $ctx->joinPrefilterReport = [$appliedIds, $errored, $dropped];
                if ($keys !== null && count($keys) !== $position - 1) return Value::list($output, $keys);
                return Value::list($output);
            }
            if ($prefilter !== null) $ctx->joinPrefilterReport = [$appliedIds, $errored, $dropped];
            if ($leftExtractor !== null) {
                if ($leftValue->isList && $leftValue->storage !== null) {
                    foreach ($leftValue->storage as $item) {
                        $row = $aliasLeft($item);
                        $key = $leftExtractor($row);
                        self::checkJoinPair($equi, $key, $facts);
                        $matches = $key === null || is_array($key) ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            $project->many($row, $matches, $output);
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                        }
                    }
                } else {
                    $each($leftValue, static function (Value $item) use (&$buckets, &$output, $aliasLeft, $leftExtractor, $leftJoin, $project, $equi, $facts): void {
                        $row = $aliasLeft($item);
                        $key = $leftExtractor($row);
                        self::checkJoinPair($equi, $key, $facts);
                        $matches = $key === null || is_array($key) ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            $project->many($row, $matches, $output);
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                        }
                    });
                }
            } else {
                $b1Lower = \Sel\Utf8::lower($b1);
                $hasLower1 = $b1Lower !== $b1;
                $frameLeft = [$b1 => Value::none(), '_1' => Value::none(), '_' => Value::none()];
                if ($hasLower1) $frameLeft[$b1Lower] = Value::none();
                $ctx->pushFrame($frameLeft);
                try {
                    $each($leftValue, function (Value $item) use (&$buckets, &$output, $b1, $b1Lower, $hasLower1, $aliasLeft, $equi, $a, $leftJoin, $project, $ctx, $facts): void {
                        $row = $aliasLeft($item);
                        $ctx->setFrameValue($b1, $row);
                        if ($hasLower1) $ctx->setFrameValue($b1Lower, $row);
                        $ctx->setFrameValue('_1', $row);
                        $ctx->setFrameValue('_', $row);
                        $key = self::canonicalJoinKey($a->evalNode($equi['left']), $equi['numeric']);
                        self::checkJoinPair($equi, $key, $facts);
                        $matches = $key === null || is_array($key) ? null : ($buckets[$key] ?? null);
                        if ($matches !== null) {
                            $project->many($row, $matches, $output);
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
                $b1 => Value::none(), \Sel\Utf8::lower($b1) => Value::none(), '_1' => Value::none(), '_' => Value::none(),
                $b2 => Value::none(), \Sel\Utf8::lower($b2) => Value::none(), '_2' => Value::none(),
            ];
            $ctx->pushFrame($frame);
            try {
                // The right side is listed ONCE (SPEC 7.3 snapshot): it is walked again for
                // every left row, and a predicate that grows it must not hand later left rows
                // more rows.
                $rightItems = [];
                $rightValue->forEachElement(static function (string $k, Value $item) use (&$rightItems): void {
                    $rightItems[] = $item;
                });
                // Hoisted out of the loops (PHP-P21): the lower-cased binder names, and
                // the aliased right rows. A right row is aliased once, on first use, when
                // the predicate cannot write (pureSource): the alias is a wrapper over the
                // same elements, so a pair that re-made it was only re-proving it. A
                // predicate with an assignment keeps a fresh alias per pair, as before.
                $b1l = \Sel\Utf8::lower($b1);
                $b2l = \Sel\Utf8::lower($b2);
                $shareAliases = self::pureSource($predicate);
                $aliased = [];
                $each($leftValue, function (Value $leftItem) use (&$output, $b1, $b2, $b1l, $b2l, $rightItems, &$aliased, $shareAliases, $aliasLeft, $aliasRight, $a, $predicate, $leftJoin, $project, $ctx): void {
                    $left = $aliasLeft($leftItem);
                    $ctx->setFrameValue($b1, $left);
                    $ctx->setFrameValue($b1l, $left);
                    $ctx->setFrameValue('_1', $left);
                    $ctx->setFrameValue('_', $left);
                    $matched = false;
                    foreach ($rightItems as $ri => $rightItem) {
                        $right = $shareAliases ? ($aliased[$ri] ??= $aliasRight($rightItem)) : $aliasRight($rightItem);
                        $ctx->setFrameValue($b2, $right);
                        $ctx->setFrameValue($b2l, $right);
                        $ctx->setFrameValue('_2', $right);
                        if ($a->evalNode($predicate)->asBool($predicate['pos'])) {
                            $matched = true;
                            $output[] = $project($left, $right);
                        }
                    }
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
                $dir = \Sel\Utf8::upper($a->text(2));
            } elseif ($a->isSymbol(1)) {
                $binder = $a->symbol(1);
                $body = $a->node(2);
            } else {
                $body = $a->node(1);
                $dir = \Sel\Utf8::upper($a->text(2));
            }
        } elseif ($sortCount === 4) {
            $binder = $a->symbol(1);
            $body = $a->node(2);
            $dir = \Sel\Utf8::upper($a->text(3));
        } else {
            fail('E_ARITY', "{$a->name} has an invalid sort form", $a->pos);
        }
        if ($dir !== 'ASC' && $dir !== 'DESC') {
            $directionIndex = $sortCount === 4 ? 3 : 2;
            fail('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", $a->posOf($directionIndex));
        }
        // Count and direction are arguments like any other (spec §7.4): both are
        // evaluated and checked before an empty result is returned.
        if ($limit === 0 || $value->isNull()) return Value::list([]);

        // The one ordering SORT uses too (Core::compareValues); only the
        // bounded selection below is TOP's own.
        $compare = static function (array $left, array $right) use ($dir): int {
            $c = Core::compareKeys($left['sk'], $right['sk']);
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
        // `_K` is bound for every row only when the body can read it (PHP-P26), as
        // BUCKET does; and when the limit reaches the row count there is nothing to
        // select, so the rows are collected and sorted once with the same comparator
        // (ties still break by position) instead of going through the heap.
        $needsK = $binder !== null && Core::containsVar($body, '_K');
        // Collected once its key is computed (spec §3.4): a key that might write
        // copies the element as it is admitted, so a later key's write cannot reach it.
        $eager = $binder !== null && Core::mayWrite($body);
        $collectAll = $limit >= max(1, $value->size());
        if ($frame !== null) $ctx->pushFrame($frame);
        try {
            $consume = function (string $key, Value $item) use (
                &$heap, &$index, $limit, $binder, $body, $ctx, $a, $compare, $siftUp, $siftDown, &$frame,
                $needsK, $collectAll, $eager,
            ): void {
                if ($binder === null) {
                    $candidate = ['item' => $item, 'sk' => Core::sortKey($item), 'idx' => $index++];
                } else {
                    $frame[$binder] = $item;
                    $ctx->setFrameValue($binder, $frame[$binder]);
                    if ($needsK) {
                        $frame['_K'] = Value::text($key);
                        $ctx->setFrameValue('_K', $frame['_K']);
                    }
                    $candidate = ['item' => $item, 'sk' => Core::sortKey($a->evalNode($body)), 'idx' => $index++];
                    if ($eager) $candidate['item'] = $item->copyBelow(1, $a->pos);
                }
                if ($collectAll) {
                    $heap[] = $candidate;
                } elseif (count($heap) < $limit) {
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
                $value->forEachElement($consume);
            }
        } finally {
            if ($frame !== null) $ctx->popFrame();
        }
        usort($heap, $compare);
        $pos = $a->pos;
        if ($eager) return Value::list(array_map(static fn (array $entry): Value => $entry['item'], $heap));
        return Value::list(array_map(static fn (array $entry): Value => $entry['item']->copyBelow(1, $pos), $heap));
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
        // NULL and NONE are an empty list; a value with no children but a scalar
        // is a ONE-element list (spec §7.3), which forEachElement already yields.
        if ($value->isNull()) return Value::list([]);
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
        $needsK = Core::containsVar($keyNode, '_K');
        // A row is collected when its key is computed and it is grouped (spec §3.4):
        // when the key or the projection might write, it is copied then, so neither
        // a later key nor the projection can change a row already grouped.
        $eager = Core::mayWrite($keyNode) || ($aggregateNode !== null && Core::mayWrite($aggregateNode));
        $frame = [$binder => Value::none()];
        if ($needsK) $frame['_K'] = Value::none();
        $ctx->pushFrame($frame);
        try {
            $index = 0;
            $value->forEachElement(function (string $key, Value $source) use (
                &$index, &$frame, &$groups, &$buckets, $a, $ctx, $keyNode, $aggregateNode, $binder, $needsK, $eager,
            ): void {
                $item = $source;
                $frame[$binder] = $source;
                $ctx->setFrameValue($binder, $source);
                if ($needsK) {
                    $frame['_K'] = Value::text($key);
                    $ctx->setFrameValue('_K', $frame['_K']);
                }
                $groupKey = $a->evalNode($keyNode);
                // Bare: a list inside the record (two levels below); projected: the group list (one).
                if ($eager) $item = $source->copyBelow($aggregateNode === null ? 2 : 1, $a->pos);
                // A bare bucket's key is an index key (spec §3.3): the scalar,
                // verbatim, and refused the way indexing refuses it -- never
                // collapsed onto a string that stands for every list, record or
                // NULL. The projected spelling has no map to key and groups by
                // identity instead.
                $keyString = $aggregateNode === null ? self::bucketKeyText($groupKey, $keyNode['pos']) : '';
                $found = null;
                if ($aggregateNode === null) {
                    // The bare spelling groups by the index key's text, whatever
                    // structure the key value carries: two keys "x" with different
                    // children are one group (spec §3.3, §7.3).
                    $hash = 's' . $keyString;
                    $found = $buckets[$hash][0] ?? null;
                } else {
                    $hash = $groupKey->structuralHash();
                    foreach ($buckets[$hash] ?? [] as $groupIndex) {
                        if ($groups[$groupIndex]['key']->eql($groupKey)) {
                            $found = $groupIndex;
                            break;
                        }
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

        if ($groups === []) return Value::list([]);
        if ($aggregateNode === null) {
            $out = Value::none();
            // The members are collected here, so they are copies (spec §3.4): two
            // levels below the result, a list inside the record.
            foreach ($groups as $group) {
                $rows = [];
                foreach ($group['rows'] as $row) $rows[] = $eager ? $row : $row->copyBelow(2, $a->pos);
                $out->set($group['keyString'], Value::list($rows));
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
                $out[] = $a->evalNode($aggregateNode)->copyBelow(1, $a->pos);
            }
        } finally {
            $ctx->popFrame();
        }
        return Value::list($out);
    }
}

final class JoinProjector
{
    /** @param \Closure(Value, ?Value): Value $project */
    /** @param \Closure(Value, list<Value>, list<Value>&): void $manyBatch */
    public function __construct(
        public \Closure $project,
        public \Closure $manyBatch,
    ) {
    }

    /** Rows built so far by this LINK, and where a row over the collection cap is refused (spec §6.4). */
    private int $emitted = 0;
    /** @var array<string,mixed>|null */
    private ?array $limitPos = null;

    /** @param array<string,mixed> $pos the LINK call */
    public function limitAt(array $pos): void
    {
        $this->emitted = 0;
        $this->limitPos = $pos;
    }

    private function tick(int $rows): void
    {
        $this->emitted += $rows;
        if ($this->emitted > \Sel\Limits::MAX_COLLECTION) {
            fail('E_RANGE', 'LINK would produce more than ' . \Sel\Limits::MAX_COLLECTION . ' rows', $this->limitPos);
        }
    }

    public function __invoke(Value $left, ?Value $right): Value
    {
        $this->tick(1);
        return ($this->project)($left, $right);
    }

    /** @param list<Value> $rights @param list<Value> $output */
    public function many(Value $left, array $rights, array &$output): void
    {
        $this->tick(count($rights));
        ($this->manyBatch)($left, $rights, $output);
    }
}

/**
 * What the totality check knows about one side's rows: the union of its keys
 * (with the names its row is bound under), the keys of its first row (a field
 * every row carries is on the first one: a cheap refusal before a scan),
 * per-field presence and kind on demand, and whether its rows may be
 * null-extended (a LINK_LEFT's right).
 */
final class JoinSideFacts
{
    /** @var array<string,true> */
    public array $first;
    /** @var array<string,bool> */
    private array $facts = [];

    /** @var array<string,true> the member names the row is bound under (binder and its lower-case alias; never `_1`/`_2`) */
    public array $names = [];

    /** @param array<string,true> $keys @param list<string> $bound */
    public function __construct(public readonly Value $value, public readonly array $keys, public readonly bool $nullable, array $bound = [])
    {
        foreach ($bound as $b) {
            if ($b === '_1' || $b === '_2' || $b === '_') continue;
            $this->names[$b] = true;
            $this->names[\Sel\Utf8::lower($b)] = true;
        }
        $this->first = [];
        $firstRow = null;
        if ($value->storage !== null && $value->storage !== [] && ($value->isList || $value->shape !== null)) {
            $firstRow = $value->storage[0];
        } elseif ($value->size() > 0 && $value->children !== null) {
            foreach ($value->children as $item) { $firstRow = $item; break; }
        }
        if ($firstRow !== null) foreach ($firstRow->keys() as $k) $this->first[\Sel\Utf8::upper($k)] = true;
    }

    /** The field NAME is a key of every row, whatever its value. */
    public function present(string $name): bool
    {
        $id = 'PRESENT:' . $name;
        if (!array_key_exists($id, $this->facts)) $this->facts[$id] = Structure::rowFactOf($this->value, $name, 'PRESENT');
        return $this->facts[$id];
    }

    public function total(string $name, string $kind): bool
    {
        if (!isset($this->first[\Sel\Utf8::upper($name)]) || $this->nullable) return false;
        $id = $kind . ':' . $name;
        if (!array_key_exists($id, $this->facts)) {
            $this->facts[$id] = Structure::rowFactOf($this->value, $name, $kind);
        }
        return $this->facts[$id];
    }
}
