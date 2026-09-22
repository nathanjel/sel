"""The evaluator, and the argument framework built-ins are written against.

Nothing here catches a SelError. An error surfaces from the innermost node that
failed, carrying that node's position, and no layer rewrites it.
"""

from __future__ import annotations

from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH, Pos, SelError, fail
from .math_plan import MathPlan, OpCode
from .parser import Node
from .utf8 import bytes_compare
from .value import BOOL, NONE, TEXT, Value


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


class Context:
    __slots__ = ('root', 'frames', 'depth', 'join_prefilter')

    def __init__(self, root: Value | None = None) -> None:
        self.root = root if root is not None else Value.none()
        self.frames: list[dict[str, Value]] = []   # aggregate binders
        self.depth = 0
        # A FILTER whose source is a LINK hands the join its leading conjuncts
        # here, for the join to pre-apply to left rows where that is provably
        # the same as filtering the joined rows (builtins/structure.py, _link).
        self.join_prefilter = None

    def lookup(self, name: str) -> Value | None:
        frames = self.frames
        n = len(frames)
        if n == 1:
            v = frames[0].get(name)
            if v is not None:
                return v
        elif n > 1:
            for i in range(n - 1, -1, -1):
                v = frames[i].get(name)
                if v is not None:
                    return v
        return self.root.get(name)

    def is_bound(self, name: str) -> bool:
        return any(name in frame for frame in self.frames)

    def push_frame(self, mapping: dict[str, Value]) -> None:
        self.frames.append(mapping)

    def pop_frame(self) -> None:
        self.frames.pop()


# --- arguments --------------------------------------------------------------

class Args:
    """Wraps the flattened argument vector. Values are evaluated at most once,
    so a built-in body can read the same argument repeatedly without thinking
    about it, and typed accessors report failures against the argument's own
    position.
    """

    __slots__ = ('nodes', 'name', 'pos', 'ctx', '_vals', 'record_shape')

    def __init__(self, node: Node, ctx: Context) -> None:
        self.nodes = node.args
        self.record_shape = node.record_shape
        self.name = node.name
        self.pos = node.pos
        self.ctx = ctx
        self._vals: list[Value | None] = [None] * len(node.args)

    def count(self) -> int:
        return len(self.nodes)

    def node(self, i: int) -> Node:
        return self.nodes[i]

    def pos_of(self, i: int) -> Pos:
        return self.nodes[i].pos

    def val(self, i: int) -> Value:
        if self._vals[i] is None:
            self._vals[i] = eval_node(self.nodes[i], self.ctx)
        return self._vals[i]

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

    def non_neg_int(self, i: int) -> int:
        n = self.int(i)
        if n < 0:
            fail('E_RANGE', f'{self.name} argument {i + 1} must not be negative',
                 self.pos_of(i))
        return n

    def symbol(self, i: int) -> str:
        """Requires the argument to be a bare identifier in the source — the AST
        shape check that gives aggregates their three-argument binder form.
        """
        n = self.nodes[i]
        if n.t != 'var' or n.grouped:
            fail('E_EXPECT_SYMBOL', f'{self.name} argument {i + 1} must be a plain name',
                 n.pos)
        return n.name

    def is_symbol(self, i: int) -> bool:
        n = self.nodes[i]
        return n.t == 'var' and not n.grouped


# --- evaluation -------------------------------------------------------------

def eval_node(node: Node, ctx: Context) -> Value:
    ctx.depth += 1
    if ctx.depth > MAX_DEPTH:
        ctx.depth -= 1
        fail('E_DEPTH', 'evaluation nested too deeply', node.pos)
    try:
        if node.math_plan is not None:
            return _eval_math_plan(node.math_plan, ctx)
        return _dispatch(node, ctx)
    finally:
        ctx.depth -= 1


