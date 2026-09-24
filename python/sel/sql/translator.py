"""Stage 4: render the normalised tree.

Kind inference is folded into this walk rather than run as a separate pass. The
walk is post-order, so every operand's kind is already known when its parent
needs it -- which is exactly what a separate pass would have computed, at the
cost of a second traversal and a side table keyed by node identity. Whole-
expression refusal is unaffected: nothing becomes characters until
``Fragment.as_value()`` is called, so a kind failure still escapes with no
partial output.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .. import utf8
from ..builtins import regex as _regex
from ..errors import Pos, SelError
from ..eval import Context, MAX_DEPTH, eval_node
from ..lexer import ascii_upper
from ..parser import Node
from ..value import Value, quote_dump
from . import constants as _constants
from . import map as _map
from . import normalise as _normalise
from .binder import Binder
from .bindings import Bindings
from .emit import Emit
from .errors import refuse
from .fragment import Fragment
from .relational_plan import JoinPlan, RelationalPlan
from ..optimizer import PIPELINE_OPS as OPTIMIZER_PIPELINE_OPS

# The optimiser's list, not a second copy: one vocabulary of pipeline operators
# per host, or the planner and the translator drift apart.
PIPELINE_OPS = OPTIMIZER_PIPELINE_OPS

# SEL list keys are the canonical decimals "1", "2", … -- so "01" is not a key
# and neither is "1\n", and the evaluator answers E_NO_KEY for both. This layer
# used to answer *element 1* for both, in every host, because `^[0-9]+$` accepts
# a trailing newline (Python's `$`, and PHP's without the D modifier) and
# int()/(int) accept leading zeros. decimal.py already uses fullmatch for
# exactly this reason; the SQL layer did not inherit the lesson.
#
# Nine digits at most, so the conversion is exact in every host that will ever
# implement this: C++'s stoi throws above int32, PHP saturates above int64, JS
# loses precision above 2^53. No list this layer can build has a billion
# elements, so the cap costs nothing and removes the question.
_LIST_KEY = re.compile(r'[1-9][0-9]{0,8}')


def _list_key(k: str) -> int | None:
    """The 1-based position a key names, or None when it names none."""
    return int(k) if _LIST_KEY.fullmatch(k) else None

#: Lowered by stage 2; none of them is a `funcs` entry. See sql/MAP.md §4.
AGGREGATES = ('ALL', 'ANY', 'MAP', 'FILTER', 'SUM', 'JOIN')

AGG_RETURNS = {'ALL': 'BOOL', 'ANY': 'BOOL', 'SUM': 'NUM', 'JOIN': 'TEXT',
               'MAP': 'LIST', 'FILTER': 'LIST'}
AGG_SKELETON = {'ALL': 'all', 'ANY': 'any', 'SUM': 'sum', 'JOIN': 'join'}
AGG_FOLD = {'ALL': 'AND', 'ANY': 'OR', 'SUM': '+'}

# The functions whose result has children, so the scalar rule does not apply to
# them. Measured rather than written: every non-lazy name in registry.names()
# was called and the results with size() > 0 kept. Every one is already refused
# by the dialect documents; the point of the list is that source() used to reach
# its scalar fallback without ever consulting the map, so COUNT and HAS folded
# to 0 and FALSE instead.
# The four text functions that yield a list (measured), the constructors,
# and every pipeline step -- the optimiser's vocabulary, so a new step is
# covered by being one (review 2026-09-15 finding X: COUNT(LIST(1, 2, 3))
# was 0).
YIELDS_LIST = ('BTL', 'INDEXES', 'RGROUPS', 'SPLIT', 'LIST', 'RECORD',
               *OPTIMIZER_PIPELINE_OPS)

# The functions that read their argument as bytes, and the one that takes a
# BOOL. Both lists were measured rather than written: every name in
# registry.names() was called with TO_UTF8("a") and with TRUE, and these are the
# ones SEL did not answer E_NOT_* for. Writing them by hand would be the second
# copy of SEL's argument rules that §11.4 exists to avoid -- this is a cached
# measurement, and sql/oracle/ re-measures it.
BIN_ARGUMENT_OK = ('BLEN', 'CRC32', 'ENCODE_BASE64', 'FROM_UTF8', 'ISNUM',
                   'TO_HEX', 'TO_UTF8')
BOOL_ARGUMENT_OK = ('ISNUM',)

NUMERIC_ARGUMENT_AT: dict[str, tuple[int, ...] | bool] = {
    'ABS': (0,),
    'SIGN': (0,),
    'CEIL': (0,),
    'FLOOR': (0,),
    'TRUNC': (0,),
    'ROUND': (0, 1),
    'POWER': (0, 1),
    'MIN': True,
    'MAX': True,
    'LEFT': (1,),
    'RIGHT': (1,),
    'SUBSTR': (1, 2),
    'FIND': (2,),
    'REPEAT': (1,),
    'PADL': (1,),
    'PADR': (1,),
    'CHAR': (0,),
    'CANON': (0,),
}


def _is_numeric_argument(name: str, i: int) -> bool:
    at = NUMERIC_ARGUMENT_AT.get(name)
    if at is True:
        return True
    return isinstance(at, tuple) and i in at


# The runtime kind classes EQL and IN compare, which are not the static kinds.
# A SEL number IS a text value (spec §4), so `1 EQL "1"` is TRUE and NUM and
# TEXT are one class here. BOOL and BIN are each their own: `0 EQL FALSE` is
# FALSE because a number is not a boolean, and `TO_UTF8("a") EQL "a"` is FALSE
# because bytes are not text.
EQL_CLASS = {'NUM': 'text', 'TEXT': 'text', 'BOOL': 'bool', 'BIN': 'bin'}

_NUMERIC_OPS = ('==', '!=', '<', '<=', '>', '>=')
_TEXTUAL_OPS = ('$==', '$!=', '$<', '$<=', '$>', '$>=', 'EQL')
_BYTE_COMPARISONS = ('$==', '$!=', '$<', '$<=', '$>', '$>=', 'EQL', 'IN')
_REGEX_AT = {'RMATCH': 0, 'RFIND': 0, 'RREPLACE': 0, 'RGROUPS': 0}


def _lit_node(t: str, v: Any, pos: Pos) -> Node:
    return Node(t=t, pos=pos, v=v)


class Translator:
    def __init__(self, dialect: str, bindings: Bindings,
                 options: dict[str, Any] | None = None) -> None:
        self.dialect = dialect
        self.emit = Emit(dialect)
        self.bindings = bindings
        self.strict = bool((options or {}).get('strict', False))
        self.params: list[Value] = []
        self.param_kinds: list[str] = []
        # Aggregate binders, innermost last. Consulted before the bindings map,
        # the same precedence sel.eval.Context.lookup gives a binder over a
        # variable, and pushed per element so that nested aggregates shadow
        # independently.
        self.frames: list[dict[str, Binder]] = []
        self.caveats: dict[str, bool] = {}
        self.const_names: dict[str, bool] = {}
        self.const_ctx = None
        # Walk depth, counted exactly as eval.eval_node counts evaluation nesting.
        self.depth = 0
        self.statement_plan: RelationalPlan | None = None
        self.in_where: bool = False
        self.in_having: bool = False
        self.subquery_counter = 0

    def _begin(self, ast: Node):
        """What both entry points do before they differ: the dialect and alias
        checks (in that order -- a caller with both a bad dialect and a duplicate
        alias gets E_SQL_DIALECT), the per-translation state, the constant scope,
        stage 1, and the planner's look at the result. Returns the normalised
        tree and the plan, or None where the tree is an expression."""
        _map.require_target(self.dialect)
        self.bindings.check_aliases()

        self.params = []
        self.param_kinds = []
        self.caveats = {}
        self.frames = []
        self.depth = 0
        self.subquery_counter = 0
        self.statement_plan = None
        self.const_names, self.const_ctx = _constants.scope(self.bindings)
        # Stage 1 and nothing else: the translator renders the tree it is
        # handed. Two hosts ran the logical optimiser here and three did not,
        # so the same program rendered different SQL per host (review
        # 2026-09-15 finding C). The planner is the one place that optimises
        # before translating, and it does so in every host.
        norm = _normalise.run(ast, self.const_names, self.const_ctx)
        return norm, self.analyze_pipeline(norm)

    def translate(self, ast: Node) -> Fragment:
        norm, plan = self._begin(ast)
        if plan is not None:
            return self.compile_statement(plan)
        f = self._node(norm)

        out = Fragment(f.parts, f.kind, self.dialect, self.params,
                       self.param_kinds, list(self.caveats))
        # Public: it says the value is a canonical number, whose spelling is
        # the contract and not only its value (the SQL oracle compares it as
        # text). Lost here once, in every host at the same time.
        out.canonical = f.canonical
        return out

    def translate_statement(self, ast: Node) -> Fragment:
        _norm, plan = self._begin(ast)
        if plan is None:
            refuse('E_SQL_SHAPE', 'expected a relational query or pipeline')
        return self.compile_statement(plan)

    @staticmethod
    def _record_fields(node: Node) -> list[tuple[str, Node]]:
        """The (name, value) pairs of a RECORD(k, v, ...) call, refusing what the
        evaluator would: an odd count at the call, a name that is not a text
        literal at the name. The planner reads RECORD in three places -- a
        bucket's projection, a bucket's key, a MAP's projection -- and each
        used to walk the pairs itself."""
        if len(node.args) % 2:
            refuse('E_ARITY', 'RECORD takes an even number of arguments', node.pos)
        fields: list[tuple[str, Node]] = []
        for i in range(0, len(node.args), 2):
            key = node.args[i]
            if key.t != 'text':
                refuse('E_BAD_ARG', 'RECORD field names must be string literals', key.pos)
            fields.append((key.v, node.args[i + 1]))
        return fields

    # --- the walk ------------------------------------------------------------

    def _node(self, n: Any) -> Fragment:
        """Ask SEL whether the expression is valid before asking the map whether
        it is translatable, wherever the arguments are literals and SEL can
        answer.

        Only compound nodes are worth checking -- a literal cannot be out of
        range on its own -- but *every* one of them, not just the outermost.
        Checking only the outermost looks like a free optimisation and is not,
        because SEL is lazy. ``FALSE AND (1 / 0 > 0)`` is constant and SEL
        answers FALSE without ever dividing, so validating the AND alone accepts
        it and ``(1 / 0)`` goes into the SQL -- while ``F AND (1 / 0 > 0)``, with
        a column in place of the FALSE, was refused, because then the AND is not
        constant and the walk reaches the division on its own. Same division,
        opposite answer, decided by whether the operand beside it happened to be
        written down.

        The dead branch is not currently a wrong answer: MariaDB and PostgreSQL
        were both asked, and both short-circuit ``AND`` and ``CASE`` rather than
        evaluating the arm they do not take. It is refused because §11.2's first
        row records that SQL does not promise that, and because a rule that
        refuses one of two identical divisions is not a rule.

        **The walk is also bounded, at the evaluator's own limit.** Nothing
        bounded it, so a flat chain of 201 terms over a column translated -- and
        the evaluator answers E_DEPTH for that same expression. A rule the
        database answers and SEL does not is the defect above in a different
        costume, and it was in every host: PHP rendered it, and this one
        happened to die of its own stack at around 510 terms, which is an
        implementation accident rather than a decision. The guard reads
        ``eval.MAX_DEPTH`` rather than repeating 200, so the two cannot drift.
        """
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.depth -= 1
            refuse('E_SQL_DEPTH',
                   f'this expression nests deeper than SEL will evaluate '
                   f'({MAX_DEPTH}), so there is nothing to translate; the '
                   'evaluator answers E_DEPTH for it', n.pos)
        try:
            if n.t not in ('bin', 'un', 'call') \
                    or not _constants.is_constant(n, self.const_names):
                return self._dispatch(n)

            f = self._dispatch(n)
            _constants.validate(n, self.const_ctx)
            return f
        finally:
            self.depth -= 1

    def _dispatch(self, n: Any) -> Fragment:
        t = n.t
        if t == 'num':
            return self._literal(Value.num(n.v), 'NUM')
        if t == 'text':
            return self._literal(Value.text(n.v), 'TEXT')
        if t == 'bool':
            return self._literal(Value.bool(n.v), 'BOOL')
        if t == 'var':
            return self._variable(n)
        if t == 'index':
            return self._index(n)
        if t == 'un':
            return self._unary(n)
        if t == 'bin':
            return self._binary(n)
        if t in ('list', 'clist'):
            refuse('E_SQL_SHAPE',
                   'a list is not a SQL value; a list can only be the thing an '
                   'aggregate iterates', n.pos)
        if t == 'call':
            return self._call(n)
        refuse('E_SQL_SHAPE', f'cannot translate a {t} node', n.pos)

    def _literal(self, v: Value, kind: str) -> Fragment:
        """Every literal becomes a parameter slot; §9 of docs/internals/sql-translation.md.

        ``kind`` is both the static kind the expression has and the form the
        literal is written in, and the two are the same thing only because this
        is where the AST node kind is still known. A ``num`` node gives NUM, a
        ``text`` node gives TEXT, and no later stage has to guess which of the two
        a value that happens to read as a number came from.
        """
        self.params.append(v)
        self.param_kinds.append('TEXT' if kind in ('UNKNOWN', 'LIST') else kind)
        return Fragment([len(self.params)], kind, self.dialect)

    def _variable(self, n: Node) -> Fragment:
        bound = self._binder(n.name)
        if bound is not None:
            return self._from_binder(bound, n)
        b = self.bindings.get(n.name, n.pos)
        kind = b['kind']
        if kind == 'column':
            return self._column_ref(b)
        if kind == 'value':
            v = b['value']
            if v.size() > 0:
                refuse('E_SQL_SHAPE',
                       f'{n.name} is bound to a list, and a list is not a SQL value; '
                       'it can only be the thing an aggregate iterates', n.pos)
            # An empty binding -- {"kind": "value", "value": []}, which is what an
            # empty result set looks like -- has no characters, and asking for
            # them raised a SelError from inside as_value(): the wrong class, and
            # thrown past try_translate(), which a host uses precisely so it does
            # not need a try/except. It is legal as the thing an aggregate
            # iterates (ALL over nothing is TRUE) and nothing as a value, so the
            # refusal belongs here, where the walk knows a scalar was asked for.
            if v.is_none():
                refuse('E_SQL_SHAPE',
                       f'{n.name} is bound to an empty value, which is not a SQL '
                       'value; only an aggregate can be given an empty binding',
                       n.pos)
            return self._literal(v, _declared_kind(b, v))
        if kind in ('columns', 'relation'):
            refuse('E_SQL_SHAPE',
                   f'{n.name} is bound as a {kind}, which names a set of values '
                   'rather than one; use it as the first argument of an aggregate, '
                   'not as a value on its own', n.pos)
        refuse('E_SQL_BINDING', f'unusable binding for {n.name}', n.pos)

    def _column_ref(self, c: dict[str, Any]) -> Fragment:
        sql = (str(c['raw']) if c.get('raw') is not None
               else self.emit.column(c.get('table'), str(c['column'])))
        frag = Fragment([sql], str(c.get('type') or 'UNKNOWN'), self.dialect,
                        exact=bool(c.get('exact', False)),
                        sargable=bool(c.get('sargable', False)),
                        guard=bool(c.get('guard', False)))
        if c.get('prefilter') == 'separate':
            frag.separate_prefilter = True
        frag.canonical = bool(c.get('canonical', False))
        return frag

    def _index(self, n: Node) -> Fragment:
        """Indexing is meaningful against a relation or columns binding -- a field
        or a position -- and against nothing else. ``A[k]`` on a scalar column
        would have to reach inside a value SQL has no way to look inside.
        """
        obj = n.obj
        if (self.statement_plan is not None and obj is not None
                and obj.t == 'index' and obj.obj is not None and obj.obj.t == 'var'):
            # `_["orders"]["status"]` names a joined relation's field -- when
            # the inner name is a row. Over a bucket's members or a projected
            # row the inner index is itself the thing to refuse.
            inner = self._binder(obj.obj.name)
            if inner is not None and inner.shape in (Binder.GROUP, Binder.PROJECTED):
                self._node(obj)
            qualifier = self._constant_index(obj.idx)
            field = self._constant_index(n.idx)
            return self._index_qualified(qualifier, field, n)
        if obj.t != 'var':
            refuse('E_SQL_SHAPE',
                   'only a bound name can be indexed here; SQL has no way to index '
                   'into the result of an expression', n.pos)
        bound = self._binder(obj.name)
        if bound is not None:
            return self._index_binder(bound, obj.name, self._constant_index(n.idx), n)
        b = self.bindings.get(obj.name, obj.pos)
        key = self._constant_index(n.idx)

        if b['kind'] == 'relation':
            # The binding NAME, not a binder over it. SEL has no row here to
            # index -- ITEMS is a list of rows -- so it raises E_NO_KEY, and this
            # produced a bare column reference to a table no FROM clause
            # mentions. Inside an aggregate body it was worse than invalid:
            # ALL(ITEMS, I, ITEMS["QTY"] > 0) emitted SQL byte-identical to the
            # binder form and ran and answered, for an expression SEL has no
            # answer for.
            refuse('E_SQL_SHAPE',
                   f'{obj.name} is a relation, which is a list of rows; indexing it '
                   'names no value SEL can produce, so use an aggregate and index '
                   'the row its binder gives you', n.pos)
        if b['kind'] == 'columns':
            i = _list_key(key)
            if i is None or i > len(b['items']):
                refuse('E_SQL_BINDING',
                       f"{obj.name}[{key}] is outside that binding's "
                       f"{len(b['items'])} column(s)", n.pos)
            return self._column_ref(b['items'][i - 1])
        if b['kind'] == 'value':
            child = b['value'].get(key)
            if child is None:
                refuse('E_SQL_BINDING',
                       f'{obj.name}["{key}"] is not a key of that value', n.pos)
            if child.size() > 0:
                refuse('E_SQL_SHAPE',
                       f'{obj.name}["{key}"] is a list, not a SQL value', n.pos)
            return self._literal(child, _declared_kind(b, child))
        refuse('E_SQL_SHAPE',
               f'{obj.name} is bound as a column, which has no parts to index', n.pos)

    def _index_qualified(self, qualifier: str, key: str, n: Node) -> Fragment:
        plan = self.statement_plan
        sources = [{
            'names': [plan.source_name, plan.source_alias, _relation_alias(plan.source_relation)],
            'relation': plan.source_relation,
            'table': plan.source_alias or _relation_alias(plan.source_relation),
        }]
        for join in plan.joins:
            sources.append({
                'names': [join.source_name, join.source_alias,
                          _relation_alias(join.source_relation), join.left_binder,
                          join.right_binder],
                'relation': join.source_relation,
                'table': join.source_alias or _relation_alias(join.source_relation),
            })
        source = next((item for item in sources
                       if any(name is not None and str(name).upper() == qualifier.upper()
                              for name in item['names'])), None)
        if source is None:
            # A qualifier names a relation by its binding name, its table, its
            # alias or a binder the join predicate declared, and nothing else:
            # a position (`_[1]["amount"]`) or a stray name is the shape the
            # row does not have, E_SQL_SHAPE as C++ and Lisp always said
            # (SEL-0043). It used to be E_SQL_BINDING "unknown joined
            # relation" here, the accident of the alias lookup.
            if _list_key(qualifier) is not None:
                refuse('E_SQL_SHAPE',
                       f'[{qualifier}] asks for a row by position, and a relation has no '
                       'first row without an ORDER BY that nothing here can supply', n.pos)
            refuse('E_SQL_SHAPE',
                   'only a bound name can be indexed here; SQL has no way to index into '
                   f'the result of an expression ({qualifier} names no relation of this statement)',
                   n.pos)
        field = (source['relation'].get('fields') or {}).get(ascii_upper(key))
        if field is None:
            refuse('E_SQL_BINDING', f'{qualifier}["{key}"] is not a field of that relation', n.pos)
        if field.get('raw') is not None:
            return self._column_ref(field)
        return self._column_ref({**field, 'table': source['table']})

    def _relation_table_alias(self, relation: dict[str, Any], name: str | None = None) -> str:
        plan = self.statement_plan
        if plan is not None:
            source_names = [plan.source_name, plan.source_alias]
            if relation is plan.source_relation and (name is None or any(
                    item is not None and str(item).upper() == str(name).upper()
                    for item in source_names)):
                return plan.source_alias or _relation_alias(relation)
            for index, join in enumerate(plan.joins):
                names = [join.source_name, join.source_alias, join.left_binder,
                         join.right_binder, f'_{index + 2}']
                if relation is join.source_relation and (name is None or any(
                        item is not None and str(item).upper() == str(name).upper()
                        for item in names)):
                    return join.source_alias or _relation_alias(relation)
            if relation is plan.source_relation:
                return plan.source_alias or _relation_alias(relation)
        return _relation_alias(relation)

    def _constant_index(self, idx: Node) -> str:
        if idx.t in ('num', 'text'):
            return str(idx.v)
        refuse('E_SQL_SHAPE',
               'an index must be a constant here: the column it names has to be '
               'known before the query runs', idx.pos)

    def _unary(self, n: Node) -> Fragment:
        x = self._node(n.x)
        if n.op == 'NOT':
            x = self._require_bool(x, n.x.pos, 'NOT')
        else:
            self._require_not_bool(x, n.x.pos, n.op)
            # No _require_numeric_constant here, unlike _binary(). A unary node
            # whose operand is constant IS constant, so _node()'s own whole-node
            # check has already refused it -- `-"x" + T` is E_SQL_INVALID with or
            # without a call here, which makes one unwritable-as-a-case and
            # therefore not a check. The guard below is a different matter: it
            # fires on a NON-constant operand, which is exactly what _node()
            # cannot see.
            x = self._guard_numeric(x, n.x)
        return self._apply('ops', n.op, [x], n.pos)

    def _binary(self, n: Node) -> Fragment:
        op = n.op

        if op == 'IN':
            return self._in_operator(n)

        l = self._node(n.l)
        r = self._node(n.r)

        if op in ('AND', 'OR', 'XOR'):
            l = self._require_bool(l, n.l.pos, op)
            r = self._require_bool(r, n.r.pos, op)
        # SEL reads both operands of an arithmetic or numeric-comparison operator
        # as numbers, and a BOOL is not one: `0 != FALSE` is E_NOT_NUM in the
        # evaluator, not FALSE. MariaDB and SQLite coerce a boolean to 1 or 0 and
        # answer anyway -- a wrong answer with no error attached -- and PostgreSQL
        # says `cannot cast type boolean to numeric` and fails the query.
        if op in ('+', '-', '*', '/', '%') or op in _NUMERIC_OPS:
            self._require_not_bool(l, n.l.pos, op)
            self._require_not_bool(r, n.r.pos, op)
            # And an operand whose value is written down has to BE a number.
            # After the BOOL guard, not before: `TRUE + 1` is E_SQL_SHAPE and
            # stays that way.
            self._require_numeric_constant(n.l)
            self._require_numeric_constant(n.r)
            # And an operand nobody has vouched for is wrapped so that a value
            # SEL would refuse becomes NULL rather than a number the server
            # invented. A NUM operand passes through untouched.
            l = self._guard_numeric(l, n.l)
            r = self._guard_numeric(r, n.r)
        # The `$` family and `&`, not EQL and IN: those two are structural and
        # `TRUE EQL TRUE` is TRUE, while `"x" $== TRUE` is E_NOT_BIN.
        if op == '&' or (op[0] == '$' and op != '$'):
            self._require_not_bool_operand(l, n.l.pos, op)
            self._require_not_bool_operand(r, n.r.pos, op)
        variant = self._variant_for(op, [l, r])
        if variant == 'coerce':
            self._coerce_scale_limits([(l, n.l), (r, n.r)])
        if op in _BYTE_COMPARISONS:
            _require_comparable_kinds(l, r, op, n.pos)
            # See Emit.text_operand for why the operands are transformed here
            # rather than by the template. Selected by operator, NOT by the
            # variant being named "text": `&` has a variant of that name too and
            # is concatenation, not a comparison -- casting and collating its
            # operands would be wrong and, briefly, was.
            #
            # Two BIN operands are already bytes and are compared as bytes by
            # every dialect here, so the cast is not merely redundant: casting
            # them to characters made `TO_UTF8(X) EQL TO_UTF8(Y)` answer NULL on
            # MariaDB and MySQL whenever either side was not valid UTF-8, where
            # SEL answers FALSE. The corpus had exactly one BIN value, 7ac3a9,
            # which is valid UTF-8 and could not show it.
            l_exact = getattr(l, 'exact', False)
            r_exact = getattr(r, 'exact', False)
            l_lit = (n.l.t == 'text')
            r_lit = (n.r.t == 'text')
            if (l_exact and (r_exact or r_lit)) or (r_exact and l_lit):
                pass
            elif op == '$==' and ((getattr(l, 'sargable', False) and r_lit)
                                  or (getattr(r, 'sargable', False) and l_lit)):
                # A sargable column against a literal, either way round: the
                # coarse comparison the index can serve, AND the exact one.
                if self.emit.lex('sargablePrefilter') == 'true':
                    coarse = self._apply('ops', '$==', [l, r], n.pos, variant)
                    residual = self._apply('ops', '$==',
                                           [self.emit.text_operand(l), self.emit.text_operand(r)],
                                           n.pos, variant)
                    res = self._apply('ops', 'AND', [coarse, residual], n.pos)
                    res.prefilter = coarse
                    res.separate_prefilter = bool(getattr(l, 'separate_prefilter', False)
                                                  or getattr(r, 'separate_prefilter', False))
                    return res
            elif l.kind != 'BIN' or r.kind != 'BIN':
                l = self.emit.text_operand(l)
                r = self.emit.text_operand(r)
        res = self._apply('ops', op, [l, r], n.pos, variant)
        if op == 'AND':
            l_pref = getattr(l, 'prefilter', None)
            r_pref = getattr(r, 'prefilter', None)
            if l_pref is not None and r_pref is not None:
                res.prefilter = self._apply('ops', 'AND', [l_pref, r_pref], n.pos)
            elif l_pref is not None:
                res.prefilter = self._apply('ops', 'AND', [l_pref, r], n.pos)
            elif r_pref is not None:
                res.prefilter = self._apply('ops', 'AND', [l, r_pref], n.pos)
            if getattr(l, 'separate_prefilter', False) or getattr(r, 'separate_prefilter', False):
                res.separate_prefilter = True
        return res

    def _in_operator(self, n: Node) -> Fragment:
        """``x IN list`` is the one operator whose right operand is a list on
        purpose. SEL's IN is EQL-based and therefore structural; SQL's is a value
        comparison under a collation. For scalars under the binary collation the
        two agree, and that is the only shape accepted.
        """
        # The needle is rendered per branch, and per comparison in the list
        # branch, rather than once up front. Rendering it eagerly bound a value
        # the list branch then never used, leaving one more entry in `params`
        # than there were placeholders -- the mirror of the bug below.
        rhs = n.r

        # `x IN rel` is the one place a relation appears on the right of an
        # operator rather than as an aggregate's source, so it is lowered here
        # and not by _aggregate(). {body} is the relation's declared scalar.
        if rhs.t == 'var' and self._binder(rhs.name) is None \
                and self.bindings.has(rhs.name):
            b = self.bindings.get(rhs.name, rhs.pos)
            if b['kind'] == 'relation':
                scalar = ascii_upper(str(b['scalar'])) if b.get('scalar') is not None else None
                if scalar is None or scalar not in b['fields']:
                    refuse('E_SQL_SHAPE',
                           f'IN over {rhs.name} needs the binding to name a "scalar" '
                           'field: that is the column the subquery projects', rhs.pos)
                # A relation with more than one field is a list of ROWS, and SEL
                # compares a scalar against a row structurally: it is FALSE for
                # every row, always. Projecting one column would translate
                # something the evaluator never answers -- the oracle found this
                # by asking both sides and getting [1] from the server and []
                # from SEL. Bind the column as its own one-field relation and the
                # two agree; anything else is a guess about which field the author
                # meant.
                if len(b['fields']) != 1:
                    refuse('E_SQL_SHAPE',
                           f'IN over {rhs.name} is refused: the relation declares '
                           f"{len(b['fields'])} fields, so SEL reads its rows as maps "
                           'and a scalar can never equal one. Bind the projected '
                           'column as a relation with that one field.', rhs.pos)
                return Fragment(
                    self._fill_named(self._skeleton('inRelation', n.pos),
                                     _slots(self._relation_slots(b), {
                                         'needle': [self.emit.text_operand(self._node(n.l))],
                                         'body': [self.emit.text_operand(
                                             self._column_ref(b['fields'][scalar]))],
                                     }), n.pos),
                    'BOOL', self.dialect)

        elements = None
        if rhs.t == 'list':
            elements = rhs.items
        elif rhs.t == 'clist':
            elements = [v for _k, v in rhs.entries]
        elif rhs.t == 'var' and self._binder(rhs.name) is None \
                and self.bindings.has(rhs.name):
            # A `value` binding holding a list is the natural way a host writes
            # an allow-list, and §5.4 says it unrolls exactly like a literal
            # list. The elements are already synthesised into nodes for the
            # aggregates; this reuses that rather than adding a second path.
            b = self.bindings.get(rhs.name, rhs.pos)
            if b['kind'] == 'value' and b['value'].size() > 0:
                elements = [binder.payload
                            for binder in self._value_elements(b, rhs.pos).values()]

        if elements is None:
            r = self._node(rhs)              # a scalar; spec §5.4's second case
            l = self._node(n.l)
            _require_comparable_kinds(l, r, 'IN', n.pos)
            return self._apply('ops', 'IN',
                               [self.emit.text_operand(l), self.emit.text_operand(r)],
                               n.pos, 'scalar')

        # A literal list becomes a chain of byte comparisons rather than SQL's
        # IN. Casting each element inside a variadic template is not expressible,
        # and casting only the needle is not enough: on MariaDB 11.8,
        # CAST(3.0 AS CHAR) COLLATE utf8mb4_bin IN (3) is 1, because the numeric
        # right-hand side pulls the comparison back to numbers, where SEL says
        # FALSE. The cast has already cost the index SQL's IN would have used, so
        # the chain gives up nothing the fix had not already spent.
        if not elements:
            return self._literal(Value.bool(False), 'BOOL')
        tests = []
        for e in elements:
            # Needle first, because it is emitted first. A parameter slot is
            # numbered when it is created and a positional placeholder carries no
            # number, so a driver binds values in creation order to placeholders
            # in text order -- and the two are the same order only if operands are
            # rendered left to right.
            #
            # The needle is also rendered once per comparison rather than once and
            # spliced N times: splicing one Fragment twice puts the same slot
            # number in the output twice while `params` holds one entry.
            raw = self._node(n.l)
            is_exact = getattr(raw, 'exact', False)
            needle = raw if is_exact else self.emit.text_operand(raw)
            f = self._node(e)
            if f.kind == 'LIST':
                refuse('E_SQL_SHAPE',
                       'IN over a list of lists is structural in SEL and has no SQL '
                       'counterpart', e.pos)
            _require_comparable_kinds(raw, f, 'IN', e.pos)
            item = f if is_exact else self.emit.text_operand(f)
            tests.append(self._apply('ops', 'EQL',
                                     [needle, item], e.pos, 'text'))
        return self.fold_pairwise('OR', tests, n.pos)

    def fold_pairwise(self, op: str, parts: list[Fragment], pos: Pos) -> Fragment:
        """Fold fragments pairwise-left through an operator's own template -- the
        same path a hand-written chain takes, so an unrolled aggregate and a
        written-out chain produce the same bytes.
        """
        acc = parts[0]
        for nxt in parts[1:]:
            acc = self._apply('ops', op, [acc, nxt], pos,
                              self._variant_for(op, [acc, nxt]))
        return acc

    def _call(self, n: Node) -> Fragment:
        name = n.name

        # The two aggregates over a bucket's members -- COUNT(g) is COUNT(*)
        # and SUM(g, [x,] body) is SUM over the grouped rows -- fire on the
        # GROUP binder alone: over a relation row, COUNT(_) is the row's
        # number of fields in SEL (review 2026-09-15 finding X), and SEL has
        # no per-group MIN or MAX (finding J). The body binds the member row,
        # as the evaluator's walk does: `_` for the two-argument form, the
        # name given for the three-argument one.
        if self.statement_plan is not None:
            arg0 = n.args[0] if n.args else None
            group = self._binder(arg0.name) if arg0 is not None and arg0.t == 'var' else None
            if group is not None and group.shape == Binder.GROUP:
                if name == 'COUNT' and len(n.args) == 1:
                    return Fragment(['COUNT(*)'], 'NUM', self.dialect)
                if name == 'SUM' and len(n.args) >= 2:
                    has_custom_binder = len(n.args) == 3 and _constants.is_binder_name(n.args[1])
                    body_node = n.args[2] if has_custom_binder else n.args[1]
                    src = {'relation': group.payload, 'filters': [], 'pos': n.pos}
                    inner = self._with_row(src, n.args[1].name if has_custom_binder else '_',
                                           lambda: self._node(body_node))
                    return Fragment([f"COALESCE(SUM({''.join(inner.parts)}), 0)"], 'NUM', self.dialect)

        if name in AGGREGATES:
            return self._aggregate(n)
        if name == 'COUNT':
            return self._count(n)
        if name == 'HAS':
            return self._has(n)
        if name == 'INDEXES':
            refuse('E_SQL_SHAPE',
                   'INDEXES yields a list of keys, and a SQL expression is a scalar',
                   n.pos)
        if name == 'ABORT':
            refuse('E_SQL_UNSUPPORTED',
                   'ABORT raises an error, which is a control-flow effect and not a '
                   'value a SQL expression can be', n.pos)
        if name in ('IF', 'COND'):
            return self._conditional(n)

        n = self._rewrite_regex(n)

        args = []
        for i, arg in enumerate(n.args):
            f = self._node(arg)
            if f.kind == 'LIST':
                refuse('E_SQL_SHAPE',
                       f'argument to {name} is a list, and a SQL expression is a '
                       'scalar', arg.pos)
            self._require_argument_kind(name, f, arg.pos)
            if _is_numeric_argument(name, i):
                self._require_numeric_constant(arg)
                f = self._guard_numeric(f, arg)
            args.append(f)
        out = self._apply('funcs', name, args, n.pos)
        if name == 'CANON':
            out.canonical = True
        return out

    def _require_argument_kind(self, name: str, f: Fragment, pos: Pos) -> None:
        """Refuse an argument whose kind SEL would refuse.

        Nothing checked function arguments at all, and the operators' own guards
        did not reach them. Measured on MariaDB: 26 functions accepted a BOOL and
        26 accepted a BIN where SEL raises. ``UPPER(BLOB)`` answered '1' on three
        servers and '\\X31' on PostgreSQL; ``LEN(BLOB)`` answered 1 on three and 4
        on PostgreSQL. Every one of those is a translation reporting success for
        an expression SEL has no answer for.

        A column is where this bites, which is why the constant check could not
        cover it: ``BLOB + 1`` has no value to hand the evaluator, and only the
        declared kind says anything.
        """
        if f.kind == 'BOOL' and name not in BOOL_ARGUMENT_OK:
            refuse('E_SQL_SHAPE',
                   f'{name} does not take a BOOL argument; SEL raises here rather '
                   'than reading a boolean as text or as 1', pos)
        if f.kind == 'BIN' and name not in BIN_ARGUMENT_OK:
            refuse('E_SQL_SHAPE',
                   f'{name} reads its argument as text, and this is BIN; SEL raises '
                   'here rather than reinterpreting bytes as characters', pos)

    def _rewrite_regex(self, n: Node) -> Node:
        """Put a regex pattern through the language's own rewriter before it is
        emitted.

        Spec §7.8 expands \\d, \\w and \\s into explicit ASCII classes rather than
        passing them through, because otherwise a library flag decides what they
        mean -- and MariaDB's engine decides differently. Verified on 11.8:
        '٣' REGEXP '^\\d$' is 1 there and FALSE in SEL.

        The rewriter is sel.builtins.regex's own. A copy here would be a second
        thing to keep in step, and it would fail silently when they drifted.

        Both the pattern and the flags must be literals: a pattern read from a
        column cannot be rewritten, and the flag selects the template.
        """
        if n.name not in _REGEX_AT:
            return n
        at = _REGEX_AT[n.name]
        pat = n.args[at] if at < len(n.args) else None
        if pat is None or pat.t != 'text':
            refuse('E_SQL_UNSUPPORTED',
                   f'{n.name} needs a literal pattern here: SEL rewrites \\d, \\w and '
                   '\\s into explicit ASCII classes before matching, and a pattern '
                   'that is not known until the query runs cannot be rewritten',
                   pat.pos if pat is not None else n.pos)
        # portable_source raises SelError for a pattern outside the portable
        # subset, and SEL raises it too -- but only when the call is reached.
        # Translation walks every branch, so a pattern in a branch the evaluator
        # never takes reaches here anyway, and a SelError escaping Sql.translate
        # would break the one thing try_translate() promises: that a rule which
        # cannot be pushed down returns None rather than raising. Found by the
        # fuzz lane, as a fatal error in the middle of a run.
        try:
            # PHP names this portableSource, a one-line alias for validate();
            # this host has only validate, whose docstring says the same thing --
            # "validates and rewrites in one pass, returning source that means
            # the same thing to every engine". Calling it directly rather than
            # adding an alias keeps tools/check-api.sh's host surface unchanged.
            source = _regex.validate(str(pat.v), pat.pos)
        except SelError as e:
            refuse('E_SQL_UNSUPPORTED',
                   f"{n.name}'s pattern is not in SEL's portable subset, so there is "
                   f'nothing to translate: {e.message}', pat.pos)

        # Dotall is permanently on in SEL (spec §7.8) and off by default in the
        # server, so every pattern carries (?s). The modifier goes in the pattern
        # rather than in the template because the flag argument is not something
        # the template should see: selecting an arity-keyed template by argument
        # count gave every three-argument call the case-insensitive form, and left
        # the flag bound as a parameter nothing emitted.
        inline = '(?s)'

        flag_at = 3 if n.name == 'RREPLACE' else 2
        args = list(n.args)
        if flag_at >= len(args):
            args[at] = _lit_node('text', inline + source, args[at].pos)
            return _replace(n, args=args)
        flags = args[flag_at]
        if flags.t != 'text':
            refuse('E_SQL_UNSUPPORTED',
                   f'{n.name} needs literal flags here: their content selects the '
                   'mapping, so they have to be known before the query runs',
                   flags.pos)

        # The flag string's CONTENT chooses the template. Choosing by argument
        # count instead meant every three-argument call got the case-insensitive
        # form, so RMATCH(p, s, "") matched case-insensitively where SEL does not,
        # and RMATCH(p, s, "zzz") compiled happily where SEL raises E_BAD_ARG. An
        # empty flag string is dropped so the two-argument template applies.
        text = str(flags.v)
        # Spelled as the two strings that pass rather than as a case fold:
        # PHP's strtolower is ASCII-only and str.lower() is not ("İ".lower() is
        # two code points), and `strtolower($t) !== 'i'` admits exactly "i" and
        # "I". Naming them is byte-exact and needs no ascii_lower.
        if text not in ('', 'i', 'I'):
            refuse('E_SQL_UNSUPPORTED',
                   f'{n.name} accepts only the i flag here, and SEL accepts only i '
                   'at all; ' + quote_dump(text) + ' is not it', flags.pos)
        if text != '':
            # The evaluator refuses i on a pattern with non-ASCII literals,
            # because case folding above ASCII is the one thing PCRE and
            # ECMAScript cannot be made to agree on. A translation that accepted
            # it would disagree with the host that refused it.
            for cp in utf8.to_code_points(source):
                if cp > 0x7f:
                    refuse('E_SQL_UNSUPPORTED',
                           'the i flag needs an ASCII-only pattern, which SEL '
                           'requires for the same reason and refuses here too',
                           flags.pos)
            inline = '(?si)'
        args[at] = _lit_node('text', inline + source, args[at].pos)
        del args[flag_at]                    # folded into the pattern
        return _replace(n, args=args)

    def _conditional(self, n: Node) -> Fragment:
        """IF and COND are the same construct: condition/result pairs and a
        default. IF's two-argument form defaults to TEXT "" exactly as spec §7.2
        says, so one builder covers both and the CASE skeleton has one shape.
        """
        args = list(n.args)
        if n.name == 'IF' and len(args) == 2:
            args.append(_lit_node('text', '', n.pos))

        branch_tpl = self._skeleton('caseBranch', n.pos)
        case_tpl = self._skeleton('case', n.pos)

        branches = []
        results = []
        last = len(args) - 1
        for i in range(0, last, 2):
            cond = self._require_bool(self._node(args[i]), args[i].pos, n.name)
            then = self._node(args[i + 1])
            results.append(then)
            branches.append(Fragment(
                self._fill_named(branch_tpl, {'cond': [cond], 'then': [then]}, n.pos),
                'UNKNOWN', self.dialect))
        else_ = self._node(args[last])
        results.append(else_)

        # The branches are joined by the skeleton's own spacing, not by ", ".
        joined: list[Any] = []
        for i, b in enumerate(branches):
            if i > 0:
                joined.append(' ')
            joined.append(b)
        parts = self._fill_named(case_tpl, {'branches': joined, 'else': [else_]}, n.pos)
        return Fragment(parts, _unify(results, n.pos), self.dialect)

    # --- aggregates: docs/internals/sql-translation.md §7 ------------------------------
    #
    # Lowering runs inside this walk rather than as an AST pass before it. Two of
    # the three shapes have to render -- a relation becomes a subquery, which is
    # characters -- and the third needs the dialect's operator templates, which a
    # tree rewrite has no access to.

    def _binder(self, name: str) -> Binder | None:
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return None

    def _group_key(self, src: dict[str, Any], group: dict[str, Any], projected: bool = False) -> Fragment:
        """A group key, rendered as the GROUP BY expression itself -- wherever
        it appears: the clause, the ``_K`` projection, a HAVING. A TEXT key is
        cast and collated the way the ``$`` family compares text, because the
        evaluator groups by the key's exact bytes and a case-insensitive
        collation would merge groups it keeps apart (review 2026-09-15
        finding L; MariaDB's default merged 'A' and 'a'). The result is marked
        exact so a comparison over it does not wrap it a second time --
        MySQL's only_full_group_by accepts a projected or compared key only as
        the identical expression."""
        key = self._with_row(src, group['binder'], lambda: self._node(group['node']))
        identity = self._identity_group_key(group['node'], key)
        if projected and key.kind == 'NUM':
            return Fragment(['MIN(', *key.parts, ')'], 'NUM', self.dialect,
                            key.params, key.param_kinds, key.caveats)
        return identity

    def _identity_group_key(self, node: Node, fragment: Fragment) -> Fragment:
        if fragment.canonical and fragment.kind == 'NUM':
            # One spelling per value, so the value's equality is the spelling's:
            # grouped and compared as the number it is, which keeps it a number
            # for whatever sorts it afterwards.
            return fragment
        if fragment.kind == 'UNKNOWN':
            refuse('E_SQL_SHAPE', 'group keys require proven scalar identity', node.pos)
        if fragment.kind == 'NUM':
            if node.t not in ('var', 'index', 'num'):
                refuse('E_SQL_SHAPE', 'computed numeric group keys do not preserve SEL identity', node.pos)
            numeric = Fragment(fragment.parts, 'NUM', self.dialect, fragment.params,
                               fragment.param_kinds, fragment.caveats)
            wrapped = self.emit.text_operand(numeric)
            return Fragment(wrapped.parts, 'TEXT', self.dialect, wrapped.params,
                            wrapped.param_kinds, wrapped.caveats, True, False, False)
        return self._collated_key(fragment)

    def _order_key(self, fragment: Fragment, pos: Pos) -> Fragment:
        """A sort key, as SEL's sort compares it. SEL sorts numbers as numbers
        and other text by its bytes -- and number-shaped TEXT as a number, which
        SQL's ORDER BY cannot: it sorts a text key by its bytes throughout, so
        "10" comes before "9" (SEL-0060). A NUM key sorts as SEL sorts it. A
        canonical number the dialect can only carry as text is always a number
        to SEL, so sorting it in SQL is simply wrong: refused, and the planner
        sorts in memory (SEL-0058). Any other TEXT or UNKNOWN key is sorted
        anyway, declared text-order, and refused under strict."""
        if fragment.kind == 'NUM':
            return fragment
        if fragment.canonical:
            refuse('E_SQL_UNSUPPORTED',
                   f'CANON is text on {self.dialect}, which SQL sorts by its bytes, and SEL '
                   'sorts it as the number it is; sort it in memory', pos)
        if fragment.kind in ('TEXT', 'UNKNOWN'):
            if self.strict:
                refuse('E_SQL_UNSUPPORTED',
                       'a text key sorts by its bytes in SQL, where SEL sorts number-shaped '
                       'text as numbers (text-order); strict mode refuses that', pos)
            self.caveats['text-order'] = True
        return self._collated_key(fragment)

    def _collated_key(self, fragment: Fragment) -> Fragment:
        if fragment.kind != 'TEXT' or fragment.exact:
            return fragment
        wrapped = self.emit.text_operand(fragment)
        return Fragment(wrapped.parts, 'TEXT', self.dialect, wrapped.params, wrapped.param_kinds,
                        wrapped.caveats, True, False, False)

    def _from_binder(self, b: Binder, n: Node) -> Fragment:
        if b.shape == Binder.NODE:
            return self._node(b.payload)
        if b.shape == Binder.KEY:
            # Inside a row already: bind the key's own binder to that row and
            # render the key there, then collate it exactly as the GROUP BY does.
            group, row = b.payload
            self.frames.append({group['binder']: row})
            try:
                key = self._node(group['node'])
            finally:
                self.frames.pop()
            collated = self._identity_group_key(group['node'], key)
            if key.kind == 'NUM':
                # Textual identity must not change arithmetic/order over _K.
                out = Fragment(['MIN(', *key.parts, ')'], 'NUM', self.dialect,
                               key.params, key.param_kinds, key.caveats)
                out.canonical = key.canonical
                return out
            # In a HAVING, MariaDB and MySQL resolve a column only against the
            # GROUP BY columns and the select list, not against an equal
            # expression: `HAVING CAST(cat ...) COLLATE ...` is "unknown
            # column cat" once the grouping is the collated expression. The
            # key is constant within its group, so MIN of it IS the key, and
            # an aggregate is what every server lets a HAVING name.
            # Nested key projections (e.g. LEN(_K)) also require an aggregate
            # under MySQL ONLY_FULL_GROUP_BY. Direct keys use _group_key.
            if collated is not key:
                out = Fragment(['MIN(', *collated.parts, ')'], 'TEXT', self.dialect,
                               collated.params, collated.param_kinds, collated.caveats,
                               True, False, False)
                out.canonical = key.canonical
                return out
            return collated
        if b.shape == Binder.COLUMN:
            return self._column_ref(b.payload)
        if b.shape == Binder.GROUP:
            refuse('E_SQL_SHAPE',
                   f"{n.name} is the list of a bucket's members, which is not a value "
                   'SQL has; count it (COUNT), sum over it (SUM), or name the group key (_K)',
                   n.pos)
        if b.shape == Binder.PROJECTED:
            refuse('E_SQL_SHAPE',
                   f'{n.name} is the record the projection built, which is a map in SEL '
                   'and not one value; name the field you mean', n.pos)
        if b.shape == Binder.ROW:
            rel = b.payload
            # The guard `IN` got and nothing else did. A row of a relation with
            # more than one field is a MAP in SEL, and a map is not the value of
            # one of its fields: `ANY(ITEMS, _ $== "AB-1000")` is [] in SEL,
            # because comparing a map against text is structurally false for
            # every row, and was [1] on all four servers. That is byte for byte
            # the multi-field IN defect, reached through the bare binder instead.
            # A one-field relation is genuinely a scalar and keeps working.
            if len(rel['fields']) > 1:
                refuse('E_SQL_SHAPE',
                       f'{n.name} is a row of a relation with {len(rel["fields"])} '
                       'fields, which is a map in SEL and not one value; name the '
                       'field you mean', n.pos)
            scalar = ascii_upper(str(rel['scalar'])) if rel.get('scalar') is not None else None
            if scalar is None or scalar not in rel['fields']:
                refuse('E_SQL_SHAPE',
                       f'{n.name} names a row, and the relation does not say which of '
                       'its fields a bare reference means; give the binding a '
                       '"scalar", or index the field you want', n.pos)
            field = rel['fields'][scalar]
            if field.get('raw') is not None or self.statement_plan is None:
                return self._column_ref(field)
            return self._column_ref({**field, 'table': self._relation_table_alias(rel, n.name)})
        refuse('E_SQL_SHAPE', str(b.reason), n.pos)

    def _index_binder(self, b: Binder, name: str, key: str, n: Node) -> Fragment:
        if b.shape == Binder.GROUP:
            # The group is the list of its members: indexing it by a field name
            # is E_NO_KEY in SEL, and by a position asks for a member SQL cannot
            # single out. Either way the field is read inside an aggregate over
            # the members, SUM(g, _["amount"]), and nowhere else.
            refuse('E_SQL_SHAPE',
                   f'{name}["{key}"] indexes the list of a bucket\'s members, which SEL '
                   'refuses (E_NO_KEY); read a member\'s field inside an aggregate over '
                   f'the group, SUM({name}, _["{key}"])', n.pos)
        if b.shape == Binder.PROJECTED:
            # After the projection a row is the record it built, and has the
            # projection's fields under their aliases and nothing else -- not
            # the source's columns, which SEL no longer has (E_NO_KEY).
            relation, projections = b.payload
            proj = next((p for p in projections if p.get('alias') == key), None)
            if proj is None:
                proj = next((p for p in projections if p.get('alias') is not None
                             and ascii_upper(p['alias']) == ascii_upper(key)), None)
            if proj is None:
                known = sorted(p['alias'] for p in projections if p.get('alias') is not None)
                refuse('E_SQL_SHAPE',
                       f'{name}["{key}"] is not a field of the projection'
                       + ('; it has ' + ', '.join(known) if known else ''), n.pos)
            src = {'relation': relation, 'filters': [], 'pos': n.pos}
            if proj.get('group_key') is not None:
                return self._from_binder(Binder.key((proj['group_key'], Binder.row(relation))), n)
            return self._with_group(src, proj['binder'], lambda: self._node(proj['node']))
        if b.shape == Binder.ROW:
            if _list_key(key) is not None:
                refuse('E_SQL_SHAPE',
                       f'{name}[{key}] asks for a row by position, and a relation has '
                       'no first row without an ORDER BY that nothing here can supply',
                       n.pos)
            field = ascii_upper(key)
            if b.joined and self.statement_plan is not None and self.statement_plan.joins:
                matches = []
                sources = [(self.statement_plan.source_relation, self.statement_plan.source_name)]
                sources.extend((join.source_relation, join.source_name)
                               for join in self.statement_plan.joins)
                for relation, label in sources:
                    candidate = (relation.get('fields') or {}).get(field)
                    if candidate is not None:
                        matches.append({**candidate,
                                        'table': self._relation_table_alias(relation, label)})
                if len(matches) > 1:
                    refuse('E_SQL_SHAPE', f'field "{key}" is ambiguous across joined relations', n.pos)
                if len(matches) == 1:
                    return self._column_ref(matches[0])
            if field not in b.payload['fields']:
                known = sorted(b.payload['fields'])
                tail = ('; it declares none' if not known
                        else '; it has ' + ', '.join(known))
                refuse('E_SQL_BINDING',
                       f'{name}["{key}"] is not a field of that relation' + tail, n.pos)
            field_spec = b.payload['fields'][field]
            if field_spec.get('raw') is not None:
                return self._column_ref(field_spec)
            if (self.statement_plan is not None
                    and (self.statement_plan.joins or self.statement_plan.source_subquery is not None)):
                return self._column_ref({**field_spec,
                                         'table': self._relation_table_alias(b.payload, name)})
            return self._column_ref(field_spec)
        if b.shape == Binder.NODE:
            elem = _child_of(b.payload, key)
            if elem is None:
                refuse('E_SQL_BINDING',
                       f'{name}["{key}"] is not a key of that element', n.pos)
            return self._node(elem)
        refuse('E_SQL_SHAPE',
               f'{name} names a single column, which has no parts to index', n.pos)

    def _source(self, src: Any, call: Node) -> dict[str, Any]:
        """Classify an aggregate's first argument into one of the three shapes,
        absorbing any FILTER on the way through. Recursive, so
        ``FILTER(FILTER(L, p1), p2)`` conjoins both predicates over L.
        """
        if src.t == 'call' and src.name == 'FILTER':
            f_binder, f_body = _agg_shape(src)
            inner = self._source(src.args[0], call)
            inner['filters'].append({'binder': f_binder, 'body': f_body})
            return inner
        if src.t == 'call' and src.name == 'MAP':
            refuse('E_SQL_UNSUPPORTED',
                   'MAP as the thing an aggregate iterates is not translated: unlike '
                   'FILTER, which only decides whether an element takes part, MAP '
                   'changes what the element is, so the two binders mean different '
                   'things and binding both to one element is not enough. See '
                   'docs/internals/sql-translation.md §7.5', src.pos)

        # Built by a helper rather than by a merge: PHP's array union keeps the
        # LEFT operand for a duplicated key, so `$base + [… 'scalarRule' => true]`
        # left scalarRule permanently false and made COUNT of a scalar answer 1
        # where the evaluator answers 0.
        if src.t == 'list':
            return _static_source({str(i + 1): Binder.node(item)
                                   for i, item in enumerate(src.items)})
        if src.t == 'clist':
            return _static_source({k: Binder.node(v) for k, v in src.entries})

        if src.t == 'var':
            bound = self._binder(src.name)
            if bound is not None:
                if bound.shape == Binder.NODE:
                    return self._source(bound.payload, call)
                if bound.shape == Binder.NONE:
                    refuse('E_SQL_SHAPE', str(bound.reason), src.pos)
                # A bucket's members are iterated by COUNT and SUM alone
                # (_call), as one aggregate over the grouped rows; ALL, ANY and
                # the rest would each need a correlated subquery this layer
                # does not build.
                if bound.shape == Binder.GROUP:
                    refuse('E_SQL_SHAPE',
                           f"{src.name} is the list of a bucket's members, over which "
                           'only COUNT and SUM are translated', src.pos)
                if bound.shape == Binder.PROJECTED:
                    refuse('E_SQL_SHAPE',
                           f'{src.name} is the record the projection built, a map with '
                           'one child per field; SQL has no way to iterate or count that',
                           src.pos)
                # A column is one value, so it is a one-element list containing
                # itself -- spec §7.3, the same rule the evaluator applies. This
                # is what makes ALL(V, ALL(V, …)) work.
                #
                # A multi-field ROW is not one value, and applying the scalar rule
                # to it answered for a different question: COUNT(I) folded to 0
                # where SEL says 3, HAS(I, "QTY") to FALSE where SEL says TRUE,
                # and ANY(I, …) iterated nothing where SEL iterates the row's
                # values.
                if bound.shape == Binder.ROW and len(bound.payload['fields']) > 1:
                    refuse('E_SQL_SHAPE',
                           f'{src.name} is a row of a multi-field relation, which is '
                           'a map with one child per field; SQL has no way to iterate '
                           'or count that', src.pos)
                return _static_source({'1': bound}, True)
            b = self.bindings.get(src.name, src.pos)
            if b['kind'] == 'relation':
                return {'shape': 'relation', 'relation': b, 'filters': [],
                        'scalarRule': False}
            if b['kind'] == 'columns':
                return {'shape': 'columns',
                        'elements': {str(i + 1): Binder.column(item)
                                     for i, item in enumerate(b['items'])},
                        'filters': [], 'scalarRule': False}
            if b['kind'] == 'value':
                v = b['value']
                # A scalar value binding is one value, so the scalar rule applies
                # to it exactly as it does to a column.
                return _static_source(self._value_elements(b, src.pos),
                                      v.size() == 0 and not v.is_none())

        # Anything else that is one value: the scalar rule again -- but only if it
        # IS one value. A call that yields a list is not, and treating one as a
        # scalar is how three map refusals were bypassed:
        # COUNT(SPLIT("a,b", ",")) folded to 0 where SEL says 2, and
        # HAS(SPLIT(…), 1) to FALSE where SEL says TRUE. The same functions refuse
        # correctly under ALL, SUM, JOIN and FILTER, which is what made it hard to
        # see. The refusal string in the dialect document says "yields a list, and
        # a SQL expression is a scalar"; this is the path that never asked it.
        if src.t == 'call' and src.name in YIELDS_LIST:
            refuse('E_SQL_SHAPE',
                   f'{src.name} yields a list, and the scalar rule does not apply to '
                   'it; SQL has no way to count or index what it produces', src.pos)
        # And a call is one value only once it has rendered as one: COUNT
        # folds a scalar to 0 without rendering it, and IF(TRUE, LIST(1, 2),
        # 3) is a list SEL counts as 2 -- a refusal, not a 0.
        if src.t == 'call':
            self._node(src)
        return _static_source({'1': Binder.node(src)}, True)

    def _value_elements(self, b: dict[str, Any], pos: Pos) -> dict[str, Binder]:
        """A ``value`` binding holds Values, not AST nodes, so its children are
        synthesised into nodes before binding. A child with children becomes a
        ``clist``; a scalar becomes the node kind its declared type asks for,
        which is the same rule _declared_kind applies to the value as a whole --
        so what decides quoting is stated once.
        """
        v = b['value']
        if v.size() == 0:
            # A NONE with no children is genuinely empty -- what FILTER returns
            # when nothing matched. A scalar is a one-element list of itself.
            if v.is_none():
                return {}
            return {'1': Binder.node(self._value_node(v, b, pos))}
        return {k: Binder.node(self._value_node(child, b, pos))
                for k, child in v.entries()}

    def _value_node(self, v: Value, b: dict[str, Any], pos: Pos) -> Any:
        if v.size() > 0:
            return _normalise.CList(pos, [(k, self._value_node(child, b, pos))
                                          for k, child in v.entries()])
        if v.is_bool():
            return _lit_node('bool', v.as_bool(pos), pos)
        if v.is_bin():
            refuse('E_SQL_SHAPE',
                   'a BIN element of a value binding has no literal node to become; '
                   'bind it as a column, or convert it before translating', pos)
        return _lit_node('num' if b.get('type') == 'NUM' else 'text',
                         v.as_text(pos), pos)

    def _aggregate(self, n: Node) -> Fragment:
        name = n.name
        if name in ('MAP', 'FILTER'):
            refuse('E_SQL_SHAPE',
                   f'{name} yields a list, and a SQL expression is a scalar; it can '
                   'only be the thing another aggregate iterates', n.pos)
        if name == 'JOIN':
            return self._join_aggregate(n)

        binder_name, body = _agg_shape(n)
        src = self._source(n.args[0], n)

        if src['shape'] == 'relation':
            rendered = self._with_row(src, binder_name,
                                      lambda: self._agg_body(name, body, src, n))
            return self._relation_aggregate(name, src['relation'], rendered, n)

        parts = [self._with_element(src, binder_name, elem, str(key), n,
                                    lambda: self._agg_body(name, body, src, n))
                 for key, elem in src['elements'].items()]
        if not parts:
            if name == 'ALL':                          # spec §7.3's empty cases
                return self._literal(Value.bool(True), 'BOOL')
            if name == 'ANY':
                return self._literal(Value.bool(False), 'BOOL')
            return self._literal(Value.num('0'), 'NUM')
        if len(parts) == 1:
            return parts[0]
        return self.fold_pairwise(AGG_FOLD[name], parts, n.pos)

    def _agg_body(self, name: str, body: Node, src: dict[str, Any], n: Node) -> Fragment:
        """Render the body, and combine it with any absorbed FILTER predicates.

        The four rewrites of §7.5, and each is NULL-safe under the skeletons of
        §7.3: for ALL a NULL predicate with a FALSE body gives a NULL result,
        which ``IS NOT TRUE`` includes -- the element is treated as having been in
        the filter and having failed, which is the conservative reading.
        """
        q = self._node(body)
        q = (self._require_num(q, body.pos, name) if name == 'SUM'
             else self._require_bool(q, body.pos, name))

        for f in src['filters']:
            p = self._require_bool(self._node(f['body']), f['body'].pos, 'FILTER')
            if name == 'SUM':
                q = self._case_when(p, q, self._literal(Value.num('0'), 'NUM'), n.pos)
                continue
            if name == 'ALL':
                q = self._apply('ops', 'OR',
                                [self._apply('ops', 'NOT', [p], n.pos), q], n.pos)
            else:
                q = self._apply('ops', 'AND', [p, q], n.pos)
        return q

    def _with_element(self, src: dict[str, Any], binder_name: str, elem: Binder,
                      key: str, n: Node, render: Callable[[], Fragment]) -> Fragment:
        """Push a frame for one element of a static or columns unroll and render.

        Every absorbed FILTER's binder is bound to the same element, which is what
        makes absorption three lines rather than a substitution pass -- see §7.5.
        """
        frame = {binder_name: elem, '_K': Binder.node(_lit_node('text', key, n.pos))}
        for f in src['filters']:
            frame[f['binder']] = elem
        self.frames.append(frame)
        try:
            return render()
        finally:
            self.frames.pop()

    def _with_row(self, src: dict[str, Any], binder_name: str,
                  render: Callable[[], Fragment]) -> Fragment:
        """The same for a relation, where there is one frame rather than one per
        element.

        ``_K`` is in scope only to refuse: a row has no portable key, and
        inventing one -- ROW_NUMBER(), the primary key -- would be a guess about
        the schema this layer is careful never to make.
        """
        # A relation nested inside itself reuses its own fixed alias, and the
        # inner FROM shadows the outer one, so the predicate is constantly false
        # and all four servers answered [] where SEL answers [1,4,5].
        # Bindings.check_aliases dedupes across *distinct* binding names, and this
        # is one name, so it could never fire. Re-aliasing is not available as a
        # fix: `correlate` is host-written SQL that spells the alias itself. Two
        # different relations with distinct aliases nest correctly.
        alias = _relation_alias(src['relation'])
        for frame in self.frames:
            for binder in frame.values():
                if binder.shape == Binder.ROW \
                        and _relation_alias(binder.payload) == alias:
                    refuse('E_SQL_SHAPE',
                           f'this relation is already open as {alias} further out, '
                           'and a subquery reusing its own alias shadows the outer '
                           'row rather than comparing against it; the correlation '
                           'names the alias, so it cannot be renamed here',
                           src.get('pos'))

        row = Binder.row(src['relation'])
        # The row of a joined statement: a field read through it resolves across
        # the sides (ambiguous when both have it), whatever the binder is called.
        # Gating that on the name `_` let `MAP(r, RECORD("name", r["name"]))`
        # after a LINK resolve to the left side where `run()` raises E_NO_KEY.
        if self.statement_plan is not None and self.statement_plan.joins:
            row.joined = True
        k_binder = Binder.none('a row of a relation has no key: SQL rows are '
                               'unordered and unkeyed unless the schema says '
                               'otherwise, and guessing which column is the key '
                               'is not something this layer does')
        frame = {binder_name: row, '_K': k_binder}
        for f in src['filters']:
            frame[f['binder']] = row
        # After a LINK only the row is in scope (spec §7.4): the binders are
        # scoped to its predicate, and the evaluator raises E_UNDEF_VAR for
        # ``C["id"]`` in a later step -- the joined row carries them as keys,
        # not as names. This frame used to bind ``_1``, ``_2``, the relations'
        # names and the right binder for every later step, so
        # ``FILTER(C["id"] > 1)`` translated where ``run()`` fails (review
        # 2026-09-15 finding W2).
        self.frames.append(frame)
        try:
            return render()
        finally:
            self.frames.pop()

    def _with_group(self, src: dict[str, Any], binder_name: str,
                    render: Callable[[], Fragment]) -> Fragment:
        """The frame for a bucket's own body: the projection, and a FILTER or a
        sort over the groups before it. The binder is the group -- the list of
        its members, which only COUNT and SUM read (_call) -- and ``_K`` is the
        group key, when there is one key to be it. This is the one place ``_K``
        is a group key: before the bucket it is a source row's position, after
        the projection the projected row's, and SQL has neither (review
        2026-09-15 finding K).
        """
        group_by = self.statement_plan.group_by if self.statement_plan is not None else None
        if group_by is not None and len(group_by) == 1:
            k_binder = Binder.key((group_by[0], Binder.row(src['relation'])))
        else:
            k_binder = Binder.none('the key of a bucket over several keys is a list, '
                                   'which SQL has no value for; name one key')
        self.frames.append({binder_name: Binder.group(src['relation']), '_K': k_binder})
        try:
            return render()
        finally:
            self.frames.pop()

    def _with_projected(self, src: dict[str, Any], binder_name: str,
                        render: Callable[[], Fragment]) -> Fragment:
        """The frame for a step after a bucket's projection: a FILTER (HAVING)
        or a sort over the projected rows. The binder is the record the
        projection built, whose fields are the projection's aliases; ``_K`` is
        its position in the renumbered list, which SQL does not have.
        """
        self.frames.append({
            binder_name: Binder.projected(src['relation'], self.statement_plan.projections or []),
            '_K': Binder.none('after a projection the rows are a list renumbered from "1", '
                              'and SQL has no row position to compare against'),
        })
        try:
            return render()
        finally:
            self.frames.pop()

    def _with_join_binders(self, plan: RelationalPlan, join: JoinPlan,
                           render: Callable[[], Fragment]) -> Fragment:
        left = Binder.row(plan.source_relation)
        right = Binder.row(join.source_relation)
        frame = {
            '_': left,
            '_1': left,
            plan.source_name: left,
            '_2': right,
            join.left_binder: left,
            join.right_binder: right,
            join.source_name: right,
        }
        if plan.source_alias:
            frame[plan.source_alias] = left
        if join.source_alias:
            frame[join.source_alias] = right
        self.frames.append(frame)
        try:
            return render()
        finally:
            self.frames.pop()

    def _relation_aggregate(self, name: str, rel: dict[str, Any],
                            body: Fragment, n: Node) -> Fragment:
        is_separate = (rel.get('prefilter') == 'separate'
                       or (rel.get('prefilter') is None and getattr(body, 'separate_prefilter', False)))
        if name == 'ANY' and getattr(body, 'prefilter', None) is not None and is_separate:
            pre = Fragment(
                self._fill_named(self._skeleton('prefilter', n.pos),
                                 _slots(self._relation_slots(rel), {'body': [body.prefilter]}), n.pos),
                AGG_RETURNS[name], self.dialect)
            main = Fragment(
                self._fill_named(self._skeleton(AGG_SKELETON[name], n.pos),
                                 _slots(self._relation_slots(rel), {'body': [body]}), n.pos),
                AGG_RETURNS[name], self.dialect)
            return self._apply('ops', 'AND', [pre, main], n.pos)
        return Fragment(
            self._fill_named(self._skeleton(AGG_SKELETON[name], n.pos),
                             _slots(self._relation_slots(rel), {'body': [body]}), n.pos),
            AGG_RETURNS[name], self.dialect)

    def _relation_slots(self, rel: dict[str, Any]) -> dict[str, list[str]]:
        """``{from}`` is the table and alias, or a query the binding carries;
        ``{corr}`` is the join back to the outer row, or the dialect's TRUE when
        the binding has none -- an uncorrelated relation is a subquery over the
        whole table, which is legal and occasionally what you want.
        """
        frm = rel['from']
        out = (str(frm['raw']) if isinstance(frm, dict) and 'raw' in frm
               else self.emit.ident(str(frm)))
        if rel.get('alias'):
            out += ' ' + self.emit.ident(str(rel['alias']))
        corr = rel.get('correlate')
        return {'from': [out],
                'corr': [str(corr['raw']) if corr else str(self.emit.lex('true'))]}

    def _count(self, n: Node) -> Fragment:
        src = self._source(n.args[0], n)

        # COUNT is the number of children, so the scalar rule does not apply to
        # it: spec §7.4 says a value with no children counts 0, where §7.3's
        # one-element rule is about what an aggregate iterates.
        if src['shape'] != 'relation' and src['scalarRule'] and not src['filters']:
            return self._literal(Value.num('0'), 'NUM')
        if src['filters']:
            # COUNT(FILTER(L, p)) is SUM(L, CASE WHEN p THEN 1 ELSE 0 END).
            body = _lit_node('num', '1', n.pos)
            if src['shape'] == 'relation':
                rendered = self._with_row(src, '_',
                                          lambda: self._agg_body('SUM', body, src, n))
                return self._relation_aggregate('SUM', src['relation'], rendered, n)
            parts = [self._with_element(src, '_', elem, str(key), n,
                                        lambda: self._agg_body('SUM', body, src, n))
                     for key, elem in src['elements'].items()]
            if not parts:
                return self._literal(Value.num('0'), 'NUM')
            return parts[0] if len(parts) == 1 else self.fold_pairwise('+', parts, n.pos)
        if src['shape'] == 'relation':
            return Fragment(
                self._fill_named(self._skeleton('count', n.pos),
                                 self._relation_slots(src['relation']), n.pos),
                'NUM', self.dialect)
        return self._literal(Value.num(str(len(src['elements']))), 'NUM')

    def _has(self, n: Node) -> Fragment:
        if n.args[1].t not in ('text', 'num'):
            refuse('E_SQL_SHAPE',
                   'HAS needs a constant key here: which column it asks about has to '
                   'be known before the query runs', n.args[1].pos)
        key = str(n.args[1].v)
        src = self._source(n.args[0], n)
        if src['filters']:
            refuse('E_SQL_SHAPE',
                   'HAS over a FILTER would have to know at translation time which '
                   'elements the filter kept', n.pos)
        # A relation is a list of row maps, so its keys are "1", "2", … and never
        # a field name -- the row oracle's own load_context builds it that way.
        # Answering from the declared fields asked a different question and got
        # both directions wrong: HAS(ITEMS, "QTY") was TRUE where SEL says FALSE,
        # and HAS(SKUS, "1") was FALSE where SEL says TRUE. The positional
        # direction cannot be answered here at all -- it needs the row count -- so
        # refusal is the only honest outcome for either.
        if src['shape'] == 'relation':
            refuse('E_SQL_SHAPE',
                   'HAS over a relation asks whether it has a key, and a relation is '
                   'a list of rows whose keys are positions; the answer needs the row '
                   'count, which no expression here knows', n.pos)
        found = not src['scalarRule'] and key in src['elements']
        return self._literal(Value.bool(found), 'BOOL')

    def _join_aggregate(self, n: Node) -> Fragment:
        """JOIN is strict, not an aggregate: its second argument is a separator.

        Folded pairwise through the dialect's own concatenation, because ``&`` is
        what SEL's JOIN is, and a variadic concat would need a lexical key spelled
        two ways for the sake of one function.
        """
        src = self._source(n.args[0], n)
        if src['shape'] == 'relation':
            rel = src['relation']
            scalar = ascii_upper(str(rel['scalar'])) if rel.get('scalar') is not None else None
            if scalar is None or scalar not in rel['fields']:
                refuse('E_SQL_SHAPE',
                       'JOIN over a relation needs the binding to name a "scalar" '
                       'field', n.pos)
            body = self._column_ref(rel['fields'][scalar])
            skel = self._skeleton('join', n.pos)      # refuses with the map's reason
            return Fragment(
                self._fill_named(skel, _slots(self._relation_slots(rel),
                                              {'body': [body],
                                               'sep': [self._node(n.args[1])]}), n.pos),
                'TEXT', self.dialect)

        parts: list[Fragment] = []
        for key, elem in src['elements'].items():
            if parts:
                # Rendered per gap, not once and reused: see the note in
                # _in_operator on why splicing one Fragment twice breaks `params`.
                parts.append(self._node(n.args[1]))
            parts.append(self._with_element(
                src, '_', elem, str(key), n,
                lambda e=elem: self._from_binder(e, n)))
        if not parts:
            return self._literal(Value.text(''), 'TEXT')
        return parts[0] if len(parts) == 1 else self.fold_pairwise('&', parts, n.pos)

    def _case_when(self, cond: Fragment, then: Fragment, else_: Fragment,
                   pos: Pos) -> Fragment:
        branch = Fragment(
            self._fill_named(self._skeleton('caseBranch', pos),
                             {'cond': [cond], 'then': [then]}, pos),
            'UNKNOWN', self.dialect)
        return Fragment(
            self._fill_named(self._skeleton('case', pos),
                             {'branches': [branch], 'else': [else_]}, pos),
            then.kind if then.kind == else_.kind else 'UNKNOWN', self.dialect)

    # --- map application -----------------------------------------------------

    def _apply(self, section: str, key: str, args: list[Fragment], pos: Pos,
               variant: str | None = None) -> Fragment:
        """Look one entry up, check it, and fill it.

        Every refusal in the map arrives here, and a refusal spelled as a string
        in the map becomes the message the caller reads -- which is what makes one
        error class enough.
        """
        entry = _map.entry(self.dialect, section, key)
        what = f'the {key} operator' if section == 'ops' else key

        if entry is _map.MISSING or entry == _map.MISSING or entry is None:
            refuse('E_SQL_UNSUPPORTED',
                   f'{what} has no mapping in dialect {self.dialect}', pos)
        if isinstance(entry, str):
            refuse('E_SQL_UNSUPPORTED',
                   f'{what} has no mapping in dialect {self.dialect} — {entry}', pos)
        # `.get(k) is not None`, not `k in entry`: these transcribe isset(),
        # which is false for an explicit null. Reachable through a runtime
        # define() carrying a null field, which is the documented escape hatch.
        if entry.get('builder') is not None:
            return entry['builder'](self.emit, args, {'pos': pos})

        # The map's `arity` narrows SEL's own for this dialect, and sql/MAP.md
        # §4.1 gives it a job: "how PostgreSQL refuses the three-argument form its
        # POSITION cannot express while MariaDB still accepts it -- graceful
        # degradation as data, with no host code involved." There was no host code
        # involved, and there was no degradation either: the field was validated by
        # the generator and enforced by nobody, so a narrowed entry took the call
        # anyway and filled the template it had, silently dropping the arguments
        # the template did not name. Found the hour sqlite was written, by the
        # oracle: FIND("a","banana",3) is 4 in SEL and instr('banana','a') is 2.
        if entry.get('arity') is not None:
            lo, hi = entry['arity']
            if not lo <= len(args) <= hi:
                refuse('E_SQL_UNSUPPORTED',
                       f'{what} takes {lo} to {hi} argument(s) in dialect '
                       f'{self.dialect}, and this call has {len(args)}', pos)

        if entry.get('since') is not None \
                and not _map.version_at_least(_map.version(self.dialect), entry['since']):
            refuse('E_SQL_DIALECT',
                   f'{what} needs {self.dialect} {entry["since"]}, and this map '
                   f'assumes {_map.version(self.dialect)}', pos)
        if entry.get('caveat') is not None:
            if self.strict:
                refuse('E_SQL_UNSUPPORTED',
                       f'{what} maps to something that is not exactly equivalent '
                       f'({entry["caveat"]}), and strict mode refuses those', pos)
            self.caveats[entry['caveat']] = True

        tpl = self._template_of(entry, args, variant, what, pos)
        return Fragment(self.emit.fill(tpl, args, pos),
                        _ret_kind(entry, args, pos), self.dialect)

    def _template_of(self, entry: dict[str, Any], args: list[Fragment],
                     variant: str | None, what: str, pos: Pos) -> str:
        if entry.get('variants') is not None:
            if (variant is None or variant not in entry['variants']
                    or entry['variants'][variant] is None):
                shape = 'this shape' if variant is None else f'{variant} operands'
                refuse('E_SQL_UNSUPPORTED',
                       f'{what} has no mapping in dialect {self.dialect} for {shape}',
                       pos)
            return str(entry['variants'][variant])
        tpl = entry['tpl']
        if isinstance(tpl, str):
            return tpl
        n = str(len(args))
        # `*` is the fallback for counts the entry does not NAME, and naming a
        # count with null names it: sql/MAP.md §2 says null is a refusal without a
        # reason, so `{"1": None, "*": …}` withdraws the one-argument form and the
        # fallback must not rescue it. Membership rather than a None test is the
        # whole of that rule; without it the withdrawal was unwritable, and the
        # None reached the renderer and emitted the literal text `None` into SQL.
        if n not in tpl and '*' in tpl:
            n = '*'
        if n not in tpl or tpl[n] is None:
            refuse('E_SQL_UNSUPPORTED',
                   f'{what} has no mapping in dialect {self.dialect} for {n} '
                   'argument(s); it maps ' + ', '.join(sorted(tpl)), pos)
        return str(tpl[n])

    def _variant_for(self, op: str, args: list[Fragment]) -> str | None:
        """Which variant a family selects.

        Not in the data -- sql/MAP.md §4.3 fixes three selectors and every host
        implements them identically.
        """
        if op in _NUMERIC_OPS:
            return 'num' if args[0].kind == 'NUM' and args[1].kind == 'NUM' else 'coerce'
        if op in _TEXTUAL_OPS:
            return 'text'
        if op == '&':
            return 'bin' if args[0].kind == 'BIN' or args[1].kind == 'BIN' else 'text'
        return None

    # --- kind guards ---------------------------------------------------------

    def _guard_numeric(self, f: Fragment, n: Node) -> Fragment:
        """Wrap an operand the numeric context cannot be sure of.

        A constant is skipped, because ``_require_numeric_constant`` has just
        proved it IS a number -- guarding it would ask the server a question
        already answered here, and would cost a bound value a second parameter
        for the repeated slot. What is left is what could not be settled at
        translation time: columns, raw, relation fields.
        """
        if _constants.is_constant(n, self.const_names):
            return f
        guarded = self.emit.numeric_operand(f, n.pos)
        if guarded is not f:
            # The guard reads the text as the dialect's numericCast type, and
            # where that type fixes a scale the data's digits past it are gone
            # before anything else sees them (SEL-0059).
            self._scale_limited(n.pos, 'this operand is read as a number')
        return guarded

    def _numeric_cast_scale(self) -> int | None:
        """How many fractional digits the dialect's numericCast and
        numericGuard keep, or None where they keep every one (sql/MAP.md §3)."""
        cap = self.emit.lex('numericCastScale')
        return int(cap) if isinstance(cap, str) and cap.isascii() and cap.isdigit() else None

    def _scale_limited(self, pos: Pos, what: str) -> None:
        cap = self._numeric_cast_scale()
        if cap is None:
            return
        if self.strict:
            refuse('E_SQL_UNSUPPORTED',
                   f'{what} through a DECIMAL that keeps {cap} fractional digits, and a '
                   f'value with more loses them on {self.dialect} (scale-limit); strict '
                   'mode refuses that', pos)
        self.caveats['scale-limit'] = True

    def _coerce_scale_limits(self, pairs: list[tuple[Fragment, Node]]) -> None:
        """The coerce variant reads both operands through numericCast. A constant's
        scale is known and only one past the cap is truncated; a column's is
        not -- NUM says it is a number, not how many fractional digits it has."""
        cap = self._numeric_cast_scale()
        if cap is None:
            return
        for _f, node in pairs:
            if _constants.is_constant(node, self.const_names):
                if _constants.constant_scale(node, self.const_ctx) > cap:
                    self._scale_limited(node.pos, 'this constant is read as a number')
            else:
                self._scale_limited(node.pos, 'this operand is read as a number')

    def _require_numeric_constant(self, n: Node) -> None:
        """An operand in a numeric position whose value is knowable here.

        The kind guards it sits beside ask what the binding *declared*; this
        asks what the constant *is*, which is a different and stronger question
        wherever the answer is written down. See ``constants.require_numeric``
        for why refusing loses nothing, and for why it is never keyed on a
        declared kind.
        """
        if _constants.is_constant(n, self.const_names):
            _constants.require_numeric(n, self.const_ctx)

    def _require_not_bool(self, f: Fragment, pos: Pos, where: str) -> None:
        """A number was expected and a boolean cannot become one.

        UNKNOWN passes here: this guard is about kinds that make a number
        *impossible*, and an undeclared column is not one of them. What happens
        to an UNKNOWN operand afterwards is _guard_numeric's business, not this
        one's.
        """
        # spec §4: "BOOL and BIN are never numbers". The guard implemented the
        # first half of that sentence for a milestone: `BLOB + 1` translated, and
        # MariaDB answered 2.0 while MySQL answered 50 -- two servers, two
        # answers, neither SEL's, from a column the schema said was binary.
        if f.kind not in ('BOOL', 'BIN'):
            return
        what = 'a BOOL' if f.kind == 'BOOL' else 'a BIN'
        refuse('E_SQL_SHAPE',
               f'{where} reads its operands as numbers, and {what} is not one; SEL '
               'answers E_NOT_NUM here rather than coercing it', pos)

    def _require_not_bool_operand(self, f: Fragment, pos: Pos, where: str) -> None:
        """``&`` takes text or bytes, and a BOOL is neither -- spec §5.2 ends
        "BOOL is E_NOT_TEXT". It was left out of the arithmetic list because it is
        not arithmetic, and nothing else covered it: ``FLAG & NAME`` concatenated,
        and MariaDB answered '1a' where PostgreSQL answered 'truea'.
        """
        if f.kind != 'BOOL':
            return
        refuse('E_SQL_SHAPE',
               f'{where} reads its operands as text or bytes, and a BOOL is neither; '
               'SEL answers E_NOT_TEXT here rather than spelling it 1 or true', pos)

    def _require_bool(self, f: Fragment, pos: Pos, where: str) -> Fragment:
        # UNKNOWN used to pass, on the reasoning that an undeclared column may
        # well be boolean and the database is the one that knows. Measured, the
        # database does not know: MariaDB answers `1 AND TRUE` as TRUE, so an
        # undeclared column holding 1 matched a row SEL refuses with E_NOT_BOOL,
        # and PostgreSQL raises 42804 instead. No dialect can ask "is this a
        # boolean" -- in the MySQL family a boolean IS a TINYINT, so testing
        # IN (0, 1) would also admit a NUM column SEL refuses -- so there is
        # nothing to wrap it in, and refusing is the only answer that keeps the
        # warrant.
        if f.kind == 'BOOL':
            return f
        refuse('E_SQL_SHAPE',
               f'{where} needs a BOOL here and this is {f.kind}; SEL has no '
               'truthiness, so neither does its translation', pos)

    def _require_num(self, f: Fragment, pos: Pos, where: str) -> Fragment:
        """SUM's counterpart to _require_bool, and no longer its mirror:
        _require_bool refuses UNKNOWN and this one still passes it.

        A body the binding declared TEXT is refused; an undeclared body is summed
        bare. It reaches neither _binary nor _unary, which is where the numeric
        guards go, so _guard_numeric never sees it and MariaDB sums numeric
        prefixes of values SEL answers E_NOT_NUM for. That is the "bare aggregate
        body" cell docs/internals/sql-kinds.md §4 still marks broken -- §4.1a records why
        (nothing yet says which argument of which function is read as a number)
        and gives `_["QTY"] * 1` as the workaround that does get the guard.
        """
        if f.kind in ('NUM', 'UNKNOWN'):
            return f
        refuse('E_SQL_SHAPE',
               f'{where} adds its body up, so it needs a number here and this is '
               f'{f.kind}', pos)

    # --- skeletons -----------------------------------------------------------

    def _skeleton(self, name: str, pos: Pos) -> str:
        s = _map.entry(self.dialect, 'skel', name)
        if s is _map.MISSING or s == _map.MISSING or s is None:
            refuse('E_SQL_UNSUPPORTED',
                   f'dialect {self.dialect} has no {name} skeleton', pos)
        if isinstance(s, str):
            refuse('E_SQL_UNSUPPORTED',
                   f'dialect {self.dialect} cannot express {name} — {s}', pos)
        # A skeleton may carry a caveat, and until MariaDB's CASE needed one
        # nothing here read it -- so `skel` was the one section whose entries could
        # declare an inexactness that never reached Fragment.caveats and that
        # `strict` never refused.
        if s.get('caveat') is not None:
            if self.strict:
                refuse('E_SQL_UNSUPPORTED',
                       f'the {name} skeleton for {self.dialect} is not exactly '
                       f'equivalent ({s["caveat"]}), and strict mode refuses those',
                       pos)
            self.caveats[s['caveat']] = True
        return str(s['tpl'])

    def _fill_named(self, tpl: str, slots: dict[str, list[Any]], pos: Pos) -> list[Any]:
        """Fill a skeleton, whose placeholders are named rather than numbered."""
        parts: list[Any] = []

        def push(s: str) -> None:
            if s == '':
                return
            if parts and isinstance(parts[-1], str):
                parts[-1] += s
            else:
                parts.append(s)

        i = 0
        n_tpl = len(tpl)
        while i < n_tpl:
            if tpl[i] != '{':
                push(tpl[i])
                i += 1
                continue
            end = tpl.find('}', i)
            if end == -1:
                push(tpl[i:])
                break
            name = tpl[i + 1:end]
            i = end + 1
            if name not in slots:
                refuse('E_SQL_UNSUPPORTED',
                       f'a skeleton in dialect {self.dialect} uses {{{name}}}, which '
                       'is not one of its slots', pos)
            for item in slots[name]:
                if isinstance(item, str):
                    push(item)
                    continue
                for p in item.parts:
                    if isinstance(p, str):
                        push(p)
                    else:
                        parts.append(p)
        return parts

    # --- Relational Pipeline Statement Compilation --------------------------

    def _plan_has_rows_above(self, plan: RelationalPlan) -> bool:
        return bool(plan.projections is not None or plan.select_cols is not None
                    or plan.group_by is not None or plan.distinct
                    or plan.limit is not None or plan.offset is not None
                    or plan.order_by)

    def _plan_needs_wrap_before_map(self, plan: RelationalPlan) -> bool:
        """Whether a MAP must wrap the plan first. An ORDER BY alone does
        not: the projection and the sort can share one statement (ORDER BY
        may name the input's columns), and a derived table is where MariaDB
        DROPS an ORDER BY that has no LIMIT beside it -- the statement
        oracle's sorted rows came back in table order. Everything else above
        the rows still wraps."""
        return bool(plan.projections is not None or plan.select_cols is not None
                    or plan.group_by is not None or plan.distinct
                    or plan.limit is not None or plan.offset is not None)

    def _output_field_names(self, plan: RelationalPlan) -> list[str]:
        names = []
        if plan.projections is not None:
            for index, projection in enumerate(plan.projections):
                if projection.get('alias') is not None:
                    names.append(projection['alias'])
                elif (projection.get('node') is not None
                      and projection['node'].t == 'index'
                      and projection['node'].idx.t == 'text'):
                    names.append(projection['node'].idx.v)
                else:
                    names.append(f'expr{index + 1}')
        elif plan.select_cols is not None:
            names.extend(plan.select_cols)
        elif plan.joins:
            names.extend(f['name'] for f in self._joined_row_fields(plan))
        elif plan.source_relation and plan.source_relation.get('fields'):
            names.extend(plan.source_relation['fields'].keys())
        return list(dict.fromkeys(names))

    def _joined_row_fields(self, plan: RelationalPlan) -> list[dict[str, Any]]:
        """The fields of a joined row that SQL can carry (spec §7.4 "Joined
        rows"): the promoted ones -- a side's fields whose names, compared
        ASCII-case-insensitively, do not occur on the other side --
        accumulated join by join as the evaluator promotes them. The binders
        (``_1``, ``_2``, the relations' names) are nested records with no
        column, and a name both sides carry is E_NO_KEY in SEL; neither is
        projected, so a read of either over the derived table is refused
        where ``run()`` raises. ``SELECT o.*`` was the row before: the left
        table's columns, which a continuation read where SEL has no key, and
        which made a derived table over a join name columns it did not have
        (finding Y, lanes).
        """
        def entries(rel):
            return [{'name': name, 'spec': spec, 'owner': rel}
                    for name, spec in ((rel or {}).get('fields') or {}).items()]

        acc = entries(plan.source_relation)
        for join in plan.joins:
            right = entries(join.source_relation)
            left_names = {ascii_upper(f['name']) for f in acc}
            right_names = {ascii_upper(f['name']) for f in right}
            acc = ([f for f in acc if ascii_upper(f['name']) not in right_names]
                   + [f for f in right if ascii_upper(f['name']) not in left_names])
        return acc

    def _output_field_type(self, plan: RelationalPlan, name: str) -> str:
        # Only a direct field read proves that the SQL output retains the
        # binding's declared type. Computed projections remain UNKNOWN.
        if plan.projections is not None:
            projection = next((p for p in plan.projections if p.get('alias') == name), None)
            node = projection['node'] if projection else None
            if not (node and node.t == 'index' and node.obj.t == 'var'
                    and node.obj.name == projection['binder'] and node.idx.t == 'text'):
                return 'UNKNOWN'
            name = node.idx.v
        matches = [rel['fields'][ascii_upper(name)]
                   for rel in [plan.source_relation, *(j.source_relation for j in plan.joins)]
                   if rel and ascii_upper(name) in (rel.get('fields') or {})]
        return (matches[0].get('type', 'UNKNOWN')
                if len(matches) == 1 and not matches[0].get('guard')
                and matches[0].get('raw') is None else 'UNKNOWN')

    def _output_canon_kind(self, plan: RelationalPlan, name: str) -> str | None:
        """The kind of a projected CANON(...), which a derived table's column
        keeps: the dialect's CANON kind -- the map entry's ret, NUM or TEXT."""
        if plan.projections is None:
            return None
        projection = next((p for p in plan.projections if p.get('alias') == name), None)
        node = projection['node'] if projection else None
        if not (node is not None and node.t == 'call' and node.name == 'CANON'):
            return None
        entry = _map.entry(self.dialect, 'funcs', 'CANON')
        return str(entry['ret']) if isinstance(entry, dict) else None

    def _wrap_plan_as_derived_table(self, plan: RelationalPlan) -> RelationalPlan:
        self.subquery_counter += 1
        alias = f'_sub{self.subquery_counter}'
        fields = {}
        for name in self._output_field_names(plan):
            source_field = None
            if plan.projections is None and plan.select_cols is None:
                source_field = ((plan.source_relation or {}).get('fields') or {}).get(ascii_upper(name))
                if source_field is None:
                    for join in plan.joins:
                        source_field = ((join.source_relation or {}).get('fields') or {}).get(ascii_upper(name))
                        if source_field is not None:
                            break
            fields[ascii_upper(name)] = {
                'kind': 'column',
                'column': source_field.get('column', name) if source_field else name,
                'table': alias,
                'type': self._output_field_type(plan, name),
            }
            canon_kind = self._output_canon_kind(plan, name)
            if canon_kind is not None:
                fields[ascii_upper(name)]['type'] = canon_kind
                fields[ascii_upper(name)]['canonical'] = True
        derived = RelationalPlan()
        derived.source_name = alias
        derived.source_relation = {'kind': 'relation', 'from': {'raw': ''},
                                   'alias': alias, 'fields': fields}
        derived.source_table = ''
        derived.source_alias = alias
        derived.source_subquery = plan
        if plan.bucket is not None:
            derived.bucket = 'sealed'
        return derived

    def _ensure_derived(self, plan: RelationalPlan, predicate) -> RelationalPlan:
        return self._wrap_plan_as_derived_table(plan) if predicate(plan) else plan

    def _eval_int_param(self, n: Node, op: str) -> int:
        try:
            val = eval_node(n, self.const_ctx or Context())
        except SelError as e:
            _constants.refuse_as_sel(e, n)
        if not val.looks_numeric() or val.is_null():
            refuse('E_NOT_NUM', f'{op} count must be a number', n.pos)
        d = val.as_decimal(n.pos)
        if d.scale != 0:
            refuse('E_NOT_INT', f'{op} count must be an integer', n.pos)
        if d.neg:
            refuse('E_RANGE', f'{op} count cannot be negative', n.pos)
        return d.digits

    def _bucket_projection(self, plan: RelationalPlan, binder: str,
                           aggregate_node: Node | None) -> None:
        """The projection of a bucket: the RECORD (or single expression)
        evaluated once per group, with ``binder`` bound to the group and _K to
        its key. Shared by the two spellings SEL has for it -- BUCKET(src, key,
        proj) and BUCKET(src, key) .> MAP(proj) -- which are one value in the
        evaluator and have to be one statement here. With no projection at all
        the keys are projected, which is the most SQL can say about a bucket
        on its own.
        """
        if aggregate_node is not None:
            if (aggregate_node.t == 'call'
                    and aggregate_node.name == 'RECORD'):
                projections = []
                for alias, value_node in self._record_fields(aggregate_node):
                    actual = value_node
                    # _K is the key, which was written against the KEY's
                    # binder -- the MAP spelling may name the group
                    # differently, so the projection keeps the binder the
                    # key node was written for.
                    # is rendered as the group key itself (_group_key), under
                    # the binder the key node was written for.
                    node_binder = binder
                    group_key = None
                    if value_node.t == 'var' and value_node.name == '_K' and len(plan.group_by) == 1:
                        actual = plan.group_by[0]['node']
                        node_binder = plan.group_by[0]['binder']
                        group_key = plan.group_by[0]
                    projections.append({'alias': alias, 'binder': node_binder, 'node': actual,
                                        'group_key': group_key})
                plan.projections = projections
            else:
                plan.projections = [{'alias': None, 'binder': binder,
                                     'node': aggregate_node}]
        else:
            plan.projections = [{'alias': group.get('alias'),
                                 'binder': group['binder'], 'node': group['node'],
                                 'group_key': group}
                                for group in plan.group_by]
        plan.select_cols = None

    def analyze_pipeline(self, n: Node) -> RelationalPlan | None:
        if _constants.identity_loss_before_grouping(n):
            refuse('E_SQL_SHAPE', 'grouping depends on a computed projection without identity preservation', n.pos)
        steps: list[Node] = []
        curr = n
        while curr.t == 'call' and curr.name in PIPELINE_OPS:
            if not curr.args:
                break
            steps.append(curr)
            curr = curr.args[0]
        if curr.t != 'var' or not self.bindings.has(curr.name):
            return None
        source_binding = self.bindings.get(curr.name)
        if source_binding.get('kind') != 'relation':
            return None

        plan = RelationalPlan()
        plan.source_name = curr.name
        plan.source_relation = source_binding
        plan.source_table = source_binding.get('from')
        plan.source_alias = source_binding.get('alias')
        correlate = source_binding.get('correlate')
        plan.correlate = (str(correlate.get('raw', '')) if isinstance(correlate, dict)
                          else correlate if isinstance(correlate, str) else None)

        for step in reversed(steps):
            name, args = step.name, step.args
            # A FILTER after an open bucket is a HAVING and a MAP is the
            # bucket's projection; anything else spends the members. Either
            # way a step written directly after the bare bucket runs over its
            # groups, and is rendered in the bucket's own frame (_with_group).
            over_groups = plan.bucket == 'open'
            if plan.bucket == 'open' and name not in ('FILTER', 'MAP'):
                plan.bucket = 'sealed'
            if name == 'FILTER':
                # A FILTER over a bare bucket whose members are spent: SQL has
                # only the keys left, and SEL's value is still a map of groups.
                if plan.bucket == 'sealed':
                    refuse('E_SQL_SHAPE', 'a FILTER over buckets must follow the BUCKET directly: SQL '
                           "keeps a bucket's members only for the projection that ends the grouping",
                           step.pos)
                # A FILTER after a LIMIT or OFFSET is a WHERE over the rows that
                # survived them, grouped or not -- SEL applies the TAKE first,
                # and a HAVING would run before it. Otherwise a FILTER directly
                # after a grouping is its HAVING, and an ORDER BY in between
                # changes nothing (HAVING then ORDER BY is sort-then-filter's
                # rows).
                plan = self._ensure_derived(plan, lambda candidate:
                    candidate.limit is not None or candidate.offset is not None
                    or (candidate.group_by is None and bool(
                        candidate.projections is not None or candidate.select_cols is not None
                        or candidate.order_by or candidate.distinct)))
                if len(args) == 2:
                    binder, predicate = '_', args[1]
                elif len(args) == 3:
                    if not _constants.is_binder_name(args[1]):
                        refuse('E_SQL_SHAPE', 'the binder of FILTER must be a bare name', args[1].pos)
                    binder, predicate = args[1].name, args[2]
                else:
                    refuse('E_ARITY', 'FILTER takes 2 or 3 arguments', step.pos)
                if plan.group_by is not None:
                    plan.having.append({'binder': binder, 'node': predicate, 'pos': step.pos,
                                        'over_groups': over_groups})
                else:
                    plan.filters.append({'binder': binder, 'node': predicate, 'pos': step.pos})

            elif name == 'BUCKET':
                # A bucket over a bare bucket's rows: SQL has only the keys
                # (open) or has spent the members (sealed); either way SEL's
                # value is a map of groups and re-grouping it is a different
                # program.
                if plan.bucket is not None:
                    refuse('E_SQL_SHAPE', "a BUCKET over buckets: SQL keeps a bucket's members "
                           'only for the projection that ends the grouping', step.pos)
                plan = self._ensure_derived(plan, self._plan_has_rows_above)
                if len(args) == 2:
                    binder, key_node, aggregate_node = '_', args[1], None
                elif len(args) == 3:
                    binder, key_node, aggregate_node = '_', args[1], args[2]
                elif len(args) == 4:
                    if not _constants.is_binder_name(args[1]):
                        refuse('E_SQL_SHAPE', 'the binder of BUCKET must be a bare name', args[1].pos)
                    binder, key_node, aggregate_node = args[1].name, args[2], args[3]
                else:
                    refuse('E_ARITY', 'BUCKET takes 2 to 4 arguments', step.pos)

                # A bare bucket's key is an index key (spec §7.4): one text or
                # number. A list or record key is refused by the evaluator, and
                # the boolean and binary kinds are refused below, once known.
                several_keys = (key_node.t == 'list'
                                or (key_node.t == 'call' and key_node.name in ('LIST', 'RECORD')))
                if aggregate_node is None and several_keys:
                    refuse('E_SQL_SHAPE', 'a bare BUCKET groups by one text or number key, as an index '
                           'does; BUCKET(src, key, proj) groups by several', key_node.pos)
                group_by = []
                if (key_node.t == 'list'
                        or (key_node.t == 'call' and key_node.name == 'LIST')):
                    items = key_node.items if key_node.t == 'list' else key_node.args
                    for item in items:
                        group_by.append({'alias': None, 'binder': binder, 'node': item,
                                         'pos': item.pos or step.pos})
                elif key_node.t == 'call' and key_node.name == 'RECORD':
                    for alias, value in self._record_fields(key_node):
                        group_by.append({'alias': alias, 'binder': binder, 'node': value,
                                         'pos': value.pos or step.pos})
                else:
                    group_by.append({'alias': None, 'binder': binder, 'node': key_node,
                                     'pos': key_node.pos or step.pos})
                plan.group_by = group_by
                plan.bucket = 'open' if aggregate_node is None else None
                plan.bare_key = aggregate_node is None
                self._bucket_projection(plan, binder, aggregate_node)

            elif name == 'SELECT_COLS':
                # The same rule as a MAP's: an ORDER BY alone does not wrap (a
                # derived table is where MariaDB drops an ORDER BY with no LIMIT
                # beside it), everything else above the rows does (SEL-0048).
                plan = self._ensure_derived(plan, self._plan_needs_wrap_before_map)
                column_args = args[1:]
                items = (column_args[0].items if len(column_args) == 1
                         and column_args[0].t == 'list' else column_args)
                columns = []
                for item in items:
                    if item.t != 'text':
                        refuse('E_BAD_ARG', 'SELECT_COLS column names must be string literals', item.pos)
                    column = item.v
                    field_name = ascii_upper(column)
                    matches = 0
                    if field_name in (plan.source_relation.get('fields') or {}):
                        matches += 1
                    for join in plan.joins:
                        if field_name in (join.source_relation.get('fields') or {}):
                            matches += 1
                    if matches > 1:
                        refuse('E_SQL_SHAPE',
                               f"column '{column}' is ambiguous across joined tables; qualify with a table alias",
                               item.pos)
                    fields = plan.source_relation.get('fields') or {}
                    if fields and matches == 0:
                        refuse('E_SQL_SHAPE',
                               f"relation {plan.source_name} has no field '{column}'; the relation declares "
                               + ', '.join(fields.keys()), item.pos)
                    columns.append(column)
                plan.select_cols = columns
                plan.projections = None

            elif name == 'MAP':
                if plan.bucket == 'sealed':
                    refuse('E_SQL_SHAPE', 'a MAP over buckets must follow the BUCKET, with at '
                           'most a FILTER between: SQL keeps a bucket\'s members only for '
                           'the projection that ends the grouping', step.pos)
                if len(args) == 2:
                    binder, expr = '_', args[1]
                elif len(args) == 3:
                    if not _constants.is_binder_name(args[1]):
                        refuse('E_SQL_SHAPE', 'the binder of MAP must be a bare name', args[1].pos)
                    binder, expr = args[1].name, args[2]
                else:
                    refuse('E_ARITY', 'MAP takes 2 or 3 arguments', step.pos)
                # BUCKET(src, key) .> MAP(proj) is BUCKET(src, key, proj): the
                # MAP's body is evaluated once per group, so it is the bucket's
                # projection.
                if plan.bucket == 'open':
                    plan.bucket = None
                    self._bucket_projection(plan, binder, expr)
                    continue
                plan = self._ensure_derived(plan, self._plan_needs_wrap_before_map)
                if expr.t == 'call' and expr.name == 'RECORD':
                    plan.projections = [{'alias': alias, 'binder': binder, 'node': value}
                                        for alias, value in self._record_fields(expr)]
                else:
                    plan.projections = [{'alias': None, 'binder': binder, 'node': expr}]
                plan.select_cols = None

            elif name in ('DISTINCT', 'DEDUPE'):
                plan = self._ensure_derived(plan, lambda candidate:
                    candidate.limit is not None or candidate.offset is not None)
                if plan.projections is None and plan.select_cols is None:
                    refuse('E_SQL_SHAPE', 'DISTINCT requires an explicit typed projection', step.pos)
                plan.distinct = True

            elif name == 'TAKE':
                if len(args) != 2:
                    refuse('E_ARITY', 'TAKE takes 2 arguments', step.pos)
                limit = self._eval_int_param(args[1], 'TAKE')
                plan.limit = limit if plan.limit is None else min(plan.limit, limit)

            elif name == 'DROP':
                if len(args) != 2:
                    refuse('E_ARITY', 'DROP takes 2 arguments', step.pos)
                offset = self._eval_int_param(args[1], 'DROP')
                # Consume the bounded slice. Avoid sums beyond the exact
                # integer range shared by hosts by retaining a SQL boundary.
                skipped = offset if plan.limit is None else min(offset, plan.limit)
                if (plan.offset or 0) > 9007199254740991 - skipped:
                    plan = self._wrap_plan_as_derived_table(plan)
                    plan.offset = offset
                else:
                    if plan.limit is not None:
                        plan.limit -= skipped
                    plan.offset = (plan.offset or 0) + skipped

            elif name in ('SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY'):
                # A sort after a LIMIT or OFFSET sorts the rows that survived
                # them, grouped or not, so those wrap; a sort over a projection
                # or a DISTINCT wraps so its key can name what they produced. A
                # sort after a sort does not wrap: the sorts are stable, so the
                # earlier one is the later one's tie-breaker, and the later
                # one's keys go FIRST in the ORDER BY (review 2026-09-15
                # finding V).
                plan = self._ensure_derived(plan, lambda candidate:
                    candidate.limit is not None or candidate.offset is not None
                    or (candidate.group_by is None and bool(
                        candidate.projections is not None or candidate.select_cols is not None
                        or candidate.distinct)))
                before = len(plan.order_by)
                self._analyze_sort_step_extended(step, plan)
                added = plan.order_by[before:]
                for order in added:
                    order['over_groups'] = over_groups
                plan.order_by = added + plan.order_by[:before]

            elif name in ('LINK', 'LINK_LEFT'):
                plan = self._ensure_derived(plan, self._plan_has_rows_above)
                if len(args) not in (3, 5):
                    refuse('E_ARITY', f'{name} takes 3 or 5 arguments', step.pos)
                right_node = args[1]
                if right_node.t != 'var' or not self.bindings.has(right_node.name):
                    refuse('E_SQL_SHAPE', f'{name} requires a bound relation as its right side', right_node.pos)
                right_relation = self.bindings.get(right_node.name, right_node.pos)
                if right_relation.get('kind') != 'relation':
                    refuse('E_SQL_SHAPE', f'{right_node.name} is not bound as a relation', right_node.pos)
                join = JoinPlan()
                join.type = 'LEFT' if name == 'LINK_LEFT' else 'INNER'
                join.source_name = right_node.name
                join.source_relation = right_relation
                join.source_table = right_relation.get('from')
                join.source_alias = right_relation.get('alias')
                if len(args) == 5:
                    if (not _constants.is_binder_name(args[2])
                            or not _constants.is_binder_name(args[3])):
                        refuse('E_SQL_SHAPE', 'join binders must be bare names', args[2].pos)
                    join.left_binder = args[2].name
                    join.right_binder = args[3].name
                    join.on_pred = args[4]
                else:
                    join.left_binder = plan.source_alias or '_1'
                    join.right_binder = join.source_alias or '_2'
                    join.on_pred = args[2]
                if join.source_alias is None:
                    join.source_alias = join.right_binder
                join.pos = step.pos
                plan.joins.append(join)

        return plan

    def _analyze_sort_step_extended(self, step: Node, plan: RelationalPlan) -> None:
        name, args = step.name, step.args
        is_top = name in ('TOP', 'TOP_DESC', 'TOP_BY')
        count = len(args) - 1 if is_top else len(args)
        if is_top:
            limit = self._eval_int_param(args[-1], name)
            plan.limit = limit if plan.limit is None else min(plan.limit, limit)

        if name in ('SORT', 'SORT_DESC', 'TOP', 'TOP_DESC'):
            direction = 'DESC' if name in ('SORT_DESC', 'TOP_DESC') else 'ASC'
            if count == 1:
                scalar = plan.source_relation.get('scalar')
                fields = plan.source_relation.get('fields') or {}
                field_name = scalar or (next(iter(fields)) if len(fields) == 1 else None)
                if field_name is None:
                    refuse('E_SQL_SHAPE',
                           'SORT on a multi-field relation requires a key expression; use SORT_BY', step.pos)
                key = Node('index', step.pos, obj=Node('var', step.pos, name='_'),
                           idx=Node('text', step.pos, v=field_name))
                plan.order_by.append({'binder': '_', 'node': key,
                                      'dir': direction, 'pos': step.pos})
                return
            if count == 2:
                plan.order_by.append({'binder': '_', 'node': args[1],
                                      'dir': direction, 'pos': step.pos})
                return
            if count == 3:
                if not _constants.is_binder_name(args[1]):
                    refuse('E_SQL_SHAPE', 'the binder of SORT must be a bare name', args[1].pos)
                plan.order_by.append({'binder': args[1].name, 'node': args[2],
                                      'dir': direction, 'pos': step.pos})
                return
            refuse('E_ARITY', f'{name} takes 1 to 3 arguments', step.pos)

        if count == 2:
            binder, key, direction = '_', args[1], 'ASC'
        elif count == 3:
            if args[2].t == 'text':
                binder, key, direction = '_', args[1], ascii_upper(args[2].v)
            elif _constants.is_binder_name(args[1]):
                binder, key, direction = args[1].name, args[2], 'ASC'
            else:
                # Neither form: the third slot is a direction the evaluator
                # would compute, and SQL cannot -- the four-argument form's
                # refusal.
                refuse('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args[2].pos)
        elif count == 4:
            if not _constants.is_binder_name(args[1]):
                refuse('E_SQL_SHAPE', 'the binder of SORT_BY must be a bare name', args[1].pos)
            binder, key = args[1].name, args[2]
            if args[3].t != 'text':
                refuse('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'", args[3].pos)
            direction = ascii_upper(args[3].v)
        else:
            refuse('E_ARITY', 'SORT_BY takes 2 to 4 arguments', step.pos)
        if direction not in ('ASC', 'DESC'):
            refuse('E_BAD_ARG', "sort direction must be 'ASC' or 'DESC'",
                   args[3].pos if count == 4 else args[2].pos)
        plan.order_by.append({'binder': binder, 'node': key,
                              'dir': direction, 'pos': step.pos})

    def compile_statement(self, plan: RelationalPlan) -> Fragment:
        # SQL aliases are not RECORD insertions. Keep overwritten expressions
        # local: deleting them can suppress errors and change field order.
        for entries in (plan.projections, plan.group_by):
            seen = set()
            for entry in entries or []:
                if entry.get('alias') is not None:
                    key = ascii_upper(entry['alias'])
                    if key in seen:
                        refuse('E_SQL_SHAPE', 'duplicate or case-colliding RECORD fields require local evaluation', entry['node'].pos)
                    seen.add(key)
        previous_plan = self.statement_plan
        previous_where = self.in_where
        self.statement_plan = plan
        try:
            parts: list[Any] = ['SELECT DISTINCT ' if plan.distinct else 'SELECT ']
            src = {'relation': plan.source_relation, 'filters': plan.filters, 'pos': None}

            if plan.projections is not None:
                for index, projection in enumerate(plan.projections):
                    if index:
                        parts.append(', ')
                    if projection.get('group_key') is not None:
                        fragment = self._group_key(src, projection['group_key'], True)
                    elif plan.group_by is not None:
                        fragment = self._with_group(
                            src, projection['binder'], lambda p=projection: self._node(p['node']))
                    else:
                        fragment = self._with_row(
                            src, projection['binder'], lambda p=projection: self._node(p['node']))
                    if plan.distinct:
                        if fragment.kind in ('UNKNOWN', 'NUM') and not fragment.canonical:
                            refuse('E_SQL_SHAPE', 'DISTINCT requires proven structural output identity', projection['node'].pos)
                        fragment = self._identity_group_key(projection['node'], fragment)
                    parts.extend(fragment.parts)
                    if projection.get('alias') is not None:
                        parts.append(' AS ' + self.emit.ident(projection['alias']))
            elif plan.select_cols is not None:
                for index, column in enumerate(plan.select_cols):
                    if index:
                        parts.append(', ')
                    field_name = ascii_upper(column)
                    field_spec = (plan.source_relation.get('fields') or {}).get(field_name)
                    owner = plan.source_relation
                    if field_spec is None:
                        for join in plan.joins:
                            candidate = (join.source_relation.get('fields') or {}).get(field_name)
                            if candidate is not None:
                                field_spec, owner = candidate, join.source_relation
                                break
                    if plan.joins:
                        table = self._relation_table_alias(owner)
                    else:
                        table = (field_spec.get('table') if field_spec else None) or (
                            plan.source_alias if owner is plan.source_relation else _relation_alias(owner))
                    output_column = field_spec.get('column', column) if field_spec else column
                    sql = self.emit.column(table, output_column)
                    if plan.distinct and (not field_spec or field_spec.get('type', 'UNKNOWN') in ('UNKNOWN', 'NUM')):
                        refuse('E_SQL_SHAPE', 'DISTINCT requires known output kinds', None)
                    if plan.distinct and field_spec and field_spec.get('type') in ('TEXT', 'NUM'):
                        fragment = self.emit.text_operand(Fragment([sql], field_spec['type'], self.dialect))
                        parts.extend(fragment.parts)
                        parts.append(' AS ' + self.emit.ident(output_column))
                    else:
                        parts.append(sql)
            elif plan.joins:
                # A joined row is its promoted fields (spec §7.4); see
                # _joined_row_fields.
                fields = self._joined_row_fields(plan)
                if not fields:
                    last = plan.joins[-1]
                    refuse('E_SQL_SHAPE', 'the joined row has no field SQL can carry: '
                           'every field is on both sides, and the binders are nested '
                           'records', last.pos)
                for index, f in enumerate(fields):
                    if index:
                        parts.append(', ')
                    spec = f['spec'] or {}
                    parts.append(self.emit.column(self._relation_table_alias(f['owner']),
                                                 spec.get('column', f['name'])))
            else:
                parts.append(self.emit.ident(plan.source_alias) + '.*'
                             if plan.source_alias else '*')

            parts.append(' FROM ')
            if plan.source_subquery is not None:
                subquery = self.compile_statement(plan.source_subquery)
                parts.append('(')
                parts.extend(subquery.parts)
                parts.append(') ' + self.emit.ident(str(plan.source_alias)))
            else:
                if isinstance(plan.source_table, dict) and 'raw' in plan.source_table:
                    source_sql = str(plan.source_table['raw'])
                else:
                    source_sql = self.emit.ident(str(plan.source_table))
                if plan.source_alias:
                    source_sql += ' ' + self.emit.ident(str(plan.source_alias))
                parts.append(source_sql)

            for join in plan.joins:
                parts.append(' LEFT JOIN ' if join.type == 'LEFT' else ' INNER JOIN ')
                if isinstance(join.source_table, dict) and 'raw' in join.source_table:
                    right_sql = str(join.source_table['raw'])
                else:
                    right_sql = self.emit.ident(str(join.source_table))
                if join.source_alias:
                    right_sql += ' ' + self.emit.ident(str(join.source_alias))
                parts.append(right_sql + ' ON ')
                on_fragment = self._with_join_binders(
                    plan, join,
                    lambda j=join: self._require_bool(self._node(j.on_pred), j.pos, 'LINK'))
                parts.extend(on_fragment.parts)

            condition_parts: list[list[Any]] = []
            if plan.correlate:
                condition_parts.append([plan.correlate])
            self.in_where = True
            try:
                for filter_ in plan.filters:
                    fragment = self._with_row(
                        src, filter_['binder'],
                        lambda f=filter_: self._require_bool(
                            self._node(f['node']), f['pos'], 'FILTER'))
                    condition_parts.append(fragment.parts)
            finally:
                self.in_where = previous_where
            if condition_parts:
                parts.append(' WHERE ')
                for index, condition in enumerate(condition_parts):
                    if index:
                        parts.append(' AND ')
                    parts.extend(condition)

            if plan.group_by:
                parts.append(' GROUP BY ')
                for index, group in enumerate(plan.group_by):
                    if index:
                        parts.append(', ')
                    fragment = self._group_key(src, group)
                    if plan.bare_key and fragment.kind in ('BOOL', 'BIN'):
                        refuse('E_SQL_SHAPE', 'a bare BUCKET groups by one text or number key, as an '
                               'index does; SEL refuses a boolean or binary key (E_NOT_TEXT)',
                               group['pos'])
                    parts.extend(fragment.parts)

            if plan.having:
                parts.append(' HAVING ')
                self.in_having = True
                try:
                    for index, having in enumerate(plan.having):
                        if index:
                            parts.append(' AND ')
                        with_frame = self._with_group if having.get('over_groups') else self._with_projected
                        fragment = with_frame(
                            src, having['binder'],
                            lambda h=having: self._require_bool(
                                self._node(h['node']), h['pos'], 'FILTER'))
                        parts.extend(fragment.parts)
                finally:
                    self.in_having = False

            if plan.order_by:
                parts.append(' ORDER BY ')
                for index, order in enumerate(plan.order_by):
                    if index:
                        parts.append(', ')
                    # A TEXT sort key is collated like a group key: SEL sorts
                    # text by its bytes, and a server's default collation would not.
                    with_frame = (self._with_group if order.get('over_groups')
                                  else self._with_projected if plan.group_by is not None
                                  else self._with_row)
                    fragment = self._order_key(with_frame(
                        src, order['binder'], lambda o=order: self._node(o['node'])), order['node'].pos)
                    parts.extend(fragment.parts)
                    parts.append(' ' + order['dir'])

            if plan.limit is not None and plan.offset is not None:
                parts.append(f' LIMIT {plan.limit} OFFSET {plan.offset}')
            elif plan.limit is not None:
                parts.append(f' LIMIT {plan.limit}')
            elif plan.offset is not None:
                chain = _map.chain(self.dialect)
                if any(d in chain for d in ('mariadb', 'mysql', 'mysql-family')):
                    parts.append(f' LIMIT 18446744073709551615 OFFSET {plan.offset}')
                elif 'sqlite' in chain:
                    parts.append(f' LIMIT -1 OFFSET {plan.offset}')
                else:
                    parts.append(f' OFFSET {plan.offset}')

            return Fragment(parts, 'STATEMENT', self.dialect, self.params,
                            self.param_kinds, list(self.caveats))
        finally:
            self.statement_plan = previous_plan
            self.in_where = previous_where


# --- module-level helpers ----------------------------------------------------

def _replace(n: Node, **kw) -> Node:
    import dataclasses
    return dataclasses.replace(n, **kw)


def _static_source(elements: dict[str, Binder], scalar_rule: bool = False) -> dict[str, Any]:
    return {'shape': 'static', 'elements': elements, 'filters': [],
            'scalarRule': scalar_rule}


def _agg_shape(n: Node) -> tuple[str, Node]:
    """The 2- and 3-argument forms: ``_`` by default, a bare name when given."""
    if len(n.args) == 3:
        if not _constants.is_binder_name(n.args[1]):
            refuse('E_SQL_SHAPE', f'the binder of {n.name} must be a bare name',
                   n.args[1].pos)
        return n.args[1].name, n.args[2]
    return '_', n.args[1]


def _relation_alias(rel: dict[str, Any]) -> str:
    """The alias a relation binding renders under -- its own, or the table name
    when it declares none. The same rule Bindings.check_aliases applies.
    """
    alias = rel.get('alias')
    if isinstance(alias, str) and alias != '':
        return alias
    frm = rel.get('from')
    if isinstance(frm, dict):
        return str(frm.get('raw', ''))
    return str(frm if frm is not None else '')


def _child_of(node: Any, key: str) -> Any:
    if node.t == 'list':
        i = _list_key(key)
        if i is None or i > len(node.items):
            return None
        return node.items[i - 1]
    if node.t == 'clist':
        for k, v in node.entries:
            if k == key:
                return v
    return None


def _require_comparable_kinds(l: Fragment, r: Fragment, op: str, pos: Pos) -> None:
    """Refuse a structural comparison between two different known kind classes.

    These operators compare kinds first, and no amount of casting says that in
    SQL. ``CAST(0 AS CHAR)`` and ``CAST(FALSE AS CHAR)`` are both '0', so
    ``NOT (0 IN FALSE)`` answered TRUE in SEL and FALSE on the server -- found by
    the fuzz lane, which produces operand pairs a hand-written corpus does not.

    It used to compare only BOOL-ness, which let BIN through: SEL says
    ``TO_UTF8("a") EQL "a"`` is FALSE, and the emitted comparison cast both sides
    to characters and answered 1 on MariaDB, MySQL and SQLite. Refused rather
    than folded to FALSE -- folding is the road §11.4 closed.

    The same call serves the ``$`` family, for a different reason with the same
    answer. SEL's byte comparisons do NOT compare kinds -- ``TO_UTF8("a") $== "a"``
    is genuinely TRUE, both operands read as bytes -- but saying that in SQL means
    casting the TEXT side to bytes, and the templates cast the BIN side to
    characters instead, which is backwards: PostgreSQL compares against the
    literal ``\\x61`` and answers false. Per-dialect byte casts for a comparison
    nobody writes is not a trade worth making, so mixed operands are refused here
    too. Two operands of one class still translate, and for two BINs the character
    cast is skipped -- see _binary().

    One UNKNOWN is the accepted limit -- the binding did not say, so nothing here
    can either.
    """
    cl = EQL_CLASS.get(l.kind)
    cr = EQL_CLASS.get(r.kind)
    if cl is None or cr is None or cl == cr:
        return
    other = r.kind if l.kind == 'BOOL' else l.kind
    refuse('E_SQL_SHAPE',
           f'{op} compares a BOOL with a {other}, which SEL answers FALSE for every '
           'value because the kinds differ. SQL has no way to say that: both sides '
           'cast to the same characters', pos)


def _ret_kind(entry: dict[str, Any], args: list[Fragment],
              pos: Pos | None = None) -> str:
    ret = str(entry['ret'])
    if ret == '@concat':
        return 'BIN' if any(a.kind == 'BIN' for a in args) else 'TEXT'
    if ret.startswith('@unify:'):
        pick = [args[int(i)] for i in ret[7:].split(',') if int(i) < len(args)]
        return _unify(pick, pos)
    return ret


def _unify(fs: list[Fragment], pos: Pos | None = None) -> str:
    """The one kind a set of branches all produce.

    UNKNOWN unifies with anything: that is what it is for, and a column whose type
    the binding did not declare is the ordinary case. Two *known* kinds that
    differ are another matter, and used to yield UNKNOWN as well. They cannot: SQL
    types the whole CASE, and there is no rendering of the result that agrees with
    SEL's.

    ``IF(TRUE, TRUE, "A-1")`` is the one the fuzz lane found. SEL answers the BOOL
    TRUE, whose text is "TRUE"; the CASE answers 1. Nothing casts one to the other
    and no caveat says "a boolean becomes 1", so the honest outcome is a refusal.
    ``IF(p, 1, "x")`` goes the same way for the same reason.

    The rule is one line: known-kind branches must agree.
    """
    kind = None
    for f in fs:
        if f.kind == 'UNKNOWN':
            continue
        if kind is None:
            kind = f.kind
        elif kind != f.kind:
            refuse('E_SQL_SHAPE',
                   f'these branches produce different kinds — {kind} and {f.kind} — '
                   'and SQL gives the whole expression one type, which cannot match '
                   "SEL's for both", pos)
    return kind or 'UNKNOWN'


def _declared_kind(b: dict[str, Any], v: Value) -> str:
    """The kind of a value supplied by the host, which is the one place the
    num/text ambiguity cannot be resolved from the AST -- there is no AST, the host
    handed us a Value.

    So it is not guessed. BOOL and BIN are unambiguous; everything else is TEXT,
    and therefore quoted, unless the binding declares ``type: NUM``. Guessing from
    looks_numeric() would emit a product code of "00123" as the number 123, and a
    rule comparing it with $== would then be answered by the database rather than
    by SEL's semantics.
    """
    if v.is_bool():
        return 'BOOL'
    if v.is_bin():
        return 'BIN'
    if v.is_none():
        return 'LIST'
    return 'NUM' if b.get('type') == 'NUM' else 'TEXT'


def _slots(*maps: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """Merge named slot maps for a skeleton, refusing to let one shadow another.

    The three relation skeletons are filled from two sources: _relation_slots()
    supplies ``from`` and ``corr``, the caller supplies ``body`` and friends. A
    silent overwrite either way would lose one of them without a word -- and the
    PHP union operator used where a merge was meant has already cost this layer
    two defects, one of which left COUNT("hello") answering 1.

    The disjointness was true by inspection and enforced by nothing. This is the
    assertion that comment was standing in for. A collision is a bug in the
    translator rather than in a rule or a map, so it raises RuntimeError like
    every other startup mistake, not SqlError.
    """
    out: dict[str, list[Any]] = {}
    for m in maps:
        for k, v in m.items():
            if k in out:
                raise RuntimeError(
                    f'two sources both supply the skeleton slot {{{k}}}; one would '
                    'silently shadow the other')
            out[k] = v
    return out
