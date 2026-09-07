<?php
// SQL-aimed usage — pushing a rule down to the database, from PHP.
//
//   php examples/sql/php.php
//
// The same rule that validates one order in the application can filter a
// million of them in the database. What makes that safe is that the translation
// refuses rather than guesses: if SQL cannot be made to mean what SEL means, no
// SQL is emitted and the rule stays where it already worked.
//
// The four files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.

declare(strict_types=1);

// Two bootstraps, because the SQL layer is opt-in: a host that never translates
// should not load the dialect tables to find that out.
require_once __DIR__ . '/../../php/src/bootstrap.php';
require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';

use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;

// 1 — a rule, and what the database should call its inputs --------------------
// dependencies() says exactly what has to be bound. A name the program reads
// and the bindings do not describe is a refusal, not a guess.

echo "1. a rule pushed down\n";
$rule = Sel::compile('TOTAL > 100.00 AND STATUS $== "open"');
echo '   needs        => ', implode(' ', $rule->dependencies()), "\n";

$bindings = [
    'TOTAL'  => Binding::column('total', 'o', 'NUM'),
    'STATUS' => Binding::column('status', 'o', 'TEXT'),
];
$frag = Sql::translate($rule, 'mariadb', $bindings);
echo '   sql          => ', $frag->asCondition(), "\n";

// 2 — the same rule as a prepared statement ------------------------------------
// Inline is for reading and for a query you build once. `params` is what you
// hand a driver: the literals become placeholders and bindings() gives the
// values in the order the placeholders appear in the output.

echo "2. as parameters\n";
echo '   sql          => ', $frag->asCondition('params'), "\n";
echo '   values       => ',
    implode(', ', array_map(fn ($v) => $v->dump(), $frag->bindings())), "\n";

// 3 — one rule, every dialect ---------------------------------------------------
// The differences below are the databases', not the rule's. Nothing in the
// program changed.

echo "3. every dialect\n";
foreach (Sql::dialects() as $dialect) {
    printf("   %-12s => %s\n", $dialect,
        Sql::translate($rule, $dialect, $bindings)->asCondition());
}

// 4 — a rule over a related table ------------------------------------------------
// An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
// which is the shape a database can actually use an index for.

echo "4. over a relation\n";
$lines = Sel::compile('ALL(ITEMS, I, I["QTY"] > 0)');
echo '   sql          => ', Sql::translate($lines, 'mariadb', [
    'ITEMS' => Binding::relation('order_items', 'oi',
        fields: ['QTY' => Binding::column('qty', type: 'NUM')],
        correlate: '`oi`.`order_id` = `o`.`id`'),
])->asCondition(), "\n";

// 5 — refusal is an ordinary answer -----------------------------------------------
// tryTranslate returns null so the caller can fall back to the evaluator without
// a try/catch. translate() throws the same refusal with the reason written out,
// which is what you want in a build-time audit of a rule set.

echo "5. refusal\n";
$unbound = Sel::compile('MYSTERY > 1');
echo '   tryTranslate => ', Sql::tryTranslate($unbound, 'mariadb', $bindings) === null
    ? 'null — evaluate it in the host instead' : 'translated', "\n";
try {
    Sql::translate($unbound, 'mariadb', $bindings);
} catch (SqlError $e) {   // typed: a bug in the translator still escapes
    echo '   translate    => ', $e->code, "\n";
}

// 6 — what a fragment knows about itself -------------------------------------------
// A caveat is the map saying "this dialect's answer may differ from SEL's here".
// An empty list is the layer promising it does not.

echo "6. the fragment\n";
echo '   kind         => ', $frag->kind, "\n";
echo '   dialect      => ', $frag->dialect, "\n";
echo '   exact        => ', count($frag->caveats) === 0 ? 'TRUE' : 'FALSE', "\n";
