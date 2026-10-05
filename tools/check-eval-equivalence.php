#!/usr/bin/env php
<?php
// Plain AST versus optimised execution over the conformance corpus: the PHP
// twin of tools/check-eval-equivalence.mjs -- see that file for the rule. Each `.selt` source (with its setup), runs on fresh contexts as
// Evaluator::evalNode($program->ast) (plain) and as $program->run() (optimiser
// and math plans), twice each on one Program; value dump, error code and
// position, and the final context dump must agree.
//
//   php -d memory_limit=-1 tools/check-eval-equivalence.php [file.selt ...]
//
// Exit status is non-zero on any difference. A gate lane of tools/check.sh
// ("PHP plain vs optimised").

declare(strict_types=1);

// The corpus includes conformance/29's operations at the size caps (16M-character
// texts, million-element lists), run four times and dumped: gigabytes of PHP values.
ini_set('memory_limit', '-1');

require_once __DIR__ . '/../php/src/bootstrap.php';

use Sel\Context;
use Sel\Evaluator;
use Sel\Sel;
use Sel\SelError;
use Sel\Value;

const WS = " \t\r\n";

/** @return list<array{0:string,1:?string,2:string}> */
function cases(string $file): array
{
    $out = [];
    $cur = -1;
    $sec = null;
    foreach (explode("\n", (string) file_get_contents($file)) as $line) {
        if (str_starts_with($line, '### ')) {
            preg_match('/name:\s*(\S+)/', $line, $m);
            $out[] = ['name' => $m[1], 'setup' => null, 'source' => []];
            $cur = count($out) - 1;
            $sec = null;
        } elseif ($line === '===') {
            $cur = -1;
            $sec = null;
        } elseif (str_starts_with($line, '--- ')) {
            $sec = trim(substr($line, 4), WS);
            if ($sec === 'setup' && $cur >= 0) {
                $out[$cur]['setup'] = [];
            }
        } elseif ($cur >= 0 && $sec === 'source') {
            $out[$cur]['source'][] = $line;
        } elseif ($cur >= 0 && $sec === 'setup') {
            $out[$cur]['setup'][] = $line;
        }
    }
    return array_map(static fn (array $c): array => [
        $c['name'],
        $c['setup'] === null ? null : trim(implode("\n", $c['setup']), WS),
        trim(implode("\n", $c['source']), WS),
    ], $out);
}

/** @return array{0:string,1:int} */
function observe(string $mode, $program, Value $root): array
{
    $ctx = new Context($root);
    try {
        $v = $mode === 'plain' ? Evaluator::evalNode($program->ast, $ctx) : $program->run($root);
        $answer = $v->dump();
    } catch (SelError $e) {
        $answer = "!{$e->code}@{$e->line}:{$e->col}";
    }
    try {
        $dump = $root->dump();
    } catch (SelError $e) {
        $dump = "!{$e->code}";
    }
    return ["{$answer} | ctx={$dump}", $mode === 'plain' ? $ctx->depth : 0];
}

/** @return array{0:array{0:string,1:int},1:array{0:string,1:int}} */
function run(?string $setup, string $source, string $mode): array
{
    $fresh = static function () use ($setup): Value {
        $root = Value::fromNative([]);
        if ($setup !== null && $setup !== '') {
            Sel::compile($setup)->run($root);
        }
        return $root;
    };
    try {
        $program = Sel::compile($source);
    } catch (SelError $e) {
        $at = ["!{$e->code}@{$e->line}:{$e->col} (compile)", 0];
        return [$at, $at];
    }
    return [observe($mode, $program, $fresh()), observe($mode, $program, $fresh())];
}

$argv0 = array_slice($argv, 1);
$root = dirname(__DIR__);
if ($argv0 === []) {
    $files = glob($root . '/conformance/*.selt');
    sort($files);
} else {
    $files = $argv0;
}

$total = 0;
$bad = [];
foreach ($files as $f) {
    foreach (cases($f) as [$name, $setup, $source]) {
        $total++;
        [[$p1, $d1], [$p2]] = run($setup, $source, 'plain');
        [[$q1], [$q2]] = run($setup, $source, 'physical');
        if ($p1 !== $p2) {
            $bad[] = [$name, 'plain run is not repeatable', "{$p1}\n   vs {$p2}"];
        } elseif ($p1 !== $q1) {
            $bad[] = [$name, 'physical differs from plain', 'plain=' . substr($p1, 0, 160) . "\n   phys =" . substr($q1, 0, 160)];
        } elseif ($q1 !== $q2) {
            $bad[] = [$name, 'physical run is not repeatable', "{$q1}\n   vs {$q2}"];
        }
        if ($d1 !== 0) {
            $bad[] = [$name, 'plain evaluation left depth counted', (string) $d1];
        }
    }
}
foreach ($bad as [$n, $why, $detail]) {
    echo "DIFF {$n}: {$why}\n   {$detail}\n";
}
echo "{$total} sources, " . count($bad) . " differ\n";
exit($bad === [] ? 0 : 1);
