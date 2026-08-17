// Exact decimal arithmetic on digit strings. See spec/SPEC.md §4.
//
// Neither host has a usable exact numeric type — PHP has no bigint and BCMath is
// an optional extension, JS has doubles — so this is written from scratch and
// ported line for line. Everything here is deterministic and allocation-cheap
// enough for validation-sized numbers.
//
// A decimal is { neg, digits, scale }, meaning  (neg ? -1 : 1) * digits / 10^scale.
// `digits` is the unscaled integer as a string with no leading zeros ("0" for
// zero). Zero is never negative. Scale is part of the value: 2.50 is digits
// "250" at scale 2, and stays "2.50" through addition.

import { fail } from './errors.mjs';

export const DIV_SCALE = 10;

// --- digit-string primitives (non-negative, no leading zeros) ----------------

function strip(s) {
  let i = 0;
  while (i < s.length - 1 && s.charCodeAt(i) === 48) i++;
  return i === 0 ? s : s.slice(i);
}

function cmpAbs(a, b) {
  if (a.length !== b.length) return a.length < b.length ? -1 : 1;
  return a === b ? 0 : (a < b ? -1 : 1);
}

function addAbs(a, b) {
  const out = [];
  let i = a.length - 1, j = b.length - 1, carry = 0;
  while (i >= 0 || j >= 0 || carry) {
    const s = (i >= 0 ? a.charCodeAt(i--) - 48 : 0) + (j >= 0 ? b.charCodeAt(j--) - 48 : 0) + carry;
    out.push(s % 10);
    carry = s >= 10 ? 1 : 0;
  }
  return out.reverse().join('');
}

// Requires a >= b.
function subAbs(a, b) {
  const out = [];
  let i = a.length - 1, j = b.length - 1, borrow = 0;
  while (i >= 0) {
    let s = (a.charCodeAt(i--) - 48) - (j >= 0 ? b.charCodeAt(j--) - 48 : 0) - borrow;
    if (s < 0) { s += 10; borrow = 1; } else { borrow = 0; }
    out.push(s);
  }
  return strip(out.reverse().join(''));
}

function mulAbs(a, b) {
  if (a === '0' || b === '0') return '0';
  const n = a.length, m = b.length;
  const acc = new Array(n + m).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    const av = a.charCodeAt(i) - 48;
    if (av === 0) continue;
    let carry = 0;
    for (let j = m - 1; j >= 0; j--) {
      const t = acc[i + j + 1] + av * (b.charCodeAt(j) - 48) + carry;
      acc[i + j + 1] = t % 10;
      carry = (t - (t % 10)) / 10;
    }
    acc[i] += carry;
  }
  return strip(acc.join(''));
}

// Schoolbook long division. Trial digits by repeated subtraction — at most nine
// per output digit, which keeps it obviously correct and trivial to port.
function divModAbs(a, b) {
  if (b === '0') return null;
  if (cmpAbs(a, b) < 0) return [ '0', a ];
  const q = [];
  let r = '0';
  for (let i = 0; i < a.length; i++) {
    r = strip(r + a[i]);
    let k = 0;
    while (cmpAbs(r, b) >= 0) { r = subAbs(r, b); k++; }
    q.push(k);
  }
  return [ strip(q.join('')), r ];
}

function scaleUp(digits, k) {
  return k <= 0 ? digits : (digits === '0' ? '0' : digits + '0'.repeat(k));
}

const POW10 = (k) => (k === 0 ? '1' : '1' + '0'.repeat(k));

// --- construction -----------------------------------------------------------

function make(neg, digits, scale) {
  return { neg: digits === '0' ? false : neg, digits, scale };
}

export const ZERO = make(false, '0', 0);

const NUM_RE = /^-?[0-9]+(\.[0-9]+)?$/;

// Returns null when the text is not a number; callers raise E_NOT_NUM with the
// position of the offending node. No trimming — " 2" is not a number.
export function parse(text) {
  if (typeof text !== 'string' || !NUM_RE.test(text)) return null;
  const neg = text.charCodeAt(0) === 45;
  const body = neg ? text.slice(1) : text;
  const dot = body.indexOf('.');
  const intPart = dot < 0 ? body : body.slice(0, dot);
  const fracPart = dot < 0 ? '' : body.slice(dot + 1);
  return make(neg, strip(intPart + fracPart), fracPart.length);
}

export function format(d) {
  const sign = d.neg ? '-' : '';
  if (d.scale === 0) return sign + d.digits;
  const padded = d.digits.length <= d.scale
    ? '0'.repeat(d.scale - d.digits.length + 1) + d.digits
    : d.digits;
  return sign + padded.slice(0, padded.length - d.scale) + '.' + padded.slice(padded.length - d.scale);
}

export function fromInt(n) {
  const neg = n < 0;
  return make(neg, String(Math.abs(n)), 0);
}

