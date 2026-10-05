// Exact decimal arithmetic (Spec §4).

#[doc(hidden)]
pub use crate::large_dec::LargeDec;
use crate::limits::{DIV_SCALE, MAX_FRAC_DIGITS, MAX_INT_DIGITS};
use crate::utf8::{Pos, SelError};
use std::borrow::Cow;
use std::cmp::Ordering;
use std::fmt;
use std::sync::Arc;

pub const POW10_128: [i128; 39] = {
    let mut arr = [1i128; 39];
    let mut i = 1;
    let mut p = 1i128;
    while i < 39 {
        p *= 10;
        arr[i] = p;
        i += 1;
    }
    arr
};

/// A large mantissa is shared: cloning a `Dec` -- reading a number out of a
/// value, copying it, negating it -- costs a reference count, not a copy.
/// Nothing changes a mantissa once it is built, so sharing is never
/// observable; anything that ever needs to would go through `Arc::make_mut`.
#[doc(hidden)]
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecRepr {
    Small(i128),
    Large(Arc<LargeDec>),
}

/// An exact decimal: a sign, a mantissa and the count of its fraction digits
/// (SPEC §4). Built by `dec_parse`, `from_i64`, `from_small` or arithmetic;
/// the fields are the crate's, so no value can disagree with its own
/// invariants (a zero is never negative; the mantissa is held small when it
/// fits an i128).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Dec {
    pub(crate) neg: bool,
    pub(crate) scale: u32,
    pub(crate) repr: DecRepr,
}

// A compiled program carries Decs (literals, plan constants) and may be moved
// to another thread (tests/evaluator_stack.rs), so a Dec must stay Send + Sync:
// a shared mantissa has to be an Arc, never an Rc.
const _: () = {
    const fn assert_send_sync<T: Send + Sync>() {}
    assert_send_sync::<Dec>();
};

impl Dec {
    pub fn zero() -> Self {
        Self {
            neg: false,
            scale: 0,
            repr: DecRepr::Small(0),
        }
    }

    pub fn from_i64(n: i64) -> Self {
        if n < 0 {
            Self {
                neg: true,
                scale: 0,
                repr: DecRepr::Small((n as i128).unsigned_abs() as i128),
            }
        } else {
            Self {
                neg: false,
                scale: 0,
                repr: DecRepr::Small(n as i128),
            }
        }
    }

    pub fn from_small(neg: bool, mantissa: i128, scale: u32) -> Self {
        let mut m = mantissa;
        let mut n = neg;
        if m < 0 {
            n = !n;
            let Some(magnitude) = m.checked_neg() else {
                return Self::from_large(n, LargeDec::from(m.unsigned_abs()), scale);
            };
            m = magnitude;
        }
        if m == 0 {
            n = false;
        }
        Self {
            neg: n,
            scale,
            repr: DecRepr::Small(m),
        }
    }

    pub fn from_large(neg: bool, digits: LargeDec, scale: u32) -> Self {
        if digits.is_zero() {
            return Self {
                neg: false,
                scale,
                repr: DecRepr::Small(0),
            };
        }
        if let Some(u) = digits.to_u128() {
            if u <= i128::MAX as u128 {
                return Self {
                    neg,
                    scale,
                    repr: DecRepr::Small(u as i128),
                };
            }
        }
        Self {
            neg,
            scale,
            repr: DecRepr::Large(Arc::new(digits)),
        }
    }

    /// True for a number below zero (a zero never is).
    pub fn is_negative(&self) -> bool {
        self.neg
    }

    /// The count of fraction digits as written or computed: `1.50` has 2.
    pub fn scale(&self) -> u32 {
        self.scale
    }

    /// The mantissa's representation (tests and benchmarks).
    #[doc(hidden)]
    pub fn repr(&self) -> &DecRepr {
        &self.repr
    }

