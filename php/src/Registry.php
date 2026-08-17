<?php
// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

declare(strict_types=1);

namespace Sel;

final class Registry
{
    /** @var array<string, array<string,mixed>> */
    private static array $table = [];

    /** @param array<string,mixed> $spec */
    public static function define(array $spec): void
    {
        $name = strtoupper($spec['name']);
        if (isset(self::$table[$name])) {
            throw new \LogicException("SEL function {$name} defined twice");
        }
        self::$table[$name] = [
            'name' => $name,
            'min' => $spec['min'],
            'max' => $spec['max'] ?? $spec['min'],   // PHP_INT_MAX for variadic
            'lazy' => (bool) ($spec['lazy'] ?? false),
            'binds' => (bool) ($spec['binds'] ?? false),
            // Optional extra arity rule, checked at compile time after min/max.
            // Returns a message when the count is wrong, or null when it is fine.
            'arityError' => $spec['arityError'] ?? null,
            'fn' => $spec['fn'],
        ];
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
