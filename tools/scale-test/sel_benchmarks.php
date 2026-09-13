#!/usr/bin/env php
<?php
// PHP scale/parity runner.
//
// The benchmark_results.json file is the Lisp oracle for both result rows and
// generated SQL. This runner deliberately checks the PHP evaluator and both SQL
// dialect plans; database execution remains the responsibility of the existing
// PostgreSQL/MariaDB harness.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/Sql/bootstrap.php';

use Sel\Args;
use Sel\RecordShape;
use Sel\Registry;
use Sel\Sel;
use Sel\Value;
use Sel\Sql\Binding;
use Sel\Sql\HybridPlan;
use Sel\Sql\Sql;

const ROOT = __DIR__ . '/../..';

/** @return array<string,mixed> */
function read_json(string $path): array
{
    $resolved = str_starts_with($path, '/') ? $path : ROOT . '/' . ltrim($path, '/');
    $value = json_decode((string) file_get_contents($resolved), true,
        512, JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING);
    if (!is_array($value)) throw new RuntimeException("expected JSON object/array: {$path}");
    return $value;
}

/** @return array<string,string> */
function options(): array
{
    $out = [];
    for ($i = 1; $i < $_SERVER['argc']; $i++) {
        $arg = $_SERVER['argv'][$i];
        if (str_starts_with($arg, '--')) {
            $key = substr($arg, 2);
            $out[$key] = $_SERVER['argv'][$i + 1] ?? '';
            $i++;
        }
    }
    return $out;
}

function parse_whole(string $text, int $fallback): int
{
    return preg_match('/\A-?[0-9]+/', trim($text), $m) === 1 ? (int) $m[0] : $fallback;
}

function register_benchmark_builtins(): void
{
    Registry::define([
        'name' => 'CUSTOM_VIP_SCORE', 'min' => 2, 'max' => 2,
        'fn' => static function (Args $a): Value {
            $tier = $a->val(0)->asText();
            $year = parse_whole($a->val(1)->asText(), 2024);
            $base = $tier === 'PLATINUM' ? 100 : ($tier === 'GOLD' ? 50 : ($tier === 'SILVER' ? 25 : 10));
            return Value::int($base + (2026 - $year) * 5);
        },
    ]);
    Registry::define([
        'name' => 'HOST_RISK_SCORE', 'min' => 2, 'max' => 2,
        'fn' => static function (Args $a): Value {
            $country = $a->val(0)->asText();
            $discount = parse_whole($a->val(1)->asText(), 0);
            return Value::int(($country === 'US' ? 30 : 10) + $discount * 2);
        },
    ]);
}

function float32(float $value): float
{
    return unpack('g', pack('g', $value))[1];
}

function clock_ns(): int
{
    return hrtime(true);
}

function elapsed_ms(int $started): float
{
    return (clock_ns() - $started) / 1_000_000.0;
}

/** @return array<string,int|null> */
function memory_snapshot(): array
{
    $currentRss = null;
    $peakRss = null;
    if (is_readable('/proc/self/status')) {
        $status = file_get_contents('/proc/self/status');
        if (is_string($status)) {
            if (preg_match('/^VmRSS:\s+(\d+)\s+kB$/m', $status, $match) === 1) {
                $currentRss = (int) $match[1] * 1024;
            }
            if (preg_match('/^VmHWM:\s+(\d+)\s+kB$/m', $status, $match) === 1) {
                $peakRss = (int) $match[1] * 1024;
            }
        }
    }
    return [
        'usage_bytes' => memory_get_usage(false),
        'allocated_bytes' => memory_get_usage(true),
        'peak_usage_bytes' => memory_get_peak_usage(false),
        'peak_allocated_bytes' => memory_get_peak_usage(true),
        'rss_bytes' => $currentRss,
        'peak_rss_bytes' => $peakRss,
    ];
}

