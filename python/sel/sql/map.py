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
import re

from ._map import DIALECTS, RULES
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
    if exists(name):
        raise RuntimeError(
            f'SQL dialect {name} is already defined; a name means one dialect')
    extends = spec.get('extends')
    if extends is None:
        raise RuntimeError(f'SQL dialect {name} must extend another dialect')
    if not exists(extends):
        raise RuntimeError(f'SQL dialect {name} extends {extends}, which does not exist')

    # Each default is applied when the key is absent OR explicitly null, because
    # PHP's `?? …` is. `spec.get('version', …)` would store the explicit None.
    def _or(key, default):
        v = spec.get(key)
        return default if v is None else v

    version = _or('version', _record(extends)['version'])
    # Dotted-numeric, as sql/MAP.md §4.5 says and nothing cleverer. A live server
    # reports "11.8.8-MariaDB", which is the natural thing to pass and is not a
    # version this map can compare: PHP's intval read it as 11.8.8 by guessing
    # and int() raised a ValueError out of the first translation that had a
    # `since`. Refused here, at the line that wrote it.
    if not isinstance(version, str) or not _DOTTED.fullmatch(version):
        raise RuntimeError(f'SQL dialect {name} has version {version!r}, which is not '
                           'dotted-numeric; strip any suffix a server reports '
                           '(11.8.8-MariaDB is 11.8.8)')
    target = _or('target', True)
    if not isinstance(target, bool):
        raise RuntimeError(f'SQL dialect {name} has a target that is not a boolean; '
                           'truthiness differs between hosts and must not decide this')
    lexical = _or('lexical', {})
    if not isinstance(lexical, dict):
        raise RuntimeError(f'SQL dialect {name} has a lexical that is not a map')
    for k, v in lexical.items():
        _check_lexical(str(k), v, f'SQL dialect {name}')

    _extra[name] = {'extends': extends, 'version': version,
                    'target': target, 'lexical': lexical}


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
    _check_key(section, key)
    _check_entry(section, key, entry)
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
    # Membership, not `is not None`, and for the same reason entry() uses it:
    # sql/MAP.md §3 says a null lexical value is a WITHDRAWAL -- "a null
    # binaryLiteral refuses BIN literals" -- and a None test reads that as
    # "absent" and walks on to the base, which handed the withdrawn value back.
    # The documented withdrawal was unimplementable, in both hosts.
    for d in chain(dialect):
        lx = _record(d).get('lexical') or {}
        if key in lx:
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


_DOTTED = re.compile(r'[0-9]+(\.[0-9]+)*')
_TPL_KEY = re.compile(r'0|[1-9][0-9]{0,2}')
_UNIFY = re.compile(r'@unify:[0-9]+(,[0-9]+)*')
_SLOT_IN_TPL = re.compile(r'\{([^}]*)\}')


# --- registration validation -------------------------------------------------
#
# What tools/gen-sql-map.mjs enforces at generation time, enforced here at
# registration time, against the vocabulary that file EMITS rather than a second
# copy of it. Every one of these refusals closes a place where the two hosts
# improvised differently over an entry the generator would never have accepted --
# a JSON list where a template belongs, an arity of strings, a `ret` that was not
# there at all.
#
# RuntimeError, not SqlError: a malformed registration is a mistake in the
# application's startup, and try_translate() must not swallow it.

def _check_lexical(key: str, v: Any, where: str) -> None:
    types = RULES['lexicalTypes']
    if key not in types:
        raise RuntimeError(f'{where} sets the unknown lexical key {key}; known keys '
                           'are ' + ', '.join(types))
    # None is a WITHDRAWAL everywhere in the map, so it is always allowed --
    # sql/MAP.md §3 says a null binaryLiteral refuses BIN literals, and lexical()
    # looks keys up by presence so that it can.
    if v is None:
        return
    if types[key] == 'map':
        # textEscape given as a STRING made both hosts skip escaping entirely and
        # emit 'it's' unquoted. That is an injection, it was in both hosts, and
        # nothing checked.
        if not isinstance(v, dict):
            raise RuntimeError(f'{where} sets {key} to a {type(v).__name__}; it must '
                               'be a map of character to replacement')
        for frm, to in v.items():
            if frm == '' or not isinstance(to, str):
                raise RuntimeError(f"{where}'s {key} maps {frm!r} to something that "
                                   'is not a string')
        return
    # Everything else is a string, and is never cast to one: `true` given as a
    # JSON boolean rendered as `1` on the PHP host and `True` here.
    if not isinstance(v, str):
        raise RuntimeError(f'{where} sets {key} to a {type(v).__name__}; it must be '
                           'a string')
    # A quote character that is not a character cannot quote. Left through, the
    # hosts disagreed about what it meant -- str.replace puts the escape between
    # every character AND at each end, the JS host's split/join only between --
    # and both answers are nonsense. textCollate is legitimately empty (ansi and
    # sqlite ship it that way); these two are not.
    if v == '' and key in ('identQuote', 'textQuote'):
        raise RuntimeError(f'{where} sets {key} to the empty string; a quote '
                           'character that is not a character cannot quote')


