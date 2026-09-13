#!/usr/bin/env php
<?php
// PHP-specific representation and materialization microbenchmarks.
//
// This is intentionally separate from the cross-lane benchmark: it compares
// PHP implementation strategies while keeping result parity and memory
// observations visible. It does not replace the evaluator/SQL parity runner.

declare(strict_types=1);

require_once __DIR__ . '/../../php/src/bootstrap.php';

use Sel\RecordShape;
use Sel\Dec;
use Sel\Value;

const ROOT = __DIR__ . '/../..';

/** @return array<string,string> */
function memory_options(): array
{
    $out = [];
    for ($i = 1; $i < $_SERVER['argc']; $i++) {
        if (!str_starts_with($_SERVER['argv'][$i], '--')) continue;
        $out[substr($_SERVER['argv'][$i], 2)] = $_SERVER['argv'][$i + 1] ?? '';
        $i++;
    }
    return $out;
}

function memory_clock(): int
{
    return hrtime(true);
}

function memory_ms(int $started): float
{
    return (memory_clock() - $started) / 1_000_000.0;
}

/** @return array<string,int|null> */
function memory_stats(): array
{
    $rss = null;
    $hwm = null;
    $status = is_readable('/proc/self/status') ? file_get_contents('/proc/self/status') : false;
    if (is_string($status)) {
        if (preg_match('/^VmRSS:\s+(\d+)\s+kB$/m', $status, $match) === 1) {
            $rss = (int) $match[1] * 1024;
        }
        if (preg_match('/^VmHWM:\s+(\d+)\s+kB$/m', $status, $match) === 1) {
            $hwm = (int) $match[1] * 1024;
        }
    }
    return [
        'usage_bytes' => memory_get_usage(false),
        'allocated_bytes' => memory_get_usage(true),
        'peak_usage_bytes' => memory_get_peak_usage(false),
        'peak_allocated_bytes' => memory_get_peak_usage(true),
        'rss_bytes' => $rss,
        'peak_rss_bytes' => $hwm,
    ];
}

final class DecimalProbeObject
{
    public function __construct(
        public readonly bool $neg,
        public readonly string $digits,
        public readonly int $scale,
    ) {
    }
}

/** @param array{neg:bool,digits:string,scale:int} $d */
function decimal_probe_pack(array $d, string $layout): mixed
{
    return match ($layout) {
        'tuple' => [$d['neg'], $d['digits'], $d['scale']],
        'object' => new DecimalProbeObject($d['neg'], $d['digits'], $d['scale']),
        default => $d,
    };
}

/** @return array{neg:bool,digits:string,scale:int} */
function decimal_probe_unpack(mixed $value, string $layout): array
{
    return match ($layout) {
        'tuple' => ['neg' => $value[0], 'digits' => $value[1], 'scale' => $value[2]],
        'object' => ['neg' => $value->neg, 'digits' => $value->digits, 'scale' => $value->scale],
        default => $value,
    };
}

/** @param array{neg:bool,digits:string,scale:int} $d */
function decimal_probe_format(array $d): string
{
    $sign = $d['neg'] ? '-' : '';
    if ($d['scale'] === 0) return $sign . $d['digits'];
    $padded = strlen($d['digits']) <= $d['scale']
        ? str_repeat('0', $d['scale'] - strlen($d['digits']) + 1) . $d['digits']
        : $d['digits'];
    $cut = strlen($padded) - $d['scale'];
    return $sign . substr($padded, 0, $cut) . '.' . substr($padded, $cut);
}

/** @param list<string> $texts */
function decimal_probe_parse(array $texts, string $layout): int
{
    $checksum = 0;
    foreach ($texts as $text) {
        $parsed = Dec::parse($text);
        if ($parsed === null) throw new RuntimeException("decimal probe parse failed: {$text}");
        $value = decimal_probe_pack($parsed, $layout);
        $checksum += strlen($layout === 'object' ? $value->digits : ($layout === 'tuple' ? $value[1] : $value['digits']));
    }
    return $checksum;
}