def _eval_math_plan(plan: MathPlan, ctx: Context) -> Value:
    scratchpad: list[Any] = [None] * plan.scratchpad_size
    for step in plan.steps:
        op = step.op
        if op == _LOAD_VAR:
            val = ctx.lookup(step.name)
            if val is None:
                fail('E_UNDEF_VAR', f'undefined variable {step.name}', step.pos)
            scratchpad[step.dst] = val.as_decimal(step.pos)
        elif op == _LOAD_CONST:
            scratchpad[step.dst] = step.const_val
        elif op == _LOAD_LEAF:
            val = eval_node(step.leaf_node, ctx)
            scratchpad[step.dst] = val.as_decimal(step.leaf_node.pos)
        elif op == _ADD:
            scratchpad[step.dst] = D.add(scratchpad[step.src1], scratchpad[step.src2], step.pos)
        elif op == _SUB:
            scratchpad[step.dst] = D.sub(scratchpad[step.src1], scratchpad[step.src2], step.pos)
        elif op == _MUL:
            scratchpad[step.dst] = D.mul(scratchpad[step.src1], scratchpad[step.src2], step.pos)
        elif op == _DIV:
            scratchpad[step.dst] = D.div(scratchpad[step.src1], scratchpad[step.src2], step.pos)
        elif op == _MOD:
            scratchpad[step.dst] = D.mod(scratchpad[step.src1], scratchpad[step.src2], step.pos)
        elif op == _NEG:
            scratchpad[step.dst] = D.negate(scratchpad[step.src1])
        elif op == _ABS:
            scratchpad[step.dst] = D.abs_(scratchpad[step.src1])
        elif op == _SIGN:
            s = D.sign(scratchpad[step.src1])
            scratchpad[step.dst] = D.make(s < 0, abs(s), 0)
        elif op == _CEIL:
            scratchpad[step.dst] = D.ceil(scratchpad[step.src1])
        elif op == _FLOOR:
            scratchpad[step.dst] = D.floor(scratchpad[step.src1])
        elif op == _TRUNC:
            scratchpad[step.dst] = D.trunc(scratchpad[step.src1])
        elif op == _ROUND:
            d2 = scratchpad[step.src2]
            if not D.is_integer(d2):
                fail('E_NOT_INT', 'ROUND argument 2 must be a whole number', step.aux_pos)
            n = D.to_safe_int(d2)
            if n < 0:
                fail('E_RANGE', 'ROUND argument 2 must not be negative', step.aux_pos)
            if n > 1000000:
                fail('E_RANGE', f'ROUND scale {n} exceeds the maximum of 1000000', step.aux_pos)
            scratchpad[step.dst] = D.round(scratchpad[step.src1], n, step.pos)
        elif op == _POWER:
            d2 = scratchpad[step.src2]
            if not D.is_integer(d2):
                fail('E_NOT_INT', 'POWER argument 2 must be a whole number', step.aux_pos)
            n = D.to_safe_int(d2)
            if n < 0:
                fail('E_RANGE', 'POWER argument 2 must not be negative', step.aux_pos)
            if n > 100000:
                fail('E_RANGE', f'POWER exponent {n} exceeds the maximum of 100000', step.aux_pos)
            scratchpad[step.dst] = D.power(scratchpad[step.src1], n, step.pos)
        elif op == _MIN:
            a = scratchpad[step.src1]
            b = scratchpad[step.src2]
            scratchpad[step.dst] = b if D.cmp(b, a) < 0 else a
        elif op == _MAX:
            a = scratchpad[step.src1]
            b = scratchpad[step.src2]
            scratchpad[step.dst] = b if D.cmp(b, a) > 0 else a
    return Value.num(scratchpad[plan.output_slot])


def _dispatch(node: Node, ctx: Context) -> Value:
    t = node.t

    if t == 'num':
        v = Value(TEXT, node.v)
        if node.dec is not None:
            v._dec_val = node.dec
        return v
    if t == 'text':
        return Value.text(node.v)
    if t == 'bool':
        return Value.bool(node.v)
    if t == 'null':
        return Value.null()

    if t == 'var':
        v = ctx.lookup(node.name)
        if v is None:
            fail('E_UNDEF_VAR', f'undefined variable {node.name}', node.pos)
        return v

    if t == 'index':
        obj_node = node.obj
        if obj_node.t == 'var':
            obj = ctx.lookup(obj_node.name)
            if obj is None:
                fail('E_UNDEF_VAR', f'undefined variable {obj_node.name}', obj_node.pos)
        else:
            obj = eval_node(obj_node, ctx)

        cached = node._cached_slot
        if cached is not None and obj.shape is cached[0]:
            return obj.storage[cached[1]]

        key = node.idx.v if node.idx.t == 'text' else eval_node(node.idx, ctx).as_text(node.idx.pos)
        if obj.shape is not None:
            index = obj.shape.key_map.get(key)
            if index is not None:
                node._cached_slot = (obj.shape, index)
                return obj.storage[index]
            fail('E_NO_KEY', f'no key "{key}"', node.pos)

        child = obj.get(key)
        if child is None:
            fail('E_NO_KEY', f'no key "{key}"', node.pos)
        return child

    if t == 'seq':
        last = None
        for item in node.items:
            last = eval_node(item, ctx)
        return last

    if t == 'list':
        return _eval_list(node, ctx)
    if t == 'un':
        return _eval_unary(node, ctx)
    if t == 'bin':
        return _eval_binary(node, ctx)
    if t == 'assign':
        return _eval_assign(node, ctx)

    if t == 'call':
        args = Args(node, ctx)
        if not node.spec.lazy:
            # Strict: every argument evaluated once, left to right, before the body.
            for i in range(len(node.args)):
                args.val(i)
        return node.spec.fn(args, ctx)

    fail('E_SYNTAX', f'cannot evaluate node {t}', node.pos)


