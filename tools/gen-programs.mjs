#!/usr/bin/env node
// Generates random SEL programs for the differential fuzzer.
//
// The generator is deliberately not "valid programs only" — roughly a third of
// what it emits fails at compile or run time, and agreement on the *error code
// and position* is as much a part of the invariant as agreement on values.
//
// Output is the corpus format described in tools/README.md — records separated
// by `### n` lines — so that every implementation can read it without a JSON
// parser, for the same reason conformance/ is line-oriented.
//
//   node tools/gen-programs.mjs [count] [seed] > corpus.selc

function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const count = Number(process.argv[2] || 3000);
if (!Number.isInteger(count) || count < 1) {
  process.stderr.write(`gen-programs: count must be a positive integer, got ${process.argv[2]}\n`);
  process.exit(2);
}
const seed = Number(process.argv[3] || 20260813);
const rnd = mulberry32(seed);

const pick = (xs) => xs[Math.floor(rnd() * xs.length)];
const chance = (p) => rnd() < p;
const int = (lo, hi) => lo + Math.floor(rnd() * (hi - lo + 1));

// Text with the awkward cases over-represented: astral pairs, combining marks,
// characters whose UTF-8 length differs from their UTF-16 length.
const TEXTS = [
  '""', '"a"', '"abc"', '"Zażółć"', '"👍"', '"a👍b"', '"é"', '"\\u{0661}"',
  '"\\u{00A0}"', '"  x  "', '"0"', '"5.00"', '"-3"', '" 2"', '"a,b,c"', '"41"',
  '"aGVsbG8="', '"31-874"', '"x\\ny"', '"A-1"',
];

const NUMS = [
  '0', '1', '2', '3', '7', '10', '255', '1000000',
  '0.1', '0.5', '2.5', '2.50', '1.005', '0.0001', '123.456',
  '007', '99999999999999999999', '12345678901234567890.12345',
];

// Patterns confined to the portable subset, plus a few that must be rejected.
const PATTERNS = [
  "'^\\d+$'", "'[a-c]+'", "'^\\w{2,4}$'", "'a|b'", "'^(?:ab)+$'", "'.'",
  "'^[\\d.]+$'", "'\\s'", "'^\\D+$'", "'x*'", "'<.+?>'", "'(a)(b)'",
  "'\\b'", "'(?=a)'", "'a++'", "'[[:alpha:]]'", "'\\p{L}'", "'[]]'",
];

const NUM_FN = ['ABS', 'SIGN', 'CEIL', 'FLOOR', 'TRUNC'];
const TXT_FN1 = ['LEN', 'UPPER', 'LOWER', 'TRIM', 'LTRIM', 'RTRIM', 'BACKWARDS', 'CODE'];
const BIN_FN1 = ['BLEN', 'TO_HEX', 'TO_UTF8', 'ENCODE_BASE64', 'CRC32', 'BTL'];
const ARITH = ['+', '-', '*', '/', '%'];
const NUMCMP = ['==', '!=', '<', '<=', '>', '>='];
const TXTCMP = ['$==', '$!=', '$<', '$<=', '$>', '$>='];
const VARS = ['A', 'B', 'LST', 'ITEMS'];

// Typed sub-generators. Used most of the time so that a good share of programs
// actually produce values — an all-errors corpus would only ever compare error
// codes, and the point is to compare arithmetic and text results too. The
// untyped `expr` still leaks in often enough to keep type errors well covered.
function numExpr(d) {
  if (d <= 0 || chance(0.35)) return chance(0.5) ? pick(NUMS) : `${int(-5, 20)}`;
  switch (int(0, 5)) {
    case 0: return `(${numExpr(d - 1)} ${pick(ARITH)} ${numExpr(d - 1)})`;
    case 1: return `${pick(NUM_FN)}(${numExpr(d - 1)})`;
    case 2: return `ROUND(${numExpr(d - 1)}, ${int(0, 4)})`;
    case 3: return `POWER(${numExpr(d - 1)}, ${int(0, 4)})`;
    case 4: return `${pick(['MIN', 'MAX'])}(${numExpr(d - 1)}, ${numExpr(d - 1)})`;
    default: return `-${numExpr(d - 1)}`;
  }
}

function txtExpr(d) {
  if (d <= 0 || chance(0.4)) return pick(TEXTS);
  switch (int(0, 5)) {
    case 0: return `(${txtExpr(d - 1)} & ${txtExpr(d - 1)})`;
    case 1: return `${pick(TXT_FN1.filter((f) => f !== 'LEN' && f !== 'CODE'))}(${txtExpr(d - 1)})`;
    case 2: return `LEFT(${txtExpr(d - 1)}, ${int(0, 5)})`;
    case 3: return `RIGHT(${txtExpr(d - 1)}, ${int(0, 5)})`;
    case 4: return `REPLACE("a", ${txtExpr(d - 1)}, ${txtExpr(d - 1)})`;
    default: return `PAD${pick(['L', 'R'])}(${txtExpr(d - 1)}, ${int(0, 6)}, "0")`;
  }
}

