<?php
// The evaluator. Ported from js/src/eval.mjs.
//
// Nothing here catches a SelError. An error surfaces from the innermost node
// that failed, carrying that node's position, and no layer rewrites it.

declare(strict_types=1);

namespace Sel;

use Sel\Builtins\Number;

/**
 * @phpstan-import-type Decimal from Dec
 */
final class Evaluator
{
    /** A compound assignment's operator, by its spelling. */
    private const COMPOUND = [
        '+=' => '+', '-=' => '-', '*=' => '*', '/=' => '/', '%=' => '%', '&=' => '&',
    ];

    /** @param array<string,mixed> $node */
    public static function evalNode(array $node, Context $ctx): Value
    {
        if (++$ctx->depth > MAX_DEPTH) {
            $ctx->depth--;
            fail('E_DEPTH', 'evaluation nested too deeply', $node['pos']);
        }
        try {
            if (isset($node['mathPlan'])) {
                return self::evalMathPlan($node['mathPlan'], $ctx);
            }
            return self::dispatch($node, $ctx);
        } finally {
            $ctx->depth--;
        }
    }

    private const DEC_NEG_ONE = ['neg' => true, 'digits' => '1', 'scale' => 0];
    private const DEC_ZERO = ['neg' => false, 'digits' => '0', 'scale' => 0];
    private const DEC_ONE = ['neg' => false, 'digits' => '1', 'scale' => 0];

    /**
     * A scratchpad slot holds either a decimal a step computed or the Value a
     * load produced. The Value is coerced here, when an operation consumes it,
     * and not when it was loaded: SPEC 6.2 evaluates every operand first and
     * only then coerces, left to right, so a later operand's evaluation error
     * must be found before an earlier operand's coercion error, and an operand
     * mutated by a later one is seen as it stands at the operation.
     *
     * @param array<string,mixed>|Value $slot
     * @param array<string,mixed>|null $pos
     * @return Decimal
     */
    private static function operand(array|Value $slot, ?array $pos): array
    {
        return $slot instanceof Value ? $slot->asDecimalLazy($pos) : $slot;
    }

