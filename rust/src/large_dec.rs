//! Unsigned big integers in binary, the magnitude of a `Dec` (whose sign and
//! decimal scale live in dec.rs): little-endian 64-bit words, no zero high
//! word, and zero has no words at all.
//!
//! Arithmetic stays binary; decimal digits exist only at the edges. A numeral is
//! read once (`from_decimal_parts`) and a value written once (`Display`), both
//! divide and conquer, so a million digits cost a few large multiplications
//! rather than a quadratic sweep. Between the edges -- products, sums,
//! comparisons, digit counts, scale alignment -- the words are never converted:
//! a digit count comes from the bit length (plus one comparison when a power of
//! ten falls inside that bit length), and 10^k is 5^k shifted left k bits. The
//! powers of five are cached, each with the reciprocal that makes dividing by it
//! two multiplications.
//!
//! Algorithms: schoolbook products below `KARATSUBA` words and Karatsuba above,
//! each with a squaring variant (a square needs about half the word products);
//! Knuth's algorithm D when the divisor or the quotient is short, and Barrett
//! reduction with a Newton reciprocal when both are long.
use std::cell::{OnceCell, RefCell};
use std::cmp::Ordering;
use std::collections::HashMap;
use std::rc::Rc;
use std::{fmt, str::FromStr};

// Thresholds, in 64-bit words. The tests cross every one of them.
const KARATSUBA: usize = 32;
const KARATSUBA_SQR: usize = 48;
/// Divisor and quotient both at least this long: Barrett reduction, when the
/// divisor's reciprocal is cached (a power of five) ...
const BARRETT_CACHED: usize = 160;
/// ... or has to be computed for this one division (tune_division_thresholds).
const BARRETT_FRESH: usize = 2560;
/// Newton's recursion for a reciprocal ends in long division below this.
const RECIP_BASE: usize = 32;
/// Decimal conversion below this many words (digits) is word by word.
const DEC_LEAF_WORDS: usize = 12;
const DEC_LEAF_DIGITS: usize = 19 * DEC_LEAF_WORDS;

/// 10^19, the largest power of ten in a word.
const TEN19: u64 = 10_000_000_000_000_000_000;
/// 5^27, the largest power of five in a word.
const FIVE27: u64 = 7_450_580_596_923_828_125;
/// floor(log10(2) * 2^64).
const LOG10_2_Q64: u128 = 5_553_023_288_523_357_132;
/// Cached powers of five, in words, before the cache starts over.
const POW5_CACHE_WORDS: usize = 1 << 21;

// ---- words -----------------------------------------------------------------

#[inline]
fn trimmed(a: &[u64]) -> &[u64] {
    let mut n = a.len();
    while n > 0 && a[n - 1] == 0 {
        n -= 1;
    }
    &a[..n]
}

#[inline]
fn normalize(v: &mut Vec<u64>) {
    while v.last() == Some(&0) {
        v.pop();
    }
}

/// Orders two trimmed magnitudes.
fn cmp_nat(a: &[u64], b: &[u64]) -> Ordering {
    a.len().cmp(&b.len()).then_with(|| a.iter().rev().cmp(b.iter().rev()))
}

/// acc += b, acc at least as long as b; whether a carry left acc.
fn add_in(acc: &mut [u64], b: &[u64]) -> bool {
    let mut carry = 0u64;
    for (x, &y) in acc.iter_mut().zip(b) {
        let t = *x as u128 + y as u128 + carry as u128;
        *x = t as u64;
        carry = (t >> 64) as u64;
    }
    if carry != 0 {
        for x in &mut acc[b.len()..] {
            let (s, c) = x.overflowing_add(1);
            *x = s;
            if !c {
                return false;
            }
        }
        return true;
    }
    false
}

/// acc -= b, acc at least as long as b; whether a borrow left acc.
fn sub_in(acc: &mut [u64], b: &[u64]) -> bool {
    let mut borrow = 0u64;
    for (x, &y) in acc.iter_mut().zip(b) {
        // Wrapping: a borrow leaves the high half all ones.
        let t = (*x as u128).wrapping_sub(y as u128).wrapping_sub(borrow as u128);
        *x = t as u64;
        borrow = (t >> 127) as u64;
    }
    if borrow != 0 {
        for x in &mut acc[b.len()..] {
            let (d, b) = x.overflowing_sub(1);
            *x = d;
            if !b {
                return false;
            }
        }
        return true;
    }
    false
}

fn add_nat(a: &[u64], b: &[u64]) -> Vec<u64> {
    let (long, short) = if a.len() >= b.len() { (a, b) } else { (b, a) };
    let mut out = Vec::with_capacity(long.len() + 1);
    out.extend_from_slice(long);
    if add_in(&mut out, short) {
        out.push(1);
    }
    out
}

/// a - b, for a >= b.
fn sub_nat(a: &[u64], b: &[u64]) -> Vec<u64> {
    let mut out = a.to_vec();
    let borrow = sub_in(&mut out, b);
    assert!(!borrow, "unsigned mantissa subtraction underflow");
    normalize(&mut out);
    out
}

/// acc[..a.len()] += a * m; the word carried out.
#[inline]
fn addmul_1(acc: &mut [u64], a: &[u64], m: u64) -> u64 {
    let mut carry = 0u64;
    for (x, &y) in acc.iter_mut().zip(a) {
        let t = y as u128 * m as u128 + *x as u128 + carry as u128;
        *x = t as u64;
        carry = (t >> 64) as u64;
    }
    carry
}

/// acc[..a.len()] -= a * m; the word borrowed out.
#[inline]
fn submul_1(acc: &mut [u64], a: &[u64], m: u64) -> u64 {
    let mut borrow = 0u64;
    for (x, &y) in acc.iter_mut().zip(a) {
        let t = y as u128 * m as u128 + borrow as u128;
        let (d, under) = x.overflowing_sub(t as u64);
        *x = d;
        borrow = (t >> 64) as u64 + under as u64;
    }
    borrow
}

/// a *= m; the word carried out.
fn mul_1(a: &mut [u64], m: u64) -> u64 {
    let mut carry = 0u64;
    for x in a.iter_mut() {
        let t = *x as u128 * m as u128 + carry as u128;
        *x = t as u64;
        carry = (t >> 64) as u64;
    }
    carry
}

