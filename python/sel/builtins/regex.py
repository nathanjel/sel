r"""The portable regex subset. See spec/SPEC.md §7.8.

A pattern is validated against a whitelist before it reaches the host engine, so
anything two engines would disagree about fails loudly here instead of producing
different answers on different hosts. `validate()` below is a line-for-line port
of the shared validator and its output is **host-neutral** — every host compiles
the identical rewritten source.

What is Python-specific lives in `_lower_anchors` and `_compile`:

* **Anchors.** Python's `$` also matches before a trailing newline, exactly like
  PCRE and cl-ppcre and unlike ECMAScript. SEL anchors `^` and `$` to the ends of
  the subject and nothing else, so they are lowered to `\A` and `\Z` after
  validation — the same fix the Lisp host applies. It cannot go inside
  `validate()`: `\A` is not ECMAScript and would break the JS host.

* **Case folding.** `re.IGNORECASE` on a `str` pattern folds *more* than
  ECMAScript's `iu` and PCRE2's `ui` do. Enumerated exhaustively over the whole
  code space, Python considers exactly four non-ASCII code points equal to an
  ASCII letter — U+212A to `k`, U+017F to `s`, and **U+0130 and U+0131 to `i`**.
  The first two are right; the last two are not, and made
  `RMATCH('^i$', "\u{0130}", "i")` true here and false everywhere else.

  So the `i` flag is compiled with `re.ASCII | re.IGNORECASE`, which restricts
  folding to ASCII and drops all four, and the subject is then pre-folded to put
  the two correct ones back: U+212A -> `k`, U+017F -> `s`. This is the same
  mechanism the Lisp host uses for the same two code points, arriving from the
  opposite direction — cl-ppcre folds neither, Python folded too many.

  Pre-folding the *subject* is only safe because both substitutions are one code
  point for one code point, so every offset and length is preserved. Group text
  and replacement output are still sliced from the **original** subject, never
  from the folded one, or `RGROUPS` would hand back a `k` the user never wrote.
  U+00DF is deliberately not in the table: it folds to `ss` only under *full*
  folding, which changes length, and SEL specifies simple folding.

  `re.ASCII` has no other effect here — `\d`, `\w` and `\s` are already
  rewritten to explicit classes by validate(), and `\b` is rejected outright.

* **Offsets are free.** `re` runs over a `str`, so match offsets are already code
  point indices. The other hosts convert from UTF-16 or byte offsets here; there
  is deliberately no conversion to port, and adding one would be wrong.

`re.finditer` and `re.sub` are not used. finditer's zero-width advancement
changed in 3.7 and differs from the loop the other hosts run, and `sub` would
give `\1` and `\g<name>` meaning inside a user-supplied replacement string.
"""

import re

from ..errors import fail
from ..registry import define
from ..value import Value

# \d, \w and \s are rewritten into explicit ASCII classes rather than passed
# through, so the guarantee is structural instead of dependent on a library flag.
EXPAND_OUTSIDE = {
    'd': '[0-9]', 'D': '[^0-9]',
    'w': '[0-9A-Za-z_]', 'W': '[^0-9A-Za-z_]',
    's': '[ \\t\\n\\r\\f\\x0b]', 'S': '[^ \\t\\n\\r\\f\\x0b]',
}
EXPAND_INSIDE = {'d': '0-9', 'w': '0-9A-Za-z_', 's': ' \\t\\n\\r\\f\\x0b'}

# \v is excluded: in PCRE it means "any vertical whitespace", in ECMAScript it
# means U+000B. Same spelling, different language.
CONTROL_ESCAPES = frozenset(['n', 'r', 't', 'f'])
SYNTAX_CHARS = frozenset(['^', '$', '\\', '.', '*', '+', '?', '(', ')',
                          '[', ']', '{', '}', '|', '/'])

MAX_QUANTIFIER = 65535   # PCRE2's own hard limit


def _bad(message, pattern, at, pos):
    fail('E_REGEX_SYNTAX', f'{message} (at offset {at} of /{pattern}/)', pos)