/** @param array<string,mixed> $dataset */
function load_context(array $dataset): Value
{
    $context = Value::none();
    foreach ($dataset as $table => $rows) {
        $value = is_array($rows) && array_is_list($rows)
            ? Value::fromNativeRows($rows)
            : Value::fromNative($rows);
        $context->set(strtoupper((string) $table), $value);
    }
    $customers = $context->get('CUSTOMERS');
    if ($customers !== null) {
        $shapedCustomers = [];
        $customerValues = $customers->storage ?? $customers->values();
        $sourceShape = null;
        $augmentedShape = null;
        foreach ($customerValues as $customer) {
            $latitude = float32((float) $customer->get('latitude')->asText());
            $longitude = float32((float) $customer->get('longitude')->asText());
            $dx = $longitude - 13.404954;
            $dy = $latitude - 52.520008;
            $distance = sqrt($dx * $dx + $dy * $dy);
            if ($customer->shape !== null && $customer->storage !== null) {
                if ($sourceShape === null) {
                    $sourceShape = $customer->shape;
                    $keys = $sourceShape->keys;
                    $keys[] = 'dist_berlin';
                    $augmentedShape = RecordShape::intern($keys);
                }
                $targetShape = $augmentedShape;
                if ($customer->shape !== $sourceShape || $targetShape === null) {
                    $keys = $customer->shape->keys;
                    $keys[] = 'dist_berlin';
                    $targetShape = RecordShape::intern($keys);
                }
                $values = $customer->storage;
                $values[] = Value::text(number_format($distance, 6, '.', ''));
                $shapedCustomers[] = Value::fromShape($targetShape, $values);
            } else {
                $entries = $customer->entries();
                $entries[] = ['dist_berlin', Value::text(number_format($distance, 6, '.', ''))];
                $shapedCustomers[] = Value::fromEntries($entries);
            }
        }
        $context->set('CUSTOMERS', Value::list($shapedCustomers));
    }
    return $context;
}

function relation_column(string $column, string $table, string $type): Binding
{
    return Binding::column($column, $table, $type);
}

/** @param array<string,string> $fields */
function relation(string $table, array $fields): Binding
{
    $mapped = [];
    foreach ($fields as $key => $column) {
        $type = in_array($key, ['CODE', 'NAME', 'SKU', 'COUNTRY', 'TIER', 'STATUS'], true)
            ? 'TEXT' : 'NUM';
        $mapped[$key] = relation_column($column, $table, $type);
    }
    return Binding::relation($table, $table, $mapped);
}

/** @return array<string,Binding> */
function schema(string $dialect): array
{
    $categories = relation('categories', ['ID' => 'id', 'CODE' => 'code', 'NAME' => 'name', 'VAT_RATE' => 'vat_rate']);
    $products = relation('products', [
        'ID' => 'id', 'SKU' => 'sku', 'NAME' => 'name', 'CATEGORY_ID' => 'category_id',
        'PRICE' => 'price', 'IS_ACTIVE' => 'is_active',
    ]);
    $customerFields = [
        'ID' => Binding::column('id', 'customers', 'NUM'),
        'NAME' => Binding::column('name', 'customers', 'TEXT'),
        'TIER' => Binding::column('tier', 'customers', 'TEXT'),
        'COUNTRY' => Binding::column('country', 'customers', 'TEXT'),
        'CREATED_YEAR' => Binding::column('created_year', 'customers', 'NUM'),
        'LATITUDE' => Binding::column('latitude', 'customers', 'NUM'),
        'LONGITUDE' => Binding::column('longitude', 'customers', 'NUM'),
        'DIST_BERLIN' => $dialect === 'postgresql'
            ? Binding::raw('ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)', 'NUM')
            : Binding::raw('ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), POINT(13.404954, 52.520008)), 6)', 'NUM'),
    ];
    return [
        'CATEGORIES' => $categories,
        'PRODUCTS' => $products,
        'CUSTOMERS' => Binding::relation('customers', 'customers', $customerFields),
        'ORDERS' => relation('orders', ['ID' => 'id', 'CUSTOMER_ID' => 'customer_id', 'STATUS' => 'status', 'DISCOUNT' => 'discount', 'ORDER_YEAR' => 'order_year']),
        'ORDER_ITEMS' => relation('order_items', ['ID' => 'id', 'ORDER_ID' => 'order_id', 'PRODUCT_ID' => 'product_id', 'QUANTITY' => 'quantity', 'UNIT_PRICE' => 'unit_price']),
    ];
}

