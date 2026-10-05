#!/usr/bin/env php
<?php
// Executing evidence for the server-limit half of the SQL contract.
//
// A .sqlt case asserts the string a translator emits; only a server can say
// whether that string RUNS. Two claims need one:
//
//   1. Unrolled lists. `T IN (v1, ..., vN)`, and ANY / ALL / SUM over a list,
//      translate to an expression nested O(N) deep, and SQLite refuses a tree
//      deeper than 1000 at run time ("Expression tree is too large (maximum
//      depth 1000)"). A translation the server cannot run is not a refusal and
//      not an answer.
//   2. Counts. TAKE/DROP counts are emitted exactly, and each server accepts a
//      different range: PostgreSQL and SQLite stop at 2^63-1, MariaDB/MySQL at
//      2^64-1. A count the server rejects must not be emitted.
//
// The rule applied to each probe: the translator REFUSES (SqlError), or the
// emitted SQL RUNS on the server and, where there is a data question, agrees
// with SEL over the same rows. Anything else is a failure, and the report says
// which server said what -- that table is the "supported server ranges" the
// contract needs written down.
//
//     tools/oracle-db.sh run php tools/check-sql-limits.php
//     SEL_SQL_SQLITE_DSN='sqlite::memory:' php tools/check-sql-limits.php
//
// No DSN for a dialect: that dialect is skipped, loudly. SQLite needs only
// pdo_sqlite.

declare(strict_types=1);

require_once __DIR__ . '/../php/src/Sql/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Sql\Binding;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;

$failures = 0;
$ran = 0;

function report(string $dialect, string $probe, string $outcome, bool $ok, string $detail = ''): void
{
    global $failures, $ran;
    $ran++;
    if (!$ok) {
        $failures++;
    }
    printf("%s %-10s %-44s %s%s\n", $ok ? 'ok  ' : 'FAIL', $dialect, $probe, $outcome,
        $detail === '' ? '' : ' -- ' . substr($detail, 0, 160));
}

function connect(string $dialect): ?PDO
{
    $env = 'SEL_SQL_' . strtoupper($dialect);
    $dsn = getenv($env . '_DSN');
    if ($dsn === false || $dsn === '') {
        echo "skip       {$dialect}: {$env}_DSN is not set\n";
        return null;
    }
    return new PDO($dsn, (string) (getenv($env . '_USER') ?: ''), (string) (getenv($env . '_PASS') ?: ''), [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    ]);
}

function q(string $dialect, string $ident): string
{
    return in_array($dialect, ['mariadb', 'mysql'], true) ? "`{$ident}`" : "\"{$ident}\"";
}

function fresh_table(PDO $pdo, string $dialect): void
{
    $k = q($dialect, 'k');
    $pdo->exec("DROP TABLE IF EXISTS {$k}");
    $pdo->exec("CREATE TABLE {$k} (id INTEGER PRIMARY KEY, t VARCHAR(40) NOT NULL, n INTEGER NOT NULL)");
    $ins = $pdo->prepare("INSERT INTO {$k} (id, t, n) VALUES (?, ?, ?)");
    foreach (['v0', 'v3', 'v7', 'zz', 'v1099', 'v4999'] as $i => $t) {
        $ins->execute([$i + 1, $t, $i + 1]);
    }
}

/** SEL's own answer: the ids whose row satisfies the rule, evaluated over the same data. */
function sel_ids(string $src, array $rows): array
{
    $p = Sel::compile($src);
    $ids = [];
    foreach ($rows as [$id, $t, $n]) {
        try {
            $v = $p->run(['T' => $t, 'N' => (string) $n]);
            if ($v->kind === 'BOOL' ? $v->scalar : false) {
                $ids[] = $id;
            }
        } catch (SelError $e) {
            // A row SEL refuses is not selected; the server must not select it either.
        }
    }
    return $ids;
}

$rows = [[1, 'v0', 1], [2, 'v3', 2], [3, 'v7', 3], [4, 'zz', 4], [5, 'v1099', 5], [6, 'v4999', 6]];

foreach (['sqlite', 'mariadb', 'mysql', 'postgresql'] as $dialect) {
    $pdo = connect($dialect);
    if ($pdo === null) {
        continue;
    }
    $k = q($dialect, 'k');
    fresh_table($pdo, $dialect);
    $bindings = ['T' => Binding::column('t', 'k', 'TEXT'), 'N' => Binding::column('n', 'k', 'NUM')];

    // 1. unrolled lists ------------------------------------------------------
    foreach ([900, 1100, 5000] as $n) {
        $list = implode(', ', array_map(fn($i) => '"v' . $i . '"', range(0, $n - 1)));
        $shapes = [
            "in-list N={$n}" => "T IN ({$list})",
            "any-over-list N={$n}" => "ANY(({$list}), _ \$== T)",
            "sum-over-list N={$n}" => "SUM(({$list}), 1) > N",
        ];
        foreach ($shapes as $probe => $src) {
            try {
                $frag = Sql::translate(Sel::compile($src), $dialect, $bindings);
                $cond = $frag->asCondition();
            } catch (SqlError $e) {
                report($dialect, $probe, 'refused ' . $e->getCode(), true);
                continue;
            }
            try {
                $got = $pdo->query("SELECT id FROM {$k} WHERE {$cond} ORDER BY id")->fetchAll(PDO::FETCH_COLUMN);
            } catch (Throwable $e) {
                report($dialect, $probe, 'translated, SERVER REJECTED', false, $e->getMessage());
                continue;
            }
            $want = sel_ids($src, $rows);
            $same = array_map('intval', $got) === $want;
            report($dialect, $probe, $same ? 'ran, agrees with SEL' : 'ran, DISAGREES with SEL', $same,
                $same ? '' : 'sql=' . json_encode($got) . ' sel=' . json_encode($want));
        }
    }

    // 2. counts ---------------------------------------------------------------
    $relation = ['ITEMS' => Binding::relation('k', 'k')];
    $counts = [
        '2^53+1' => '9007199254740993',
        'int64 max' => '9223372036854775807',
        '2^63' => '9223372036854775808',
        'uint64 max' => '18446744073709551615',
        '2^64' => '18446744073709551616',
        '1e23' => '99999999999999999999999',
    ];
    foreach (['TAKE', 'DROP'] as $verb) {
        foreach ($counts as $label => $c) {
            $probe = "{$verb}({$label})";
            $src = "ITEMS .> {$verb}({$c})";
            try {
                $stmt = Sql::translateStatement(Sel::compile($src), $dialect, $relation)->asStatement();
            } catch (SqlError $e) {
                report($dialect, $probe, 'refused ' . $e->getCode(), true);
                continue;
            }
            try {
                $got = count($pdo->query($stmt)->fetchAll());
            } catch (Throwable $e) {
                report($dialect, $probe, 'translated, SERVER REJECTED', false, $e->getMessage() . ' :: ' . $stmt);
                continue;
            }
            // SEL: TAKE of a huge count is everything, DROP of a huge count is nothing.
            $want = $verb === 'TAKE' ? count($rows) : 0;
            $same = $got === $want;
            report($dialect, $probe, $same ? "ran, {$got} rows as SEL" : "ran, {$got} rows, SEL says {$want}", $same);
        }
    }
    $pdo->exec("DROP TABLE IF EXISTS {$k}");
}

echo "\n{$ran} probes, {$failures} failed\n";
exit($failures === 0 ? 0 : 1);