/// a /= d (d > 0); the remainder.
fn div_1(a: &mut [u64], d: u64) -> u64 {
    let mut r = 0u64;
    for x in a.iter_mut().rev() {
        let t = (r as u128) << 64 | *x as u128;
        *x = (t / d as u128) as u64;
        r = (t % d as u128) as u64;
    }
    r
}

/// a mod d (d > 0).
fn rem_1(a: &[u64], d: u64) -> u64 {
    let mut r = 0u64;
    for &x in a.iter().rev() {
        r = (((r as u128) << 64 | x as u128) % d as u128) as u64;
    }
    r
}

/// a << bits for bits < 64: one word longer, not trimmed.
fn shl_bits(a: &[u64], bits: u32) -> Vec<u64> {
    let mut out = Vec::with_capacity(a.len() + 1);
    if bits == 0 {
        out.extend_from_slice(a);
        out.push(0);
        return out;
    }
    let mut carry = 0u64;
    for &x in a {
        out.push(x << bits | carry);
        carry = x >> (64 - bits);
    }
    out.push(carry);
    out
}

/// a >> bits for bits < 64, trimmed.
fn shr_bits(a: &[u64], bits: u32) -> Vec<u64> {
    let mut out = a.to_vec();
    if bits != 0 {
        for i in 0..out.len() {
            let high = if i + 1 < a.len() { a[i + 1] << (64 - bits) } else { 0 };
            out[i] = a[i] >> bits | high;
        }
    }
    normalize(&mut out);
    out
}

/// a << k, trimmed.
fn shl_nat(a: &[u64], k: usize) -> Vec<u64> {
    if a.is_empty() {
        return Vec::new();
    }
    let mut out = vec![0u64; k / 64];
    out.extend(shl_bits(a, (k % 64) as u32));
    normalize(&mut out);
    out
}

/// a >> k, trimmed.
fn shr_nat(a: &[u64], k: usize) -> Vec<u64> {
    if k / 64 >= a.len() {
        return Vec::new();
    }
    shr_bits(&a[k / 64..], (k % 64) as u32)
}

/// a mod 2^k, trimmed.
fn low_bits(a: &[u64], k: usize) -> Vec<u64> {
    let (words, bits) = (k / 64, k % 64);
    let mut out: Vec<u64> = a.iter().take(words + 1).copied().collect();
    if out.len() > words {
        if bits == 0 {
            out.truncate(words);
        } else {
            out[words] &= (1u64 << bits) - 1;
        }
    }
    normalize(&mut out);
    out
}

fn bit_len(a: &[u64]) -> usize {
    a.last().map_or(0, |&w| a.len() * 64 - w.leading_zeros() as usize)
}

/// For a nonzero a.
fn trailing_zero_bits(a: &[u64]) -> usize {
    let i = a.iter().position(|&w| w != 0).expect("nonzero");
    i * 64 + a[i].trailing_zeros() as usize
}

// ---- multiplication --------------------------------------------------------

thread_local! {
    // Karatsuba's temporaries, reused from product to product.
    static SCRATCH: RefCell<Vec<u64>> = const { RefCell::new(Vec::new()) };
}

/// Words of scratch a product of `n` result words may need: each Karatsuba
/// level holds under 2.5n words and hands its children at most 2n/3.
fn scratch_len(n: usize) -> usize {
    8 * n + 512
}

fn with_scratch<R>(len: usize, f: impl FnOnce(&mut [u64]) -> R) -> R {
    // Taken out of the cell, so a nested product (none today) would simply
    // allocate its own. Every user zeroes the part it uses.
    let mut buf = SCRATCH.with(|s| std::mem::take(&mut *s.borrow_mut()));
    if buf.len() < len {
        buf.resize(len, 0);
    }
    let r = f(&mut buf[..len]);
    SCRATCH.with(|s| {
        let mut s = s.borrow_mut();
        if s.len() < buf.len() {
            *s = buf;
        }
    });
    r
}

/// out = a * b by rows; out zeroed and at least a.len() + b.len() long.
fn basecase_mul(out: &mut [u64], a: &[u64], b: &[u64]) {
    for (j, &m) in b.iter().enumerate() {
        if m != 0 {
            out[j + a.len()] = addmul_1(&mut out[j..], a, m);
        }
    }
}

/// out = a^2; out zeroed and at least 2 a.len() long. Each cross product once,
/// doubled, then the squares of the words on the diagonal.
fn basecase_sqr(out: &mut [u64], a: &[u64]) {
    let n = a.len();
    for i in 0..n.saturating_sub(1) {
        out[i + n] = addmul_1(&mut out[2 * i + 1..], &a[i + 1..], a[i]);
    }
    let mut top = 0u64;
    for x in out[..2 * n].iter_mut() {
        let doubled = *x << 1 | top;
        top = *x >> 63;
        *x = doubled;
    }
    debug_assert_eq!(top, 0);
    let mut carry = false;
    for (i, &w) in a.iter().enumerate() {
        let sq = w as u128 * w as u128;
        let (s, c1) = out[2 * i].overflowing_add(sq as u64);
        let (s, c2) = s.overflowing_add(carry as u64);
        out[2 * i] = s;
        let (t, c3) = out[2 * i + 1].overflowing_add((sq >> 64) as u64);
        let (t, c4) = t.overflowing_add((c1 || c2) as u64);
        out[2 * i + 1] = t;
        carry = c3 || c4;
    }
    debug_assert!(!carry);
}

/// d = |a - b| in d.len() == a.len() >= b.len() words; how a compares to b.
fn abs_diff_into(d: &mut [u64], a: &[u64], b: &[u64]) -> Ordering {
    let (a, b) = (trimmed(a), trimmed(b));
    let ord = cmp_nat(a, b);
    d.fill(0);
    let (big, small) = match ord {
        Ordering::Equal => return ord,
        Ordering::Greater => (a, b),
        Ordering::Less => (b, a),
    };
    d[..big.len()].copy_from_slice(big);
    sub_in(d, small);
    ord
}

