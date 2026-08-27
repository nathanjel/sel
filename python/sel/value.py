"""The SEL value. One class, used by the interpreter and by host code alike —
there is deliberately no second representation of state. See spec/SPEC.md §3.
"""

from __future__ import annotations

from typing import Any, Iterator

from . import decimal as D
from .errors import Pos, SelError, fail
from .utf8 import bytes_to_hex, decode_utf8, encode_utf8, to_code_points

NONE = 'NONE'
TEXT = 'TEXT'
BIN = 'BIN'
BOOL = 'BOOL'


class Value:
    __slots__ = ('kind', 'scalar', 'children')

    # The kind constants, mirrored as class attributes so `Value.BOOL` works the
    # way `Value::BOOL` does in PHP. They are also exported from sel/__init__.py.
    NONE = NONE
    TEXT = TEXT
    BIN = BIN
    BOOL = BOOL

    def __init__(self, kind: str, scalar: Any) -> None:
        self.kind = kind
        self.scalar = scalar
        # dict, created on demand. Python dicts are insertion-ordered and
        # re-assigning an existing key keeps its original position, which is
        # exactly the contract §3.3 requires — the same reason the JS host uses
        # Map rather than a plain object.
        self.children: dict[str, Value] | None = None

    # --- kind predicates ------------------------------------------------------
    #
    # The recommended way to branch on kind in every host, because it is the one
    # spelling that reads the same in all five: the kind *values* are a string
    # here and in JS, a class constant in PHP, an enum in C++ and a keyword in
    # Lisp, so only a predicate can be documented uniformly. These test the
    # value's own kind and do not apply scalar context.

    def is_none(self) -> bool:
        return self.kind == NONE

    def is_text(self) -> bool:
        return self.kind == TEXT

    def is_bin(self) -> bool:
        return self.kind == BIN

    def is_bool(self) -> bool:
        return self.kind == BOOL

    # --- constructors ---------------------------------------------------------

    @staticmethod
    def none() -> Value:
        return Value(NONE, None)

    @staticmethod
    def text(s: str) -> Value:
        # Validated at the boundary: a str carrying a lone surrogate has no UTF-8
        # encoding and cannot be a SEL TEXT value.
        to_code_points(s, None)
        return Value(TEXT, s)

    @staticmethod
    def bin(b: bytes | bytearray | list[int]) -> Value:
        # Validated at the boundary, like Value.text: a list carrying a value
        # outside 0-255 is E_RANGE and not a bare Python ValueError. Host code is
        # the one place bad data can enter, so it is the place to reject it, and
        # spec/SPEC.md §8 says every failure a host sees is a SelError.
        try:
            return Value(BIN, bytes(b))
        except (ValueError, TypeError) as e:
            fail('E_RANGE', f'not a sequence of bytes: {e}', None)

    @staticmethod
    def bool(b: bool) -> Value:  # noqa: A003
        return Value(BOOL, bool(b))

    @staticmethod
    def num(d: str | D.Dec) -> Value:
        """A string is canonicalised and validated: "007" becomes "7", and
        anything that is not a number is E_NOT_NUM here rather than a TEXT value
        that fails later somewhere else. Internal callers pass a Dec.
        """
        if not isinstance(d, str):
            return Value(TEXT, D.format(d))
        parsed = D.parse(d)
        if parsed is None:
            fail('E_NOT_NUM', f'not a number: {d!r}', None)
        return Value(TEXT, D.format(parsed))

    @staticmethod
    def int(n: int) -> Value:  # noqa: A003
        return Value(TEXT, D.format(D.from_int(n)))

    @staticmethod
    def list(values: list[Value]) -> Value:  # noqa: A003
        """Builds a list keyed "1".."n". Used by `,` and by list-returning
        built-ins.
        """
        v = Value.none()
        for i, x in enumerate(values):
            v.set(str(i + 1), x)
        return v

    # --- children -------------------------------------------------------------

    def size(self) -> int:
        """A method, not a property, so it reads the same as $v->size(),
        v.size() and (sel:value-size v) in the other four hosts.
        tools/check-api.sh keeps it that way.
        """
        return len(self.children) if self.children else 0

    def has(self, key: str) -> bool:
        return bool(self.children) and key in self.children

    def get(self, key: str) -> Value | None:
        return self.children.get(key) if self.children else None

    def keys(self) -> list[str]:
        return list(self.children.keys()) if self.children else []

    def values(self) -> list[Value]:
        return list(self.children.values()) if self.children else []

    def entries(self) -> list[tuple[str, Value]]:
        return list(self.children.items()) if self.children else []

    def set(self, key: str, value: Value) -> Value:
        # Re-assigning an existing key keeps its original position — dict does
        # this, as long as the key is not deleted first.
        if self.children is None:
            self.children = {}
        self.children[key] = value
        return self

    def delete(self, key: str) -> Value:
        if self.children:
            self.children.pop(key, None)
        return self

    # --- scalar context (§3.2) ------------------------------------------------

    def scalar_source(self, pos: Pos | None = None) -> Value:
        """The value that supplies the scalar: itself, or its first child,
        recursively.
        """
        v = self
        guard = 0
        while v.kind == NONE:
            if not v.children:
                fail('E_NO_SCALAR', 'value has no scalar and no children', pos)
            v = next(iter(v.children.values()))
            guard += 1
            if guard > 1000:
                fail('E_DEPTH', 'scalar context nested too deeply', pos)
        return v

    def as_text(self, pos: Pos | None = None) -> str:
        v = self.scalar_source(pos)
        if v.kind == TEXT:
            return v.scalar
        if v.kind == BIN:
            fail('E_NOT_TEXT', 'expected text, got binary (use FROM_UTF8)', pos)
        fail('E_NOT_TEXT', 'expected text, got boolean', pos)

    def as_bytes(self, pos: Pos | None = None) -> bytes:
        v = self.scalar_source(pos)
        if v.kind == BIN:
            return v.scalar
        if v.kind == TEXT:
            return encode_utf8(v.scalar, pos)
        fail('E_NOT_BIN', 'expected binary or text, got boolean', pos)

    def as_bool(self, pos: Pos | None = None) -> bool:
        v = self.scalar_source(pos)
        if v.kind == BOOL:
            return v.scalar
        fail('E_NOT_BOOL', 'expected a boolean — SEL has no truthiness', pos)

    def as_decimal(self, pos: Pos | None = None) -> D.Dec:
        v = self.scalar_source(pos)
        if v.kind != TEXT:
            fail('E_NOT_NUM', f'expected a number, got {v.kind.lower()}', pos)
        d = D.parse(v.scalar)
        if d is None:
            fail('E_NOT_NUM', f'not a number: {v.scalar!r}', pos)
        return d

    def looks_numeric(self) -> bool:
        """Non-throwing probe for ISNUM."""
        if self.kind == NONE and self.size() == 0:
            return False
        try:
            v = self.scalar_source(None)
        except SelError:
            return False
        return v.kind == TEXT and D.parse(v.scalar) is not None

    # --- copying --------------------------------------------------------------

    def clone(self) -> Value:
        """Assignment copies by value: two variables never share structure (§5.7)."""
        out = Value(self.kind, self.scalar)
        if self.children:
            out.children = {k: v.clone() for k, v in self.children.items()}
        return out

    # PHP spells the deep copy `->copy()`; the alias means a reader coming from
    # docs/EXTENDING.md's Value API table finds whichever name they looked up.
    copy = clone

    # --- structural equality (§5.4) -------------------------------------------

    def eql(self, other: Value) -> bool:
        if self.kind != other.kind:
            return False
        if self.kind in (TEXT, BOOL, BIN):
            if self.scalar != other.scalar:
                return False
        if self.size() != other.size():
            return False
        if self.size() == 0:
            return True
        a, b = self.entries(), other.entries()
        for i in range(len(a)):
            if a[i][0] != b[i][0]:      # key order is normative
                return False
            if not a[i][1].eql(b[i][1]):
                return False
        return True

    # --- canonical dump (conformance/README.md) -------------------------------

    def dump(self) -> str:
        if self.kind == NONE:
            s = '-'
        elif self.kind == TEXT:
            s = 't' + quote_dump(self.scalar)
        elif self.kind == BIN:
            s = 'b' + bytes_to_hex(self.scalar)
        else:
            s = 'TRUE' if self.scalar else 'FALSE'
        if self.size() == 0:
            return s
        parts = [f'{quote_dump(k)}={v.dump()}' for k, v in self.entries()]
        return s + '{' + ', '.join(parts) + '}'

    # --- host convenience -----------------------------------------------------

    @staticmethod
    def from_native(x: Any) -> Value:
        if x is None:
            return Value.none()
        if isinstance(x, Value):
            return x
        if isinstance(x, bool):          # before int: bool is a subclass of int
            return Value.bool(x)
        if isinstance(x, int):
            return Value.text(str(x))
        if isinstance(x, float):
            # Refused on purpose, exactly as PHP's Value::fromNative does. There
            # is no floating point anywhere in SEL, and the host boundary is the
            # place to say so: 0.1 + 0.2 has no exact decimal form, and guessing
            # one here is how a backend and a frontend start disagreeing.
            raise TypeError(
                f'cannot convert float {x!r} to SEL — pass a string such as '
                f'"{x!r}" so the decimal value is exactly what you wrote')
        if isinstance(x, str):
            return Value.text(x)
        if isinstance(x, (bytes, bytearray)):
            return Value.bin(x)
        if isinstance(x, (list, tuple)):
            return Value.list([Value.from_native(i) for i in x])
        if isinstance(x, dict):
            v = Value.none()
            for k, item in x.items():
                v.set(str(k), Value.from_native(item))
            return v
        raise TypeError(f'cannot convert {type(x).__name__} to SEL')

    def to_native(self) -> Any:
        if self.kind == TEXT or self.kind == BIN or self.kind == BOOL:
            scalar = self.scalar
        else:
            scalar = None
        if self.size() == 0:
            return scalar
        obj = {k: v.to_native() for k, v in self.entries()}
        if scalar is None:
            return obj
        return {'_': scalar, **obj}

    # --- Python niceties, outside the cross-host contract ---------------------

    def __repr__(self) -> str:
        return f'<sel.Value {self.dump()}>'

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return self.size()

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __getitem__(self, key: str) -> Value:
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v


_DUMP_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r'}


def quote_dump(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _DUMP_ESCAPES:
            out.append(_DUMP_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f'\\u{ord(ch):04x}')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)
