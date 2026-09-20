// Exact decimal arithmetic on native BigInt magnitudes. See spec/SPEC.md §4.
//
// A decimal is { neg, digits, scale }, meaning (neg ? -1 : 1) * digits / 10^scale.
// `digits` is the unscaled magnitude as a native BigInt (0n for zero).
// Zero is never negative. Scale is part of the value: 2.50 is digits 250n at
// scale 2, and stays "2.50" through addition.
// Carrying native BigInt mantissas eliminates intermediate string conversions
// during arithmetic operations, matching Python's int and SBCL's bignum cores.

import { fail } from './errors.mjs';

export const DIV_SCALE = 10;

// spec/SPEC.md §6.4. These bound the *value*; ROUND's scale cap and POWER's
// exponent cap bound *arguments*, and an argument cap is not a value cap —
// POWER's base is unbounded, so nesting one POWER inside another multiplies the
// exponents and steps straight over the exponent cap. Two independent numbers
// rather than one shared budget, because ROUND(99.5, 1000000) is 1 000 002
// digits and legal under the scale cap: a shared budget would have shrunk what
// the spec already sanctions.
export const MAX_INT_DIGITS = 1000000;
export const MAX_FRAC_DIGITS = 1000000;

const _MAX_INT_BITS = 3321929;
const _INT_LIMIT_SHIFT = BigInt(_MAX_INT_BITS - 1);
const _FAST_BOUND = 10n ** 18n;

const POW10_TABLE = [1n];
for (let i = 1; i <= 64; i++) POW10_TABLE.push(POW10_TABLE[i - 1] * 10n);
const POW10_CACHE = new Map();
const POW10_CACHE_ENTRIES = 64;
const POW10_CACHE_MAX_EXPONENT = 1000000;
const POW10_CACHE_DIGITS = 1048576;
let pow10Weight = 0;

export function pow10(k) {
  if (k <= 64) return POW10_TABLE[k];
  let v = POW10_CACHE.get(k);
  if (!v) {
    v = 10n ** BigInt(k);
    if (k <= POW10_CACHE_MAX_EXPONENT) {
      if (POW10_CACHE.size >= POW10_CACHE_ENTRIES || pow10Weight + k > POW10_CACHE_DIGITS) {
        POW10_CACHE.clear();
        pow10Weight = 0;
      }
      POW10_CACHE.set(k, v);
      pow10Weight += k;
    }
  }
  return v;
}

function bitLength(n) {
  if (n === 0n) return 0;
  const hex = n.toString(16);
  return (hex.length - 1) * 4 + (32 - Math.clz32(parseInt(hex[0], 16)));
}

function numDigits(n) {
  if (n === 0n) return 1;
  const bitLen = bitLength(n);
  let d = Math.floor((bitLen * 30103) / 100000) + 1;
  while (n < pow10(d - 1)) d--;
  return d;
}

function make(neg, digits, scale) {
  const d = typeof digits === 'bigint' ? digits : BigInt(digits);
  const actualNeg = d === 0n ? false : !!neg;
  return { neg: actualNeg, digits: d, scale };
}

// Refuses a value SEL cannot hold, where it is built rather than where it is
// rendered. Every operation that can grow a number passes its result through
// here, so POWER — repeated squaring over mul — trips on an intermediate and the
// enormous value is never allocated: without that, nesting POWER three deep
// exhausted the host's memory before any check could run.
function guard(d, pos) {
  if (d.scale > MAX_FRAC_DIGITS) {
    fail('E_RANGE', `number has more than ${MAX_FRAC_DIGITS} fractional digits`, pos);
  }
  if (d.digits > _FAST_BOUND) {
    // A shift past the magnitude returns zero without rendering its digits.
    // Only values near the cap need the exact digit count (and its hex string).
    if ((d.digits >> _INT_LIMIT_SHIFT) !== 0n && numDigits(d.digits) - d.scale > MAX_INT_DIGITS) {
      fail('E_RANGE', `number has more than ${MAX_INT_DIGITS} integer digits`, pos);
    }
  }
  return d;
}

export const ZERO = make(false, 0n, 0);

const NUM_RE = /^-?[0-9]+(\.[0-9]+)?$/;

// Returns null when the text is not a number; callers raise E_NOT_NUM with the
// position of the offending node. No trimming — " 2" is not a number.
//
// A well-formed numeral too big to hold is E_RANGE, not null: every character of
// it is a digit, so "not a number" would be false. Callers that must not raise —
// ISNUM's probe — catch it and answer no.
export function parse(text, pos) {
  if (typeof text !== 'string' || !NUM_RE.test(text)) return null;
  const neg = text.charCodeAt(0) === 45;
  const body = neg ? text.slice(1) : text;
  const dot = body.indexOf('.');
  const intPart = dot < 0 ? body : body.slice(0, dot);
  const fracPart = dot < 0 ? '' : body.slice(dot + 1);
  const stripped = (intPart + fracPart).replace(/^0+/, '') || '0';
  if (fracPart.length > MAX_FRAC_DIGITS) {
    fail('E_RANGE', `number has more than ${MAX_FRAC_DIGITS} fractional digits`, pos);
  }
  if (stripped.length - fracPart.length > MAX_INT_DIGITS) {
    fail('E_RANGE', `number has more than ${MAX_INT_DIGITS} integer digits`, pos);
  }
  return make(neg, BigInt(stripped), fracPart.length);
}