/** @param list<mixed> $values */
function decimal_probe_work(array $values, string $layout, string $operation): int
{
    $checksum = 0;
    $count = count($values);
    for ($i = 0; $i < 2048; $i++) {
        $left = decimal_probe_unpack($values[$i % $count], $layout);
        // Use the non-zero tail for the division probe; the first two inputs
        // intentionally cover positive and negative zero.
        $right = decimal_probe_unpack($values[($i % 6) + 2], $layout);
        if ($operation === 'format') {
            $text = $layout === 'current' ? Dec::format($left) : decimal_probe_format($left);
            $checksum += strlen($text);
            continue;
        }
        if ($operation === 'copy') {
            $copy = decimal_probe_pack($left, $layout === 'current' ? 'current' : $layout);
            $checksum += strlen(decimal_probe_unpack($copy, $layout)['digits']);
            continue;
        }
        $result = match ($operation) {
            'cmp' => Dec::cmp($left, $right),
            'add' => Dec::add($left, $right),
            'mul' => Dec::mul($left, $right),
            // All right-hand probe values are non-zero. The tuple/object
            // candidates deliberately delegate arithmetic to the exact current
            // digit-string core; this is a representation screen, not a claim
            // that an unimplemented alternative arithmetic core is faster.
            'div' => Dec::div($left, $right),
            default => throw new RuntimeException("unknown decimal operation: {$operation}"),
        };
        $checksum += is_int($result) ? $result : strlen(Dec::format($result));
    }
    return $checksum;
}

/** @return array<string,mixed> */
function decimal_layout_probe(int $runs, int $warmups): array
{
    $texts = [
        '0', '-0', '1.50', '-123456789.25', '9223372036854775807',
        '-9223372036854775808', '0.0000000001', '99999999999999999999.9999',
    ];
    $base = [];
    foreach ($texts as $text) {
        $parsed = Dec::parse($text);
        if ($parsed === null) throw new RuntimeException("decimal probe parse failed: {$text}");
        $base[] = $parsed;
    }
    $values = [
        'current' => $base,
        'tuple' => array_map(static fn (array $d): mixed => decimal_probe_pack($d, 'tuple'), $base),
        'object' => array_map(static fn (array $d): mixed => decimal_probe_pack($d, 'object'), $base),
    ];
    $operations = ['parse', 'cmp', 'add', 'mul', 'div', 'format', 'copy'];
    $report = [
        'values' => count($texts),
        'boundary_inputs' => $texts,
        'arithmetic_note' => 'tuple/object arithmetic delegates to Dec exact digit-string core after unpacking',
        'layouts' => [],
    ];
    foreach (array_keys($values) as $layout) {
        $layoutReport = [];
        foreach ($operations as $operation) {
            if ($operation === 'parse') {
                $layoutReport[$operation] = measure_memory_work(
                    static fn (): int => decimal_probe_parse($texts, $layout), $runs, $warmups,
                );
                continue;
            }
            $layoutReport[$operation] = measure_memory_work(
                static fn (): int =>
                    decimal_probe_work($values[$layout], $layout, $operation),
                $runs,
                $warmups,
            );
        }
        $report['layouts'][$layout] = $layoutReport;
    }
    return $report;
}

/** @param list<float> $samples @return array<string,mixed> */
function memory_phase_stats(array $samples): array
{
    sort($samples, SORT_NUMERIC);
    $count = count($samples);
    $mean = array_sum($samples) / $count;
    $middle = intdiv($count, 2);
    $median = $count % 2 === 1
        ? $samples[$middle]
        : ($samples[$middle - 1] + $samples[$middle]) / 2.0;
    $variance = 0.0;
    foreach ($samples as $sample) $variance += ($sample - $mean) ** 2;
    $stdev = $count > 1 ? sqrt($variance / ($count - 1)) : 0.0;
    return [
        'count' => $count,
        'mean_ms' => $mean,
        'median_ms' => $median,
        'min_ms' => $samples[0],
        'max_ms' => $samples[$count - 1],
        'stdev_ms' => $stdev,
        'cv' => $mean == 0.0 ? 0.0 : $stdev / $mean,
        'samples_ms' => $samples,
    ];
}

