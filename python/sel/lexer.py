"""Tokeniser. See spec/grammar.md.

Python `str` is already a sequence of code points, so unlike the JS and PHP
hosts this needs no explicit code-point array: every index into `self.chars` is
a code point index, which is what keeps reported positions identical across
hosts. The source is still passed through to_code_points once, to reject lone
surrogates as E_UTF8 rather than let them become silently mangled tokens.

String interpolation is resolved here and nowhere else: a literal containing
{...} is emitted as the token stream of a parenthesised `&` chain, so the parser
never learns that interpolation exists.
"""

from __future__ import annotations

from bisect import bisect_right
import re

from . import _lexicon
from .errors import Pos, describe_char, fail
from .utf8 import check_source

# Every symbol token, longest first, and the reserved words: spec/lexicon.json,
# rendered into _lexicon.py (tools/gen-lexicon.mjs). Not a list of this host's.
OPERATORS = list(_lexicon.SYMBOLS)

RESERVED = frozenset(_lexicon.RESERVED)

_SIMPLE_ESCAPES = {
    '\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', '{': '{', '}': '}',
}

# .fullmatch(), for the reason given in sel/decimal.py: Python's `$` also
# matches before a trailing newline, so `\u{41<newline>}` was accepted here
# and rejected by every other host.
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


# Identifiers and number literals are ASCII (§2.3, §2.4), by specification and by
# hand: Python's str.isdigit()/isalpha() accept Unicode ("٣".isdigit() is True and
# "é".isalpha() is True), the same trap SBCL's DIGIT-CHAR-P set for the Lisp host,
# so nothing below consults Python's opinion -- every class is written out.
# The scanners, written as regular
# expressions with explicit classes (never \w, \d or \s, which are Unicode's) so
# the interpreter's C matcher does the per-character work. `endpos` is the range
# bound the loops used to carry as `to`.
_SPACES = re.compile(r'[ \t\r\n]*')
_NUMBER = re.compile(r'[0-9]+(?:\.[0-9]+)?')      # a '.' only when a digit follows
_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

# Operators by length, for a longest-first set lookup in place of a scan of the
# whole table (the table is ordered so that the first match IS the longest: no
# operator is a proper prefix of one listed after it; tests/test_perf_lexer.py
# checks that against the scan).
_OPS3 = frozenset(op for op in OPERATORS if len(op) == 3)
_OPS2 = frozenset(op for op in OPERATORS if len(op) == 2)
_OPS1 = frozenset(op for op in OPERATORS if len(op) == 1)


# The kinds of work lex_range keeps on its explicit stack.
_T_RANGE, _T_PART, _T_CLOSE, _T_END = 0, 1, 2, 3


class Token:
    """A token. A plain slots class: one is built per lexeme, and the dataclass
    __init__ was measurable."""
    __slots__ = ('type', 'value', 'pos')

    def __init__(self, type: str, value: str, pos: Pos) -> None:      # noqa: A002
        self.type = type
        self.value = value
        self.pos = pos

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not Token:
            return NotImplemented
        return (self.type == other.type and self.value == other.value
                and self.pos == other.pos)

    def __repr__(self) -> str:
        return f'Token(type={self.type!r}, value={self.value!r}, pos={self.pos!r})'


_RAW_RUN = re.compile(r"[^']+")
_QUOTED_RUN = re.compile(r'[^"\\{]+')


