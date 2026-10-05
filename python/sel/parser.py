"""Parser: precedence climbing, the shared design note.

Every host parses the same way, with the same function names:
`parse_program` -> `parse_sequence` -> `parse_list` -> `parse_term` ->
`parse_prefix` -> `parse_postfix` -> `parse_primary`, and the sixteen levels of
spec/SPEC.md §5 as the binding-power table below rather than sixteen functions
(docs/contributing.md, "Where everything lives", says where a host departs from
the names).

Why not one method per production of spec/grammar.md, which is how the parsers
were first written: that shape costs **35 stack frames per level of parenthesis
nesting** (measured on a transcribed parser: 44 frames at one paren, 1409 at
forty, linear at 35.0). E_DEPTH trips at 100 nested parens, so it needs ~3500
frames where Python's default recursion limit is 1000, and a host would raise
RecursionError where the others raise E_DEPTH. Raising sys.setrecursionlimit
would paper over that. Precedence climbing removes it: one level of nesting
costs six frames instead of thirty-five.

The table is written to be transcribed rather than to be clever, and anything
specific to one language is kept out of the loop.

Five things this has to reproduce exactly, none of which the type checker will
catch for you — they all produce a *valid parse of the wrong tree*:

 1. NOT is a LOOSE prefix operator (bp 7 — looser than comparison, tighter than
    AND) while unary `-` is a TIGHT one (bp 16). Textbook precedence climbing
    puts every prefix operator in parse_primary, at the tightest end, which
    would make `NOT a == b` parse as `(NOT a) == b`. Prefix operators get their
    own binding power here, and `parse_prefix` refuses one whose binding power
    is looser than the position allows — which is what makes `a == NOT b` and
    `-NOT x` E_RESERVED, as the grammar makes them.
 2. Comparison is NON-associative. Its right side is parsed one level tighter
    and a second comparison operator afterwards is E_SYNTAX, reported at that
    second operator.
 3. `,` and `;` build N-ary list/seq nodes, not left-leaning binary trees.
    dependencies() walks `items`, and parse_call flattens a top-level `list`
    into an argument vector; binary nodes would break both.
 4. The `grouped` flag marks a parenthesised node. It is what makes `F((1,2))`
    pass one list rather than two arguments, and `(A) = 1` an E_BAD_ASSIGN.
 5. Node positions: `bin` and `un` take the operator token's, `list`/`seq` take
    items[0]'s, `assign` takes the target's. Every pinned position in
    conformance/12-misuse.selt depends on these.

Depth is entered in parse_sequence and parse_primary — the same two places the
other hosts use, so the trip point stays two per paren and lim.parse-depth still
reports E_DEPTH at 1:101 — plus prefix operators, which are counted for the
reason given on parse_prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH, Pos, fail
from .lexer import RESERVED, Token, tokenize
from .registry import INF, REGEX_FLAG_AT, Spec, is_host_function, lookup

from .opinfo import ASSIGN_OPS, BP_ASSIGN, BP_NEG, BP_NOT, INFIX, OPS, PREFIX

# spec/SPEC.md §5, as a table: spec/lexicon.json's binding powers (higher binds
# tighter) and associativity, rendered into _lexicon.py. `;` and `,` are not
# rows: they build N-ary nodes and are parsed by their own functions
# (parse_sequence, parse_list), not by the climbing loop. NOT and unary minus
# are prefix operators (parse_prefix), at BP_NOT and BP_NEG.
#
# Infix operator -> (binding power, associativity). 'L' left, 'R' right,
# 'N' non-associative. Word operators are lexed as identifiers, so they are
# looked up separately; the binding powers are the same table.
INFIX_OPS: dict[str, tuple[int, str]] = {
    o.token: (o.bp, o.assoc) for o in INFIX.values()
    if not o.word and o.node in ('bin', 'assign')
}
INFIX_WORDS: dict[str, tuple[int, str]] = {
    o.token: (o.bp, o.assoc) for o in INFIX.values()
    if o.word and o.node in ('bin', 'assign')
}

# The prefix and postfix operators are parsed by hand (parse_prefix,
# parse_postfix), so the lexicon is held to what those functions handle: a
# prefix or postfix operator added there and not here fails at import.
if ({(o.token, o.word, o.name, o.bp) for o in PREFIX.values()}
        != {('NOT', True, 'NOT', BP_NOT), ('-', False, 'NEG', BP_NEG)}
        or {o.token for o in OPS if o.fixity == 'postfix'} != {'[', '.>'}):
    raise RuntimeError('parser.py does not parse the prefix/postfix operators of spec/lexicon.json')


def _prepare_record_shape(name, args):
    if name != 'RECORD' or not args or len(args) % 2 or any(n.t != 'text' for n in args[::2]):
        return None
    from .value import _unique_record_shape
    return _unique_record_shape([n.v for n in args[::2]])


@dataclass(slots=True)
class Node:
    t: str
    pos: Pos
    v: Any = None                       # num / text / bool literal
    name: str = ''                      # var, call
    op: str = ''                        # bin, un, assign
    l: Node | None = None               # bin left
    r: Node | None = None               # bin right
    x: Node | None = None               # un operand
    obj: Node | None = None             # index object
    idx: Node | None = None             # index key
    target: Node | None = None          # assign target
    value: Node | None = None           # assign value
    items: list[Node] = field(default_factory=list)     # seq, list
    args: list[Node] = field(default_factory=list)      # call
    spec: Spec | None = None            # call
    grouped: bool = False
    dec: Any = None
    math_plan: Any = None
    _cached_slot: Any = None
    record_shape: Any = None
    # Physical-tree metadata (this host's optimiser stamps it on its own copy,
    # never on the caller's tree): a FILTER body whose result keys nothing
    # observes, so the evaluator's join pre-filter may drop rows below the join
    # (SEL-0050).
    keys_unobserved: bool = False
    # SQL stage 1's memo: found NOT constant at depth 0 with nothing bound (constants.py).
    # Trees are immutable once built, so it never goes stale.
    _not_constant: bool = False
    # Physical-tree metadata: for an `IN` whose right operand is a list of literals,
    # the optimiser's ConstantList (the list's Value, built once, and a set of the
    # texts when every element is text or a number) so the test does not rebuild
    # and clone the list for every row. Private to the node: only `IN`
    # reads it, and it is never returned or stored where a program could reach it.
    const_value: Any = None
    # Physical-tree metadata on a FILTER step: the step after it only reads the
    # kept elements and copies whatever it collects (optimizer.adopts_elements),
    # so FILTER hands them on as they are instead of cloning each one.
    adopt_items: bool = False
    # SQL planner metadata, on the planner's own copy of a `var`: this read is
    # the catalogue's BINDING, reached by unwinding through a helper of the same
    # name (`ORDERS = ORDERS .> DROP(2); ORDERS .> ...`), not a read of that
    # helper -- stage 1 does not inline the helper into it, and it does not keep
    # the helper alive (sql/hybrid.py, _unwind_through_helpers).
    binding: bool = False
    # Optimiser metadata, on its own copy of a pipeline step: how deep the step
    # stands in the tree as written (the outermost step is the call itself), for
    # the rewrite that would deepen a subtree (FILTER fusion). 0 is unknown.
    step_depth: int = 0
    # Physical-tree metadata: the function eval_node runs this node with
    # (eval.handler_for), stamped by the optimiser on the nodes its own copy
    # holds (optimizer.bind_handlers). replaced() does not carry it -- a copy
    # may be turned into another kind of node -- so a copy takes eval_node's
    # generic path; and it is no part of what the node is.
    ev: Any = field(default=None, compare=False, repr=False)

    def replaced(self, **changes) -> 'Node':
        """A shallow copy with FIELD=VALUE overrides, like dataclasses.replace but
        without its per-call field introspection (which was most of the cost of
        an optimiser that copies every node). Unchanged fields, lists included,
        are shared with the original, exactly as replace shares them; a caller
        that means to edit `args` or `items` passes a fresh list. The positional
        order below is the field order: tests/test_perf_node_copy.py checks that
        every declared field is carried, so a field added to Node cannot be
        silently dropped here."""
        c = Node(self.t, self.pos, self.v, self.name, self.op, self.l, self.r, self.x,
                 self.obj, self.idx, self.target, self.value, self.items, self.args,
                 self.spec, self.grouped, self.dec, self.math_plan, self._cached_slot,
                 self.record_shape, self.keys_unobserved, self._not_constant,
                 self.const_value, self.adopt_items, self.binding, self.step_depth)
        for k, v in changes.items():
            setattr(c, k, v)
        return c


def children(n: Node) -> tuple:
    """Every child slot of a node, in one place: a walker that means "all of
    the tree" iterates this, so a field added to Node is added here once. The
    walkers that skip a slot on purpose -- an assignment's target is a path, not
    a read; a binder argument is a name -- say so where they walk."""
    return (*n.args, *n.items, n.l, n.r, n.x, n.obj, n.idx, n.target, n.value)


def may_write(node: Node | None) -> bool:
    """Whether evaluating NODE might write into a value it reaches: it holds an
    assignment, or calls an application's own function (registry.is_host_function),
    which may do anything to a value it is handed. Iterative: a body is as deep as
    the source is long. An assignment is answered at the node, so its target and
    value are not walked."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        t = n.t
        if t == 'assign':
            return True
        if t == 'call':
            if is_host_function(n.name or ''):
                return True
            stack.extend(n.args)
        elif t == 'index':
            stack.append(n.obj)
            stack.append(n.idx)
        elif t == 'bin':
            stack.append(n.l)
            stack.append(n.r)
        elif t == 'un':
            stack.append(n.x)
        elif t in ('seq', 'list'):
            stack.extend(n.items)
    return False


