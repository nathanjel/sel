// The portable regex subset. See spec/SPEC.md §7.8.
//
// A pattern is validated against a whitelist before it reaches the host engine,
// so anything the two engines would disagree about fails loudly here instead of
// producing different answers on the backend and the frontend.
//
// Both hosts compile with `u` (code point matching, and \d \w \s stay ASCII in
// both) and with dotall permanently on. `m` and `s` are not offered: JS treats
// \r, U+2028 and U+2029 as line terminators and PCRE does not, so anything whose
// meaning depends on where a line ends cannot be made portable. With dotall
// always on, `.` means "any code point" in both, and ^ and $ anchor only to the
// ends of the subject.

import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { toCodePoints, fromCodePoints, cpIndex } from '../utf8.mjs';
import { MAX_DEPTH } from '../errors.mjs';
import { MAX_REGEX_PATTERN, MAX_REGEX_GROUPS, MAX_REGEX_QUANTIFIER } from '../_limits.mjs';
import { cpLength, checkText } from '../budget.mjs';
import { analyse, norm, negate, MAX_CP, Refuse } from './regex_ambiguity.mjs';

// \d, \w and \s are rewritten into explicit ASCII classes rather than passed
// through. PHP's `u` modifier turns on PCRE2's UCP, which makes \d match
// Arabic-Indic digits and \w match accented letters, while ECMAScript's `u`
// leaves both ASCII. Expanding them here makes the guarantee structural instead
// of dependent on a library flag that neither host lets us fully control.
const EXPAND_OUTSIDE = {
  d: '[0-9]', D: '[^0-9]',
  w: '[0-9A-Za-z_]', W: '[^0-9A-Za-z_]',
  s: '[ \\t\\n\\r\\f\\x0b]', S: '[^ \\t\\n\\r\\f\\x0b]',
};
const EXPAND_INSIDE = { d: '0-9', w: '0-9A-Za-z_', s: ' \\t\\n\\r\\f\\x0b' };

// \v is excluded: in PCRE it means "any vertical whitespace", in ECMAScript it
// means U+000B. Same spelling, different language.
const CONTROL_ESCAPES = new Set(['n', 'r', 't', 'f']);
// Exactly JS's u-mode identity escapes; PCRE accepts all of these too.
const SYNTAX_CHARS = new Set(['^', '$', '\\', '.', '*', '+', '?', '(', ')', '[', ']', '{', '}', '|', '/']);

function bad(message, pattern, at, pos) {
  fail('E_REGEX_SYNTAX', `${message} (at offset ${at} of /${pattern}/)`, pos);
}

function rejectEscape(e, pattern, at, pos) {
  if (e === 'b' || e === 'B') {
    bad(`\\${e} is not portable — word boundaries depend on the engine's idea of a word `
      + 'character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])',
    pattern, at, pos);
  }
  if (e === 'v') {
    bad('\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B',
      pattern, at, pos);
  }
  if (e >= '0' && e <= '9') bad('backreferences are not portable', pattern, at, pos);
  if (e === 'p' || e === 'P') bad('\\p{...} is not portable', pattern, at, pos);
  if (e === 'A' || e === 'z' || e === 'Z' || e === 'G' || e === 'K') {
    bad(`\\${e} is not portable — use ^ and $`, pattern, at, pos);
  }
  bad(`unsupported escape \\${e}`, pattern, at, pos);
}

// Validates and rewrites, returning source that means the same thing to both
// engines. Every host runs this, so every host compiles the identical pattern.
//
// It is a parser, not a scanner: the pattern becomes a tree (atoms, groups,
// repeats) and the rules that depend on structure -- a loop whose body can match
// nothing, a capture that may skip an iteration, the group nesting depth -- are
// checks on that tree (SPEC 7.8), and so is the last one, the refusal of a pattern
// a backtracking engine can be made to run in exponential time on
// (regex_ambiguity.mjs). `ignoreCase` is the `i` flag: it folds the sets that
// analysis reads, so callers pass it and caches are keyed by it.
export function validate(pattern, pos, ignoreCase = false) {
  if (cpLength(pattern) > MAX_REGEX_PATTERN) {
    bad(`pattern is longer than ${MAX_REGEX_PATTERN} code points`, pattern, MAX_REGEX_PATTERN, pos);
  }
  const p = toCodePoints(pattern, pos).map((c) => fromCodePoints([c]));
  // The `i` fold is analysed only for an ASCII pattern (SPEC 7.8): a non-ASCII
  // pattern under `i` is refused at run time (E_BAD_ARG) whatever else it holds, so
  // the analysis must not turn it into a compile-time refusal of its own.
  if (ignoreCase && p.some((c) => c.codePointAt(0) > 0x7f)) ignoreCase = false;
  const st = { p, n: p.length, i: 0, pattern, pos, groups: 0, ignoreCase };
  const top = parseAlternation(st, 0);
  if (st.i < st.n) bad('unmatched )', pattern, st.i, pos);
  checkTree(top, st);
  try {
    analyse(top, ignoreCase);
  } catch (e) {
    if (!(e instanceof Refuse)) throw e;
    bad(`this pattern can take exponential time to match (${e.message}) — `
      + 'write it so that no input can be matched in two different ways', pattern, 0, pos);
  }
  return emit(top);
}

