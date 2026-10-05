"""The evaluator, and the argument framework built-ins are written against.

Nothing here catches a SelError. An error surfaces from the innermost node that
failed, carrying that node's position, and no layer rewrites it.
"""

from __future__ import annotations

import builtins

from typing import Any, NoReturn

from . import decimal as D
from ._budget import check_collection, check_text
from .errors import MAX_DEPTH, Pos, SelError, fail
from .math_plan import MathPlan, OpCode
from .parser import Node
from .utf8 import bytes_compare
from .value import BOOL, NONE, TEXT, Value
from .builtins.number import MAX_SCALE, MAX_POWER, check_sized_int


# Resolve enum attributes once; the hot interpreter loop compares cached opcodes.
_LOAD_VAR = OpCode.LOAD_VAR
_LOAD_CONST = OpCode.LOAD_CONST
_LOAD_LEAF = OpCode.LOAD_LEAF
_ADD = OpCode.ADD
_SUB = OpCode.SUB
_MUL = OpCode.MUL
_DIV = OpCode.DIV
_MOD = OpCode.MOD
_NEG = OpCode.NEG
_ABS = OpCode.ABS
_SIGN = OpCode.SIGN
_CEIL = OpCode.CEIL
_FLOOR = OpCode.FLOOR
_TRUNC = OpCode.TRUNC
_ROUND = OpCode.ROUND
_POWER = OpCode.POWER
_MIN = OpCode.MIN
_MAX = OpCode.MAX
_COERCE = OpCode.COERCE


class Context:
    __slots__ = ('root', 'frames', 'bound', 'depth', 'join_prefilter', 'join_prefilter_report')

    def __init__(self, root: Value | None = None) -> None:
        self.root = root if root is not None else Value.none()
        self.frames: list[dict[str, Value]] = []   # aggregate binders
        # How many pushed frames bind each name. A frame's names are fixed while
        # it is pushed (a binder changes its value, never its name), so a name
        # missing here is in no frame: lookup goes straight to the root and
        # is_bound is one test.
        self.bound: dict[str, int] = {}
        self.depth = 0
        # A FILTER whose source is a LINK hands the join its conjuncts here,
        # for the join to test on the rows it joins where that is provably
        # the same as filtering the joined rows (builtins/structure.py, _link;
        # SEL-0052, SEL-0054).
        self.join_prefilter = None
        # What a join below reported after applying them: which conjuncts
        # every row that came up has passed, whether any row was kept on an
        # error -- in which case the join above applies them all again -- and
        # whether any row was dropped.
        self.join_prefilter_report = None

    def lookup(self, name: str) -> Value | None:
        frames = self.frames
        if frames:
            # The innermost frame first: it holds the binder nearly every read
            # inside an aggregate asks for.
            v = frames[-1].get(name)
            if v is not None:
                return v
            # Further out only when some frame binds the name at all.
            if len(frames) > 1 and name in self.bound:
                for i in range(len(frames) - 2, -1, -1):
                    v = frames[i].get(name)
                    if v is not None:
                        return v
        root = self.root
        if root.storage is None:            # Value.get's own last case, without the call
            children = root.children
            return children.get(name) if children else None
        return root.get(name)

    def is_bound(self, name: str) -> bool:
        return name in self.bound

    def push_frame(self, mapping: dict[str, Value]) -> None:
        self.frames.append(mapping)
        bound = self.bound
        for name in mapping:
            bound[name] = bound.get(name, 0) + 1

    def pop_frame(self) -> None:
        bound = self.bound
        for name in self.frames.pop():
            n = bound[name] - 1
            if n:
                bound[name] = n
            else:
                del bound[name]


# --- arguments --------------------------------------------------------------