function canonical(mixed $value): mixed
{
    if (!is_array($value)) return $value;
    if (array_is_list($value)) return array_map('canonical', $value);
    $out = [];
    foreach ($value as $key => $item) $out[(string) $key] = canonical($item);
    ksort($out, SORT_STRING);
    return $out;
}

function benchmark_value(Value $value): mixed
{
    // Value::toNative() walks packed storage and shaped slots directly. Keep
    // this wrapper so the benchmark's conversion boundary remains explicit.
    return $value->toNative();
}

function same_value(mixed $actual, mixed $expected): bool
{
    return json_encode(canonical($actual), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
        === json_encode(canonical($expected), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

function plan_sql(?HybridPlan $plan): ?string
{
    return $plan?->sqlStatement?->asStatement('inline');
}

/** @param array<string,mixed> $expected @param list<string> $failures */
function check_plan(HybridPlan $plan, array $expected, string $dialect, array &$failures): void
{
    $sqlKey = $dialect === 'postgresql' ? 'sql_postgres' : 'sql_mariadb';
    if (plan_sql($plan) !== ($expected[$sqlKey] ?? null)) $failures[] = "{$dialect} SQL differs from Lisp reference";
    $expectedHybrid = ($expected['is_hybrid'] ?? false) === true || ($expected['has_continuation'] ?? false) === true;
    if ($plan->isHybrid() !== $expectedHybrid) $failures[] = "{$dialect} hybrid flag differs";
    if ($plan->pureSql !== !$expectedHybrid) $failures[] = "{$dialect} pureSql flag differs";
    if (($plan->continuationProgram !== null) !== (($expected['has_continuation'] ?? false) === true)) {
        $failures[] = "{$dialect} continuation presence differs";
    }
}

/** @return array<string,mixed> */
function fixture_metadata(string $path, array $dataset): array
{
    $resolved = str_starts_with($path, '/') ? $path : ROOT . '/' . ltrim($path, '/');
    $tableRows = [];
    foreach ($dataset as $table => $rows) {
        $tableRows[strtoupper((string) $table)] = is_array($rows) ? count($rows) : null;
    }
    return [
        'path' => realpath($resolved) ?: $resolved,
        'sha256' => hash_file('sha256', $resolved),
        'table_rows' => $tableRows,
        'total_source_rows' => array_sum(array_filter($tableRows, static fn ($count): bool => $count !== null)),
        'schema_version' => 1,
    ];
}

/** @return array<string,mixed> */
function runtime_metadata(): array
{
    $jit = function_exists('opcache_get_status') ? opcache_get_status(false) : false;
    $jitConfig = function_exists('opcache_get_configuration') ? opcache_get_configuration() : [];
    $options = ['opcache.enable_cli', 'opcache.jit_buffer_size', 'opcache.jit'];
    $requested = [];
    $effective = [];
    foreach ($options as $key) {
        $requested[$key] = ini_get($key);
        $effective[$key] = $jitConfig['directives'][$key] ?? ini_get($key);
    }
    $cpu = 'unknown';
    if (is_readable('/proc/cpuinfo')) {
        $cpuInfo = file_get_contents('/proc/cpuinfo');
        if (is_string($cpuInfo) && preg_match('/^model name\s*:\s*(.+)$/m', $cpuInfo, $match) === 1) {
            $cpu = trim($match[1]);
        }
    }
    $availableMemory = null;
    if (is_readable('/proc/meminfo')) {
        $memoryInfo = file_get_contents('/proc/meminfo');
        if (is_string($memoryInfo) && preg_match('/^MemAvailable:\s*(\d+)\s*kB$/m', $memoryInfo, $match) === 1) {
            $availableMemory = (int) $match[1] * 1024;
        }
    }
    return [
        'php' => PHP_VERSION,
        'sapi' => PHP_SAPI,
        'os' => php_uname('s') . ' ' . php_uname('r'),
        'machine' => php_uname('m'),
        'cpu' => $cpu,
        'logical_cpus' => function_exists('shell_exec') ? (int) shell_exec('getconf _NPROCESSORS_ONLN 2>/dev/null') : null,
        'available_memory_bytes' => $availableMemory,
        'opcache_requested' => $requested,
        'opcache_effective' => $effective,
        'jit_available' => is_array($jit) && (($jit['jit']['on'] ?? false) === true),
        'jit_status' => is_array($jit) ? ($jit['jit'] ?? null) : null,
        'memory_limit' => ini_get('memory_limit'),
        'gc_status' => function_exists('gc_status') ? gc_status() : null,
    ];
}

/** @param list<float> $samples @return array<string,mixed> */
function phase_stats(array $samples): array
{
    if ($samples === []) throw new RuntimeException('cannot summarize empty samples');
    foreach ($samples as $sample) {
        if (!is_finite($sample) || $sample < 0) throw new RuntimeException('invalid benchmark sample');
    }
    sort($samples, SORT_NUMERIC);
    $count = count($samples);
    $mean = array_sum($samples) / $count;
    $middle = intdiv($count, 2);
    $median = $count % 2 === 1 ? $samples[$middle] : ($samples[$middle - 1] + $samples[$middle]) / 2.0;
    $variance = 0.0;
    foreach ($samples as $sample) $variance += ($sample - $mean) ** 2;
    $stdev = $count > 1 ? sqrt($variance / ($count - 1)) : 0.0;
    $rank = max(1, (int) ceil(0.95 * $count));
    return [
        'count' => $count,
        'mean_ms' => $mean,
        'median_ms' => $median,
        'min_ms' => $samples[0],
        'max_ms' => $samples[$count - 1],
        'stdev_ms' => $stdev,
        'cv' => $mean == 0.0 ? 0.0 : $stdev / $mean,
        'p95_ms' => $count >= 20 ? $samples[$rank - 1] : null,
        'samples_ms' => $samples,
    ];
}

/** @param array<string,int> $counts */
function representation_counts(Value $value, array &$counts): void
{
    $value->force();
    if ($value->isList) $counts['lists']++;
    elseif ($value->shape !== null) $counts['shaped_records']++;
    elseif ($value->size() > 0) $counts['fallback_records']++;
    if ($value->storage !== null) {
        foreach ($value->storage as $child) representation_counts($child, $counts);
    } else {
        foreach ($value->children as $child) representation_counts($child, $counts);
    }
}

/** @return array{scenario_prepare_ms:float,run_ms:float,materialize_ms:float,end_to_end_ms:float,prepared_ms:float,rows:mixed} */
function run_and_materialize(
    \Sel\Program $program,
    Value $context,
    string $contextMode,
): array {
    $scenarioStart = clock_ns();
    $prepareStart = clock_ns();
    $runContext = $contextMode === 'fresh-clone' ? $context->copy() : $context;
    $scenarioPrepareMs = elapsed_ms($prepareStart);
    $runStart = clock_ns();
    $actual = $program->run($runContext);
    $runMs = elapsed_ms($runStart);
    $materializeStart = clock_ns();
    $rows = benchmark_value($actual);
    $materializeMs = elapsed_ms($materializeStart);
    $endToEndMs = elapsed_ms($scenarioStart);
    return [
        'scenario_prepare_ms' => $scenarioPrepareMs,
        'run_ms' => $runMs,
        'materialize_ms' => $materializeMs,
        'end_to_end_ms' => $endToEndMs,
        'prepared_ms' => $endToEndMs,
        'rows' => $rows,
    ];
}

function corrected_main(): int
{
    $opts = options();
    $datasetFile = $opts['dataset'] ?? 'tools/scale-test/dataset-10x.json';
    $referenceFile = $opts['reference'] ?? 'tools/scale-test/benchmark_results.json';
    $runs = isset($opts['runs']) ? (int) $opts['runs'] : 1;
    $warmups = isset($opts['warmups']) ? (int) $opts['warmups'] : 0;
    $timingMode = $opts['timing-mode'] ?? 'steady-state';
    $contextMode = $opts['context-mode'] ?? 'shared';
    if ($runs < 1 || $warmups < 0) throw new RuntimeException('--runs must be positive and --warmups non-negative');
    if (!in_array($timingMode, ['steady-state', 'gc-controlled'], true)) throw new RuntimeException('unsupported timing mode');
    if (!in_array($contextMode, ['shared', 'fresh-clone'], true)) throw new RuntimeException('unsupported context mode');
    $dataset = read_json($datasetFile);
    $reference = read_json($referenceFile);
    $resolvedReferenceFile = str_starts_with($referenceFile, '/')
        ? $referenceFile : ROOT . '/' . ltrim($referenceFile, '/');
    $referenceSha256 = hash_file('sha256', $resolvedReferenceFile);
    if ($referenceSha256 === false) throw new RuntimeException("cannot hash reference: {$resolvedReferenceFile}");
    $ids = array_map(static fn (array $item): string => (string) $item['id'], $reference);
    if (count($ids) !== count(array_unique($ids))) throw new RuntimeException('reference has duplicate scenario ids');
    $only = isset($opts['only']) ? array_fill_keys(explode(',', $opts['only']), true) : null;
    $selected = array_values(array_filter($reference, static fn (array $item): bool => $only === null || isset($only[$item['id']])));
    if ($selected === []) throw new RuntimeException('--only selected no scenarios');

    register_benchmark_builtins();
    $memoryBeforeContext = memory_snapshot();
    RecordShape::resetStats();
    RecordShape::enableInstrumentation(true);
    $contextStart = clock_ns();
    $context = load_context($dataset);
    $contextPrepareMs = elapsed_ms($contextStart);
    $shapeStats = RecordShape::stats();
    RecordShape::enableInstrumentation(false);
    $memoryAfterContext = memory_snapshot();
    $representation = ['shaped_records' => 0, 'fallback_records' => 0, 'lists' => 0];
    representation_counts($context, $representation);
    $contextSignature = $context->structuralHash();
    $programs = [];
    $compileMs = [];
    foreach ($selected as $expected) {
        $start = clock_ns();
        $programs[$expected['id']] = Sel::compile($expected['query']);
        $compileMs[$expected['id']] = elapsed_ms($start);
        foreach (['postgresql', 'mariadb'] as $dialect) {
            $failures = [];
            check_plan(Sql::planHybrid($programs[$expected['id']], $dialect, schema($dialect)), $expected, $dialect, $failures);
            if ($failures !== []) throw new RuntimeException("PHP SQL parity failed for {$expected['id']}: " . implode('; ', $failures));
        }
    }

    $report = [
        'schema_version' => 2,
        'implementation' => 'php',
        'scenarios' => [],
        'passed' => true,
        'metadata' => [
            'fixture' => fixture_metadata($datasetFile, $dataset),
            'reference_path' => $resolvedReferenceFile,
            'reference_sha256' => $referenceSha256,
            'reference_scenario_ids' => array_map(static fn (array $item): string => (string) $item['id'], $selected),
            'runtime' => runtime_metadata(),
            'representation' => $representation,
            'context_prepare_ms' => $contextPrepareMs,
            'memory_before_context' => $memoryBeforeContext,
            'memory_after_context' => $memoryAfterContext,
            'memory_at_report' => memory_snapshot(),
            'shape_interning' => $shapeStats,
            'materialization_strategy' => 'Value::toNative direct packed/shape/children traversal',
            'context_mode' => $contextMode,
            'scenario_order' => array_map(static fn (array $item): string => $item['id'], $selected),
            'timing_mode' => $timingMode,
            'gc_policy' => $timingMode === 'gc-controlled' ? 'gc_collect_cycles() before warmups and samples' : 'not forced',
            'runs' => $runs,
            'warmups' => $warmups,
        ],
    ];

    foreach ($selected as $expected) {
        $id = $expected['id'];
        $failures = [];
        $before = $context->structuralHash();
        $validation = run_and_materialize($programs[$id], $context, $contextMode);
        $contextUnchanged = $before === $context->structuralHash() && $before === $contextSignature;
        if (!$contextUnchanged) $failures[] = 'prepared context changed during untimed validation';
        if (!same_value($validation['rows'], $expected['in_memory_rows'])) $failures[] = 'in-memory rows differ from Lisp reference during validation';
        for ($warmup = 0; $warmup < $warmups; $warmup++) {
            if ($timingMode === 'gc-controlled') gc_collect_cycles();
            echo "[php] {$id} warmup " . ($warmup + 1) . "/{$warmups}\n";
            $result = run_and_materialize($programs[$id], $context, $contextMode);
            if (!same_value($result['rows'], $expected['in_memory_rows'])) $failures[] = 'warmup result differs from Lisp reference';
        }
        $samples = [];
        for ($run = 0; $run < $runs; $run++) {
            echo "[php] {$id} measured " . ($run + 1) . "/{$runs}\n";
            if ($timingMode === 'gc-controlled') gc_collect_cycles();
            $result = run_and_materialize($programs[$id], $context, $contextMode);
            if (!same_value($result['rows'], $expected['in_memory_rows'])) $failures[] = 'measured result differs from Lisp reference';
            if ($context->structuralHash() !== $contextSignature) $failures[] = 'measured run changed prepared context';
            $samples[] = [
                'scenario_prepare_ms' => $result['scenario_prepare_ms'],
                'program_run_ms' => $result['run_ms'],
                'materialize_ms' => $result['materialize_ms'],
                'end_to_end_ms' => $result['end_to_end_ms'],
                'prepared_total_ms' => $result['prepared_ms'],
                'elapsed_ms' => $result['prepared_ms'],
                'memory' => memory_snapshot(),
            ];
        }
        $statistics = [];
        foreach (['scenario_prepare_ms', 'program_run_ms', 'materialize_ms', 'end_to_end_ms', 'prepared_total_ms'] as $phase) {
            $statistics[$phase] = phase_stats(array_map(static fn (array $sample): float => $sample[$phase], $samples));
        }
        $passed = $failures === [];
        $report['passed'] = $report['passed'] && $passed;
        $report['scenarios'][] = [
            'id' => $id,
            'query' => $expected['query'],
            'rows' => is_array($validation['rows']) ? count($validation['rows']) : null,
            'compile_ms' => $compileMs[$id],
            'samples' => $samples,
            'statistics' => $statistics,
            'context_unchanged' => $contextUnchanged,
            'parity' => ['passed' => $passed, 'failures' => $failures],
            'passed' => $passed,
            'failures' => $failures,
        ];
        echo ($passed ? 'PASS' : 'FAIL') . " {$id}: " . ($passed ? 'in-memory + PostgreSQL SQL + MariaDB SQL' : implode('; ', $failures)) . "\n";
    }
    if (isset($opts['output']) && $opts['output'] !== '') {
        $outputPath = str_starts_with($opts['output'], '/') ? $opts['output'] : ROOT . '/' . ltrim($opts['output'], '/');
        file_put_contents($outputPath, json_encode($report, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . PHP_EOL);
    }
    echo 'PHP scale parity: ' . count(array_filter($report['scenarios'], static fn (array $s): bool => $s['passed']))
        . '/' . count($report['scenarios']) . " passed; mode={$timingMode}; runs={$runs}; warmups={$warmups}\n";
    return $report['passed'] ? 0 : 1;
}

exit(corrected_main());