const isLoop = (rep) => rep.hi > 1;

// alternation := sequence { '|' sequence }, ending at ')' or the end.
function parseAlternation(st, depth) {
  const alts = [parseSequence(st, depth)];
  while (st.i < st.n && st.p[st.i] === '|') {
    st.i++;
    alts.push(parseSequence(st, depth));
  }
  return { k: 'group', capture: false, alts, top: depth === 0 };
}

function parseSequence(st, depth) {
  const { p, n, pattern, pos } = st;
  const items = [];
  while (st.i < n && p[st.i] !== '|' && p[st.i] !== ')') {
    const c = p[st.i];
    let atom;

    if (c === '*' || c === '+' || c === '?') bad('nothing to repeat', pattern, st.i, pos);
    if (c === '{') {
      // A `{` that begins a quantifier has nothing before it here; one that does
      // not is an error of its own.
      validateBraces(p, st.i, pattern, pos);
      bad('nothing to repeat', pattern, st.i, pos);
    }

    if (c === '\\') {
      if (st.i + 1 >= n) bad('trailing backslash', pattern, st.i, pos);
      const e = p[st.i + 1];
      if (EXPAND_OUTSIDE[e] !== undefined) {
        const set = ESC_SET[e.toLowerCase()];
        atom = { k: 'atom', src: EXPAND_OUTSIDE[e], anchor: false,
          ranges: norm(e === e.toLowerCase() ? set : negate(set)) };
      } else if (CONTROL_ESCAPES.has(e) || SYNTAX_CHARS.has(e)) {
        const cp = CONTROL_CP[e] ?? e.codePointAt(0);
        atom = { k: 'atom', src: c + e, anchor: false, ranges: [cp, cp] };
      } else {
        rejectEscape(e, pattern, st.i, pos);
      }
      st.i += 2;
    } else if (c === '[') {
      const [text, next] = validateClass(p, st.i, pattern, pos);
      atom = { k: 'atom', src: text, anchor: false,
        ranges: classRanges(p, st.i, next, pattern, pos) };
      st.i = next;
    } else if (c === '(') {
      atom = parseGroup(st, depth);
    } else if (c === '}') {
      bad('unmatched } — escape it as \\}', pattern, st.i, pos);
    } else if (c === ']') {
      bad('unmatched ] — escape it as \\]', pattern, st.i, pos);
    } else {
      const anchor = c === '^' || c === '$';
      const cp = c === '.' ? -1 : c.codePointAt(0);
      atom = { k: 'atom', src: c, anchor, ranges: anchor ? [] : cp < 0 ? [0, MAX_CP] : [cp, cp] };
      st.i++;
    }

    // A quantifier binds to the atom just read.
    const q = p[st.i];
    if (q === '*' || q === '+' || q === '?' || q === '{') {
      const start = st.i;
      let lo, hi, end;
      if (q === '{') {
        [lo, hi, end] = validateBraces(p, start, pattern, pos);
      } else {
        lo = q === '+' ? 1 : 0;
        hi = q === '?' ? 1 : Infinity;
        end = start + 1;
      }
      if (atom.anchor) bad('a quantified anchor is not portable', pattern, start, pos);
      end = afterQuantifier(p, end, pattern, pos);
      atom = { k: 'rep', node: atom, lo, hi, src: p.slice(start, end).join('') };
      st.i = end;
      const r = p[st.i];
      if (r === '*' || r === '+' || r === '?' || r === '{') bad('nothing to repeat', pattern, st.i, pos);
    }
    items.push(atom);
  }
  return items;
}

