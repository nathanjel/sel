# A frozen copy of python/sel/lexer.py as of 223885e, before PY-P10, kept as the reference the
# scanner rewrite is tested against (tests/test_perf_lexer.py). Do not edit.
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

from dataclasses import dataclass
import re

from sel.errors import Pos, fail
from sel.utf8 import check_source

OPERATORS = [
    '???', '??',
    '$==', '$!=', '$<=', '$>=',
    '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
    '.>',
    '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
]

RESERVED = frozenset([
    'TRUE', 'FALSE', 'NULL', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
])

_SIMPLE_ESCAPES = {
    '\\': '\\', '"': '"', 'n': '\n', 't': '\t', 'r': '\r', '{': '{', '}': '}',
}

# .fullmatch(), for the reason given in sel/decimal.py: Python's `$` also
# matches before a trailing newline, so `\u{41<newline>}` was accepted here
# and rejected by every other host.
_HEX_RE = re.compile(r'^[0-9a-fA-F]+$')


# ASCII by specification, and by hand. Python's str.isdigit()/isalpha() accept
# Unicode — "٣".isdigit() is True and "é".isalpha() is True — which is the same
# trap SBCL's DIGIT-CHAR-P set for the Lisp host. Identifiers and number
# literals are ASCII (§2.3, §2.4), so nothing here may consult Python's opinion.
def _is_digit(c: str) -> bool:
    return '0' <= c <= '9'


def _is_alpha(c: str) -> bool:
    return ('A' <= c <= 'Z') or ('a' <= c <= 'z') or c == '_'


def _is_ident(c: str) -> bool:
    return _is_alpha(c) or _is_digit(c)


def _is_space(c: str) -> bool:
    return c in ' \t\r\n'


# The kinds of work lex_range keeps on its explicit stack.
_T_RANGE, _T_PART, _T_CLOSE, _T_END = 0, 1, 2, 3


@dataclass(slots=True)
class Token:
    type: str
    value: str
    pos: Pos


class Lexer:
    def __init__(self, source: str) -> None:
        check_source(source)      # rejects lone surrogates, positioned (SPEC 2)
        self.chars = source
        self.n = len(source)
        # brace_ends[i] is the index just past the '}' matching the '{' at i,
        # once some scan has established it. See match_brace.
        self.brace_ends: dict[int, int] = {}
        self.line_starts = [0]
        for i, ch in enumerate(source):
            if ch == '\n':
                self.line_starts.append(i + 1)

    def pos_at(self, offset: int) -> Pos:
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return Pos(lo + 1, offset - self.line_starts[lo] + 1, offset)

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

            if _is_space(c):
                i += 1
                continue

            if c == '#':
                while i < to and self.chars[i] != '\n':
                    i += 1
                continue

            pos = self.pos_at(i)

            if _is_digit(c):
                j = i
                while j < to and _is_digit(self.chars[j]):
                    j += 1
                # Only consume the dot when a digit follows, so `1.` is not a number.
                if j + 1 < to and self.chars[j] == '.' and _is_digit(self.chars[j + 1]):
                    j += 1
                    while j < to and _is_digit(self.chars[j]):
                        j += 1
                out.append(Token('num', self.chars[i:j], pos))
                i = j
                continue

            if _is_alpha(c):
                j = i
                while j < to and _is_ident(self.chars[j]):
                    j += 1
                # ASCII-only identifiers, so ASCII-only upper-casing. str.upper()
                # is full Unicode and would fold "ß" to "SS" — it cannot reach a
                # non-ASCII character here, but saying so explicitly is cheaper
                # than the next reader having to prove it.
                word = self.chars[i:j]
                out.append(Token('ident', ascii_upper(word), pos))
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

            fail('E_SYNTAX', f'unexpected character {c!r}', pos)

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
        for op in OPERATORS:
            if i + len(op) > to:
                continue
            if self.chars[i:i + len(op)] == op:
                return op
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
            buf.append(c)
            i += 1
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

            buf.append(c)
            i += 1
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


def ascii_upper(s: str) -> str:
    return ''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in s)


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
