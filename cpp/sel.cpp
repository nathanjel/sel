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
#include "sel_ast.hpp"
#include "sel_builtin_manifest.hpp"
#include "sel_math_ops.hpp"

// SEL rejects \p{...} at compile time as non-portable, so SRELL's Unicode
// property tables are unreachable from this language. Leaving them out cuts the
// compile substantially and removes the only part of the library SEL can never
// use. See third_party/srell/PINNED.md.
#define SRELL_NO_UNICODE_PROPERTY
#if defined(SEL_SYSTEM_SRELL)
// Opt-in, for package managers that carry SRELL themselves. It must be the same
// release as third_party/srell/PINNED.md records: the regex engine is what makes
// this host agree with the JavaScript one, so a different one is a different
// language. Run tools/check.sh before trusting a build with this defined.
#include <srell.hpp>
#else
#include "third_party/srell/srell.hpp"
#endif

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <span>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace sel {
namespace {

// ============================================================================
// --- errors
// ============================================================================

[[noreturn]] void fail(const char* code, const std::string& message, Pos pos = {}) {
  throw SelError(code, message, pos);
}

// spec/SPEC.md §6.4's three caps live in sel.hpp now: the SEL->SQL translator is
// a fourth caller and is a separate translation unit, and one number cannot be
// in an anonymous namespace and shared at the same time. Nothing is declared
// here, deliberately -- a second definition at this scope would be ambiguous
// with sel::MAX_DEPTH rather than shadowing it, which is the compiler making the
// same point.

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

constexpr long long DIV_SCALE = sel_limits::DIV_SCALE;   // spec/limits.json

// spec/SPEC.md §6.4. These bound the *value*; MAX_SCALE and MAX_POWER below
// bound *arguments*, and an argument cap is not a value cap — POWER's base is
// unbounded, so nesting one POWER inside another multiplies the exponents and
// steps straight over MAX_POWER. Two independent numbers rather than one shared
// budget, because ROUND(99.5, 1000000) is 1 000 002 digits and legal under the
// scale cap: a shared budget would have shrunk what the spec already sanctions.
constexpr long long MAX_INT_DIGITS = sel_limits::MAX_INT_DIGITS;
constexpr long long MAX_FRAC_DIGITS = sel_limits::MAX_FRAC_DIGITS;

// Upper bounds on the arguments that name a size, from spec/SPEC.md §6.4's
// first table. These are not the same as the value caps above: they bound what
// a call may ask for, not how big the answer may be, and an argument cap alone
// left POWER's base free to step over MAX_POWER by nesting.
constexpr long long MAX_SCALE = 1000000;
constexpr long long MAX_POWER = 100000;
constexpr long long MAX_QUANTIFIER = 65535;   // PCRE2's own hard limit

}  // namespace (anonymous)

enum class MathOp : uint8_t {
  LoadVar,
  LoadConst,
  LoadLeaf,
  Add,
  Sub,
  Mul,
  Div,
  Mod,
  Neg,
  Abs,
  Sign,
  Ceil,
  Floor,
  Trunc,
  Round,
  Power,
  Min,
  Max
};

struct MathStep {
  MathOp op = MathOp::LoadVar;
  uint16_t dst = 0;
  uint16_t src1 = 0;
  uint16_t src2 = 0;
  Pos pos;
  Pos aux_pos;
  std::string name;
  Dec const_val;
  NodePtr leaf_node;
};

struct MathPlan {
  std::vector<MathStep> steps;
  uint16_t output_slot = 0;
  uint16_t scratchpad_size = 0;
};

namespace {

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

// Base-10^9 constants
constexpr uint32_t BASE_10E9 = 1000000000;
constexpr int LIMB_DIGITS = 9;

constexpr __int128_t POW10_128[39] = {
    1ull,
    10ull,
    100ull,
    1000ull,
    10000ull,
    100000ull,
    1000000ull,
    10000000ull,
    100000000ull,
    1000000000ull,
    10000000000ull,
    100000000000ull,
    1000000000000ull,
    10000000000000ull,
    100000000000000ull,
    1000000000000000ull,
    10000000000000000ull,
    100000000000000000ull,
    1000000000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 10,
    static_cast<__int128_t>(1000000000000000000ull) * 100,
    static_cast<__int128_t>(1000000000000000000ull) * 1000,
    static_cast<__int128_t>(1000000000000000000ull) * 10000,
    static_cast<__int128_t>(1000000000000000000ull) * 100000,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000,
    static_cast<__int128_t>(1000000000000000000ull) * 10000000,
    static_cast<__int128_t>(1000000000000000000ull) * 100000000,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000,
    static_cast<__int128_t>(1000000000000000000ull) * 10000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 100000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 10000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 100000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 10000000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 100000000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000000000000ull,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000000000000ull * 10,
    static_cast<__int128_t>(1000000000000000000ull) * 1000000000000000000ull * 100
};

std::vector<uint32_t> string_to_limbs(const std::string& s) {
  std::vector<uint32_t> limbs;
  limbs.reserve((s.size() + LIMB_DIGITS - 1) / LIMB_DIGITS);
  long long i = static_cast<long long>(s.size());
  while (i > 0) {
    long long start = std::max(0LL, i - LIMB_DIGITS);
    uint32_t val = 0;
    for (long long j = start; j < i; ++j) {
      val = val * 10 + (s[j] - '0');
    }
    limbs.push_back(val);
    i = start;
  }
  return limbs;
}

std::string limbs_to_string(const std::vector<uint32_t>& limbs) {
  if (limbs.empty()) return "0";
  size_t idx = limbs.size() - 1;
  while (idx > 0 && limbs[idx] == 0) idx--;
  std::string s = std::to_string(limbs[idx]);
  while (idx > 0) {
    idx--;
    std::string part = std::to_string(limbs[idx]);
    if (part.size() < LIMB_DIGITS) {
      s.append(LIMB_DIGITS - part.size(), '0');
    }
    s += part;
  }
  return s;
}

std::vector<uint32_t> add_limbs(const std::vector<uint32_t>& la, const std::vector<uint32_t>& lb) {
  size_t n = std::max(la.size(), lb.size());
  std::vector<uint32_t> res;
  res.reserve(n + 1);
  uint32_t carry = 0;
  for (size_t i = 0; i < n || carry; ++i) {
    uint64_t sum = carry + (i < la.size() ? la[i] : 0) + (i < lb.size() ? lb[i] : 0);
    res.push_back(static_cast<uint32_t>(sum % BASE_10E9));
    carry = static_cast<uint32_t>(sum / BASE_10E9);
  }
  return res;
}

std::vector<uint32_t> sub_limbs(const std::vector<uint32_t>& la, const std::vector<uint32_t>& lb) {
  std::vector<uint32_t> res;
  res.reserve(la.size());
  int64_t borrow = 0;
  for (size_t i = 0; i < la.size(); ++i) {
    int64_t diff = static_cast<int64_t>(la[i]) - (i < lb.size() ? lb[i] : 0) - borrow;
    if (diff < 0) {
      diff += BASE_10E9;
      borrow = 1;
    } else {
      borrow = 0;
    }
    res.push_back(static_cast<uint32_t>(diff));
  }
  while (res.size() > 1 && res.back() == 0) res.pop_back();
  return res;
}

int cmp_limbs(const std::vector<uint32_t>& la, const std::vector<uint32_t>& lb) {
  if (la.size() != lb.size()) return la.size() < lb.size() ? -1 : 1;
  for (size_t i = la.size(); i > 0; --i) {
    if (la[i - 1] != lb[i - 1]) return la[i - 1] < lb[i - 1] ? -1 : 1;
  }
  return 0;
}

std::vector<uint32_t> mul_limbs(const std::vector<uint32_t>& la, const std::vector<uint32_t>& lb) {
  if ((la.size() == 1 && la[0] == 0) || (lb.size() == 1 && lb[0] == 0)) return {0};
  if (la.size() == 1 && la[0] == 1) return lb;
  if (lb.size() == 1 && lb[0] == 1) return la;
  const size_t n = la.size(), m = lb.size();
  const size_t total = n + m;
  unsigned __int128 stack_acc[256];
  std::vector<unsigned __int128> heap_acc;
  unsigned __int128* acc = nullptr;
  if (total <= 256) {
    std::fill_n(stack_acc, total, 0);
    acc = stack_acc;
  } else {
    heap_acc.assign(total, 0);
    acc = heap_acc.data();
  }
  for (size_t i = 0; i < n; ++i) {
    const uint64_t av = la[i];
    if (av == 0) continue;
    for (size_t j = 0; j < m; ++j) {
      acc[i + j] += static_cast<unsigned __int128>(av) * lb[j];
    }
  }
  std::vector<uint32_t> res(total);
  unsigned __int128 carry = 0;
  for (size_t i = 0; i < total; ++i) {
    unsigned __int128 cur = acc[i] + carry;
    res[i] = static_cast<uint32_t>(cur % BASE_10E9);
    carry = cur / BASE_10E9;
  }
  size_t len = total;
  while (len > 1 && res[len - 1] == 0) len--;
  res.resize(len);
  return res;
}

void scale_up_limbs(std::vector<uint32_t>& limbs, long long diff) {
  if (diff <= 0 || (limbs.size() == 1 && limbs[0] == 0)) return;
  long long words = diff / 9;
  long long rem = diff % 9;
  if (rem > 0) {
    uint64_t factor = static_cast<uint64_t>(POW10_128[rem]);
    uint64_t carry = 0;
    for (size_t i = 0; i < limbs.size(); ++i) {
      uint64_t cur = static_cast<uint64_t>(limbs[i]) * factor + carry;
      limbs[i] = static_cast<uint32_t>(cur % BASE_10E9);
      carry = cur / BASE_10E9;
    }
    if (carry > 0) limbs.push_back(static_cast<uint32_t>(carry));
  }
  if (words > 0) {
    limbs.insert(limbs.begin(), static_cast<size_t>(words), 0);
  }
}

std::string dec_digits_from_magnitude(unsigned __int128 magnitude) {
  if (magnitude == 0) return "0";
  char buf[42];
  int pos = 42;
  while (magnitude != 0) {
    buf[--pos] = static_cast<char>('0' + (magnitude % 10));
    magnitude /= 10;
  }
  return std::string(buf + pos, 42 - pos);
}

std::string add_abs(const std::string& a, const std::string& b) {
  if (a == "0") return b;
  if (b == "0") return a;
  if (a.size() <= 18 && b.size() <= 18) {
    unsigned __int128 va = 0, vb = 0;
    for (char c : a) va = va * 10 + (c - '0');
    for (char c : b) vb = vb * 10 + (c - '0');
    return dec_digits_from_magnitude(va + vb);
  }
  return limbs_to_string(add_limbs(string_to_limbs(a), string_to_limbs(b)));
}

// Requires a >= b.
std::string sub_abs(const std::string& a, const std::string& b) {
  if (a == b) return "0";
  if (b == "0") return a;
  if (a.size() <= 18 && b.size() <= 18) {
    unsigned __int128 va = 0, vb = 0;
    for (char c : a) va = va * 10 + (c - '0');
    for (char c : b) vb = vb * 10 + (c - '0');
    return dec_digits_from_magnitude(va - vb);
  }
  return limbs_to_string(sub_limbs(string_to_limbs(a), string_to_limbs(b)));
}

// Schoolbook long division with power-of-10 and single-digit fast paths.
bool divmod_abs(const std::string& a, const std::string& b, std::string& q, std::string& r) {
  if (b == "0") return false;
  if (cmp_abs(a, b) < 0) { q = "0"; r = a; return true; }
  // Check power of 10
  if (b[0] == '1') {
    bool is_pow10 = true;
    for (size_t i = 1; i < b.size(); ++i) {
      if (b[i] != '0') { is_pow10 = false; break; }
    }
    if (is_pow10) {
      size_t k = b.size() - 1;
      if (a.size() <= k) {
        q = "0";
        r = strip(a);
      } else {
        q = a.substr(0, a.size() - k);
        r = strip(a.substr(a.size() - k));
      }
      return true;
    }
  }
  // Check single-limb divisor
  if (b.size() <= 9) {
    uint32_t divisor = std::stoul(b);
    std::string quo;
    quo.reserve(a.size());
    uint64_t rem = 0;
    for (char c : a) {
      rem = rem * 10 + (c - '0');
      quo.push_back(static_cast<char>('0' + (rem / divisor)));
      rem %= divisor;
    }
    q = strip(quo);
    r = std::to_string(rem);
    return true;
  }
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

const std::vector<uint32_t>& dec_get_limbs(const Dec& d) {
  if (!d.limbs.empty()) return d.limbs;
  if (d.small) {
    std::vector<uint32_t>& l = const_cast<Dec&>(d).limbs;
    unsigned __int128 mag = d.mantissa < 0 ? static_cast<unsigned __int128>(-(d.mantissa))
                                           : static_cast<unsigned __int128>(d.mantissa);
    if (mag == 0) {
      l.push_back(0);
    } else {
      while (mag > 0) {
        l.push_back(static_cast<uint32_t>(mag % BASE_10E9));
        mag /= BASE_10E9;
      }
    }
    return d.limbs;
  }
  if (!d.digits.empty()) {
    const_cast<Dec&>(d).limbs = string_to_limbs(d.digits);
    return d.limbs;
  }
  const_cast<Dec&>(d).limbs = {0};
  return d.limbs;
}

const std::string& dec_get_digits(const Dec& d) {
  if (!d.digits.empty()) return d.digits;
  if (d.small) {
    unsigned __int128 mag = d.mantissa < 0 ? static_cast<unsigned __int128>(-(d.mantissa))
                                           : static_cast<unsigned __int128>(d.mantissa);
    const_cast<Dec&>(d).digits = dec_digits_from_magnitude(mag);
    return d.digits;
  }
  if (!d.limbs.empty()) {
    const_cast<Dec&>(d).digits = limbs_to_string(d.limbs);
    return d.digits;
  }
  const_cast<Dec&>(d).digits = "0";
  return d.digits;
}

Dec dec_from_mantissa(__int128_t mantissa, long long scale) {
  Dec d;
  d.neg = mantissa < 0;
  d.scale = static_cast<std::int32_t>(scale);
  d.small = true;
  d.mantissa = mantissa;
  return d;
}

Dec dec_from_limbs(bool neg, std::vector<uint32_t> limbs, long long scale) {
  while (limbs.size() > 1 && limbs.back() == 0) limbs.pop_back();
  if (limbs.empty() || (limbs.size() == 1 && limbs[0] == 0)) {
    return dec_from_mantissa(0, scale);
  }
  if (scale <= 38 && limbs.size() <= 4) {
    unsigned __int128 mag = 0;
    for (size_t i = limbs.size(); i > 0; --i) {
      mag = mag * BASE_10E9 + limbs[i - 1];
    }
    const unsigned __int128 limit = neg ? (static_cast<unsigned __int128>(1) << 127)
                                        : static_cast<unsigned __int128>(~((static_cast<unsigned __int128>(1)) << 127));
    if (mag <= limit) {
      __int128_t mantissa = neg ? -static_cast<__int128_t>(mag) : static_cast<__int128_t>(mag);
      return dec_from_mantissa(mantissa, scale);
    }
  }
  Dec d;
  d.neg = neg;
  d.small = false;
  d.scale = static_cast<std::int32_t>(scale);
  d.limbs = std::move(limbs);
  return d;
}

std::optional<__int128_t> dec_small_mantissa(const std::string& digits, bool neg) {
  if (digits.size() > 38) return std::nullopt;
  const unsigned __int128 limit =
      neg ? (static_cast<unsigned __int128>(1) << 127)
          : static_cast<unsigned __int128>(~((static_cast<unsigned __int128>(1)) << 127));
  unsigned __int128 magnitude = 0;
  for (const char ch : digits) {
    const unsigned digit = static_cast<unsigned>(ch - '0');
    if (magnitude > (limit - digit) / 10) return std::nullopt;
    magnitude = magnitude * 10 + digit;
  }
  if (!neg) return static_cast<__int128_t>(magnitude);
  if (magnitude == (static_cast<unsigned __int128>(1) << 127)) {
    return static_cast<__int128_t>(magnitude);
  }
  return -static_cast<__int128_t>(magnitude);
}

Dec dec_make(bool neg, std::string digits, long long scale) {
  Dec d;
  d.digits = strip(digits);
  d.neg = d.digits == "0" ? false : neg;
  d.scale = static_cast<std::int32_t>(scale);
  if (const auto small = dec_small_mantissa(d.digits, d.neg)) {
    d.small = true;
    d.mantissa = *small;
  }
  return d;
}

// Refuses a value SEL cannot hold, where it is built rather than where it is
// rendered. Every operation that can grow a number passes its result through
// here, so dec_power — repeated squaring over dec_mul — trips on an intermediate
// and the enormous value is never allocated.
Dec dec_guard(Dec&& d, Pos pos) {
  if (d.scale > MAX_FRAC_DIGITS) {
    fail("E_RANGE", "number has more than " + std::to_string(MAX_FRAC_DIGITS) + " fractional digits",
         pos);
  }
  if (d.small) {
    return std::move(d);
  }
  if (!d.limbs.empty()) {
    if (static_cast<long long>(d.limbs.size()) * 9 - d.scale <= MAX_INT_DIGITS) {
      return std::move(d);
    }
  }
  const std::string& digits = dec_get_digits(d);
  if (static_cast<long long>(digits.size()) - d.scale > MAX_INT_DIGITS) {
    fail("E_RANGE", "number has more than " + std::to_string(MAX_INT_DIGITS) + " integer digits",
         pos);
  }
  return std::move(d);
}

const Dec DEC_ZERO = dec_from_mantissa(0, 0);

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
// A well-formed numeral too big to hold is E_RANGE, not false: every character
// of it is a digit, so "not a number" would be false. Callers that must not
// raise — ISNUM's probe — catch it and answer no.
bool dec_parse(std::string_view text, Dec& out, Pos pos = {}) {
  if (!dec_is_number(text)) return false;
  const bool neg = text[0] == '-';
  const std::string_view body = neg ? text.substr(1) : text;
  const std::size_t dot = body.find('.');
  const long long frac_len = dot == std::string_view::npos ? 0 : static_cast<long long>(body.size() - 1 - dot);

  if (body.size() <= 38) {
    unsigned __int128 mag = 0;
    bool has_nonzero = false;
    for (size_t i = 0; i < body.size(); ++i) {
      if (i == dot) continue;
      unsigned d = static_cast<unsigned>(body[i] - '0');
      if (d > 0) has_nonzero = true;
      mag = mag * 10 + d;
    }
    const bool actual_neg = has_nonzero && neg;
    __int128_t mantissa = actual_neg ? -static_cast<__int128_t>(mag) : static_cast<__int128_t>(mag);
    out = dec_guard(dec_from_mantissa(mantissa, frac_len), pos);
    return true;
  }

  const std::string int_part(dot == std::string_view::npos ? body : body.substr(0, dot));
  const std::string frac_part(dot == std::string_view::npos ? std::string_view()
                                                            : body.substr(dot + 1));
  out = dec_guard(dec_make(neg, strip(int_part + frac_part), frac_len), pos);
  return true;
}

std::string dec_format(const Dec& d) {
  const std::string sign = d.neg ? "-" : "";
  const std::string& digits = dec_get_digits(d);
  if (d.scale == 0) return sign + digits;
  const std::size_t scale = static_cast<std::size_t>(d.scale);
  const std::string padded =
      digits.size() <= scale ? std::string(scale - digits.size() + 1, '0') + digits
                             : digits;
  return sign + padded.substr(0, padded.size() - scale) + "." +
         padded.substr(padded.size() - scale);
}

std::size_t dec_format_buf(const Dec& d, char* out) {
  char* p = out;
  if (d.neg) *p++ = '-';
  if (d.small) {
    __int128_t m = d.mantissa < 0 ? -d.mantissa : d.mantissa;
    char digits[48];
    int dlen = 0;
    if (m == 0) {
      digits[dlen++] = '0';
    } else {
      while (m > 0) {
        digits[dlen++] = '0' + static_cast<char>(m % 10);
        m /= 10;
      }
    }
    if (d.scale == 0) {
      for (int i = dlen - 1; i >= 0; --i) *p++ = digits[i];
      return static_cast<std::size_t>(p - out);
    }
    const int scale = d.scale;
    if (dlen <= scale) {
      *p++ = '0';
      *p++ = '.';
      for (int i = 0; i < scale - dlen; ++i) *p++ = '0';
      for (int i = dlen - 1; i >= 0; --i) *p++ = digits[i];
    } else {
      const int int_part = dlen - scale;
      for (int i = dlen - 1; i >= dlen - int_part; --i) *p++ = digits[i];
      *p++ = '.';
      for (int i = dlen - int_part - 1; i >= 0; --i) *p++ = digits[i];
    }
    return static_cast<std::size_t>(p - out);
  }
  const std::string s = dec_format(d);
  std::memcpy(out, s.data(), s.size());
  return s.size();
}

Dec dec_from_int(long long n) {
  return dec_from_mantissa(n, 0);
}

bool dec_is_zero(const Dec& d) {
  if (d.small) return d.mantissa == 0;
  if (!d.limbs.empty()) return d.limbs.size() == 1 && d.limbs[0] == 0;
  return dec_get_digits(d) == "0";
}

Dec dec_negate(const Dec& d) {
  if (d.small) return dec_from_mantissa(-d.mantissa, d.scale);
  if (!d.limbs.empty()) {
    Dec r = d;
    if (!(r.limbs.size() == 1 && r.limbs[0] == 0)) r.neg = !r.neg;
    return r;
  }
  return dec_make(!d.neg, dec_get_digits(d), d.scale);
}

Dec dec_abs(const Dec& d) {
  if (d.small) return dec_from_mantissa(d.mantissa < 0 ? -d.mantissa : d.mantissa, d.scale);
  if (!d.limbs.empty()) {
    Dec r = d;
    r.neg = false;
    return r;
  }
  return dec_make(false, dec_get_digits(d), d.scale);
}

int dec_sign(const Dec& d) {
  if (d.small) return d.mantissa == 0 ? 0 : (d.mantissa < 0 ? -1 : 1);
  if (!d.limbs.empty()) {
    if (d.limbs.size() == 1 && d.limbs[0] == 0) return 0;
    return d.neg ? -1 : 1;
  }
  return dec_is_zero(d) ? 0 : (d.neg ? -1 : 1);
}

// --- arithmetic

void dec_aligned(const Dec& a, const Dec& b, std::string& A, std::string& B, long long& s) {
  s = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
  A = scale_up(dec_get_digits(a), s - a.scale);
  B = scale_up(dec_get_digits(b), s - b.scale);
}

// The checked native fast path shared by addition and comparison: both small
// mantissas brought to the larger scale in __int128, or false when the scale
// gap is past the power table or the multiply overflows. The caller falls
// through to the limb path on false exactly as each did with its own copy;
// signed bounds are __builtin_mul_overflow's. Two callers, one rule (WL-001
// SEL-0018); it is not a small-integer abstraction for the other hosts.
inline bool align_small(const Dec& a, const Dec& b, __int128_t& sa, __int128_t& sb,
                        long long& target_scale) {
  target_scale = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
  if (target_scale > 38 || (target_scale - a.scale) > 38 || (target_scale - b.scale) > 38) return false;
  sa = a.mantissa;
  sb = b.mantissa;
  if (target_scale > a.scale && __builtin_mul_overflow(sa, POW10_128[target_scale - a.scale], &sa)) return false;
  if (target_scale > b.scale && __builtin_mul_overflow(sb, POW10_128[target_scale - b.scale], &sb)) return false;
  return true;
}

Dec dec_add(const Dec& a, const Dec& b, Pos pos = {}) {
  if (a.small && b.small) {
    __int128_t sa, sb;
    long long target_scale;
    if (align_small(a, b, sa, sb, target_scale)) {
      __int128_t sum;
      if (!__builtin_add_overflow(sa, sb, &sum)) {
        return dec_guard(dec_from_mantissa(sum, target_scale), pos);
      }
    }
  }
  const std::vector<uint32_t>& ref_a = dec_get_limbs(a);
  const std::vector<uint32_t>& ref_b = dec_get_limbs(b);
  const long long s = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
  const std::vector<uint32_t>* pa = &ref_a;
  const std::vector<uint32_t>* pb = &ref_b;
  std::vector<uint32_t> scaled_a, scaled_b;
  if (s > a.scale) {
    scaled_a = ref_a;
    scale_up_limbs(scaled_a, s - a.scale);
    pa = &scaled_a;
  }
  if (s > b.scale) {
    scaled_b = ref_b;
    scale_up_limbs(scaled_b, s - b.scale);
    pb = &scaled_b;
  }

  if (a.neg == b.neg) {
    return dec_guard(dec_from_limbs(a.neg, add_limbs(*pa, *pb), s), pos);
  }
  const int c = cmp_limbs(*pa, *pb);
  if (c == 0) return dec_from_mantissa(0, s);
  return c > 0 ? dec_guard(dec_from_limbs(a.neg, sub_limbs(*pa, *pb), s), pos)
               : dec_guard(dec_from_limbs(b.neg, sub_limbs(*pb, *pa), s), pos);
}

Dec dec_sub(const Dec& a, const Dec& b, Pos pos = {}) { return dec_add(a, dec_negate(b), pos); }

Dec dec_mul(const Dec& a, const Dec& b, Pos pos = {}) {
  if (a.small && b.small && a.scale <= MAX_FRAC_DIGITS - b.scale) {
    const long long prod_scale = static_cast<long long>(a.scale) + b.scale;
    if (prod_scale <= 38) {
      __int128_t prod;
      if (!__builtin_mul_overflow(a.mantissa, b.mantissa, &prod)) {
        return dec_guard(dec_from_mantissa(prod, prod_scale), pos);
      }
    }
  }
  const std::vector<uint32_t>& la = dec_get_limbs(a);
  const std::vector<uint32_t>& lb = dec_get_limbs(b);
  const bool res_neg = a.neg != b.neg;
  return dec_guard(dec_from_limbs(res_neg, mul_limbs(la, lb), static_cast<long long>(a.scale) + b.scale), pos);
}

int dec_cmp(const Dec& a, const Dec& b) {
  if (dec_is_zero(a) && dec_is_zero(b)) return 0;
  if (a.neg != b.neg) return a.neg ? -1 : 1;
  if (a.small && b.small) {
    __int128_t sa, sb;
    long long target_scale;
    if (align_small(a, b, sa, sb, target_scale)) return (sa > sb) - (sa < sb);
  }
  const std::vector<uint32_t>& ref_a = dec_get_limbs(a);
  const std::vector<uint32_t>& ref_b = dec_get_limbs(b);
  int c = 0;
  if (a.scale == b.scale) {
    c = cmp_limbs(ref_a, ref_b);
  } else if (a.scale < b.scale) {
    std::vector<uint32_t> la = ref_a;
    scale_up_limbs(la, b.scale - a.scale);
    c = cmp_limbs(la, ref_b);
  } else {
    std::vector<uint32_t> lb = ref_b;
    scale_up_limbs(lb, a.scale - b.scale);
    c = cmp_limbs(ref_a, lb);
  }
  return a.neg ? -c : c;
}

// Exact when the quotient terminates within DIV_SCALE fractional digits (and
// then reported at its minimal scale); otherwise rounded half away from zero to
// exactly DIV_SCALE digits. So 4/2 is "2" and 1/3 is "0.3333333333".
Dec dec_div(const Dec& a, const Dec& b, Pos pos = {}) {
  if (dec_is_zero(b)) fail("E_DIV_ZERO", "division by zero", pos);
  if (a.small && b.small) {
    const long long n_scale = static_cast<long long>(b.scale) + DIV_SCALE;
    const long long d_scale = a.scale;
    const long long min_s = std::min(n_scale, d_scale);
    const long long n_pow = n_scale - min_s;
    const long long d_pow = d_scale - min_s;
    if (n_pow <= 38 && d_pow <= 38) {
      __int128_t num = a.mantissa < 0 ? -a.mantissa : a.mantissa;
      __int128_t den = b.mantissa < 0 ? -b.mantissa : b.mantissa;
      bool ov = false;
      if (n_pow > 0 && __builtin_mul_overflow(num, POW10_128[n_pow], &num)) ov = true;
      if (d_pow > 0 && __builtin_mul_overflow(den, POW10_128[d_pow], &den)) ov = true;
      if (!ov && den != 0) {
        __int128_t q = num / den;
        __int128_t r = num % den;
        const bool neg = a.neg != b.neg;
        if (r == 0) {
          long long scale = DIV_SCALE;
          while (scale > 0 && q % 10 == 0) {
            q /= 10;
            scale--;
          }
          if (q == 0) scale = 0;
          __int128_t signed_q = neg ? -q : q;
          return dec_guard(dec_from_mantissa(signed_q, scale), pos);
        } else {
          if (2 * r >= den) q++;
          __int128_t signed_q = neg ? -q : q;
          return dec_guard(dec_from_mantissa(signed_q, DIV_SCALE), pos);
        }
      }
    }
  }
  const std::string N = scale_up(dec_get_digits(a), b.scale);
  const std::string D = scale_up(dec_get_digits(b), a.scale);
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
    return dec_guard(dec_make(neg, digits, scale), pos);
  }
  const std::string up = cmp_abs(add_abs(r, r), D) >= 0 ? add_abs(q, "1") : q;
  return dec_guard(dec_make(neg, up, DIV_SCALE), pos);
}

// Remainder of truncated division: takes the sign of the dividend.
Dec dec_mod(const Dec& a, const Dec& b, Pos pos = {}) {
  if (dec_is_zero(b)) fail("E_DIV_ZERO", "modulo by zero", pos);
  if (a.small && b.small) {
    const long long target_scale = std::max(a.scale, b.scale);
    if (target_scale <= 38 && (target_scale - a.scale) <= 38 && (target_scale - b.scale) <= 38) {
      __int128_t sa = a.mantissa < 0 ? -a.mantissa : a.mantissa;
      __int128_t sb = b.mantissa < 0 ? -b.mantissa : b.mantissa;
      bool ov = false;
      if (target_scale > a.scale) {
        if (__builtin_mul_overflow(sa, POW10_128[target_scale - a.scale], &sa)) ov = true;
      }
      if (target_scale > b.scale) {
        if (__builtin_mul_overflow(sb, POW10_128[target_scale - b.scale], &sb)) ov = true;
      }
      if (!ov && sb != 0) {
        __int128_t r = sa % sb;
        __int128_t signed_r = a.neg ? -r : r;
        return dec_from_mantissa(signed_r, target_scale);
      }
    }
  }
  std::string A, B;
  long long s;
  dec_aligned(a, b, A, B, s);
  std::string q, r;
  divmod_abs(A, B, q, r);
  return dec_make(a.neg, r, s);
}

// --- rounding. Every rounding in SEL is half away from zero (spec §4.4).

Dec dec_round(const Dec& d, long long n, Pos pos = {}) {
  if (n >= d.scale) {
    if (d.small && (n - d.scale) <= 38) {
      __int128_t res;
      if (!__builtin_mul_overflow(d.mantissa, POW10_128[n - d.scale], &res)) {
        return dec_guard(dec_from_mantissa(res, n), pos);
      }
    }
    return dec_guard(dec_make(d.neg, scale_up(dec_get_digits(d), n - d.scale), n), pos);
  }
  if (d.small && (d.scale - n) <= 38) {
    __int128_t p = POW10_128[d.scale - n];
    __int128_t abs_m = d.mantissa < 0 ? -d.mantissa : d.mantissa;
    __int128_t q = abs_m / p;
    __int128_t r = abs_m % p;
    if (2 * r >= p) q++;
    __int128_t signed_q = d.neg ? -q : q;
    return dec_guard(dec_from_mantissa(signed_q, n), pos);
  }
  const long long k = d.scale - n;
  const std::string p = pow10(k);
  std::string q, r;
  divmod_abs(dec_get_digits(d), p, q, r);
  // Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
  const std::string up = cmp_abs(add_abs(r, r), p) >= 0 ? add_abs(q, "1") : q;
  return dec_guard(dec_make(d.neg, up, n), pos);
}

Dec dec_trunc(const Dec& d) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    return dec_from_mantissa(d.mantissa / POW10_128[d.scale], 0);
  }
  std::string q, r;
  divmod_abs(dec_get_digits(d), pow10(d.scale), q, r);
  return dec_make(d.neg, q, 0);
}

