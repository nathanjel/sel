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

from .._budget import check_text
from .._stack import recursion_budget
from .._limits import MAX_DEPTH, MAX_REGEX_GROUPS, MAX_REGEX_PATTERN, MAX_TEXT_LEN
from ..errors import fail
from ..registry import define
from ..value import Value
from . import _regex_ambiguity as _amb

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


class _Node:
    """One node of the pattern's tree. `validate()` builds it as it reads, so the
    structural rules below are checked on a real parse, and the static analyses
    that follow (the exponential-ambiguity rule, SPEC 7.8) have the tree to walk.

    kind      'set' (one code point from `ranges`, negated or not), 'anchor'
              (`^` `$`), 'cat', 'alt', 'rep' or 'group'
    nullable  can match the empty string
    must/may  the capture numbers that take part in EVERY match of the node /
              in SOME match of it
    """
    __slots__ = ('kind', 'kids', 'lo', 'hi', 'nullable', 'must', 'may', 'ranges', 'neg',
                 'pre')

    def __init__(self, kind, kids=(), lo=1, hi=1, nullable=False,
                 must=frozenset(), may=frozenset(), ranges=(), neg=False, pre=False):
        self.kind = kind
        self.kids = kids
        self.lo = lo
        self.hi = hi
        self.nullable = nullable
        self.must = must
        self.may = may
        self.ranges = ranges
        self.neg = neg
        # A negated escape (\D \W \S) is negated BEFORE the `i` fold; a negated
        # class ([^...]) is folded first and negated after (SPEC 7.8).
        self.pre = pre


_EMPTY = frozenset()
_ALL_RANGES = ((0, 0x10FFFF),)
_DIGIT = ((0x30, 0x39),)
_WORD = ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A))
_SPACE = ((0x09, 0x0D), (0x20, 0x20))     # \t \n \v \f \r and space, as expanded
_CLASS_ESCAPE_RANGES = {'d': _DIGIT, 'w': _WORD, 's': _SPACE}
_CONTROL = {'n': 10, 'r': 13, 't': 9, 'f': 12}


def _cat(items):
    if len(items) == 1:
        return items[0]
    must = _EMPTY
    may = _EMPTY
    for it in items:
        if it.may:
            may = may | it.may
            must = must | it.must
    return _Node('cat', items, nullable=all(it.nullable for it in items), must=must, may=may)


def _alt(branches):
    if len(branches) == 1:
        return branches[0]
    must = branches[0].must
    may = _EMPTY
    for b in branches:
        must = must & b.must
        may = may | b.may
    return _Node('alt', branches, nullable=any(b.nullable for b in branches),
                 must=must, may=may)


def validate(pattern, pos=None, ignore_case=False):
    """Validates and rewrites in one pass, returning source that means the same
    thing to every engine. Every host runs this, so every host compiles the
    identical pattern. It is also a real parser: the pattern's tree is built as
    it goes (`parse()` returns it) and the structural rules that need one — a
    quantified anchor, a loop over something that can match nothing, a capture
    that need not take part in every iteration, nesting depth, group count — are
    decided here (SPEC 7.8). Iterative: the depth it reads is data.
    """
    return parse(pattern, pos, ignore_case)[0]


