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
        'FILTER', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
        'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
        'LINK', 'LINK_LEFT',
    ];

    /**
     * The evaluator is the depth authority (spec §6.4): a tree that reaches the
     * cap is evaluated as written, so it is returned as written. Folding at the
     * boundary erased the E_DEPTH the evaluator raises for a chain of 201
     * additions (each of them foldable), and a rewrite that lifts a child would
     * move it; not rewriting loses nothing, because such a tree either raises or
     * keeps its deep part on a branch that is never evaluated.
     *
     * @param array<string,mixed> $ast
     */
    public static function optimize(array $ast, bool $physical = true, array $options = []): array
    {
        return self::exceedsDepth($ast, 1) ? $ast : self::optimizeTree($ast, $physical, 1, $options);
    }

    /**
     * Whether any node of the tree lies past the evaluator's depth cap, counted
     * the way the evaluator counts: the root at 1, every child one deeper, an
     * assignment's target excluded (the evaluator walks it iteratively). The walk
     * stops at the cap, so it is bounded however deep the tree is.
     *
     * @param array<string,mixed>|null $node
     */
    private static function exceedsDepth(?array $node, int $depth): bool
    {
        if ($node === null) return false;
        if ($depth > MAX_DEPTH) return true;
        $next = $depth + 1;
        foreach (['args', 'items'] as $key) {
            foreach ($node[$key] ?? [] as $item) {
                if (is_array($item) && self::exceedsDepth($item, $next)) return true;
            }
        }
        foreach (['l', 'r', 'x', 'obj', 'idx'] as $key) {
            if (isset($node[$key]) && is_array($node[$key]) && self::exceedsDepth($node[$key], $next)) return true;
        }
        if (isset($node['value']) && is_array($node['value']) && self::exceedsDepth($node['value'], $next)) return true;
        return false;
    }

    /** @param array<string,mixed> $node @return array{source:array<string,mixed>,steps:list<array<string,mixed>>} */
    public static function unwindPipeline(array $node): array
    {
        $steps = [];
        $current = $node;
        while (($current['t'] ?? null) === 'call'
            && in_array($current['name'], self::PIPELINE_OPS, true)
            && !empty($current['args'])) {
            $steps[] = $current;
            $current = $current['args'][0];
        }
        return ['source' => $current, 'steps' => array_reverse($steps)];
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
    private static function optimizeTree(array $node, bool $physical, int $depth, array $options, bool $inMath = false): array
    {
        // The evaluator/SQL normaliser owns the public depth error and its source
        // position. optimize() never descends into a tree that reaches the cap;
        // this guard keeps the walk bounded should a rewrite ever deepen one.
        if ($depth > MAX_DEPTH) {
            return $node;
        }

        if (($node['t'] ?? null) === 'call'
            && in_array($node['name'], self::PIPELINE_OPS, true)) {
            $unwound = self::unwindPipeline($node);
            $source = self::optimizeTree($unwound['source'], $physical, $depth + 1, $options, false);
            $steps = [];
            foreach ($unwound['steps'] as $step) {
                $copy = self::copyNode($step);
                $copy['args'] = [$copy['args'][0]];
                foreach (array_slice($step['args'], 1, null, true) as $index => $arg) {
                    $copy['args'][] = self::optimizeTree($arg, $physical, $depth + 1, self::stepArgOptions($step, $index, $options), false);
                }
                $steps[] = $copy;
            }
            $sourcePos = null;
            $steps = self::logicalSteps($source, $steps, $options + ['logical' => !$physical], $sourcePos);
            if ($sourcePos !== null) {
                $source['pos'] = $sourcePos;
            }
            if ($physical) {
                // A tree fact the evaluator's join pre-filter needs (SEL-0050):
                // whether anything can see the keys a FILTER's result carries.
                // A following step that renumbers without reading `_K` hides
                // them (keysRenumberedBy, the same notion the logical rewrites
                // use); the end of the pipeline or another FILTER does not.
                foreach ($steps as $index => $step) {
                    if ($step['name'] === 'FILTER') {
                        $last = count($step['args']) - 1;
                        $steps[$index]['args'][$last]['keysUnobserved'] = self::keysRenumberedBy($steps[$index + 1] ?? null);
                    }
                }
            }
            return self::buildPipeline($source, $steps);
        }

        $isCurrMath = MathPlan::isMathOp($node);
        $nextInMath = $isCurrMath;

        $copy = self::copyNode($node);
        if (isset($copy['args'])) {
            $copy['args'] = array_map(
                static fn (array $item): array => self::optimizeTree($item, $physical, $depth + 1, $options, $nextInMath),
                $copy['args'],
            );
        }
        if (isset($copy['items'])) {
            $copy['items'] = array_map(
                static fn (array $item): array => self::optimizeTree($item, $physical, $depth + 1, $options, false),
                $copy['items'],
            );
        }
        foreach (['l', 'r', 'x'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) {
                $copy[$key] = self::optimizeTree($copy[$key], $physical, $depth + 1, $options, $nextInMath);
            }
        }
        foreach (['obj', 'idx'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) {
                $copy[$key] = self::optimizeTree($copy[$key], $physical, $depth + 1, $options, false);
            }
        }
        // An assignment's target (only assign nodes have one) is walked
        // iteratively by the evaluator, so it is not charged here.
        if (isset($copy['value']) && is_array($copy['value'])) {
            $copy['value'] = self::optimizeTree($copy['value'], $physical, $depth + 1, $options, false);
        }
        // Only an explicit false disables folding, as in JS and Python.
        $folded = (($options['foldConstants'] ?? true) === false) ? $copy : self::foldNode($copy);
        if ($physical && !$inMath && MathPlan::isMathOp($folded)) {
            $plan = MathPlan::compile($folded);
            if ($plan !== null) {
                $folded['mathPlan'] = $plan;
            }
        }
        return $folded;
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

    // A fold that replaces a node by one of its children must not move the error
    // position an operator over the result reports: spec §6.3 names the node that
    // actually failed, and to the operator the operand IS the folded node, not the
    // literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
    // So a hoisted child is re-stamped with the folded node's position -- which is
    // only exact for a leaf literal, the one shape that carries no positions of
    // its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
    // at its own column), so it is not a literal here.
    private const LITERAL_TYPES = ['num', 'text', 'bool', 'null'];

    /** @param array<string,mixed>|null $node */
    private static function isLiteral(?array $node): bool
    {
        return $node !== null && in_array($node['t'] ?? null, self::LITERAL_TYPES, true);
    }

    /** @param array<string,mixed> $child @param array<string,mixed> $pos @return array<string,mixed> */
    private static function hoistLiteral(array $child, array $pos): array
    {
        $child['pos'] = $pos;
        return $child;
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
            if (($node['op'] ?? null) === 'NEG' && ($child['t'] ?? null) === 'num') {
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
                if (($left['t'] ?? null) === 'bool' && !$left['v']) return self::boolNode(false, $node['pos']);
                if (($left['t'] ?? null) === 'bool' && ($right['t'] ?? null) === 'bool') {
                    return self::boolNode((bool) $left['v'] && (bool) $right['v'], $node['pos']);
                }
                return $node;
            }
            if ($op === 'OR') {
                if (($left['t'] ?? null) === 'bool' && $left['v']) return self::boolNode(true, $node['pos']);
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

            // Two text literals joined by `&` are one text literal (PHP-P29): the
            // fold cannot fail, since the result is shorter than the cap unless the
            // source itself was enormous, and then the node is left for the evaluator.
            if ($op === '&' && ($left['t'] ?? null) === 'text' && ($right['t'] ?? null) === 'text'
                && strlen((string) $left['v']) + strlen((string) $right['v']) <= \Sel\Limits::MAX_TEXT_LEN) {
                return ['t' => 'text', 'v' => (string) $left['v'] . (string) $right['v'], 'pos' => $node['pos']];
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
            $branch = $node['args'][0]['v'] ? $node['args'][1] : $node['args'][2];
            return self::isLiteral($branch) ? self::hoistLiteral($branch, $node['pos']) : $node;
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

    /**
     * The evaluator resolves the three-argument SORT_BY / TOP_BY form by shape
     * (spec §7.3): a text literal in the third slot is the direction, otherwise
     * a bare name in the second slot is the binder and the third slot is its
     * key. A fold that hoists a text literal into that slot -- `IF(TRUE,
     * "DESC", "ASC")` -- would change the form, so the slot is walked without
     * folding.
     *
     * @param array<string,mixed> $step
     * @param array<string,mixed> $options
     * @return array<string,mixed>
     */
    private static function stepArgOptions(array $step, int $index, array $options): array
    {
        $name = $step['name'] ?? '';
        $sortCount = $name === 'SORT_BY' ? count($step['args'])
            : ($name === 'TOP_BY' ? count($step['args']) - 1 : 0);
        if ($sortCount === 3 && $index === 2 && ($step['args'][1]['t'] ?? null) === 'var'
            && !($step['args'][1]['grouped'] ?? false)) {
            $options['foldConstants'] = false;
        }
        return $options;
    }

    /**
     * @param array<string,mixed> $source
     * @param list<array<string,mixed>> $steps
     * @param array<string,mixed> $options
     * @return list<array<string,mixed>>
     */
    private static function logicalSteps(array $source, array $steps, array $options, ?array &$sourcePos = null): array
    {
        $logical = (bool) ($options['logical'] ?? false);
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
                        $merged['pos'] = $second['pos'];   // the node that produces the value fails, not its first stage
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
                        $merged['pos'] = $second['pos'];
                        $merged['args'] = [$first['args'][0], self::numNode((string) ($left + $right), $second['args'][1]['pos'])];
                        $next[] = $merged;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && $secondName === 'TAKE'
                    && in_array($firstName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && count($second['args']) === 2
                    // Fused only for a numeric literal count of at least 1 (SPEC
                    // 6.2): the fused TOP evaluates the count -- and validates it --
                    // before the keys, and TOP(..., 0) skips them, so any other
                    // count, or a 0, changes which error is raised, or whether one is.
                    && (self::numericLiteral($second['args'][1]) ?? 0) >= 1) {
                    $topName = $firstName === 'SORT' ? 'TOP'
                        : ($firstName === 'SORT_DESC' ? 'TOP_DESC' : 'TOP_BY');
                    $merged = self::copyNode($first);
                    $merged['pos'] = $second['pos'];
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
                    if ($details['valid'] && $refs !== [] && self::allIn($refs, $passes)
                        && !self::readsRowOrKey($details['predicate'], $details['binder'])
                        && self::keysRenumberedBy($steps[$i + 2] ?? null)
                        && self::mapCannotRaise($first, $logical)) {
                        [$first['pos'], $second['pos']] = [$second['pos'], $first['pos']];
                        $next[] = $second;
                        $next[] = $first;
                        $i++;
                        $changed = true;
                        continue;
                    }
                }
                if ($second !== null && in_array($firstName, ['SORT', 'SORT_DESC', 'SORT_BY'], true)
                    && $secondName === 'FILTER' && !self::stepReadsKey($second)
                    && self::keysRenumberedBy($steps[$i + 2] ?? null)
                    && self::cannotRaise(self::sortDetails($first)['key'], self::sortDetails($first)['binder'] ?? '_', $logical)
                    && ($logical || self::predicateCannotRaise(self::filterDetails($second)['predicate'], self::filterDetails($second)['binder'], false))) {
                    [$first['pos'], $second['pos']] = [$second['pos'], $first['pos']];
                    $next[] = $second;
                    $next[] = $first;
                    $i++;
                    $changed = true;
                    continue;
                }
                if ($second !== null && $firstName === 'SELECT_COLS' && $secondName === 'FILTER') {
                    $details = self::filterDetails($second);
                    $refs = self::fieldRefs($details['predicate'], $details['binder']);
                    if ($details['valid'] && $refs !== [] && self::allIn($refs, self::selectFields($first))
                        && !self::readsRowOrKey($details['predicate'], $details['binder'])
                        && self::keysRenumberedBy($steps[$i + 2] ?? null)) {
                        [$first['pos'], $second['pos']] = [$second['pos'], $first['pos']];
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
                    // Only a key over pass-through fields is the same value before
                    // the MAP: a keyless sort compares the MAP's outputs, and a key
                    // that reads the whole row or `_K` reads what the MAP changes.
                    $details = self::sortDetails($second);
                    $refs = $details['key'] === null ? [] : self::fieldRefs($details['key'], $details['binder'] ?? '_');
                    if ($details['key'] !== null && $refs !== [] && self::allIn($refs, self::mapPassthroughs($first))
                        && !self::readsRowOrKey($details['key'], $details['binder'] ?? '_')
                        && self::mapCannotRaise($first, $logical)
                        && self::cannotRaise($details['key'], $details['binder'] ?? '_', $logical)) {
                        [$first['pos'], $second['pos']] = [$second['pos'], $first['pos']];
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
                    // Fused, the second predicate runs on a row before the first
                    // has seen the rows after it: only one that cannot raise.
                    if ($left['valid'] && $right['valid'] && self::predicateCannotRaise($right['predicate'], $right['binder'], $logical)) {
                        $predicate = \Sel\Utf8::casecmp($right['binder'], $left['binder']) === 0
                            ? $right['predicate']
                            : self::renameVar($right['predicate'], $right['binder'], $left['binder']);
                        $merged = self::copyNode($first);
                        $merged['pos'] = $second['pos'];
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
                // No rule drops a sort followed by another sort: the sorts are
                // stable, so the first is the second's tie-breaker
                // (rel.sort.then-sort-keeps-the-tie-order), and a rule that
                // removed it changed the value.
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
                    && $filter['predicate']['v'] === true
                    && ($next !== [] || $i > 0 || self::sourceIsList($source))) {
                    // The value this stage produced is now the previous stage's
                    // (or the source's), and a parent that rejects it reports the
                    // position of the node that produced it: the eliminated one.
                    if ($next !== []) {
                        $next[count($next) - 1]['pos'] = $first['pos'];
                    } else {
                        $sourcePos = $first['pos'];
                    }
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
    /**
     * Whether evaluating NODE for one row can raise -- conservatively: a rewrite
     * that moves a FILTER in front of a step, runs a step on fewer rows, or fuses
     * two FILTERs changes which rows reach what, so it may only pass over
     * expressions that cannot raise on any of them (spec §7.3; review 2026-09-25
     * SEM-07/SEM-08). Literals, _K and the binder itself never raise. On the
     * logical path the rows are a bound relation's, which always carry their
     * typed columns, so a field read through the binder cannot raise either, nor
     * a comparison, AND/OR/NOT or + - * over such reads; `/` and `%`, calls and
     * anything else may. The in-memory path has no schema.
     *
     * @param array<string,mixed>|null $node
     */
    private static function cannotRaise(?array $node, string $binder, bool $logical): bool
    {
        if ($node === null) return true;
        switch ($node['t'] ?? null) {
            case 'num': case 'text': case 'bool': case 'null':
                return true;
            case 'var':
                $name = \Sel\Utf8::upper((string) $node['name']);
                return $name === '_K' || $name === \Sel\Utf8::upper($binder);
            case 'index':
                return $logical && ($node['obj']['t'] ?? null) === 'var'
                    && \Sel\Utf8::casecmp((string) $node['obj']['name'], $binder) === 0
                    && ($node['idx']['t'] ?? null) === 'text';
            case 'bin':
                return $logical && in_array($node['op'], self::SAFE_LOGICAL_OPS, true)
                    && self::cannotRaise($node['l'] ?? null, $binder, $logical)
                    && self::cannotRaise($node['r'] ?? null, $binder, $logical);
            case 'un':
                return $logical && $node['op'] === 'NOT' && self::cannotRaise($node['x'] ?? null, $binder, $logical);
            default:
                return false;
        }
    }

    /**
     * cannotRaise for an expression FILTER will take as its predicate. FILTER
     * then requires a BOOL, so a number, text, NULL, `_K` or the binder itself,
     * which cannot raise as an expression, raises E_NOT_BOOL as a predicate: a
     * fusion (or a swap) that treated a bare variable as harmless ran the second
     * predicate before the first had seen the rows after it and reported the
     * wrong error (PHP-C11). Only a BOOL literal is known to pass; the rest
     * defers to cannotRaise, whose logical-path forms are all boolean.
     *
     * @param array<string,mixed>|null $node
     */
    private static function predicateCannotRaise(?array $node, string $binder, bool $logical): bool
    {
        if ($node === null) return false;
        switch ($node['t'] ?? null) {
            case 'bool': return true;
            case 'num': case 'text': case 'null': case 'var': return false;
            default: return self::cannotRaise($node, $binder, $logical);
        }
    }

    private const SAFE_LOGICAL_OPS = ['==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
        'AND', 'OR', '+', '-', '*'];

    /** Every field a MAP computes (or its whole body) cannot raise. */
    private static function mapCannotRaise(array $step, bool $logical): bool
    {
        $details = self::mapDetails($step);
        $body = $details['body'] ?? null;
        if (($body['t'] ?? null) === 'call' && $body['name'] === 'RECORD') {
            foreach ($body['args'] as $i => $arg) {
                if ($i % 2 === 0 ? ($arg['t'] ?? null) !== 'text' : !self::cannotRaise($arg, $details['binder'], $logical)) return false;
            }
            return true;
        }
        return self::cannotRaise($body, $details['binder'], $logical);
    }

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
        if (($body['t'] ?? null) !== 'call' || $body['name'] !== 'RECORD') return [];
        $fields = [];
        for ($i = 0; $i + 1 < count($body['args']); $i += 2) {
            $key = $body['args'][$i];
            $value = $body['args'][$i + 1];
            if (($key['t'] ?? null) === 'text' && ($value['t'] ?? null) === 'index'
                && ($value['obj']['t'] ?? null) === 'var'
                && \Sel\Utf8::casecmp($value['obj']['name'], $details['binder']) === 0
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
            || $body['name'] !== 'RECORD'
            || count(self::mapPassthroughs($step)) * 2 !== count($body['args']);
    }

    /**
     * The keys under which a parser node holds children: the two list-valued
     * ones and the seven single-valued ones. The one place the optimizer's
     * walks know the node shapes; fieldRefs and readsVar each used to spell
     * the loops out (SEL-0040).
     */
    private const CHILD_LISTS = ['args', 'items'];
    private const CHILD_NODES = ['l', 'r', 'x', 'obj', 'idx', 'target', 'value'];

    /**
     * Calls $visit on every direct child of $node, lists first, in the order
     * the keys are declared above.
     *
     * @param array<string,mixed> $node
     * @param callable(?array):void $visit
     */
    private static function forEachChild(array $node, callable $visit): void
    {
        foreach (self::CHILD_LISTS as $key) {
            foreach ($node[$key] ?? [] as $child) $visit($child);
        }
        foreach (self::CHILD_NODES as $key) {
            if (isset($node[$key]) && is_array($node[$key])) $visit($node[$key]);
        }
    }

    /** @return list<string> */
    private static function fieldRefs(?array $node, string $binder = '_'): array
    {
        $result = [];
        $visit = function (?array $item) use (&$visit, &$result, $binder): void {
            if ($item === null) return;
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text'
                && in_array(\Sel\Utf8::upper($item['obj']['name']), array_map([\Sel\Utf8::class, 'upper'], [$binder, '_', '_1', '_2']), true)) {
                $result[] = (string) $item['idx']['v'];
            }
            self::forEachChild($item, $visit);
        };
        $visit($node);
        return array_values(array_unique($result));
    }

    /**
     * Whether `$node` reads one of `$names` as a variable -- other than as
     * `name["field"]`, which is a field read. Case-insensitively, like the
     * evaluator's frames.
     *
     * @param array<string,mixed>|null $node
     * @param list<string> $names
     */
    private static function readsVar(?array $node, array $names): bool
    {
        $wanted = array_map([\Sel\Utf8::class, 'upper'], $names);
        $found = false;
        $visit = function (?array $item) use (&$visit, &$found, $wanted): void {
            if ($item === null || $found) return;
            if (($item['t'] ?? null) === 'var' && in_array(\Sel\Utf8::upper($item['name']), $wanted, true)) {
                $found = true;
                return;
            }
            if (($item['t'] ?? null) === 'index' && ($item['obj']['t'] ?? null) === 'var'
                && ($item['idx']['t'] ?? null) === 'text') {
                return;
            }
            self::forEachChild($item, $visit);
        };
        $visit($node);
        return $found;
    }

    /**
     * Whether a body reads the element as a whole (its binder, or any of the
     * pipeline's implicit names) or its key `_K`. The field set says what a
     * rewrite may rely on; this says when it may not: a body that reads either
     * cannot move across a step that changes the rows' shape (MAP, SELECT_COLS)
     * or renumbers them (MAP, SELECT_COLS, the sorts).
     *
     * @param array<string,mixed>|null $node
     */
    private static function readsRowOrKey(?array $node, string $binder = '_'): bool
    {
        return self::readsVar($node, [$binder, '_', '_1', '_2', '_K']);
    }

    /**
     * Whether a step's own arguments (not its input) read `_K`: the keys a
     * sort renumbers, so such a step keeps its place relative to one.
     *
     * @param array<string,mixed> $step
     */
    private static function stepReadsKey(array $step): bool
    {
        foreach (array_slice($step['args'], 1) as $arg) {
            if (self::readsVar($arg, ['_K'])) return true;
        }
        return false;
    }

    /**
     * Whether the step after a FILTER hides where the FILTER ran. FILTER keeps its
     * input's keys (spec §7.3) and MAP, SELECT_COLS and the sorts renumber, so a
     * FILTER moved in front of one of them carries the source's keys where the
     * program as written carried the step's -- visible in the answer, and in any
     * later `_K`. Only a following step that renumbers again without reading `_K`
     * hides that; the end of the pipeline, or another FILTER, does not.
     *
     * @param array<string,mixed>|null $step
     */
    private static function keysRenumberedBy(?array $step): bool
    {
        return $step !== null && ($step['name'] ?? '') !== 'FILTER' && !self::stepReadsKey($step);
    }

    /**
     * Whether the source a pipeline starts from is a list already, so a FILTER
     * whose predicate is a constant TRUE over it is the identity. Over a scalar
     * it is not: FILTER wraps a scalar into a one-element list (spec §7.3), and
     * only a later step, a list literal or a constructor is known not to be one.
     *
     * @param array<string,mixed> $source
     */
    private static function sourceIsList(array $source): bool
    {
        return ($source['t'] ?? null) === 'list'
            || (($source['t'] ?? null) === 'call' && in_array($source['name'] ?? '', ['LIST', 'RECORD'], true));
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
            } elseif (($args[1]['t'] ?? null) === 'var' && !($args[1]['grouped'] ?? false)) {
                // The binder form, three slots or four (with a direction): the
                // other hosts read both, and PHP's missing four-slot branch made
                // its rewrites differ (review 2026-09-25 HYG-05).
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
        if (($copy['t'] ?? null) === 'var' && \Sel\Utf8::casecmp($copy['name'], $old) === 0) $copy['name'] = $new;
        foreach (['args', 'items'] as $key) {
            if (isset($copy[$key])) $copy[$key] = array_map(
                static fn (array $child): array => self::renameVar($child, $old, $new), $copy[$key]);
        }
        foreach (['l', 'r', 'x', 'obj', 'idx', 'target', 'value'] as $key) {
            if (isset($copy[$key]) && is_array($copy[$key])) $copy[$key] = self::renameVar($copy[$key], $old, $new);
        }
        return $copy;
    }
}
