// SEL — Simple Expression Language, C++23 implementation.
//
// One translation unit, laid out in the order docs/EXTENDING.md prescribes and
// the other implementations follow, so a divergence found by the fuzzer lands in
// the same place in every host:
//
//     errors, utf8, decimal, value, registry, lexer, parser, eval, builtins,
//     host API
//
// spec/SPEC.md is normative. Nothing here may consult the host's own idea of
// string length, case, ordering or regex flags — those are precisely what differ
// between hosts, and every one of them has already caused a real divergence in
// this project. See the traps list in docs/EXTENDING.md.

#include "sel.hpp"

// SEL rejects \p{...} at compile time as non-portable, so SRELL's Unicode
// property tables are unreachable from this language. Leaving them out cuts the
// compile substantially and removes the only part of the library SEL can never
// use. See third_party/srell/PINNED.md.
#define SRELL_NO_UNICODE_PROPERTY
#include "third_party/srell/srell.hpp"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <span>
#include <stdexcept>
#include <unordered_map>

namespace sel {
namespace {

// ============================================================================
// --- errors
// ============================================================================

[[noreturn]] void fail(const char* code, const std::string& message, Pos pos = {}) {
  throw SelError(code, message, pos);
}

}  // namespace

SelError::SelError(std::string code, std::string message, Pos pos)
    : code_(std::move(code)), message_(std::move(message)), pos_(pos) {}

std::string SelError::str() const {
  return code_ + " at " + std::to_string(pos_.line) + ":" + std::to_string(pos_.col) +
         ": " + message_;
}

namespace {

// ============================================================================
// --- utf8
//
// Hand-written on purpose. Every length, offset and slice in SEL counts code
// points, and that has to be true in every host or nothing else is.
// ============================================================================

using CodePoints = std::vector<char32_t>;

// Strict: rejects overlong forms, surrogates, values above U+10FFFF and
// truncated sequences. No replacement characters, ever.
CodePoints decode_utf8(std::string_view bytes, Pos pos = {}) {
  CodePoints cps;
  cps.reserve(bytes.size());
  const std::size_t n = bytes.size();
  std::size_t i = 0;
  while (i < n) {
    const unsigned char b = static_cast<unsigned char>(bytes[i]);
    int need;
    char32_t cp;
    unsigned char lo, hi;
    if (b < 0x80) {
      cps.push_back(b);
      i++;
      continue;
    } else if (b >= 0xc2 && b <= 0xdf) {
      need = 1; cp = b & 0x1f; lo = 0x80; hi = 0xbf;
    } else if (b == 0xe0) {
      need = 2; cp = 0; lo = 0xa0; hi = 0xbf;       // reject overlong 3-byte
    } else if (b >= 0xe1 && b <= 0xec) {
      need = 2; cp = b & 0x0f; lo = 0x80; hi = 0xbf;
    } else if (b == 0xed) {
      need = 2; cp = 0x0d; lo = 0x80; hi = 0x9f;    // reject surrogates
    } else if (b >= 0xee && b <= 0xef) {
      need = 2; cp = b & 0x0f; lo = 0x80; hi = 0xbf;
    } else if (b == 0xf0) {
      need = 3; cp = 0; lo = 0x90; hi = 0xbf;       // reject overlong 4-byte
    } else if (b >= 0xf1 && b <= 0xf3) {
      need = 3; cp = b & 0x07; lo = 0x80; hi = 0xbf;
    } else if (b == 0xf4) {
      need = 3; cp = 4; lo = 0x80; hi = 0x8f;       // cap at U+10FFFF
    } else {
      char buf[3];
      std::snprintf(buf, sizeof buf, "%02x", b);
      fail("E_UTF8", "invalid start byte 0x" + std::string(buf) + " at byte " +
                         std::to_string(i), pos);
    }

    if (i + static_cast<std::size_t>(need) >= n) {
      fail("E_UTF8", "truncated sequence at byte " + std::to_string(i), pos);
    }
    for (int k = 1; k <= need; k++) {
      const unsigned char c = static_cast<unsigned char>(bytes[i + k]);
      const unsigned char min = k == 1 ? lo : 0x80;
      const unsigned char max = k == 1 ? hi : 0xbf;
      if (c < min || c > max) {
        fail("E_UTF8", "invalid continuation byte at byte " + std::to_string(i + k), pos);
      }
      cp = (cp << 6) | (c & 0x3f);
    }
    cps.push_back(cp);
    i += static_cast<std::size_t>(need) + 1;
  }
  return cps;
}

void encode_cp(std::string& out, char32_t c) {
  if (c < 0x80) {
    out.push_back(static_cast<char>(c));
  } else if (c < 0x800) {
    out.push_back(static_cast<char>(0xc0 | (c >> 6)));
    out.push_back(static_cast<char>(0x80 | (c & 0x3f)));
  } else if (c < 0x10000) {
    out.push_back(static_cast<char>(0xe0 | (c >> 12)));
    out.push_back(static_cast<char>(0x80 | ((c >> 6) & 0x3f)));
    out.push_back(static_cast<char>(0x80 | (c & 0x3f)));
  } else {
    out.push_back(static_cast<char>(0xf0 | (c >> 18)));
    out.push_back(static_cast<char>(0x80 | ((c >> 12) & 0x3f)));
    out.push_back(static_cast<char>(0x80 | ((c >> 6) & 0x3f)));
    out.push_back(static_cast<char>(0x80 | (c & 0x3f)));
  }
}

std::string encode_utf8(const CodePoints& cps) {
  std::string out;
  out.reserve(cps.size());
  for (char32_t c : cps) encode_cp(out, c);
  return out;
}

std::string encode_utf8(std::span<const char32_t> cps) {
  std::string out;
  out.reserve(cps.size());
  for (char32_t c : cps) encode_cp(out, c);
  return out;
}

bool is_valid_utf8(std::string_view bytes) {
  try {
    decode_utf8(bytes);
    return true;
  } catch (const SelError&) {
    return false;
  }
}

std::size_t cp_length(std::string_view text, Pos pos = {}) {
  return decode_utf8(text, pos).size();
}

std::string to_hex(std::string_view bytes) {
  static const char* DIGITS = "0123456789abcdef";
  std::string out;
  out.reserve(bytes.size() * 2);
  for (char ch : bytes) {
    const unsigned char b = static_cast<unsigned char>(ch);
    out.push_back(DIGITS[b >> 4]);
    out.push_back(DIGITS[b & 0x0f]);
  }
  return out;
}

// Bytewise, as spec/SPEC.md §5.3 requires. std::string::compare is bytewise on
// every implementation, but say so explicitly rather than rely on it.
int bytes_compare(std::string_view a, std::string_view b) {
  const std::size_t n = std::min(a.size(), b.size());
  for (std::size_t i = 0; i < n; i++) {
    const unsigned char x = static_cast<unsigned char>(a[i]);
    const unsigned char y = static_cast<unsigned char>(b[i]);
    if (x != y) return x < y ? -1 : 1;
  }
  if (a.size() == b.size()) return 0;
  return a.size() < b.size() ? -1 : 1;
}

// ============================================================================
// --- decimal
//
// Exact decimal arithmetic on digit strings. See spec/SPEC.md §4. Ported line
// for line from js/src/decimal.mjs and php/src/Dec.php; the three must stay
// recognisably the same code, because tools/check-decimal.sh is the only thing
// standing between a subtle rounding difference and a wrong invoice.
//
// A decimal is { neg, digits, scale }, meaning (neg ? -1 : 1) * digits / 10^scale.
// `digits` is the unscaled integer with no leading zeros ("0" for zero). Zero is
// never negative. Scale is part of the value: 2.50 is "250" at scale 2.
// ============================================================================

constexpr long long DIV_SCALE = 10;

// An upper bound on any scale a built-in will construct. Not a language limit —
// spec/SPEC.md sets none — but a value beyond this asks for a string longer than
// memory, and failing with E_RANGE beats either a wrapped integer or a crash.
constexpr long long MAX_SCALE = 1000000;
constexpr long long MAX_POWER = 100000;
constexpr long long MAX_QUANTIFIER = 65535;   // PCRE2's own hard limit

// `scale` is 64-bit deliberately. It is a count of fractional digits, and an
// `int` here is a 32-bit limit nobody chose — the limits that do apply are in
// spec/SPEC.md §6.4 and are enforced where the argument is read, not by the
// width of a field.
struct Dec {
  bool neg = false;
  std::string digits = "0";
  long long scale = 0;
};

// --- digit-string primitives (non-negative, no leading zeros)

std::string strip(const std::string& s) {
  std::size_t i = 0;
  while (i + 1 < s.size() && s[i] == '0') i++;
  return i == 0 ? s : s.substr(i);
}

int cmp_abs(const std::string& a, const std::string& b) {
  if (a.size() != b.size()) return a.size() < b.size() ? -1 : 1;
  if (a == b) return 0;
  return a < b ? -1 : 1;
}

std::string add_abs(const std::string& a, const std::string& b) {
  std::string out;
  long i = static_cast<long>(a.size()) - 1;
  long j = static_cast<long>(b.size()) - 1;
  int carry = 0;
  while (i >= 0 || j >= 0 || carry) {
    const int s = (i >= 0 ? a[i--] - '0' : 0) + (j >= 0 ? b[j--] - '0' : 0) + carry;
    out.push_back(static_cast<char>('0' + (s % 10)));
    carry = s >= 10 ? 1 : 0;
  }
  std::reverse(out.begin(), out.end());
  return out;
}

// Requires a >= b.
std::string sub_abs(const std::string& a, const std::string& b) {
  std::string out;
  long i = static_cast<long>(a.size()) - 1;
  long j = static_cast<long>(b.size()) - 1;
  int borrow = 0;
  while (i >= 0) {
    int s = (a[i--] - '0') - (j >= 0 ? b[j--] - '0' : 0) - borrow;
    if (s < 0) { s += 10; borrow = 1; } else { borrow = 0; }
    out.push_back(static_cast<char>('0' + s));
  }
  std::reverse(out.begin(), out.end());
  return strip(out);
}

std::string mul_abs(const std::string& a, const std::string& b) {
  if (a == "0" || b == "0") return "0";
  const std::size_t n = a.size(), m = b.size();
  std::vector<int> acc(n + m, 0);
  for (long i = static_cast<long>(n) - 1; i >= 0; i--) {
    const int av = a[i] - '0';
    if (av == 0) continue;
    int carry = 0;
    for (long j = static_cast<long>(m) - 1; j >= 0; j--) {
      const int t = acc[i + j + 1] + av * (b[j] - '0') + carry;
      acc[i + j + 1] = t % 10;
      carry = t / 10;
    }
    acc[i] += carry;
  }
  std::string out;
  out.reserve(acc.size());
  for (int d : acc) out.push_back(static_cast<char>('0' + d));
  return strip(out);
}

// Schoolbook long division. Trial digits by repeated subtraction — at most nine
// per output digit, which keeps it obviously correct and trivial to port.
bool divmod_abs(const std::string& a, const std::string& b, std::string& q, std::string& r) {
  if (b == "0") return false;
  if (cmp_abs(a, b) < 0) { q = "0"; r = a; return true; }
  std::string quo;
  quo.reserve(a.size());
  std::string rem = "0";
  for (std::size_t i = 0; i < a.size(); i++) {
    rem = strip(rem + a[i]);
    int k = 0;
    while (cmp_abs(rem, b) >= 0) { rem = sub_abs(rem, b); k++; }
    quo.push_back(static_cast<char>('0' + k));
  }
  q = strip(quo);
  r = rem;
  return true;
}

std::string scale_up(const std::string& digits, long long k) {
  if (k <= 0) return digits;
  if (digits == "0") return "0";
  return digits + std::string(static_cast<std::size_t>(k), '0');
}

std::string pow10(long long k) {
  return k == 0 ? "1" : "1" + std::string(static_cast<std::size_t>(k), '0');
}

// --- construction

Dec dec_make(bool neg, std::string digits, long long scale) {
  Dec d;
  d.digits = std::move(digits);
  d.neg = d.digits == "0" ? false : neg;
  d.scale = scale;
  return d;
}

const Dec DEC_ZERO = dec_make(false, "0", 0);

bool dec_is_number(std::string_view text) {
  std::size_t i = 0;
  if (i < text.size() && text[i] == '-') i++;
  const std::size_t int_start = i;
  while (i < text.size() && text[i] >= '0' && text[i] <= '9') i++;
  if (i == int_start) return false;
  if (i == text.size()) return true;
  if (text[i] != '.') return false;
  i++;
  const std::size_t frac_start = i;
  while (i < text.size() && text[i] >= '0' && text[i] <= '9') i++;
  return i > frac_start && i == text.size();
}

// Returns false when the text is not a number; callers raise E_NOT_NUM with the
// position of the offending node. No trimming — " 2" is not a number.
bool dec_parse(std::string_view text, Dec& out) {
  if (!dec_is_number(text)) return false;
  const bool neg = text[0] == '-';
  const std::string_view body = neg ? text.substr(1) : text;
  const std::size_t dot = body.find('.');
  const std::string int_part(dot == std::string_view::npos ? body : body.substr(0, dot));
  const std::string frac_part(dot == std::string_view::npos ? std::string_view()
                                                            : body.substr(dot + 1));
  out = dec_make(neg, strip(int_part + frac_part), static_cast<long long>(frac_part.size()));
  return true;
}

std::string dec_format(const Dec& d) {
  const std::string sign = d.neg ? "-" : "";
  if (d.scale == 0) return sign + d.digits;
  const std::size_t scale = static_cast<std::size_t>(d.scale);
  const std::string padded =
      d.digits.size() <= scale ? std::string(scale - d.digits.size() + 1, '0') + d.digits
                               : d.digits;
  return sign + padded.substr(0, padded.size() - scale) + "." +
         padded.substr(padded.size() - scale);
}

Dec dec_from_int(long long n) {
  const bool neg = n < 0;
  // Negate in the unsigned domain so LLONG_MIN does not overflow.
  const unsigned long long mag =
      neg ? (~static_cast<unsigned long long>(n) + 1ull) : static_cast<unsigned long long>(n);
  return dec_make(neg, std::to_string(mag), 0);
}

bool dec_is_zero(const Dec& d) { return d.digits == "0"; }
Dec dec_negate(const Dec& d) { return dec_make(!d.neg, d.digits, d.scale); }
Dec dec_abs(const Dec& d) { return dec_make(false, d.digits, d.scale); }
int dec_sign(const Dec& d) { return dec_is_zero(d) ? 0 : (d.neg ? -1 : 1); }

// --- arithmetic

void dec_aligned(const Dec& a, const Dec& b, std::string& A, std::string& B, long long& s) {
  s = std::max(a.scale, b.scale);
  A = scale_up(a.digits, s - a.scale);
  B = scale_up(b.digits, s - b.scale);
}

Dec dec_add(const Dec& a, const Dec& b) {
  std::string A, B;
  long long s;
  dec_aligned(a, b, A, B, s);
  if (a.neg == b.neg) return dec_make(a.neg, add_abs(A, B), s);
  const int c = cmp_abs(A, B);
  if (c == 0) return dec_make(false, "0", s);
  return c > 0 ? dec_make(a.neg, sub_abs(A, B), s) : dec_make(b.neg, sub_abs(B, A), s);
}

Dec dec_sub(const Dec& a, const Dec& b) { return dec_add(a, dec_negate(b)); }

Dec dec_mul(const Dec& a, const Dec& b) {
  return dec_make(a.neg != b.neg, mul_abs(a.digits, b.digits), a.scale + b.scale);
}

int dec_cmp(const Dec& a, const Dec& b) {
  if (dec_is_zero(a) && dec_is_zero(b)) return 0;
  if (a.neg != b.neg) return a.neg ? -1 : 1;
  std::string A, B;
  long long s;
  dec_aligned(a, b, A, B, s);
  const int c = cmp_abs(A, B);
  return a.neg ? -c : c;
}

// Exact when the quotient terminates within DIV_SCALE fractional digits (and
// then reported at its minimal scale); otherwise rounded half away from zero to
// exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
Dec dec_div(const Dec& a, const Dec& b, Pos pos = {}) {
  if (dec_is_zero(b)) fail("E_DIV_ZERO", "division by zero", pos);
  const std::string N = scale_up(a.digits, b.scale);
  const std::string D = scale_up(b.digits, a.scale);
  std::string q, r;
  divmod_abs(scale_up(N, DIV_SCALE), D, q, r);
  const bool neg = a.neg != b.neg;

  if (r == "0") {
    // Exact: drop trailing zeros to reach the minimal scale.
    std::string digits = q;
    long long scale = DIV_SCALE;
    while (scale > 0 && digits.size() > 1 && digits.back() == '0') {
      digits.pop_back();
      scale--;
    }
    if (digits == "0") scale = 0;
    return dec_make(neg, digits, scale);
  }
  const std::string up = cmp_abs(add_abs(r, r), D) >= 0 ? add_abs(q, "1") : q;
  return dec_make(neg, up, DIV_SCALE);
}

// Remainder of truncated division: takes the sign of the dividend.
Dec dec_mod(const Dec& a, const Dec& b, Pos pos = {}) {
  if (dec_is_zero(b)) fail("E_DIV_ZERO", "modulo by zero", pos);
  std::string A, B;
  long long s;
  dec_aligned(a, b, A, B, s);
  std::string q, r;
  divmod_abs(A, B, q, r);
  return dec_make(a.neg, r, s);
}

// --- rounding. Every rounding in SEL is half away from zero (spec §4.4).

Dec dec_round(const Dec& d, long long n) {
  if (n >= d.scale) return dec_make(d.neg, scale_up(d.digits, n - d.scale), n);
  const long long k = d.scale - n;
  const std::string p = pow10(k);
  std::string q, r;
  divmod_abs(d.digits, p, q, r);
  const std::string up = cmp_abs(add_abs(r, r), p) >= 0 ? add_abs(q, "1") : q;
  return dec_make(d.neg, up, n);
}

Dec dec_trunc(const Dec& d) {
  if (d.scale == 0) return d;
  std::string q, r;
  divmod_abs(d.digits, pow10(d.scale), q, r);
  return dec_make(d.neg, q, 0);
}

Dec dec_floor(const Dec& d) {
  if (d.scale == 0) return d;
  std::string q, r;
  divmod_abs(d.digits, pow10(d.scale), q, r);
  return dec_make(d.neg, d.neg && r != "0" ? add_abs(q, "1") : q, 0);
}

Dec dec_ceil(const Dec& d) {
  if (d.scale == 0) return d;
  std::string q, r;
  divmod_abs(d.digits, pow10(d.scale), q, r);
  return dec_make(d.neg, !d.neg && r != "0" ? add_abs(q, "1") : q, 0);
}

// True when the value has no fractional part left after its scale is honoured.
bool dec_is_integer(const Dec& d) {
  if (d.scale == 0) return true;
  std::string q, r;
  divmod_abs(d.digits, pow10(d.scale), q, r);
  return r == "0";
}

// n must be a non-negative integer; the result scale is scale(x) * n, which
// falls out of repeated multiplication.
Dec dec_power(const Dec& a, long long n) {
  Dec result = dec_make(false, "1", 0);
  Dec base = a;
  long long e = n;
  while (e > 0) {
    if (e & 1) result = dec_mul(result, base);
    e >>= 1;
    if (e > 0) base = dec_mul(base, base);
  }
  return result;
}

// Truncates towards zero and converts. Used where a built-in needs a count or a
// length; the caller has already checked the range it cares about.
long long dec_to_int(const Dec& d) {
  const Dec t = dec_trunc(d);
  long long v = 0;
  for (char c : t.digits) {
    if (v > (9223372036854775807LL - (c - '0')) / 10) return t.neg ? -9223372036854775807LL - 1
                                                                  : 9223372036854775807LL;
    v = v * 10 + (c - '0');
  }
  return t.neg ? -v : v;
}

// ============================================================================
// --- value
//
// See spec/SPEC.md §3. The public shape is in sel.hpp; what follows is the part
// the interpreter needs and host code does not.
// ============================================================================

std::string quote_dump(std::string_view s);

}  // namespace

// --- Value, out of line ------------------------------------------------------

// The unchecked constructor. Every internal producer of TEXT either copied
// existing text or encoded code points it had just validated, so re-validating
// would be pure cost; Value::text() is the checked entry point host code uses.
struct Internals {
  static Value raw(Kind kind, std::string scalar, bool b) {
    Value v;
    v.kind_ = kind;
    v.scalar_ = std::move(scalar);
    v.bool_ = b;
    return v;
  }
};

namespace {

Value make_text(std::string utf8) { return Internals::raw(Kind::Text, std::move(utf8), false); }
Value make_bin(std::string bytes) { return Internals::raw(Kind::Bin, std::move(bytes), false); }
Value make_num(const Dec& d) { return make_text(dec_format(d)); }
Value make_int(long long n) { return make_text(dec_format(dec_from_int(n))); }

}  // namespace

Value Value::none() { return Internals::raw(Kind::None, "", false); }

Value Value::text(std::string utf8) {
  if (!sel::is_valid_utf8(utf8)) {
    throw SelError("E_UTF8", "text is not valid UTF-8", Pos{});
  }
  return Internals::raw(Kind::Text, std::move(utf8), false);
}

Value Value::bin(std::string bytes) { return Internals::raw(Kind::Bin, std::move(bytes), false); }

Value Value::bin(const std::vector<std::uint8_t>& bytes) {
  return Internals::raw(Kind::Bin, std::string(bytes.begin(), bytes.end()), false);
}

Value Value::boolean(bool b) { return Internals::raw(Kind::Bool, "", b); }

Value Value::num(const std::string& decimal) {
  Dec d;
  if (!sel::dec_parse(decimal, d)) {
    throw SelError("E_NOT_NUM", "not a number: " + decimal, Pos{});
  }
  return Internals::raw(Kind::Text, dec_format(d), false);
}

Value Value::integer(long long n) {
  return Internals::raw(Kind::Text, dec_format(dec_from_int(n)), false);
}

Value Value::list(std::vector<Value> values) {
  Value v = none();
  for (std::size_t i = 0; i < values.size(); i++) {
    v.set(std::to_string(i + 1), std::move(values[i]));
  }
  return v;
}

std::vector<Value::Entry>::iterator Value::find(const std::string& key) {
  return std::find_if(children_.begin(), children_.end(),
                      [&](const Entry& e) { return e.first == key; });
}

std::vector<Value::Entry>::const_iterator Value::find(const std::string& key) const {
  return std::find_if(children_.begin(), children_.end(),
                      [&](const Entry& e) { return e.first == key; });
}

bool Value::has(const std::string& key) const { return find(key) != children_.end(); }

const Value* Value::get(const std::string& key) const {
  auto it = find(key);
  return it == children_.end() ? nullptr : &it->second;
}

Value* Value::get(const std::string& key) {
  auto it = find(key);
  return it == children_.end() ? nullptr : &it->second;
}

std::vector<std::string> Value::keys() const {
  std::vector<std::string> out;
  out.reserve(children_.size());
  for (const auto& e : children_) out.push_back(e.first);
  return out;
}

// Re-assigning an existing key keeps its original position — order is normative.
Value& Value::set(std::string key, Value value) {
  auto it = find(key);
  if (it != children_.end()) {
    it->second = std::move(value);
  } else {
    children_.emplace_back(std::move(key), std::move(value));
  }
  return *this;
}

void Value::erase(const std::string& key) {
  auto it = find(key);
  if (it != children_.end()) children_.erase(it);
}

// The value that supplies the scalar: itself, or its first child, recursively.
const Value& Value::scalar_source(Pos pos) const {
  const Value* v = this;
  int guard = 0;
  while (v->kind_ == Kind::None) {
    if (v->children_.empty()) {
      throw SelError("E_NO_SCALAR", "value has no scalar and no children", pos);
    }
    v = &v->children_.front().second;
    if (++guard > 1000) throw SelError("E_DEPTH", "scalar context nested too deeply", pos);
  }
  return *v;
}

const std::string& Value::as_text(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.kind_ == Kind::Text) return v.scalar_;
  if (v.kind_ == Kind::Bin) {
    throw SelError("E_NOT_TEXT", "expected text, got binary (use FROM_UTF8)", pos);
  }
  throw SelError("E_NOT_TEXT", "expected text, got boolean", pos);
}