/// out = a * b. out is zeroed and one word longer than the product (Karatsuba's
/// intermediate sums use it and leave it zero); `scratch` holds the rest.
fn mul_into(out: &mut [u64], a: &[u64], b: &[u64], scratch: &mut [u64]) {
    let (a, b) = if a.len() >= b.len() { (a, b) } else { (b, a) };
    if b.is_empty() {
        return;
    }
    if b.len() < KARATSUBA {
        basecase_mul(out, a, b);
    } else if a.len() >= 2 * b.len() {
        // Unbalanced: the long factor in pieces as long as the short one.
        let n = b.len();
        let (tmp, rest) = scratch.split_at_mut(2 * n + 1);
        for (k, piece) in a.chunks(n).enumerate() {
            let piece = trimmed(piece);
            if piece.is_empty() {
                continue;
            }
            let t = &mut tmp[..piece.len() + n + 1];
            t.fill(0);
            mul_into(t, piece, b, rest);
            let carry = add_in(&mut out[k * n..], trimmed(t));
            debug_assert!(!carry);
        }
    } else {
        karatsuba(out, a, b, scratch);
    }
}

/// x * y for y.len() <= x.len() < 2 y.len(): with x = x0 + x1 B^h and
/// y = y0 + y1 B^h, xy = x0 y0 (1 + B^h) + x1 y1 (B^h + B^2h) - (x1 - x0)(y1 - y0) B^h.
fn karatsuba(out: &mut [u64], x: &[u64], y: &[u64], scratch: &mut [u64]) {
    let h = y.len() / 2;
    let (x0, x1) = x.split_at(h);
    let (y0, y1) = y.split_at(h);
    let (x0, y0) = (trimmed(x0), trimmed(y0));
    let (t0, rest) = scratch.split_at_mut(2 * h + 1);
    let (t2, rest) = rest.split_at_mut(x1.len() + y1.len() + 1);
    t0.fill(0);
    t2.fill(0);
    mul_into(t0, x0, y0, rest);
    mul_into(t2, x1, y1, rest);
    let (t0, t2) = (trimmed(t0), trimmed(t2));
    out[..t0.len()].copy_from_slice(t0);
    out[2 * h..2 * h + t2.len()].copy_from_slice(t2);
    add_in(&mut out[h..], t0);
    add_in(&mut out[h..], t2);
    let (dx, rest) = rest.split_at_mut(x1.len());
    let (dy, rest) = rest.split_at_mut(y1.len());
    let sx = abs_diff_into(dx, x1, x0);
    let sy = abs_diff_into(dy, y1, y0);
    if sx == Ordering::Equal || sy == Ordering::Equal {
        return;
    }
    let (dx, dy) = (trimmed(dx), trimmed(dy));
    let (p, rest) = rest.split_at_mut(dx.len() + dy.len() + 1);
    p.fill(0);
    mul_into(p, dx, dy, rest);
    let p = trimmed(p);
    if sx == sy {
        let borrow = sub_in(&mut out[h..], p);
        debug_assert!(!borrow);
    } else {
        let carry = add_in(&mut out[h..], p);
        debug_assert!(!carry);
    }
}

/// out = a^2, out zeroed and 2 a.len() + 1 long: Karatsuba's squaring,
/// a^2 = a0^2 (1 + B^h) + a1^2 (B^h + B^2h) - (a1 - a0)^2 B^h.
fn sqr_into(out: &mut [u64], a: &[u64], scratch: &mut [u64]) {
    if a.len() < KARATSUBA_SQR {
        basecase_sqr(out, a);
        return;
    }
    let h = a.len() / 2;
    let (a0, a1) = a.split_at(h);
    let a0 = trimmed(a0);
    let (t0, rest) = scratch.split_at_mut(2 * h + 1);
    let (t2, rest) = rest.split_at_mut(2 * a1.len() + 1);
    t0.fill(0);
    t2.fill(0);
    sqr_into(t0, a0, rest);
    sqr_into(t2, a1, rest);
    let (t0, t2) = (trimmed(t0), trimmed(t2));
    out[..t0.len()].copy_from_slice(t0);
    out[2 * h..2 * h + t2.len()].copy_from_slice(t2);
    add_in(&mut out[h..], t0);
    add_in(&mut out[h..], t2);
    let (d, rest) = rest.split_at_mut(a1.len());
    if abs_diff_into(d, a1, a0) == Ordering::Equal {
        return;
    }
    let d = trimmed(d);
    let (p, rest) = rest.split_at_mut(2 * d.len() + 1);
    p.fill(0);
    sqr_into(p, d, rest);
    let borrow = sub_in(&mut out[h..], trimmed(p));
    debug_assert!(!borrow);
}

fn mul_nat(a: &[u64], b: &[u64]) -> Vec<u64> {
    let (a, b) = (trimmed(a), trimmed(b));
    if a.is_empty() || b.is_empty() {
        return Vec::new();
    }
    // A square costs about half a product: MUL of a value by an equal one
    // (zr * zr, reading the same variable twice) takes that road. Both reads
    // share one mantissa (item 1), so the pointer decides before the words.
    if a.len() == b.len() && (std::ptr::eq(a.as_ptr(), b.as_ptr()) || a == b) {
        return sqr_nat(a);
    }
    if a.len() == 1 || b.len() == 1 {
        let (long, m) = if b.len() == 1 { (a, b[0]) } else { (b, a[0]) };
        let mut out = long.to_vec();
        let carry = mul_1(&mut out, m);
        if carry != 0 {
            out.push(carry);
        }
        return out;
    }
    let n = a.len() + b.len() + 1;
    let mut out = vec![0u64; n];
    if a.len().min(b.len()) < KARATSUBA {
        let (long, short) = if a.len() >= b.len() { (a, b) } else { (b, a) };
        basecase_mul(&mut out, long, short);
    } else {
        with_scratch(scratch_len(n), |s| mul_into(&mut out, a, b, s));
    }
    normalize(&mut out);
    out
}

fn sqr_nat(a: &[u64]) -> Vec<u64> {
    let n = 2 * a.len() + 1;
    let mut out = vec![0u64; n];
    if a.len() < KARATSUBA_SQR {
        basecase_sqr(&mut out, a);
    } else {
        with_scratch(scratch_len(n), |s| sqr_into(&mut out, a, s));
    }
    normalize(&mut out);
    out
}

// ---- division --------------------------------------------------------------