    /**
     * @param array{steps:list<array<string,mixed>>,outputSlot:int,outputPos:array<string,mixed>|null,scratchpadSize:int} $plan
     */
    public static function evalMathPlan(array $plan, Context $ctx): Value
    {
        $scratchpad = [];
        foreach ($plan['steps'] as $step) {
            switch ($step['op']) {
                case MathOpCode::LOAD_VAR:
                    $val = $ctx->lookup($step['name']);
                    if ($val === null) {
                        fail('E_UNDEF_VAR', "undefined variable {$step['name']}", $step['pos']);
                    }
                    $scratchpad[$step['dst']] = $val;
                    break;
                case MathOpCode::LOAD_CONST:
                    $scratchpad[$step['dst']] = $step['constVal'];
                    break;
                case MathOpCode::LOAD_LEAF:
                    $val = self::evalNode($step['leafNode'], $ctx);
                    $scratchpad[$step['dst']] = $val;
                    break;
                case MathOpCode::ADD:
                    $scratchpad[$step['dst']] = Dec::add(self::operand($scratchpad[$step['src1']], $step['p1']), self::operand($scratchpad[$step['src2']], $step['p2']), $step['pos']);
                    break;
                case MathOpCode::SUB:
                    $scratchpad[$step['dst']] = Dec::sub(self::operand($scratchpad[$step['src1']], $step['p1']), self::operand($scratchpad[$step['src2']], $step['p2']), $step['pos']);
                    break;
                case MathOpCode::MUL:
                    $scratchpad[$step['dst']] = Dec::mul(self::operand($scratchpad[$step['src1']], $step['p1']), self::operand($scratchpad[$step['src2']], $step['p2']), $step['pos']);
                    break;
                case MathOpCode::DIV:
                    $scratchpad[$step['dst']] = Dec::div(self::operand($scratchpad[$step['src1']], $step['p1']), self::operand($scratchpad[$step['src2']], $step['p2']), $step['pos']);
                    break;
                case MathOpCode::MOD:
                    $scratchpad[$step['dst']] = Dec::mod(self::operand($scratchpad[$step['src1']], $step['p1']), self::operand($scratchpad[$step['src2']], $step['p2']), $step['pos']);
                    break;
                case MathOpCode::NEG:
                    $scratchpad[$step['dst']] = Dec::negate(self::operand($scratchpad[$step['src1']], $step['p1']));
                    break;
                case MathOpCode::ABS:
                    $scratchpad[$step['dst']] = Dec::abs(self::operand($scratchpad[$step['src1']], $step['p1']));
                    break;
                case MathOpCode::SIGN:
                    $s = Dec::sign(self::operand($scratchpad[$step['src1']], $step['p1']));
                    $scratchpad[$step['dst']] = $s < 0 ? self::DEC_NEG_ONE : ($s === 0 ? self::DEC_ZERO : self::DEC_ONE);
                    break;
                case MathOpCode::CEIL:
                    $scratchpad[$step['dst']] = Dec::ceil(self::operand($scratchpad[$step['src1']], $step['p1']));
                    break;
                case MathOpCode::FLOOR:
                    $scratchpad[$step['dst']] = Dec::floor(self::operand($scratchpad[$step['src1']], $step['p1']));
                    break;
                case MathOpCode::TRUNC:
                    $scratchpad[$step['dst']] = Dec::trunc(self::operand($scratchpad[$step['src1']], $step['p1']));
                    break;
                case MathOpCode::ROUND:
                    $d1 = self::operand($scratchpad[$step['src1']], $step['p1']);
                    $d2 = self::operand($scratchpad[$step['src2']], $step['p2']);
                    $n = Number::sizedArg($d2, 'ROUND', 2, Limits::MAX_ROUND_SCALE, 'ROUND scale', $step['auxPos']);
                    $scratchpad[$step['dst']] = Dec::round($d1, $n, $step['pos']);
                    break;
                case MathOpCode::POWER:
                    $d1 = self::operand($scratchpad[$step['src1']], $step['p1']);
                    $d2 = self::operand($scratchpad[$step['src2']], $step['p2']);
                    $n = Number::sizedArg($d2, 'POWER', 2, Limits::MAX_POWER_EXPONENT, 'POWER exponent', $step['auxPos']);
                    $scratchpad[$step['dst']] = Dec::power($d1, $n, $step['pos']);
                    break;
                case MathOpCode::MIN:
                    $a = self::operand($scratchpad[$step['src1']], $step['p1']);
                    $b = self::operand($scratchpad[$step['src2']], $step['p2']);
                    $scratchpad[$step['dst']] = Dec::cmp($b, $a) < 0 ? $b : $a;
                    break;
                case MathOpCode::MAX:
                    $a = self::operand($scratchpad[$step['src1']], $step['p1']);
                    $b = self::operand($scratchpad[$step['src2']], $step['p2']);
                    $scratchpad[$step['dst']] = Dec::cmp($b, $a) > 0 ? $b : $a;
                    break;
            }
        }
        return Value::numTrusted(self::operand($scratchpad[$plan['outputSlot']], $plan['outputPos']));
    }

    /** @param array<string,mixed> $node */
    private static function dispatch(array $node, Context $ctx): Value
    {
        switch ($node['t']) {
            case 'num':                                 // canonicalised by the parser
                $v = Value::textTrusted($node['v']);
                if (isset($node['dec'])) {
                    $v->decVal = $node['dec'];
                }
                return $v;

            case 'text':
                return Value::textTrusted($node['v']);

            case 'bool':
                return Value::bool($node['v']);

            case 'null':
                return Value::null();

            case 'var':
                $v = $ctx->lookup($node['name']);
                if ($v === null) {
                    fail('E_UNDEF_VAR', "undefined variable {$node['name']}", $node['pos']);
                }
                return $v;

            case 'index':
                $objNode = $node['obj'];
                if (($objNode['t'] ?? null) === 'var') {
                    $obj = $ctx->lookup($objNode['name']);
                    if ($obj === null) {
                        fail('E_UNDEF_VAR', "undefined variable {$objNode['name']}", $objNode['pos']);
                    }
                } else {
                    $obj = self::evalNode($objNode, $ctx);
                }

                $cache = $node['slotCache'] ?? null;
                if ($cache !== null && $obj->shape !== null && $obj->shape === $cache->shape) {
                    return $obj->storage[$cache->slot];
                }

                $isLiteral = ($node['idx']['t'] ?? null) === 'text';
                $key = $isLiteral
                    ? $node['idx']['v']
                    : self::evalNode($node['idx'], $ctx)->asText($node['idx']['pos']);

                if ($isLiteral && $obj->shape !== null) {
                    $index = $obj->shape->keyMap[$key] ?? null;
                    if ($index !== null) {
                        if ($cache !== null) {
                            $cache->shape = $obj->shape;
                            $cache->slot = $index;
                        }
                        return $obj->storage[$index];
                    }
                    fail('E_NO_KEY', 'no key ' . quote_text((string) $key), $node['pos']);
                }

                $child = $obj->get($key);
                if ($child === null) {
                    fail('E_NO_KEY', 'no key ' . quote_text((string) $key), $node['pos']);
                }
                return $child;

            case 'seq':
                $last = null;
                foreach ($node['items'] as $item) {
                    $last = self::evalNode($item, $ctx);
                }
                return $last;

            case 'list':
                return self::evalList($node, $ctx);

            case 'un':
                return self::evalUnary($node, $ctx);

            case 'bin':
                return self::evalBinary($node, $ctx);

            case 'assign':
                return self::evalAssign($node, $ctx);

            case 'call':
                $args = new Args($node, $ctx);
                if (!$node['spec']['lazy']) {
                    // Strict: every argument evaluated once, left to right.
                    for ($i = 0, $n = count($node['args']); $i < $n; $i++) {
                        $args->val($i);
                    }
                }
                return ($node['spec']['fn'])($args, $ctx);
        }
        fail('E_SYNTAX', "cannot evaluate node {$node['t']}", $node['pos']);
    }