function parseGroup(st, depth) {
  const { p, pattern, pos } = st;
  const at = st.i;
  let capture = true;
  if (p[st.i + 1] === '?') {
    if (p[st.i + 2] === ':') { capture = false; st.i += 3; } else {
      const kind = p[st.i + 2] === '=' || p[st.i + 2] === '!' ? 'lookahead'
        : p[st.i + 2] === '<' ? 'lookbehind and named groups'
          : p[st.i + 2] === '>' ? 'atomic groups'
            : 'this group type';
      bad(`${kind} is not portable — only (?: ) is`, pattern, at, pos);
    }
  } else if (p[st.i + 1] === '*') {
    bad('PCRE verbs such as (*FAIL) are not portable', pattern, at, pos);
  } else {
    st.i++;
  }
  if (depth + 1 > MAX_DEPTH) bad(`groups nest deeper than ${MAX_DEPTH}`, pattern, at, pos);
  if (++st.groups > MAX_REGEX_GROUPS) bad(`more than ${MAX_REGEX_GROUPS} groups`, pattern, at, pos);
  const inner = parseAlternation(st, depth + 1);
  if (st.p[st.i] !== ')') bad('unterminated group', pattern, at, pos);
  st.i++;
  return { k: 'group', capture, alts: inner.alts, top: false };
}

function nullable(node) {
  switch (node.k) {
    case 'atom': return node.anchor;
    case 'rep': return node.lo === 0 || nullable(node.node);
    default: return node.alts.some((seq) => seq.every(nullable));
  }
}

// The checks that need the tree (SPEC 7.8): a loop -- a quantifier whose maximum
// is above 1 -- must not have a body that can match the empty string, and must
// not hold a capture that can skip an iteration.
function checkTree(node, st) {
  const stack = [node];
  while (stack.length > 0) {
    const n = stack.pop();
    if (n.k === 'rep') {
      if (isLoop(n)) {
        if (nullable(n.node)) {
          bad('a loop whose body can match the empty string is not portable', st.pattern, 0, st.pos);
        }
        checkCaptures(n.node, false, st);
      }
      stack.push(n.node);
    } else if (n.k === 'group') {
      for (const seq of n.alts) for (const item of seq) stack.push(item);
    }
  }
}

function checkCaptures(node, optional, st) {
  if (node.k === 'rep') {
    checkCaptures(node.node, optional || node.lo === 0, st);
  } else if (node.k === 'group') {
    if (node.capture && optional) {
      bad('a capture inside a loop must take part in every iteration', st.pattern, 0, st.pos);
    }
    const inner = optional || node.alts.length > 1;
    for (const seq of node.alts) for (const item of seq) checkCaptures(item, inner, st);
  }
}

// A run of identical plain atoms is written as one counted atom: V8 refuses a
// pattern of tens of thousands of literals as "too large" although the language
// allows 65 535 code points, and `a{4}` means what `aaaa` does. Only atoms that
// carry no quantifier and are not anchors are folded.
const RUN_MIN = 4;
function emitSequence(seq) {
  let out = '';
  for (let i = 0; i < seq.length;) {
    const item = seq[i];
    if (item.k === 'atom' && !item.anchor) {
      let j = i + 1;
      while (j < seq.length && seq[j].k === 'atom' && seq[j].src === item.src) j++;
      if (j - i >= RUN_MIN) {
        out += `${item.src}{${j - i}}`;
        i = j;
        continue;
      }
    }
    out += emit(item);
    i++;
  }
  return out;
}

function emit(node) {
  switch (node.k) {
    case 'atom': return node.src;
    case 'rep': return emit(node.node) + node.src;
    default: {
      const body = node.alts.map(emitSequence).join('|');
      return node.top ? body : (node.capture ? '(' : '(?:') + body + ')';
    }
  }
}

// A quantifier may be followed by `?` (lazy). `+` would make it possessive,
// which PCRE supports and ECMAScript does not.
function afterQuantifier(p, i, pattern, pos) {
  if (p[i] === '+') bad('possessive quantifiers are not portable', pattern, i, pos);
  if (p[i] === '?') return i + 1;
  return i;
}

// spec/SPEC.md §6.4. Both bounds are checked here rather than left to the
// engine: PCRE2 and SRELL reject a huge repeat count as a syntax error while
// ECMAScript and cl-ppcre accept it and never match, and cl-ppcre also accepts
// the empty {2,1}.
// MAX_REGEX_QUANTIFIER (spec/limits.json) is PCRE2's own hard limit; above it
// PCRE refuses to compile.