/** @param callable():mixed $work @return array<string,mixed> */
function measure_memory_work(callable $work, int $runs, int $warmups): array
{
    for ($i = 0; $i < $warmups; $i++) {
        gc_collect_cycles();
        $value = $work();
        unset($value);
    }
    $samples = [];
    $checksums = [];
    for ($i = 0; $i < $runs; $i++) {
        gc_collect_cycles();
        $started = memory_clock();
        $value = $work();
        $samples[] = memory_ms($started);
        // The work closures return a small scalar derived from the result, so
        // the construction cannot be treated as dead by the engine.
        $checksums[] = is_int($value) || is_string($value) ? $value : null;
        unset($value);
    }
    return [
        'statistics' => memory_phase_stats($samples),
        'checksums' => $checksums,
    ];
}

/** @param mixed $value */
function legacy_materialize($value): mixed
{
    $value->force();
    if ($value->size() === 0) {
        if ($value->isList) return [];
        if ($value->isText() || $value->isBin() || $value->isBool()) return $value->scalar;
        return null;
    }
    if ($value->isList) {
        $out = [];
        foreach ($value->values() as $child) $out[] = legacy_materialize($child);
        return $out;
    }
    $out = [];
    foreach ($value->entries() as [$key, $child]) $out[$key] = legacy_materialize($child);
    if (!$value->isNone()) $out['_'] = $value->scalar;
    return $out;
}

/** @param list<Value> $inputs */
function build_join_fill(array $inputs, int $width, int $rows): int
{
    $checksum = 0;
    for ($row = 0; $row < $rows; $row++) {
        $storage = array_fill(0, $width, null);
        for ($i = 0; $i < $width; $i++) {
            $storage[$i] = $inputs[$i % count($inputs)];
            $checksum += $storage[$i]->scalar === null ? 0 : strlen((string) $storage[$i]->scalar);
        }
    }
    return $checksum;
}

/** @param list<Value> $inputs */
function build_join_append(array $inputs, int $width, int $rows): int
{
    $checksum = 0;
    for ($row = 0; $row < $rows; $row++) {
        $storage = [];
        for ($i = 0; $i < $width; $i++) {
            $storage[] = $inputs[$i % count($inputs)];
            $checksum += $storage[$i]->scalar === null ? 0 : strlen((string) $storage[$i]->scalar);
        }
    }
    return $checksum;
}

