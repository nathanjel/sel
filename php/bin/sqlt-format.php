<?php
// The .sqlt case-file format, shared by php/bin/sqlt (which runs the cases) and
// php/bin/sqldoc (which checks that the design document quotes them correctly).
//
// One parser, because a second one is a second thing to drift -- and drifting
// documentation is the defect this file exists to help catch.

declare(strict_types=1);

const SUITE = __DIR__ . '/../../sql/cases';

const SECTIONS = ['dialect', 'register', 'bindings', 'options', 'as', 'mode',
                  'source', 'expect', 'params', 'error', 'throws'];

/** @return list<array<string,mixed>> */
function parse_sqlt(string $text, string $file): array
{
    $cases = [];
    $cur = -1;
    $section = null;
    foreach (explode("\n", $text) as $idx => $line) {
        $at = $file . ':' . ($idx + 1);
        if (str_starts_with($line, '### ')) {
            if (preg_match('/^###\s+name:\s*(\S+)\s*$/', $line, $m) !== 1) {
                throw new RuntimeException("{$at}: malformed case header");
            }
            $cases[] = ['name' => $m[1], 'at' => $at]
                + array_fill_keys(SECTIONS, null);
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
                throw new RuntimeException("{$at}: section outside a case");
            }
            $section = trim(substr($line, 4));
            if (in_array($section, SECTIONS, true)) {
                $cases[$cur][$section] = [];
            } elseif ($section !== 'note') {
                throw new RuntimeException("{$at}: unknown section {$section}");
            }
            continue;
        }
        if ($cur < 0 || $section === null || $section === 'note') {
            continue;                              // header text, ignored
        }
        $cases[$cur][$section][] = $line;
    }

    foreach ($cases as &$c) {
        foreach (SECTIONS as $s) {
            if ($c[$s] !== null) {
                $c[$s] = trim(implode("\n", $c[$s]));
            }
        }
        if ($c['source'] === null) {
            throw new RuntimeException("{$c['at']}: case {$c['name']} has no --- source");
        }
        $outcomes = array_filter([$c['expect'], $c['error'], $c['throws']],
            static fn ($x): bool => $x !== null);
        if (count($outcomes) !== 1) {
            throw new RuntimeException("{$c['at']}: case {$c['name']} needs exactly "
                . 'one of --- expect, --- error and --- throws');
        }
    }
    return $cases;
}

/** @return array<string,mixed> */
function decode_json(?string $text, string $what, string $at): array
{
    if ($text === null || $text === '') {
        return [];
    }
    $v = json_decode($text, true);
    if (!is_array($v)) {
        throw new RuntimeException("{$at}: --- {$what} is not a JSON object");
    }
    return $v;
}