class Args:
    """Wraps the flattened argument vector. Values are evaluated at most once,
    so a built-in body can read the same argument repeatedly without thinking
    about it, and typed accessors report failures against the argument's own
    position.
    """

    __slots__ = ('nodes', 'name', 'pos', 'ctx', '_vals', 'call')

    def __init__(self, node: Node, ctx: Context) -> None:
        self.nodes = node.args
        self.call = node
        self.name = node.name
        self.pos = node.pos
        self.ctx = ctx
        self._vals: list[Value | None] = [None] * len(node.args)

    # Read by the two built-ins that use them (RECORD, FILTER) and by nothing else:
    # a slot each cost every call's Args a store for a rule most calls never ask.
    @property
    def record_shape(self):
        return self.call.record_shape

    @property
    def adopt(self) -> bool:
        return self.call.adopt_items

    def count(self) -> int:
        return len(self.nodes)

    def _oob(self, i: int) -> NoReturn:
        # A registered host function read an argument the call does not have
        # (spec/SPEC.md 8.1): a SEL error at the call, never the host's own
        # IndexError -- and never Python's negative indexing answering for it.
        fail('E_BAD_ARG',
             f'{self.name} has no argument {i} (the call has {len(self.nodes)})', self.pos)

    # The bounds check is a sign test plus the IndexError the list raises itself:
    # a host function reads an argument it was not given -> `_oob`, never the
    # host's IndexError, and never Python's negative indexing answering for it.
    # (Two len() calls per read were measurable on a program that reads millions.)
    def node(self, i: int) -> Node:
        if i < 0:
            self._oob(i)
        try:
            return self.nodes[i]
        except IndexError:
            self._oob(i)

    def pos_of(self, i: int) -> Pos:
        if i < 0:
            self._oob(i)
        try:
            return self.nodes[i].pos
        except IndexError:
            self._oob(i)

    def val(self, i: int) -> Value:
        if i < 0:
            self._oob(i)
        try:
            v = self._vals[i]
        except IndexError:
            self._oob(i)
        if v is None:
            v = self._vals[i] = eval_node(self.nodes[i], self.ctx)
        return v

    def eval_node(self, node: Node) -> Value:
        """For lazy functions re-evaluating a body node under changed bindings."""
        return eval_node(node, self.ctx)

    def text(self, i: int) -> str:
        return self.val(i).as_text(self.pos_of(i))

    def bytes(self, i: int) -> bytes:
        return self.val(i).as_bytes(self.pos_of(i))

    def bool(self, i: int) -> bool:  # noqa: A003
        return self.val(i).as_bool(self.pos_of(i))

    def dec(self, i: int) -> D.Dec:
        return self.val(i).as_decimal(self.pos_of(i))

    def int(self, i: int) -> int:  # noqa: A003
        d = self.dec(i)
        if not D.is_integer(d):
            fail('E_NOT_INT', f'{self.name} argument {i + 1} must be a whole number',
                 self.pos_of(i))
        return D.to_safe_int(d)

    def non_neg_int(self, i: builtins.int) -> builtins.int:
        n = self.int(i)
        if n < 0:
            fail('E_RANGE', f'{self.name} argument {i + 1} must not be negative',
                 self.pos_of(i))
        return n

    def symbol(self, i: builtins.int) -> str:
        """Requires the argument to be a bare identifier in the source — the AST
        shape check that gives aggregates their three-argument binder form.
        """
        if i < 0:
            self._oob(i)
        try:
            n = self.nodes[i]
        except IndexError:
            self._oob(i)
        if n.t != 'var' or n.grouped:
            fail('E_EXPECT_SYMBOL', f'{self.name} argument {i + 1} must be a plain name',
                 n.pos)
        return n.name

    def is_symbol(self, i: builtins.int) -> builtins.bool:
        if i < 0:
            self._oob(i)
        try:
            n = self.nodes[i]
        except IndexError:
            self._oob(i)
        return n.t == 'var' and not n.grouped


# --- evaluation -------------------------------------------------------------

def eval_node(node: Node, ctx: Context) -> Value:
    ctx.depth += 1
    if ctx.depth > MAX_DEPTH:
        ctx.depth -= 1
        fail('E_DEPTH', 'evaluation nested too deeply', node.pos)
    try:
        ev = node.ev
        if ev is not None:
            return ev(node, ctx)
        if node.math_plan is not None:
            return _eval_planned(node, ctx)
        return _EVAL.get(node.t, _eval_unknown)(node, ctx)
    finally:
        ctx.depth -= 1


def _eval_planned(node: Node, ctx: Context) -> Value:
    """Runs NODE's math plan (SPEC 6.2): step by step (_interpret_math_plan)
    until it has run _PLAN_HOT times, then as one Python function built from
    its steps (_compile_math_plan) -- unless it has more than _PLAN_MAX_STEPS.
    Compiling costs about as much as a few hundred interpreted runs, so a plan
    that runs once (a one-shot evaluate, a conformance case) never pays it.
    """
    plan = node.math_plan
    run = plan.run
    if run is not None:
        return run(ctx)
    hits = plan.hits = plan.hits + 1
    if hits == _PLAN_HOT and len(plan.steps) <= _PLAN_MAX_STEPS:
        plan.run = _compile_math_plan(plan)
    return _interpret_math_plan(plan, ctx)