Dec dec_floor(const Dec& d) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    __int128_t p = POW10_128[d.scale];
    __int128_t q = d.mantissa / p;
    __int128_t r = d.mantissa % p;
    if (d.mantissa < 0 && r != 0) q--;
    return dec_from_mantissa(q, 0);
  }
  std::string q, r;
  divmod_abs(dec_get_digits(d), pow10(d.scale), q, r);
  return dec_make(d.neg, d.neg && r != "0" ? add_abs(q, "1") : q, 0);
}

Dec dec_ceil(const Dec& d) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    __int128_t p = POW10_128[d.scale];
    __int128_t q = d.mantissa / p;
    __int128_t r = d.mantissa % p;
    if (d.mantissa > 0 && r != 0) q++;
    return dec_from_mantissa(q, 0);
  }
  std::string q, r;
  divmod_abs(dec_get_digits(d), pow10(d.scale), q, r);
  return dec_make(d.neg, !d.neg && r != "0" ? add_abs(q, "1") : q, 0);
}

// True when the value has no fractional part left after its scale is honoured.
bool dec_is_integer(const Dec& d) {
  if (d.scale == 0) return true;
  if (d.small && d.scale <= 38) {
    return (d.mantissa % POW10_128[d.scale]) == 0;
  }
  std::string q, r;
  divmod_abs(dec_get_digits(d), pow10(d.scale), q, r);
  return r == "0";
}

// n must be a non-negative integer; the result scale is scale(x) * n, which
// falls out of repeated multiplication.
Dec dec_power(const Dec& a, long long n, Pos pos = {}) {
  Dec result = dec_from_mantissa(1, 0);
  Dec base = a;
  long long e = n;
  while (e > 0) {
    if (e & 1) result = dec_mul(result, base, pos);
    e >>= 1;
    if (e > 0) base = dec_mul(base, base, pos);
  }
  return result;
}

// Truncates towards zero and converts. Used where a built-in needs a count or a
// length; the caller has already checked the range it cares about.
long long dec_to_int(const Dec& d) {
  if (d.small) {
    __int128_t m = d.mantissa;
    if (d.scale > 0) {
      if (d.scale <= 38) m /= POW10_128[d.scale];
      else m = 0;
    }
    if (m > 9223372036854775807LL) return 9223372036854775807LL;
    if (m < -9223372036854775807LL - 1) return -9223372036854775807LL - 1;
    return static_cast<long long>(m);
  }
  const Dec t = dec_trunc(d);
  long long v = 0;
  for (char c : dec_get_digits(t)) {
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

RecordShape::RecordShape(std::vector<std::string> names) : keys(std::move(names)) {
  key_map.reserve(keys.size() * 2);
  for (std::size_t i = 0; i < keys.size(); i++) key_map.emplace(keys[i], i);
}

namespace {

// Lisp interns record shapes so rows produced by the same RECORD/MAP/JOIN
// layout can share one immutable key map.  Keep the C++ cache process-local and
// synchronized: shapes are tiny, long-lived metadata, while rows only retain a
// shared_ptr<const RecordShape> and their flat slots.
constexpr std::size_t SHAPE_CACHE_ENTRIES = 256;
constexpr std::size_t SHAPE_CACHE_MAX_KEYS = 256;
constexpr std::size_t SHAPE_CACHE_MAX_BYTES = 16384;

bool cacheable_record_keys(const std::vector<std::string>& keys) {
  if (keys.size() > SHAPE_CACHE_MAX_KEYS) return false;
  std::size_t bytes = 0;
  for (const auto& key : keys) {
    if (key.size() > SHAPE_CACHE_MAX_BYTES - bytes) return false;
    bytes += key.size();
  }
  return true;
}

std::shared_ptr<const RecordShape> intern_record_shape(std::vector<std::string> keys) {
  static std::mutex cache_mutex;
  static std::map<std::vector<std::string>, std::shared_ptr<const RecordShape>> cache;
  std::lock_guard<std::mutex> lock(cache_mutex);
  const auto found = cache.find(keys);
  if (found != cache.end()) return found->second;
  auto shape = std::make_shared<RecordShape>(std::move(keys));
  if (cacheable_record_keys(shape->keys)) {
    if (cache.size() >= SHAPE_CACHE_ENTRIES) cache.clear();
    cache.emplace(shape->keys, shape);
  }
  return shape;
}

std::shared_ptr<const RecordShape> prepare_record_shape(const Node& node) {
  if (node.s != "RECORD" || node.items.empty() || node.items.size() % 2) return {};
  std::vector<std::string> keys;
  for (std::size_t i = 0; i < node.items.size(); i += 2) {
    if (node.items[i]->t != NT::Text) return {};
    const auto& key = node.items[i]->s;
    if (std::find(keys.begin(), keys.end(), key) != keys.end()) return {};
    keys.push_back(key);
  }
  return intern_record_shape(std::move(keys));
}

// Match Lisp's list-key contract: decimal keys 1..9 digits, no leading zero,
// and at most nine characters.  Keeping this parser on the flat path avoids
// materializing "1", "2", ... entries merely to answer LIST[index].
std::optional<std::size_t> parse_list_slot(const std::string& key) {
  if (key.empty() || key.size() > 9 || key[0] < '1' || key[0] > '9') {
    return std::nullopt;
  }
  std::size_t value = static_cast<std::size_t>(key[0] - '0');
  for (std::size_t i = 1; i < key.size(); i++) {
    if (key[i] < '0' || key[i] > '9') return std::nullopt;
    value = value * 10 + static_cast<std::size_t>(key[i] - '0');
  }
  return value - 1;
}

}  // namespace

// The unchecked constructor. Every internal producer of TEXT either copied
// existing text or encoded code points it had just validated, so re-validating
// would be pure cost; Value::text() is the checked entry point host code uses.
struct Internals {
  static Value raw(Kind kind, std::string scalar, bool b, bool is_list = false) {
    Value v;
    v.p_->kind = kind;
    v.p_->scalar = std::move(scalar);
    v.p_->scalar_computed = true;
    v.p_->boolean = b;
    v.p_->is_list = is_list;
    return v;
  }
  static Value with_children(Kind kind, std::vector<Value::Entry> entries, bool is_list = false) {
    Value v(Value::make_collection_impl());
    v.p_->kind = kind;
    v.p_->scalar_computed = true;
    v.p_->boolean = false;
    v.p_->is_list = is_list;
    v.p_->mutable_coll().children = std::move(entries);
    return v;
  }
  static Value shaped_direct(std::shared_ptr<const RecordShape> shape, std::size_t reserve_size) {
    Value v(Value::make_collection_impl());
    v.p_->mutable_coll().shape = std::move(shape);
    v.p_->mutable_coll().storage.reserve(reserve_size);
    return v;
  }
  static Value from_dec(Dec d) {
    Value v;
    v.p_->kind = Kind::Text;
    v.p_->decimal = std::make_unique<Dec>(std::move(d));
    v.p_->scalar_computed = false;
    return v;
  }
  static std::vector<Value>& storage(Value& v) {
    return v.p_->mutable_coll().storage;
  }
};

namespace {
// Containers have one allocation for their header and payload. Scalars retain
// the small header and can still acquire a separate payload through an alias.
struct CollectionImpl : Value::Impl {
  Value::Collection payload;
  CollectionImpl() {
    inline_collection = true;
    collection.reset(&payload);
  }
  // The derived object owns this payload. Release the base's pointer before
  // member destruction so it cannot delete the embedded Collection a second time.
  ~CollectionImpl() { collection.release(); }
};

void delete_impl(Value::Impl* impl) {
  if (impl->inline_collection) delete static_cast<CollectionImpl*>(impl);
  else delete impl;
}

struct ImplFreelist {
  Value::Impl* head = nullptr;
  std::size_t count = 0;
  static constexpr std::size_t MAX_CACHED = 2048;
  ~ImplFreelist() {
    while (head) {
      Value::Impl* next = head->next_free;
      ::operator delete(head);
      head = next;
    }
  }
};
thread_local ImplFreelist tl_impl_freelist;
}  // namespace

void* Value::Impl::operator new(std::size_t size) {
  if (size == sizeof(Value::Impl) && tl_impl_freelist.head) {
    Value::Impl* p = tl_impl_freelist.head;
    tl_impl_freelist.head = p->next_free;
    --tl_impl_freelist.count;
    return p;
  }
  return ::operator new(size);
}

void Value::Impl::operator delete(void* ptr, std::size_t size) noexcept {
  if (size == sizeof(Value::Impl) && ptr && tl_impl_freelist.count < ImplFreelist::MAX_CACHED) {
    Value::Impl* p = static_cast<Value::Impl*>(ptr);
    p->next_free = tl_impl_freelist.head;
    tl_impl_freelist.head = p;
    ++tl_impl_freelist.count;
    return;
  }
  ::operator delete(ptr);
}

Value::Value() : p_(new Impl()) {}

Value::Impl* Value::make_collection_impl() { return new CollectionImpl(); }

Kind Value::kind() const {
  return p_ ? p_->kind : Kind::None;
}

bool Value::is_none() const { return !p_ || kind() == Kind::None; }
bool Value::is_text() const { return p_ && kind() == Kind::Text; }
bool Value::is_bin() const { return p_ && kind() == Kind::Bin; }
bool Value::is_bool() const { return p_ && kind() == Kind::Bool; }
bool Value::is_list() const {
  return p_ && p_->is_list;
}
void Value::set_is_list(bool b) {
  if (!p_) return;
  if (!b && p_->is_list && !p_->coll().storage.empty()) {
    ensure_children();
    p_->mutable_coll().storage.clear();
  }
  p_->is_list = b;
  if (b) {
    p_->mutable_coll().shape.reset();
    p_->mutable_coll().storage.clear();
  }
}

// The deep copy. Recursive, because children are handles too: copying the
// vector alone would share every subtree.
// The three recursive walks over a value, and the cap they share.
//
// A value's nesting is the third thing spec/SPEC.md §6.4 caps, after the
// parser's and the evaluator's, and it was the last one left uncounted. These
// three -- and the destructor below -- recurse once per level, so a value nested
// deeply enough reached the host's own stack: RecursionError on Python at about
// a thousand levels, an uncaught RangeError on JS at about four, a segfault here
// at about sixty. Three hosts answered where two died, on the same program.
//
// The depth rides as a parameter, as it does in dependencies(): there is nothing
// to release on the way out, so no guard object is needed and all five hosts
// spell it the same way. A value of exactly MAX_DEPTH levels is fine; the level
// past it is refused.
//
// `pos` is the caller's, reported when there is one: the evaluator knows which
// node asked for the clone or the comparison, and every other E_DEPTH in this
// file names a position. A call from host code has no node to name and passes
// the empty Pos, the way Value::num already does.
Value Value::clone_at(int depth, Pos pos) const {
  if (depth > MAX_DEPTH) {
    fail("E_DEPTH", "value nested too deeply", pos);
  }
  Value out(p_->collection ? make_collection_impl() : new Impl());
  out.p_->kind = p_->kind;
  out.p_->scalar = p_->scalar;
  out.p_->scalar_computed = p_->scalar_computed;
  out.p_->boolean = p_->boolean;
  out.p_->is_list = p_->is_list;
  if (p_->decimal) out.p_->decimal = std::make_unique<Dec>(*p_->decimal);
  if (!p_->coll().shape && (!p_->is_list || p_->coll().storage.empty()) && p_->coll().children.empty()) {
    return out;
  }
  if (p_->coll().shape) {
    out.p_->mutable_coll().shape = p_->coll().shape;
    out.p_->mutable_coll().storage.reserve(p_->coll().storage.size());
    for (const Value& value : p_->coll().storage) {
      out.p_->mutable_coll().storage.push_back(value.clone_at(depth + 1, pos));
    }
  } else if (p_->is_list && p_->coll().children.empty()) {
    out.p_->mutable_coll().storage.reserve(p_->coll().storage.size());
    for (const Value& value : p_->coll().storage) {
      out.p_->mutable_coll().storage.push_back(value.clone_at(depth + 1, pos));
    }
  } else {
    out.p_->mutable_coll().children.reserve(p_->coll().children.size());
    for (const Entry& e : p_->coll().children) {
      out.p_->mutable_coll().children.emplace_back(e.first, e.second.clone_at(depth + 1, pos));
    }
    out.p_->mutable_coll().index = p_->coll().index;
  }
  return out;
}

Value Value::clone(Pos pos) const { return clone_at(1, pos); }

// Iterative, for the reason Node's destructor is: destroying a child is usually
// the last reference to it, so freeing a deep tree recursed once per level and
// found the stack at about a hundred thousand of them.
void Value::destroy(Impl* p) {
  if (!p) return;
  std::vector<Impl*> pending;
  const auto steal = [&pending](Impl* impl) {
    if (!impl->collection) return;
    for (Entry& e : impl->collection->children) {
      if (Impl* child = e.second.p_) {
        e.second.p_ = nullptr;
        if (--child->ref_count == 0) {
          pending.push_back(child);
        }
      }
    }
    impl->collection->children.clear();
    for (Value& val : impl->collection->storage) {
      if (Impl* child = val.p_) {
        val.p_ = nullptr;
        if (--child->ref_count == 0) {
          pending.push_back(child);
        }
      }
    }
    impl->collection->storage.clear();
  };

  steal(p);
  delete_impl(p);
  while (!pending.empty()) {
    Impl* curr = pending.back();
    pending.pop_back();
    steal(curr);
    delete_impl(curr);
  }
}

Value Value::num(const Dec& d) {
  return Internals::from_dec(d);
}

Value Value::num(std::shared_ptr<const Dec> d) {
  if (!d) return Value::none();
  return Value::num(*d);
}

namespace {

Value make_text(std::string utf8) { return Internals::raw(Kind::Text, std::move(utf8), false); }
Value make_bin(std::string bytes) { return Internals::raw(Kind::Bin, std::move(bytes), false); }
Value make_num(const Dec& d) {
  return Value::num(d);
}
Value make_num(Dec&& d) {
  return Internals::from_dec(std::move(d));
}
Value make_int(long long n) {
  return make_num(dec_from_int(n));
}

}  // namespace

Value Value::none() { return Internals::raw(Kind::None, "", false); }

Value Value::null() { return Internals::raw(Kind::None, "", false, false); }

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
  return make_num(std::move(d));
}

Value Value::integer(long long n) {
  return make_num(dec_from_int(n));
}

Value Value::list(std::vector<Value> values) {
  Value v(make_collection_impl());
  v.p_->scalar_computed = true;
  v.p_->is_list = true;
  v.p_->mutable_coll().storage = std::move(values);
  return v;
}

Value Value::record(std::vector<std::string> keys, std::vector<Value> values) {
  if (keys.size() != values.size()) {
    throw std::invalid_argument("SEL record needs one value per key");
  }
  bool duplicate = false;
  if (keys.size() <= 16) {
    // Host-loaded records are small. A short linear probe avoids allocating a
    // temporary hash table for every JSON object while retaining exact
    // insertion/update semantics for the uncommon duplicate-key case.
    for (std::size_t i = 0; i < keys.size() && !duplicate; ++i) {
      duplicate = std::find(keys.begin(), keys.begin() + static_cast<std::ptrdiff_t>(i),
                            keys[i]) != keys.begin() + static_cast<std::ptrdiff_t>(i);
    }
  } else {
    std::unordered_set<std::string> unique;
    unique.reserve(keys.size() * 2);
    for (const std::string& key : keys) {
      if (!unique.insert(key).second) {
        duplicate = true;
        break;
      }
    }
  }
  if (duplicate) {
    Value out(make_collection_impl());
    out.p_->scalar_computed = true;
    for (std::size_t i = 0; i < keys.size(); ++i) out.set(keys[i], values[i]);
    return out;
  }
  return shaped(intern_record_shape(std::move(keys)), std::move(values));
}

Value Value::shaped(std::shared_ptr<const RecordShape> shape, std::vector<Value> storage) {
  if (!shape || shape->keys.size() != storage.size()) {
    throw std::invalid_argument("SEL shaped value needs one slot per record key");
  }
  Value v(make_collection_impl());
  v.p_->scalar_computed = true;
  v.p_->mutable_coll().shape = std::move(shape);
  v.p_->mutable_coll().storage = std::move(storage);
  return v;
}

void Value::ensure_children() const {
  if (p_->coll().shape) {
    if (p_->coll().children.size() == p_->coll().shape->keys.size()) return;
    p_->mutable_coll().children.clear();
    p_->mutable_coll().children.reserve(p_->coll().shape->keys.size());
    for (std::size_t i = 0; i < p_->coll().shape->keys.size(); i++) {
      p_->mutable_coll().children.emplace_back(p_->coll().shape->keys[i], p_->coll().storage[i]);
    }
    return;
  }
  if (p_->is_list && !p_->coll().storage.empty() &&
      p_->coll().children.size() != p_->coll().storage.size()) {
    p_->mutable_coll().children.clear();
    p_->mutable_coll().children.reserve(p_->coll().storage.size());
    for (std::size_t i = 0; i < p_->coll().storage.size(); i++) {
      p_->mutable_coll().children.emplace_back(std::to_string(i + 1), p_->coll().storage[i]);
    }
    if (p_->coll().children.size() >= INDEX_THRESHOLD) build_index();
  }
}

void Value::build_index() const {
  p_->mutable_coll().index.clear();
  p_->mutable_coll().index.reserve(p_->coll().children.size() * 2);
  for (std::size_t i = 0; i < p_->coll().children.size(); i++) p_->mutable_coll().index.emplace(p_->coll().children[i].first, i);
}

std::vector<Value::Entry>::iterator Value::find(const std::string& key) {
  ensure_children();
  if (p_->mutable_coll().index.empty() && p_->mutable_coll().children.size() >= INDEX_THRESHOLD) {
    build_index();
  }
  if (!p_->mutable_coll().index.empty()) {
    auto it = p_->mutable_coll().index.find(key);
    return it == p_->mutable_coll().index.end() ? p_->mutable_coll().children.end()
                              : p_->mutable_coll().children.begin() + static_cast<std::ptrdiff_t>(it->second);
  }
  return std::find_if(p_->mutable_coll().children.begin(), p_->mutable_coll().children.end(),
                      [&](const Entry& e) { return e.first == key; });
}

std::vector<Value::Entry>::const_iterator Value::find(const std::string& key) const {
  ensure_children();
  if (p_->coll().index.empty() && p_->coll().children.size() >= INDEX_THRESHOLD) {
    build_index();
  }
  if (!p_->coll().index.empty()) {
    auto it = p_->coll().index.find(key);
    return it == p_->coll().index.end() ? p_->coll().children.end()
                              : p_->coll().children.begin() + static_cast<std::ptrdiff_t>(it->second);
  }
  return std::find_if(p_->coll().children.begin(), p_->coll().children.end(),
                      [&](const Entry& e) { return e.first == key; });
}

std::size_t Value::size() const {
  if (p_->coll().shape) return p_->coll().shape->keys.size();
  if (p_->is_list && !p_->coll().storage.empty()) return p_->coll().storage.size();
  return p_->coll().children.size();
}

const std::vector<Value::Entry>& Value::entries() const {
  ensure_children();
  return p_->coll().children;
}

const std::shared_ptr<const RecordShape>& Value::shape() const {
  return p_->coll().shape;
}

const std::vector<Value>& Value::storage() const {
  return p_->coll().storage;
}

const Value* Value::slot(std::size_t index) const {
  return index < p_->coll().storage.size() ? &p_->coll().storage[index] : nullptr;
}

bool Value::has(const std::string& key) const {
  if (p_->coll().shape) return p_->coll().shape->key_map.find(key) != p_->coll().shape->key_map.end();
  if (p_->is_list && !p_->coll().storage.empty()) {
    const auto index = parse_list_slot(key);
    return index && *index < p_->coll().storage.size();
  }
  return find(key) != p_->coll().children.end();
}

const Value* Value::get(const std::string& key) const {
  if (!p_->collection) return nullptr;
  if (p_->coll().shape) {
    const auto it = p_->coll().shape->key_map.find(key);
    return it == p_->coll().shape->key_map.end() ? nullptr : &p_->coll().storage[it->second];
  }
  if (p_->is_list && !p_->coll().storage.empty()) {
    const auto index = parse_list_slot(key);
    return index && *index < p_->coll().storage.size() ? &p_->coll().storage[*index] : nullptr;
  }
  auto it = find(key);
  return it == p_->coll().children.end() ? nullptr : &it->second;
}

Value* Value::get(const std::string& key) {
  if (!p_->collection) return nullptr;
  if (p_->mutable_coll().shape) {
    const auto it = p_->mutable_coll().shape->key_map.find(key);
    return it == p_->mutable_coll().shape->key_map.end() ? nullptr : &p_->mutable_coll().storage[it->second];
  }
  if (p_->is_list && !p_->mutable_coll().storage.empty()) {
    const auto index = parse_list_slot(key);
    return index && *index < p_->mutable_coll().storage.size() ? &p_->mutable_coll().storage[*index] : nullptr;
  }
  auto it = find(key);
  return it == p_->mutable_coll().children.end() ? nullptr : &it->second;
}

std::vector<std::string> Value::keys() const {
  if (p_->coll().shape) return p_->coll().shape->keys;
  if (p_->is_list && !p_->coll().storage.empty()) {
    std::vector<std::string> out;
    out.reserve(p_->coll().storage.size());
    for (std::size_t i = 0; i < p_->coll().storage.size(); i++) {
      out.push_back(std::to_string(i + 1));
    }
    return out;
  }
  std::vector<std::string> out;
  out.reserve(p_->coll().children.size());
  for (const auto& e : p_->coll().children) out.push_back(e.first);
  return out;
}

// Re-assigning an existing key keeps its original position — order is normative.
Value& Value::set(std::string key, Value value) {
  if (p_->mutable_coll().shape) {
    const auto shape_it = p_->mutable_coll().shape->key_map.find(key);
    if (shape_it != p_->mutable_coll().shape->key_map.end()) {
      p_->mutable_coll().storage[shape_it->second] = std::move(value);
      if (p_->mutable_coll().children.size() == p_->mutable_coll().shape->keys.size()) {
        p_->mutable_coll().children[shape_it->second].second = p_->mutable_coll().storage[shape_it->second];
      }
      return *this;
    }
    // Adding a field changes the layout. Materialize the ordered fallback only
    // at this uncommon mutation boundary, then discard the immutable shape.
    ensure_children();
    p_->mutable_coll().shape.reset();
    p_->mutable_coll().storage.clear();
    p_->mutable_coll().index.clear();
  }
  if (p_->is_list && !p_->mutable_coll().storage.empty()) {
    const auto list_index = parse_list_slot(key);
    if (list_index && *list_index < p_->mutable_coll().storage.size()) {
      p_->mutable_coll().storage[*list_index] = std::move(value);
      if (p_->mutable_coll().children.size() == p_->mutable_coll().storage.size()) {
        p_->mutable_coll().children[*list_index].second = p_->mutable_coll().storage[*list_index];
      }
      return *this;
    }
    // A non-index field turns the packed list into the ordinary ordered
    // representation, matching Lisp's value-set fallback.
    ensure_children();
    p_->mutable_coll().storage.clear();
  }
  auto it = find(key);
  if (it != p_->mutable_coll().children.end()) {
    it->second = std::move(value);   // re-assignment keeps the original position
    return *this;
  }
  p_->mutable_coll().children.emplace_back(std::move(key), std::move(value));
  if (!p_->mutable_coll().index.empty()) {
    p_->mutable_coll().index.emplace(p_->mutable_coll().children.back().first, p_->mutable_coll().children.size() - 1);
  } else if (p_->mutable_coll().children.size() >= INDEX_THRESHOLD) {
    build_index();
  }
  return *this;
}

bool Value::is_null() const {
  return p_->kind == Kind::None && size() == 0 && !p_->is_list;
}

bool Value::is_vacuous() const {
  if (is_null()) return true;
  if (p_->kind == Kind::None && size() == 0) return true;
  if (p_->kind == Kind::Text && size() == 0) {
    const std::string& sc = scalar();
    if (sc.empty()) return true;
    for (char ch : sc) {
      if (ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n') return false;
    }
    return true;
  }
  return false;
}

// The value that supplies the scalar: itself, or its first child, recursively.
const Value& Value::scalar_source(Pos pos) const {
  if (p_->kind != Kind::None) return *this;
  const Value* v = this;
  int guard = 0;
  while (true) {
    if (v->p_->kind != Kind::None) break;
    if (v->is_null()) {
      throw SelError("E_NULL", "value is NULL", pos);
    }
    if (v->size() == 0) {
      throw SelError("E_NO_SCALAR", "value has no scalar and no children", pos);
    }
    const Value* first = v->slot(0);
    if (!first) {
      v->ensure_children();
      first = v->p_->coll().children.empty() ? nullptr : &v->p_->coll().children.front().second;
    }
    if (!first) {
      throw SelError("E_NO_SCALAR", "value has no scalar and no children", pos);
    }
    v = first;
    if (++guard > 1000) throw SelError("E_DEPTH", "scalar context nested too deeply", pos);
  }
  return *v;
}

const std::string& Value::as_text(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.p_->kind == Kind::Text) {
    if (!v.p_->scalar_computed && v.p_->decimal) {
      v.p_->scalar = dec_format((*v.p_->decimal));
      v.p_->scalar_computed = true;
    }
    return v.p_->scalar;
  }
  if (v.p_->kind == Kind::Bin) {
    throw SelError("E_NOT_TEXT", "expected text, got binary (use FROM_UTF8)", pos);
  }
  throw SelError("E_NOT_TEXT", "expected text, got boolean", pos);
}

// TEXT already holds its UTF-8 bytes, so this is free for both kinds.
const std::string& Value::as_bytes(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.p_->kind == Kind::Text) {
    if (!v.p_->scalar_computed && v.p_->decimal) {
      v.p_->scalar = dec_format((*v.p_->decimal));
      v.p_->scalar_computed = true;
    }
    return v.p_->scalar;
  }
  if (v.p_->kind == Kind::Bin) return v.p_->scalar;
  throw SelError("E_NOT_BIN", "expected binary or text, got boolean", pos);
}