export function isZero(d) { return d.digits === '0'; }
export function negate(d) { return make(!d.neg, d.digits, d.scale); }
export function abs(d) { return make(false, d.digits, d.scale); }
export function sign(d) { return isZero(d) ? 0 : (d.neg ? -1 : 1); }

// True when the value has no fractional part left after its scale is honoured.
export function isInteger(d) {
  if (d.scale === 0) return true;
  const [, r] = divModAbs(d.digits, POW10(d.scale));
  return r === '0';
}

export function toSafeInt(d) {
  const t = trunc(d);
  const v = Number(t.digits);
  return t.neg ? -v : v;
}

// --- arithmetic -------------------------------------------------------------

function aligned(a, b) {
  const s = Math.max(a.scale, b.scale);
  return [ scaleUp(a.digits, s - a.scale), scaleUp(b.digits, s - b.scale), s ];
}

export function add(a, b) {
  const [A, B, s] = aligned(a, b);
  if (a.neg === b.neg) return make(a.neg, addAbs(A, B), s);
  const c = cmpAbs(A, B);
  if (c === 0) return make(false, '0', s);
  return c > 0 ? make(a.neg, subAbs(A, B), s) : make(b.neg, subAbs(B, A), s);
}

export function sub(a, b) { return add(a, negate(b)); }

export function mul(a, b) {
  return make(a.neg !== b.neg, mulAbs(a.digits, b.digits), a.scale + b.scale);
}

export function cmp(a, b) {
  if (isZero(a) && isZero(b)) return 0;
  if (a.neg !== b.neg) return a.neg ? -1 : 1;
  const [A, B] = aligned(a, b);
  const c = cmpAbs(A, B);
  return a.neg ? -c : c;
}

// Exact when the quotient terminates within DIV_SCALE fractional digits (and
// then reported at its minimal scale); otherwise rounded half away from zero to
// exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
export function div(a, b, pos) {
  if (isZero(b)) fail('E_DIV_ZERO', 'division by zero', pos);
  const N = scaleUp(a.digits, b.scale);
  const D = scaleUp(b.digits, a.scale);
  const [q, r] = divModAbs(scaleUp(N, DIV_SCALE), D);
  const neg = a.neg !== b.neg;

  if (r === '0') {
    // Exact: drop trailing zeros to reach the minimal scale.
    let digits = q, scale = DIV_SCALE;
    while (scale > 0 && digits.length > 1 && digits.charCodeAt(digits.length - 1) === 48) {
      digits = digits.slice(0, -1);
      scale--;
    }
    if (digits === '0') scale = 0;
    return make(neg, digits, scale);
  }
  const up = cmpAbs(addAbs(r, r), D) >= 0 ? addAbs(q, '1') : q;
  return make(neg, up, DIV_SCALE);
}

// Remainder of truncated division: takes the sign of the dividend.
export function mod(a, b, pos) {
  if (isZero(b)) fail('E_DIV_ZERO', 'modulo by zero', pos);
  const [A, B, s] = aligned(a, b);
  const [, r] = divModAbs(A, B);
  return make(a.neg, r, s);
}

// --- rounding ---------------------------------------------------------------

export function round(d, n) {
  if (n >= d.scale) return make(d.neg, scaleUp(d.digits, n - d.scale), n);
  const k = d.scale - n;
  const p = POW10(k);
  const [q, r] = divModAbs(d.digits, p);
  const up = cmpAbs(addAbs(r, r), p) >= 0 ? addAbs(q, '1') : q;
  return make(d.neg, up, n);
}

export function trunc(d) {
  if (d.scale === 0) return d;
  const [q] = divModAbs(d.digits, POW10(d.scale));
  return make(d.neg, q, 0);
}

export function floor(d) {
  if (d.scale === 0) return d;
  const [q, r] = divModAbs(d.digits, POW10(d.scale));
  return make(d.neg, d.neg && r !== '0' ? addAbs(q, '1') : q, 0);
}

export function ceil(d) {
  if (d.scale === 0) return d;
  const [q, r] = divModAbs(d.digits, POW10(d.scale));
  return make(d.neg, !d.neg && r !== '0' ? addAbs(q, '1') : q, 0);
}

// n must be a non-negative integer; the result scale is scale(x) * n, which
// falls out of repeated multiplication.
export function power(a, n) {
  let result = make(false, '1', 0);
  let base = a;
  // Arithmetic, not bit operators: JS's `&` and `>>` coerce to *32 bits*, so a
  // exponent above 2^31 silently wrapped and POWER(10, 4294967299) answered
  // 1000 with total confidence. Numbers are exact integers to 2^53 here.
  let e = n;
  while (e > 0) {
    if (e % 2 === 1) result = mul(result, base);
    e = Math.floor(e / 2);
    if (e > 0) base = mul(base, base);
  }
  return result;
}