// TEXT already holds its UTF-8 bytes, so this is free for both kinds.
const std::string& Value::as_bytes(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.kind_ == Kind::Bin || v.kind_ == Kind::Text) return v.scalar_;
  throw SelError("E_NOT_BIN", "expected binary or text, got boolean", pos);
}

bool Value::as_bool(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.kind_ == Kind::Bool) return v.bool_;
  throw SelError("E_NOT_BOOL", "expected a boolean — SEL has no truthiness", pos);
}

std::string Value::as_num(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.kind_ != Kind::Text) {
    throw SelError("E_NOT_NUM",
                   std::string("expected a number, got ") +
                       (v.kind_ == Kind::Bin ? "bin" : v.kind_ == Kind::Bool ? "bool" : "none"),
                   pos);
  }
  Dec d;
  if (!sel::dec_parse(v.scalar_, d)) {
    throw SelError("E_NOT_NUM", "not a number: \"" + v.scalar_ + "\"", pos);
  }
  return dec_format(d);
}

bool Value::looks_numeric() const {
  if (kind_ == Kind::None && children_.empty()) return false;
  const Value* v;
  try {
    v = &scalar_source();
  } catch (const SelError&) {
    return false;
  }
  Dec d;
  return v->kind_ == Kind::Text && sel::dec_parse(v->scalar_, d);
}

// Same kind, equal scalars with numbers *not* normalised, children with the same
// keys in the same order, pairwise EQL.
bool Value::eql(const Value& other) const {
  if (kind_ != other.kind_) return false;
  if (kind_ == Kind::Text || kind_ == Kind::Bin) {
    if (scalar_ != other.scalar_) return false;
  } else if (kind_ == Kind::Bool) {
    if (bool_ != other.bool_) return false;
  }
  if (children_.size() != other.children_.size()) return false;
  for (std::size_t i = 0; i < children_.size(); i++) {
    if (children_[i].first != other.children_[i].first) return false;   // order is normative
    if (!children_[i].second.eql(other.children_[i].second)) return false;
  }
  return true;
}

std::string Value::dump() const {
  std::string s;
  switch (kind_) {
    case Kind::None: s = "-"; break;
    case Kind::Text: s = "t" + sel::quote_dump(scalar_); break;
    case Kind::Bin: s = "b" + sel::to_hex(scalar_); break;
    case Kind::Bool: s = bool_ ? "TRUE" : "FALSE"; break;
  }
  if (children_.empty()) return s;
  s += "{";
  for (std::size_t i = 0; i < children_.size(); i++) {
    if (i > 0) s += ", ";
    s += sel::quote_dump(children_[i].first) + "=" + children_[i].second.dump();
  }
  return s + "}";
}