    /**
     * §5.9 — a value with children and no scalar contributes its children's
     * values; anything else contributes itself. Keys are always renumbered from 1.
     *
     * @param array<string,mixed> $node
     */
    private static function evalList(array $node, Context $ctx): Value
    {
        $out = [];
        foreach ($node['items'] as $item) {
            $v = self::evalNode($item, $ctx);
            // `,` collects, so it copies what it collects (spec §3.4): the list
            // never shares an element with the values it was built from, and a
            // value that would end up past the depth cap is refused here, at the
            // list node, not at 0:0 when something later walks it.
            if ($v->kind === Value::NONE && $v->size() > 0) {
                // Refused before the children are copied (spec §6.4): a list that
                // flattens past the collection cap is not built.
                Budget::checkCollection(count($out) + $v->size(), $node['pos'], 'the list');
                foreach ($v->values() as $child) {
                    $out[] = $child->copyBelow(1, $node['pos']);
                }
            } else {
                $out[] = $v->copyBelow(1, $node['pos']);
            }
        }
        if (count($out) > Limits::MAX_COLLECTION) Budget::checkCollection(count($out), $node['pos'], 'the list');
        return Value::list($out);
    }

    /** @param array<string,mixed> $node */
    private static function evalUnary(array $node, Context $ctx): Value
    {
        $v = self::evalNode($node['x'], $ctx);
        if ($node['op'] === 'NOT') {
            return Value::bool(!$v->asBool($node['x']['pos']));
        }
        return Value::numTrusted(Dec::negate($v->asDecimalLazy($node['x']['pos'])));
    }

    /** @param array<string,mixed> $node */
    private static function evalBinary(array $node, Context $ctx): Value
    {
        $op = $node['op'];

        // Short-circuit before either side is touched (§5.5, §5.6).
        if ($op === 'AND' || $op === 'OR') {
            $left = self::evalNode($node['l'], $ctx)->asBool($node['l']['pos']);
            if ($op === 'AND' && !$left) {
                return Value::bool(false);
            }
            if ($op === 'OR' && $left) {
                return Value::bool(true);
            }
            return Value::bool(self::evalNode($node['r'], $ctx)->asBool($node['r']['pos']));
        }

        // ?? falls back on NULL, ??? on any vacuous value; both on a missing
        // key or name.
        if ($op === '??' || $op === '???') {
            try {
                $l = self::evalNode($node['l'], $ctx);
            } catch (SelError $e) {
                if ($e->code === 'E_NO_KEY' || $e->code === 'E_UNDEF_VAR') {
                    return self::evalNode($node['r'], $ctx);
                }
                throw $e;
            }
            if ($op === '??' ? $l->isNull() : $l->isVacuous()) {
                return self::evalNode($node['r'], $ctx);
            }
            return $l;
        }

        $l = self::evalNode($node['l'], $ctx);
        $r = self::evalNode($node['r'], $ctx);
        $lp = $node['l']['pos'];
        $rp = $node['r']['pos'];

        switch ($op) {
            // Dec::arith's table, spelled out: this runs per node.
            case '+': return Value::numTrusted(Dec::add($l->asDecimalLazy($lp), $r->asDecimalLazy($rp), $node['pos']));
            case '-': return Value::numTrusted(Dec::sub($l->asDecimalLazy($lp), $r->asDecimalLazy($rp), $node['pos']));
            case '*': return Value::numTrusted(Dec::mul($l->asDecimalLazy($lp), $r->asDecimalLazy($rp), $node['pos']));
            case '/': return Value::numTrusted(Dec::div($l->asDecimalLazy($lp), $r->asDecimalLazy($rp), $node['pos']));
            case '%': return Value::numTrusted(Dec::mod($l->asDecimalLazy($lp), $r->asDecimalLazy($rp), $node['pos']));

            case '&': return self::concat($l, $r, $lp, $rp, $node['pos']);

            case '==': case '!=': case '<': case '<=': case '>': case '>=':
                return Value::bool(self::compareResult($op, Dec::cmp($l->asDecimalLazy($lp), $r->asDecimalLazy($rp)), $node['pos']));

            case '$==': case '$!=': case '$<': case '$<=': case '$>': case '$>=':
                // strcmp is bytewise, which is exactly what §5.3 requires.
                $c = strcmp($l->asBytes($lp), $r->asBytes($rp));
                return Value::bool(self::compareResult(substr($op, 1), $c <=> 0, $node['pos']));

            case 'EQL': return Value::bool($l->eql($r, $node['pos']));
            case 'IN': return Value::bool(self::isIn($l, $r));

            case 'XOR': return Value::bool($l->asBool($lp) !== $r->asBool($rp));

            case 'BAND': case 'BOR': case 'BXOR':
                return self::bitwise($op, $l->asBytes($lp), $r->asBytes($rp), $node['pos']);
        }
        fail('E_SYNTAX', "unknown operator {$op}", $node['pos']);
    }

