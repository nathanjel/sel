<?php
// Dialect lookup, and the runtime registration an application extends the map
// with. The generated tables in MapData are already flattened, so a shipped
// lookup is a hash access; the overlay written here is what re-introduces the
// extends chain, and it is the only thing that does.

declare(strict_types=1);

namespace Sel\Sql;

final class Map
{
    public const SECTIONS = ['ops', 'funcs', 'skel'];

    /** Sentinel for "no dialect in the chain mentioned this key". */
    public const MISSING = "\0missing";

    /** Dialects declared at run time. @var array<string, array<string,mixed>> */
    private static array $extra = [];

    /**
     * Runtime entries, consulted before the generated table.
     * dialect => section => key => entry|callable
     *
     * @var array<string, array<string, array<string, mixed>>>
     */
    private static array $overlay = [];

    // --- registration -------------------------------------------------------

    /**
     * Declare a dialect. The usual reason is an older or newer server than the
     * shipped map assumes, which needs no special code because a version is
     * only another link in the chain:
     *
     *     Map::defineDialect('mariadb-11.8', ['extends' => 'mariadb', 'version' => '11.8']);
     *
     * @param array{extends?:string, version?:string, target?:bool, lexical?:array<string,mixed>} $spec
     */
    public static function defineDialect(string $name, array $spec): void
    {
        $extends = $spec['extends'] ?? null;
        if ($extends === null) {
            throw new \LogicException("SQL dialect {$name} must extend another dialect");
        }
        if (!self::exists($extends)) {
            throw new \LogicException("SQL dialect {$name} extends {$extends}, which does not exist");
        }
        self::$extra[$name] = [
            'extends' => $extends,
            'version' => $spec['version'] ?? self::record($extends)['version'],
            'target' => $spec['target'] ?? true,
            'lexical' => $spec['lexical'] ?? [],
        ];
    }

    /**
     * Define or withdraw one entry. Unlike Sel\Registry::define, redefinition is
     * allowed and the last writer wins: a duplicate SEL function is always a
     * bug, while a duplicate SQL entry is usually an application deliberately
     * overriding a shipped default for its own schema or server build.
     *
     * Passing a string withdraws the entry and makes the string the reason the
     * caller is given; passing null withdraws it without one.
     *
     * @param array<string,mixed>|string|null $entry
     */
    public static function define(string $dialect, string $section, string $key, $entry): void
    {
        self::checkSection($section);
        if (!self::exists($dialect)) {
            throw new \LogicException("SQL dialect {$dialect} does not exist");
        }
        self::$overlay[$dialect][$section][$section === 'ops' ? $key : strtoupper($key)] = $entry;
    }

    /**
     * The escape hatch, for what a template cannot say. A builder receives the
     * already-rendered arguments and returns a Fragment. This is the SQL layer's
     * equivalent of the `fn` in Sel\Registry::define.
     *
     * @param callable(Emit, list<Fragment>, array<string,mixed>): Fragment $fn
     */
    public static function defineBuilder(string $dialect, string $section, string $key, callable $fn): void
    {
        self::define($dialect, $section, $key, ['builder' => $fn]);
    }

    /** Forget every runtime registration. For tests; nothing else should need it. */
    public static function reset(): void
    {
        self::$extra = [];
        self::$overlay = [];
    }

    // --- lookup -------------------------------------------------------------

    public static function exists(string $dialect): bool
    {
        return isset(self::$extra[$dialect]) || isset(MapData::DIALECTS[$dialect]);
    }

    /** @return array<string,mixed> */
    private static function record(string $dialect): array
    {
        return self::$extra[$dialect] ?? MapData::DIALECTS[$dialect];
    }

    /** Every dialect that may be named in a translate() call, sorted. */
    public static function targets(): array
    {
        $out = [];
        foreach (array_keys(MapData::DIALECTS) as $d) {
            if (MapData::DIALECTS[$d]['target']) {
                $out[] = $d;
            }
        }
        foreach (self::$extra as $d => $r) {
            if ($r['target']) {
                $out[] = $d;
            }
        }
        sort($out);
        return array_values(array_unique($out));
    }

    /**
     * Check a dialect may be translated to. A base is not a target: `ansi` and
     * `mysql-family` name no server anyone runs, and a dialect no database
     * implements is not one a caller should be able to aim at.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     */
    public static function requireTarget(string $dialect, ?array $pos = null): void
    {
        if (!self::exists($dialect)) {
            refuse('E_SQL_DIALECT',
                "there is no SQL dialect {$dialect}; known targets are "
                . implode(', ', self::targets()), $pos);
        }
        if (!self::record($dialect)['target']) {
            refuse('E_SQL_DIALECT',
                "{$dialect} is a base other dialects inherit from, not a server anyone runs; "
                . 'translate to one of ' . implode(', ', self::targets()), $pos);
        }
    }

    /** Self first, then extends, up to ansi. @return list<string> */
    public static function chain(string $dialect): array
    {
        $out = [];
        $cur = $dialect;
        while ($cur !== null && self::exists($cur) && !in_array($cur, $out, true)) {
            $out[] = $cur;
            $cur = self::record($cur)['extends'] ?? null;
        }
        return $out;
    }

    public static function version(string $dialect): string
    {
        return (string) self::record($dialect)['version'];
    }

    /**
     * A lexical value. Runtime dialects may override individual keys; otherwise
     * the generated table already holds the flattened result.
     *
     * @return mixed
     */
    public static function lexical(string $dialect, string $key)
    {
        foreach (self::chain($dialect) as $d) {
            $r = self::record($d);
            if (isset($r['lexical'][$key])) {
                return $r['lexical'][$key];
            }
        }
        return null;
    }

    /**
     * One entry, or MISSING. The overlay is consulted first and walks the chain;
     * the generated table does not need walking because the generator flattened
     * it.
     *
     * @return mixed
     */
    public static function entry(string $dialect, string $section, string $key)
    {
        self::checkSection($section);
        $chain = self::chain($dialect);

        // The whole overlay chain first, and only then the generated table.
        // Interleaving the two per level would look tidier and would be wrong:
        // the generated tables are already flattened, so a generated hit at the
        // leaf would shadow a runtime entry registered against a base, and
        // registering against `ansi` is documented to reach every dialect.
        foreach ($chain as $d) {
            if (array_key_exists($key, self::$overlay[$d][$section] ?? [])) {
                return self::$overlay[$d][$section][$key];
            }
        }
        foreach ($chain as $d) {
            if (array_key_exists($key, MapData::DIALECTS[$d][$section] ?? [])) {
                return MapData::DIALECTS[$d][$section][$key];
            }
        }
        return self::MISSING;
    }

    private static function checkSection(string $section): void
    {
        if (!in_array($section, self::SECTIONS, true)) {
            throw new \LogicException(
                "unknown map section {$section}; use " . implode(', ', self::SECTIONS));
        }
    }

    /** Dotted-numeric, as sql/MAP.md §4.5 specifies and nothing cleverer. */
    public static function versionAtLeast(string $have, string $want): bool
    {
        $a = array_map('intval', explode('.', $have));
        $b = array_map('intval', explode('.', $want));
        $n = max(count($a), count($b));
        for ($i = 0; $i < $n; $i++) {
            $x = $a[$i] ?? 0;
            $y = $b[$i] ?? 0;
            if ($x !== $y) {
                return $x > $y;
            }
        }
        return true;
    }
}
