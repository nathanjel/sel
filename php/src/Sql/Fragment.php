<?php
// The result of a translation: a part list, its bound values, and the static
// kind it produces.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

/**
 * A rendered SQL expression.
 *
 * `parts` alternates finished SQL and parameter slots — a string is SQL, an int
 * is the 1-based index of a value in `params`. The renderer never concatenates
 * a literal into a string, so `inline` and `params` output are two ways of
 * joining one structure rather than two code paths. A part list cannot be
 * confused about where a literal ends, whatever the literal contains, and that
 * is the class of bug this shape exists to make unreachable.
 */
final class Fragment
{
    public const KINDS = ['NUM', 'TEXT', 'BOOL', 'BIN', 'UNKNOWN', 'LIST'];

    /** @var list<string|int> */
    public array $parts;
    /** @var list<Value> */
    public array $params;
    /**
     * The literal form of each slot: NUM, TEXT, BOOL or BIN, parallel to
     * $params. Kept beside the values rather than derived from them because it
     * cannot be derived — see Emit::literal.
     *
     * @var list<string>
     */
    public array $paramKinds;
    public string $kind;
    public string $dialect;
    /** @var list<string> */
    public array $caveats;
    public bool $exact;
    public bool $sargable;
    public bool $guard;
    public ?self $prefilter = null;
    public bool $separatePrefilter = false;

    /**
     * @param list<string|int> $parts
     * @param list<Value> $params
     * @param list<string> $paramKinds
     * @param list<string> $caveats
     */
    public function __construct(
        array $parts,
        string $kind,
        string $dialect,
        array $params = [],
        array $paramKinds = [],
        array $caveats = [],
        bool $exact = false,
        bool $sargable = false,
        bool $guard = false
    ) {
        $this->parts = $parts;
        $this->kind = $kind;
        $this->dialect = $dialect;
        $this->params = $params;
        $this->paramKinds = $paramKinds;
        $this->caveats = $caveats;
        $this->exact = $exact;
        $this->sargable = $sargable;
        $this->guard = $guard;
    }

    /**
     * Usable in a select list, GROUP BY, ORDER BY or HAVING. Any kind but LIST,
     * which is not a SQL value at all.
     */
    public function asValue(string $mode = 'inline'): string
    {
        if ($this->kind === 'LIST') {
            refuse('E_SQL_SHAPE', 'this expression yields a list, and a SQL expression is a scalar');
        }
        return $this->join($mode);
    }

    /**
     * Usable as a condition. A declared BOOL, and nothing else.
     *
     * UNKNOWN was wrapped in the dialect's IS TRUE test until the kind warrant:
     * that folds NULL to false but not a number, and `1 IS TRUE` is TRUE on
     * MariaDB where SEL raises E_NOT_BOOL. See docs/SQL-KINDS.md.
     *
     * A NUM or TEXT fragment is refused rather than accepted. Silently allowing
     * `WHERE o.total` is how a database turns a validation rule into the
     * truthiness test SEL spent its whole design avoiding.
     */
    public function asCondition(string $mode = 'inline'): string
    {
        if ($this->kind === 'BOOL') {
            return $this->join($mode);
        }
        // UNKNOWN was wrapped in isTrue here rather than trusted, which folds
        // NULL to false but not a number: `1 IS TRUE` is TRUE on MariaDB, and
        // SEL raises E_NOT_BOOL for a number in a condition. Wrapping cannot
        // fix that, so an undeclared column is no longer a condition; declare
        // the binding BOOL. isTrue stays in the map -- the aggregate skeletons
        // use it on a body that is already known to be BOOL.
        refuse('E_SQL_SHAPE',
            "a condition must be BOOL, and this expression is {$this->kind}; "
            . 'SQL has no truthiness and neither does SEL');
    }