function expr(d) {
  if (d <= 0) return atom();
  switch (int(0, 16)) {
    case 0: return `(${numExpr(d - 1)} ${pick(ARITH)} ${chance(0.85) ? numExpr(d - 1) : expr(d - 1)})`;
    case 1: return `(${txtExpr(d - 1)} & ${chance(0.85) ? txtExpr(d - 1) : expr(d - 1)})`;
    case 2: return `(${numExpr(d - 1)} ${pick(NUMCMP)} ${numExpr(d - 1)})`;
    case 3: return `(${txtExpr(d - 1)} ${pick(TXTCMP)} ${txtExpr(d - 1)})`;
    case 4: return `(${bool(d - 1)} ${pick(['AND', 'OR', 'XOR'])} ${bool(d - 1)})`;
    case 5: return `NOT ${bool(d - 1)}`;
    case 6: return chance(0.5)
      ? `IF(${bool(d - 1)}, ${expr(d - 1)}, ${expr(d - 1)})`
      : `COND(${bool(d - 1)}, ${expr(d - 1)}, ${bool(d - 1)}, ${expr(d - 1)}${chance(0.15) ? '' : `, ${expr(d - 1)}`})`;
    case 7: return `(${expr(d - 1)}, ${expr(d - 1)}${chance(0.4) ? `, ${expr(d - 1)}` : ''})`;
    case 8: return `${pick(NUM_FN)}(${chance(0.8) ? numExpr(d - 1) : expr(d - 1)})`;
    case 9: return `${pick(TXT_FN1)}(${chance(0.8) ? txtExpr(d - 1) : expr(d - 1)})`;
    case 10: return `${pick(BIN_FN1)}(${chance(0.8) ? txtExpr(d - 1) : expr(d - 1)})`;
    case 11: return textFn(d - 1);
    case 12: return aggregate(d - 1);
    case 13: return regexCall(d - 1);
    case 14: return `${pick(VARS)}[${expr(d - 1)}]`;
    case 15: return chance(0.5) ? sideEffectingAssign(d - 1) : sizedCall(d - 1);
    default: return atom();
  }
}

function atom() {
  switch (int(0, 6)) {
    case 0: case 1: return pick(NUMS);
    case 2: case 3: return pick(TEXTS);
    case 4: return pick(['TRUE', 'FALSE']);
    case 5: return pick(VARS);
    default: return `${int(-5, 20)}`;
  }
}

function bool(d) {
  if (d <= 0 || chance(0.3)) return pick(['TRUE', 'FALSE']);
  return `(${expr(d - 1)} ${pick(NUMCMP.concat(TXTCMP, ['EQL', 'IN']))} ${expr(d - 1)})`;
}

function textFn(d) {
  switch (int(0, 9)) {
    case 0: return `LEFT(${txtExpr(d)}, ${int(0, 5)})`;
    case 1: return `RIGHT(${txtExpr(d)}, ${int(0, 5)})`;
    case 2: return `SUBSTR(${txtExpr(d)}, ${int(0, 4)}${chance(0.5) ? `, ${int(0, 4)}` : ''})`;
    case 3: return `FIND(${txtExpr(d)}, ${txtExpr(d)})`;
    case 4: return `REPLACE(${txtExpr(d)}, ${txtExpr(d)}, ${txtExpr(d)})`;
    case 5: return `SPLIT(${txtExpr(d)}, ${txtExpr(d)})`;
    case 6: return `PAD${pick(['L', 'R'])}(${txtExpr(d)}, ${int(0, 6)}, ${pick(['"0"', '"ab"', '""'])})`;
    case 7: return `ROUND(${numExpr(d)}, ${int(0, 4)})`;
    case 8: return `POWER(${numExpr(d)}, ${int(0, 4)})`;
    default: return `REPEAT(${txtExpr(d)}, ${int(0, 3)})`;
  }
}

function aggregate(d) {
  const list = chance(0.5) ? pick(VARS) : `(${numExpr(d)}, ${numExpr(d)})`;
  const named = chance(0.3);
  const name = named ? 'IT' : '_';
  const body = chance(0.5) ? `${name} ${pick(NUMCMP)} ${numExpr(d)}` : `${name}`;
  const head = named ? `${list}, IT, ` : `${list}, `;
  const fn = pick(['ALL', 'ANY', 'MAP', 'FILTER', 'SUM']);
  if (fn === 'MAP' || fn === 'FILTER' || fn === 'SUM') {
    const b = fn === 'FILTER' ? `${name} ${pick(NUMCMP)} ${numExpr(d)}` : body;
    return `${fn}(${head}${b})`;
  }
  return `${fn}(${head}${body})`;
}