/// Knuth's algorithm D (TAOCP 4.3.1) for a divisor of two words or more:
/// quotient and remainder, trimmed.
fn knuth_div(u: &[u64], v: &[u64]) -> (Vec<u64>, Vec<u64>) {
    let shift = v[v.len() - 1].leading_zeros();
    let mut vn = shl_bits(v, shift);
    vn.pop();
    let mut un = shl_bits(u, shift);
    let n = vn.len();
    let m = un.len() - n - 1;
    let mut q = vec![0u64; m + 1];
    let (vtop, vnext) = (vn[n - 1] as u128, vn[n - 2] as u128);
    for j in (0..=m).rev() {
        let num = (un[j + n] as u128) << 64 | un[j + n - 1] as u128;
        let mut qhat = num / vtop;
        let mut rhat = num % vtop;
        while qhat > u64::MAX as u128 || qhat * vnext > (rhat << 64 | un[j + n - 2] as u128) {
            qhat -= 1;
            rhat += vtop;
            if rhat > u64::MAX as u128 {
                break;
            }
        }
        let borrow = submul_1(&mut un[j..j + n], &vn, qhat as u64);
        let (top, under) = un[j + n].overflowing_sub(borrow);
        un[j + n] = top;
        if under {
            qhat -= 1;
            let carry = add_in(&mut un[j..j + n], &vn);
            un[j + n] = un[j + n].wrapping_add(carry as u64);
        }
        q[j] = qhat as u64;
    }
    normalize(&mut q);
    (q, shr_bits(&un[..n], shift))
}

/// A divisor prepared for Barrett reduction: shifted until its top bit is set
/// (`v`, n words), with r = floor(B^2n / v).
struct Recip {
    shift: u32,
    v: Vec<u64>,
    r: Vec<u64>,
}

impl Recip {
    fn new(d: &[u64]) -> Self {
        let shift = d[d.len() - 1].leading_zeros();
        let mut v = shl_bits(d, shift);
        v.pop();
        let r = reciprocal(&v);
        Self { shift, v, r }
    }
}

/// floor(B^2n / v) for v of n words with its top bit set. Newton's iteration
/// from the reciprocal of v's top half: x1 = x0 + x0 (B^2n - v x0) / B^2n
/// doubles the correct words, then a product fixes the last unit or two.
fn reciprocal(v: &[u64]) -> Vec<u64> {
    let n = v.len();
    let mut b2n = vec![0u64; 2 * n + 1];
    b2n[2 * n] = 1;
    if n <= RECIP_BASE {
        return knuth_div(&b2n, v).0;
    }
    let h = n / 2 + 1;
    let rh = reciprocal(&v[n - h..]);
    // x0 = rh B^(n-h): its low n-h words are zero, so v x0 = (v rh) B^(n-h).
    let mut x = vec![0u64; n - h];
    x.extend_from_slice(&rh);
    let vx = shl_words(&mul_nat(v, &rh), n - h);
    let (negative, e) = match cmp_nat(&vx, &b2n) {
        Ordering::Greater => (true, sub_nat(&vx, &b2n)),
        _ => (false, sub_nat(&b2n, &vx)),
    };
    // x0 e / B^2n = rh e / B^(n+h); e's low words cannot move the result by a unit.
    let e_high = shr_words(&e, n - 1);
    let t = shr_words(&mul_nat(&rh, &e_high), h + 1);
    x = if negative { sub_nat(&x, &t) } else { add_nat(&x, &t) };
    let mut p = mul_nat(v, &x);
    while cmp_nat(&p, &b2n) == Ordering::Greater {
        x = sub_nat(&x, &[1]);
        p = sub_nat(&p, v);
    }
    loop {
        let rest = sub_nat(&b2n, &p);
        if cmp_nat(&rest, v) == Ordering::Less {
            break;
        }
        x = add_nat(&x, &[1]);
        p = add_nat(&p, v);
    }
    x
}

fn shl_words(a: &[u64], k: usize) -> Vec<u64> {
    if a.is_empty() {
        return Vec::new();
    }
    let mut out = vec![0u64; k];
    out.extend_from_slice(a);
    out
}

fn shr_words(a: &[u64], k: usize) -> Vec<u64> {
    if k >= a.len() {
        return Vec::new();
    }
    a[k..].to_vec()
}

/// x < v B^n for the prepared v of n words: floor(x / v) and x mod v. Barrett
/// (HAC 14.42): the estimate is at most two short.
fn barrett_step(x: &[u64], rec: &Recip) -> (Vec<u64>, Vec<u64>) {
    let n = rec.v.len();
    if cmp_nat(x, &rec.v) == Ordering::Less {
        return (Vec::new(), x.to_vec());
    }
    let q1 = &x[(n - 1).min(x.len())..];
    let mut q = shr_words(&mul_nat(q1, &rec.r), n + 1);
    let mut r = sub_nat(x, &mul_nat(&q, &rec.v));
    while cmp_nat(&r, &rec.v) != Ordering::Less {
        r = sub_nat(&r, &rec.v);
        q = add_nat(&q, &[1]);
    }
    (q, r)
}

/// u / d with d prepared: long division in base B^n, one Barrett step per block.
fn barrett_div(u: &[u64], rec: &Recip) -> (Vec<u64>, Vec<u64>) {
    let n = rec.v.len();
    let mut us = shl_bits(u, rec.shift);
    normalize(&mut us);
    let blocks = us.len().div_ceil(n);
    let mut q = vec![0u64; blocks * n];
    let mut r: Vec<u64> = Vec::new();
    for b in (0..blocks).rev() {
        let lo = b * n;
        let hi = (lo + n).min(us.len());
        let mut x = Vec::with_capacity(n + r.len());
        x.extend_from_slice(&us[lo..hi]);
        x.resize(n, 0);
        x.extend_from_slice(&r);
        normalize(&mut x);
        let (qb, rb) = barrett_step(&x, rec);
        q[lo..lo + qb.len()].copy_from_slice(&qb);
        r = rb;
    }
    normalize(&mut q);
    (q, shr_bits(&r, rec.shift))
}

/// Quotient and remainder of trimmed u and v, v nonzero; `power` supplies a
/// cached reciprocal when v is a power of five.
fn div_rem_nat(u: &[u64], v: &[u64], power: Option<&Power>) -> (Vec<u64>, Vec<u64>) {
    assert!(!v.is_empty(), "unsigned mantissa division by zero");
    if cmp_nat(u, v) == Ordering::Less {
        return (Vec::new(), u.to_vec());
    }
    if v.len() == 1 {
        let mut q = u.to_vec();
        let r = div_1(&mut q, v[0]);
        normalize(&mut q);
        return (q, if r == 0 { Vec::new() } else { vec![r] });
    }
    let (n, m) = (v.len(), u.len() - v.len());
    match power {
        Some(p) if n >= BARRETT_CACHED && m >= BARRETT_CACHED => barrett_div(u, p.recip()),
        None if n >= BARRETT_FRESH && m >= BARRETT_FRESH => barrett_div(u, &Recip::new(v)),
        _ => knuth_div(u, v),
    }
}

