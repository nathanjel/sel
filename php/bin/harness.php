<?php
// What the PHP runners share: the CLI's rendering of a value, the batch-corpus
// reader and the `.selt` reader. One copy each, so bin/sel and
// tools/run-batch.php --show cannot print a value differently, bin/sqlfuzz and
// tools/run-batch.php cannot cut a corpus differently, and bin/conformance and
// tools/check-eval-equivalence.php cannot read a case differently.
//
// Not part of the library (php/src); shipped beside bin/sel, which needs show().
// Namespaced so that a runner's own helper names cannot collide with these.

declare(strict_types=1);

namespace SelBin;

use Sel\Value;

/**
 * How `sel` prints a result (docs/usage/repl.md): a scalar text bare, a boolean
 * as TRUE or FALSE, a binary as `bin:<hex>`, anything else as its dump.
 */
function show(Value $v): string
{
    if ($v->size() === 0) {
        if ($v->kind === Value::TEXT) {
            return (string) $v->scalar;
        }
        if ($v->kind === Value::BOOL) {
            return $v->scalar ? 'TRUE' : 'FALSE';
        }
        if ($v->kind === Value::BIN) {
            return 'bin:' . bin2hex((string) $v->scalar);
        }
    }
    return $v->dump();
}

/**
 * A runner's input file, as bytes. A path that cannot be read -- missing,
 * unreadable, a directory -- ends the run with `cannot read <path>` on stderr
 * and exit status 1 (tools/README.md), never a PHP warning and an empty input.
 */
function read_file_or_exit(string $path): string
{
    // is_file first: file_get_contents on a directory warns and returns '' on
    // some builds, and a warning is not a refusal.
    $text = is_file($path) && is_readable($path) ? @file_get_contents($path) : false;
    if ($text === false) {
        fwrite(STDERR, "cannot read {$path}\n");
        exit(1);
    }
    return $text;
}

/**
 * The records of a batch corpus (tools/README.md, "The corpus format"): a line
 * beginning `### ` starts a record and everything up to the next such line is
 * its source. Text before the first marker belongs to no record.
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
    // error positions differ with it. A CR anywhere is program text.
    $out = [];
    foreach ($records as $lines) {
        $joined = implode("\n", $lines);
        $out[] = str_ends_with($joined, "\n") ? substr($joined, 0, -1) : $joined;
    }
    return $out;
}

// SEL's four whitespace characters. See conformance/README.md.
const WS = " \t\r\n";

/**
 * The cases of a `.selt` file (conformance/README.md): each with its name, the
 * `file:line` of its header, and its setup (or null), source and expect
 * sections trimmed of SEL whitespace only. A malformed file is an exception
 * naming the line, never a case silently dropped.
 *
 * @return list<array<string,mixed>>
 */
function parse_selt(string $text, string $file): array
{
    $cases = [];
    $cur = -1;
    $section = null;
    foreach (explode("\n", $text) as $idx => $line) {
        $at = $file . ':' . ($idx + 1);
        if (str_starts_with($line, '### ')) {
            if (preg_match('/^###\s+name:\s*(\S+)\s*$/', $line, $m) !== 1) {
                throw new \RuntimeException("{$at}: malformed case header");
            }
            $cases[] = ['name' => $m[1], 'at' => $at, 'setup' => null, 'source' => null, 'expect' => null];
            $cur = count($cases) - 1;
            $section = null;
            continue;
        }
        if ($line === '===') {
            $cur = -1;
            $section = null;
            continue;
        }
        if (str_starts_with($line, '--- ')) {
            if ($cur < 0) {
                throw new \RuntimeException("{$at}: section outside a case");
            }
            $section = trim(substr($line, 4), WS);
            if (in_array($section, ['setup', 'source', 'expect'], true)) {
                $cases[$cur][$section] = [];
            } elseif ($section !== 'note') {
                throw new \RuntimeException("{$at}: unknown section {$section}");
            }
            continue;
        }
        if ($cur < 0 || $section === null || $section === 'note') {
            continue;                              // header text, ignored
        }
        $cases[$cur][$section][] = $line;
    }

    foreach ($cases as &$c) {
        if ($c['source'] === null) {
            throw new \RuntimeException("{$c['at']}: case {$c['name']} has no --- source");
        }
        if ($c['expect'] === null) {
            throw new \RuntimeException("{$c['at']}: case {$c['name']} has no --- expect");
        }
        // WS, not trim(): PHP's default set adds NUL and a vertical tab, which
        // the other hosts' readers do not strip. Narrower than it needs to be
        // today and the same width as everybody else, which is the point.
        $c['setup'] = $c['setup'] === null ? null : trim(implode("\n", $c['setup']), WS);
        $c['source'] = trim(implode("\n", $c['source']), WS);
        $c['expect'] = trim(implode("\n", $c['expect']), WS);
    }
    return $cases;
}

/**
 * Runs a database fixture file (php/bin/sqlo and hybrid-parity.php): comment
 * lines out first, then one statement per `;`. Returns how many statements ran,
 * so the caller can refuse a fixture that held none.
 *
 * Comments come out before the split, not after: a statement preceded by a
 * comment block otherwise arrives with the comment attached, and a "skip
 * anything starting with --" rule then drops the statement with it.
 */
function exec_fixture(\PDO $pdo, string $path): int
{
    $sql = preg_replace('/^\s*--.*$/m', '', (string) file_get_contents($path)) ?? '';
    $statements = 0;
    foreach (explode(';', $sql) as $stmt) {
        if (trim($stmt) !== '') {
            $pdo->exec($stmt);
            $statements++;
        }
    }
    return $statements;
}
