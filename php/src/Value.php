<?php
// The SEL value. One class, used by the interpreter and by host code alike.
// See spec/SPEC.md §3.
//
// A PHP string is a byte array, so TEXT holds validated UTF-8 bytes and BIN holds
// arbitrary bytes — the same PHP type, told apart by `kind`. That makes asBytes()
// on TEXT free, and makes the TEXT/BIN distinction a deliberate choice rather
// than an accident of representation.
//
// Children live in a plain array. PHP silently turns numeric-string keys into
// ints, so every key that leaves this class is cast back to string; lookups are
// unaffected because PHP normalises both directions the same way.

declare(strict_types=1);

namespace Sel;

final class RecordShape
{
    /** @var array<string,self> */
    private static array $cache = [];
    private static array $aliasPlans = [];
    private const CACHE_ENTRIES = 256;
    private const CACHE_MAX_KEYS = 256;
    private const CACHE_MAX_BYTES = 16384;
    private static bool $instrumentation = false;
    /** @var array<string,int> */
    private static array $stats = [
        'intern_calls' => 0,
        'intern_hits' => 0,
        'new_shapes' => 0,
        'signature_ns' => 0,
        'alias_calls' => 0,
        'alias_hits' => 0,
        'alias_builds' => 0,
    ];

    /** @var list<string> */
    public readonly array $keys;
    /** @var array<string,int> */
    public readonly array $keyMap;
    public readonly int $size;
    /** @var list<string> */
    public readonly array $keyHashParts;
    /** @var array<string,array{shape:self,oldSize:int,addLower:bool}> */
    public array $aliasCache = [];

    /** @param list<string> $keys */
    public static function intern(array $keys): self
    {
        $started = self::$instrumentation ? hrtime(true) : 0;
        if (self::$instrumentation) self::$stats['intern_calls']++;
        if (!array_is_list($keys)) {
            $keys = array_values($keys);
        }
        // serialize preserves order, key type, and embedded NUL bytes. Unlike a
        // delimiter join it cannot merge two different SEL key sequences.
        $signature = serialize($keys);
        if (self::$instrumentation) self::$stats['signature_ns'] += hrtime(true) - $started;
        if (isset(self::$cache[$signature])) {
            if (self::$instrumentation) self::$stats['intern_hits']++;
            return self::$cache[$signature];
        }
        if (self::$instrumentation) self::$stats['new_shapes']++;
        $shape = new self($keys);
        if (count($keys) <= self::CACHE_MAX_KEYS &&
            array_sum(array_map('strlen', $keys)) <= self::CACHE_MAX_BYTES) {
            if (count(self::$cache) >= self::CACHE_ENTRIES) self::$cache = [];
            self::$cache[$signature] = $shape;
        }
        return $shape;
    }

    public static function enableInstrumentation(bool $enabled): void
    {
        self::$instrumentation = $enabled;
    }

    public static function resetStats(): void
    {
        foreach (self::$stats as $key => $_) self::$stats[$key] = 0;
    }

    /** @return array<string,int> */
    public static function stats(): array
    {
        $stats = self::$stats;
        $stats['cache_size'] = count(self::$cache);
        $stats['alias_cache_entries'] = count(self::$aliasPlans);
        return $stats;
    }

    /** @param list<string> $keys */
    public function __construct(array $keys)
    {
        $this->keys = array_is_list($keys) ? $keys : array_values($keys);
        $keyMap = [];
        $keyHashParts = [];
        foreach ($this->keys as $i => $key) {
            $keyMap[$key] = $i;
            $keyHashParts[] = strlen($key) . ':' . $key . '=';
        }
        $this->keyMap = $keyMap;
        $this->keyHashParts = $keyHashParts;
        $this->size = count($this->keys);
    }

