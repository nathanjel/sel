<?php
// A database runner for the SQL examples — PHP.
//
//   require_once __DIR__ . '/../lib/db.php';
//   use function Db\{connect, query, render, runner};
//
// Every example that talks to a database goes through the functions below, and
// the four files beside this one do the same with their own drivers. The
// contract is small on purpose, because it is what makes five hosts print the
// same thing:
//
//   - connect($dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in
//     the environment (tools/check-usage.sh sets them).
//   - query($conn, $sql, $params) runs a statement whose placeholders are `?`
//     — the spelling SEL's `params` mode emits — and returns the rows as a SEL
//     Value: a list of records, every column TEXT and SQL NULL as NULL. Money
//     stays text, as it does everywhere in SEL; a float reaching here is an
//     error.
//   - runner($conn) is query() in the shape Sql::executeHybrid() wants.
//   - render($rows, $pad) prints rows as `field=value` lines. It is written in
//     SEL, so it prints the same bytes on every host by construction.
//
// PDO takes `?` itself, for all three drivers, and its scanner is quote-aware:
// a `?` inside a quoted literal or identifier stays a character (a numeric
// guard's regex literal has `?` in it), and MariaDB's backslash escapes are
// honoured. So nothing is rewritten here. PDO does convert two things on the
// way back: integers arrive as PHP ints, which text() spells in decimal, and
// DOUBLE / float8 arrive as PHP floats, which text() refuses. DECIMAL and
// NUMERIC arrive as strings with their scale ("12.50"), dates as ISO text,
// with MySQL's emulated prepares (PDO's default there) and native ones alike.

declare(strict_types=1);

namespace Db;

require_once __DIR__ . '/../../php/src/bootstrap.php';

use PDO;
use Sel\Sel;
use Sel\Value;

function env(string $name, ?string $default = null): ?string
{
    $value = getenv($name);
    return $value === false ? $default : $value;
}

// EXAMPLE-BEGIN runner
function connect(string $dialect): PDO
{
    if ($dialect === 'sqlite') {
        return new PDO('sqlite:' . env('SEL_DB_SQLITE_FILE'));
    }
    $dsn = match ($dialect) {
        'postgresql' => 'pgsql:port=' . env('SEL_DB_POSTGRESQL_PORT'),
        'mariadb'    => 'mysql:charset=utf8mb4;port=' . env('SEL_DB_MARIADB_PORT'),
        default      => throw new \InvalidArgumentException("no runner for $dialect"),
    };
    return new PDO($dsn . ';host=' . env('SEL_DB_HOST', '127.0.0.1') . ';dbname=' . env('SEL_DB_NAME'),
                   env('SEL_DB_USER'), env('SEL_DB_PASSWORD'));
}

/** @param list<Value> $params */
function query(PDO $conn, string $sql, array $params = []): Value
{
    $stmt = $conn->prepare($sql);
    $stmt->execute(array_map(fn (Value $p) => $p->isNull() ? null : $p->asText(), $params));
    $rows = Value::none();
    $n = 0;
    while (($row = $stmt->fetch(PDO::FETCH_ASSOC)) !== false) {
        $record = Value::none();
        foreach ($row as $name => $cell) {
            $record->set((string) $name, $cell === null ? Value::null() : Value::text(text($cell)));
        }
        $rows->set((string) ++$n, $record);
    }
    return $rows;
}

function text(int|string|float $cell): string
{
    if (is_float($cell)) {
        throw new \TypeError('a float reached SEL; declare the column DECIMAL or TEXT');
    }
    return (string) $cell;
}

function runner(PDO $conn): callable
{
    return fn (string $sql, array $params) => query($conn, $sql, $params);
}
// EXAMPLE-END runner

function render(Value $rows, string $pad = ''): string
{
    static $render = null;
    $render ??= Sel::compile('JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & "=" & (_ ?? "NULL")), "  ")), "\\n")');
    $ctx = Value::none();
    $ctx->set('ROWS', $rows);
    $ctx->set('PAD', Value::text($pad));
    return $render->run($ctx)->asText();
}