    /// A Dec from its parts as given, with none of the normalisation the
    /// constructors apply: for tests that hand the API a value it must
    /// defend against.
    #[doc(hidden)]
    pub fn from_raw_parts(neg: bool, scale: u32, repr: DecRepr) -> Self {
        Self { neg, scale, repr }
    }

    pub fn is_zero(&self) -> bool {
        match &self.repr {
            DecRepr::Small(m) => *m == 0,
            DecRepr::Large(b) => b.is_zero(),
        }
    }

    pub fn to_large(&self) -> LargeDec {
        match &self.repr {
            DecRepr::Small(m) => LargeDec::from(m.unsigned_abs()),
            DecRepr::Large(b) => (**b).clone(),
        }
    }

    pub fn to_i64(&self) -> Option<i64> {
        let t = dec_trunc(self);
        match &t.repr {
            DecRepr::Small(m) => {
                if t.neg {
                    let v = -*m;
                    if v >= i64::MIN as i128 && v <= i64::MAX as i128 {
                        Some(v as i64)
                    } else {
                        None
                    }
                } else if *m <= i64::MAX as i128 {
                    Some(*m as i64)
                } else {
                    None
                }
            }
            DecRepr::Large(_) => None,
        }
    }

    pub fn to_safe_i64(&self) -> i64 {
        let t = dec_trunc(self);
        match &t.repr {
            DecRepr::Small(m) => {
                if t.neg {
                    let v = -*m;
                    if v < i64::MIN as i128 {
                        i64::MIN
                    } else if v > i64::MAX as i128 {
                        i64::MAX
                    } else {
                        v as i64
                    }
                } else if *m > i64::MAX as i128 {
                    i64::MAX
                } else {
                    *m as i64
                }
            }
            DecRepr::Large(_) => {
                if t.neg {
                    i64::MIN
                } else {
                    i64::MAX
                }
            }
        }
    }

    pub fn is_integer(&self) -> bool {
        if self.scale == 0 {
            return true;
        }
        match &self.repr {
            DecRepr::Small(m) => {
                if (self.scale as usize) < POW10_128.len() {
                    let p = POW10_128[self.scale as usize];
                    m % p == 0
                } else {
                    *m == 0
                }
            }
            DecRepr::Large(b) => b.is_multiple_of_pow10(self.scale as usize),
        }
    }
}

/// The magnitude, borrowed when it is already large.
fn mag(d: &Dec) -> Cow<'_, LargeDec> {
    match &d.repr {
        DecRepr::Small(m) => Cow::Owned(LargeDec::from(m.unsigned_abs())),
        DecRepr::Large(b) => Cow::Borrowed(&**b),
    }
}

/// The magnitude times 10^k: aligned to a scale k places finer.
fn mag_scaled(d: &Dec, k: usize) -> Cow<'_, LargeDec> {
    if k == 0 {
        mag(d)
    } else {
        Cow::Owned(mag(d).mul_pow10(k))
    }
}

/// Bounds on the mantissa's decimal digit count; for a large one they come from
/// its bit length, with no conversion and no division.
fn digit_bounds(d: &Dec) -> (usize, usize) {
    match &d.repr {
        DecRepr::Small(m) => {
            let n = if *m == 0 { 1 } else { m.ilog10() as usize + 1 };
            (n, n)
        }
        DecRepr::Large(b) => b.digit_bounds(),
    }
}

pub fn dec_guard(d: Dec, pos: Pos) -> Result<Dec, SelError> {
    if d.scale as usize > MAX_FRAC_DIGITS {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} fractional digits", MAX_FRAC_DIGITS),
            pos,
        ));
    }
    // At most scale + MAX_INT_DIGITS mantissa digits. The bounds decide every
    // value not within a digit of that line; only those are counted exactly.
    let limit = d.scale as usize + MAX_INT_DIGITS;
    let (lo, hi) = digit_bounds(&d);
    let over = match &d.repr {
        DecRepr::Large(b) if lo <= limit && hi > limit => b.digits() > limit,
        _ => lo > limit,
    };
    if over {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} integer digits", MAX_INT_DIGITS),
            pos,
        ));
    }
    Ok(d)
}

