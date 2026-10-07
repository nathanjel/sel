// A ray tracer in SEL, and the host function it needs -- from C++.
//
//   cd cpp && make build/example-raytrace && cd ..
//   cpp/build/example-raytrace                     (from the repository root)
//   cpp/build/example-raytrace --ppm 640 360 2 > mark.ppm
//   cpp/build/example-raytrace --bench report.json
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
// The visible difference here is that C++ has no big integers. SQRT works in
// 128-bit integers whenever the numbers fit, which is every call the scene
// makes, and otherwise by the digit-pair method over a few lines of base-10^9
// arithmetic: slower, and the same digits.

#include "../../cpp/sel.hpp"

#include <bit>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr long long MAX_SCALE = 1000000;   // the cap ROUND's scale has (spec/limits.json)

// EXAMPLE-BEGIN sqrt
// x = m / 10^s. An exact root is taken from x itself, so that its cost depends
// on x and not on n: with the scale made even, x = m2 / 10^(2h) (m2 = m, or 10m
// when s is odd), and sqrt(x) = isqrt(m2) / 10^h exactly when m2 is a perfect
// square -- the answer, once its spare zeros are gone, unless that still leaves
// more than n fractional digits. Every other root is rounded at scale n:
// sqrt(x) * 10^n = sqrt(m * 10^e) with e = 2n - s, which is sqrt(v / p) for
// v = m * 10^e, p = 1 when e >= 0, else v = m, p = 10^-e. With r = isqrt(q) for
// q = v div p, v = q*p + rem and q = r^2 + t (so 0 <= t <= 2r), the rule's
//
//     4*v >= (2r+1)^2 * p     iff  t > r, or t == r and 4*rem >= p    (round up)
//
// because v/p >= (r + 1/2)^2 means t + rem/p >= r + 1/4, and rem < p. The
// right-hand side multiplies nothing wider than q, so 128 bits go further.
// Rounding never meets an exact root that fits in n digits: the first step
// returned it. Both paths below take the same two steps.

using u128 = __uint128_t;

int bit_length(u128 v) {
  const auto hi = static_cast<std::uint64_t>(v >> 64);
  return hi ? 64 + std::bit_width(hi) : std::bit_width(static_cast<std::uint64_t>(v));
}

// Newton's method from above, in integers only: 2^ceil(bits/2) is at least
// sqrt(v), and each step stays at or above isqrt(v) until it stops falling.
template <class U>
U newton_isqrt(U v) {
  if (v < 2) return v;
  U x = U(1) << ((bit_length(v) + 1) / 2);
  for (;;) {
    const U y = (x + v / x) / 2;
    if (y >= x) return x;
    x = y;
  }
}

// In 64 bits, which the CPU divides in one instruction, whenever v fits.
u128 isqrt(u128 v) {
  return v >> 64 ? newton_isqrt(v) : newton_isqrt(static_cast<std::uint64_t>(v));
}

u128 pow10(long long k) {
  u128 p = 1;
  while (k-- > 0) p *= 10;
  return p;
}

sel::Dec small_dec(u128 r, long long scale) {
  sel::Dec d;
  d.small = true;
  d.mantissa = static_cast<sel::dec_mantissa_t>(r);
  d.scale = static_cast<std::int32_t>(scale);
  return d;
}

// The fast path, which every call the scene makes takes: x's magnitude is the
// 128-bit mantissa (Dec::small is set -- otherwise it is held as digits or
// words, which dec_digits() reads), and m2 and q fit in 128 bits too.
std::optional<sel::Dec> small_sqrt(const sel::Dec& x, long long n) {
  if (!x.small) return std::nullopt;
  const u128 m = static_cast<u128>(x.mantissa);   // not negative: checked
  const bool odd = x.scale % 2;
  if (odd && m > ~u128(0) / 10) return std::nullopt;
  const u128 m2 = odd ? 10 * m : m;
  u128 r = isqrt(m2);
  if (r * r == m2) {                              // exact: drop the zeros it does not need
    long long scale = r == 0 ? 0 : (x.scale + odd) / 2;
    while (scale > 0 && r % 10 == 0) {
      r /= 10;
      --scale;
    }
    if (scale <= n) return small_dec(r, scale);
  }
  const long long e = 2 * n - x.scale;
  u128 q, rem = 0, p = 1;
  if (e >= 0) {
    if (e > 38 || m > ~u128(0) / pow10(e)) return std::nullopt;
    q = m * pow10(e);
  } else {
    if (e < -37) return std::nullopt;             // so that 4 * rem fits
    p = pow10(-e);
    q = m / p;
    rem = m % p;
  }
  r = isqrt(q);
  const u128 t = q - r * r;
  if (t > r || (t == r && 4 * rem >= p)) ++r;     // at or past the half: away from zero
  return small_dec(r, n);
}

// A whole number in base 10^9, least significant limb first (zero has none):
// just the arithmetic the digit-pair method needs.
struct Big {
  static constexpr std::uint32_t BASE = 1000000000;
  std::vector<std::uint32_t> limbs;

