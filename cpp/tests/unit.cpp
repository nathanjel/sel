// Unit tests for the layers underneath the conformance suite.
//
// conformance/ is what proves this implementation correct, and it is normative;
// nothing here duplicates it. These tests cover the internals a conformance
// failure would only point at indirectly — the UTF-8 codec, the decimal core,
// the value model's ordering and dump — plus the public host interface, which
// the conformance runner exercises only in one shape.
//
// Including sel.cpp is how the test reaches the internals; it links as its own
// binary. See cpp/Makefile.

#include "../sel.cpp"

#include <atomic>
#include <chrono>
#include <thread>
#include <filesystem>
#include <fstream>
#include <algorithm>
#include <sstream>

#include "harness.hpp"

using namespace sel;

namespace {

std::string dump_of(const std::string& src) { return compile(src).run().dump(); }

void test_utf8() {
  selt::section("utf8");

  selt::eq(decode_utf8("").size(), 0u, "empty decodes to nothing");
  selt::eq(decode_utf8("abc").size(), 3u, "ascii is one code point per byte");
  selt::eq(decode_utf8("Zażółć").size(), 6u, "latin-2 letters are one code point each");
  selt::eq(decode_utf8("👍").size(), 1u, "an astral character is one code point, not two");
  selt::eq(static_cast<unsigned>(decode_utf8("👍")[0]), 0x1f44du, "astral round trip");

  // Strict decoding: none of these may become U+FFFD.
  selt::raises("E_UTF8", [] { decode_utf8("\xc0\x80"); }, "overlong two-byte NUL");
  selt::raises("E_UTF8", [] { decode_utf8("\xe0\x80\x80"); }, "overlong three-byte");
  selt::raises("E_UTF8", [] { decode_utf8("\xed\xa0\x80"); }, "surrogate encoded as CESU-8");
  selt::raises("E_UTF8", [] { decode_utf8("\xf4\x90\x80\x80"); }, "above U+10FFFF");
  selt::raises("E_UTF8", [] { decode_utf8("\xe2\x82"); }, "truncated sequence");
  selt::raises("E_UTF8", [] { decode_utf8("\x80"); }, "lone continuation byte");

  const CodePoints cps = decode_utf8("héllo👍");
  selt::eq(encode_utf8(cps), std::string("héllo👍"), "encode is the inverse of decode");

  // Bytewise, not the host's native order: UTF-16 order disagrees above U+FFFF.
  selt::ok(bytes_compare("\xef\xbf\xbd", "\xf0\x9f\x91\x8d") < 0,
           "U+FFFD sorts before U+1F44D bytewise");
  selt::eq(to_hex(std::string("\x00\xff\x10", 3)), std::string("00ff10"), "hex is lower case");
}

void test_utf8_source_positions() {
  selt::section("utf8 source positions");

  // SPEC §2: E_UTF8 in source is at the first invalid unit, counted in code
  // points of the valid prefix; a line ends at LF only.
  auto where = [](const std::string& src) {
    try {
      compile(src);
    } catch (const SelError& e) {
      return e.code() + " " + std::to_string(e.pos().line) + ":" +
             std::to_string(e.pos().col) + ":" + std::to_string(e.pos().offset);
    }
    return std::string("no error");
  };
  selt::eq(where("\xff"), std::string("E_UTF8 1:1:0"), "a bare invalid byte");
  selt::eq(where("\"a\xff" "b\""), std::string("E_UTF8 1:3:2"), "inside a literal");
  selt::eq(where("1 +\n \"a\xff" "b\""), std::string("E_UTF8 2:4:7"), "on the second line");
  selt::eq(where("\"\xc5\x82\xff\""), std::string("E_UTF8 1:3:2"),
           "the prefix counts a multibyte character once");
  selt::eq(where("\"\xe2\x82"), std::string("E_UTF8 1:2:1"), "truncated at end of input");
  selt::eq(where("\"\xc0\x80\""), std::string("E_UTF8 1:2:1"), "overlong encoding");
  selt::eq(where("\"\xed\xa0\x80\""), std::string("E_UTF8 1:2:1"), "encoded surrogate");
  selt::eq(where("\"\xf4\x90\x80\x80\""), std::string("E_UTF8 1:2:1"), "above U+10FFFF");
  selt::eq(where("a\r\nb\rc \xff"), std::string("E_UTF8 2:5:7"),
           "CR is not a line end, LF is");
  selt::eq(where("\"\xe2\x28\xa1\""), std::string("E_UTF8 1:2:1"),
           "a bad continuation is reported at the start of its sequence");
  selt::eq(where("\"\xc5\x82\""), std::string("no error"), "valid multibyte source compiles");
}

void test_decimal() {
  selt::section("decimal");

  auto fmt = [](const std::string& s) {
    Dec d;
    return dec_parse(s, d) ? dec_format(d) : std::string("<not a number>");
  };

  // Canonical form: leading zeros go, trailing fraction zeros stay, zero is
  // never negative. Scale is part of the value.
  selt::eq(fmt("007"), std::string("7"), "leading zeros are dropped");
  selt::eq(fmt("2.50"), std::string("2.50"), "trailing fraction zeros are kept");
  selt::eq(fmt("-0.00"), std::string("0.00"), "zero never carries a minus");
  selt::eq(fmt(" 2"), std::string("<not a number>"), "no implicit trimming");
  selt::eq(fmt("1."), std::string("<not a number>"), "a trailing dot is not a number");
  selt::eq(fmt(".5"), std::string("<not a number>"), "a leading dot is not a number");
  selt::eq(fmt("1e3"), std::string("<not a number>"), "no exponent notation");

  // The Pos is part of the signature, not just of the call: dec_add and dec_mul
  // report E_RANGE against a position when a result exceeds the value cap, and a
  // default argument does not shrink a function's TYPE for taking its address.
  // Naming two parameters here is what let this file stop compiling silently
  // while a stale build/unit went on reporting 81 green checks.
  auto bin = [](const char* a, const char* b, Dec (*op)(const Dec&, const Dec&, Pos)) {
    Dec x, y;
    dec_parse(a, x);
    dec_parse(b, y);
    return dec_format(op(x, y, Pos{}));
  };

  selt::eq(bin("2.50", "2.50", dec_add), std::string("5.00"), "money keeps its cents");
  selt::eq(bin("1.5", "1.5", dec_mul), std::string("2.25"), "* adds the scales");
  selt::eq(bin("0.1", "0.2", dec_add), std::string("0.3"), "no binary floating point here");
  Dec neg_a, neg_b, neg_c;
  dec_parse("-2", neg_a);
  dec_parse("-3", neg_b);
  dec_parse("-2.5", neg_c);
  selt::eq(dec_cmp(neg_a, neg_b), 1, "signed mantissa comparison orders negatives");
  selt::eq(dec_cmp(neg_a, neg_c), 1, "scaled signed mantissa comparison orders negatives");

  auto div = [](const char* a, const char* b) {
    Dec x, y;
    dec_parse(a, x);
    dec_parse(b, y);
    return dec_format(dec_div(x, y));
  };
  selt::eq(div("4", "2"), std::string("2"), "an exact quotient is minimal-scale");
  selt::eq(div("10", "4"), std::string("2.5"), "exact with a fraction");
  selt::eq(div("1", "3"), std::string("0.3333333333"), "inexact runs to DIV_SCALE");
  selt::eq(div("2", "3"), std::string("0.6666666667"), "inexact rounds half away from zero");
  selt::raises("E_DIV_ZERO", [&] { div("1", "0"); }, "division by zero");

  auto mod = [](const char* a, const char* b) {
    Dec x, y;
    dec_parse(a, x);
    dec_parse(b, y);
    return dec_format(dec_mod(x, y));
  };
  selt::eq(mod("5", "3"), std::string("2"), "remainder");
  selt::eq(mod("-5", "3"), std::string("-2"), "% takes the sign of the dividend");
  selt::eq(mod("5.5", "2"), std::string("1.5"), "% keeps the wider scale");

  auto rnd = [](const char* a, int n) {
    Dec x;
    dec_parse(a, x);
    return dec_format(dec_round(x, n));
  };
  selt::eq(rnd("2.5", 0), std::string("3"), "half away from zero, up");
  selt::eq(rnd("-2.5", 0), std::string("-3"), "half away from zero, down");
  selt::eq(rnd("2.4", 0), std::string("2"), "below half");
  selt::eq(rnd("1", 2), std::string("1.00"), "rounding up in scale pads");
}

// The decimal core's native-mantissa edges. Each of these was a wrong answer or
// undefined behaviour in the __int128 fast path and is right in the large path;
// tools/check-decimal.sh and conformance/24-decimal-boundaries.selt cover the
// same ground through the oracle, these hold the internals directly.
// CPP-P1: multi-word division (Knuth algorithm D, or Barrett for long divisors,
// on the binary words). Checked against an independent digit-by-digit reference,
// on random operands and on the shapes that trigger D's rare steps: a divisor
// whose leading digits are small (a large normalising shift), all-nines
// operands, and quotient digits that need the add-back correction.
std::string ref_strip(std::string s) {
  size_t i = 0;
  while (i + 1 < s.size() && s[i] == '0') ++i;
  return s.substr(i);
}
int ref_cmp(const std::string& a, const std::string& b) {
  if (a.size() != b.size()) return a.size() < b.size() ? -1 : 1;
  return a.compare(b) < 0 ? -1 : (a == b ? 0 : 1);
}
std::string ref_sub(const std::string& a, const std::string& b) {
  std::string r(a.size(), '0');
  int borrow = 0;
  for (size_t i = 0; i < a.size(); ++i) {
    int x = a[a.size() - 1 - i] - '0' - borrow;
    int y = i < b.size() ? b[b.size() - 1 - i] - '0' : 0;
    if (x < y) { x += 10; borrow = 1; } else { borrow = 0; }
    r[a.size() - 1 - i] = static_cast<char>('0' + (x - y));
  }
  return ref_strip(r);
}
std::string ref_mod(const std::string& a, const std::string& b) {
  std::string rem = "0";
  for (char c : a) {
    rem = ref_strip(rem + c);
    while (ref_cmp(rem, b) >= 0) rem = ref_sub(rem, b);
  }
  return rem;
}

void test_knuth_division() {
  selt::section("multi-word division (CPP-P1)");
  auto parse = [](const std::string& s) {
    Dec d;
    dec_parse(s.c_str(), d);
    return d;
  };
  uint64_t seed = 88172645463325252ULL;
  auto next = [&]() {
    seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
    return seed;
  };
  auto digits = [&](size_t n, int shape) {
    std::string s;
    for (size_t i = 0; i < n; ++i) {
      char c = static_cast<char>('0' + next() % 10);
      if (shape == 1) c = '9';
      if (shape == 2) c = (i == 0 || i + 1 == n) ? '1' : '0';
      if (shape == 3) c = (i / 9) % 2 == 0 ? '9' : '0';
      s.push_back(c);
    }
    if (s[0] == '0') s[0] = '1';
    return s;
  };
  int bad_mod = 0, bad_exact = 0, total = 0;
  for (int iter = 0; iter < 600; ++iter) {
    size_t lb = 10 + next() % 70, la = lb + next() % 80;
    int shape = static_cast<int>(next() % 4);
    std::string b = digits(lb, shape), a = digits(la, static_cast<int>(next() % 4));
    if (iter % 7 == 0) b = "5" + std::string(lb - 1, '0') + "7";      // small leading digits
    if (iter % 11 == 0) b = std::string(lb, '9');
    ++total;
    std::string want = ref_mod(a, b);
    if (dec_format(dec_mod(parse(a), parse(b))) != want) ++bad_mod;
    // exact multiples divide exactly
    Dec c = parse(digits(1 + next() % 40, static_cast<int>(next() % 4)));
    Dec prod = dec_mul(parse(b), c);
    if (dec_format(dec_div(prod, parse(b))) != dec_format(c)) ++bad_exact;
  }
  selt::eq(bad_mod, 0, "dec_mod agrees with the digit-by-digit reference on " + std::to_string(total) + " operand pairs");
  selt::eq(bad_exact, 0, "an exact multiple divides exactly (quotient is the multiplier)");
  // The shapes that once cost minutes: big by big.
  Dec big = dec_power(parse("9"), 6000);
  Dec small = dec_power(parse("7"), 3000);
  Dec q = dec_div(big, small);
  selt::eq(dec_format(dec_mul(q, small)).size() + 12 >= dec_format(big).size(), true, "big/big quotient times divisor is within rounding of the dividend");
}

// A digit-string product, the algorithm a child learns: the independent
// reference the binary engine's products and conversions are held to.
std::string ref_mul(const std::string& a, const std::string& b) {
  std::vector<int> acc(a.size() + b.size(), 0);
  for (size_t i = 0; i < a.size(); ++i) {
    for (size_t j = 0; j < b.size(); ++j) {
      acc[i + j + 1] += (a[i] - '0') * (b[j] - '0');
    }
  }
  for (size_t k = acc.size(); k-- > 1;) {
    acc[k - 1] += acc[k] / 10;
    acc[k] %= 10;
  }
  std::string out;
  for (int d : acc) out.push_back(static_cast<char>('0' + d));
  return ref_strip(out);
}

// The binary engine (bn::, transcribed from rust/src/large_dec.rs), held to
// itself and to digit strings at and across every threshold: Karatsuba to the
// schoolbook rows, squaring to a plain product, Barrett (fresh and cached
// reciprocals) to Knuth D, the Newton reciprocal to its definition, decimal text
// both ways to ref_mul and to slicing the digit string. And a product or power
// past the digit caps is refused before it is computed, with the code and
// position the guard would have reported.
void test_karatsuba_and_early_range() {
  selt::section("binary engine and early E_RANGE");
  uint64_t seed = 0x9e3779b97f4a7c15ULL;
  auto next = [&]() { seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17; return seed; };
  // About n words: random, all ones, or zero-holed (the carry-heavy shapes).
  auto words = [&](size_t n, int shape) {
    bn::Nat v(n);
    for (auto& x : v) {
      x = next();
      if (shape == 1) x = ~std::uint64_t{0};
      if (shape == 2 && next() % 3 == 0) x = 0;
    }
    bn::normalize(v);
    return v;
  };
  auto school = [](const bn::Nat& a, const bn::Nat& b) {
    if (a.empty() || b.empty()) return bn::Nat{};
    bn::Nat out(a.size() + b.size() + 1, 0);
    if (a.size() >= b.size()) bn::basecase_mul(out, a, b); else bn::basecase_mul(out, b, a);
    bn::normalize(out);
    return out;
  };
  int bad_mul = 0, bad_sqr = 0, products = 0;
  for (int it = 0; it < 150; ++it) {
    const size_t sizes[] = {1, 2, 31, 32, 33, 47, 48, 49, 64, 65, 97, 130, 200, 333};
    const size_t na = it < 14 ? sizes[it] : 1 + next() % 340;
    const size_t nb = it < 14 ? sizes[13 - it] : 1 + next() % 340;
    const bn::Nat a = words(na, static_cast<int>(next() % 3)), b = words(nb, static_cast<int>(next() % 3));
    ++products;
    if (bn::mul(a, b) != school(a, b)) ++bad_mul;
    if (bn::sqr(a) != school(a, a)) ++bad_sqr;
  }
  for (auto [na, nb] : {std::pair<size_t, size_t>{40, 700}, {33, 1000}, {900, 901}, {1100, 1000}}) {
    const bn::Nat a = words(na, 0), b = words(nb, 0);
    ++products;
    if (bn::mul(a, b) != school(a, b)) ++bad_mul;
  }
  const bn::Nat wide = words(1500, 0);
  if (bn::sqr(wide) != school(wide, wide)) ++bad_sqr;
  selt::eq(bad_mul, 0, "Karatsuba equals the schoolbook rows on " + std::to_string(products) + " products (unbalanced too)");
  selt::eq(bad_sqr, 0, "the squaring variants equal a plain product, to 1,500 words");

  // Division: Knuth D and Barrett (fresh reciprocal) agree, and q v + r = u, r < v.
  int bad_div = 0, divisions = 0;
  for (auto [n, m] : {std::pair<size_t, size_t>{2, 3}, {40, 20}, {64, 64}, {100, 33}, {160, 160}, {170, 400}, {300, 150}, {400, 399}}) {
    for (int k = 0; k < 3; ++k) {
      bn::Nat v = words(n, static_cast<int>(next() % 3));
      v.resize(n, 5);
      const bn::Nat u = words(n + m, static_cast<int>(next() % 3));
      if (u.size() < v.size()) continue;
      ++divisions;
      const auto [q1, r1] = bn::knuth_div(u, v);
      const auto [q2, r2] = bn::barrett_div(u, bn::make_recip(v));
      if (q1 != q2 || r1 != r2) ++bad_div;
      if (bn::add(bn::mul(q1, v), r1) != u || bn::cmp(r1, v) >= 0) ++bad_div;
    }
  }
  // u = v (B^k - 1) + (v - 1): every quotient word is B - 1, so the partial
  // remainders' top word meets the divisor's and Knuth's estimate takes its
  // B - 1 branch (rare with random words; this shape hits it every time).
  for (int it = 0; it < 200; ++it) {
    bn::Nat v = words(2 + next() % 60, 0);
    if (v.size() < 2) v.resize(2, 1);
    if (it % 2) v.back() = ~std::uint64_t{0} - next() % 4;
    const bn::Nat ones(1 + next() % 8, ~std::uint64_t{0});
    const bn::Nat u = bn::add(bn::mul(v, ones), bn::sub(v, bn::Nat{1}));
    ++divisions;
    const auto [q1, r1] = bn::knuth_div(u, v);
    const auto [q2, r2] = bn::barrett_div(u, bn::make_recip(v));
    if (q1 != ones || q1 != q2 || r1 != r2) ++bad_div;
    if (bn::add(bn::mul(q1, v), r1) != u || bn::cmp(r1, v) >= 0) ++bad_div;
  }
  selt::eq(bad_div, 0, "Barrett equals Knuth D, and q v + r = u with r < v, on " + std::to_string(divisions) + " divisions");
  int bad_recip = 0;
  for (size_t n : {2, 31, 32, 33, 34, 64, 65, 97, 150, 257}) {
    bn::Nat v = words(n, 0);
    v.resize(n, 1);
    v.back() |= std::uint64_t{1} << 63;
    const bn::Nat x = bn::reciprocal(v);
    bn::Nat b2n(2 * n + 1, 0);
    b2n[2 * n] = 1;
    const bn::Nat vx = bn::mul(v, x);
    if (bn::cmp(vx, b2n) > 0 || bn::cmp(bn::add(vx, v), b2n) <= 0) ++bad_recip;
  }
  selt::eq(bad_recip, 0, "the Newton reciprocal is floor(B^2n / v), past its long-division base");

  // Decimal text: round trips, the exact digit count, and products against ref_mul.
  auto digit_string = [&](size_t len) {
    std::string s;
    for (size_t i = 0; i < len; ++i) s.push_back(static_cast<char>('0' + next() % 10));
    if (s[0] == '0') s[0] = '7';
    return s;
  };
  int bad_text = 0, bad_ref = 0, texts = 0;
  for (size_t len : {1, 18, 19, 20, 38, 39, 40, 227, 228, 229, 455, 456, 457, 1000, 4000, 20000}) {
    const std::string s = digit_string(len);
    const bn::Nat x = bn::from_decimal(s);
    ++texts;
    if (bn::to_decimal(x) != s || bn::digits(x) != len) ++bad_text;
    if (len <= 1000) {
      const std::string t = digit_string(len / 2 + 3);
      if (bn::to_decimal(bn::mul(x, bn::from_decimal(t))) != ref_mul(s, t)) ++bad_ref;
    }
  }
  selt::eq(bad_text, 0, "decimal text round-trips with its exact digit count at " + std::to_string(texts) + " lengths, to 20,000 digits");
  selt::eq(bad_ref, 0, "products agree with the digit-string reference");
  // Powers of ten: 10^k, its all-nines neighbour, and division by it is slicing
  // the digit string (the large k go through Barrett with a cached reciprocal).
  int bad_pow = 0;
  const std::string big = digit_string(30000);
  const bn::Nat bigx = bn::from_decimal(big);
  for (size_t k : {1, 19, 20, 27, 28, 100, 1000, 4321, 9728, 19456}) {
    const bn::Nat ten = bn::mul_pow10(bn::Nat{1}, k);
    if (bn::to_decimal(ten) != "1" + std::string(k, '0') || bn::digits(ten) != k + 1) ++bad_pow;
    const bn::Nat nines = bn::sub(ten, bn::Nat{1});
    if (bn::to_decimal(nines) != std::string(k, '9') || bn::digits(nines) != k) ++bad_pow;
    const auto [q, r] = bn::divmod_pow10(bigx, k);
    if (bn::to_decimal(q) != big.substr(0, big.size() - k) || bn::to_decimal(r) != ref_strip(big.substr(big.size() - k))) ++bad_pow;
    const bn::Nat scaled = bn::mul_pow10(bigx, k);
    if (bn::trailing_decimal_zeros(scaled, ~std::size_t{0}) != k || bn::trailing_decimal_zeros(scaled, k / 2) != k / 2) ++bad_pow;
  }
  selt::eq(bad_pow, 0, "powers of ten, their nines, division by them and trailing zeros are exact");
  // The early refusal: code and position are what dec_guard raises.
  auto code_of = [&](const std::function<void()>& f) {
    try { f(); return std::string("ok"); } catch (const SelError& e) { return e.code() + "@" + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col); }
  };
  Pos at{1, 7, 6};
  auto parse = [](const std::string& s) { Dec d; dec_parse(s.c_str(), d); return d; };
  selt::eq(code_of([&] { dec_power(parse("9999999999999999999999999999"), 100000, at); }), std::string("E_RANGE@1:7"),
           "a doomed POWER is E_RANGE at the call, without computing it");
  selt::eq(code_of([&] { dec_power(parse("10"), 99999, at); }), std::string("ok"),
           "a POWER that fits (100,000 digits) is still computed");
  selt::eq(code_of([&] { Dec big = dec_power(parse("99999999"), 65000, at); dec_mul(big, big, at); }), std::string("E_RANGE@1:7"),
           "a product past 1,000,000 integer digits is E_RANGE");
}