def _check_key(section: str, key: str) -> None:
    if section == 'ops' and key not in RULES['opArity']:
        raise RuntimeError(f'{key} is not a SEL operator, so an ops entry for it '
                           'would never be looked up')
    # `funcs` keys are SEL function names and case-insensitive; ops and skel keys
    # are looked up verbatim, which is why define() upper-cases only the first.
    # Registering `and` or `Case` used to be silently dead.
    if section == 'funcs' and ascii_upper(key) not in RULES['funcArity']:
        raise RuntimeError(f'{key} is not a SEL function this layer maps; the '
                           'aggregates and IF/COND/COUNT/HAS/INDEXES/ABORT are '
                           'lowered by stage 2 and never reach the funcs table')
    if section == 'skel' and key not in RULES['skelSlots']:
        raise RuntimeError(f'{key} is not a skeleton; known ones are '
                           + ', '.join(RULES['skelSlots']))


def _check_entry(section: str, key: str, entry: Any) -> None:
    where = f'the {section} entry for {key}'
    # A string is a refusal carrying its reason; None is a refusal without one.
    # Both are entries, and neither has anything else to check.
    if entry is None or isinstance(entry, str):
        return
    if not isinstance(entry, dict):
        raise RuntimeError(f'{where} must be a map, a string or null, and is '
                           f'{type(entry).__name__}')
    if entry.get('builder') is not None:
        if not callable(entry['builder']):
            raise RuntimeError(f'{where} has a builder that is not callable; use '
                               'map.define_builder()')
        return

    # A skeleton is a template with NAMED slots and no kind: the translator
    # decides what a CASE or a subquery yields, not the map. So it is checked for
    # its slots and nothing else.
    if section == 'skel':
        if not isinstance(entry.get('tpl'), str):
            raise RuntimeError(f'{where} needs a tpl that is a string')
        allowed = RULES['skelSlots'][key]
        for slot in _SLOT_IN_TPL.findall(entry['tpl']):
            if slot not in allowed:
                raise RuntimeError(f'{where} uses the slot {{{slot}}}; {key} has '
                                   + ', '.join(allowed) + ' — a typo would survive '
                                   'as literal text in every query')
        if entry.get('caveat') is not None and entry['caveat'] not in RULES['caveats']:
            raise RuntimeError(f'{where} declares the caveat {entry["caveat"]!r}, which '
                               'is not on the closed list in sql/MAP.md §4.6')
        return

    if ('tpl' in entry) == ('variants' in entry):
        raise RuntimeError(f'{where} needs exactly one of tpl and variants')
    ret = entry.get('ret')
    if not isinstance(ret, str) or (ret not in RULES['retKinds'] and ret != '@concat'
                                    and not _UNIFY.fullmatch(ret)):
        raise RuntimeError(f'{where} has ret {ret!r}; use one of '
                           + ', '.join(RULES['retKinds'])
                           + ', @concat or @unify:<n>[,<n>...]')
    if entry.get('caveat') is not None and entry['caveat'] not in RULES['caveats']:
        raise RuntimeError(f'{where} declares the caveat {entry["caveat"]!r}, which is '
                           'not on the closed list in sql/MAP.md §4.6; a caveat an '
                           'application cannot branch on is prose')
    since = entry.get('since')
    if since is not None and (not isinstance(since, str) or not _DOTTED.fullmatch(since)):
        raise RuntimeError(f'{where} has a since that is not dotted-numeric')
    arity = entry.get('arity')
    if arity is not None:
        ok = (isinstance(arity, list) and len(arity) == 2
              and all(isinstance(x, int) and not isinstance(x, bool) for x in arity)
              and arity[0] >= 0 and arity[1] >= arity[0])
        if not ok:
            raise RuntimeError(f'{where} has an arity that is not [min, max] of two '
                               'integers')
    if 'variants' in entry:
        if not isinstance(entry['variants'], dict) or not entry['variants']:
            raise RuntimeError(f'{where} has variants that are not a map')
        allowed = RULES['variants'].get(key)
        if allowed is None:
            raise RuntimeError(f'{where} uses variants, and {key} is not a variant family')
        for name in entry['variants']:
            if name not in allowed:
                raise RuntimeError(f'{where} declares the variant {name}; {key} has '
                                   + ', '.join(allowed))
    tpl = entry.get('tpl')
    if isinstance(tpl, (dict, list)):
        # Every key is an argument COUNT the entry can actually be called with,
        # checked against SEL's own arity narrowed by the entry's.
        #
        # Checking the shape alone is not enough, and PHP is why: a JSON list
        # ["a", "b"] decodes there to an array whose keys are 0 and 1, which are
        # perfectly good count keys, so it is indistinguishable from
        # {"0": "a", "1": "b"} -- and it reached the renderer and emitted the
        # literal `b`. Against UPPER's arity of [1, 1] the count 0 is out of
        # range, and the list is refused for the reason it is actually wrong.
        # A list is included here so both hosts refuse it at the same line.
        lo, hi = (RULES['opArity'][key] if section == 'ops'
                  else RULES['funcArity'][ascii_upper(key)])
        if arity is not None:
            lo = max(lo, arity[0])
            hi = arity[1] if hi is None else min(hi, arity[1])
        keys = range(len(tpl)) if isinstance(tpl, list) else tpl
        for n in keys:
            if n == '*':
                continue
            if not _TPL_KEY.fullmatch(str(n)):
                raise RuntimeError(f'{where} keys a template by {n!r}; an arity-keyed '
                                   'template uses a count or *')
            c = int(n)
            if c < lo or (hi is not None and c > hi):
                raise RuntimeError(f'{where} keys a template by {c}, and {key} takes '
                                   f'{lo} to {hi if hi is not None else "any"} '
                                   'argument(s), so that template could never be chosen')


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
