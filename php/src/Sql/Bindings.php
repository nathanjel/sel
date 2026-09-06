<?php
// Where a SEL variable lives in the schema. The host answers dependencies()
// with one of these per name; nothing is inferred, and a name with no binding
// is E_SQL_UNBOUND rather than a guess at a column.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

final class Bindings
{
    /** @var array<string, array<string,mixed>> upper-case name => binding */
    private array $map = [];

    /**
     * Name => Binding, and nothing else.
     *
     * The array-of-arrays this used to take was a JSON document in all but
     * name, and validating one by hand is where the two hosts diverged: PHP's
     * `is_array` cannot tell a JSON object from a JSON array, Python's
     * `isinstance` can, and neither difference was a decision anybody made. A
     * Binding is built by a typed constructor (see Binding.php), so the
     * malformed shapes are unrepresentable rather than refusable, and this
     * class has nothing left to validate.
     *
     * @param array<string, Binding> $bindings
     */
    public function __construct(array $bindings)
    {
        foreach ($bindings as $name => $b) {
            if (!($b instanceof Binding)) {
                throw new SqlError('E_SQL_BINDING',
                    "the binding for {$name} is a " . get_debug_type($b)
                    . '; build one with Binding::column(), ::columns(), ::relation(), '
                    . '::relationQuery(), ::raw() or ::value()');
            }
            $this->map[strtoupper((string) $name)] = $b->spec;
        }
    }

    public function has(string $name): bool
    {
        return isset($this->map[strtoupper($name)]);
    }

    /**
     * @param array{line:int,col:int,offset:int}|null $pos
     * @return array<string,mixed>
     */
    public function get(string $name, ?array $pos = null): array
    {
        $key = strtoupper($name);
        if (!isset($this->map[$key])) {
            $known = array_keys($this->map);
            sort($known);
            refuse('E_SQL_UNBOUND',
                "{$key} is read by this rule but no binding says where it lives"
                . ($known === [] ? '; no bindings were given' : '; bound names are ' . implode(', ', $known)),
                $pos);
        }
        return $this->map[$key];
    }

    /** @return list<string> */
    public function names(): array
    {
        $out = array_keys($this->map);
        sort($out);
        return $out;
    }

    /**
     * A relation alias may name only one thing. Two relations sharing an alias
     * in one expression would produce a subquery correlated to the wrong rows,
     * and the host chose the aliases, so the host can fix them.
     *
     * @param array{line:int,col:int,offset:int}|null $pos
     */
    public function checkAliases(?array $pos = null): void
    {
        $seen = [];
        foreach ($this->map as $name => $b) {
            if ($b['kind'] !== 'relation') {
                continue;
            }
            // A raw `from` is an array, and using it as an array key raised a
            // TypeError — not a SqlError, so tryTranslate() did not catch it and
            // a host using the refusal-tolerant API got a fatal instead of null.
            $alias = $b['alias'] ?? null;
            // Same class as the raw `from` above and missed by the same pass: an
            // array alias reached `isset($seen[$alias])` and raised a TypeError,
            // which tryTranslate() does not catch.
            if ($alias !== null && !is_string($alias)) {
                refuse('E_SQL_BINDING',
                    "the relation binding for {$name} has an alias that is not a string",
                    $pos);
            }
            if ($alias === null) {
                $alias = is_array($b['from'])
                    ? (string) ($b['from']['raw'] ?? '')
                    : (string) $b['from'];
            }
            if (isset($seen[$alias])) {
                refuse('E_SQL_BINDING',
                    "relations {$seen[$alias]} and {$name} share the alias {$alias}; "
                    . 'give each one its own', $pos);
            }
            $seen[$alias] = $name;
        }
    }
}