bool Value::as_bool(Pos pos) const {
  const Value& v = scalar_source(pos);
  if (v.p_->kind == Kind::Bool) return v.p_->boolean;
  throw SelError("E_NOT_BOOL", "expected a boolean — SEL has no truthiness", pos);
}

const std::string& Value::scalar() const {
  if (p_->kind == Kind::Text && !p_->scalar_computed && p_->decimal) {
    p_->scalar = dec_format((*p_->decimal));
    p_->scalar_computed = true;
  }
  return p_->scalar;
}

bool Value::boolean_scalar() const {
  return p_->boolean;
}

bool Value::looks_numeric() const {
  if (p_->kind == Kind::None && size() == 0) return false;
  try {
    const Value& v = scalar_source();
    if (v.p_->decimal) return true;
    Dec d;
    return v.p_->kind == Kind::Text && sel::dec_parse(v.scalar(), d);
  } catch (const SelError&) {
    return false;
  }
}

// Same kind, equal scalars with numbers *not* normalised, children with the same
// keys in the same order, pairwise EQL.
bool Value::eql(const Value& other, Pos pos) const { return eql_at(other, 1, pos); }

bool Value::eql_at(const Value& other, int depth, Pos pos) const {
  if (depth > MAX_DEPTH) {
    fail("E_DEPTH", "value nested too deeply", pos);
  }
  if (p_->kind != other.p_->kind) return false;
  if (p_->kind == Kind::Text) {
    if (!p_->scalar_computed && !other.p_->scalar_computed &&
        p_->decimal && other.p_->decimal) {
      const Dec& a = (*p_->decimal);
      const Dec& b = (*other.p_->decimal);
      if (a.neg != b.neg || a.scale != b.scale) return false;
      if (a.small && b.small) {
        if (a.mantissa != b.mantissa) return false;
      } else {
        if (dec_get_digits(a) != dec_get_digits(b)) return false;
      }
    } else {
      if (scalar() != other.scalar()) return false;
    }
  } else if (p_->kind == Kind::Bin) {
    if (p_->scalar != other.p_->scalar) return false;
  } else if (p_->kind == Kind::Bool) {
    if (p_->boolean != other.p_->boolean) return false;
  }
  if (size() != other.size()) return false;
  if (p_->coll().shape && other.p_->coll().shape && p_->coll().shape == other.p_->coll().shape) {
    for (std::size_t i = 0; i < p_->coll().storage.size(); i++) {
      if (!p_->coll().storage[i].eql_at(other.p_->coll().storage[i], depth + 1, pos)) return false;
    }
    return true;
  }
  if (p_->is_list && other.p_->is_list && p_->coll().children.empty() && other.p_->coll().children.empty()) {
    for (std::size_t i = 0; i < p_->coll().storage.size(); i++) {
      if (!p_->coll().storage[i].eql_at(other.p_->coll().storage[i], depth + 1, pos)) return false;
    }
    return true;
  }
  ensure_children();
  other.ensure_children();
  for (std::size_t i = 0; i < p_->coll().children.size(); i++) {
    if (p_->coll().children[i].first != other.p_->coll().children[i].first) return false;  // order is normative
    if (!p_->coll().children[i].second.eql_at(other.p_->coll().children[i].second, depth + 1, pos)) {
      return false;
    }
  }
  return true;
}

std::string Value::dump() const { return dump_at(1); }

std::string Value::dump_at(int depth) const {
  if (depth > MAX_DEPTH) {
    fail("E_DEPTH", "value nested too deeply", {});
  }
  std::string s;
  switch (p_->kind) {
    case Kind::None: s = "-"; break;
    case Kind::Text: s = "t" + sel::quote_dump(scalar()); break;
    case Kind::Bin: s = "b" + sel::to_hex(p_->scalar); break;
    case Kind::Bool: s = p_->boolean ? "TRUE" : "FALSE"; break;
  }
  if (size() == 0) return s;
  s += "{";
  if (p_->coll().shape) {
    for (std::size_t i = 0; i < p_->coll().shape->keys.size(); i++) {
      if (i > 0) s += ", ";
      s += sel::quote_dump(p_->coll().shape->keys[i]) + "=" +
           p_->coll().storage[i].dump_at(depth + 1);
    }
    return s + "}";
  }
  if (p_->is_list && p_->coll().children.empty()) {
    for (std::size_t i = 0; i < p_->coll().storage.size(); i++) {
      if (i > 0) s += ", ";
      s += sel::quote_dump(std::to_string(i + 1)) + "=" +
           p_->coll().storage[i].dump_at(depth + 1);
    }
    return s + "}";
  }
  ensure_children();
  for (std::size_t i = 0; i < p_->coll().children.size(); i++) {
    if (i > 0) s += ", ";
    s += sel::quote_dump(p_->coll().children[i].first) + "=" +
         p_->coll().children[i].second.dump_at(depth + 1);
  }
  return s + "}";
}

