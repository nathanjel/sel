#!/usr/bin/env php
<?php
// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   php tools/run-batch.php [--show] corpus.selc

declare(strict_types=1);

require_once __DIR__ . '/../php/src/bootstrap.php';
// The corpus reader and the rendering bin/sel uses (--show): one copy each,
// shared with php/bin/sel and php/bin/sqlfuzz.
require_once __DIR__ . '/../php/bin/harness.php';

use Sel\Sel;
use Sel\SelError;
use Sel\Value;

use function SelBin\read_corpus;
use function SelBin\show as render;

// The runner contract (tools/README.md): a path that cannot be read, or a
// corpus with no program in it, is a one-line refusal and a non-zero exit --
// never a stack trace, and never a successful run that compared nothing.
function refuse(int $status, string $msg): never
{
    fwrite(STDERR, "run-batch: {$msg}\n");
    exit($status);
}

$args = array_slice($argv, 1);
$show = in_array('--show', $args, true);
$paths = array_values(array_filter($args, fn ($a) => $a !== '--show'));
if (count($paths) !== 1 || str_starts_with($paths[0], '-')) {
    refuse(2, 'usage: run-batch.php [--show] <corpus>');
}
$path = $paths[0];
// is_file first: file_get_contents on a directory warns and returns '' on some
// builds, and a warning is not a refusal.
$text = is_file($path) && is_readable($path) ? @file_get_contents($path) : false;
if ($text === false) {
    refuse(2, "cannot read {$path}");
}

$programs = read_corpus($text);
if ($programs === []) {
    refuse(1, "no programs in {$path}: a corpus is `### ` records");
}
$lines = [];
foreach ($programs as $src) {
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
