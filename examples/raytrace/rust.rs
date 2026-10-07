// A ray tracer in SEL, and the host function it needs -- from Rust.
//
//   bash rust/build.sh && rust/build/example-raytrace       (from the repository root)
//   rust/build/example-raytrace --ppm 640 360 2 > mark.ppm
//   rust/build/example-raytrace --bench report.json
//
// raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
// root has no exact decimal result -- so the application gives it one: SQRT(x, n)
// is the square root of x to n fractional digits (10 if n is left out). Like
// `/`, it is exact when it can be: a root with at most n fractional digits comes
// back at its minimal scale, and any other is rounded half away from zero to
// exactly n. It is computed on whole numbers, so every host gives every digit
// the same. For x = m / 10^s, x has an exact root when m, with the scale made
// even, is a perfect square -- which costs what x's size costs, whatever n is.
// Any other root is rounded from
//
//     sqrt(x) * 10^n = sqrt(m * 10^(2n - s))
//
// whose integer square root is the truncated answer; one comparison of whole
// numbers decides the rounding.
//
// With no arguments it prints a few square roots and a small frame; --ppm prints
// a frame of any size as PPM, and --bench times the frame the benchmarks use.
// The files beside this one print byte-identical output.
//
// The visible difference here: Rust's standard library has no integer wider than
// u128, so SQRT has two paths. When every number in the computation fits a u128
// -- every call the scene makes -- it runs on u128s. Anything larger takes the
// root the way it is taken on paper, two decimal digits at a time, which needs
// no division and has no size limit.
// And Rust, like C++, has no from_native, so each context is built key by key.

use std::cmp::Ordering;
use std::env;
use std::error::Error;
use std::fs;
use std::io::{self, Write};
use std::process::ExitCode;
use std::time::Instant;

use sel_lang::{
    compile, dec_format, dec_parse, evaluate, register_function, Args, Dec, Pos, Program, SelError,
    Value,
};

const MAX_SCALE: usize = sel_lang::limits::MAX_ROUND_SCALE; // the cap ROUND's scale has (spec/limits.json)

// EXAMPLE-BEGIN sqrt
fn sqrt(args: &mut Args) -> Result<Value, SelError> {
    let x = args.dec(0)?;
    let n = if args.count() > 1 { args.non_neg_int(1)? } else { 10 };
    if n as usize > MAX_SCALE {
        return Err(SelError::range(format!("SQRT: scale above {MAX_SCALE}"), args.pos_of(1)?));
    }
    if x.is_negative() {
        return Err(SelError::range("SQRT of a negative number", args.pos_of(0)?));
    }
    // x = m / 10^s. Every operand the scene passes has an m that fits a u128; a
    // larger one goes by its digits.
    let text = dec_format(&x);
    if let Ok(m) = text.replace('.', "").parse::<u128>() {
        if let Some(root) = sqrt_u128(m, x.scale(), n as u32) {
            return Value::num(root);
        }
    }
    Value::num(dec_parse(&sqrt_digits(&text, x.scale(), n as u32), args.pos())?)
}

/// SQRT on u128s; None when a number on the way does not fit.
fn sqrt_u128(m: u128, s: u32, n: u32) -> Option<Dec> {
    // An exact root is taken from x itself, so that what it costs depends on x,
    // not on n: at an even scale s2, sqrt(m2 / 10^s2) = isqrt(m2) / 10^(s2 / 2).
    let (m2, s2) = if s.is_multiple_of(2) { (m, s) } else { (m.checked_mul(10)?, s + 1) };
    let (mut r, mut t) = (isqrt(m2), s2 / 2);
    if r * r == m2 {
        while t > 0 && r.is_multiple_of(10) {   // drop the zeros it does not need
            r /= 10;
            t -= 1;
        }
        if t <= n {
            return Some(Dec::from_small(false, r as i128, t));
        }
    }
    // Any other root is rounded at scale n: sqrt(x) * 10^n = sqrt(m * 10^e) with
    // e = 2n - s; when e is negative that is sqrt(m / 10^-e), whose integer part
    // is isqrt(m / 10^-e)
    let e = 2 * i64::from(n) - i64::from(s);
    let (v, p) = if e >= 0 {
        (m.checked_mul(10u128.checked_pow(u32::try_from(e).ok()?)?)?, 1)
    } else {
        (m, 10u128.checked_pow(u32::try_from(-e).ok()?)?)
    };
    let mut r = isqrt(v / p);
    if v.checked_mul(4)? >= (2 * r + 1).checked_pow(2)?.checked_mul(p)? {
        r += 1;                                 // at or past the half: away from zero
    }
    Some(Dec::from_small(false, r as i128, n))
}

