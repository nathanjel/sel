<?php
// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

declare(strict_types=1);

namespace Sel;

final class Registry
{
    /** @var array<string, array<string,mixed>> */
    private static array $table = [];

    /**
     * The shipped table is authored once, in spec/builtins.json, and rendered
     * into BuiltinManifest.php. A name the manifest knows is held to it:
     * min/max/lazy/binds must agree, and the extra arity rule (COND's odd
     * count, LINK's three-or-five) comes from the manifest rather than from
     * the caller — one body for all five hosts. A name it does not know is a
     * host's own function (examples/fn-*) and passes.
     *
     * @param array<string,mixed> $spec
     */
    public static function define(array $spec): void
    {
        $name = strtoupper($spec['name']);
        if (isset(self::$table[$name])) {
            throw new \LogicException("SEL function {$name} defined twice");
        }
        $min = $spec['min'];
        $max = $spec['max'] ?? $spec['min'];   // PHP_INT_MAX for variadic
        $lazy = (bool) ($spec['lazy'] ?? false);
        $binds = (bool) ($spec['binds'] ?? false);
        // Optional extra arity rule, checked at compile time after min/max.
        // Returns a message when the count is wrong, or null when it is fine.
        $arityError = $spec['arityError'] ?? null;
        $m = BuiltinManifest::BUILTINS[$name] ?? null;
        if ($m !== null) {
            [$mMin, $mMax, $mLazy, $mBinds, $rule] = $m;
            $wrong = [];
            if ($min !== $mMin) $wrong[] = "min {$min} vs {$mMin}";
            if ($max !== $mMax) $wrong[] = "max {$max} vs {$mMax}";
            if ($lazy !== $mLazy) $wrong[] = 'lazy ' . var_export($lazy, true) . ' vs ' . var_export($mLazy, true);
            if ($binds !== $mBinds) $wrong[] = 'binds ' . var_export($binds, true) . ' vs ' . var_export($mBinds, true);
            if ($arityError !== null) $wrong[] = 'an arity rule of its own, which the manifest owns';
            if ($wrong !== []) {
                throw new \LogicException("SEL function {$name} disagrees with spec/builtins.json: " . implode('; ', $wrong));
            }
            if ($rule !== null) $arityError = self::manifestArityError($rule);
        }
        self::$table[$name] = [
            'name' => $name,
            'min' => $min,
            'max' => $max,
            'lazy' => $lazy,
            'binds' => $binds,
            'arityError' => $arityError,
            'fn' => $spec['fn'],
        ];
    }

    /** @param array<int,mixed> $rule */
    private static function manifestArityError(array $rule): callable
    {
        [$kind, $detail, $message] = $rule;
        if ($kind === 'parity') {
            $odd = $detail === 'odd';
            return static fn (int $n): ?string =>
                ($n % 2 === 1) === $odd ? null : str_replace('{count}', (string) $n, $message);
        }
        $allowed = array_fill_keys($detail, true);
        return static fn (int $n): ?string =>
            isset($allowed[$n]) ? null : str_replace('{count}', (string) $n, $message);
    }

    /**
     * Called once the shipped modules have registered: a manifest entry with
     * no definition is a host that would silently lack a builtin the others
     * have.
     */
    public static function assertManifestCovered(): void
    {
        $missing = [];
        foreach (BuiltinManifest::BUILTINS as $name => $_) {
            if (!isset(self::$table[$name])) $missing[] = $name;
        }
        if ($missing !== []) {
            throw new \LogicException('spec/builtins.json names builtins this host never defined: ' . implode(', ', $missing));
        }
    }

    /** @return array<string,mixed>|null */
    public static function lookup(string $name): ?array
    {
        return self::$table[strtoupper($name)] ?? null;
    }

    /** @return list<string> */
    public static function names(): array
    {
        $names = array_keys(self::$table);
        sort($names);
        return $names;
    }
}
