"""The SEL value. One class, used by the interpreter and by host code alike —
there is deliberately no second representation of state. See spec/SPEC.md §3.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from . import decimal as D
from .errors import MAX_DEPTH, Pos, SelError, fail
from .utf8 import bytes_to_hex, encode_utf8, validate_text

NONE = 'NONE'
TEXT = 'TEXT'
BIN = 'BIN'
BOOL = 'BOOL'


class RecordShape:
    """Shared field layout for regular records.

    The evaluator still supports the ordered-dict representation for irregular
    or subsequently mutated values, but the common row shape is kept as a
    tuple of keys plus a flat value array.  This is the representation used by
    the JS lane and is important for the relational hot path: field access no
    longer allocates or hashes a dictionary entry for every row.
    """

    __slots__ = ('keys', 'key_map', 'size', 'key_hashes')

    def __init__(self, keys: tuple[str, ...], key_map: dict[str, int] | None = None) -> None:
        self.keys = keys
        self.key_map = (key_map if key_map is not None
                        else {key: i for i, key in enumerate(keys)})
        self.size = len(keys)
        self.key_hashes: tuple[int, ...] = tuple(hash(k) for k in keys)


_SHAPES: dict[tuple[str, ...], RecordShape] = {}
_SHAPE_CACHE_ENTRIES = 256
_SHAPE_CACHE_MAX_KEYS = 256
_SHAPE_CACHE_MAX_CHARS = 16384


def _cache_record_shape(signature: tuple[str, ...], shape: RecordShape) -> None:
    if len(signature) > _SHAPE_CACHE_MAX_KEYS or sum(map(len, signature)) > _SHAPE_CACHE_MAX_CHARS:
        return
    if len(_SHAPES) >= _SHAPE_CACHE_ENTRIES:
        _SHAPES.clear()
    _SHAPES[signature] = shape

_LIST_KEY = re.compile(r'[1-9][0-9]{0,8}\Z')


def _record_shape(keys: list[str] | tuple[str, ...]) -> RecordShape:
    signature = keys if isinstance(keys, tuple) else tuple(keys)
    shape = _SHAPES.get(signature)
    if shape is None:
        shape = RecordShape(signature)
        _cache_record_shape(signature, shape)
    return shape


def _unique_record_shape(keys: list[str] | tuple[str, ...]) -> RecordShape | None:
    """Return the cached shape, or None for a duplicate-key record.

    This is the constructor-side equivalent of Lisp's duplicate check.  It
    builds the key map while checking uniqueness, so regular rows do not pay for
    a temporary ``set(keys)`` on every record produced by a scale query.
    """
    signature = keys if isinstance(keys, tuple) else tuple(keys)
    shape = _SHAPES.get(signature)
    if shape is not None:
        return shape
    key_map: dict[str, int] = {}
    for index, key in enumerate(signature):
        if key in key_map:
            return None
        key_map[key] = index
    shape = RecordShape(signature, key_map)
    _cache_record_shape(signature, shape)
    return shape


def _list_index(key: str, length: int) -> int:
    if not isinstance(key, str) or _LIST_KEY.fullmatch(key) is None:
        return -1
    index = int(key) - 1
    return index if 0 <= index < length else -1


def iter_entries(value: Any):
    """Iterate ordered children without materialising an entry list.

    The list-returning ``entries()`` API remains for callers that need a stable
    snapshot.  Evaluator hot paths use this iterator so packed list/shape
    storage does not become a stream of temporary ``(key, value)`` tuples.
    """
    if value.shape is not None:
        for index, key in enumerate(value.shape.keys):
            yield key, value.storage[index]
        return
    if value.is_list and value.storage is not None:
        if value.list_keys is not None:
            for index, item in enumerate(value.storage):
                yield value.list_keys[index], item
            return
        for index, item in enumerate(value.storage):
            yield str(index + 1), item
        return
    if value.children:
        for key, item in value.children.items():
            yield key, item
        return


def iter_values(value: Any):
    """Iterate collection values directly, omitting synthetic keys."""
    if value.storage is not None:
        for item in value.storage:
            yield item
        return
    if value.children:
        for item in value.children.values():
            yield item
        return
    if value.kind != NONE:
        yield value


def iter_elements(value: Any):
    """Iterate aggregate elements, including a scalar as synthetic key ``1``."""
    if value.shape is not None:
        for index, key in enumerate(value.shape.keys):
            yield key, value.storage[index]
        return
    if value.is_list and value.storage is not None:
        if value.list_keys is not None:
            for index, item in enumerate(value.storage):
                yield value.list_keys[index], item
            return
        for index, item in enumerate(value.storage):
            yield str(index + 1), item
        return
    if value.children:
        for key, item in value.children.items():
            yield key, item
        return
    if value.kind != NONE:
        yield '1', value


class Value:
    __slots__ = ('kind', '_scalar', 'children', 'is_list', 'shape', 'storage',
                 '_dec_val', 'list_keys', '_list_key_map')

    # The kind constants, mirrored as class attributes so `Value.BOOL` works the
    # way `Value::BOOL` does in PHP. They are also exported from sel/__init__.py.
    NONE = NONE
    TEXT = TEXT
    BIN = BIN
    BOOL = BOOL

    def __init__(self, kind: str, scalar: Any, is_list: bool = False) -> None:
        self.kind = kind
        self._scalar = scalar
        self.is_list = is_list
        # dict, created on demand. Python dicts are insertion-ordered and
        # re-assigning an existing key keeps its original position, which is
        # exactly the contract §3.3 requires — the same reason the JS host uses
        # Map rather than a plain object.
        self.children: dict[str, Value] | None = None
        self.shape: RecordShape | None = None
        self.storage: list[Value] | None = None
        self.list_keys: list[str] | None = None
        self._list_key_map: dict[str, int] | None = None
        # Lisp's VALUE-DEC-VAL is the same useful cache in Python: the text
        # representation remains normative, while repeated numeric coercions
        # reuse the immutable parsed decimal rather than allocating another Dec.
        self._dec_val: D.Dec | None = None

    @property
    def scalar(self) -> Any:
        if self._scalar is None and self._dec_val is not None:
            self._scalar = D.format(self._dec_val)
        return self._scalar

    @scalar.setter
    def scalar(self, val: Any) -> None:
        self._scalar = val

    # --- kind predicates ------------------------------------------------------
    #
    # The recommended way to branch on kind in every host, because it is the one
    # spelling that reads the same in all five: the kind *values* are a string
    # here and in JS, a class constant in PHP, an enum in C++ and a keyword in
    # Lisp, so only a predicate can be documented uniformly. These test the
    # value's own kind and do not apply scalar context.

    def is_none(self) -> bool:
        return self.kind == NONE

    def is_null(self) -> bool:
        return self.kind == NONE and self.size() == 0 and not self.is_list

    def is_vacuous(self) -> bool:
        if self.is_null():
            return True
        if self.kind == NONE and self.size() == 0:
            return True
        if self.kind == TEXT and self.size() == 0:
            return len(self.scalar) == 0 or all(ch in ' \t\r\n' for ch in self.scalar)
        return False

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
    def null() -> Value:
        return Value(NONE, None)

    @staticmethod
    def text(s: str) -> Value:
        # Validated at the boundary: a str carrying a lone surrogate has no UTF-8
        # encoding and cannot be a SEL TEXT value.
        validate_text(s, None)
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
    def shaped(keys: list[str] | tuple[str, ...] | RecordShape,
               values: list[Value] | tuple[Value, ...]) -> Value:
        if not keys:
            return Value.none()
        shape = keys if isinstance(keys, RecordShape) else _record_shape(keys)
        return Value._from_shape(shape, values)

    @staticmethod
    def _from_shape(shape: RecordShape,
                    values: list[Value] | tuple[Value, ...]) -> Value:
        v = Value(NONE, None)
        v.shape = shape
        # The caller transfers ownership of freshly built storage.  Keeping the
        # list avoids a second allocation in the join projector and list
        # constructors; tuples/other sequences still get one defensive list.
        v.storage = values if isinstance(values, list) else list(values)
        return v

    @staticmethod
    def record(keys: list[str], values: list[Value]) -> Value:
        if not keys:
            return Value.none()
        shape = _unique_record_shape(keys)
        if shape is not None:
            return Value._from_shape(shape, values)
        v = Value.none()
        for key, val in zip(keys, values):
            v.set(key, val)
        return v

    @staticmethod
    def from_entries(entries: list[tuple[str, Value]], is_list: bool = False) -> Value:
        if is_list:
            keys = [key for key, _ in entries]
            values = [value for _, value in entries]
            is_dense = all(keys[i] == str(i + 1) for i in range(len(keys)))
            return Value.list(values, None if is_dense else keys)
        keys = [key for key, _ in entries]
        shape = _unique_record_shape(keys)
        if shape is not None:
            return Value._from_shape(shape, [value for _, value in entries])
        v = Value.none()
        for key, value in entries:
            v.set(key, value)
        return v

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
            v = Value(TEXT, None)
            v._dec_val = d
            return v
        parsed = D.parse(d)
        if parsed is None:
            fail('E_NOT_NUM', f'not a number: {d!r}', None)
        v = Value(TEXT, None)
        v._dec_val = parsed
        return v

    @staticmethod
    def int(n: int) -> Value:  # noqa: A003
        v = Value(TEXT, None)
        v._dec_val = D.from_int(n)
        return v

    @staticmethod
    def list(values: list[Value], keys: list[str] | None = None) -> Value:  # noqa: A003
        """Builds a list keyed "1".."n" (or preserved keys). Used by `,` and by
        list-returning built-ins.
        """
        v = Value(NONE, None, is_list=True)
        v.storage = values if isinstance(values, list) else list(values)
        v.list_keys = keys
        return v

    # --- children -------------------------------------------------------------

    def size(self) -> int:
        """A method, not a property, so it reads the same as $v->size(),
        v.size() and (sel:value-size v) in the other four hosts.
        tools/check-api.sh keeps it that way.
        """
        if self.storage is not None:
            return len(self.storage)
        return len(self.children) if self.children else 0

    def has(self, key: str) -> bool:
        if self.shape is not None:
            return key in self.shape.key_map
        if self.is_list and self.storage is not None:
            if self.list_keys is not None:
                if self._list_key_map is None:
                    self._list_key_map = {k: i for i, k in enumerate(self.list_keys)}
                return key in self._list_key_map
            return _list_index(key, len(self.storage)) >= 0
        return bool(self.children) and key in self.children

    def get(self, key: str) -> Value | None:
        if self.shape is not None:
            index = self.shape.key_map.get(key)
            return None if index is None else self.storage[index]
        if self.is_list and self.storage is not None:
            if self.list_keys is not None:
                if self._list_key_map is None:
                    self._list_key_map = {k: i for i, k in enumerate(self.list_keys)}
                index = self._list_key_map.get(key)
                return None if index is None else self.storage[index]
            index = _list_index(key, len(self.storage))
            return None if index < 0 else self.storage[index]
        return self.children.get(key) if self.children else None

    def keys(self) -> list[str]:
        if self.shape is not None:
            return list(self.shape.keys)
        if self.is_list and self.storage is not None:
            if self.list_keys is not None:
                return list(self.list_keys)
            return [str(i + 1) for i in range(len(self.storage))]
        return list(self.children.keys()) if self.children else []

    def values(self) -> list[Value]:
        return list(iter_values(self))

    def entries(self) -> list[tuple[str, Value]]:
        return list(iter_entries(self))

    def set(self, key: str, value: Value) -> Value:
        # Re-assigning an existing key keeps its original position — dict does
        # this, as long as the key is not deleted first.
        if self.shape is not None:
            index = self.shape.key_map.get(key)
            if index is not None:
                self.storage[index] = value
                return self
            entries = self.entries()
            self.shape = None
            self.storage = None
            self.children = dict(entries)
        elif self.is_list and self.storage is not None:
            if self.list_keys is not None:
                if self._list_key_map is None:
                    self._list_key_map = {k: i for i, k in enumerate(self.list_keys)}
                index = self._list_key_map.get(key)
                if index is not None:
                    self.storage[index] = value
                    return self
            else:
                index = _list_index(key, len(self.storage))
                if index >= 0:
                    self.storage[index] = value
                    return self
            entries = self.entries()
            self.storage = None
            self.list_keys = None
            self._list_key_map = None
            self.children = dict(entries)
        if self.children is None:
            self.children = {}
        self.children[key] = value
        return self

    # --- scalar context (§3.2) ------------------------------------------------

    def scalar_source(self, pos: Pos | None = None) -> Value:
        """The value that supplies the scalar: itself, or its first child,
        recursively.
        """
        if self.kind != NONE:
            return self
        v = self
        guard = 0
        while v.kind == NONE:
            if v.is_null():
                fail('E_NULL', 'value is NULL', pos)
            if v.size() == 0:
                fail('E_NO_SCALAR', 'value has no scalar and no children', pos)
            # Scalar context follows one child, independent of collection width.
            v = v.storage[0] if v.storage is not None else next(iter(v.children.values()))
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
        if v._dec_val is not None:
            return v._dec_val
        d = D.parse(v.scalar, pos)
        if d is None:
            fail('E_NOT_NUM', f'not a number: {v.scalar!r}', pos)
        v._dec_val = d
        return d

    def looks_numeric(self) -> bool:
        """Non-throwing probe for ISNUM."""
        if self.kind == NONE and self.size() == 0:
            return False
        # A well-formed numeral too big to hold raises E_RANGE out of parse.
        # The probe answers no rather than raising, so ISNUM is true exactly
        # when the value can be used as a number — before the cap it said true
        # for a 2 000 000-digit text that then failed on first use.
        try:
            v = self.scalar_source(None)
            if v.kind != TEXT:
                return False
            if v._dec_val is not None:
                return True
            d = D.parse(v.scalar)
            if d is not None:
                v._dec_val = d
                return True
            return False
        except SelError:
            return False

    # --- copying --------------------------------------------------------------

    def clone(self, pos: Pos | None = None) -> Value:
        """Assignment copies by value: two variables never share structure (§5.7).

