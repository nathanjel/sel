<?php
// Optimised relational and structural built-ins.

declare(strict_types=1);

namespace Sel\Builtins;

use Sel\Args;
use Sel\BuiltinManifest;
use Sel\SelError;
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
                $value->forEachElement(static function (string $key, Value $item) use (&$buckets, &$out): void {
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
            foreach ([$tableName, strtolower($tableName)] as $name) {
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
        $lower = strtolower($name);
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
        foreach ($rightKeys as $key) $rightNames[strtoupper($key)] = true;
        foreach ($leftKeys as $i => $key) {
            if (self::category($leftValues[$i]) !== self::NESTED && !isset($rightNames[strtoupper($key)])) {
                $put($key, $leftValues[$i]);
            }
        }
        if ($right !== null) {
            $leftNames = [];
            foreach ($leftKeys as $key) $leftNames[strtoupper($key)] = true;
            foreach ($rightKeys as $i => $key) {
                if (self::category($rightValues[$i]) === self::SCALAR && !isset($leftNames[strtoupper($key)])) {
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
        foreach ($rkeys as $key) $rightNames[strtoupper($key)] = true;
        foreach ($lkeys as $i => $key) {
            if ($lcat[$i] !== self::NESTED && !isset($rightNames[strtoupper($key)])) $put($key, 0, $i);
        }
        if ($matched) {
            $rcat = array_map([self::class, 'category'], $rside->storage);
            $leftNames = [];
            foreach ($lkeys as $key) $leftNames[strtoupper($key)] = true;
            foreach ($rkeys as $i => $key) {
                if ($rcat[$i] === self::SCALAR && !isset($leftNames[strtoupper($key)])) $put($key, 1, $i);
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
    private static function makeJoinProjector(string $b1, string $b2, ?Value $nullRight): callable
    {
        $plans = [];
        // The last pair's shapes, its builder and the left row that builder
        // was last checked against (or built from), and the key of the plans
        // for those shapes. Plain variables, and fields read in place rather
        // than through locals: an object a variable lets go of while it is
        // still referenced is a candidate root for PHP's cycle collector, and
        // one per field per row makes the collector run often (SEL-0053).
        $lastLeft = $lastBuild = $pairL = $pairR = $pairKey = null;
        $pairMatched = false;
        // A plan's builder: the row, or null when the pair breaks the plan's
        // assumptions -- with CHECKLEFT, that each left field is (or is not)
        // a nested record as it was; and always, that a right field it copies
        // (promotes) is a non-NULL scalar, and that one it leaves out for
        // being NULL or a record still is one. Each field is checked as it is
        // copied: op 0 copies a left field that was not a nested record, op 4
        // one that was; the left fields the row leaves out are checked apart
        // (lrest: slot => nested). (The right fields are checked pair by
        // pair: a pass over the right rows to learn they are all flat, as JS,
        // C++ and Lisp make, saves PHP nothing measurable.)
        $builder = static function (array $plan): \Closure {
            $ops = $plan['ops'];
            $slots = $plan['slots'];
            $shape = $plan['shape'];
            $rkept = $plan['rkept'];
            $lnested = $plan['lnested'];
            $lrest = $lnested;
            foreach ($ops as $i => $op) {
                if ($op === 0) {
                    if ($lnested[$slots[$i]]) $ops[$i] = 4;
                    unset($lrest[$slots[$i]]);
                }
            }
            return static function (Value $left, Value $rside, bool $checkLeft)
                use ($ops, $slots, $shape, $lrest, $rkept): ?Value {
                $ls = $left->storage;
                $rs = $rside->storage;
                $storage = [];
                foreach ($ops as $i => $op) {
                    if ($op === 0) {
                        if ($checkLeft && $ls[$slots[$i]]->kind === Value::NONE && !$ls[$slots[$i]]->isList
                                && ($ls[$slots[$i]]->storage ?? $ls[$slots[$i]]->children) !== []) return null;
                        $storage[] = $ls[$slots[$i]];
                    } elseif ($op === 1) {
                        if ($rs[$slots[$i]]->kind === Value::NONE && !$rs[$slots[$i]]->isList) return null;
                        $storage[] = $rs[$slots[$i]];
                    } elseif ($op === 4) {
                        if ($checkLeft && ($ls[$slots[$i]]->kind !== Value::NONE || $ls[$slots[$i]]->isList
                                || ($ls[$slots[$i]]->storage ?? $ls[$slots[$i]]->children) === [])) return null;
                        $storage[] = $ls[$slots[$i]];
                    } else {
                        $storage[] = $op === 2 ? $left : $rside;
                    }
                }
                if ($checkLeft) {
                    foreach ($lrest as $i => $nested) {
                        if (($ls[$i]->kind === Value::NONE && !$ls[$i]->isList
                                && ($ls[$i]->storage ?? $ls[$i]->children) !== []) !== $nested) return null;
                    }
                }
                foreach ($rkept as $i) {
                    if ($rs[$i]->kind !== Value::NONE || $rs[$i]->isList) return null;
                }
                return Value::fromShape($shape, $storage);
            };
        };
        return static function (Value $left, ?Value $right) use (&$plans, &$lastLeft, &$lastBuild, &$pairL, &$pairR, &$pairKey, &$pairMatched, $builder, $b1, $b2, $nullRight): Value {
            $rside = $right ?? $nullRight;
            if ($left->shape === null || $left->storage === null || $rside === null
                    || $rside->shape === null || $rside->storage === null) {
                return self::makeJoinedRow($left, $right, $b1, $b2, $nullRight);
            }
            $matched = $right !== null;
            // The same shapes as the last pair: its builder first, checking
            // the left row only when it is a new one.
            if ($pairL === $left->shape && $pairR === $rside->shape && $pairMatched === $matched) {
                $row = $lastBuild($left, $rside, $lastLeft !== $left);
                if ($row !== null) {
                    if ($lastLeft !== $left) $lastLeft = $left;
                    return $row;
                }
                $key = $pairKey;
            } else {
                $key = spl_object_id($left->shape) . ':' . spl_object_id($rside->shape) . ':' . ($matched ? 1 : 0);
            }
            foreach ($plans[$key] ?? [] as $build) {
                $row = $build($left, $rside, true);
                if ($row !== null) {
                    $lastLeft = $left; $lastBuild = $build;
                    $pairL = $left->shape; $pairR = $rside->shape; $pairMatched = $matched; $pairKey = $key;
                    return $row;
                }
            }
            $plan = self::rowPlan($left, $rside, $matched, $b1, $b2);
            $plan['lnested'] = array_map([self::class, 'leftNested'], $left->storage);
            // Right fields the left does not name that were left out for being
            // NULL or records: they must still be, for the plan to hold. (A
            // scalar left out because its name is already a key of the row
            // stays out whatever it holds; so does one named like a binder
            // key, which the binder holds.)
            $leftNames = [];
            foreach ($left->shape->keys as $k) $leftNames[strtoupper((string) $k)] = true;
            $binderNames = array_flip([...self::binderKeys($b1, '_1'), ...self::binderKeys($b2, '_2')]);
            $kept = [];
            if ($matched) {
                foreach ($rside->shape->keys as $i => $k) {
                    $k = (string) $k;
                    if (!isset($leftNames[strtoupper($k)]) && !isset($binderNames[$k])
                            && $rside->storage[$i]->kind === Value::NONE && !$rside->storage[$i]->isList) {
                        $kept[] = $i;
                    }
                }
            }
            $plan['rkept'] = $kept;
            $build = $builder($plan);
            $plans[$key][] = $build;
            $lastLeft = $left; $lastBuild = $build;
            $pairL = $left->shape; $pairR = $rside->shape; $pairMatched = $matched; $pairKey = $key;
            return $build($left, $rside, false);
        };
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
     * ['node' => ..., 'fields' => set|null, 'total' => reqs|null, 'pushed' => false]
     * for a LINK to pre-apply to its left rows. `fields`: the upper-cased
     * fields of the row the conjunct reads (a nested `_["orders"]["year"]`
     * reads ORDERS), or null when it reads anything else. `total`: for a
     * comparison between literals and bare field reads, the [name, kind]
     * requirements under which it cannot raise; null when not provable.
     * @return list<array{node: array, fields: array<string,true>|null, total: list<array{0:string,1:string}>|null, pushed: bool}>
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
                    if ($bareRead($n)) { $fields[strtoupper($n['idx']['v'])] = true; return true; }
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
            $out[] = ['node' => $c, 'fields' => $ok ? $fields : null, 'total' => $total, 'pushed' => false, 'binder' => $binder];
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
     * The conjuncts a join may pre-apply to its left rows, in stage order, and
     * where the walk stopped. One whose fields are all owned by the left rows
     * is applied; one that reads a field of some right side ends the walk
     * (AND short-circuits left to right) UNLESS it is total here, in which case
     * it is passed over for the join above; one that reads anything but fields
     * ends the walk too; one the optimiser pushed below is skipped while its
     * tentative FILTER kept no row on an error. A deferral relies on no lower
     * relation carrying the field; the join that has those rows repeats the
     * walk with them.
     * @return array{0: list<array>, 1: array{0:int,1:int}|null}
     */
    private static function stageWalk(array $stages, callable $ownedHere, callable $totalHere, bool $pushedHeld): array
    {
        $applied = [];
        foreach ($stages as $si => [$binder, $conjuncts]) {
            foreach ($conjuncts as $ci => $c) {
                if ($c['pushed']) {
                    if ($pushedHeld) continue;
                    return [$applied, [$si, $ci]];
                }
                if ($c['fields'] !== null && $ownedHere($c['fields'])) { $applied[] = $c; continue; }
                if ($c['total'] !== null && $totalHere($c['total'])) continue;
                return [$applied, [$si, $ci]];
            }
        }
        return [$applied, null];
    }

    /** A key naming one conjunct node of the tree: arrays have no identity, and the position and shape of a node do. */
    private static function conjunctId(array $node): string
    {
        return json_encode($node['pos']) . '|' . ($node['op'] ?? $node['t']) . '|' . md5((string) json_encode($node, JSON_PARTIAL_OUTPUT_ON_ERROR));
    }

    private static function truncateStages(array $stages, ?array $stop): array
    {
        if ($stop === null) return $stages;
        [$si, $ci] = $stop;
        $out = array_slice($stages, 0, $si);
        if ($ci) $out[] = [$stages[$si][0], array_slice($stages[$si][1], 0, $ci)];
        return $out;
    }

    /** NODE with every `_["orders"]` -- a read through the left binder's own name (NAMES, upper-cased) -- replaced by `_`. */
    private static function readSelf(?array $node, array $names, string $binder): ?array
    {
        if ($node === null) return null;
        if ($node['t'] === 'index' && isset($node['obj']) && $node['obj']['t'] === 'var' && $node['obj']['name'] === $binder
                && isset($node['idx']) && $node['idx']['t'] === 'text' && isset($names[strtoupper($node['idx']['v'])])) {
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
        foreach ($bound as $b) $keys[strtoupper($b)] = true;
        $shapes = [];
        self::forEachRow($value, static function (Value $row) use (&$keys, &$shapes): void {
            if ($row->shape !== null) {
                $id = spl_object_id($row->shape);
                if (isset($shapes[$id])) return;
                $shapes[$id] = true;
                foreach ($row->shape->keys as $k) $keys[strtoupper($k)] = true;
            } else {
                foreach ($row->keys() as $k) $keys[strtoupper($k)] = true;
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
        if ($value->size() > 0) {
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
            $key = strtoupper($name);
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
        $aboveKeys = [];
        foreach ($above as $side) foreach ($side->keys as $k => $_) $aboveKeys[$k] = true;
        // The keys a side contributes to the joined row include the names its
        // row is bound under: `_["products"]` after LINK(PRODUCTS, ...) is the
        // right row, not a field of the left ones.
        $b2Names = $count === 5 ? [$a->symbol(3), '_2'] : [self::singleRelationName($rightNode) ?? '_2', '_2'];
        $b1Names = $count === 5 ? [$a->symbol(2), '_1'] : [self::singleRelationName($leftNode) ?? '_1', '_1'];
        $keptBefore = $ctx->tentativeKept;
        $rightSide = null;
        // With conjuncts to pre-apply and a left source that is itself a join,
        // the right source is evaluated first -- unobservable when both
        // sources are pure -- so that the conjuncts still askable of the rows
        // below can travel down to the join below, and from there to the base
        // rows, where dropping a row saves every join above it.
        if ($deep && $stages !== [] && $jequi !== null && $leftNode !== null && $leftNode['t'] === 'call'
                && in_array($leftNode['name'], ['LINK', 'LINK_LEFT', 'FILTER'], true)
                && self::pureSource($leftNode) && self::pureSource($rightNode)) {
            $rightValue = $a->val(1);
            $rightSide = new JoinSideFacts($rightValue, self::rowKeys($rightValue, $b2Names), $leftJoin, $b2Names);
            $ownedBelow = static function (array $fields) use ($rightSide, $aboveKeys): bool {
                foreach ($fields as $f => $_) if (isset($rightSide->keys[$f]) || isset($aboveKeys[$f])) return false;
                return true;
            };
            $totalBelow = static fn (array $reqs): bool => self::totality($reqs, null, $rightSide, $above);
            // Whether the conjuncts the optimiser pushed below held so far: a
            // tentative FILTER on this join's right side has just run.
            [, $stop] = self::stageWalk($stages, $ownedBelow, $totalBelow, $ctx->tentativeKept === $keptBefore);
            $handed = self::truncateStages($stages, $stop);
            if ($handed !== []) {
                // This join computes its left key on every row it receives; a
                // row dropped below never arrives, so the key goes down as an
                // obligation for the join that drops to prove (keysSafe).
                $ownKey = ['key' => $jequi['left'], 'rowNames' => [$jb1 => true, strtolower($jb1) => true, '_1' => true, '_' => true],
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
        $pushedHeld = $ctx->tentativeKept === $keptBefore;
        // The join below, if it applied some of these conjuncts, says which
        // ones every row that came up has passed; those are skipped here
        // unless a row was kept on an error below.
        $below = $ctx->joinPrefilterReport;
        $ctx->joinPrefilterReport = null;
        $appliedBelow = ($below !== null && !$below[1]) ? $below[0] : [];
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

            // The pre-filter, decided from the rows themselves (stageWalk). On
            // a left row a conjunct evaluates FALSE the row is dropped -- the
            // joined rows it would have produced would all have been dropped
            // by the same conjunct. On an error the row is KEPT: the full
            // predicate runs over the joined rows afterwards and raises there.
            $prefix = [];
            $binders = [];
            $appliedIds = [];
            $errored = false;
            if ($prefilter !== null) {
                if ($rightSide === null) {
                    $rightSide = new JoinSideFacts($rightValue, self::rowKeys($rightValue, $b2Names), $leftJoin, $b2Names);
                }
                foreach ($stages as [$binder, ]) $binders[] = $binder;
                $leftSide = new JoinSideFacts($leftValue, self::rowKeys($leftValue, $b1Names), false, $b1Names);
                // A handed-down join key that could raise on a dropped row,
                // and nothing is dropped.
                $safe = $obligations === [] || self::keysSafe($obligations, $leftSide, $rightSide, $above);
                // A joined row carries a left element's field exactly as the
                // element does whenever no right element has the name (§7.4,
                // pair by pair); no row depends on another.
                $ownedHere = static function (array $fields) use ($rightSide, $aboveKeys): bool {
                    foreach ($fields as $f => $_) {
                        if (isset($rightSide->keys[$f]) || isset($aboveKeys[$f])) return false;
                    }
                    return true;
                };
                $totalHere = static fn (array $reqs): bool => self::totality($reqs, $leftSide, $rightSide, $above);
                $selfNames = [strtoupper($b1) => true, '_1' => true];
                [$applied, ] = $safe ? self::stageWalk($stages, $ownedHere, $totalHere, $pushedHeld) : [[], null];
                foreach ($applied as $c) {
                    $key = self::conjunctId($c['node']);
                    $appliedIds[$key] = true;
                    if (isset($appliedBelow[$key])) continue;
                    $node = $c['node'];
                    foreach ($c['fields'] as $f => $_) {
                        if (isset($selfNames[$f]) && !isset($leftSide->first[$f])) { $node = self::readSelf($node, $selfNames, $c['binder']); break; }
                    }
                    $prefix[] = $node;
                }
            }
            $rejects = static function (Value $row) use (&$prefix, &$binders, &$errored, $a, $ctx): bool {
                foreach ($binders as $binder) $ctx->setFrameValue($binder, $row);
                foreach ($prefix as $conjunct) {
                    try {
                        $keep = $a->evalNode($conjunct)->asBool($conjunct['pos']);
                    } catch (SelError $e) {
                        $errored = true;
                        return false;
                    }
                    if (!$keep) return true;
                }
                return false;
            };

            $leftAllowed = [$b1, strtolower($b1), '_1', '_'];
            $leftExtractor = self::compileEquiKeyExtractor($equi['left'], $leftAllowed, $equi['numeric'], $sampleLeft);
            if ($prefix !== []) {
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
                if ($deep && $el['t'] === 'index' && isset($el['obj']) && $el['obj']['t'] === 'var'
                        && isset($el['idx']) && $el['idx']['t'] === 'text'
                        && in_array(strtoupper($el['obj']['name']), [strtoupper($b1), '_1', '_'], true)) {
                    $fastField = $el['idx']['v'];
                }
                $b1Lower = strtolower($b1);
                $hasLower1 = $b1Lower !== $b1;
                $frameLeft = [$b1 => Value::none(), '_1' => Value::none(), '_' => Value::none()];
                if ($hasLower1) $frameLeft[$b1Lower] = Value::none();
                foreach ($binders as $binder) $frameLeft[$binder] ??= Value::none();
                $ctx->pushFrame($frameLeft);
                try {
                    $each($leftValue, function (Value $item) use (&$buckets, &$output, &$keys, &$position, $b1, $b1Lower, $hasLower1, $aliasLeft, $leftExtractor, $equi, $a, $leftJoin, $project, $ctx, $rejects, $fastField, $deep): void {
                        $row = $aliasLeft($item);
                        $asked = false;
                        if ($fastField !== null && $row->get($fastField) !== null) {
                            $asked = true;
                            if ($rejects($row)) return;
                        }
                        $ctx->setFrameValue($b1, $row);
                        if ($hasLower1) $ctx->setFrameValue($b1Lower, $row);
                        $ctx->setFrameValue('_1', $row);
                        $ctx->setFrameValue('_', $row);
                        $key = $leftExtractor !== null ? $leftExtractor($row)
                            : self::canonicalJoinKey($a->evalNode($equi['left']), $equi['numeric']);
                        $matches = $key === null ? null : ($buckets[$key] ?? null);
                        if (!$asked && $rejects($row)) {
                            if (!$deep) $position += $matches !== null ? count($matches) : ($leftJoin ? 1 : 0);
                            return;
                        }
                        if ($matches !== null) {
                            foreach ($matches as $right) {
                                $output[] = $project($row, $right);
                                if ($keys !== null) $keys[] = (string) $position;
                                $position++;
                            }
                        } elseif ($leftJoin) {
                            $output[] = $project($row, null);
                            if ($keys !== null) $keys[] = (string) $position;
                            $position++;
                        }
                    });
                } finally {
                    $ctx->popFrame();
                }
                $ctx->joinPrefilterReport = [$appliedIds, $errored];
                if ($keys !== null && count($keys) !== $position - 1) return Value::list($output, $keys);
                return Value::list($output);
            }
            if ($prefilter !== null) $ctx->joinPrefilterReport = [$appliedIds, $errored];
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
                    $rightValue->forEachElement(function (string $rightKey, Value $rightItem) use (
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
                $value->forEachElement($consume);
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
            $value->forEachElement(function (string $key, Value $item) use (
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
            $this->names[strtolower($b)] = true;
        }
        $this->first = [];
        $firstRow = null;
        if ($value->storage !== null && $value->storage !== [] && ($value->isList || $value->shape !== null)) {
            $firstRow = $value->storage[0];
        } elseif ($value->size() > 0) {
            foreach ($value->children as $item) { $firstRow = $item; break; }
        }
        if ($firstRow !== null) foreach ($firstRow->keys() as $k) $this->first[strtoupper($k)] = true;
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
        if (!isset($this->first[strtoupper($name)]) || $this->nullable) return false;
        $id = $kind . ':' . $name;
        if (!array_key_exists($id, $this->facts)) {
            $this->facts[$id] = Structure::rowFactOf($this->value, $name, $kind);
        }
        return $this->facts[$id];
    }
}
