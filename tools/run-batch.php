#!/usr/bin/env php
<?php
// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   php tools/run-batch.php [--show] corpus.selc

declare(strict_types=1);

require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

$args = array_slice($argv, 1);
$show = in_array('--show', $args, true);
$path = array_values(array_filter($args, fn ($a) => $a !== '--show'))[0];

/**
 * A line beginning `### ` starts a record; everything after it is source until
 * the next marker.
 *
 * @return list<string>
 */
function read_corpus(string $text): array
{
    $records = [];
    $cur = null;
    foreach (explode("\n", $text) as $line) {
        if (str_starts_with($line, '### ')) {
            $records[] = [];
            $cur = count($records) - 1;
            continue;
        }
        if ($cur !== null) {
            $records[$cur][] = $line;
        }
    }
    // Strip ONE trailing newline, the one the file's final newline contributed.
    // Not preg_replace('/\n$/'): PCRE's `$` also matches before a final newline
    // and the replace is global, so a record ending in two newlines loses both,
    // while the JS reader loses one. The source text differs, and end-of-input
    // error positions differ with it.
    return array_map(
        static fn (array $lines) => (function (string $s): string {
            return str_ends_with($s, "\n") ? substr($s, 0, -1) : $s;
        })(implode("\n", $lines)),
        $records,
    );
}

// The rendering bin/sel uses, so a documentation example can be pasted into the
// CLI and produce exactly what the documentation claims.
function render(Value $v): string
{
    if ($v->size() === 0) {
        if ($v->kind === 'TEXT') {
            return $v->scalar;
        }
        if ($v->kind === 'BOOL') {
            return $v->scalar ? 'TRUE' : 'FALSE';
        }
        if ($v->kind === 'BIN') {
            return 'bin:' . substr($v->dump(), 1);
        }
    }
    return $v->dump();
}

$lines = [];
foreach (read_corpus(file_get_contents($path)) as $src) {
    try {
        $v = Sel::compile($src)->run(Value::none());
        $lines[] = $show ? render($v) : $v->dump();
    } catch (SelError $e) {
        $lines[] = $show ? "!{$e->code}" : "!{$e->code}@{$e->line}:{$e->col}";
    } catch (\Throwable $e) {
        $lines[] = '!HOST ' . get_class($e) . ': ' . $e->getMessage();
    }
}
// One line per program is the protocol; a value containing a newline must not be
// allowed to desynchronise the comparison.
echo implode("\n", array_map(fn ($l) => str_replace("\n", '\\n', $l), $lines)), "\n";