def _reject_escape(e, pattern, at, pos):
    if e in ('b', 'B'):
        _bad(f'\\{e} is not portable — word boundaries depend on the engine\'s idea '
             'of a word character, which differs. Use an explicit class such as '
             '(^|[^0-9A-Za-z_])', pattern, at, pos)
    if e == 'v':
        _bad('\\v is not portable — PCRE reads it as any vertical whitespace and '
             'ECMAScript as U+000B', pattern, at, pos)
    if '0' <= e <= '9':
        _bad('backreferences are not portable', pattern, at, pos)
    if e in ('p', 'P'):
        _bad('\\p{...} is not portable', pattern, at, pos)
    if e in ('A', 'z', 'Z', 'G', 'K'):
        _bad(f'\\{e} is not portable — use ^ and $', pattern, at, pos)
    _bad(f'unsupported escape \\{e}', pattern, at, pos)


def validate(pattern, pos=None):
    """Validates and rewrites in one pass, returning source that means the same
    thing to every engine. Every host runs this, so every host compiles the
    identical pattern.
    """
    p = pattern
    n = len(p)
    out = []
    i = 0

    while i < n:
        c = p[i]

        if c == '\\':
            if i + 1 >= n:
                _bad('trailing backslash', pattern, i, pos)
            e = p[i + 1]
            if e in EXPAND_OUTSIDE:
                out.append(EXPAND_OUTSIDE[e])
                i += 2
                continue
            if e in CONTROL_ESCAPES or e in SYNTAX_CHARS:
                out.append(c + e)
                i += 2
                continue
            _reject_escape(e, pattern, i, pos)

        if c == '[':
            text, nxt = _validate_class(p, i, pattern, pos)
            out.append(text)
            i = nxt
            continue

        if c == '(':
            if i + 1 < n and p[i + 1] == '?':
                nxt = p[i + 2] if i + 2 < n else ''
                if nxt == ':':
                    out.append('(?:')
                    i += 3
                    continue
                kind = ('lookahead' if nxt in ('=', '!')
                        else 'lookbehind and named groups' if nxt == '<'
                        else 'atomic groups' if nxt == '>'
                        else 'this group type')
                _bad(f'{kind} is not portable — only (?: ) is', pattern, i, pos)
            out.append('(')
            i += 1
            continue

        if c == '{':
            end = _after_quantifier(p, _validate_braces(p, i, pattern, pos), pattern, pos)
            out.append(p[i:end])
            i = end
            continue
        if c in ('*', '+', '?'):
            end = _after_quantifier(p, i + 1, pattern, pos)
            out.append(p[i:end])
            i = end
            continue
        if c == '}':
            _bad('unmatched } — escape it as \\}', pattern, i, pos)
        if c == ']':
            _bad('unmatched ] — escape it as \\]', pattern, i, pos)

        out.append(c)
        i += 1
    return ''.join(out)


def _after_quantifier(p, i, pattern, pos):
    """A quantifier may be followed by `?` (lazy). `+` would make it possessive,
    which PCRE supports and ECMAScript does not.
    """
    if i < len(p) and p[i] == '+':
        _bad('possessive quantifiers are not portable', pattern, i, pos)
    if i < len(p) and p[i] == '?':
        return i + 1
    return i


def _validate_braces(p, start, pattern, pos):
    """spec/SPEC.md §6.4. Both bounds are checked here rather than left to the
    engine: PCRE2 and SRELL reject a huge repeat count as a syntax error while
    ECMAScript and cl-ppcre accept it and never match, and cl-ppcre also accepts
    the empty {2,1}. Python accepts both, so this check is what stops it.
    """
    i = start + 1
    lo_start = i
    while i < len(p) and '0' <= p[i] <= '9':
        i += 1
    if i == lo_start:
        _bad('{ must begin a quantifier such as {2,4} — escape it as \\{',
             pattern, start, pos)
    lo = int(p[lo_start:i])
    hi = None
    if i < len(p) and p[i] == ',':
        i += 1
        hi_start = i
        while i < len(p) and '0' <= p[i] <= '9':
            i += 1
        if i > hi_start:
            hi = int(p[hi_start:i])
    if i >= len(p) or p[i] != '}':
        _bad('malformed quantifier', pattern, start, pos)
    if lo > MAX_QUANTIFIER or (hi is not None and hi > MAX_QUANTIFIER):
        _bad(f'quantifier bound exceeds the maximum of {MAX_QUANTIFIER}',
             pattern, start, pos)
    if hi is not None and hi < lo:
        _bad(f'quantifier {{{lo},{hi}}} is empty — the upper bound is below the '
             'lower one', pattern, start, pos)
    return i + 1