namespace {

// The dump's escape set, fixed by conformance/README.md: backslash, quote, the
// three whitespace escapes, and \uXXXX for anything else below U+0020.
std::string quote_dump(std::string_view s) {
  std::string out = "\"";
  for (char32_t c : decode_utf8(s)) {
    switch (c) {
      case U'\\': out += "\\\\"; continue;
      case U'"': out += "\\\""; continue;
      case U'\n': out += "\\n"; continue;
      case U'\t': out += "\\t"; continue;
      case U'\r': out += "\\r"; continue;
      default: break;
    }
    if (c < 0x20) {
      char buf[8];
      std::snprintf(buf, sizeof buf, "\\u%04x", static_cast<unsigned>(c));
      out += buf;
    } else {
      encode_cp(out, c);
    }
  }
  return out + "\"";
}

// ============================================================================
// --- registry
//
// The function table, fixed at startup. SEL has no DEFUN, which is what lets an
// unknown name and a wrong argument count be compile-time errors.
// ============================================================================

class Args;
struct Context;

constexpr int VARIADIC = 1 << 20;

struct Spec {
  std::string name;
  int min = 0;
  int max = 0;
  bool lazy = false;
  bool binds = false;   // introduces an element binder; see dependencies()
  // Optional extra arity rule, checked after min/max. Returns a message when the
  // count is wrong and an empty string when it is fine.
  std::string (*arity_error)(int) = nullptr;
  Value (*fn)(Args&, Context&) = nullptr;
};

// A function-local static, so the table is built on first use rather than
// depending on the order of static initialisers across the translation unit.
std::map<std::string, Spec>& table() {
  static std::map<std::string, Spec> t;
  return t;
}

void define(Spec spec) {
  if (table().count(spec.name)) {
    throw std::runtime_error("SEL function " + spec.name + " defined twice");
  }
  table()[spec.name] = std::move(spec);
}

const Spec* registry_lookup(const std::string& name) {
  auto it = table().find(name);
  return it == table().end() ? nullptr : &it->second;
}

void register_builtins();   // defined after the built-ins themselves

// Every entry point that can parse must go through this first: the table has to
// be complete before any source is read.
void ensure_registered() {
  static bool done = [] {
    register_builtins();
    return true;
  }();
  (void)done;
}

// ============================================================================
// --- lexer
//
// See spec/grammar.md. The source is held as code points, so every offset, line
// and column in an error is a code point index — which is what keeps reported
// positions identical across hosts.
//
// String interpolation is resolved here and nowhere else: a literal containing
// {…} is emitted as the token stream of a parenthesised `&` chain, so the parser
// never learns that interpolation exists.
// ============================================================================

// Longest match first: `$<=` must not lex as `$<` followed by `=`.
const std::vector<std::string>& operators() {
  static const std::vector<std::string> ops = {
      "$==", "$!=", "$<=", "$>=",
      "$<", "$>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "&=",
      "+", "-", "*", "/", "%", "&", "=", "<", ">", "(", ")", "[", "]", ",", ";",
  };
  return ops;
}

bool is_reserved(const std::string& w) {
  static const std::set<std::string> r = {
      "TRUE", "FALSE", "AND", "OR", "NOT", "XOR", "EQL", "IN", "BAND", "BOR", "BXOR",
  };
  return r.count(w) > 0;
}

enum class Tok { Num, Text, Ident, Op, Eof };

struct Token {
  Tok type = Tok::Eof;
  std::string value;
  Pos pos;
};

bool is_digit(char32_t c) { return c >= U'0' && c <= U'9'; }
bool is_alpha(char32_t c) {
  return (c >= U'A' && c <= U'Z') || (c >= U'a' && c <= U'z') || c == U'_';
}
bool is_ident(char32_t c) { return is_alpha(c) || is_digit(c); }
bool is_space(char32_t c) { return c == U' ' || c == U'\t' || c == U'\r' || c == U'\n'; }

class Lexer {
 public:
  explicit Lexer(const std::string& source) {
    // Decoding here also validates the source: bad UTF-8 is E_UTF8 at the
    // offending byte rather than a silently mangled token.
    chars_ = decode_utf8(source);
    n_ = chars_.size();
    line_starts_.push_back(0);
    for (std::size_t i = 0; i < n_; i++) {
      if (chars_[i] == U'\n') line_starts_.push_back(i + 1);
    }
  }

  std::vector<Token> tokenize() {
    std::vector<Token> out;
    lex_range(0, n_, out);
    out.push_back(Token{Tok::Eof, "", pos_at(n_)});
    return out;
  }

 private:
  CodePoints chars_;
  std::size_t n_ = 0;
  std::vector<std::size_t> line_starts_;

  Pos pos_at(std::size_t offset) const {
    std::size_t lo = 0, hi = line_starts_.size() - 1;
    while (lo < hi) {
      const std::size_t mid = (lo + hi + 1) / 2;
      if (line_starts_[mid] <= offset) lo = mid; else hi = mid - 1;
    }
    Pos p;
    p.line = static_cast<int>(lo) + 1;
    p.col = static_cast<int>(offset - line_starts_[lo]) + 1;
    p.offset = static_cast<int>(offset);
    return p;
  }

  std::string slice(std::size_t from, std::size_t to) const {
    return encode_utf8(std::span<const char32_t>(chars_).subspan(from, to - from));
  }

  void lex_range(std::size_t from, std::size_t to, std::vector<Token>& out) {
    std::size_t i = from;
    while (i < to) {
      const char32_t c = chars_[i];

      if (is_space(c)) { i++; continue; }

      if (c == U'#') {
        while (i < to && chars_[i] != U'\n') i++;
        continue;
      }

      const Pos pos = pos_at(i);

      if (is_digit(c)) {
        std::size_t j = i;
        while (j < to && is_digit(chars_[j])) j++;
        // Only consume the dot when a digit follows, so `1.` is not a number.
        if (j + 1 < to && chars_[j] == U'.' && is_digit(chars_[j + 1])) {
          j++;
          while (j < to && is_digit(chars_[j])) j++;
        }
        out.push_back(Token{Tok::Num, slice(i, j), pos});
        i = j;
        continue;
      }

      if (is_alpha(c)) {
        std::size_t j = i;
        while (j < to && is_ident(chars_[j])) j++;
        // Identifiers are ASCII and case-insensitive; upper case is canonical.
        std::string word = slice(i, j);
        for (char& ch : word) {
          if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 32);
        }
        out.push_back(Token{Tok::Ident, word, pos});
        i = j;
        continue;
      }

      if (c == U'"') { i = lex_quoted(i, to, out); continue; }
      if (c == U'\'') { i = lex_raw(i, to, out); continue; }

      const std::string op = match_operator(i, to);
      if (!op.empty()) {
        out.push_back(Token{Tok::Op, op, pos});
        i += op.size();
        continue;
      }

      fail("E_SYNTAX", "unexpected character \"" + slice(i, i + 1) + "\"", pos);
    }
  }

  std::string match_operator(std::size_t i, std::size_t to) const {
    for (const std::string& op : operators()) {
      if (i + op.size() > to) continue;
      bool ok = true;
      for (std::size_t k = 0; k < op.size(); k++) {
        if (chars_[i + k] != static_cast<char32_t>(static_cast<unsigned char>(op[k]))) {
          ok = false;
          break;
        }
      }
      if (ok) return op;
    }
    return "";
  }

  // --- text literals

  // Raw 'literals' take no escapes and no interpolation; '' is one quote. This
  // is the form to use for regex patterns.
  std::size_t lex_raw(std::size_t start, std::size_t to, std::vector<Token>& out) {
    const Pos pos = pos_at(start);
    std::size_t i = start + 1;
    std::string buf;
    while (i < to) {
      const char32_t c = chars_[i];
      if (c == U'\'') {
        if (i + 1 < to && chars_[i + 1] == U'\'') { buf += "'"; i += 2; continue; }
        out.push_back(Token{Tok::Text, buf, pos});
        return i + 1;
      }
      encode_cp(buf, c);
      i++;
    }
    fail("E_UNTERMINATED", "unterminated raw text literal", pos);
  }

  struct Part {
    bool is_expr = false;
    std::string text;
    std::size_t from = 0, to = 0;
  };

  std::size_t lex_quoted(std::size_t start, std::size_t to, std::vector<Token>& out) {
    const Pos pos = pos_at(start);
    std::vector<Part> parts;
    std::string buf;
    std::size_t i = start + 1;

    while (i < to) {
      const char32_t c = chars_[i];

      if (c == U'"') {
        parts.push_back(Part{false, buf, 0, 0});
        emit_parts(parts, pos, out);
        return i + 1;
      }

      if (c == U'\\') {
        std::size_t next;
        buf += read_escape(i, to, next);
        i = next;
        continue;
      }

      if (c == U'{') {
        const std::size_t close = match_brace(i, to) - 1;   // index of the matching '}'
        parts.push_back(Part{false, buf, 0, 0});
        buf.clear();
        parts.push_back(Part{true, "", i + 1, close});
        i = close + 1;
        continue;
      }

      encode_cp(buf, c);
      i++;
    }
    fail("E_UNTERMINATED", "unterminated text literal", pos);
  }

  std::string read_escape(std::size_t i, std::size_t to, std::size_t& next) {
    const Pos pos = pos_at(i);
    if (i + 1 >= to) fail("E_UNTERMINATED", "text literal ends in a backslash", pos);
    const char32_t e = chars_[i + 1];

    switch (e) {
      case U'\\': next = i + 2; return "\\";
      case U'"': next = i + 2; return "\"";
      case U'n': next = i + 2; return "\n";
      case U't': next = i + 2; return "\t";
      case U'r': next = i + 2; return "\r";
      case U'{': next = i + 2; return "{";
      case U'}': next = i + 2; return "}";
      default: break;
    }

    if (e == U'u') {
      if (i + 2 >= to || chars_[i + 2] != U'{') fail("E_ESCAPE", "\\u must be followed by {", pos);
      std::size_t j = i + 3;
      std::string hex;
      while (j < to && chars_[j] != U'}') { encode_cp(hex, chars_[j]); j++; }
      if (j >= to) fail("E_UNTERMINATED", "unterminated \\u{...} escape", pos);
      const bool ok = !hex.empty() && hex.size() <= 6 &&
                      std::all_of(hex.begin(), hex.end(), [](char ch) {
                        return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') ||
                               (ch >= 'A' && ch <= 'F');
                      });
      if (!ok) fail("E_ESCAPE", "bad \\u{" + hex + "} escape", pos);
      const unsigned long cp = std::stoul(hex, nullptr, 16);
      if (cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
        std::string upper = hex;
        for (char& ch : upper) {
          if (ch >= 'a' && ch <= 'f') ch = static_cast<char>(ch - 32);
        }
        fail("E_RANGE", "code point U+" + upper + " is not encodable", pos);
      }
      next = j + 1;
      std::string s;
      encode_cp(s, static_cast<char32_t>(cp));
      return s;
    }

    fail("E_ESCAPE", "unknown escape \\" + slice(i + 1, i + 2), pos);
  }

  // Returns the index just past the matching '}'. Nested literals are skipped so
  // that a brace inside a string inside an interpolation does not close it.
  std::size_t match_brace(std::size_t i, std::size_t to) {
    const Pos pos = pos_at(i);
    int depth = 0;
    std::size_t j = i;
    while (j < to) {
      const char32_t c = chars_[j];
      if (c == U'"') { j = skip_quoted(j, to); continue; }
      if (c == U'\'') { j = skip_raw(j, to); continue; }
      if (c == U'{') { depth++; j++; continue; }
      if (c == U'}') { depth--; j++; if (depth == 0) return j; continue; }
      if (c == U'#') { while (j < to && chars_[j] != U'\n') j++; continue; }
      j++;
    }
    fail("E_UNTERMINATED", "unterminated { in text literal", pos);
  }

  std::size_t skip_quoted(std::size_t j, std::size_t to) {
    const Pos pos = pos_at(j);
    j++;
    while (j < to) {
      const char32_t c = chars_[j];
      if (c == U'\\') { j += 2; continue; }
      if (c == U'"') return j + 1;
      if (c == U'{') { j = match_brace(j, to); continue; }
      j++;
    }
    fail("E_UNTERMINATED", "unterminated text literal", pos);
  }

  std::size_t skip_raw(std::size_t j, std::size_t to) {
    const Pos pos = pos_at(j);
    j++;
    while (j < to) {
      if (chars_[j] == U'\'') {
        if (j + 1 < to && chars_[j + 1] == U'\'') { j += 2; continue; }
        return j + 1;
      }
      j++;
    }
    fail("E_UNTERMINATED", "unterminated raw text literal", pos);
  }

  // A literal with no interpolation is one token. Otherwise it becomes the
  // tokens of `( "seg" & expr & "seg" )` — empty segments included, so the
  // result always goes through `&` and obeys §5.2.
  void emit_parts(const std::vector<Part>& parts, Pos pos, std::vector<Token>& out) {
    if (parts.size() == 1) {
      out.push_back(Token{Tok::Text, parts[0].text, pos});
      return;
    }
    out.push_back(Token{Tok::Op, "(", pos});
    for (std::size_t k = 0; k < parts.size(); k++) {
      if (k > 0) out.push_back(Token{Tok::Op, "&", pos});
      if (!parts[k].is_expr) {
        out.push_back(Token{Tok::Text, parts[k].text, pos});
      } else {
        const std::size_t mark = out.size();
        out.push_back(Token{Tok::Op, "(", pos_at(parts[k].from)});
        lex_range(parts[k].from, parts[k].to, out);
        if (out.size() == mark + 1) {
          fail("E_SYNTAX", "empty interpolation {}", pos_at(parts[k].from));
        }
        out.push_back(Token{Tok::Op, ")", pos_at(parts[k].to)});
      }
    }
    out.push_back(Token{Tok::Op, ")", pos});
  }
};