/** @param array<string,mixed> $dataset */
function main_memory(array $dataset, array $opts): int
{
    $table = $opts['table'] ?? 'order_items';
    if (!isset($dataset[$table]) || !is_array($dataset[$table])) {
        throw new RuntimeException("dataset table is not a row list: {$table}");
    }
    $rows = $dataset[$table];
    $limit = isset($opts['rows']) ? max(1, (int) $opts['rows']) : count($rows);
    if ($limit < count($rows)) $rows = array_slice($rows, 0, $limit);
    $runs = max(1, (int) ($opts['runs'] ?? 5));
    $warmups = max(0, (int) ($opts['warmups'] ?? 1));

    RecordShape::resetStats();
    RecordShape::enableInstrumentation(true);
    $preparedStarted = memory_clock();
    $prepared = Value::fromNativeRows($rows);
    $preparedMs = memory_ms($preparedStarted);
    $preparedShapeStats = RecordShape::stats();
    RecordShape::enableInstrumentation(false);

    $genericStarted = memory_clock();
    $generic = Value::fromNative($rows);
    $genericMs = memory_ms($genericStarted);
    $sameRows = $prepared->structuralHash() === $generic->structuralHash();
    unset($generic);
    RecordShape::resetStats();
    RecordShape::enableInstrumentation(true);
    $genericInstrumented = Value::fromNative($rows);
    $genericShapeStats = RecordShape::stats();
    RecordShape::enableInstrumentation(false);
    unset($genericInstrumented);

    $ingestion = [
        'rows' => count($rows),
        'prepared_once_ms' => $preparedMs,
        'generic_once_ms' => $genericMs,
        'prepared_shape_stats' => $preparedShapeStats,
        'generic_shape_stats' => $genericShapeStats,
        'same_structural_rows' => $sameRows,
        'prepared_representation' => [
            'shape' => $prepared->storage[0]->shape !== null,
            'storage_packed' => $prepared->storage !== null && array_is_list($prepared->storage),
            'first_row_shape_reused' => count($prepared->storage ?? []) < 2
                || $prepared->storage[0]->shape === $prepared->storage[1]->shape,
        ],
    ];

    $materializationSamples = [];
    $legacySamples = [];
    $legacyMemory = [];
    $directMemory = [];
    for ($i = 0; $i < $warmups; $i++) {
        legacy_materialize($prepared);
        $prepared->toNative();
    }
    for ($i = 0; $i < $runs; $i++) {
        gc_collect_cycles();
        $started = memory_clock();
        $legacy = legacy_materialize($prepared);
        $legacySamples[] = memory_ms($started);
        $legacyMemory[] = memory_stats();
        unset($legacy);
        gc_collect_cycles();
        $started = memory_clock();
        $native = $prepared->toNative();
        $materializationSamples[] = memory_ms($started);
        $directMemory[] = memory_stats();
        unset($native);
    }
    $checkValue = Value::list(array_slice($prepared->storage ?? [], 0, min(100, count($rows))));
    $legacyCheck = legacy_materialize($checkValue);
    $directCheck = $checkValue->toNative();
    $sameMaterialized = $legacyCheck === $directCheck;
    unset($legacyCheck, $directCheck, $checkValue);
    $materialization = [
        'legacy_entries_values' => memory_phase_stats($legacySamples),
        'direct_to_native' => memory_phase_stats($materializationSamples),
        'same_native_rows_on_check' => $sameMaterialized,
        'check_rows' => min(100, count($rows)),
        'legacy_memory_samples' => $legacyMemory,
        'direct_memory_samples' => $directMemory,
        'strategy' => 'direct traversal of storage/shape/children',
    ];

    $inputs = [];
    for ($i = 0; $i < 16; $i++) $inputs[] = Value::text((string) $i);
    $joinRows = max(1000, min(100000, count($rows)));
    $width = 12;
    $joinFill = measure_memory_work(
        static fn (): int => build_join_fill($inputs, $width, $joinRows), $runs, $warmups,
    );
    $joinAppend = measure_memory_work(
        static fn (): int => build_join_append($inputs, $width, $joinRows), $runs, $warmups,
    );
    $join = [
        'rows' => $joinRows,
        'width' => $width,
        'array_fill_indexed' => $joinFill,
        'sequential_append' => $joinAppend,
        'selected' => 'sequential_append',
    ];

    $runtime = [
        'php' => PHP_VERSION,
        'sapi' => PHP_SAPI,
        'memory_limit' => ini_get('memory_limit'),
        'opcache_enable_cli' => ini_get('opcache.enable_cli'),
        'opcache_jit_buffer_size' => ini_get('opcache.jit_buffer_size'),
        'opcache_jit' => ini_get('opcache.jit'),
    ];
    $report = [
        'schema_version' => 1,
        'implementation' => 'php',
        'dataset' => [
            'table' => $table,
            'rows' => count($rows),
            'path' => $opts['dataset'] ?? 'tools/scale-test/dataset-10x.json',
        ],
        'configuration' => ['runs' => $runs, 'warmups' => $warmups],
        'runtime' => $runtime,
        'ingestion' => $ingestion,
        'materialization' => $materialization,
        'join_destination' => $join,
        'decimal_layouts' => decimal_layout_probe($runs, $warmups),
        'memory_after' => memory_stats(),
        'passed' => $sameRows && $sameMaterialized
            && $joinFill['checksums'] === $joinAppend['checksums'],
    ];
    echo json_encode($report, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR) . PHP_EOL;
    return $report['passed'] ? 0 : 1;
}

$opts = memory_options();
$datasetPath = $opts['dataset'] ?? 'tools/scale-test/dataset-10x.json';
$resolved = str_starts_with($datasetPath, '/') ? $datasetPath : ROOT . '/' . ltrim($datasetPath, '/');
$dataset = json_decode((string) file_get_contents($resolved), true,
    512, JSON_THROW_ON_ERROR | JSON_BIGINT_AS_STRING);
if (!is_array($dataset)) throw new RuntimeException('dataset must be a JSON object');
exit(main_memory($dataset, $opts));
