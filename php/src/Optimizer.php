<?php
// Engine-independent pipeline rewrites shared by the in-memory and SQL lanes.
//
// The parser AST is an array tree. Optimizer never edits a caller-owned node;
// every rewrite copies the node it changes and recursively copies children.

declare(strict_types=1);

namespace Sel;

final class Optimizer
{
    /** @var list<string> */
    public const PIPELINE_OPS = [
        'FILTER', 'GROUP_BY', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
        'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
        'LINK', 'LINK_LEFT',
    ];

    /** @param array<string,mixed> $ast */
    public static function optimize(array $ast, bool $physical = true, array $options = []): array
    {
        return self::optimizeTree($ast, $physical, 1, $options);
    }

    /** @param array<string,mixed> $node @return array{source:array<string,mixed>,steps:list<array<string,mixed>>} */
    public static function unwindPipeline(array $node): array
    {
        $steps = [];
        $current = $node;
        while (($current['t'] ?? null) === 'call'
            && in_array($current['name'], self::PIPELINE_OPS, true)
            && !empty($current['args'])) {
            array_unshift($steps, $current);
            $current = $current['args'][0];
        }
        return ['source' => $current, 'steps' => $steps];
    }

    /** @param array<string,mixed> $source @param list<array<string,mixed>> $steps */
    public static function buildPipeline(array $source, array $steps): array
    {
        $current = $source;
        foreach ($steps as $step) {
            $next = self::copyNode($step);
            $next['args'] = array_merge([$current], array_slice($step['args'], 1));
            $current = $next;
        }
        return $current;
    }

    /** @param array<string,mixed> $node */
    private static function optimizeTree(array $node, bool $physical, int $depth, array $options): array
    {
        // Let Parser/Evaluator report the normative depth error. Stopping the
        // rewrite here avoids making the optimizer a second depth authority.
        if ($depth > MAX_DEPTH) {
            return $node;
        }

        if (($node['t'] ?? null) === 'call'
            && in_array($node['name'], self::PIPELINE_OPS, true)) {
            $unwound = self::unwindPipeline($node);
            $source = self::optimizeTree($unwound['source'], $physical, $depth + 1, $options);
            $steps = [];
            foreach ($unwound['steps'] as $step) {
                $copy = self::copyNode($step);
                $copy['args'] = [$copy['args'][0]];
                foreach (array_slice($step['args'], 1) as $arg) {
                    $copy['args'][] = self::optimizeTree($arg, $physical, $depth + 1, $options);
                }
                $steps[] = $copy;
            }
            $steps = self::logicalSteps($steps, $options);
            if ($physical) {
                while (true) {
                    $pushed = self::pushdownJoinFilters($steps);
                    $steps = $pushed['steps'];
                    if (!$pushed['changed']) break;
                    $steps = self::logicalSteps($steps, $options);
                }
                foreach ($steps as &$step) {
                    if (($step['name'] ?? '') !== 'MAP') {
                        continue;
                    }
                    $details = self::mapDetails($step);
                    if ($details === null || ($details['body']['t'] ?? null) !== 'call'
                        || ($details['body']['name'] ?? '') !== 'RECORD'
                        || count($details['body']['args']) < 4) {
                        continue;
                    }
                    $body = self::copyNode($details['body']);
                    $body['name'] = 'LAZY_RECORD';
                    $body['spec'] = Registry::lookup('LAZY_RECORD');
                    $step['args'] = $details['explicit']
                        ? [$step['args'][0], $step['args'][1], $body]
                        : [$step['args'][0], $body];
                }
                unset($step);
            }
            return self::buildPipeline($source, $steps);
        }

        $copy = self::copyNode($node);
        foreach (['args', 'items'] as $key) {
            if (isset($copy[$key])) {
                $copy[$key] = array_map(
                    static fn (array $item): array => self::optimizeTree($item, $physical, $depth + 1, $options),
                    $copy[$key],
                );
            }
        }
        foreach (['l', 'r', 'x', 'obj', 'idx'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) {
                $copy[$key] = self::optimizeTree($copy[$key], $physical, $depth + 1, $options);
            }
        }
        if (($copy['t'] ?? null) === 'assign') {
            if (isset($copy['value']) && is_array($copy['value'])) {
                $copy['value'] = self::optimizeTree($copy['value'], $physical, $depth + 1, $options);
            }
        } else {
            foreach (['target', 'value'] as $key) {
                if (isset($copy[$key]) && is_array($copy[$key])) {
                    $copy[$key] = self::optimizeTree($copy[$key], $physical, $depth + 1, $options);
                }
            }
        }
        return self::foldNode($copy);
    }