std::uint64_t Value::structural_hash(Pos pos) const {
  const auto mix = [](std::uint64_t h, std::uint64_t v) {
    // A cheap 64-bit avalanche is enough here: equal values are still checked
    // with eql() inside each bucket, so this only controls the fast path.
    h ^= v + UINT64_C(0x9e3779b97f4a7c15) + (h << 6) + (h >> 2);
    return h;
  };
  const auto walk = [&](auto& self, const Value& value, int depth) -> std::uint64_t {
    if (depth > MAX_DEPTH) fail("E_DEPTH", "value nested too deeply", pos);
    std::uint64_t h = mix(UINT64_C(0xcbf29ce484222325), static_cast<std::uint64_t>(value.p_->kind));
    h = mix(h, value.p_->boolean ? 1 : 0);
    if (value.p_->kind == Kind::Text) {
      if (value.p_->scalar_computed) {
        h = mix(h, std::hash<std::string_view>{}(value.p_->scalar));
      } else if (value.p_->decimal && value.p_->decimal->small && value.p_->decimal->scale <= 38) {
        // Only bounded native decimals fit in this allocation-free buffer.
        char buf[64];
        const std::size_t len = dec_format_buf((*value.p_->decimal), buf);
        h = mix(h, std::hash<std::string_view>{}(std::string_view(buf, len)));
      } else {
        h = mix(h, std::hash<std::string_view>{}(value.scalar()));
      }
    } else {
      h = mix(h, std::hash<std::string_view>{}(value.p_->scalar));
    }
    if (value.p_->coll().shape) {
      for (std::size_t i = 0; i < value.p_->coll().shape->keys.size(); i++) {
        h = mix(h, std::hash<std::string>{}(value.p_->coll().shape->keys[i]));
        h = mix(h, self(self, value.p_->coll().storage[i], depth + 1));
      }
    } else if (value.p_->is_list && value.p_->coll().children.empty()) {
      // Derived keys are still logical keys, just as in materialized records.
      std::size_t index = 0;
      for (const Value& item : value.p_->coll().storage) {
        h = mix(h, std::hash<std::string>{}(std::to_string(++index)));
        h = mix(h, self(self, item, depth + 1));
      }
    } else {
      value.ensure_children();
      for (const auto& entry : value.p_->coll().children) {
        h = mix(h, std::hash<std::string>{}(entry.first));
        h = mix(h, self(self, entry.second, depth + 1));
      }
    }
    return h;
  };
  return walk(walk, *this, 1);
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



// A function-local static, so the table is built on first use rather than
// depending on the order of static initialisers across the translation unit.
std::map<std::string, Spec>& table() {
  static std::map<std::string, Spec> t;
  return t;
}

// The shipped table is authored once, in spec/builtins.json, and rendered into
// sel_builtin_manifest.hpp. A name the manifest knows is held to it:
// min/max/lazy/binds must agree, and the extra arity rule (COND's odd count,
// LINK's three-or-five) comes from the manifest rather than from the caller —
// one body for all five hosts. A name it does not know is a host's own
// function (examples/fn-*) and passes.
const sel_builtin_manifest::Entry* manifest_entry(const std::string& name) {
  using sel_builtin_manifest::ENTRIES;
  using sel_builtin_manifest::COUNT;
  const auto* end = ENTRIES + COUNT;
  const auto* it = std::lower_bound(ENTRIES, end, name,
                                    [](const sel_builtin_manifest::Entry& e, const std::string& n) { return n.compare(e.name) > 0; });
  return it != end && name == it->name ? it : nullptr;
}

void define(Spec spec) {
  if (table().count(spec.name)) {
    throw std::runtime_error("SEL function " + spec.name + " defined twice");
  }
  if (const auto* m = manifest_entry(spec.name)) {
    const int m_max = m->max < 0 ? VARIADIC : m->max;
    std::string wrong;
    auto note = [&](const std::string& s) { wrong += (wrong.empty() ? "" : "; ") + s; };
    if (spec.min != m->min) note("min " + std::to_string(spec.min) + " vs " + std::to_string(m->min));
    if (spec.max != m_max) note("max " + std::to_string(spec.max) + " vs " + std::to_string(m_max));
    if (spec.lazy != m->lazy) note(std::string("lazy ") + (spec.lazy ? "true" : "false") + " vs " + (m->lazy ? "true" : "false"));
    if (spec.binds != m->binds) note(std::string("binds ") + (spec.binds ? "true" : "false") + " vs " + (m->binds ? "true" : "false"));
    if (spec.arity_error != nullptr) note("an arity rule of its own, which the manifest owns");
    if (!wrong.empty()) {
      throw std::logic_error("SEL function " + spec.name + " disagrees with spec/builtins.json: " + wrong);
    }
    spec.arity_error = m->arity_error;
  }
  table()[spec.name] = std::move(spec);
}

// Called once the shipped builtins have registered: a manifest entry with no
// definition is a host that would silently lack a builtin the others have.
void assert_manifest_covered() {
  std::string missing;
  for (int i = 0; i < sel_builtin_manifest::COUNT; i++) {
    const char* name = sel_builtin_manifest::ENTRIES[i].name;
    if (!table().count(name)) missing += (missing.empty() ? "" : ", ") + std::string(name);
  }
  if (!missing.empty()) {
    throw std::logic_error("spec/builtins.json names builtins this host never defined: " + missing);
  }
}

const Spec* registry_lookup(const std::string& name) {
  auto it = table().find(name);
  return it == table().end() ? nullptr : &it->second;
}

void register_builtins();   // defined after the built-ins themselves
void math_ops_check();      // defined with the math plan; every manifest operation has an opcode

// Every entry point that can parse must go through this first: the table has to
// be complete before any source is read.
void ensure_registered() {
  static bool done = [] {
    register_builtins();
    assert_manifest_covered();
    math_ops_check();
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
      "???", "??",
      "$==", "$!=", "$<=", "$>=",
      "$<", "$>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "&=",
      ".>",
      "+", "-", "*", "/", "%", "&", "=", "<", ">", "(", ")", "[", "]", ",", ";",
  };
  return ops;
}

bool is_reserved(const std::string& w) {
  static const std::set<std::string> r = {
      "TRUE", "FALSE", "NULL", "AND", "OR", "NOT", "XOR", "EQL", "IN", "BAND", "BOR", "BXOR",
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
// Precedence climbing. The sixteen levels of spec/SPEC.md §5 are the table
// below rather than sixteen functions, so adding an operator is adding a row.
// python/sel/parser.py is the reference implementation of this shape and its
// module docstring is the rationale; docs/EXTENDING.md, "Adding an operator",
// step 5, records what every host had to get right, each item of which
// produces a valid parse of the WRONG TREE when it is wrong.
//
// `;` and `,` stay hand-written N-ary loops outside the table, because they
// build N-ary nodes rather than binary ones -- dependencies() walks `items`,
// and parse_call flattens a top-level list into the argument vector.
// ============================================================================

namespace {

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

// The operator families, named once for the PARSER. The precedence table is
// built from these rather than repeating them, and the evaluator asks
// compare_ops() whether an operator is a numeric comparison -- so the parser
// and the evaluator cannot disagree about what a comparison is.
//
// That is the whole of the claim. Adding a comparison operator is still three
// edits, and they are here, in operators() for the lexer, and in
// compare_result() for its meaning. The third used to be the dangerous one: its
// last branch answered for every operator it did not name, so an operator added
// to the first two and forgotten here silently meant ">=". It now fails.
const std::set<std::string>& assign_ops() {
  static const std::set<std::string> ops = {"=", "+=", "-=", "*=", "/=", "%=", "&="};
  return ops;
}

const std::set<std::string>& compare_ops() {
  static const std::set<std::string> ops = {"==", "!=", "<", "<=", ">", ">=",
                                            "$==", "$!=", "$<", "$<=", "$>", "$>="};
  return ops;
}

const std::set<std::string>& compare_words() {
  static const std::set<std::string> words = {"EQL", "IN"};
  return words;
}

bool is_compare_op(const Token& t) {
  return t.type == Tok::Op && compare_ops().count(t.value) > 0;
}

// spec/SPEC.md §5, as a table. Higher binds tighter. The gaps are the levels
// that are not infix: 16 is postfix/primary, 15 is unary minus, 7 is NOT.
[[maybe_unused]] constexpr int BP_SEQ = 1;    // ;  documentation only:
[[maybe_unused]] constexpr int BP_LIST = 2;   // ,  both are N-ary loops
constexpr int BP_ASSIGN = 3;    // = += -= *= /= %= &=   (right associative)
constexpr int BP_OR = 4;
constexpr int BP_XOR = 5;
constexpr int BP_AND = 6;
constexpr int BP_NOT = 7;       // prefix
constexpr int BP_COMPARE = 8;   // non-associative
constexpr int BP_COALESCE = 9;  // ?? ??? (right associative)
constexpr int BP_BOR = 10;
constexpr int BP_BXOR = 11;
constexpr int BP_BAND = 12;
constexpr int BP_CONCAT = 13;   // &
constexpr int BP_ADD = 14;      // + -
constexpr int BP_MUL = 15;      // * / %
constexpr int BP_NEG = 16;      // prefix

// An infix operator's binding power and associativity. 'L' parses its right
// side at bp + 1, 'R' at bp -- that is what makes it right-associative -- and
// 'N' at bp + 1 and then rejects a second operator at the same level.
struct Infix {
  int bp;
  char assoc;
};

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

  // C++ has no `finally`, so every enter() is released by one of these going out
  // of scope -- including on the throw fail() raises, which is the whole point.
  // It was written out locally at each of the four counted constructs; the fifth
  // is what made one type worth having.
  struct Leave {
    Parser* p;
    explicit Leave(Parser* parser) : p(parser) {}
    ~Leave() { p->leave(); }
    Leave(const Leave&) = delete;
    Leave& operator=(const Leave&) = delete;
  };

  static std::shared_ptr<Node> make(NT t, Pos pos) {
    auto n = std::make_shared<Node>();
    n->t = t;
    n->pos = pos;
    return n;
  }

  // sequence = list { ";" list } [ ";" ]
  //
  // The guard around the counter is the last of the five to be protected. It
  // costs nothing -- a failing parse abandons the Parser either way -- and this
  // host was the only one left leaving it to fall through, which is the first
  // thing a reviewer asks about.
  NodePtr parse_sequence() {
    const Pos start = peek().pos;
    enter(start);
    std::vector<NodePtr> items;
    {
      const Leave leave_guard{this};
      items.push_back(parse_list());
      while (at_op(";")) {
        next();
        // A trailing ';' before a closer or end of input is permitted.
        if (at_eof() || at_op(")") || at_op("]")) break;
        items.push_back(parse_list());
      }
    }
    if (items.size() == 1) return items[0];
    auto n = make(NT::Seq, items[0]->pos);
    n->items = std::move(items);
    return n;
  }

  // list = term { "," term }
  NodePtr parse_list() {
    std::vector<NodePtr> items{parse_term(BP_ASSIGN)};
    while (at_op(",")) {
      next();
      items.push_back(parse_term(BP_ASSIGN));
    }
    if (items.size() == 1) return items[0];
    auto n = make(NT::List, items[0]->pos);
    n->items = std::move(items);
    return n;
  }

  // --- the precedence-climbing loop -----------------------------------------

  // The two tables are one lookup. Every question about an operator -- what it
  // binds at, how it associates, and whether it may follow a comparison -- is
  // answered from here, so adding an operator really is adding a row. Asking a
  // separate list anywhere would put that claim back in doubt.
  //
  // Two tables and not one because word operators lex as identifiers and symbol
  // operators as ops, so they cannot share a key space; the binding powers are
  // one scale. Returns nullptr for a token that is not an infix operator, which
  // is the same answer as "stop here".
  static const Infix* infix_entry(const Token& t) {
    static const std::map<std::string, Infix> ops = [] {
      std::map<std::string, Infix> m = {
          {"??", {BP_COALESCE, 'R'}}, {"???", {BP_COALESCE, 'R'}},
          {"&", {BP_CONCAT, 'L'}},
          {"+", {BP_ADD, 'L'}}, {"-", {BP_ADD, 'L'}},
          {"*", {BP_MUL, 'L'}}, {"/", {BP_MUL, 'L'}}, {"%", {BP_MUL, 'L'}},
      };
      for (const std::string& op : assign_ops()) m[op] = {BP_ASSIGN, 'R'};
      for (const std::string& op : compare_ops()) m[op] = {BP_COMPARE, 'N'};
      return m;
    }();
    static const std::map<std::string, Infix> words = [] {
      std::map<std::string, Infix> m = {
          {"OR", {BP_OR, 'L'}}, {"XOR", {BP_XOR, 'L'}}, {"AND", {BP_AND, 'L'}},
          {"BOR", {BP_BOR, 'L'}}, {"BXOR", {BP_BXOR, 'L'}}, {"BAND", {BP_BAND, 'L'}},
      };
      for (const std::string& w : compare_words()) m[w] = {BP_COMPARE, 'N'};
      return m;
    }();

    const std::map<std::string, Infix>* table = nullptr;
    if (t.type == Tok::Op) table = &ops;
    else if (t.type == Tok::Ident) table = &words;
    else return nullptr;
    const auto it = table->find(t.value);
    return it == table->end() ? nullptr : &it->second;
  }

  NodePtr parse_term(int min_bp) {
    NodePtr left = parse_prefix(min_bp);

    for (;;) {
      const Token t = peek();
      const Infix* e = infix_entry(t);
      if (e == nullptr || e->bp < min_bp) return left;

      next();

      if (e->assoc == 'R') {
        if (assign_ops().count(t.value)) {
          // Assignment. The target is validated against the AST shape, not against
          // a value, which is what makes `(A) = 1` a compile error.
          check_target(left, t);
          // Counted, for the same reason parse_prefix counts: the right side
          // recurses through neither parse_sequence nor parse_primary, so
          // uncounted a chain of assignments is bounded by nothing but this host's
          // own stack -- `A=` forty-four thousand times terminated it with SIGSEGV
          // through the public CLI.
          enter(t.pos);
          const Leave leave_guard{this};
          auto n = make(NT::Assign, left->pos);
          n->s = t.value;
          n->l = left;
          n->r = parse_term(e->bp);        // bp, not bp + 1: right associative
          left = n;
          continue;
        }

        auto n = make(NT::Bin, t.pos);
        n->s = t.value;
        n->l = left;
        n->r = parse_term(e->bp);
        left = n;
        continue;
      }

      if (e->assoc == 'N') {
        // Deliberately non-associative, and the E_SYNTAX is reported at the
        // SECOND operator rather than at the first or at the expression.
        NodePtr right = parse_term(e->bp + 1);
        const Token& after = peek();
        const Infix* ae = infix_entry(after);
        if (ae != nullptr && ae->assoc == 'N') {
          fail("E_SYNTAX",
               "comparison operators do not chain — parenthesise, as in (a " + t.value +
                   " b) AND (b " + after.value + " c)",
               after.pos);
        }
        auto n = make(NT::Bin, t.pos);
        n->s = t.value;
        n->l = left;
        n->r = right;
        left = n;
        continue;
      }

      auto n = make(NT::Bin, t.pos);
      n->s = t.value;
      n->l = left;
      n->r = parse_term(e->bp + 1);
      left = n;
    }
  }

  // NOT and unary minus.
  //
  // Each is accepted only where its own binding power reaches: NOT at bp 7
  // cannot appear inside a comparison operand, which is parsed at bp 9, so
  // `a == NOT b` falls through to parse_primary -- which sees the bare
  // identifier NOT and raises E_RESERVED, the same error the transcribed parser
  // gave, by a different route. `-NOT x` is E_RESERVED for the same reason.
  //
  // This is the part that is not textbook. Folding prefix operators into
  // parse_primary, where precedence climbing usually puts them, would make
  // `NOT a == b` parse as `(NOT a) == b` and would break lim.parse-depth and
  // lim.prefix-depth-does-not-shift-parens at the same time.
  //
  // Counted, for the reason the two functions this replaced were counted:
  // a prefix operator recurses through neither parse_sequence nor
  // parse_primary, and uncounted it reached this host's own stack limit instead
  // of E_DEPTH -- `--------...1` at about twenty thousand characters segfaulted
  // the process. Entered only when a prefix operator is actually consumed, so
  // every other expression's trip point is unchanged.
  NodePtr parse_prefix(int min_bp) {
    const Token t = peek();

    if (t.type == Tok::Ident && t.value == "NOT" && min_bp <= BP_NOT) {
      next();
      enter(t.pos);
      const Leave leave_guard{this};
      auto n = make(NT::Un, t.pos);
      n->s = "NOT";
      n->l = parse_term(BP_NOT);
      return n;
    }

    if (t.type == Tok::Op && t.value == "-" && min_bp <= BP_NEG) {
      next();
      enter(t.pos);
      const Leave leave_guard{this};
      auto n = make(NT::Un, t.pos);
      n->s = "NEG";
      n->l = parse_term(BP_NEG);
      return n;
    }

    return parse_postfix();
  }

  // postfix = primary { "[" sequence "]" }
  //
  // The bracket counts a level of its own. Without it an index is the one
  // nesting door that recurses from OUTSIDE parse_primary's enter/leave -- this
  // loop is where it happens -- so it charged one level per nesting where "(",
  // "f(" and the prefix operators all charge two. Five stack frames against one
  // level of the budget is the widest ratio in the grammar, and it put a[a[...]]
  // over CPython's stack before the 200-level guard could fire: a host crash
  // through the public CLI while this host still answered. Counting the bracket
  // halves the density to 2.5 and moves the boundary from ~198 nestings to 99.
  // conformance/10-limits.selt pins both sides; spec/SPEC.md §6.4 says what each
  // nesting construct costs.
  NodePtr parse_postfix() {
    NodePtr node = parse_primary();
    while (at_op("[") || at_op(".>")) {
      if (at_op("[")) {
        const Token br = next();
        enter(br.pos);
        const Leave leave_guard{this};
        NodePtr idx = parse_sequence();
        expect_op("]");
        auto n = make(NT::Index, br.pos);
        n->l = node;
        n->r = idx;
        node = n;
      } else {
        next();
        node = parse_pipe_step(std::move(node));
      }
    }
    return node;
  }

  NodePtr parse_pipe_step(NodePtr left) {
    const Token& t = peek();
    if (t.type != Tok::Ident || t.value == "TRUE" || t.value == "FALSE" || t.value == "NULL") {
      fail("E_SYNTAX", "right-hand side of .> must be a function call or function name", t.pos);
    }
    const Token name_tok = next();
    std::vector<NodePtr> args;
    if (at_op("(")) {
      next();
      if (at_op(")")) {
        next();
      } else {
        NodePtr inner = parse_sequence();
        expect_op(")");
        if (inner->t == NT::List && !inner->grouped) args = inner->items;
        else args.push_back(inner);
      }
    }

    const Spec* spec = registry_lookup(name_tok.value);
    if (!spec) fail("E_UNKNOWN_FUNC", "unknown function " + name_tok.value, name_tok.pos);

    const int count = static_cast<int>(args.size());
    bool has_placeholder = false;
    if (!spec->binds && count >= spec->min) {
      for (std::size_t i = 0; i < args.size(); ++i) {
        if (args[i]->t == NT::Var && args[i]->s == "_" && !args[i]->grouped) {
          args[i] = left;
          has_placeholder = true;
        }
      }
    }
    if (!has_placeholder) {
      args.insert(args.begin(), std::move(left));
    }

    const int final_count = static_cast<int>(args.size());
    if (final_count < spec->min || final_count > spec->max) {
      fail("E_ARITY", spec->name + " takes " + arity_text(*spec) + ", got " + std::to_string(final_count),
           name_tok.pos);
    }
    if (spec->arity_error) {
      const std::string problem = spec->arity_error(final_count);
      if (!problem.empty()) fail("E_ARITY", problem, name_tok.pos);
    }
    auto n = make(NT::Call, name_tok.pos);
    n->s = spec->name;
    n->spec = spec;
    n->items = std::move(args);
    n->record_shape = prepare_record_shape(*n);
    return n;
  }

  NodePtr parse_primary() {
    const Token t = peek();
    enter(t.pos);
    const Leave leave_guard{this};

    if (t.type == Tok::Num) {
      next();
      // Canonicalised once, here: the literal 007 is the value 7.
      Dec d;
      dec_parse(t.value, d, t.pos);
      auto n = make(NT::Num, t.pos);
      n->s = dec_format(d);
      n->dec = std::make_shared<Dec>(std::move(d));
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
      if (t.value == "NULL") {
        next();
        return make(NT::Null, t.pos);
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
    n->record_shape = prepare_record_shape(*n);
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

}  // namespace

// Context, Args and eval_node are at sel:: scope rather than in the anonymous
// namespace above, and not by preference: Spec's `fn` is a
// `Value (*)(Args&, Context&)`, Node holds a `const Spec*`, and Node lives in
// sel_ast.hpp so that a second translation unit can walk the tree. A type in an
// anonymous namespace cannot be named across translation units, so naming Spec
// in a header names these two as well. eval_node comes with them because its
// declaration sits between them and has to be on the same side as its
// definition.
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
      : nodes_(node.items), record_shape_(node.record_shape), name_(node.s), pos_(node.pos), ctx_(ctx), vals_(node.items.size()) {}

  int count() const { return static_cast<int>(nodes_.size()); }
  const Node& node(int i) const { return *nodes_[i]; }
  const std::shared_ptr<const RecordShape>& record_shape() const { return record_shape_; }
  NodePtr node_ptr(int i) const { return nodes_[i]; }
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
    if (v.has_dec()) return v.dec_ref();
    if (v.kind() != Kind::Text) {
      fail("E_NOT_NUM",
           std::string("expected a number, got ") +
               (v.kind() == Kind::Bin ? "bin" : v.kind() == Kind::Bool ? "bool" : "none"),
           pos_of(i));
    }
    Dec d;
    if (!dec_parse(v.scalar(), d, pos_of(i))) {
      fail("E_NOT_NUM", "not a number: \"" + v.scalar() + "\"", pos_of(i));
    }
    v.set_dec(d);
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

  bool is_symbol(int i) const {
    const Node& n = *nodes_[i];
    return n.t == NT::Var && !n.grouped;
  }

 private:
  const std::vector<NodePtr>& nodes_;
  const std::shared_ptr<const RecordShape>& record_shape_;
  const std::string& name_;
  Pos pos_;
  Context& ctx_;
  std::vector<std::optional<Value>> vals_;
};

namespace {

bool compare_result(const std::string& op, int c, Pos pos) {
  if (op == "==") return c == 0;
  if (op == "!=") return c != 0;
  if (op == "<") return c < 0;
  if (op == "<=") return c <= 0;
  if (op == ">") return c > 0;
  if (op == ">=") return c >= 0;
  // Not a fallthrough. `return c >= 0` stood here and answered for every
  // operator it did not name: an operator added to the lexer and to
  // compare_ops() but forgotten here evaluated as ">=" and reported nothing,
  // which is the hidden assumption this project would rather fail than carry.
  // Unreachable today -- the parser only builds these six -- and that is the
  // point of saying so out loud.
  fail("E_SYNTAX", "unknown comparison operator " + op, pos);
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
  std::vector<Value> out;
  out.reserve(node.items.size());
  for (const auto& item : node.items) {
    Value v = eval_node(*item, ctx);
    if (v.kind() == Kind::None && v.size() > 0) {
      // Cloned, not aliased: `,` copies what it collects (§5.9), so the list it
      // builds does not share structure with the values that fed it. Two of the
      // five places anything in this file clones — js/src/eval.mjs:163,165.
      for (const auto& child : v.entries()) out.push_back(child.second.clone());
    } else {
      out.push_back(v.clone());
    }
  }
  return Value::list(std::move(out));
}

// Numeric coercion at an arbitrary position, used by the operators. Built-ins go
// through Args::dec instead, which reports against the argument's own position.
Dec as_dec(const Value& v, Pos pos) {
  const Value& src = v.scalar_source(pos);
  if (src.has_dec()) {
    return src.dec_ref();
  }
  if (src.kind() != Kind::Text) {
    fail("E_NOT_NUM",
         std::string("expected a number, got ") +
             (src.kind() == Kind::Bin ? "bin" : src.kind() == Kind::Bool ? "bool" : "none"),
         pos);
  }
  Dec d;
  if (!dec_parse(src.scalar(), d, pos)) fail("E_NOT_NUM", "not a number: \"" + src.scalar() + "\"", pos);
  src.set_dec(d);
  return d;
}

Value eval_unary(const Node& node, Context& ctx) {
  const Value v = eval_node(*node.l, ctx);
  if (node.s == "NOT") return Value::boolean(!v.as_bool(node.l->pos));
  const Dec d = as_dec(v, node.l->pos);
  return make_num(dec_negate(d));
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

  if (op == "??") {
    try {
      const Value l = eval_node(*node.l, ctx);
      if (!l.is_null()) return l;
    } catch (const SelError& e) {
      if (e.code() != "E_NO_KEY" && e.code() != "E_UNDEF_VAR") throw;
    }
    return eval_node(*node.r, ctx);
  }

  if (op == "???") {
    try {
      const Value l = eval_node(*node.l, ctx);
      if (!l.is_vacuous()) return l;
    } catch (const SelError& e) {
      if (e.code() != "E_NO_KEY" && e.code() != "E_UNDEF_VAR") throw;
    }
    return eval_node(*node.r, ctx);
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
    if (op == "+") return make_num(dec_add(a, b, node.pos));
    if (op == "-") return make_num(dec_sub(a, b, node.pos));
    if (op == "*") return make_num(dec_mul(a, b, node.pos));
    if (op == "/") return make_num(dec_div(a, b, node.pos));
    return make_num(dec_mod(a, b, node.pos));
  }

  if (op == "&") return concat(l, r, lp, rp);

  if (op == "EQL") return Value::boolean(l.eql(r, node.pos));
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
    return Value::boolean(compare_result(op.substr(1), bytes_compare(a, b), node.pos));
  }
  if (is_compare_op(Token{Tok::Op, op, {}})) {
    const Dec a = as_dec(l, lp);
    const Dec b = as_dec(r, rp);
    return Value::boolean(compare_result(op, dec_cmp(a, b), node.pos));
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
// The walk keeps only the path built so far and re-derives from the root after
// every evaluation. That is **not** a C++ workaround — every host does it, and
// js/src/eval.mjs and python/sel/eval.py say so in the same words. It is
// spec/SPEC.md §5.7: the store lands at the path in the tree as it exists once
// the right-hand side has run, so holding the container found during the walk
// would silently discard the assignment whenever that container has since been
// detached. The depth is a handful of levels; the repeated walk costs nothing
// worth measuring.
//
// (Before 0.3.0 this comment claimed the re-derivation was needed because
// `children_` is a std::vector and a pointer into it dies when the tree grows.
// That was true of the pointer and false as an explanation: the other hosts do
// the same thing for the semantic reason above. Value is a handle now, so the
// pointer half of the story has gone entirely.)
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
  // The chain was walked iteratively, which is why nothing has counted it yet:
  // `A[1][2][3]` is a chain of index nodes, not a nesting of them, so neither
  // the parser's depth nor the evaluator's ever sees it -- and the value it is
  // about to build is one level deeper than the chain is long. Uncounted, that
  // built a value deeper than clone(), dump() and eql() can walk, so the
  // assignment succeeded and reading the result back afterwards failed. The
  // position is the target's, which is what every other E_DEPTH here reports.
  if (static_cast<int>(chain.size()) + 1 > MAX_DEPTH) {
    fail("E_DEPTH", "value nested too deeply", target.pos);
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
  if (node.l->t == NT::Var) {
    const std::string& var_name = node.l->s;
    if (ctx.is_bound(var_name)) {
      fail("E_BAD_ASSIGN", var_name + " is an aggregate binder and cannot be assigned", node.l->pos);
    }
    Value value;
    if (node.s == "=") {
      value = eval_node(*node.r, ctx).clone(node.pos);
      ctx.root->set(var_name, value);
      return value;
    } else {
      Value* current = ctx.root->get(var_name);
      if (!current) fail("E_UNDEF_VAR", node.s + " needs an existing target", node.l->pos);
      const Value target_value = *current;

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
          case '+': res = dec_add(a, b, node.pos); break;
          case '-': res = dec_sub(a, b, node.pos); break;
          case '*': res = dec_mul(a, b, node.pos); break;
          case '/': res = dec_div(a, b, node.pos); break;
          default: res = dec_mod(a, b, node.pos); break;
        }
        value = make_num(std::move(res));
      }
      *current = value;
      return value;
    }
  }

  // The target is resolved first, then the right-hand side — the order the other
  // hosts use, and observable: `A[1] = COUNT(A)` sees the A that resolving the
  // target just created.
  const std::vector<std::string> path = resolve_target(*node.l, ctx);
  const std::string& key = path.back();

  Value value;
  if (node.s == "=") {
    // Cloned here, where the value is produced, and not at the store below.
    // `=` copies by value (§5.7), and the assignment *evaluates to* that copy —
    // so cloning late would return something that still aliases the right-hand
    // side, and `A[1] = A` would answer with the A the store had just mutated
    // instead of the value that was assigned. js/src/eval.mjs:262 clones in
    // exactly this position, for exactly this reason.
    value = eval_node(*node.r, ctx).clone(node.pos);
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
        case '+': res = dec_add(a, b, node.pos); break;
        case '-': res = dec_sub(a, b, node.pos); break;
        case '*': res = dec_mul(a, b, node.pos); break;
        case '/': res = dec_div(a, b, node.pos); break;
        default: res = dec_mod(a, b, node.pos); break;
      }
      value = make_num(std::move(res));
    }
  }

  // Re-derived after the right-hand side ran, which may have replaced or
  // deleted any level along the path. `value` is already an independent copy in
  // both branches — cloned above for `=`, freshly constructed for the compound
  // forms — so the store aliases nothing and cannot build a cycle.
  walk_create(ctx, path, path.size() - 1)->set(key, value);
  return value;
}

Value eval_dispatch(const Node& node, Context& ctx) {
  switch (node.t) {
    case NT::Num: {
      if (node.dec) {
        return Value::num(*node.dec);
      }
      return Value::num(node.s);
    }
    case NT::Text: return make_text(node.s);
    case NT::Bool: return Value::boolean(node.b);
    case NT::Null: return Value::null();

    case NT::Var: {
      const Value* v = ctx.lookup(node.s);
      if (!v) fail("E_UNDEF_VAR", "undefined variable " + node.s, node.pos);
      return *v;
    }

    case NT::Index: {
      const Value obj = eval_node(*node.l, ctx);
      if (node.r->t == NT::Text) {
        const Value* child = obj.get(node.r->s);
        if (!child) fail("E_NO_KEY", "no key " + quote_dump(node.r->s), node.pos);
        return *child;
      }
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

Value eval_math_plan(const MathPlan& plan, Context& ctx) {
  constexpr std::size_t MAX_STACK_SLOTS = 32;
  std::array<Dec, MAX_STACK_SLOTS> stack_pad;
  std::vector<Dec> heap_pad;
  Dec* scratchpad = nullptr;
  if (plan.scratchpad_size <= MAX_STACK_SLOTS) {
    scratchpad = stack_pad.data();
  } else {
    heap_pad.resize(plan.scratchpad_size);
    scratchpad = heap_pad.data();
  }

  for (const MathStep& step : plan.steps) {
    switch (step.op) {
      case MathOp::LoadVar: {
        const Value* v = ctx.lookup(step.name);
        if (!v) fail("E_UNDEF_VAR", "undefined variable " + step.name, step.pos);
        scratchpad[step.dst] = as_dec(*v, step.pos);
        break;
      }
      case MathOp::LoadConst: {
        scratchpad[step.dst] = step.const_val;
        break;
      }
      case MathOp::LoadLeaf: {
        Value val = eval_node(*step.leaf_node, ctx);
        scratchpad[step.dst] = as_dec(val, step.leaf_node->pos);
        break;
      }
      case MathOp::Add:
        scratchpad[step.dst] = dec_add(scratchpad[step.src1], scratchpad[step.src2], step.pos);
        break;
      case MathOp::Sub:
        scratchpad[step.dst] = dec_sub(scratchpad[step.src1], scratchpad[step.src2], step.pos);
        break;
      case MathOp::Mul:
        scratchpad[step.dst] = dec_mul(scratchpad[step.src1], scratchpad[step.src2], step.pos);
        break;
      case MathOp::Div:
        scratchpad[step.dst] = dec_div(scratchpad[step.src1], scratchpad[step.src2], step.pos);
        break;
      case MathOp::Mod:
        scratchpad[step.dst] = dec_mod(scratchpad[step.src1], scratchpad[step.src2], step.pos);
        break;
      case MathOp::Neg:
        scratchpad[step.dst] = dec_negate(scratchpad[step.src1]);
        break;
      case MathOp::Abs:
        scratchpad[step.dst] = dec_abs(scratchpad[step.src1]);
        break;
      case MathOp::Sign:
        scratchpad[step.dst] = dec_from_int(dec_sign(scratchpad[step.src1]));
        break;
      case MathOp::Ceil:
        scratchpad[step.dst] = dec_ceil(scratchpad[step.src1]);
        break;
      case MathOp::Floor:
        scratchpad[step.dst] = dec_floor(scratchpad[step.src1]);
        break;
      case MathOp::Trunc:
        scratchpad[step.dst] = dec_trunc(scratchpad[step.src1]);
        break;
      case MathOp::Round: {
        const Dec& d2 = scratchpad[step.src2];
        if (!dec_is_integer(d2)) fail("E_NOT_INT", "ROUND argument 2 must be a whole number", step.aux_pos);
        const long long n = dec_to_int(d2);
        if (n < 0) fail("E_RANGE", "ROUND argument 2 must not be negative", step.aux_pos);
        if (n > MAX_SCALE) fail("E_RANGE", "ROUND scale " + std::to_string(n) + " exceeds the maximum of " + std::to_string(MAX_SCALE), step.aux_pos);
        scratchpad[step.dst] = dec_round(scratchpad[step.src1], n, step.pos);
        break;
      }
      case MathOp::Power: {
        const Dec& d2 = scratchpad[step.src2];
        if (!dec_is_integer(d2)) fail("E_NOT_INT", "POWER argument 2 must be a whole number", step.aux_pos);
        const long long n = dec_to_int(d2);
        if (n < 0) fail("E_RANGE", "POWER argument 2 must not be negative", step.aux_pos);
        if (n > MAX_POWER) fail("E_RANGE", "POWER exponent " + std::to_string(n) + " exceeds the maximum of " + std::to_string(MAX_POWER), step.aux_pos);
        scratchpad[step.dst] = dec_power(scratchpad[step.src1], n, step.pos);
        break;
      }
      case MathOp::Min: {
        const Dec& a = scratchpad[step.src1];
        const Dec& b = scratchpad[step.src2];
        scratchpad[step.dst] = dec_cmp(b, a) < 0 ? b : a;
        break;
      }
      case MathOp::Max: {
        const Dec& a = scratchpad[step.src1];
        const Dec& b = scratchpad[step.src2];
        scratchpad[step.dst] = dec_cmp(b, a) > 0 ? b : a;
        break;
      }
    }
  }
  return make_num(std::move(scratchpad[plan.output_slot]));
}

}  // namespace

Value eval_node(const Node& node, Context& ctx) {
  if (++ctx.depth > MAX_DEPTH) {
    ctx.depth--;
    fail("E_DEPTH", "evaluation nested too deeply", node.pos);
  }
  struct Pop {
    Context* c;
    ~Pop() { c->depth--; }
  } pop{&ctx};
  if (node.math_plan) return eval_math_plan(*node.math_plan, ctx);
  return eval_dispatch(node, ctx);
}

// The operators' own numeric coercion, exported for the SEL→SQL translator.
// as_dec above is what `+` and the comparisons call, so a value this accepts is
// exactly a value SEL would go on to compute with, and the code, message and
// position of a refusal are the evaluator's rather than a paraphrase. Declared
// in sel_ast.hpp and defined at namespace scope rather than in the anonymous
// namespace above, because that caller is a second translation unit.
void require_number(const Value& v, Pos pos) { as_dec(v, pos); }

namespace {

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
  define(Spec{"COND", 3, VARIADIC, true, false, nullptr,   // odd count: spec/builtins.json
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

// Collection traversal follows Lisp's storage fast path.  The public entries()
// view remains available for callers that need keys, but the evaluator can walk
// a flat list/record by slot without allocating an Entry vector or repeating
// list keys in memory.
std::size_t collection_size(const Value& value) {
  return value.size() != 0 || value.kind() == Kind::None ? value.size() : 1;
}

const Value& collection_item(const Value& value, std::size_t index) {
  if (value.size() != 0) {
    if (const Value* slot = value.slot(index)) return *slot;
    return value.entries()[index].second;
  }
  if (index == 0 && value.kind() != Kind::None) return value;
  throw std::out_of_range("SEL collection item index");
}

std::string collection_key(const Value& value, std::size_t index) {
  if (value.size() != 0) {
    if (const auto shape = value.shape()) return shape->keys[index];
    if (value.is_list() && !value.storage().empty()) return std::to_string(index + 1);
    return value.entries()[index].first;
  }
  return "1";
}

template <typename Fn>
void for_each_collection_value(const Value& value, Fn&& fn) {
  const std::size_t count = collection_size(value);
  for (std::size_t i = 0; i < count; i++) fn(collection_item(value, i));
}

std::string upper_name(std::string name) {
  for (char& ch : name) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
  return name;
}

bool is_nested_record(const Value& value) { return value.size() > 0 && !value.is_list(); }

// nullptr means "there is no first element"; a NULL or field-less first
// element is an element like any other (spec §7.4) and comes back as one.
const Value* first_collection_item(const Value& value) {
  if (value.is_null() || (value.kind() == Kind::None && value.size() == 0)) return nullptr;
  return collection_size(value) == 0 ? nullptr : &collection_item(value, 0);
}

struct AliasPlanKey {
  std::shared_ptr<const RecordShape> source;
  std::string table;

  bool operator==(const AliasPlanKey& other) const noexcept {
    return source.get() == other.source.get() && table == other.table;
  }
};

struct AliasPlanKeyHash {
  std::size_t operator()(const AliasPlanKey& key) const noexcept {
    const std::size_t source_hash = std::hash<const RecordShape*>{}(key.source.get());
    const std::size_t table_hash = std::hash<std::string>{}(key.table);
    return source_hash ^ (table_hash + static_cast<std::size_t>(0x9e3779b9) +
                          (source_hash << 6) + (source_hash >> 2));
  }
};

struct AliasPlan {
  std::shared_ptr<const RecordShape> destination;
  bool append_lower = false;
};

// Alias rows are rebuilt for every input row, but their immutable layout is a
// function only of the source shape and table name.  Keep that layout outside
// RecordShape (which is intentionally immutable) so repeated joins do not
// recopy keys or take the shape interner mutex for every row.
std::shared_ptr<const AliasPlan> alias_plan_for(
    const std::shared_ptr<const RecordShape>& source, const std::string& table) {
  static std::mutex cache_mutex;
  static std::unordered_map<AliasPlanKey, std::shared_ptr<const AliasPlan>, AliasPlanKeyHash>
      cache;

  AliasPlanKey lookup{source, table};
  {
    std::lock_guard<std::mutex> lock(cache_mutex);
    const auto found = cache.find(lookup);
    if (found != cache.end()) return found->second;
  }

  std::vector<std::string> keys = source->keys;
  keys.push_back(table);
  std::string lower = table;
  for (char& ch : lower) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
  const bool append_lower = lower != table &&
                            source->key_map.find(lower) == source->key_map.end();
  if (append_lower) keys.push_back(lower);

  auto plan = std::make_shared<AliasPlan>();
  plan->destination = intern_record_shape(std::move(keys));
  plan->append_lower = append_lower;

  if (!cacheable_record_keys(source->keys) ||
      !cacheable_record_keys(plan->destination->keys)) return plan;
  std::lock_guard<std::mutex> lock(cache_mutex);
  if (cache.size() >= SHAPE_CACHE_ENTRIES) cache.clear();
  const auto [it, inserted] = cache.emplace(std::move(lookup), plan);
  return inserted ? std::move(plan) : it->second;
}

// `_1` and `_2` name a position, not a relation: an argument with no name is
// bound bare (spec §7.4).
bool is_positional_binder(const std::string& name) { return name == "_1" || name == "_2"; }

Value ensure_row_table_alias(const Value& row, const std::string& table) {
  if (table.empty() || is_positional_binder(table) || row.has(table)) return row;
  if (const auto old_shape = row.shape()) {
    const std::shared_ptr<const AliasPlan> plan = alias_plan_for(old_shape, table);
    std::vector<Value> storage;
    storage.reserve(plan->destination->keys.size());
    const auto& old_storage = row.storage();
    storage.insert(storage.end(), old_storage.begin(), old_storage.end());
    storage.push_back(row);
    if (plan->append_lower) storage.push_back(row);
    return Value::shaped(plan->destination, std::move(storage));
  }
  Value out = Value::none();
  for (const auto& [key, value] : row.entries()) out.set(key, value);
  out.set(table, row);
  const std::string lower = [&] {
    std::string v = table;
    for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return v;
  }();
  if (lower != table && !row.has(lower)) out.set(lower, row);
  return out;
}

Value make_null_record(const Value& sample, const std::string& table) {
  Value out = Value::none();
  if (!sample.is_null()) {
    for (const std::string& key : sample.keys()) out.set(key, Value::none());
  }
  if (!table.empty() && !is_positional_binder(table)) {
    out.set(table, Value::none());
    const std::string lower = [&] {
      std::string v = table;
      for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
      return v;
    }();
    if (lower != table) out.set(lower, Value::none());
  }
  return out;
}

Value make_joined_row(const Value& left, const Value* right, const std::string& b1,
                      const std::string& b2, const std::vector<std::string>& promoted_left,
                      const std::vector<std::string>& promoted_right,
                      const std::vector<std::string>& nested_left, const Value& null_right) {
  // Each key once, where it first occurred (spec §7.4): a carried or promoted
  // key keeps its first value, a binder key holds the row this LINK bound
  // even where an earlier LINK's `_1` or a relation joined twice carried a
  // record of the same name. That is the row the compiled projector below
  // builds from the shape (binders first, then slots); this path used to keep
  // the carried record instead, so the two paths disagreed. Value::set on a
  // key that exists replaces the value in place, keeping its position.
  Value out = Value::none();
  const auto put = [&out](const std::string& key, const Value& value) {
    if (!out.has(key)) out.set(key, value);
  };
  const auto bind = [&out](const std::string& key, const Value& value) { out.set(key, value); };
  for (const auto& [key, value] : left.entries()) {
    if (is_nested_record(value)) put(key, value);
  }

  const std::string low1 = [&] {
    std::string v = b1;
    for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return v;
  }();
  bind(b1, left);
  if (low1 != b1) bind(low1, left);
  if (b1 != "_1") bind("_1", left);

  const Value actual_right = right ? *right : null_right;
  const std::string low2 = [&] {
    std::string v = b2;
    for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return v;
  }();
  bind(b2, actual_right);
  if (low2 != b2) bind(low2, actual_right);
  if (b2 != "_2") bind("_2", actual_right);

  for (const std::string& key : promoted_left) {
    const Value* value = left.get(key);
    if (value) put(key, *value);
  }
  if (right) {
    for (const std::string& key : promoted_right) {
      const Value* value = right->get(key);
      if (value && !value->is_null()) put(key, *value);
    }
  }
  (void)nested_left;
  return out;
}

struct JoinAction {
  enum class Kind { Left, Right, LeftSlot, RightSlot, LeftKey, RightKey, None } kind = Kind::None;
  std::size_t slot = 0;
  std::string key;
  const RecordShape* source_shape = nullptr;
};

struct JoinProjector {
  std::shared_ptr<const RecordShape> shape;
  std::vector<JoinAction> actions;
  std::vector<std::string> promoted_left;
  std::vector<std::string> promoted_right;
  std::vector<std::string> nested_left;
  std::string b1;
  std::string b2;
  Value null_right;

  Value operator()(const Value& left, const Value* right) const {
    if (!right) {
      return make_joined_row(left, nullptr, b1, b2, promoted_left, promoted_right,
                             nested_left, null_right);
    }
    Value out = Internals::shaped_direct(shape, actions.size());
    auto& slots = Internals::storage(out);
    const RecordShape* left_shape = left.shape().get();
    const RecordShape* right_shape = right->shape().get();
    static const Value none_val = Value::none();
    const auto append = [&slots](const Value* value) {
      slots.push_back(value ? *value : none_val);
    };
    for (std::size_t i = 0; i < actions.size(); i++) {
      const JoinAction& action = actions[i];
      switch (action.kind) {
        case JoinAction::Kind::Left: slots.push_back(left); break;
        case JoinAction::Kind::Right: slots.push_back(*right); break;
        case JoinAction::Kind::LeftSlot: {
          const Value* value = action.source_shape == left_shape
                                   ? left.slot(action.slot)
                                   : left.get(action.key);
          append(value);
          break;
        }
        case JoinAction::Kind::RightSlot: {
          const Value* value = action.source_shape == right_shape
                                   ? right->slot(action.slot)
                                   : right->get(action.key);
          append(value);
          break;
        }
        case JoinAction::Kind::LeftKey: {
          const Value* value = left.get(action.key);
          append(value);
          break;
        }
        case JoinAction::Kind::RightKey: {
          const Value* value = right->get(action.key);
          append(value);
          break;
        }
        case JoinAction::Kind::None: slots.push_back(none_val); break;
      }
    }
    return out;
  }
};

std::size_t entry_slot(const Value& value, const std::string& key) {
  if (const auto shape = value.shape()) {
    const auto found = shape->key_map.find(key);
    if (found != shape->key_map.end()) return found->second;
    return shape->keys.size();
  }
  const auto& entries = value.entries();
  for (std::size_t i = 0; i < entries.size(); i++) {
    if (entries[i].first == key) return i;
  }
  return entries.size();
}

JoinProjector make_join_projector(const Value* sample_left, const Value* sample_right,
                                  const std::string& b1, const std::string& b2,
                                  std::vector<std::string> promoted_left,
                                  std::vector<std::string> promoted_right,
                                  std::vector<std::string> nested_left, Value null_right) {
  JoinProjector projector;
  projector.b1 = b1;
  projector.b2 = b2;
  projector.promoted_left = std::move(promoted_left);
  projector.promoted_right = std::move(promoted_right);
  projector.nested_left = std::move(nested_left);
  projector.null_right = std::move(null_right);
  if (!sample_left || !sample_right) return projector;

  const Value sample = make_joined_row(*sample_left, sample_right, b1, b2,
                                       projector.promoted_left, projector.promoted_right,
                                       projector.nested_left, projector.null_right);
  std::vector<std::string> keys = sample.keys();
  projector.shape = intern_record_shape(std::move(keys));
  const std::set<std::string> left_aliases{b1, [&] {
    std::string v = b1;
    for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return v;
  }(), "_1"};
  const std::set<std::string> right_aliases{b2, [&] {
    std::string v = b2;
    for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return v;
  }(), "_2"};
  projector.actions.reserve(projector.shape->keys.size());
  for (const std::string& key : projector.shape->keys) {
    JoinAction action;
    if (left_aliases.count(key)) {
      action.kind = JoinAction::Kind::Left;
    } else if (right_aliases.count(key)) {
      action.kind = JoinAction::Kind::Right;
    } else if (std::find(projector.nested_left.begin(), projector.nested_left.end(), key) !=
                   projector.nested_left.end() ||
               std::find(projector.promoted_left.begin(), projector.promoted_left.end(), key) !=
                   projector.promoted_left.end()) {
      const std::size_t slot = entry_slot(*sample_left, key);
      if (sample_left->shape() && slot < sample_left->size()) {
        action.kind = JoinAction::Kind::LeftSlot;
        action.slot = slot;
        action.source_shape = sample_left->shape().get();
        action.key = key;
      } else {
        action.kind = JoinAction::Kind::LeftKey;
        action.key = key;
      }
    } else if (std::find(projector.promoted_right.begin(), projector.promoted_right.end(), key) !=
               projector.promoted_right.end()) {
      const std::size_t slot = entry_slot(*sample_right, key);
      if (sample_right->shape() && slot < sample_right->size()) {
        action.kind = JoinAction::Kind::RightSlot;
        action.slot = slot;
        action.source_shape = sample_right->shape().get();
        action.key = key;
      } else {
        action.kind = JoinAction::Kind::RightKey;
        action.key = key;
      }
    }
    projector.actions.push_back(std::move(action));
  }
  return projector;
}

bool expr_depends_only(const Node& node, const std::set<std::string>& allowed) {
  switch (node.t) {
    case NT::Var: return allowed.count(upper_name(node.s)) != 0;
    case NT::Index: return expr_depends_only(*node.l, allowed) && expr_depends_only(*node.r, allowed);
    case NT::Call:
    case NT::Seq:
    case NT::List:
      for (const NodePtr& item : node.items) {
        if (!expr_depends_only(*item, allowed)) return false;
      }
      return true;
    case NT::Un: return expr_depends_only(*node.l, allowed);
    case NT::Bin:
    case NT::Assign: return expr_depends_only(*node.l, allowed) && expr_depends_only(*node.r, allowed);
    default: return true;
  }
}

bool node_contains_var(const Node& node, std::string_view wanted) {
  if (node.t == NT::Var && upper_name(node.s) == upper_name(std::string(wanted))) return true;
  if (node.l && node_contains_var(*node.l, wanted)) return true;
  if (node.r && node_contains_var(*node.r, wanted)) return true;
  for (const NodePtr& item : node.items) {
    if (item && node_contains_var(*item, wanted)) return true;
  }
  return false;
}

struct JoinEqui {
  NodePtr left;
  NodePtr right;
  bool numeric = false;
};

std::optional<JoinEqui> extract_join_equi(const Node& node, const std::string& b1,
                                          const std::string& b2) {
  if (node.t != NT::Bin || (node.s != "==" && node.s != "$==")) return std::nullopt;
  const std::set<std::string> left{upper_name(b1), "_1", "_"};
  const std::set<std::string> right{upper_name(b2), "_2"};
  if (expr_depends_only(*node.l, left) && expr_depends_only(*node.r, right)) {
    return JoinEqui{node.l, node.r, node.s == "=="};
  }
  if (expr_depends_only(*node.r, left) && expr_depends_only(*node.l, right)) {
    return JoinEqui{node.r, node.l, node.s == "=="};
  }
  return std::nullopt;
}

struct FastJoinKey {
  enum class Type : uint8_t { Empty, Int64, SmallDec, BigDec, Text };
  Type type = Type::Empty;
  bool neg = false;
  int32_t scale = 0;
  int64_t int_val = 0;
  std::string text;

  bool operator==(const FastJoinKey& o) const noexcept {
    if (type != o.type) return false;
    switch (type) {
      case Type::Empty: return true;
      case Type::Int64: return int_val == o.int_val;
      case Type::SmallDec: return int_val == o.int_val && scale == o.scale && neg == o.neg;
      case Type::BigDec: return scale == o.scale && neg == o.neg && text == o.text;
      case Type::Text: return text == o.text;
    }
    return false;
  }
};

struct FastJoinKeyHash {
  std::size_t operator()(const FastJoinKey& k) const noexcept {
    switch (k.type) {
      case FastJoinKey::Type::Int64:
        return std::hash<int64_t>{}(k.int_val);
      case FastJoinKey::Type::SmallDec: {
        std::size_t h = std::hash<int64_t>{}(k.int_val);
        h ^= std::hash<int32_t>{}(k.scale) + 0x9e3779b9 + (h << 6) + (h >> 2);
        h ^= (k.neg ? 1 : 0);
        return h;
      }
      case FastJoinKey::Type::BigDec: {
        std::size_t h = std::hash<std::string>{}(k.text);
        h ^= std::hash<int32_t>{}(k.scale) + 0x9e3779b9 + (h << 6) + (h >> 2);
        h ^= (k.neg ? 1 : 0);
        return h;
      }
      case FastJoinKey::Type::Text:
        return std::hash<std::string>{}(k.text);
      default:
        return 0;
    }
  }
};

std::optional<FastJoinKey> make_fast_join_key(const Value& value, bool numeric) {
  if (value.is_null()) return std::nullopt;
  FastJoinKey key;
  if (numeric) {
    Dec d;
    try {
      d = as_dec(value, {});
    } catch (const SelError&) {
      return std::nullopt;
    }
    if (d.small) {
      __int128_t m = d.mantissa;
      int32_t s = d.scale;
      bool neg = d.neg;
      if (m == 0) {
        key.type = FastJoinKey::Type::Int64;
        key.int_val = 0;
        return key;
      }
      while (s > 0 && (m % 10) == 0) {
        m /= 10;
        s--;
      }
      if (s == 0 && m >= INT64_MIN && m <= INT64_MAX) {
        key.type = FastJoinKey::Type::Int64;
        key.int_val = static_cast<int64_t>(m);
        return key;
      }
      if (m >= INT64_MIN && m <= INT64_MAX) {
        key.type = FastJoinKey::Type::SmallDec;
        key.neg = neg;
        key.scale = s;
        key.int_val = static_cast<int64_t>(m);
        return key;
      }
      key.type = FastJoinKey::Type::BigDec;
      key.neg = neg;
      key.scale = s;
      key.text = dec_get_digits(d);
      return key;
    } else {
      std::string digits = dec_get_digits(d);
      int32_t s = d.scale;
      bool neg = d.neg;
      if (digits == "0") {
        key.type = FastJoinKey::Type::Int64;
        key.int_val = 0;
        return key;
      }
      while (s > 0 && !digits.empty() && digits.back() == '0') {
        digits.pop_back();
        s--;
      }
      key.type = FastJoinKey::Type::BigDec;
      key.neg = neg;
      key.scale = s;
      key.text = std::move(digits);
      return key;
    }
  }
  if (value.kind() != Kind::Text) return std::nullopt;
  key.type = FastJoinKey::Type::Text;
  key.text = value.scalar();
  return key;
}

std::string single_relation_name(const Node& node) {
  if (node.t == NT::Var) return node.s;
  if (node.t == NT::Call && !node.items.empty() && node.s != "LINK" && node.s != "LINK_LEFT") {
    return single_relation_name(*node.items.front());
  }
  return {};
}

Value do_link(Args& a, Context& ctx, bool left_join) {
  const int count = a.count();
  if (count != 3 && count != 5) {
    fail("E_ARITY", a.name() + " takes 3 or 5 arguments, got " + std::to_string(count), a.pos());
  }
  const Value left_value = a.val(0);
  const Value right_value = a.val(1);
  std::string b1 = "_1";
  std::string b2 = "_2";
  NodePtr predicate;
  if (count == 3) {
    b1 = single_relation_name(a.node(0));
    if (b1.empty()) b1 = "_1";
    b2 = single_relation_name(a.node(1));
    if (b2.empty()) b2 = "_2";
    predicate = a.node_ptr(2);
  } else {
    b1 = a.symbol(2);
    b2 = a.symbol(3);
    predicate = a.node_ptr(4);
  }
  if (left_value.is_null()) return Value::list({});

  const Value* first_left = first_collection_item(left_value);
  const Value* first_right = first_collection_item(right_value);
  const bool have_left = first_left != nullptr;
  const bool have_right = first_right != nullptr;
  if (!have_left || (!have_right && !left_join)) return Value::list({});

  const Value sample_left = have_left ? ensure_row_table_alias(*first_left, b1) : Value::none();
  const Value sample_right = have_right ? ensure_row_table_alias(*first_right, b2) : Value::none();
  const Value null_right = left_join ? make_null_record(sample_right, b2) : Value::none();

  const std::vector<std::string> left_keys = sample_left.keys();
  const std::vector<std::string> right_keys = sample_right.keys();
  std::set<std::string> right_key_set;
  std::set<std::string> left_key_set;
  for (const std::string& key : right_keys) right_key_set.insert(upper_name(key));
  for (const std::string& key : left_keys) left_key_set.insert(upper_name(key));
  std::vector<std::string> promoted_left;
  std::vector<std::string> promoted_right;
  std::vector<std::string> nested_left;
  for (const std::string& key : left_keys) {
    const Value* value = sample_left.get(key);
    if (value && is_nested_record(*value)) nested_left.push_back(key);
    if (value && !is_nested_record(*value) && !right_key_set.count(upper_name(key))) {
      promoted_left.push_back(key);
    }
  }
  for (const std::string& key : right_keys) {
    const Value* value = sample_right.get(key);
    if (value && !is_nested_record(*value) && !left_key_set.count(upper_name(key))) {
      promoted_right.push_back(key);
    }
  }

  JoinProjector projector = make_join_projector(
      have_left ? &sample_left : nullptr, have_right ? &sample_right : nullptr, b1, b2,
      std::move(promoted_left), std::move(promoted_right), std::move(nested_left), null_right);
  const std::optional<JoinEqui> equi = have_right ? extract_join_equi(*predicate, b1, b2)
                                                   : std::nullopt;
  std::vector<Value> output;

  const auto set_frame = [](std::vector<std::pair<std::string, Value>>& frame,
                            const std::string& name, const Value& value) {
    for (auto& entry : frame) {
      if (entry.first == name) entry.second = value;
    }
  };
  const auto add_frame_names = [](std::vector<std::pair<std::string, Value>>& frame,
                                  const std::string& name, const Value& value) {
    frame.emplace_back(name, value);
    const std::string lower = [&] {
      std::string v = name;
      for (char& ch : v) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
      return v;
    }();
    if (lower != name) frame.emplace_back(lower, value);
  };

  if (equi && have_right) {
    std::unordered_map<FastJoinKey, std::vector<Value>, FastJoinKeyHash> buckets;
    buckets.reserve(collection_size(right_value));
    std::vector<std::pair<std::string, Value>> frame;
    add_frame_names(frame, b2, Value::none());
    frame.emplace_back("_2", Value::none());
    ctx.frames.push_back(std::move(frame));
    try {
      for_each_collection_value(right_value, [&](const Value& item) {
        const Value row = ensure_row_table_alias(item, b2);
        set_frame(ctx.frames.back(), b2, row);
        set_frame(ctx.frames.back(), "_2", row);
        const auto join_key = make_fast_join_key(a.eval(*equi->right), equi->numeric);
        if (join_key) buckets[*join_key].push_back(row);
      });
    } catch (...) {
      ctx.frames.pop_back();
      throw;
    }
    ctx.frames.pop_back();

    frame.clear();
    add_frame_names(frame, b1, Value::none());
    frame.emplace_back("_1", Value::none());
    frame.emplace_back("_", Value::none());
    ctx.frames.push_back(std::move(frame));
    try {
      for_each_collection_value(left_value, [&](const Value& item) {
        const Value row = ensure_row_table_alias(item, b1);
        set_frame(ctx.frames.back(), b1, row);
        set_frame(ctx.frames.back(), "_1", row);
        set_frame(ctx.frames.back(), "_", row);
        const auto join_key = make_fast_join_key(a.eval(*equi->left), equi->numeric);
        auto it = join_key ? buckets.find(*join_key) : buckets.end();
        if (it != buckets.end()) {
          for (const Value& right : it->second) output.push_back(projector(row, &right));
        } else if (left_join) {
          output.push_back(projector(row, nullptr));
        }
      });
    } catch (...) {
      ctx.frames.pop_back();
      throw;
    }
    ctx.frames.pop_back();
  } else {
    std::vector<std::pair<std::string, Value>> frame;
    add_frame_names(frame, b1, Value::none());
    frame.emplace_back("_1", Value::none());
    frame.emplace_back("_", Value::none());
    add_frame_names(frame, b2, Value::none());
    frame.emplace_back("_2", Value::none());
    ctx.frames.push_back(std::move(frame));
    try {
      for_each_collection_value(left_value, [&](const Value& item) {
        const Value left = ensure_row_table_alias(item, b1);
        set_frame(ctx.frames.back(), b1, left);
        set_frame(ctx.frames.back(), "_1", left);
        set_frame(ctx.frames.back(), "_", left);
        bool matched = false;
        for_each_collection_value(right_value, [&](const Value& right_item) {
          const Value right = ensure_row_table_alias(right_item, b2);
          set_frame(ctx.frames.back(), b2, right);
          set_frame(ctx.frames.back(), "_2", right);
          if (a.eval(*predicate).as_bool(predicate->pos)) {
            matched = true;
            output.push_back(projector(left, &right));
          }
        });
        if (left_join && !matched) output.push_back(projector(left, nullptr));
      });
    } catch (...) {
      ctx.frames.pop_back();
      throw;
    }
    ctx.frames.pop_back();
  }
  return Value::list(std::move(output));
}

Value shape_record(const Value& record) {
  if (record.is_list() || record.size() == 0) return record;
  std::vector<std::string> keys;
  std::vector<Value> values;
  keys.reserve(record.size());
  values.reserve(record.size());
  for (const auto& [key, value] : record.entries()) {
    keys.push_back(key);
    values.push_back(value);
  }
  return Value::shaped(intern_record_shape(std::move(keys)), std::move(values));
}

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

  define(Spec{"LIST", 0, VARIADIC, false, false, nullptr, [](Args& a, Context&) -> Value {
                std::vector<Value> out;
                out.reserve(a.count());
                for (int i = 0; i < a.count(); i++) {
                  out.push_back(a.val(i).clone());
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"RECORD", 0, VARIADIC, false, false, nullptr,   // even count: spec/builtins.json
              [](Args& a, Context&) -> Value {
                Value rec = Value::none();
                const int n = a.count();
                for (int i = 0; i < n; i += 2) {
                  rec.set(a.text(i), a.val(i + 1).clone());
                }
                if (a.record_shape() && a.record_shape()->keys == rec.keys()) {
                  std::vector<Value> values;
                  values.reserve(rec.size());
                  for (const auto& entry : rec.entries()) values.push_back(entry.second);
                  return Value::shaped(a.record_shape(), std::move(values));
                }
                return shape_record(rec);
              }});

  define(Spec{"TAKE", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                const long long count = a.non_neg_int(1);
                if (count == 0 || val.is_null()) return Value::list({});
                const std::size_t source_size = collection_size(val);
                std::vector<Value> out;
                const std::size_t limit =
                    std::min<std::size_t>(static_cast<std::size_t>(count), source_size);
                out.reserve(limit);
                for (std::size_t i = 0; i < limit; i++) {
                  out.push_back(collection_item(val, i));
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"DROP", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                const long long count = a.non_neg_int(1);
                if (val.is_null()) return Value::list({});
                const std::size_t source_size = collection_size(val);
                if (static_cast<std::size_t>(count) >= source_size) return Value::list({});
                std::vector<Value> out;
                out.reserve(source_size - static_cast<std::size_t>(count));
                for (std::size_t i = static_cast<std::size_t>(count); i < source_size; i++) {
                  out.push_back(collection_item(val, i));
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"SELECT_COLS", 2, VARIADIC, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                if (val.is_null()) return Value::list({});
                const int col_count = a.count();
                std::vector<std::string> cols;
                cols.reserve(col_count - 1);
                for (int i = 1; i < col_count; i++) {
                  cols.push_back(a.text(i));
                }
                std::vector<Value> out;
                out.reserve(collection_size(val));
                for_each_collection_value(val, [&](const Value& row) {
                  Value new_row = Value::none();
                  for (const auto& c : cols) {
                    if (row.has(c)) {
                      new_row.set(c, *row.get(c));
                    }
                  }
                  out.push_back(std::move(new_row));
                });
                return Value::list(std::move(out));
              }});

  define(Spec{"DISTINCT", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                if (val.is_null()) return Value::list({});
                std::vector<Value> out;
                for_each_collection_value(val, [&](const Value& item) {
                  bool found = false;
                  for (const auto& existing : out) {
                    if (item.eql(existing)) {
                      found = true;
                      break;
                    }
                  }
                  if (!found) {
                    out.push_back(item);
                  }
                });
                return Value::list(std::move(out));
              }});

  define(Spec{"DEDUPE", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                if (val.is_null()) return Value::list({});
                std::unordered_map<std::uint64_t, std::vector<Value>> buckets;
                std::vector<Value> out;
                for_each_collection_value(val, [&](const Value& item) {
                  const std::uint64_t hash = item.structural_hash();
                  auto& bucket = buckets[hash];
                  bool found = false;
                  for (const Value& existing : bucket) {
                    if (item.eql(existing)) {
                      found = true;
                      break;
                    }
                  }
                  if (!found) {
                    bucket.push_back(item);
                    out.push_back(item);
                  }
                });
                return Value::list(std::move(out));
              }});

  // Three or five arguments, refused at compile time like every E_ARITY (spec
  // §7.4); the rule is spec/builtins.json's and define() installs it.
  define(Spec{"LINK", 3, 5, true, true, nullptr,
              [](Args& a, Context& ctx) -> Value { return do_link(a, ctx, false); }});
  define(Spec{"LINK_LEFT", 3, 5, true, true, nullptr,
              [](Args& a, Context& ctx) -> Value { return do_link(a, ctx, true); }});
}

// --- aggregates. These are why SEL needs no loop: each evaluates one argument
// node once per element, which is the same move IF makes, repeated.

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result.
template <typename Visitor>
std::optional<Value> walk(Args& a, Context& ctx, Visitor&& visit) {
  const bool three = a.count() == 3;
  const std::string binder = three ? a.symbol(1) : std::string("_");
  const Node& body = a.node(three ? 2 : 1);
  const bool needs_k = node_contains_var(body, "_K");

  const Value& coll = a.val(0);
  const std::size_t count = collection_size(coll);
  if (count == 0) return std::nullopt;

  std::vector<std::pair<std::string, Value>> frame;
  frame.reserve(needs_k ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k) frame.emplace_back("_K", Value::none());
  ctx.frames.push_back(std::move(frame));

  std::optional<Value> stopped;
  try {
    for (std::size_t i = 0; i < count; ++i) {
      const Value& item = collection_item(coll, i);
      ctx.frames.back()[0].second = item;
      if (needs_k) {
        ctx.frames.back()[1].second = make_text(collection_key(coll, i));
      }
      const Value r = a.eval(body);
      std::optional<Value> result = visit(r, i, item, body);
      if (result.has_value()) {
        stopped = std::move(result);
        break;
      }
    }
  } catch (...) {
    ctx.frames.pop_back();
    throw;
  }
  ctx.frames.pop_back();
  return stopped;
}

int compare_values(const Value& a, const Value& b) {
  const bool a_null = a.is_null();
  const bool b_null = b.is_null();
  if (a_null && b_null) return 0;
  if (a_null) return -1;
  if (b_null) return 1;

  const bool a_num = a.looks_numeric();
  const bool b_num = b.looks_numeric();
  if (a_num && b_num) {
    const Dec da = as_dec(a, Pos{});
    const Dec db = as_dec(b, Pos{});
    return dec_cmp(da, db);
  }

  if (a.kind() == Kind::Bool && b.kind() == Kind::Bool) {
    const int av = a.boolean_scalar() ? 1 : 0;
    const int bv = b.boolean_scalar() ? 1 : 0;
    return (av > bv) - (av < bv);
  }

  if ((a.kind() == Kind::Text || a.kind() == Kind::Bin) &&
      (b.kind() == Kind::Text || b.kind() == Kind::Bin)) {
    const std::string& as = a.as_bytes();
    const std::string& bs = b.as_bytes();
    if (as < bs) return -1;
    if (as > bs) return 1;
    return 0;
  }

  auto rank = [](const Value& v) -> int {
    if (v.is_null()) return 0;
    if (v.kind() == Kind::Bool) return 1;
    if (v.looks_numeric()) return 2;
    if (v.kind() == Kind::Text) return 3;
    if (v.kind() == Kind::Bin) return 4;
    return 5;
  };

  const int ra = rank(a);
  const int rb = rank(b);
  return (ra > rb) - (ra < rb);
}

struct SortEntry {
  Value item;
  Value key;
  std::size_t idx;
};

Value do_sort(Args& a, Context& ctx, std::optional<std::string> forced_dir) {
  const Value& val = a.val(0);
  if (val.is_null()) return Value::list({});
  const std::size_t source_size = collection_size(val);
  if (source_size == 0) return Value::list({});

  const int count = a.count();
  std::string direction;
  std::vector<SortEntry> indexed;
  indexed.reserve(source_size);

  if (count == 1) {
    direction = forced_dir.value_or("ASC");
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = collection_item(val, i);
      Value detached = item.clone();
      // The comparator only reads the key. Keep one detached tree and give the
      // output item and comparison key handles to that same immutable snapshot;
      // the old code recursively cloned the item twice.
      indexed.push_back({detached, std::move(detached), i});
    }
  } else {
    std::string binder;
    const Node* body = nullptr;

    if (count == 2) {
      binder = "_";
      body = &a.node(1);
      direction = forced_dir.value_or("ASC");
    } else if (count == 3) {
      if (forced_dir.has_value()) {
        binder = a.symbol(1);
        body = &a.node(2);
        direction = *forced_dir;
      } else if (a.node(2).t == NT::Text) {
        binder = "_";
        body = &a.node(1);
        std::string d = a.text(2);
        for (char& c : d) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        direction = d;
      } else if (a.is_symbol(1)) {
        binder = a.symbol(1);
        body = &a.node(2);
        direction = "ASC";
      } else {
        binder = "_";
        body = &a.node(1);
        std::string d = a.text(2);
        for (char& c : d) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        direction = d;
      }
    } else {  // 4
      binder = a.symbol(1);
      body = &a.node(2);
      std::string d = a.text(3);
      for (char& c : d) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
      direction = d;
    }

    if (direction != "ASC" && direction != "DESC") {
      const int pos_idx = (count == 4) ? 3 : 2;
      fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", a.pos_of(pos_idx));
    }

    const bool needs_k = node_contains_var(*body, "_K");
    std::vector<std::pair<std::string, Value>> frame;
    frame.reserve(needs_k ? 2 : 1);
    frame.emplace_back(binder, Value::none());
    if (needs_k) frame.emplace_back("_K", Value::none());
    ctx.frames.push_back(std::move(frame));

    try {
      for (std::size_t i = 0; i < source_size; i++) {
        const Value& item = collection_item(val, i);
        ctx.frames.back()[0].second = item;
        if (needs_k) {
          ctx.frames.back()[1].second = make_text(collection_key(val, i));
        }
        Value eval_key = a.eval(*body);
        indexed.push_back({item, std::move(eval_key), i});
      }
    } catch (...) {
      ctx.frames.pop_back();
      throw;
    }
    ctx.frames.pop_back();
  }

  const bool desc = (direction == "DESC");
  std::stable_sort(indexed.begin(), indexed.end(), [desc](const SortEntry& x, const SortEntry& y) {
    int c = compare_values(x.key, y.key);
    if (desc) c = -c;
    return c < 0;
  });

  std::vector<Value> out;
  out.reserve(indexed.size());
  for (auto& x : indexed) {
    out.push_back(std::move(x.item));
  }
  return Value::list(std::move(out));
}

struct TopEntry {
  Value item;
  Value key;
  std::size_t idx = 0;
};

Value do_top(Args& a, Context& ctx, std::optional<std::string> forced_dir) {
  const Value& value = a.val(0);
  const long long limit = a.non_neg_int(a.count() - 1);
  if (limit == 0 || value.is_null()) return Value::list({});
  const std::size_t source_size = collection_size(value);
  if (source_size == 0) return Value::list({});

  const int sort_count = a.count() - 1;
  std::string binder = "_";
  const Node* body = nullptr;
  std::string direction = forced_dir.value_or("ASC");
  if (sort_count == 1) {
    binder.clear();
  } else if (sort_count == 2) {
    body = &a.node(1);
  } else if (sort_count == 3) {
    if (forced_dir.has_value()) {
      binder = a.symbol(1);
      body = &a.node(2);
    } else if (a.node(2).t == NT::Text) {
      body = &a.node(1);
      direction = upper_name(a.text(2));
    } else if (a.is_symbol(1)) {
      binder = a.symbol(1);
      body = &a.node(2);
    } else {
      body = &a.node(1);
      direction = upper_name(a.text(2));
    }
  } else if (sort_count == 4) {
    binder = a.symbol(1);
    body = &a.node(2);
    direction = upper_name(a.text(3));
  } else {
    fail("E_ARITY", a.name() + " has an invalid sort form", a.pos());
  }
  if (direction != "ASC" && direction != "DESC") {
    const int direction_index = sort_count == 4 ? 3 : 2;
    fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", a.pos_of(direction_index));
  }

  const auto compare = [&direction](const TopEntry& lhs, const TopEntry& rhs) {
    int c = compare_values(lhs.key, rhs.key);
    if (direction == "DESC") c = -c;
    if (c != 0) return c < 0;
    return lhs.idx < rhs.idx;
  };
  // `compare(a, b)` is the final output order (true means a is better/earlier).
  // The bounded heap keeps the worst retained row at its root, so its
  // predicate is deliberately the reverse direction.
  const auto worse = [&compare](const TopEntry& lhs, const TopEntry& rhs) {
    return compare(rhs, lhs);
  };
  std::vector<TopEntry> heap;
  const std::size_t k = std::min<std::size_t>(static_cast<std::size_t>(limit), source_size);

  const auto sift_up = [&heap, &worse](std::size_t index) {
    while (index > 0) {
      const std::size_t parent = (index - 1) / 2;
      if (!worse(heap[index], heap[parent])) break;
      std::swap(heap[index], heap[parent]);
      index = parent;
    }
  };
  const auto sift_down = [&heap, &worse](std::size_t index) {
    while (true) {
      const std::size_t left = index * 2 + 1;
      const std::size_t right = left + 1;
      std::size_t worst = index;
      if (left < heap.size() && worse(heap[left], heap[worst])) worst = left;
      if (right < heap.size() && worse(heap[right], heap[worst])) worst = right;
      if (worst == index) return;
      std::swap(heap[index], heap[worst]);
      index = worst;
    }
  };

  const bool needs_k = body && node_contains_var(*body, "_K");
  if (body) {
    std::vector<std::pair<std::string, Value>> frame;
    frame.reserve(needs_k ? 2 : 1);
    frame.emplace_back(binder, Value::none());
    if (needs_k) frame.emplace_back("_K", Value::none());
    ctx.frames.push_back(std::move(frame));
  }

  std::size_t index = 0;
  try {
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = collection_item(value, i);
      TopEntry candidate;
      candidate.item = item;
      candidate.idx = index;
      if (body) {
        ctx.frames.back()[0].second = item;
        if (needs_k) {
          ctx.frames.back()[1].second = make_text(collection_key(value, i));
        }
        candidate.key = a.eval(*body);
      } else {
        candidate.key = item;
      }
      if (heap.size() < k) {
        heap.push_back(std::move(candidate));
        sift_up(heap.size() - 1);
      } else if (worse(heap.front(), candidate)) {
        heap.front() = std::move(candidate);
        sift_down(0);
      }
      index++;
    }
  } catch (...) {
    if (body) ctx.frames.pop_back();
    throw;
  }
  if (body) ctx.frames.pop_back();
  std::stable_sort(heap.begin(), heap.end(), compare);
  std::vector<Value> out;
  out.reserve(heap.size());
  for (TopEntry& entry : heap) out.push_back(std::move(entry.item));
  return Value::list(std::move(out));
}

struct GroupEntry {
  Value key;
  std::string key_str;
  std::vector<Value> rows;
};

Value do_bucket(Args& a, Context& ctx) {
  const Value& val = a.val(0);
  if (val.is_null()) return Value::list({});
  const std::size_t source_size = collection_size(val);
  if (source_size == 0) return Value::list({});

  const int count = a.count();
  std::string binder = "_";
  const Node* key_node = nullptr;
  const Node* agg_node = nullptr;

  if (count == 2) {
    key_node = &a.node(1);
  } else if (count == 3) {
    key_node = &a.node(1);
    agg_node = &a.node(2);
  } else {
    binder = a.symbol(1);
    key_node = &a.node(2);
    agg_node = &a.node(3);
  }

  std::vector<GroupEntry> groups;
  // Group order is observable (the first key wins its output position), so the
  // hash table stores candidate indexes rather than replacing the vector with
  // an unordered map.  Structural equality remains the final authority for
  // collisions and preserves SEL's exact EQL semantics for keys such as 1 and
  // 1.0.
  std::unordered_map<std::uint64_t, std::vector<std::size_t>> group_buckets;
  const bool needs_k_key = node_contains_var(*key_node, "_K");
  std::vector<std::pair<std::string, Value>> frame;
  frame.reserve(needs_k_key ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k_key) frame.emplace_back("_K", Value::none());
  ctx.frames.push_back(std::move(frame));

  try {
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = collection_item(val, i);
      ctx.frames.back()[0].second = item;
      if (needs_k_key) {
        ctx.frames.back()[1].second = make_text(collection_key(val, i));
      }
      Value eval_key = a.eval(*key_node);

      // A bare bucket's key is an index key (spec §3.3): the scalar, verbatim,
      // and refused the way indexing refuses it -- never collapsed onto a string
      // that stands for every list, record or NULL. The projected spelling has
      // no map to key and groups by identity instead.
      std::string key_str;
      if (!agg_node) {
        if (eval_key.kind() == Kind::None) {
          if (eval_key.is_null()) fail("E_NULL", "value is NULL", key_node->pos);
          fail("E_NOT_TEXT", "a bucket key must be text or a number, got a list or record",
               key_node->pos);
        }
        key_str = eval_key.as_text(key_node->pos);
      }

      int found = -1;
      const std::uint64_t hash = eval_key.structural_hash();
      const auto candidates = group_buckets.find(hash);
      if (candidates != group_buckets.end()) {
        for (const std::size_t g_idx : candidates->second) {
          if (groups[g_idx].key.eql(eval_key)) {
            found = static_cast<int>(g_idx);
            break;
          }
        }
      }

      if (found >= 0) {
        groups[found].rows.push_back(item);
      } else {
        groups.push_back(GroupEntry{std::move(eval_key), std::move(key_str), {item}});
        group_buckets[hash].push_back(groups.size() - 1);
      }
    }
  } catch (...) {
    ctx.frames.pop_back();
    throw;
  }
  ctx.frames.pop_back();

  if (!agg_node) {
    Value out = Value::none();
    for (auto& g : groups) {
      out.set(g.key_str, Value::list(std::move(g.rows)));
    }
    return out;
  }

  std::vector<Value> out;
  out.reserve(groups.size());
  const bool needs_k_agg = node_contains_var(*agg_node, "_K");
  frame.clear();
  frame.reserve(needs_k_agg ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k_agg) frame.emplace_back("_K", Value::none());
  ctx.frames.push_back(std::move(frame));
  try {
    for (auto& g : groups) {
      ctx.frames.back()[0].second = Value::list(std::move(g.rows));
      if (needs_k_agg) {
        ctx.frames.back()[1].second = std::move(g.key);
      }
      out.push_back(a.eval(*agg_node));
    }
  } catch (...) {
    ctx.frames.pop_back();
    throw;
  }
  ctx.frames.pop_back();
  return Value::list(std::move(out));
}

void register_aggregates() {
  define(Spec{"ALL", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                auto s = walk(a, ctx, [](const Value& r, std::size_t, const Value&,
                                         const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) return std::nullopt;
                  return Value::boolean(false);
                });
                return s.value_or(Value::boolean(true));
              }});

  define(Spec{"ANY", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                auto s = walk(a, ctx, [](const Value& r, std::size_t, const Value&,
                                         const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) return Value::boolean(true);
                  return std::nullopt;
                });
                return s.value_or(Value::boolean(false));
              }});

  define(Spec{"MAP", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                std::vector<Value> out;
                walk(a, ctx, [&out](const Value& r, std::size_t, const Value&,
                                    const Node&) -> std::optional<Value> {
                  out.push_back(r);
                  return std::nullopt;
                });
                return Value::list(std::move(out));
              }});

  // The one aggregate that preserves keys — a filtered list should still be
  // addressable the way the original was.
  define(Spec{"FILTER", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                const Value& coll = a.val(0);
                std::vector<Value::Entry> entries;
                walk(a, ctx, [&entries, &coll](const Value& r, std::size_t idx, const Value& item,
                                               const Node& body) -> std::optional<Value> {
                  if (r.as_bool(body.pos)) {
                    entries.emplace_back(collection_key(coll, idx), item);
                  }
                  return std::nullopt;
                });
                if (entries.empty()) {
                  Value out = Value::none();
                  out.set_is_list(true);
                  return out;
                }
                return Internals::with_children(Kind::None, std::move(entries), true);
              }});

  define(Spec{"SUM", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                Dec total = DEC_ZERO;
                walk(a, ctx, [&total](const Value& r, std::size_t, const Value&,
                                      const Node& body) -> std::optional<Value> {
                  total = dec_add(total, as_dec(r, body.pos), body.pos);
                  return std::nullopt;
                });
                return make_num(total);
              }});

  // Strict, not an aggregate: its second argument is a separator, not a body.
  define(Spec{"JOIN", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string sep = a.text(1);
                std::string out;
                bool first = true;
                for_each_collection_value(a.val(0), [&](const Value& item) {
                  if (!first) out += sep;
                  out += item.as_text(a.pos_of(0));
                  first = false;
                });
                return make_text(out);
              }});

  define(Spec{"SORT", 1, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_sort(a, ctx, "ASC");
              }});

  define(Spec{"SORT_DESC", 1, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_sort(a, ctx, "DESC");
              }});

  define(Spec{"SORT_BY", 2, 4, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_sort(a, ctx, std::nullopt);
              }});

  define(Spec{"TOP", 2, 4, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_top(a, ctx, "ASC");
              }});
  define(Spec{"TOP_DESC", 2, 4, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_top(a, ctx, "DESC");
              }});
  define(Spec{"TOP_BY", 3, 5, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_top(a, ctx, std::nullopt);
              }});

  define(Spec{"BUCKET", 2, 4, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                return do_bucket(a, ctx);
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
                return make_num(dec_round(a.dec(0), n, a.pos()));
              }});
  define(Spec{"POWER", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const long long n = a.non_neg_int(1);
                if (n > MAX_POWER) {
                  fail("E_RANGE",
                       "POWER exponent " + std::to_string(n) + " exceeds the maximum of " +
                           std::to_string(MAX_POWER),
                       a.pos_of(1));
                }
                return make_num(dec_power(a.dec(0), n, a.pos()));
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

}  // namespace

// Validates and rewrites in one pass, returning source that means the same thing
// to every engine. Every host runs this, so every host compiles the same
// pattern -- and the SEL→SQL translator is a fifth caller from another
// translation unit, which is why it is declared in sel_ast.hpp and defined at
// namespace scope rather than in the anonymous namespace above.
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

namespace {

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

void register_null() {
  define(Spec{"IS_NULL", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(a.val(0).is_null());
              }});

  define(Spec{"IS_NOT_NULL", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(!a.val(0).is_null());
              }});

  define(Spec{"COALESCE", 1, VARIADIC, true, false, nullptr, [](Args& a, Context&) -> Value {
                for (int i = 0; i < a.count(); i++) {
                  const Value v = a.val(i);
                  if (!v.is_null()) return v;
                }
                return Value::null();
              }});

  define(Spec{"GET", 2, 3, true, false, nullptr, [](Args& a, Context&) -> Value {
                const Value target = a.val(0);
                const std::string key = a.text(1);
                if (!target.is_null() && target.has(key)) {
                  const Value* v = target.get(key);
                  if (v != nullptr) return *v;
                }
                if (a.count() > 2) return a.val(2);
                return Value::null();
              }});

  define(Spec{"PATH", 2, 3, true, false, nullptr, [](Args& a, Context&) -> Value {
                const Value target = a.val(0);
                const std::string path_str = a.text(1);
                if (path_str.empty()) return target;

                std::vector<std::string> segments;
                std::size_t start = 0;
                while (true) {
                  std::size_t dot = path_str.find('.', start);
                  if (dot == std::string::npos) {
                    segments.push_back(path_str.substr(start));
                    break;
                  }
                  segments.push_back(path_str.substr(start, dot - start));
                  start = dot + 1;
                }

                Value cur = target;
                for (const auto& seg : segments) {
                  if (cur.is_null() || !cur.has(seg)) {
                    if (a.count() > 2) return a.val(2);
                    return Value::null();
                  }
                  const Value* next = cur.get(seg);
                  if (next == nullptr) {
                    if (a.count() > 2) return a.val(2);
                    return Value::null();
                  }
                  cur = *next;
                }
                return cur;
              }});

  define(Spec{"IS_BLANK", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(a.val(0).is_vacuous());
              }});

  define(Spec{"IS_PRESENT", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return Value::boolean(!a.val(0).is_vacuous());
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
  register_null();
}

// --- dependencies -----------------------------------------------------------

// The static walk of the tree, and the third thing in this host that recurses
// over it. spec/SPEC.md §6.4 caps the other two -- the parser's nesting and the
// evaluator's -- and says why: uncounted recursion over a tree the source can
// make arbitrarily deep reaches the host's own stack limit, which segfaulted
// this host and raised a host-level RangeError on JS. This walk was uncounted,
// and did both: `sel --deps` on a flat chain of about 48,000 operators died
// here with no error at all.
//
// The depth rides as a parameter rather than as a member with an RAII guard,
// because there is nothing to release on the way out -- which is also what lets
// the five hosts spell this identically. It is capped at the same MAX_DEPTH the
// evaluator uses and trips at the same node, so a program whose dependencies
// cannot be computed is exactly a program that could not have been evaluated.
void collect(const Node* node, std::set<std::string>& bound, std::set<std::string>& reads,
             std::set<std::string>& assigned, int depth) {
  if (!node) return;
  if (depth > MAX_DEPTH) fail("E_DEPTH", "expression nested too deeply", node->pos);
  switch (node->t) {
    case NT::Var:
      if (!bound.count(node->s)) reads.insert(node->s);
      return;

    case NT::Assign: {
      const Node* target = node->l.get();
      const Node* t = target;
      while (t->t == NT::Index) {
        collect(t->r.get(), bound, reads, assigned, depth + 1);
        t = t->l.get();
      }
      // `A = x` defines A; `A[k] = x` and `A += x` also read it.
      if (target->t != NT::Var || node->s != "=") {
        if (!bound.count(t->s)) reads.insert(t->s);
      }
      assigned.insert(t->s);
      collect(node->r.get(), bound, reads, assigned, depth + 1);
      return;
    }

    case NT::Call: {
      // Which arguments run inside the binder, and what they see, is decided
      // once, by binding_form() over the manifest's forms (spec/builtins.md).
      // No form -- a strict function, or a count the evaluator would refuse --
      // and every argument is read where the call stands.
      const auto form = binding_form(node->s, node->items, node->spec);
      if (!form) {
        for (const auto& arg : node->items) collect(arg.get(), bound, reads, assigned, depth + 1);
        return;
      }
      std::optional<std::set<std::string>> inner;
      for (std::size_t i = 0; i < node->items.size(); i++) {
        const auto scope = form->scopes[i];
        if (scope == sel_builtin_manifest::Scope::Binder) continue;
        if (scope == sel_builtin_manifest::Scope::Inner) {
          if (!inner) {
            inner = bound;
            for (const auto& b : form->binds) inner->insert(b);
          }
          collect(node->items[i].get(), *inner, reads, assigned, depth + 1);
        } else {
          collect(node->items[i].get(), bound, reads, assigned, depth + 1);
        }
      }
      return;
    }

    case NT::Seq:
    case NT::List:
      for (const auto& item : node->items) collect(item.get(), bound, reads, assigned, depth + 1);
      return;

    case NT::Index:
    case NT::Bin:
      collect(node->l.get(), bound, reads, assigned, depth + 1);
      collect(node->r.get(), bound, reads, assigned, depth + 1);
      return;

    case NT::Un:
      collect(node->l.get(), bound, reads, assigned, depth + 1);
      return;

    default:
      return;
  }
}

}  // namespace