# Measured on Mandelbrot's plans (3-9 steps): compiling costs 320-650 us and
# saves 1.4-3.6 us a run, so it pays for itself after 120-250 runs. Compile time
# grows faster than the plan, hence the cap.
_PLAN_HOT = 256
_PLAN_MAX_STEPS = 256


def _interpret_math_plan(plan: MathPlan, ctx: Context) -> Value:
    """Runs a math plan as a pure optimisation of the plain tree (SPEC 6.2).

    A variable or leaf is loaded as the VALUE, not a decimal: the operation that
    consumes it coerces it, after every operand has been evaluated -- so a later
    operand's error is found before an earlier operand's coercion error, and a
    mutation by a later operand is visible through a variable read earlier
    (SPEC 3.4). Operations coerce their operands left to right, as the plain
    tree does. Slots produced by an operation already hold decimals.
    """
    scratchpad: list[Any] = [None] * plan.scratchpad_size
    slot_pos = plan.slot_pos
    Dec = D.Dec

    def dec(i: int) -> Any:
        x = scratchpad[i]
        if x.__class__ is Dec:
            return x
        return x.as_decimal(slot_pos[i])

    for step in plan.steps:
        op = step.op
        if op == _LOAD_VAR:
            val = ctx.lookup(step.name)
            if val is None:
                fail('E_UNDEF_VAR', f'undefined variable {step.name}', step.pos)
            scratchpad[step.dst] = val
        elif op == _LOAD_CONST:
            scratchpad[step.dst] = step.const_val
        elif op == _LOAD_LEAF:
            scratchpad[step.dst] = eval_node(step.leaf_node, ctx)
        elif op == _COERCE:
            scratchpad[step.dst] = dec(step.src1)
        elif op == _ADD:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = D.add(a, b, step.pos)
        elif op == _SUB:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = D.sub(a, b, step.pos)
        elif op == _MUL:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = D.mul(a, b, step.pos)
        elif op == _DIV:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = D.div(a, b, step.pos)
        elif op == _MOD:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = D.mod(a, b, step.pos)
        elif op == _NEG:
            scratchpad[step.dst] = D.negate(dec(step.src1))
        elif op == _ABS:
            scratchpad[step.dst] = D.abs_(dec(step.src1))
        elif op == _SIGN:
            s = D.sign(dec(step.src1))
            scratchpad[step.dst] = D.make(s < 0, abs(s), 0)
        elif op == _CEIL:
            scratchpad[step.dst] = D.ceil(dec(step.src1))
        elif op == _FLOOR:
            scratchpad[step.dst] = D.floor(dec(step.src1))
        elif op == _TRUNC:
            scratchpad[step.dst] = D.trunc(dec(step.src1))
        elif op == _ROUND:
            x = dec(step.src1); e = dec(step.src2)
            n = check_sized_int(e, 'ROUND', 2, MAX_SCALE, 'ROUND scale', step.aux_pos)
            scratchpad[step.dst] = D.round(x, n, step.pos)
        elif op == _POWER:
            x = dec(step.src1); e = dec(step.src2)
            n = check_sized_int(e, 'POWER', 2, MAX_POWER, 'POWER exponent', step.aux_pos)
            scratchpad[step.dst] = D.power(x, n, step.pos)
        elif op == _MIN:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = b if D.cmp(b, a) < 0 else a
        elif op == _MAX:
            a = dec(step.src1); b = dec(step.src2)
            scratchpad[step.dst] = b if D.cmp(b, a) > 0 else a
    return Value._num_owned(scratchpad[plan.output_slot])