pub fn dec_parse(text: &str, pos: Pos) -> Result<Dec, SelError> {
    let s = text;
    if s.is_empty() {
        return Err(SelError::new("E_NOT_NUM", "expected a number", pos));
    }
    let mut neg = false;
    let mut rest = s;
    if rest.starts_with('-') {
        neg = true;
        rest = &rest[1..];
    }
    if rest.is_empty() {
        return Err(SelError::new(
            "E_NOT_NUM",
            "expected digits after sign",
            pos,
        ));
    }

    let mut dot_idx = None;
    for (i, b) in rest.bytes().enumerate() {
        if b == b'.' {
            if dot_idx.is_some() {
                return Err(SelError::new("E_NOT_NUM", "multiple decimal points", pos));
            }
            dot_idx = Some(i);
        } else if !b.is_ascii_digit() {
            return Err(SelError::new("E_NOT_NUM", "invalid digit in number", pos));
        }
    }

    let (int_part, frac_part) = match dot_idx {
        Some(dot) => {
            let int_p = &rest[..dot];
            let frac_p = &rest[dot + 1..];
            if int_p.is_empty() || frac_p.is_empty() {
                return Err(SelError::new(
                    "E_NOT_NUM",
                    "missing digits around decimal point",
                    pos,
                ));
            }
            (int_p, frac_p)
        }
        None => (rest, ""),
    };

    if frac_part.len() > MAX_FRAC_DIGITS {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} fractional digits", MAX_FRAC_DIGITS),
            pos,
        ));
    }

    let significant_int = int_part.trim_start_matches('0');
    let int_len = significant_int.len();
    if int_len > MAX_INT_DIGITS {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} integer digits", MAX_INT_DIGITS),
            pos,
        ));
    }

    let scale = frac_part.len() as u32;

    let significant_frac = if significant_int.is_empty() {
        frac_part.trim_start_matches('0')
    } else {
        frac_part
    };
    if significant_int.len() + significant_frac.len() <= 39 {
        let mantissa = significant_int
            .bytes()
            .chain(significant_frac.bytes())
            .try_fold(0i128, |n, byte| {
                n.checked_mul(10)?.checked_add((byte - b'0') as i128)
            });
        if let Some(mantissa) = mantissa {
            return dec_guard(Dec::from_small(neg, mantissa, scale), pos);
        }
    }
    let digits = LargeDec::from_decimal_parts(significant_int, significant_frac);
    dec_guard(Dec::from_large(neg, digits, scale), pos)
}

pub fn dec_format(d: &Dec) -> String {
    let sign = if d.neg && !d.is_zero() { "-" } else { "" };
    let digits_str = match &d.repr {
        DecRepr::Small(m) => m.to_string(),
        DecRepr::Large(b) => b.to_string(),
    };
    if d.scale == 0 {
        return format!("{}{}", sign, digits_str);
    }
    let scale = d.scale as usize;
    let s = if digits_str.len() <= scale {
        let pad = "0".repeat(scale + 1 - digits_str.len());
        format!("{}{}", pad, digits_str)
    } else {
        digits_str
    };
    let dot_idx = s.len() - scale;
    format!("{}{}.{}", sign, &s[..dot_idx], &s[dot_idx..])
}

/// The length of `dec_format(d)` without producing it: the sign, the mantissa's
/// digits (padded to scale + 1 when all fractional), and the point. The text is
/// ASCII, so this is its code-point count too.
pub fn dec_format_len(d: &Dec) -> usize {
    let sign = usize::from(d.neg && !d.is_zero());
    let digits = match &d.repr {
        DecRepr::Small(m) => m.checked_ilog10().map_or(1, |n| n as usize + 1),
        DecRepr::Large(b) => b.digits(),
    };
    let scale = d.scale as usize;
    sign + if scale == 0 { digits } else { digits.max(scale + 1) + 1 }
}