const Spec* lookup_builtin(const std::string& name) {
  ensure_registered();
  return registry_lookup(name);
}

// ============================================================================
// --- AST optimizer ----------------------------------------------------------
//
// The optimizer is deliberately in this translation unit: constant folding
// must use the evaluator's exact decimal implementation, not a second C++
// numeric model.  The AST itself remains immutable to callers; every rewrite
// below starts with a shallow copy and only replaces the path it changes.

namespace {

constexpr std::string_view OPT_PIPELINE_OPS[] = {
    "FILTER", "BUCKET", "SELECT_COLS", "MAP", "DISTINCT", "DEDUPE",
    "TAKE", "DROP", "SORT", "SORT_DESC", "SORT_BY", "TOP", "TOP_DESC", "TOP_BY",
    "LINK", "LINK_LEFT"};

bool opt_pipeline_op(std::string_view name) {
  return std::find(std::begin(OPT_PIPELINE_OPS), std::end(OPT_PIPELINE_OPS), name) !=
         std::end(OPT_PIPELINE_OPS);
}

std::shared_ptr<Node> opt_copy(const NodePtr& node) {
  if (!node) return nullptr;
  auto copy = std::make_shared<Node>(*node);
  copy->l = node->l;
  copy->r = node->r;
  copy->items = node->items;
  return copy;
}

NodePtr opt_call(const std::string& name, std::vector<NodePtr> args, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Call;
  node->pos = pos;
  node->s = name;
  node->spec = registry_lookup(name);
  node->items = std::move(args);
  return node;
}

NodePtr opt_bool(bool value, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Bool;
  node->pos = pos;
  node->b = value;
  return node;
}

NodePtr opt_num(std::string value, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Num;
  node->pos = pos;
  node->s = std::move(value);
  Dec d;
  if (dec_parse(node->s, d, pos)) {
    node->dec = std::make_shared<Dec>(std::move(d));
  }
  return node;
}

// A fold that replaces a node by one of its children must not move the error
// position an operator over the result reports: spec §6.3 names the node that
// actually failed, and to the operator the operand IS the folded node, not the
// literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
// So a hoisted child is re-stamped with the folded node's position -- which is
// only exact for a leaf literal, the one shape that carries no positions of
// its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
// at its own column), so it is not a literal here.
bool opt_is_literal(const NodePtr& node) {
  return node && (node->t == NT::Num || node->t == NT::Text || node->t == NT::Bool ||
                  node->t == NT::Null);
}

NodePtr opt_hoist_literal(const NodePtr& child, Pos pos) {
  auto copy = opt_copy(child);
  copy->pos = pos;
  return copy;
}

std::pair<NodePtr, std::vector<NodePtr>> opt_unwind(const NodePtr& root) {
  std::vector<NodePtr> steps;
  NodePtr current = root;
  while (current && current->t == NT::Call && opt_pipeline_op(current->s) &&
         !current->items.empty()) {
    steps.push_back(current);
    current = current->items.front();
  }
  std::reverse(steps.begin(), steps.end());
  return {current, steps};
}

NodePtr opt_build_pipeline(NodePtr source, const std::vector<NodePtr>& steps) {
  NodePtr current = std::move(source);
  for (const NodePtr& step : steps) {
    auto next = opt_copy(step);
    next->items.clear();
    next->items.push_back(current);
    next->items.insert(next->items.end(), step->items.begin() + 1, step->items.end());
    current = std::move(next);
  }
  return current;
}

int opt_text_compare(std::string_view a, std::string_view b) {
  return bytes_compare(a, b);
}

NodePtr opt_fold(const NodePtr& node) {
  if (!node) return node;
  if (node->t == NT::Un && node->l) {
    if (node->s == "NOT" && node->l->t == NT::Bool) return opt_bool(!node->l->b, node->pos);
    if (node->s == "NEG" && node->l->t == NT::Num) {
      try {
        Dec value;
        if (dec_parse(node->l->s, value, node->pos)) {
          return opt_num(dec_format(dec_negate(value)), node->pos);
        }
      } catch (const SelError&) {
      }
    }
    return node;
  }
  if (node->t == NT::Bin && node->l && node->r) {
    const NodePtr& left = node->l;
    const NodePtr& right = node->r;
    if (node->s == "AND") {
      if (left->t == NT::Bool && !left->b) return opt_bool(false, node->pos);
      if (left->t == NT::Bool && right->t == NT::Bool) return opt_bool(left->b && right->b, node->pos);
    }
    if (node->s == "OR") {
      if (left->t == NT::Bool && left->b) return opt_bool(true, node->pos);
      if (left->t == NT::Bool && right->t == NT::Bool) return opt_bool(left->b || right->b, node->pos);
    }
    if (left->t == NT::Num && right->t == NT::Num &&
        (node->s == "+" || node->s == "-" || node->s == "*" || node->s == "/" || node->s == "%")) {
      try {
        Dec a, b;
        if (dec_parse(left->s, a, node->pos) && dec_parse(right->s, b, node->pos)) {
          Dec result;
          if (node->s == "+") result = dec_add(a, b, node->pos);
          else if (node->s == "-") result = dec_sub(a, b, node->pos);
          else if (node->s == "*") result = dec_mul(a, b, node->pos);
          else if (node->s == "/") result = dec_div(a, b, node->pos);
          else result = dec_mod(a, b, node->pos);
          return opt_num(dec_format(result), node->pos);
        }
      } catch (const SelError&) {
      }
    }
    if (left->t == NT::Num && right->t == NT::Num &&
        (node->s == "==" || node->s == "!=" || node->s == "<" || node->s == "<=" ||
         node->s == ">" || node->s == ">=")) {
      try {
        Dec a, b;
        if (dec_parse(left->s, a, node->pos) && dec_parse(right->s, b, node->pos)) {
          const int c = dec_cmp(a, b);
          const bool result = node->s == "==" ? c == 0 : node->s == "!=" ? c != 0
              : node->s == "<" ? c < 0 : node->s == "<=" ? c <= 0
              : node->s == ">" ? c > 0 : c >= 0;
          return opt_bool(result, node->pos);
        }
      } catch (const SelError&) {
      }
    }
    if (left->t == NT::Text && right->t == NT::Text &&
        (node->s == "$==" || node->s == "$!=" || node->s == "$<" || node->s == "$<=" ||
         node->s == "$>" || node->s == "$>=")) {
      const int c = opt_text_compare(left->s, right->s);
      const bool result = node->s == "$==" ? c == 0 : node->s == "$!=" ? c != 0
          : node->s == "$<" ? c < 0 : node->s == "$<=" ? c <= 0
          : node->s == "$>" ? c > 0 : c >= 0;
      return opt_bool(result, node->pos);
    }
    return node;
  }
  // items are the arguments themselves: the condition is items[0]. This arm
  // once tested for four items and never fired, which is how C++ came to
  // report the IF's position while the four hosts that folded reported the
  // branch literal's.
  if (node->t == NT::Call && node->s == "IF" && node->items.size() == 3 &&
      node->items[0]->t == NT::Bool) {
    const NodePtr& branch = node->items[node->items[0]->b ? 1 : 2];
    return opt_is_literal(branch) ? opt_hoist_literal(branch, node->pos) : node;
  }
  return node;
}

std::vector<std::string> opt_field_refs(const Node& node, std::string binder = "_") {
  std::set<std::string> refs;
  const auto walk = [&](const auto& self, const Node& item) -> void {
    if (item.t == NT::Index && item.l && item.r && item.l->t == NT::Var &&
        item.r->t == NT::Text) {
      const std::string var = upper_name(item.l->s);
      if (var == upper_name(binder) || var == "_" || var == "_1" || var == "_2") {
        refs.insert(item.r->s);
      }
    }
    if (item.l) self(self, *item.l);
    if (item.r) self(self, *item.r);
    for (const NodePtr& child : item.items) if (child) self(self, *child);
  };
  walk(walk, node);
  return {refs.begin(), refs.end()};
}

// Whether `node` reads one of `names` as a variable -- other than as
// `name["field"]`, which is a field read. Case-insensitively, like the
// evaluator's frames.
bool opt_reads_var(const Node& node, const std::vector<std::string>& names) {
  std::set<std::string> wanted;
  for (const std::string& name : names) wanted.insert(upper_name(name));
  bool found = false;
  const auto walk = [&](const auto& self, const Node& item) -> void {
    if (found) return;
    if (item.t == NT::Var && wanted.count(upper_name(item.s))) { found = true; return; }
    if (item.t == NT::Index && item.l && item.r && item.l->t == NT::Var && item.r->t == NT::Text) return;
    if (item.l) self(self, *item.l);
    if (item.r) self(self, *item.r);
    for (const NodePtr& child : item.items) if (child) self(self, *child);
  };
  walk(walk, node);
  return found;
}

// Whether a body reads the element as a whole (its binder, or any of the
// pipeline's implicit names) or its key `_K`. The field set says what a
// rewrite may rely on; this says when it may not: a body that reads either
// cannot move across a step that changes the rows' shape (MAP, SELECT_COLS)
// or renumbers them (MAP, SELECT_COLS, the sorts).
bool opt_reads_row_or_key(const Node& node, const std::string& binder = "_") {
  return opt_reads_var(node, {binder, "_", "_1", "_2", "_K"});
}

// Whether a step's own arguments (not its input) read `_K`: the keys a sort
// renumbers, so such a step keeps its place relative to one.
bool opt_step_reads_key(const Node& step) {
  for (std::size_t i = 1; i < step.items.size(); i++) {
    if (step.items[i] && opt_reads_var(*step.items[i], {"_K"})) return true;
  }
  return false;
}

// Whether the step after a FILTER hides where the FILTER ran. FILTER keeps its
// input's keys (spec §7.3) and MAP, SELECT_COLS and the sorts renumber, so a
// FILTER moved in front of one of them carries the source's keys where the
// program as written carried the step's -- visible in the answer, and in any
// later `_K`. Only a following step that renumbers again without reading `_K`
// hides that; the end of the pipeline, or another FILTER, does not.
bool opt_keys_renumbered_by(const NodePtr* step) {
  return step != nullptr && *step && (*step)->s != "FILTER" && !opt_step_reads_key(**step);
}

// Whether the source a pipeline starts from is a list already, so a FILTER
// whose predicate is a constant TRUE over it is the identity. Over a scalar
// it is not: FILTER wraps a scalar into a one-element list (spec §7.3), and
// only a later step, a list literal or a constructor is known not to be one.
bool opt_source_is_list(const NodePtr& source) {
  return source && (source->t == NT::List ||
                    (source->t == NT::Call && (source->s == "LIST" || source->s == "RECORD")));
}

// The evaluator resolves the three-argument SORT_BY / TOP_BY form by shape
// (spec §7.3): a text literal in the third slot is the direction, otherwise a
// bare name in the second slot is the binder and the third slot is its key.
// A fold that hoists a text literal into that slot -- `IF(TRUE, "DESC",
// "ASC")` -- would change the form, so the slot is walked without folding.
bool opt_step_arg_folds(const Node& step, std::size_t index) {
  const std::size_t sort_count = step.s == "SORT_BY" ? step.items.size()
      : step.s == "TOP_BY" ? step.items.size() - 1 : 0;
  return !(sort_count == 3 && index == 2 && step.items[1]->t == NT::Var && !step.items[1]->grouped);
}

struct OptMapInfo { std::string binder; NodePtr body; bool explicit_binder = false; };
struct OptFilterInfo { std::string binder; NodePtr predicate; bool explicit_binder = false; bool valid = false; };

OptMapInfo opt_map_info(const Node& step) {
  const auto& args = step.items;
  const bool explicit_binder = args.size() == 3 && args[1]->t == NT::Var && !args[1]->grouped;
  return {explicit_binder ? args[1]->s : "_",
          explicit_binder ? args[2] : args.size() > 1 ? args[1] : nullptr,
          explicit_binder};
}

OptFilterInfo opt_filter_info(const Node& step) {
  const auto& args = step.items;
  const bool explicit_binder = args.size() == 3 && args[1]->t == NT::Var && !args[1]->grouped;
  return {explicit_binder ? args[1]->s : "_",
          explicit_binder ? args[2] : args.size() > 1 ? args[1] : nullptr,
          explicit_binder, args.size() == 2 || explicit_binder};
}

std::vector<std::string> opt_map_passthroughs(const Node& step) {
  const OptMapInfo info = opt_map_info(step);
  if (!info.body || info.body->t != NT::Call ||
      info.body->s != "RECORD") return {};
  std::vector<std::string> fields;
  for (std::size_t i = 0; i + 1 < info.body->items.size(); i += 2) {
    const NodePtr& key = info.body->items[i];
    const NodePtr& value = info.body->items[i + 1];
    if (key->t == NT::Text && value->t == NT::Index && value->l && value->r &&
        value->l->t == NT::Var && value->r->t == NT::Text &&
        upper_name(value->l->s) == upper_name(info.binder) && value->r->s == key->s) {
      fields.push_back(key->s);
    }
  }
  return fields;
}

bool opt_map_has_computed(const Node& step) {
  const OptMapInfo info = opt_map_info(step);
  if (!info.body || info.body->t != NT::Call ||
      info.body->s != "RECORD") return true;
  return opt_map_passthroughs(step).size() * 2 != info.body->items.size();
}

struct OptSortInfo { std::string binder = "_"; NodePtr key; };

OptSortInfo opt_sort_info(const Node& step) {
  const auto& args = step.items;
  const std::size_t count = args.size();
  OptSortInfo info;
  if (step.s == "SORT" || step.s == "SORT_DESC") {
    if (count == 1) return info;
    info.binder = count == 3 && args[1]->t == NT::Var && !args[1]->grouped ? args[1]->s : "_";
    info.key = count == 3 ? args[2] : args[1];
  } else if (step.s == "TOP" || step.s == "TOP_DESC") {
    if (count == 2) return info;
    // TOP(source, key, n) has three arguments and TOP(source, binder, key, n)
    // has four; `sort_count` excludes n, so the explicit-binder form is 3.
    const std::size_t sort_count = count - 1;
    info.binder = sort_count == 3 && args[1]->t == NT::Var && !args[1]->grouped ? args[1]->s : "_";
    info.key = sort_count == 3 ? args[2] : args[1];
  } else if (step.s == "SORT_BY" || step.s == "TOP_BY") {
    const std::size_t sort_count = step.s == "TOP_BY" ? count - 1 : count;
    if (sort_count == 2 || (sort_count == 3 && args[2]->t == NT::Text)) {
      info.key = args[1];
    } else if (sort_count == 3 && args[1]->t == NT::Var && !args[1]->grouped) {
      info.binder = args[1]->s;
      info.key = args[2];
    } else if (count > 2 && args[1]->t == NT::Var && !args[1]->grouped) {
      info.binder = args[1]->s;
      info.key = args[2];
    }
  }
  return info;
}

std::vector<std::string> opt_select_fields(const Node& step) {
  std::vector<std::string> out;
  for (std::size_t i = 1; i < step.items.size(); i++) {
    const NodePtr& arg = step.items[i];
    if (arg->t == NT::List) {
      for (const NodePtr& item : arg->items) if (item->t == NT::Text) out.push_back(item->s);
    } else if (arg->t == NT::Text) {
      out.push_back(arg->s);
    }
  }
  return out;
}

std::optional<long long> opt_numeric_literal(const NodePtr& node) {
  if (!node || node->t != NT::Num) return std::nullopt;
  Dec value;
  try {
    if (!dec_parse(node->s, value) || value.neg || !dec_is_integer(value)) return std::nullopt;
    if (value.small) {
      if (value.scale == 0) {
        if (value.mantissa >= 0 && value.mantissa <= std::numeric_limits<long long>::max()) {
          return static_cast<long long>(value.mantissa);
        }
      }
    }
    const std::string& digits = dec_get_digits(value);
    if (digits.size() > 18) return std::nullopt;
    unsigned long long n = 0;
    for (char ch : digits) {
      if (n > (std::numeric_limits<unsigned long long>::max() - static_cast<unsigned>(ch - '0')) / 10) {
        return std::nullopt;
      }
      n = n * 10 + static_cast<unsigned>(ch - '0');
    }
    if (n > static_cast<unsigned long long>(std::numeric_limits<long long>::max())) return std::nullopt;
    return static_cast<long long>(n);
  } catch (const SelError&) {
    return std::nullopt;
  }
}

std::vector<NodePtr> opt_split_and(const NodePtr& node) {
  if (node && node->t == NT::Bin && node->s == "AND") {
    auto left = opt_split_and(node->l);
    auto right = opt_split_and(node->r);
    left.insert(left.end(), right.begin(), right.end());
    return left;
  }
  return {node};
}

NodePtr opt_combine_and(const std::vector<NodePtr>& nodes, Pos pos = {}) {
  if (nodes.empty()) return nullptr;
  NodePtr result = nodes.front();
  for (std::size_t i = 1; i < nodes.size(); i++) {
    auto node = std::make_shared<Node>();
    node->t = NT::Bin;
    node->pos = pos.line ? pos : result->pos;
    node->s = "AND";
    node->l = result;
    node->r = nodes[i];
    result = std::move(node);
  }
  return result;
}

NodePtr opt_rename_var(const NodePtr& node, const std::string& old_name, const std::string& new_name) {
  if (!node) return nullptr;
  auto copy = opt_copy(node);
  if (copy->t == NT::Var && upper_name(copy->s) == upper_name(old_name)) copy->s = new_name;
  if (copy->l) copy->l = opt_rename_var(copy->l, old_name, new_name);
  if (copy->r) copy->r = opt_rename_var(copy->r, old_name, new_name);
  for (NodePtr& item : copy->items) item = opt_rename_var(item, old_name, new_name);
  return copy;
}

std::vector<NodePtr> opt_logical_steps(const NodePtr& source, std::vector<NodePtr> current) {
  bool changed = true;
  while (changed) {
    changed = false;
    std::vector<NodePtr> next;
    for (std::size_t i = 0; i < current.size();) {
      const NodePtr& first = current[i];
      const NodePtr* second = i + 1 < current.size() ? &current[i + 1] : nullptr;
      const NodePtr* third = i + 2 < current.size() ? &current[i + 2] : nullptr;
      if (second && (*second)->s == "TAKE" && first->s == "TAKE" && first->items.size() == 2 &&
          (*second)->items.size() == 2) {
        const auto left = opt_numeric_literal(first->items[1]);
        const auto right = opt_numeric_literal((*second)->items[1]);
        if (left && right) {
          auto merged = opt_copy(first);
          merged->items = {first->items[0], opt_num(std::to_string(std::min(*left, *right)), (*second)->items[1]->pos)};
          next.push_back(std::move(merged));
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && (*second)->s == "DROP" && first->s == "DROP" && first->items.size() == 2 &&
          (*second)->items.size() == 2) {
        const auto left = opt_numeric_literal(first->items[1]);
        const auto right = opt_numeric_literal((*second)->items[1]);
        if (left && right && *left <= std::numeric_limits<long long>::max() - *right) {
          auto merged = opt_copy(first);
          merged->items = {first->items[0], opt_num(std::to_string(*left + *right), (*second)->items[1]->pos)};
          next.push_back(std::move(merged));
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && (*second)->s == "TAKE" && (*second)->items.size() == 2 &&
          (first->s == "SORT" || first->s == "SORT_DESC" || first->s == "SORT_BY")) {
        const std::string top_name = first->s == "SORT" ? "TOP" : first->s == "SORT_DESC" ? "TOP_DESC" : "TOP_BY";
        auto fused = opt_copy(first);
        fused->s = top_name;
        fused->spec = registry_lookup(top_name);
        fused->items.push_back((*second)->items[1]);
        next.push_back(std::move(fused));
        i += 2;
        changed = true;
        continue;
      }
      if (second && first->s == "MAP" && (*second)->s == "FILTER") {
        const auto passes = opt_map_passthroughs(*first);
        const OptFilterInfo info = opt_filter_info(**second);
        const auto refs = info.predicate ? opt_field_refs(*info.predicate, info.binder) : std::vector<std::string>{};
        if (info.valid && !refs.empty() && std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {
              return std::find(passes.begin(), passes.end(), f) != passes.end();
            }) && !opt_reads_row_or_key(*info.predicate, info.binder) &&
            opt_keys_renumbered_by(third)) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && (first->s == "SORT" || first->s == "SORT_DESC" || first->s == "SORT_BY") &&
          (*second)->s == "FILTER" && !opt_step_reads_key(**second) &&
          opt_keys_renumbered_by(third)) {
        next.push_back(*second);
        next.push_back(first);
        i += 2;
        changed = true;
        continue;
      }
      if (second && first->s == "SELECT_COLS" && (*second)->s == "FILTER") {
        const OptFilterInfo info = opt_filter_info(**second);
        const auto refs = info.predicate ? opt_field_refs(*info.predicate, info.binder) : std::vector<std::string>{};
        const auto fields = opt_select_fields(*first);
        if (info.valid && !refs.empty() && std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {
              return std::find(fields.begin(), fields.end(), f) != fields.end();
            }) && !opt_reads_row_or_key(*info.predicate, info.binder) &&
            opt_keys_renumbered_by(third)) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && first->s == "MAP" &&
          ((*second)->s == "TOP" || (*second)->s == "TOP_DESC" || (*second)->s == "TOP_BY" ||
           (*second)->s == "SORT" || (*second)->s == "SORT_DESC" || (*second)->s == "SORT_BY") &&
          opt_map_has_computed(*first)) {
        // Only a key over pass-through fields is the same value before the
        // MAP: a keyless sort compares the MAP's outputs, and a key that
        // reads the whole row or `_K` reads what the MAP changes.
        const OptSortInfo sort = opt_sort_info(**second);
        const auto refs = sort.key ? opt_field_refs(*sort.key, sort.binder) : std::vector<std::string>{};
        const auto passes = opt_map_passthroughs(*first);
        if (sort.key && !refs.empty() && std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {
              return std::find(passes.begin(), passes.end(), f) != passes.end();
            }) && !opt_reads_row_or_key(*sort.key, sort.binder)) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && first->s == "FILTER" && (*second)->s == "FILTER") {
        const OptFilterInfo left = opt_filter_info(*first);
        const OptFilterInfo right = opt_filter_info(**second);
        if (left.valid && right.valid) {
          const NodePtr right_pred = upper_name(left.binder) == upper_name(right.binder)
              ? right.predicate : opt_rename_var(right.predicate, right.binder, left.binder);
          NodePtr predicate = opt_combine_and({left.predicate, right_pred}, left.predicate->pos);
          auto merged = opt_copy(first);
          merged->items = left.explicit_binder
              ? std::vector<NodePtr>{first->items[0], first->items[1], predicate}
              : std::vector<NodePtr>{first->items[0], predicate};
          next.push_back(std::move(merged));
          i += 2;
          changed = true;
          continue;
        }
      }
      // No rule drops a sort followed by another sort: the sorts are stable,
      // so the first is the second's tie-breaker (rel.sort.then-sort-keeps-
      // the-tie-order), and a rule that removed it changed the value.
      if (second && (first->s == "DISTINCT" || first->s == "DEDUPE") &&
          ((*second)->s == "DISTINCT" || (*second)->s == "DEDUPE")) {
        next.push_back(first);
        i += 2;
        changed = true;
        continue;
      }
      const OptFilterInfo filter = first->s == "FILTER" ? opt_filter_info(*first) : OptFilterInfo{};
      if (filter.valid && filter.predicate && filter.predicate->t == NT::Bool && filter.predicate->b &&
          (!next.empty() || i > 0 || opt_source_is_list(source))) {
        i++;
        changed = true;
        continue;
      }
      next.push_back(first);
      i++;
    }
    current = std::move(next);
  }
  return current;
}

