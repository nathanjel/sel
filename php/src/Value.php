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

final class SlotCache
{
    public ?RecordShape $shape = null;
    public int $slot = -1;
}

final class RecordShape
{
    /** @var array<string,self> */
    private static array $cache = [];
    private static array $aliasPlans = [];
    private const CACHE_ENTRIES = 256;
    private const CACHE_MAX_KEYS = 256;
    private const CACHE_MAX_BYTES = 16384;
    /**
     * Benchmark counters, off unless tools/scale-test turns them on
     * (enableInstrumentation): one static bool test per intern or alias when
     * off. Production observability is cacheSizes(), which costs nothing.
     */
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
        // A shape is built once per key sequence, so this is where its keys are
        // checked: text, distinct (spec §8).
        Value::checkDistinctKeys($keys, 'a record shape');
        $shape = new self($keys);
        if (count($keys) <= self::CACHE_MAX_KEYS &&
            array_sum(array_map('strlen', $keys)) <= self::CACHE_MAX_BYTES) {
            if (count(self::$cache) >= self::CACHE_ENTRIES) self::$cache = [];
            self::$cache[$signature] = $shape;
        }
        return $shape;
    }

    /**
     * How full the two bounded caches are: interned shapes (`cache_size`) and
     * LINK alias plans (`alias_cache_entries`), each at most 256 entries. The
     * PHP metadata check (tools/metadata/php.php) holds them to that bound.
     *
     * @return array{cache_size:int, alias_cache_entries:int}
     */
    public static function cacheSizes(): array
    {
        return ['cache_size' => count(self::$cache), 'alias_cache_entries' => count(self::$aliasPlans)];
    }

    /** @internal benchmark hook (tools/scale-test): count interns and alias builds. */
    public static function enableInstrumentation(bool $enabled): void
    {
        self::$instrumentation = $enabled;
    }

    /** @internal benchmark hook (tools/scale-test): zero the counters. */
    public static function resetStats(): void
    {
        foreach (self::$stats as $key => $_) self::$stats[$key] = 0;
    }

    /**
     * @internal benchmark hook (tools/scale-test): the counters, which move
     * only while instrumentation is on, plus cacheSizes().
     *
     * @return array<string,int>
     */
    public static function stats(): array
    {
        return self::$stats + self::cacheSizes();
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
        $lower = \Sel\Utf8::lower($tableName);
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

/**
 * @phpstan-import-type Decimal from \Sel\Dec
 * @phpstan-import-type EagerDecimal from \Sel\Dec
 */
final class Value
{
    public const NONE = 'NONE';
    public const TEXT = 'TEXT';
    public const BIN = 'BIN';
    public const BOOL = 'BOOL';

    public string $kind;
    /** @var string|bool|null */
    private $scalar = null;
    /** @var array<array-key, Value>|null */
    public ?array $children = null;
    public bool $isList = false;
    public ?RecordShape $shape = null;
    /** @var list<Value>|null */
    public ?array $storage = null;
    /** @var list<string>|null */
    public ?array $listKeys = null;
    /** @var array<string,int>|null */
    private ?array $listKeyMap = null;
    /** @var Decimal|null */
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
            if ($this->decVal['scale'] === 0 && $this->decVal['digits'] !== null) {
                $this->scalar = ($this->decVal['neg'] ? '-' : '') . $this->decVal['digits'];
            } else {
                $this->scalar = Dec::format($this->decVal);
            }
        }
        return $this->scalar;
    }

    /**
     * The magic accessors exist for one name: `scalar`, private so a number's
     * text can be written lazily. Any other name is a mistake (a typo such as
     * `->scaler`, or a private property) and raises rather than reading null
     * or writing nothing.
     */
    public function __get(string $name): mixed
    {
        if ($name === 'scalar') {
            return $this->getScalar();
        }
        throw new \Error('Undefined property: ' . self::class . '::$' . $name);
    }

    public function __set(string $name, mixed $value): void
    {
        if ($name === 'scalar') {
            $this->scalar = $value;
            $this->decVal = null;
            return;
        }
        throw new \Error('Cannot set undefined or private property ' . self::class . '::$' . $name);
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

    /**
     * The public constructors take `mixed` and check by hand: a scalar type
     * declaration turns a wrong argument into the host's own TypeError (strict
     * callers) or a silent coercion (`int(1.5)`, `text(5)`; non-strict ones), and
     * spec §8 says a malformed call is E_BAD_ARG, never the host's exception.
     *
     * @param mixed $s
     */
    public static function text($s): self
    {
        if (!is_string($s)) {
            self::badCall('text', 'a string', $s);
        }
        self::checkText($s);
        return new self(self::TEXT, $s);
    }

    /**
     * INTERNAL: a TEXT whose bytes the engine itself knows are valid UTF-8 — a
     * parser literal (the lexer validated the source and every escape it decodes
     * is a scalar value), a number's digits, a cut of a valid text at character
     * boundaries, the concatenation of two valid texts. Skips the PCRE validity
     * pass Value::text makes (~280 ns, ~40% of evaluating a literal, PHP-P11).
     * Anything from outside — host input, bytes decoded from BIN — must still
     * go through Value::text.
     */
    public static function textTrusted(string $s): self
    {
        return new self(self::TEXT, $s);
    }

    /** @param mixed $x */
    private static function describe($x): string
    {
        return get_debug_type($x);
    }

    /** @param mixed $got */
    private static function badCall(string $ctor, string $wants, $got): never
    {
        fail('E_BAD_ARG', "Value::{$ctor} takes {$wants}, not " . self::describe($got), null);
    }

    /** A key entering from host code (spec §8): valid UTF-8, like every text. */
    public static function checkKey(string $key): void
    {
        self::checkText($key);
    }

    /** @param mixed $key */
    private static function keyNotString($key): never
    {
        fail('E_BAD_ARG', 'a key must be a string, not ' . gettype($key), null);
    }

    /**
     * Keys a host hands in for a list or a record shape: strings, valid UTF-8,
     * each once (spec §8).
     *
     * @param array<mixed> $keys
     */
    public static function checkDistinctKeys(array $keys, string $what): void
    {
        $seen = [];
        foreach ($keys as $key) {
            if (!is_string($key)) self::keyNotString($key);
            self::checkText($key);
            if (isset($seen[$key])) {
                fail('E_BAD_ARG', "{$what} cannot hold the key " . json_encode($key) . ' twice', null);
            }
            $seen[$key] = true;
        }
    }

    /**
     * Every text entering is valid UTF-8, keys included (spec §8; review
     * 2026-09-25 HOST-05). PCRE's strict UTF-8 check is the fast path; only a
     * string it rejects meets the hand-written codec, which raises E_UTF8.
     */
    private static function checkText(string $s): void
    {
        if (preg_match('//u', $s) !== 1) {
            Utf8::validate($s);
        }
    }

    /**
     * @param mixed $b a string of bytes, or a list of whole numbers 0..255
     */
    public static function bin($b): self
    {
        if (is_array($b)) {
            $bytes = '';
            foreach ($b as $n) {
                if (!is_int($n) || $n < 0 || $n > 255) {
                    fail('E_BAD_ARG', 'bin takes whole numbers 0..255, not ' . self::describe($n), null);
                }
                $bytes .= chr($n);
            }
            return new self(self::BIN, $bytes);
        }
        if (!is_string($b)) {
            self::badCall('bin', 'a string or a list of bytes', $b);
        }
        return new self(self::BIN, $b);
    }

    /**
     * A fresh value every time. The two BOOL values used to be shared
     * flyweights, and a Value is mutable (`set`, the `scalar` setter, a
     * host's own `$v->children[...]`), so one program's assignment into TRUE
     * changed TRUE for every other program in the process (PHP-C1). The
     * allocation is what every other kind pays.
     */
    /** @param mixed $b */
    public static function bool($b): self
    {
        if (!is_bool($b)) {
            self::badCall('bool', 'a bool', $b);
        }
        return new self(self::BOOL, $b);
    }

    /**
     * A string is canonicalised and validated: "007" becomes "7", and anything
     * that is not a number is E_NOT_NUM here rather than a TEXT value that fails
     * later somewhere else. Internal callers pass a decimal record, not a string.
     *
     * @param Decimal|string $d
     */
    public static function num($d): self
    {
        if (!is_string($d)) {
            $v = new self(self::TEXT, null);
            $v->decVal = Dec::checked($d);
            return $v;
        }
        $parsed = Dec::parse($d);
        if ($parsed === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($d));
        }
        // The scalar is derived from the parsed decimal, not kept as typed:
        // "007" is 7 and "-0" is 0 (spec §4, §8), and a caller that spelled it
        // otherwise still gets one canonical text (PHP-C10, a 0.9.2 regression).
        $v = new self(self::TEXT, null);
        $v->decVal = $parsed;
        return $v;
    }

    /**
     * The number for a decimal an operation of this library just built. Dec's own
     * arithmetic has already canonicalised it and enforced the digit caps, so the
     * well-formedness pass `num()` runs on host input (HOST-13/14, PHP-C40) is
     * skipped: a quarter of each numeric result went to it (PHP-P12). Never call
     * this with a decimal that came from outside the library.
     *
     * @param Decimal $d
     */
    public static function numTrusted(array $d): self
    {
        $v = new self(self::TEXT, null);
        $v->decVal = $d;
        return $v;
    }

    /** @param mixed $n */
    public static function int($n): self
    {
        if (!is_int($n)) {
            self::badCall('int', 'an int', $n);
        }
        // The decimal is NOT built here: it is derived from the scalar text on the
        // first arithmetic that needs it (asDecimal caches it). Eager, it was an
        // array per integer — 440 bytes on top of the object's 255 — and a list of
        // a million bytes (BTL at the collection cap) took 675 MB.
        return new self(self::TEXT, (string) $n);
    }

    /** Builds a list keyed "1".."n" (or preserved keys). Used by `,` and by list-returning built-ins. */
    /** @param list<Value> $values @param list<string>|null $keys */
    public static function list($values, $keys = null): self
    {
        if (!is_array($values) || ($keys !== null && !is_array($keys))) {
            self::badCall('list', 'an array of Values (and an array of keys, or null)', is_array($values) ? $keys : $values);
        }
        foreach ($values as $item) {
            if (!$item instanceof self) {
                fail('E_BAD_ARG', 'a list is built from Values, not ' . get_debug_type($item), null);
            }
        }
        if ($keys !== null) {
            // A list's keys pair up with its values and are distinct text
            // (spec §8; review 2026-09-28 HOST-12, HOST-17, HOST-18).
            if (count($keys) !== count($values)) {
                fail('E_BAD_ARG', count($keys) . ' key(s) and ' . count($values) . ' value(s) do not pair up', null);
            }
            self::checkDistinctKeys($keys, 'a list');
        }
        $v = new self(self::NONE, null, true);
        // Keep PHP's packed representation when the caller already supplied a
        // list. array_values() would eagerly duplicate a large COW array.
        $v->storage = array_is_list($values) ? $values : array_values($values);
        $v->listKeys = $keys !== null ? (array_is_list($keys) ? $keys : array_values($keys)) : null;
        return $v;
    }

    /** @param list<string> $keys @param list<Value> $values */
    /**
     * A record from keys and values side by side. A repeated key keeps its
     * first position and takes its last value, as RECORD does (spec §8).
     *
     * @param list<string> $keys @param list<Value> $values
     */
    public static function shaped(array $keys, array $values): self
    {
        return self::record($keys, $values);
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
            fail('E_BAD_ARG', $shape->size . ' key(s) and ' . count($values) . ' value(s) do not pair up', null);
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
    public static function record($keys, $values): self
    {
        if (!is_array($keys) || !is_array($values)) {
            self::badCall('record', 'an array of keys and an array of Values', is_array($keys) ? $values : $keys);
        }
        if (count($keys) !== count($values)) {
            fail('E_BAD_ARG', count($keys) . ' key(s) and ' . count($values) . ' value(s) do not pair up', null);
        }
        foreach ($values as $item) {
            if (!$item instanceof self) {
                fail('E_BAD_ARG', 'a record is built from Values, not ' . get_debug_type($item), null);
            }
        }
        if ($keys === []) {
            return self::none();
        }
        foreach ($keys as $key) {
            if (!is_string($key)) self::keyNotString($key);
        }
        // Distinct keys share a shape; a repeated key is set again, last wins.
        if (count(array_flip($keys)) === count($keys)) {
            return self::fromShape(RecordShape::intern($keys), $values);
        }
        $v = self::none();
        foreach ($keys as $i => $key) {
            $v->set($key, $values[$i]);
        }
        return $v;
    }

    /**
     * An entry's key: text, or a whole number spelled as text. `null` used to
     * become "" and an array "Array"; neither is a key.
     *
     * @param mixed $key
     */
    private static function entryKey($key): string
    {
        if (is_string($key)) return $key;
        if (is_int($key)) return (string) $key;
        fail('E_BAD_ARG', 'a key must be a string or an int, not ' . self::describe($key), null);
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
                if (!is_array($entry) || count($entry) !== 2) {
                    fail('E_BAD_ARG', 'an entry must be a [key, value] pair', null);
                }
                $key = self::entryKey($entry[0]);
                $values[] = $entry[1];
                $keys[] = $key;
                if (!$needsCustomKeys && $key !== (string) $expectedIndex) {
                    $needsCustomKeys = true;
                }
                $expectedIndex++;
            }
            return self::list($values, $needsCustomKeys ? $keys : null);
        }
        $keys = [];
        $values = [];
        foreach ($entries as $entry) {
            if (!is_array($entry) || count($entry) !== 2) {
                fail('E_BAD_ARG', 'an entry must be a [key, value] pair', null);
            }
            $key = self::entryKey($entry[0]);
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
        return $this->storage !== null ? count($this->storage) : ($this->children !== null ? count($this->children) : 0);
    }

    public function has(string $key): bool
    {
        if ($this->shape !== null) {
            return isset($this->shape->keyMap[$key]);
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                $this->listKeyMap ??= array_flip($this->listKeys);
                return isset($this->listKeyMap[$key]);
            }
            $index = self::listIndex($key, count($this->storage));
            return $index >= 0;
        }
        return $this->children !== null && array_key_exists($key, $this->children);
    }

    public function get(string $key): ?Value
    {
        if ($this->shape !== null) {
            $index = $this->shape->keyMap[$key] ?? null;
            return $index === null ? null : $this->storage[$index];
        }
        if ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                $this->listKeyMap ??= array_flip($this->listKeys);
                $index = $this->listKeyMap[$key] ?? null;
                return $index === null ? null : $this->storage[$index];
            }
            $index = self::listIndex($key, count($this->storage));
            return $index < 0 ? null : $this->storage[$index];
        }
        if ($this->children === null || !array_key_exists($key, $this->children)) {
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
        return $this->children !== null ? array_map('strval', array_keys($this->children)) : [];
    }

    /** @return list<Value> */
    public function values(): array
    {
        if ($this->storage !== null) {
            return $this->storage;
        }
        return $this->children !== null ? array_values($this->children) : [];
    }

    /**
     * Calls $callback(key, item) once per ELEMENT, in order, in the sense the
     * aggregates use: a collection's children under their keys, a scalar as a
     * single element under "1", and NONE as nothing. Each storage layout is
     * walked directly rather than through entries(), because this is the hot
     * loop of every aggregate and the intermediate list is what it cost. Two
     * builtin classes each carried a private copy of this (SEL-0040).
     *
     * @param callable(string,Value):void $callback
     */
    public function forEachElement(callable $callback): void
    {
        if ($this->isNull()) return;
        if ($this->isList && $this->storage !== null) {
            // Every read below is from a local copy taken before the first
            // callback (spec §7.3: an aggregate visits a snapshot). PHP arrays are
            // copy-on-write, so the copy is free until the callback writes.
            $keys = $this->listKeys;
            if ($keys !== null) {
                foreach ($this->storage as $i => $item) {
                    $callback($keys[$i], $item);
                }
            } else {
                foreach ($this->storage as $i => $item) {
                    $callback((string) ($i + 1), $item);
                }
            }
            return;
        }
        if ($this->shape !== null && $this->storage !== null) {
            $storage = $this->storage;
            foreach ($this->shape->keys as $i => $key) {
                $callback($key, $storage[$i]);
            }
            return;
        }
        if ($this->size() > 0) {
            if ($this->children !== null) {
                foreach ($this->children as $key => $item) $callback((string) $key, $item);
            }
            return;
        }
        if ($this->kind !== Value::NONE) $callback('1', $this);
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
        if ($this->children !== null) {
            foreach ($this->children as $k => $v) {
                $out[] = [(string) $k, $v];
            }
        }
        return $out;
    }

    /** Re-assigning an existing key keeps its original position. */
    public function set(string $key, Value $value): self
    {
        self::checkText($key);
        if ($this->shape !== null) {
            $index = $this->shape->keyMap[$key] ?? null;
            if ($index !== null) {
                $this->storage[$index] = $value;
                return $this;
            }
            $this->demote();
        } elseif ($this->isList && $this->storage !== null) {
            if ($this->listKeys !== null) {
                $this->listKeyMap ??= array_flip($this->listKeys);
                $index = $this->listKeyMap[$key] ?? null;
                if ($index !== null) {
                    $this->storage[$index] = $value;
                    return $this;
                }
                $this->demote();
            } else {
                $index = self::listIndex($key, count($this->storage));
                if ($index >= 0) {
                    $this->storage[$index] = $value;
                    return $this;
                }
                $this->demote();
            }
        }
        $this->children[$key] = $value;
        return $this;
    }

    /**
     * A shaped record or a packed list about to take a key its layout cannot
     * hold becomes an ordinary children map, entries in order.
     */
    private function demote(): void
    {
        $children = [];
        foreach ($this->entries() as [$key, $value]) {
            $children[$key] = $value;
        }
        $this->children = $children;
        $this->shape = null;
        $this->storage = null;
        $this->listKeys = null;
        $this->listKeyMap = null;
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
     * @return EagerDecimal
     */
    public function asDecimal(?array $pos = null): array
    {
        $v = $this->scalarSource($pos);
        if ($v->kind !== self::TEXT) {
            fail('E_NOT_NUM', 'expected a number, got ' . \Sel\Utf8::lower($v->kind), $pos);
        }
        if ($v->decVal !== null) {
            // The host sees today's array: a lazy value writes its digits out,
            // once (item 1).
            if ($v->decVal['digits'] === null) {
                $v->decVal = Dec::eager($v->decVal);
            }
            return $v->decVal;
        }
        $d = Dec::parse((string) $v->getScalar(), $pos);
        if ($d === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($v->getScalar()), $pos);
        }
        $v->decVal = $d;
        return $d;
    }

    /**
     * asDecimal() for the evaluator: a value's decimal as it is kept, which a
     * computed big number may keep lazily -- its magnitude as GMP, its digits not
     * yet written (item 1). Everything it is handed to is Dec's.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return Decimal
     */
    public function asDecimalLazy(?array $pos = null): array
    {
        $v = $this->scalarSource($pos);
        if ($v->kind === self::TEXT && $v->decVal !== null) {
            return $v->decVal;
        }
        return $this->asDecimal($pos);
    }

    /** Non-throwing probe for ISNUM. */
    public function looksNumeric(): bool
    {
        if ($this->kind === self::NONE && $this->size() === 0) {
            return false;
        }
        try {
            $v = $this->scalarSource(null);
            if ($v->kind !== self::TEXT) return false;
            if ($v->decVal !== null) return true;
            // Dec::parse answers null for text that is not a number, so a probe
            // never needs asDecimal()'s fail(): building a SelError (trace and a
            // json_encode'd message) and catching it made every non-numeric text
            // key cost ~7x a parsed one (PHP-P3).
            // A well-formed numeral too big to hold raises E_RANGE out of parse.
            // The probe answers no rather than raising, so ISNUM is true exactly
            // when the value can be used as a number — before the cap it said
            // TRUE for a 2 000 000-digit text that then failed on first use.
            $d = Dec::parse((string) $v->getScalar(), null);
            if ($d === null) return false;
            $v->decVal = $d;
            return true;
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

    /**
     * A copy made to be stored `$below` levels down: an assignment to a target
     * whose path is that long, or a collector holding the value as one of its
     * elements (`$below` = 1). The value's own depth is checked from there, so a
     * 200-level value is refused one level down, at `$pos` (spec §3.4, §6.4).
     *
     * @param array<string,mixed>|null $pos
     */
    public function copyBelow(int $below, ?array $pos = null): Value
    {
        return $this->copyAt($below + 1, $pos);
    }

    /**
     * A copy of a record made to be written through by a program that assigns
     * only to the top-level names in `$writable` (PHP-P22): those children are
     * deep-copied, every other top-level child is shared with the original. The
     * program cannot reach a shared child through an assignment, so the original is
     * never written to, and the copy costs the size of what is written rather than
     * of the whole context. A root that is not a plain record is copied whole.
     *
     * @param list<string> $writable
     */
    public function copyWritable(array $writable): Value
    {
        $w = array_fill_keys($writable, true);
        if ($this->shape !== null && $this->storage !== null) {
            $values = [];
            foreach ($this->shape->keys as $i => $k) {
                $child = $this->storage[$i];
                $values[] = isset($w[$k]) ? $child->copyAt(2, null) : $child;
            }
            return self::fromShape($this->shape, $values);
        }
        if ($this->isList || $this->storage !== null || $this->children === null) {
            return $this->copy();
        }
        $out = new self($this->kind, $this->scalar, false);
        $out->decVal = $this->decVal;
        $out->children = [];
        foreach ($this->children as $k => $v) {
            $out->children[$k] = isset($w[(string) $k]) ? $v->copyAt(2, null) : $v;
        }
        return $out;
    }

    /** @param array<string,mixed>|null $pos */
    private function copyAt(int $depth, ?array $pos): Value
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', $pos);
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
            // Built directly, not through list(): the elements are copies of
            // Values and the keys are this list's own, already validated (text,
            // distinct, paired), so list()'s instanceof pass, per-key preg and
            // duplicate table would only re-prove it on every assignment, `,` and
            // aggregate collect of a keyed list (PHP-P16). The key map is rebuilt
            // lazily, as for any fresh list.
            $v = new self(self::NONE, null, true);
            $v->storage = $values;
            $v->listKeys = $this->listKeys;
            return $v;
        }
        $out = new self($this->kind, $this->scalar, $this->isList);
        $out->decVal = $this->decVal;
        if ($this->children !== null) {
            $out->children = [];
            foreach ($this->children as $k => $v) {
                $out->children[$k] = $v->copyAt($depth + 1, $pos);
            }
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
        // A value with no children is keyed by its own kind, length and scalar
        // (PHP-P25): no HashContext, and injective, which is all a bucket key has
        // to be -- every bucket confirms with eql(). It cannot equal the digest of
        // a container, which is sixteen hex digits and has no ':'.
        if ($this->size() === 0) {
            return $this->scalarHashPart();
        }
        $hash = hash_init('xxh3');
        $this->updateStructuralHash($hash, 1);
        return hash_final($hash);
    }

    /** The kind, length and scalar, unambiguously: `TEXT:2:ab`. */
    private function scalarHashPart(): string
    {
        $scalar = match ($this->kind) {
            self::NONE => '',
            self::TEXT, self::BIN => (string) $this->getScalar(),
            self::BOOL => $this->scalar ? '1' : '0',
        };
        return $this->kind . ':' . strlen($scalar) . ':' . $scalar;
    }

    private function updateStructuralHash(\HashContext $hash, int $depth): void
    {
        if ($depth > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', null);
        }
        // Hash logical scalar/children only: eqlAt ignores the storage layout
        // and list marker. Packed storage may also carry a host-set scalar.
        hash_update($hash, $this->scalarHashPart() . ';');
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
        if ($this->children !== null) {
            foreach ($this->children as $key => $value) {
                $key = (string) $key;
                hash_update($hash, strlen($key) . ':' . $key . '=');
                $value->updateStructuralHash($hash, $depth + 1);
                hash_update($hash, ';');
            }
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
                    || !Dec::sameDigits($this->decVal, $other->decVal)) {
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

    /** @var array<string,string>|null */
    private static ?array $dumpEscapes = null;

    public static function quoteDump(string $s): string
    {
        // Byte-level: every character that needs an escape is ASCII, and no ASCII
        // byte occurs inside a multi-byte character. (This split the text into an
        // array of characters first — gigabytes for a text at the length cap.)
        if (self::$dumpEscapes === null) {
            $map = ['\\' => '\\\\', '"' => '\\"', "\n" => '\\n', "\t" => '\\t', "\r" => '\\r'];
            for ($i = 0; $i < 0x20; $i++) {
                if (!isset($map[chr($i)])) $map[chr($i)] = sprintf('\\u%04x', $i);
            }
            self::$dumpEscapes = $map;
        }
        return '"' . strtr($s, self::$dumpEscapes) . '"';
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
            fail('E_BAD_ARG', 'floats have no exact decimal form; pass a numeric string instead', null);
        }
        if (is_string($x)) {
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
                $key = (string) $k;
                self::checkText($key);
                $keys[] = $key;
                $values[] = self::fromNativeAt($item, $depth + 1);
            }
            return self::record($keys, $values);
        }
        fail('E_BAD_ARG', 'cannot convert ' . gettype($x) . ' to SEL', null);
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
        if ($this->isList && $this->storage !== null && $this->listKeys === null) {
            // Keyed 1..n: a packed PHP list, which fromNative reads back as one.
            $out = [];
            foreach ($this->storage as $value) {
                $out[] = $value->toNativeAt($depth + 1);
            }
            return $out;
        }
        if ($this->isList && $this->storage === null && $this->children !== null && array_keys($this->children) === range(1, count($this->children))) {
            $out = [];
            foreach ($this->children as $value) {
                $out[] = $value->toNativeAt($depth + 1);
            }
            return $out;
        }
        // Any other keys -- the ones a FILTER kept, say -- travel as written, so
        // the round trip keeps them (spec §8; review 2026-09-25 HOST-08). The one
        // shape PHP cannot keep is a record keyed "0" .. "n-1", which is a list
        // to PHP (the named exception).
        $out = [];
        if ($this->isList && $this->storage !== null) {
            foreach ($this->listKeys as $i => $key) {
                $out[$key] = $this->storage[$i]->toNativeAt($depth + 1);
            }
            return $out;
        }
        if ($scalar !== null) {
            // A value's own scalar travels under "_"; with a child of that name
            // too, one of them would be lost (review 2026-09-25 HOST-01).
            if ($this->has('_')) {
                fail('E_BAD_ARG', 'a value with both a scalar and a child named "_" has no native form', null);
            }
            $out['_'] = $scalar;
        }
        if ($this->shape !== null && $this->storage !== null) {
            foreach ($this->shape->keys as $i => $key) {
                $out[$key] = $this->storage[$i]->toNativeAt($depth + 1);
            }
            return $out;
        }
        if ($this->children !== null) {
            foreach ($this->children as $key => $value) {
                $out[$key] = $value->toNativeAt($depth + 1);
            }
        }
        return $out;
    }
}
