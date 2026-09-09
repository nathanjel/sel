<?php
// How an application says where a SEL variable lives in the schema.
//
// Constructed in code, never decoded from a document. That is the whole point:
// this layer used to take a nested array shaped like JSON and validate it by
// hand, and a cross-host review found the two hosts disagreeing about what a
// malformed one meant -- `from: ["order_items"]` was refused by PHP and spliced
// into an identifier by Python; `items` as an object was accepted by one and
// refused by the other. None of that was a decision anybody made; it was
// json_decode's shape rules on one side and Python's on the other, leaking into
// the translator.
//
// A typed constructor makes the whole class unrepresentable rather than
// refusable. `relation()` takes a string table, so there is no array to
// mis-splice; `columns()` takes Binding objects, so there is no object-vs-array
// question to answer differently in two languages. An application whose
// bindings come from a schema file generates these calls; SEL parses nothing.
//
// See docs/SQL-TRANSLATION.md §5.

declare(strict_types=1);

namespace Sel\Sql;

use Sel\Value;

final class Binding
{
    /**
     * The normalised record the translator reads. Public so Bindings can take
     * it; there is no other consumer, and nothing outside this file builds one.
     *
     * @var array<string,mixed>
     */
    public array $spec;

    /** @param array<string,mixed> $spec */
    private function __construct(array $spec)
    {
        $this->spec = $spec;
    }

    /**
     * One column, optionally qualified by a table, optionally typed.
     *
     * `type` is what the kind guards read, and leaving it UNKNOWN is honest --
     * but it no longer means the operand passes everything. Where a BOOL is
     * required an UNKNOWN operand is now refused, because no dialect can ask
     * whether a value is a boolean; in an arithmetic or comparison operand it
     * is wrapped so a value SEL would refuse becomes NULL, at the cost of the
     * column's index. Declaring NUM is what buys the plain comparison back, and
     * it is the only thing that does.
     *
     * Bare in exactly two places, both recorded in docs/SQL-KINDS.md §4: a
     * numeric function argument, and a bare aggregate body.
     */
    public static function column($column, $table = null,
                                  $type = 'UNKNOWN', bool $exact = false,
                                  bool $sargable = false, bool $guard = false,
                                  ?string $collation = null): self
    {
        self::checkName('column', $column);
        if ($table !== null) {
            self::checkName('table', $table);
        }
        self::checkType($type);
        if ($collation !== null) {
            [$cExact, $cSargable] = self::checkCollation($collation);
            $exact = $exact || $cExact;
            $sargable = $sargable || $cSargable;
        }
        return new self(['kind' => 'column', 'column' => $column,
                         'table' => $table, 'type' => $type,
                         'exact' => $exact, 'sargable' => $sargable,
                         'guard' => $guard]);
    }

    /**
     * A column expressed as SQL this layer will not read.
     *
     * The one place an application writes SQL here. It is emitted verbatim, so
     * whatever it contains is the application's promise rather than this
     * layer's -- which is exactly why it is a named constructor and not a key
     * somebody can leave in a map by accident.
     */
    public static function raw($sql, $type = 'UNKNOWN', bool $exact = false,
                               bool $sargable = false, bool $guard = false,
                               ?string $collation = null): self
    {
        self::checkString('a raw column binding', $sql);
        if ($sql === '') {
            throw new SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty');
        }
        self::checkType($type);
        if ($collation !== null) {
            [$cExact, $cSargable] = self::checkCollation($collation);
            $exact = $exact || $cExact;
            $sargable = $sargable || $cSargable;
        }
        return new self(['kind' => 'column', 'raw' => $sql, 'type' => $type,
                         'exact' => $exact, 'sargable' => $sargable,
                         'guard' => $guard]);
    }

    /**
     * An ordered set of columns, iterated by an aggregate and indexed by
     * position: the first is `V[1]`.
     */
    public static function columns(self ...$items): self
    {
        if ($items === []) {
            throw new SqlError('E_SQL_BINDING', 'a columns binding needs at least one column');
        }
        $out = [];
        foreach ($items as $i => $item) {
            if (($item->spec['kind'] ?? null) !== 'column') {
                throw new SqlError('E_SQL_BINDING',
                    'a columns binding takes column bindings, and item ' . ($i + 1)
                    . " is a {$item->spec['kind']}");
            }
            $out[] = $item->spec;
        }
        return new self(['kind' => 'columns', 'items' => $out]);
    }