std::set<std::string> opt_source_names(const Node& node) {
  std::set<std::string> names;
  const auto walk = [&](const auto& self, const Node& item) -> void {
    if (item.t == NT::Var) {
      names.insert(upper_name(item.s));
    } else if (item.t == NT::Call) {
      if ((item.s == "LINK" || item.s == "LINK_LEFT") && item.items.size() >= 2) {
        self(self, *item.items[0]);
        self(self, *item.items[1]);
      } else if (opt_pipeline_op(item.s) && !item.items.empty()) {
        self(self, *item.items[0]);
      }
    }
  };
  walk(walk, node);
  return names;
}

enum class OptAffinity { Left, Right, Unknown };

OptAffinity opt_conjunct_affinity(const Node& conjunct, const std::set<std::string>& left_names,
                                  const std::set<std::string>& right_names, const std::string& binder) {
  bool has_left = false, has_right = false, ambiguous = false, unknown = false;
  const auto walk = [&](const auto& self, const Node& item) -> void {
    if (item.t == NT::Index && item.l && item.l->t == NT::Index && item.l->l &&
        item.l->l->t == NT::Var && item.l->r && item.l->r->t == NT::Text &&
        (upper_name(item.l->l->s) == upper_name(binder) || item.l->l->s == "_")) {
      const std::string table = upper_name(item.l->r->s);
      if (left_names.count(table)) has_left = true;
      else if (right_names.count(table)) has_right = true;
      else unknown = true;
      return;
    }
    if (item.t == NT::Index && item.l && item.l->t == NT::Var && item.r && item.r->t == NT::Text) {
      // `O["id"]` or `ORDERS["id"]` after the LINK: the binders are scoped to
      // the predicate (spec §7.4), so as written this is E_UNDEF_VAR, or
      // E_NO_KEY on the relation's list. Pushing it into the side it names
      // turned that error into rows (review 2026-09-15, W2) -- only
      // `_["O"]["id"]`, a read through the joined row's key, names a side.
      const std::string name = upper_name(item.l->s);
      if (name == upper_name(binder) || name == "_") ambiguous = true;
      else unknown = true;
      return;
    }
    if (item.t == NT::Var) {
      const std::string name = upper_name(item.s);
      if (name != upper_name(binder) && name != "_") unknown = true;
    }
    if (item.l) self(self, *item.l);
    if (item.r) self(self, *item.r);
    for (const NodePtr& child : item.items) if (child) self(self, *child);
  };
  walk(walk, conjunct);
  if (has_left && !has_right && !ambiguous && !unknown) return OptAffinity::Left;
  if (has_right && !has_left && !ambiguous && !unknown) return OptAffinity::Right;
  return OptAffinity::Unknown;
}

