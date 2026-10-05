<?php
// The hybrid-parity lane (sql/oracle/hybrid.json) for the PHP host, shared by
// php/bin/sqlo (`hybrid`, on each server a DSN names) and tools/check-php-optimizer.php
// (on an in-memory SQLite, so the gate runs it without a server).

declare(strict_types=1);

require_once __DIR__ . '/../src/Sql/bootstrap.php';
require_once __DIR__ . '/json-bindings.php';

use Sel\Dec;
use Sel\Registry;
use Sel\Sel;
use Sel\SelError;
use Sel\Sql\Sql;
use Sel\Sql\SqlError;
use Sel\Value;

/**
 * A server's number in a form Dec::parse accepts.
 *
 * SQLite prints a large REAL as `1.2345678901234567e+19`, and Dec::parse rejects
 * scientific notation because SEL has none. Without this the comparator would
 * answer "not both numbers" and report a DIFFER on every large-number
 * expression, which is a defect in the CHECK rather than in the translation --
 * the value may well be right, and if it is wrong the caveat is what should say
 * so, not a parse failure.
 *
 * The expansion is exact: it moves the point, it does not round. A value that
 * lost precision inside the server has already lost it, and that difference is
 * what decimal-float declares.
 */
function plain_number(string $text): string
{
    if (preg_match('/^(-?)([0-9]+)(?:\.([0-9]+))?[eE]([-+]?[0-9]+)$/', $text, $m) !== 1) {
        return $text;
    }
    [, $sign, $int, $frac, $exp] = $m + [3 => ''];
    $digits = $int . $frac;
    $point = strlen($int) + (int) $exp;
    if ($point <= 0) {
        return $sign . '0.' . str_repeat('0', -$point) . $digits;
    }
    if ($point >= strlen($digits)) {
        return $sign . $digits . str_repeat('0', $point - strlen($digits));
    }
    return $sign . substr($digits, 0, $point) . '.' . substr($digits, $point);
}

/**
 * The hybrid-parity lane (sql/oracle/hybrid.json): a program is planned,
 * its SQL prefix is run on the server through executeHybrid's runner, and the
 * result must be what SEL answers for the whole program over the same rows.
 * See the note in hybrid.json for the contract this holds a plan to: the value
 * (keys included, except for a pure_sql ROWSET), the error (code and position,
 * or a pure_memory plan), the caller's context untouched, and no reliance on a
 * database's natural order.
 */
function hybrid_normalise($x)
{
    if (is_array($x)) {
        $out = [];
        foreach ($x as $k => $v) {
            $out[(string) $k] = hybrid_normalise($v);
        }
        return $out;
    }
    if (is_bool($x)) {
        return $x ? 'TRUE' : 'FALSE';
    }
    if ($x === null) {
        return 'NULL';
    }
    $text = (string) $x;
    $d = Dec::parse(plain_number($text));
    return $d !== null ? Dec::format($d) : $text;
}

/** @return array{0:string,1:mixed} ['ok', Value] or ['err', "CODE@line:col"] */
function hybrid_outcome(callable $fn): array
{
    try {
        $v = $fn();
        return ['ok', $v instanceof Value ? $v : Value::fromNative($v)];
    } catch (SelError $e) {
        return ['err', $e->code . '@' . $e->line . ':' . $e->col];
    } catch (SqlError $e) {
        return ['err', 'SQL:' . $e->code];
    } catch (Throwable $e) {
        return ['err', 'HOST:' . get_class($e) . ': ' . substr($e->getMessage(), 0, 160)];
    }
}

function hybrid_column(array $native, string $field): array
{
    $out = [];
    foreach ($native as $row) {
        $out[] = is_array($row) && array_key_exists($field, $row) ? hybrid_normalise($row[$field]) : '<absent>';
    }
    return $out;
}

/**
 * The application functions the corpus's `application` section calls (see its
 * note): POKE writes its argument in place, HOSTF is an identity with no SQL
 * spelling. Registering replaces an earlier registration, so this is idempotent.
 */
function hybrid_register_application_functions(): void
{
    Registry::registerFunction('POKE', 1, 1, static function ($args): Value {
        $v = $args->val(0);
        $v->set('k', Value::fromNative('9'));
        return $v;
    });
    Registry::registerFunction('HOSTF', 1, 1, static fn ($args): Value => $args->val(0));
}