std::vector<Token> tokenize(const std::string& source) { return Lexer(source).tokenize(); }

}  // namespace

// ============================================================================
// --- parser
//
// Recursive descent, one function per precedence level, mirroring
// spec/grammar.md exactly so that the four implementations can be read side by
// side.
// ============================================================================

enum class NT { Num, Text, Bool, Var, Index, Seq, List, Un, Bin, Assign, Call };

struct Node {
  NT t = NT::Num;
  Pos pos;

  std::string s;        // Num/Text: the literal. Var: the name. Un/Bin/Assign: the operator.
  bool b = false;       // Bool: the value.
  bool grouped = false; // came from ( ), so F((1,2)) passes one list not two arguments

  std::shared_ptr<const Node> l, r;       // Bin: operands. Index: obj, idx. Assign: target, value.
  std::vector<std::shared_ptr<const Node>> items;   // Seq/List/Call arguments
  const Spec* spec = nullptr;             // Call
};

using NodePtr = std::shared_ptr<const Node>;

namespace {

constexpr int MAX_DEPTH = 200;

std::string describe(const Token& t) {
  switch (t.type) {
    case Tok::Eof: return "end of input";
    case Tok::Text: return "a text literal";
    case Tok::Num: return "number " + t.value;
    default: return "\"" + t.value + "\"";
  }
}

std::string arity_text(const Spec& spec) {
  const std::string plural = spec.min == 1 ? "" : "s";
  if (spec.max >= VARIADIC) return "at least " + std::to_string(spec.min) + " argument" + plural;
  if (spec.min == spec.max) return std::to_string(spec.min) + " argument" + plural;
  return std::to_string(spec.min) + " to " + std::to_string(spec.max) + " arguments";
}

bool is_assign_op(const Token& t) {
  static const std::set<std::string> ops = {"=", "+=", "-=", "*=", "/=", "%=", "&="};
  return t.type == Tok::Op && ops.count(t.value) > 0;
}

bool is_compare_op(const Token& t) {
  static const std::set<std::string> ops = {"==", "!=", "<", "<=", ">", ">=",
                                            "$==", "$!=", "$<", "$<=", "$>", "$>="};
  return t.type == Tok::Op && ops.count(t.value) > 0;
}

bool is_compare_word(const Token& t) {
  return t.type == Tok::Ident && (t.value == "EQL" || t.value == "IN");
}

// The target must be an identifier followed by zero or more index operations.
void check_target(const NodePtr& node, const Token& op_tok) {
  const Node* n = node.get();
  while (n->t == NT::Index) n = n->l.get();
  if (n->t != NT::Var || node->grouped) {
    fail("E_BAD_ASSIGN", "cannot assign with " + op_tok.value + " to this expression", node->pos);
  }
}

class Parser {
 public:
  explicit Parser(std::vector<Token> tokens) : toks_(std::move(tokens)) {}

  NodePtr parse_program() {
    NodePtr node = parse_sequence();
    if (!at_eof()) fail("E_SYNTAX", "unexpected " + describe(peek()), peek().pos);
    return node;
  }

 private:
  std::vector<Token> toks_;
  std::size_t i_ = 0;
  int depth_ = 0;

  const Token& peek() const { return toks_[i_]; }
  const Token& next() { return toks_[i_++]; }
  bool at_op(const char* v) const { return peek().type == Tok::Op && peek().value == v; }
  bool at_word(const char* v) const { return peek().type == Tok::Ident && peek().value == v; }
  bool at_eof() const { return peek().type == Tok::Eof; }

  void expect_op(const char* v) {
    if (!at_op(v)) {
      fail("E_SYNTAX", std::string("expected \"") + v + "\", got " + describe(peek()), peek().pos);
    }
    next();
  }

  void enter(Pos pos) {
    if (++depth_ > MAX_DEPTH) fail("E_DEPTH", "expression nested too deeply", pos);
  }
  void leave() { depth_--; }

  static std::shared_ptr<Node> make(NT t, Pos pos) {
    auto n = std::make_shared<Node>();
    n->t = t;
    n->pos = pos;
    return n;
  }

  // sequence = list { ";" list } [ ";" ]
  NodePtr parse_sequence() {
    const Pos start = peek().pos;
    enter(start);
    std::vector<NodePtr> items{parse_list()};
    while (at_op(";")) {
      next();
      // A trailing ';' before a closer or end of input is permitted.
      if (at_eof() || at_op(")") || at_op("]")) break;
      items.push_back(parse_list());
    }
    leave();
    if (items.size() == 1) return items[0];
    auto n = make(NT::Seq, items[0]->pos);
    n->items = std::move(items);
    return n;
  }

  // list = assignment { "," assignment }
  NodePtr parse_list() {
    std::vector<NodePtr> items{parse_assignment()};
    while (at_op(",")) {
      next();
      items.push_back(parse_assignment());
    }
    if (items.size() == 1) return items[0];
    auto n = make(NT::List, items[0]->pos);
    n->items = std::move(items);
    return n;
  }

  // assignment = disjunction [ assign_op assignment ]   (right associative)
  NodePtr parse_assignment() {
    NodePtr left = parse_or();
    if (is_assign_op(peek())) {
      const Token op = next();
      check_target(left, op);
      NodePtr value = parse_assignment();
      auto n = make(NT::Assign, left->pos);
      n->s = op.value;
      n->l = left;
      n->r = value;
      return n;
    }
    return left;
  }

  NodePtr parse_or() { return parse_word_binary("OR", &Parser::parse_xor); }
  NodePtr parse_xor() { return parse_word_binary("XOR", &Parser::parse_and); }
  NodePtr parse_and() { return parse_word_binary("AND", &Parser::parse_not); }

  NodePtr parse_word_binary(const char* word, NodePtr (Parser::*sub)()) {
    NodePtr left = (this->*sub)();
    while (at_word(word)) {
      const Token op = next();
      NodePtr right = (this->*sub)();
      auto n = make(NT::Bin, op.pos);
      n->s = word;
      n->l = left;
      n->r = right;
      left = n;
    }
    return left;
  }

  // negation = "NOT" negation | comparison
  NodePtr parse_not() {
    if (at_word("NOT")) {
      const Token op = next();
      auto n = make(NT::Un, op.pos);
      n->s = "NOT";
      n->l = parse_not();
      return n;
    }
    return parse_comparison();
  }

  // comparison = bit_or [ compare_op bit_or ]   — deliberately non-associative
  NodePtr parse_comparison() {
    NodePtr left = parse_bit_or();
    const Token t = peek();
    if (!is_compare_op(t) && !is_compare_word(t)) return left;

    next();
    NodePtr right = parse_bit_or();
    const Token& after = peek();
    if (is_compare_op(after) || is_compare_word(after)) {
      fail("E_SYNTAX",
           "comparison operators do not chain — parenthesise, as in (a " + t.value + " b) AND (b " +
               after.value + " c)",
           after.pos);
    }
    auto n = make(NT::Bin, t.pos);
    n->s = t.value;
    n->l = left;
    n->r = right;
    return n;
  }

  NodePtr parse_bit_or() { return parse_word_binary("BOR", &Parser::parse_bit_xor); }
  NodePtr parse_bit_xor() { return parse_word_binary("BXOR", &Parser::parse_bit_and); }
  NodePtr parse_bit_and() { return parse_word_binary("BAND", &Parser::parse_concat); }

  NodePtr parse_concat() { return parse_op_binary({"&"}, &Parser::parse_additive); }
  NodePtr parse_additive() { return parse_op_binary({"+", "-"}, &Parser::parse_multiplicative); }
  NodePtr parse_multiplicative() { return parse_op_binary({"*", "/", "%"}, &Parser::parse_unary); }

  NodePtr parse_op_binary(std::initializer_list<const char*> ops, NodePtr (Parser::*sub)()) {
    NodePtr left = (this->*sub)();
    for (;;) {
      const Token t = peek();
      if (t.type != Tok::Op) return left;
      bool match = false;
      for (const char* op : ops) {
        if (t.value == op) { match = true; break; }
      }
      if (!match) return left;
      next();
      auto n = make(NT::Bin, t.pos);
      n->s = t.value;
      n->l = left;
      n->r = (this->*sub)();
      left = n;
    }
  }

  // unary = "-" unary | postfix
  NodePtr parse_unary() {
    if (at_op("-")) {
      const Token op = next();
      auto n = make(NT::Un, op.pos);
      n->s = "NEG";
      n->l = parse_unary();
      return n;
    }
    return parse_postfix();
  }

  // postfix = primary { "[" sequence "]" }
  NodePtr parse_postfix() {
    NodePtr node = parse_primary();
    while (at_op("[")) {
      const Token br = next();
      NodePtr idx = parse_sequence();
      expect_op("]");
      auto n = make(NT::Index, br.pos);
      n->l = node;
      n->r = idx;
      node = n;
    }
    return node;
  }

  NodePtr parse_primary() {
    const Token t = peek();
    enter(t.pos);
    struct Leave {
      Parser* p;
      ~Leave() { p->leave(); }
    } leave_guard{this};

    if (t.type == Tok::Num) {
      next();
      // Canonicalised once, here: the literal 007 is the value 7.
      Dec d;
      dec_parse(t.value, d);
      auto n = make(NT::Num, t.pos);
      n->s = dec_format(d);
      return n;
    }
    if (t.type == Tok::Text) {
      next();
      auto n = make(NT::Text, t.pos);
      n->s = t.value;
      return n;
    }

    if (t.type == Tok::Ident) {
      if (t.value == "TRUE" || t.value == "FALSE") {
        next();
        auto n = make(NT::Bool, t.pos);
        n->b = t.value == "TRUE";
        return n;
      }
      const Token& after = toks_[i_ + 1];
      if (after.type == Tok::Op && after.value == "(") return parse_call();
      if (is_reserved(t.value)) {
        fail("E_RESERVED", t.value + " is a reserved word and cannot be a variable", t.pos);
      }
      next();
      auto n = make(NT::Var, t.pos);
      n->s = t.value;
      return n;
    }

    if (t.type == Tok::Op && t.value == "(") {
      next();
      if (at_op(")")) fail("E_SYNTAX", "empty parentheses", t.pos);
      NodePtr inner = parse_sequence();
      expect_op(")");
      // Marked so that F((1,2)) passes one list rather than two arguments.
      auto copy = std::make_shared<Node>(*inner);
      copy->grouped = true;
      return copy;
    }

    fail("E_SYNTAX", "unexpected " + describe(t), t.pos);
  }

  NodePtr parse_call() {
    const Token name_tok = next();
    expect_op("(");
    std::vector<NodePtr> args;
    if (at_op(")")) {
      next();
    } else {
      NodePtr inner = parse_sequence();
      expect_op(")");
      if (inner->t == NT::List && !inner->grouped) args = inner->items;
      else args.push_back(inner);
    }

    const Spec* spec = registry_lookup(name_tok.value);
    if (!spec) fail("E_UNKNOWN_FUNC", "unknown function " + name_tok.value, name_tok.pos);
    const int count = static_cast<int>(args.size());
    if (count < spec->min || count > spec->max) {
      fail("E_ARITY", spec->name + " takes " + arity_text(*spec) + ", got " + std::to_string(count),
           name_tok.pos);
    }
    if (spec->arity_error) {
      const std::string problem = spec->arity_error(count);
      if (!problem.empty()) fail("E_ARITY", problem, name_tok.pos);
    }
    auto n = make(NT::Call, name_tok.pos);
    n->s = spec->name;
    n->spec = spec;
    n->items = std::move(args);
    return n;
  }
};

NodePtr parse(const std::string& source) {
  ensure_registered();
  return Parser(tokenize(source)).parse_program();
}

// ============================================================================
// --- eval
//
// Nothing here catches a SelError. An error surfaces from the innermost node
// that failed, carrying that node's position, and no layer rewrites it.
// ============================================================================

struct Context {
  Value* root;
  // Aggregate binders. The only scoping SEL has: one name for the duration of
  // one element, pushed by the aggregates and popped again afterwards.
  std::vector<std::vector<std::pair<std::string, Value>>> frames;
  int depth = 0;

  explicit Context(Value& r) : root(&r) {}

  const Value* lookup(const std::string& name) const {
    for (auto it = frames.rbegin(); it != frames.rend(); ++it) {
      for (const auto& e : *it) {
        if (e.first == name) return &e.second;
      }
    }
    return root->get(name);
  }

  bool is_bound(const std::string& name) const {
    for (auto it = frames.rbegin(); it != frames.rend(); ++it) {
      for (const auto& e : *it) {
        if (e.first == name) return true;
      }
    }
    return false;
  }
};

Value eval_node(const Node& node, Context& ctx);

// Wraps the flattened argument vector. Values are evaluated at most once, so a
// built-in body can read the same argument repeatedly without thinking about it,
// and typed accessors report failures against the argument's own position.
class Args {
 public:
  Args(const Node& node, Context& ctx)
      : nodes_(node.items), name_(node.s), pos_(node.pos), ctx_(ctx), vals_(node.items.size()) {}

  int count() const { return static_cast<int>(nodes_.size()); }
  const Node& node(int i) const { return *nodes_[i]; }
  Pos pos_of(int i) const { return nodes_[i]->pos; }
  Pos pos() const { return pos_; }
  const std::string& name() const { return name_; }
  Context& ctx() { return ctx_; }

  const Value& val(int i) {
    if (!vals_[i].has_value()) vals_[i] = eval_node(*nodes_[i], ctx_);
    return *vals_[i];
  }

  // For lazy functions re-evaluating a body node under changed bindings.
  Value eval(const Node& n) { return eval_node(n, ctx_); }

