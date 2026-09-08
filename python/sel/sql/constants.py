"""Does SEL itself accept this expression?

The translator's job is to answer "can this rule be pushed into that database".
It was answering that question without ever asking a prior one: is the rule
*valid*. ``LEFT("abc", -1)`` translates cleanly into every dialect here, and SEL
raises E_RANGE for it while MariaDB answers ``''``, PostgreSQL answers ``'ab'``
and SQLite answers ``'abc'``. Three databases, three answers, none of them
SEL's -- from a translation that reported success.

That is the core promise inverted. So where an expression's arguments are all
literals, the values SEL would reject are known *here*, and this asks SEL.

It is validation, not constant folding. The value is computed and thrown away;
the translation that follows is byte-identical to the one that would have been
emitted without this check. Folding would have been the tempting second step and
would have blinded the oracle: an expression replaced by its answer no longer
exercises the database's version of the operation, which is the only thing
sql/oracle/ exists to compare.

What it cannot do is check a value it does not have. ``LEFT(col, -1)`` is exactly
as wrong and passes, because ``col`` is a column and its value is not knowable at
translation time. See docs/SQL-TRANSLATION.md §11.4.
"""

from __future__ import annotations

from typing import Any

from ..errors import Pos, SelError
from ..eval import Context, eval_node
from ..parser import Node
from ..value import Value
from .errors import refuse


def is_binder_name(node: Node) -> bool:
    """Is this node an aggregate's binder NAME, rather than a read of one?

    The evaluator's rule, in Args.symbol: a bare ``var`` node that did NOT come
    from parentheses. Both halves matter and the second was missing here, in all
    three hosts. ``(C)`` parses as a ``var`` node carrying the parser's
    ``grouped`` flag -- which is exactly what makes ``(A) = 1`` an E_BAD_ASSIGN
    -- so testing only the kind accepted a binder the evaluator refuses with
    E_EXPECT_SYMBOL, and ``ALL(V, (C), C > 0)`` translated to working SQL for a
    rule that can never run. A translation accepted where the language refuses is
    the one direction this layer must never fail in.
    """
    return node.t == 'var' and not node.grouped

def scope(bindings) -> tuple[dict[str, bool], Context]:
    """The value bindings, as a name set and an evaluation context.

    A ``value`` binding is a constant the translator *has* -- §5.4 calls it "a
    constant supplied at translation time, inlined as a literal", and
    ``Translator._variable`` hands it straight to literal(). So ``LEFT("abc", X)``
    with X bound to "-1" is exactly as knowable as ``LEFT("abc", -1)``, and
    before this it was exactly as wrong: SEL raised E_RANGE and the four servers
    answered '', '', 'ab' and ''. §11.4's headline defect, still open through the
    documented way a host passes a parameter.

    Only scalars are lifted. A list-valued binding is what an aggregate iterates
    and its shape is the translator's business, not the evaluator's.
    """
    names: dict[str, bool] = {}
    root = Value.none()
    if bindings is None:
        return names, Context(root)
    for name in bindings.names():
        b = bindings.get(name)
        if b.get('kind') != 'value':
            continue
        v = b['value']
        if not isinstance(v, Value) or v.is_none() or v.size() > 0:
            continue
        names[name] = True
        root.set(name, v)
    return names, Context(root)


def is_constant(n: Node, bound: dict[str, bool] | None = None) -> bool:
    """Whether every leaf under ``n`` is a literal.

    A binder an aggregate introduces inside ``n`` counts as bound, so
    ``ALL((1, 2), _ > 0)`` is constant and ``ALL(ITEMS, _ > 0)`` is not. That is
    the same rule the evaluator applies, which is what lets the whole node be
    handed to it below.
    """
    bound = bound or {}
    t = n.t
    if t in ('num', 'text', 'bool'):
        return True
    if t == 'var':
        return n.name in bound
    if t == 'un':
        return is_constant(n.x, bound)
    if t == 'bin':
        return is_constant(n.l, bound) and is_constant(n.r, bound)
    if t == 'index':
        return is_constant(n.obj, bound) and is_constant(n.idx, bound)
    if t == 'clist':
        # Stage 1 builds this one; the evaluator has never seen it and cannot
        # evaluate it. Nothing containing one is checkable.
        return False
    if t == 'list':
        return all(is_constant(item, bound) for item in n.items)
    if t == 'call':
        return _constant_call(n, bound)
    # assign and seq are gone by now (stage 1), and an unknown node type is not
    # something to guess about: not constant, so nothing is validated and the
    # walk refuses it in the ordinary way.
    return False


