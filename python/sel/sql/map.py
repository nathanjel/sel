"""Dialect lookup, and the runtime registration an application extends the map
with.

The generated table in ``_map.py`` is already flattened, so a shipped lookup is a
dict access; the overlay written here is what re-introduces the extends chain,
and it is the only thing that does.

**There is no trace facility here, deliberately.** PHP's ``Map::traceOn`` exists
for two checks -- the oracle's per-entry coverage gate and sqlt's caveat pins --
and both ask about ``sql/dialects/*.json`` and ``sql/cases/*.sqlt``, which are
shared data that PHP already measures. Running them a second time here would
measure the same thing twice. See python/bin/sqlt's docstring for the full split.
"""

from __future__ import annotations

from typing import Any

from ..errors import Pos
from ..lexer import ascii_upper
from ._map import DIALECTS
from .errors import refuse

SECTIONS = ('ops', 'funcs', 'skel')

#: Sentinel for "no dialect in the chain mentioned this key".
MISSING = '\0missing'

# Dialects declared at run time.
_extra: dict[str, dict[str, Any]] = {}

# Runtime entries, consulted before the generated table:
# dialect -> section -> key -> entry | builder
_overlay: dict[str, dict[str, dict[str, Any]]] = {}


# --- registration ------------------------------------------------------------

def define_dialect(name: str, spec: dict[str, Any]) -> None:
    """Declare a dialect.

    The usual reason is an older or newer server than the shipped map assumes,
    which needs no special code because a version is only another link in the
    chain::

        map.define_dialect('mariadb-11.8', {'extends': 'mariadb', 'version': '11.8'})

    Raises RuntimeError, not SqlError: a malformed registration is a mistake in
    the application's startup, and ``try_translate`` must not swallow it.
    """
    extends = spec.get('extends')
    if extends is None:
        raise RuntimeError(f'SQL dialect {name} must extend another dialect')
    if not exists(extends):
        raise RuntimeError(f'SQL dialect {name} extends {extends}, which does not exist')
    # Each default is applied when the key is absent OR explicitly null, because
    # PHP's `$spec['version'] ?? …` is. `spec.get('version', …)` would store the
    # explicit None, and version() would then hand version_at_least the string
    # 'None' for int() to choke on.
    def _or(key, default):
        v = spec.get(key)
        return default if v is None else v

    _extra[name] = {
        'extends': extends,
        'version': _or('version', _record(extends)['version']),
        'target': _or('target', True),
        'lexical': _or('lexical', {}),
    }


def define(dialect: str, section: str, key: str, entry: Any) -> None:
    """Define or withdraw one entry.

    Unlike ``sel.registry.define``, redefinition is allowed and the last writer
    wins: a duplicate SEL function is always a bug, while a duplicate SQL entry
    is usually an application deliberately overriding a shipped default for its
    own schema or server build.

    Passing a string withdraws the entry and makes the string the reason the
    caller is given; passing None withdraws it without one.
    """
    _check_section(section)
    if not exists(dialect):
        raise RuntimeError(f'SQL dialect {dialect} does not exist')
    # Only `funcs` keys are SEL function names, which are case-insensitive.
    # `ops` keys are operator tokens and `skel` keys are camel-case names the
    # translator looks up verbatim -- upper-casing those stored a registered
    # skeleton under a key nothing ever reads, which made the documented escape
    # hatch silently dead.
    # ascii_upper, matching PHP's strtoupper and sel.registry.define; see
    # the note in bindings.py on why str.upper() is not the same function.
    k = ascii_upper(key) if section == 'funcs' else key
    _overlay.setdefault(dialect, {}).setdefault(section, {})[k] = entry


def define_builder(dialect: str, section: str, key: str, fn: Any) -> None:
    """The escape hatch, for what a template cannot say.

    A builder receives the already-rendered arguments and returns a Fragment.
    This is the SQL layer's equivalent of the ``fn`` in ``sel.registry.define``.
    """
    define(dialect, section, key, {'builder': fn})


def reset() -> None:
    """Forget every runtime registration. For tests; nothing else should need it."""
    _extra.clear()
    _overlay.clear()


# --- lookup ------------------------------------------------------------------

def exists(dialect: str) -> bool:
    return dialect in _extra or dialect in DIALECTS