impl fmt::Display for Dec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", dec_format(self))
    }
}

pub fn dec_trim_scale(d: &Dec) -> Dec {
    if d.is_zero() {
        return Dec::zero();
    }
    if d.scale == 0 {
        return d.clone();
    }
    if let DecRepr::Small(mut m) = d.repr {
        let mut scale = d.scale;
        while scale > 0 && m % 10 == 0 {
            m /= 10;
            scale -= 1;
        }
        return Dec::from_small(d.neg, m, scale);
    }
    let DecRepr::Large(digits) = &d.repr else {
        unreachable!()
    };
    let zeros = digits.trailing_zeros_at_most(d.scale as usize);
    if zeros == 0 {
        return d.clone();
    }
    Dec::from_large(d.neg, digits.div_pow10(zeros).0, d.scale - zeros as u32)
}

pub fn dec_negate(d: &Dec) -> Dec {
    if d.is_zero() {
        d.clone()
    } else {
        Dec {
            neg: !d.neg,
            scale: d.scale,
            repr: d.repr.clone(),
        }
    }
}

pub fn dec_abs(d: &Dec) -> Dec {
    Dec {
        neg: false,
        scale: d.scale,
        repr: d.repr.clone(),
    }
}

pub fn dec_sign(d: &Dec) -> i64 {
    if d.is_zero() {
        0
    } else if d.neg {
        -1
    } else {
        1
    }
}

fn align_small(a: &Dec, b: &Dec) -> Option<(i128, i128, u32)> {
    if let (DecRepr::Small(ma), DecRepr::Small(mb)) = (&a.repr, &b.repr) {
        if a.scale == b.scale {
            return Some((*ma, *mb, a.scale));
        }
        if a.scale > b.scale {
            let diff = (a.scale - b.scale) as usize;
            if diff < POW10_128.len() {
                if let Some(scaled_b) = mb.checked_mul(POW10_128[diff]) {
                    return Some((*ma, scaled_b, a.scale));
                }
            }
        } else {
            let diff = (b.scale - a.scale) as usize;
            if diff < POW10_128.len() {
                if let Some(scaled_a) = ma.checked_mul(POW10_128[diff]) {
                    return Some((scaled_a, *mb, b.scale));
                }
            }
        }
    }
    None
}

pub fn dec_add(a: &Dec, b: &Dec, pos: Pos) -> Result<Dec, SelError> {
    add_signed(a, b, b.neg, pos)
}

/// a - b is a + (-b), and negating only flips the sign, so b's mantissa is
/// used where it is. The sign rule is `dec_negate`'s: a zero keeps its own.
pub fn dec_sub(a: &Dec, b: &Dec, pos: Pos) -> Result<Dec, SelError> {
    add_signed(a, b, if b.is_zero() { b.neg } else { !b.neg }, pos)
}

/// a + b, with `b_neg` standing for b's sign.
fn add_signed(a: &Dec, b: &Dec, b_neg: bool, pos: Pos) -> Result<Dec, SelError> {
    if let Some((ma, mb, scale)) = align_small(a, b) {
        if a.neg == b_neg {
            if let Some(sum) = ma.checked_add(mb) {
                return dec_guard(Dec::from_small(a.neg, sum, scale), pos);
            }
        } else if ma >= mb {
            return dec_guard(Dec::from_small(a.neg, ma - mb, scale), pos);
        } else {
            return dec_guard(Dec::from_small(b_neg, mb - ma, scale), pos);
        }
    }

    let scale = a.scale.max(b.scale);
    let ba = mag_scaled(a, (scale - a.scale) as usize);
    let bb = mag_scaled(b, (scale - b.scale) as usize);

    if a.neg == b_neg {
        let sum = ba.add(&bb);
        dec_guard(Dec::from_large(a.neg, sum, scale), pos)
    } else if ba >= bb {
        let diff = ba.sub(&bb);
        dec_guard(Dec::from_large(a.neg, diff, scale), pos)
    } else {
        let diff = bb.sub(&ba);
        dec_guard(Dec::from_large(b_neg, diff, scale), pos)
    }
}