def _constant_call(n: Node, bound: dict[str, bool]) -> bool:
    """The binding form is the only reason this is not three lines.

    ``MAP(list, X, X + 1)`` names its binder in argument 1 and uses it in
    argument 2; the two-argument form binds ``_`` implicitly. Neither name is a
    free variable, so neither disqualifies the call -- but the *source* still has
    to be constant, or the body has nothing to iterate.
    """
    args = n.args
    if not (n.spec is not None and n.spec.binds):
        return all(is_constant(a, bound) for a in args)

    if not is_constant(args[0], bound):
        return False
    inner = dict(bound)
    body = 1
    if len(args) >= 3:
        # Malformed; not constant, and _agg_shape refuses it for real.
        if not is_binder_name(args[1]):
            return False
        inner[args[1].name] = True
        body = 2
    else:
        inner['_'] = True
    return all(is_constant(args[i], inner) for i in range(body, len(args)))


def validate(n: Node, ctx: Context | None = None) -> None:
    """Evaluate ``n`` the way SEL would, and refuse the translation if SEL
    refuses the expression.

    Called *after* the node has been translated, not before, so that every
    refusal the translator already had keeps its own message. ``TRUE + 1`` is an
    expression SEL rejects and also a BOOL where a number is required; the second
    is the more useful sentence and is the one an author reading it can act on,
    and it is the same sentence ``FLAG + 1`` gets, where no value is known and
    only the kind check can fire. This check adds refusals where translation used
    to *succeed* -- which is the whole of the defect it exists for -- and changes
    none of the ones that already existed. ABORT and an unportable regex are
    refused by name on the way past and never reach here.

    The position reported is SEL's own -- the innermost node that failed, not the
    outermost one this was called with -- because that is the character the author
    has to change.
    """
    try:
        eval_node(n, ctx if ctx is not None else Context())
    except SelError as e:
        _refuse_as_sel(e, n)


def require_numeric(n: Node, ctx: Context | None = None) -> None:
    """The same question asked of one *operand* rather than a whole expression.

    ``validate`` above only fires where the entire node is knowable, and that
    turned out to be the wrong shape: ``T + (1 + "x")`` refused while
    ``(T + 1) + "x"`` -- the same expression, differently parenthesised --
    translated, because one column anywhere in the node switched the check off.
    Whether a defect is caught may not depend on where the author put brackets.

    A constant in a numeric position is knowable on its own, and what it settles
    does not depend on the rest: ``"x"`` is not a number, so SEL raises E_NOT_NUM
    whatever the column holds. Refusing therefore loses nothing -- there is no
    value of the other operand that SEL would have answered.

    The test is the constant's VALUE and never a declared kind. A TEXT column
    holding numerals is a legitimate schema and ``A == 5`` must keep translating,
    because SEL's ``==`` compares numerically and a bare ``=`` between two text
    columns would answer FALSE where SEL answers TRUE. That is pinned as
    op.compare.coerce-variant-when-a-side-is-not, and again as
    const.numeric.a-text-column-still-coerces so this check cannot grow into it.
    """
    try:
        eval_node(n, ctx if ctx is not None else Context()).as_decimal(n.pos)
    except SelError as e:
        _refuse_as_sel(e, n)


def _refuse_as_sel(e: SelError, n: Node) -> None:
    """SEL's own refusal, reported as the translator's.

    The position is SEL's own -- the innermost node that failed, not the
    outermost one this was entered at -- because that is the character the author
    has to change.
    """
    pos = Pos(e.line, e.col, e.offset) if e.line > 0 else n.pos
    refuse('E_SQL_INVALID',
           f'SEL rejects this expression ({e.code}: {e.message}), so there is '
           'nothing to translate; a database would answer something rather '
           'than fail', pos)