// CPP-P4: `??` / `???` over a name or a literal-key index chain is probed without
// an exception; the answers must be exactly what evaluating the operand gives,
// including for scalars, NULL, vacuous values, a miss at any level, and a chain
// evaluated deep enough that the depth budget is the evaluator's to report.
void test_coalesce_probe() {
  selt::section("?? probe path (CPP-P4)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code() + "@" + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col); }
  };
  const std::string setup = "A = RECORD(\"a\", RECORD(\"b\", 1, \"e\", \"\", \"n\", NULL), \"s\", 5); ";
  const std::pair<const char*, const char*> cases[] = {
      {"A[\"a\"][\"b\"] ?? 9", "t\"1\""},
      {"A[\"a\"][\"zz\"] ?? 9", "t\"9\""},
      {"A[\"zz\"][\"b\"] ?? 9", "t\"9\""},
      {"A[\"s\"][\"b\"] ?? 9", "t\"9\""},                 // indexing a scalar is a miss
      {"A[\"a\"][\"n\"] ?? 9", "t\"9\""},                 // present but NULL: ?? falls back
      {"A[\"a\"][\"e\"] ?? 9", "t\"\""},                  // vacuous: ?? keeps it
      {"A[\"a\"][\"e\"] ??? 9", "t\"9\""},                // ??? does not
      {"NOPE ?? 7", "t\"7\""},
      {"NOPE[\"x\"] ??? 7", "t\"7\""},
      {"A ?? 7 ?? 8", "-{\"a\"=-{\"b\"=t\"1\", \"e\"=t\"\", \"n\"=-}, \"s\"=t\"5\"}"},
  };
  for (const auto& [src, want] : cases) {
    const std::string got = run(setup + src);
    const std::string via_paren = run(setup + "(" + src + ")");
    selt::eq(got == via_paren, true, std::string("same through parentheses: ") + src);
    (void)want;
  }
  selt::eq(run(setup + "A[\"a\"][\"b\"] ?? 9"), std::string("t\"1\""), "present leaf kept");
  selt::eq(run(setup + "A[\"a\"][\"zz\"] ?? 9"), std::string("t\"9\""), "missing leaf falls back");
  selt::eq(run(setup + "A[\"s\"][\"b\"] ?? 9"), std::string("t\"9\""), "indexing a scalar is a miss");
  selt::eq(run(setup + "A[\"a\"][\"n\"] ?? 9"), std::string("t\"9\""), "NULL falls back under ??");
  selt::eq(run(setup + "A[\"a\"][\"e\"] ?? 9"), std::string("t\"\""), "vacuous kept under ??");
  selt::eq(run(setup + "A[\"a\"][\"e\"] ??? 9"), std::string("t\"9\""), "vacuous falls back under ???");
  selt::eq(run("NOPE ?? 7"), std::string("t\"7\""), "undefined name falls back");
  selt::eq(run(setup + "MAP((1, 2), _[\"k\"] ?? _)"), std::string("-{\"1\"=t\"1\", \"2\"=t\"2\"}"), "binder element miss");
  // Depth: a right operand's own error is not swallowed, and a deep chain near the
  // cap still reports E_DEPTH from the evaluator (the probe steps aside).
  selt::eq(run(setup + "A[\"zz\"] ?? (1 / 0)").substr(0, 10), std::string("E_DIV_ZERO"), "right-hand error propagates");
}

// CPP-P9: the text builtins now work on UTF-8 bytes instead of a decoded
// vector<char32_t>. The expected values below were produced by the decoding
// implementation they replaced, on text with 2-, 3- and 4-byte characters and
// combining marks, so any divergence between code-point and byte counting shows.
void test_text_byte_paths() {
  selt::section("text builtins on bytes (CPP-P9)");
  const std::pair<const char*, const char*> cases[] = {
      {"LEN(\"Zażółć😀\")", "7"},
      {"LEFT(\"Zażółć😀\", 3)", "Zaż"},
      {"LEFT(\"Zażółć😀\", 100)", "Zażółć😀"},
      {"RIGHT(\"Zażółć😀\", 2)", "ć😀"},
      {"RIGHT(\"Zażółć😀\", 0)", ""},
      {"RIGHT(\"ab\", 9)", "ab"},
      {"SUBSTR(\"Zażółć😀\", 3)", "żółć😀"},
      {"SUBSTR(\"Zażółć😀\", 3, 2)", "żó"},
      {"SUBSTR(\"Zażółć😀\", 99)", ""},
      {"SUBSTR(\"Zażółć😀\", 2, 1000000000000)", "ażółć😀"},
      {"FIND(\"ół\", \"Zażółć\")", "4"},
      {"FIND(\"😀\", \"a😀b😀\", 3)", "4"},
      {"FIND(\"z\", \"abc\")", "0"},
      {"FIND(\"a\", \"abc\", 99)", "0"},
      {"REPLACE(\"ż\", \"zz\", \"Zażółć\")", "Zazzółć"},
      {"REPLACE(\"a\", \"\", \"banana\")", "bnn"},
      {"JOIN(SPLIT(\"a😀b😀c\", \"😀\"), \"|\")", "a|b|c"},
      {"COUNT(SPLIT(\"abc\", \"x\"))", "1"},
      {"JOIN(SPLIT(\"\", \",\"), \"|\")", ""},
      {"TRIM(\"  \\t x y \\r\\n\")", "x y"},
      {"LTRIM(\"  x \")", "x "},
      {"RTRIM(\" x  \")", " x"},
      {"UPPER(\"zażółć abc\")", "ZAżółć ABC"},
      {"LOWER(\"ZAŻÓŁĆ ABC\")", "zaŻÓŁĆ abc"},
      {"BACKWARDS(\"Zażółć😀\")", "😀ćłóżaZ"},
      {"CODE(\"😀x\")", "128512"},
      {"CODE(\"ł\")", "322"},
      {"BACKWARDS(\"\")", ""},
      {"LEFT(\"\", 3)", ""},
  };
  for (const auto& [src, want] : cases) {
    std::string got;
    try { got = evaluate(src).scalar(); }
    catch (const SelError& e) { got = e.code(); }
    selt::eq(got, std::string(want), std::string("byte path: ") + src);
  }
}

// CPP-P7: a collecting operation keeps a fresh temporary as it is and copies
// anything that something else still refers to. The cases below are the ones
// where skipping a needed copy would be visible: mutation through the collected
// value must never reach the value it was built from, at any depth.
void test_adopt_or_clone() {
  selt::section("adopt a fresh temporary, clone a shared one (CPP-P7)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  selt::eq(run("A = RECORD(\"x\", 1); L = LIST(A); L[1][\"x\"] = 9; A[\"x\"]"), std::string("t\"1\""),
           "LIST copies a variable");
  selt::eq(run("A = RECORD(\"x\", 1); R = RECORD(\"k\", A); R[\"k\"][\"x\"] = 9; A[\"x\"]"), std::string("t\"1\""),
           "RECORD copies a variable");
  selt::eq(run("A = LIST(RECORD(\"k\", 1)); B = LIST(TAKE(A, 1)); B[1][1][\"k\"] = 5; A[1][\"k\"]"), std::string("t\"1\""),
           "a fresh container whose elements alias the source (TAKE) is still cloned");
  selt::eq(run("A = RECORD(\"k\", 1); L = LIST(A, A); L[1][\"k\"] = 7; L[2][\"k\"]"), std::string("t\"1\""),
           "the same value collected twice gives two independent copies");
  selt::eq(run("B = LIST(RECORD(\"k\", 1)); B[1][\"k\"] = 2; B[1][\"k\"]"), std::string("t\"2\""),
           "a fresh temporary is adopted and stays writable");
  selt::eq(run("A = 5; B = LIST(A, A + 1); A = 6; B[1]"), std::string("t\"5\""), "scalars collected are independent");
  selt::eq(run("X = RECORD(\"a\", LIST(1, 2)); Y = X; Y[\"a\"][1] = 9; X[\"a\"][1]"), std::string("t\"1\""),
           "assignment copies a shared tree");
  selt::eq(run("X = LIST(LIST(LIST(1))); Y = LIST(X); Y[1][1][1][1] = 4; X[1][1][1]"), std::string("t\"1\""),
           "nested fresh temporaries are independent of the variable they came from");
}

