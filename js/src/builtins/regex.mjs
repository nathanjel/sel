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
import { toCodePoints, fromCodePoints } from '../utf8.mjs';

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

// Validates and rewrites in one pass, returning source that means the same thing
// to both engines. Both hosts run this, so both compile the identical pattern.
export function validate(pattern, pos) {
  const p = toCodePoints(pattern, pos).map((c) => fromCodePoints([c]));
  const n = p.length;
  let out = '';
  let i = 0;

  while (i < n) {
    const c = p[i];

    if (c === '\\') {
      if (i + 1 >= n) bad('trailing backslash', pattern, i, pos);
      const e = p[i + 1];
      if (EXPAND_OUTSIDE[e] !== undefined) { out += EXPAND_OUTSIDE[e]; i += 2; continue; }
      if (CONTROL_ESCAPES.has(e) || SYNTAX_CHARS.has(e)) { out += c + e; i += 2; continue; }
      rejectEscape(e, pattern, i, pos);
    }

    if (c === '[') {
      const [text, next] = validateClass(p, i, pattern, pos);
      out += text;
      i = next;
      continue;
    }

    if (c === '(') {
      if (p[i + 1] === '?') {
        if (p[i + 2] === ':') { out += '(?:'; i += 3; continue; }
        const kind = p[i + 2] === '=' || p[i + 2] === '!' ? 'lookahead'
          : p[i + 2] === '<' ? 'lookbehind and named groups'
            : p[i + 2] === '>' ? 'atomic groups'
              : 'this group type';
        bad(`${kind} is not portable — only (?: ) is`, pattern, i, pos);
      }
      out += '(';
      i++;
      continue;
    }

    if (c === '{') {
      const end = afterQuantifier(p, validateBraces(p, i, pattern, pos), pattern, pos);
      out += p.slice(i, end).join('');
      i = end;
      continue;
    }
    if (c === '*' || c === '+' || c === '?') {
      const end = afterQuantifier(p, i + 1, pattern, pos);
      out += p.slice(i, end).join('');
      i = end;
      continue;
    }
    if (c === '}') bad('unmatched } — escape it as \\}', pattern, i, pos);
    if (c === ']') bad('unmatched ] — escape it as \\]', pattern, i, pos);

    out += c;
    i++;
  }
  return out;
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
const MAX_QUANTIFIER = 65535;   // PCRE2's own hard limit; above it PCRE refuses to compile

function validateBraces(p, start, pattern, pos) {
  let i = start + 1;
  const loStart = i;
  while (i < p.length && p[i] >= '0' && p[i] <= '9') i++;
  if (i === loStart) bad('{ must begin a quantifier such as {2,4} — escape it as \\{', pattern, start, pos);
  const lo = Number(p.slice(loStart, i).join(''));
  let hi = null;
  if (p[i] === ',') {
    i++;
    const hiStart = i;
    while (i < p.length && p[i] >= '0' && p[i] <= '9') i++;
    if (i > hiStart) hi = Number(p.slice(hiStart, i).join(''));
  }
  if (p[i] !== '}') bad('malformed quantifier', pattern, start, pos);
  if (lo > MAX_QUANTIFIER || (hi !== null && hi > MAX_QUANTIFIER)) {
    bad(`quantifier bound exceeds the maximum of ${MAX_QUANTIFIER}`, pattern, start, pos);
  }
  if (hi !== null && hi < lo) {
    bad(`quantifier {${lo},${hi}} is empty — the upper bound is below the lower one`,
      pattern, start, pos);
  }
  return i + 1;
}

// Returns [rewritten text, index just past the closing ']'].
function validateClass(p, start, pattern, pos) {
  let i = start + 1;
  let out = '[';
  if (p[i] === '^') { out += '^'; i++; }
  if (p[i] === '[' && p[i + 1] === ':') {
    bad('POSIX classes such as [[:alpha:]] are not portable', pattern, i, pos);
  }
  // `]` always closes the class. PCRE treats a leading `]` as a literal while
  // ECMAScript reads `[]` as an empty class, so neither spelling is portable —
  // write `\]` instead.
  let count = 0;
  while (i < p.length) {
    const c = p[i];
    if (c === ']') {
      if (count === 0) {
        bad('empty character class — write \\] for a literal bracket', pattern, start, pos);
      }
      return [out + ']', i + 1];
    }
    count++;
    if (c === '\\') {
      if (i + 1 >= p.length) bad('trailing backslash in character class', pattern, i, pos);
      const e = p[i + 1];
      if (EXPAND_INSIDE[e] !== undefined) { out += EXPAND_INSIDE[e]; i += 2; continue; }
      if (e === 'D' || e === 'W' || e === 'S') {
        bad(`\\${e} inside a character class cannot be expressed portably — `
          + 'negate the whole class instead', pattern, i, pos);
      }
      if (CONTROL_ESCAPES.has(e) || SYNTAX_CHARS.has(e) || e === '-') {
        out += c + e;
        i += 2;
        continue;
      }
      rejectEscape(e, pattern, i, pos);
    }
    out += c;
    i++;
  }
  bad('unterminated character class', pattern, start, pos);
}

// --- compilation ------------------------------------------------------------

const cache = new Map();

function compile(pattern, flags, pos, patPos) {
  let ignoreCase = false;
  for (const ch of flags) {
    const f = ch.toLowerCase();
    if (f === 'i') { ignoreCase = true; continue; }
    if (f === 'm' || f === 's') {
      fail('E_BAD_ARG',
        `flag ${JSON.stringify(ch)} is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject`,
        pos);
    }
    fail('E_BAD_ARG', `unknown regex flag ${JSON.stringify(ch)}`, pos);
  }

  if (ignoreCase) {
    for (const cp of toCodePoints(pattern, patPos)) {
      if (cp > 0x7f) {
        fail('E_BAD_ARG',
          'the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript',
          pos);
      }
    }
  }

  const key = (ignoreCase ? 'i ' : ' ') + pattern;
  let re = cache.get(key);
  if (!re) {
    const source = validate(pattern, patPos);
    try {
      re = new RegExp(source, ignoreCase ? 'usgi' : 'usg');
    } catch (e) {
      fail('E_REGEX_SYNTAX', `${e.message} in /${pattern}/`, patPos);
    }
    cache.set(key, re);
  }
  re.lastIndex = 0;
  return re;
}

// JS reports UTF-16 offsets; SEL reports code point offsets.
function cpIndex(str, utf16Index) {
  let count = 0;
  let i = 0;
  while (i < utf16Index) {
    const c = str.charCodeAt(i);
    i += (c >= 0xd800 && c <= 0xdbff && i + 1 < str.length) ? 2 : 1;
    count++;
  }
  return count;
}

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

function argsFor(args, patIndex, subjIndex, flagIndex) {
  const pattern = args.text(patIndex);
  const subject = args.text(subjIndex);
  const flags = args.count() > flagIndex ? args.text(flagIndex) : '';
  const flagPos = args.count() > flagIndex ? args.posOf(flagIndex) : args.pos;
  return { re: compile(pattern, flags, flagPos, args.posOf(patIndex)), subject };
}

define({
  name: 'RMATCH', min: 2, max: 3,
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    return Value.bool(re.test(subject));
  },
});