/// Newton's method from above, in integers: 2^ceil(bits / 2) >= sqrt(v).
fn isqrt(v: u128) -> u128 {
    if v < 2 {
        return v;
    }
    let mut x = 1u128 << (128 - v.leading_zeros()).div_ceil(2);
    loop {
        let y = (x + v / x) / 2;
        if y >= x {
            return x;
        }
        x = y;
    }
}

/// The same two steps for any operand, on decimal digits (`x` is x's text),
/// with isqrt_digits below for isqrt; the result is written out as text.
fn sqrt_digits(x: &str, s: u32, n: u32) -> String {
    let (s, n) = (s as usize, n as usize);
    let mut m: Vec<u8> = x.bytes().filter(u8::is_ascii_digit).map(|b| b - b'0').collect();
    if m.iter().all(|&d| d == 0) {
        return "0".to_string();                 // 0 is its own root, every zero dropped
    }
    // The exact root, from x itself: m2 is m at the even scale s + s % 2.
    let mut m2 = m.clone();
    m2.resize(m.len() + s % 2, 0);
    let (mut r, square) = isqrt_digits(&m2);
    let t = s.div_ceil(2);
    if square {
        let zeros = r.iter().rev().take_while(|&&d| d == 0).count().min(t);
        if t - zeros <= n {
            r.truncate(r.len() - zeros);
            return decimal(&r, t - zeros);
        }
    }
    // Any other root, rounded at scale n. One root digit more than the answer
    // needs decides it: the root of v * 100 / p = m * 10^(2n - s + 2), cut to a
    // whole number, is 10r + d, and d >= 5 is the comparison 4v >= (2r + 1)^2 p.
    match usize::try_from(2 * n as i64 - s as i64 + 2) {
        Ok(zeros) => m.resize(m.len() + zeros, 0),
        Err(_) => m.truncate((m.len() + 2 * n + 2).saturating_sub(s)),
    }
    let (mut r, _) = isqrt_digits(&m);
    if r.pop().unwrap_or(0) >= 5 {
        match r.iter().rposition(|&d| d != 9) { // r += 1, on its digits
            Some(i) => {
                r[i] += 1;
                r[i + 1..].fill(0);
            }
            None => {
                r.fill(0);
                r.insert(0, 1);
            }
        }
    }
    decimal(&r, n)
}

/// r / 10^scale written out, for r's digits, most significant first.
fn decimal(r: &[u8], scale: usize) -> String {
    let mut text: String = r.iter().map(|&d| char::from(b'0' + d)).collect();
    if text.len() <= scale {
        text.insert_str(0, &"0".repeat(scale + 1 - text.len()));
    }
    if scale > 0 {
        text.insert(text.len() - scale, '.');
    }
    text
}

/// isqrt of a whole number written in decimal, as on paper: bring the digits
/// down two at a time, and each next root digit is the largest y with
/// (20 * root + y) * y <= remainder. Returns the root's digits (none for 0)
/// and whether the remainder is zero. A d-digit root takes d steps on numbers
/// of about 2d digits: O(d^2), with no division.
fn isqrt_digits(digits: &[u8]) -> (Vec<u8>, bool) {
    let first = digits.iter().position(|&d| d != 0).unwrap_or(digits.len());
    let mut padded = vec![0; (digits.len() - first) % 2];
    padded.extend_from_slice(&digits[first..]);
    let (mut root, mut remainder, mut out) = (Vec::new(), Vec::new(), Vec::new());
    for pair in padded.chunks(2) {
        remainder = mul_add(&remainder, 100, u64::from(pair[0] * 10 + pair[1]));
        let take = |y| mul_add(&mul_add(&root, 20, y), y, 0);
        let y = (1..=9).rev().find(|&y| cmp(&take(y), &remainder) != Ordering::Greater).unwrap_or(0);
        sub(&mut remainder, &take(y));
        root = mul_add(&root, 10, y);
        out.push(y as u8);
    }
    (out, remainder.is_empty())
}

// Whole numbers as base-10^9 limbs, least significant first, no high zero limb.
const BASE: u64 = 1_000_000_000;

/// a * k + c, for small k and c.
fn mul_add(a: &[u64], k: u64, c: u64) -> Vec<u64> {
    let mut out = Vec::with_capacity(a.len() + 1);
    let mut carry = c;
    for &limb in a {
        let t = limb * k + carry;
        out.push(t % BASE);
        carry = t / BASE;
    }
    while carry > 0 {
        out.push(carry % BASE);
        carry /= BASE;
    }
    while out.last() == Some(&0) {
        out.pop();
    }
    out
}

fn cmp(a: &[u64], b: &[u64]) -> Ordering {
    a.len().cmp(&b.len()).then_with(|| a.iter().rev().cmp(b.iter().rev()))
}