pub fn dec_mul(a: &Dec, b: &Dec, pos: Pos) -> Result<Dec, SelError> {
    let neg = a.neg != b.neg;
    // Check scale before multiplication, including zero products. Use a
    // wider sum so even a scale from_small accepted unchecked cannot wrap.
    let scale = a.scale as u64 + b.scale as u64;
    if scale > MAX_FRAC_DIGITS as u64 {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} fractional digits", MAX_FRAC_DIGITS),
            pos,
        ));
    }
    let prod_scale = scale as u32;

    if let (DecRepr::Small(ma), DecRepr::Small(mb)) = (&a.repr, &b.repr) {
        if let Some(prod) = ma.checked_mul(*mb) {
            return dec_guard(Dec::from_small(neg, prod, prod_scale), pos);
        }
    }

    // Nonzero products have at least da + db - 1 mantissa digits. Refuse
    // provably oversized results before multiplying; the lower digit bounds
    // prove it without counting, and the exact result is guarded anyway.
    if !a.is_zero()
        && !b.is_zero()
        && (digit_bounds(a).0 + digit_bounds(b).0 - 1).saturating_sub(prod_scale as usize)
            > MAX_INT_DIGITS
    {
        return Err(SelError::new(
            "E_RANGE",
            format!("number has more than {} integer digits", MAX_INT_DIGITS),
            pos,
        ));
    }
    let prod = mag(a).mul(&mag(b));
    dec_guard(Dec::from_large(neg, prod, prod_scale), pos)
}

/// Orders |a| and |b| (both nonzero) from their digit bounds when those
/// cannot overlap: |a| < 10^(hi_a - scale_a) <= 10^(lo_b - 1 - scale_b) <= |b|.
fn cmp_mag_by_size(a: &Dec, b: &Dec) -> Option<Ordering> {
    if a.is_zero() || b.is_zero() {
        return None;
    }
    let ((alo, ahi), (blo, bhi)) = (digit_bounds(a), digit_bounds(b));
    let (sa, sb) = (a.scale as i64, b.scale as i64);
    if ahi as i64 - sa <= blo as i64 - 1 - sb {
        Some(Ordering::Less)
    } else if bhi as i64 - sb <= alo as i64 - 1 - sa {
        Some(Ordering::Greater)
    } else {
        None
    }
}

pub fn dec_cmp(a: &Dec, b: &Dec) -> std::cmp::Ordering {
    if a.is_zero() && b.is_zero() {
        return std::cmp::Ordering::Equal;
    }
    if a.neg != b.neg {
        return if a.neg {
            std::cmp::Ordering::Less
        } else {
            std::cmp::Ordering::Greater
        };
    }
    let ord = if let Some((ma, mb, _)) = align_small(a, b) {
        ma.cmp(&mb)
    } else if let Some(ord) = cmp_mag_by_size(a, b) {
        ord
    } else {
        let scale = a.scale.max(b.scale);
        let ba = mag_scaled(a, (scale - a.scale) as usize);
        let bb = mag_scaled(b, (scale - b.scale) as usize);
        ba.cmp(&bb)
    };
    if a.neg {
        ord.reverse()
    } else {
        ord
    }
}

