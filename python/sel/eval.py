"""The evaluator, and the argument framework built-ins are written against.

Nothing here catches a SelError. An error surfaces from the innermost node that
failed, carrying that node's position, and no layer rewrites it.
"""

from __future__ import annotations

from typing import Any, Callable

from . import decimal as D
from .errors import Pos, fail
from .parser import Node
from .utf8 import bytes_compare
from .value import BIN, BOOL, NONE, TEXT, Value

MAX_DEPTH = 200


class Context:
    __slots__ = ('root', 'frames', 'depth')

    def __init__(self, root: Value | None = None) -> None:
        self.root = root if root is not None else Value.none()
        self.frames: list[dict[str, Value]] = []   # aggregate binders
        self.depth = 0

    def lookup(self, name: str) -> Value | None:
        for frame in reversed(self.frames):
            v = frame.get(name)
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

    __slots__ = ('nodes', 'name', 'pos', 'ctx', '_vals')

    def __init__(self, node: Node, ctx: Context) -> None:
        self.nodes = node.args
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


# --- evaluation -------------------------------------------------------------

def eval_node(node: Node, ctx: Context) -> Value:
    ctx.depth += 1
    if ctx.depth > MAX_DEPTH:
        ctx.depth -= 1
        fail('E_DEPTH', 'evaluation nested too deeply', node.pos)
    try:
        return _dispatch(node, ctx)
    finally:
        ctx.depth -= 1


def _dispatch(node: Node, ctx: Context) -> Value:
    t = node.t

    if t == 'num':
        return Value.text(node.v)        # canonicalised by the parser
    if t == 'text':
        return Value.text(node.v)
    if t == 'bool':
        return Value.bool(node.v)

    if t == 'var':
        v = ctx.lookup(node.name)
        if v is None:
            fail('E_UNDEF_VAR', f'undefined variable {node.name}', node.pos)
        return v

    if t == 'index':
        obj = eval_node(node.obj, ctx)
        key = eval_node(node.idx, ctx).as_text(node.idx.pos)
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
    out = Value.none()
    n = 0
    for item in node.items:
        v = eval_node(item, ctx)
        if v.kind == NONE and v.size() > 0:
            for child in v.values():
                n += 1
                out.set(str(n), child.clone())
        else:
            n += 1
            out.set(str(n), v.clone())
    return out


def _eval_unary(node: Node, ctx: Context) -> Value:
    v = eval_node(node.x, ctx)
    if node.op == 'NOT':
        return Value.bool(not v.as_bool(node.x.pos))
    return Value.num(D.negate(v.as_decimal(node.x.pos)))


def _eval_binary(node: Node, ctx: Context) -> Value:
    op = node.op

    # Short-circuit before either side is touched (§5.5).
    if op == 'AND' or op == 'OR':
        left = eval_node(node.l, ctx).as_bool(node.l.pos)
        if op == 'AND' and not left:
            return Value.bool(False)
        if op == 'OR' and left:
            return Value.bool(True)
        return Value.bool(eval_node(node.r, ctx).as_bool(node.r.pos))

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
        return Value.num(D.add(a, b))
    if op == '-':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.sub(a, b))
    if op == '*':
        a = l.as_decimal(lp); b = r.as_decimal(rp)
        return Value.num(D.mul(a, b))
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
        return Value.bool(_compare_result(op, D.cmp(a, b)))

    if op in ('$==', '$!=', '$<', '$<=', '$>', '$>='):
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return Value.bool(_compare_result(op[1:], bytes_compare(a, b)))

    if op == 'EQL':
        return Value.bool(l.eql(r))
    if op == 'IN':
        return Value.bool(_is_in(l, r))

    if op == 'XOR':
        a = l.as_bool(lp); b = r.as_bool(rp)
        return Value.bool(a != b)

    if op in ('BAND', 'BOR', 'BXOR'):
        a = l.as_bytes(lp); b = r.as_bytes(rp)
        return _bitwise(op, a, b, node.pos)

    fail('E_SYNTAX', f'unknown operator {op}', node.pos)


def _compare_result(op: str, c: int) -> bool:
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
    return c >= 0


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
        value = eval_node(node.value, ctx).clone()
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
                res = D.add(a, b)
            elif bin_op == '-':
                res = D.sub(a, b)
            elif bin_op == '*':
                res = D.mul(a, b)
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