/**
 * Run the corpus on `$pdo`, which speaks `$dialect`.
 *
 * @return array{failed:int}
 */
function run_hybrid(PDO $pdo, string $dialect, bool $verbose, string $label = ''): array
{
    $oracle = __DIR__ . '/../../sql/oracle';
    $spec = json_decode((string) file_get_contents($oracle . '/hybrid.json'), true);
    if (!is_array($spec)) {
        throw new RuntimeException('hybrid.json is not JSON');
    }
    $fixture = $spec['fixture'][$dialect] ?? null;
    if ($fixture === null) {
        echo "{$label}hybrid: no fixture for {$dialect}, skipped\n";
        return ['failed' => 0];
    }
    $sql = preg_replace('/^\s*--.*$/m', '', (string) file_get_contents($oracle . '/' . $fixture)) ?? '';
    $n = 0;
    foreach (explode(';', $sql) as $stmt) {
        if (trim($stmt) !== '') {
            $pdo->exec($stmt);
            $n++;
        }
    }
    if ($n === 0) {
        throw new RuntimeException("{$fixture} contained no statements");
    }
    $bindings = bindings_from_json($spec['bindings'], $label);

    // The relations as SEL sees them: lists of records of TEXT, in id order.
    $base = [];
    foreach ($spec['relations'] as $name => $rel) {
        $rows = [];
        foreach ($pdo->query($rel['query'])->fetchAll(PDO::FETCH_ASSOC) as $r) {
            $one = [];
            foreach ($rel['columns'] as $col) {
                $one[$col] = (string) $r[$col];
            }
            $rows[] = $one;
        }
        $base[$name] = $rows;
    }

    // What the runner hands executeHybrid back: the rowset the statement
    // answers, every cell TEXT as in the context above, NULL kept.
    $runner = static function (string $statement, array $bound) use ($pdo): array {
        $stmt = $pdo->prepare($statement);
        foreach ($bound as $i => $value) {
            $native = $value->toNative();
            $type = $native === null ? PDO::PARAM_NULL : (is_bool($native) ? PDO::PARAM_BOOL : PDO::PARAM_STR);
            $stmt->bindValue($i + 1, $native, $type);
        }
        $stmt->execute();
        $rows = [];
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $r) {
            $rows[] = array_map(static fn ($v) => $v === null ? null : (string) $v, $r);
        }
        return $rows;
    };

    $tally = ['bad' => [], 'ok' => 0, 'kinds' => ['pure_sql' => 0, 'hybrid' => 0, 'pure_memory' => 0]];
    $skipped = 0;
    foreach ($spec['programs'] as $case) {
        foreach ($spec['contexts'] as $ctxSpec) {
            foreach ($case['requires'] ?? [] as $var) {
                if (!array_key_exists($var, $ctxSpec['vars'])) {
                    $skipped++;
                    continue 2;
                }
            }
            hybrid_case($case, $case['name'] . ' [' . $ctxSpec['name'] . ']', array_merge($base, $ctxSpec['vars']),
                $dialect, $bindings, $runner, $verbose, $tally);
        }
    }
    // Programs that call application functions, over the relations and the
    // section's own variables.
    if (isset($spec['application'])) {
        hybrid_register_application_functions();
        foreach ($spec['application']['programs'] as $case) {
            hybrid_case($case, $case['name'], array_merge($base, $spec['application']['vars']),
                $dialect, $bindings, $runner, $verbose, $tally);
        }
    }

    foreach ($spec['bounded'] ?? [] as $case) {
        $program = Sel::compile($case['sel']);
        $t0 = microtime(true);
        try {
            Sql::planHybrid($program, $dialect, $bindings);
        } catch (Throwable $e) {
            // a refusal is an answer; only the time is asserted
        }
        $ms = (microtime(true) - $t0) * 1000;
        if ($ms > $case['max_ms']) {
            $tally['bad'][] = [$case['name'], '', sprintf('planning took %.0f ms, the bound is %d', $ms, $case['max_ms'])];
        } else {
            $tally['ok']++;
        }
    }

    foreach ($tally['bad'] as [$name, $statement, $why]) {
        printf("DIFFER  %s\n        %s\n        %s\n", $name, $statement, $why);
    }
    $kinds = $tally['kinds'];
    printf("{$label}hybrid: %d agree, %d differ (plans: %d pure_sql, %d hybrid, %d pure_memory; %d variants skipped)\n",
        $tally['ok'], count($tally['bad']), $kinds['pure_sql'], $kinds['hybrid'], $kinds['pure_memory'], $skipped);
    return ['failed' => count($tally['bad'])];
}