  const std::string& text(int i) { return val(i).as_text(pos_of(i)); }
  const std::string& bytes(int i) { return val(i).as_bytes(pos_of(i)); }
  bool boolean(int i) { return val(i).as_bool(pos_of(i)); }

  Dec dec(int i) {
    const Value& v = val(i).scalar_source(pos_of(i));
    if (v.kind() != Kind::Text) {
      fail("E_NOT_NUM",
           std::string("expected a number, got ") +
               (v.kind() == Kind::Bin ? "bin" : v.kind() == Kind::Bool ? "bool" : "none"),
           pos_of(i));
    }
    Dec d;
    if (!dec_parse(v.scalar(), d)) {
      fail("E_NOT_NUM", "not a number: \"" + v.scalar() + "\"", pos_of(i));
    }
    return d;
  }

  long long integer(int i) {
    const Dec d = dec(i);
    if (!dec_is_integer(d)) {
      fail("E_NOT_INT", name_ + " argument " + std::to_string(i + 1) + " must be a whole number",
           pos_of(i));
    }
    return dec_to_int(d);
  }

  long long non_neg_int(int i) {
    const long long n = integer(i);
    if (n < 0) {
      fail("E_RANGE", name_ + " argument " + std::to_string(i + 1) + " must not be negative",
           pos_of(i));
    }
    return n;
  }

  // Requires the argument to be a bare identifier in the source — the AST shape
  // check that gives aggregates their three-argument binder form.
  const std::string& symbol(int i) {
    const Node& n = *nodes_[i];
    if (n.t != NT::Var || n.grouped) {
      fail("E_EXPECT_SYMBOL", name_ + " argument " + std::to_string(i + 1) + " must be a plain name",
           n.pos);
    }
    return n.s;
  }

 private:
  const std::vector<NodePtr>& nodes_;
  const std::string& name_;
  Pos pos_;
  Context& ctx_;
  std::vector<std::optional<Value>> vals_;
};

bool compare_result(const std::string& op, int c) {
  if (op == "==") return c == 0;
  if (op == "!=") return c != 0;
  if (op == "<") return c < 0;
  if (op == "<=") return c <= 0;
  if (op == ">") return c > 0;
  return c >= 0;   // ">="
}

// TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
Value concat(const Value& l, const Value& r, Pos lp, Pos rp) {
  const Value& lv = l.scalar_source(lp);
  const Value& rv = r.scalar_source(rp);
  if (lv.kind() == Kind::Bool) fail("E_NOT_TEXT", "cannot concatenate a boolean", lp);
  if (rv.kind() == Kind::Bool) fail("E_NOT_TEXT", "cannot concatenate a boolean", rp);
  if (lv.kind() == Kind::Text && rv.kind() == Kind::Text) {
    return make_text(lv.scalar() + rv.scalar());
  }
  const std::string a = l.as_bytes(lp);   // sequenced: left before right
  const std::string b = r.as_bytes(rp);
  return make_bin(a + b);
}

bool is_in(const Value& needle, const Value& hay) {
  if (hay.size() == 0) return hay.eql(needle);
  for (const auto& e : hay.entries()) {
    if (e.second.eql(needle)) return true;
  }
  return false;
}

Value bitwise(const std::string& op, const std::string& a, const std::string& b, Pos pos) {
  if (a.size() != b.size()) {
    fail("E_LEN_MISMATCH",
         op + " needs operands of equal length (" + std::to_string(a.size()) + " vs " +
             std::to_string(b.size()) + ")",
         pos);
  }
  std::string out(a.size(), '\0');
  for (std::size_t i = 0; i < a.size(); i++) {
    const unsigned char x = static_cast<unsigned char>(a[i]);
    const unsigned char y = static_cast<unsigned char>(b[i]);
    out[i] = static_cast<char>(op == "BAND" ? (x & y) : op == "BOR" ? (x | y) : (x ^ y));
  }
  return make_bin(std::move(out));
}

// §5.9 — a value with children and no scalar contributes its children's values;
// anything else contributes itself. Keys are always renumbered from 1.
Value eval_list(const Node& node, Context& ctx) {
  Value out = Value::none();
  int n = 0;
  for (const auto& item : node.items) {
    Value v = eval_node(*item, ctx);
    if (v.kind() == Kind::None && v.size() > 0) {
      for (const auto& child : v.entries()) out.set(std::to_string(++n), child.second);
    } else {
      out.set(std::to_string(++n), std::move(v));
    }
  }
  return out;
}

Value eval_unary(const Node& node, Context& ctx) {
  const Value v = eval_node(*node.l, ctx);
  if (node.s == "NOT") return Value::boolean(!v.as_bool(node.l->pos));
  const Value& src = v.scalar_source(node.l->pos);
  Dec d;
  if (src.kind() != Kind::Text || !dec_parse(src.scalar(), d)) {
    fail("E_NOT_NUM", "expected a number", node.l->pos);
  }
  return make_num(dec_negate(d));
}

// Numeric coercion at an arbitrary position, used by the operators. Built-ins go
// through Args::dec instead, which reports against the argument's own position.
Dec as_dec(const Value& v, Pos pos) {
  const Value& src = v.scalar_source(pos);
  if (src.kind() != Kind::Text) {
    fail("E_NOT_NUM",
         std::string("expected a number, got ") +
             (src.kind() == Kind::Bin ? "bin" : src.kind() == Kind::Bool ? "bool" : "none"),
         pos);
  }
  Dec d;
  if (!dec_parse(src.scalar(), d)) fail("E_NOT_NUM", "not a number: \"" + src.scalar() + "\"", pos);
  return d;
}

Value eval_binary(const Node& node, Context& ctx) {
  const std::string& op = node.s;

  // Short-circuit before either side is touched (§5.5).
  if (op == "AND" || op == "OR") {
    const bool left = eval_node(*node.l, ctx).as_bool(node.l->pos);
    if (op == "AND" && !left) return Value::boolean(false);
    if (op == "OR" && left) return Value::boolean(true);
    return Value::boolean(eval_node(*node.r, ctx).as_bool(node.r->pos));
  }

  const Value l = eval_node(*node.l, ctx);
  const Value r = eval_node(*node.r, ctx);
  const Pos lp = node.l->pos, rp = node.r->pos;

  // Each pair of coercions below is sequenced through named locals rather than
  // written as two arguments to one call. The order of evaluation of function
  // arguments is unspecified in C++ — GCC evaluates them right to left — and SEL
  // requires strictly left to right (§6.2), which is observable: `TRUE $== FALSE`
  // must report the *left* operand's position. The differential fuzzer found
  // this; do not collapse these back into one expression.
  if (op == "+" || op == "-" || op == "*" || op == "/" || op == "%") {
    const Dec a = as_dec(l, lp);
    const Dec b = as_dec(r, rp);
    if (op == "+") return make_num(dec_add(a, b));
    if (op == "-") return make_num(dec_sub(a, b));
    if (op == "*") return make_num(dec_mul(a, b));
    if (op == "/") return make_num(dec_div(a, b, node.pos));
    return make_num(dec_mod(a, b, node.pos));
  }

  if (op == "&") return concat(l, r, lp, rp);

  if (op == "EQL") return Value::boolean(l.eql(r));
  if (op == "IN") return Value::boolean(is_in(l, r));
  if (op == "XOR") {
    const bool a = l.as_bool(lp);
    const bool b = r.as_bool(rp);
    return Value::boolean(a != b);
  }

  if (op == "BAND" || op == "BOR" || op == "BXOR") {
    const std::string a = l.as_bytes(lp);
    const std::string b = r.as_bytes(rp);
    return bitwise(op, a, b, node.pos);
  }

  if (!op.empty() && op[0] == '$') {
    const std::string a = l.as_bytes(lp);
    const std::string b = r.as_bytes(rp);
    return Value::boolean(compare_result(op.substr(1), bytes_compare(a, b)));
  }
  if (is_compare_op(Token{Tok::Op, op, {}})) {
    const Dec a = as_dec(l, lp);
    const Dec b = as_dec(r, rp);
    return Value::boolean(compare_result(op, dec_cmp(a, b)));
  }

  fail("E_SYNTAX", "unknown operator " + op, node.pos);
}

// Walks from the root along PATH, creating any level that is missing, and
// returns the value at the end. Never holds a pointer across an evaluation, and
// never assumes a level survived one: the right-hand side of an assignment, or a
// later index expression, can delete or replace anything.
Value* walk_create(Context& ctx, const std::vector<std::string>& path, std::size_t upto) {
  Value* cur = ctx.root;
  for (std::size_t i = 0; i < upto; i++) {
    Value* next = cur->get(path[i]);
    if (!next) {
      cur->set(path[i], Value::none());
      next = cur->get(path[i]);
    }
    cur = next;
  }
  return cur;
}

// Walks the target chain and returns the full key path, evaluating each index
// expression exactly once, left to right, and creating each intermediate level
// as it goes — the order the other hosts use, and observable, because a later
// index expression can read the level an earlier one just created.
//
// The awkward part is C++-specific. `Value::children_` is a std::vector, so any
// pointer into it dies when the tree grows, and evaluating an index expression
// can run arbitrary code that grows it. So the walk keeps only the path built so
// far and re-derives the pointer from the root after every evaluation. The depth
// is a handful of levels, so the repeated walk costs nothing worth measuring.
std::vector<std::string> resolve_target(const Node& target, Context& ctx) {
  std::vector<const Node*> chain;
  const Node* n = &target;
  while (n->t == NT::Index) {
    chain.insert(chain.begin(), n->r.get());
    n = n->l.get();
  }

  if (ctx.is_bound(n->s)) {
    fail("E_BAD_ASSIGN", n->s + " is an aggregate binder and cannot be assigned", target.pos);
  }
  std::vector<std::string> path{n->s};
  if (chain.empty()) return path;

  // The base variable comes into existence before the first index expression
  // runs, so `A[COUNT(A)] = 1` sees the A that this created.
  if (!ctx.root->get(n->s)) ctx.root->set(n->s, Value::none());

  for (std::size_t i = 0; i + 1 < chain.size(); i++) {
    const std::string k = eval_node(*chain[i], ctx).as_text(chain[i]->pos);
    Value* cur = walk_create(ctx, path, path.size());   // re-derived after the evaluation
    if (!cur->get(k)) cur->set(k, Value::none());
    path.push_back(k);
  }
  const Node* last = chain.back();
  path.push_back(eval_node(*last, ctx).as_text(last->pos));
  return path;
}

Value eval_assign(const Node& node, Context& ctx) {
  // The target is resolved first, then the right-hand side — the order the other
  // hosts use, and observable: `A[1] = COUNT(A)` sees the A that resolving the
  // target just created.
  const std::vector<std::string> path = resolve_target(*node.l, ctx);
  const std::string& key = path.back();

  Value value;
  if (node.s == "=") {
    value = eval_node(*node.r, ctx);
  } else {
    const Value* current = walk_create(ctx, path, path.size() - 1)->get(key);
    if (!current) fail("E_UNDEF_VAR", node.s + " needs an existing target", node.l->pos);
    const Value target_value = *current;   // copied: the right-hand side may move the tree

    const Value rhs = eval_node(*node.r, ctx);
    const Pos tp = node.l->pos, vp = node.r->pos;
    const char binop = node.s[0];

    if (binop == '&') {
      value = concat(target_value, rhs, tp, vp);
    } else {
      const Dec a = as_dec(target_value, tp);
      const Dec b = as_dec(rhs, vp);
      Dec res;
      switch (binop) {
        case '+': res = dec_add(a, b); break;
        case '-': res = dec_sub(a, b); break;
        case '*': res = dec_mul(a, b); break;
        case '/': res = dec_div(a, b, node.pos); break;
        default: res = dec_mod(a, b, node.pos); break;
      }
      value = make_num(res);
    }
  }

  // Re-derived after the right-hand side ran, which may have replaced or
  // deleted any level along the path.
  walk_create(ctx, path, path.size() - 1)->set(key, value);
  return value;
}

Value eval_dispatch(const Node& node, Context& ctx) {
  switch (node.t) {
    case NT::Num: return make_text(node.s);     // canonicalised by the parser
    case NT::Text: return make_text(node.s);
    case NT::Bool: return Value::boolean(node.b);

    case NT::Var: {
      const Value* v = ctx.lookup(node.s);
      if (!v) fail("E_UNDEF_VAR", "undefined variable " + node.s, node.pos);
      return *v;
    }

    case NT::Index: {
      const Value obj = eval_node(*node.l, ctx);
      const std::string key = eval_node(*node.r, ctx).as_text(node.r->pos);
      const Value* child = obj.get(key);
      if (!child) fail("E_NO_KEY", "no key " + quote_dump(key), node.pos);
      return *child;
    }

    case NT::Seq: {
      Value last;
      for (const auto& item : node.items) last = eval_node(*item, ctx);
      return last;
    }

    case NT::List: return eval_list(node, ctx);
    case NT::Un: return eval_unary(node, ctx);
    case NT::Bin: return eval_binary(node, ctx);
    case NT::Assign: return eval_assign(node, ctx);

    case NT::Call: {
      Args args(node, ctx);
      if (!node.spec->lazy) {
        // Strict: every argument evaluated once, left to right, before the body.
        for (int i = 0; i < args.count(); i++) args.val(i);
      }
      return node.spec->fn(args, ctx);
    }
  }
  fail("E_SYNTAX", "cannot evaluate node", node.pos);
}

Value eval_node(const Node& node, Context& ctx) {
  if (++ctx.depth > MAX_DEPTH) {
    ctx.depth--;
    fail("E_DEPTH", "evaluation nested too deeply", node.pos);
  }
  struct Pop {
    Context* c;
    ~Pop() { c->depth--; }
  } pop{&ctx};
  return eval_dispatch(node, ctx);
}

// ============================================================================
// --- builtins
// ============================================================================

CodePoints cps_of(const std::string& s) { return decode_utf8(s); }

// 0-based code point index of `needle` in `hay`, or -1.
long index_of_cp(const CodePoints& hay, const CodePoints& needle, long from) {
  const long n = static_cast<long>(needle.size());
  if (n == 0) return -1;
  // `from` comes from a user-supplied count and can be enormous; returning
  // early keeps `i + n` below the overflow that UBSan flags.
  if (from < 0 || from > static_cast<long>(hay.size())) return -1;
  for (long i = from; i + n <= static_cast<long>(hay.size()); i++) {
    bool ok = true;
    for (long j = 0; j < n; j++) {
      if (hay[i + j] != needle[j]) { ok = false; break; }
    }
    if (ok) return i;
  }
  return -1;
}

std::string slice_cp(const CodePoints& c, long from, long to) {
  from = std::max(0L, std::min(from, static_cast<long>(c.size())));
  to = std::max(from, std::min(to, static_cast<long>(c.size())));
  return encode_utf8(std::span<const char32_t>(c).subspan(from, to - from));
}

// --- control. The whole of SEL's control flow: lazy, so only the taken branch
// is evaluated — exactly the property the AST calling convention exists for.

void register_control() {
  define(Spec{"IF", 2, 3, true, false, nullptr, [](Args& a, Context&) -> Value {
                if (a.boolean(0)) return a.val(1);
                if (a.count() == 3) return a.val(2);
                return make_text("");
              }});

  // Flat multi-branch selection, with exactly IF's laziness. The argument count
  // must be odd: condition/result pairs plus a mandatory default. With an even
  // count a single miscounted comma would shift every pair by one and still
  // compile, so requiring the default turns that into a compile-time E_ARITY
  // rather than a wrong answer at run time.
  define(Spec{"COND", 3, VARIADIC, true, false,
              [](int n) -> std::string {
                if (n % 2 == 0) {
                  return "COND takes condition/result pairs and a final default (an odd number "
                         "of arguments), got " + std::to_string(n);
                }
                return "";
              },
              [](Args& a, Context&) -> Value {
                const int last = a.count() - 1;
                for (int i = 0; i < last; i += 2) {
                  if (a.boolean(i)) return a.val(i + 1);
                }
                return a.val(last);
              }});

  // The one error a rule author raises deliberately.
  define(Spec{"ABORT", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                fail("E_ABORT", a.text(0), a.pos_of(0));
              }});
}