  void mul_add(const Big& a, std::uint32_t k, std::uint32_t c) {   // *this = a * k + c, k, c <= 200
    limbs.resize(a.limbs.size());                                  // (a may be *this)
    std::uint64_t carry = c;
    for (std::size_t i = 0; i < limbs.size(); ++i) {
      carry += std::uint64_t{a.limbs[i]} * k;
      limbs[i] = static_cast<std::uint32_t>(carry % BASE);
      carry /= BASE;
    }
    if (carry) limbs.push_back(static_cast<std::uint32_t>(carry));
    while (!limbs.empty() && limbs.back() == 0) limbs.pop_back();
  }
  void sub(const Big& b) {                                         // *this -= b, b <= *this
    std::int64_t borrow = 0;
    for (std::size_t i = 0; i < limbs.size(); ++i) {
      std::int64_t d = std::int64_t{limbs[i]} - borrow - (i < b.limbs.size() ? b.limbs[i] : 0);
      borrow = d < 0;
      limbs[i] = static_cast<std::uint32_t>(d + (borrow ? BASE : 0));
    }
    while (!limbs.empty() && limbs.back() == 0) limbs.pop_back();
  }
  friend int compare(const Big& a, const Big& b) {
    if (a.limbs.size() != b.limbs.size()) return a.limbs.size() < b.limbs.size() ? -1 : 1;
    for (std::size_t i = a.limbs.size(); i-- > 0;)
      if (a.limbs[i] != b.limbs[i]) return a.limbs[i] < b.limbs[i] ? -1 : 1;
    return 0;
  }
  std::string digits() const {
    if (limbs.empty()) return "0";
    std::string out = std::to_string(limbs.back());
    for (std::size_t i = limbs.size() - 1; i-- > 0;) {
      const std::string limb = std::to_string(limbs[i]);
      out += std::string(9 - limb.size(), '0') + limb;
    }
    return out;
  }
};

// {r, t}: r = isqrt(q) and t = q - r^2 for q given as decimal digits, by the
// digit-pair method -- long division's cousin: bring down the next pair of q's
// digits, and the next digit of r is the largest x with (20r + x) * x <= t.
// O(d^2) for d digits of q: d/2 steps, each at most ten trial products of
// about d/2 digits.
std::pair<Big, Big> isqrt_digits(const std::string& digits) {
  const std::string q = digits.size() % 2 ? "0" + digits : digits;
  Big r, t, y;
  for (std::size_t at = 0; at < q.size(); at += 2) {
    t.mul_add(t, 100, static_cast<std::uint32_t>((q[at] - '0') * 10 + (q[at + 1] - '0')));
    std::uint32_t x = 10;
    do {
      --x;
      y.mul_add(r, 20 * x, x * x);                // (20r + x) * x
    } while (compare(y, t) > 0);
    t.sub(y);
    r.mul_add(r, 10, x);
  }
  return {std::move(r), std::move(t)};
}

sel::Dec big_dec(std::string digits, long long scale) {
  sel::Dec d;
  d.digits = std::move(digits);
  d.scale = static_cast<std::int32_t>(scale);
  return d;
}

// The general path, for any size, on m's decimal digits.
sel::Dec big_sqrt(const std::string& m, long long s, long long n) {
  const bool odd = s % 2;
  if (auto [r, t] = isqrt_digits(odd ? m + "0" : m); t.limbs.empty()) {
    // exact: drop the zeros it does not need, counted and cut once
    std::string digits = r.digits();
    const long long h = digits == "0" ? 0 : (s + odd) / 2;
    long long zeros = 0;
    while (zeros < h && digits[digits.size() - 1 - static_cast<std::size_t>(zeros)] == '0') ++zeros;
    digits.resize(digits.size() - static_cast<std::size_t>(zeros));
    if (h - zeros <= n) return big_dec(std::move(digits), h - zeros);
  }
  // v div p and v mod p are a shift of the decimal point in m's digits
  const long long e = 2 * n - s;
  std::string q = m, rem;                         // rem / p is 0.<rem>
  if (e >= 0) {
    q.append(static_cast<std::size_t>(e), '0');
  } else if (q.size() > static_cast<std::size_t>(-e)) {
    rem = q.substr(q.size() - static_cast<std::size_t>(-e));
    q.resize(q.size() - static_cast<std::size_t>(-e));
  } else {
    rem = std::string(static_cast<std::size_t>(-e) - q.size(), '0') + q;
    q = "0";
  }
  auto [r, t] = isqrt_digits(q);
  // 4 * rem >= p, i.e. 0.<rem> >= 0.25, which its first two digits decide
  const bool quarter = (rem + "00").compare(0, 2, "25") >= 0;
  if (compare(t, r) > 0 || (compare(t, r) == 0 && quarter)) r.mul_add(r, 1, 1);   // away from zero
  return big_dec(r.digits(), n);
}