pub fn dec_div(a: &Dec, b: &Dec, pos: Pos) -> Result<Dec, SelError> {
    if b.is_zero() {
        return Err(SelError::new("E_DIV_ZERO", "division by zero", pos));
    }
    let neg = a.neg != b.neg;

    // Fast path: if small and numbers fit in i128 with DIV_SCALE
    if let (DecRepr::Small(ma), DecRepr::Small(mb)) = (&a.repr, &b.repr) {
        let n_scale = b.scale as usize + DIV_SCALE;
        let d_scale = a.scale as usize;
        let min_s = n_scale.min(d_scale);
        let n_pow = n_scale - min_s;
        let d_pow = d_scale - min_s;

        if n_pow < POW10_128.len() && d_pow < POW10_128.len() {
            if let (Some(num), Some(den)) = (
                ma.checked_mul(POW10_128[n_pow]),
                mb.checked_mul(POW10_128[d_pow]),
            ) {
                if den != 0 {
                    let mut q = num / den;
                    let r = num % den;
                    if r == 0 {
                        let mut scale = DIV_SCALE as u32;
                        while scale > 0 && q % 10 == 0 {
                            q /= 10;
                            scale -= 1;
                        }
                        if q == 0 {
                            scale = 0;
                        }
                        return dec_guard(Dec::from_small(neg, q, scale), pos);
                    } else {
                        // Half away from zero. Compare without doubling the
                        // remainder: 2*r may overflow even when num/den fit.
                        if r >= den - r {
                            q += 1;
                        }
                        return dec_guard(Dec::from_small(neg, q, DIV_SCALE as u32), pos);
                    }
                }
            }
        }
    }

    // Large: |a| 10^(sb - sa + DIV_SCALE) / |b| 10^(sa - sb), whichever
    // exponents are positive.
    let (sa, sb) = (a.scale as usize, b.scale as usize);
    let num = mag_scaled(a, sb.saturating_sub(sa) + DIV_SCALE);
    let den = mag_scaled(b, sa.saturating_sub(sb));
    let (mut q, r) = num.div_rem(&den);

    if r.is_zero() {
        // An exact quotient takes its minimal scale: drop up to DIV_SCALE zeros.
        let zeros = q.trailing_zeros_at_most(DIV_SCALE);
        let scale = if q.is_zero() { 0 } else { (DIV_SCALE - zeros) as u32 };
        let q = if zeros == 0 { q } else { q.div_pow10(zeros).0 };
        dec_guard(Dec::from_large(neg, q, scale), pos)
    } else {
        let two_r = r.mul_small(2);
        if two_r >= *den {
            q.add_one();
        }
        dec_guard(Dec::from_large(neg, q, DIV_SCALE as u32), pos)
    }
}

pub fn dec_mod(a: &Dec, b: &Dec, pos: Pos) -> Result<Dec, SelError> {
    if b.is_zero() {
        return Err(SelError::new("E_DIV_ZERO", "modulo by zero", pos));
    }
    if let Some((ma, mb, scale)) = align_small(a, b) {
        let rem = ma % mb;
        return dec_guard(Dec::from_small(a.neg, rem, scale), pos);
    }
    let scale = a.scale.max(b.scale);
    let ba = mag_scaled(a, (scale - a.scale) as usize);
    let bb = mag_scaled(b, (scale - b.scale) as usize);
    let rem = ba.div_rem(&bb).1;
    dec_guard(Dec::from_large(a.neg, rem, scale), pos)
}

