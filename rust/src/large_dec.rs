//! Unsigned base-10^9 mantissas. Zero has no limbs; all other values have a
//! nonzero final limb. Decimal scale and sign live in `Dec`.
use std::{cmp::Ordering, fmt, str::FromStr};

const BASE: u64 = 1_000_000_000;

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LargeDec {
    limbs: Vec<u32>,
}

impl LargeDec {
    pub fn limbs(&self) -> &[u32] {
        &self.limbs
    }
    pub fn zero() -> Self {
        Self::default()
    }
    pub fn one() -> Self {
        Self { limbs: vec![1] }
    }
    pub fn is_zero(&self) -> bool {
        self.limbs.is_empty()
    }
    fn normalized(mut limbs: Vec<u32>) -> Self {
        while limbs.last() == Some(&0) {
            limbs.pop();
        }
        Self { limbs }
    }
    // Both slices have already passed SEL's digit validation. Reading across
    // the decimal point avoids allocating a joined mantissa string.
    pub(crate) fn from_decimal_parts(integer: &str, fraction: &str) -> Self {
        let mut limbs = Vec::with_capacity((integer.len() + fraction.len()).div_ceil(9));
        let mut limb = 0u32;
        let mut place = 1u32;
        for byte in fraction.bytes().rev().chain(integer.bytes().rev()) {
            limb += (byte - b'0') as u32 * place;
            if place == 100_000_000 {
                limbs.push(limb);
                limb = 0;
                place = 1;
            } else {
                place *= 10;
            }
        }
        if place != 1 {
            limbs.push(limb);
        }
        Self::normalized(limbs)
    }