    /** @param array<string,mixed> $node */
    private static function copyNode(array $node): array
    {
        $copy = $node;
        if (isset($node['args'])) $copy['args'] = array_values($node['args']);
        if (isset($node['items'])) $copy['items'] = array_values($node['items']);
        return $copy;
    }

    /** @return array<string,mixed> */
    private static function boolNode(bool $value, array $pos): array
    {
        return ['t' => 'bool', 'v' => $value, 'pos' => $pos];
    }

    /** @return array<string,mixed> */
    private static function numNode(string $value, array $pos): array
    {
        return ['t' => 'num', 'v' => $value, 'pos' => $pos];
    }

    /** @return array<string,mixed> */
    private static function callNode(string $name, array $args, array $pos): array
    {
        return ['t' => 'call', 'name' => $name, 'spec' => Registry::lookup($name),
                'args' => $args, 'pos' => $pos];
    }

    /** @param array<string,mixed> $node @return array<string,mixed> */
    private static function foldNode(array $node): array
    {
        $type = $node['t'] ?? null;
        if ($type === 'un') {
            $child = $node['x'] ?? null;
            if (($node['op'] ?? null) === 'NOT' && ($child['t'] ?? null) === 'bool') {
                return self::boolNode(!(bool) $child['v'], $node['pos']);
            }
            if (($node['op'] ?? null) === '-' && ($child['t'] ?? null) === 'num') {
                try {
                    $value = Dec::parse((string) $child['v'], $child['pos'] ?? null);
                    if ($value !== null) {
                        return self::numNode(Dec::format(Dec::negate($value)), $node['pos']);
                    }
                } catch (\Throwable) {
                    // Leave the expression for the evaluator, which owns the
                    // normative error and source position.
                }
            }
            return $node;
        }

        if ($type === 'bin') {
            $left = $node['l'] ?? null;
            $right = $node['r'] ?? null;
            $op = $node['op'] ?? null;
            if ($op === 'AND') {
                if (($left['t'] ?? null) === 'bool' && !$left['v']) return $left;
                if (($left['t'] ?? null) === 'bool' && ($right['t'] ?? null) === 'bool') {
                    return self::boolNode((bool) $left['v'] && (bool) $right['v'], $node['pos']);
                }
                return $node;
            }
            if ($op === 'OR') {
                if (($left['t'] ?? null) === 'bool' && $left['v']) return $left;
                if (($left['t'] ?? null) === 'bool' && ($right['t'] ?? null) === 'bool') {
                    return self::boolNode((bool) $left['v'] || (bool) $right['v'], $node['pos']);
                }
                return $node;
            }

            if (($left['t'] ?? null) === 'num' && ($right['t'] ?? null) === 'num') {
                try {
                    $l = Dec::parse((string) $left['v'], $left['pos'] ?? null);
                    $r = Dec::parse((string) $right['v'], $right['pos'] ?? null);
                    if ($l !== null && $r !== null) {
                        if (in_array($op, ['+', '-', '*', '/', '%'], true)) {
                            $value = match ($op) {
                                '+' => Dec::add($l, $r, $node['pos']),
                                '-' => Dec::sub($l, $r, $node['pos']),
                                '*' => Dec::mul($l, $r, $node['pos']),
                                '/' => Dec::div($l, $r, $node['pos']),
                                '%' => Dec::mod($l, $r, $node['pos']),
                            };
                            return self::numNode(Dec::format($value), $node['pos']);
                        }
                        if (in_array($op, ['==', '!=', '<', '<=', '>', '>='], true)) {
                            return self::boolNode(self::compareLiteral($op, Dec::cmp($l, $r)), $node['pos']);
                        }
                    }
                } catch (\Throwable) {
                    // Keep errors lazy; the evaluator will report them when the
                    // expression is actually reached.
                }
            }

            if (($left['t'] ?? null) === 'text' && ($right['t'] ?? null) === 'text'
                && in_array($op, ['$==', '$!=', '$<', '$<=', '$>', '$>='], true)) {
                $cmp = strcmp((string) $left['v'], (string) $right['v']) <=> 0;
                return self::boolNode(self::compareLiteral(substr($op, 1), $cmp), $node['pos']);
            }
            return $node;
        }

        if ($type === 'call' && ($node['name'] ?? null) === 'IF'
            && count($node['args'] ?? []) === 3
            && (($node['args'][0]['t'] ?? null) === 'bool')) {
            return $node['args'][0]['v'] ? $node['args'][1] : $node['args'][2];
        }
        return $node;
    }