    /** @return array{shape:self,oldSize:int,addLower:bool} */
    public function alias(string $tableName): array
    {
        if (self::$instrumentation) self::$stats['alias_calls']++;
        $id = spl_object_id($this);
        $entry = self::$aliasPlans[$id] ?? null;
        $cached = $entry !== null && $entry[1] === $tableName ? $entry[2] : null;
        if ($cached !== null) {
            if (self::$instrumentation) self::$stats['alias_hits']++;
            return $cached;
        }
        $lower = strtolower($tableName);
        $addLower = $lower !== $tableName && !isset($this->keyMap[$lower]);
        $keys = $this->keys;
        $keys[] = $tableName;
        if ($addLower) {
            $keys[] = $lower;
        }
        $cached = [
            'shape' => self::intern($keys),
            'oldSize' => $this->size,
            'addLower' => $addLower,
        ];
        if (count($keys) <= self::CACHE_MAX_KEYS &&
            array_sum(array_map('strlen', $keys)) <= self::CACHE_MAX_BYTES) {
            if (count(self::$aliasPlans) >= self::CACHE_ENTRIES) self::$aliasPlans = [];
            // Retain the source so an object id cannot be recycled under a plan.
            self::$aliasPlans[$id] = [$this, $tableName, $cached];
        }
        if (self::$instrumentation) self::$stats['alias_builds']++;
        return $cached;
    }
}

final class Value
{
    public const NONE = 'NONE';
    public const TEXT = 'TEXT';
    public const BIN = 'BIN';
    public const BOOL = 'BOOL';

    public string $kind;
    /** @var string|bool|null */
    private $scalar = null;
    /** @var array<array-key, Value> */
    public array $children = [];
    public bool $isList = false;
    public ?RecordShape $shape = null;
    /** @var list<Value>|null */
    public ?array $storage = null;
    /** @var list<string>|null */
    public ?array $listKeys = null;
    /** @var array<string,int>|null */
    private ?array $listKeyMap = null;
    /** @var array{neg:bool,digits:string,scale:int}|null */
    public ?array $decVal = null;

    /** @param string|bool|null $scalar */
    private function __construct(string $kind, $scalar, bool $isList = false)
    {
        $this->kind = $kind;
        $this->scalar = $scalar;
        $this->isList = $isList;
    }

    public function getScalar(): mixed
    {
        if ($this->scalar === null && $this->decVal !== null) {
            if ($this->decVal['scale'] === 0) {
                $this->scalar = ($this->decVal['neg'] ? '-' : '') . $this->decVal['digits'];
            } else {
                $this->scalar = Dec::format($this->decVal);
            }
        }
        return $this->scalar;
    }

    public function __get(string $name): mixed
    {
        if ($name === 'scalar') {
            return $this->getScalar();
        }
        return null;
    }

    public function __set(string $name, mixed $value): void
    {
        if ($name === 'scalar') {
            $this->scalar = $value;
            $this->decVal = null;
        }
    }

    public function __isset(string $name): bool
    {
        if ($name === 'scalar') {
            return $this->scalar !== null || $this->decVal !== null;
        }
        return false;
    }

    public static function none(): self
    {
        return new self(self::NONE, null);
    }

    public static function null(): self
    {
        return new self(self::NONE, null, false);
    }

    public static function text(string $s): self
    {
        return new self(self::TEXT, $s);
    }

    public static function bin(string $b): self
    {
        return new self(self::BIN, $b);
    }

    public static function bool(bool $b): self
    {
        return new self(self::BOOL, $b);
    }