pub fn dec_round(d: &Dec, n: usize, pos: Pos) -> Result<Dec, SelError> {
    if n > MAX_FRAC_DIGITS {
        return Err(SelError::new(
            "E_RANGE",
            "ROUND scale exceeds the fractional digit limit",
            pos,
        ));
    }
    if n >= d.scale as usize {
        let diff = n - d.scale as usize;
        if let DecRepr::Small(m) = &d.repr {
            if diff < POW10_128.len() {
                if let Some(res) = m.checked_mul(POW10_128[diff]) {
                    return dec_guard(Dec::from_small(d.neg, res, n as u32), pos);
                }
            }
        }
        let b = mag(d).mul_pow10(diff);
        return dec_guard(Dec::from_large(d.neg, b, n as u32), pos);
    }

    let diff = d.scale as usize - n;
    if let DecRepr::Small(m) = &d.repr {
        if diff < POW10_128.len() {
            let p = POW10_128[diff];
            let mut q = m / p;
            let r = m % p;
            // Round half up (Spec §4: ROUND)
            if r >= p - r {
                q += 1;
            }
            return dec_guard(Dec::from_small(d.neg, q, n as u32), pos);
        }
        // For diff >= 39, even i128::MAX is below half of 10^diff.
        return dec_guard(Dec::from_small(false, 0, n as u32), pos);
    }

    let (mut q, r) = mag(d).div_pow10(diff);
    // Half away from zero: 2r >= 10^diff.
    if !r.is_zero() && r.mul_small(2) >= LargeDec::pow10(diff) {
        q.add_one();
    }
    dec_guard(Dec::from_large(d.neg, q, n as u32), pos)
}

pub fn dec_trunc(d: &Dec) -> Dec {
    if d.scale == 0 {
        return d.clone();
    }
    if let DecRepr::Small(m) = &d.repr {
        if (d.scale as usize) < POW10_128.len() {
            let p = POW10_128[d.scale as usize];
            return Dec::from_small(d.neg, m / p, 0);
        }
        return Dec::zero();
    }
    let q = mag(d).div_pow10(d.scale as usize).0;
    Dec::from_large(d.neg, q, 0)
}

pub fn dec_floor(d: &Dec, pos: Pos) -> Result<Dec, SelError> {
    if d.scale == 0 {
        return dec_guard(d.clone(), pos);
    }
    if let DecRepr::Small(m) = &d.repr {
        if (d.scale as usize) < POW10_128.len() {
            let p = POW10_128[d.scale as usize];
            let mut q = m / p;
            let r = m % p;
            if d.neg && r != 0 {
                q += 1;
            }
            return dec_guard(Dec::from_small(d.neg, q, 0), pos);
        }
        return Ok(Dec::from_small(d.neg, i128::from(d.neg && *m != 0), 0));
    }
    let (mut q, r) = mag(d).div_pow10(d.scale as usize);
    if d.neg && !r.is_zero() {
        q.add_one();
    }
    dec_guard(Dec::from_large(d.neg, q, 0), pos)
}

pub fn dec_ceil(d: &Dec, pos: Pos) -> Result<Dec, SelError> {
    if d.scale == 0 {
        return dec_guard(d.clone(), pos);
    }
    if let DecRepr::Small(m) = &d.repr {
        if (d.scale as usize) < POW10_128.len() {
            let p = POW10_128[d.scale as usize];
            let mut q = m / p;
            let r = m % p;
            if !d.neg && r != 0 {
                q += 1;
            }
            return dec_guard(Dec::from_small(d.neg, q, 0), pos);
        }
        return Ok(Dec::from_small(d.neg, i128::from(!d.neg && *m != 0), 0));
    }
    let (mut q, r) = mag(d).div_pow10(d.scale as usize);
    if !d.neg && !r.is_zero() {
        q.add_one();
    }
    dec_guard(Dec::from_large(d.neg, q, 0), pos)
}