# What a generated plan function calls, bound into it as free variables.
_PLAN_ENV = {
    'fail': fail, 'num': Value._num_owned, 'add': D.add, 'sub': D.sub, 'mul': D.mul,
    'div': D.div, 'mod': D.mod, 'negate': D.negate, 'abs_': D.abs_, 'sign': D.sign,
    'make': D.make, 'ceil': D.ceil, 'floor': D.floor, 'trunc': D.trunc, 'round_': D.round,
    'power': D.power, 'cmp': D.cmp, 'check_sized_int': check_sized_int,
    'MAX_SCALE': MAX_SCALE, 'MAX_POWER': MAX_POWER, 'abs': abs,
}
_PLAN_BINARY = {_ADD: 'add', _SUB: 'sub', _MUL: 'mul', _DIV: 'div', _MOD: 'mod'}
_PLAN_UNARY = {_NEG: 'negate', _ABS: 'abs_', _CEIL: 'ceil', _FLOOR: 'floor', _TRUNC: 'trunc'}
# Every operation has a translation, checked at load time (spec/math-ops.md).
_PLAN_MISSING = set(OpCode) - set(_PLAN_BINARY) - set(_PLAN_UNARY) - {
    _LOAD_VAR, _LOAD_CONST, _LOAD_LEAF, _COERCE, _SIGN, _ROUND, _POWER, _MIN, _MAX}
if _PLAN_MISSING:
    raise ImportError(f'math plan compiler has no translation for {sorted(_PLAN_MISSING)}')


def _compile_math_plan(plan: MathPlan) -> Any:
    """The steps _interpret_math_plan runs, as straight-line Python with one
    local per slot: the same loads, coercions (each at the operation that
    consumes the slot), operations and error positions, in the same order --
    without the per-step dispatch, the scratchpad list or a closure per run.
    The source holds no program text: names, positions, constants and leaf
    nodes reach it as free variables k<n>, as namedtuple and dataclasses build
    their methods."""
    env = dict(_PLAN_ENV, eval_node=eval_node)

    def k(x: Any) -> str:
        name = f'k{len(env)}'
        env[name] = x
        return name

    loaded: set[int] = set()     # slots holding a loaded Value, not yet a decimal

    def dec(slot: int) -> str:
        if slot in loaded:
            return f's{slot}.as_decimal({k(plan.slot_pos[slot])})'
        return f's{slot}'

    lines = []
    for st in plan.steps:
        op, d = st.op, f's{st.dst}'
        if op == _LOAD_VAR:
            lines.append(f'{d} = ctx.lookup({k(st.name)})')
            lines.append(f'if {d} is None: fail("E_UNDEF_VAR", '
                         f'{k(f"undefined variable {st.name}")}, {k(st.pos)})')
            loaded.add(st.dst)
        elif op == _LOAD_CONST:
            lines.append(f'{d} = {k(st.const_val)}')
            loaded.discard(st.dst)
        elif op == _LOAD_LEAF:
            lines.append(f'{d} = eval_node({k(st.leaf_node)}, ctx)')
            loaded.add(st.dst)
        elif op == _COERCE:
            lines.append(f'{d} = {dec(st.src1)}')
            loaded.discard(st.dst)
        elif op in _PLAN_BINARY:
            lines.append(f'{d} = {_PLAN_BINARY[op]}({dec(st.src1)}, {dec(st.src2)}, {k(st.pos)})')
        elif op in _PLAN_UNARY:
            lines.append(f'{d} = {_PLAN_UNARY[op]}({dec(st.src1)})')
        elif op == _SIGN:
            lines.append(f'{d} = sign({dec(st.src1)}); {d} = make({d} < 0, abs({d}), 0)')
        elif op == _ROUND or op == _POWER:
            fn, cap, what = ('round_', 'MAX_SCALE', 'ROUND') if op == _ROUND else ('power', 'MAX_POWER', 'POWER')
            label = 'ROUND scale' if op == _ROUND else 'POWER exponent'
            lines.append(f'{d} = {fn}({dec(st.src1)}, check_sized_int({dec(st.src2)}, "{what}", 2, '
                         f'{cap}, "{label}", {k(st.aux_pos)}), {k(st.pos)})')
        else:   # _MIN, _MAX
            lines.append(f'a = {dec(st.src1)}; b = {dec(st.src2)}; '
                         f'{d} = b if cmp(b, a) {"<" if op == _MIN else ">"} 0 else a')
    lines.append(f'return num(s{plan.output_slot})')
    src = (f'def factory({", ".join(env)}):\n    def run(ctx):\n'
           + ''.join(f'        {line}\n' for line in lines) + '    return run\n')
    scope: dict[str, Any] = {}
    exec(compile(src, '<math plan>', 'exec'), scope)
    # Taken out of the dict that is its globals, so the two make no cycle and a
    # dropped plan is freed by reference counting (see _gc.py).
    return scope.pop('factory')(*env.values())


# --- one function per node type, chosen by _EVAL (the table ends the module) --