sel::Value sqrt_fn(sel::HostArgs& args) {
  const sel::Dec x = args.decimal(0);
  const long long n = args.count() > 1 ? args.non_neg_int(1) : 10;
  if (n > MAX_SCALE) throw sel::SelError("E_RANGE", "SQRT: scale above 1000000", args.pos_of(1));
  if (x.neg) throw sel::SelError("E_RANGE", "SQRT of a negative number", args.pos_of(0));
  if (const auto root = small_sqrt(x, n)) return sel::Value::num(*root);
  return sel::Value::num(big_sqrt(sel::dec_digits(x), x.scale, n));
}

void register_sqrt() { sel::register_function("SQRT", 1, 2, sqrt_fn); }
// EXAMPLE-END sqrt

// Left-pad to a fixed width, so this file's columns line up with the
// files written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string read(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("cannot read " + path);
  std::ostringstream text;
  text << in.rdbuf();
  return text.str();
}

// The context raytrace.sel reads: the size and the samples, as text.
sel::Value frame_context(const std::string& w, const std::string& h, const std::string& ss) {
  sel::Value context = sel::Value::none();
  context.set("W", sel::Value::text(w));
  context.set("H", sel::Value::text(h));
  context.set("SS", sel::Value::text(ss));
  return context;
}

std::string frame(const sel::Program& scene, const std::string& w, const std::string& h,
                  const std::string& ss) {
  sel::Value context = frame_context(w, h, ss);
  return scene.run(context).as_text();
}

std::string crc32(const sel::Program& crc, const std::string& img) {
  sel::Value context = sel::Value::none();
  context.set("IMG", sel::Value::text(img));
  return crc.run(context).as_text();
}

int bench(const sel::Program& scene, const std::string& report);

}  // namespace

int main(int argc, char** argv) {
  const std::vector<std::string> args(argv + 1, argv + argc);
  register_sqrt();
  const sel::Program scene = sel::compile(read("examples/raytrace/raytrace.sel"));
  if (!args.empty() && args[0] == "--ppm" && args.size() == 4) {
    std::cout << frame(scene, args[1], args[2], args[3]);
    return 0;
  }
  if (!args.empty() && args[0] == "--bench" && args.size() == 2) return bench(scene, args[1]);
  if (!args.empty()) {
    std::cerr << "usage: example-raytrace [--ppm W H SS | --bench REPORT.json]\n";
    return 2;
  }

  std::cout << "1. SQRT, the one function the ray tracer needs from the host\n";
  for (const std::string src : {"SQRT(2)", "SQRT(2, 40)", "SQRT(2.25)", "SQRT(1000000, 3)", "SQRT(0.000)",
                                "SQRT(6.25, 3000)", "SQRT(0.0025, 1)", "SQRT(0.0225, 1)", "SQRT(99.999999, 2)",
                                "SQRT(POWER(12345678901234567890, 2))", "SQRT(POWER(10, 41) + 1, 3)",
                                "SQRT(-4)", "SQRT(\"four\")", "SQRT(4, -1)", "SQRT(4, 0.5)", "SQRT(4, 1000001)"}) {
    std::string result;
    try {
      result = sel::compile(src).run().as_text();
    } catch (const sel::SelError& e) {
      result = e.code() + " at " + std::to_string(e.line()) + ":" + std::to_string(e.col());
    }
    std::cout << "   " << pad(src, 38) << " => " << result << "\n";
  }

  std::cout << "2. the scene, 64 x 36, one ray per pixel\n";
  const std::string img = frame(scene, "64", "36", "1");
  const std::string crc = crc32(sel::compile("CRC32(IMG)"), img);
  std::cout << "   " << img.size() << " bytes of PPM, CRC32 " << crc << "\n";
  return 0;
}

namespace {

int env_int(const char* name, int fallback) {
  const char* text = std::getenv(name);
  return text ? std::atoi(text) : fallback;
}

// The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
// pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5).
int bench(const sel::Program& scene, const std::string& report) {
  using Clock = std::chrono::steady_clock;
  const int warmups = env_int("RAYTRACE_WARMUPS", 2);
  const int runs = env_int("RAYTRACE_RUNS", 5);
  const sel::Program crc = sel::compile("CRC32(IMG)");
  std::vector<double> samples;
  std::vector<std::string> outputs;
  for (int i = 0; i < warmups + runs; ++i) {
    sel::Value context = frame_context("64", "36", "1");
    const auto t = Clock::now();
    const std::string img = scene.run(context).as_text();
    const double elapsed = std::chrono::duration<double, std::milli>(Clock::now() - t).count();
    if (i >= warmups) {
      samples.push_back(elapsed);
      outputs.push_back(crc32(crc, img));
    }
  }
  // No JSON library: the report is four keys, written by hand.
  std::ofstream out(report);
  if (!out) throw std::runtime_error("cannot write " + report);
  out << std::fixed << std::setprecision(3) << "{\"samples_ms\": [";
  for (std::size_t i = 0; i < samples.size(); ++i) out << (i ? ", " : "") << samples[i];
  out << "], \"outputs\": [";
  for (std::size_t i = 0; i < outputs.size(); ++i) out << (i ? ", " : "") << '"' << outputs[i] << '"';
  out << "], \"warmups\": " << warmups << ", \"runs\": " << runs << "}";
  return 0;
}

}  // namespace
