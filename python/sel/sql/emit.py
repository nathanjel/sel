"""Everything that turns a value or a template into characters. The one place
quoting happens, so there is one place to get it right.
"""

from __future__ import annotations

import re

from .. import decimal as D
from ..errors import Pos
from ..value import Value, quote_dump
from . import map as _map
from .errors import refuse

# Template slots are 0-based and canonical: {0}, {1}, {0:}. `{01}` and `{1\n}`
# are not slots, and the generator (tools/gen-sql-map.mjs) already refuses both
# -- JS's `$` matches only at end of string -- so the shipped map and a
# runtime-registered template were being read by two different grammars. A
# fullmatch and a three-digit cap close that: one grammar, and no host's integer
# parser is consulted.
_SLOT = re.compile(r'0|[1-9][0-9]{0,2}')


def _slot_index(s: str) -> int | None:
    """The argument a template slot names, or None when it names none."""
    return int(s) if _SLOT.fullmatch(s) else None


# --- literals ----------------------------------------------------------------

def literal(dialect: str, v: Value, form: str = 'TEXT', pos: Pos | None = None) -> str:
    """A SEL value as a SQL literal, in the form the caller says it has.

    A module function so Fragment can join without holding an emitter, which
    keeps a Fragment a plain data object.

    The form is passed in and never inferred, because it cannot be inferred: SEL
    numbers *are* TEXT values (spec §4), so ``Value.num('5.00')`` and
    ``Value.text('5.00')`` are the same object and no predicate can tell "the
    author wrote 5.00" from "the author wrote \\"5.00\\"". Only the AST knows, and
    it is the AST that tells us.

    Getting this wrong is not cosmetic. Emitted bare, ``"5.00" $== "5"`` becomes
    ``5.00 = 5``, which the database answers TRUE and SEL answers FALSE.
    """
    if form == 'BOOL' or v.is_bool():
        return str(_map.lexical(dialect, 'true' if v.as_bool(pos) else 'false'))
    if form == 'BIN' or v.is_bin():
        tpl = _map.lexical(dialect, 'binaryLiteral')
        if not isinstance(tpl, str):
            refuse('E_SQL_UNSUPPORTED',
                   f'dialect {dialect} has no binary literal syntax', pos)
        return tpl.replace('{hex}', v.as_bytes(pos).hex())
    # A NONE value has no characters, and asking for them raises a SelError --
    # which try_translate() does not catch, so a host using the refusal-tolerant
    # API got a fatal out of as_value() rather than None. Reachable from ordinary
    # host data: {"kind": "value", "value": []} is an empty result set. Standing
    # alone the variable is refused as a LIST, but as an operand the result kind
    # comes from the template and the LIST-ness is gone by the time anything looks.
    if v.is_none():
        refuse('E_SQL_BINDING',
               'a value binding holding no value cannot be a SQL literal; only an '
               'aggregate can be given an empty binding', pos)
    if form == 'NUM':
        return _numeric_literal(dialect, v, pos)
    return text_literal(dialect, v.as_text(pos))


def _numeric_literal(dialect: str, v: Value, pos: Pos | None) -> str:
    """The only unquoted output in the layer.

    A NUM literal is the one thing emitted without quotes, which makes it the one
    thing that has to be a number. The AST path arrives already parsed, but a
    ``value`` binding declaring ``type: NUM`` reaches here straight from host
    data, and ``"1 OR 1=1 -- "`` would go out verbatim. Fragment's part list
    keeps a literal from being confused with SQL; it cannot keep a literal from
    BEING SQL.

    What is emitted is what the parse recovered -- ``decimal.format``'s output --
    rather than the text the caller supplied. The two agree for everything the
    parser produces, and the difference is the point: proving a string is a
    number and then emitting a *different* string is a gap, however small, and
    the gap is where "1 OR 1=1" lived. After this the characters that can leave
    here are digits, one ``.`` and a leading ``-``, by construction.
    """
    text = v.as_text(pos)
    d = D.parse(text)
    if d is None:
        refuse('E_SQL_BINDING',
               'a value bound as NUM must be a number, and '
               + quote_dump(text) + ' is not', pos)
    n = D.format(d)

    # How the dialect spells a number is the dialect's business, and one of them
    # has to spell it as text. SQLite has no exact decimal: 2.50 is a REAL that
    # prints as 2.5, so `2.50 $== 2.5` would be TRUE there and FALSE in SEL.
    # Quoted, the exact characters survive, and SQLite's dynamic typing reads
    # them as a number wherever a number is wanted. Applied AFTER format, so the
    # digits-by-construction guarantee is unaffected: it decides how to spell a
    # number that has already been proved to be one.
    wrap = _map.lexical(dialect, 'numericLiteral')
    if isinstance(wrap, str) and wrap != '{0}':
        return wrap.replace('{0}', n)

    # A negative number is parenthesised so that unary minus in front of it
    # cannot produce `--`. MariaDB reads that as double negation and gets the
    # right answer by luck; PostgreSQL and SQLite read it as the start of a line
    # comment and the rest of the expression disappears. Only reachable through a
    # `value` binding, since the parser never produces a signed `num` node.
    return '(' + n + ')' if n.startswith('-') else n