// --- structure

void register_structure() {
  define(Spec{"COUNT", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_int(static_cast<long long>(a.val(0).size()));
              }});

  define(Spec{"INDEXES", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                std::vector<Value> out;
                for (const std::string& k : a.val(0).keys()) out.push_back(make_text(k));
                return Value::list(std::move(out));
              }});

  define(Spec{"HAS", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(a.val(0).has(a.text(1)));
              }});
}

// --- aggregates. These are why SEL needs no loop: each evaluates one argument
// node once per element, which is the same move IF makes, repeated.

// A scalar with no children behaves as a one-element list containing itself,
// consistent with scalar context (§3.2). A NONE with no children is genuinely
// empty — that is what FILTER returns when nothing matched, and ALL over it must
// be TRUE rather than a scalar-context failure.
std::vector<Value::Entry> elements(const Value& value) {
  if (value.size() > 0) return value.entries();
  if (value.kind() == Kind::None) return {};
  return {{"1", value}};
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result.
std::optional<Value> walk(Args& a, Context& ctx,
                          const std::function<std::optional<Value>(const Value&, const std::string&,
                                                                   const Value&, const Node&)>& visit) {
  const bool three = a.count() == 3;
  const std::string binder = three ? a.symbol(1) : std::string("_");
  const Node& body = a.node(three ? 2 : 1);

  for (const auto& [key, item] : elements(a.val(0))) {
    ctx.frames.push_back({{binder, item}, {"_K", make_text(key)}});
    std::optional<Value> result;
    try {
      const Value r = a.eval(body);
      result = visit(r, key, item, body);
    } catch (...) {
      ctx.frames.pop_back();
      throw;
    }
    ctx.frames.pop_back();
    if (result.has_value()) return result;
  }
  return std::nullopt;
}

void register_aggregates() {
  define(Spec{"ALL", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                auto s = walk(a, ctx, [](const Value& r, const std::string&, const Value&,
                                         const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) return std::nullopt;
                  return Value::boolean(false);
                });
                return s.value_or(Value::boolean(true));
              }});

  define(Spec{"ANY", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                auto s = walk(a, ctx, [](const Value& r, const std::string&, const Value&,
                                         const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) return Value::boolean(true);
                  return std::nullopt;
                });
                return s.value_or(Value::boolean(false));
              }});

  define(Spec{"MAP", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                std::vector<Value> out;
                walk(a, ctx, [&out](const Value& r, const std::string&, const Value&,
                                    const Node&) -> std::optional<Value> {
                  out.push_back(r);
                  return std::nullopt;
                });
                return Value::list(std::move(out));
              }});

  // The one aggregate that preserves keys — a filtered list should still be
  // addressable the way the original was.
  define(Spec{"FILTER", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                Value out = Value::none();
                walk(a, ctx, [&out](const Value& r, const std::string& key, const Value& item,
                                    const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) out.set(key, item);
                  return std::nullopt;
                });
                return out;
              }});

  define(Spec{"SUM", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                Dec total = DEC_ZERO;
                walk(a, ctx, [&total](const Value& r, const std::string&, const Value&,
                                      const Node& body) -> std::optional<Value> {
                  total = dec_add(total, as_dec(r, body.pos));
                  return std::nullopt;
                });
                return make_num(total);
              }});

  // Strict, not an aggregate: its second argument is a separator, not a body.
  define(Spec{"JOIN", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string sep = a.text(1);
                std::string out;
                bool first = true;
                for (const auto& [k, item] : elements(a.val(0))) {
                  if (!first) out += sep;
                  out += item.as_text(a.pos_of(0));
                  first = false;
                }
                return make_text(out);
              }});
}

// --- text. Everything counts code points — never bytes, never UTF-16 units —
// so positions and lengths agree with the other hosts on astral characters.
// Positions are 1-based and 0 means "not found" (§7.5).

// ASCII only, deliberately. The hosts' own case mappings cannot be reconciled
// without shipping a case table, and guessing would break the invariant silently.
std::string ascii_case(const std::string& s, bool up) {
  CodePoints c = cps_of(s);
  for (char32_t& ch : c) {
    if (up && ch >= U'a' && ch <= U'z') ch -= 32;
    else if (!up && ch >= U'A' && ch <= U'Z') ch += 32;
  }
  return encode_utf8(c);
}

bool is_sel_space(char32_t c) {
  return c == 0x20 || c == 0x09 || c == 0x0d || c == 0x0a;
}

std::string trim_text(const std::string& s, bool left, bool right) {
  const CodePoints c = cps_of(s);
  long a = 0, b = static_cast<long>(c.size());
  if (left) while (a < b && is_sel_space(c[a])) a++;
  if (right) while (b > a && is_sel_space(c[b - 1])) b--;
  return slice_cp(c, a, b);
}

Value pad(Args& a, bool left) {
  const CodePoints c = cps_of(a.text(0));
  const long long width = a.non_neg_int(1);
  const CodePoints fill = cps_of(a.text(2));
  if (fill.empty()) fail("E_BAD_ARG", "pad fill must not be empty", a.pos_of(2));
  if (static_cast<long long>(c.size()) >= width) return make_text(encode_utf8(c));
  const std::size_t need = static_cast<std::size_t>(width) - c.size();
  CodePoints padding;
  while (padding.size() < need) padding.push_back(fill[padding.size() % fill.size()]);
  CodePoints out;
  if (left) {
    out = padding;
    out.insert(out.end(), c.begin(), c.end());
  } else {
    out = c;
    out.insert(out.end(), padding.begin(), padding.end());
  }
  return make_text(encode_utf8(out));
}


void register_text() {
  define(Spec{"LEN", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_int(static_cast<long long>(cps_of(a.text(0)).size()));
              }});

  define(Spec{"LEFT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints c = cps_of(a.text(0));
                return make_text(slice_cp(c, 0, static_cast<long>(a.non_neg_int(1))));
              }});

  define(Spec{"RIGHT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints c = cps_of(a.text(0));
                const long n = static_cast<long>(a.non_neg_int(1));
                return make_text(slice_cp(c, static_cast<long>(c.size()) - n,
                                          static_cast<long>(c.size())));
              }});

  define(Spec{"SUBSTR", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints c = cps_of(a.text(0));
                const long long start = a.integer(1);
                if (start < 1) {
                  fail("E_RANGE", "SUBSTR start is 1-based and must be at least 1", a.pos_of(1));
                }
                const long from = static_cast<long>(start - 1);
                const long size = static_cast<long>(c.size());
                if (a.count() == 2) return make_text(slice_cp(c, from, size));
                // Clamped rather than added: non_neg_int saturates at LLONG_MAX,
                // so `from + n` is signed overflow (UB) for a huge length, and
                // the other hosts simply return the rest of the string.
                const long long n = a.non_neg_int(2);
                const long to = (n >= static_cast<long long>(size - from)) ? size : from + static_cast<long>(n);
                return make_text(slice_cp(c, from, to));
              }});

  define(Spec{"FIND", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints needle = cps_of(a.text(0));
                const CodePoints hay = cps_of(a.text(1));
                long from = 0;
                if (a.count() == 3) {
                  const long long f = a.integer(2);
                  if (f < 1) {
                    fail("E_RANGE", "FIND start is 1-based and must be at least 1", a.pos_of(2));
                  }
                  from = static_cast<long>(f - 1);
                }
                if (needle.empty()) fail("E_BAD_ARG", "FIND needle must not be empty", a.pos_of(0));
                return make_int(index_of_cp(hay, needle, from) + 1);
              }});

  define(Spec{"REPLACE", 3, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints needle = cps_of(a.text(0));
                const CodePoints repl = cps_of(a.text(1));
                const CodePoints hay = cps_of(a.text(2));
                if (needle.empty()) {
                  fail("E_BAD_ARG", "REPLACE needle must not be empty", a.pos_of(0));
                }
                CodePoints out;
                long i = 0;
                for (;;) {
                  const long at = index_of_cp(hay, needle, i);
                  if (at < 0) break;
                  out.insert(out.end(), hay.begin() + i, hay.begin() + at);
                  out.insert(out.end(), repl.begin(), repl.end());
                  i = at + static_cast<long>(needle.size());
                }
                out.insert(out.end(), hay.begin() + i, hay.end());
                return make_text(encode_utf8(out));
              }});

  define(Spec{"SPLIT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints hay = cps_of(a.text(0));
                const CodePoints sep = cps_of(a.text(1));
                if (sep.empty()) {
                  fail("E_BAD_ARG", "SPLIT separator must not be empty", a.pos_of(1));
                }
                std::vector<Value> parts;
                long i = 0;
                for (;;) {
                  const long at = index_of_cp(hay, sep, i);
                  if (at < 0) break;
                  parts.push_back(make_text(slice_cp(hay, i, at)));
                  i = at + static_cast<long>(sep.size());
                }
                parts.push_back(make_text(slice_cp(hay, i, static_cast<long>(hay.size()))));
                return Value::list(std::move(parts));
              }});

  define(Spec{"TRIM", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(trim_text(a.text(0), true, true));
              }});
  define(Spec{"LTRIM", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(trim_text(a.text(0), true, false));
              }});
  define(Spec{"RTRIM", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(trim_text(a.text(0), false, true));
              }});

  define(Spec{"UPPER", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(ascii_case(a.text(0), true));
              }});
  define(Spec{"LOWER", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(ascii_case(a.text(0), false));
              }});

  define(Spec{"BACKWARDS", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                CodePoints c = cps_of(a.text(0));
                std::reverse(c.begin(), c.end());
                return make_text(encode_utf8(c));
              }});

  define(Spec{"REPEAT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string s = a.text(0);
                const long long n = a.non_neg_int(1);
                std::string out;
                for (long long i = 0; i < n; i++) out += s;
                return make_text(out);
              }});

  define(Spec{"PADL", 3, 3, false, false, nullptr,
              [](Args& a, Context&) -> Value { return pad(a, true); }});
  define(Spec{"PADR", 3, 3, false, false, nullptr,
              [](Args& a, Context&) -> Value { return pad(a, false); }});

  define(Spec{"CHAR", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const long long n = a.integer(0);
                if (n < 0 || n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) {
                  fail("E_RANGE", std::to_string(n) + " is not an encodable code point",
                       a.pos_of(0));
                }
                std::string s;
                encode_cp(s, static_cast<char32_t>(n));
                return make_text(s);
              }});

  define(Spec{"CODE", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const CodePoints c = cps_of(a.text(0));
                if (c.empty()) fail("E_RANGE", "CODE of empty text", a.pos_of(0));
                return make_int(static_cast<long long>(c[0]));
              }});
}

// --- numbers