A value's nesting is the third thing spec/SPEC.md §6.4 caps, after the
parser's and the evaluator's, and it was the last one left uncounted. clone,
eql, dump and the two native conversions each recurse once per level, so a
value nested deeply enough reached the host's own stack: RecursionError here
at about a thousand levels, an uncaught RangeError on JS at about four, a
segfault on C++ at about sixty. Three hosts answered where two died, on the
same program.

The depth rides as a parameter, as it does in dependencies(): nothing has to be
released on the way out, so no guard object is needed and all five hosts spell
it the same way. A value of exactly MAX_DEPTH levels is fine; the level past it
is refused. `pos` is reported when the caller has one -- the evaluator knows
which node asked -- and is None for a call from host code, the same convention
as as_text().
        """
        return self._clone_at(1, pos)

    def _clone_at(self, depth: int, pos: Pos | None) -> Value:
        if depth > MAX_DEPTH:
            fail('E_DEPTH', 'value nested too deeply', pos)
        if depth > 1 and self.shape is None and self.storage is None and not self.children:
            return self
        out = Value(self.kind, self._scalar, self.is_list)
        out._dec_val = self._dec_val
        if self.shape is not None:
            out.shape = self.shape
            out.storage = [v._clone_at(depth + 1, pos) for v in self.storage]
        elif self.storage is not None:
            out.storage = [v._clone_at(depth + 1, pos) for v in self.storage]
            if self.list_keys is not None:
                out.list_keys = list(self.list_keys)
        elif self.children:
            out.children = {k: v._clone_at(depth + 1, pos)
                            for k, v in self.children.items()}
        return out

    # PHP spells the deep copy `->copy()`; the alias means a reader coming from
    # docs/contributing.md's Value API table finds whichever name they looked up.
    copy = clone

    # --- structural equality (§5.4) -------------------------------------------

    def eql(self, other: Value, pos: Pos | None = None) -> bool:
        return self._eql_at(other, 1, pos)

    def _eql_at(self, other: Value, depth: int, pos: Pos | None) -> bool:
        if depth > MAX_DEPTH:
            fail('E_DEPTH', 'value nested too deeply', pos)
        if self.kind != other.kind:
            return False
        if self.kind == TEXT:
            if (self._scalar is None and other._scalar is None
                    and self._dec_val is not None and other._dec_val is not None):
                if (self._dec_val.neg != other._dec_val.neg or
                    self._dec_val.scale != other._dec_val.scale or
                    self._dec_val.digits != other._dec_val.digits):
                    return False
            else:
                if self.scalar != other.scalar:
                    return False
        elif self.kind in (BOOL, BIN):
            if self.scalar != other.scalar:
                return False
        if self.size() != other.size():
            return False
        if self.size() == 0:
            return True
        if (self.is_list and other.is_list and self.storage is not None and other.storage is not None
                and self.list_keys is None and other.list_keys is None):
            for i in range(len(self.storage)):
                if not self.storage[i]._eql_at(other.storage[i], depth + 1, pos):
                    return False
            return True
        a, b = self.entries(), other.entries()
        for i in range(len(a)):
            if a[i][0] != b[i][0]:      # key order is normative
                return False
            if not a[i][1]._eql_at(b[i][1], depth + 1, pos):
                return False
        return True

    # --- canonical dump (conformance/README.md) -------------------------------

    def dump(self) -> str:
        return self._dump_at(1)

    def _dump_at(self, depth: int) -> str:
        if depth > MAX_DEPTH:
            fail('E_DEPTH', 'value nested too deeply', None)
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
        parts = [f'{quote_dump(k)}={v._dump_at(depth + 1)}' for k, v in self.entries()]
        return s + '{' + ', '.join(parts) + '}'

    # --- host convenience -----------------------------------------------------

    @staticmethod
    def from_native(x: Any) -> Value:
        return Value._from_native_at(x, 1)

    @staticmethod
    def _from_native_at(x: Any, depth: int) -> Value:
        if depth > MAX_DEPTH:
            fail('E_DEPTH', 'value nested too deeply', None)
        if x is None:
            return Value.null()
        if isinstance(x, Value):
            return x
        if isinstance(x, bool):          # before int: bool is a subclass of int
            return Value.bool(x)
        if isinstance(x, int):
            return Value.int(x)
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
            return Value.list([Value._from_native_at(i, depth + 1) for i in x])
        if isinstance(x, dict):
            return Value.from_entries([
                (str(k), Value._from_native_at(item, depth + 1))
                for k, item in x.items()
            ])
        raise TypeError(f'cannot convert {type(x).__name__} to SEL')

    def to_native(self) -> Any:
        return self._to_native_at(1)

    def _to_native_at(self, depth: int) -> Any:
        if depth > MAX_DEPTH:
            fail('E_DEPTH', 'value nested too deeply', None)
        if self.kind == TEXT or self.kind == BIN or self.kind == BOOL:
            scalar = self.scalar
        else:
            scalar = None
        if self.size() == 0:
            return scalar
        obj = {k: v._to_native_at(depth + 1) for k, v in self.entries()}
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


def structural_hash(value: Value) -> int:
    """Return a storage-independent hash suitable for equality buckets."""
    return _structural_hash_at(value, 1)


def _structural_hash_at(value: Value, depth: int) -> int:
    if depth > MAX_DEPTH:
        fail('E_DEPTH', 'value nested too deeply', None)
    k = value.kind
    if k == TEXT:
        h = hash(value.scalar) ^ 1000003
    elif k == BOOL:
        h = 12345 if value.scalar else 67890
    elif k == BIN:
        h = hash(value.scalar) ^ 2000003
    else:  # NONE
        h = 0

    if value.size() == 0:
        return h

    if value.shape is not None and value.storage is not None:
        for kh, child in zip(value.shape.key_hashes, value.storage):
            ch = _structural_hash_at(child, depth + 1)
            h = ((h * 1000003) ^ kh ^ ch) & 0xffffffffffffffff
    elif value.is_list and value.storage is not None:
        if value.list_keys is None:
            for i, child in enumerate(value.storage, 1):
                ch = _structural_hash_at(child, depth + 1)
                h = ((h * 1000003) ^ hash(str(i)) ^ ch) & 0xffffffffffffffff
        else:
            for k, child in zip(value.list_keys, value.storage):
                ch = _structural_hash_at(child, depth + 1)
                h = ((h * 1000003) ^ hash(k) ^ ch) & 0xffffffffffffffff
    elif value.children:
        for k, child in value.children.items():
            ch = _structural_hash_at(child, depth + 1)
            h = ((h * 1000003) ^ hash(k) ^ ch) & 0xffffffffffffffff
    return h


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