def _record(dialect: str) -> dict[str, Any]:
    return _extra[dialect] if dialect in _extra else DIALECTS[dialect]


def targets() -> list[str]:
    """Every dialect that may be named in a translate() call, sorted."""
    out = {d for d, r in DIALECTS.items() if r['target']}
    out |= {d for d, r in _extra.items() if r['target']}
    return sorted(out)


def require_target(dialect: str, pos: Pos | None = None) -> None:
    """Check a dialect may be translated to.

    A base is not a target: `ansi` and `mysql-family` name no server anyone runs,
    and a dialect no database implements is not one a caller should be able to
    aim at.
    """
    if not exists(dialect):
        refuse('E_SQL_DIALECT',
               f'there is no SQL dialect {dialect}; known targets are '
               + ', '.join(targets()), pos)
    if not _record(dialect)['target']:
        refuse('E_SQL_DIALECT',
               f'{dialect} is a base other dialects inherit from, not a server '
               'anyone runs; translate to one of ' + ', '.join(targets()), pos)


def chain(dialect: str) -> list[str]:
    """Self first, then extends, up to ansi."""
    out: list[str] = []
    cur: str | None = dialect
    while cur is not None and exists(cur) and cur not in out:
        out.append(cur)
        cur = _record(cur).get('extends')
    return out


def version(dialect: str) -> str:
    return str(_record(dialect)['version'])


def lexical(dialect: str, key: str) -> Any:
    """A lexical value.

    Runtime dialects may override individual keys; otherwise the generated table
    already holds the flattened result.
    """
    for d in chain(dialect):
        lx = _record(d).get('lexical') or {}
        # `is not None`, matching PHP's isset: a lexical key present but null is
        # not an override. `textCollate` is legitimately the empty string, which
        # IS an override and must not be skipped -- so the test is on None and
        # never on truthiness.
        if lx.get(key) is not None:
            return lx[key]
    return None


def entry(dialect: str, section: str, key: str) -> Any:
    """One entry, or MISSING.

    The overlay is consulted first and walks the chain; the generated table does
    not need walking because the generator flattened it.
    """
    _check_section(section)
    ch = chain(dialect)

    # The whole overlay chain first, and only then the generated table.
    # Interleaving the two per level would look tidier and would be wrong: the
    # generated tables are already flattened, so a generated hit at the leaf
    # would shadow a runtime entry registered against a base, and registering
    # against `ansi` is documented to reach every dialect.
    for d in ch:
        sec = _overlay.get(d, {}).get(section, {})
        if key in sec:
            return sec[key]
    for d in ch:
        sec = DIALECTS.get(d, {}).get(section, {})
        if key in sec:
            return sec[key]
    return MISSING


def entries(dialect: str, section: str) -> dict[str, Any]:
    """Every key one section of a dialect resolves, overlay and generated table
    together, with the entry each resolves to.

    The chain is walked leaf-first so a nearer definition wins, which is the same
    precedence ``entry()`` applies one key at a time.
    """
    _check_section(section)
    # `k not in out`, never `out.get(k) is None`, and for the same reason
    # ``entry()`` tests membership: a dialect withdraws an entry by defining it
    # as None, and a truthiness or None test reads that as "not set yet" and lets
    # the base's live entry through. PHP shipped exactly that, with `??=` here
    # against `array_key_exists` there -- so entry() said a withdrawn entry was
    # gone while entries() still listed it.
    out: dict[str, Any] = {}
    for d in chain(dialect):
        for k, v in (_overlay.get(d, {}).get(section, {})).items():
            if k not in out:
                out[k] = v
    for d in chain(dialect):
        for k, v in (DIALECTS.get(d, {}).get(section, {})).items():
            if k not in out:
                out[k] = v
    return {k: out[k] for k in sorted(out)}


def _check_section(section: str) -> None:
    if section not in SECTIONS:
        raise RuntimeError(f'unknown map section {section}; use ' + ', '.join(SECTIONS))


def version_at_least(have: str, want: str) -> bool:
    """Dotted-numeric, as sql/MAP.md §4.5 specifies and nothing cleverer."""
    a = [int(x) for x in have.split('.')]
    b = [int(x) for x in want.split('.')]
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if x != y:
            return x > y
    return True