def _eval_num(node: Node, ctx: Context) -> Value:
    v = Value(TEXT, node.v)
    if node.dec is not None:
        v._dec_val = node.dec
    return v


def _eval_text(node: Node, ctx: Context) -> Value:
    # An ASCII literal needs no validation (and Value.text's isinstance + validate
    # call cost as much as building the Value, per evaluation); anything
    # else, including a hand-built node carrying something that is not a str, takes
    # the validating constructor as before.
    v = node.v
    if type(v) is str and v.isascii():
        return Value(TEXT, v)
    return Value.text(v)


def _eval_bool(node: Node, ctx: Context) -> Value:
    return Value.bool(node.v)


def _eval_null(node: Node, ctx: Context) -> Value:
    return Value.null()


def _eval_var(node: Node, ctx: Context) -> Value:
    v = ctx.lookup(node.name)
    if v is None:
        fail('E_UNDEF_VAR', f'undefined variable {node.name}', node.pos)
    return v


def _eval_index(node: Node, ctx: Context) -> Value:
    obj_node = node.obj
    if obj_node.t == 'var':
        obj = ctx.lookup(obj_node.name)
        if obj is None:
            fail('E_UNDEF_VAR', f'undefined variable {obj_node.name}', obj_node.pos)
    else:
        obj = eval_node(obj_node, ctx)

    # The slot cache is keyed on the record's shape alone, so it may only
    # answer for a literal key: a computed key can differ at every read
    # (and must be evaluated, errors included).
    literal = node.idx.t == 'text'
    if literal:
        cached = node._cached_slot
        if cached is not None and obj.shape is cached[0]:
            return obj.storage[cached[1]]
        key = node.idx.v
    else:
        key = eval_node(node.idx, ctx).as_text(node.idx.pos)
    if obj.shape is not None:
        index = obj.shape.key_map.get(key)
        if index is not None:
            if literal:
                node._cached_slot = (obj.shape, index)
            return obj.storage[index]
        fail('E_NO_KEY', f'no key "{key}"', node.pos)

    child = obj.get(key)
    if child is None:
        fail('E_NO_KEY', f'no key "{key}"', node.pos)
    return child


def _eval_seq(node: Node, ctx: Context) -> Value:
    last = None
    for item in node.items:
        last = eval_node(item, ctx)
    return last


def _eval_call(node: Node, ctx: Context) -> Value:
    args = Args(node, ctx)
    if not node.spec.lazy:
        # Strict: every argument evaluated once, left to right, before the body.
        for i in range(len(node.args)):
            args.val(i)
    return node.spec.fn(args, ctx)


def _eval_unknown(node: Node, ctx: Context) -> Value:
    fail('E_SYNTAX', f'cannot evaluate node {node.t}', node.pos)


def _eval_list(node: Node, ctx: Context) -> Value:
    """§5.9 — a value with children and no scalar contributes its children's
    values; anything else contributes itself. Keys are always renumbered from 1.
    """
    values = []
    for item in node.items:
        v = eval_node(item, ctx)
        if v.kind == NONE and v.size() > 0:
            # Checked before the children are copied, so a doubling never builds
            # what it refuses (SPEC 6.4).
            check_collection(len(values) + v.size(), node.pos)
            # Keep list literals on the flat storage path.  Calling set() for
            # each element first creates numeric keys and then has to retain an
            # ordered dict; the evaluator already knows the final list length,
            # so a single packed list is both cheaper and closer to Lisp's
            # vector-backed make-list-value.
            if v.storage is not None:
                children = v.storage
            elif v.children:
                children = v.children.values()
            else:
                children = ()
            # Held one level down, and a value past the cap is refused here, at
            # the node that built it (SPEC 3.4), not at 0:0 by whatever walks it.
            values.extend(child.clone(node.pos, 2) for child in children)
        else:
            values.append(v.clone(node.pos, 2))
        check_collection(len(values), node.pos)
    return Value._list_owned(values)


def _eval_unary(node: Node, ctx: Context) -> Value:
    v = eval_node(node.x, ctx)
    if node.op == 'NOT':
        return Value.bool(not v.as_bool(node.x.pos))
    return Value._num_owned(D.negate(v.as_decimal(node.x.pos)))


