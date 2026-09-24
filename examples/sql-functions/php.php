<?php
// The application's own functions, in memory and in PostgreSQL — PHP.
//
//   tools/check-usage.sh sql-functions         (starts the databases for you)
//
// The application registers five functions of its own. Each has a local
// implementation — the code Sel::registerFunction runs — and four also get a
// SQL spelling for PostgreSQL, which the application promises computes the same
// thing (spec §8.1, sql/MAP.md §4.7):
//
//   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
//   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
//   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
//   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
//   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
//   WORDS(title)                returns a list               -> no spelling: stays in memory
//
// Every pipeline prints its plan and its rows, and whether those rows are the rows
// the same program computes in memory — which is how the example checks that the
// two implementations of each function agree on this data.
//
// The four files beside this one print byte-identical output.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';
require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';
require_once __DIR__ . '/../lib/db.php';

use Sel\Args;
use Sel\Sel;
use Sel\Sql\Binding;
use Sel\Sql\Map;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;
use Sel\Value;
use function Db\{connect, query, render, runner};

$conn = connect('postgresql');

// 1 — the local implementations ------------------------------------------------------
// What Sel::registerFunction runs: plain code, or — where exact decimal
// arithmetic matters — a SEL expression, so the local answer has SEL's numbers.

// EXAMPLE-BEGIN local
$slug = function (Args $args): Value {
    $out = '';
    $dash = false;
    foreach (str_split($args->text(0)) as $c) {
        $o = ord($c);
        $o = $o >= 0x41 && $o <= 0x5A ? $o + 32 : $o;            // bytes, ASCII only, as SQL's
        if (($o >= 0x61 && $o <= 0x7A) || ($o >= 0x30 && $o <= 0x39)) {   // [^a-z0-9]+ sees it
            if ($dash && $out !== '') {
                $out .= '-';
            }
            $out .= chr($o);
            $dash = false;
        } else {
            $dash = true;
        }
    }
    return Value::text($out);
};

$margin = Sel::compile('ROUND((PRICE - COST) * 100 / PRICE, 1)');

$marginPct = function (Args $args) use ($margin): Value {
    $ctx = Value::none();
    $ctx->set('PRICE', $args->val(0));
    $ctx->set('COST', $args->val(1));
    return $margin->run($ctx);
};

$rates = [];
foreach (query($conn, 'SELECT * FROM vat_rates')->values() as $r) {
    $rates[$r->get('country')->asText()][$r->get('category')->asText()] = $r->get('rate');
}

$vatRate = function (Args $args) use ($rates): Value {
    [$country, $category] = [$args->text(0), $args->text(1)];
    // ?? tests for null, not truthiness, so a rate with no children still counts
    $rate = $rates[$country][$category] ?? $rates[$country]['*'] ?? null;
    return $rate !== null ? $rate->copy() : Value::text('0');
};

$shipping = Sel::compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
                         . ' * IF(COUNTRY $== "PL", 1, 2)');

$shippingCost = function (Args $args) use ($shipping): Value {
    $ctx = Value::none();
    $ctx->set('KG', $args->val(0));
    $ctx->set('COUNTRY', $args->val(1));
    return $shipping->run($ctx);
};

$hasTag = function (Args $args): Value {
    [$tags, $tag] = [$args->val(0), $args->text(1)];
    $values = $tags->size() > 0 ? $tags->values() : [$tags];   // a scalar is a list of one
    foreach ($values as $v) {
        if ($v->asText() === $tag) {
            return Value::bool(true);
        }
    }
    return Value::bool(false);
};

$words = function (Args $args) use ($slug): Value {
    $out = Value::none();
    foreach (explode('-', $slug($args)->asText()) as $w) {
        if ($w !== '') {
            $out->set((string) ($out->size() + 1), Value::text($w));
        }
    }
    return $out;
};

Sel::registerFunction('SLUG', 1, 1, $slug);
Sel::registerFunction('MARGIN_PCT', 2, 2, $marginPct);
Sel::registerFunction('VAT_RATE', 2, 2, $vatRate);
Sel::registerFunction('SHIPPING_COST', 2, 2, $shippingCost);
Sel::registerFunction('HAS_TAG', 2, 2, $hasTag);
Sel::registerFunction('WORDS', 1, 1, $words);
// EXAMPLE-END local

// 2 — the SQL spellings ------------------------------------------------------------------
// After the functions: a spelling for a name that is not registered is refused.

