<?php
// JSON -> Binding, for the test tools only.
//
// The SQL layer takes Binding objects built in code and decodes no documents:
// decoding one is where PHP's and Python's shape rules got to decide what a
// malformed binding meant, and neither answer was a decision anybody made.
// But sql/cases/*.sqlt, sql/oracle/expressions.selo and sql/oracle/rows.json are
// test DATA, so something has to turn them into constructor calls -- and that
// something is a test tool, whose mistakes are suite errors rather than
// translations.
//
// An application whose bindings come from a schema file does exactly the same
// thing: it generates the calls. See docs/SQL-TRANSLATION.md §5.
//
// Used by php/bin/sqlo alone. The case files no longer come through here:
// tools/gen-sql-cases.mjs reads them and emits php/bin/CaseData.php and
// python/bin/case_data.py, so the two runners load code rather than each
// decoding the same document with a different decoder -- which they did, and
// disagreed about.
//
// The oracle's fixtures (sql/oracle/*.selo and rows.json) have not been moved
// yet. They are PHP-only data read by a PHP-only tool, so no second host can
// disagree with this file about them; folding them into the generator is the
// same job again and is worth doing when a second host grows an oracle.

declare(strict_types=1);

require_once __DIR__ . '/../src/Sql/bootstrap.php';

use Sel\Sql\Binding;
use Sel\Value;

/**
 * Build typed bindings from a case file's `--- bindings` block.
 *
 * This lives in the RUNNER and not in the library, and the distinction is the
 * point. The SQL layer takes Binding objects built in code; it decodes no
 * documents, because decoding one is where PHP's and Python's shape rules got
 * to decide what a malformed binding meant. A case file is test data, so
 * something has to turn it into calls -- and that something is a test tool,
 * whose mistakes are suite errors rather than translations.
 *
 * An application whose bindings come from a schema file does the same thing:
 * generates the calls. See docs/SQL-TRANSLATION.md §5.
 *
 * @param array<string,mixed> $json
 * @return array<string, Binding>
 */
function bindings_from_json(array $json, string $at): array
{
    $out = [];
    foreach ($json as $name => $b) {
        if (!is_array($b) || !isset($b['kind'])) {
            throw new RuntimeException("{$at}: binding {$name} has no kind");
        }
        $out[$name] = binding_from_json($b, "{$at}: binding {$name}");
    }
    return $out;
}

/** @param array<string,mixed> $b */
function binding_from_json(array $b, string $where): Binding
{
    $type = $b['type'] ?? 'UNKNOWN';
    switch ($b['kind']) {
        case 'column':
            return isset($b['raw'])
                ? Binding::raw($b['raw'], $type)
                : Binding::column($b['column'], $b['table'] ?? null, $type);

        case 'columns':
            // array_is_list, because json_decode(assoc) cannot tell a JSON
            // object from a JSON array and Python's isinstance can -- so the
            // distinction has to be drawn HERE, in the tool, and drawn the same
            // way in both. A columns binding is indexed by position, so it is a
            // list. Without this the two tools disagreed about `items` as an
            // object: PHP took the values, Python refused.
            if (!is_array($b['items']) || !array_is_list($b['items'])) {
                throw new RuntimeException("{$where}: items must be a JSON array");
            }
            $items = [];
            foreach ($b['items'] as $item) {
                $items[] = binding_from_json(['kind' => 'column'] + $item, $where);
            }
            return Binding::columns(...$items);

        case 'relation':
            // The mirror of the rule above: fields are NAMED, so a JSON array is
            // wrong here. `[]` is both an empty list and an empty object once
            // decoded, so it is allowed.
            $declared = $b['fields'] ?? [];
            if (!is_array($declared) || ($declared !== [] && array_is_list($declared))) {
                throw new RuntimeException("{$where}: fields must be a JSON object");
            }
            $fields = [];
            foreach ($declared as $k => $spec) {
                $fields[$k] = binding_from_json(['kind' => 'column'] + $spec, $where);
            }
            // `?? null` would turn a malformed `correlate` or `from` in a case
            // file into a silently absent one, and several cases exist to assert
            // that a malformed one is REFUSED. So the raw value is passed through
            // and the constructor is the one that judges it.
            $corr = $b['correlate'] ?? null;
            $args = [$b['alias'] ?? null, $fields, $b['scalar'] ?? null,
                     is_array($corr) ? ($corr['raw'] ?? $corr) : $corr];
            $from = $b['from'];
            return is_array($from)
                ? Binding::relationQuery($from['raw'] ?? $from, ...$args)
                : Binding::relation($from, ...$args);

        case 'value':
            $v = $b['value'];
            // The corpus spells a BIN value as {"bin": "<hex>"}, because JSON has
            // no byte string. Recognised here, in the tool, rather than in the
            // library -- Binding::value takes a Value and asks no questions.
            if (is_array($v) && isset($v['bin']) && count($v) === 1) {
                $bytes = @hex2bin((string) $v['bin']);
                if ($bytes === false) {
                    throw new RuntimeException("{$where}: the bin value is not hex");
                }
                return Binding::value(Value::bin($bytes), $b['type'] ?? null);
            }
            // A JSON number is refused, in both tools, and that is a decision
            // rather than an omission: PHP's json_decode turns a 20-digit
            // integer into a float, Python keeps it exact, and JS cannot tell
            // 1.0 from 1. No decoding rule is implementable in all six hosts, so
            // test data spells its numbers as strings and says `"type": "NUM"`
            // when it wants them unquoted -- which is what the library asks of
            // an application too.
            if (is_int($v) || is_float($v)) {
                throw new RuntimeException(
                    "{$where}: write the number as a string and declare "
                    . '"type": "NUM"; a JSON number does not survive every host');
            }
            return Binding::value(Value::fromNative($v), $b['type'] ?? null);
    }
    throw new RuntimeException("{$where}: unknown kind {$b['kind']}");
}
