"""Parser.

**This is the precedence-climbing pilot.** The other four hosts transcribe
spec/grammar.md one method per production — `parse_sequence` -> `parse_list` ->
`parse_assignment` -> `parse_or` -> ... -> `parse_primary`, sixteen deep, plus a
thunk frame per binary helper. That shape is a deliberate choice there and it
costs those hosts nothing, but it is **35 stack frames per level of parenthesis
nesting** (measured against the JS host: 44 frames at one paren, 1409 at forty,
linear at 35.0). E_DEPTH trips at 100 nested parens, so transcribing it here
would need ~3500 Python frames against a default recursion limit of 1000, and
this host would raise RecursionError where the other four raise E_DEPTH.

Raising sys.setrecursionlimit would paper over that. Precedence climbing removes
it: the sixteen levels of spec/SPEC.md §5 become the table below, and one level
of nesting costs six frames instead of thirty-five.

The intent is that the other four hosts adopt this shape in turn, so the table
is written to be transcribed rather than to be clever, and anything specific to
Python is kept out of the loop.

Five things this has to reproduce exactly, none of which the type checker will
catch for you — they all produce a *valid parse of the wrong tree*:

 1. NOT is a LOOSE prefix operator (bp 7 — looser than comparison, tighter than
    AND) while unary `-` is a TIGHT one (bp 15). Textbook precedence climbing
    puts every prefix operator in parse_primary, at the tightest end, which
    would make `NOT a == b` parse as `(NOT a) == b`. Prefix operators get their
    own binding power here, and `parse_prefix` refuses one whose binding power
    is looser than the position allows — which is what makes `a == NOT b` and
    `-NOT x` E_RESERVED, exactly as the transcribed parsers make them.
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
from .registry import Spec, lookup

ASSIGN_OPS = frozenset(['=', '+=', '-=', '*=', '/=', '%=', '&='])
COMPARE_OPS = frozenset(['==', '!=', '<', '<=', '>', '>=',
                         '$==', '$!=', '$<', '$<=', '$>', '$>='])
COMPARE_WORDS = frozenset(['EQL', 'IN'])

# spec/SPEC.md §5, as a table. Higher binds tighter. The gaps are the levels
# that are not infix: 16 is postfix/primary, 15 is unary minus, 7 is NOT.
BP_SEQ = 1        # ;
BP_LIST = 2       # ,
BP_ASSIGN = 3     # = += -= *= /= %= &=   (right associative)
BP_OR = 4
BP_XOR = 5
BP_AND = 6
BP_NOT = 7        # prefix
BP_COMPARE = 8    # non-associative
BP_BOR = 9
BP_BXOR = 10
BP_BAND = 11
BP_CONCAT = 12    # &
BP_ADD = 13       # + -
BP_MUL = 14       # * / %
BP_NEG = 15       # prefix

# Infix operator -> (binding power, associativity). 'L' left, 'R' right,
# 'N' non-associative. Word operators are lexed as identifiers, so they are
# looked up separately; the binding powers are the same table.
INFIX_OPS: dict[str, tuple[int, str]] = {
    '&': (BP_CONCAT, 'L'),
    '+': (BP_ADD, 'L'), '-': (BP_ADD, 'L'),
    '*': (BP_MUL, 'L'), '/': (BP_MUL, 'L'), '%': (BP_MUL, 'L'),
    **{op: (BP_ASSIGN, 'R') for op in ASSIGN_OPS},
    **{op: (BP_COMPARE, 'N') for op in COMPARE_OPS},
}
INFIX_WORDS: dict[str, tuple[int, str]] = {
    'OR': (BP_OR, 'L'), 'XOR': (BP_XOR, 'L'), 'AND': (BP_AND, 'L'),
    'BOR': (BP_BOR, 'L'), 'BXOR': (BP_BXOR, 'L'), 'BAND': (BP_BAND, 'L'),
    **{w: (BP_COMPARE, 'N') for w in COMPARE_WORDS},
}


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
    # The try/finally around the depth counter is deliberate and differs from the
    # JS, PHP and C++ parsers, which leave parse_sequence's leave() unprotected.
    # It costs nothing — a failing parse abandons the Parser either way — and the
    # Lisp host's with-depth macro already protects it, so this is the shape the
    # other three should converge on rather than a Python deviation.
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

            if assoc == 'R':
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
        while self.at_op('['):
            br = self.next()
            self.enter(br.pos)
            try:
                idx = self.parse_sequence()
                self.expect_op(']')
                node = Node('index', br.pos, obj=node, idx=idx)
            finally:
                self.leave()
        return node

    def parse_primary(self) -> Node:
        t = self.peek()
        self.enter(t.pos)
        try:
            if t.type == 'num':
                self.next()
                # Canonicalised once, here: the literal 007 is the value 7.
                return Node('num', t.pos, v=D.format(D.parse(t.value, t.pos)))

            if t.type == 'text':
                self.next()
                return Node('text', t.pos, v=t.value)

            if t.type == 'ident':
                if t.value in ('TRUE', 'FALSE'):
                    self.next()
                    return Node('bool', t.pos, v=(t.value == 'TRUE'))
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
        if len(args) < spec.min or len(args) > spec.max:
            fail('E_ARITY', f'{spec.name} takes {arity_text(spec)}, got {len(args)}',
                 name_tok.pos)
        if spec.arity_error is not None:
            problem = spec.arity_error(len(args))
            if problem:
                fail('E_ARITY', problem, name_tok.pos)
        return Node('call', name_tok.pos, name=spec.name, spec=spec, args=args)


def arity_text(spec: Spec) -> str:
    if spec.max == float('inf'):
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
