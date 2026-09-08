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

    /**
     * Dialects whose numericGuard has been checked against their ISNUM. Keyed by
     * name, because the answer cannot change once both are registered and the
     * shipped dialects pass trivially.
     *
     * @var array<string,true>
     */
    private static array $guardChecked = [];

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
    /** Every key defineDialect() accepts. sql/MAP.md §3 is the normative list. */
    public const DIALECT_KEYS = ['extends', 'version', 'target', 'lexical'];

    public static function defineDialect(string $name, array $spec): void
    {
        if (self::exists($name)) {
            throw new \LogicException(
                "SQL dialect {$name} is already defined; a name means one dialect");
        }
        // The keys a dialect declaration carries, and nothing else. `ops`, `funcs`
        // and `skel` are NOT among them -- they are defined one entry at a time
        // with define() -- and passing them here used to be accepted and silently
        // dropped, which is a registration that looks like it worked.
        $unknown = array_diff(array_keys($spec), self::DIALECT_KEYS);
        if ($unknown) {
            sort($unknown);
            throw new \LogicException("SQL dialect {$name} declares "
                . implode(', ', $unknown) . ', which a dialect declaration does not '
                . 'carry; ops, funcs and skel entries are defined one at a time '
                . 'with define()');
        }

        // array_key_exists, not `?? null`: a dialect with no parent is a real
        // thing -- `ansi` is one -- but forgetting the key is a typo, and the two
        // must not look alike. Without this a missing `extends` would quietly
        // produce a root that inherits nothing and answers every lookup MISSING.
        if (!array_key_exists('extends', $spec)) {
            throw new \LogicException("SQL dialect {$name} must say what it extends; "
                . 'write extends: null for a dialect with no parent, as ansi has');
        }
        $extends = $spec['extends'];
        if ($extends !== null && !self::exists($extends)) {
            throw new \LogicException("SQL dialect {$name} extends {$extends}, which does not exist");
        }
        // A root inherits nothing, so it has to state its own version.
        if ($extends === null) {
            if (($spec['version'] ?? null) === null) {
                throw new \LogicException("SQL dialect {$name} extends nothing, so it "
                    . 'must declare a version; there is none to inherit');
            }
            $version = $spec['version'];
        } else {
            $version = $spec['version'] ?? self::record($extends)['version'];
        }
        // Dotted-numeric, as sql/MAP.md §4.5 says and nothing cleverer. A live
        // server reports "11.8.8-MariaDB", which is the natural thing to pass and
        // is not a version this map can compare: PHP's intval read it as 11.8.8
        // by guessing and Python's int() raised a ValueError out of the first
        // translation that had a `since`. Refused here, at the line that wrote
        // it, so nothing downstream has to guess.
        if (!is_string($version) || preg_match('/\A[0-9]+(\.[0-9]+)*\z/', $version) !== 1) {
            throw new \LogicException("SQL dialect {$name} has version "
                . var_export($version, true) . ', which is not dotted-numeric; '
                . 'strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)');
        }
        $target = $spec['target'] ?? true;
        if (!is_bool($target)) {
            throw new \LogicException("SQL dialect {$name} has a target that is not "
                . 'a boolean; truthiness differs between hosts and must not decide this');
        }
        $lexical = $spec['lexical'] ?? [];
        if (!is_array($lexical)) {
            throw new \LogicException("SQL dialect {$name} has a lexical that is not a map");
        }
        foreach ($lexical as $k => $v) {
            self::checkLexical((string) $k, $v, "SQL dialect {$name}");
        }
        self::$extra[$name] = [
            'extends' => $extends,
            'version' => $version,
            'target' => $target,
            'lexical' => $lexical,
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
        self::checkKey($section, $key);
        self::checkEntry($section, $key, $entry);
        // Only `funcs` keys are SEL function names, which are case-insensitive.
        // `ops` keys are operator tokens and `skel` keys are camel-case names
        // the translator looks up verbatim — upper-casing those stored a
        // registered skeleton under a key nothing ever reads, which made the
        // documented escape hatch silently dead.
        self::$overlay[$dialect][$section][$section === 'funcs' ? strtoupper($key) : $key]
            = $entry;
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
        self::$guardChecked = [];
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
        // array_key_exists, not isset, and for the same reason entry() uses it:
        // sql/MAP.md §3 says a null lexical value is a WITHDRAWAL -- "a null
        // binaryLiteral refuses BIN literals" -- and isset() reads that as
        // "absent" and walks on to the base, which handed the withdrawn value
        // back. The documented withdrawal was unimplementable, in both hosts.
        foreach (self::chain($dialect) as $d) {
            $r = self::record($d);
            if (array_key_exists($key, $r['lexical'] ?? [])) {
                return $r['lexical'][$key];
            }
        }
        return null;
    }

    /**
     * sql/MAP.md §7 rule 10, asked at the moment the guard is used.
     *
     * The generator checks it when the map is built, and for six months that was
     * the whole of it: an application registering its own dialect could declare a
     * `numericGuard` that disagreed with its `ISNUM`, or one that tested nothing,
     * and nothing refused it. MAP.md said so and filed it beside a binding
     * declared NUM over a column that is not -- the caller's promise.
     *
     * It does not belong there. A wrong `NUM` declaration is a claim the caller
     * makes about their own data; a wrong `numericGuard` is a claim about SEL's
     * numeral grammar, which the caller has no way to check and every other
     * lexical key fails loudly about. This one fails silently: it emits SQL that
     * answers where SEL would not, which is the one outcome docs/SQL-KINDS.md
     * exists to rule out. The first external user of this layer registered a
     * derived dialect on their first day, overriding one lexical key. It was
     * `textCollate`; it could have been this.
     *
     * Checked here rather than in defineDialect because registration has no end:
     * `funcs.ISNUM` is defined one entry at a time with define(), so at the
     * moment a dialect is declared its ISNUM may not exist yet. By the time a
     * guard is being USED, everything either side of the rule is registered.
     */
    public static function checkNumericGuard(string $dialect): void
    {
        if (isset(self::$guardChecked[$dialect])) {
            return;
        }
        self::$guardChecked[$dialect] = true;
        $guard = self::lexical($dialect, 'numericGuard');
        if (!is_string($guard)) {
            return;
        }
        $isnum = self::entry($dialect, 'funcs', 'ISNUM');
        $tpl = is_array($isnum) ? ($isnum['tpl'] ?? null) : null;
        if (!is_string($tpl)) {
            throw new \LogicException("SQL dialect {$dialect} declares a numericGuard "
                . 'but maps no funcs.ISNUM with a template for it to agree with; the two '
                . 'ask the same question and sql/MAP.md §7 rule 10 is that one place '
                . 'defines a thing');
        }
        // Every quoted run, not the first: two genuinely different numeral tests
        // that happen to share an earlier literal -- a flag, a collation clause --
        // compare equal if only the first is read. The generator learned this from
        // a decoy that defeated it.
        $want = self::quotedRuns($tpl);
        if ($want === []) {
            throw new \LogicException("SQL dialect {$dialect} maps a funcs.ISNUM that "
                . 'carries no quoted pattern, so its numericGuard has nothing to agree with');
        }
        $missing = array_values(array_diff($want, self::quotedRuns($guard)));
        if ($missing !== []) {
            throw new \LogicException("SQL dialect {$dialect} declares a numericGuard that "
                . 'does not carry ' . implode(', ', array_map(
                    static fn ($m) => "'{$m}'", $missing))
                . ", which its funcs.ISNUM tests; they ask the same question, and a guard "
                . 'that asks a different one answers for rows SEL refuses');
        }
    }

    /** @return list<string> */
    private static function quotedRuns(string $tpl): array
    {
        preg_match_all("/'([^']*)'/", $tpl, $m);
        return $m[1];
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
        if (self::$trace !== null) {
            self::$trace["{$section}.{$key}"] = true;
        }
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

    /**
     * Every key one section of a dialect resolves, overlay and generated table
     * together, with the entry each resolves to.
     *
     * The chain is walked leaf-first so a nearer definition wins, which is the
     * same precedence entry() applies one key at a time. Used by the coverage
     * gate to ask what there is to cover; nothing in a translation needs it.
     *
     * @return array<string,mixed>
     */
    public static function entries(string $dialect, string $section): array
    {
        self::checkSection($section);
        // array_key_exists, not ??=, and for the same reason entry() uses it:
        // a dialect withdraws an entry by defining it as null, and `??=` reads
        // that as "not set yet" and lets the base's live entry through. So
        // entry() said a withdrawn entry was gone while entries() still listed
        // it -- and the coverage gate reads entries(), so a withdrawn entry
        // stayed in the denominator and was demanded of the corpus forever.
        $out = [];
        foreach (self::chain($dialect) as $d) {
            foreach ((self::$overlay[$d][$section] ?? []) as $k => $v) {
                if (!array_key_exists($k, $out)) {
                    $out[$k] = $v;
                }
            }
        }
        foreach (self::chain($dialect) as $d) {
            foreach ((MapData::DIALECTS[$d][$section] ?? []) as $k => $v) {
                if (!array_key_exists($k, $out)) {
                    $out[$k] = $v;
                }
            }
        }
        ksort($out);
        return $out;
    }

    /**
     * Which entries a translation looked at, or null when nobody is watching.
     *
     * Coverage is measured, not declared. The oracle corpus groups its
     * expressions under `### entry:` headers, and a header is a claim that the
     * group reaches that entry — a claim exactly as trustworthy as the ones this
     * whole layer keeps getting wrong. This is the one place every op, func and
     * skeleton lookup passes through, so a trace here is the fact the headers
     * are checked against.
     *
     * Off by default and cheap when off. Nothing in a translation depends on it.
     *
     * @var array<string,bool>|null
     */
    private static ?array $trace = null;

    /** Start recording entry lookups, discarding any previous recording. */
    public static function traceOn(): void
    {
        self::$trace = [];
    }

    /**
     * Stop recording and return the entries seen, as `section.key` strings.
     *
     * @return list<string>
     */
    public static function traceOff(): array
    {
        $seen = array_keys(self::$trace ?? []);
        self::$trace = null;
        sort($seen);
        return $seen;
    }

    // --- registration validation ------------------------------------------
    //
    // What tools/gen-sql-map.mjs enforces at generation time, enforced here at
    // registration time, against the vocabulary that file EMITS rather than a
    // second copy of it. Every one of these refusals closes a place where the
    // two hosts improvised differently over an entry the generator would never
    // have accepted -- a JSON list where a template belongs, an arity of
    // strings, a `ret` that was not there at all.
    //
    // LogicException, not SqlError: a malformed registration is a mistake in the
    // application's startup, and tryTranslate() must not swallow it.

    /** @param mixed $v */
    private static function checkLexical(string $key, $v, string $where): void
    {
        $types = MapData::RULES['lexicalTypes'];
        if (!isset($types[$key])) {
            throw new \LogicException("{$where} sets the unknown lexical key {$key}; "
                . 'known keys are ' . implode(', ', array_keys($types)));
        }
        // null is a WITHDRAWAL everywhere in the map, so it is always allowed --
        // sql/MAP.md §3 says a null binaryLiteral refuses BIN literals, and
        // lexical() looks keys up by presence so that it can.
        if ($v === null) {
            return;
        }
        if ($types[$key] === 'map') {
            // textEscape given as a STRING made both hosts skip escaping
            // entirely and emit 'it's' unquoted. That is an injection, it was in
            // both hosts, and nothing checked.
            if (!is_array($v)) {
                throw new \LogicException("{$where} sets {$key} to a "
                    . get_debug_type($v) . '; it must be a map of character to replacement');
            }
            foreach ($v as $from => $to) {
                if ($from === '' || !is_string($to)) {
                    throw new \LogicException("{$where}'s {$key} maps "
                        . var_export($from, true) . ' to something that is not a string');
                }
            }
            return;
        }
        // Everything else is a string, and is never cast to one: `true` given as
        // a JSON boolean rendered as `1` here and `True` on the Python host.
        if (!is_string($v)) {
            throw new \LogicException("{$where} sets {$key} to a "
                . get_debug_type($v) . '; it must be a string');
        }
        // A quote character that is not a character cannot quote. Left through,
        // the hosts disagreed about what it meant -- str_replace and Python's
        // str.replace put the escape between every character AND at each end,
        // the JS host's split/join only between -- and both answers are
        // nonsense. textCollate is legitimately empty (ansi and sqlite ship it
        // that way); these two are not.
        if ($v === '' && ($key === 'identQuote' || $key === 'textQuote')) {
            throw new \LogicException("{$where} sets {$key} to the empty string; "
                . 'a quote character that is not a character cannot quote');
        }
    }

    private static function checkKey(string $section, string $key): void
    {
        $rules = MapData::RULES;
        if ($section === 'ops' && !isset($rules['opArity'][$key])) {
            throw new \LogicException("{$key} is not a SEL operator, so an ops entry "
                . 'for it would never be looked up');
        }
        // `funcs` keys are SEL function names and case-insensitive; ops and skel
        // keys are looked up verbatim, which is why define() upper-cases only the
        // first. Registering `and` or `Case` used to be silently dead.
        if ($section === 'funcs' && !isset($rules['funcArity'][strtoupper($key)])) {
            throw new \LogicException("{$key} is not a SEL function this layer maps; "
                . 'the aggregates and IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by '
                . 'stage 2 and never reach the funcs table');
        }
        if ($section === 'skel' && !isset($rules['skelSlots'][$key])) {
            throw new \LogicException("{$key} is not a skeleton; known ones are "
                . implode(', ', array_keys($rules['skelSlots'])));
        }
    }

    /** @param mixed $entry */
    private static function checkEntry(string $section, string $key, $entry): void
    {
        $where = "the {$section} entry for {$key}";
        // A string is a refusal carrying its reason; null is a refusal without
        // one. Both are entries, and neither has anything else to check.
        if ($entry === null || is_string($entry)) {
            return;
        }
        if (!is_array($entry)) {
            throw new \LogicException("{$where} must be a map, a string or null, and is "
                . get_debug_type($entry));
        }
        if (isset($entry['builder'])) {
            if (!is_callable($entry['builder'])) {
                throw new \LogicException("{$where} has a builder that is not callable; "
                    . 'use Map::defineBuilder()');
            }
            return;
        }
        $rules = MapData::RULES;

        // A skeleton is a template with NAMED slots and no kind: the translator
        // decides what a CASE or a subquery yields, not the map. So it is checked
        // for its slots and nothing else.
        if ($section === 'skel') {
            if (!is_string($entry['tpl'] ?? null)) {
                throw new \LogicException("{$where} needs a tpl that is a string");
            }
            $allowed = $rules['skelSlots'][$key];
            preg_match_all('/\{([^}]*)\}/', $entry['tpl'], $m);
            foreach ($m[1] as $slot) {
                if (!in_array($slot, $allowed, true)) {
                    throw new \LogicException("{$where} uses the slot {{$slot}}; "
                        . "{$key} has " . implode(', ', $allowed)
                        . ' — a typo would survive as literal text in every query');
                }
            }
            if (isset($entry['caveat'])
                && !in_array($entry['caveat'], $rules['caveats'], true)) {
                throw new \LogicException("{$where} declares the caveat "
                    . var_export($entry['caveat'], true) . ', which is not on the '
                    . 'closed list in sql/MAP.md §4.6');
            }
            return;
        }

        if (array_key_exists('tpl', $entry) === array_key_exists('variants', $entry)) {
            throw new \LogicException("{$where} needs exactly one of tpl and variants");
        }
        $ret = $entry['ret'] ?? null;
        if (!is_string($ret)
            || (!in_array($ret, $rules['retKinds'], true) && $ret !== '@concat'
                && preg_match('/\A@unify:[0-9]+(,[0-9]+)*\z/', $ret) !== 1)) {
            throw new \LogicException("{$where} has ret " . var_export($ret, true)
                . '; use one of ' . implode(', ', $rules['retKinds'])
                . ', @concat or @unify:<n>[,<n>...]');
        }
        if (isset($entry['caveat']) && !in_array($entry['caveat'], $rules['caveats'], true)) {
            throw new \LogicException("{$where} declares the caveat "
                . var_export($entry['caveat'], true) . ', which is not on the closed '
                . 'list in sql/MAP.md §4.6; a caveat an application cannot branch on '
                . 'is prose');
        }
        if (isset($entry['since'])
            && (!is_string($entry['since'])
                || preg_match('/\A[0-9]+(\.[0-9]+)*\z/', $entry['since']) !== 1)) {
            throw new \LogicException("{$where} has a since that is not dotted-numeric");
        }
        if (isset($entry['arity'])) {
            $a = $entry['arity'];
            if (!is_array($a) || count($a) !== 2 || !is_int($a[0] ?? null)
                || !is_int($a[1] ?? null) || $a[0] < 0 || $a[1] < $a[0]) {
                throw new \LogicException("{$where} has an arity that is not "
                    . '[min, max] of two integers');
            }
        }
        if (isset($entry['variants'])) {
            if (!is_array($entry['variants']) || $entry['variants'] === []) {
                throw new \LogicException("{$where} has variants that are not a map");
            }
            $allowed = $rules['variants'][$key] ?? null;
            if ($allowed === null) {
                throw new \LogicException("{$where} uses variants, and {$key} is not "
                    . 'a variant family');
            }
            foreach (array_keys($entry['variants']) as $name) {
                if (!in_array($name, $allowed, true)) {
                    throw new \LogicException("{$where} declares the variant {$name}; "
                        . "{$key} has " . implode(', ', $allowed));
                }
            }
        }
        if (array_key_exists('tpl', $entry) && is_array($entry['tpl'])) {
            // Every key is an argument COUNT the entry can actually be called
            // with, checked against SEL's own arity narrowed by the entry's.
            //
            // Checking the shape alone is not enough, and PHP is why: a JSON
            // list ["a", "b"] decodes to an array whose keys are 0 and 1, which
            // are perfectly good count keys, so it is indistinguishable from
            // {"0": "a", "1": "b"} -- and it reached the renderer and emitted the
            // literal `b`. Against UPPER's arity of [1, 1] the count 0 is out of
            // range, and the list is refused for the reason it is actually wrong.
            [$min, $max] = $section === 'ops'
                ? $rules['opArity'][$key]
                : $rules['funcArity'][strtoupper($key)];
            if (isset($entry['arity'])) {
                $min = max($min, $entry['arity'][0]);
                $max = $max === null ? $entry['arity'][1]
                                     : min($max, $entry['arity'][1]);
            }
            foreach (array_keys($entry['tpl']) as $n) {
                if ($n === '*') {
                    continue;
                }
                if (preg_match('/\A(?:0|[1-9][0-9]{0,2})\z/', (string) $n) !== 1) {
                    throw new \LogicException("{$where} keys a template by "
                        . var_export($n, true) . '; an arity-keyed template uses a '
                        . 'count or *');
                }
                $c = (int) $n;
                if ($c < $min || ($max !== null && $c > $max)) {
                    throw new \LogicException("{$where} keys a template by {$c}, and "
                        . "{$key} takes {$min} to " . ($max ?? 'any')
                        . ' argument(s), so that template could never be chosen');
                }
            }
        }
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
