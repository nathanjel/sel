"""What each operator is, from spec/lexicon.json (rendered into _lexicon.py).

The one place this host asks "is this a comparison, an arithmetic operator, a
short circuit?": the lexer, the parser, the evaluator, the constant folder, the
optimiser, the join pre-filter, the dependency walker and the SQL layer all
take their operator sets from here, and every set is derived from the
lexicon's records -- none is spelled out by hand. Opcodes and the operators'
semantics stay native (eval.py); eval.py checks at import that it handles
every binary operator named here.
"""

from __future__ import annotations

from ._lexicon import BP, OPS, RELATIONS, RESERVED, SYMBOLS, Op  # noqa: F401 - re-exported

#: Infix operators by token (symbols and words share one key space here: no
#: word is spelled like a symbol). Includes `,` and `;`, which the parser
#: handles with loops of their own.
INFIX: dict[str, Op] = {o.token: o for o in OPS if o.fixity == 'infix'}

#: Prefix operators by token (`-`, `NOT`).
PREFIX: dict[str, Op] = {o.token: o for o in OPS if o.fixity == 'prefix'}


def _family(*families: str) -> frozenset[str]:
    return frozenset(o.token for o in INFIX.values() if o.family in families)


#: Every operator that builds a binary node.
BINARY_OPS = frozenset(o.token for o in INFIX.values() if o.node == 'bin')
#: `=` and the compound assignments.
ASSIGN_OPS = _family('assign')
#: A compound assignment's binary operator: `+=` -> `+`.
COMPOUND = {o.token: o.compound for o in INFIX.values() if o.compound is not None}
#: == != < <= > >= : numbers compared by value (§4.5).
NUMERIC_COMPARE = _family('compare')
#: $== $!= $< $<= $> $>= : bytes compared bytewise (§5.3).
TEXT_COMPARE = _family('text-compare')
#: EQL IN (§5.4).
DEEP_COMPARE = _family('deep-compare')
#: The two relational families: every operator with a relation.
RELATIONAL = NUMERIC_COMPARE | TEXT_COMPARE
#: Every comparison operator (§5's comparison level).
COMPARE_ALL = RELATIONAL | DEEP_COMPARE
#: + - * / % (binary).
ARITH_OPS = _family('arith')
#: AND OR XOR.
LOGIC_OPS = _family('logic')
#: BAND BOR BXOR.
BITWISE_OPS = _family('bitwise')
#: ?? ???.
COALESCE_OPS = _family('coalesce')
#: The binary operators whose right operand may never run.
SHORT_CIRCUIT = frozenset(o.token for o in INFIX.values() if o.short_circuit)

#: A text comparison's numeric twin with the same relation: `$<=` -> `<=`.
NUMERIC_TWIN = {t.token: n.token for t in INFIX.values() if t.family == 'text-compare'
                for n in INFIX.values() if n.family == 'compare' and n.relation == t.relation}

# The parser's binding powers, by level.
BP_SEQ = BP['SEQ']
BP_LIST = BP['LIST']
BP_ASSIGN = BP['ASSIGN']
BP_NOT = BP['NOT']
BP_NEG = BP['NEG']
