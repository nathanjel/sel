<?php
declare(strict_types=1);

namespace Sel;

final class MathOpCode
{
    public const LOAD_VAR = 1;
    public const LOAD_CONST = 2;
    public const LOAD_LEAF = 3;
    public const ADD = 4;
    public const SUB = 5;
    public const MUL = 6;
    public const DIV = 7;
    public const MOD = 8;
    public const NEG = 9;
    public const ABS = 10;
    public const SIGN = 11;
    public const CEIL = 12;
    public const FLOOR = 13;
    public const TRUNC = 14;
    public const ROUND = 15;
    public const POWER = 16;
    public const MIN = 17;
    public const MAX = 18;
}

final class MathPlan
{
    private const MATH_BINARY_OPS = ['+' => true, '-' => true, '*' => true, '/' => true, '%' => true];
    private const MATH_UNARY_OPS = ['NEG' => true];
    private const MATH_BUILTINS = [
        'ROUND' => true, 'ABS' => true, 'SIGN' => true, 'CEIL' => true,
        'FLOOR' => true, 'TRUNC' => true, 'POWER' => true, 'MIN' => true, 'MAX' => true,
    ];

    /** @param array<string,mixed>|null $node */
    public static function isMathOp(?array $node): bool
    {
        if ($node === null) return false;
        $t = $node['t'] ?? null;
        if ($t === 'bin' && isset(self::MATH_BINARY_OPS[$node['op']])) return true;
        if ($t === 'un' && isset(self::MATH_UNARY_OPS[$node['op']])) return true;
        if ($t === 'call' && isset(self::MATH_BUILTINS[$node['name']])) return true;
        return false;
    }