void register_numbers() {
  define(Spec{"ABS", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_abs(a.dec(0)));
              }});
  define(Spec{"SIGN", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_int(dec_sign(a.dec(0)));
              }});
  define(Spec{"CEIL", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_ceil(a.dec(0)));
              }});
  define(Spec{"FLOOR", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_floor(a.dec(0)));
              }});
  define(Spec{"TRUNC", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_trunc(a.dec(0)));
              }});

  define(Spec{"ROUND", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                // Range-checked, not cast. A bare static_cast<int> wraps modulo
                // 2^32, which turned ROUND(1.5, 4294967296) into a confident `2`
                // — a plausible wrong answer, the worst failure this project
                // can produce — and a wrapped negative scale then built a
                // string of length SIZE_MAX and escaped as std::length_error.
                const long long n = a.non_neg_int(1);
                if (n > MAX_SCALE) {
                  fail("E_RANGE",
                       "ROUND scale " + std::to_string(n) + " exceeds the maximum of " +
                           std::to_string(MAX_SCALE),
                       a.pos_of(1));
                }
                return make_num(dec_round(a.dec(0), n));
              }});
  define(Spec{"POWER", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const long long n = a.non_neg_int(1);
                if (n > MAX_POWER) {
                  fail("E_RANGE",
                       "POWER exponent " + std::to_string(n) + " exceeds the maximum of " +
                           std::to_string(MAX_POWER),
                       a.pos_of(1));
                }
                return make_num(dec_power(a.dec(0), n));
              }});

  define(Spec{"MIN", 1, VARIADIC, false, false, nullptr, [](Args& a, Context&) -> Value {
                Dec best = a.dec(0);
                for (int i = 1; i < a.count(); i++) {
                  const Dec d = a.dec(i);
                  if (dec_cmp(d, best) < 0) best = d;
                }
                return make_num(best);
              }});
  define(Spec{"MAX", 1, VARIADIC, false, false, nullptr, [](Args& a, Context&) -> Value {
                Dec best = a.dec(0);
                for (int i = 1; i < a.count(); i++) {
                  const Dec d = a.dec(i);
                  if (dec_cmp(d, best) > 0) best = d;
                }
                return make_num(best);
              }});

  // The non-throwing probe. Every other numeric path raises E_NOT_NUM instead.
  define(Spec{"ISNUM", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(a.val(0).looks_numeric());
              }});
}

// --- binary

const char* B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

int b64_index(char c) {
  const char* p = std::strchr(B64_ALPHABET, c);
  return (p == nullptr || c == '\0') ? -1 : static_cast<int>(p - B64_ALPHABET);
}

int hex_value(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// CRC-32/ISO-HDLC: reflected, polynomial 0xEDB88320, init and final xor all ones.
const std::array<std::uint32_t, 256>& crc_table() {
  static const std::array<std::uint32_t, 256> t = [] {
    std::array<std::uint32_t, 256> out{};
    for (std::uint32_t i = 0; i < 256; i++) {
      std::uint32_t c = i;
      for (int k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320u ^ (c >> 1)) : (c >> 1);
      out[i] = c;
    }
    return out;
  }();
  return t;
}

void register_binary() {
  define(Spec{"BLEN", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_int(static_cast<long long>(a.bytes(0).size()));
              }});

  define(Spec{"TO_UTF8", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_bin(a.bytes(0));
              }});

  define(Spec{"FROM_UTF8", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& b = a.bytes(0);
                decode_utf8(b, a.pos_of(0));   // validates; E_UTF8 if not
                return make_text(b);
              }});

  define(Spec{"TO_HEX", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(to_hex(a.bytes(0)));
              }});

  define(Spec{"FROM_HEX", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string s = a.text(0);
                if (s.size() % 2 != 0) {
                  fail("E_BAD_ARG", "FROM_HEX needs an even number of digits", a.pos_of(0));
                }
                std::string out(s.size() / 2, '\0');
                for (std::size_t i = 0; i < out.size(); i++) {
                  const int hi = hex_value(s[i * 2]);
                  const int lo = hex_value(s[i * 2 + 1]);
                  if (hi < 0 || lo < 0) {
                    fail("E_BAD_ARG", "FROM_HEX: \"" + s.substr(i * 2, 2) + "\" is not hex",
                         a.pos_of(0));
                  }
                  out[i] = static_cast<char>((hi << 4) | lo);
                }
                return make_bin(std::move(out));
              }});

  define(Spec{"ENCODE_BASE64", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& b = a.bytes(0);
                std::string out;
                for (std::size_t i = 0; i < b.size(); i += 3) {
                  const unsigned n =
                      (static_cast<unsigned char>(b[i]) << 16) |
                      ((i + 1 < b.size() ? static_cast<unsigned char>(b[i + 1]) : 0) << 8) |
                      (i + 2 < b.size() ? static_cast<unsigned char>(b[i + 2]) : 0);
                  out += B64_ALPHABET[(n >> 18) & 63];
                  out += B64_ALPHABET[(n >> 12) & 63];
                  out += i + 1 < b.size() ? B64_ALPHABET[(n >> 6) & 63] : '=';
                  out += i + 2 < b.size() ? B64_ALPHABET[n & 63] : '=';
                }
                return make_text(out);
              }});

  // Strict: padding is required and any character outside the alphabet fails.
  define(Spec{"DECODE_BASE64", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string s = a.text(0);
                const Pos pos = a.pos_of(0);
                if (s.size() % 4 != 0) {
                  fail("E_BAD_ARG", "DECODE_BASE64 needs a length that is a multiple of 4", pos);
                }
                std::string out;
                for (std::size_t i = 0; i < s.size(); i += 4) {
                  int quad[4] = {0, 0, 0, 0};
                  int padding = 0;
                  for (int k = 0; k < 4; k++) {
                    const char ch = s[i + k];
                    if (ch == '=') {
                      if (i + 4 < s.size() || k < 2) {
                        fail("E_BAD_ARG", "misplaced base64 padding", pos);
                      }
                      padding++;
                      continue;
                    }
                    if (padding > 0) fail("E_BAD_ARG", "misplaced base64 padding", pos);
                    const int v = b64_index(ch);
                    if (v < 0) {
                      fail("E_BAD_ARG", std::string("invalid base64 character \"") + ch + "\"", pos);
                    }
                    quad[k] = v;
                  }
                  const unsigned n = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3];
                  out += static_cast<char>((n >> 16) & 255);
                  if (padding < 2) out += static_cast<char>((n >> 8) & 255);
                  if (padding < 1) out += static_cast<char>(n & 255);
                }
                return make_bin(std::move(out));
              }});

  define(Spec{"CRC32", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const auto& t = crc_table();
                const std::string& b = a.bytes(0);
                std::uint32_t crc = 0xffffffffu;
                for (char ch : b) {
                  crc = t[(crc ^ static_cast<unsigned char>(ch)) & 255] ^ (crc >> 8);
                }
                crc ^= 0xffffffffu;
                char buf[16];
                std::snprintf(buf, sizeof buf, "%08x", crc);
                return make_text(buf);
              }});

  define(Spec{"BTL", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                std::vector<Value> out;
                for (char ch : a.bytes(0)) {
                  out.push_back(make_int(static_cast<unsigned char>(ch)));
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"LTB", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& v = a.val(0);
                std::vector<Value> items;
                if (v.size() > 0) {
                  for (const auto& e : v.entries()) items.push_back(e.second);
                } else {
                  items.push_back(v);
                }
                std::string out(items.size(), '\0');
                for (std::size_t i = 0; i < items.size(); i++) {
                  const Dec d = as_dec(items[i], a.pos_of(0));
                  const long long n = dec_to_int(d);
                  if (d.scale != 0 || n < 0 || n > 255) {
                    fail("E_RANGE", "LTB element " + std::to_string(i + 1) + " is not a byte value",
                         a.pos_of(0));
                  }
                  out[i] = static_cast<char>(n);
                }
                return make_bin(std::move(out));
              }});
}

// --- regex. See spec/SPEC.md §7.8.
//
// A pattern is validated against a whitelist and rewritten before it reaches the
// engine, so anything the engines would disagree about fails loudly here instead
// of producing different answers on different hosts. That pass is ported
// verbatim from the other implementations and is the part that must not drift.
//
// The engine underneath is SRELL (third_party/srell/), an ECMAScript-conformant
// library — the same language as the JS host's RegExp, which is why the two
// agree by construction. It is driven over u32string, so match offsets are
// already code point offsets, which is what SEL reports.

// \d, \w and \s are rewritten into explicit ASCII classes rather than passed
// through, because PHP's `u` modifier turns on PCRE2's UCP and ECMAScript's does
// not. Expanding them here makes the guarantee structural instead of dependent
// on a library flag no host fully controls.
const std::map<char32_t, std::string>& expand_outside() {
  static const std::map<char32_t, std::string> m = {
      {U'd', "[0-9]"}, {U'D', "[^0-9]"},
      {U'w', "[0-9A-Za-z_]"}, {U'W', "[^0-9A-Za-z_]"},
      {U's', "[ \\t\\n\\r\\f\\x0b]"}, {U'S', "[^ \\t\\n\\r\\f\\x0b]"},
  };
  return m;
}

const std::map<char32_t, std::string>& expand_inside() {
  static const std::map<char32_t, std::string> m = {
      {U'd', "0-9"}, {U'w', "0-9A-Za-z_"}, {U's', " \\t\\n\\r\\f\\x0b"},
  };
  return m;
}

// \v is excluded: in PCRE it means "any vertical whitespace", in ECMAScript it
// means U+000B. Same spelling, different language.
bool is_control_escape(char32_t e) {
  return e == U'n' || e == U'r' || e == U't' || e == U'f';
}

// Exactly ECMAScript's u-mode identity escapes; PCRE accepts all of these too.
bool is_syntax_char(char32_t e) {
  static const std::u32string chars = U"^$\\.*+?()[]{}|/";
  return chars.find(e) != std::u32string::npos;
}

[[noreturn]] void bad_regex(const std::string& message, const std::string& pattern, std::size_t at,
                            Pos pos) {
  fail("E_REGEX_SYNTAX",
       message + " (at offset " + std::to_string(at) + " of /" + pattern + "/)", pos);
}

[[noreturn]] void reject_escape(char32_t e, const std::string& pattern, std::size_t at, Pos pos) {
  if (e == U'b' || e == U'B') {
    bad_regex(std::string("\\") + static_cast<char>(e) +
                  " is not portable — word boundaries depend on the engine's idea of a word "
                  "character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])",
              pattern, at, pos);
  }
  if (e == U'v') {
    bad_regex("\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as "
              "U+000B", pattern, at, pos);
  }
  if (e >= U'0' && e <= U'9') bad_regex("backreferences are not portable", pattern, at, pos);
  if (e == U'p' || e == U'P') bad_regex("\\p{...} is not portable", pattern, at, pos);
  if (e == U'A' || e == U'z' || e == U'Z' || e == U'G' || e == U'K') {
    bad_regex(std::string("\\") + static_cast<char>(e) + " is not portable — use ^ and $", pattern,
              at, pos);
  }
  std::string s;
  encode_cp(s, e);
  bad_regex("unsupported escape \\" + s, pattern, at, pos);
}

// A quantifier may be followed by `?` (lazy). `+` would make it possessive,
// which PCRE supports and ECMAScript does not.
std::size_t after_quantifier(const CodePoints& p, std::size_t i, const std::string& pattern,
                             Pos pos) {
  if (i < p.size() && p[i] == U'+') {
    bad_regex("possessive quantifiers are not portable", pattern, i, pos);
  }
  if (i < p.size() && p[i] == U'?') return i + 1;
  return i;
}

// Reads a {n}, {n,} or {n,m} quantifier, checking both bounds. The engines
// disagree about the extremes — a huge repeat count is a syntax error to PCRE2
// and SRELL but merely never matches in JS and cl-ppcre — so the subset checker
// settles it (spec/SPEC.md §6.4) rather than delegating.
long long read_bound(const CodePoints& p, std::size_t& i) {
  long long v = 0;
  while (i < p.size() && p[i] >= U'0' && p[i] <= U'9') {
    if (v <= MAX_QUANTIFIER) v = v * 10 + static_cast<long long>(p[i] - U'0');
    i++;
  }
  return v;
}

std::size_t validate_braces(const CodePoints& p, std::size_t start, const std::string& pattern,
                            Pos pos) {
  std::size_t i = start + 1;
  const std::size_t lo_start = i;
  const long long lo = read_bound(p, i);
  if (i == lo_start) {
    bad_regex("{ must begin a quantifier such as {2,4} — escape it as \\{", pattern, start, pos);
  }
  bool has_hi = false;
  long long hi = 0;
  if (i < p.size() && p[i] == U',') {
    i++;
    const std::size_t hi_start = i;
    hi = read_bound(p, i);
    has_hi = i > hi_start;
  }
  if (i >= p.size() || p[i] != U'}') bad_regex("malformed quantifier", pattern, start, pos);
  if (lo > MAX_QUANTIFIER || (has_hi && hi > MAX_QUANTIFIER)) {
    bad_regex("quantifier bound exceeds the maximum of " + std::to_string(MAX_QUANTIFIER),
              pattern, start, pos);
  }
  if (has_hi && hi < lo) {
    bad_regex("quantifier {" + std::to_string(lo) + "," + std::to_string(hi) +
                  "} is empty — the upper bound is below the lower one",
              pattern, start, pos);
  }
  return i + 1;
}