NodePtr opt_rewrite_for_relation(const NodePtr& node, const std::set<std::string>& targets,
                                 const std::string& binder) {
  if (!node) return nullptr;
  if (node->t == NT::Index && node->l && node->l->t == NT::Index && node->l->l &&
      node->l->l->t == NT::Var && node->l->r && node->l->r->t == NT::Text &&
      (upper_name(node->l->l->s) == upper_name(binder) || node->l->l->s == "_") &&
      targets.count(upper_name(node->l->r->s))) {
    auto var = std::make_shared<Node>();
    var->t = NT::Var;
    var->pos = node->l->l->pos;
    var->s = "_";
    auto out = std::make_shared<Node>(*node);
    out->l = var;
    out->r = node->r;
    out->items.clear();
    return out;
  }
  auto copy = opt_copy(node);
  if (copy->l) copy->l = opt_rewrite_for_relation(copy->l, targets, binder);
  if (copy->r) copy->r = opt_rewrite_for_relation(copy->r, targets, binder);
  for (NodePtr& child : copy->items) child = opt_rewrite_for_relation(child, targets, binder);
  return copy;
}

NodePtr opt_filter_call(const NodePtr& source, const NodePtr& predicate, Pos pos) {
  return opt_call("FILTER", {source, predicate}, pos);
}