// ---- powers of five and ten ------------------------------------------------

struct Power {
    /// 5^k, trimmed.
    value: Vec<u64>,
    recip: OnceCell<Recip>,
}

impl Power {
    fn recip(&self) -> &Recip {
        self.recip.get_or_init(|| Recip::new(&self.value))
    }
}

#[derive(Default)]
struct Pow5Cache {
    map: HashMap<usize, Rc<Power>>,
    words: usize,
}

thread_local! {
    static POW5: RefCell<Pow5Cache> = RefCell::new(Pow5Cache::default());
}

/// 5^k (k > 27), cached with its halves.
fn pow5(k: usize) -> Rc<Power> {
    if let Some(p) = POW5.with(|c| c.borrow().map.get(&k).cloned()) {
        return p;
    }
    let value = if k <= 27 {
        vec![5u64.pow(k as u32)]
    } else {
        let half = pow5(k / 2);
        let mut v = sqr_nat(&half.value);
        if k % 2 == 1 {
            let carry = mul_1(&mut v, 5);
            if carry != 0 {
                v.push(carry);
            }
        }
        v
    };
    let p = Rc::new(Power { value, recip: OnceCell::new() });
    POW5.with(|c| {
        let mut c = c.borrow_mut();
        if c.words + p.value.len() > POW5_CACHE_WORDS {
            c.map.clear();
            c.words = 0;
        }
        c.words += p.value.len();
        c.map.insert(k, p.clone());
    });
    p
}

/// 10^k for k <= 19.
const fn pow10_word(k: usize) -> u64 {
    let mut p = 1u64;
    let mut i = 0;
    while i < k {
        p *= 10;
        i += 1;
    }
    p
}

/// a * 10^k = (a * 5^k) << k.
fn mul_pow10_nat(a: &[u64], k: usize) -> Vec<u64> {
    if a.is_empty() || k == 0 {
        return a.to_vec();
    }
    if k <= 19 {
        let mut out = a.to_vec();
        let carry = mul_1(&mut out, pow10_word(k));
        if carry != 0 {
            out.push(carry);
        }
        return out;
    }
    shl_nat(&mul_nat(a, &pow5(k).value), k)
}

/// a / 5^k and a mod 5^k.
fn divmod_pow5(a: &[u64], k: usize) -> (Vec<u64>, Vec<u64>) {
    if k <= 27 {
        let mut q = a.to_vec();
        let r = div_1(&mut q, 5u64.pow(k as u32));
        normalize(&mut q);
        return (q, if r == 0 { Vec::new() } else { vec![r] });
    }
    let p = pow5(k);
    div_rem_nat(a, &p.value, Some(&p))
}

/// a / 10^k and a mod 10^k: a >> k divided by 5^k, the low k bits kept.
fn divmod_pow10(a: &[u64], k: usize) -> (Vec<u64>, Vec<u64>) {
    if a.is_empty() || k == 0 {
        return (a.to_vec(), Vec::new());
    }
    if k <= 19 {
        let mut q = a.to_vec();
        let r = div_1(&mut q, pow10_word(k));
        normalize(&mut q);
        return (q, if r == 0 { Vec::new() } else { vec![r] });
    }
    let (q, r5) = divmod_pow5(&shr_nat(a, k), k);
    let low = low_bits(a, k);
    let mut r = shl_nat(&r5, k);
    if r.len() < low.len() {
        r.resize(low.len(), 0);
    }
    for (x, &y) in r.iter_mut().zip(&low) {
        *x |= y;
    }
    normalize(&mut r);
    (q, r)
}

/// Whether a < 10^k: 10^k = 5^k 2^k, so a < 10^k exactly when a >> k < 5^k.
fn below_pow10(a: &[u64], k: usize) -> bool {
    if k <= 19 {
        return a.len() <= 1 && a.first().map_or(0, |&w| w) < pow10_word(k);
    }
    if bit_len(a) <= k {
        return true;
    }
    cmp_nat(&shr_nat(a, k), &pow5(k).value) == Ordering::Less
}

/// floor(b log10 2), and whether the Q64 constant proves it (it does unless
/// b log10 2 lies within b / 2^64 of an integer).
fn floor_log10_pow2(b: usize) -> (usize, bool) {
    let f = b as u128 * LOG10_2_Q64;
    let exact = (f as u64 as u128) + (b as u128) < (1u128 << 64);
    ((f >> 64) as usize, exact)
}

/// Bounds on the decimal digit count of a nonzero a from its bit length alone:
/// 2^(b-1) <= a < 2^b.
fn digit_bounds_nat(a: &[u64]) -> (usize, usize) {
    let b = bit_len(a);
    let (lo, lo_exact) = floor_log10_pow2(b - 1);
    let (hi, hi_exact) = floor_log10_pow2(b);
    (lo + 1, hi + 1 + usize::from(!(lo_exact && hi_exact)))
}

/// The exact decimal digit count of a nonzero a.
fn digits_nat(a: &[u64]) -> usize {
    let (lo, hi) = digit_bounds_nat(a);
    let mut d = lo;
    while d < hi && !below_pow10(a, d) {
        d += 1;
    }
    d
}

/// The number of decimal zeros ending a nonzero a, at most `cap`: the lesser of
/// its factors of two (free, the low zero bits) and of five (found in strides
/// that double while they divide).
fn trailing_decimal_zeros(a: &[u64], cap: usize) -> usize {
    let cap = cap.min(trailing_zero_bits(a));
    if cap == 0 || rem_1(a, 5) != 0 {
        return 0;
    }
    let mut x = a.to_vec();
    let mut t = 0;
    let mut stride = 1usize;
    loop {
        if t + stride <= cap {
            let (q, r) = divmod_pow5(&x, stride);
            if r.is_empty() {
                x = q;
                t += stride;
                stride *= 2;
                continue;
            }
        }
        if stride == 1 {
            return t;
        }
        stride /= 2;
    }
}

// ---- decimal text ----------------------------------------------------------