// Returns the rewritten text; `next` receives the index just past the closing ']'.
std::string validate_class(const CodePoints& p, std::size_t start, const std::string& pattern,
                           Pos pos, std::size_t& next) {
  std::size_t i = start + 1;
  std::string out = "[";
  if (i < p.size() && p[i] == U'^') { out += "^"; i++; }
  if (i + 1 < p.size() && p[i] == U'[' && p[i + 1] == U':') {
    bad_regex("POSIX classes such as [[:alpha:]] are not portable", pattern, i, pos);
  }
  // `]` always closes the class. PCRE treats a leading `]` as a literal while
  // ECMAScript reads `[]` as an empty class, so neither spelling is portable —
  // write `\]` instead.
  int count = 0;
  while (i < p.size()) {
    const char32_t c = p[i];
    if (c == U']') {
      if (count == 0) {
        bad_regex("empty character class — write \\] for a literal bracket", pattern, start, pos);
      }
      next = i + 1;
      return out + "]";
    }
    count++;
    if (c == U'\\') {
      if (i + 1 >= p.size()) {
        bad_regex("trailing backslash in character class", pattern, i, pos);
      }
      const char32_t e = p[i + 1];
      auto it = expand_inside().find(e);
      if (it != expand_inside().end()) { out += it->second; i += 2; continue; }
      if (e == U'D' || e == U'W' || e == U'S') {
        bad_regex(std::string("\\") + static_cast<char>(e) +
                      " inside a character class cannot be expressed portably — negate the whole "
                      "class instead",
                  pattern, i, pos);
      }
      if (is_control_escape(e) || is_syntax_char(e) || e == U'-') {
        encode_cp(out, c);
        encode_cp(out, e);
        i += 2;
        continue;
      }
      reject_escape(e, pattern, i, pos);
    }
    encode_cp(out, c);
    i++;
  }
  bad_regex("unterminated character class", pattern, start, pos);
}

// Validates and rewrites in one pass, returning source that means the same thing
// to every engine. All four hosts run this, so all four compile the same pattern.
std::string validate_pattern(const std::string& pattern, Pos pos) {
  const CodePoints p = decode_utf8(pattern, pos);
  const std::size_t n = p.size();
  std::string out;
  std::size_t i = 0;

  while (i < n) {
    const char32_t c = p[i];

    if (c == U'\\') {
      if (i + 1 >= n) bad_regex("trailing backslash", pattern, i, pos);
      const char32_t e = p[i + 1];
      auto it = expand_outside().find(e);
      if (it != expand_outside().end()) { out += it->second; i += 2; continue; }
      if (is_control_escape(e) || is_syntax_char(e)) {
        encode_cp(out, c);
        encode_cp(out, e);
        i += 2;
        continue;
      }
      reject_escape(e, pattern, i, pos);
    }

    if (c == U'[') {
      std::size_t next = 0;
      out += validate_class(p, i, pattern, pos, next);
      i = next;
      continue;
    }

    if (c == U'(') {
      if (i + 1 < n && p[i + 1] == U'?') {
        if (i + 2 < n && p[i + 2] == U':') { out += "(?:"; i += 3; continue; }
        const char32_t k = i + 2 < n ? p[i + 2] : U'\0';
        const char* kind = (k == U'=' || k == U'!') ? "lookahead"
                           : k == U'<'              ? "lookbehind and named groups"
                           : k == U'>'              ? "atomic groups"
                                                    : "this group type";
        bad_regex(std::string(kind) + " is not portable — only (?: ) is", pattern, i, pos);
      }
      out += "(";
      i++;
      continue;
    }

    if (c == U'{') {
      const std::size_t end = after_quantifier(p, validate_braces(p, i, pattern, pos), pattern, pos);
      out += encode_utf8(std::span<const char32_t>(p).subspan(i, end - i));
      i = end;
      continue;
    }
    if (c == U'*' || c == U'+' || c == U'?') {
      const std::size_t end = after_quantifier(p, i + 1, pattern, pos);
      out += encode_utf8(std::span<const char32_t>(p).subspan(i, end - i));
      i = end;
      continue;
    }
    if (c == U'}') bad_regex("unmatched } — escape it as \\}", pattern, i, pos);
    if (c == U']') bad_regex("unmatched ] — escape it as \\]", pattern, i, pos);

    encode_cp(out, c);
    i++;
  }
  return out;
}

using Regex = srell::u32regex;

// Compiled patterns are cached: a rule inside an aggregate compiles its pattern
// once per element otherwise.
Regex& compile_regex(const std::string& pattern, const std::string& flags, Pos flag_pos,
                     Pos pat_pos) {
  bool ignore_case = false;
  for (char32_t ch : decode_utf8(flags, flag_pos)) {
    const char32_t f = (ch >= U'A' && ch <= U'Z') ? ch + 32 : ch;
    if (f == U'i') { ignore_case = true; continue; }
    std::string s;
    encode_cp(s, ch);
    if (f == U'm' || f == U's') {
      fail("E_BAD_ARG",
           "flag \"" + s +
               "\" is not offered — SEL always matches . against any character and anchors ^ $ to "
               "the whole subject",
           flag_pos);
    }
    fail("E_BAD_ARG", "unknown regex flag \"" + s + "\"", flag_pos);
  }

  if (ignore_case) {
    for (char32_t cp : decode_utf8(pattern, pat_pos)) {
      if (cp > 0x7f) {
        fail("E_BAD_ARG",
             "the i flag needs an ASCII-only pattern — case folding above ASCII differs between "
             "PCRE and ECMAScript",
             flag_pos);
      }
    }
  }

  // Guarded because sel.hpp presents a drop-in library whose Program is
  // immutable after compile(), so hosts will naturally evaluate rules on a
  // thread pool. Concurrent std::map insertion is memory corruption, not a
  // stale-value race. ThreadSanitizer caught this on four threads compiling
  // distinct patterns.
  static std::mutex cache_mutex;
  static std::map<std::string, Regex> cache;
  std::lock_guard<std::mutex> lock(cache_mutex);
  const std::string key = (ignore_case ? "i " : " ") + pattern;
  auto it = cache.find(key);
  if (it != cache.end()) return it->second;

  const std::string source = validate_pattern(pattern, pat_pos);
  auto opts = srell::regex_constants::ECMAScript | srell::regex_constants::dotall;
  if (ignore_case) opts |= srell::regex_constants::icase;
  try {
    // The source is UTF-8; SRELL's u32regex wants code points.
    const CodePoints cps = decode_utf8(source, pat_pos);
    Regex re(std::u32string(cps.begin(), cps.end()), opts);
    return cache.emplace(key, std::move(re)).first->second;
  } catch (const srell::regex_error& e) {
    fail("E_REGEX_SYNTAX", std::string(e.what()) + " in /" + pattern + "/", pat_pos);
  }
}

struct RegexCall {
  Regex* re;
  std::u32string subject;
};

RegexCall regex_args(Args& a, int pat_index, int subj_index, int flag_index) {
  const std::string pattern = a.text(pat_index);
  const CodePoints subject = cps_of(a.text(subj_index));
  const std::string flags = a.count() > flag_index ? a.text(flag_index) : "";
  const Pos flag_pos = a.count() > flag_index ? a.pos_of(flag_index) : a.pos();
  return RegexCall{&compile_regex(pattern, flags, flag_pos, a.pos_of(pat_index)),
                   std::u32string(subject.begin(), subject.end())};
}

// SEL replacement syntax is $0–$9 and $$ for a literal $; every other character
// is literal. Spliced by hand rather than handed to the engine, whose own
// replacement syntax differs between hosts.
std::string expand_replacement(const std::string& repl, const srell::u32smatch& m, Pos pos) {
  std::string out;
  for (std::size_t i = 0; i < repl.size(); i++) {
    if (repl[i] != '$') {
      out += repl[i];
      continue;
    }
    const char next = i + 1 < repl.size() ? repl[i + 1] : '\0';
    if (next == '$') { out += '$'; i++; continue; }
    if (next >= '0' && next <= '9') {
      const std::size_t g = static_cast<std::size_t>(next - '0');
      if (g >= m.size()) {
        fail("E_BAD_ARG",
             "replacement refers to $" + std::to_string(g) + " but the pattern has " +
                 std::to_string(m.size() - 1) + " groups",
             pos);
      }
      // A capture that did not participate yields "".
      if (m[g].matched) {
        const std::u32string s = m[g].str();
        out += encode_utf8(std::span<const char32_t>(s.data(), s.size()));
      }
      i++;
      continue;
    }
    out += '$';
  }
  return out;
}

void register_regex() {
  define(Spec{"RMATCH", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                return Value::boolean(srell::regex_search(c.subject, *c.re));
              }});

  define(Spec{"RFIND", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                srell::u32smatch m;
                if (!srell::regex_search(c.subject, m, *c.re)) return make_int(0);
                // Offsets are already code points, because the subject is u32.
                return make_int(static_cast<long long>(m.position(0)) + 1);
              }});

  define(Spec{"RGROUPS", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                srell::u32smatch m;
                if (!srell::regex_search(c.subject, m, *c.re)) return Value::none();
                std::vector<Value> out;
                for (std::size_t i = 0; i < m.size(); i++) {
                  if (!m[i].matched) {
                    out.push_back(make_text(""));
                    continue;
                  }
                  const std::u32string s = m[i].str();
                  out.push_back(make_text(encode_utf8(std::span<const char32_t>(s.data(), s.size()))));
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"RREPLACE", 3, 4, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string pattern = a.text(0);
                const std::string repl = a.text(1);
                const CodePoints subj_cps = cps_of(a.text(2));
                const std::u32string subject(subj_cps.begin(), subj_cps.end());
                const std::string flags = a.count() > 3 ? a.text(3) : "";
                const Pos flag_pos = a.count() > 3 ? a.pos_of(3) : a.pos();
                Regex& re = compile_regex(pattern, flags, flag_pos, a.pos_of(0));

                // regex_iterator, not a loop over regex_search on shrinking
                // substrings: re-searching from an offset would let `^` match
                // there, and SEL anchors ^ and $ to the ends of the whole
                // subject. The iterator also handles zero-width matches the way
                // a global ECMAScript match does.
                std::u32string out;
                std::size_t last = 0;
                srell::u32sregex_iterator it(subject.cbegin(), subject.cend(), re);
                const srell::u32sregex_iterator end;
                for (; it != end; ++it) {
                  const srell::u32smatch& m = *it;
                  const std::size_t at = static_cast<std::size_t>(m.position(0));
                  const std::size_t len = static_cast<std::size_t>(m.length(0));
                  out += subject.substr(last, at - last);
                  const std::string piece = expand_replacement(repl, m, a.pos_of(1));
                  const CodePoints pc = decode_utf8(piece, a.pos_of(1));
                  out.append(pc.begin(), pc.end());
                  last = at + len;
                }
                out += subject.substr(std::min(last, subject.size()));
                return make_text(encode_utf8(std::span<const char32_t>(out.data(), out.size())));
              }});
}

void register_builtins() {
  register_control();
  register_structure();
  register_aggregates();
  register_text();
  register_numbers();
  register_binary();
  register_regex();
}

// --- dependencies -----------------------------------------------------------

void collect(const Node* node, std::set<std::string>& bound, std::set<std::string>& reads,
             std::set<std::string>& assigned) {
  if (!node) return;
  switch (node->t) {
    case NT::Var:
      if (!bound.count(node->s)) reads.insert(node->s);
      return;

    case NT::Assign: {
      const Node* target = node->l.get();
      const Node* t = target;
      while (t->t == NT::Index) {
        collect(t->r.get(), bound, reads, assigned);
        t = t->l.get();
      }
      // `A = x` defines A; `A[k] = x` and `A += x` also read it.
      if (target->t != NT::Var || node->s != "=") {
        if (!bound.count(t->s)) reads.insert(t->s);
      }
      assigned.insert(t->s);
      collect(node->r.get(), bound, reads, assigned);
      return;
    }

    case NT::Call: {
      // An aggregate's three-argument form binds its second argument as a name
      // for the duration of the third.
      if (node->spec && node->spec->binds && node->items.size() == 3 &&
          node->items[1]->t == NT::Var) {
        collect(node->items[0].get(), bound, reads, assigned);
        std::set<std::string> inner = bound;
        inner.insert(node->items[1]->s);
        inner.insert("_K");
        collect(node->items[2].get(), inner, reads, assigned);
        return;
      }
      if (node->spec && node->spec->binds && node->items.size() == 2) {
        collect(node->items[0].get(), bound, reads, assigned);
        std::set<std::string> inner = bound;
        inner.insert("_");
        inner.insert("_K");
        collect(node->items[1].get(), inner, reads, assigned);
        return;
      }
      for (const auto& arg : node->items) collect(arg.get(), bound, reads, assigned);
      return;
    }

    case NT::Seq:
    case NT::List:
      for (const auto& item : node->items) collect(item.get(), bound, reads, assigned);
      return;

    case NT::Index:
    case NT::Bin:
      collect(node->l.get(), bound, reads, assigned);
      collect(node->r.get(), bound, reads, assigned);
      return;

    case NT::Un:
      collect(node->l.get(), bound, reads, assigned);
      return;

    default:
      return;
  }
}

}  // namespace

// ============================================================================
// --- host API. See spec/SPEC.md §8.
// ============================================================================

Program::Program(std::string source, std::shared_ptr<const Node> ast)
    : source_(std::move(source)), ast_(std::move(ast)) {}

Value Program::run(Value& context) const {
  Context ctx(context);
  return eval_node(*ast_, ctx);
}

Value Program::run() const {
  Value ctx = Value::none();
  return run(ctx);
}

std::vector<std::string> Program::dependencies() const {
  std::set<std::string> bound, reads, assigned;
  collect(ast_.get(), bound, reads, assigned);
  std::vector<std::string> out;
  for (const std::string& r : reads) {
    if (!assigned.count(r)) out.push_back(r);
  }
  std::sort(out.begin(), out.end());
  return out;
}

Program compile(const std::string& source) { return Program(source, parse(source)); }

Value evaluate(const std::string& source, Value& context) { return compile(source).run(context); }

Value evaluate(const std::string& source) { return compile(source).run(); }

std::vector<std::string> function_names() {
  ensure_registered();
  std::vector<std::string> out;
  for (const auto& [name, spec] : table()) out.push_back(name);
  std::sort(out.begin(), out.end());
  return out;
}

}  // namespace sel
