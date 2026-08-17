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

final class Value
{
    public const NONE = 'NONE';
    public const TEXT = 'TEXT';
    public const BIN = 'BIN';
    public const BOOL = 'BOOL';

    public string $kind;
    /** @var string|bool|null */
    public $scalar;
    /** @var array<array-key, Value> */
    public array $children = [];

    /** @param string|bool|null $scalar */
    private function __construct(string $kind, $scalar)
    {
        $this->kind = $kind;
        $this->scalar = $scalar;
    }

    public static function none(): self
    {
        return new self(self::NONE, null);
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
            return new self(self::TEXT, Dec::format($d));
        }
        $parsed = Dec::parse($d);
        if ($parsed === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($d));
        }
        return new self(self::TEXT, Dec::format($parsed));
    }

    public static function int(int $n): self
    {
        return new self(self::TEXT, (string) $n);
    }

    /** Builds a list keyed "1".."n". Used by `,` and by list-returning built-ins. */
    /** @param list<Value> $values */
    public static function list(array $values): self
    {
        $v = self::none();
        $i = 0;
        foreach ($values as $x) {
            $v->set((string) (++$i), $x);
        }
        return $v;
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
        return count($this->children);
    }

    public function has(string $key): bool
    {
        return array_key_exists($key, $this->children);
    }

    public function get(string $key): ?Value
    {
        return $this->children[$key] ?? null;
    }

    /** @return list<string> */
    public function keys(): array
    {
        return array_map('strval', array_keys($this->children));
    }

    /** @return list<Value> */
    public function values(): array
    {
        return array_values($this->children);
    }

    /** @return list<array{0:string,1:Value}> */
    public function entries(): array
    {
        $out = [];
        foreach ($this->children as $k => $v) {
            $out[] = [(string) $k, $v];
        }
        return $out;
    }

    /** Re-assigning an existing key keeps its original position. */
    public function set(string $key, Value $value): self
    {
        $this->children[$key] = $value;
        return $this;
    }

    // --- scalar context (§3.2) ----------------------------------------------

    /** @param array{line:int,col:int,offset:int}|null $pos */
    public function scalarSource(?array $pos = null): Value
    {
        $v = $this;
        $guard = 0;
        while ($v->kind === self::NONE) {
            if (!$v->children) {
                fail('E_NO_SCALAR', 'value has no scalar and no children', $pos);
            }
            foreach ($v->children as $first) {
                $v = $first;
                break;
            }
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
            return (string) $v->scalar;
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
            return (string) $v->scalar;
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
        $d = Dec::parse((string) $v->scalar);
        if ($d === null) {
            fail('E_NOT_NUM', 'not a number: ' . json_encode($v->scalar), $pos);
        }
        return $d;
    }

    /** Non-throwing probe for ISNUM. */
    public function looksNumeric(): bool
    {
        if ($this->kind === self::NONE && !$this->children) {
            return false;
        }
        try {
            $v = $this->scalarSource(null);
        } catch (SelError) {
            return false;
        }
        return $v->kind === self::TEXT && Dec::parse((string) $v->scalar) !== null;
    }

    // --- copying ------------------------------------------------------------

    /** Assignment copies by value: two variables never share structure (§5.7). */
    public function copy(): Value
    {
        $out = new self($this->kind, $this->scalar);
        foreach ($this->children as $k => $v) {
            $out->children[$k] = $v->copy();
        }
        return $out;
    }

    // --- structural equality (§5.4) -----------------------------------------

    public function eql(Value $other): bool
    {
        if ($this->kind !== $other->kind) {
            return false;
        }
        if ($this->scalar !== $other->scalar) {
            return false;
        }
        if ($this->size() !== $other->size()) {
            return false;
        }
        $a = $this->entries();
        $b = $other->entries();
        foreach ($a as $i => [$key, $val]) {
            if ($key !== $b[$i][0]) {   // key order is normative
                return false;
            }
            if (!$val->eql($b[$i][1])) {
                return false;
            }
        }
        return true;
    }

    // --- canonical dump (conformance/README.md) -----------------------------

    public function dump(): string
    {
        $s = match ($this->kind) {
            self::NONE => '-',
            self::TEXT => 't' . self::quoteDump((string) $this->scalar),
            self::BIN => 'b' . bin2hex((string) $this->scalar),
            self::BOOL => $this->scalar ? 'TRUE' : 'FALSE',
        };
        if (!$this->children) {
            return $s;
        }
        $parts = [];
        foreach ($this->entries() as [$k, $v]) {
            $parts[] = self::quoteDump($k) . '=' . $v->dump();
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
        if ($x === null) {
            return self::none();
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
            $v = self::none();
            // A packed 0-based array is a list, and SEL lists are keyed from 1 —
            // that is what `,` produces and what the JS host produces for a JS
            // array. Without the renumbering, ITEMS[1] would mean the first line
            // on the frontend and the second on the backend.
            if (array_is_list($x)) {
                $i = 0;
                foreach ($x as $item) {
                    $v->set((string) (++$i), self::fromNative($item));
                }
                return $v;
            }
            foreach ($x as $k => $item) {
                $v->set((string) $k, self::fromNative($item));
            }
            return $v;
        }
        throw new \InvalidArgumentException('cannot convert ' . gettype($x) . ' to SEL');
    }

    /** @return mixed */
    public function toNative()
    {
        $scalar = $this->kind === self::NONE ? null : $this->scalar;
        if (!$this->children) {
            return $scalar;
        }
        $out = [];
        if ($scalar !== null) {
            $out['_'] = $scalar;
        }
        foreach ($this->entries() as [$k, $v]) {
            $out[$k] = $v->toNative();
        }
        return $out;
    }
}