pub fn dec_power(a: &Dec, n: usize, pos: Pos) -> Result<Dec, SelError> {
    let mut result = Dec::from_small(false, 1, 0);
    let mut base = a.clone();
    let mut e = n;
    while e > 0 {
        if e % 2 == 1 {
            result = dec_mul(&result, &base, pos)?;
        }
        e /= 2;
        if e > 0 {
            base = dec_mul(&base, &base, pos)?;
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signed_small_constructor_handles_minimum() {
        let d = Dec::from_small(false, i128::MIN, 0);
        assert_eq!(dec_format(&d), i128::MIN.to_string());
        assert_eq!(
            dec_format(&Dec::from_small(true, i128::MIN, 0)),
            i128::MIN.unsigned_abs().to_string()
        );
    }

    #[test]
    fn format_len_is_the_formatted_length() {
        let pos = Pos::default();
        let mut seed = 0x0bad_cafe_d00d_f00du64;
        let mut next = move || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for digits in [1usize, 2, 9, 19, 38, 39, 40, 100, 300] {
            for _ in 0..6 {
                let mantissa: String = (0..digits)
                    .map(|i| (b'0' + (next() % 10) as u8 + u8::from(i == 0 && digits > 1)).min(b'9') as char)
                    .collect();
                for scale in [0usize, 1, digits - 1, digits, digits + 1, digits + 7] {
                    for neg in [false, true] {
                        let text = if scale == 0 {
                            mantissa.clone()
                        } else {
                            let padded = format!("{:0>w$}", mantissa, w = scale + 1);
                            format!("{}.{}", &padded[..padded.len() - scale], &padded[padded.len() - scale..])
                        };
                        let text = if neg { format!("-{text}") } else { text };
                        let d = dec_parse(&text, pos).unwrap();
                        assert_eq!(dec_format_len(&d), dec_format(&d).len(), "{text}");
                    }
                }
            }
        }
        for zero in ["0", "0.000", "-0.0"] {
            let d = dec_parse(zero, pos).unwrap();
            assert_eq!(dec_format_len(&d), dec_format(&d).len(), "{zero}");
        }
    }

    #[test]
    fn rounding_agrees_across_representations() {
        let pos = Pos::default();
        for (a, b) in [
            ("1", "20000000000"),
            ("-1", "20000000000"),
            (
                "8600000000000000000000000000.0000000000",
                "99999999999999999999999999999999999999",
            ),
            ("99999999999999999999999999999999999999", "3"),
        ] {
            let a = dec_parse(a, pos).unwrap();
            let b = dec_parse(b, pos).unwrap();
            assert_eq!(
                dec_div(&a, &b, pos).unwrap(),
                dec_div(&force_large(&a), &force_large(&b), pos).unwrap()
            );
            for scale in [0, 10, 38] {
                assert_eq!(
                    dec_round(&a, scale, pos).unwrap(),
                    dec_round(&force_large(&a), scale, pos).unwrap()
                );
            }
        }
    }

    /// The same value with its mantissa forced into the large representation.
    fn force_large(d: &Dec) -> Dec {
        Dec { neg: d.neg, scale: d.scale, repr: DecRepr::Large(d.to_large().into()) }
    }

    #[test]
    fn subtraction_is_addition_of_the_negation() {
        // dec_sub must agree with dec_add(a, -b) whatever it does about the
        // copy that negation implies: signs, zeros (a negative zero included),
        // scales, and both representations of either operand.
        let pos = Pos::default();
        let texts = [
            "0", "0.00", "5", "-5", "2.5", "-2.50", "0.0000000000000000000000000000000000001",
            "170141183460469231731687303715884105727", "-170141183460469231731687303715884105728",
            "1234567890123456789012345678901234567890123456789.25",
            "-98765432109876543210987654321098765432109876543.5",
        ];
        let mut values: Vec<Dec> = texts.iter().map(|t| dec_parse(t, pos).unwrap()).collect();
        values.push(Dec { neg: true, scale: 1, repr: DecRepr::Small(0) });
        for a in &values {
            for b in &values {
                for (a, b) in [
                    (a.clone(), b.clone()),
                    (force_large(a), b.clone()),
                    (a.clone(), force_large(b)),
                    (force_large(a), force_large(b)),
                ] {
                    let want = dec_add(&a, &dec_negate(&b), pos).unwrap();
                    let got = dec_sub(&a, &b, pos).unwrap();
                    assert_eq!(dec_format(&got), dec_format(&want), "{a:?} - {b:?}");
                    assert_eq!((got.neg, got.scale), (want.neg, want.scale), "{a:?} - {b:?}");
                }
            }
        }
    }
}