// Returns [lo, hi, index just past the closing '}'].
function validateBraces(p, start, pattern, pos) {
  let i = start + 1;
  const loStart = i;
  while (i < p.length && p[i] >= '0' && p[i] <= '9') i++;
  if (i === loStart) bad('{ must begin a quantifier such as {2,4} — escape it as \\{', pattern, start, pos);
  const lo = Number(p.slice(loStart, i).join(''));
  let hi = lo;
  if (p[i] === ',') {
    i++;
    const hiStart = i;
    while (i < p.length && p[i] >= '0' && p[i] <= '9') i++;
    hi = i > hiStart ? Number(p.slice(hiStart, i).join('')) : Infinity;
  }
  if (p[i] !== '}') bad('malformed quantifier', pattern, start, pos);
  if (lo > MAX_REGEX_QUANTIFIER || (hi !== Infinity && hi > MAX_REGEX_QUANTIFIER)) {
    bad(`quantifier bound exceeds the maximum of ${MAX_REGEX_QUANTIFIER}`, pattern, start, pos);
  }
  if (hi < lo) {
    bad(`quantifier {${lo},${hi}} is empty — the upper bound is below the lower one`,
      pattern, start, pos);
  }
  return [lo, hi, i + 1];
}

// A `[` followed by `:`, `.` or `=` inside a class is refused, closed or not
// (SPEC 7.8): the POSIX bracket forms `[:alpha:]`, `[.x.]` and `[=x=]` are read
// differently by the engines, and so is an unfinished one, so no spelling of the
// prefix is portable.
function posixForm(p, i) {
  const k = p[i + 1];
  return k === ':' || k === '.' || k === '=';
}

// The sets \d \w \s stand for (SPEC 7.8), and the control escapes' code points:
// what the ambiguity rule reads (regex_ambiguity.mjs), not what the engine gets.
const ESC_SET = {
  d: [0x30, 0x39],
  w: [0x30, 0x39, 0x41, 0x5a, 0x5f, 0x5f, 0x61, 0x7a],
  s: [0x09, 0x0d, 0x20, 0x20],
};
const CONTROL_CP = { n: 10, r: 13, t: 9, f: 12 };

// [code point, next index] for a member, [null, next, set] for \d \w \s.
function classAtom(p, j) {
  const c = p[j];
  if (c === '\\') {
    const e = p[j + 1];
    if (ESC_SET[e] !== undefined) return [null, j + 2, ESC_SET[e]];
    if (CONTROL_CP[e] !== undefined) return [CONTROL_CP[e], j + 2];
    return [e.codePointAt(0), j + 2];
  }
  return [c.codePointAt(0), j + 1];
}

// The code points a validated class matches, as a sorted set of ranges. The class
// text ends at `end - 1` (its ']'), and has been validated, so the only thing left
// to refuse is a range that runs backwards.
function classRanges(p, start, end, pattern, pos) {
  let j = start + 1;
  let neg = false;
  if (p[j] === '^') { neg = true; j++; }
  const rs = [];
  const last = end - 1;
  while (j < last) {
    const [lo, next, set] = classAtom(p, j);
    j = next;
    if (lo === null) { rs.push(...set); continue; }
    if (p[j] === '-' && j + 1 < last) {
      const [hi, next2] = classAtom(p, j + 1);
      if (hi < lo) bad('a class range runs backwards', pattern, j, pos);
      rs.push(lo, hi);
      j = next2;
    } else {
      rs.push(lo, lo);
    }
  }
  // A negated class stays members plus a flag: under `i` the members are folded
  // BEFORE they are negated (SPEC 7.8), so the analysis takes the negation.
  return neg ? { neg: true, members: norm(rs) } : norm(rs);
}