    /**
     * A set of rows, rendered as a correlated subquery.
     *
     * `$fields` maps a SEL key to a column binding; the keys are upper-cased
     * here, once, so every consumer looks one up the same way. `$scalar` names
     * the field a bare reference means, and only a ONE-field relation may
     * declare it -- a wider row is a map in SEL, and a map is not the value of
     * one of its fields.
     *
     * `$correlate` is SQL, like raw(), and joins the subquery back to the outer
     * row. Without it the subquery is over the whole table, which is legal and
     * occasionally what you want.
     *
     * @param array<string, self> $fields
     */
    public static function relation($from, $alias = null,
                                    $fields = [], $scalar = null,
                                    $correlate = null): self
    {
        self::checkName('from', $from);
        if ($alias !== null) {
            self::checkName('alias', $alias);
        }
        return self::makeRelation(['kind' => 'relation', 'from' => $from],
            $alias, $fields, $scalar, $correlate);
    }

    /**
     * The same, over a query the application writes rather than a table name.
     *
     * @param array<string, self> $fields
     */
    public static function relationQuery($query, $alias = null,
                                         $fields = [], $scalar = null,
                                         $correlate = null): self
    {
        self::checkString('a relation query', $query);
        if ($query === '') {
            throw new SqlError('E_SQL_BINDING', 'a relation query cannot be empty');
        }
        if ($alias !== null) {
            self::checkName('alias', $alias);
        }
        return self::makeRelation(['kind' => 'relation', 'from' => ['raw' => $query]],
            $alias, $fields, $scalar, $correlate);
    }

    /**
     * A constant the application supplies, inlined as a literal.
     *
     * Takes a Value, never a native number or string, and that is the fix for
     * the last cross-host divergence in this file: PHP's json_decode turns a
     * 20-digit integer into a float, Python keeps it exact, and JS cannot tell
     * 1.0 from 1. Asking the caller for a Value moves the decision to the line
     * that knows the answer.
     *
     * `type` is NUM or nothing. It decides whether the value is emitted quoted,
     * which is a question no inspection can settle: SEL numbers ARE text values
     * (spec §4), so Value::num('5.00') and Value::text('5.00') are one object.
     */
    public static function value(Value $v, $type = null): self
    {
        if ($type !== null && !is_string($type)) {
            throw new SqlError('E_SQL_BINDING',
                'a value binding has a type that is not a string');
        }
        if ($type !== null && $type !== 'NUM') {
            self::checkType($type);
        }
        if ($type === 'NUM') {
            self::checkNumeric('this value binding', $v);
        }
        return new self(['kind' => 'value', 'type' => $type, 'value' => $v]);
    }

    // --- internals ----------------------------------------------------------

    /**
     * @param array<string,mixed> $base
     * @param array<string, self> $fields
     */
    private static function makeRelation(array $base, $alias, $fields,
                                         $scalar, $correlate): self
    {
        if (!is_array($fields)) {
            throw new SqlError('E_SQL_BINDING',
                'the fields of a relation binding must be a map of name to column '
                . 'binding, and this is ' . get_debug_type($fields));
        }
        if ($scalar !== null) {
            self::checkString("a relation binding's scalar", $scalar);
        }
        if ($correlate !== null) {
            self::checkString("a relation binding's correlate", $correlate);
        }
        $out = [];
        foreach ($fields as $name => $b) {
            if (!($b instanceof self) || ($b->spec['kind'] ?? null) !== 'column') {
                throw new SqlError('E_SQL_BINDING',
                    "the field {$name} of a relation binding must be a column binding");
            }
            // strtoupper, which is ASCII-only in PHP and so matches
            // sel.registry's ascii_upper on the Python side. str.upper()
            // there would fold "ß" to "SS" and change the key's length.
            $out[strtoupper((string) $name)] = $b->spec;
        }
        if ($scalar !== null) {
            $key = strtoupper($scalar);
            if (!isset($out[$key])) {
                throw new SqlError('E_SQL_BINDING',
                    "a relation binding names {$scalar} as its scalar, which is not "
                    . 'one of its fields');
            }
        }
        if ($correlate !== null && $correlate === '') {
            throw new SqlError('E_SQL_BINDING', 'a relation correlate cannot be empty');
        }
        $spec = $base + ['alias' => $alias, 'fields' => $out];
        if ($scalar !== null) {
            $spec['scalar'] = $scalar;
        }
        if ($correlate !== null) {
            $spec['correlate'] = ['raw' => $correlate];
        }
        return new self($spec);
    }

