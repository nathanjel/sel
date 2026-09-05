<?php
// Where a SEL variable lives in the schema. The host answers dependencies()
// with one of these per name; nothing is inferred, and a name with no binding
// is E_SQL_UNBOUND rather than a guess at a column.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

final class Bindings
{
    public const KINDS = ['column', 'columns', 'relation', 'value'];

    /** @var array<string, array<string,mixed>> upper-case name => binding */
    private array $map = [];

    /** @param array<string, array<string,mixed>> $bindings */
    public function __construct(array $bindings)
    {
        foreach ($bindings as $name => $b) {
            $key = strtoupper((string) $name);
            $this->map[$key] = self::validate($key, $b);
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

    // --- validation ---------------------------------------------------------

    /**
     * @param array<string,mixed> $b
     * @return array<string,mixed>
     */
    private static function validate(string $name, $b): array
    {
        if (!is_array($b) || !isset($b['kind'])) {
            throw new SqlError('E_SQL_BINDING',
                "the binding for {$name} has no kind; use one of " . implode(', ', self::KINDS));
        }
        $kind = (string) $b['kind'];
        if (!in_array($kind, self::KINDS, true)) {
            throw new SqlError('E_SQL_BINDING',
                "the binding for {$name} has kind {$kind}; use one of " . implode(', ', self::KINDS));
        }

        switch ($kind) {
            case 'column':
                self::checkColumn($name, $b);
                return $b + ['type' => 'UNKNOWN'];

            case 'columns':
                if (!isset($b['items']) || !is_array($b['items']) || $b['items'] === []) {
                    throw new SqlError('E_SQL_BINDING',
                        "the columns binding for {$name} needs a non-empty items list");
                }
                $items = [];
                foreach (array_values($b['items']) as $i => $item) {
                    self::checkColumn("{$name}[" . ($i + 1) . ']', $item);
                    $items[] = $item + ['type' => 'UNKNOWN'];
                }
                return ['kind' => 'columns', 'items' => $items];

            case 'relation':
                if (!isset($b['from'])) {
                    throw new SqlError('E_SQL_BINDING',
                        "the relation binding for {$name} needs a from");
                }
                $fields = [];
                foreach (($b['fields'] ?? []) as $f => $spec) {
                    self::checkColumn("{$name}[\"{$f}\"]", $spec);
                    $fields[strtoupper((string) $f)] = $spec + ['type' => 'UNKNOWN'];
                }
                // `raw` is the one place a host writes SQL this layer cannot
                // check the meaning of. It can still check the SHAPE, and must:
                // (string) on an array yields "Array" plus a warning, and that
                // string is then spliced into a correlated subquery as if it
                // were a join condition. Found while making the row oracle
                // dialect-aware, when a per-dialect table of correlations
                // reached relationSlots() unresolved.
                if (isset($b['correlate'])) {
                    if (!is_array($b['correlate']) || !isset($b['correlate']['raw'])
                        || !is_string($b['correlate']['raw'])) {
                        throw new SqlError('E_SQL_BINDING',
                            "the relation binding for {$name} has a correlate that is "
                            . 'not ["raw" => string]; correlate is raw SQL and there is '
                            . 'nothing else it can be');
                    }
                }
                if (isset($b['from']) && is_array($b['from'])
                    && (!isset($b['from']['raw']) || !is_string($b['from']['raw']))) {
                    throw new SqlError('E_SQL_BINDING',
                        "the relation binding for {$name} has a from that is an array "
                        . 'but not ["raw" => string]');
                }
                if (isset($b['scalar']) && !isset($fields[strtoupper((string) $b['scalar'])])) {
                    throw new SqlError('E_SQL_BINDING',
                        "the relation binding for {$name} names {$b['scalar']} as its scalar, "
                        . 'which is not one of its fields');
                }
                // array_merge, not `+`: the union operator keeps the LEFT
                // operand's value for a duplicated key, so `$b + ['fields' => …]`
                // silently kept the host's raw fields and discarded the
                // normalisation — leaving lowercase keys that every consumer
                // then failed to find, because they all look up strtoupper().
                return array_merge($b, ['fields' => $fields, 'alias' => $b['alias'] ?? null]);

            case 'value':
            default:
                if (!isset($b['value'])) {
                    throw new SqlError('E_SQL_BINDING',
                        "the value binding for {$name} needs a value");
                }
                $v = $b['value'];
                $type = $b['type'] ?? null;
                if ($type !== null && !in_array($type, Fragment::KINDS, true)) {
                    throw new SqlError('E_SQL_BINDING',
                        "the value binding for {$name} has type {$type}; use one of "
                        . implode(', ', Fragment::KINDS));
                }
                // `type` decides whether a scalar goes out quoted or bare, which
                // is a question no inspection of the value can answer: SEL
                // numbers are TEXT values. See Emit::literal.
                $val = $v instanceof Value ? $v : Value::fromNative($v);
                // Declaring NUM asks for the value to be emitted unquoted, so it
                // has to be a number. Checked here, where the message can name
                // the binding, and again in Emit::literal, which is the last
                // place before the characters go out.
                if ($type === 'NUM') {
                    self::checkNumeric($name, $val);
                }
                return ['kind' => 'value', 'type' => $type, 'value' => $val];
        }
    }

    /** Every scalar reachable from a NUM-typed value binding. */
    private static function checkNumeric(string $name, Value $v): void
    {
        if ($v->size() > 0) {
            foreach ($v->entries() as [$k, $child]) {
                self::checkNumeric("{$name}[\"{$k}\"]", $child);
            }
            return;
        }
        if ($v->isNone()) {
            return;
        }
        if (!$v->isText() || !$v->looksNumeric()) {
            throw new SqlError('E_SQL_BINDING',
                "the value binding for {$name} declares type NUM, which asks for it "
                . 'to be emitted unquoted, but ' . Value::quoteDump($v->isBool()
                    ? ($v->asBool() ? 'TRUE' : 'FALSE') : $v->asText()) . ' is not a number');
        }
    }

    /** @param mixed $c */
    private static function checkColumn(string $where, $c): void
    {
        if (!is_array($c)) {
            throw new SqlError('E_SQL_BINDING', "{$where} must be an array");
        }
        if (!isset($c['column']) && !isset($c['raw'])) {
            throw new SqlError('E_SQL_BINDING', "{$where} needs a column or a raw");
        }
        if (isset($c['type']) && !in_array($c['type'], Fragment::KINDS, true)) {
            throw new SqlError('E_SQL_BINDING',
                "{$where} has type {$c['type']}; use one of " . implode(', ', Fragment::KINDS));
        }
    }
}