// Returns [rewritten text, index just past the closing ']'].
function validateClass(p, start, pattern, pos) {
  let i = start + 1;
  let out = '[';
  if (p[i] === '^') { out += '^'; i++; }
  // `]` always closes the class. PCRE treats a leading `]` as a literal while
  // ECMAScript reads `[]` as an empty class, so neither spelling is portable —
  // write `\]` instead.
  let count = 0;
  // What the previous item was, for the hyphen rule: 'class' after \d \w \s,
  // 'char' after anything else, null at the start or after a range.
  let prev = null;
  while (i < p.length) {
    const c = p[i];
    if (c === ']') {
      if (count === 0) {
        bad('empty character class — write \\] for a literal bracket', pattern, start, pos);
      }
      return [out + ']', i + 1];
    }
    count++;
    if (c === '[' && posixForm(p, i)) {
      bad('POSIX bracket forms such as [[:alpha:]] are not portable', pattern, i, pos);
    }
    if (c === '-' && prev !== null && p[i + 1] !== undefined && p[i + 1] !== ']') {
      // A hyphen between two items is a range: a class escape cannot be an end.
      const next = p[i + 1] === '\\' ? p[i + 2] : null;
      if (prev === 'class' || (next !== null && EXPAND_INSIDE[next] !== undefined)) {
        bad('a class escape cannot be the end of a range — put the hyphen first or last',
          pattern, i, pos);
      }
      out += c;
      i++;
      prev = null;
      continue;
    }
    if (c === '\\') {
      if (i + 1 >= p.length) bad('trailing backslash in character class', pattern, i, pos);
      const e = p[i + 1];
      if (EXPAND_INSIDE[e] !== undefined) { out += EXPAND_INSIDE[e]; i += 2; prev = 'class'; continue; }
      if (e === 'D' || e === 'W' || e === 'S') {
        bad(`\\${e} inside a character class cannot be expressed portably — `
          + 'negate the whole class instead', pattern, i, pos);
      }
      if (CONTROL_ESCAPES.has(e) || SYNTAX_CHARS.has(e) || e === '-') {
        out += c + e;
        i += 2;
        prev = 'char';
        continue;
      }
      rejectEscape(e, pattern, i, pos);
    }
    out += c;
    i++;
    prev = 'char';
  }
  bad('unterminated character class', pattern, start, pos);
}

// --- compilation ------------------------------------------------------------

const cache = new Map();
const CACHE_MAX = 256;

// The last (pattern, flags) and its RegExp: a FILTER over rows calls with the same
// literal pair every time, and two string compares beat a flag loop, a key
// concatenation and a Map lookup. The entry is always one the cache holds or held.
let lastPattern = null, lastFlags = null, lastRe = null;
function compile(pattern, flags, pos, patPos) {
  if (pattern === lastPattern && flags === lastFlags) { lastRe.lastIndex = 0; return lastRe; }
  let ignoreCase = false;
  for (const ch of flags) {
    // Lowercase `i` only: toLowerCase would take U+0130 and U+212A for letters.
    if (ch === 'i') { ignoreCase = true; continue; }
    if (ch === 'm' || ch === 's' || ch === 'M' || ch === 'S') {
      fail('E_BAD_ARG',
        `flag ${JSON.stringify(ch)} is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject`,
        pos);
    }
    fail('E_BAD_ARG', `unknown regex flag ${JSON.stringify(ch)}`, pos);
  }

  // '\\0' as an escape, never the byte itself. Written literally the NUL made
  // this file `data` rather than text, and GNU grep silently skips a binary
  // file's contents -- so every `grep -r` across js/src missed this module
  // entirely, in a repo whose reviews are grep-driven.
  const key = (ignoreCase ? 'i\0' : '\0') + pattern;
  let re = cache.get(key);
  if (!re) {
    // Only a pattern that passed this check is ever cached, so a hit needs none:
    // the scan (and its error, raised on every call for a bad pattern, since a
    // refusal is never cached) lives on the miss path.
    if (ignoreCase) {
      for (const cp of toCodePoints(pattern, patPos)) {
        if (cp > 0x7f) {
          fail('E_BAD_ARG',
            'the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript',
            pos);
        }
      }
    }
    const source = validate(pattern, patPos, ignoreCase);
    try {
      re = new RegExp(source, ignoreCase ? 'usgi' : 'usg');
      // V8 compiles lazily and reports "too large" at the first exec, so run it
      // once here, where a refusal can still become a compile-time error.
      re.exec('');
    } catch (e) {
      // Any host refusal of a pattern the subset let through -- a program too
      // large, a stack too deep -- is the pattern's fault, not a host failure.
      fail('E_REGEX_SYNTAX', `${e.message} in /${pattern}/`, patPos);
    }
    // At most CACHE_MAX compiled patterns (SPEC 7.8), the oldest evicted first:
    // patterns come from data, and an unbounded cache is a leak nobody sees.
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
    cache.set(key, re);
  }
  re.lastIndex = 0;
  lastPattern = pattern; lastFlags = flags; lastRe = re;
  return re;
}

// JS reports UTF-16 offsets; SEL reports code point offsets.
function* matches(re, subject) {
  re.lastIndex = 0;
  for (;;) {
    const m = re.exec(subject);
    if (!m) return;
    yield m;
    if (m[0].length === 0) {
      // Advance a whole code point so a zero-width match cannot loop.
      const c = subject.charCodeAt(re.lastIndex);
      re.lastIndex += (c >= 0xd800 && c <= 0xdbff) ? 2 : 1;
      if (re.lastIndex > subject.length) return;
    }
  }
}