function regexCall(d) {
  const p = pick(PATTERNS);
  const flags = chance(0.25) ? `, ${pick(['"i"', '"m"', '"x"'])}` : '';
  switch (int(0, 3)) {
    case 0: return `RMATCH(${p}, ${txtExpr(d)}${flags})`;
    case 1: return `RFIND(${p}, ${txtExpr(d)}${flags})`;
    case 2: return `RGROUPS(${p}, ${txtExpr(d)}${flags})`;
    default: return `RREPLACE(${p}, ${pick(['"-"', '"$0"', '"$1$2"', '"$$"', '"x$&y"'])}, ${txtExpr(d)})`;
  }
}

// Assignments whose *target* has side effects. The C++ port segfaulted on these
// — it held a pointer into a container across an evaluation that could grow it —
// and 20 000 generated programs never once produced the shape, because the
// generator only ever assigned to a constant index. Every bug the reviewers
// found in this class was invisible to the fuzzer until this existed.
function sideEffectingAssign(d) {
  // Includes index expressions and right-hand sides that replace the base
  // variable being indexed — the case spec/SPEC.md §5.7 settles, and the one
  // that crashed the C++ host.
  const idx = () => pick([
    `(Z = "1")`, `(A = 2)`, `COUNT(A)`, `(A = (9, 9))["1"]`, `(B = "k")`,
    `LEN(B & "x")`, `${int(0, 3)}`, `"k"`,
  ]);
  const rhs = () => pick([`(A = 2)`, `(B = 9)`, `(Z = "7")`, `${int(0, 9)}`,
                          expr(Math.max(0, d - 1))]);
  switch (int(0, 4)) {
    case 0: return `A[${idx()}] = ${rhs()}`;
    case 1: return `A[${idx()}][${idx()}] = ${rhs()}`;
    case 2: return `A[${idx()}] = ${rhs()}; A`;
    case 3: return `A = (1, 2); A[${idx()}] = ${rhs()}; A`;
    default: return `A[${idx()}] = 1; A[${idx()}] += ${int(1, 5)}; A`;
  }
}

// Size arguments at and around the documented caps (spec/SPEC.md §6.4), where
// the hosts previously failed in four different ways — including one silent
// wrong answer from a 32-bit shift.
function sizedCall(d) {
  // Deliberately built from *literals*, not from numExpr/txtExpr. Letting these
  // nest was a mistake worth recording: POWER over a POWER over a 20-digit
  // literal multiplies the digit count each time, and schoolbook multiplication
  // is quadratic in it, so a handful of such programs took the fuzz run from two
  // seconds to over seven minutes in all four hosts at once. The point of these
  // programs is which side of a documented cap an argument falls on, and a
  // literal tests that exactly as well.
  const over = () => pick(['1000001', '4294967296', '4294967299', '9223372036854775807']);
  const tiny = () => pick(['0', '1', '2']);
  const base = () => pick(['2', '10', '1.5', '0.1', '-3']);
  const text = () => pick(['"abc"', '"Zażółć"', '"a👍b"', '""']);
  switch (int(0, 5)) {
    case 0: return `ROUND(${base()}, ${chance(0.5) ? tiny() : over()})`;
    // POWER only ever gets a tiny exponent or one that is rejected outright;
    // an exponent at the cap is legal and would produce a 100 000-digit number.
    case 1: return `POWER(${base()}, ${chance(0.5) ? tiny() : over()})`;
    // Lengths and offsets clamp rather than allocate, so any magnitude is cheap.
    case 2: return `SUBSTR(${text()}, 2, ${chance(0.5) ? tiny() : over()})`;
    case 3: return `FIND("a", ${text()}, ${chance(0.5) ? tiny() : over()})`;
    case 4: return `LEFT(${text()}, ${over()})`;
    default: return `RMATCH('a{${pick(['2,1', '3,1', '65535', '65536', '99999999999', '2,4', '3'])}}', "aaa")`;
  }
}

// A setup prelude so variable references usually resolve, and sometimes do not.
function setup() {
  const parts = [];
  if (chance(0.85)) parts.push(`A = ${pick(NUMS.concat(TEXTS))}`);
  if (chance(0.7)) parts.push(`B = ${pick(NUMS.concat(TEXTS))}`);
  if (chance(0.7)) parts.push(`LST = (${pick(NUMS)}, ${pick(NUMS)}, ${pick(NUMS)})`);
  if (chance(0.6)) {
    parts.push('ITEMS[1]["QTY"] = 2; ITEMS[1]["PRICE"] = 3.50');
    parts.push('ITEMS[2]["QTY"] = 0; ITEMS[2]["PRICE"] = 1.25');
  }
  return parts.join('; ');
}

const out = [];
for (let i = 0; i < count; i++) {
  const pre = setup();
  const body = expr(int(1, 4));
  out.push(`### ${i + 1}\n${pre ? `${pre}; ${body}` : body}\n`);
}
process.stdout.write(out.join(''));