/// Where a decimal conversion of `len` digits splits: the largest 19 * 2^j that
/// leaves the high part at least as long as a leaf, so every split point is a
/// power of ten the cache already holds.
fn split_digits(len: usize) -> usize {
    let mut k = 19;
    while 4 * k <= len {
        k *= 2;
    }
    k
}

/// ASCII digits (validated by the caller) as a magnitude.
fn from_decimal(digits: &[u8]) -> Vec<u64> {
    if digits.len() <= DEC_LEAF_DIGITS {
        let mut acc: Vec<u64> = Vec::new();
        let first = match digits.len() % 19 {
            0 => 19,
            n => n,
        };
        let mut start = 0;
        let mut len = first.min(digits.len());
        while start < digits.len() {
            let chunk = digits[start..start + len]
                .iter()
                .fold(0u64, |n, &d| n * 10 + (d - b'0') as u64);
            let carry = mul_1(&mut acc, pow10_word(len));
            if carry != 0 {
                acc.push(carry);
            }
            if chunk != 0 {
                if acc.is_empty() {
                    acc.push(0);
                }
                if add_in(&mut acc, &[chunk]) {
                    acc.push(1);
                }
            }
            start += len;
            len = 19;
        }
        normalize(&mut acc);
        return acc;
    }
    let k = split_digits(digits.len());
    let (high, low) = digits.split_at(digits.len() - k);
    add_nat(&mul_pow10_nat(&from_decimal(high), k), &from_decimal(low))
}

/// The digits of a < 10^out.len() into out (filled with b'0' by the caller),
/// right-aligned.
fn write_decimal(a: &[u64], out: &mut [u8]) {
    if a.len() <= DEC_LEAF_WORDS {
        let mut cur = a.to_vec();
        let mut end = out.len();
        while !cur.is_empty() {
            let mut r = div_1(&mut cur, TEN19);
            normalize(&mut cur);
            let start = end.saturating_sub(19);
            for slot in out[start..end].iter_mut().rev() {
                *slot = b'0' + (r % 10) as u8;
                r /= 10;
            }
            end = start;
        }
        return;
    }
    let k = split_digits(out.len());
    let (q, r) = divmod_pow10(a, k);
    let at = out.len() - k;
    let (high, low) = out.split_at_mut(at);
    write_decimal(&q, high);
    write_decimal(&r, low);
}

fn to_decimal(a: &[u64]) -> String {
    if a.is_empty() {
        return "0".to_string();
    }
    let mut out = vec![b'0'; digits_nat(a)];
    write_decimal(a, &mut out);
    String::from_utf8(out).expect("ASCII digits")
}

// ---- the magnitude type ----------------------------------------------------

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LargeDec {
    words: Vec<u64>,
}

impl LargeDec {
    /// The little-endian 64-bit words, without zero high words.
    pub fn words(&self) -> &[u64] {
        &self.words
    }
    pub fn from_words(mut words: Vec<u64>) -> Self {
        normalize(&mut words);
        Self { words }
    }
    pub fn zero() -> Self {
        Self::default()
    }
    pub fn one() -> Self {
        Self { words: vec![1] }
    }
    pub fn is_zero(&self) -> bool {
        self.words.is_empty()
    }
    // Both slices have already passed SEL's digit validation.
    pub(crate) fn from_decimal_parts(integer: &str, fraction: &str) -> Self {
        let digits: Vec<u8> = integer.bytes().chain(fraction.bytes()).collect();
        Self { words: from_decimal(&digits) }
    }
    pub fn to_u128(&self) -> Option<u128> {
        match self.words[..] {
            [] => Some(0),
            [lo] => Some(lo as u128),
            [lo, hi] => Some((hi as u128) << 64 | lo as u128),
            _ => None,
        }
    }
    /// The decimal digit count (zero has one).
    pub fn digits(&self) -> usize {
        if self.is_zero() {
            1
        } else {
            digits_nat(&self.words)
        }
    }
    /// Bounds on `digits()` from the bit length, without any arithmetic; they
    /// differ by at most one except, astronomically rarely, by two.
    pub fn digit_bounds(&self) -> (usize, usize) {
        if self.is_zero() {
            (1, 1)
        } else {
            digit_bounds_nat(&self.words)
        }
    }
    /// The decimal zeros ending the value (none for zero).
    pub fn trailing_zeros(&self) -> usize {
        self.trailing_zeros_at_most(usize::MAX)
    }
    pub fn trailing_zeros_at_most(&self, cap: usize) -> usize {
        if self.is_zero() {
            0
        } else {
            trailing_decimal_zeros(&self.words, cap)
        }
    }
    #[cfg(test)]
    pub fn last_digit(&self) -> u32 {
        rem_1(&self.words, 10) as u32
    }
    pub fn pow10(exponent: usize) -> Self {
        Self::one().mul_pow10(exponent)
    }
    pub fn mul_pow10(&self, exponent: usize) -> Self {
        Self { words: mul_pow10_nat(&self.words, exponent) }
    }
    pub fn div_pow10(&self, exponent: usize) -> (Self, Self) {
        let (q, r) = divmod_pow10(&self.words, exponent);
        (Self { words: q }, Self { words: r })
    }
    pub fn is_multiple_of_pow10(&self, exponent: usize) -> bool {
        if self.is_zero() || exponent == 0 {
            return true;
        }
        if trailing_zero_bits(&self.words) < exponent {
            return false;
        }
        divmod_pow10(&self.words, exponent).1.is_empty()
    }
    pub fn add(&self, rhs: &Self) -> Self {
        Self { words: add_nat(&self.words, &rhs.words) }
    }
    pub fn add_one(&mut self) {
        if add_in(&mut self.words, &[1]) || self.words.is_empty() {
            self.words.push(1);
        }
    }
    pub fn sub(&self, rhs: &Self) -> Self {
        assert!(self >= rhs, "unsigned mantissa subtraction underflow");
        Self { words: sub_nat(&self.words, &rhs.words) }
    }
    pub fn mul_small(&self, rhs: u32) -> Self {
        Self { words: mul_nat(&self.words, &[rhs as u64]) }
    }
    pub fn mul(&self, rhs: &Self) -> Self {
        Self { words: mul_nat(&self.words, &rhs.words) }
    }
    #[cfg(test)]
    pub fn div_small_assign(&mut self, divisor: u32) -> u32 {
        assert_ne!(divisor, 0);
        let r = div_1(&mut self.words, divisor as u64);
        normalize(&mut self.words);
        r as u32
    }
    pub fn div_rem(&self, divisor: &Self) -> (Self, Self) {
        let (q, r) = div_rem_nat(&self.words, &divisor.words, None);
        (Self { words: q }, Self { words: r })
    }
}