    /**
     * The six comparisons, and nothing else.
     *
     * The match had no default arm, so an operator it did not name raised
     * \UnhandledMatchError -- loud, which is right, but a PHP error rather than
     * a SEL one, so it carried no code and no position and could not be caught
     * where every other failure in this file is caught. The other four hosts
     * answered silently in their own ways; all five now refuse identically.
     * Unreachable today, since every caller (this file's and the optimizer's
     * constant folding) reaches it only with the six.
     *
     * @param array<string,mixed>|null $pos
     */
    public static function compareResult(string $op, int $c, ?array $pos): bool
    {
        return match ($op) {
            '==' => $c === 0,
            '!=' => $c !== 0,
            '<' => $c < 0,
            '<=' => $c <= 0,
            '>' => $c > 0,
            '>=' => $c >= 0,
            default => fail('E_SYNTAX', "unknown comparison operator {$op}", $pos),
        };
    }

    /**
     * TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
     *
     * @param array<string,mixed> $lp
     * @param array<string,mixed> $rp
     */
    private static function concat(Value $l, Value $r, array $lp, array $rp, array $opPos): Value
    {
        $lv = $l->scalarSource($lp);
        $rv = $r->scalarSource($rp);
        if ($lv->kind === Value::BOOL) {
            fail('E_NOT_TEXT', 'cannot concatenate a boolean', $lp);
        }
        if ($rv->kind === Value::BOOL) {
            fail('E_NOT_TEXT', 'cannot concatenate a boolean', $rp);
        }
        if ($lv->kind === Value::TEXT && $rv->kind === Value::TEXT) {
            $ls = (string) $lv->scalar;
            $rs = (string) $rv->scalar;
            // The length is checked before the result is built (spec §6.4); the
            // byte length bounds the code point length, so only a candidate over
            // the cap is counted.
            if (strlen($ls) + strlen($rs) > Limits::MAX_TEXT_LEN) {
                Budget::checkText(Utf8::length($ls) + Utf8::length($rs), $opPos, 'the concatenation');
            }
            return Value::text($ls . $rs);
        }
        $lb = $l->asBytes($lp);
        $rb = $r->asBytes($rp);
        Budget::checkText(strlen($lb) + strlen($rb), $opPos, 'the concatenation');
        return Value::bin($lb . $rb);
    }

    private static function isIn(Value $needle, Value $hay): bool
    {
        if ($hay->size() === 0) {
            return $hay->eql($needle);
        }
        foreach ($hay->values() as $child) {
            if ($child->eql($needle)) {
                return true;
            }
        }
        return false;
    }

    /** @param array<string,mixed> $pos */
    private static function bitwise(string $op, string $a, string $b, array $pos): Value
    {
        $la = strlen($a);
        $lb = strlen($b);
        if ($la !== $lb) {
            fail('E_LEN_MISMATCH', "{$op} needs operands of equal length ({$la} vs {$lb})", $pos);
        }
        $out = '';
        for ($i = 0; $i < $la; $i++) {
            $x = ord($a[$i]);
            $y = ord($b[$i]);
            $out .= chr($op === 'BAND' ? ($x & $y) : ($op === 'BOR' ? ($x | $y) : ($x ^ $y)));
        }
        return Value::bin($out);
    }