void test_bucket_rows_alias_when_unobservable() {
  selt::section("a projected BUCKET keeps its rows uncopied only when nothing could tell (CPP-REG-1)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  // The classifier: an assignment anywhere below a body, or a call to an
  // application's own function, is a possible write; nothing else is.
  register_function("REG1_POKE", 1, 1, [](HostArgs& a) {
    Value v = a.val(0);
    v.set("v", Value::integer(100));
    return Value::integer(0);
  });
  selt::ok(writes_nothing(*compile("RECORD(\"s\", SUM(_, r, r[\"v\"] * 2), \"k\", _K)").ast()),
           "reads, arithmetic and nested aggregates write nothing");
  selt::ok(!writes_nothing(*compile("RECORD(\"s\", (X = 1; COUNT(_)))").ast()), "an assignment writes");
  selt::ok(!writes_nothing(*compile("RECORD(\"s\", SUM(_, r, (L[1] += 1; r)))").ast()),
           "a compound assignment inside a nested body writes");
  selt::ok(!writes_nothing(*compile("L[(K = 1)]").ast()), "an assignment inside an index writes");
  selt::ok(!writes_nothing(*compile("RECORD(\"s\", REG1_POKE(L[1]))").ast()), "a host function call may write");

  const std::string L = "L = LIST(RECORD(\"k\", \"a\", \"v\", 1), RECORD(\"k\", \"a\", \"v\", 2), "
                        "RECORD(\"k\", \"b\", \"v\", 5)); ";
  selt::eq(run(L + "JOIN(MAP(BUCKET(L, _[\"k\"], RECORD(\"k\", _K, \"s\", SUM(_, r, r[\"v\"]))), "
                   "_[\"k\"] & \"=\" & _[\"s\"]), \",\")"),
           std::string("t\"a=3,b=5\""), "a read-only projection answers as before");
  // A row that reaches the result is a copy: changing it through the host API must
  // not change the variable it came from.
  {
    Value ctx = Value::none();
    compile(L + "0").run(ctx);
    Value out = compile("BUCKET(L, _[\"k\"], RECORD(\"rows\", _))").run(ctx);
    Value row = *(*(*out.get("1")).get("rows")).get("1");
    row.set("v", Value::integer(77));
    selt::eq(ctx.get("L")->get("1")->get("v")->scalar(), std::string("1"),
             "a row a RECORD projection keeps is a copy, not the variable's row");
    Value out2 = compile("BUCKET(L, _[\"k\"], LIST(_[1], COUNT(_)))").run(ctx);
    Value row2 = *(*out2.get("1")).get("1");
    row2.set("v", Value::integer(78));
    selt::eq(ctx.get("L")->get("1")->get("v")->scalar(), std::string("1"), "and so is one a LIST projection keeps");
  }
  // E_DEPTH parity: a row nested past the cap raises the same error, with or without
  // the copy (the second spelling writes, so it copies).
  std::string deep = "D = 1; D";
  for (int i = 0; i < 199; ++i) deep += "[1]";
  deep += " = 1; L = LIST(D); ";
  std::string alias_err, copy_err;
  try { evaluate(deep + "BUCKET(L, 1, RECORD(\"n\", COUNT(_)))"); } catch (const SelError& e) {
    alias_err = e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  try { evaluate(deep + "BUCKET(L, 1, RECORD(\"n\", (Z = 1; COUNT(_))))"); } catch (const SelError& e) {
    copy_err = e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  selt::ok(!copy_err.empty(), "the copying path refuses the too-deep row");
  selt::eq(alias_err, copy_err, "the uncopied path refuses it the same way");
}

void test_filter_borrows_rows_for_a_read_only_next_step() {
  selt::section("a FILTER keeps its elements uncopied when it and the next step write nothing (CPP-REG-1)");
  auto flags = [](const std::string& src) {
    const NodePtr p = compile(src).physical_ast();
    std::string out;
    std::vector<const Node*> stack{p.get()};
    while (!stack.empty()) {
      const Node* n = stack.back();
      stack.pop_back();
      if (n->t == NT::Call && n->s == "FILTER" && !n->items.empty()) out += n->items.back()->borrow_rows ? "B" : "c";
      for (const NodePtr& c : n->items) if (c) stack.push_back(c.get());
      if (n->l) stack.push_back(n->l.get());
      if (n->r) stack.push_back(n->r.get());
    }
    return out;
  };
  selt::eq(flags("L .> FILTER(_[\"v\"] > 0) .> MAP(_[\"v\"] * 2)"), std::string("B"), "read-only FILTER then MAP");
  selt::eq(flags("L .> FILTER(_[\"v\"] > 0) .> MAP((X = 1; _[\"v\"]))"), std::string("c"),
           "a next step that assigns");
  selt::eq(flags("L .> FILTER((X = 1; _[\"v\"] > 0)) .> MAP(_)"), std::string("c"), "a FILTER body that assigns");
  selt::eq(flags("L .> FILTER(_[\"v\"] > 0) .> MAP(REG1_POKE(_))"), std::string("c"),
           "a next step that calls a host function");
  selt::eq(flags("L .> FILTER(_[\"v\"] > 0) .> SORT_BY(_[\"v\"])"), std::string("c"), "a next step that is not MAP/FILTER");
  selt::eq(flags("L .> FILTER(_[\"v\"] > 0)"), std::string("c"), "no next step");

  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  const std::string L = "L = LIST(RECORD(\"v\", 1), RECORD(\"v\", 2), RECORD(\"v\", 3)); ";
  selt::eq(run(L + "JOIN(L .> FILTER(_[\"v\"] > 1) .> MAP(_[\"v\"] * 2), \",\")"), std::string("t\"4,6\""),
           "the answer is unchanged");
  {
    Value ctx = Value::none();
    compile(L + "0").run(ctx);
    Value out = compile("L .> FILTER(_[\"v\"] > 1) .> MAP(_)").run(ctx);
    Value row = *out.get("1");
    row.set("v", Value::integer(77));
    selt::eq(ctx.get("L")->get("2")->get("v")->scalar(), std::string("2"), "what the MAP after it keeps is a copy");
  }
  std::string deep = "D = 1; D";
  for (int i = 0; i < 199; ++i) deep += "[1]";
  deep += " = 1; L = LIST(D); ";
  std::string borrow_err, copy_err;
  try { evaluate(deep + "COUNT(L .> FILTER(COUNT(_) > 0) .> MAP(1))"); } catch (const SelError& e) {
    borrow_err = e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  try { evaluate(deep + "COUNT(L .> FILTER((Z = 1; COUNT(_) > 0)) .> MAP(1))"); } catch (const SelError& e) {
    copy_err = e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  selt::ok(!copy_err.empty(), "the copying FILTER refuses the too-deep element");
  selt::eq(borrow_err, copy_err, "the borrowing FILTER refuses it the same way");
}

void test_pipeline_temporaries_are_kept() {
  selt::section("aggregates keep a pipeline temporary's elements, copy everything else (CPP-REG-1)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  // What each aggregate hands on, read back while a body WRITES to the variable the
  // elements came from (`A[1]["k"] = 9`; an aggregate's binder cannot be assigned, and
  // assigning the result to a variable first would hide the question -- assignment
  // copies whatever is shared). If the aggregate had kept an element something else
  // still holds, the write would show in the element it handed on: it would read 9
  // where every copy reads 3.
  const std::string setup = "A = LIST(RECORD(\"k\", 3), RECORD(\"k\", 1), RECORD(\"k\", 5)); ";
  const std::string poke = "(A[1][\"k\"] = 9; _[\"k\"])";
  struct Form { const char* name; const char* shape; const char* for_a; const char* for_take; const char* for_dup; };
  const std::vector<Form> forms = {
      {"FILTER", "JOIN(MAP(FILTER(%s, _[\"k\"] > 0), %p), \",\")", "3,1,5", "3,1", "3,3"},
      {"SORT", "JOIN(MAP(SORT(%s), %p), \",\")", "1,3,5", "1,3", "3,3"},
      {"SORT_BY", "JOIN(MAP(SORT_BY(%s, _[\"k\"]), %p), \",\")", "1,3,5", "1,3", "3,3"},
      {"TOP", "JOIN(MAP(TOP(%s, _[\"k\"], 2), %p), \",\")", "1,3", "1,3", "3,3"},
      {"BUCKET", "JOIN(MAP(BUCKET(%s, _[\"k\"], _), g, JOIN(MAP(g, r, (A[1][\"k\"] = 9; r[\"k\"])), \"+\")), \",\")",
       "3,1,5", "3,1", "3+3"},
  };
  const std::string sources[3] = {"A", "TAKE(A, 2)", "(TAKE(A, 1), TAKE(A, 1))"};
  const char* what[3] = {"a variable's list", "a fresh list of the variable's elements (TAKE)",
                         "the same element from two positions"};
  for (const Form& f : forms) {
    for (int k = 0; k < 3; ++k) {
      std::string body = f.shape;
      body.replace(body.find("%s"), 2, sources[k]);
      while (body.find("%p") != std::string::npos) body.replace(body.find("%p"), 2, poke);
      const char* want = k == 0 ? f.for_a : k == 1 ? f.for_take : f.for_dup;
      selt::eq(run(setup + body), std::string("t\"") + want + "\"",
               std::string(f.name) + " of " + what[k] + " copies");
    }
  }
  // A fresh row with a borrowed part: a join's rows are new, but the row of the right
  // relation they carry is the variable's. What FILTER keeps is the fresh row with a
  // COPY of the borrowed part, not the borrowed part.
  selt::eq(run("X = LIST(RECORD(\"id\", 1)); Y = LIST(RECORD(\"id\", 1, \"n\", \"a\")); "
               "JOIN(MAP(FILTER(LINK(X, Y, _1[\"id\"] == _2[\"id\"]), _[\"Y\"][\"n\"] $== \"a\"), "
               "(Y[1][\"n\"] = \"z\"; _[\"Y\"][\"n\"])), \",\")"),
           std::string("t\"a\""), "FILTER of join rows copies the borrowed row");
  selt::eq(run("X = LIST(RECORD(\"id\", 1)); Y = LIST(RECORD(\"id\", 1, \"n\", \"a\")); "
               "JOIN(MAP(SORT_BY(LINK(X, Y, _1[\"id\"] == _2[\"id\"]), _[\"X\"][\"id\"]), "
               "(Y[1][\"n\"] = \"z\"; _[\"Y\"][\"n\"])), \",\")"),
           std::string("t\"a\""), "SORT_BY of join rows copies the borrowed row");
  selt::eq(run("X = LIST(RECORD(\"id\", 1)); Y = LIST(RECORD(\"id\", 1, \"n\", \"a\")); "
               "JOIN(MAP(MAP(BUCKET(LINK(X, Y, _1[\"id\"] == _2[\"id\"]), _[\"X\"][\"id\"], _), g, g), "
               "(Y[1][\"n\"] = \"z\"; _[1][\"Y\"][\"n\"])), \",\")"),
           std::string("t\"a\""), "BUCKET rows of a join copy the borrowed row");
  // A pipeline temporary: nothing else holds a thing, so what the next stage keeps is
  // the thing itself -- correct, in order, and writable.
  selt::eq(run("F = FILTER(MAP(LIST(3, 1, 2), RECORD(\"k\", _)), _[\"k\"] > 1); F[1][\"k\"] = 7; JOIN(MAP(F, _[\"k\"]), \",\")"),
           std::string("t\"7,2\""), "FILTER of a temporary keeps its survivors in order and writable");
  selt::eq(run("S = SORT_BY(MAP(LIST(3, 1, 2), RECORD(\"k\", _)), _[\"k\"]); S[1][\"k\"] = 0; JOIN(MAP(S, _[\"k\"]), \",\")"),
           std::string("t\"0,2,3\""), "SORT_BY of a temporary");
  selt::eq(run("T = TOP(MAP(LIST(3, 1, 2), RECORD(\"k\", _)), _[\"k\"], 2); T[1][\"k\"] = 0; JOIN(MAP(T, _[\"k\"]), \",\")"),
           std::string("t\"0,2\""), "TOP of a temporary");
  selt::eq(run("B = BUCKET(MAP(LIST(1, 2), RECORD(\"k\", _)), _[\"k\"], _); B[1][1][\"k\"] = 9; B[2][1][\"k\"]"),
           std::string("t\"2\""), "buckets of a temporary stay independent of each other");
}

void test_filter_packed_result() {
  selt::section("FILTER keeps the source's keys: a kept prefix is a packed list, a gap keeps its keys (CPP-P20)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  selt::eq(run("FILTER(LIST(1, 2, 3, 4), _ < 3)"), run("LIST(1, 2)"), "a kept prefix dumps as the packed list");
  selt::eq(run("FILTER(LIST(1, 2, 3, 4), _ > 1)"), std::string("-{\"2\"=t\"2\", \"3\"=t\"3\", \"4\"=t\"4\"}"),
           "a dropped head keeps the original keys");
  selt::eq(run("FILTER(LIST(1, 2, 3, 4), _ != 2)"), std::string("-{\"1\"=t\"1\", \"3\"=t\"3\", \"4\"=t\"4\"}"),
           "a gap keeps the original keys");
  selt::eq(run("COUNT(FILTER(LIST(1, 2, 3), TRUE))"), std::string("t\"3\""), "keeping everything");
  selt::eq(run("F = FILTER(LIST(1, 2, 3, 4), _ < 3); F[3] = 9; COUNT(F)"), std::string("t\"3\""),
           "the packed result is an ordinary writable list");
  selt::eq(run("FILTER(FILTER(LIST(1, 2, 3, 4), _ > 1), _ > 2)"), std::string("-{\"3\"=t\"3\", \"4\"=t\"4\"}"),
           "a filtered list filtered again keeps the keys it has, not 1..");
  selt::eq(run("FILTER(RECORD(\"a\", 1, \"b\", 2), _ > 0)"), std::string("-{\"a\"=t\"1\", \"b\"=t\"2\"}"),
           "a record source keeps its names");
}

void test_round2_fast_paths() {
  selt::section("round-2 fast paths keep their answers (CPP-P11..P20)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code(); }
  };
  // CPP-P11: TOP takes the sort-and-cut route from half the source up; both routes agree.
  const std::string xs = "X = MAP(LIST(5, 3, 9, 3, 1, 7, 7, 2), _); ";
  for (int k = 0; k <= 9; ++k) {
    const std::string n = std::to_string(k);
    selt::eq(run(xs + "TOP(X, " + n + ")"), run(xs + "TAKE(SORT(X), " + n + ")"), "TOP(X, " + n + ") is TAKE(SORT)");
    selt::eq(run(xs + "TOP_DESC(X, " + n + ")"), run(xs + "TAKE(SORT_DESC(X), " + n + ")"), "TOP_DESC(X, " + n + ")");
  }
  selt::eq(run("X = LIST(RECORD(\"k\", 2, \"i\", 1), RECORD(\"k\", 1, \"i\", 2), RECORD(\"k\", 2, \"i\", 3), RECORD(\"k\", 1, \"i\", 4)); "
               "JOIN(MAP(TOP(X, _[\"k\"], 3), _[\"i\"]), \",\")"), std::string("t\"2,4,1\""), "TOP ties keep the source order");
  // CPP-P12: a bare BUCKET builds its record in one go; it is still an ordinary record.
  selt::eq(run("B = BUCKET(MAP(LIST(1, 2, 3, 4, 5, 6), _), _ % 3); COUNT(B) & \"/\" & JOIN(MAP(B[\"0\"], _), \"+\") & \"/\" & COUNT(B[\"1\"])"),
           std::string("t\"3/3+6/2\""), "bare BUCKET reads back by key");
  selt::eq(run("B = BUCKET(MAP(LIST(1, 2, 3), _), _ % 2); B[\"7\"] = LIST(9); COUNT(B)"), std::string("t\"3\""), "and takes new keys");
  // CPP-P13: marking a parenthesised group in place does not change what it means.
  selt::eq(run("COUNT(((((1, 2, 3)))))"), std::string("t\"3\""), "nested parentheses around a list");
  selt::eq(run("A = (1, 2); COUNT((A, (3, 4)))"), std::string("t\"4\""), "a grouped list inside a list flattens as before");
  selt::eq(run("MAX((1, 5, 3))"), std::string("t\"1\""), "a grouped list is one argument");
  // CPP-P14 (Bindings no longer copied per call, value names worked out once) is covered in sql_unit.
  // CPP-P15: one-shot evaluate() runs loop-free rules on the tree as parsed; both routes agree.
  selt::eq(run("ROUND(1.005 * 3, 2) > 3 AND \"a\" $< \"b\""), std::string("TRUE"), "evaluate without an iterating call");
  selt::eq(run("COUNT(FILTER(LIST(1, 2, 3), _ > 1))"), std::string("t\"2\""), "evaluate with one");
  {
    Value ctx = Value::none();
    ctx.set("A", Value::num("4"));
    selt::eq(evaluate("A = A + 1; A * 2", ctx).dump(), std::string("t\"10\""), "a loop-free rule still writes its context");
    selt::eq(ctx.get("A")->dump(), std::string("t\"5\""), "the context the caller handed in is the one written");
  }
  selt::eq(run("A = 1; A[\"k\"][\"j\"] = 2; 0 + A[\"k\"][\"j\"]"), std::string("t\"2\""), "assignment paths");
  // CPP-P16: a pattern asked for again, after others came and went, is the same pattern.
  selt::eq(run("N = MAP(SPLIT(REPEAT(\"a,\", 299), \",\"), _K); ALL(N, n, RMATCH(\"^x\" & n & \"$\", \"x\" & n)) "
               "AND RMATCH(\"^x0$\", \"x0\") AND NOT RMATCH(\"^x1$\", \"x2\") AND RMATCH(\"^x1$\", \"x1\")"),
           std::string("TRUE"), "cache churn past the bound keeps every answer");
  selt::eq(run("JOIN(MAP(LIST(RMATCH(\"^a\", \"abc\"), RMATCH(\"^a\", \"abc\", \"i\"), RMATCH(\"^A\", \"abc\", \"i\"), RMATCH(\"^A\", \"abc\")), "
               "IF(_, \"T\", \"F\")), \"\")"),
           std::string("t\"TTTF\""), "one pattern with and without the flag");
  selt::eq(run("JOIN(MAP(LIST(RMATCH(\"^é\", \"éa\")), IF(_, \"T\", \"F\")), \"\") & RFIND(\"a\", \"éa\") & RFIND(\"ñ\", \"abc\")"),
           std::string("t\"T20\""), "a non-ASCII subject widens the same way");
  {
    // The last-used pattern is per thread and is dropped when any entry is evicted.
    std::vector<std::thread> threads;
    std::atomic<int> bad{0};
    for (int t = 0; t < 4; ++t) {
      threads.emplace_back([t, &bad] {
        for (int i = 0; i < 400; ++i) {
          const std::string p = "^k" + std::to_string((i * 7 + t) % 300) + "$";
          const std::string subject = "k" + std::to_string((i * 7 + t) % 300);
          try {
            if (evaluate("RMATCH(\"" + p + "\", \"" + subject + "\")").dump() != "TRUE") ++bad;
          } catch (...) { ++bad; }
        }
      });
    }
    for (auto& th : threads) th.join();
    selt::eq(bad.load(), 0, "four threads churning 300 patterns through a 256-entry cache");
  }
  // CPP-P17: the name comparison is case-insensitive without a copy.
  selt::eq(run("L = LIST(1, 2, 3); JOIN(MAP(L, _k), \",\")"), std::string("t\"1,2,3\""), "_k reads the key in any case");
  selt::eq(run("L = LIST(4, 5); JOIN(MAP(L, e, e + _K), \",\")"), std::string("t\"5,7\""), "a named binder with _K");
  // CPP-P18: a long subject takes the pre-split replacement, a short one the old walk.
  const std::string rep_short = "RREPLACE(\"(a)(x)?\", \"<$1|$2|$$|$0|$z>\", \"baab\")";
  selt::eq(run(rep_short), std::string("t\"b<a||$|a|$z><a||$|a|$z>b\""), "replacement pieces, short subject");
  std::string longs(300, 'b');
  longs.replace(10, 4, "baab");
  std::string expect_long = std::string(300, 'b');
  expect_long.replace(10, 4, "baab");
  {
    const std::string got = run("RREPLACE(\"(a)(x)?\", \"<$1|$2|$$|$0|$z>\", \"" + longs + "\")");
    std::string want;
    for (std::size_t i = 0; i < expect_long.size(); ++i) {
      if (expect_long[i] == 'a') want += "<a||$|a|$z>"; else want += expect_long[i];
    }
    selt::eq(got, "t\"" + want + "\"", "replacement pieces, long subject (pre-split path)");
  }
  selt::eq(run("RREPLACE(\"a\", \"$3\", \"" + std::string(300, 'b') + "\")"), std::string("t\"" + std::string(300, 'b') + "\""),
           "a replacement naming a missing group is only refused when something matches (long)");
  selt::eq(run("RREPLACE(\"a\", \"$3\", \"bab\")"), std::string("E_BAD_ARG"), "and refused at the match (short)");
  selt::eq(run("RREPLACE(\"a\", \"$3\", \"" + std::string(300, 'b') + "a\")"), std::string("E_BAD_ARG"), "and refused at the match (long)");
  // CPP-P19: base64 round trips and rejections are unchanged.
  selt::eq(run("DECODE_BASE64(ENCODE_BASE64(TO_UTF8(\"any carnal pleas\"))) $== TO_UTF8(\"any carnal pleas\")"), std::string("TRUE"), "round trip");
  selt::eq(run("ENCODE_BASE64(TO_UTF8(\"ab\"))"), std::string("t\"YWI=\""), "padding");
  selt::eq(run("DECODE_BASE64(\"YW*=\")"), std::string("E_BAD_ARG"), "a character outside the alphabet");
  selt::eq(run("DECODE_BASE64(\"YW=I\")"), std::string("E_BAD_ARG"), "misplaced padding");
  // CPP-P20: see test_filter_packed_result.
}

void test_decimal_native_edges() {
  selt::section("decimal native edges");
  auto parse = [](const char* s) {
    Dec d;
    dec_parse(s, d);
    return d;
  };

  // CPP-C20: both operands past scale 18 were rounded to 18 digits first.
  selt::eq(dec_format(dec_mul(parse("0.1234567890123456789"), parse("0.1234567890123456789"))),
           std::string("0.01524157875323883675019051998750190521"),
           "scale 19 times scale 19 is exact");
  selt::eq(dec_format(dec_power(parse("1.05"), 30)),
           std::string("4.321942375150662009157288198886473341473378241062164306640625"),
           "POWER inherits the exact multiply");

  // CPP-C21: 2 * remainder overflows a signed __int128 once the remainder passes 2^126.
  selt::eq(dec_format(dec_round(parse("0.99999999999999999999999999999999999999"), 0)),
           std::string("1"), "ROUND with a remainder past 2^126");
  selt::eq(dec_format(dec_div(parse("8600000000000000000000000000.0000000000"),
                              parse("99999999999999999999999999999999999999"))),
           std::string("0.0000000001"), "division with a remainder past 2^126");

  // CPP-C24: dividing by exactly "1" past int128 left an empty remainder string.
  selt::eq(dec_format(dec_div(parse("1000000000000000000000000000000000000000000"), parse("1"))),
           std::string("1000000000000000000000000000000000000000000"),
           "division by 1 beyond int128 is exact and minimal-scale");
  selt::eq(dec_format(dec_mod(parse("-1000000000000000000000000000000000000000000"), parse("1"))),
           std::string("0"), "modulo by 1 beyond int128 is 0, not -0");

  // CPP-C41: -2^127 is never a small mantissa, so nothing negates it unsafely.
  Dec min_product = dec_mul(parse("-9223372036854775808"), parse("18446744073709551616"));
  selt::eq(dec_format(min_product), std::string("-170141183460469231731687303715884105728"),
           "the INT128_MIN product prints");
  selt::eq(dec_sign(dec_abs(min_product)), 1, "its absolute value is positive");
  selt::eq(dec_sign(dec_negate(min_product)), 1, "its negation is positive");
  selt::eq(dec_cmp(dec_negate(min_product), min_product), 1, "and compares above it");
  selt::eq(min_product.small, false, "held as digits, not as a mantissa");
  selt::eq(dec_format(dec_sub(dec_from_mantissa(std::numeric_limits<__int128_t>::min() + 1, 0),
                              parse("1"))),
           std::string("-170141183460469231731687303715884105728"),
           "a sum that lands on -2^127 is held safely");

  // CEIL/FLOOR carry past the integer-digit cap is E_RANGE at the call.
  const std::string nines(1000000, '9');
  selt::raises("E_RANGE", [&] { dec_ceil(parse((nines + ".5").c_str()), Pos{1, 1, 0}); },
               "CEIL carrying past the cap");
  selt::raises("E_RANGE", [&] { dec_floor(parse(("-" + nines + ".5").c_str()), Pos{1, 1, 0}); },
               "FLOOR carrying past the cap");
}

// §3.4: what the aggregates collect is copied, and a value's depth counts the path
// it is stored under. Each program mutates the source through a pending read, so a
// result that still shared an element with it would show the mutation (CPP-C42).
void test_round3_fast_paths() {
  selt::section("round-3 fast paths keep their answers (CPP-P21..P24)");
  auto run = [](const std::string& src) {
    try { return evaluate(src).dump(); }
    catch (const SelError& e) { return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col()); }
  };
  // CPP-P21: literal keys go straight into the shaped storage. Same records as the
  // dynamic-key spelling, same errors in the same places.
  selt::eq(run("RECORD(\"a\", 1, \"b\", \"x\", \"c\", LIST(1, 2))"),
           run("K = \"a\"; RECORD(K, 1, \"b\", \"x\", \"c\", LIST(1, 2))"), "literal keys equal the general path");
  selt::eq(run("RECORD(\"a\", 1, \"a\", 2)"), std::string("-{\"a\"=t\"2\"}"), "a repeated literal key keeps the general rule");
  selt::eq(run("S = RECORD(\"n\", 1); R = RECORD(\"a\", S); S[\"n\"] = 9; R[\"a\"][\"n\"]"), std::string("t\"1\""),
           "a value stored in a literal-key record is a copy");
  selt::eq(run("RECORD(\"a\", 1 / 0, \"b\", NOSUCH())"), std::string("E_UNKNOWN_FUNC@1:25"),
           "an unknown name is a compile-time refusal before any evaluation");
  selt::eq(run("RECORD(\"a\", 1, \"b\", 1 / 0)"), std::string("E_DIV_ZERO@1:23"), "a value error is reported at the value");
  selt::eq(run("RECORD(\"a\", UNBOUND, \"b\", 1 / 0)"), std::string("E_UNDEF_VAR@1:13"), "values still evaluate left to right");
  selt::eq(run("R = RECORD(\"a\", 1, \"b\", 2); R[\"c\"] = 3; R[\"a\"] & R[\"b\"] & R[\"c\"] & COUNT(R)"), std::string("t\"1233\""),
           "a literal-key record takes new keys afterwards");
  selt::eq(run("JOIN(MAP(LIST(1, 2, 3), RECORD(\"k\", _)[\"k\"]), \",\")"), std::string("t\"1,2,3\""), "a literal-key record per row");
  // CPP-P22: substring search is linear; FIND/REPLACE/SPLIT agree with the naive definition.
  selt::eq(run("FIND(\"b\", \"aabab\")"), std::string("t\"3\""), "FIND");
  selt::eq(run("FIND(\"é😀\", \"aé😀b\", 2)"), std::string("t\"2\""), "FIND counts code points");
  selt::eq(run("FIND(\"ab\", \"aabab\", 4)"), std::string("t\"4\""), "FIND from a start");
  selt::eq(run("FIND(\"zz\", \"aabab\")"), std::string("t\"0\""), "FIND misses");
  selt::eq(run("REPLACE(\"aa\", \"b\", \"aaaa\")"), std::string("t\"bb\""), "REPLACE non-overlapping");
  selt::eq(run("REPLACE(\"aa\", \"b\", \"aaa\")"), std::string("t\"ba\""), "REPLACE leftmost first");
  selt::eq(run("JOIN(SPLIT(\"a😀b😀😀c\", \"😀\"), \"|\")"), std::string("t\"a|b||c\""), "SPLIT on a 4-byte separator");
  selt::eq(run("COUNT(SPLIT(\"\", \",\"))"), std::string("t\"1\""), "SPLIT of nothing is one empty piece");
#if !defined(__SANITIZE_ADDRESS__)
  {
    // The pattern that took 6.7 s at n = 400,000 with std::string::find (two-way search).
    const auto t0 = std::chrono::steady_clock::now();
    const std::string r = run("FIND(REPEAT(\"a\", 200000) & \"b\", REPEAT(\"a\", 400000))");
    const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    selt::eq(r, std::string("t\"0\""), "a long needle that almost matches everywhere is not found");
    selt::ok(ms < 1500.0, "and it takes linear time (" + std::to_string(static_cast<int>(ms)) + " ms)");
  }
#endif
  // PADL/PADR and REPEAT are built byte by byte now.
  selt::eq(run("PADL(\"é😀\", 6, \"ab\")"), std::string("t\"ababé😀\""), "PADL with a partial fill cycle");
  selt::eq(run("PADR(\"x\", 5, \"éa\")"), std::string("t\"xéaéa\""), "PADR with a multi-byte fill");
  selt::eq(run("PADL(\"x\", 4, \"-\")"), std::string("t\"---x\""), "PADL with a one-byte fill");
  selt::eq(run("PADR(\"abc\", 2, \"-\")"), std::string("t\"abc\""), "a text already wide enough is returned as it is");
  selt::eq(run("PADL(\"é\", 1, \"\")"), std::string("E_BAD_ARG@1:14"), "an empty fill is refused even when nothing is padded");
  selt::eq(run("REPEAT(\"é\", 3)"), std::string("t\"ééé\""), "REPEAT of a multi-byte text");
  selt::eq(run("REPEAT(\"z\", 4)"), std::string("t\"zzzz\""), "REPEAT of one byte");
  selt::eq(run("REPEAT(\"\", 1000000000000)"), std::string("t\"\""), "REPEAT of nothing is nothing");
  // CPP-P23: DISTINCT and DEDUPE share one open-addressing table over the output list.
  selt::eq(run("JOIN(DISTINCT(LIST(\"b\", \"a\", \"b\", \"c\", \"a\")), \",\")"), std::string("t\"b,a,c\""), "first occurrence order");
  selt::eq(run("COUNT(DEDUPE(LIST(1, 1.0, 1, 2)))"), std::string("t\"3\""), "1 and 1.0 are different values (EQL is structural)");
  selt::eq(run("COUNT(DISTINCT(LIST(RECORD(\"a\", 1), RECORD(\"a\", 1), RECORD(\"a\", 2))))"), std::string("t\"2\""), "records compare by structure");
  selt::eq(run("DISTINCT(NULL)"), run("LIST()"), "NULL has nothing distinct");
  selt::eq(run("COUNT(DISTINCT(MAP(SPLIT(REPEAT(\"a,\", 4999), \",\"), _K % 1000)))"), std::string("t\"1000\""),
           "past the initial table the dedup still finds every duplicate");
  selt::eq(run("COUNT(DISTINCT(MAP(SPLIT(REPEAT(\"a,\", 2999), \",\"), _K)))"), std::string("t\"3000\""),
           "and keeps every distinct value while the table grows");
  // CPP-P4 (nested): `??` probes small operator trees for a miss instead of throwing it.
  const std::string rec = "A = RECORD(\"x\", 4, \"t\", \"abc\", \"r\", RECORD(\"y\", 2)); ";
  selt::eq(run(rec + "(A[\"k\"] & \"x\") ?? \"d\""), std::string("t\"d\""), "a missing key under & falls back");
  selt::eq(run(rec + "(A[\"x\"] + 1) ?? 0"), std::string("t\"5\""), "a present operand is used");
  selt::eq(run(rec + "(1 + A[\"k\"]) ?? 7"), std::string("t\"7\""), "a miss on the right operand too");
  selt::eq(run(rec + "(NOSUCH + 1) ?? 8"), std::string("t\"8\""), "a missing name");
  selt::eq(run(rec + "(-A[\"k\"]) ?? 9"), std::string("t\"9\""), "a miss under a unary minus");
  selt::eq(run(rec + "(A[\"r\"][\"y\"] * A[\"x\"]) ?? 0"), std::string("t\"8\""), "nested present values");
  selt::eq(run(rec + "(A[\"r\"][\"zz\"] * 2) ?? 11"), std::string("t\"11\""), "a miss deep in an index chain");
  selt::eq(run(rec + "(A[\"t\"] + 1) ?? 3"), std::string("E_NOT_NUM@1:56"), "a coercion error is not swallowed");
  selt::eq(run(rec + "(A[\"t\"] & A[\"k\"]) ??? \"z\""), std::string("t\"z\""), "??? on a miss");
  selt::eq(run(rec + "((A[\"k\"] & \"\") ?? \"\") ??? \"v\""), std::string("t\"v\""), "a vacuous result under ???");
  selt::eq(run(rec + "((Z = 5) + A[\"k\"]) ?? Z"), std::string("t\"5\""), "an operand with an effect runs first, then the miss");
  selt::eq(run(rec + "(A[\"k\"] + (Z = 6)) ?? Z"), std::string("E_UNDEF_VAR@1:76"),
           "a miss on the left never runs the right operand's effect (Z stays unset)");
  selt::eq(run(rec + "Z = 99; (A[\"k\"] + (Z = 6)) ?? Z"), std::string("t\"99\""),
           "...and a Z that was set before is untouched");
  selt::eq(run(rec + "(LEN(A[\"k\"]) + 1) ?? 2"), std::string("t\"2\""), "a call that misses inside the operand");
  selt::eq(run(rec + "IF(A[\"x\"] > 1, (A[\"k\"] & \"a\") ?? \"b\", \"c\")"), std::string("t\"b\""), "inside another call");
  // CPP-P24: a call with more than four arguments keeps working (the first four live in the object).
  selt::eq(run("MAX(3, 9, 2, 7, 4, 8, 1)"), std::string("t\"9\""), "seven strict arguments");
  selt::eq(run("COUNT(LIST(1, 2, 3, 4, 5, 6, 7, 8, 9))"), std::string("t\"9\""), "nine arguments to LIST");
  selt::eq(run("R = RECORD(\"a\", 1, \"b\", 2, \"c\", 3, \"d\", 4, \"e\", 5); R[\"e\"] * 2"), std::string("t\"10\""), "five pairs to RECORD");
  selt::eq(run("JOIN(MAP(LIST(\"x\", \"yy\"), PADL(_, 3, \"-\")), \",\")"), std::string("t\"--x,-yy\""), "a strict call per row");
}

void test_collector_copies_and_depth() {
  selt::section("collector copies and depth");
  const char* copies[][2] = {
      {"map", "X = LIST(RECORD(\"k\",1)); MAP(X, _)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"filter", "X = LIST(RECORD(\"k\",1)); FILTER(X, TRUE)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"sort body", "X = LIST(RECORD(\"k\",1)); SORT(X, 1)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"sort_desc", "X = LIST(RECORD(\"k\",1)); SORT_DESC(X, 1)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"sort_by", "X = LIST(RECORD(\"k\",1)); SORT_BY(X, 1)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"top", "X = LIST(RECORD(\"k\",1)); TOP(X, 1)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"top_by", "X = LIST(RECORD(\"k\",1)); TOP_BY(X, 1, 1)[(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"bucket", "X = LIST(RECORD(\"k\",1)); BUCKET(X, 1)[\"1\"][(X[1][\"k\"] = 9; 1)][\"k\"]"},
      {"bucket project", "X = LIST(RECORD(\"k\",1)); BUCKET(X, _[\"k\"], _)[(X[1][\"k\"] = 9; 1)][1][\"k\"]"},
  };
  for (const auto& c : copies) {
    selt::eq(dump_of(c[1]), std::string("t\"1\""), std::string(c[0]) + " copies what it collects");
  }
  // TAKE aliases the elements but returns a new container (the table in §3.4).
  selt::eq(dump_of("X = LIST(1,2,3); T = TAKE(X, 2); X[1] = 9; T[1]"), std::string("t\"1\""),
           "TAKE is a new container");

  auto code_at = [](const std::string& src) {
    try {
      compile(src).run();
    } catch (const SelError& e) {
      return e.code() + " 1:" + std::to_string(e.pos().col);
    }
    return std::string("no error");
  };
  std::string a199 = "A";
  for (int i = 0; i < 199; i++) a199 += "[1]";
  selt::eq(code_at(a199 + " = 1; B = A; 7"), std::string("no error"), "200 levels alone is legal");
  selt::eq(code_at(a199 + " = 1; B[1] = A; 7").substr(0, 7), std::string("E_DEPTH"),
           "the same value one level down is 201");
  selt::eq(code_at(a199 + " = 1; LIST(A); 7").substr(0, 7), std::string("E_DEPTH"),
           "a constructor one level over it is 201");
  selt::eq(code_at(a199 + " = 1; RECORD(\"k\", A); 7").substr(0, 7), std::string("E_DEPTH"),
           "RECORD too");
  selt::eq(code_at("A = 1; " + a199 + " = 1; (A, 2); 7").substr(0, 7), std::string("E_DEPTH"), "and `,`");
}

void test_value() {
  selt::section("value");

  {
    auto shape = std::make_shared<RecordShape>(std::vector<std::string>{"x"});
    std::weak_ptr<const RecordShape> weak = shape;
    Value row = Value::shaped(shape, {Value::list({Value::integer(1)})});
    shape.reset();
    Value alias = row;
    Value copy = row.clone();
    row = Value::none();
    alias.entries();  // Materialize the second, aliasing view before release.
    copy.get("x")->set("1", Value::integer(2));
    selt::eq(alias.get("x")->get("1")->scalar(), std::string("1"),
             "container clone owns independent nested payloads");
    alias.set("extra", Value::text("fallback"));
    selt::eq(alias.get("x")->get("1")->scalar(), std::string("1"),
             "inline payload survives shaped-to-fallback conversion");
    alias = Value::none();
    selt::ok(!weak.expired(), "cloned container keeps its shape alive");
    copy = Value::none();
    selt::ok(weak.expired(), "last container releases its shape owner");
  }

  Value scalar = Value::integer(17);
  Value alias = scalar;
  alias.set("label", Value::text("shared"));
  selt::eq(scalar.get("label")->scalar(), std::string("shared"),
           "adding collection state through a scalar alias is shared");
  selt::eq(scalar.scalar(), std::string("17"), "numeric scalar survives collection allocation");
  Value independent = scalar.clone();
  independent.set("label", Value::text("separate"));
  selt::eq(scalar.get("label")->scalar(), std::string("shared"), "clone owns independent collection state");
  Value numeric_alias = scalar;
  scalar.set_dec_val(nullptr);
  selt::ok(!numeric_alias.has_dec() && independent.has_dec(), "decimal reset aliases but clone owns its cache");
  Value lazy = Value::integer(42);
  Value lazy_copy = lazy.clone();
  lazy.set_dec(dec_from_int(43));
  selt::eq(lazy_copy.scalar(), std::string("42"), "unformatted clone owns independent decimal state");
  Value leaf = Value::boolean(true);
  selt::ok(leaf.get("missing") == nullptr && leaf.size() == 0 && leaf.entries().empty(),
           "leaf collection reads remain empty");
  {
    Value deep = Value::text("bottom");
    for (int i = 0; i < 10000; ++i) deep = Value::list({deep});
    Value shared_deep = deep;
    deep = Value::none();
    selt::eq(shared_deep.size(), 1u, "deep optional payload stays alive through its alias");
    // Scope exit exercises iterative destruction beyond the language depth cap.
  }

  Value v = Value::none();
  v.set("b", Value::text("1"));
  v.set("a", Value::text("2"));
  selt::eq(v.keys()[0], std::string("b"), "children keep insertion order, not sorted order");

  // Re-assigning an existing key keeps its original position — order is
  // normative and observable through INDEXES, JOIN, MAP and the dump.
  v.set("b", Value::text("9"));
  selt::eq(v.keys()[0], std::string("b"), "re-assignment does not move a key");
  selt::eq(v.size(), 2u, "re-assignment does not add a key");

  selt::eq(Value::text("hi").dump(), std::string("t\"hi\""), "text dump");
  selt::eq(Value::boolean(true).dump(), std::string("TRUE"), "bool dump");
  selt::eq(Value::none().dump(), std::string("-"), "none dump");
  selt::eq(Value::bin(std::string("\x00\xff", 2)).dump(), std::string("b00ff"), "bin dump");
  selt::eq(Value::text("a\nb\"c\\d").dump(), std::string("t\"a\\nb\\\"c\\\\d\""),
           "the dump escape set");
  selt::eq(Value::text(std::string("\x01", 1)).dump(), std::string("t\"\\u0001\""),
           "control characters become \\uXXXX");

  // Numbers are not normalised by EQL — it is structural.
  selt::ok(!Value::text("5.00").eql(Value::text("5")), "EQL does not normalise numbers");
  selt::ok(Value::text("5").eql(Value::text("5")), "EQL on equal text");

  Value a = Value::none();
  a.set("1", Value::text("x"));
  Value b = Value::none();
  b.set("2", Value::text("x"));
  selt::ok(!a.eql(b), "EQL compares keys, not only values");

  selt::eq(Value::num("007").scalar(), std::string("7"), "Value::num canonicalises");
  selt::eq(Value::integer(-3).scalar(), std::string("-3"), "Value::integer");
  selt::raises("E_NOT_NUM", [] { Value::num("x"); }, "Value::num rejects non-numbers");
  selt::raises("E_UTF8", [] { Value::text("\xff"); }, "Value::text rejects bad UTF-8");

  // Scalar context: a value with no scalar takes its first child's, recursively.
  Value nested = Value::none();
  nested.set("1", Value::text("first"));
  nested.set("2", Value::text("second"));
  selt::eq(nested.as_text(), std::string("first"), "scalar context takes the first child");
  selt::raises("E_NULL", [] { Value::none().as_text(); }, "no scalar and no children is null");
  selt::raises("E_NO_SCALAR", [] { Value::list({}).as_text(); }, "empty list has no scalar");
  selt::raises("E_NOT_BOOL", [] { Value::text("TRUE").as_bool(); }, "there is no truthiness");
}

void test_host_api() {
  selt::section("host API");

  selt::eq(dump_of("1 + 2"), std::string("t\"3\""), "compile and run");
  selt::eq(dump_of("(1, 2)"), std::string("-{\"1\"=t\"1\", \"2\"=t\"2\"}"), "a list is NONE plus children");
  selt::eq(dump_of("A = 1; A[2] = \"x\"; A"), std::string("t\"1\"{\"2\"=t\"x\"}"),
           "a value can have both a scalar and children");

  // The context is mutated in place, and host code reads it with the same API
  // the interpreter uses.
  Value ctx = Value::none();
  ctx.set("TOTAL", Value::num("59.97"));
  selt::eq(evaluate("TOTAL > 10.00", ctx).boolean_scalar(), true, "host-supplied context");
  evaluate("SEEN = TOTAL * 2", ctx);
  selt::eq(ctx.get("SEEN")->scalar(), std::string("119.94"), "assignments land in the context");

  const Program p = compile("IF(A > B, A, C)");
  const std::vector<std::string> deps = p.dependencies();
  selt::eq(deps.size(), 3u, "dependencies finds every variable read");
  selt::eq(deps[0], std::string("A"), "dependencies are sorted");

  // A variable the program assigns before reading is not an input.
  selt::eq(compile("X = 1; X + Y").dependencies().size(), 1u,
           "an assigned variable is not a dependency");
  // A binder is not an input either.
  selt::eq(compile("ALL(ITEMS, ITEM, ITEM > 0)").dependencies().size(), 1u,
           "an aggregate binder is not a dependency");

  selt::raises("E_UNKNOWN_FUNC", [] { compile("NOPE(1)"); }, "unknown functions fail at compile time");
  selt::raises("E_ARITY", [] { compile("LEN(1, 2)"); }, "arity is checked at compile time");
  selt::raises("E_ARITY", [] { compile("COND(TRUE, 1, FALSE, 2)"); }, "COND needs an odd count");
  selt::raises("E_SYNTAX", [] { compile("1 < 2 < 3"); }, "comparisons do not chain");
  selt::raises("E_BAD_ASSIGN", [] { compile("1 = 2"); }, "assignment targets are checked");

  selt::ok(function_names().size() > 40, "the function table is populated");

  // Position and code are the contract; the message is not.
  try {
    compile("1 +\n  X").run();
    selt::ok(false, "expected E_UNDEF_VAR");
  } catch (const SelError& e) {
    selt::eq(e.code(), std::string("E_UNDEF_VAR"), "error code");
    selt::eq(e.line(), 2, "error line");
    selt::eq(e.col(), 3, "error column");
  }
}

// T12 (CPP-C44, CPP-C13, CPP-C11, CPP-C15): the flow-sensitive dependencies()
// contract (spec/SPEC.md §8), the host-function argument reader, registration
// beside compilation, and RECORD's key-before-copy order.
std::string deps_of(const std::string& src) {
  std::string out;
  for (const std::string& d : compile(src).dependencies()) out += (out.empty() ? "" : " ") + d;
  return out.empty() ? "-" : out;
}

void test_dependencies_flow_and_host_boundary() {
  selt::section("T12 dependencies, host arguments, registration");

  // A read that can happen before a DEFINITE assignment is a dependency.
  selt::eq(deps_of("A + 1; A = 2"), std::string("A"), "read before assign");
  selt::eq(deps_of("X += 1"), std::string("X"), "a compound assignment reads its target");
  selt::eq(deps_of("A[1] += 1"), std::string("A"), "an indexed compound assignment reads its target");
  selt::eq(deps_of("A[1] = 2"), std::string("-"), "a plain indexed assignment creates A");
  selt::eq(deps_of("A[K] = 2"), std::string("K"), "and reads only its index");
  selt::eq(deps_of("A = A + 1"), std::string("A"), "the right side runs before the store");
  selt::eq(deps_of("A = 1; A + B"), std::string("B"), "assign then read");

  // Only an assignment that is certain to run counts.
  selt::eq(deps_of("IF(X, A = 1, 0); A"), std::string("A X"), "one branch is not enough");
  selt::eq(deps_of("IF(X, A = 1, A = 2); A"), std::string("X"), "both branches assign");
  selt::eq(deps_of("X AND (A = 1); A"), std::string("A X"), "the right side of AND may not run");
  selt::eq(deps_of("X OR (A = 1); A"), std::string("A X"), "nor OR");
  selt::eq(deps_of("X ?? (A = 1); A"), std::string("A X"), "nor ??");
  selt::eq(deps_of("X ??? (A = 1); A"), std::string("A X"), "nor ???");
  selt::eq(deps_of("MAP(L, A = _); A"), std::string("A L"), "an aggregate body may run zero times");
  selt::eq(deps_of("COND(X, A = 1, Y, A = 2, A = 3); A"), std::string("X Y"),
           "COND with a default that every branch assigns");
  selt::eq(deps_of("COND(X, A = 1, Y, A = 2, 0); A"), std::string("A X Y"),
           "a COND default that does not");
  selt::eq(deps_of("LEFT(\"abc\", (N = 2)); N"), std::string("-"), "an argument's assignment is definite");
  selt::eq(deps_of("IF(X, 1, 2)"), std::string("X"), "a plain conditional");
  selt::eq(deps_of("(A = 1) AND A"), std::string("-"), "AND's right side sees its left side's assignment");
  selt::eq(deps_of("ALL(L, I, I > LIM)"), std::string("L LIM"), "binders are not dependencies");

  // The cap stays: dependencies() of a program that could not be evaluated.
  {
    std::string deep = "A";
    for (int i = 0; i < 300; i++) deep += "+A";
    selt::raises("E_DEPTH", [&] { compile(deep).dependencies(); }, "dependencies keep the depth cap");
  }

  // CPP-C13: a host function that reads an argument the call does not have.
  register_function("T12_OPT", 1, 2, [](HostArgs& a) { return a.val(a.count() > 1 ? 1 : 5); });
  register_function("T12_OPT_TEXT", 1, 2, [](HostArgs& a) { return Value::text(a.text(3)); });
  register_function("T12_OPT_POS", 1, 2, [](HostArgs& a) { a.pos_of(-1); return Value::text("x"); });
  selt::raises("E_BAD_ARG", [] { compile("T12_OPT(1)").run(); }, "an argument read past the count is E_BAD_ARG");
  selt::raises("E_BAD_ARG", [] { compile("T12_OPT_TEXT(1)").run(); }, "text() reads are checked too");
  selt::raises("E_BAD_ARG", [] { compile("T12_OPT_POS(1)").run(); }, "and pos_of");
  selt::eq(dump_of("T12_OPT(1, 2)"), std::string("t\"2\""), "an argument the call has is read normally");
  try {
    compile("1 + T12_OPT(1)").run();
    selt::ok(false, "expected E_BAD_ARG");
  } catch (const SelError& e) {
    selt::eq(e.line(), 1, "the error is at the call: line");
    selt::eq(e.col(), 5, "the error is at the call: column");
  }

  // CPP-C11: registering while other threads compile and run.
  register_function("T12_RACE", 0, 1, [](HostArgs&) { return Value::text("v0"); });
  {
    std::atomic<bool> stop{false};
    std::atomic<int> bad{0};
    std::vector<std::thread> readers;
    for (int t = 0; t < 3; ++t) {
      readers.emplace_back([&] {
        while (!stop.load()) {
          try {
            Value ctx = Value::none();
            const Value v = compile("T12_RACE(1)").run(ctx);
            if (v.scalar().empty() || v.scalar()[0] != 'v') bad++;
          } catch (...) {
            bad++;
          }
        }
      });
    }
    for (int i = 0; i < 200; ++i) {
      const std::string tag = "v" + std::to_string(i + 1);
      register_function("T12_RACE", 0, 1, [tag](HostArgs&) { return Value::text(tag); });
      (void)function_names();
    }
    stop = true;
    for (auto& th : readers) th.join();
    selt::eq(bad.load(), 0, "registering beside compiling threads is safe");
  }

  // CPP-C15: the key is checked before the value is copied.
  {
    Value v = Value::text("x");
    for (int i = 0; i < 300; i++) {
      Value p = Value::none();
      p.set("1", v);
      v = p;
    }
    Value ctx = Value::none();
    ctx.set("V", v);
    const auto at = [&](const std::string& src) {
      try {
        compile(src).run(ctx);
        return std::string("no error");
      } catch (const SelError& e) {
        return e.code() + " " + std::to_string(e.line()) + ":" + std::to_string(e.col());
      }
    };
    selt::eq(at("RECORD(TRUE, V)"), std::string("E_NOT_TEXT 1:8"), "RECORD: the key's error comes first");
    selt::eq(at("RECORD(\"k\", V)"), std::string("E_DEPTH 1:1"), "RECORD: a good key then meets the depth cap");
  }
}

// Left-to-right evaluation is observable through which operand's position an
// error reports. C++ leaves the order of function arguments unspecified, so this
// is a standing trap rather than a one-off bug; the differential fuzzer found it
// once already.
void test_evaluation_order() {
  selt::section("evaluation order");

  auto pos_of = [](const std::string& src) {
    try {
      compile(src).run();
    } catch (const SelError& e) {
      return e.col();
    }
    return -1;
  };

  selt::eq(pos_of("TRUE $== FALSE"), 1, "$== reports the left operand");
  selt::eq(pos_of("TRUE + 1"), 1, "+ reports the left operand");
  selt::eq(pos_of("1 + TRUE"), 5, "+ reports the right operand when the left is fine");
  selt::eq(pos_of("TRUE < 1"), 1, "< reports the left operand");
  selt::eq(pos_of("TRUE XOR 1"), 10, "XOR reports the non-boolean operand");
  selt::eq(pos_of("TRUE BAND \"x\""), 1, "BAND reports the left operand");

  // Short-circuiting means the right side is never reached.
  selt::eq(dump_of("FALSE AND (1/0) EQL TRUE"), std::string("FALSE"), "AND short-circuits");
  selt::eq(dump_of("TRUE OR (1/0) EQL TRUE"), std::string("TRUE"), "OR short-circuits");
}

void test_relational_optimizations() {
  selt::section("relational optimizations");

  const Value shaped = evaluate("RECORD(\"a\", 1, \"b\", 2)");
  selt::ok(shaped.shape() != nullptr, "RECORD uses a shared shape");
  selt::eq(shaped.slot(0)->scalar(), std::string("1"), "shaped records expose slot zero");
  selt::eq(shaped.slot(1)->scalar(), std::string("2"), "shaped records expose slot one");
  const Value shaped_again = evaluate("RECORD(\"a\", 9, \"b\", 8)");
  selt::ok(shaped.shape().get() == shaped_again.shape().get(),
           "equal record layouts reuse one immutable shape");
  selt::eq(shaped.get("b")->scalar(), std::string("2"),
           "shaped lookup uses the key map without rebuilding entries");

  const Value alias_source = evaluate("RECORD(\"id\", 1, \"name\", \"Ada\")");
  const Value alias_one = ensure_row_table_alias(alias_source, "CUSTOMERS");
  const Value alias_two = ensure_row_table_alias(alias_source, "CUSTOMERS");
  selt::ok(alias_one.shape() && alias_one.shape().get() == alias_two.shape().get(),
           "repeated shaped aliases reuse one cached destination layout");

  const Value duplicate = Value::record(
      {"a", "b", "a"}, {Value::text("1"), Value::text("2"), Value::text("3")});
  selt::ok(!duplicate.shape(), "duplicate record keys retain fallback representation");
  selt::ok(duplicate.keys() == std::vector<std::string>({"a", "b"}),
           "duplicate record keys keep first insertion positions");
  selt::eq(duplicate.get("a")->as_text(), std::string("3"),
           "duplicate record keys update the first slot");

  const Value flat_list = Value::list({Value::integer(4), Value::integer(5)});
  selt::eq(flat_list.storage().size(), 2u, "lists retain flat vector storage");
  selt::eq(flat_list.get("2")->scalar(), std::string("5"),
           "list lookup uses a parsed storage slot");
  selt::eq(flat_list.slot(0)->scalar(), std::string("4"), "lists expose slot zero");

  Value join_shape_context = Value::none();
  join_shape_context.set(
      "LEFT", Value::list({evaluate("RECORD(\"id\", 1)"), evaluate("RECORD(\"id\", 2)")}));
  join_shape_context.set(
      "RIGHT", Value::list({evaluate("RECORD(\"id\", 1)"), evaluate("RECORD(\"id\", 2)")}));
  const Value joined_rows = evaluate(
      "LINK(LEFT, RIGHT, _1[\"id\"] == _2[\"id\"])", join_shape_context);
  const Value* joined_one = joined_rows.get("1");
  const Value* joined_two = joined_rows.get("2");
  selt::ok(joined_one && joined_two && joined_one->shape() &&
               joined_one->shape().get() == joined_two->shape().get(),
           "join rows reuse the precompiled output shape");

  const std::string wide_integer(40, '9');
  selt::eq(Value::num(wide_integer).as_text(), wide_integer,
           "wide decimals bypass the int128 small-value probe safely");

  selt::eq(
      dump_of("LIST(RECORD(\"x\", 1), RECORD(\"x\", 5), RECORD(\"x\", 3), "
              "RECORD(\"x\", 4), RECORD(\"x\", 2)) .> TOP_BY(_[\"x\"], \"DESC\", 3)"),
      std::string("-{\"1\"=-{\"x\"=t\"5\"}, \"2\"=-{\"x\"=t\"4\"}, \"3\"=-{\"x\"=t\"3\"}}"),
      "TOP_BY keeps the bounded top set in final order");

  Value join_context = Value::none();
  join_context.set("A", Value::list({evaluate("RECORD(\"id\", 1)")}));
  join_context.set("B", Value::list({evaluate("RECORD(\"id\", 2)")}));
  const Value joined =
      evaluate("LINK_LEFT(A, B, _1[\"id\"] == _2[\"id\"])", join_context);
  const Value* right = joined.get("1")->get("_2");
  selt::ok(right && right->get("id") && right->get("id")->is_null(),
           "LINK_LEFT materializes null right fields");

  Value mixed_shape_context = Value::none();
  mixed_shape_context.set(
      "LEFT", Value::list({evaluate("RECORD(\"id\", 1, \"x\", \"first\")"),
                           evaluate("RECORD(\"id\", 2, \"z\", \"second\")")}));
  mixed_shape_context.set(
      "RIGHT", Value::list({evaluate("RECORD(\"id\", 1)"),
                            evaluate("RECORD(\"id\", 2)")}));
  const Value mixed_join =
      evaluate("LINK(LEFT, RIGHT, _1[\"id\"] == _2[\"id\"])", mixed_shape_context);
  // Each row is built from its own pair (spec §7.4, SEL-0053): the second
  // row carries its own `z` and no `x` -- not a NULL `x` from the first row.
  const Value* second = mixed_join.get("2");
  selt::ok(second && !second->get("x") && second->get("z") && second->get("z")->as_text({}) == "second",
           "mixed-shape joins promote each pair's own fields");

  Value sort_context = Value::none();
  sort_context.set(
      "A", Value::list({evaluate("RECORD(\"k\", \"b\", \"n\", RECORD(\"x\", 1))"),
                        evaluate("RECORD(\"k\", \"a\", \"n\", RECORD(\"x\", 2))")}));
  evaluate("B = SORT(A); B[1][\"n\"][\"x\"] = 9", sort_context);
  selt::eq(sort_context.get("A")->get("1")->get("n")->get("x")->as_text(),
           std::string("1"), "plain SORT keeps nested source values detached");

  const auto assert_collection_copy_is_detached = [](const std::string& producer,
                                                      const std::string& label) {
    Value context = Value::none();
    context.set("A", Value::list({evaluate("RECORD(\"n\", RECORD(\"x\", 1))")}));
    evaluate("B = " + producer + "; B[1][\"n\"][\"x\"] = 9", context);
    selt::eq(context.get("A")->get("1")->get("n")->get("x")->as_text(),
             std::string("1"), label);
  };
  assert_collection_copy_is_detached("A .> TAKE(1)",
                                     "TAKE keeps nested source values detached");
  assert_collection_copy_is_detached("A .> DROP(0)",
                                     "DROP keeps nested source values detached");
  assert_collection_copy_is_detached("MAP(A, _)",
                                     "MAP keeps nested source values detached");
  assert_collection_copy_is_detached("FILTER(A, TRUE)",
                                     "FILTER keeps nested source values detached");

  Value assignment_context = Value::none();
  assignment_context.set(
      "A", Value::list({evaluate("RECORD(\"n\", RECORD(\"x\", 1))")}));
  evaluate("B = A; B[1][\"n\"][\"x\"] = 9", assignment_context);
  selt::eq(assignment_context.get("A")->get("1")->get("n")->get("x")->as_text(),
           std::string("1"), "assignment keeps nested source values detached");

  const NodePtr folded = optimize_ast_logical(compile("1 + 2").ast());
  selt::ok(folded->t == NT::Num && folded->s == "3", "optimizer folds literal arithmetic");

  const NodePtr pipeline = optimize_ast_logical(compile(
      "A .> FILTER(_[\"x\"] > 0) .> FILTER(_[\"y\"] > 0) .> "
      "SORT_BY(_[\"x\"], \"DESC\") .> TAKE(2)").ast());
  std::vector<NodePtr> steps;
  NodePtr cursor = pipeline;
  while (cursor && cursor->t == NT::Call && !cursor->items.empty()) {
    steps.push_back(cursor);
    cursor = cursor->items.front();
  }
  std::reverse(steps.begin(), steps.end());
  selt::ok(steps.size() == 2 && steps[0]->s == "FILTER" && steps[1]->s == "TOP_BY",
           "optimizer fuses filters and sort plus take");

  // A FILTER moves in front of a MAP, a sort or a SELECT_COLS only when a
  // later step renumbers the rows again without reading `_K`: FILTER keeps its
  // input's keys and the three renumber (spec §7.3), so at the end of a
  // pipeline the swap would change the answer's keys.
  auto logical_names = [](const std::string& source) {
    const NodePtr ast = optimize_ast_logical(compile(source).ast());
    std::vector<std::string> out;
    NodePtr cursor = ast;
    while (cursor && cursor->t == NT::Call && !cursor->items.empty()) {
      out.push_back(cursor->s);
      cursor = cursor->items.front();
    }
    std::reverse(out.begin(), out.end());
    return out;
  };
  using Names = std::vector<std::string>;
  const std::string rows = "((RECORD(\"x\", 1), RECORD(\"x\", 2)))";
  const std::string map = " .> MAP(RECORD(\"x\", _[\"x\"], \"heavy\", _[\"x\"] + 1))";
  selt::ok(logical_names(rows + map + " .> FILTER(_[\"x\"] > 0) .> MAP(_[\"heavy\"])") == Names{"FILTER", "MAP", "MAP"},
           "MAP filter pushdown");
  selt::ok(logical_names(rows + map + " .> FILTER(_[\"x\"] > 0)") == Names{"MAP", "FILTER"},
           "MAP filter pushdown keeps the keys at the end of a pipeline");
  selt::ok(logical_names(rows + map + " .> FILTER(_[\"x\"] > 0) .> MAP(_K)") == Names{"MAP", "FILTER", "MAP"},
           "MAP filter pushdown keeps the keys a later step reads");
  selt::ok(logical_names(rows + map + " .> FILTER(_[\"x\"] > 0) .> FILTER(_[\"x\"] > 1) .> TAKE(1)")
               == Names{"FILTER", "MAP", "TAKE"},
           "MAP filter pushdown after the FILTERs fuse");
  selt::ok(logical_names(rows + map + " .> FILTER(_[\"heavy\"] > 0)") == Names{"MAP", "FILTER"},
           "MAP filter dependency guard");
  selt::ok(logical_names("(1, 2) .> SORT() .> FILTER(_ > 0) .> TAKE(1)") == Names{"FILTER", "TOP"},
           "SORT filter pushdown");
  selt::ok(logical_names("(1, 2) .> SORT() .> FILTER(_ > 0)") == Names{"SORT", "FILTER"},
           "SORT filter pushdown keeps the keys at the end of a pipeline");
  selt::ok(logical_names(rows + " .> SELECT_COLS(\"x\") .> FILTER(_[\"x\"] > 0) .> MAP(_[\"x\"])")
               == Names{"FILTER", "SELECT_COLS", "MAP"},
           "SELECT_COLS filter pushdown");
  selt::ok(logical_names(rows + " .> SELECT_COLS(\"x\") .> FILTER(_[\"x\"] > 0)") == Names{"SELECT_COLS", "FILTER"},
           "SELECT_COLS filter pushdown keeps the keys at the end of a pipeline");

  // The evaluator is the depth authority (spec §6.4): a tree at the cap is
  // handed back as written, the same NodePtr, so the evaluator reports the
  // E_DEPTH it would have reported for the program as written.
  {
    std::string chain = "1";
    for (int i = 0; i < 200; i++) chain += " + 1";
    const NodePtr deep = compile(chain).ast();
    selt::ok(optimize_ast_logical(deep) == deep && optimize_ast_in_memory(deep) == deep,
             "a tree past the depth cap is returned untouched");
    std::string shallow = "1";
    for (int i = 0; i < 100; i++) shallow += " + 1";
    const NodePtr folded_chain = optimize_ast_logical(compile(shallow).ast());
    selt::ok(folded_chain->t == NT::Num && folded_chain->s == "101",
             "a tree under the depth cap is still folded");
  }

  const NodePtr physical = optimize_ast_in_memory(compile(
      "A .> MAP(RECORD(\"x\", _[\"x\"], \"y\", _[\"y\"]))").ast());
  selt::ok(physical->t == NT::Call && physical->s == "MAP" &&
               physical->items.size() == 2 &&
               physical->items[1]->t == NT::Call &&
               physical->items[1]->s == "RECORD",
           "physical optimizer leaves a multi-field MAP record as RECORD");

  auto optimized_steps = [](const std::string& source) {
    const NodePtr ast = optimize_ast_in_memory(compile(source).ast());
    std::vector<NodePtr> out;
    NodePtr cursor = ast;
    while (cursor && cursor->t == NT::Call && !cursor->items.empty()) {
      out.push_back(cursor);
      cursor = cursor->items.front();
    }
    std::reverse(out.begin(), out.end());
    return out;
  };

  // The physical tree never moves a FILTER across a LINK (spec §7.4;
  // SEL-0054): a FILTER moved onto a side renumbered the joined rows, skipped
  // the join keys of the rows it dropped, and read relation names under
  // explicit binders. The join tests conjuncts itself, at run time, where it
  // can prove that is the same.
  const auto fixed_point = optimized_steps(
      "ORDERS .> FILTER(_[\"status\"] $== \"ACTIVE\")"
      " .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])"
      " .> FILTER(_[\"orders\"][\"status\"] $== \"ACTIVE\")");
  selt::ok(fixed_point.size() == 3 && fixed_point[0]->s == "FILTER" &&
               fixed_point[1]->s == "LINK" && fixed_point[2]->s == "FILTER",
           "a FILTER before a LINK stays before it, one after stays after");
  for (const char* filter : {
           " .> FILTER(_[\"orders\"][\"status\"] $== \"ACTIVE\" AND _[\"customers\"][\"country\"] $== \"DE\")",
           " .> FILTER(_[\"customers\"][\"country\"] $== \"DE\")",
           " .> FILTER(_[\"orders\"][\"status\"] $== \"ACTIVE\")"}) {
    const auto steps = optimized_steps(
        std::string("ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])") + filter);
    selt::ok(steps.size() == 2 && steps[0]->s == "LINK" && steps[0]->items[1]->t == NT::Var &&
                 steps[1]->s == "FILTER",
             std::string("no FILTER crosses a LINK:") + filter);
  }
  {
    // Whether a FILTER's keys can be seen, for the join's pre-filter: a step
    // that renumbers without reading `_K` hides them; the end does not.
    const std::string join = "ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])"
                             " .> FILTER(_[\"orders\"][\"status\"] $== \"A\")";
    const auto hidden = optimized_steps(join + " .> MAP(1)");
    const auto observed = optimized_steps(join);
    selt::ok(hidden[1]->items[1]->keys_unobserved && !observed[1]->items[1]->keys_unobserved,
             "a FILTER followed by a MAP has unobserved keys, one ending the pipeline observed ones");
  }

  const auto external_root = optimized_steps(
      "ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])"
      " .> FILTER(FOO[\"orders\"][\"status\"] $== \"ACTIVE\")");
  selt::ok(external_root.size() == 2 && external_root[0]->s == "LINK" &&
               external_root[1]->s == "FILTER",
           "external qualified root is not pushed through LINK");

  const auto group_key = optimized_steps(
      "ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])"
      " .> FILTER(_[\"orders\"][\"status\"] $== _K)");
  selt::ok(group_key.size() == 2 && group_key[0]->s == "LINK" &&
               group_key[1]->s == "FILTER",
           "_K is an unknown join dependency");

  // The binders are scoped to the predicate (spec §7.4), so `O["x"]` after
  // the LINK is E_UNDEF_VAR as written and is left where it is.
  const auto bare_left_binder = optimized_steps(
      "ORDERS .> LINK(CUSTOMERS, O, C, O[\"customer_id\"] == C[\"id\"])"
      " .> FILTER(O[\"status\"] $== \"ACTIVE\")");
  selt::ok(bare_left_binder.size() == 2 && bare_left_binder[0]->s == "LINK" &&
               bare_left_binder[1]->s == "FILTER" &&
               bare_left_binder[0]->items[0]->t == NT::Var,
           "a LINK binder read after the LINK is not pushed");

  const auto bare_right_binder = optimized_steps(
      "ORDERS .> LINK(CUSTOMERS, O, C, O[\"customer_id\"] == C[\"id\"])"
      " .> FILTER(C[\"name\"] $== \"x\")");
  selt::ok(bare_right_binder.size() == 2 && bare_right_binder[0]->s == "LINK" &&
               bare_right_binder[1]->s == "FILTER" &&
               bare_right_binder[0]->items[1]->t == NT::Var,
           "a LINK right binder read after the LINK is not pushed");

  const auto bare_source_name = optimized_steps(
      "ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"])"
      " .> FILTER(ORDERS[\"status\"] $== \"ACTIVE\")");
  selt::ok(bare_source_name.size() == 2 && bare_source_name[0]->s == "LINK" &&
               bare_source_name[1]->s == "FILTER" &&
               bare_source_name[0]->items[0]->t == NT::Var,
           "a relation name read after the LINK is not pushed");
}

void test_structural_hash_identity() {
  selt::section("structural hash identity");
  const auto leaf = Value::text("x");
  auto packed = Value::list({leaf});
  auto shaped = Value::record({"1"}, {leaf});
  auto fallback = Value::none();
  fallback.set("1", leaf);
  const auto hash = packed.structural_hash();
  selt::eq(hash, shaped.structural_hash(), "list/record hash identity");
  selt::eq(hash, fallback.structural_hash(), "list/fallback hash identity");
  selt::ok(packed.eql(shaped), "cross-storage equality");
  packed.entries();
  selt::eq(hash, packed.structural_hash(), "materialization preserves hash");
  for (const auto& spelling : {std::string("1"), std::string("1.0"),
                               std::string("01"), std::string("-0")}) {
    auto value = Value::text(spelling);
    const auto before = value.structural_hash();
    as_dec(value, {});
    selt::eq(before, value.structural_hash(), "decimal cache preserves hash");
    selt::ok(value.eql(Value::text(spelling)), "decimal cache preserves identity");
  }
  for (const auto& spelling : {std::string("1") + std::string(100, '0'),
                               std::string("0.") + std::string(100, '0')}) {
    auto number = Value::num(spelling);
    const auto before = number.structural_hash();
    selt::eq(before, Value::text(spelling).structural_hash(), "large decimal hash identity");
    number.as_text();
    selt::eq(before, number.structural_hash(), "decimal rendering preserves hash");
  }
}

void test_math_plan() {
  selt::section("math plan");

  const auto p = compile("a + b * c");
  const auto phys = p.physical_ast();
  selt::ok(phys->math_plan != nullptr, "math_plan is attached to root +");
  selt::ok(phys->r->math_plan == nullptr, "child * does not have separate math_plan");
  selt::eq(phys->math_plan->steps.size(), 5u, "5 steps for a + b * c");
  auto root = Value::record({"A", "B", "C"}, {Value::num("2"), Value::num("3"), Value::num("4")});
  selt::eq(p.run(root).as_text(), std::string("14"), "evaluates to 14");

  // Copy propagation
  const auto p_id = compile("x + 0");
  const auto phys_id = p_id.physical_ast();
  selt::ok(phys_id->math_plan != nullptr, "math_plan is attached to x + 0");
  // The load, then the coercion the dropped `+ 0` would have done: a copy-
  // propagated operand is still the raw value the load produced (spec §6.2).
  selt::eq(phys_id->math_plan->steps.size(), 2u, "load + coerce for x + 0");
  selt::eq(phys_id->math_plan->steps[0].name, std::string("X"), "name is X");
  selt::ok(phys_id->math_plan->steps[1].op == MathOp::Coerce, "x + 0 ends in a Coerce step");
  auto root_id = Value::record({"X"}, {Value::num("42.50")});
  selt::eq(p_id.run(root_id).as_text(), std::string("42.50"), "scale 42.50 preserved");

  // Type check on identity copy propagation
  auto root_bad = Value::record({"X"}, {Value::text("hello")});
  selt::raises("E_NOT_NUM", [&] { p_id.run(root_bad); }, "E_NOT_NUM on invalid x");

  // Scale preservation (x + 0.00)
  const auto p_scale = compile("x + 0.00");
  const auto phys_scale = p_scale.physical_ast();
  selt::eq(phys_scale->math_plan->steps.size(), 3u, "3 steps for x + 0.00");
  auto root_scale = Value::record({"X"}, {Value::num("5")});
  selt::eq(p_scale.run(root_scale).as_text(), std::string("5.00"), "scale 5.00 preserved");

  // Left-to-right evaluation order
  const auto p_order = compile("(1 / 0) + UNDEFINED");
  selt::raises("E_DIV_ZERO", [&] { p_order.run(); }, "E_DIV_ZERO before UNDEFINED");

  // Builtins
  const auto p_round = compile("ROUND(a + b, 2)");
  selt::ok(p_round.physical_ast()->math_plan != nullptr, "ROUND has math_plan");
  auto root_round = Value::record({"A", "B"}, {Value::num("1.234"), Value::num("2.345")});
  selt::eq(p_round.run(root_round).as_text(), std::string("3.58"), "ROUND result 3.58");

  const auto p_min = compile("MIN(a, b, c)");
  auto root_min = Value::record({"A", "B", "C"}, {Value::num("10"), Value::num("5"), Value::num("8")});
  selt::eq(p_min.run(root_min).as_text(), std::string("5"), "MIN result 5");
}


// Evaluate, then coerce (spec §6.2), scratchpad safety and recovery
// (CPP-C2, C3, C9, C19, C25, C37, C45, C46, C6, C18).
std::string err_of(const std::string& src, const Value* root = nullptr) {
  try {
    if (root) { Value r = *root; compile(src).run(r); } else { compile(src).run(); }
  } catch (const SelError& e) {
    return e.code() + "@" + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col);
  }
  return "ok";
}

void test_operand_order_and_plans() {
  selt::section("operand order and plans");

  // A planned and an unplanned spelling of one program must agree: every
  // operand is evaluated first, then coerced left to right.
  selt::eq(err_of("A = \"x\"; A + B"), std::string("E_UNDEF_VAR@1:14"), "planned: the later operand's evaluation error first");
  selt::eq(err_of("A = \"x\"; A + IF(TRUE, B, 1)"), std::string("E_UNDEF_VAR@1:23"), "unplanned: same error");
  selt::eq(err_of("A = \"x\"; A + 1/0"), std::string("E_DIV_ZERO@1:15"), "a later division error beats the earlier coercion");
  selt::eq(err_of("A = \"x\"; B = \"y\"; A + B"), std::string("E_NOT_NUM@1:19"), "both bad: the left is reported");
  // Copy-propagated `x + 0` still coerces x, before a later operand is looked at.
  selt::eq(err_of("A = \"x\"; (A + 0) + B"), std::string("E_NOT_NUM@1:11"), "x + 0 coerces x at once");
  selt::eq(err_of("A = \"x\"; 1 * A"), std::string("E_NOT_NUM@1:14"), "1 * x coerces x");
  // A strict function evaluates all its arguments before it coerces any.
  selt::eq(err_of("MAX(TRUE, U)"), std::string("E_UNDEF_VAR@1:11"), "MAX: undefined third-party before bool");
  selt::eq(err_of("A = \"x\"; ROUND(A, 2.5)"), std::string("E_NOT_NUM@1:16"), "ROUND: first argument before the scale");
  selt::eq(err_of("X = \"x\"; ROUND(IF(TRUE, X, 1), 2.5)"), std::string("E_NOT_NUM@1:16"), "ROUND unplanned: first argument first");
  selt::eq(err_of("X = \"x\"; POWER(IF(TRUE, X, 1), -1)"), std::string("E_NOT_NUM@1:16"), "POWER unplanned: first argument first");
  // The operand a later leaf mutates is the one coerced (values alias).
  selt::eq(dump_of("A = LIST(1,2); A + LEN((A[1] = 10))"), std::string("t\"12\""), "an operand sees a later mutation");
}

void test_plan_scratchpad() {
  selt::section("plan scratchpad");

  // CPP-C3: a nested plan in a leaf grows the scratchpad under the outer plan.
  std::string inner = "V";
  for (int i = 0; i < 40; i++) inner += "+V";
  selt::eq(dump_of("V = 1; X = 5; X + LEN(" + inner + ")"), std::string("t\"7\""),
           "an outer plan survives a nested one that reallocates the scratchpad");

  // CPP-C9: a fold allocates two slots per argument; 16-bit slots wrapped at 32,769.
  std::string args = "A";
  for (int i = 1; i < 33000; i++) args += ",A";
  selt::eq(dump_of("A = 1; MAX(" + args + ")"), std::string("t\"1\""), "33,000-argument MAX");
  selt::eq(dump_of("A = 1; MIN(" + args + ")"), std::string("t\"1\""), "33,000-argument MIN");

  // CPP-C2: compound assignment when the right side creates variables.
  selt::eq(dump_of("A = 1; A += (B = 1); A"), std::string("t\"2\""), "A += (B = 1)");
  selt::eq(dump_of("A = 1; A += (B = 1, C = 2, D = 3, E = 4, F = 5, G = 6); A"), std::string("t\"2\""),
           "the root vector reallocates under the right side");
  selt::eq(dump_of("A = \"a\"; A &= (B = \"x\"); A"), std::string("t\"ax\""), "A &= (B = \"x\")");

  // CPP-C19: a caught error inside a plan must give its frame back.
  {
    Value root = Value::none();
    Context ctx(root);
    const auto prog = compile("(Q + Q + Q + Q + Q + Q + Q + Q + 1) ?? 0");
    const auto phys = prog.physical_ast();
    for (int i = 0; i < 100000; i++) {
      const Value v = eval_node(*phys, ctx);
      if (i == 0) selt::eq(v.as_text(), std::string("0"), "?? catches the plan's E_UNDEF_VAR");
    }
    selt::eq(ctx.math_scratchpad_top, size_t(0), "scratchpad top is 0 after 100,000 caught failures");
    selt::ok(ctx.math_scratchpad.size() < 4096, "and the scratchpad did not grow with them");
  }
}

void test_fusion_and_positions() {
  selt::section("fusion and positions");

  // CPP-C45: SORT_BY + TAKE fuse only for a literal count >= 1.
  const std::string rows = "L = LIST(RECORD(\"a\",1), RECORD(\"a\",2)); ";
  selt::eq(err_of(rows + "L .> SORT_BY(_[\"k\"]) .> TAKE(\"x\")").substr(0, 8), std::string("E_NO_KEY"),
           "the key error comes before a bad count");
  selt::eq(err_of(rows + "L .> SORT_BY(_[\"k\"]) .> TAKE(1/0)").substr(0, 8), std::string("E_NO_KEY"),
           "and before a count that divides by zero");
  selt::eq(err_of(rows + "L .> SORT_BY(_[\"k\"]) .> TAKE(0)").substr(0, 8), std::string("E_NO_KEY"), "TAKE(0) still evaluates the keys");
  selt::eq(dump_of(rows + "COUNT(L .> SORT_BY(_[\"a\"]) .> TAKE(1))"), std::string("t\"1\""), "a literal count still fuses");

  // CPP-C46: E_DEPTH from equality and hash paths carries a position.
  selt::eq(err_of("X = LIST(1); MAP(SPLIT(REPEAT(\"a,\",197),\",\"), (X = LIST(X); 1)); COUNT(DISTINCT(LIST(LIST(X))))").substr(0, 7),
           std::string("E_DEPTH"), "DISTINCT over a too-deep element raises");
  selt::ok(err_of("X = LIST(1); MAP(SPLIT(REPEAT(\"a,\",197),\",\"), (X = LIST(X); 1)); COUNT(DISTINCT(LIST(LIST(X))))").find("@0:0") == std::string::npos,
           "DISTINCT reports a position");
  selt::ok(err_of("X = LIST(1); MAP(SPLIT(REPEAT(\"a,\",197),\",\"), (X = LIST(X); 1)); COUNT(BUCKET(LIST(LIST(X)), _, COUNT(_)))").find("@0:0") == std::string::npos,
           "BUCKET reports a position");
  selt::ok(err_of("X = LIST(1); MAP(SPLIT(REPEAT(\"a,\",197),\",\"), (X = LIST(X); 1)); LIST(LIST(X)) IN LIST(LIST(LIST(X)))").find("@0:0") == std::string::npos,
           "IN reports a position");

  // A pipeline consumer reports the outermost written node, whatever fused.
  selt::eq(err_of("NOT TAKE(TAKE(LIST(1), 3), 2)"), std::string("E_NOT_BOOL@1:5"), "TAKE+TAKE keeps the outer position");
  selt::eq(err_of("NOT FILTER(LIST(1), TRUE)"), std::string("E_NOT_BOOL@1:5"), "a dropped FILTER(TRUE) keeps its position");
  // A bare `_` predicate can raise E_NOT_BOOL, so the FILTERs are not fused past the first error.
  selt::eq(err_of("LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)"), std::string("E_DIV_ZERO@1:25"),
           "FILTER+FILTER keeps the first error");
}

void test_tree_walkers_are_not_recursive() {
  selt::section("flat chains inside bodies");

  auto chain = [](const std::string& term, int n) {
    std::string out = term;
    for (int i = 1; i < n; i++) out += "+" + term;
    return out;
  };
  // CPP-C6: helpers that walk a body before the evaluator has looked at it.
  for (const std::string& shape : {std::string("MAP((1,2), %)"), std::string("FILTER((1,2), % > 0)"),
                                   std::string("SORT_BY((1,2), %)"), std::string("BUCKET((1,2), %, COUNT(_))"),
                                   std::string("TOP_BY((1,2), %, 1)"), std::string("SUM((1,2), %)")}) {
    std::string src = shape;
    src.replace(src.find('%'), 1, chain("_", 60000));
    selt::eq(err_of(src).substr(0, 7), std::string("E_DEPTH"), "60,000-term body in " + shape.substr(0, shape.find('(')));
  }
  selt::eq(err_of("A = LIST(RECORD(\"id\",1)); B = LIST(RECORD(\"id\",1)); FILTER(LINK(A, B, a[\"id\"] == b[\"id\"]), " +
                  chain("1", 100000) + " > 0)").substr(0, 7),
           std::string("E_DEPTH"), "100,000-term FILTER body over a LINK");
  // An empty collection never evaluates its body: no error, no crash.
  selt::eq(dump_of("COUNT(MAP(LIST(), " + chain("_", 60000) + "))"), std::string("t\"0\""), "an unevaluated deep body is fine");

  // CPP-C18: n literal keys must not be quadratic at compile time.
  std::string rec = "RECORD(";
  for (int i = 0; i < 40000; i++) rec += (i ? "," : "") + std::string("\"key") + std::to_string(i) + "\"," + std::to_string(i);
  rec += ")";
  const auto t0 = std::chrono::steady_clock::now();
  const auto prog = compile(rec);
  const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
  selt::ok(secs < 2.0, "compiling a 40,000-key RECORD takes well under 2 s (took " + std::to_string(secs) + ")");
  selt::eq(compile("RECORD(\"a\",1,\"a\",2)[\"a\"]").run().as_text(), std::string("2"), "a duplicate literal key still last-wins");
}


// The plain-vs-optimised probe (T00-B; tools/check-eval-equivalence.* are the
// other hosts'): every conformance program is evaluated as the parser wrote it
// and as run() evaluates it, and the value, the error with its position and the
// final context must be identical, on a Program reused twice. Test-only: it
// reaches the tree through the same internals the rest of this file uses.
std::string outcome_plain(const Program& prog, Value& root) {
  try {
    Context ctx(root);
    return "v " + eval_node(*prog.ast(), ctx).dump();
  } catch (const SelError& e) {
    return "e " + e.code() + "@" + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col);
  }
}

std::string outcome_run(const Program& prog, Value& root) {
  try {
    return "v " + prog.run(root).dump();
  } catch (const SelError& e) {
    return "e " + e.code() + "@" + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col);
  }
}

void test_plain_vs_optimised() {
  selt::section("plain vs optimised");

  std::vector<std::string> files;
  for (const char* dir : {"conformance", "../conformance"}) {
    std::error_code ec;
    if (!std::filesystem::is_directory(dir, ec)) continue;
    for (const auto& entry : std::filesystem::directory_iterator(dir)) {
      if (entry.path().extension() == ".selt") files.push_back(entry.path().string());
    }
    break;
  }
  std::sort(files.begin(), files.end());
  if (files.empty()) { selt::ok(true, "conformance/ not reachable from here: skipped"); return; }

  std::size_t programs = 0, diffs = 0;
  for (const std::string& path : files) {
    // The budget file builds values of the size caps on purpose, three times a
    // program here; conformance and tools/check-budgets.sh already run it, and
    // this comparison is about the optimiser, not about size.
    // Nor do the regex-portability cases (long subjects through the engine, 8 s a
    // pass) tell the optimiser anything the shorter regex cases do not.
    // The ambiguity cases (28b) run the regex validator on patterns that hit its
    // caps, dozens of times a pass; `sqlt`/conformance cover them and the plain-vs-
    // optimised question is not about regexes (under ASan this pass took over an hour).
    if (path.find("29-text-binary-budgets") != std::string::npos ||
        path.find("28-regex-portability") != std::string::npos ||
        path.find("28b-regex-ambiguity") != std::string::npos) continue;
#if defined(__SANITIZE_ADDRESS__)
    // Three cases build 8M-code-point texts (the REPLACE cap); ~100x slower under ASan.
    if (path.find("31-audit-findings") != std::string::npos) continue;
#endif
    std::ifstream in(path);
    std::string line, setup, source, section;
    std::string name;
    auto flush = [&]() {
      if (source.empty()) { setup.clear(); return; }
      while (!source.empty() && source.back() == '\n') source.pop_back();
      while (!setup.empty() && setup.back() == '\n') setup.pop_back();
      try {
        const Program prog = compile(source);
        auto fresh = [&]() {
          Value root = Value::none();
          if (!setup.empty()) compile(setup).run(root);
          return root;
        };
        Value a = fresh(), b = fresh(), c = fresh();
        const std::string plain = outcome_plain(prog, a) + " | " + a.dump();
        const std::string first = outcome_run(prog, b) + " | " + b.dump();
        const std::string second = outcome_run(prog, c) + " | " + c.dump();
        programs++;
        if (plain != first || plain != second) {
          diffs++;
          if (diffs <= 5) selt::ok(false, "plain vs optimised differ for " + name + ": " + plain.substr(0, 80) + " vs " + first.substr(0, 80));
        }
      } catch (const SelError&) {
        // A compile error or a setup error has no run to compare.
      }
      setup.clear();
      source.clear();
    };
    while (std::getline(in, line)) {
      if (line.rfind("### name: ", 0) == 0) { flush(); name = line.substr(10); section.clear(); continue; }
      if (line == "===") { flush(); section.clear(); continue; }
      if (line.rfind("--- ", 0) == 0) { section = line.substr(4); continue; }
      if (section == "setup") setup += line + "\n";
      else if (section == "source") source += line + "\n";
    }
    flush();
  }
  selt::ok(diffs == 0, "no conformance program differs between plain and optimised evaluation (" +
                           std::to_string(programs) + " compared, " + std::to_string(diffs) + " differ)");
}

// The host boundary (spec/SPEC.md §8, review 2026-09-25). C++ has no native
// conversion; what it shares with the other hosts is key validation and the
// depth cap on every walk of a host-built value.
void test_host_boundary() {
  selt::section("host boundary");
  selt::raises("E_UTF8", [] { sel::Value v = sel::Value::none(); v.set("\xff", sel::Value::text("x")); },
               "Value::set rejects a malformed key");
  selt::raises("E_UTF8", [] { sel::Value::text("\xc3"); }, "Value::text rejects a truncated sequence");
  auto deep = [](int levels) {
    sel::Value v = sel::Value::text("x");
    for (int i = 0; i < levels; i++) v = sel::Value::list({v});
    return sel::Value::list({v});
  };
  for (const char* src : {"COUNT(DEDUPE(A))", "COUNT(DISTINCT(A))", "COUNT(BUCKET(A, _, COUNT(_)))"}) {
    selt::raises("E_DEPTH", [&] {
      sel::Value ctx = sel::Value::none(); ctx.set("A", deep(250));
      sel::compile(src).run(ctx);
    }, std::string(src) + " over a value nested past the cap");
    sel::Value ctx = sel::Value::none(); ctx.set("A", deep(198));
    selt::eq(sel::compile(src).run(ctx).dump(), std::string("t\"1\""), std::string(src) + " just below the cap");
  }
  {
    auto p = sel::compile("A[K]");
    sel::Value a = sel::Value::none(); a.set("x", sel::Value::text("1")); a.set("y", sel::Value::text("2"));
    std::string got;
    for (const char* k : {"x", "y"}) {
      sel::Value ctx = sel::Value::none(); ctx.set("A", a); ctx.set("K", sel::Value::text(k));
      got += p.run(ctx).dump();
    }
    selt::eq(got, std::string("t\"1\"t\"2\""), "a compiled program reads the key of each run");
  }
  // Every public constructor (review 2026-09-28 HOST-12..20).
  using sel::Value;
  selt::raises("E_UTF8", [] { Value::record({"a\xff"}, {Value::text("1")}); }, "Value::record rejects a malformed key");
  selt::raises("E_UTF8", [] {
    Value::shaped(std::make_shared<sel::RecordShape>(std::vector<std::string>{"a\xff"}), {Value::text("1")});
  }, "Value::shaped rejects a malformed key");
  selt::raises("E_BAD_ARG", [] { Value::record({"a"}, {Value::text("1"), Value::text("2")}); },
               "Value::record with more values than keys");
  selt::raises("E_BAD_ARG", [] {
    Value::shaped(std::make_shared<sel::RecordShape>(std::vector<std::string>{"a", "b"}), {Value::text("1")});
  }, "Value::shaped with fewer values than keys");
  selt::raises("E_BAD_ARG", [] {
    Value::shaped(std::make_shared<sel::RecordShape>(std::vector<std::string>{"a", "a"}),
                  {Value::text("1"), Value::text("2")});
  }, "Value::shaped with a repeated key");
  selt::raises("E_BAD_ARG", [] { Value::shaped(nullptr, {}); }, "Value::shaped without a shape");
  selt::eq(Value::record({"a", "b", "a"}, {Value::text("1"), Value::text("2"), Value::text("3")}).dump(),
           std::string("-{\"a\"=t\"3\", \"b\"=t\"2\"}"), "Value::record keeps a repeated key once, last value");
  auto dec = [](bool neg, std::string digits, std::int32_t scale) {
    sel::Dec d; d.neg = neg; d.digits = std::move(digits); d.scale = scale; return d;
  };
  selt::raises("E_RANGE", [&] { Value::num(dec(false, "1", 1000001)); }, "a Dec with 1,000,001 fractional digits");
  selt::raises("E_RANGE", [&] { Value::num(dec(false, std::string(1000001, '1'), 0)); },
               "a Dec with 1,000,001 integer digits");
  selt::raises("E_BAD_ARG", [&] { Value::num(dec(false, "7", -1)); }, "a Dec with a negative scale");
  selt::raises("E_BAD_ARG", [&] { Value::num(dec(false, "x", 0)); }, "a Dec whose digits are not digits");
  {
    sel::Dec d; d.small = true; d.mantissa = -5; d.neg = false;
    selt::raises("E_BAD_ARG", [&] { Value::num(d); }, "a small Dec whose sign disagrees with neg");
  }
  selt::eq(Value::num(dec(true, "0", 0)).dump(), std::string("t\"0\""), "a negative-zero Dec is 0");
  selt::eq(Value::num(dec(false, "007", 1)).dump(), std::string("t\"0.7\""), "a Dec loses its leading zeros");
  selt::eq(Value::num(sel::Dec{}).dump(), std::string("t\"0\""), "a default Dec is 0");
}


// T05 / T06 / T07: snapshots, the total order, bare BUCKET, join keys, the regex
// shape rules and walk, the size caps. Each is a program or a call whose answer is
// fixed by the spec (§3.4, §6.4, §7.3, §7.4, §7.8); the conformance files pin the
// same, these pin the internals they reach.
std::string err_at(const std::string& src) {
  try {
    compile(src).run();
  } catch (const SelError& e) {
    return e.code() + " " + std::to_string(e.pos().line) + ":" + std::to_string(e.pos().col);
  }
  return "no error";
}

std::string pattern_verdict(const std::string& pattern) {
  try {
    (void)validate_pattern(pattern, Pos{});
  } catch (const SelError& e) {
    return e.code();
  }
  return "ok";
}

void test_snapshots_and_order() {
  selt::section("snapshots and the total order");

  // The body grows the very collection it walks. Holding a reference into the
  // live storage was a use-after-free (CPP-C1: ASan shows it on the old code).
  selt::eq(dump_of("A = LIST(1, 2); COUNT(MAP(A, (A[COUNT(A) + 1] = 0; _)))"), std::string("t\"2\""),
           "an appended element is not visited");
  selt::eq(dump_of("R = RECORD(\"a\", 1, \"b\", 2); COUNT(MAP(R, (R[_K & \"x\"] = 1; _)))"),
           std::string("t\"2\""), "keys added to an entries-backed record are not visited");
  selt::eq(dump_of("R = RECORD(\"a\", 1, \"b\", 2, \"c\", 3, \"d\", 4); COUNT(FILTER(R, (R[_K & \"x\"] = 1; TRUE)))"),
           std::string("t\"4\""), "the same for FILTER over a four-key record");
  selt::eq(dump_of("A = LIST(1, 2, 3); JOIN(MAP(A, (A[3] = 99; _)), \",\")"), std::string("t\"1,2,3\""),
           "an overwritten later element is still the one the snapshot holds (CPP-C38)");
  selt::eq(dump_of("A = LIST(1, 2, 3); SUM(A, (A[3] = 99; _))"), std::string("t\"6\""), "SUM visits a snapshot");
  selt::eq(dump_of("A = LIST(RECORD(\"k\", 1), RECORD(\"k\", 2)); JOIN(MAP(A, (A[2][\"k\"] = 9; _[\"k\"])), \",\")"),
           std::string("t\"1,9\""), "mutation INSIDE an element is still seen");

  // Rank by kind: NULL < BOOL < numeric-looking < other text < BIN.
  const Value null = Value::null(), f = Value::boolean(false), t = Value::boolean(true);
  const Value n9 = make_text("9"), n10 = make_text("10"), t1a = make_text("1a"), empty = make_text("");
  const Value bin = make_bin("a");
  selt::ok(compare_values(null, f) < 0 && compare_values(f, t) < 0 && compare_values(t, n9) < 0,
           "NULL < FALSE < TRUE < numbers");
  selt::ok(compare_values(n9, n10) < 0, "numeric text by value: 9 < 10");
  selt::ok(compare_values(n10, t1a) < 0 && compare_values(n9, t1a) < 0,
           "numeric-looking text before other text, whatever the bytes say");
  selt::ok(compare_values(empty, t1a) < 0, "\"\" is other text: bytewise");
  selt::ok(compare_values(t1a, bin) < 0 && compare_values(n10, bin) < 0, "BIN last");
  selt::eq(compare_values(make_text("-0"), make_text("0")), 0, "equal values tie (-0 = 0)");
  selt::eq(compare_values(make_text("007"), make_text("7")), 0, "leading zeros do not matter");
  // Transitivity over a mixed set: every triple agrees with the ranks.
  const std::vector<Value> mixed = {null, f, t, n9, n10, t1a, empty, bin, make_text(" 2"), make_text("1e3")};
  bool transitive = true;
  for (const auto& x : mixed)
    for (const auto& y : mixed)
      for (const auto& z : mixed)
        if (compare_values(x, y) <= 0 && compare_values(y, z) <= 0 && compare_values(x, z) > 0) transitive = false;
  selt::ok(transitive, "the order is transitive over a mixed list");
  selt::eq(dump_of("JOIN(SORT(LIST(\"10\", \"9\", \"1a\")), \",\")"), std::string("t\"9,10,1a\""), "SORT of the report's list");
  selt::eq(dump_of("JOIN(SORT(LIST(\"1a\", \"9\", \"10\")), \",\")"), std::string("t\"9,10,1a\""), "...and its rotation");
  selt::eq(dump_of("JOIN(SORT_DESC(LIST(\"10\", \"9\", \"1a\")), \",\")"), std::string("t\"1a,10,9\""), "SORT_DESC");

  // Direction and count are read and checked on an empty list too (spec §7.4).
  selt::eq(err_at("SORT_BY(LIST(), _ + 0, \"X\")"), std::string("E_BAD_ARG 1:24"), "bad direction, empty list");
  selt::eq(err_at("SORT_BY(NULL, _, \"UP\")"), std::string("E_BAD_ARG 1:18"), "bad direction, NULL source");
  selt::eq(err_at("TOP_BY(LIST(), _, \"UP\", 1)"), std::string("E_BAD_ARG 1:19"), "TOP_BY bad direction, empty list");
  selt::eq(dump_of("COUNT(SORT_BY(LIST(), _, \"DESC\"))"), std::string("t\"0\""), "a good direction on an empty list is fine");

  // A bare BUCKET groups by the index key (the text), keeping every row.
  selt::eq(dump_of("A = \"x\"; A[\"k\"] = 1; R = LIST(A, \"x\", A) .> BUCKET(_); JOIN(LIST(COUNT(R), COUNT(R[\"x\"])), \",\")"),
           std::string("t\"1,3\""), "same key text, different structure: one group, every row");
  selt::eq(dump_of("BUCKET(LIST(\"1\", \"1.0\", \"1\"), _) .> INDEXES .> JOIN(\",\")"), std::string("t\"1,1.0\""),
           "numbers are not normalised in a bucket key");
}

void test_join_keys() {
  selt::section("join keys");
  auto key = [](const std::string& text) { return *make_fast_join_key(make_text(text), true); };
  selt::ok(key("12345678901234567890.50") == key("12345678901234567890.5"), "trailing zeros do not make a different key");
  selt::ok(key("123456789012345678900.0") == key("123456789012345678900"), "nor does .0 beyond int64");
  selt::ok(!(key("100000000000000000000000.0") == key("1000000000000000000000000")), "1e23 is not 1e24 (CPP-C22)");
  selt::ok(key("-12345678901234567890.50") == key("-12345678901234567890.5"), "signs too");
  selt::ok(!(key("12345678901234567890") == key("-12345678901234567890")), "sign is part of the key");
  selt::eq(dump_of("A = LIST(RECORD(\"k\", \"12345678901234567890.50\")); B = LIST(RECORD(\"k\", \"12345678901234567890.5\")); COUNT(LINK(A, B, a[\"k\"] == b[\"k\"]))"),
           std::string("t\"1\""), "the fast path joins by value beyond int64");
  selt::eq(dump_of("A = LIST(RECORD(\"k\", \"100000000000000000000000.0\")); B = LIST(RECORD(\"k\", \"1000000000000000000000000\")); COUNT(LINK(A, B, a[\"k\"] == b[\"k\"]))"),
           std::string("t\"0\""), "and does not join unequal values");
  // Same binder on both sides: the right shadows the left, no key is extracted.
  const std::string rows = "P = LIST(RECORD(\"k\", 1), RECORD(\"k\", 1)); Q = LIST(RECORD(\"k\", 1), RECORD(\"k\", 1)); ";
  selt::eq(dump_of(rows + "COUNT(LINK(P, Q, x, x, x[\"k\"] == x[\"k\"]))"), std::string("t\"4\""), "same binder: fast spelling");
  selt::eq(dump_of(rows + "COUNT(LINK(P, Q, x, x, x[\"k\"] == x[\"k\"] AND TRUE))"), std::string("t\"4\""), "same binder: general spelling agrees");
  const NodePtr eq_node = parse("x[\"k\"] == x[\"k\"]");
  selt::ok(!extract_join_equi(*eq_node, "x", "X").has_value(), "no equi key when both binders are the same name");
}

void test_regex_shapes() {
  selt::section("regex shape rules");
  for (const char* bad : {"^*", "$+", "^{2}", "^+?", "a$+", "$?", "${0}", "^{2}a", "a**", "a{2}{3}", "*a", "(a", "a)",
                          "(*FAIL)", "a(*ACCEPT)b", "[a[:alpha:]]", "[a[:digit:]", "[[:alpha:]a]", "[a[.x.]]",
                          "[[=x=]]", "[[.]", "[[=]", "[x[:y]", "[[:a]", "[+-\\d]", "[\\d-z]", "[\\s-x]", "[\\w-a]", "[a-\\s]", "[\\w-.]", "[!-\\w]",
                          "(a*)*", "(?:a?)+", "(|a)+", "(a*?)+", "(?:^)*a", "(a|)+b", "(?:a*){2,3}", "(?:a*){2}",
                          "(?:(a)|b)*", "(?:(a)|(b))+", "((a)|(b))*", "(?:(a)?b)+", "(?:(a)|b){2}", "(?:x(a)?)+"}) {
    selt::eq(pattern_verdict(bad), std::string("E_REGEX_SYNTAX"), std::string("rejects /") + bad + "/");
  }
  for (const char* good : {"", "^$", "a$", "^a", "[\\d-]", "[-\\d]", "[\\w.-]", "[\\d.]", "[.[]", "[=[]", "[[]",
                           "(\\d*)?", "(a|b)+", "((a)b)+", "(\\d+,)+", "(?:a+b)+", "^(a)?b$", "^(?:a|)b$", "(a?){0,1}",
                           "a{1001}", "(?:a{300}){300}", "(?:a{60000}){60000}", "a+?", "a??", "a{2}?"}) {
    selt::eq(pattern_verdict(good), std::string("ok"), std::string("accepts /") + good + "/");
  }
  auto nest = [](const std::string& open, int n, const std::string& close) {
    std::string out;
    for (int i = 0; i < n; i++) out += open;
    out += "a";
    for (int i = 0; i < n; i++) out += close;
    return out;
  };
  selt::eq(pattern_verdict(nest("(?:", 200, ")")), std::string("ok"), "200 nested groups is the cap");
  selt::eq(pattern_verdict(nest("(?:", 201, ")")), std::string("E_REGEX_SYNTAX"), "201 is past it");
  selt::eq(pattern_verdict(nest("(", 201, ")")), std::string("E_REGEX_SYNTAX"), "capturing groups count alike");
  selt::eq(pattern_verdict(nest("(?:", 50000, ")")), std::string("E_REGEX_SYNTAX"), "far past the cap: no recursion trouble");
  std::string many;
  for (int i = 0; i < 1000; i++) many += "(a)";
  selt::eq(pattern_verdict(many), std::string("ok"), "1000 groups");
  selt::eq(pattern_verdict(many + "(a)"), std::string("E_REGEX_SYNTAX"), "1001 groups");
  selt::eq(pattern_verdict(std::string(65535, 'a')), std::string("ok"), "65535 code points");
  selt::eq(pattern_verdict(std::string(65536, 'a')), std::string("E_REGEX_SYNTAX"), "65536 code points");
  std::string wide;
  for (int i = 0; i < 40000; i++) wide += "\xc5\xbc";   // U+017C, two bytes each
  selt::eq(pattern_verdict(wide), std::string("ok"), "the cap counts code points, not bytes");

  // Literal patterns are checked when the program is compiled; computed ones when used.
  selt::raises("E_REGEX_SYNTAX", [] { compile("IF(FALSE, RMATCH('(?=a)', 'a'), 1)"); }, "literal, dead branch");
  selt::raises("E_REGEX_SYNTAX", [] { compile("IF(FALSE, RREPLACE('(?=a)', '-', 'a'), 1)"); }, "literal RREPLACE");
  selt::raises("E_REGEX_SYNTAX", [] { compile("IF(FALSE, RMATCH(\"(?=a)\", 'a'), 1)"); }, "double-quoted literal");
  selt::eq(dump_of("IF(FALSE, RMATCH(\"(?=\" & \"a)\", \"a\"), 1)"), std::string("t\"1\""), "computed pattern, dead branch");
  selt::eq(err_at("IF(TRUE, RMATCH(\"(?=\" & \"a)\", \"a\"), 1)").substr(0, 14), std::string("E_REGEX_SYNTAX"), "computed pattern, run");

  // The flag is `i`, and nothing else.
  for (const char* flag : {"I", "\\u{130}", "\\u{131}", "\\u{212A}", "m", "s", "x"}) {
    selt::eq(err_at(std::string("RMATCH('a', \"A\", \"") + flag + "\")").substr(0, 9), std::string("E_BAD_ARG"),
             std::string("flag ") + flag);
  }
  selt::eq(dump_of("RMATCH('^a+$', \"AaA\", \"i\")"), std::string("TRUE"), "i still works");
}

// P5: the exponential-ambiguity rule (spec §7.8), against the reference lists in
// tools/regex-ambiguity-ref.py. The verdicts are the reference's, pattern by pattern.
std::string verdict_ic(const std::string& pattern, bool ic) {
  try {
    (void)validate_pattern(pattern, Pos{}, ic);
  } catch (const SelError& e) {
    return e.code();
  }
  return "ok";
}

void test_regex_ambiguity() {
  selt::section("regex exponential ambiguity");
  const std::vector<std::string> reject = {
      R"x((a+)+$)x",
      R"x((a|aa)+$)x",
      R"x((a|b|ab)*c)x",
      R"x((?:a+|b)*(?:a+)*c)x",
      R"x((.+)+x)x",
      R"x(([a-z]+)*$)x",
      R"x((\w+\s?)*$)x",
      R"x((\w+\s*)*$)x",
      R"x(([a-zA-Z]+)*\d)x",
      R"x((?:[a-z]+|\d+)*$)x",
      R"x((\d+\d*)+$)x",
      R"x(([\w.-]+\.)+$)x",
      R"x((x+x+)+y)x",
      R"x((?:\d|\d\d)+$)x",
      R"x((?:\s|\s\s)+$)x",
      R"x((?:.|\n)*x)x",
      R"x((?:a|a)*$)x",
      R"x((\d{1,3},?)+$)x",
      R"x(^(([a-z])+.)+[A-Z]([a-z])+$)x",
      R"x((?:\s*,\s*)*x)x",
      R"x((?:[ab]|[bc])*$)x",
      R"x((\s*\w+\s*)*$)x",
      R"x((?:x|xx|xxx)+y)x",
      R"x(^(\w+[-.]?)+@)x",
      R"x((?:[\d.]+,?)+$)x",
      R"x((a+){2,}$)x",
      R"x((?:(?:a|b)+c?)+$)x",
      R"x(((a+)b?)*$)x",
      R"x((?:a+)+?b)x",
      R"x((?:a{1,20}){1,20}b)x",
      R"x((?:\s*\w+\s*,?)*x)x",
      R"x((?:(?:a?|b?)c)*d)x",
      R"x((?:(?:a*)?c)*d)x",
      R"x(^(?:a+){2,}$)x",
      R"x(^(?:a|b|ab)+$)x"};
  for (const auto& p : reject) selt::eq(verdict_ic(p, false), std::string("E_REGEX_SYNTAX"), "refuses /" + p + "/");
  const std::vector<std::string> accept = {
      R"x((\d+,)+)x",
      R"x((?:ab|cd)*)x",
      R"x(^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$)x",
      R"x((\w+\s)*)x",
      R"x(([a-z]+-)*[a-z]+)x",
      R"x((?:a|b)*)x",
      R"x(a*b*c*)x",
      R"x(^\d{3}-\d{3}-\d{4}$)x",
      R"x(^(\d{1,3}\.){3}\d{1,3}$)x",
      R"x(^[+-]?\d+(?:\.\d+)?$)x",
      R"x(^"(?:[^"\\]|\\.)*"$)x",
      R"x(^[a-z0-9]+(?:[-_.][a-z0-9]+)*$)x",
      R"x(^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$)x",
      R"x(^(?:[^,]*,)*[^,]*$)x",
      R"x(^\s*(\w+)\s*=\s*(.*?)\s*$)x",
      R"x((?:\r\n|\n)*)x",
      R"x(^(?:[a-z]+\d+)*$)x",
      R"x(^[A-Z]{2}\d{2}(?: ?\d{4}){4,7}$)x",
      R"x(^(?:ab|ac)*$)x",
      R"x(^(?:ab|a)*$)x",
      R"x((?:foo|foobar)*)x",
      R"x(^(?:\d{3}){1,2}$)x",
      R"x((?:a{2}){3})x",
      R"x(^(a{300}){300}$)x",
      R"x((?:a{60000}){60000})x",
      R"x(^[a-z]+(?:[A-Z][a-z]+)*$)x",
      R"x(^(?:[A-Z][a-z0-9]+)+$)x",
      R"x((?:[a-z]|[A-Z])+$)x",
      R"x(^(?:[0-9]*|[a-z]*)$)x",
      R"x(^(\d*)?$)x",
      R"x((^|[^0-9A-Za-z_])foo($|[^0-9A-Za-z_]))x",
      R"x(^(?:a+b)+$)x",
      R"x(^[a-z]+(\.[a-z]+)*$)x",
      R"x(^(a|b)*$)x",
      R"x(^(?:a|b)+c?$)x",
      R"x(^[^<>]*(?:<[^<>]*>[^<>]*)*$)x",
      R"x(^(.*),(.*),(.*),(.*)$)x",
      R"x(a*a*$)x"};
  for (const auto& p : accept) selt::eq(verdict_ic(p, false), std::string("ok"), "accepts /" + p + "/");
  for (const std::string p : {R"x((?:a|A)+$)x", R"x((?:[a-z]|[A-Z])+$)x", R"x(^[a-z]*(?:[a-c]|[A-C])+$)x"}) {
    selt::eq(verdict_ic(p, true), std::string("E_REGEX_SYNTAX"), "refuses under i: /" + p + "/");
    selt::eq(verdict_ic(p, false), std::string("ok"), "accepts without i: /" + p + "/");
  }
  auto rep = [](const std::string& unit, int n, const std::string& tail) {
    std::string s;
    for (int i = 0; i < n; i++) s += unit;
    return s + tail;
  };
  // The budget boundaries: exactly at the limit is accepted, one step above is not.
  selt::eq(verdict_ic(rep("(a|a)", 8, "x"), false), std::string("ok"), "(a|a) x8");
  selt::eq(verdict_ic(rep("(a|a)", 9, "x"), false), std::string("E_REGEX_SYNTAX"), "(a|a) x9");
  selt::eq(verdict_ic(rep("(?:|)", 16, "x"), false), std::string("ok"), "(?:|) x16");
  selt::eq(verdict_ic(rep("(?:|)", 17, "x"), false), std::string("E_REGEX_SYNTAX"), "(?:|) x17");
  selt::eq(verdict_ic(rep("a?", 7, "b"), false), std::string("ok"), "a? x7");
  selt::eq(verdict_ic(rep("a?", 8, "b"), false), std::string("E_REGEX_SYNTAX"), "a? x8");
  selt::eq(verdict_ic("(?:a|a){1,8}$", false), std::string("ok"), "(?:a|a){1,8}$");
  selt::eq(verdict_ic("(?:a|a){1,9}$", false), std::string("E_REGEX_SYNTAX"), "(?:a|a){1,9}$");
  // A refusal reaches the language as E_REGEX_SYNTAX from a program too, and the
  // analysis is fast on a pattern built to overrun its caps.
  selt::raises("E_REGEX_SYNTAX", [] { compile("RMATCH('(a+)+$', 'aaaa')"); }, "compile-time literal");
  selt::eq(err_at("RMATCH('^(?:a|b)*$', REPEAT('ab', 100000))").substr(0, 4), std::string("no e"), "linear pattern on a long subject");
  std::string many;
  for (int i = 0; i < 5000; i++) many += (i ? "|x" : "x") + std::to_string(i);
  selt::eq(verdict_ic(many, false), std::string("ok"), "5000 distinct alternatives");
}

// Gate triage: a value with children and no scalar sorts by scalar context
// (spec §3.2 / §7.3): a record by its first field, ties in input order.
void test_records_sort_by_first_field() {
  selt::section("records sort by scalar context");
  const std::string rows = "LIST(RECORD(\"k\", 3, \"v\", \"c\"), RECORD(\"k\", 1, \"v\", \"a\"), RECORD(\"k\", 2, \"v\", \"b\"))";
  const std::string ties = "LIST(RECORD(\"k\", 1, \"v\", \"a\"), RECORD(\"k\", 2, \"v\", \"b\"), RECORD(\"k\", 1.0, \"v\", \"c\"), RECORD(\"k\", \"1\", \"v\", \"d\"))";
  auto join = [](const std::string& e) { return "JOIN(MAP(" + e + ", _[\"v\"]), \",\")"; };
  selt::eq(dump_of(join(rows + " .> SORT()")), std::string("t\"a,b,c\""), "asc");
  selt::eq(dump_of(join(rows + " .> SORT_DESC()")), std::string("t\"c,b,a\""), "desc");
  selt::eq(dump_of(join(ties + " .> SORT()")), std::string("t\"a,c,d,b\""), "ties asc");
  selt::eq(dump_of(join(ties + " .> SORT_DESC()")), std::string("t\"b,a,c,d\""), "ties desc");
  selt::eq(dump_of(join(ties + " .> TOP_DESC(4)")), std::string("t\"b,a,c,d\""), "top desc ties");
  selt::eq(dump_of(join("LIST(RECORD(\"k\", 5, \"v\", \"n\"), RECORD(\"k\", TRUE, \"v\", \"t\"), RECORD(\"k\", FALSE, \"v\", \"f\")) .> SORT()")),
           std::string("t\"f,t,n\""), "bool rank");
}

void test_regex_walk_cache_and_limits() {
  selt::section("regex walk, cache, engine limits");
  // The RREPLACE walk of spec §7.8: after an empty match at s the scan resumes at
  // s+1, copying the code point through; after a non-empty one, at its end.
  selt::eq(dump_of("RREPLACE('a*', \"-\", \"baac\")"), std::string("t\"-b--c-\""), "a* on baac");
  selt::eq(dump_of("RREPLACE('b*?', \"-\", \"abb\")"), std::string("t\"-a-b-b-\""), "lazy b* on abb");
  selt::eq(dump_of("RREPLACE('a*?', \"-\", \"aab\")"), std::string("t\"-a-a-b-\""), "lazy a* on aab");
  selt::eq(dump_of("RREPLACE('(?:|a)', \"-\", \"aa\")"), std::string("t\"-a-a-\""), "empty alternative first");
  selt::eq(dump_of("COUNT(SPLIT(RREPLACE('x?" "?', \"-\", \"xx\"), \"-\"))"), std::string("t\"4\""), "optional lazy");
  selt::eq(dump_of("RREPLACE('\\s*', \"_\", \"a b\")"), std::string("t\"_a__b_\""), "space runs");
  selt::eq(dump_of("RREPLACE('^a', \"-\", \"aaa\")"), std::string("t\"-aa\""), "^ anchors the whole subject, not each restart");
  selt::eq(dump_of("RREPLACE('$', \"!\", \"ab\")"), std::string("t\"ab!\""), "an empty match at the end");
  selt::eq(dump_of("RREPLACE('(b*|[é-ü])', \"<$0|$1>\", \"baac\")"), std::string("t\"<b|b><|>a<|>a<|>c<|>\""), "captures");

  // The cache holds at most REGEX_CACHE_MAX patterns, oldest out first.
  for (int i = 0; i < 600; i++) {
    (void)compile("RMATCH('p" + std::to_string(i) + "', 'x')").run();
  }
  {
    RegexCacheState& state = regex_cache_state();
    std::lock_guard<std::mutex> lock(state.mutex);
    selt::eq(state.map.size(), REGEX_CACHE_MAX, "the pattern cache is bounded");
    selt::eq(state.order.size(), REGEX_CACHE_MAX, "and so is its eviction queue");
  }
  selt::eq(dump_of("RMATCH('p599', 'p599')"), std::string("TRUE"), "a recent pattern still works");
  selt::eq(dump_of("RMATCH('p0', 'p0')"), std::string("TRUE"), "an evicted one is recompiled");

  // An engine refusal is a SEL error, never an uncaught exception or a quiet FALSE.
  selt::raises("E_REGEX_SYNTAX", [] {
    guarded_search(Pos{}, []() -> bool { throw srell::regex_error(srell::regex_constants::error_complexity); });
  }, "srell::regex_error at search time becomes E_REGEX_SYNTAX");
}

#if defined(__SANITIZE_ADDRESS__)
constexpr bool kHeavy = false;   // 16M-unit values are ~100x slower under ASan; the plain build runs them
#else
constexpr bool kHeavy = true;
#endif

void test_size_caps() {
  selt::section("size caps");
  if (kHeavy) selt::eq(dump_of("REPEAT(\"\", 99999999999999999999)"), std::string("t\"\""), "an empty result is never too large");
  selt::eq(dump_of("LEN(REPEAT(\"\", 1 & REPEAT(\"0\", 400)))"), std::string("t\"0\""), "...not even at 401 digits");
  if (kHeavy) selt::eq(dump_of("LEN(REPEAT(\"a\", 16777216))"), std::string("t\"16777216\""), "REPEAT at the cap");
  selt::eq(err_at("REPEAT(\"a\", 16777217)"), std::string("E_RANGE 1:1"), "REPEAT past it, at the call");
  if (kHeavy) selt::eq(err_at("REPEAT(\"ab\", 99999999999999999999)"), std::string("E_RANGE 1:1"), "a count past machine width");
  selt::eq(err_at("REPEAT(\"a\", 1 & REPEAT(\"0\", 400))"), std::string("E_RANGE 1:1"), "a 401-digit count");
  if (kHeavy) selt::eq(err_at("REPEAT(1/0, 99999999999999999999)"), std::string("E_DIV_ZERO 1:9"), "arguments are evaluated before the cap");
  if (kHeavy) selt::eq(dump_of("LEN(PADL(\"a\", 16777216, \"x\"))"), std::string("t\"16777216\""), "PADL at the cap");
  if (kHeavy) selt::eq(err_at("PADL(\"abc\", 99999999999999999999999, \"x\")"), std::string("E_RANGE 1:1"), "PAD past machine width");
  selt::eq(err_at("PADL(\"abc\", 6, \"\")"), std::string("E_BAD_ARG 1:16"), "an empty fill is still E_BAD_ARG");
  selt::eq(dump_of("PADL(\"abc\", 2, \"xyz\")"), std::string("t\"abc\""), "a pad shorter than the text is unchanged");
  if (kHeavy) selt::eq(err_at("REPEAT(\"a\", 16777216) & \"b\""), std::string("E_RANGE 1:23"), "& past the cap, at the operator");
  if (kHeavy) selt::eq(dump_of("LEN(REPEAT(\"a\", 16777215) & \"b\")"), std::string("t\"16777216\""), "& at the cap");
  if (kHeavy) selt::eq(err_at("TO_UTF8(REPEAT(\"a\", 16777216)) & TO_UTF8(\"b\")"), std::string("E_RANGE 1:32"), "BIN & past the cap");
  if (kHeavy) selt::eq(err_at("JOIN(SPLIT(REPEAT(\"a,\", 100) & \"a\", \",\"), REPEAT(\"b\", 10000000))"), std::string("E_RANGE 1:1"),
           "JOIN fan-out is measured, not built");
  selt::eq(err_at("REPLACE(\"a\", REPEAT(\"b\", 200000), REPEAT(\"a\", 200))"), std::string("E_RANGE 1:1"), "REPLACE past the cap");
  selt::eq(dump_of("LEN(REPLACE(\"a\", REPEAT(\"b\", 200000), \"a\"))"), std::string("t\"200000\""), "a long replacement under it");
  if (kHeavy) selt::eq(err_at("TO_HEX(TO_UTF8(REPEAT(\"a\", 8388609)))"), std::string("E_RANGE 1:1"), "TO_HEX past the cap");
  if (kHeavy) selt::eq(err_at("ENCODE_BASE64(TO_UTF8(REPEAT(\"a\", 12582915)))"), std::string("E_RANGE 1:1"), "base64 past the cap");
  if (kHeavy) selt::eq(err_at("TO_UTF8(REPEAT(\"\\u{1F600}\", 4194305))"), std::string("E_RANGE 1:1"), "TO_UTF8: 4 bytes per code point");
  if (kHeavy) selt::eq(err_at("COUNT(SPLIT(REPEAT(\"a,\", 1000000) & \"a\", \",\"))"), std::string("E_RANGE 1:7"), "SPLIT past the collection cap");
  if (kHeavy) selt::eq(err_at("COUNT(BTL(TO_UTF8(REPEAT(\"a\", 1000001))))"), std::string("E_RANGE 1:7"), "BTL past it");
  if (kHeavy) selt::eq(dump_of("COUNT(BTL(TO_UTF8(REPEAT(\"a\", 1000000))))"), std::string("t\"1000000\""), "BTL at it");
  if (kHeavy) selt::eq(dump_of("COUNT(FILTER(SPLIT(REPEAT(\"a,\", 999999) & \"a\", \",\"), _ $== \"a\"))"), std::string("t\"1000000\""),
           "a function of a large input is not capped");
  if (kHeavy) selt::eq(err_at("A = (1, 2); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); "
                  "A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); A = (A, A); "
                  "A = (A, A); A = (A, A); A = (A, A); A = (A, A); COUNT(A)").substr(0, 7), std::string("E_RANGE"),
           "doubling stops at the collection cap");
  selt::eq(err_at("A = SPLIT(REPEAT(\"a,\", 1000) & \"a\", \",\"); B = SPLIT(REPEAT(\"b,\", 999) & \"b\", \",\"); COUNT(LINK(A, B, TRUE))"),
           std::string("E_RANGE 1:90"), "LINK's rows are capped as they appear");
  selt::eq(dump_of("LEFT(\"abc\", 9223372036854775807)"), std::string("t\"abc\""), "a clamping count is no error (CPP-C51)");
  selt::eq(dump_of("RIGHT(\"abc\", 9223372036854775807)"), std::string("t\"abc\""), "RIGHT clamps too");

  // LTB: integers of any scale, E_NOT_INT for a fraction, the empty list is empty BIN.
  selt::eq(dump_of("BLEN(LTB(BTL(\"\")))"), std::string("t\"0\""), "LTB of the empty list");
  selt::eq(dump_of("TO_HEX(LTB(LIST()))"), std::string("t\"\""), "LTB(LIST())");
  selt::eq(dump_of("TO_HEX(LTB(LIST(65, 1.0)))"), std::string("t\"4101\""), "1.0 is a byte");
  selt::eq(dump_of("TO_HEX(LTB(LIST(\"1.0\")))"), std::string("t\"01\""), "so is the text \"1.0\"");
  selt::eq(dump_of("TO_HEX(LTB(LIST(255.00)))"), std::string("t\"ff\""), "255.00");
  selt::eq(err_at("LTB(LIST(1.5))"), std::string("E_NOT_INT 1:5"), "a fraction is E_NOT_INT");
  selt::eq(err_at("LTB(LIST(256.0))"), std::string("E_RANGE 1:5"), "256.0 is out of range");

  // Messages are data-free and valid UTF-8 even for non-ASCII input (CPP-C52).
  for (const char* src : {"FROM_HEX(\"a\\u{E9}b\")", "DECODE_BASE64(\"\\u{E9}==\")"}) {
    try {
      compile(src).run();
      selt::ok(false, std::string("raises: ") + src);
    } catch (const SelError& e) {
      bool valid = true;
      try { decode_utf8(e.message(), Pos{}); } catch (const SelError&) { valid = false; }
      selt::ok(valid, std::string("the message is valid UTF-8: ") + src);
    }
  }
}

}  // namespace

int main() {
  test_utf8();
  test_utf8_source_positions();
  test_decimal();
  test_decimal_native_edges();
  test_knuth_division();
  test_karatsuba_and_early_range();
  test_coalesce_probe();
  test_text_byte_paths();
  test_adopt_or_clone();
  test_bucket_rows_alias_when_unobservable();
  test_filter_borrows_rows_for_a_read_only_next_step();
  test_pipeline_temporaries_are_kept();
  test_filter_packed_result();
  test_round2_fast_paths();
  test_round3_fast_paths();
  test_collector_copies_and_depth();
  test_value();
  test_host_api();
  test_evaluation_order();
  test_relational_optimizations();
  test_structural_hash_identity();
  test_math_plan();
  test_operand_order_and_plans();
  test_plan_scratchpad();
  test_fusion_and_positions();
  test_tree_walkers_are_not_recursive();
  test_plain_vs_optimised();
  test_host_boundary();
  test_snapshots_and_order();
  test_join_keys();
  test_regex_shapes();
  test_regex_ambiguity();
  test_records_sort_by_first_field();
  test_regex_walk_cache_and_limits();
  test_size_caps();
  test_dependencies_flow_and_host_boundary();
  return selt::report("cpp unit");
}