def _validate_class(p, start, pattern, pos):
    """Returns (rewritten text, index just past the closing ']')."""
    i = start + 1
    out = ['[']
    if i < len(p) and p[i] == '^':
        out.append('^')
        i += 1
    if i + 1 < len(p) and p[i] == '[' and p[i + 1] == ':':
        _bad('POSIX classes such as [[:alpha:]] are not portable', pattern, i, pos)
    # `]` always closes the class. PCRE treats a leading `]` as a literal while
    # ECMAScript reads `[]` as an empty class, so neither spelling is portable.
    count = 0
    while i < len(p):
        c = p[i]
        if c == ']':
            if count == 0:
                _bad('empty character class — write \\] for a literal bracket',
                     pattern, start, pos)
            out.append(']')
            return ''.join(out), i + 1
        count += 1
        if c == '\\':
            if i + 1 >= len(p):
                _bad('trailing backslash in character class', pattern, i, pos)
            e = p[i + 1]
            if e in EXPAND_INSIDE:
                out.append(EXPAND_INSIDE[e])
                i += 2
                continue
            if e in ('D', 'W', 'S'):
                _bad(f'\\{e} inside a character class cannot be expressed portably '
                     '— negate the whole class instead', pattern, i, pos)
            if e in CONTROL_ESCAPES or e in SYNTAX_CHARS or e == '-':
                out.append(c + e)
                i += 2
                continue
            _reject_escape(e, pattern, i, pos)
        out.append(c)
        i += 1
    _bad('unterminated character class', pattern, start, pos)


# --- compilation ------------------------------------------------------------

def _lower_anchors(src: str) -> str:
    """`^` -> `\\A`, `$` -> `\\Z`, outside character classes and not escaped.

    Applied after validate(), never inside it: validate()'s output is shared with
    the hosts whose engines have no \\A. A `^` immediately after `[` is class
    negation, not an anchor, and `[$]` is a literal dollar.
    """
    out = []
    i = 0
    n = len(src)
    in_class = False
    while i < n:
        c = src[i]
        if c == '\\' and i + 1 < n:
            out.append(src[i:i + 2])
            i += 2
            continue
        if in_class:
            if c == ']':
                in_class = False
            out.append(c)
            i += 1
            continue
        if c == '[':
            in_class = True
            out.append(c)
            i += 1
            if i < n and src[i] == '^':      # negation, not an anchor
                out.append('^')
                i += 1
            continue
        if c == '^':
            out.append('\\A')
            i += 1
            continue
        if c == '$':
            out.append('\\Z')
            i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


# The two code points that fold to an ASCII letter under simple case folding.
# One code point to one code point, so folding the subject preserves offsets.
_FOLD = {0x212A: 'k', 0x017F: 's'}


def _fold_subject(subject: str) -> str:
    return subject.translate(_FOLD)


def _group_text(m, i, original):
    """Group i, sliced from the ORIGINAL subject rather than the folded one."""
    start, end = m.span(i)
    if start < 0:
        return None
    return original[start:end]


_cache: dict[tuple, re.Pattern] = {}


def _compile(pattern, flags, pos, pat_pos):
    ignore_case = False
    for ch in flags:
        f = ch.lower()
        if f == 'i':
            ignore_case = True
            continue
        if f in ('m', 's'):
            fail('E_BAD_ARG',
                 f'flag "{ch}" is not offered — SEL always matches . against any '
                 'character and anchors ^ $ to the whole subject', pos)
        fail('E_BAD_ARG', f'unknown regex flag "{ch}"', pos)

    if ignore_case:
        for ch in pattern:
            if ord(ch) > 0x7F:
                fail('E_BAD_ARG',
                     'the i flag needs an ASCII-only pattern — case folding above '
                     'ASCII differs between PCRE and ECMAScript', pos)

    key = (ignore_case, pattern)
    rx = _cache.get(key)
    if rx is None:
        source = _lower_anchors(validate(pattern, pat_pos))
        opts = re.DOTALL
        if ignore_case:
            opts |= re.IGNORECASE | re.ASCII
        try:
            rx = re.compile(source, opts)
        except re.error as e:
            fail('E_REGEX_SYNTAX', f'{e} in /{pattern}/', pat_pos)
        _cache[key] = rx
    return rx, ignore_case