/**
 * One program over one context: the corpus guard (what run() must answer),
 * then the plan executed and held to run(), and the caller's context unchanged.
 *
 * @param array<string,mixed> $case
 * @param array<string,mixed> $vars the context, as natives
 * @param array{bad:list<array{0:string,1:string,2:string}>,ok:int,kinds:array<string,int>} $tally
 */
function hybrid_case(array $case, string $name, array $vars, string $dialect, array $bindings,
                     callable $runner, bool $verbose, array &$tally): void
{
    $program = Sel::compile($case['sel']);
    $direct = hybrid_outcome(static fn () => $program->run(Value::fromNative($vars)));

    // The corpus guard: what SEL itself must answer.
    $exp = $case['expect'] ?? [];
    $guard = null;
    if (isset($exp['error'])) {
        if ($direct[0] !== 'err' || !str_starts_with($direct[1], $exp['error'] . '@')) {
            $guard = "SEL answered " . json_encode($direct) . " where the corpus says {$exp['error']}";
        }
    } elseif ($direct[0] !== 'ok') {
        $guard = 'SEL raised ' . json_encode($direct[1]);
    } else {
        foreach ($exp['fields'] ?? [] as $field => $want) {
            $got = hybrid_column($direct[1]->toNative(), $field);
            if ($got !== $want) {
                $guard = "run() column {$field} is " . json_encode($got) . ', the corpus says ' . json_encode($want);
            }
        }
        if (isset($exp['keys']) && array_map('strval', $direct[1]->keys()) !== $exp['keys']) {
            $guard = 'run() keys are ' . json_encode($direct[1]->keys()) . ', the corpus says ' . json_encode($exp['keys']);
        }
        if (array_key_exists('value', $exp) && hybrid_normalise($direct[1]->toNative()) !== $exp['value']) {
            $guard = 'run() is ' . json_encode(hybrid_normalise($direct[1]->toNative())) . ', the corpus says ' . json_encode($exp['value']);
        }
    }
    if ($guard !== null) {
        $tally['bad'][] = [$name, '', "CORPUS: {$guard}"];
        return;
    }

    try {
        $plan = Sql::planHybrid($program, $dialect, $bindings);
    } catch (Throwable $e) {
        $tally['bad'][] = [$name, '', 'planHybrid raised ' . get_class($e) . ': ' . $e->getMessage()];
        return;
    }
    $kind = $plan->kind();
    $tally['kinds'][$kind]++;
    $statement = $plan->sqlStatement?->asStatement() ?? '-';

    $callerContext = Value::fromNative($vars);
    $before = $callerContext->dump();
    $exec = hybrid_outcome(static fn () => Sql::executeHybrid($plan, $runner, $callerContext));
    $after = $callerContext->dump();

    $why = null;
    if ($direct[0] === 'err') {
        if ($exec !== $direct) {
            $why = 'run() raises ' . json_encode($direct[1]) . ' and the executed ' . $kind . ' plan gives ' . json_encode($exec);
        }
    } elseif ($exec[0] === 'err') {
        $why = "the executed {$kind} plan raises " . json_encode($exec[1]) . ' where run() answers';
    } else {
        // Keys are part of the value (spec 7.3) -- except that a pure_sql
        // plan answers the database's rowset, numbered 1..n.
        $want = hybrid_normalise($direct[1]->toNative());
        $got = hybrid_normalise($exec[1]->toNative());
        if ($kind === 'pure_sql') {
            $want = array_values($want);
            $got = array_values($got);
        } else {
            $want = ['keys' => array_map('strval', $direct[1]->keys()), 'rows' => array_values($want)];
            $got = ['keys' => array_map('strval', $exec[1]->keys()), 'rows' => array_values($got)];
        }
        if ($want !== $got) {
            $why = 'run()=' . json_encode($want) . "\n        plan=" . json_encode($got);
        }
    }
    if ($why === null && $before !== $after) {
        $why = "executeHybrid changed the caller's context ({$kind} plan)";
    }
    if ($why !== null) {
        $tally['bad'][] = [$name, "[{$kind}] {$statement}", $why];
        return;
    }
    $tally['ok']++;
    if ($verbose) {
        printf("  ok       %-64s %s\n", $name, $kind);
    }
}