/// a -= b, for a >= b.
fn sub(a: &mut Vec<u64>, b: &[u64]) {
    let mut borrow = 0;
    for (i, limb) in a.iter_mut().enumerate() {
        let take = b.get(i).copied().unwrap_or(0) + borrow;
        borrow = u64::from(*limb < take);
        *limb = *limb + borrow * BASE - take;
    }
    while a.last() == Some(&0) {
        a.pop();
    }
}

fn register_sqrt() -> Result<(), SelError> {
    register_function("SQRT", 1, 2, sqrt)
}
// EXAMPLE-END sqrt

fn read(name: &str) -> Result<String, Box<dyn Error>> {
    Ok(fs::read_to_string(format!("examples/raytrace/{name}"))?)
}

/// A context of text values, set key by key.
fn context(pairs: &[(&str, &str)]) -> Result<Value, SelError> {
    let ctx = Value::none();
    for &(key, text) in pairs {
        ctx.set(key, Value::text_owned(text.to_string()), Pos::default())?;
    }
    Ok(ctx)
}

fn frame(scene: &mut Program, w: &str, h: &str, ss: &str) -> Result<String, SelError> {
    scene.run(Some(context(&[("W", w), ("H", h), ("SS", ss)])?))?.as_text(Pos::default())
}

fn main() -> Result<ExitCode, Box<dyn Error>> {
    register_sqrt()?;
    let mut scene = compile(&read("raytrace.sel")?)?;
    let argv: Vec<String> = env::args().skip(1).collect();
    match argv.iter().map(String::as_str).collect::<Vec<_>>()[..] {
        ["--ppm", w, h, ss] => {
            io::stdout().write_all(frame(&mut scene, w, h, ss)?.as_bytes())?;
            return Ok(ExitCode::SUCCESS);
        }
        ["--bench", report] => {
            bench(&mut scene, report)?;
            return Ok(ExitCode::SUCCESS);
        }
        [] => {}
        _ => {
            eprintln!("usage: example-raytrace [--ppm W H SS | --bench REPORT.json]");
            return Ok(ExitCode::from(2));
        }
    }

    let at = Pos::default();
    println!("1. SQRT, the one function the ray tracer needs from the host");
    for src in ["SQRT(2)", "SQRT(2, 40)", "SQRT(2.25)", "SQRT(1000000, 3)", "SQRT(0.000)",
                "SQRT(6.25, 3000)", "SQRT(0.0025, 1)", "SQRT(0.0225, 1)", "SQRT(99.999999, 2)",
                "SQRT(POWER(12345678901234567890, 2))", "SQRT(POWER(10, 41) + 1, 3)",
                "SQRT(-4)", "SQRT(\"four\")", "SQRT(4, -1)", "SQRT(4, 0.5)", "SQRT(4, 1000001)"] {
        let result = match evaluate(src, None).and_then(|v| v.as_text(at)) {
            Ok(text) => text,
            Err(e) => format!("{} at {}:{}", e.code, e.pos.line, e.pos.col),
        };
        println!("   {src:<38} => {result}");
    }

    println!("2. the scene, 64 x 36, one ray per pixel");
    let img = frame(&mut scene, "64", "36", "1")?;
    let crc = evaluate("CRC32(IMG)", Some(context(&[("IMG", &img)])?))?.as_text(at)?;
    println!("   {} bytes of PPM, CRC32 {crc}", img.len());
    Ok(ExitCode::SUCCESS)
}

/// The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
/// pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5).
fn bench(scene: &mut Program, report: &str) -> Result<(), Box<dyn Error>> {
    let count = |name: &str, fallback: usize| {
        env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(fallback)
    };
    let (warmups, runs) = (count("RAYTRACE_WARMUPS", 2), count("RAYTRACE_RUNS", 5));
    let mut crc = compile("CRC32(IMG)")?;
    let (mut samples, mut outputs) = (Vec::new(), Vec::new());
    for i in 0..warmups + runs {
        let ctx = context(&[("W", "64"), ("H", "36"), ("SS", "1")])?;
        let t = Instant::now();
        let img = scene.run(Some(ctx))?.as_text(Pos::default())?;
        let elapsed = t.elapsed().as_secs_f64() * 1000.0;
        if i >= warmups {
            samples.push(format!("{elapsed:?}"));
            let sum = crc.run(Some(context(&[("IMG", &img)])?))?.as_text(Pos::default())?;
            outputs.push(format!("\"{sum}\""));
        }
    }
    // Written by hand, as there is no JSON in std: the values are numbers and hex digits.
    let json = format!(
        "{{\"samples_ms\": [{}], \"outputs\": [{}], \"warmups\": {warmups}, \"runs\": {runs}}}",
        samples.join(", "),
        outputs.join(", ")
    );
    fs::write(report, json)?;
    Ok(())
}