impl From<u128> for LargeDec {
    fn from(n: u128) -> Self {
        Self::from_words(vec![n as u64, (n >> 64) as u64])
    }
}
impl Ord for LargeDec {
    fn cmp(&self, rhs: &Self) -> Ordering {
        cmp_nat(&self.words, &rhs.words)
    }
}
impl PartialOrd for LargeDec {
    fn partial_cmp(&self, rhs: &Self) -> Option<Ordering> {
        Some(self.cmp(rhs))
    }
}
impl FromStr for LargeDec {
    type Err = &'static str;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
            return Err("invalid unsigned decimal digits");
        }
        Ok(Self { words: from_decimal(s.as_bytes()) })
    }
}
impl fmt::Display for LargeDec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&to_decimal(&self.words))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use num_bigint::BigUint;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        /// A trimmed magnitude of about `len` words: random words, or (every
        /// third call) the carry-heavy edges 0, 1 and all ones.
        fn nat(&mut self, len: usize) -> Vec<u64> {
            let edges = self.next() % 3 == 0;
            let mut v: Vec<u64> = (0..len)
                .map(|_| {
                    let n = self.next();
                    if edges {
                        [0, 1, u64::MAX, u64::MAX - 1][(n % 4) as usize]
                    } else {
                        n
                    }
                })
                .collect();
            normalize(&mut v);
            v
        }
    }

    fn big(a: &[u64]) -> BigUint {
        BigUint::from_slice(&a.iter().flat_map(|&w| [w as u32, (w >> 32) as u32]).collect::<Vec<u32>>())
    }
    fn nat(b: &BigUint) -> Vec<u64> {
        b.to_u64_digits()
    }

    #[test]
    fn products_and_squares_match_the_oracle_across_every_threshold() {
        let mut rng = Rng(0x5621_dce4_7012_986f);
        let sizes = [0, 1, 2, 3, 7, 31, 32, 33, 47, 48, 49, 63, 64, 65, 96, 97, 130, 200, 333];
        for &n in &sizes {
            for &m in &sizes {
                let (a, b) = (rng.nat(n), rng.nat(m));
                assert_eq!(mul_nat(&a, &b), nat(&(big(&a) * big(&b))), "{n} x {m}");
            }
            let a = rng.nat(n);
            assert_eq!(mul_nat(&a, &a), nat(&(big(&a) * big(&a))), "square {n}");
            assert_eq!(sqr_nat(&a), nat(&(big(&a) * big(&a))), "sqr {n}");
        }
        // Unbalanced far past two to one, and a Karatsuba four levels deep.
        for (n, m) in [(40, 700), (33, 1000), (100, 250), (1100, 1000), (900, 901)] {
            let (a, b) = (rng.nat(n), rng.nat(m));
            assert_eq!(mul_nat(&a, &b), nat(&(big(&a) * big(&b))), "{n} x {m}");
        }
        let a = rng.nat(1500);
        assert_eq!(sqr_nat(&a), nat(&(big(&a) * big(&a))), "sqr 1500");
        for _ in 0..300 {
            let (n, m) = ((rng.next() % 180) as usize, (rng.next() % 180) as usize);
            let (a, b) = (rng.nat(n), rng.nat(m));
            assert_eq!(mul_nat(&a, &b), nat(&(big(&a) * big(&b))), "{n} x {m}");
        }
    }

    fn check_division(u: &[u64], v: &[u64]) {
        let (q, r) = div_rem_nat(u, v, None);
        let (bq, br) = (big(u) / big(v), big(u) % big(v));
        assert_eq!((q.clone(), r.clone()), (nat(&bq), nat(&br)), "{} / {} words", u.len(), v.len());
        if v.len() >= 2 && u.len() >= v.len() {
            assert_eq!(knuth_div(u, v), (q, r), "knuth {} / {}", u.len(), v.len());
        }
    }

    #[test]
    fn division_matches_the_oracle_on_both_algorithms() {
        let mut rng = Rng(0x9e37_79b9_7f4a_7c15);
        for &(n, m) in &[
            (1, 1), (2, 1), (5, 3), (40, 2), (48, 48), (96, 48), (97, 49), (150, 60),
            (200, 100), (300, 50), (400, 199), (520, 260), (700, 330), (47, 47), (100, 47),
        ] {
            for _ in 0..4 {
                let (u, v) = (rng.nat(n), rng.nat(m));
                if !v.is_empty() {
                    check_division(&u, &v);
                    // u = q v + (v - 1): the largest remainder.
                    let q = rng.nat(n.saturating_sub(m) + 1);
                    let u2 = add_nat(&mul_nat(&q, &v), &sub_nat(&v, &[1]));
                    check_division(&u2, &v);
                    check_division(&mul_nat(&q, &v), &v);
                }
            }
        }
        for _ in 0..400 {
            let n = (rng.next() % 260) as usize;
            let m = 1 + (rng.next() % 130) as usize;
            let (u, v) = (rng.nat(n), rng.nat(m));
            if !v.is_empty() {
                check_division(&u, &v);
            }
        }
        // Barrett directly, at sizes either side of both of its thresholds.
        for &(n, m) in &[(40usize, 20usize), (64, 64), (100, 33), (160, 160), (170, 400), (300, 150), (400, 399)] {
            let (u, mut v) = (rng.nat(n + m), rng.nat(n));
            v.resize(n, 5);
            let (q, r) = barrett_div(&u, &Recip::new(&v));
            assert_eq!((q, r), (nat(&(big(&u) / big(&v))), nat(&(big(&u) % big(&v)))), "barrett {} / {n}", u.len());
        }
        // And past BARRETT_FRESH through the public road.
        let (u, v) = (rng.nat(2 * BARRETT_FRESH + 10), rng.nat(BARRETT_FRESH + 3));
        check_division(&u, &v);
        // Divisors whose top word is 1 or all ones: the normalizing shift at
        // its extremes, and Knuth's qhat corrections.
        for top in [1u64, 2, u64::MAX, 1 << 63] {
            for n in [2usize, 3, 50, 70] {
                let mut v = rng.nat(n);
                if v.len() < n {
                    v.resize(n, 7);
                }
                *v.last_mut().unwrap() = top;
                let u = rng.nat(3 * n + 5);
                check_division(&u, &v);
            }
        }
    }

    #[test]
    fn reciprocal_is_the_exact_floor() {
        let mut rng = Rng(0x2545_f491_4f6c_dd1d);
        for n in [2usize, 31, 32, 33, 34, 64, 65, 97, 150, 257] {
            for _ in 0..3 {
                let mut v = rng.nat(n);
                v.resize(n, 1);
                v[n - 1] |= 1 << 63;
                let mut b2n = vec![0u64; 2 * n + 1];
                b2n[2 * n] = 1;
                assert_eq!(reciprocal(&v), nat(&(big(&b2n) / big(&v))), "n = {n}");
            }
        }
    }

    #[test]
    fn decimal_text_round_trips_against_the_oracle() {
        let mut rng = Rng(0x0123_4567_89ab_cdef);
        let mut lengths: Vec<usize> = (1..=60).collect();
        lengths.extend([227, 228, 229, 455, 456, 457, 1000, 1500, 4000, 9999, 20000]);
        for &len in &lengths {
            for lead_zeros in [0usize, 3] {
                let text: String = (0..len)
                    .map(|i| {
                        let d = (rng.next() % 10) as u8;
                        (b'0' + if i < lead_zeros { 0 } else { d }) as char
                    })
                    .collect();
                let ours: LargeDec = text.parse().unwrap();
                let oracle: BigUint = text.parse().unwrap();
                assert_eq!(ours.words, nat(&oracle), "parse of {len} digits");
                assert_eq!(ours.to_string(), oracle.to_string(), "format of {len} digits");
                assert_eq!(ours.digits(), oracle.to_string().len(), "digits of {len}");
            }
        }
        // All nines and exact powers of ten: the digit count's two sides.
        for k in [1usize, 18, 19, 20, 38, 39, 100, 227, 228, 500, 2000] {
            let ten = LargeDec::pow10(k);
            assert_eq!(ten.to_string(), format!("1{}", "0".repeat(k)));
            assert_eq!(ten.digits(), k + 1);
            let nines = ten.sub(&LargeDec::one());
            assert_eq!(nines.to_string(), "9".repeat(k));
            assert_eq!(nines.digits(), k);
            let (lo, hi) = nines.digit_bounds();
            assert!(lo <= k && k <= hi && hi - lo <= 1);
        }
        assert_eq!(LargeDec::zero().to_string(), "0");
        assert!("".parse::<LargeDec>().is_err());
        assert!("12a".parse::<LargeDec>().is_err());
    }

    #[test]
    fn powers_of_ten_scale_and_divide_exactly() {
        let mut rng = Rng(0xdead_beef_cafe_f00d);
        for k in [0usize, 1, 9, 19, 20, 27, 28, 54, 100, 333, 1000, 4321] {
            let p = big(&LargeDec::pow10(k).words);
            assert_eq!(p, BigUint::from(10u32).pow(k as u32), "10^{k}");
            for n in [0usize, 1, 3, 40, 120] {
                let a = LargeDec::from_words(rng.nat(n));
                assert_eq!(big(&a.mul_pow10(k).words), big(&a.words) * &p, "a * 10^{k}");
                let (q, r) = a.div_pow10(k);
                assert_eq!((big(&q.words), big(&r.words)), (big(&a.words) / &p, big(&a.words) % &p), "a / 10^{k}");
                let scaled = a.mul_pow10(k);
                assert!(scaled.is_multiple_of_pow10(k));
                if !a.is_zero() {
                    let bumped = scaled.add(&LargeDec::one());
                    assert_eq!(bumped.is_multiple_of_pow10(k), k == 0);
                    let zeros = a.trailing_zeros();
                    assert_eq!(scaled.trailing_zeros(), zeros + k, "trailing zeros of a * 10^{k}");
                    assert_eq!(scaled.trailing_zeros_at_most(k), k.min(zeros + k));
                    assert_eq!(a.to_string().len() - a.to_string().trim_end_matches('0').len(), zeros);
                }
            }
        }
    }

    #[test]
    #[ignore]
    fn tune_division_thresholds() {
        let mut rng = Rng(0x1234_5678_9abc_def1);
        let time = |f: &mut dyn FnMut()| {
            let t = std::time::Instant::now();
            let mut reps = 0;
            while t.elapsed().as_millis() < 60 {
                f();
                reps += 1;
            }
            t.elapsed().as_nanos() as f64 / reps as f64 / 1000.0
        };
        println!("{:>6} {:>12} {:>14} {:>15}", "words", "knuth us", "fresh rec us", "cached rec us");
        for n in [32usize, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096] {
            let mut v = rng.nat(n);
            v.resize(n, 3);
            let u = rng.nat(2 * n);
            let rec = Recip::new(&v);
            let k = time(&mut || { std::hint::black_box(knuth_div(&u, &v)); });
            let f = time(&mut || { std::hint::black_box(barrett_div(&u, &Recip::new(&v))); });
            let c = time(&mut || { std::hint::black_box(barrett_div(&u, &rec)); });
            println!("{n:>6} {k:>12.1} {f:>14.1} {c:>15.1}");
        }
    }

    #[test]
    fn small_operations_and_conversions() {
        let a: LargeDec = "340282366920938463463374607431768211456".parse().unwrap(); // 2^128
        assert_eq!(a.words(), &[0, 0, 1]);
        assert_eq!(a.to_u128(), None);
        assert_eq!(LargeDec::from(u128::MAX).to_u128(), Some(u128::MAX));
        assert_eq!(LargeDec::from(0).to_u128(), Some(0));
        let mut b = LargeDec::from(u64::MAX as u128);
        b.add_one();
        assert_eq!(b.words(), &[0, 1]);
        let mut z = LargeDec::zero();
        z.add_one();
        assert_eq!(z, LargeDec::one());
        let mut c = LargeDec::from(1234567u128);
        assert_eq!(c.div_small_assign(10), 7);
        assert_eq!(c.to_u128(), Some(123456));
        assert_eq!(c.last_digit(), 6);
        assert_eq!(c.mul_small(3).to_u128(), Some(370368));
        let (q, r) = a.div_rem(&LargeDec::from(3u128));
        assert_eq!(q.mul_small(3).add(&r), a);
    }
}