def _eval_binary(node: Node, ctx: Context) -> Value:
    op = node.op

    # Short-circuit before either side is touched (§5.5, §5.6).
    if op == 'AND' or op == 'OR':
        left = eval_node(node.l, ctx).as_bool(node.l.pos)
        if op == 'AND' and not left:
            return Value.bool(False)
        if op == 'OR' and left:
            return Value.bool(True)
        return Value.bool(eval_node(node.r, ctx).as_bool(node.r.pos))

    # ?? falls back on NULL, ??? on any vacuous value; both on a missing key
    # or name.
    if op == '??' or op == '???':
        try:
            l = eval_node(node.l, ctx)
        except SelError as e:
            if e.code in ('E_NO_KEY', 'E_UNDEF_VAR'):
                return eval_node(node.r, ctx)
            raise
        if l.is_null() if op == '??' else l.is_vacuous():
            return eval_node(node.r, ctx)
        return l

    l = eval_node(node.l, ctx)
    const = node.const_value
    r = const.value if const is not None else eval_node(node.r, ctx)
    lp, rp = node.l.pos, node.r.pos

    # Each coerced operand is bound to a named local before it is used, and in
    # source order. SEL evaluates strictly left to right (§6.2) and which
    # operand's position an error reports is observable: the C++ host once
    # reported the right one for `TRUE $== FALSE` because argument evaluation
    # order is unspecified there. Python evaluates arguments left to right, so
    # this is not strictly required here — it is written this way so the file
    # can be read against the other four without a footnote.
    if op == '+':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value._num_owned(D.add(a, b, node.pos))
    if op == '-':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value._num_owned(D.sub(a, b, node.pos))
    if op == '*':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value._num_owned(D.mul(a, b, node.pos))
    if op == '/':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value._num_owned(D.div(a, b, node.pos))
    if op == '%':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value._num_owned(D.mod(a, b, node.pos))

    if op == '&':
        return _concat(l, r, lp, rp, node.pos)

    if op in ('==', '!=', '<', '<=', '>', '>='):
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        if a.scale == b.scale:
            left = -a.digits if a.neg else a.digits
            right = -b.digits if b.neg else b.digits
            if op == '==':
                return Value.bool(left == right)
            if op == '!=':
                return Value.bool(left != right)
            if op == '<':
                return Value.bool(left < right)
            if op == '<=':
                return Value.bool(left <= right)
            if op == '>':
                return Value.bool(left > right)
            return Value.bool(left >= right)
        return Value.bool(_compare_result(op, D.cmp(a, b), node.pos))

    if op == '$==':
        if l.kind == TEXT and r.kind == TEXT and not l.children and not r.children:
            return Value.bool(l.scalar == r.scalar)
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return Value.bool(a == b)
    if op == '$!=':
        if l.kind == TEXT and r.kind == TEXT and not l.children and not r.children:
            return Value.bool(l.scalar != r.scalar)
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return Value.bool(a != b)
    if op in ('$<', '$<=', '$>', '$>='):
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return Value.bool(_compare_result(op[1:], bytes_compare(a, b), node.pos))

    if op == 'EQL':
        return Value.bool(l.eql(r, node.pos))
    if op == 'IN':
        # A list of text/number literals with a membership set: a needle that is a
        # plain text value (no children) is EQL to an element exactly when their
        # texts are equal (Value._eql_at), so a set lookup answers as the scan does.
        if const is not None and const.texts is not None and l.kind == TEXT and l.size() == 0:
            return Value.bool(l.scalar in const.texts)
        return Value.bool(_is_in(l, r))

    if op == 'XOR':
        a = l.as_bool(lp); b = r.as_bool(rp)
        return Value.bool(a != b)

    if op in ('BAND', 'BOR', 'BXOR'):
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return _bitwise(op, a, b, node.pos)

    fail('E_SYNTAX', f'unknown operator {op}', node.pos)


def _eval_and(node: Node, ctx: Context) -> Value:
    if not eval_node(node.l, ctx).as_bool(node.l.pos):
        return Value.bool(False)
    return Value.bool(eval_node(node.r, ctx).as_bool(node.r.pos))


def _eval_or(node: Node, ctx: Context) -> Value:
    if eval_node(node.l, ctx).as_bool(node.l.pos):
        return Value.bool(True)
    return Value.bool(eval_node(node.r, ctx).as_bool(node.r.pos))