class Lexer:
    def __init__(self, source: str) -> None:
        check_source(source)      # rejects lone surrogates, positioned (SPEC 2)
        self.chars = source
        self.n = len(source)
        # brace_ends[i] is the index just past the '}' matching the '{' at i,
        # once some scan has established it. See match_brace.
        self.brace_ends: dict[int, int] = {}
        # One hop per newline through str.find (C), not a Python pass over every
        # character of the source.
        line_starts = [0]
        find = source.find
        at = find('\n')
        while at != -1:
            line_starts.append(at + 1)
            at = find('\n', at + 1)
        self.line_starts = line_starts

    def pos_at(self, offset: int) -> Pos:
        line = bisect_right(self.line_starts, offset) - 1
        return Pos(line + 1, offset - self.line_starts[line] + 1, offset)

    def tokenize(self) -> list[Token]:
        out: list[Token] = []
        self.lex_range(0, self.n, out)
        out.append(Token('eof', '', self.pos_at(self.n)))
        return out

    def lex_range(self, frm: int, to: int, out: list[Token]) -> None:
        """Lex chars[frm:to] into `out`. Interpolation nests without bound, so
        this is a loop over an explicit stack of tasks rather than a recursion:
        a literal pushes what it still has to emit (its parts, each interior
        range, the closers) and the loop pops them in source order. Nothing here
        can therefore reach the host's own stack, however deep the braces go.
        """
        stack: list[tuple] = [(_T_RANGE, frm, to, None)]
        while stack:
            task = stack.pop()
            kind = task[0]
            if kind == _T_RANGE:
                self.lex_tokens(task[1], task[2], out, stack, task[3])
            elif kind == _T_PART:
                self.emit_part(task[1], task[2], task[3], out, stack)
            elif kind == _T_CLOSE:
                _, mark, pfrom, pto, bal = task
                # An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
                if len(out) == mark + 1:
                    fail('E_SYNTAX', 'empty interpolation {}', self.pos_at(pfrom))
                # ... and one whose parentheses do not close inside the braces.
                if bal:
                    fail('E_SYNTAX', f'unclosed {bal[-1]} in interpolation',
                         self.pos_at(pto))
                out.append(Token('op', ')', self.pos_at(pto)))
            else:
                out.append(Token('op', ')', task[1]))

    def lex_tokens(self, frm: int, to: int, out: list[Token], stack: list[tuple],
                   bal: list[str] | None) -> None:
        """The flat part of lex_range. A quoted literal with parts ends the run:
        the tasks it pushes come first, and the rest of the range resumes after
        them.

        `bal` is the stack of parentheses and brackets open so far in an
        interpolation body (None at the top level, where the parser does the
        balancing). A body is spliced into the surrounding tokens as `( body )`,
        so a body that closes what it never opened, or leaves something open,
        would change the meaning of the text around it; each body has to balance
        inside its own braces.
        """
        i = frm
        while i < to:
            c = self.chars[i]

            if c in ' \t\r\n':
                i = _SPACES.match(self.chars, i, to).end()
                continue

            if c == '#':
                # To the end of the line (LF only, SPEC 2.1) or of the range.
                nl = self.chars.find('\n', i, to)
                i = to if nl == -1 else nl
                continue

            pos = self.pos_at(i)

            if '0' <= c <= '9':
                # Only consume the dot when a digit follows, so `1.` is not a number.
                j = _NUMBER.match(self.chars, i, to).end()
                out.append(Token('num', self.chars[i:j], pos))
                i = j
                continue

            if ('A' <= c <= 'Z') or ('a' <= c <= 'z') or c == '_':
                j = _IDENT.match(self.chars, i, to).end()
                # ASCII-only identifiers, so ASCII-only upper-casing. str.upper()
                # is full Unicode and would fold "ß" to "SS"; the word is already
                # known to be ASCII, where the two agree, so the C method is safe
                # and ascii_upper's per-character generator is not needed.
                out.append(Token('ident', self.chars[i:j].upper(), pos))
                i = j
                continue

            if c == '"':
                parts, nxt = self.scan_quoted(i, to)
                if len(parts) == 1:
                    out.append(Token('text', parts[0][1], pos))
                    i = nxt
                    continue
                # `( "seg" & expr & "seg" )`: the opener now, the rest as tasks,
                # the remainder of this range underneath them.
                out.append(Token('op', '(', pos))
                stack.append((_T_RANGE, nxt, to, bal))
                stack.append((_T_END, pos))
                for k in range(len(parts) - 1, -1, -1):
                    stack.append((_T_PART, parts[k], k, pos))
                return
            if c == "'":
                i = self.lex_raw(i, to, out)
                continue

            op = self.match_operator(i, to)
            if op:
                if bal is not None:
                    if op == '(' or op == '[':
                        bal.append(op)
                    elif op == ')' or op == ']':
                        if not bal or (bal.pop() == '(') != (op == ')'):
                            fail('E_SYNTAX', f'unbalanced {op} in interpolation', pos)
                out.append(Token('op', op, pos))
                i += len(op)
                continue

            fail('E_SYNTAX', f'unexpected character {describe_char(c)}', pos)

    def emit_part(self, part: tuple, index: int, pos: Pos, out: list[Token],
                  stack: list[tuple]) -> None:
        """One part of an interpolated literal: the `&` before it, then either
        its text or `( interior )`, the interior being a range of its own.
        """
        if index > 0:
            out.append(Token('op', '&', pos))
        if part[0] == 'text':
            out.append(Token('text', part[1], pos))
            return
        _, pfrom, pto = part
        mark = len(out)
        bal: list[str] = []
        out.append(Token('op', '(', self.pos_at(pfrom)))
        stack.append((_T_CLOSE, mark, pfrom, pto, bal))
        stack.append((_T_RANGE, pfrom, pto, bal))

    def match_operator(self, i: int, to: int) -> str | None:
        s = self.chars
        if i + 3 <= to:
            three = s[i:i + 3]
            if three in _OPS3:
                return three
        if i + 2 <= to:
            two = s[i:i + 2]
            if two in _OPS2:
                return two
        if s[i] in _OPS1:
            return s[i]
        return None

    # --- text literals --------------------------------------------------------

    def lex_raw(self, start: int, to: int, out: list[Token]) -> int:
        """Raw 'literals' take no escapes and no interpolation; '' is one quote.
        This is the form to use for regex patterns.
        """
        pos = self.pos_at(start)
        i = start + 1
        buf: list[str] = []
        while i < to:
            c = self.chars[i]
            if c == "'":
                if i + 1 < to and self.chars[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                out.append(Token('text', ''.join(buf), pos))
                return i + 1
            # The whole run up to the next quote in one slice, not a
            # character at a time: c is not a quote, so the run is never empty.
            j = _RAW_RUN.match(self.chars, i, to).end()
            buf.append(self.chars[i:j])
            i = j
        fail('E_UNTERMINATED', 'unterminated raw text literal', pos)

    def scan_quoted(self, start: int, to: int) -> tuple[list[tuple], int]:
        """Read a quoted literal into its parts and the index just past its
        closing quote, emitting nothing. Every `{...}` is skipped by
        match_brace, so the interior is not read here, only located.
        """
        pos = self.pos_at(start)
        parts: list[tuple] = []
        buf: list[str] = []
        i = start + 1

        while i < to:
            c = self.chars[i]

            if c == '"':
                parts.append(('text', ''.join(buf)))
                return parts, i + 1

            if c == '\\':
                text, nxt = self.read_escape(i, to)
                buf.append(text)
                i = nxt
                continue

            if c == '{':
                close = self.match_brace(i, to) - 1   # index of the matching '}'
                parts.append(('text', ''.join(buf)))
                buf = []
                parts.append(('expr', i + 1, close))
                i = close + 1
                continue

            # A run of ordinary characters in one slice; c is not special,
            # so the run is never empty.
            j = _QUOTED_RUN.match(self.chars, i, to).end()
            buf.append(self.chars[i:j])
            i = j
        fail('E_UNTERMINATED', 'unterminated text literal', pos)

    def read_escape(self, i: int, to: int) -> tuple[str, int]:
        pos = self.pos_at(i)
        if i + 1 >= to:
            fail('E_UNTERMINATED', 'text literal ends in a backslash', pos)
        e = self.chars[i + 1]

        if e in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[e], i + 2

        if e == 'u':
            if i + 2 >= to or self.chars[i + 2] != '{':
                fail('E_ESCAPE', '\\u must be followed by {', pos)
            j = i + 3
            hex_digits: list[str] = []
            while j < to and self.chars[j] != '}':
                hex_digits.append(self.chars[j])
                j += 1
            if j >= to:
                fail('E_UNTERMINATED', 'unterminated \\u{...} escape', pos)
            hexs = ''.join(hex_digits)
            if len(hexs) == 0 or len(hexs) > 6 or not _HEX_RE.fullmatch(hexs):
                fail('E_ESCAPE', f'bad \\u{{{hexs}}} escape', pos)
            cp = int(hexs, 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                fail('E_RANGE', f'code point U+{hexs.upper()} is not encodable', pos)
            return chr(cp), j + 1

        fail('E_ESCAPE', f'unknown escape \\{e}', pos)

    def match_brace(self, i: int, to: int) -> int:
        """Index just past the matching '}'. Nested literals are skipped so that
        a brace inside a string inside an interpolation does not close it.

        One pass with an explicit stack of what is open (a brace, a string), not
        a recursion through the strings, and every brace it closes is remembered
        in brace_ends. The second half is what keeps the lexer linear: a literal
        nested d deep is located by its parent and again by each of its own
        ancestors' interiors being lexed, and without the memo each of those
        locate-passes re-read everything below it. If anything is unterminated
        the innermost open construct is the one reported, which is where the
        recursion used to fail.
        """
        memo = self.brace_ends.get(i)
        if memo is not None:
            return memo
        chars = self.chars
        # [is_string, start, brace depth]
        opened: list[list] = [[False, i, 0]]
        j = i
        while True:
            top = opened[-1]
            if j >= to:
                fail('E_UNTERMINATED',
                     'unterminated text literal' if top[0] else 'unterminated { in text literal',
                     self.pos_at(top[1]))
            c = chars[j]
            if top[0]:
                if c == '\\':
                    j += 2
                    continue
                if c == '"':
                    opened.pop()
                    j += 1
                    continue
                if c == '{':
                    opened.append([False, j, 0])
                    continue
                j += 1
                continue
            if c == '"':
                opened.append([True, j, 0])
                j += 1
                continue
            if c == "'":
                j = self.skip_raw(j, to)
                continue
            if c == '{':
                top[2] += 1
                j += 1
                continue
            if c == '}':
                top[2] -= 1
                j += 1
                if top[2] == 0:
                    self.brace_ends[top[1]] = j
                    opened.pop()
                    if not opened:
                        return j
                continue
            if c == '#':
                while j < to and chars[j] != '\n':
                    j += 1
                continue
            j += 1

    def skip_raw(self, j: int, to: int) -> int:
        pos = self.pos_at(j)
        j += 1
        while j < to:
            if self.chars[j] == "'":
                if j + 1 < to and self.chars[j + 1] == "'":
                    j += 2
                    continue
                return j + 1
            j += 1
        fail('E_UNTERMINATED', 'unterminated raw text literal', pos)


# SEL's case rule for names and options: ASCII letters only, never the host's
# Unicode mapping ("ß".upper() is "SS"; "İ".lower() is two code points). On an
# all-ASCII string the str methods are that rule exactly, and in C.
def ascii_upper(s: str) -> str:
    if s.isascii():
        return s.upper()
    return ''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in s)


def ascii_lower(s: str) -> str:
    if s.isascii():
        return s.lower()
    return ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in s)


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