    pub fn to_u128(&self) -> Option<u128> {
        self.limbs.iter().rev().try_fold(0u128, |n, &limb| {
            n.checked_mul(BASE as u128)?.checked_add(limb as u128)
        })
    }
    pub fn digits(&self) -> usize {
        self.limbs
            .last()
            .map_or(1, |n| (self.limbs.len() - 1) * 9 + n.ilog10() as usize + 1)
    }
    pub fn trailing_zeros(&self) -> usize {
        if self.is_zero() {
            return 0;
        }
        let whole = self.limbs.iter().take_while(|&&n| n == 0).count();
        let mut low = self.limbs[whole];
        let mut count = whole * 9;
        while low % 10 == 0 {
            low /= 10;
            count += 1;
        }
        count
    }
    pub fn pow10(exponent: usize) -> Self {
        let mut limbs = vec![0; exponent / 9 + 1];
        limbs[exponent / 9] = 10u32.pow((exponent % 9) as u32);
        Self { limbs }
    }
    pub fn mul_pow10_assign(&mut self, exponent: usize) {
        if self.is_zero() {
            return;
        }
        self.mul_small_assign(10u32.pow((exponent % 9) as u32));
        let shift = exponent / 9;
        if shift != 0 {
            let old_len = self.limbs.len();
            self.limbs.resize(old_len + shift, 0);
            self.limbs.copy_within(..old_len, shift);
            self.limbs[..shift].fill(0);
        }
    }
    pub fn div_pow10(&self, exponent: usize) -> (Self, Self) {
        let shift = exponent / 9;
        if shift >= self.limbs.len() {
            return (Self::zero(), self.clone());
        }
        let mut quotient = Self::normalized(self.limbs[shift..].to_vec());
        let high_remainder = quotient.div_small_assign(10u32.pow((exponent % 9) as u32));
        let mut remainder = self.limbs[..shift].to_vec();
        remainder.push(high_remainder);
        (quotient, Self::normalized(remainder))
    }
    pub fn add(&self, rhs: &Self) -> Self {
        let mut out = Vec::with_capacity(self.limbs.len().max(rhs.limbs.len()) + 1);
        let mut carry = 0u64;
        for i in 0..self.limbs.len().max(rhs.limbs.len()) {
            let n = self.limbs.get(i).copied().unwrap_or(0) as u64
                + rhs.limbs.get(i).copied().unwrap_or(0) as u64
                + carry;
            out.push((n % BASE) as u32);
            carry = n / BASE;
        }
        if carry != 0 {
            out.push(carry as u32);
        }
        Self { limbs: out }
    }
    pub fn add_one(&mut self) {
        for limb in &mut self.limbs {
            *limb += 1;
            if *limb < BASE as u32 {
                return;
            }
            *limb = 0;
        }
        self.limbs.push(1);
    }
    pub fn sub(&self, rhs: &Self) -> Self {
        assert!(self >= rhs, "unsigned mantissa subtraction underflow");
        let mut out = self.limbs.clone();
        let mut borrow = 0i64;
        for (i, limb) in out.iter_mut().enumerate() {
            let n = *limb as i64 - rhs.limbs.get(i).copied().unwrap_or(0) as i64 - borrow;
            borrow = i64::from(n < 0);
            *limb = (n + borrow * BASE as i64) as u32;
        }
        Self::normalized(out)
    }
    pub fn mul_small(&self, rhs: u32) -> Self {
        let mut out = self.clone();
        out.mul_small_assign(rhs);
        out
    }
    fn mul_small_assign(&mut self, rhs: u32) {
        if rhs == 0 {
            self.limbs.clear();
            return;
        }
        let mut carry = 0u64;
        for limb in &mut self.limbs {
            let n = *limb as u64 * rhs as u64 + carry;
            *limb = (n % BASE) as u32;
            carry = n / BASE;
        }
        while carry != 0 {
            self.limbs.push((carry % BASE) as u32);
            carry /= BASE;
        }
    }
    fn add_shifted(out: &mut Vec<u32>, value: &Self, shift: usize) {
        out.resize(out.len().max(value.limbs.len() + shift + 1), 0);
        let mut carry = 0u64;
        for (i, &limb) in value.limbs.iter().enumerate() {
            let n = out[i + shift] as u64 + limb as u64 + carry;
            out[i + shift] = (n % BASE) as u32;
            carry = n / BASE;
        }
        let mut i = value.limbs.len() + shift;
        while carry != 0 {
            if i == out.len() {
                out.push(0);
            }
            let n = out[i] as u64 + carry;
            out[i] = (n % BASE) as u32;
            carry = n / BASE;
            i += 1;
        }
    }
    pub fn mul(&self, rhs: &Self) -> Self {
        if self.is_zero() || rhs.is_zero() {
            return Self::zero();
        }
        let n = self.limbs.len();
        let m = rhs.limbs.len();
        // Karatsuba bounds work for large balanced inputs; schoolbook handles
        // short and very asymmetric inputs without recursive allocation.
        if n.min(m) >= 32 && n.max(m) <= n.min(m) * 2 {
            let split = n.max(m) / 2;
            let parts = |x: &Self| {
                (
                    Self::normalized(x.limbs[..split.min(x.limbs.len())].to_vec()),
                    Self::normalized(x.limbs[split.min(x.limbs.len())..].to_vec()),
                )
            };
            let (a0, a1) = parts(self);
            let (b0, b1) = parts(rhs);
            let low = a0.mul(&b0);
            let high = a1.mul(&b1);
            let middle = a0.add(&a1).mul(&b0.add(&b1)).sub(&low).sub(&high);
            let mut out = low.limbs.clone();
            Self::add_shifted(&mut out, &middle, split);
            Self::add_shifted(&mut out, &high, 2 * split);
            return Self::normalized(out);
        }
        let mut out = vec![0u32; n + m];
        for (i, &a) in self.limbs.iter().enumerate() {
            if a == 0 {
                continue;
            }
            let mut carry = 0u64;
            for (j, &b) in rhs.limbs.iter().enumerate() {
                let product = a as u64 * b as u64 + out[i + j] as u64 + carry;
                out[i + j] = (product % BASE) as u32;
                carry = product / BASE;
            }
            out[i + m] = carry as u32;
        }
        Self::normalized(out)
    }
    pub fn div_small_assign(&mut self, divisor: u32) -> u32 {
        assert_ne!(divisor, 0);
        let mut remainder = 0u64;
        for limb in self.limbs.iter_mut().rev() {
            let n = remainder * BASE + *limb as u64;
            *limb = (n / divisor as u64) as u32;
            remainder = n % divisor as u64;
        }
        while self.limbs.last() == Some(&0) {
            self.limbs.pop();
        }
        remainder as u32
    }
    pub fn div_rem(&self, divisor: &Self) -> (Self, Self) {
        assert!(!divisor.is_zero(), "unsigned mantissa division by zero");
        if self < divisor {
            return (Self::zero(), self.clone());
        }
        if divisor.limbs.len() == 1 {
            let mut q = self.clone();
            let r = q.div_small_assign(divisor.limbs[0]);
            return (q, Self::from(r as u128));
        }
        // Powers of ten occur in alignment, rounding and truncation. Splitting
        // limbs avoids quadratic long division at the million-digit boundary.
        let exponent = divisor.trailing_zeros();
        if divisor.digits() == exponent + 1
            && divisor
                .limbs
                .last()
                .is_some_and(|&n| n == 10u32.pow((exponent % 9) as u32))
        {
            return self.div_pow10(exponent);
        }
        // Knuth Algorithm D, normalized so the leading divisor limb >= BASE/2.
        let n = divisor.limbs.len();
        let m = self.limbs.len() - n;
        let factor = (BASE / (divisor.limbs[n - 1] as u64 + 1)) as u32;
        let v = divisor.mul_small(factor);
        let mut u = self.mul_small(factor).limbs;
        u.resize(self.limbs.len() + 1, 0);
        let mut quotient = vec![0u32; m + 1];
        for j in (0..=m).rev() {
            let head = u[j + n] as u64 * BASE + u[j + n - 1] as u64;
            let mut qhat = head / v.limbs[n - 1] as u64;
            let mut rhat = head % v.limbs[n - 1] as u64;
            while qhat >= BASE || qhat * v.limbs[n - 2] as u64 > BASE * rhat + u[j + n - 2] as u64 {
                qhat -= 1;
                rhat += v.limbs[n - 1] as u64;
                if rhat >= BASE {
                    break;
                }
            }
            let mut borrow = 0u64;
            for i in 0..n {
                let product = qhat * v.limbs[i] as u64 + borrow;
                let low = product % BASE;
                borrow = product / BASE;
                let limb = u[j + i] as u64;
                if limb < low {
                    u[j + i] = (limb + BASE - low) as u32;
                    borrow += 1;
                } else {
                    u[j + i] = (limb - low) as u32;
                }
            }
            let negative = (u[j + n] as u64) < borrow;
            u[j + n] = ((u[j + n] as u64 + BASE - borrow) % BASE) as u32;
            if negative {
                qhat -= 1;
                let mut carry = 0u64;
                for i in 0..n {
                    let sum = u[j + i] as u64 + v.limbs[i] as u64 + carry;
                    u[j + i] = (sum % BASE) as u32;
                    carry = sum / BASE;
                }
                u[j + n] = ((u[j + n] as u64 + carry) % BASE) as u32;
            }
            quotient[j] = qhat as u32;
        }
        let mut remainder = Self::normalized(u[..n].to_vec());
        remainder.div_small_assign(factor);
        (Self::normalized(quotient), remainder)
    }
}