export function format(d) {
  const sign = d.neg ? '-' : '';
  const digitsStr = d.digits.toString();
  if (d.scale === 0) return sign + digitsStr;
  const padded = digitsStr.length <= d.scale
    ? '0'.repeat(d.scale - digitsStr.length + 1) + digitsStr
    : digitsStr;
  return sign + padded.slice(0, padded.length - d.scale) + '.' + padded.slice(padded.length - d.scale);
}

export function fromInt(n) {
  const big = BigInt(n);
  const neg = big < 0n;
  return make(neg, neg ? -big : big, 0);
}

export function isZero(d) {
  return d.digits === 0n;
}

export function negate(d) {
  return make(!d.neg, d.digits, d.scale);
}

export function abs(d) {
  return make(false, d.digits, d.scale);
}

export function sign(d) {
  return d.digits === 0n ? 0 : (d.neg ? -1 : 1);
}

// True when the value has no fractional part left after its scale is honoured.
export function isInteger(d) {
  if (d.scale === 0) return true;
  return d.digits % pow10(d.scale) === 0n;
}

export function toSafeInt(d) {
  const t = trunc(d);
  const v = Number(t.digits);
  return t.neg ? -v : v;
}

// --- arithmetic -------------------------------------------------------------

function aligned(a, b) {
  const s = Math.max(a.scale, b.scale);
  const A = a.scale === s ? a.digits : a.digits * pow10(s - a.scale);
  const B = b.scale === s ? b.digits : b.digits * pow10(s - b.scale);
  return [A, B, s];
}

export function add(a, b, pos) {
  const [A, B, s] = aligned(a, b);
  // Only true addition can grow: a difference is never wider than its operands,
  // and the aligned scale is the larger of two already legal ones.
  if (a.neg === b.neg) return guard(make(a.neg, A + B, s), pos);
  if (A === B) return make(false, 0n, s);
  return A > B ? make(a.neg, A - B, s) : make(b.neg, B - A, s);
}

export function sub(a, b, pos) {
  return add(a, negate(b), pos);
}

export function mul(a, b, pos) {
  return guard(make(a.neg !== b.neg, a.digits * b.digits, a.scale + b.scale), pos);
}

export function cmp(a, b) {
  if (a.digits === 0n && b.digits === 0n) return 0;
  if (a.neg !== b.neg) return a.neg ? -1 : 1;
  const [A, B] = aligned(a, b);
  const c = A === B ? 0 : (A < B ? -1 : 1);
  return a.neg ? -c : c;
}

// Exact when the quotient terminates within DIV_SCALE fractional digits (and
// then reported at its minimal scale); otherwise rounded half away from zero to
// exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
export function div(a, b, pos) {
  if (b.digits === 0n) fail('E_DIV_ZERO', 'division by zero', pos);
  const N = a.digits * pow10(b.scale);
  const D = b.digits * pow10(a.scale);
  const num = N * 10000000000n; // pow10(DIV_SCALE)
  let q = num / D;
  const r = num % D;
  const neg = a.neg !== b.neg;
  if (r === 0n) {
    let digits = q;
    let scale = DIV_SCALE;
    while (scale > 0 && digits % 10n === 0n && digits !== 0n) {
      digits /= 10n;
      scale--;
    }
    if (digits === 0n) scale = 0;
    return guard(make(neg, digits, scale), pos);
  }
  if (2n * r >= D) q += 1n;
  return guard(make(neg, q, DIV_SCALE), pos);
}

// Remainder of truncated division: takes the sign of the dividend.
export function mod(a, b, pos) {
  if (b.digits === 0n) fail('E_DIV_ZERO', 'modulo by zero', pos);
  const [A, B, s] = aligned(a, b);
  return make(a.neg, A % B, s);
}

// --- rounding ---------------------------------------------------------------
//
// All of it half away from zero, on the magnitude, with the sign reattached by
// make(). Math.round is half-up (and floats besides) so it cannot appear here.

export function round(d, n, pos) {
  if (n >= d.scale) {
    return guard(make(d.neg, d.digits * pow10(n - d.scale), n), pos);
  }
  const p = pow10(d.scale - n);
  let q = d.digits / p;
  const r = d.digits % p;
  if (2n * r >= p) q += 1n;
  return guard(make(d.neg, q, n), pos);
}

export function trunc(d) {
  if (d.scale === 0) return d;
  return make(d.neg, d.digits / pow10(d.scale), 0);
}

export function floor(d) {
  if (d.scale === 0) return d;
  const p = pow10(d.scale);
  let q = d.digits / p;
  const r = d.digits % p;
  if (d.neg && r !== 0n) q += 1n;
  return make(d.neg, q, 0);
}

export function ceil(d) {
  if (d.scale === 0) return d;
  const p = pow10(d.scale);
  let q = d.digits / p;
  const r = d.digits % p;
  if (!d.neg && r !== 0n) q += 1n;
  return make(d.neg, q, 0);
}

// n must be a non-negative integer; the result scale is scale(x) * n, which
// falls out of repeated multiplication.
export function power(a, n, pos) {
  let result = make(false, 1n, 0);
  let base = a;
  // Arithmetic, not bit operators: JS's `&` and `>>` coerce to *32 bits*, so a
  // exponent above 2^31 silently wrapped and POWER(10, 4294967299) answered
  // 1000 with total confidence. Numbers are exact integers to 2^53 here.
  let e = n;
  while (e > 0) {
    if (e % 2 === 1) result = mul(result, base, pos);
    e = Math.floor(e / 2);
    if (e > 0) base = mul(base, base, pos);
  }
  return result;
}
