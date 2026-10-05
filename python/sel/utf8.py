"""Native UTF-8 conversion with SEL diagnostics on invalid input.

Valid input uses Python's strict codec. Invalid input goes through the explicit
validator so callers receive E_UTF8 with SEL's messages and source position,
rather than a host Unicode exception.

Python does give one thing free that the other hosts pay for: `str` is a
sequence of code points, so `len()` and slicing already count what SEL counts.
That is why `to_code_points` here is nearly the identity — it exists to *reject*
lone surrogates, which Python permits in a `str` and UTF-8 has no encoding for.
"""

from __future__ import annotations

from typing import NoReturn

from .errors import Pos, fail


def validate_text(s: str, pos: Pos | None = None) -> None:
    if s.isascii():
        return
    try:
        s.encode('utf-8')
    except UnicodeEncodeError:
        to_code_points(s, pos)


def to_code_points(s: str, pos: Pos | None = None) -> list[int]:
    """Code points, rejecting the lone surrogates Python allows in a str.

    `chr(0xD800)` is a legal Python str element and has no UTF-8 encoding, so it
    cannot be a SEL TEXT value. The other hosts hit the same wall from the other
    side: JS strings are UTF-16 and produce lone surrogates by slicing.
    """
    if s.isascii():
        return [ord(ch) for ch in s]
    try:
        s.encode('utf-8')
        return [ord(ch) for ch in s]
    except UnicodeEncodeError:
        _fail_at_surrogate(s, _first_surrogate(s), pos)


def _first_surrogate(s: str) -> int:
    """The index of the first lone surrogate in `s` (a Python str may hold
    one; it has no UTF-8 encoding), or -1."""
    for i, ch in enumerate(s):
        if 0xD800 <= ord(ch) <= 0xDFFF:
            return i
    return -1


def _fail_at_surrogate(s: str, i: int, pos: Pos | None) -> NoReturn:
    which = 'high' if ord(s[i]) <= 0xDBFF else 'low'
    fail('E_UTF8', f'unpaired {which} surrogate', pos)


def _pos_after(prefix: str) -> Pos:
    """The position of the code point that follows `prefix`: line and column count
    from 1 and offsets from 0, all in code points, and only LF ends a line."""
    nl = prefix.rfind('\n')
    return Pos(prefix.count('\n') + 1, len(prefix) - nl, len(prefix))


def check_source(source: str) -> None:
    """E_UTF8 at the first lone surrogate of a source string, positioned like any
    other error (SPEC 2): counted in code points, so a str -- whose length already
    is that count -- needs no decoding to find where it is."""
    if source.isascii():
        return
    i = _first_surrogate(source)
    if i >= 0:
        _fail_at_surrogate(source, i, _pos_after(source[:i]))


def decode_source(data: bytes) -> str:
    """Source bytes to text, for a caller that reads a file or a stream. Strict, and
    nothing else: no replacement character, no newline translation. Invalid UTF-8
    is E_UTF8 positioned at the first invalid unit, counted in the code points of
    the valid prefix (SPEC 2)."""
    try:
        return data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        pass
    at = _first_invalid(data)
    prefix = data[:at].decode('utf-8', errors='strict') if at is not None else ''
    fail('E_UTF8', 'invalid UTF-8 in source', _pos_after(prefix))


def encode_utf8(s: str, pos: Pos | None = None) -> bytes:
    try:
        return s.encode('utf-8')
    except UnicodeEncodeError:
        # Only a lone surrogate fails to encode, and to_code_points raises
        # E_UTF8 at it; no replacement character is ever produced.
        to_code_points(s, pos)
        raise


def decode_utf8(data: bytes, pos: Pos | None = None) -> str:
    """Strict: rejects overlong forms, surrogates, values above U+10FFFF and
    truncated sequences. No replacement characters, ever.
    """
    try:
        return data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        pass
    # Outside the except block: do not chain a host exception into SelError.
    return _decode_utf8_diagnostic(data, pos)


def _first_invalid(data: bytes) -> int | None:
    """The byte index at which the first invalid sequence starts, or None."""
    bad = _scan(data)
    return None if bad is None else bad.at


class _Invalid:
    __slots__ = ('at', 'message')

    def __init__(self, at: int, message: str) -> None:
        self.at = at
        self.message = message


def _decode_utf8_diagnostic(data: bytes, pos: Pos | None) -> str:
    """The precise first invalid byte diagnostic, as a SEL error."""
    bad = _scan(data)
    # Raised outside any except block: no host exception is chained into it.
    fail('E_UTF8', bad.message if bad else 'invalid UTF-8 byte sequence', pos)


def _scan(data: bytes) -> '_Invalid | None':
    """The first bad sequence, or None. Returned rather than raised so the SEL
    error built from it carries no chained host exception."""
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        if b < 0x80:
            i += 1
            continue
        if 0xC2 <= b <= 0xDF:
            need, cp, lo, hi = 1, b & 0x1F, 0x80, 0xBF
        elif b == 0xE0:
            need, cp, lo, hi = 2, 0, 0xA0, 0xBF          # reject overlong 3-byte
        elif 0xE1 <= b <= 0xEC:
            need, cp, lo, hi = 2, b & 0x0F, 0x80, 0xBF
        elif b == 0xED:
            need, cp, lo, hi = 2, 0x0D, 0x80, 0x9F       # reject surrogates
        elif 0xEE <= b <= 0xEF:
            need, cp, lo, hi = 2, b & 0x0F, 0x80, 0xBF
        elif b == 0xF0:
            need, cp, lo, hi = 3, 0, 0x90, 0xBF          # reject overlong 4-byte
        elif 0xF1 <= b <= 0xF3:
            need, cp, lo, hi = 3, b & 0x07, 0x80, 0xBF
        elif b == 0xF4:
            need, cp, lo, hi = 3, 4, 0x80, 0x8F          # cap at U+10FFFF
        else:
            return _Invalid(i, f'invalid start byte 0x{b:x} at byte {i}')

        if i + need >= n:
            return _Invalid(i, f'truncated sequence at byte {i}')
        for k in range(1, need + 1):
            c = data[i + k]
            lo_k = lo if k == 1 else 0x80
            hi_k = hi if k == 1 else 0xBF
            if c < lo_k or c > hi_k:
                return _Invalid(i, f'invalid continuation byte at byte {i + k}')
            cp = (cp << 6) | (c & 0x3F)
        i += need + 1
    return None


def bytes_to_hex(data: bytes) -> str:
    return data.hex()


def bytes_compare(a: bytes, b: bytes) -> int:
    """Bytewise, as the spec requires."""
    if a == b:
        return 0
    return -1 if a < b else 1