class Parser:
    __slots__ = ('toks', 'i', 'depth')

    def __init__(self, tokens: list[Token]) -> None:
        self.toks = tokens
        self.i = 0
        self.depth = 0

    # --- token helpers --------------------------------------------------------

    def infix_entry(self, t: Token) -> tuple[int, str] | None:
        """The two tables are one lookup.

        Every question about an operator -- what it binds at, how it associates,
        and whether it may follow a comparison -- is answered from here, so
        adding an operator really is adding a row. Asking a separate list
        anywhere would put that claim back in doubt.
        """
        if t.type == 'op':
            return INFIX_OPS.get(t.value)
        if t.type == 'ident':
            return INFIX_WORDS.get(t.value)
        return None

    def peek(self) -> Token:
        return self.toks[self.i]

    def next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def at_op(self, v: str) -> bool:
        t = self.peek()
        return t.type == 'op' and t.value == v


    def at_eof(self) -> bool:
        return self.peek().type == 'eof'

    def expect_op(self, v: str) -> Token:
        if not self.at_op(v):
            t = self.peek()
            fail('E_SYNTAX', f'expected "{v}", got {describe(t)}', t.pos)
        return self.next()

    def enter(self, pos: Pos) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            fail('E_DEPTH', 'expression nested too deeply', pos)

    def leave(self) -> None:
        self.depth -= 1

    # --- entry ----------------------------------------------------------------

    def parse_program(self) -> Node:
        node = self.parse_sequence()
        if not self.at_eof():
            t = self.peek()
            fail('E_SYNTAX', f'unexpected {describe(t)}', t.pos)
        return node

    # sequence = list { ";" list } [ ";" ]
    #
    # The try/finally around the depth counter costs nothing — a failing parse
    # abandons the Parser either way — and every host protects this counter the
    # same way.
    def parse_sequence(self) -> Node:
        start = self.peek()
        self.enter(start.pos)
        try:
            items = [self.parse_list()]
            while self.at_op(';'):
                self.next()
                # A trailing ';' before a closer or end of input is permitted.
                if self.at_eof() or self.at_op(')') or self.at_op(']'):
                    break
                items.append(self.parse_list())
        finally:
            self.leave()
        if len(items) == 1:
            return items[0]
        return Node('seq', items[0].pos, items=items)

    # list = assignment { "," assignment }
    def parse_list(self) -> Node:
        items = [self.parse_term(BP_ASSIGN)]
        while self.at_op(','):
            self.next()
            items.append(self.parse_term(BP_ASSIGN))
        if len(items) == 1:
            return items[0]
        return Node('list', items[0].pos, items=items)

    # --- the precedence-climbing loop ----------------------------------------

    def parse_term(self, min_bp: int) -> Node:
        left = self.parse_prefix(min_bp)

        while True:
            t = self.peek()
            entry = self.infix_entry(t)
            if entry is None:
                return left
            bp, assoc = entry
            if bp < min_bp:
                return left

            self.next()

            if t.value in ASSIGN_OPS:
                # Assignment. The target is validated against the AST shape, not
                # against a value, which is what makes `(A) = 1` a compile error.
                check_target(left, t)
                # Counted, for the same reason parse_prefix counts: the right
                # side recurses without passing through parse_sequence or
                # parse_primary, so uncounted a chain of assignments is bounded
                # by nothing but the host's own stack -- and this host has the
                # least of it, raising RecursionError where four others were
                # still returning E_DEPTH.
                self.enter(t.pos)
                try:
                    value = self.parse_term(bp)
                    left = Node('assign', left.pos, op=t.value, target=left, value=value)
                finally:
                    self.leave()
                continue

            if assoc == 'R':
                # Counted like an assignment (SPEC 6.4): `??` and `???` are the
                # other right-associative operators, and their right side recurses
                # without passing through parse_sequence or parse_primary.
                self.enter(t.pos)
                try:
                    right = self.parse_term(bp)
                    left = Node('bin', t.pos, op=t.value, l=left, r=right)
                finally:
                    self.leave()
                continue

            if assoc == 'N':
                right = self.parse_term(bp + 1)
                after = self.peek()
                after_entry = self.infix_entry(after)
                if after_entry is not None and after_entry[1] == 'N':
                    fail('E_SYNTAX',
                         'comparison operators do not chain — parenthesise, as in '
                         f'(a {t.value} b) AND (b {after.value} c)',
                         after.pos)
                left = Node('bin', t.pos, op=t.value, l=left, r=right)
                continue

            right = self.parse_term(bp + 1)
            left = Node('bin', t.pos, op=t.value, l=left, r=right)

    def parse_prefix(self, min_bp: int) -> Node:
        """NOT and unary minus.

        Each is accepted only where its own binding power reaches: NOT at bp 7
        cannot appear inside a comparison operand (parsed at bp 9), so `a == NOT b`
        falls through to parse_primary, which sees the identifier NOT and raises
        E_RESERVED — the same error the transcribed parsers give, by a different
        route.

        Counted, for the same reason the transcribed parsers now count it: a
        prefix operator recurses without passing through parse_sequence or
        parse_primary, and uncounted it reached the host's own stack limit
        instead of E_DEPTH — a segfault in C++ and a RecursionError here.
        """
        t = self.peek()

        if t.type == 'ident' and t.value == 'NOT' and min_bp <= BP_NOT:
            self.next()
            self.enter(t.pos)
            try:
                return Node('un', t.pos, op='NOT', x=self.parse_term(BP_NOT))
            finally:
                self.leave()

        if t.type == 'op' and t.value == '-' and min_bp <= BP_NEG:
            self.next()
            self.enter(t.pos)
            try:
                return Node('un', t.pos, op='NEG', x=self.parse_term(BP_NEG))
            finally:
                self.leave()

        return self.parse_postfix()

    # postfix = primary { "[" sequence "]" }
    #
    # The bracket counts a level of its own. Without it an index is the one
    # nesting door that recurses from outside parse_primary's enter/leave, so it
    # charged one level per nesting where "(", "f(" and the prefix operators all
    # charge for the frames they actually cost. Five stack frames against one
    # level of the budget put a[a[...]] over CPython's 1000-frame limit before
    # the 200-level guard could fire, and this is the host where that showed:
    # `a[` x198 raised RecursionError through the public CLI while the others
    # still answered. Counting the bracket halves the density to 2.5 frames per
    # level, which puts the guard back in front of the stack at every depth.
    def parse_postfix(self) -> Node:
        node = self.parse_primary()
        while self.at_op('[') or self.at_op('.>'):
            if self.at_op('['):
                br = self.next()
                self.enter(br.pos)
                try:
                    idx = self.parse_sequence()
                    self.expect_op(']')
                    node = Node('index', br.pos, obj=node, idx=idx)
                finally:
                    self.leave()
            else:
                self.next()  # consume '.>'
                node = self.parse_pipe_step(node)
        return node

    def parse_pipe_step(self, left: Node) -> Node:
        t = self.peek()
        if t.type != 'ident' or t.value in ('TRUE', 'FALSE', 'NULL'):
            fail('E_SYNTAX', 'right-hand side of .> must be a function call or function name', t.pos)
        name_tok = self.next()
        args: list[Node] = []
        if self.at_op('('):
            self.next()
            if self.at_op(')'):
                self.next()
            else:
                inner = self.parse_sequence()
                self.expect_op(')')
                args = inner.items if (inner.t == 'list' and not inner.grouped) else [inner]

        spec = lookup(name_tok.value)
        if spec is None:
            fail('E_UNKNOWN_FUNC', f'unknown function {name_tok.value}', name_tok.pos)

        has_placeholder = False
        if not spec.binds and len(args) >= spec.min:
            for i, arg in enumerate(args):
                if arg.t == 'var' and arg.name == '_' and not arg.grouped:
                    args[i] = left
                    has_placeholder = True

        if not has_placeholder:
            args.insert(0, left)

        return _finish_call(name_tok, spec, args)

    def parse_primary(self) -> Node:
        t = self.peek()
        self.enter(t.pos)
        try:
            if t.type == 'num':
                self.next()
                # Canonicalised once, here: the literal 007 is the value 7.
                parsed = D.parse(t.value, t.pos)
                return Node('num', t.pos, v=D.format(parsed), dec=parsed)

            if t.type == 'text':
                self.next()
                return Node('text', t.pos, v=t.value)

            if t.type == 'ident':
                if t.value in ('TRUE', 'FALSE'):
                    self.next()
                    return Node('bool', t.pos, v=(t.value == 'TRUE'))
                if t.value == 'NULL':
                    self.next()
                    return Node('null', t.pos)
                after = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
                if after is not None and after.type == 'op' and after.value == '(':
                    return self.parse_call()
                if t.value in RESERVED:
                    fail('E_RESERVED',
                         f'{t.value} is a reserved word and cannot be a variable', t.pos)
                self.next()
                return Node('var', t.pos, name=t.value)

            if t.type == 'op' and t.value == '(':
                self.next()
                if self.at_op(')'):
                    fail('E_SYNTAX', 'empty parentheses', t.pos)
                inner = self.parse_sequence()
                self.expect_op(')')
                # Marked so that F((1,2)) passes one list rather than two arguments.
                inner.grouped = True
                return inner

            fail('E_SYNTAX', f'unexpected {describe(t)}', t.pos)
        finally:
            self.leave()

    def parse_call(self) -> Node:
        name_tok = self.next()
        self.expect_op('(')
        if self.at_op(')'):
            self.next()
            args: list[Node] = []
        else:
            inner = self.parse_sequence()
            self.expect_op(')')
            args = inner.items if (inner.t == 'list' and not inner.grouped) else [inner]

        spec = lookup(name_tok.value)
        if spec is None:
            fail('E_UNKNOWN_FUNC', f'unknown function {name_tok.value}', name_tok.pos)
        return _finish_call(name_tok, spec, args)


