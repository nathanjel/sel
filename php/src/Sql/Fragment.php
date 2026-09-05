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
        array $caveats = []
    ) {
        $this->parts = $parts;
        $this->kind = $kind;
        $this->dialect = $dialect;
        $this->params = $params;
        $this->paramKinds = $paramKinds;
        $this->caveats = $caveats;
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
     * Usable as a condition. BOOL as it stands; UNKNOWN wrapped in the dialect's
     * IS TRUE test, since a column of unknown type may be NULL and SEL has no
     * third truth value to give back.
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
        if ($this->kind === 'UNKNOWN') {
            $tpl = Map::lexical($this->dialect, 'isTrue');
            return str_replace('{0}', $this->join($mode), $tpl);
        }
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
            if (!is_string($p) && !$this->isNumeric($p)) {
                $out[] = $this->params[$p - 1];
            }
        }
        return $out;
    }

    /**
     * True for a slot holding a NUM-form literal, which is never parameterised.
     *
     * No coercion of a bound string reproduces a bare decimal literal, and that
     * is not a gap to be patched — it is what the two things are. MariaDB reads
     * `2.50` as DECIMAL with scale 2 and `12345678901234567890.12345` as DECIMAL
     * with 25 digits; a string parameter is untyped, and every way of giving it a
     * type picks the wrong one. `CAST(? AS DECIMAL(65,10))` pads the scale, so
     * TRIM(2.50) answered "2.5000000000". `(? + 0)` drops the scale and floats
     * above seventeen digits. Without either, `(? = ?)` compares two strings and
     * 2.50 = 2.5 is FALSE.
     *
     * So a NUM literal is rendered as itself in every mode. That costs nothing
     * params mode was protecting: after Emit::numericLiteral the characters it
     * can contain are digits, one `.` and a leading `-`, by construction, which
     * is the one value form that provably cannot carry a quote or a comment.
     * Everything else is still bound.
     */
    private function isNumeric(int $slot): bool
    {
        return ($this->paramKinds[$slot - 1] ?? 'TEXT') === 'NUM';
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
            if ($mode !== 'inline' && $kind === 'NUM') {
                // Never a placeholder; see isNumeric(). It does not advance $nth
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