    /**
     * An identifier the application supplied has to survive being quoted.
     *
     * Emit::ident doubles the quote character and passes everything else
     * through, which is right for every character but two. A NUL terminates the
     * C string libpq and sqlite3 are handed, so `a\0b` is malformed SQL on all
     * four servers rather than a column nobody has. An empty name quotes to `""`,
     * which PostgreSQL rejects and the other three accept -- a divergence with
     * no upside.
     */
    /**
     * Declared types are not enough, and this is the reason the checks are in
     * the body rather than in the signature.
     *
     * PHP would enforce `string $column` and refuse an array with a TypeError.
     * Python's annotations enforce nothing at run time, JS has no types to
     * declare, and Lisp's are advisory -- so a guarantee written as a signature
     * is a guarantee three of the six hosts do not make. Written here it is the
     * same refusal, with the same code, everywhere. And a TypeError would be
     * the wrong class anyway: SqlError is what an application catches.
     */
    private static function checkString(string $what, $v): void
    {
        if (!is_string($v)) {
            throw new SqlError('E_SQL_BINDING',
                "{$what} must be a string, and this is " . get_debug_type($v));
        }
    }

    /** @param mixed $v */
    private static function checkName(string $what, $v): void
    {
        self::checkString("a binding's {$what}", $v);
        if ($v === '') {
            throw new SqlError('E_SQL_BINDING', "a binding has an empty {$what} name");
        }
        if (str_contains($v, "\0")) {
            throw new SqlError('E_SQL_BINDING',
                "a binding has a {$what} name containing a NUL, which no dialect can quote");
        }
    }

    /** @param mixed $type */
    private static function checkType($type): void
    {
        if (!is_string($type) || !in_array($type, Fragment::KINDS, true)) {
            throw new SqlError('E_SQL_BINDING',
                "a binding has type {$type}; use one of " . implode(', ', Fragment::KINDS));
        }
    }

    /** Every scalar reachable from a NUM-typed value binding. */
    private static function checkNumeric(string $where, Value $v): void
    {
        if ($v->size() > 0) {
            foreach ($v->entries() as [$k, $child]) {
                self::checkNumeric("{$where}[\"{$k}\"]", $child);
            }
            return;
        }
        if ($v->isNone()) {
            return;
        }
        if (!$v->isText() || !$v->looksNumeric()) {
            throw new SqlError('E_SQL_BINDING',
                "{$where} declares type NUM, which asks for it to be emitted "
                . 'unquoted, but ' . Value::quoteDump($v->isBool()
                    ? ($v->asBool() ? 'TRUE' : 'FALSE') : $v->asText()) . ' is not a number');
        }
        // looksNumeric is broader than canonical, and Emit::numericLiteral emits
        // Dec::format's output rather than the caller's characters -- correct for
        // the AST path, where the lexer has already canonicalised, and wrong here,
        // where the application supplied the string and the evaluator was handed
        // that same string. `"007"` translated to 7 while SEL kept "007".
        $text = $v->asText();
        if (\Sel\Dec::format(\Sel\Dec::parse($text)) !== $text) {
            throw new SqlError('E_SQL_BINDING',
                "{$where} declares type NUM and is " . Value::quoteDump($text)
                . ', which is not how SEL writes that number; a NUM binding is '
                . 'emitted unquoted and must already be canonical, so pass it as '
                . 'text or drop the leading zeros');
        }
    }

    /**
     * @return array{0: bool, 1: bool}
     */
    private static function checkCollation(string $c): array
    {
        $lower = strtolower($c);
        if ($lower === 'binary' || $lower === 'exact') {
            return [true, false];
        }
        if ($lower === 'sargable' || $lower === 'prefilter') {
            return [false, true];
        }
        if ($lower === 'default' || $lower === 'none') {
            return [false, false];
        }
        throw new SqlError('E_SQL_BINDING',
            "unknown collation '{$c}'; use 'binary', 'exact', 'sargable', or 'default'");
    }
}