def _finish_call(name_tok: Token, spec: Spec, args: list[Node]) -> Node:
    """The compile-time arity rule (spec 6.2, SEL-0002) and the call node, in
    one place for both call forms; the pipeline form has already placed its
    left operand in ``args``. Every refusal reports the name token."""
    count = len(args)
    if count < spec.min or count > spec.max:
        fail('E_ARITY', f'{spec.name} takes {arity_text(spec)}, got {count}', name_tok.pos)
    if spec.arity_error is not None:
        problem = spec.arity_error(count)
        if problem:
            fail('E_ARITY', problem, name_tok.pos)
    if spec.name in REGEX_FLAG_AT:
        # A literal pattern is checked now (SPEC 7.8), even where it never runs.
        from .builtins.regex import check_literal
        check_literal(spec.name, args)
    return Node('call', name_tok.pos, name=spec.name, spec=spec, args=args,
                record_shape=_prepare_record_shape(spec.name, args))


def arity_text(spec: Spec) -> str:
    if spec.max == INF:
        return f'at least {spec.min} argument{"" if spec.min == 1 else "s"}'
    if spec.min == spec.max:
        return f'{spec.min} argument{"" if spec.min == 1 else "s"}'
    return f'{spec.min} to {spec.max} arguments'


def describe(t: Token) -> str:
    if t.type == 'eof':
        return 'end of input'
    if t.type == 'text':
        return 'a text literal'
    if t.type == 'num':
        return f'number {t.value}'
    return f'"{t.value}"'


def check_target(node: Node, op_tok: Token) -> None:
    """The target must be an identifier followed by zero or more index operations."""
    n = node
    while n.t == 'index':
        n = n.obj
    if n.t != 'var' or node.grouped:
        fail('E_BAD_ASSIGN', f'cannot assign with {op_tok.value} to this expression',
             node.pos)


def parse(source: str) -> Node:
    return Parser(tokenize(source)).parse_program()