def _matches(rx, subject):
    """Mirrors the loop the other hosts run rather than using finditer, so
    zero-width advancement is identical everywhere.
    """
    pos = 0
    n = len(subject)
    while pos <= n:
        m = rx.search(subject, pos)
        if m is None:
            return
        yield m
        if m.end() == m.start():
            # Advance a whole code point so a zero-width match cannot loop.
            pos = m.end() + 1
        else:
            pos = m.end()


def _args_for(args, pat_i, subj_i, flag_i):
    """Returns (compiled, original subject, subject to match against).

    The two differ only under the i flag, and only in the two code points in
    _FOLD — but they must not be confused: offsets come from matching the folded
    subject, text comes from the original.
    """
    pattern = args.text(pat_i)
    subject = args.text(subj_i)
    flags = args.text(flag_i) if args.count() > flag_i else ''
    flag_pos = args.pos_of(flag_i) if args.count() > flag_i else args.pos
    rx, ignore_case = _compile(pattern, flags, flag_pos, args.pos_of(pat_i))
    return rx, subject, _fold_subject(subject) if ignore_case else subject


def _rmatch(a, ctx):
    rx, _original, subject = _args_for(a, 0, 1, 2)
    return Value.bool(rx.search(subject) is not None)


def _rfind(a, ctx):
    rx, _original, subject = _args_for(a, 0, 1, 2)
    m = rx.search(subject)
    # re runs over a str, so m.start() is already a code point index.
    return Value.int(m.start() + 1 if m else 0)


def _rgroups(a, ctx):
    rx, original, subject = _args_for(a, 0, 1, 2)
    m = rx.search(subject)
    if m is None:
        return Value.none()
    out = []
    for i in range(rx.groups + 1):
        g = _group_text(m, i, original)
        out.append(Value.text(g if g is not None else ''))
    return Value.list(out)


def _expand(repl, m, rx, original, pos):
    """SEL understands $0-$9 and $$ only. Spliced by hand so that $&, $` and
    backslash references stay literal.
    """
    out = []
    i = 0
    while i < len(repl):
        if repl[i] != '$':
            out.append(repl[i])
            i += 1
            continue
        nxt = repl[i + 1] if i + 1 < len(repl) else ''
        if nxt == '$':
            out.append('$')
            i += 2
            continue
        if nxt and '0' <= nxt <= '9':
            g = int(nxt)
            if g > rx.groups:
                fail('E_BAD_ARG',
                     f'replacement refers to ${g} but the pattern has '
                     f'{rx.groups} groups', pos)
            val = _group_text(m, g, original)
            out.append(val if val is not None else '')
            i += 2
            continue
        out.append('$')
        i += 1
    return ''.join(out)


def _rreplace(a, ctx):
    pattern = a.text(0)
    repl = a.text(1)
    subject = a.text(2)
    flags = a.text(3) if a.count() > 3 else ''
    flag_pos = a.pos_of(3) if a.count() > 3 else a.pos
    rx, ignore_case = _compile(pattern, flags, flag_pos, a.pos_of(0))
    haystack = _fold_subject(subject) if ignore_case else subject

    # Offsets from the folded subject, text from the original — the fold is
    # length-preserving, so the same offsets index both.
    out = []
    last = 0
    for m in _matches(rx, haystack):
        out.append(subject[last:m.start()])
        out.append(_expand(repl, m, rx, subject, a.pos_of(1)))
        last = m.end()
    out.append(subject[last:])
    return Value.text(''.join(out))


define('RMATCH', 2, 3, fn=_rmatch)
define('RFIND', 2, 3, fn=_rfind)
define('RGROUPS', 2, 3, fn=_rgroups)
define('RREPLACE', 3, 4, fn=_rreplace)
