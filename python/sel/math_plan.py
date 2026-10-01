from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH, Pos
from .parser import Node
from ._math_ops import MATH_OPERATORS as _OPERATORS, MATH_PREFIX as _PREFIX, \
    MATH_BUILTINS as _BUILTINS, MATH_OPS as _OPS


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
    # Internal to this host, not in the manifest: coerce a loaded Value to a
    # decimal in place. Emitted where copy propagation drops the operation that
    # would have coerced it, so the coercion still happens at the same point in
    # the order as in the plain tree (SPEC 6.2, evaluate then coerce).
    COERCE = 19


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
    # Position of the source node a slot was loaded from, for the slots that
    # hold an UNCOERCED Value (variables and leaves). Every other slot holds a
    # decimal already.
    slot_pos: list
    # How often the plan has run interpreted, and the one Python function its
    # steps compile to once it is hot (eval._eval_planned); not part of what
    # the plan is.
    hits: int = field(default=0, compare=False, repr=False)
    run: Any = field(default=None, compare=False, repr=False)


# The vocabulary -- which source nodes compile, to which operation, with how
# many operands and which error positions -- is spec/math-ops.json's, rendered
# into _math_ops.py. The opcode NUMBERS are this host's own (OpCode above); the
# manifest names each operation and this maps the name to the number, refusing
# to import if the executor lacks one.
_NATIVE = {name: OpCode[name] for name in _OPS}
MATH_BINARY_OPS = frozenset(_OPERATORS)
MATH_UNARY_OPS = frozenset(_PREFIX)
MATH_BUILTINS = frozenset(_BUILTINS)


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
    # Slots that hold a loaded Value, not yet a decimal: it is coerced by the
    # operation that consumes it, after every operand has been evaluated (SPEC
    # 6.2 -- evaluate, then coerce), and it is read as the Value itself, so a
    # mutation by a later operand is visible through it (SPEC 3.4).
    value_slots: dict[int, Any] = {}

    def alloc_slot() -> int:
        nonlocal slot_count
        s = slot_count
        slot_count += 1
        return s

    def emit(node: Node, depth: int) -> tuple[int, D.Dec | None] | None:
        if depth > MAX_DEPTH:
            return None

        # 1. Variable
        if node.t == 'var':
            slot = alloc_slot()
            steps.append(Step(op=OpCode.LOAD_VAR, dst=slot, name=node.name, pos=node.pos))
            value_slots[slot] = node.pos
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

            def keep(slot: int, const: Any, operand: Node) -> tuple[int, Any]:
                # The identity operation is dropped, but not the coercion it
                # would have done: `x + 0` with a text `x` is still E_NOT_NUM,
                # at this point in the order and not when something later
                # consumes the slot.
                if slot in value_slots:
                    steps.append(Step(op=OpCode.COERCE, dst=slot, src1=slot,
                                      pos=value_slots.pop(slot)))
                return slot, const

            # Copy propagation for identity operations:
            # Rule 1: x + 0 (scale == 0) -> slot_l
            if op == '+' and const_r is not None and D.is_zero(const_r) and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return keep(slot_l, const_l, node.l)

            # Rule 2: 0 + x (scale == 0) -> slot_r
            if op == '+' and const_l is not None and D.is_zero(const_l) and const_l.scale == 0:
                return keep(slot_r, const_r, node.r)

            # Rule 3: x - 0 (scale == 0) -> slot_l
            if op == '-' and const_r is not None and D.is_zero(const_r) and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return keep(slot_l, const_l, node.l)

            # Rule 4: x * 1 (scale == 0) -> slot_l
            if op == '*' and const_r is not None and not const_r.neg and const_r.digits == 1 and const_r.scale == 0:
                if node.r.t == 'num' and steps and steps[-1].dst == slot_r:
                    steps.pop()
                return keep(slot_l, const_l, node.l)

            # Rule 5: 1 * x (scale == 0) -> slot_r
            if op == '*' and const_l is not None and not const_l.neg and const_l.digits == 1 and const_l.scale == 0:
                return keep(slot_r, const_r, node.r)

            dst = alloc_slot()
            op_code = _NATIVE[_OPERATORS[op]]
            steps.append(Step(op=op_code, dst=dst, src1=slot_l, src2=slot_r, pos=node.pos))
            return dst, None

        # 4. Prefix operator
        if node.t == 'un' and node.op in MATH_UNARY_OPS:
            if node.x is None:
                return None
            res_x = emit(node.x, depth + 1)
            if res_x is None:
                return None
            slot_x, _ = res_x
            dst = alloc_slot()
            steps.append(Step(op=_NATIVE[_PREFIX[node.op]], dst=dst, src1=slot_x, pos=node.pos))
            return dst, None

        # 5. Math builtins: operand count, fold and error positions from the
        #    manifest entry; a count the entry cannot serve derails the plan.
        if node.t == 'call' and node.name in MATH_BUILTINS:
            sym, arity, aux = _BUILTINS[node.name]
            op_code = _NATIVE[sym]
            args = node.args
            if arity == 1:
                if len(args) != 1:
                    return None
                res_arg = emit(args[0], depth + 1)
                if res_arg is None:
                    return None
                dst = alloc_slot()
                steps.append(Step(op=op_code, dst=dst, src1=res_arg[0], pos=node.pos))
                return dst, None
            if arity == 2:
                if len(args) != 2:
                    return None
                res_arg0 = emit(args[0], depth + 1)
                if res_arg0 is None:
                    return None
                res_arg1 = emit(args[1], depth + 1)
                if res_arg1 is None:
                    return None
                dst = alloc_slot()
                steps.append(Step(op=op_code, dst=dst, src1=res_arg0[0], src2=res_arg1[0],
                                  pos=node.pos, aux_pos=args[aux].pos if aux is not None else None))
                return dst, None
            # fold: one or more operands, combined pairwise left to right. Every
            # operand is evaluated before the first is coerced (SPEC 6.2).
            if len(args) < 1:
                return None
            slots = []
            for a in args:
                res_a = emit(a, depth + 1)
                if res_a is None:
                    return None
                slots.append(res_a[0])
            curr_slot = slots[0]
            for k in range(1, len(args)):
                dst = alloc_slot()
                steps.append(Step(op=op_code, dst=dst, src1=curr_slot, src2=slots[k], pos=node.pos))
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
        value_slots[slot] = node.pos
        return slot, None

    res = emit(root, 1)
    if res is None:
        return None
    output_slot, _ = res
    if not steps:
        return None

    # A slot still holding a Value at the end (an identity operation dropped by
    # copy propagation on the output) is coerced by one last step.
    if output_slot in value_slots:
        steps.append(Step(op=OpCode.COERCE, dst=output_slot, src1=output_slot,
                          pos=value_slots.pop(output_slot)))
    slot_pos = [None] * slot_count
    for st in steps:
        if st.op in (OpCode.LOAD_VAR, OpCode.LOAD_LEAF):
            slot_pos[st.dst] = st.pos
    return MathPlan(steps=steps, output_slot=output_slot, scratchpad_size=slot_count,
                    slot_pos=slot_pos)