// The engine ran out of stack on a long subject: a resource limit of the host, so
// a SEL error at the call rather than a host exception, and never a wrong answer.
function guarded(args, run) {
  try {
    return run();
  } catch (e) {
    if (e instanceof RangeError) {
      fail('E_RANGE', 'the regex engine ran out of stack on this subject', args.pos);
    }
    if (e instanceof SyntaxError) {
      fail('E_REGEX_SYNTAX', 'the regex engine refused this pattern', args.pos);
    }
    throw e;
  }
}

function argsFor(args, patIndex, subjIndex, flagIndex) {
  const pattern = args.text(patIndex);
  const subject = args.text(subjIndex);
  const flags = args.count() > flagIndex ? args.text(flagIndex) : '';
  const flagPos = args.count() > flagIndex ? args.posOf(flagIndex) : args.pos;
  return { re: compile(pattern, flags, flagPos, args.posOf(patIndex)), subject };
}

// A literal pattern is checked when the program is compiled (SPEC 7.8): a
// rejected pattern in a branch that never runs is still an error. `flagIndex` is
// where the flags argument sits; a flags argument that is not a literal is taken
// as no flags, which changes no verdict (the flag only ever narrows what a later
// rule accepts, and the run-time check repeats it with the real flags).
function literalCheck(flagIndex) {
  return (args) => {
    const pat = args[0];
    if (!pat || pat.t !== 'text') return;
    const fl = args[flagIndex];
    const ic = !!(fl && fl.t === 'text' && fl.v.includes('i'));
    validate(pat.v, pat.pos, ic);
  };
}

define({
  name: 'RMATCH', min: 2, max: 3, compileCheck: literalCheck(2),
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    return guarded(args, () => Value.bool(re.test(subject)));
  },
});

define({
  name: 'RFIND', min: 2, max: 3, compileCheck: literalCheck(2),
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    return guarded(args, () => {
      const m = re.exec(subject);
      return Value.int(m ? cpIndex(subject, m.index) + 1 : 0);
    });
  },
});

define({
  name: 'RGROUPS', min: 2, max: 3, compileCheck: literalCheck(2),
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    return guarded(args, () => {
      const m = re.exec(subject);
      if (!m) return Value.none();
      const out = [];
      for (let i = 0; i < m.length; i++) out.push(Value.textOwned(m[i] === undefined ? '' : m[i]));
      return Value.listOwned(out);
    });
  },
});

// Replacement is spliced by hand rather than handed to String.replace, whose
// $&, $` and $' have no PCRE equivalent. SEL understands $0-$9 and $$ only.
define({
  name: 'RREPLACE', min: 3, max: 4, compileCheck: literalCheck(3),
  fn: (args) => {
    const pattern = args.text(0);
    const repl = args.text(1);
    const subject = args.text(2);
    const flags = args.count() > 3 ? args.text(3) : '';
    const flagPos = args.count() > 3 ? args.posOf(3) : args.pos;
    const re = compile(pattern, flags, flagPos, args.posOf(0));

    return guarded(args, () => {
      let out = '';
      let size = 0;   // code points built so far: the result is capped (SPEC 6.4)
      let last = 0;
      for (const m of matches(re, subject)) {
        const gap = subject.slice(last, m.index);
        const piece = expand(repl, m, args.posOf(1));
        size += cpLength(gap) + cpLength(piece);
        checkText(size, args.pos, 'RREPLACE result');
        out += gap + piece;
        last = m.index + m[0].length;
      }
      const tail = subject.slice(last);
      if (tail.length > 0) checkText(size + cpLength(tail), args.pos, 'RREPLACE result');
      return Value.textOwned(out + tail);
    });
  },
});

function expand(repl, m, pos) {
  let out = '';
  for (let i = 0; i < repl.length; i++) {
    if (repl[i] !== '$') { out += repl[i]; continue; }
    const next = repl[i + 1];
    if (next === '$') { out += '$'; i++; continue; }
    if (next >= '0' && next <= '9') {
      const g = Number(next);
      if (g >= m.length) {
        fail('E_BAD_ARG', `replacement refers to $${g} but the pattern has ${m.length - 1} groups`, pos);
      }
      out += m[g] === undefined ? '' : m[g];
      i++;
      continue;
    }
    out += '$';
  }
  return out;
}