def parse(pattern, pos=None, ignore_case=False):
    """Returns (rewritten source, tree)."""
    p = pattern
    n = len(p)
    # The `i` fold is analysed only for an ASCII pattern (SPEC 7.8): a non-ASCII
    # pattern under `i` is refused at run time (E_BAD_ARG) whatever else it holds,
    # so the analysis must not turn it into a compile-time refusal of its own.
    if ignore_case and any(ord(ch) > 0x7f for ch in p):
        ignore_case = False
    if n > MAX_REGEX_PATTERN:
        _bad(f'pattern is longer than {MAX_REGEX_PATTERN} code points', pattern, 0, pos)
    out = []
    i = 0
    groups = 0
    # One frame per open group: its finished branches, the items of the branch
    # being read, and its capture number (0 for a non-capturing group).
    stack = [([], [], 0, 0)]

    while i < n:
        c = p[i]
        cat = stack[-1][1]

        if c == '\\':
            if i + 1 >= n:
                _bad('trailing backslash', pattern, i, pos)
            e = p[i + 1]
            if e in EXPAND_OUTSIDE:
                out.append(EXPAND_OUTSIDE[e])
                cat.append(_Node('set', ranges=_CLASS_ESCAPE_RANGES[e.lower()], neg=e.isupper(),
                                 pre=True))
                i += 2
                continue
            if e in CONTROL_ESCAPES or e in SYNTAX_CHARS:
                out.append(c + e)
                cp = _CONTROL[e] if e in CONTROL_ESCAPES else ord(e)
                cat.append(_Node('set', ranges=((cp, cp),)))
                i += 2
                continue
            _reject_escape(e, pattern, i, pos)

        if c == '[':
            text, nxt, ranges, neg = _validate_class(p, i, pattern, pos)
            out.append(text)
            cat.append(_Node('set', ranges=ranges, neg=neg))
            i = nxt
            continue

        if c == '(':
            capture = 0
            if i + 1 < n and p[i + 1] == '?':
                nxt = p[i + 2] if i + 2 < n else ''
                if nxt == ':':
                    out.append('(?:')
                    i += 3
                else:
                    kind = ('lookahead' if nxt in ('=', '!')
                            else 'lookbehind and named groups' if nxt == '<'
                            else 'atomic groups' if nxt == '>'
                            else 'this group type')
                    _bad(f'{kind} is not portable — only (?: ) is', pattern, i, pos)
            elif i + 1 < n and p[i + 1] == '*':
                _bad('PCRE verbs such as (*FAIL) are not portable', pattern, i, pos)
            else:
                out.append('(')
                i += 1
                capture = groups + 1
            groups += 1
            if groups > MAX_REGEX_GROUPS:
                _bad(f'more than {MAX_REGEX_GROUPS} groups', pattern, i, pos)
            if len(stack) > MAX_DEPTH:
                _bad(f'groups nested deeper than {MAX_DEPTH}', pattern, i, pos)
            stack.append(([], [], capture, 0))
            continue

        if c == ')':
            if len(stack) == 1:
                _bad('unmatched ) — escape it as \\)', pattern, i, pos)
            branches, items, capture, _ = stack.pop()
            branches.append(_cat(tuple(items)) if items else _Node('cat', (), nullable=True))
            body = _alt(tuple(branches))
            must, may = body.must, body.may
            if capture:
                must = must | {capture}
                may = may | {capture}
            stack[-1][1].append(_Node('group', (body,), nullable=body.nullable,
                                      must=must, may=may))
            out.append(')')
            i += 1
            continue

        if c == '|':
            branches, items, _, _ = stack[-1]
            branches.append(_cat(tuple(items)) if items else _Node('cat', (), nullable=True))
            stack[-1] = (branches, [], stack[-1][2], 0)
            out.append('|')
            i += 1
            continue

        if c == '{' or c in ('*', '+', '?'):
            if c == '{':
                end, lo, hi = _validate_braces(p, i, pattern, pos)
            else:
                end, lo, hi = i + 1, (1 if c == '+' else 0), (1 if c == '?' else None)
            end = _after_quantifier(p, end, pattern, pos)
            if not cat:
                _bad('nothing to repeat', pattern, i, pos)
            last = cat[-1]
            if last.kind == 'anchor':
                _bad('an anchor cannot be quantified', pattern, i, pos)
            if last.kind == 'rep':
                _bad('multiple repeat', pattern, i, pos)
            if hi is None or hi > 1:
                # A loop. SPEC 7.8: the engines disagree about an empty iteration
                # and about a capture that skipped one, so neither is portable.
                if last.nullable:
                    _bad('a loop whose body can match the empty string is not portable',
                         pattern, i, pos)
                if last.may != last.must:
                    _bad('a capture inside a loop must take part in every iteration',
                         pattern, i, pos)
            cat[-1] = _Node('rep', (last,), lo=lo, hi=hi,
                            nullable=(lo == 0 or last.nullable),
                            must=(_EMPTY if lo == 0 else last.must), may=last.may)
            out.append(p[i:end])
            i = end
            continue
        if c == '}':
            _bad('unmatched } — escape it as \\}', pattern, i, pos)
        if c == ']':
            _bad('unmatched ] — escape it as \\]', pattern, i, pos)

        if c == '^' or c == '$':
            cat.append(_Node('anchor', nullable=True))
        elif c == '.':
            cat.append(_Node('set', ranges=_ALL_RANGES))
        else:
            cp = ord(c)
            cat.append(_Node('set', ranges=((cp, cp),)))
        out.append(c)
        i += 1

    if len(stack) > 1:
        _bad('missing )', pattern, n, pos)
    branches, items, _, _ = stack[0]
    branches.append(_cat(tuple(items)) if items else _Node('cat', (), nullable=True))
    tree = _alt(tuple(branches))
    _check_ambiguity(pattern, tree, ignore_case, pos)
    return ''.join(out), tree