def _eval_compare(node: Node, ctx: Context) -> Value:
    """The six numeric comparisons, exactly as _eval_binary runs them (only IN
    carries a const_value, so the right operand is always evaluated)."""
    l = eval_node(node.l, ctx)
    r = eval_node(node.r, ctx)
    a = l.as_decimal(node.l.pos); b = r.as_decimal(node.r.pos)
    op = node.op
    if a.scale == b.scale:
        left = -a.digits if a.neg else a.digits
        right = -b.digits if b.neg else b.digits
        if op == '>':
            return Value.bool(left > right)
        if op == '<':
            return Value.bool(left < right)
        if op == '==':
            return Value.bool(left == right)
        if op == '!=':
            return Value.bool(left != right)
        if op == '>=':
            return Value.bool(left >= right)
        return Value.bool(left <= right)
    return Value.bool(_compare_result(op, D.cmp(a, b), node.pos))


def _eval_text_equal(node: Node, ctx: Context) -> Value:
    """$== and $!=, as _eval_binary runs them."""
    l = eval_node(node.l, ctx)
    r = eval_node(node.r, ctx)
    if l.kind == TEXT and r.kind == TEXT and not l.children and not r.children:
        same = l.scalar == r.scalar
    else:
        a = l.as_bytes(node.l.pos); b = r.as_bytes(node.r.pos)
        same = a == b
    return Value.bool(same if node.op == '$==' else not same)


_BINARY = {
    'AND': _eval_and, 'OR': _eval_or, '$==': _eval_text_equal, '$!=': _eval_text_equal,
    **{op: _eval_compare for op in ('==', '!=', '<', '<=', '>', '>=')},
}


def _compare_result(op: str, c: int, pos: Pos) -> bool:
    """The six comparisons, and nothing else.

    The last branch was `return c >= 0`, which answered for every operator it
    did not name -- so a comparison operator added to the parser and forgotten
    here evaluated as `>=` and reported nothing. Unreachable today, since the
    caller only reaches this with the six, and that is the point of saying so
    out loud rather than answering.
    """
    if op == '==':
        return c == 0
    if op == '!=':
        return c != 0
    if op == '<':
        return c < 0
    if op == '<=':
        return c <= 0
    if op == '>':
        return c > 0
    if op == '>=':
        return c >= 0
    fail('E_SYNTAX', f'unknown comparison operator {op}', pos)


def _concat(l: Value, r: Value, lp: Pos, rp: Pos, pos: Pos) -> Value:
    """TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2)."""
    lv = l.scalar_source(lp)
    rv = r.scalar_source(rp)
    if lv.kind == BOOL:
        fail('E_NOT_TEXT', 'cannot concatenate a boolean', lp)
    if rv.kind == BOOL:
        fail('E_NOT_TEXT', 'cannot concatenate a boolean', rp)
    if lv.kind == TEXT and rv.kind == TEXT:
        # The length the result would have is refused before it is built (SPEC 6.4).
        check_text(len(lv.scalar) + len(rv.scalar), pos)
        return Value.text(lv.scalar + rv.scalar)
    a = l.as_bytes(lp)
    b = r.as_bytes(rp)
    check_text(len(a) + len(b), pos)
    return Value.bin(a + b)


def _is_in(needle: Value, hay: Value) -> bool:
    if hay.size() == 0:
        return hay.eql(needle)
    return any(child.eql(needle) for child in hay.values())


_BITWISE_INT_MIN = 64


def _bitwise(op: str, a: bytes, b: bytes, pos: Pos) -> Value:
    if len(a) != len(b):
        fail('E_LEN_MISMATCH',
             f'{op} needs operands of equal length ({len(a)} vs {len(b)})', pos)
    n = len(a)
    if n >= _BITWISE_INT_MIN:
        # Two integers and one machine-word operation each, instead of a generator
        # step per byte: the same bytes, at memory speed. Small operands
        # keep the byte loop, which is cheaper than building the integers.
        x = int.from_bytes(a, 'big')
        y = int.from_bytes(b, 'big')
        r = x & y if op == 'BAND' else (x | y if op == 'BOR' else x ^ y)
        return Value.bin(r.to_bytes(n, 'big'))
    if op == 'BAND':
        return Value.bin(bytes(x & y for x, y in zip(a, b)))
    if op == 'BOR':
        return Value.bin(bytes(x | y for x, y in zip(a, b)))
    return Value.bin(bytes(x ^ y for x, y in zip(a, b)))


# --- assignment -------------------------------------------------------------

_COMPOUND = {'+=': '+', '-=': '-', '*=': '*', '/=': '/', '%=': '%', '&=': '&'}


