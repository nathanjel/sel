#!/usr/bin/env php
<?php
/** Persistent PDO client used by the corrected database benchmark harness. */

declare(strict_types=1);

function option(string $name, string $fallback): string
{
    global $argv;
    for ($i = 1; $i + 1 < count($argv); $i++) {
        if ($argv[$i] === $name) return $argv[$i + 1];
    }
    return $fallback;
}

function emit(array $value): void
{
    echo json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . PHP_EOL;
    flush();
}

$dialect = option('--dialect', 'postgresql');
$database = option('--database', 'sel_oracle');
$connectStart = hrtime(true);
try {
    if ($dialect === 'postgresql') {
        $dsn = getenv('SEL_BENCH_PG_DSN') ?: "pgsql:host=127.0.0.1;port=15432;dbname={$database}";
        $user = getenv('SEL_BENCH_PG_USER') ?: 'postgres';
        $password = getenv('SEL_BENCH_PG_PASSWORD') ?: 'sel';
    } elseif ($dialect === 'mariadb') {
        $dsn = getenv('SEL_BENCH_MARIA_DSN') ?: "mysql:unix_socket=/var/lib/mysql/mysql.sock;dbname={$database};charset=utf8mb4";
        $user = getenv('SEL_BENCH_MARIA_USER') ?: 'nathan';
        $password = getenv('SEL_BENCH_MARIA_PASSWORD') ?: '';
    } else {
        throw new RuntimeException("unsupported database dialect: {$dialect}");
    }
    $pdo = new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_STRINGIFY_FETCHES => true,
    ]);
    $quote = $dialect === 'postgresql' ? '"' : '`';
    $tableRows = [];
    foreach (['customers', 'orders', 'order_items', 'products', 'categories'] as $table) {
        $statement = $pdo->query("SELECT COUNT(*) AS row_count FROM {$quote}{$table}{$quote}");
        $tableRows[strtoupper($table)] = (int) $statement->fetchColumn();
    }
    emit([
        'ready' => true,
        'dialect' => $dialect,
        'connection_reused' => true,
        'boundary' => 'execute/fetch combined; PDO buffered fetchAll',
        'connect_ms' => (hrtime(true) - $connectStart) / 1_000_000.0,
        'prepared_statement_policy' => 'none; generated reference SQL has no bind parameters',
        'table_rows' => $tableRows,
        'total_source_rows' => array_sum($tableRows),
    ]);
    while (($line = fgets(STDIN)) !== false) {
        $request = json_decode($line, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($request) || !isset($request['id'], $request['sql'])) {
            throw new RuntimeException('invalid benchmark request');
        }
        $queryStart = hrtime(true);
        $statement = $pdo->query((string) $request['sql']);
        $rows = $statement->fetchAll(PDO::FETCH_ASSOC);
        $queryMs = (hrtime(true) - $queryStart) / 1_000_000.0;
        emit([
            'id' => (string) $request['id'],
            'rows' => $rows,
            'db_execute_fetch_ms' => $queryMs,
            'db_materialize_ms' => 0.0,
        ]);
    }
} catch (Throwable $error) {
    emit(['error' => $error->getMessage()]);
    exit(1);
}