    /**
     * @param array<string,mixed> $root
     * @return array{steps:list<array<string,mixed>>,outputSlot:int,scratchpadSize:int}|null
     */
    public static function compile(array $root): ?array
    {
        if (!self::isMathOp($root)) return null;

        $steps = [];
        $slotCount = 0;
        $allocSlot = static function () use (&$slotCount): int {
            return $slotCount++;
        };

        $emit = static function (array $node, int $depth) use (&$emit, &$steps, $allocSlot): ?array {
            if ($depth > MAX_DEPTH) return null;

            $t = $node['t'] ?? null;

            if ($t === 'var') {
                $slot = $allocSlot();
                $steps[] = [
                    'op' => MathOpCode::LOAD_VAR,
                    'dst' => $slot,
                    'name' => $node['name'],
                    'pos' => $node['pos'],
                ];
                return ['slot' => $slot, 'constVal' => null];
            }

            if ($t === 'num') {
                $dec = $node['dec'] ?? null;
                if ($dec === null) {
                    try {
                        $dec = Dec::parse((string) $node['v'], $node['pos']);
                        if ($dec === null) return null;
                    } catch (\Throwable) {
                        return null;
                    }
                }
                $slot = $allocSlot();
                $steps[] = [
                    'op' => MathOpCode::LOAD_CONST,
                    'dst' => $slot,
                    'constVal' => $dec,
                    'pos' => $node['pos'],
                ];
                return ['slot' => $slot, 'constVal' => $dec];
            }

            if ($t === 'bin' && isset(self::MATH_BINARY_OPS[$node['op']])) {
                if (!isset($node['l'], $node['r'])) return null;
                $resL = $emit($node['l'], $depth + 1);
                if ($resL === null) return null;
                $resR = $emit($node['r'], $depth + 1);
                if ($resR === null) return null;

                $op = $node['op'];

                // Copy propagation:
                // Rule 1: x + 0 (scale == 0) -> resL
                if ($op === '+' && $resR['constVal'] !== null && Dec::isZero($resR['constVal']) && $resR['constVal']['scale'] === 0) {
                    if (($node['r']['t'] ?? null) === 'num' && !empty($steps) && $steps[count($steps) - 1]['dst'] === $resR['slot']) {
                        array_pop($steps);
                    }
                    return $resL;
                }
                // Rule 2: 0 + x (scale == 0) -> resR
                if ($op === '+' && $resL['constVal'] !== null && Dec::isZero($resL['constVal']) && $resL['constVal']['scale'] === 0) {
                    return $resR;
                }
                // Rule 3: x - 0 (scale == 0) -> resL
                if ($op === '-' && $resR['constVal'] !== null && Dec::isZero($resR['constVal']) && $resR['constVal']['scale'] === 0) {
                    if (($node['r']['t'] ?? null) === 'num' && !empty($steps) && $steps[count($steps) - 1]['dst'] === $resR['slot']) {
                        array_pop($steps);
                    }
                    return $resL;
                }
                // Rule 4: x * 1 (scale == 0) -> resL
                if ($op === '*' && $resR['constVal'] !== null && !$resR['constVal']['neg'] && $resR['constVal']['digits'] === '1' && $resR['constVal']['scale'] === 0) {
                    if (($node['r']['t'] ?? null) === 'num' && !empty($steps) && $steps[count($steps) - 1]['dst'] === $resR['slot']) {
                        array_pop($steps);
                    }
                    return $resL;
                }
                // Rule 5: 1 * x (scale == 0) -> resR
                if ($op === '*' && $resL['constVal'] !== null && !$resL['constVal']['neg'] && $resL['constVal']['digits'] === '1' && $resL['constVal']['scale'] === 0) {
                    return $resR;
                }

                $dst = $allocSlot();
                $opCode = match ($op) {
                    '+' => MathOpCode::ADD,
                    '-' => MathOpCode::SUB,
                    '*' => MathOpCode::MUL,
                    '/' => MathOpCode::DIV,
                    '%' => MathOpCode::MOD,
                };
                $steps[] = [
                    'op' => $opCode,
                    'dst' => $dst,
                    'src1' => $resL['slot'],
                    'src2' => $resR['slot'],
                    'pos' => $node['pos'],
                ];
                return ['slot' => $dst, 'constVal' => null];
            }

            if ($t === 'un' && $node['op'] === 'NEG') {
                if (!isset($node['x'])) return null;
                $resX = $emit($node['x'], $depth + 1);
                if ($resX === null) return null;
                $dst = $allocSlot();
                $steps[] = [
                    'op' => MathOpCode::NEG,
                    'dst' => $dst,
                    'src1' => $resX['slot'],
                    'pos' => $node['pos'],
                ];
                return ['slot' => $dst, 'constVal' => null];
            }

            if ($t === 'call' && isset(self::MATH_BUILTINS[$node['name']])) {
                $name = $node['name'];
                if (in_array($name, ['ABS', 'SIGN', 'CEIL', 'FLOOR', 'TRUNC'], true)) {
                    if (!isset($node['args']) || count($node['args']) !== 1) return null;
                    $resArg = $emit($node['args'][0], $depth + 1);
                    if ($resArg === null) return null;
                    $dst = $allocSlot();
                    $opCode = match ($name) {
                        'ABS' => MathOpCode::ABS,
                        'SIGN' => MathOpCode::SIGN,
                        'CEIL' => MathOpCode::CEIL,
                        'FLOOR' => MathOpCode::FLOOR,
                        'TRUNC' => MathOpCode::TRUNC,
                    };
                    $steps[] = [
                        'op' => $opCode,
                        'dst' => $dst,
                        'src1' => $resArg['slot'],
                        'pos' => $node['pos'],
                    ];
                    return ['slot' => $dst, 'constVal' => null];
                }
                if ($name === 'ROUND' || $name === 'POWER') {
                    if (!isset($node['args']) || count($node['args']) !== 2) return null;
                    $res0 = $emit($node['args'][0], $depth + 1);
                    if ($res0 === null) return null;
                    $res1 = $emit($node['args'][1], $depth + 1);
                    if ($res1 === null) return null;
                    $dst = $allocSlot();
                    $opCode = $name === 'ROUND' ? MathOpCode::ROUND : MathOpCode::POWER;
                    $steps[] = [
                        'op' => $opCode,
                        'dst' => $dst,
                        'src1' => $res0['slot'],
                        'src2' => $res1['slot'],
                        'pos' => $node['pos'],
                        'auxPos' => $node['args'][1]['pos'],
                    ];
                    return ['slot' => $dst, 'constVal' => null];
                }
                if ($name === 'MIN' || $name === 'MAX') {
                    if (!isset($node['args']) || count($node['args']) < 1) return null;
                    $res0 = $emit($node['args'][0], $depth + 1);
                    if ($res0 === null) return null;
                    $currSlot = $res0['slot'];
                    $opCode = $name === 'MIN' ? MathOpCode::MIN : MathOpCode::MAX;
                    for ($k = 1, $numArgs = count($node['args']); $k < $numArgs; $k++) {
                        $resNext = $emit($node['args'][$k], $depth + 1);
                        if ($resNext === null) return null;
                        $dst = $allocSlot();
                        $steps[] = [
                            'op' => $opCode,
                            'dst' => $dst,
                            'src1' => $currSlot,
                            'src2' => $resNext['slot'],
                            'pos' => $node['pos'],
                        ];
                        $currSlot = $dst;
                    }
                    return ['slot' => $currSlot, 'constVal' => null];
                }
            }

            if ($t === 'bin' || $t === 'un') return null;
            if ($t === 'assign' || $t === 'seq' || $t === 'list') return null;
            if ($t === 'call' && in_array($node['name'] ?? '', ['IF', 'COND'], true)) return null;

            $slot = $allocSlot();
            $steps[] = [
                'op' => MathOpCode::LOAD_LEAF,
                'dst' => $slot,
                'leafNode' => $node,
                'pos' => $node['pos'],
            ];
            return ['slot' => $slot, 'constVal' => null];
        };

        $res = $emit($root, 1);
        if ($res === null || empty($steps)) return null;

        return [
            'steps' => $steps,
            'outputSlot' => $res['slot'],
            'scratchpadSize' => $slotCount,
        ];
    }
}