    /** @param array{neg:bool,digits:string,scale:int}|string $d */
    /**
     * A string is canonicalised and validated: "007" becomes "7", and anything
     * that is not a number is E_NOT_NUM here rather than a TEXT value that fails
     * later somewhere else. Internal callers pass a decimal record, not a string.
     *
     * @param array{neg:bool,digits:string,scale:int}|string $d
     */
    public static function num($d): self
    {
        if (!is_string($d)) {
            $v = new self(self::TEXT, null);
            $v->decVal = $d;
            return $v;
        }
        $parsed = Dec::parse($d);
        if ($parsed === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($d));
        }
        $v = new self(self::TEXT, null);
        $v->decVal = $parsed;
        return $v;
    }

    public static function int(int $n): self
    {
        $v = new self(self::TEXT, null);
        $v->decVal = Dec::fromInt($n);
        return $v;
    }

    /** Builds a list keyed "1".."n" (or preserved keys). Used by `,` and by list-returning built-ins. */
    /** @param list<Value> $values @param list<string>|null $keys */
    public static function list(array $values, ?array $keys = null): self
    {
        $v = new self(self::NONE, null, true);
        // Keep PHP's packed representation when the caller already supplied a
        // list. array_values() would eagerly duplicate a large COW array.
        $v->storage = array_is_list($values) ? $values : array_values($values);
        $v->listKeys = $keys !== null ? (array_is_list($keys) ? $keys : array_values($keys)) : null;
        return $v;
    }

    /** @param list<string> $keys @param list<Value> $values */
    public static function shaped(array $keys, array $values): self
    {
        if ($keys === []) {
            return self::none();
        }
        if (count($keys) !== count($values)) {
            throw new \InvalidArgumentException('record shape and storage sizes differ');
        }
        $shape = RecordShape::intern($keys);
        return self::fromShape($shape, $values);
    }

    /**
     * Reuse a previously interned shape without rebuilding its key map. The
     * storage is still a separate packed array, as SEL values are mutable and
     * assignment must not mutate a sibling value through PHP COW.
     *
     * @param list<Value> $values
     */
    public static function fromShape(RecordShape $shape, array $values): self
    {
        if (count($values) !== $shape->size) {
            throw new \InvalidArgumentException('record shape and storage sizes differ');
        }
        $v = new self(self::NONE, null);
        $v->shape = $shape;
        $v->storage = array_is_list($values) ? $values : array_values($values);
        return $v;
    }

    /**
     * Build a record from parallel packed arrays. Keeping keys and values
     * separate avoids allocating one two-element PHP array per field in the
     * common RECORD path.
     *
     * @param list<string> $keys
     * @param list<Value> $values
     */
    public static function record(array $keys, array $values): self
    {
        if ($keys === []) {
            return self::none();
        }
        if (count($keys) !== count($values)) {
            throw new \InvalidArgumentException('record keys and values sizes differ');
        }
        $seen = [];
        $unique = true;
        foreach ($keys as $key) {
            if (array_key_exists($key, $seen)) {
                $unique = false;
                break;
            }
            $seen[$key] = true;
        }
        if ($unique) {
            return self::shaped($keys, $values);
        }
        $v = self::none();
        foreach ($keys as $i => $key) {
            $v->set($key, $values[$i]);
        }
        return $v;
    }

    /** @param list<array{0:string,1:Value}> $entries */
    public static function fromEntries(array $entries, bool $isList = false): self
    {
        if ($isList) {
            $values = [];
            $keys = [];
            $needsCustomKeys = false;
            $expectedIndex = 1;
            foreach ($entries as $entry) {
                $key = (string) $entry[0];
                $values[] = $entry[1];
                $keys[] = $key;
                if (!$needsCustomKeys && (int) $key !== $expectedIndex) {
                    $needsCustomKeys = true;
                }
                $expectedIndex++;
            }
            return self::list($values, $needsCustomKeys ? $keys : null);
        }
        $keys = [];
        $values = [];
        foreach ($entries as $entry) {
            $key = (string) $entry[0];
            $keys[] = $key;
            $values[] = $entry[1];
        }
        return self::record($keys, $values);
    }

    /**
     * Convert a list of native rows using one prepared shape while the rows
     * remain homogeneous. This is an internal ingestion path: it validates
     * the first record and every subsequent key sequence, and falls back to
     * the ordinary recursive converter as soon as the input stops matching.
     *
     * @param list<mixed> $rows
     */
    public static function fromNativeRows(array $rows): self
    {
        if ($rows === []) {
            return self::list([]);
        }
        $out = [];
        $shape = null;
        $shapeKeys = null;
        $homogeneous = true;
        foreach ($rows as $row) {
            if ($homogeneous && $shape !== null) {
                if (is_array($row) && !array_is_list($row)
                    && self::nativeKeysMatch($row, $shapeKeys)) {
                    $values = [];
                    foreach ($row as $item) {
                        $values[] = self::fromNativeAt($item, 3);
                    }
                    $out[] = self::fromShape($shape, $values);
                    continue;
                }
                // A heterogeneous row is deliberately converted generically;
                // do not trust the prepared shape for any later rows.
                $homogeneous = false;
            }
            if ($homogeneous && $shape === null) {
                $candidate = self::nativeRecordKeys($row);
                if ($candidate !== null) {
                    $shapeKeys = $candidate;
                    $shape = RecordShape::intern($candidate);
                    $values = [];
                    foreach ($row as $item) {
                        $values[] = self::fromNativeAt($item, 3);
                    }
                    $out[] = self::fromShape($shape, $values);
                    continue;
                }
                $homogeneous = false;
            }
            $out[] = self::fromNativeAt($row, 2);
        }
        return self::list($out);
    }

    // --- children -----------------------------------------------------------

    /**
     * Kind predicates. The recommended way to branch on kind in every host,
     * because it is the one spelling that reads the same in all four. These
     * test the value's own kind and do not apply scalar context.
     */
    public function isNone(): bool
    {
        return $this->kind === self::NONE;
    }

    public function isNull(): bool
    {
        return $this->kind === self::NONE && $this->size() === 0 && !$this->isList;
    }

    public function isVacuous(): bool
    {
        if ($this->isNull()) {
            return true;
        }
        if ($this->kind === self::NONE && $this->size() === 0) {
            return true;
        }
        if ($this->kind === self::TEXT && $this->size() === 0) {
            return preg_match('/^[ \t\r\n]*$/', (string) $this->getScalar()) === 1;
        }
        return false;
    }

    public function isText(): bool
    {
        return $this->kind === self::TEXT;
    }

    public function isBin(): bool
    {
        return $this->kind === self::BIN;
    }

    public function isBool(): bool
    {
        return $this->kind === self::BOOL;
    }

    public function size(): int
    {
        return $this->storage !== null ? count($this->storage) : count($this->children);
    }

    public function has(string $key): bool
    {
        if ($this->shape !== null) {
            return isset($this->shape->keyMap[$key]);
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                if ($this->listKeyMap === null) {
                    $this->listKeyMap = array_flip($this->listKeys);
                }
                return isset($this->listKeyMap[$key]);
            }
            $index = self::listIndex($key, count($this->storage));
            return $index >= 0;
        }
        return array_key_exists($key, $this->children);
    }

    public function get(string $key): ?Value
    {
        if ($this->shape !== null) {
            $index = $this->shape->keyMap[$key] ?? null;
            return $index === null ? null : $this->storage[$index];
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                if ($this->listKeyMap === null) {
                    $this->listKeyMap = array_flip($this->listKeys);
                }
                $index = $this->listKeyMap[$key] ?? null;
                return $index === null ? null : $this->storage[$index];
            }
            $index = self::listIndex($key, count($this->storage));
            return $index < 0 ? null : $this->storage[$index];
        }
        if (!array_key_exists($key, $this->children)) {
            return null;
        }
        return $this->children[$key];
    }

    /** @return list<string> */
    public function keys(): array
    {
        if ($this->shape !== null) {
            return $this->shape->keys;
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                return $this->listKeys;
            }
            return array_map(static fn (int $i): string => (string) ($i + 1), array_keys($this->storage));
        }
        return array_map('strval', array_keys($this->children));
    }

    /** @return list<Value> */
    public function values(): array
    {
        if ($this->storage !== null) {
            return $this->storage;
        }
        return array_values($this->children);
    }

    /** @return list<array{0:string,1:Value}> */
    public function entries(): array
    {
        $out = [];
        if ($this->shape !== null) {
            foreach ($this->shape->keys as $i => $key) {
                $out[] = [$key, $this->storage[$i]];
            }
            return $out;
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                foreach ($this->storage as $i => $value) {
                    $out[] = [$this->listKeys[$i], $value];
                }
                return $out;
            }
            foreach ($this->storage as $i => $value) {
                $out[] = [(string) ($i + 1), $value];
            }
            return $out;
        }
        foreach ($this->children as $k => $v) {
            $out[] = [(string) $k, $v];
        }
        return $out;
    }

    /** Re-assigning an existing key keeps its original position. */
    public function set(string $key, Value $value): self
    {
        if ($this->shape !== null) {
            $index = $this->shape->keyMap[$key] ?? null;
            if ($index !== null) {
                $this->storage[$index] = $value;
                return $this;
            }
            $this->children = [];
            foreach ($this->entries() as [$existingKey, $existingValue]) {
                $this->children[$existingKey] = $existingValue;
            }
            $this->shape = null;
            $this->storage = null;
        } elseif ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                if ($this->listKeyMap === null) {
                    $this->listKeyMap = array_flip($this->listKeys);
                }
                $index = $this->listKeyMap[$key] ?? null;
                if ($index !== null) {
                    $this->storage[$index] = $value;
                    return $this;
                }
                $this->children = [];
                foreach ($this->entries() as [$existingKey, $existingValue]) {
                    $this->children[$existingKey] = $existingValue;
                }
                $this->storage = null;
                $this->listKeys = null;
                $this->listKeyMap = null;
            } else {
                $index = self::listIndex($key, count($this->storage));
                if ($index >= 0) {
                    $this->storage[$index] = $value;
                    return $this;
                }
                $this->children = [];
                foreach ($this->entries() as [$existingKey, $existingValue]) {
                    $this->children[$existingKey] = $existingValue;
                }
                $this->storage = null;
            }
        }
        $this->children[$key] = $value;
        return $this;
    }

    private static function listIndex(string $key, int $length): int
    {
        $len = strlen($key);
        if ($len === 0 || $len > 9 || $key[0] < '1' || $key[0] > '9' || !ctype_digit($key)) {
            return -1;
        }
        $index = (int) $key - 1;
        return $index < $length ? $index : -1;
    }

    // --- scalar context (§3.2) ----------------------------------------------

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function scalarSource(?array $pos = null): Value
    {
        if ($this->kind !== self::NONE) {
            return $this;
        }
        $v = $this;
        $guard = 0;
        while ($v->kind === self::NONE) {
            if ($v->isNull()) {
                fail('E_NULL', 'value is NULL', $pos);
            }
            if ($v->size() === 0) {
                fail('E_NO_SCALAR', 'value has no scalar and no children', $pos);
            }
            $v = $v->storage !== null
                ? $v->storage[0]
                : $v->children[array_key_first($v->children)];
            if (++$guard > 1000) {
                fail('E_DEPTH', 'scalar context nested too deeply', $pos);
            }
        }
        return $v;
    }

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function asText(?array $pos = null): string
    {
        $v = $this->scalarSource($pos);
        if ($v->kind === self::TEXT) {
            return (string) $v->getScalar();
        }
        if ($v->kind === self::BIN) {
            fail('E_NOT_TEXT', 'expected text, got binary (use FROM_UTF8)', $pos);
        }
        fail('E_NOT_TEXT', 'expected text, got boolean', $pos);
    }

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function asBytes(?array $pos = null): string
    {
        $v = $this->scalarSource($pos);
        // TEXT already holds its UTF-8 bytes, so this is the identity.
        if ($v->kind === self::BIN || $v->kind === self::TEXT) {
            return (string) $v->getScalar();
        }
        fail('E_NOT_BIN', 'expected binary or text, got boolean', $pos);
    }

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function asBool(?array $pos = null): bool
    {
        $v = $this->scalarSource($pos);
        if ($v->kind === self::BOOL) {
            return (bool) $v->scalar;
        }
        fail('E_NOT_BOOL', 'expected a boolean — SEL has no truthiness', $pos);
    }

    /**
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array{neg:bool,digits:string,scale:int}
     */
    public function asDecimal(?array $pos = null): array
    {
        $v = $this->scalarSource($pos);
        if ($v->kind !== self::TEXT) {
            fail('E_NOT_NUM', 'expected a number, got ' . strtolower($v->kind), $pos);
        }
        if ($v->decVal !== null) {
            return $v->decVal;
        }
        $d = Dec::parse((string) $v->getScalar(), $pos);
        if ($d === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($v->getScalar()), $pos);
        }
        $v->decVal = $d;
        return $d;
    }

    /** Non-throwing probe for ISNUM. */
    public function looksNumeric(): bool
    {
        if ($this->kind === self::NONE && $this->size() === 0) {
            return false;
        }
        try {
            $v = $this->scalarSource(null);
            // A well-formed numeral too big to hold raises E_RANGE out of parse.
            // The probe answers no rather than raising, so ISNUM is true exactly
            // when the value can be used as a number — before the cap it said
            // TRUE for a 2 000 000-digit text that then failed on first use.
            return $v->kind === self::TEXT && $v->asDecimal() !== null;
        } catch (SelError) {
            return false;
        }
    }

    // --- copying ------------------------------------------------------------

    /**
     * Assignment copies by value: two variables never share structure (§5.7).
     *
     * A value's nesting is the third thing spec/SPEC.md §6.4 caps, after the parser's
     * and the evaluator's, and it was the last one left uncounted. copy, eql, dump and
     * the two native conversions each recurse once per level, so a value nested deeply
     * enough reached the host's own stack: RecursionError on Python at about a thousand
     * levels, an uncaught RangeError on JS at about four, a segfault on C++ at about
     * sixty. Three hosts answered where two died, on the same program.
     *
     * The depth rides as a parameter, as it does in dependencies(): nothing has to be
     * released on the way out, so all five hosts spell it the same way. A value of
     * exactly MAX_DEPTH levels is fine; the level past it is refused. `$pos` is
     * reported when the caller has one -- the evaluator knows which node asked -- and
     * is null for a call from host code, the same convention as asText().
     *
     * @param array<string,mixed>|null $pos
     */
    public function copy(?array $pos = null): Value
    {
        return $this->copyAt(1, $pos);
    }

    /** @param array<string,mixed>|null $pos */
    private function copyAt(int $depth, ?array $pos): Value
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', $pos);
        }
        if ($this->shape === null && $this->storage === null && $this->children === []) {
            $out = new self($this->kind, $this->scalar, $this->isList);
            $out->decVal = $this->decVal;
            return $out;
        }
        if ($this->shape !== null) {
            $values = [];
            foreach ($this->storage as $value) {
                $values[] = $value->copyAt($depth + 1, $pos);
            }
            return self::fromShape($this->shape, $values);
        }
        if ($this->isList && $this->storage !== null) {
            $values = [];
            foreach ($this->storage as $value) {
                $values[] = $value->copyAt($depth + 1, $pos);
            }
            return self::list($values, $this->listKeys);
        }
        $out = new self($this->kind, $this->scalar, $this->isList);
        $out->decVal = $this->decVal;
        foreach ($this->children as $k => $v) {
            $out->children[$k] = $v->copyAt($depth + 1, $pos);
        }
        return $out;
    }

    // --- structural equality (§5.4) -----------------------------------------

    /** @param array<string,mixed>|null $pos */
    public function eql(Value $other, ?array $pos = null): bool
    {
        return $this->eqlAt($other, 1, $pos);
    }

    public function structuralHash(): string
    {
        $hash = hash_init('xxh3');
        $this->updateStructuralHash($hash, 1);
        return hash_final($hash);
    }

    private function updateStructuralHash(\HashContext $hash, int $depth): void
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', null);
        }
        // Hash logical scalar/children only: eqlAt ignores the storage layout
        // and list marker. Packed storage may also carry a host-set scalar.
        $scalar = match ($this->kind) {
            self::NONE => '',
            self::TEXT, self::BIN => (string) $this->getScalar(),
            self::BOOL => $this->scalar ? '1' : '0',
        };
        hash_update($hash, $this->kind . ':' . strlen($scalar) . ':' . $scalar . ';');
        if ($this->shape !== null) {
            $storage = $this->storage;
            $nextDepth = $depth + 1;
            foreach ($this->shape->keyHashParts as $i => $part) {
                hash_update($hash, $part);
                $storage[$i]->updateStructuralHash($hash, $nextDepth);
                hash_update($hash, ';');
            }
            return;
        }
        if ($this->isList && $this->storage !== null) {
            $storage = $this->storage;
            $nextDepth = $depth + 1;
            if ($this->listKeys === null) {
                foreach ($storage as $i => $value) {
                    $key = (string) ($i + 1);
                    hash_update($hash, strlen($key) . ':' . $key . '=');
                    $value->updateStructuralHash($hash, $nextDepth);
                    hash_update($hash, ';');
                }
            } else {
                foreach ($storage as $i => $value) {
                    $key = $this->listKeys[$i];
                    hash_update($hash, strlen($key) . ':' . $key . '=');
                    $value->updateStructuralHash($hash, $nextDepth);
                    hash_update($hash, ';');
                }
            }
            return;
        }
        foreach ($this->children as $key => $value) {
            $key = (string) $key;
            hash_update($hash, strlen($key) . ':' . $key . '=');
            $value->updateStructuralHash($hash, $depth + 1);
            hash_update($hash, ';');
        }
    }

    /** @param array<string,mixed>|null $pos */
    private function eqlAt(Value $other, int $depth, ?array $pos): bool
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', $pos);
        }
        if ($this->kind !== $other->kind) {
            return false;
        }
        if ($this->kind === self::TEXT) {
            if ($this->scalar === null && $other->scalar === null
                && $this->decVal !== null && $other->decVal !== null) {
                if ($this->decVal['neg'] !== $other->decVal['neg']
                    || $this->decVal['scale'] !== $other->decVal['scale']
                    || $this->decVal['digits'] !== $other->decVal['digits']) {
                    return false;
                }
            } else {
                if ($this->getScalar() !== $other->getScalar()) {
                    return false;
                }
            }
        } elseif ($this->kind === self::BOOL || $this->kind === self::BIN) {
            if ($this->scalar !== $other->scalar) {
                return false;
            }
        }
        if ($this->size() !== $other->size()) {
            return false;
        }
        if ($this->shape !== null && $other->shape !== null && $this->shape === $other->shape) {
            foreach ($this->storage as $i => $value) {
                if (!$value->eqlAt($other->storage[$i], $depth + 1, $pos)) return false;
            }
            return true;
        }
        if ($this->isList && $other->isList && $this->storage !== null && $other->storage !== null) {
            if ($this->listKeys !== null || $other->listKeys !== null) {
                if ($this->keys() !== $other->keys()) return false;
            }
            foreach ($this->storage as $i => $value) {
                if (!$value->eqlAt($other->storage[$i], $depth + 1, $pos)) return false;
            }
            return true;
        }
        $a = $this->entries();
        $b = $other->entries();
        foreach ($a as $i => [$key, $val]) {
            if ($key !== $b[$i][0]) {   // key order is normative
                return false;
            }
            if (!$val->eqlAt($b[$i][1], $depth + 1, $pos)) {
                return false;
            }
        }
        return true;
    }

    // --- canonical dump (conformance/README.md) -----------------------------

    public function dump(): string
    {
        return $this->dumpAt(1);
    }

    private function dumpAt(int $depth): string
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', null);
        }
        $s = match ($this->kind) {
            self::NONE => '-',
            self::TEXT => 't' . self::quoteDump((string) $this->getScalar()),
            self::BIN => 'b' . bin2hex((string) $this->getScalar()),
            self::BOOL => $this->scalar ? 'TRUE' : 'FALSE',
        };
        if ($this->size() === 0) {
            return $s;
        }
        $parts = [];
        foreach ($this->entries() as [$k, $v]) {
            $parts[] = self::quoteDump($k) . '=' . $v->dumpAt($depth + 1);
        }
        return $s . '{' . implode(', ', $parts) . '}';
    }

    public static function quoteDump(string $s): string
    {
        $out = '"';
        foreach (Utf8::chars($s) as $ch) {
            $out .= match ($ch) {
                '\\' => '\\\\',
                '"' => '\\"',
                "\n" => '\\n',
                "\t" => '\\t',
                "\r" => '\\r',
                default => ord($ch[0]) < 0x20
                    ? sprintf('\\u%04x', Utf8::ord($ch))
                    : $ch,
            };
        }
        return $out . '"';
    }

    // --- host convenience ---------------------------------------------------

    /** @param mixed $x */
    public static function fromNative($x): Value
    {
        return self::fromNativeAt($x, 1);
    }

    /** @param mixed $x */
    private static function fromNativeAt($x, int $depth): Value
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', null);
        }
        if ($x === null) {
            return self::null();
        }
        if ($x instanceof Value) {
            return $x;
        }
        if (is_bool($x)) {
            return self::bool($x);
        }
        if (is_int($x)) {
            return self::int($x);
        }
        if (is_float($x)) {
            throw new \InvalidArgumentException(
                'floats have no exact decimal form; pass a numeric string instead',
            );
        }
        if (is_string($x)) {
            Utf8::validate($x);
            return self::text($x);
        }
        if (is_array($x)) {
            // A packed 0-based array is a list, and SEL lists are keyed from 1 —
            // that is what `,` produces and what the JS host produces for a JS
            // array. Without the renumbering, ITEMS[1] would mean the first line
            // on the frontend and the second on the backend.
            if (array_is_list($x)) {
                return self::list(array_map(
                    static fn ($item): Value => self::fromNativeAt($item, $depth + 1),
                    $x,
                ));
            }
            $keys = [];
            $values = [];
            foreach ($x as $k => $item) {
                $keys[] = (string) $k;
                $values[] = self::fromNativeAt($item, $depth + 1);
            }
            return self::record($keys, $values);
        }
        throw new \InvalidArgumentException('cannot convert ' . gettype($x) . ' to SEL');
    }

    /** @param mixed $row @return list<string>|null */
    private static function nativeRecordKeys($row): ?array
    {
        if (!is_array($row) || array_is_list($row)) {
            return null;
        }
        $keys = [];
        $seen = [];
        foreach ($row as $key => $_) {
            $key = (string) $key;
            if (array_key_exists($key, $seen)) {
                return null;
            }
            $seen[$key] = true;
            $keys[] = $key;
        }
        return $keys === [] ? null : $keys;
    }

    /** @param array<mixed> $row @param list<string>|null $keys */
    private static function nativeKeysMatch(array $row, ?array $keys): bool
    {
        if ($keys === null || count($row) !== count($keys)) {
            return false;
        }
        $i = 0;
        foreach ($row as $key => $_) {
            if ((string) $key !== $keys[$i++]) {
                return false;
            }
        }
        return true;
    }

    /** @return mixed */
    public function toNative()
    {
        return $this->toNativeAt(1);
    }

    /** @return mixed */
    private function toNativeAt(int $depth)
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', null);
        }
        $scalar = $this->kind === self::NONE ? null : $this->getScalar();
        if ($this->size() === 0) {
            return $scalar;
        }
        if ($this->isList) {
            $out = [];
            $children = $this->storage ?? array_values($this->children);
            foreach ($children as $value) {
                $out[] = $value->toNativeAt($depth + 1);
            }
            return $out;
        }
        $out = [];
        if ($scalar !== null) {
            $out['_'] = $scalar;
        }
        if ($this->shape !== null && $this->storage !== null) {
            foreach ($this->shape->keys as $i => $key) {
                $out[$key] = $this->storage[$i]->toNativeAt($depth + 1);
            }
            return $out;
        }
        foreach ($this->children as $key => $value) {
            $out[$key] = $value->toNativeAt($depth + 1);
        }
        return $out;
    }
}