    /**
     * The bound values for `params` mode, in placeholder order.
     *
     * Derived from the part list rather than returned as stored, because the two
     * orders are not the same. A slot is numbered when it is created, and the
     * template decides where it lands: `FIND(needle, hay)` maps to
     * `INSTR({1}, {0})`, so the second slot created is the first one emitted.
     * A positional `?` carries no number, so a driver binds the first value to
     * the first placeholder — which is right only if this walks the output.
     *
     * A slot appearing more than once yields its value more than once, which is
     * also right: two placeholders need two bindings, even of the same value.
     *
     * @return list<Value>
     */
    public function bindings(): array
    {
        $out = [];
        foreach ($this->parts as $p) {
            if (!is_string($p) && !$this->isInline($p)) {
                $out[] = $this->params[$p - 1];
            }
        }
        return $out;
    }

    /**
     * True for a slot rendered as a literal in every mode, never as a parameter.
     *
     * Two forms qualify, for the same underlying reason: **neither carries any
     * character the caller chose**, so there is nothing for a placeholder to
     * protect, and both are damaged by being sent as a string.
     *
     * NUM, because no coercion of a bound string reproduces a bare numeric
     * literal. MariaDB reads `2.50` as DECIMAL with scale 2 and
     * `12345678901234567890.12345` as DECIMAL with 25 digits; a parameter is
     * untyped, and every way of giving it a type picks the wrong one.
     * `CAST(? AS DECIMAL(65,10))` pads the scale, so `TRIM(2.50)` answered
     * "2.5000000000". `(? + 0)` drops the scale and floats above seventeen
     * digits. With neither, `(? = ?)` compares two strings and 2.50 = 2.5 is
     * FALSE. After Emit::numericLiteral the characters a NUM literal can contain
     * are digits, one `.` and a leading `-`, by construction — and then whatever
     * quoting the dialect's `numericLiteral` adds, which is also the map's.
     *
     * BOOL, because the token is `Map::lexical($dialect, 'true'|'false')` — it
     * comes out of the dialect document, not out of a rule. Binding it as a
     * string breaks SQLite outright: `1 = '1'` is **0** there, since INTEGER and
     * TEXT are different storage classes and no affinity applies to a bare
     * parameter, so `TRUE XOR TRUE` answered TRUE in params mode and FALSE
     * inline. Found by the fuzz lane on sqlite's first run.
     *
     * Everything else is still bound.
     */
    private function isInline(int $slot): bool
    {
        $kind = $this->paramKinds[$slot - 1] ?? 'TEXT';
        // BIN joins them. A BIN parameter is bytes, and a driver sends them
        // through the connection's text encoding: on PostgreSQL 130 of the 256
        // single-byte values then failed -- 129 as `22021 invalid byte sequence
        // for encoding "UTF8"` and 0x00 silently -- while the same values
        // inlined through `binaryLiteral` were correct on all four dialects, all
        // 256. The corpus's one BIN witness is 7ac3a9, valid UTF-8, which is
        // precisely the byte string that survives. A host cannot work around it:
        // bindings() hands back Values, and the cast wrapping the placeholder is
        // what breaks it.
        return $kind === 'NUM' || $kind === 'BOOL' || $kind === 'BIN';
    }

    /** True when nothing about this translation is inexact. */
    public function isExact(): bool
    {
        return $this->caveats === [];
    }

    private function join(string $mode): string
    {
        $out = '';
        $nth = 0;                       // position in bindings(), not slot id
        foreach ($this->parts as $p) {
            if (is_string($p)) {
                $out .= $p;
                continue;
            }
            $kind = $this->paramKinds[$p - 1] ?? 'TEXT';
            if ($mode !== 'inline' && $this->isInline($p)) {
                // Never a placeholder; see isInline(). It does not advance $nth
                // either, because it emits no placeholder for a binding to land
                // in.
                $out .= Emit::literal($this->dialect, $this->params[$p - 1], $kind);
                continue;
            }
            $nth++;
            $out .= match ($mode) {
                'inline' => Emit::literal($this->dialect, $this->params[$p - 1], $kind),
                // The ordinal a numbered placeholder carries — PostgreSQL's $n —
                // must agree with bindings(), which walks the output. The slot
                // id would not: it is a creation number, and a reordering
                // template emits creation numbers out of order.
                'params' => Emit::placeholder($this->dialect, $nth),
                'debug' => "~{$nth}~",
                default => throw new \InvalidArgumentException(
                    "unknown render mode {$mode}; use inline, params or debug"),
            };
        }
        return $out;
    }
}