define({
  name: 'RFIND', min: 2, max: 3,
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    const m = re.exec(subject);
    return Value.int(m ? cpIndex(subject, m.index) + 1 : 0);
  },
});

define({
  name: 'RGROUPS', min: 2, max: 3,
  fn: (args) => {
    const { re, subject } = argsFor(args, 0, 1, 2);
    re.lastIndex = 0;
    const m = re.exec(subject);
    if (!m) return Value.none();
    const out = [];
    for (let i = 0; i < m.length; i++) out.push(Value.text(m[i] === undefined ? '' : m[i]));
    return Value.list(out);
  },
});

// Replacement is spliced by hand rather than handed to String.replace, whose
// $&, $` and $' have no PCRE equivalent. SEL understands $0-$9 and $$ only.
define({
  name: 'RREPLACE', min: 3, max: 4,
  fn: (args) => {
    const pattern = args.text(0);
    const repl = args.text(1);
    const subject = args.text(2);
    const flags = args.count() > 3 ? args.text(3) : '';
    const flagPos = args.count() > 3 ? args.posOf(3) : args.pos;
    const re = compile(pattern, flags, flagPos, args.posOf(0));

    let out = '';
    let last = 0;
    for (const m of matches(re, subject)) {
      out += subject.slice(last, m.index);
      out += expand(repl, m, args.posOf(1));
      last = m.index + m[0].length;
    }
    return Value.text(out + subject.slice(last));
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