// EXAMPLE-BEGIN spell
Map::define('postgresql', 'funcs', 'SLUG',
    ['tpl' => 'slug({0})', 'ret' => 'TEXT', 'args' => ['TEXT']]);
Map::define('postgresql', 'funcs', 'MARGIN_PCT',
    ['tpl' => 'margin_pct({0}, {1})', 'ret' => 'NUM', 'args' => ['NUM', 'NUM']]);
Map::define('postgresql', 'funcs', 'VAT_RATE',
    ['tpl' => 'vat_rate({0}, {1})', 'ret' => 'NUM', 'args' => ['TEXT', 'TEXT']]);
Map::define('postgresql', 'funcs', 'SHIPPING_COST',
    ['tpl' => 'shipping_cost({0}, {1})', 'ret' => 'NUM', 'args' => ['NUM', 'TEXT']]);
Map::define('postgresql', 'funcs', 'HAS_TAG',
    ['tpl' => '({1} = ANY(ARRAY[{0}]))', 'ret' => 'BOOL', 'args' => ['LIST', 'TEXT']]);
// WORDS returns a list: no spelling can say that, so it has none.
// EXAMPLE-END spell

function relation(string $table, string $alias, array $fields): Binding
{
    $columns = [];
    foreach ($fields as $name => $kind) {
        $columns[$name] = Binding::column($name, $alias, $kind);
    }
    return Binding::relation($table, $alias, fields: $columns);
}

$schema = [
    'PRODUCTS' => relation('products', 'p', ['product_id' => 'NUM', 'title' => 'TEXT',
                                             'category' => 'TEXT', 'price' => 'NUM',
                                             'cost' => 'NUM', 'weight_kg' => 'NUM',
                                             'tag1' => 'TEXT', 'tag2' => 'TEXT', 'tag3' => 'TEXT']),
    'ORDERS'   => relation('orders', 'o', ['order_id' => 'NUM', 'country' => 'TEXT']),
    'LINES'    => relation('order_lines', 'l', ['order_id' => 'NUM', 'line_no' => 'NUM',
                                                'product_id' => 'NUM', 'qty' => 'NUM']),
];

$tables = Value::none();
foreach ([['PRODUCTS', 'products', 'product_id'], ['ORDERS', 'orders', 'order_id'],
          ['LINES', 'order_lines', 'order_id, line_no']] as [$name, $table, $key]) {
    $tables->set($name, query($conn, "SELECT * FROM $table ORDER BY $key"));
}

const PIPELINES = [
    ['gifts with a margin of 40% or more', 'gifts-by-margin.sel'],
    ['gross revenue and shipping per country', 'gross-per-country.sel'],
    ['words in the titles of the better-margin products', 'title-words.sel'],
];

foreach (PIPELINES as $i => [$title, $file]) {
    // EXAMPLE-BEGIN run
    $program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
    $plan = Sql::planHybrid($program, 'postgresql', $schema);
    $rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
    // EXAMPLE-END run
    $kind = $plan->pureSql ? 'pure_sql' : ($plan->pureMemory ? 'pure_memory' : 'hybrid');
    echo $i + 1, ". $title\n";
    echo '   plan        ', $kind, "\n";
    if ($plan->sqlStatement !== null) {
        echo '   sql         ', $plan->sqlStatement->asStatement(), "\n";
        echo '   caveats     ', implode(', ', $plan->sqlStatement->caveats) ?: '(none)', "\n";
    }
    echo render($rows, '   | '), "\n";
    echo '   in memory   ', $program->run($tables->copy())->dump() === $rows->dump()
        ? 'same rows' : 'DIFFERENT', "\n";
}

// 4 — what strict translation says ---------------------------------------------------------
// A spelling is the application's promise, not this layer's, so strict mode —
// exact or nothing — refuses it.

echo count(PIPELINES) + 1, ". strict translation\n";
// EXAMPLE-BEGIN strict
$rule = Sel::compile('SLUG(TITLE) $== "cast-iron-pan"');
$title = ['TITLE' => Binding::column('title', 'p', 'TEXT')];
echo '   caveats     ', implode(', ', Sql::translate($rule, 'postgresql', $title)->caveats), "\n";
try {
    Sql::translate($rule, 'postgresql', $title, ['strict' => true]);
} catch (SqlError $e) {
    echo '   strict      ', $e->code, "\n";
}
// EXAMPLE-END strict