    // --- assignment ---------------------------------------------------------

    /** @param array<string,mixed> $node */
    private static function evalAssign(array $node, Context $ctx): Value
    {
        $path = self::resolveTarget($node['target'], $ctx);
        $key = $path[count($path) - 1];

        if ($node['op'] === '=') {
            // Stored `count($path) - 1` levels down, so its own depth is checked
            // from there (spec §3.4, §6.4): path plus value, reported at the target.
            $value = self::evalNode($node['value'], $ctx)->copyBelow(count($path) - 1, $node['pos']);
        } else {
            $current = self::walkCreate($ctx, $path, count($path) - 1)->get($key);
            if ($current === null) {
                fail('E_UNDEF_VAR', "{$node['op']} needs an existing target", $node['target']['pos']);
            }
            $rhs = self::evalNode($node['value'], $ctx);
            $binOp = self::COMPOUND[$node['op']];
            $tp = $node['target']['pos'];
            $vp = $node['value']['pos'];
            if ($binOp === '&') {
                $value = self::concat($current, $rhs, $tp, $vp, $node['pos']);
            } else {
                $a = $current->asDecimalLazy($tp);
                $b = $rhs->asDecimalLazy($vp);
                $value = Value::numTrusted(Dec::arith($binOp, $a, $b, $node['pos']));
            }
        }

        // Re-derived after the right-hand side ran, which may have replaced or
        // removed any level along the path.
        self::walkCreate($ctx, $path, count($path) - 1)->set($key, $value);
        return $value;
    }

    /**
     * Walks from the root along $path, creating any level that is missing, and
     * returns the value at the end. Re-derived rather than remembered — see
     * resolveTarget.
     *
     * @param list<string> $path
     */
    private static function walkCreate(Context $ctx, array $path, int $upto): Value
    {
        $cur = $ctx->root;
        for ($i = 0; $i < $upto; $i++) {
            $next = $cur->get($path[$i]);
            if ($next === null) {
                $next = Value::none();
                $cur->set($path[$i], $next);
            }
            $cur = $next;
        }
        return $cur;
    }

    /**
     * Walks the target chain and returns the full key path, evaluating each index
     * expression exactly once, left to right, and creating each intermediate
     * level as it goes — so `A[COUNT(A)] = 1` sees the A the walk just created.
     *
     * A path rather than a live container reference (§5.7). The right-hand side
     * may replace any level the walk just found; the assignment then lands in the
     * tree that exists afterwards, rather than in an object that has been
     * detached from it and which nothing can ever read.
     *
     * @param array<string,mixed> $target
     * @return list<string>
     */
    private static function resolveTarget(array $target, Context $ctx): array
    {
        $chain = [];
        $n = $target;
        while ($n['t'] === 'index') {
            // Appended and reversed once: array_unshift moves every element, so a
            // 40,000-bracket target cost ten seconds.
            $chain[] = $n['idx'];
            $n = $n['obj'];
        }
        $chain = array_reverse($chain);

        if ($ctx->isBound($n['name'])) {
            fail(
                'E_BAD_ASSIGN',
                "{$n['name']} is an aggregate binder and cannot be assigned",
                $target['pos'],
            );
        }
        // The chain was walked iteratively, which is why nothing has counted it
        // yet: `A[1][2][3]` is a chain of index nodes, not a nesting of them, so
        // neither the parser's depth nor the evaluator's ever sees it -- and the
        // value it is about to build is one level deeper than the chain is long.
        // Uncounted, that built a value deeper than copy, eql and dump can walk,
        // so the assignment succeeded and reading the result back afterwards
        // failed.
        if (count($chain) + 1 > MAX_DEPTH) {
            fail('E_DEPTH', 'value nested too deeply', $target['pos']);
        }
        $path = [$n['name']];
        if (!$chain) {
            return $path;
        }

        if ($ctx->root->get($n['name']) === null) {
            $ctx->root->set($n['name'], Value::none());
        }

        for ($i = 0, $last = count($chain) - 1; $i < $last; $i++) {
            $k = self::evalNode($chain[$i], $ctx)->asText($chain[$i]['pos']);
            $cur = self::walkCreate($ctx, $path, count($path));
            if ($cur->get($k) === null) {
                $cur->set($k, Value::none());
            }
            $path[] = $k;
        }
        $lastNode = $chain[count($chain) - 1];
        $path[] = self::evalNode($lastNode, $ctx)->asText($lastNode['pos']);
        return $path;
    }
}