def _eval_list(node: Node, ctx: Context) -> Value:
    """§5.9 — a value with children and no scalar contributes its children's
    values; anything else contributes itself. Keys are always renumbered from 1.
    """
    values = []
    for item in node.items:
        v = eval_node(item, ctx)
        if v.kind == NONE and v.size() > 0:
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
            values.extend(child.clone() for child in children)
        else:
            values.append(v.clone())
    return Value.list(values)


def _eval_unary(node: Node, ctx: Context) -> Value:
    v = eval_node(node.x, ctx)
    if node.op == 'NOT':
        return Value.bool(not v.as_bool(node.x.pos))
    return Value.num(D.negate(v.as_decimal(node.x.pos)))


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

    if op == '??':
        try:
            l = eval_node(node.l, ctx)
        except SelError as e:
            if e.code in ('E_NO_KEY', 'E_UNDEF_VAR'):
                return eval_node(node.r, ctx)
            raise
        if l.is_null():
            return eval_node(node.r, ctx)
        return l

    if op == '???':
        try:
            l = eval_node(node.l, ctx)
        except SelError as e:
            if e.code in ('E_NO_KEY', 'E_UNDEF_VAR'):
                return eval_node(node.r, ctx)
            raise
        if l.is_vacuous():
            return eval_node(node.r, ctx)
        return l

    l = eval_node(node.l, ctx)
    r = eval_node(node.r, ctx)
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
        return Value.num(D.add(a, b, node.pos))
    if op == '-':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.sub(a, b, node.pos))
    if op == '*':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.mul(a, b, node.pos))
    if op == '/':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.div(a, b, node.pos))
    if op == '%':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.mod(a, b, node.pos))

    if op == '&':
        return _concat(l, r, lp, rp)

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
        return Value.bool(_is_in(l, r))

    if op == 'XOR':
        a = l.as_bool(lp); b = r.as_bool(rp)
        return Value.bool(a != b)

    if op in ('BAND', 'BOR', 'BXOR'):
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return _bitwise(op, a, b, node.pos)

    fail('E_SYNTAX', f'unknown operator {op}', node.pos)


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


def _concat(l: Value, r: Value, lp: Pos, rp: Pos) -> Value:
    """TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2)."""
    lv = l.scalar_source(lp)
    rv = r.scalar_source(rp)
    if lv.kind == BOOL:
        fail('E_NOT_TEXT', 'cannot concatenate a boolean', lp)
    if rv.kind == BOOL:
        fail('E_NOT_TEXT', 'cannot concatenate a boolean', rp)
    if lv.kind == TEXT and rv.kind == TEXT:
        return Value.text(lv.scalar + rv.scalar)
    a = l.as_bytes(lp)
    b = r.as_bytes(rp)
    return Value.bin(a + b)


def _is_in(needle: Value, hay: Value) -> bool:
    if hay.size() == 0:
        return hay.eql(needle)
    return any(child.eql(needle) for child in hay.values())


def _bitwise(op: str, a: bytes, b: bytes, pos: Pos) -> Value:
    if len(a) != len(b):
        fail('E_LEN_MISMATCH',
             f'{op} needs operands of equal length ({len(a)} vs {len(b)})', pos)
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
        value = eval_node(node.value, ctx).clone(node.pos)
    else:
        current = _walk_create(ctx, path, len(path) - 1).get(key)
        if current is None:
            fail('E_UNDEF_VAR', f'{node.op} needs an existing target', node.target.pos)
        rhs = eval_node(node.value, ctx)
        bin_op = _COMPOUND[node.op]
        tp, vp = node.target.pos, node.value.pos
        if bin_op == '&':
            value = _concat(current, rhs, tp, vp)
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
            value = Value.num(res)

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