def _eval_assign(node: Node, ctx: Context) -> Value:
    path = _resolve_target(node.target, ctx)
    key = path[-1]

    if node.op == '=':
        # The value lands len(path) levels down, so its own nesting starts there
        # and the sum is what the cap applies to (SPEC 6.4), reported at the
        # target -- not just the path, which is all this used to count.
        value = eval_node(node.value, ctx).clone(node.target.pos, len(path))
    else:
        current = _walk_create(ctx, path, len(path) - 1).get(key)
        if current is None:
            fail('E_UNDEF_VAR', f'{node.op} needs an existing target', node.target.pos)
        rhs = eval_node(node.value, ctx)
        bin_op = _COMPOUND[node.op]
        tp, vp = node.target.pos, node.value.pos
        if bin_op == '&':
            value = _concat(current, rhs, tp, vp, node.pos)
        else:
            a = current.as_decimal(tp)
            b = rhs.as_decimal(vp)
            if bin_op == '+':
                res = D.add(a, b, node.pos)
            elif bin_op == '-':
                res = D.sub(a, b, node.pos)
            elif bin_op == '*':
                res = D.mul(a, b, node.pos)
            elif bin_op == '/':
                res = D.div(a, b, node.pos)
            else:
                res = D.mod(a, b, node.pos)
            value = Value._num_owned(res)

    # Re-derived after the right-hand side ran, which may have replaced or
    # removed any level along the path.
    _walk_create(ctx, path, len(path) - 1).set(key, value)
    return value


def _walk_create(ctx: Context, path: list[str], upto: int) -> Value:
    """Walks from the root along `path`, creating any level that is missing, and
    returns the value at the end. Re-derived rather than remembered — see
    _resolve_target.
    """
    cur = ctx.root
    for i in range(upto):
        nxt = cur.get(path[i])
        if nxt is None:
            nxt = Value.none()
            cur.set(path[i], nxt)
        cur = nxt
    return cur


def _resolve_target(target: Node, ctx: Context) -> list[str]:
    """Walks the target chain and returns the full key path, evaluating each
    index expression exactly once, left to right, and creating each intermediate
    level as it goes — so `A[COUNT(A)] = 1` sees the A the walk just created.

    A path rather than a live container reference (§5.7). The right-hand side may
    replace any level the walk just found; the assignment then lands in the tree
    that exists afterwards, rather than in an object that has been detached from
    it and which nothing can ever read.
    """
    chain: list[Node] = []
    n = target
    while n.t == 'index':
        chain.insert(0, n.idx)
        n = n.obj

    if ctx.is_bound(n.name):
        fail('E_BAD_ASSIGN',
             f'{n.name} is an aggregate binder and cannot be assigned', target.pos)
    # The chain was walked iteratively, which is why nothing has counted it yet:
    # `A[1][2][3]` is a chain of index nodes, not a nesting of them, so neither
    # the parser's depth nor the evaluator's ever sees it -- and the value it is
    # about to build is one level deeper than the chain is long. Uncounted, that
    # built a value deeper than clone, eql and dump can walk, so the assignment
    # succeeded and reading the result back afterwards failed.
    if len(chain) + 1 > MAX_DEPTH:
        fail('E_DEPTH', 'value nested too deeply', target.pos)
    path = [n.name]
    if not chain:
        return path

    if ctx.root.get(n.name) is None:
        ctx.root.set(n.name, Value.none())

    for i in range(len(chain) - 1):
        k = eval_node(chain[i], ctx).as_text(chain[i].pos)
        cur = _walk_create(ctx, path, len(path))
        if cur.get(k) is None:
            cur.set(k, Value.none())
        path.append(k)
    last = chain[-1]
    path.append(eval_node(last, ctx).as_text(last.pos))
    return path


_EVAL = {
    'num': _eval_num, 'text': _eval_text, 'bool': _eval_bool, 'null': _eval_null,
    'var': _eval_var, 'index': _eval_index, 'seq': _eval_seq, 'list': _eval_list,
    'un': _eval_unary, 'bin': _eval_binary, 'assign': _eval_assign, 'call': _eval_call,
}


def handler_for(node: Node) -> Any:
    """The function eval_node runs NODE with, for the optimiser to stamp on the
    physical tree: a planned node's plan, or its type's entry in _EVAL."""
    if node.math_plan is not None:
        return _eval_planned
    if node.t == 'bin':
        return _BINARY.get(node.op, _eval_binary)
    return _EVAL.get(node.t, _eval_unknown)
