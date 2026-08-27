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

from .errors import Pos, fail
from .utf8 import to_code_points

OPERATORS = [
    '$==', '$!=', '$<=', '$>=',
    '$<', '$>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=', '&=',
    '+', '-', '*', '/', '%', '&', '=', '<', '>', '(', ')', '[', ']', ',', ';',
]

RESERVED = frozenset([
    'TRUE', 'FALSE', 'AND', 'OR', 'NOT', 'XOR', 'EQL', 'IN', 'BAND', 'BOR', 'BXOR',
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


@dataclass(slots=True)
class Token:
    type: str
    value: str
    pos: Pos


class Lexer:
    def __init__(self, source: str) -> None:
        to_code_points(source, None)      # validate; rejects lone surrogates
        self.chars = source
        self.n = len(source)
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
                i = self.lex_quoted(i, to, out)
                continue
            if c == "'":
                i = self.lex_raw(i, to, out)
                continue

            op = self.match_operator(i, to)
            if op:
                out.append(Token('op', op, pos))
                i += len(op)
                continue

            fail('E_SYNTAX', f'unexpected character {c!r}', pos)

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

    def lex_quoted(self, start: int, to: int, out: list[Token]) -> int:
        pos = self.pos_at(start)
        parts: list[tuple] = []
        buf: list[str] = []
        i = start + 1

        while i < to:
            c = self.chars[i]

            if c == '"':
                parts.append(('text', ''.join(buf)))
                self.emit_parts(parts, pos, out)
                return i + 1

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
        """
        pos = self.pos_at(i)
        depth = 0
        j = i
        while j < to:
            c = self.chars[j]
            if c == '"':
                j = self.skip_quoted(j, to)
                continue
            if c == "'":
                j = self.skip_raw(j, to)
                continue
            if c == '{':
                depth += 1
                j += 1
                continue
            if c == '}':
                depth -= 1
                j += 1
                if depth == 0:
                    return j
                continue
            if c == '#':
                while j < to and self.chars[j] != '\n':
                    j += 1
                continue
            j += 1
        fail('E_UNTERMINATED', 'unterminated { in text literal', pos)

    def skip_quoted(self, j: int, to: int) -> int:
        pos = self.pos_at(j)
        j += 1
        while j < to:
            c = self.chars[j]
            if c == '\\':
                j += 2
                continue
            if c == '"':
                return j + 1
            if c == '{':
                j = self.match_brace(j, to)
                continue
            j += 1
        fail('E_UNTERMINATED', 'unterminated text literal', pos)

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

    def emit_parts(self, parts: list[tuple], pos: Pos, out: list[Token]) -> None:
        """A literal with no interpolation is one token. Otherwise it becomes the
        tokens of `( "seg" & expr & "seg" )` — empty segments included, so the
        result always goes through `&` and obeys §5.2.
        """
        if len(parts) == 1:
            out.append(Token('text', parts[0][1], pos))
            return
        out.append(Token('op', '(', pos))
        for k, part in enumerate(parts):
            if k > 0:
                out.append(Token('op', '&', pos))
            if part[0] == 'text':
                out.append(Token('text', part[1], pos))
            else:
                _, frm, to = part
                mark = len(out)
                out.append(Token('op', '(', self.pos_at(frm)))
                self.lex_range(frm, to, out)
                if len(out) == mark + 1:
                    fail('E_SYNTAX', 'empty interpolation {}', self.pos_at(frm))
                out.append(Token('op', ')', self.pos_at(to)))
        out.append(Token('op', ')', pos))


def ascii_upper(s: str) -> str:
    return ''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in s)


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