impl From<u128> for LargeDec {
    fn from(mut n: u128) -> Self {
        let mut limbs = Vec::new();
        while n != 0 {
            limbs.push((n % BASE as u128) as u32);
            n /= BASE as u128;
        }
        Self { limbs }
    }
}
impl Ord for LargeDec {
    fn cmp(&self, rhs: &Self) -> Ordering {
        self.limbs
            .len()
            .cmp(&rhs.limbs.len())
            .then_with(|| self.limbs.iter().rev().cmp(rhs.limbs.iter().rev()))
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
        let limbs = s
            .as_bytes()
            .rchunks(9)
            .map(|chunk| chunk.iter().fold(0u32, |n, b| n * 10 + (b - b'0') as u32))
            .collect();
        Ok(Self::normalized(limbs))
    }
}
impl fmt::Display for LargeDec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut limbs = self.limbs.iter().rev();
        let Some(first) = limbs.next() else {
            return f.write_str("0");
        };
        write!(f, "{first}")?;
        for limb in limbs {
            write!(f, "{limb:09}")?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use num_bigint::BigUint;

    fn oracle(n: &LargeDec) -> BigUint {
        n.to_string().parse().unwrap()
    }
    fn check(a: &LargeDec, b: &LargeDec) {
        let x = oracle(a);
        let y = oracle(b);
        assert_eq!(a.add(b).to_string(), (&x + &y).to_string());
        assert_eq!(a.mul(b).to_string(), (&x * &y).to_string());
        assert_eq!(a.cmp(b), x.cmp(&y));
        if a >= b {
            assert_eq!(a.sub(b).to_string(), (&x - &y).to_string());
        }
        if !b.is_zero() {
            let (q, r) = a.div_rem(b);
            assert_eq!(q.to_string(), (&x / &y).to_string(), "a={a} b={b}");
            assert_eq!(r.to_string(), (&x % &y).to_string(), "a={a} b={b}");
            assert!(r < *b);
            assert_eq!(q.mul(b).add(&r), *a);
        }
    }

    #[test]
    fn limb_arithmetic_matches_independent_binary_integer_oracle() {
        let mut seed = 0x5621_dce4_7012_986fu64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for i in 0..3000 {
            let a_len = next() as usize % 180;
            let b_len = next() as usize % 180;
            let mut make = |len| SelfLike::make(len, &mut next, i % 5 == 0);
            let a = make(a_len);
            let b = make(b_len);
            check(&a, &b);
        }
        // Exercise both sides of the Karatsuba threshold and asymmetric splits.
        for (n, m) in [
            (31, 32),
            (32, 32),
            (32, 64),
            (33, 65),
            (64, 129),
            (1112, 1111),
        ] {
            let a = SelfLike::make(n, &mut next, false);
            let b = SelfLike::make(m, &mut next, false);
            check(&a, &b);
        }
    }
    struct SelfLike;
    impl SelfLike {
        fn make(len: usize, next: &mut impl FnMut() -> u64, edges: bool) -> LargeDec {
            LargeDec::normalized(
                (0..len)
                    .map(|_| {
                        let n = next();
                        if edges {
                            [0, 1, BASE as u32 - 1][n as usize % 3]
                        } else {
                            (n % BASE) as u32
                        }
                    })
                    .collect(),
            )
        }
    }

    #[test]
    fn division_estimate_correction_and_addback_boundaries() {
        for top in [1, 2, BASE as u32 / 2, BASE as u32 - 1] {
            for middle in [0, 1, BASE as u32 - 1] {
                let b = LargeDec::normalized(vec![BASE as u32 - 1, middle, top]);
                for q in [2u128, BASE as u128 - 1, BASE as u128, BASE as u128 + 1] {
                    let product = b.mul(&LargeDec::from(q));
                    for a in [
                        product.sub(&LargeDec::one()),
                        product.clone(),
                        product.add(&b).sub(&LargeDec::one()),
                    ] {
                        check(&a, &b);
                    }
                }
            }
        }
    }

    #[test]
    fn decimal_limb_operations_and_canonical_storage() {
        for text in [
            "0",
            "0000",
            "1",
            "999999999",
            "1000000000",
            "1000000000000000001",
            "340282366920938463463374607431768211455",
            "340282366920938463463374607431768211456",
        ] {
            let a: LargeDec = text.parse().unwrap();
            assert!(a.limbs().iter().all(|&n| (n as u64) < BASE));
            assert_ne!(a.limbs().last(), Some(&0));
            assert_eq!(a.digits(), a.to_string().len());
            assert_eq!(a.to_u128(), text.parse::<u128>().ok());
            for power in 0..=85 {
                let p = LargeDec::pow10(power);
                let mut shifted = a.clone();
                shifted.mul_pow10_assign(power);
                assert_eq!(shifted, a.mul(&p));
                assert_eq!(shifted.div_pow10(power), (a.clone(), LargeDec::zero()));
                assert_eq!(a.div_pow10(power), a.div_rem(&p));
                check(&a, &p);
            }
        }
        for invalid in ["", "-1", "+1", "1.0", "١"] {
            assert!(invalid.parse::<LargeDec>().is_err());
        }
    }
}
