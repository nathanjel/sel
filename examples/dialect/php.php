<?php
// Adding a SQL flavour — teaching the translator about your database, from PHP.
//
//   php examples/dialect/php.php
//
// The shipped map covers four targets over two bases. A deployment is rarely
// exactly one of them: a driver wants numbered placeholders, a function is
// spelled differently, an extension is not installed. A dialect is registered
// rather than forked, so what you write is only the difference.
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
use Sel\Sql\Emit;
use Sel\Sql\Fragment;
use Sel\Sql\Map;
use Sel\Sql\Sql;

$rule = Sel::compile('NAME $== "ok" AND TOTAL > 10.00');
$bindings = [
    'NAME'  => Binding::column('name', 't', 'TEXT'),
    'TOTAL' => Binding::column('total', 't', 'NUM'),
];
$sqlIn = fn (string $dialect) =>
    Sql::translate($rule, $dialect, $bindings)->asCondition('params');

// 1 — what ships ---------------------------------------------------------------

echo "1. what ships\n";
echo '   targets      => ', implode(' ', Sql::dialects()), "\n";
echo '   postgresql   => ', implode(' -> ', Map::chain('postgresql')), "\n";

// 2 — a flavour of your own -------------------------------------------------------
// `extends` is the whole mechanism: the new dialect answers for what it declares
// and defers upward for everything else. Two-phase lookup -- the whole overlay
// chain, then the whole shipped chain -- so an override never half-applies.

echo "2. a flavour of your own\n";
Map::defineDialect('pg-libpq', [
    'extends' => 'postgresql',
    'version' => '15',
    'target' => true,                         // a base is not a target; this is a server
    'lexical' => ['placeholder' => '${n}'],   // libpq numbers its parameters
]);
echo '   targets      => ', implode(' ', Sql::dialects()), "\n";
echo '   chain        => ', implode(' -> ', Map::chain('pg-libpq')), "\n";
echo '   base         => ', $sqlIn('postgresql'), "\n";
echo '   pg-libpq     => ', $sqlIn('pg-libpq'), "\n";

// 3 — spelling one function differently ---------------------------------------------
// {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
// while every position SEL reports is one-based -- these are template holes, not
// SEL positions. The entry also says what it returns, because the translator
// infers kinds and will not guess.

echo "3. one function, respelled\n";
Map::define('pg-libpq', 'funcs', 'UPPER', ['tpl' => 'UPPER({0} COLLATE "C")', 'ret' => 'TEXT']);
echo '   upper        => ',
    Sql::translate(Sel::compile('UPPER(NAME)'), 'pg-libpq', $bindings)->asValue(), "\n";

// 4 — withdrawing what a deployment does not have ------------------------------------
// A null entry withdraws it. This is not the same as leaving it unmapped: it is
// the map saying "not here", and the rule is refused rather than emitted against
// a function the server does not have.

echo "4. withdrawing an entry\n";
Map::define('pg-libpq', 'funcs', 'RMATCH', null);
$re = Sel::compile("RMATCH('^a', NAME)");
echo '   postgresql   => ', Sql::tryTranslate($re, 'postgresql', $bindings) === null
    ? 'refused' : 'translated', "\n";
echo '   pg-libpq     => ', Sql::tryTranslate($re, 'pg-libpq', $bindings) === null
    ? 'refused' : 'translated', "\n";

// 5 — a builder, for what a template cannot say -----------------------------------------
// The escape hatch. It receives the emitter and the already-rendered arguments,
// and returns a fragment, so it can do what no string with holes in it can.
//
// Splice the argument's `parts` rather than its rendered SQL. A part list is
// strings alternating with parameter slots, so splicing keeps a bound value
// bound; flattening it to a string first would inline whatever the argument
// carried and quietly turn a prepared statement back into concatenation.

echo "5. a builder\n";
Map::defineBuilder('pg-libpq', 'funcs', 'LEN', fn (Emit $emit, array $args) =>
    new Fragment(['length(', ...$args[0]->parts, ')'], 'NUM', $emit->dialect()));
echo '   len          => ',
    Sql::translate(Sel::compile('LEN(NAME)'), 'pg-libpq', $bindings)->asValue(), "\n";

// 6 — putting it back ---------------------------------------------------------------------
// reset() drops every registration and leaves the shipped map. Worth knowing in
// a test suite: a registration that leaks into the next test is a test that
// passes for the wrong reason.

echo "6. reset\n";
Map::reset();
echo '   targets      => ', implode(' ', Sql::dialects()), "\n";
echo '   pg-libpq     => ', Map::exists('pg-libpq') ? 'still there' : 'gone', "\n";