# The analysis is a function of (pattern, flag) alone, so a pattern it has
# accepted is remembered (bounded, oldest evicted): a literal pattern is checked
# when the program compiles and again when it first runs.
_accepted = {}
_ACCEPTED_MAX = 256


def _check_ambiguity(pattern, tree, ignore_case, pos):
    """SPEC 7.8, 'Refused for its running time': exponential ambiguity. Runs on
    the tree `parse` just built, after every structural rule has passed."""
    key = (ignore_case, pattern)
    if key in _accepted:
        return
    try:
        with recursion_budget():
            _amb.analyse(_to_analysis(tree, ignore_case))
    except _amb.Reject as e:
        _bad(str(e), pattern, 0, pos)
    if len(_accepted) >= _ACCEPTED_MAX:
        _accepted.pop(next(iter(_accepted)))
    _accepted[key] = True


def _to_analysis(node, ic):
    """The parse tree in the analysis's own vocabulary (EPS LET CAT ALT GRP REP)."""
    k = node.kind
    if k == 'set':
        rs = _amb.norm(node.ranges)
        if node.neg and node.pre:
            rs = _amb.negate(rs)
        if ic:
            rs = _amb.fold(rs)
        if node.neg and not node.pre:
            rs = _amb.negate(rs)
        return _amb.N('LET', rs)
    if k == 'anchor':
        return _amb.N('EPS')
    if k == 'cat':
        if not node.kids:
            return _amb.N('EPS')
        return _amb.N('CAT', [_to_analysis(x, ic) for x in node.kids])
    if k == 'alt':
        return _amb.N('ALT', [_to_analysis(x, ic) for x in node.kids])
    if k == 'group':
        return _amb.N('GRP', _to_analysis(node.kids[0], ic))
    if k == 'rep':
        return _amb.N('REP', _to_analysis(node.kids[0], ic), node.lo, node.hi)
    raise AssertionError(k)


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
    Returns (index past the quantifier, lo, hi) with hi None when open-ended.
    """
    i = start + 1
    lo_start = i
    while i < len(p) and '0' <= p[i] <= '9':
        i += 1
    if i == lo_start:
        _bad('{ must begin a quantifier such as {2,4} — escape it as \\{',
             pattern, start, pos)
    lo = int(p[lo_start:i])
    hi = lo
    if i < len(p) and p[i] == ',':
        i += 1
        hi_start = i
        while i < len(p) and '0' <= p[i] <= '9':
            i += 1
        hi = int(p[hi_start:i]) if i > hi_start else None
    if i >= len(p) or p[i] != '}':
        _bad('malformed quantifier', pattern, start, pos)
    if lo > MAX_QUANTIFIER or (hi is not None and hi > MAX_QUANTIFIER):
        _bad(f'quantifier bound exceeds the maximum of {MAX_QUANTIFIER}',
             pattern, start, pos)
    if hi is not None and hi < lo:
        _bad(f'quantifier {{{lo},{hi}}} is empty — the upper bound is below the '
             'lower one', pattern, start, pos)
    return i + 1, lo, hi


def _class_item(p, i, pattern, pos):
    """One member of a class at p[i]: returns (source text, ranges, is_escape,
    next index). `is_escape` is true for \\d \\w \\s, which stand for a set and so
    cannot be the end of a range."""
    c = p[i]
    if c == '\\':
        if i + 1 >= len(p):
            _bad('trailing backslash in character class', pattern, i, pos)
        e = p[i + 1]
        if e in EXPAND_INSIDE:
            return EXPAND_INSIDE[e], _CLASS_ESCAPE_RANGES[e], True, i + 2
        if e in ('D', 'W', 'S'):
            _bad(f'\\{e} inside a character class cannot be expressed portably '
                 '— negate the whole class instead', pattern, i, pos)
        if e in CONTROL_ESCAPES or e in SYNTAX_CHARS or e == '-':
            cp = _CONTROL[e] if e in CONTROL_ESCAPES else ord(e)
            return c + e, ((cp, cp),), False, i + 2
        _reject_escape(e, pattern, i, pos)
    cp = ord(c)
    return c, ((cp, cp),), False, i + 1


def _validate_class(p, start, pattern, pos):
    """Returns (rewritten text, index just past the closing ']', ranges, negated)."""
    n = len(p)
    i = start + 1
    out = ['[']
    neg = False
    if i < n and p[i] == '^':
        out.append('^')
        neg = True
        i += 1
    ranges = []
    # `]` always closes the class. PCRE treats a leading `]` as a literal while
    # ECMAScript reads `[]` as an empty class, so neither spelling is portable.
    count = 0
    while i < n:
        c = p[i]
        if c == ']':
            if count == 0:
                _bad('empty character class — write \\] for a literal bracket',
                     pattern, start, pos)
            out.append(']')
            return ''.join(out), i + 1, tuple(ranges), neg
        count += 1
        # A `[` followed by `:`, `.` or `=` inside a class is refused, closed or not
        # (SPEC 7.8): the POSIX bracket forms are read differently by the engines,
        # and so is an unfinished one.
        if c == '[' and i + 1 < n and p[i + 1] in ':.=':
            _bad('POSIX bracket forms ([:x:], [.x.], [=x=]) are not portable',
                 pattern, i, pos)
        text, rs, is_escape, nxt = _class_item(p, i, pattern, pos)
        # A hyphen with something after it (other than the closing bracket) makes a
        # range of this member and the next. PCRE takes `[\\d-z]` as a literal
        # hyphen and ECMAScript refuses it, so a class escape at either end is out.
        if nxt + 1 < n and p[nxt] == '-' and p[nxt + 1] != ']':
            if is_escape:
                _bad('a class escape cannot start a range', pattern, i, pos)
            rtext, rrs, r_escape, after = _class_item(p, nxt + 1, pattern, pos)
            if r_escape:
                _bad('a class escape cannot end a range', pattern, nxt + 1, pos)
            if rrs[0][0] < rs[0][0]:
                _bad('character class range is reversed', pattern, i, pos)
            out.append(text + '-' + rtext)
            ranges.append((rs[0][0], rrs[0][0]))
            i = after
            continue
        out.append(text)
        ranges.extend(rs)
        i = nxt
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
            # `[`, `&&`, `||`, `~~` inside a set are reserved for future set
            # operations: Python warns (FutureWarning) that their meaning may
            # change. In SEL they are literals, so they are escaped here. This is
            # the only place the rewrite differs from the other hosts', because
            # the others' engines do not warn.
            elif c in '[&|~':
                out.append('\\')
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
    # Nothing to fold in an ASCII subject (the two code points are above it), so it is
    # returned as it is instead of being copied through translate().
    if subject.isascii():
        return subject
    return subject.translate(_FOLD)


def _group_text(m, i, original):
    """Group i, sliced from the ORIGINAL subject rather than the folded one."""
    start, end = m.span(i)
    if start < 0:
        return None
    return original[start:end]


_CACHE_MAX = 256                       # SPEC 7.8: bounded, oldest evicted first
_cache: dict[tuple, re.Pattern] = {}


def _flags(flags, pos):
    """`i` and nothing else (SPEC 7.8): not `I`, not U+0130 or U+212A, which
    str.lower() would have folded onto it."""
    ignore_case = False
    for ch in flags:
        if ch == 'i':
            ignore_case = True
            continue
        if ch in ('m', 's', 'M', 'S'):
            fail('E_BAD_ARG',
                 f'flag "{ch}" is not offered — SEL always matches . against any '
                 'character and anchors ^ $ to the whole subject', pos)
        fail('E_BAD_ARG', f'unknown regex flag "{ch}"', pos)
    return ignore_case


def _compile(pattern, flags, pos, pat_pos):
    # No flags is the common call: skip the flag scan altogether.
    ignore_case = _flags(flags, pos) if flags else False

    if ignore_case and not pattern.isascii():
        fail('E_BAD_ARG',
             'the i flag needs an ASCII-only pattern — case folding above '
             'ASCII differs between PCRE and ECMAScript', pos)

    key = (ignore_case, pattern)
    rx = _cache.get(key)
    if rx is None:
        source = _lower_anchors(validate(pattern, pat_pos, ignore_case))
        opts = re.DOTALL
        if ignore_case:
            opts |= re.IGNORECASE | re.ASCII
        try:
            rx = re.compile(source, opts)
        except re.error as e:
            fail('E_REGEX_SYNTAX', f'{e} in /{pattern}/', pat_pos)
        except (RecursionError, OverflowError, MemoryError):
            fail('E_REGEX_SYNTAX', f'the pattern is too large for the engine: /{pattern[:40]}/',
                 pat_pos)
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = rx
    return rx, ignore_case


def check_literal(name, args):
    """Compile-time check of a literal pattern (SPEC 7.8): a pattern that is a
    plain text literal is validated when the program is compiled, so a bad one is
    E_REGEX_SYNTAX even where it is never run. A pattern that is computed can only
    be checked when it runs. The flags narrow the check only when they are literal
    and contain an `i`; a flags argument that is computed, unknown or invalid is
    taken as no flags and left to the run-time E_BAD_ARG -- it never stops the
    pattern being checked, so a bad pattern beside a bad flag is the pattern's
    error, at compile time, on every host."""
    if not args or args[0].t != 'text':
        return
    flag_i = 3 if name == 'RREPLACE' else 2
    ignore_case = False
    if len(args) > flag_i:
        f = args[flag_i]
        ignore_case = f.t == 'text' and 'i' in f.v
    validate(args[0].v, args[0].pos, ignore_case)


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
    return Value._list_owned(out)


def _parse_replacement(repl):
    """The replacement, parsed ONCE per call: a tuple of literal strings and
    group numbers. SEL understands $0-$9 and $$ only; $&, $` and backslash
    references stay literal (it was re-scanned character by character for
    every match)."""
    parts = []
    lit = []
    i = 0
    n = len(repl)
    while i < n:
        c = repl[i]
        if c != '$':
            j = repl.find('$', i)
            if j < 0:
                j = n
            lit.append(repl[i:j])
            i = j
            continue
        nxt = repl[i + 1] if i + 1 < n else ''
        if nxt == '$':
            lit.append('$')
            i += 2
            continue
        if nxt and '0' <= nxt <= '9':
            if lit:
                parts.append(''.join(lit))
                lit = []
            parts.append(int(nxt))
            i += 2
            continue
        lit.append('$')
        i += 1
    if lit:
        parts.append(''.join(lit))
    return parts


def _rreplace(a, ctx):
    pattern = a.text(0)
    repl = a.text(1)
    subject = a.text(2)
    flags = a.text(3) if a.count() > 3 else ''
    flag_pos = a.pos_of(3) if a.count() > 3 else a.pos
    rx, ignore_case = _compile(pattern, flags, flag_pos, a.pos_of(0))
    haystack = _fold_subject(subject) if ignore_case else subject

    parts = _parse_replacement(repl)
    groups = rx.groups
    # A reference past the pattern's groups is an error only when a match occurs
    # (it always was: the old _expand raised while expanding the first match).
    bad = next((g for g in parts if type(g) is int and g > groups), None)
    constant = None
    if not any(type(p) is int for p in parts):
        constant = ''.join(parts)

    # The loop the other hosts run, inlined (was a generator plus a call per match):
    # a zero-width match advances a whole code point, so it cannot loop. Offsets
    # come from the folded subject, text from the original -- the fold is
    # length-preserving, so the same offsets index both.
    search = rx.search
    n = len(subject)
    out = []
    append = out.append
    last = 0
    total = 0
    pos = 0
    while pos <= n:
        m = search(haystack, pos)
        if m is None:
            break
        start, end = m.span()
        if start > last:
            append(subject[last:start])
        if bad is not None:
            fail('E_BAD_ARG',
                 f'replacement refers to ${bad} but the pattern has {groups} groups',
                 a.pos_of(1))
        if constant is not None:
            piece = constant
        else:
            pieces = []
            for p in parts:
                if type(p) is int:
                    st, en = m.span(p)
                    if st >= 0:
                        pieces.append(subject[st:en])
                else:
                    pieces.append(p)
            piece = ''.join(pieces)
        append(piece)
        # The result is measured as it grows and refused past the cap (SPEC 6.4).
        total += start - last + len(piece)
        if total + (n - end) > MAX_TEXT_LEN:
            check_text(total + (n - end), a.pos)
        last = end
        pos = end + 1 if end == start else end
    append(subject[last:])
    return Value.text(''.join(out))


define('RMATCH', 2, 3, fn=_rmatch)
define('RFIND', 2, 3, fn=_rfind)
define('RGROUPS', 2, 3, fn=_rgroups)
define('RREPLACE', 3, 4, fn=_rreplace)
