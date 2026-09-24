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
     * A host's own binding function (define with 'binds' outside the manifest,
     * examples/fn-complex) has no manifest forms; it gets the two classic shapes.
     */
    private const GENERIC_FORMS = [
        [['outer', 'inner'], null, ['_', '_K']],
        [['outer', 'binder', 'inner'], [1, 'name'], ['_K']],
    ];

    /**
     * Which argument of a binding call runs where (spec/builtins.md, "Binding
     * forms"): ['scopes' => per-argument 'outer' (evaluated where the call is),
     * 'binder' (a bare name, never evaluated) or 'inner' (once per element),
     * 'binds' => the names bound inside]. Null when the call is not a binding
     * builtin or no form takes this count: the evaluator would refuse it, and
     * a static consumer reads every argument where the call stands. The
     * dependency walker and the SQL layer's stage 1 both classify through
     * here, so they cannot disagree.
     *
     * @param list<array<string,mixed>> $args
     * @return array{scopes:list<string>,binds:list<string>}|null
     */
    public static function bindingForm(string $name, array $args): ?array
    {
        $upper = strtoupper($name);
        $forms = BuiltinManifest::FORMS[$upper] ?? null;
        if ($forms === null) {
            $spec = self::$table[$upper] ?? null;
            if ($spec === null || !$spec['binds']) return null;
            $forms = self::GENERIC_FORMS;
        }
        $count = count($args);
        foreach ($forms as [$scopes, $when, $binds]) {
            if (count($scopes) !== $count) continue;
            if ($when !== null) {
                $a = $args[$when[0]];
                $ok = $when[1] === 'name'
                    ? ($a['t'] === 'var' && empty($a['grouped']))
                    : $a['t'] === 'text';
                if (!$ok) continue;
            }
            foreach ($scopes as $i => $scope) {
                if ($scope === 'binder' && $args[$i]['t'] === 'var') $binds[] = $args[$i]['name'];
            }
            return ['scopes' => $scopes, 'binds' => $binds];
        }
        return null;
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
    /** @var array<string, true> names registered through registerFunction(), which alone may be replaced */
    private static array $host = [];

    /**
     * An application's own strict function (spec/SPEC.md §8.1). It adds to
     * the language and never changes it: a builtin's name or a reserved word
     * is refused, and re-registering a host function replaces it. A bad
     * registration is a programming error, so it throws
     * InvalidArgumentException rather than SelError.
     *
     * @param callable(Args): Value $fn
     */
    public static function registerFunction(string $name, int $min, int $max, callable $fn): void
    {
        if (preg_match('/\A[A-Za-z][A-Za-z0-9_]*\z/', $name) !== 1) {
            throw new \InvalidArgumentException(
                "SEL function name must be ASCII letters, digits and _, starting with a letter: {$name}");
        }
        $key = strtoupper($name);
        if (in_array($key, Lexer::RESERVED, true)) {
            throw new \InvalidArgumentException("{$key} is a reserved word");
        }
        if (isset(self::$table[$key]) && !isset(self::$host[$key])) {
            throw new \InvalidArgumentException("{$key} is a builtin; a host function cannot replace it");
        }
        if ($min < 0 || $max < $min) {
            throw new \InvalidArgumentException("SEL function {$key}: arity must be whole numbers with 0 <= min <= max");
        }
        self::$table[$key] = [
            'name' => $key,
            'min' => $min,
            'max' => $max,
            'lazy' => false,
            'binds' => false,
            'arityError' => null,
            'fn' => static function (Args $args) use ($fn, $key): Value {
                $result = $fn($args);
                if (!$result instanceof Value) {
                    throw new \UnexpectedValueException(
                        "SEL function {$key} returned " . get_debug_type($result) . ', not a Value');
                }
                return $result;
            },
        ];
        self::$host[$key] = true;
    }

    /**
     * The [min, max] of a host function registered with registerFunction(), or
     * null when the name is not one. The SQL layer reads it: a host function's
     * SQL spelling is checked against, and recorded with, this arity.
     *
     * @return array{0:int,1:int}|null
     */
    public static function hostArity(string $name): ?array
    {
        $key = strtoupper($name);
        if (!isset(self::$host[$key])) {
            return null;
        }
        return [self::$table[$key]['min'], (int) self::$table[$key]['max']];
    }

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
