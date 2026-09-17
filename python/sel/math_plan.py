from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH, Pos
from .parser import Node


class OpCode(IntEnum):
    LOAD_VAR = 1
    LOAD_CONST = 2
    LOAD_LEAF = 3
    ADD = 4
    SUB = 5
    MUL = 6
    DIV = 7
    MOD = 8
    NEG = 9
    ABS = 10
    SIGN = 11
    CEIL = 12
    FLOOR = 13
    TRUNC = 14
    ROUND = 15
    POWER = 16
    MIN = 17
    MAX = 18


@dataclass(slots=True)
class Step:
    op: int
    dst: int
    src1: int = 0
    src2: int = 0
    pos: Pos | None = None
    aux_pos: Pos | None = None
    name: str = ''
    const_val: Any = None
    leaf_node: Any = None


@dataclass(slots=True)
class MathPlan:
    steps: list[Step]
    output_slot: int
    scratchpad_size: int


MATH_BINARY_OPS = frozenset({'+', '-', '*', '/', '%'})
MATH_UNARY_OPS = frozenset({'NEG'})
MATH_BUILTINS = frozenset({
    'ROUND', 'ABS', 'SIGN', 'CEIL', 'FLOOR', 'TRUNC', 'POWER', 'MIN', 'MAX'
})


def is_math_op(node: Node | None) -> bool:
    if node is None:
        return False
    if node.t == 'bin' and node.op in MATH_BINARY_OPS:
        return True
    if node.t == 'un' and node.op in MATH_UNARY_OPS:
        return True
    if node.t == 'call' and node.name in MATH_BUILTINS:
        return True
    return False


def compile_math_plan(root: Node) -> MathPlan | None:
    if not is_math_op(root):
        return None

    steps: list[Step] = []
    slot_count = 0

    def alloc_slot() -> int:
        nonlocal slot_count
        s = slot_count
        slot_count += 1
        return s

    def emit(node: Node, depth: int) -> tuple[int, D.Dec | None] | None:
        nonlocal steps
        if depth > MAX_DEPTH:
            return None

        # 1. Variable
        if node.t == 'var':
            slot = alloc_slot()
            steps.append(Step(op=OpCode.LOAD_VAR, dst=slot, name=node.name, pos=node.pos))
            return slot, None

        # 2. Number literal
        if node.t == 'num':
            dec = node.dec
            if dec is None:
                try:
                    dec = D.parse(node.v, node.pos)
                except Exception:
                    return None
            slot = alloc_slot()
            steps.append(Step(op=OpCode.LOAD_CONST, dst=slot, const_val=dec, pos=node.pos))
            return slot, dec

        # 3. Binary math operator
        if node.t == 'bin' and node.op in MATH_BINARY_OPS:
            if node.l is None or node.r is None:
                return None

            res_l = emit(node.l, depth + 1)
            if res_l is None:
                return None
            slot_l, const_l = res_l

            res_r = emit(node.r, depth + 1)
            if res_r is None:
                return None
            slot_r, const_r = res_r

            op = node.op

            # Copy propagation for identity operations:
            # Rule 1: x + 0 (scale == 0) -> slot_l
            if op == '+' and const_r is not None and D.is_zero(const_r) and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return slot_l, const_l

            # Rule 2: 0 + x (scale == 0) -> slot_r
            if op == '+' and const_l is not None and D.is_zero(const_l) and const_l.scale == 0:
                return slot_r, const_r

            # Rule 3: x - 0 (scale == 0) -> slot_l
            if op == '-' and const_r is not None and D.is_zero(const_r) and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return slot_l, const_l

            # Rule 4: x * 1 (scale == 0) -> slot_l
            if op == '*' and const_r is not None and not const_r.neg and const_r.digits == 1 and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return slot_l, const_l

            # Rule 5: 1 * x (scale == 0) -> slot_r
            if op == '*' and const_l is not None and not const_l.neg and const_l.digits == 1 and const_l.scale == 0:
                return slot_r, const_r

            dst = alloc_slot()
            op_code = {
                '+': OpCode.ADD,
                '-': OpCode.SUB,
                '*': OpCode.MUL,
                '/': OpCode.DIV,
                '%': OpCode.MOD,
            }[op]
            steps.append(Step(op=op_code, dst=dst, src1=slot_l, src2=slot_r, pos=node.pos))
            return dst, None

        # 4. Unary NEG
        if node.t == 'un' and node.op == 'NEG':
            if node.x is None:
                return None
            res_x = emit(node.x, depth + 1)
            if res_x is None:
                return None
            slot_x, _ = res_x
            dst = alloc_slot()
            steps.append(Step(op=OpCode.NEG, dst=dst, src1=slot_x, pos=node.pos))
            return dst, None

        # 5. Math builtins
        if node.t == 'call' and node.name in MATH_BUILTINS:
            name = node.name
            if name in ('ABS', 'SIGN', 'CEIL', 'FLOOR', 'TRUNC'):
                if len(node.args) != 1:
                    return None
                res_arg = emit(node.args[0], depth + 1)
                if res_arg is None:
                    return None
                slot_arg, _ = res_arg
                dst = alloc_slot()
                op_code = {
                    'ABS': OpCode.ABS,
                    'SIGN': OpCode.SIGN,
                    'CEIL': OpCode.CEIL,
                    'FLOOR': OpCode.FLOOR,
                    'TRUNC': OpCode.TRUNC,
                }[name]
                steps.append(Step(op=op_code, dst=dst, src1=slot_arg, pos=node.pos))
                return dst, None

            if name in ('ROUND', 'POWER'):
                if len(node.args) != 2:
                    return None
                res_arg0 = emit(node.args[0], depth + 1)
                if res_arg0 is None:
                    return None
                slot_arg0, _ = res_arg0

                res_arg1 = emit(node.args[1], depth + 1)
                if res_arg1 is None:
                    return None
                slot_arg1, _ = res_arg1

                dst = alloc_slot()
                op_code = OpCode.ROUND if name == 'ROUND' else OpCode.POWER
                steps.append(Step(op=op_code, dst=dst, src1=slot_arg0, src2=slot_arg1,
                                  pos=node.pos, aux_pos=node.args[1].pos))
                return dst, None

            if name in ('MIN', 'MAX'):
                if len(node.args) < 1:
                    return None
                res_prev = emit(node.args[0], depth + 1)
                if res_prev is None:
                    return None
                curr_slot, _ = res_prev

                op_code = OpCode.MIN if name == 'MIN' else OpCode.MAX
                for k in range(1, len(node.args)):
                    res_next = emit(node.args[k], depth + 1)
                    if res_next is None:
                        return None
                    next_slot, _ = res_next
                    dst = alloc_slot()
                    steps.append(Step(op=op_code, dst=dst, src1=curr_slot, src2=next_slot, pos=node.pos))
                    curr_slot = dst
                return curr_slot, None

        # 6. Derailing constructs:
        if node.t == 'bin':
            return None
        if node.t == 'un':
            return None
        if node.t in ('assign', 'seq', 'list'):
            return None
        if node.t == 'call' and node.name in ('IF', 'COND'):
            return None

        # 7. Leaves (variables already handled, numbers already handled)
        slot = alloc_slot()
        steps.append(Step(op=OpCode.LOAD_LEAF, dst=slot, leaf_node=node, pos=node.pos))
        return slot, None

    res = emit(root, 1)
    if res is None:
        return None
    output_slot, _ = res
    if not steps:
        return None

    return MathPlan(steps=steps, output_slot=output_slot, scratchpad_size=slot_count)
