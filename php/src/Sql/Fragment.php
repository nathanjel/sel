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

    /** The bound values for `params` mode, in placeholder order. */
    public function bindings(): array
    {
        return $this->params;
    }

    /** True when nothing about this translation is inexact. */
    public function isExact(): bool
    {
        return $this->caveats === [];
    }

    private function join(string $mode): string
    {
        $out = '';
        foreach ($this->parts as $p) {
            if (is_string($p)) {
                $out .= $p;
                continue;
            }
            $out .= match ($mode) {
                'inline' => Emit::literal($this->dialect, $this->params[$p - 1],
                    $this->paramKinds[$p - 1] ?? 'TEXT'),
                'params' => Emit::placeholder($this->dialect, $p),
                'debug' => "~{$p}~",
                default => throw new \InvalidArgumentException(
                    "unknown render mode {$mode}; use inline, params or debug"),
            };
        }
        return $out;
    }
}