    private static function compareLiteral(string $op, int $cmp): bool
    {
        return match ($op) {
            '==' => $cmp === 0,
            '!=' => $cmp !== 0,
            '<' => $cmp < 0,
            '<=' => $cmp <= 0,
            '>' => $cmp > 0,
            '>=' => $cmp >= 0,
            default => false,
        };
    }

    /** @return ?int */
    private static function numericLiteral(?array $node): ?int
    {
        if ($node === null || ($node['t'] ?? null) !== 'num') return null;
        $d = Dec::parse((string) $node['v'], $node['pos'] ?? null);
        if ($d === null || $d['scale'] !== 0 || $d['neg']) return null;
        if (strlen($d['digits']) > strlen((string) PHP_INT_MAX)
            || (strlen($d['digits']) === strlen((string) PHP_INT_MAX)
                && $d['digits'] > (string) PHP_INT_MAX)) return null;
        return (int) $d['digits'];
    }

    /** @param list<array<string,mixed>> $steps @return list<array<string,mixed>> */
    private static function logicalSteps(array $steps, array $options): array
    {
        $changed = true;
        while ($changed) {
            $changed = false;
            $next = [];
            $count = count($steps);
            for ($i = 0; $i < $count; $i++) {
                $first = $steps[$i];
                $second = $steps[$i + 1] ?? null;
                $firstName = $first['name'] ?? '';
                $secondName = $second['name'] ?? '';

                if ($second !== null && $firstName === 'TAKE' && $secondName === 'TAKE'
                    && count($first['args']) === 2 && count($second['args']) === 2) {
                    $left = self::numericLiteral($first['args'][1]);
                    $right = self::numericLiteral($second['args'][1]);
                    if ($left !== null && $right !== null) {
                        $merged = self::copyNode($first);
                        $merged['args'] = [$first['args'][0], self::numNode((string) min($left, $right), $second['args'][1]['pos'])];
                        $next[] = $merged;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && $firstName === 'DROP' && $secondName === 'DROP'
                    && count($first['args']) === 2 && count($second['args']) === 2) {
                    $left = self::numericLiteral($first['args'][1]);
                    $right = self::numericLiteral($second['args'][1]);
                    if ($left !== null && $right !== null && $left <= PHP_INT_MAX - $right) {
                        $merged = self::copyNode($first);
                        $merged['args'] = [$first['args'][0], self::numNode((string) ($left + $right), $second['args'][1]['pos'])];
                        $next[] = $merged;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && $secondName === 'TAKE'
                    && in_array($firstName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && count($second['args']) === 2) {
                    $topName = $firstName === 'SORT' ? 'TOP'
                        : ($firstName === 'SORT_DESC' ? 'TOP_DESC' : 'TOP_BY');
                    $merged = self::copyNode($first);
                    $merged['name'] = $topName;
                    $merged['spec'] = Registry::lookup($topName);
                    $merged['args'] = array_merge($first['args'], [$second['args'][1]]);
                    $next[] = $merged;
                    $i++;
                    $changed = true;
                    continue;
                }
                if ($second !== null && $firstName === 'MAP' && $secondName === 'FILTER') {
                    $passes = self::mapPassthroughs($first);
                    $details = self::filterDetails($second);
                    $refs = self::fieldRefs($details['predicate'], $details['binder']);
                    if ($details['valid'] && $refs !== [] && self::allIn($refs, $passes)) {
                        $next[] = $second;
                        $next[] = $first;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && in_array($firstName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && $secondName === 'FILTER') {
                    $next[] = $second;
                    $next[] = $first;
                    $i++;
                    $changed = true;
                    continue;
                }
                if ($second !== null && $firstName === 'SELECT_COLS' && $secondName === 'FILTER') {
                    $details = self::filterDetails($second);
                    $refs = self::fieldRefs($details['predicate'], $details['binder']);
                    if ($details['valid'] && $refs !== [] && self::allIn($refs, self::selectFields($first))) {
                        $next[] = $second;
                        $next[] = $first;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && $firstName === 'MAP'
                    && in_array($secondName, ['TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && self::mapHasComputedFields($first)) {
                    $details = self::sortDetails($second);
                    $refs = $details['key'] === null ? [] : self::fieldRefs($details['key'], $details['binder'] ?? '_');
                    if ($refs === [] || self::allIn($refs, self::mapPassthroughs($first))) {
                        $next[] = $second;
                        $next[] = $first;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if (($options['fuseFilters'] ?? true) && $second !== null
                    && $firstName === 'FILTER' && $secondName === 'FILTER') {
                    $left = self::filterDetails($first);
                    $right = self::filterDetails($second);
                    if ($left['valid'] && $right['valid']) {
                        $predicate = strcasecmp($right['binder'], $left['binder']) === 0
                            ? $right['predicate']
                            : self::renameVar($right['predicate'], $right['binder'], $left['binder']);
                        $merged = self::copyNode($first);
                        $and = ['t' => 'bin', 'op' => 'AND', 'l' => $left['predicate'],
                                'r' => $predicate, 'pos' => $left['predicate']['pos']];
                        $merged['args'] = $left['explicit']
                            ? [$first['args'][0], $first['args'][1], $and]
                            : [$first['args'][0], $and];
                        $next[] = $merged;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && in_array($firstName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && in_array($secondName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)) {
                    $next[] = $second;
                    $i++;
                    $changed = true;
                    continue;
                }
                if ($second !== null && in_array($firstName, ['DISTINCT', 'DEDUPE'], true)
                    && in_array($secondName, ['DISTINCT', 'DEDUPE'], true)) {
                    $next[] = $first;
                    $i++;
                    $changed = true;
                    continue;
                }
                $filter = $firstName === 'FILTER' ? self::filterDetails($first) : null;
                if ($filter !== null && $filter['valid']
                    && ($filter['predicate']['t'] ?? null) === 'bool'
                    && $filter['predicate']['v'] === true) {
                    $changed = true;
                    continue;
                }
                $next[] = $first;
            }
            $steps = $next;
        }
        return $steps;
    }

    /** @param list<string> $values @param list<string> $allowed */
    private static function allIn(array $values, array $allowed): bool
    {
        foreach ($values as $value) if (!in_array($value, $allowed, true)) return false;
        return true;
    }

    /** @return array{binder:string,predicate:array<string,mixed>,explicit:bool,valid:bool} */
    private static function filterDetails(array $step): array
    {
        $args = $step['args'];
        $explicit = count($args) === 3 && ($args[1]['t'] ?? null) === 'var'
            && !($args[1]['grouped'] ?? false);
        return [
            'binder' => $explicit ? $args[1]['name'] : '_',
            'predicate' => $explicit ? $args[2] : ($args[1] ?? ['t' => 'bool', 'v' => false, 'pos' => $step['pos']]),
            'explicit' => $explicit,
            'valid' => count($args) === 2 || $explicit,
        ];
    }

    /** @return array{binder:string,body:?array<string,mixed>,explicit:bool}|null */
    private static function mapDetails(array $step): ?array
    {
        $args = $step['args'];
        $explicit = count($args) === 3 && ($args[1]['t'] ?? null) === 'var'
            && !($args[1]['grouped'] ?? false);
        $body = $explicit ? ($args[2] ?? null) : ($args[1] ?? null);
        return ['binder' => $explicit ? $args[1]['name'] : '_', 'body' => $body, 'explicit' => $explicit];
    }

    /** @return list<string> */
    private static function mapPassthroughs(array $step): array
    {
        $details = self::mapDetails($step);
        $body = $details['body'] ?? null;
        if (($body['t'] ?? null) !== 'call' || !in_array($body['name'], ['RECORD', 'LAZY_RECORD'], true)) return [];
        $fields = [];
        for ($i = 0; $i + 1 < count($body['args']); $i += 2) {
            $key = $body['args'][$i];
            $value = $body['args'][$i + 1];
            if (($key['t'] ?? null) === 'text' && ($value['t'] ?? null) === 'index'
                && ($value['obj']['t'] ?? null) === 'var'
                && strcasecmp($value['obj']['name'], $details['binder']) === 0
                && ($value['idx']['t'] ?? null) === 'text'
                && $value['idx']['v'] === $key['v']) {
                $fields[] = $key['v'];
            }
        }
        return $fields;
    }

    private static function mapHasComputedFields(array $step): bool
    {
        $details = self::mapDetails($step);
        $body = $details['body'] ?? null;
        return ($body['t'] ?? null) !== 'call'
            || !in_array($body['name'], ['RECORD', 'LAZY_RECORD'], true)
            || count(self::mapPassthroughs($step)) * 2 !== count($body['args']);
    }

    /** @return list<string> */
    private static function fieldRefs(?array $node, string $binder = '_'): array
    {
        $result = [];
        $visit = function (?array $item) use (&$visit, &$result, $binder): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text'
                && in_array(strtoupper($item['obj']['name']), array_map('strtoupper', [$binder, '_', '_1', '_2']), true)) {
                $result[] = (string) $item['idx']['v'];
            }
            foreach (['args', 'items'] as $key) {
                foreach ($item[$key] ?? [] as $child) $visit($child);
            }
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($item[$key]) && is_array($item[$key])) $visit($item[$key]);
            }
        };
        $visit($node);
        return array_values(array_unique($result));
    }

    /** @return list<string> */
    private static function selectFields(array $step): array
    {
        $result = [];
        foreach (array_slice($step['args'], 1) as $arg) {
            if (($arg['t'] ?? null) === 'list') {
                foreach ($arg['items'] as $item) if (($item['t'] ?? null) === 'text') $result[] = $item['v'];
            } elseif (($arg['t'] ?? null) === 'text') {
                $result[] = $arg['v'];
            }
        }
        return $result;
    }

    /** @return array{binder:?string,key:?array<string,mixed>} */
    private static function sortDetails(array $step): array
    {
        $args = $step['args'];
        $count = count($args);
        $name = $step['name'];
        $binder = '_';
        $key = null;
        if (in_array($name, ['SORT', 'SORT_DESC'], true)) {
            if ($count === 1) return ['binder' => null, 'key' => null];
            $binder = $count === 3 && ($args[1]['t'] ?? null) === 'var' ? $args[1]['name'] : '_';
            $key = $count === 3 ? $args[2] : $args[1];
        } elseif (in_array($name, ['TOP', 'TOP_DESC'], true)) {
            if ($count === 2) return ['binder' => null, 'key' => null];
            $sortCount = $count - 1;
            $binder = $sortCount === 3 && ($args[1]['t'] ?? null) === 'var' ? $args[1]['name'] : '_';
            $key = $sortCount === 3 ? $args[2] : $args[1];
        } elseif (in_array($name, ['SORT_BY', 'TOP_BY'], true)) {
            $sortCount = $name === 'TOP_BY' ? $count - 1 : $count;
            if ($sortCount === 2 || ($sortCount === 3 && ($args[2]['t'] ?? null) === 'text')) {
                $key = $args[1];
            } elseif ($sortCount === 3 && ($args[1]['t'] ?? null) === 'var') {
                $binder = $args[1]['name'];
                $key = $args[2];
            }
        }
        return ['binder' => $binder, 'key' => $key];
    }

    /** @param array<string,mixed> $node */
    private static function renameVar(array $node, string $old, string $new): array
    {
        $copy = self::copyNode($node);
        if (($copy['t'] ?? null) === 'var' && strcasecmp($copy['name'], $old) === 0) $copy['name'] = $new;
        foreach (['args', 'items'] as $key) {
            if (isset($copy[$key])) $copy[$key] = array_map(
                static fn (array $child): array => self::renameVar($child, $old, $new), $copy[$key]);
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) $copy[$key] = self::renameVar($copy[$key], $old, $new);
        }
        return $copy;
    }

    /** @param list<array<string,mixed>> $steps
     * @return array{steps:list<array<string,mixed>>,changed:bool} */
    private static function pushdownJoinFilters(array $steps): array
    {
        $out = [];
        $changed = false;
        for ($i = 0; $i < count($steps); $i++) {
            $link = $steps[$i];
            $filter = $steps[$i + 1] ?? null;
            if ($filter === null || !in_array($link['name'], ['LINK', 'LINK_LEFT'], true)
                || ($filter['name'] ?? '') !== 'FILTER') {
                $out[] = $link;
                continue;
            }
            $info = self::filterDetails($filter);
            if (!$info['valid']) {
                $out[] = $link;
                continue;
            }
            // An unqualified `_` after a join denotes the composite row and is
            // deliberately ambiguous; only the left binder may be pushed into
            // the left input.
            $leftNames = ['_1'];
            $rightNames = ['_2'];
            $leftNames = array_merge($leftNames, self::collectPipelineSourceNames($link['args'][0]));
            $rightNames = array_merge($rightNames, self::collectPipelineSourceNames($link['args'][1]));
            if (count($link['args']) === 5) {
                if (($link['args'][2]['t'] ?? null) === 'var') $leftNames[] = $link['args'][2]['name'];
                if (($link['args'][3]['t'] ?? null) === 'var') $rightNames[] = $link['args'][3]['name'];
            }
            $left = [];
            $right = [];
            $remaining = [];
            foreach (self::splitAnd($info['predicate']) as $conjunct) {
                $affinity = self::predicateAffinity($conjunct, $info['binder'], $leftNames, $rightNames);
                if ($affinity === 'left') $left[] = self::rewriteJoinRefs($conjunct, $leftNames, $info['binder']);
                elseif ($affinity === 'right' && $link['name'] === 'LINK') $right[] = self::rewriteJoinRefs($conjunct, $rightNames, $info['binder']);
                else $remaining[] = $conjunct;
            }
            if ($left === [] && $right === []) {
                $out[] = $link;
                continue;
            }
            if ($left !== []) {
                $out[] = self::callNode('FILTER', [$link['args'][0], self::combineAnd($left, $filter['pos'])], $filter['pos']);
            }
            $newLink = $link;
            if ($right !== []) {
                $newLink = self::copyNode($link);
                $newLink['args'] = $link['args'];
                $newLink['args'][1] = self::callNode('FILTER', [$link['args'][1], self::combineAnd($right, $filter['pos'])], $filter['pos']);
            }
            $out[] = $newLink;
            if ($remaining !== []) {
                $newFilter = self::copyNode($filter);
                $predicate = self::combineAnd($remaining, $filter['pos']);
                $newFilter['args'] = $info['explicit']
                    ? [$newLink, $filter['args'][1], $predicate]
                    : [$newLink, $predicate];
                $out[] = $newFilter;
            }
            $changed = true;
            $i++;
        }
        return ['steps' => $out, 'changed' => $changed];
    }

    /** @param array<string,mixed>|null $node @return list<string> */
    private static function collectPipelineSourceNames(?array $node): array
    {
        $names = [];
        $visit = function (?array $item) use (&$visit, &$names): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'var') {
                $names[] = (string) $item['name'];
                return;
            }
            if (($item['t'] ?? null) !== 'call') return;
            $name = $item['name'] ?? '';
            if (in_array($name, ['LINK', 'LINK_LEFT'], true)) {
                $visit($item['args'][0] ?? null);
                $visit($item['args'][1] ?? null);
                return;
            }
            if (in_array($name, self::PIPELINE_OPS, true)) {
                $visit($item['args'][0] ?? null);
            }
        };
        $visit($node);
        return array_values(array_unique($names, SORT_STRING));
    }

    /** @return list<array<string,mixed>> */
    private static function splitAnd(array $node): array
    {
        return ($node['t'] ?? null) === 'bin' && ($node['op'] ?? null) === 'AND'
            ? array_merge(self::splitAnd($node['l']), self::splitAnd($node['r']))
            : [$node];
    }

    /** @param list<array<string,mixed>> $nodes */
    private static function combineAnd(array $nodes, array $pos): array
    {
        $result = array_shift($nodes);
        foreach ($nodes as $node) {
            $result = ['t' => 'bin', 'op' => 'AND', 'l' => $result, 'r' => $node,
                       'pos' => $result['pos'] ?? $pos];
        }
        return $result;
    }

    /** @return 'left'|'right'|'both'|'unknown'|'ambiguous' */
    private static function predicateAffinity(array $node, string $binder, array $leftNames, array $rightNames): string
    {
        $hasLeft = false;
        $hasRight = false;
        $unknown = false;
        $ambiguous = false;
        $visit = function (?array $item) use (&$visit, &$hasLeft, &$hasRight, &$unknown, &$ambiguous, $binder, $leftNames, $rightNames): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'index'
                && ($item['obj']['t'] ?? null) === 'index'
                && ($item['obj']['obj']['t'] ?? null) === 'var'
                && ($item['obj']['idx']['t'] ?? null) === 'text'
                && ($item['idx']['t'] ?? null) === 'text'
                && in_array(strtoupper((string) $item['obj']['obj']['name']), [strtoupper($binder), '_'], true)) {
                $table = strtoupper((string) $item['obj']['idx']['v']);
                if (in_array($table, array_map('strtoupper', $leftNames), true)) $hasLeft = true;
                elseif (in_array($table, array_map('strtoupper', $rightNames), true)) $hasRight = true;
                else $unknown = true;
                return;
            }
            if (($item['t'] ?? null) === 'index'
                && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text') {
                $name = strtoupper((string) $item['obj']['name']);
                if (in_array($name, array_map('strtoupper', $leftNames), true)) $hasLeft = true;
                elseif (in_array($name, array_map('strtoupper', $rightNames), true)) $hasRight = true;
                elseif ($name === strtoupper($binder) || $name === '_') $ambiguous = true;
                else $unknown = true;
                return;
            }
            if (($item['t'] ?? null) === 'var') {
                $name = strtoupper((string) $item['name']);
                if ($name !== strtoupper($binder) && $name !== '_') {
                    if (in_array($name, array_map('strtoupper', $leftNames), true)) $hasLeft = true;
                    elseif (in_array($name, array_map('strtoupper', $rightNames), true)) $hasRight = true;
                    else $unknown = true;
                }
            }
            foreach (['args', 'items'] as $key) foreach ($item[$key] ?? [] as $child) $visit($child);
            foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
                if (isset($item[$key]) && is_array($item[$key])) $visit($item[$key]);
            }
        };
        $visit($node);
        if ($ambiguous || $unknown) return $ambiguous ? 'ambiguous' : 'unknown';
        if ($hasLeft && $hasRight) return 'both';
        if ($hasLeft) return 'left';
        if ($hasRight) return 'right';
        return 'unknown';
    }

    /** @param list<string> $names */
    private static function rewriteJoinRefs(array $node, array $names, string $binder = '_'): array
    {
        $copy = self::copyNode($node);
        if (($copy['t'] ?? null) === 'index'
            && ($copy['obj']['t'] ?? null) === 'index'
            && ($copy['obj']['obj']['t'] ?? null) === 'var'
            && ($copy['obj']['idx']['t'] ?? null) === 'text'
            && ($copy['idx']['t'] ?? null) === 'text'
            && in_array(strtoupper((string) $copy['obj']['obj']['name']), ['_', strtoupper($binder)], true)
            && in_array(strtoupper((string) $copy['obj']['idx']['v']), array_map('strtoupper', $names), true)) {
            $copy['obj'] = ['t' => 'var', 'name' => '_', 'pos' => $copy['obj']['obj']['pos']];
            return $copy;
        }
        if (($copy['t'] ?? null) === 'index' && ($copy['obj']['t'] ?? null) === 'var'
            && in_array(strtoupper($copy['obj']['name']), array_map('strtoupper', $names), true)) {
            $copy['obj'] = ['t' => 'var', 'name' => '_', 'pos' => $copy['obj']['pos']];
        }
        foreach (['args', 'items'] as $key) if (isset($copy[$key])) {
            $copy[$key] = array_map(static fn (array $item): array => self::rewriteJoinRefs($item, $names, $binder), $copy[$key]);
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) $copy[$key] = self::rewriteJoinRefs($copy[$key], $names, $binder);
        }
        return $copy;
    }
}