std::pair<std::vector<NodePtr>, bool> opt_pushdown_join_filters(const std::vector<NodePtr>& steps) {
  std::vector<NodePtr> result;
  bool changed = false;
  for (std::size_t i = 0; i < steps.size();) {
    if (i + 1 >= steps.size() || (steps[i]->s != "LINK" && steps[i]->s != "LINK_LEFT") ||
        steps[i + 1]->s != "FILTER") {
      result.push_back(steps[i++]);
      continue;
    }
    const NodePtr& link = steps[i];
    const NodePtr& filter = steps[i + 1];
    const bool inner = link->s == "LINK";
    const std::string b1 = link->items.size() == 5 ? link->items[2]->s
        : (single_relation_name(*link->items[0]).empty() ? "_1" : single_relation_name(*link->items[0]));
    const std::string b2 = link->items.size() == 5 ? link->items[3]->s
        : (single_relation_name(*link->items[1]).empty() ? "_2" : single_relation_name(*link->items[1]));
    std::set<std::string> left_names{upper_name(b1), "_1"};
    std::set<std::string> right_names{upper_name(b2), "_2"};
    const auto source_left = opt_source_names(*link->items[0]);
    const auto source_right = opt_source_names(*link->items[1]);
    left_names.insert(source_left.begin(), source_left.end());
    right_names.insert(source_right.begin(), source_right.end());
    const OptFilterInfo info = opt_filter_info(*filter);
    if (!info.valid || !info.predicate) {
      result.push_back(link);
      i++;
      continue;
    }
    std::vector<NodePtr> left, right, remaining;
    for (const NodePtr& conjunct : opt_split_and(info.predicate)) {
      const OptAffinity affinity = opt_conjunct_affinity(*conjunct, left_names, right_names, info.binder);
      if (affinity == OptAffinity::Left) left.push_back(opt_rewrite_for_relation(conjunct, left_names, info.binder));
      else if (affinity == OptAffinity::Right && inner) right.push_back(opt_rewrite_for_relation(conjunct, right_names, info.binder));
      else remaining.push_back(conjunct);
    }
    if (left.empty() && right.empty()) {
      result.push_back(link);
      i++;
      continue;
    }
    if (!left.empty()) result.push_back(opt_filter_call(link->items[0], opt_combine_and(left, filter->pos), filter->pos));
    auto new_link = opt_copy(link);
    if (!right.empty()) {
      new_link->items[1] = opt_filter_call(link->items[1], opt_combine_and(right, filter->pos), filter->pos);
    }
    result.push_back(std::move(new_link));
    if (!remaining.empty()) {
      auto new_filter = opt_copy(filter);
      const NodePtr predicate = opt_combine_and(remaining, filter->pos);
      new_filter->items = info.explicit_binder
          ? std::vector<NodePtr>{filter->items[0], filter->items[1], predicate}
          : std::vector<NodePtr>{filter->items[0], predicate};
      result.push_back(std::move(new_filter));
    }
    i += 2;
    changed = true;
  }
  return {result, changed};
}

std::vector<NodePtr> opt_inmemory_steps(const NodePtr& source, std::vector<NodePtr> steps) {
  bool changed = true;
  while (changed) {
    steps = opt_logical_steps(source, std::move(steps));
    auto pushed = opt_pushdown_join_filters(steps);
    steps = std::move(pushed.first);
    changed = pushed.second;
  }
  std::vector<NodePtr> rewritten;
  rewritten.reserve(steps.size());
  for (const NodePtr& step : steps) rewritten.push_back(opt_copy(step));
  return rewritten;
}

// The vocabulary -- which source nodes compile, to which operation, with how
// many operands and which error positions -- is spec/math-ops.json's, rendered
// into sel_math_ops.hpp. The opcode ENUM is this host's own (MathOp);
// math_op_native maps the manifest's name to it, and math_ops_check refuses to
// start if the executor lacks one.
MathOp math_op_native(const std::string& name) {
  static const std::map<std::string, MathOp> table = {
    {"ADD", MathOp::Add}, {"SUB", MathOp::Sub}, {"MUL", MathOp::Mul}, {"DIV", MathOp::Div},
    {"MOD", MathOp::Mod}, {"NEG", MathOp::Neg}, {"ABS", MathOp::Abs}, {"SIGN", MathOp::Sign},
    {"CEIL", MathOp::Ceil}, {"FLOOR", MathOp::Floor}, {"TRUNC", MathOp::Trunc},
    {"ROUND", MathOp::Round}, {"POWER", MathOp::Power}, {"MIN", MathOp::Min}, {"MAX", MathOp::Max},
  };
  const auto it = table.find(name);
  if (it == table.end()) {
    throw std::logic_error("spec/math-ops.json names " + name + ", which this host's math plan has no opcode for");
  }
  return it->second;
}

void math_ops_check() {
  for (int i = 0; i < sel_math_ops::COUNT; i++) (void)math_op_native(sel_math_ops::OPS[i].name);
}

// The manifest entry for a source node, or nullptr when the node is not one
// the plan compiles.
const sel_math_ops::Op* math_op_for(const Node& node) {
  int kind;
  if (node.t == NT::Bin) kind = 1;
  else if (node.t == NT::Un) kind = 2;
  else if (node.t == NT::Call) kind = 3;
  else return nullptr;
  for (int i = 0; i < sel_math_ops::COUNT; i++) {
    const auto& op = sel_math_ops::OPS[i];
    if (op.kind == kind && node.s == op.token) return &op;
  }
  return nullptr;
}

bool is_math_op(const Node& node) { return math_op_for(node) != nullptr; }

struct EmitResult {
  uint16_t slot = 0;
  bool is_const = false;
  Dec const_val;
};

std::shared_ptr<const MathPlan> opt_compile_math_plan(const NodePtr& root) {
  if (!root || !is_math_op(*root)) return nullptr;

  auto plan = std::make_shared<MathPlan>();
  uint16_t slot_count = 0;
  const auto alloc_slot = [&slot_count]() -> uint16_t { return slot_count++; };

  const auto emit = [&](auto& self, const NodePtr& node, int depth) -> std::optional<EmitResult> {
    if (!node || depth > MAX_DEPTH) return std::nullopt;

    if (node->t == NT::Var) {
      const uint16_t slot = alloc_slot();
      MathStep step;
      step.op = MathOp::LoadVar;
      step.dst = slot;
      step.name = node->s;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{slot, false, {}};
    }

    if (node->t == NT::Num) {
      Dec dec;
      if (node->dec) {
        dec = *node->dec;
      } else if (!dec_parse(node->s, dec, node->pos)) {
        return std::nullopt;
      }
      const uint16_t slot = alloc_slot();
      MathStep step;
      step.op = MathOp::LoadConst;
      step.dst = slot;
      step.const_val = dec;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{slot, true, dec};
    }

    if (node->t == NT::Bin && (node->s == "+" || node->s == "-" || node->s == "*" || node->s == "/" || node->s == "%")) {
      if (!node->l || !node->r) return std::nullopt;
      const auto res_l = self(self, node->l, depth + 1);
      if (!res_l) return std::nullopt;
      const auto res_r = self(self, node->r, depth + 1);
      if (!res_r) return std::nullopt;

      const std::string& op = node->s;

      // Copy propagation:
      // x + 0
      if (op == "+" && res_r->is_const && dec_is_zero(res_r->const_val) && res_r->const_val.scale == 0) {
        if (node->r->t == NT::Num && !plan->steps.empty() && plan->steps.back().dst == res_r->slot) {
          plan->steps.pop_back();
        }
        return res_l;
      }
      // 0 + x
      if (op == "+" && res_l->is_const && dec_is_zero(res_l->const_val) && res_l->const_val.scale == 0) {
        return res_r;
      }
      // x - 0
      if (op == "-" && res_r->is_const && dec_is_zero(res_r->const_val) && res_r->const_val.scale == 0) {
        if (node->r->t == NT::Num && !plan->steps.empty() && plan->steps.back().dst == res_r->slot) {
          plan->steps.pop_back();
        }
        return res_l;
      }
      // x * 1
      if (op == "*" && res_r->is_const && !res_r->const_val.neg && dec_get_digits(res_r->const_val) == "1" && res_r->const_val.scale == 0) {
        if (node->r->t == NT::Num && !plan->steps.empty() && plan->steps.back().dst == res_r->slot) {
          plan->steps.pop_back();
        }
        return res_l;
      }
      // 1 * x
      if (op == "*" && res_l->is_const && !res_l->const_val.neg && dec_get_digits(res_l->const_val) == "1" && res_l->const_val.scale == 0) {
        return res_r;
      }

      const uint16_t dst = alloc_slot();
      const MathOp op_code = math_op_native(math_op_for(*node)->name);

      MathStep step;
      step.op = op_code;
      step.dst = dst;
      step.src1 = res_l->slot;
      step.src2 = res_r->slot;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{dst, false, {}};
    }

    if (node->t == NT::Un && is_math_op(*node)) {
      if (!node->l) return std::nullopt;
      const auto res_x = self(self, node->l, depth + 1);
      if (!res_x) return std::nullopt;
      const uint16_t dst = alloc_slot();
      MathStep step;
      step.op = math_op_native(math_op_for(*node)->name);
      step.dst = dst;
      step.src1 = res_x->slot;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{dst, false, {}};
    }

    // Math builtins: operand count, fold and error positions from the manifest
    // entry; a count the entry cannot serve derails the plan.
    if (node->t == NT::Call && is_math_op(*node)) {
      const auto* entry = math_op_for(*node);
      const MathOp op_code = math_op_native(entry->name);
      const auto& args = node->items;
      if (entry->arity == 1) {
        if (args.size() != 1) return std::nullopt;
        const auto res_arg = self(self, args[0], depth + 1);
        if (!res_arg) return std::nullopt;
        const uint16_t dst = alloc_slot();
        MathStep step;
        step.op = op_code;
        step.dst = dst;
        step.src1 = res_arg->slot;
        step.pos = node->pos;
        plan->steps.push_back(std::move(step));
        return EmitResult{dst, false, {}};
      }
      if (entry->arity == 2) {
        if (args.size() != 2) return std::nullopt;
        const auto res0 = self(self, args[0], depth + 1);
        if (!res0) return std::nullopt;
        const auto res1 = self(self, args[1], depth + 1);
        if (!res1) return std::nullopt;
        const uint16_t dst = alloc_slot();
        MathStep step;
        step.op = op_code;
        step.dst = dst;
        step.src1 = res0->slot;
        step.src2 = res1->slot;
        step.pos = node->pos;
        if (entry->aux >= 0) step.aux_pos = args[static_cast<std::size_t>(entry->aux)]->pos;
        plan->steps.push_back(std::move(step));
        return EmitResult{dst, false, {}};
      }
      // fold: one or more operands, combined pairwise left to right
      if (args.empty()) return std::nullopt;
      const auto res0 = self(self, args[0], depth + 1);
      if (!res0) return std::nullopt;
      uint16_t curr_slot = res0->slot;
      for (std::size_t k = 1; k < args.size(); k++) {
        const auto res_next = self(self, args[k], depth + 1);
        if (!res_next) return std::nullopt;
        const uint16_t dst = alloc_slot();
        MathStep step;
        step.op = op_code;
        step.dst = dst;
        step.src1 = curr_slot;
        step.src2 = res_next->slot;
        step.pos = node->pos;
        plan->steps.push_back(std::move(step));
        curr_slot = dst;
      }
      return EmitResult{curr_slot, false, {}};
    }

    if (node->t == NT::Bin || node->t == NT::Un) return std::nullopt;
    if (node->t == NT::Assign || node->t == NT::Seq || node->t == NT::List) return std::nullopt;
    if (node->t == NT::Call && (node->s == "IF" || node->s == "COND")) return std::nullopt;

    const uint16_t slot = alloc_slot();
    MathStep step;
    step.op = MathOp::LoadLeaf;
    step.dst = slot;
    step.leaf_node = node;
    step.pos = node->pos;
    plan->steps.push_back(std::move(step));
    return EmitResult{slot, false, {}};
  };

  const auto res = emit(emit, root, 1);
  if (!res || plan->steps.empty()) return nullptr;
  plan->output_slot = res->slot;
  plan->scratchpad_size = slot_count;
  return plan;
}

// `fold` is the other hosts' foldConstants option: off for the one slot
// whose shape the evaluator reads (opt_step_arg_folds).
NodePtr opt_tree(const NodePtr& node, bool physical, int depth, bool fold = true, bool in_math = false) {
  if (!node) return node;
  // The evaluator/SQL normaliser owns the public depth error and its source
  // position. opt_root never descends into a tree that reaches the cap; this
  // guard keeps the walk bounded should a rewrite ever deepen one.
  if (depth > MAX_DEPTH) return node;
  if (node->t == NT::Call && opt_pipeline_op(node->s) && !node->items.empty()) {
    auto [source, steps] = opt_unwind(node);
    NodePtr optimized_source = opt_tree(source, physical, depth + 1, fold, false);
    std::vector<NodePtr> optimized_steps;
    optimized_steps.reserve(steps.size());
    for (const NodePtr& step : steps) {
      auto copy = opt_copy(step);
      copy->items.clear();
      copy->items.push_back(step->items[0]);
      for (std::size_t i = 1; i < step->items.size(); i++) {
        copy->items.push_back(opt_tree(step->items[i], physical, depth + 1,
                                       fold && opt_step_arg_folds(*step, i), false));
      }
      optimized_steps.push_back(std::move(copy));
    }
    std::vector<NodePtr> final_steps = opt_logical_steps(optimized_source, std::move(optimized_steps));
    if (physical) final_steps = opt_inmemory_steps(optimized_source, std::move(final_steps));
    return opt_build_pipeline(std::move(optimized_source), final_steps);
  }

  const bool is_curr_math = is_math_op(*node);
  const bool next_in_math = is_curr_math;

  auto copy = opt_copy(node);
  if (copy->l) copy->l = opt_tree(copy->l, physical, depth + 1, fold, next_in_math);
  if (copy->r) copy->r = opt_tree(copy->r, physical, depth + 1, fold, next_in_math);
  for (NodePtr& child : copy->items) child = opt_tree(child, physical, depth + 1, fold, next_in_math);
  NodePtr folded = fold ? opt_fold(copy) : copy;
  if (physical && !in_math && is_math_op(*folded)) {
    auto plan = opt_compile_math_plan(folded);
    if (plan) {
      auto copy_with_plan = opt_copy(folded);
      copy_with_plan->math_plan = std::move(plan);
      return copy_with_plan;
    }
  }
  return folded;
}

// Whether any node of the tree lies past the evaluator's depth cap, counted
// the way the evaluator counts: the root at 1, every child one deeper, an
// assignment's target excluded (the evaluator walks it iteratively). The walk
// stops at the cap, so it is bounded however deep the tree is. It visits the
// same child slots opt_tree does: `l`, `r` and `items`.
bool opt_exceeds_depth(const Node& node, int depth) {
  if (depth > MAX_DEPTH) return true;
  const int next = depth + 1;
  for (const NodePtr& item : node.items) {
    if (item && opt_exceeds_depth(*item, next)) return true;
  }
  if (node.t != NT::Assign && node.l && opt_exceeds_depth(*node.l, next)) return true;
  if (node.r && opt_exceeds_depth(*node.r, next)) return true;
  return false;
}

// The evaluator is the depth authority (spec §6.4): a tree that reaches the
// cap is evaluated as written, so it is returned as written. Folding at the
// boundary erased the E_DEPTH the evaluator raises for a chain of 201
// additions (each of them foldable), and a rewrite that lifts a child would
// move it; not rewriting loses nothing, because such a tree either raises or
// keeps its deep part on a branch that is never evaluated.
NodePtr opt_root(const NodePtr& ast, bool physical) {
  if (ast && opt_exceeds_depth(*ast, 1)) return ast;
  return opt_tree(ast, physical, 1);
}

}  // namespace

#include "sel_optimizer.cpp"

// ============================================================================
// --- host API. See spec/SPEC.md §8.
// ============================================================================

// The physical tree run() evaluates: ast_ after the in-memory optimiser, built
// on the first run and kept, because the rewrite and the copy it makes cost
// more than evaluating a small rule does. One cell per compiled tree, shared by
// every copy of the Program; call_once makes the first run under concurrent
// callers build it exactly once. The optimiser cannot raise (a tree that
// reaches the depth cap is returned as written, and the evaluator reports
// E_DEPTH), so the retry call_once allows after a throwing build only covers
// host-level exceptions such as std::bad_alloc, which are then not cached as
// an absent tree. SQL translation never sees it, since a physical rewrite
// (join pushdown) is not something a database can be asked to run.
struct Program::Physical {
  std::once_flag once;
  NodePtr tree;
};

Program::Program(std::string source, std::shared_ptr<const Node> ast)
    : source_(std::move(source)), ast_(std::move(ast)),
      physical_(std::make_shared<Physical>()) {}

std::shared_ptr<const Node> Program::physical_ast() const {
  std::call_once(physical_->once, [this] { physical_->tree = optimize_ast(ast_); });
  return physical_->tree;
}

Value Program::run(Value& context) const {
  Context ctx(context);
  return eval_node(*physical_ast(), ctx);
}

Value Program::run() const {
  Value ctx = Value::none();
  return run(ctx);
}

std::vector<std::string> Program::dependencies() const {
  std::set<std::string> bound, reads, assigned;
  collect(ast_.get(), bound, reads, assigned, 1);
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