def text_literal(dialect: str, text: str) -> str:
    quote = str(_map.lexical(dialect, 'textQuote'))
    escape = _map.lexical(dialect, 'textEscape')
    out = text
    if isinstance(escape, dict):
        # Longest first, so a rule for "\\\\" is applied before one for "\\".
        # A single left-to-right pass, never one str.replace per rule: replacing
        # "'" with "''" and then "\\" with "\\\\" would rewrite the output of the
        # first rule.
        keys = sorted(escape.keys(), key=len, reverse=True)
        buf: list[str] = []
        i = 0
        while i < len(out):
            for k in keys:
                if k and out.startswith(k, i):
                    buf.append(str(escape[k]))
                    i += len(k)
                    break
            else:
                buf.append(out[i])
                i += 1
        out = ''.join(buf)
    return quote + out + quote


def placeholder(dialect: str, n: int) -> str:
    """The params-mode placeholder for slot n, 1-based."""
    tpl = str(_map.lexical(dialect, 'placeholder'))
    return tpl.replace('{n}', str(n)) if '{n}' in tpl else tpl


class Emit:
    """The dialect-bound half: identifiers, templates, and the byte-comparison
    operand. The literal functions above are free because Fragment needs them
    without an emitter.
    """

    __slots__ = ('_dialect',)

    def __init__(self, dialect: str) -> None:
        self._dialect = dialect

    def dialect(self) -> str:
        return self._dialect

    def lex(self, key: str):
        return _map.lexical(self._dialect, key)

    def numeric_operand(self, f, pos: Pos | None = None):
        """An operand a numeric context will read as a number, made safe to read.

        SEL raises E_NOT_NUM for text that is not a number, and the server does
        not: ``CAST('x' AS DECIMAL)`` is 0 on MariaDB, MySQL and SQLite, so a rule
        comparing against 0 matched every row of a text column. Wrapping the
        operand so a non-number becomes NULL keeps the warrant -- NULL is not
        selected, which is what SEL failing has to look like from SQL.

        Not applied to a NUM operand: the binding said it is a number, and that
        declaration is where the promise transfers. It is also the only way to
        keep the index, since the guard is a function of the column.

        The pattern is SEL's own numeral grammar and lives in the map beside
        ``funcs.ISNUM``, which asks the same question; tools/gen-sql-map.mjs
        requires the two to agree. A dialect that cannot ask it -- sqlite has no
        REGEXP, ansi has no regex -- declares no ``numericGuard``, and this
        refuses rather than emitting something that answers when SEL would not.
        """
        from .fragment import Fragment
        if f.kind == 'NUM':
            return f
        guard = self.lex('numericGuard')
        if not isinstance(guard, str):
            refuse('E_SQL_UNSUPPORTED',
                   f'dialect {self._dialect} has no way to ask whether a value is a '
                   'number, so an operand it has not been told is one cannot be read '
                   'as one here; declare the binding NUM if the column really is '
                   'numeric', pos)
        return Fragment(self.fill(guard, [f], pos), 'NUM', self._dialect)

    def text_operand(self, f):
        """An operand of a byte comparison: cast to a character type, then given
        the dialect's binary collation.

        Both halves are needed and neither is enough alone. Without the collation
        MariaDB's default is case-insensitive, so ``"A" $== "a"`` is true there
        and false in SEL. Without the cast the collation does not stop two
        numeric operands being compared as numbers, so ``3.0 EQL 3`` is true
        there and false in SEL -- EQL is structural and does not normalise
        numbers.

        Applied here rather than in the templates because three places need it --
        two-operand comparisons, IN over a list, and the inRelation skeleton --
        and only one of those is a two-operand template.
        """
        from .fragment import Fragment
        cast = self.lex('textCast')
        collate = str(self.lex('textCollate') or '')
        parts = f.parts

        if isinstance(cast, str) and cast != '{0}':
            parts = self.fill(cast, [f])
        if collate != '':
            parts = [*parts, collate]
        return Fragment(parts, 'TEXT', self._dialect)

    # --- identifiers ---------------------------------------------------------

    def ident(self, name: str) -> str:
        """A table or column name, quoted.

        The quote character is doubled -- or whatever ``identEscape`` says --
        inside the name, which is what stops a binding naming a column ``a"b``
        from ending the identifier early.
        """
        q = str(self.lex('identQuote'))
        e = str(self.lex('identEscape'))
        return q + name.replace(q, e) + q

    def column(self, table: str | None, column: str) -> str:
        """``table``.``column``, or just the column when no table was given."""
        if table is None or table == '':
            return self.ident(column)
        return self.ident(table) + '.' + self.ident(column)

    # --- templates -----------------------------------------------------------

    def fill(self, tpl: str, args: list, pos: Pos | None = None,
             expanding: set | None = None) -> list:
        """Fill a template with already-rendered arguments, producing a part list.

        Splicing part lists rather than strings is the whole point: an argument
        carrying parameter slots keeps them. Concatenating the arguments into
        strings first would work exactly until a literal contained something that
        looked like a placeholder.

        Slot numbers are **absolute from the moment the literal is created** --
        one translation has one parameter vector, held by the Translator, and an
        intermediate Fragment carries indices into it rather than a vector of its
        own. So splicing copies slots verbatim and never renumbers. The
        alternative, every Fragment owning its own params and being renumbered on
        each splice, is the same information arranged so that one missed
        renumbering silently binds the wrong value to the wrong placeholder.

        ``{key}`` and ``{key:n}`` lexical forms are expanded here too. The
        generator has already done that for the shipped map; this is for entries
        an application registers at run time, which never pass through it.

        ``expanding`` is the set of lexical keys this call is already inside. A
        lexical value may reference another lexical key, and nothing stopped one
        from referencing itself: a dialect registering
        ``{'textCast': 'X({textCast:0})'}`` recursed until the host died --
        RecursionError here, a RangeError on the JS host, a host crash through
        the public API either way, which is the failure every other guard in this
        layer exists to prevent.

        The cycle is refused rather than a depth capped, because the cycle is the
        actual mistake and a depth cap would need a number nobody can justify.
        With cycles refused the chain is bounded by the number of lexical keys,
        which is fifteen.
        """
        from .fragment import Fragment
        parts: list = []

        def push(s: str) -> None:
            if s == '':
                return
            if parts and isinstance(parts[-1], str):
                parts[-1] += s
            else:
                parts.append(s)

        def splice(f) -> None:
            for p in f.parts:
                if isinstance(p, str):
                    push(p)
                else:
                    parts.append(p)      # absolute already; see the note above

        def join(subset) -> None:
            first = True
            for f in subset:
                if not first:
                    push(', ')
                first = False
                splice(f)

        i = 0
        n_tpl = len(tpl)
        while i < n_tpl:
            if tpl[i] == '{' and i + 1 < n_tpl and tpl[i + 1] == '{':
                push('{')
                i += 2
                continue
            if tpl[i] == '}' and i + 1 < n_tpl and tpl[i + 1] == '}':
                push('}')
                i += 2
                continue
            if tpl[i] != '{':
                push(tpl[i])
                i += 1
                continue
            end = tpl.find('}', i)
            if end == -1:
                push(tpl[i:])
                break
            slot = tpl[i + 1:end]
            i = end + 1

            if slot == '*':
                join(args)
                continue
            if slot.endswith(':'):
                frm = _slot_index(slot[:-1])
                if frm is not None:
                    join(args[frm:])
                    continue
            k = _slot_index(slot)
            if k is not None:
                if k >= len(args):
                    refuse('E_SQL_UNSUPPORTED',
                           f'the mapping for this expression asks for argument {k}, '
                           'which it was not given', pos)
                splice(args[k])
                continue
            # A lexical reference, from a runtime-registered template.
            key, arg = slot.split(':', 1) if ':' in slot else (slot, None)
            val = self.lex(key)
            if not isinstance(val, str):
                refuse('E_SQL_UNSUPPORTED',
                       f'a template used {{{slot}}}, which is neither an argument nor '
                       f'a lexical entry of dialect {self._dialect}', pos)
            if arg is None or arg == '':
                push(val)
                continue
            # binaryCast converts a TEXT or NUM operand to bytes. An operand that
            # is already BIN needs no conversion, and on PostgreSQL converting it
            # is destructive: text::bytea parses its input as a bytea *literal*,
            # where \\ is one backslash and \x41 is a byte, so the round trip
            # changes the bytes or fails the query. Every other cast is
            # idempotent and applied unconditionally; this is the one whose input
            # kind decides whether it means anything.
            if expanding is not None and key in expanding:
                refuse('E_SQL_UNSUPPORTED',
                       f'the {key} lexical entry of dialect {self._dialect} expands '
                       'into itself, so filling it would never finish', pos)
            cast_arg = _slot_index(arg) if key == 'binaryCast' else None
            if (cast_arg is not None and cast_arg < len(args)
                    and isinstance(args[cast_arg], Fragment)
                    and args[cast_arg].kind == 'BIN'):
                splice(args[cast_arg])
                continue
            deeper = set(expanding or ())
            deeper.add(key)
            for p in self.fill(val.replace('{0}', '{' + arg + '}'), args, pos, deeper):
                if isinstance(p, str):
                    push(p)
                else:
                    parts.append(p)
        return parts
