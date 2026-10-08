// SEL — Simple Expression Language, C++23 implementation.
//
// One translation unit, laid out in the order docs/contributing.md prescribes and
// the other implementations follow, so a divergence found by the fuzzer lands in
// the same place in every host:
//
//     errors, utf8, decimal, value, registry, lexer, parser, eval, builtins,
//     host API
//
// spec/SPEC.md is normative. Nothing here may consult the host's own idea of
// string length, case, ordering or regex flags — those are precisely what differ
// between hosts, and every one of them has already caused a real divergence in
// this project. See the traps list in docs/contributing.md.

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
#include <atomic>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <limits>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

// The big-number multiply's inner loop in MULX/ADCX/ADOX when the CPU has them
// (checked at run time, so the build needs no -m flag); everywhere else, and on
// an x86-64 without them, the portable loop. SEL_NO_ASM turns it off.
#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__)) && !defined(SEL_NO_ASM)
#define SEL_BN_ADX 1
#include <cpuid.h>
#else
#define SEL_BN_ADX 0
#endif

namespace sel {

// ASCII case (ascii_up, ascii_upper, ...) is sel_ast.hpp's, shared with the SQL layer.
namespace {

// ============================================================================
// --- errors
// ============================================================================

// SEL whitespace (spec §2.2): space, TAB, CR, LF and nothing else -- for the
// lexer, IS_BLANK/??? and TRIM alike.
constexpr bool is_sel_space(char32_t c) { return c == U' ' || c == U'\t' || c == U'\r' || c == U'\n'; }

[[noreturn]] void fail(const char* code, const std::string& message, Pos pos = {}) {
  throw SelError(code, message, pos);
}

// An argument count the parser's arity rule (spec §6.2, from the manifest) has
// already refused: only a tree compile() did not build can reach one, which is a
// bug in whatever built it, not an E_ARITY to report at run time.
[[noreturn]] void unreachable_arity(const std::string& name) {
  throw std::logic_error(name + ": an argument count compile() refuses reached the evaluator");
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
//
// With `is_source`, an invalid sequence is reported where it starts, at the
// position (line, col, offset) counted in the code points of the valid prefix
// before it -- the same unit every other position uses (SPEC §2); a line ends at
// LF only. Otherwise the error carries `pos`, the caller's own position.
CodePoints decode_utf8(std::string_view bytes, Pos pos = {}, bool is_source = false) {
  CodePoints cps;
  auto at = [&](const CodePoints& prefix) {
    Pos p;
    p.line = 1;
    std::size_t line_start = 0;
    for (std::size_t k = 0; k < prefix.size(); k++) {
      if (prefix[k] == U'\n') { p.line++; line_start = k + 1; }
    }
    p.col = static_cast<int>(prefix.size() - line_start) + 1;
    p.offset = prefix.size();
    return p;
  };
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
                         std::to_string(i), is_source ? at(cps) : pos);
    }

    if (i + static_cast<std::size_t>(need) >= n) {
      fail("E_UTF8", "truncated sequence at byte " + std::to_string(i),
           is_source ? at(cps) : pos);
    }
    for (int k = 1; k <= need; k++) {
      const unsigned char c = static_cast<unsigned char>(bytes[i + k]);
      const unsigned char min = k == 1 ? lo : 0x80;
      const unsigned char max = k == 1 ? hi : 0xbf;
      if (c < min || c > max) {
        fail("E_UTF8", "invalid continuation byte at byte " + std::to_string(i + k),
             is_source ? at(cps) : pos);
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

std::string encode_utf8(std::span<const char32_t> cps) {
  std::string out;
  out.reserve(cps.size());
  for (char32_t c : cps) encode_cp(out, c);
  return out;
}

// A text quoted in a message (spec/errors.md, "Message conventions"): a JSON
// string literal -- the quote, the backslash and the C0 controls escaped,
// every other code point as itself. Only ASCII bytes are ever escaped, so
// this works on the UTF-8 bytes without decoding them.
std::string quote_text(std::string_view text) {
  static const char* hex = "0123456789abcdef";
  std::string out = "\"";
  for (const char ch : text) {
    const auto b = static_cast<unsigned char>(ch);
    switch (b) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      default:
        if (b < 0x20) {
          out += "\\u00";
          out.push_back(hex[b >> 4]);
          out.push_back(hex[b & 15]);
        } else {
          out.push_back(ch);
        }
    }
  }
  out.push_back('"');
  return out;
}

// The lexer's unexpected character: quoted, and named by code point when it
// is not printable ASCII, so that a no-break space or a BOM is visible.
std::string describe_char(char32_t c) {
  std::string s;
  encode_cp(s, c);
  std::string out = quote_text(s);
  if (c < 0x21 || c > 0x7e) {
    char buf[16];
    std::snprintf(buf, sizeof buf, " (U+%04X)", static_cast<unsigned>(c));
    out += buf;
  }
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
// Exact decimal arithmetic. See spec/SPEC.md §4. A small magnitude is a 128-bit
// mantissa; a larger one is binary words (the engine below, transcribed from
// rust/src/large_dec.rs), with its digit string made only when text is asked
// for. tools/check-decimal.sh holds the core to Python's decimal module, which
// is what stands between a subtle rounding difference and a wrong invoice.
//
// A decimal (struct Dec, sel.hpp) means (neg ? -1 : 1) * magnitude / 10^scale,
// the magnitude having no leading zeros. Zero is never negative. Scale is part
// of the value: 2.50 is 250 at scale 2.
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
// For sizes that are products of a count and a length (spec §6.4 caps): wide enough
// that the product cannot itself overflow before it is compared with the cap.
__extension__ typedef unsigned __int128 u128;
constexpr long long MAX_TEXT_LEN = sel_limits::MAX_TEXT_LEN;
constexpr long long MAX_COLLECTION = sel_limits::MAX_COLLECTION;
constexpr long long MAX_REGEX_PATTERN = sel_limits::MAX_REGEX_PATTERN;
constexpr long long MAX_REGEX_GROUPS = sel_limits::MAX_REGEX_GROUPS;

// Upper bounds on the arguments that name a size, from spec/SPEC.md §6.4's
// first table. These are not the same as the value caps above: they bound what
// a call may ask for, not how big the answer may be, and an argument cap alone
// left POWER's base free to step over MAX_POWER by nesting.
constexpr long long MAX_SCALE = sel_limits::MAX_ROUND_SCALE;
constexpr long long MAX_POWER = sel_limits::MAX_POWER_EXPONENT;
constexpr long long MAX_QUANTIFIER = sel_limits::MAX_REGEX_QUANTIFIER;   // PCRE2's own hard limit

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
  Max,
  // Not in the manifest: coerces a still-uncoerced load in place, for a
  // copy-propagated operand (`x + 0`, `1 * x`) that no arithmetic step would
  // otherwise ever look at.
  Coerce
};

// A plan's slots are 32-bit: a MIN or MAX fold allocates two slots per argument
// and the depth cap does not bound how many arguments there are, so 16 bits
// wrapped at 32,769 of them and the executor wrote past its scratchpad.
struct MathStep {
  MathOp op = MathOp::LoadVar;
  uint32_t dst = 0;
  uint32_t src1 = 0;
  uint32_t src2 = 0;
  // Operands that a load produced are still the values that were loaded
  // (spec §6.2, evaluate then coerce): the step coerces them, left to right,
  // when it runs, at the position of the operand's own node.
  bool raw1 = false;
  bool raw2 = false;
  Pos src1_pos;
  Pos src2_pos;
  Pos pos;
  Pos aux_pos;
  std::string name;
  Dec const_val;
  NodePtr leaf_node;
};

struct MathPlan {
  std::vector<MathStep> steps;
  uint32_t output_slot = 0;
  uint32_t scratchpad_size = 0;
};

namespace {

// --- digit-string primitives (non-negative, no leading zeros)

std::string strip(const std::string& s) {
  std::size_t i = 0;
  while (i + 1 < s.size() && s[i] == '0') i++;
  return i == 0 ? s : s.substr(i);
}

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

// --- binary magnitudes
//
// The magnitude of a large Dec: little-endian 64-bit words, no zero high word,
// and zero has no words at all. Transcribed from the Rust host's
// rust/src/large_dec.rs (same algorithms, same thresholds), whose tests hold
// them to num-bigint at every threshold; the two compiled hosts share one
// design.
//
// Arithmetic stays binary; decimal digits exist only at the edges. A numeral is
// read once (from_decimal) and a value written once (to_decimal), both divide
// and conquer, so a million digits cost a few large multiplications rather than
// a quadratic sweep. Between the edges -- products, sums, comparisons, digit
// counts, scale alignment -- the words are never converted: a digit count comes
// from the bit length (plus one comparison when a power of ten falls inside
// that bit length), and 10^k is 5^k shifted left k bits. The powers of five are
// cached per thread, each with the reciprocal that makes dividing by it two
// multiplications.
//
// Schoolbook products below KARATSUBA words and Karatsuba above, each with a
// squaring variant (a square needs about half the word products); Knuth's
// algorithm D when the divisor or the quotient is short, and Barrett reduction
// with a Newton reciprocal when both are long.
namespace bn {

using Nat = std::vector<std::uint64_t>;
using Span = std::span<const std::uint64_t>;
using Out = std::span<std::uint64_t>;

constexpr std::size_t KARATSUBA = 32;
constexpr std::size_t KARATSUBA_SQR = 48;
// Divisor and quotient both at least this long: Barrett reduction, when the
// divisor's reciprocal is cached (a power of five) ...
constexpr std::size_t BARRETT_CACHED = 160;
// ... or has to be computed for this one division.
constexpr std::size_t BARRETT_FRESH = 2560;
// Newton's recursion for a reciprocal ends in long division below this.
constexpr std::size_t RECIP_BASE = 32;
// Decimal conversion below this many words (digits) is word by word.
constexpr std::size_t DEC_LEAF_WORDS = 12;
constexpr std::size_t DEC_LEAF_DIGITS = 19 * DEC_LEAF_WORDS;
// 10^19, the largest power of ten in a word.
constexpr std::uint64_t TEN19 = 10000000000000000000ULL;
// floor(log10(2) * 2^64).
constexpr u128 LOG10_2_Q64 = 5553023288523357132ULL;
// Cached powers of five, in words, before the cache starts over.
constexpr std::size_t POW5_CACHE_WORDS = std::size_t{1} << 21;
constexpr u128 WORD_MAX = std::numeric_limits<std::uint64_t>::max();

// ---- words

inline Span trimmed(Span a) {
  std::size_t n = a.size();
  while (n > 0 && a[n - 1] == 0) --n;
  return a.first(n);
}

inline void normalize(Nat& v) {
  while (!v.empty() && v.back() == 0) v.pop_back();
}

// Orders two trimmed magnitudes.
inline int cmp(Span a, Span b) {
  if (a.size() != b.size()) return a.size() < b.size() ? -1 : 1;
  for (std::size_t i = a.size(); i-- > 0;) {
    if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return 0;
}

// acc += b, acc at least as long as b; whether a carry left acc.
inline bool add_in(Out acc, Span b) {
  std::uint64_t carry = 0;
  for (std::size_t i = 0; i < b.size(); ++i) {
    const u128 t = static_cast<u128>(acc[i]) + b[i] + carry;
    acc[i] = static_cast<std::uint64_t>(t);
    carry = static_cast<std::uint64_t>(t >> 64);
  }
  for (std::size_t i = b.size(); carry != 0 && i < acc.size(); ++i) {
    acc[i] += 1;
    carry = acc[i] == 0;
  }
  return carry != 0;
}

// acc -= b, acc at least as long as b; whether a borrow left acc.
inline bool sub_in(Out acc, Span b) {
  std::uint64_t borrow = 0;
  for (std::size_t i = 0; i < b.size(); ++i) {
    // Wrapping: a borrow leaves the high half all ones.
    const u128 t = static_cast<u128>(acc[i]) - b[i] - borrow;
    acc[i] = static_cast<std::uint64_t>(t);
    borrow = static_cast<std::uint64_t>(t >> 127);
  }
  for (std::size_t i = b.size(); borrow != 0 && i < acc.size(); ++i) {
    borrow = acc[i] == 0;
    acc[i] -= 1;
  }
  return borrow != 0;
}

Nat add(Span a, Span b) {
  const Span lng = a.size() >= b.size() ? a : b;
  const Span sht = a.size() >= b.size() ? b : a;
  Nat out;
  out.reserve(lng.size() + 1);
  out.assign(lng.begin(), lng.end());
  if (add_in(out, sht)) out.push_back(1);
  return out;
}

// a - b, for a >= b.
Nat sub(Span a, Span b) {
  Nat out(a.begin(), a.end());
  if (sub_in(out, b)) throw std::logic_error("unsigned mantissa subtraction underflow");
  normalize(out);
  return out;
}

#if SEL_BN_ADX
// Whether the CPU has MULX (BMI2) and ADCX/ADOX (ADX): CPUID leaf 7, EBX bits 8
// and 19. Read once, at load time; before that it is zero-initialised, and
// false is the portable loop, so even a product computed from another
// translation unit's static initialiser is correct.
const bool HAVE_ADX = [] {
  unsigned eax = 0, ebx = 0, ecx = 0, edx = 0;
  if (__get_cpuid_count(7, 0, &eax, &ebx, &ecx, &edx) == 0) return false;
  return (ebx & (1u << 8)) != 0 && (ebx & (1u << 19)) != 0;
}();

// acc[0..8) += a[0..8) * m + carry; the word carried out. The product's low
// words and the previous high word go in on the CF chain (ADCX), acc on the
// OF chain (ADOX): two independent carry chains, which is what makes this
// about 1.5 times the portable loop's speed. The sum fits: acc + a m + carry
// < 2^(64*9), so the carry out is one word and both chains end in it.
inline std::uint64_t addmul_8_adx(std::uint64_t* acc, const std::uint64_t* a, std::uint64_t m,
                                  std::uint64_t carry) {
  std::uint64_t l0, h0, l1, h1, z;
  __asm__(
      "xorl %k[z], %k[z]\n\t"
      "mulxq 0(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[c], %[l0]\n\t"
      "adoxq 0(%[acc]), %[l0]\n\t"
      "movq %[l0], 0(%[acc])\n\t"
      "mulxq 8(%[a]), %[l1], %[h1]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 8(%[acc]), %[l1]\n\t"
      "movq %[l1], 8(%[acc])\n\t"
      "mulxq 16(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[h1], %[l0]\n\t"
      "adoxq 16(%[acc]), %[l0]\n\t"
      "movq %[l0], 16(%[acc])\n\t"
      "mulxq 24(%[a]), %[l1], %[h1]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 24(%[acc]), %[l1]\n\t"
      "movq %[l1], 24(%[acc])\n\t"
      "mulxq 32(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[h1], %[l0]\n\t"
      "adoxq 32(%[acc]), %[l0]\n\t"
      "movq %[l0], 32(%[acc])\n\t"
      "mulxq 40(%[a]), %[l1], %[h1]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 40(%[acc]), %[l1]\n\t"
      "movq %[l1], 40(%[acc])\n\t"
      "mulxq 48(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[h1], %[l0]\n\t"
      "adoxq 48(%[acc]), %[l0]\n\t"
      "movq %[l0], 48(%[acc])\n\t"
      "mulxq 56(%[a]), %[l1], %[c]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 56(%[acc]), %[l1]\n\t"
      "movq %[l1], 56(%[acc])\n\t"
      "adcxq %[z], %[c]\n\t"
      "adoxq %[z], %[c]\n\t"
      : [l0] "=&r"(l0), [h0] "=&r"(h0), [l1] "=&r"(l1), [h1] "=&r"(h1), [z] "=&r"(z), [c] "+&r"(carry),
        "+m"(*reinterpret_cast<std::uint64_t(*)[8]>(acc))
      : [a] "r"(a), [acc] "r"(acc), "d"(m), "m"(*reinterpret_cast<const std::uint64_t(*)[8]>(a))
      : "cc");
  return carry;
}

// The same for four words.
inline std::uint64_t addmul_4_adx(std::uint64_t* acc, const std::uint64_t* a, std::uint64_t m,
                                  std::uint64_t carry) {
  std::uint64_t l0, h0, l1, h1, z;
  __asm__(
      "xorl %k[z], %k[z]\n\t"
      "mulxq 0(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[c], %[l0]\n\t"
      "adoxq 0(%[acc]), %[l0]\n\t"
      "movq %[l0], 0(%[acc])\n\t"
      "mulxq 8(%[a]), %[l1], %[h1]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 8(%[acc]), %[l1]\n\t"
      "movq %[l1], 8(%[acc])\n\t"
      "mulxq 16(%[a]), %[l0], %[h0]\n\t"
      "adcxq %[h1], %[l0]\n\t"
      "adoxq 16(%[acc]), %[l0]\n\t"
      "movq %[l0], 16(%[acc])\n\t"
      "mulxq 24(%[a]), %[l1], %[c]\n\t"
      "adcxq %[h0], %[l1]\n\t"
      "adoxq 24(%[acc]), %[l1]\n\t"
      "movq %[l1], 24(%[acc])\n\t"
      "adcxq %[z], %[c]\n\t"
      "adoxq %[z], %[c]\n\t"
      : [l0] "=&r"(l0), [h0] "=&r"(h0), [l1] "=&r"(l1), [h1] "=&r"(h1), [z] "=&r"(z), [c] "+&r"(carry),
        "+m"(*reinterpret_cast<std::uint64_t(*)[4]>(acc))
      : [a] "r"(a), [acc] "r"(acc), "d"(m), "m"(*reinterpret_cast<const std::uint64_t(*)[4]>(a))
      : "cc");
  return carry;
}
#endif

// acc[..a.size()] += a * m; the word carried out.
inline std::uint64_t addmul_1(Out acc, Span a, std::uint64_t m) {
  std::uint64_t carry = 0;
  std::size_t i = 0;
#if SEL_BN_ADX
  if (HAVE_ADX) {
    for (; i + 8 <= a.size(); i += 8) carry = addmul_8_adx(&acc[i], &a[i], m, carry);
    if (i + 4 <= a.size()) {
      carry = addmul_4_adx(&acc[i], &a[i], m, carry);
      i += 4;
    }
  }
#endif
  for (; i < a.size(); ++i) {
    const u128 t = static_cast<u128>(a[i]) * m + acc[i] + carry;
    acc[i] = static_cast<std::uint64_t>(t);
    carry = static_cast<std::uint64_t>(t >> 64);
  }
  return carry;
}

// Where Karatsuba takes over from the schoolbook rows: the thresholds above
// (the Rust host's) for the portable rows; with the ADX rows, which are about
// 1.5 times as fast, the crossover moves up to 64 words for a product and 96
// for a square (measured in-process, best of 15, from 32 to 266 words: the
// 133-word square 8% and product 10% faster than at 48 and 32).
inline std::size_t karatsuba_mul_at() {
#if SEL_BN_ADX
  if (HAVE_ADX) return 64;
#endif
  return KARATSUBA;
}

inline std::size_t karatsuba_sqr_at() {
#if SEL_BN_ADX
  if (HAVE_ADX) return 96;
#endif
  return KARATSUBA_SQR;
}

// acc[..a.size()] -= a * m; the word borrowed out.
inline std::uint64_t submul_1(Out acc, Span a, std::uint64_t m) {
  std::uint64_t borrow = 0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    const u128 t = static_cast<u128>(a[i]) * m + borrow;
    const std::uint64_t lo = static_cast<std::uint64_t>(t);
    const std::uint64_t under = acc[i] < lo;
    acc[i] -= lo;
    borrow = static_cast<std::uint64_t>(t >> 64) + under;
  }
  return borrow;
}

// a *= m; the word carried out.
inline std::uint64_t mul_1(Out a, std::uint64_t m) {
  std::uint64_t carry = 0;
  for (std::uint64_t& x : a) {
    const u128 t = static_cast<u128>(x) * m + carry;
    x = static_cast<std::uint64_t>(t);
    carry = static_cast<std::uint64_t>(t >> 64);
  }
  return carry;
}

// (hi:lo) / d and its remainder, for hi < d (so the quotient is one word). One
// divq on x86-64, where GCC would lower the u128 division to a library call;
// the portable expression elsewhere.
inline std::uint64_t div_2by1(std::uint64_t hi, std::uint64_t lo, std::uint64_t d, std::uint64_t& rem) {
#if defined(__x86_64__)
  std::uint64_t q, r;
  __asm__("divq %4" : "=a"(q), "=d"(r) : "a"(lo), "d"(hi), "rm"(d));
  rem = r;
  return q;
#else
  const u128 t = static_cast<u128>(hi) << 64 | lo;
  rem = static_cast<std::uint64_t>(t % d);
  return static_cast<std::uint64_t>(t / d);
#endif
}

// a /= d (d > 0); the remainder.
inline std::uint64_t div_1(Out a, std::uint64_t d) {
  std::uint64_t r = 0;
  for (std::size_t i = a.size(); i-- > 0;) {
    std::uint64_t next;
    a[i] = div_2by1(r, a[i], d, next);
    r = next;
  }
  return r;
}

// a mod d (d > 0).
inline std::uint64_t rem_1(Span a, std::uint64_t d) {
  std::uint64_t r = 0;
  for (std::size_t i = a.size(); i-- > 0;) {
    std::uint64_t next;
    div_2by1(r, a[i], d, next);
    r = next;
  }
  return r;
}

// a << bits for bits < 64: one word longer, not trimmed.
Nat shl_bits(Span a, unsigned bits) {
  Nat out;
  out.reserve(a.size() + 1);
  if (bits == 0) {
    out.assign(a.begin(), a.end());
    out.push_back(0);
    return out;
  }
  std::uint64_t carry = 0;
  for (const std::uint64_t x : a) {
    out.push_back(x << bits | carry);
    carry = x >> (64 - bits);
  }
  out.push_back(carry);
  return out;
}

// a >> bits for bits < 64, trimmed.
Nat shr_bits(Span a, unsigned bits) {
  Nat out(a.begin(), a.end());
  if (bits != 0) {
    for (std::size_t i = 0; i < out.size(); ++i) {
      const std::uint64_t high = i + 1 < a.size() ? a[i + 1] << (64 - bits) : 0;
      out[i] = a[i] >> bits | high;
    }
  }
  normalize(out);
  return out;
}

// a << k, trimmed.
Nat shl(Span a, std::size_t k) {
  if (a.empty()) return {};
  // One allocation: the zero words, then a shifted in place after them.
  const std::size_t words = k / 64;
  const unsigned bits = static_cast<unsigned>(k % 64);
  Nat out(words + a.size() + 1, 0);
  if (bits == 0) {
    std::copy(a.begin(), a.end(), out.begin() + static_cast<std::ptrdiff_t>(words));
  } else {
    std::uint64_t carry = 0;
    for (std::size_t i = 0; i < a.size(); ++i) {
      out[words + i] = a[i] << bits | carry;
      carry = a[i] >> (64 - bits);
    }
    out[words + a.size()] = carry;
  }
  normalize(out);
  return out;
}

// a >> k, trimmed.
Nat shr(Span a, std::size_t k) {
  if (k / 64 >= a.size()) return {};
  return shr_bits(a.subspan(k / 64), static_cast<unsigned>(k % 64));
}

// a mod 2^k, trimmed.
Nat low_bits(Span a, std::size_t k) {
  const std::size_t words = k / 64, bits = k % 64;
  Nat out(a.begin(), a.begin() + std::min(a.size(), words + 1));
  if (out.size() > words) {
    if (bits == 0) {
      out.resize(words);
    } else {
      out[words] &= (std::uint64_t{1} << bits) - 1;
    }
  }
  normalize(out);
  return out;
}

inline std::size_t bit_len(Span a) {
  return a.empty() ? 0 : a.size() * 64 - static_cast<std::size_t>(__builtin_clzll(a.back()));
}

// For a nonzero a.
inline std::size_t trailing_zero_bits(Span a) {
  std::size_t i = 0;
  while (a[i] == 0) ++i;
  return i * 64 + static_cast<std::size_t>(__builtin_ctzll(a[i]));
}

// ---- multiplication

// Karatsuba's temporaries, reused from product to product.
thread_local Nat scratch_cache;

// Words of scratch a product of `n` result words may need: each Karatsuba
// level holds under 2.5n words and hands its children at most 2n/3.
inline std::size_t scratch_len(std::size_t n) { return 8 * n + 512; }

template <class F>
void with_scratch(std::size_t len, F&& f) {
  // Taken out of the cache, so a nested product (none today) would simply
  // allocate its own. Every user zeroes the part it uses.
  Nat buf = std::move(scratch_cache);
  scratch_cache = Nat();
  if (buf.size() < len) buf.resize(len);
  struct GiveBack {
    Nat& b;
    ~GiveBack() {
      if (scratch_cache.size() < b.size()) scratch_cache = std::move(b);
    }
  } give_back{buf};
  f(Out(buf.data(), len));
}

// out = a * b by rows; out zeroed and at least a.size() + b.size() long.
void basecase_mul(Out out, Span a, Span b) {
  for (std::size_t j = 0; j < b.size(); ++j) {
    if (b[j] != 0) out[j + a.size()] = addmul_1(out.subspan(j), a, b[j]);
  }
}

// out = a^2; out zeroed and at least 2 a.size() long. Each cross product once,
// doubled, then the squares of the words on the diagonal.
void basecase_sqr(Out out, Span a) {
  const std::size_t n = a.size();
  for (std::size_t i = 0; i + 1 < n; ++i) {
    out[i + n] = addmul_1(out.subspan(2 * i + 1), a.subspan(i + 1), a[i]);
  }
  std::uint64_t top = 0;
  for (std::size_t i = 0; i < 2 * n; ++i) {
    const std::uint64_t x = out[i];
    out[i] = x << 1 | top;
    top = x >> 63;
  }
  std::uint64_t carry = 0;
  for (std::size_t i = 0; i < n; ++i) {
    const u128 sq = static_cast<u128>(a[i]) * a[i];
    const u128 lo = static_cast<u128>(out[2 * i]) + static_cast<std::uint64_t>(sq) + carry;
    out[2 * i] = static_cast<std::uint64_t>(lo);
    const u128 hi = static_cast<u128>(out[2 * i + 1]) + static_cast<std::uint64_t>(sq >> 64) +
                    static_cast<std::uint64_t>(lo >> 64);
    out[2 * i + 1] = static_cast<std::uint64_t>(hi);
    carry = static_cast<std::uint64_t>(hi >> 64);
  }
}

// d = |a - b| in d.size() == a.size() >= b.size() words; how a compares to b.
int abs_diff_into(Out d, Span a, Span b) {
  a = trimmed(a);
  b = trimmed(b);
  const int ord = cmp(a, b);
  std::fill(d.begin(), d.end(), 0);
  if (ord == 0) return 0;
  const Span big = ord > 0 ? a : b;
  const Span small = ord > 0 ? b : a;
  std::copy(big.begin(), big.end(), d.begin());
  sub_in(d, small);
  return ord;
}

void karatsuba(Out out, Span x, Span y, Out scratch);

// out = a * b. out is zeroed and one word longer than the product (Karatsuba's
// intermediate sums use it and leave it zero); `scratch` holds the rest.
void mul_into(Out out, Span a, Span b, Out scratch) {
  if (a.size() < b.size()) std::swap(a, b);
  if (b.empty()) return;
  if (b.size() < karatsuba_mul_at()) {
    basecase_mul(out, a, b);
  } else if (a.size() >= 2 * b.size()) {
    // Unbalanced: the long factor in pieces as long as the short one.
    const std::size_t n = b.size();
    const Out tmp = scratch.first(2 * n + 1);
    const Out rest = scratch.subspan(2 * n + 1);
    for (std::size_t k = 0; k * n < a.size(); ++k) {
      const Span piece = trimmed(a.subspan(k * n, std::min(n, a.size() - k * n)));
      if (piece.empty()) continue;
      const Out t = tmp.first(piece.size() + n + 1);
      std::fill(t.begin(), t.end(), 0);
      mul_into(t, piece, b, rest);
      add_in(out.subspan(k * n), trimmed(t));
    }
  } else {
    karatsuba(out, a, b, scratch);
  }
}

// x * y for y.size() <= x.size() < 2 y.size(): with x = x0 + x1 B^h and
// y = y0 + y1 B^h, xy = x0 y0 (1 + B^h) + x1 y1 (B^h + B^2h) - (x1 - x0)(y1 - y0) B^h.
void karatsuba(Out out, Span x, Span y, Out scratch) {
  const std::size_t h = y.size() / 2;
  const Span x0 = trimmed(x.first(h)), x1 = x.subspan(h);
  const Span y0 = trimmed(y.first(h)), y1 = y.subspan(h);
  const Out t0 = scratch.first(2 * h + 1);
  const Out t2 = scratch.subspan(2 * h + 1, x1.size() + y1.size() + 1);
  const Out rest = scratch.subspan(2 * h + 1 + x1.size() + y1.size() + 1);
  std::fill(t0.begin(), t0.end(), 0);
  std::fill(t2.begin(), t2.end(), 0);
  mul_into(t0, x0, y0, rest);
  mul_into(t2, x1, y1, rest);
  const Span s0 = trimmed(t0), s2 = trimmed(t2);
  std::copy(s0.begin(), s0.end(), out.begin());
  std::copy(s2.begin(), s2.end(), out.begin() + static_cast<std::ptrdiff_t>(2 * h));
  add_in(out.subspan(h), s0);
  add_in(out.subspan(h), s2);
  const Out dx = rest.first(x1.size());
  const Out dy = rest.subspan(x1.size(), y1.size());
  const int sx = abs_diff_into(dx, x1, x0);
  const int sy = abs_diff_into(dy, y1, y0);
  if (sx == 0 || sy == 0) return;
  const Span dxt = trimmed(dx), dyt = trimmed(dy);
  const Out after = rest.subspan(x1.size() + y1.size());
  const Out p = after.first(dxt.size() + dyt.size() + 1);
  std::fill(p.begin(), p.end(), 0);
  mul_into(p, dxt, dyt, after.subspan(p.size()));
  if (sx == sy) {
    sub_in(out.subspan(h), trimmed(p));
  } else {
    add_in(out.subspan(h), trimmed(p));
  }
}

// out = a^2, out zeroed and 2 a.size() + 1 long: Karatsuba's squaring,
// a^2 = a0^2 (1 + B^h) + a1^2 (B^h + B^2h) - (a1 - a0)^2 B^h.
void sqr_into(Out out, Span a, Out scratch) {
  if (a.size() < karatsuba_sqr_at()) {
    basecase_sqr(out, a);
    return;
  }
  const std::size_t h = a.size() / 2;
  const Span a0 = trimmed(a.first(h)), a1 = a.subspan(h);
  const Out t0 = scratch.first(2 * h + 1);
  const Out t2 = scratch.subspan(2 * h + 1, 2 * a1.size() + 1);
  const Out rest = scratch.subspan(2 * h + 1 + 2 * a1.size() + 1);
  std::fill(t0.begin(), t0.end(), 0);
  std::fill(t2.begin(), t2.end(), 0);
  sqr_into(t0, a0, rest);
  sqr_into(t2, a1, rest);
  const Span s0 = trimmed(t0), s2 = trimmed(t2);
  std::copy(s0.begin(), s0.end(), out.begin());
  std::copy(s2.begin(), s2.end(), out.begin() + static_cast<std::ptrdiff_t>(2 * h));
  add_in(out.subspan(h), s0);
  add_in(out.subspan(h), s2);
  const Out d = rest.first(a1.size());
  if (abs_diff_into(d, a1, a0) == 0) return;
  const Span dt = trimmed(d);
  const Out after = rest.subspan(a1.size());
  const Out p = after.first(2 * dt.size() + 1);
  std::fill(p.begin(), p.end(), 0);
  sqr_into(p, dt, after.subspan(p.size()));
  sub_in(out.subspan(h), trimmed(p));
}

// The last two squares of large operands, per thread. Squaring the same value
// twice in a row is common -- `x * x` in a test and again in the expression it
// guards, as in an escape-time loop -- and recognising the operand costs one
// pass over it where the square costs a quadratic one (or Karatsuba's).
// Bounded: operands of SQR_MEMO_MIN to SQR_MEMO_MAX words only, two of them;
// below the minimum a square costs about what the lookup and the copy would.
constexpr std::size_t SQR_MEMO_MIN = 16;
constexpr std::size_t SQR_MEMO_MAX = std::size_t{1} << 14;

struct SqrMemo {
  Nat operand;
  Nat square;
};

thread_local std::array<SqrMemo, 2> sqr_memo;
thread_local unsigned sqr_memo_next = 0;

Nat sqr(Span a) {
  a = trimmed(a);
  const bool memo = a.size() >= SQR_MEMO_MIN && a.size() <= SQR_MEMO_MAX;
  if (memo) {
    for (const SqrMemo& m : sqr_memo) {
      if (m.operand.size() == a.size() && std::equal(a.begin(), a.end(), m.operand.begin())) return m.square;
    }
  }
  const std::size_t n = 2 * a.size() + 1;
  Nat out(n, 0);
  if (a.size() < karatsuba_sqr_at()) {
    basecase_sqr(out, a);
  } else {
    with_scratch(scratch_len(n), [&](Out s) { sqr_into(out, a, s); });
  }
  normalize(out);
  if (memo) {
    SqrMemo& m = sqr_memo[sqr_memo_next];
    sqr_memo_next ^= 1;
    m.operand.assign(a.begin(), a.end());
    m.square = out;
  }
  return out;
}

Nat mul(Span a, Span b) {
  a = trimmed(a);
  b = trimmed(b);
  if (a.empty() || b.empty()) return {};
  // A square costs about half a product: a value times an equal one (zr * zr,
  // reading the same variable twice) takes that road.
  if (a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin())) return sqr(a);
  if (a.size() == 1 || b.size() == 1) {
    const Span lng = b.size() == 1 ? a : b;
    const std::uint64_t m = b.size() == 1 ? b[0] : a[0];
    Nat out(lng.begin(), lng.end());
    const std::uint64_t carry = mul_1(out, m);
    if (carry != 0) out.push_back(carry);
    return out;
  }
  const std::size_t n = a.size() + b.size() + 1;
  Nat out(n, 0);
  if (std::min(a.size(), b.size()) < karatsuba_mul_at()) {
    if (a.size() >= b.size()) {
      basecase_mul(out, a, b);
    } else {
      basecase_mul(out, b, a);
    }
  } else {
    with_scratch(scratch_len(n), [&](Out s) { mul_into(out, a, b, s); });
  }
  normalize(out);
  return out;
}

// ---- division

// Knuth's algorithm D (TAOCP 4.3.1) for a divisor of two words or more and
// u.size() >= v.size(): quotient and remainder, trimmed.
std::pair<Nat, Nat> knuth_div(Span u, Span v) {
  const unsigned shift = static_cast<unsigned>(__builtin_clzll(v.back()));
  Nat vn = shl_bits(v, shift);
  vn.pop_back();
  Nat un = shl_bits(u, shift);
  const std::size_t n = vn.size();
  const std::size_t m = un.size() - n - 1;
  Nat q(m + 1, 0);
  const u128 vtop = vn[n - 1], vnext = vn[n - 2];
  for (std::size_t j = m + 1; j-- > 0;) {
    // The estimate from the top two words: a one-word division while the top
    // word is below the divisor's (the normalised top word bounds it), else
    // B - 1 (Hacker's Delight's divmnu; the same answers as dividing the
    // 128-bit numerator and stepping down from B).
    u128 qhat, rhat;
    if (un[j + n] < vn[n - 1]) {
      std::uint64_t r64;
      qhat = div_2by1(un[j + n], un[j + n - 1], vn[n - 1], r64);
      rhat = r64;
    } else {
      qhat = WORD_MAX;
      rhat = static_cast<u128>(un[j + n - 1]) + vn[n - 1];
    }
    while (rhat <= WORD_MAX && qhat * vnext > (rhat << 64 | un[j + n - 2])) {
      --qhat;
      rhat += vtop;
    }
    const std::uint64_t borrow = submul_1(Out(un).subspan(j, n), vn, static_cast<std::uint64_t>(qhat));
    const bool under = un[j + n] < borrow;
    un[j + n] -= borrow;
    if (under) {
      --qhat;
      const bool carry = add_in(Out(un).subspan(j, n), vn);
      un[j + n] += carry ? 1 : 0;
    }
    q[j] = static_cast<std::uint64_t>(qhat);
  }
  normalize(q);
  return {std::move(q), shr_bits(Span(un).first(n), shift)};
}

// A divisor prepared for Barrett reduction: shifted until its top bit is set
// (`v`, n words), with r = floor(B^2n / v).
struct Recip {
  unsigned shift = 0;
  Nat v;
  Nat r;
};

Nat shl_words(Span a, std::size_t k) {
  if (a.empty()) return {};
  Nat out(k, 0);
  out.insert(out.end(), a.begin(), a.end());
  return out;
}

Nat shr_words(Span a, std::size_t k) {
  if (k >= a.size()) return {};
  return Nat(a.begin() + static_cast<std::ptrdiff_t>(k), a.end());
}

// floor(B^2n / v) for v of n words with its top bit set. Newton's iteration
// from the reciprocal of v's top half: x1 = x0 + x0 (B^2n - v x0) / B^2n
// doubles the correct words, then a product fixes the last unit or two.
Nat reciprocal(Span v) {
  const std::size_t n = v.size();
  Nat b2n(2 * n + 1, 0);
  b2n[2 * n] = 1;
  if (n <= RECIP_BASE) return knuth_div(b2n, v).first;
  const std::size_t h = n / 2 + 1;
  const Nat rh = reciprocal(v.subspan(n - h));
  // x0 = rh B^(n-h): its low n-h words are zero, so v x0 = (v rh) B^(n-h).
  Nat x = shl_words(rh, n - h);
  const Nat vx = shl_words(mul(v, rh), n - h);
  const bool negative = cmp(vx, b2n) > 0;
  const Nat e = negative ? sub(vx, b2n) : sub(b2n, vx);
  // x0 e / B^2n = rh e / B^(n+h); e's low words cannot move the result by a unit.
  const Nat t = shr_words(mul(rh, shr_words(e, n - 1)), h + 1);
  x = negative ? sub(x, t) : add(x, t);
  const Nat one{1};
  Nat p = mul(v, x);
  while (cmp(p, b2n) > 0) {
    x = sub(x, one);
    p = sub(p, v);
  }
  for (;;) {
    const Nat rest = sub(b2n, p);
    if (cmp(rest, v) < 0) break;
    x = add(x, one);
    p = add(p, v);
  }
  return x;
}

Recip make_recip(Span d) {
  Recip rec;
  rec.shift = static_cast<unsigned>(__builtin_clzll(d.back()));
  rec.v = shl_bits(d, rec.shift);
  rec.v.pop_back();
  rec.r = reciprocal(rec.v);
  return rec;
}

// x < v B^n for the prepared v of n words: floor(x / v) and x mod v. Barrett
// (HAC 14.42): the estimate is at most two short.
std::pair<Nat, Nat> barrett_step(Span x, const Recip& rec) {
  const std::size_t n = rec.v.size();
  if (cmp(x, rec.v) < 0) return {Nat{}, Nat(x.begin(), x.end())};
  const Span q1 = x.subspan(std::min(n - 1, x.size()));
  Nat q = shr_words(mul(q1, rec.r), n + 1);
  Nat r = sub(x, mul(q, rec.v));
  const Nat one{1};
  while (cmp(r, rec.v) >= 0) {
    r = sub(r, rec.v);
    q = add(q, one);
  }
  return {std::move(q), std::move(r)};
}

// u / d with d prepared: long division in base B^n, one Barrett step per block.
std::pair<Nat, Nat> barrett_div(Span u, const Recip& rec) {
  const std::size_t n = rec.v.size();
  Nat us = shl_bits(u, rec.shift);
  normalize(us);
  const std::size_t blocks = (us.size() + n - 1) / n;
  Nat q(blocks * n, 0);
  Nat r;
  for (std::size_t b = blocks; b-- > 0;) {
    const std::size_t lo = b * n;
    const std::size_t hi = std::min(lo + n, us.size());
    Nat x(us.begin() + static_cast<std::ptrdiff_t>(lo), us.begin() + static_cast<std::ptrdiff_t>(hi));
    x.resize(n, 0);
    x.insert(x.end(), r.begin(), r.end());
    normalize(x);
    auto [qb, rb] = barrett_step(x, rec);
    std::copy(qb.begin(), qb.end(), q.begin() + static_cast<std::ptrdiff_t>(lo));
    r = std::move(rb);
  }
  normalize(q);
  return {std::move(q), shr_bits(r, rec.shift)};
}

// 5^k, trimmed, with its reciprocal made on first use.
struct Power {
  Nat value;
  mutable std::optional<Recip> recip;
  const Recip& rec() const {
    if (!recip) recip = make_recip(value);
    return *recip;
  }
};

// Quotient and remainder of trimmed u and v, v nonzero; `power` supplies a
// cached reciprocal when v is a power of five.
std::pair<Nat, Nat> div_rem(Span u, Span v, const Power* power) {
  if (v.empty()) throw std::logic_error("unsigned mantissa division by zero");
  if (cmp(u, v) < 0) return {Nat{}, Nat(u.begin(), u.end())};
  if (v.size() == 1) {
    Nat q(u.begin(), u.end());
    const std::uint64_t r = div_1(q, v[0]);
    normalize(q);
    return {std::move(q), r == 0 ? Nat{} : Nat{r}};
  }
  const std::size_t n = v.size(), m = u.size() - v.size();
  if (power != nullptr && n >= BARRETT_CACHED && m >= BARRETT_CACHED) return barrett_div(u, power->rec());
  if (power == nullptr && n >= BARRETT_FRESH && m >= BARRETT_FRESH) return barrett_div(u, make_recip(v));
  return knuth_div(u, v);
}

// ---- powers of five and ten

struct Pow5Cache {
  std::unordered_map<std::size_t, std::shared_ptr<const Power>> map;
  std::size_t words = 0;
};

thread_local Pow5Cache pow5_cache;

// 5^k, cached with its halves.
std::shared_ptr<const Power> pow5(std::size_t k) {
  if (const auto it = pow5_cache.map.find(k); it != pow5_cache.map.end()) return it->second;
  auto p = std::make_shared<Power>();
  if (k <= 27) {
    std::uint64_t v = 1;
    for (std::size_t i = 0; i < k; ++i) v *= 5;
    p->value = {v};
  } else {
    const std::shared_ptr<const Power> half = pow5(k / 2);
    p->value = sqr(half->value);
    if (k % 2 == 1) {
      const std::uint64_t carry = mul_1(p->value, 5);
      if (carry != 0) p->value.push_back(carry);
    }
  }
  if (pow5_cache.words + p->value.size() > POW5_CACHE_WORDS) {
    pow5_cache.map.clear();
    pow5_cache.words = 0;
  }
  pow5_cache.words += p->value.size();
  pow5_cache.map.emplace(k, p);
  return p;
}

// 10^k for k <= 19.
constexpr std::uint64_t pow10_word(std::size_t k) {
  std::uint64_t p = 1;
  for (std::size_t i = 0; i < k; ++i) p *= 10;
  return p;
}

// a * 10^k = (a * 5^k) << k.
Nat mul_pow10(Span a, std::size_t k) {
  if (a.empty() || k == 0) return Nat(a.begin(), a.end());
  if (k <= 19) {
    Nat out(a.begin(), a.end());
    const std::uint64_t carry = mul_1(out, pow10_word(k));
    if (carry != 0) out.push_back(carry);
    return out;
  }
  return shl(mul(a, pow5(k)->value), k);
}

// a / 5^k and a mod 5^k.
std::pair<Nat, Nat> divmod_pow5(Span a, std::size_t k) {
  if (k <= 27) {
    std::uint64_t p = 1;
    for (std::size_t i = 0; i < k; ++i) p *= 5;
    Nat q(a.begin(), a.end());
    const std::uint64_t r = div_1(q, p);
    normalize(q);
    return {std::move(q), r == 0 ? Nat{} : Nat{r}};
  }
  const std::shared_ptr<const Power> p = pow5(k);
  return div_rem(a, p->value, p.get());
}

// a / 10^k and a mod 10^k: a >> k divided by 5^k, the low k bits kept.
std::pair<Nat, Nat> divmod_pow10(Span a, std::size_t k) {
  if (a.empty() || k == 0) return {Nat(a.begin(), a.end()), Nat{}};
  if (k <= 19) {
    Nat q(a.begin(), a.end());
    const std::uint64_t r = div_1(q, pow10_word(k));
    normalize(q);
    return {std::move(q), r == 0 ? Nat{} : Nat{r}};
  }
  auto [q, r5] = divmod_pow5(shr(a, k), k);
  const Nat low = low_bits(a, k);
  Nat r = shl(r5, k);
  if (r.size() < low.size()) r.resize(low.size(), 0);
  for (std::size_t i = 0; i < low.size(); ++i) r[i] |= low[i];
  normalize(r);
  return {std::move(q), std::move(r)};
}

// Whether a < 10^k: 10^k = 5^k 2^k, so a < 10^k exactly when a >> k < 5^k.
bool below_pow10(Span a, std::size_t k) {
  if (k <= 19) return a.size() <= 1 && (a.empty() ? 0 : a[0]) < pow10_word(k);
  if (bit_len(a) <= k) return true;
  return cmp(shr(a, k), pow5(k)->value) < 0;
}

// floor(b log10 2), and whether the Q64 constant proves it (it does unless
// b log10 2 lies within b / 2^64 of an integer).
std::pair<std::size_t, bool> floor_log10_pow2(std::size_t b) {
  const u128 f = static_cast<u128>(b) * LOG10_2_Q64;
  const bool exact = static_cast<u128>(static_cast<std::uint64_t>(f)) + b < (static_cast<u128>(1) << 64);
  return {static_cast<std::size_t>(f >> 64), exact};
}

// Bounds on the decimal digit count of a nonzero a from its bit length alone:
// 2^(b-1) <= a < 2^b.
std::pair<std::size_t, std::size_t> digit_bounds(Span a) {
  const std::size_t b = bit_len(a);
  const auto [lo, lo_exact] = floor_log10_pow2(b - 1);
  const auto [hi, hi_exact] = floor_log10_pow2(b);
  return {lo + 1, hi + 1 + ((lo_exact && hi_exact) ? 0 : 1)};
}

// The exact decimal digit count of a nonzero a.
std::size_t digits(Span a) {
  const auto [lo, hi] = digit_bounds(a);
  std::size_t d = lo;
  while (d < hi && !below_pow10(a, d)) ++d;
  return d;
}

// The number of decimal zeros ending a nonzero a, at most `cap`: the lesser of
// its factors of two (free, the low zero bits) and of five (found in strides
// that double while they divide).
std::size_t trailing_decimal_zeros(Span a, std::size_t cap) {
  cap = std::min(cap, trailing_zero_bits(a));
  if (cap == 0 || rem_1(a, 5) != 0) return 0;
  Nat x(a.begin(), a.end());
  std::size_t t = 0, stride = 1;
  for (;;) {
    if (t + stride <= cap) {
      auto [q, r] = divmod_pow5(x, stride);
      if (r.empty()) {
        x = std::move(q);
        t += stride;
        stride *= 2;
        continue;
      }
    }
    if (stride == 1) return t;
    stride /= 2;
  }
}

// ---- decimal text

// Where a decimal conversion of `len` digits splits: the largest 19 * 2^j that
// leaves the high part at least as long as the low one, so every split point is
// a power of ten the cache already holds.
std::size_t split_digits(std::size_t len) {
  std::size_t k = 19;
  while (4 * k <= len) k *= 2;
  return k;
}

// ASCII digits (validated by the caller) as a magnitude.
Nat from_decimal(std::string_view digits) {
  if (digits.size() <= DEC_LEAF_DIGITS) {
    Nat acc;
    std::size_t len = std::min(digits.size() % 19 == 0 ? std::size_t{19} : digits.size() % 19, digits.size());
    for (std::size_t start = 0; start < digits.size(); start += len, len = 19) {
      std::uint64_t chunk = 0;
      for (std::size_t i = start; i < start + len; ++i) chunk = chunk * 10 + static_cast<std::uint64_t>(digits[i] - '0');
      const std::uint64_t carry = mul_1(acc, pow10_word(len));
      if (carry != 0) acc.push_back(carry);
      if (chunk != 0) {
        if (acc.empty()) acc.push_back(0);
        const std::array<std::uint64_t, 1> word{chunk};
        if (add_in(acc, word)) acc.push_back(1);
      }
    }
    normalize(acc);
    return acc;
  }
  const std::size_t k = split_digits(digits.size());
  const Nat high = from_decimal(digits.substr(0, digits.size() - k));
  const Nat low = from_decimal(digits.substr(digits.size() - k));
  return add(mul_pow10(high, k), low);
}

// The digits of a < 10^len into out[0, len) (filled with '0' by the caller),
// right-aligned.
void write_decimal(Span a, char* out, std::size_t len) {
  if (a.size() <= DEC_LEAF_WORDS) {
    Nat cur(a.begin(), a.end());
    std::size_t end = len;
    while (!cur.empty()) {
      std::uint64_t r = div_1(cur, TEN19);
      normalize(cur);
      const std::size_t start = end >= 19 ? end - 19 : 0;
      for (std::size_t i = end; i-- > start;) {
        out[i] = static_cast<char>('0' + r % 10);
        r /= 10;
      }
      end = start;
    }
    return;
  }
  const std::size_t k = split_digits(len);
  const auto [q, r] = divmod_pow10(a, k);
  write_decimal(q, out, len - k);
  write_decimal(r, out + (len - k), k);
}

std::string to_decimal(Span a) {
  if (a.empty()) return "0";
  std::string out(digits(a), '0');
  write_decimal(a, out.data(), out.size());
  return out;
}

}  // namespace bn

// Decimal digits of `m`, least significant first, into buf (at least 40 bytes);
// returns how many. A 128-bit `% 10` is a library call, so the magnitude is cut
// into 19-digit chunks by 128-bit division (one call per chunk) and each chunk
// is finished in 64-bit arithmetic -- almost every mantissa fits in 64 bits and
// never pays for the first step at all.
inline int u128_digits_rev(__uint128_t m, char* buf) {
  int n = 0;
  if (m == 0) {
    buf[n++] = '0';
    return n;
  }
  constexpr uint64_t E19 = 10000000000000000000ULL;
  while (m > std::numeric_limits<uint64_t>::max()) {
    uint64_t lo = static_cast<uint64_t>(m % E19);
    m /= E19;
    for (int i = 0; i < 19; ++i) { buf[n++] = static_cast<char>('0' + lo % 10); lo /= 10; }
  }
  uint64_t v = static_cast<uint64_t>(m);
  while (v != 0) { buf[n++] = static_cast<char>('0' + v % 10); v /= 10; }
  return n;
}

// Strips trailing decimal zeros from a mantissa while `scale` > 0, in 64-bit
// arithmetic whenever the magnitude allows it.
template <class T>
inline void trim_trailing_zeros(T& m, long long& scale) {
  if (m >= std::numeric_limits<int64_t>::min() && m <= std::numeric_limits<int64_t>::max()) {
    int64_t v = static_cast<int64_t>(m);
    while (scale > 0 && v != 0 && v % 10 == 0) { v /= 10; --scale; }
    if (v == 0) scale = scale > 0 ? 0 : scale;   // a zero keeps no fraction digits
    m = static_cast<T>(v);
    return;
  }
  while (scale > 0 && m % 10 == 0) { m /= 10; --scale; }
}

std::string dec_digits_from_magnitude(__uint128_t magnitude) {
  char rev[42];
  const int n = u128_digits_rev(magnitude, rev);
  char buf[42];
  for (int i = 0; i < n; ++i) buf[i] = rev[n - 1 - i];
  return std::string(buf, static_cast<size_t>(n));
}

std::string scale_up(const std::string& digits, long long k) {
  if (k <= 0) return digits;
  if (digits == "0") return "0";
  return digits + std::string(static_cast<std::size_t>(k), '0');
}

// --- construction
//
// A Dec holds its magnitude in up to three forms: the small mantissa (when it
// fits, every operation's fast path), the digit string (a numeral as read, and
// a large value once it has been written out), and the binary words (a large
// value as arithmetic leaves it). The string and the words are caches of each
// other, each made from the other only when something asks for it. A large Dec
// is never zero, so empty words mean "not made yet".

const std::vector<std::uint64_t>& dec_get_words(const Dec& d) {
  if (!d.words.empty()) return d.words;
  if (d.small) {
    const __uint128_t mag = d.mantissa < 0 ? static_cast<__uint128_t>(-(d.mantissa))
                                           : static_cast<__uint128_t>(d.mantissa);
    d.words = {static_cast<std::uint64_t>(mag), static_cast<std::uint64_t>(mag >> 64)};
    bn::normalize(d.words);
    return d.words;
  }
  if (!d.digits.empty() && d.digits != "0") d.words = bn::from_decimal(d.digits);
  return d.words;
}

const std::string& dec_get_digits(const Dec& d) {
  if (!d.digits.empty()) return d.digits;
  if (d.small) {
    __uint128_t mag = d.mantissa < 0 ? static_cast<__uint128_t>(-(d.mantissa))
                                     : static_cast<__uint128_t>(d.mantissa);
    d.digits = dec_digits_from_magnitude(mag);
    return d.digits;
  }
  if (!d.words.empty()) {
    d.digits = bn::to_decimal(d.words);
    return d.digits;
  }
  d.digits = "0";
  return d.digits;
}

Dec dec_from_mantissa(__int128_t mantissa, long long scale) {
  Dec d;
  // -2^127 is the one mantissa whose negation, absolute value and digit
  // extraction overflow (signed UB). It is held as digits instead, so no small
  // mantissa ever has to be negated safely: the small range is symmetric.
  if (mantissa == std::numeric_limits<__int128_t>::min()) {
    d.neg = true;
    d.scale = static_cast<std::int32_t>(scale);
    d.small = false;
    d.digits = dec_digits_from_magnitude(static_cast<__uint128_t>(1) << 127);
    return d;
  }
  d.neg = mantissa < 0;
  d.scale = static_cast<std::int32_t>(scale);
  d.small = true;
  d.mantissa = mantissa;
  return d;
}

// A Dec from a magnitude arithmetic produced: small when it fits (as a numeral
// of the same value would be), zero never negative.
Dec dec_from_words(bool neg, std::vector<std::uint64_t> words, long long scale) {
  bn::normalize(words);
  if (words.empty()) return dec_from_mantissa(0, scale);
  if (scale <= 38 && words.size() <= 2 && (words.size() < 2 || words[1] < (std::uint64_t{1} << 63))) {
    const __uint128_t mag =
        (words.size() == 2 ? static_cast<__uint128_t>(words[1]) << 64 : static_cast<__uint128_t>(0)) | words[0];
    const __int128_t mantissa = static_cast<__int128_t>(mag);
    return dec_from_mantissa(neg ? -mantissa : mantissa, scale);
  }
  Dec d;
  d.neg = neg;
  d.small = false;
  d.scale = static_cast<std::int32_t>(scale);
  d.words = std::move(words);
  return d;
}

std::optional<__int128_t> dec_small_mantissa(const std::string& digits, bool neg) {
  if (digits.size() > 38) return std::nullopt;
  // The same limit either way: -2^127 is not a small mantissa (dec_from_mantissa).
  const __uint128_t limit = ~(static_cast<__uint128_t>(1) << 127);
  __uint128_t magnitude = 0;
  for (const char ch : digits) {
    const unsigned digit = static_cast<unsigned>(ch - '0');
    if (magnitude > (limit - digit) / 10) return std::nullopt;
    magnitude = magnitude * 10 + digit;
  }
  if (!neg) return static_cast<__int128_t>(magnitude);
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

// Bounds on the magnitude's decimal digit count (at least one); for binary
// words they come from the bit length, with no conversion and no division.
std::pair<long long, long long> dec_digit_bounds(const Dec& d) {
  if (d.small) {
    const __uint128_t m = d.mantissa < 0 ? static_cast<__uint128_t>(0) - static_cast<__uint128_t>(d.mantissa)
                                         : static_cast<__uint128_t>(d.mantissa);
    if (m < 10) return {1, 1};
    // 2^(b-1) <= m < 2^b puts floor(log10 m) at t or t + 1, t = floor((b - 1)
    // log10 2) -- which (x * 1233) >> 12 is for every x < 128 -- and one
    // comparison with 10^(t+1) says which. A 128-bit `/ 10` per digit was a
    // library call each.
    const std::uint64_t hi = static_cast<std::uint64_t>(m >> 64);
    const int bits = hi != 0 ? 128 - __builtin_clzll(hi) : 64 - __builtin_clzll(static_cast<std::uint64_t>(m));
    const int t = ((bits - 1) * 1233) >> 12;
    const long long n = t + 1 + (m >= static_cast<__uint128_t>(POW10_128[t + 1]) ? 1 : 0);
    return {n, n};
  }
  if (!d.words.empty()) {
    const auto [lo, hi] = bn::digit_bounds(d.words);
    return {static_cast<long long>(lo), static_cast<long long>(hi)};
  }
  const long long n = d.digits.empty() ? 1 : static_cast<long long>(d.digits.size());
  return {n, n};
}

// Number of digits in the unscaled magnitude (at least 1), exactly.
long long dec_ndigits(const Dec& d) {
  if (!d.small && !d.words.empty()) return static_cast<long long>(bn::digits(d.words));
  return dec_digit_bounds(d).first;
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
  // At most scale + MAX_INT_DIGITS mantissa digits. The bounds decide every
  // value not within a digit of that line; only those are counted exactly.
  const long long limit = static_cast<long long>(d.scale) + MAX_INT_DIGITS;
  const auto [lo, hi] = dec_digit_bounds(d);
  const bool over = hi > limit && (lo > limit || dec_ndigits(d) > limit);
  if (over) {
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
    __uint128_t mag = 0;
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

// The length of dec_format(d) without producing it: the sign, the mantissa's
// digits (padded to scale + 1 when all fractional), and the point. The text is
// ASCII, so this is its code-point count too.
std::size_t dec_format_len(const Dec& d) {
  const std::size_t sign = d.neg ? 1 : 0;
  const std::size_t digits = static_cast<std::size_t>(dec_ndigits(d));
  const std::size_t scale = static_cast<std::size_t>(d.scale);
  return sign + (scale == 0 ? digits : std::max(digits, scale + 1) + 1);
}

std::size_t dec_format_buf(const Dec& d, char* out) {
  char* p = out;
  if (d.neg) *p++ = '-';
  if (d.small) {
    __int128_t m = d.mantissa < 0 ? -d.mantissa : d.mantissa;
    char digits[48];
    const int dlen = u128_digits_rev(static_cast<__uint128_t>(m), digits);
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
  if (!d.words.empty()) return false;
  return dec_get_digits(d) == "0";
}

Dec dec_negate(const Dec& d) {
  if (d.small) return dec_from_mantissa(-d.mantissa, d.scale);
  if (!d.words.empty()) {
    Dec r = d;
    r.neg = !r.neg;
    return r;
  }
  return dec_make(!d.neg, dec_get_digits(d), d.scale);
}

// The value with the fraction's trailing zeros removed (§7.6 CANON): 1.50 is
// 1.5, 2.000 is 2, 100 stays 100, and zero is 0 with no scale and no sign.
// Counted on the digit string when the value has one, else on the words.
Dec dec_trim_scale(const Dec& d) {
  if (dec_is_zero(d)) return dec_from_mantissa(0, 0);
  if (d.scale == 0) return d;
  if (d.small) {
    dec_mantissa_t m = d.mantissa;
    long long scale = d.scale;
    trim_trailing_zeros(m, scale);
    return dec_from_mantissa(m, scale);
  }
  if (!d.digits.empty()) {
    const std::string& digits = d.digits;
    std::size_t end = digits.size();
    const std::size_t stop = digits.size() > static_cast<std::size_t>(d.scale)
                                 ? digits.size() - static_cast<std::size_t>(d.scale)
                                 : 0;
    while (end > stop && digits[end - 1] == '0') --end;
    if (end == digits.size()) return d;
    return dec_make(d.neg, digits.substr(0, end),
                    static_cast<long long>(d.scale) - static_cast<long long>(digits.size() - end));
  }
  const std::vector<std::uint64_t>& words = dec_get_words(d);
  const std::size_t zeros = bn::trailing_decimal_zeros(words, static_cast<std::size_t>(d.scale));
  if (zeros == 0) return d;
  return dec_from_words(d.neg, bn::divmod_pow10(words, zeros).first,
                        static_cast<long long>(d.scale) - static_cast<long long>(zeros));
}

Dec dec_abs(const Dec& d) {
  if (d.small) return dec_from_mantissa(d.mantissa < 0 ? -d.mantissa : d.mantissa, d.scale);
  if (!d.words.empty()) {
    Dec r = d;
    r.neg = false;
    return r;
  }
  return dec_make(false, dec_get_digits(d), d.scale);
}

int dec_sign(const Dec& d) {
  if (d.small) return d.mantissa == 0 ? 0 : (d.mantissa < 0 ? -1 : 1);
  if (!d.words.empty()) return d.neg ? -1 : 1;
  return dec_is_zero(d) ? 0 : (d.neg ? -1 : 1);
}

// --- arithmetic

// The checked native fast path shared by addition, comparison and modulo:
// both small mantissas brought to the larger scale in __int128, or false when
// the scale gap is past the power table or the multiply overflows. The caller
// falls through to the binary path on false exactly as each did with its own
// copy; signed bounds are __builtin_mul_overflow's. Three callers, one rule
// (SEL-0018); it is not a small-integer abstraction for the other hosts.
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

// The magnitude's words brought to a scale k places finer: a borrow of the
// Dec's own words when k is zero, else the product kept in `keep`.
const std::vector<std::uint64_t>& dec_words_scaled(const Dec& d, long long k, std::vector<std::uint64_t>& keep) {
  const std::vector<std::uint64_t>& words = dec_get_words(d);
  if (k <= 0) return words;
  keep = bn::mul_pow10(words, static_cast<std::size_t>(k));
  return keep;
}

// a + b, or a - b when `minus`: b's sign is read through the flag, so a
// subtraction never copies its right operand (all its words) just to negate it.
Dec dec_add_signed(const Dec& a, const Dec& b, bool minus, Pos pos) {
  if (a.small && b.small) {
    __int128_t sa, sb;
    long long target_scale;
    if (align_small(a, b, sa, sb, target_scale)) {
      // No small mantissa is -2^127 (dec_from_mantissa), and no other one
      // reaches it times a power of ten (5 does not divide 2^127), so the
      // negation is safe -- and it is the value dec_negate would have aligned.
      if (minus) sb = -sb;
      __int128_t sum;
      if (!__builtin_add_overflow(sa, sb, &sum)) {
        return dec_guard(dec_from_mantissa(sum, target_scale), pos);
      }
    }
  }
  // A zero b's sign is never read wrongly: either branch below gives a's value.
  const bool b_neg = b.neg != minus;
  const long long s = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
  std::vector<std::uint64_t> keep_a, keep_b;
  const std::vector<std::uint64_t>& wa = dec_words_scaled(a, s - a.scale, keep_a);
  const std::vector<std::uint64_t>& wb = dec_words_scaled(b, s - b.scale, keep_b);
  if (a.neg == b_neg) {
    return dec_guard(dec_from_words(a.neg, bn::add(wa, wb), s), pos);
  }
  const int c = bn::cmp(wa, wb);
  if (c == 0) return dec_from_mantissa(0, s);
  return c > 0 ? dec_guard(dec_from_words(a.neg, bn::sub(wa, wb), s), pos)
               : dec_guard(dec_from_words(b_neg, bn::sub(wb, wa), s), pos);
}

Dec dec_add(const Dec& a, const Dec& b, Pos pos = {}) { return dec_add_signed(a, b, false, pos); }

Dec dec_sub(const Dec& a, const Dec& b, Pos pos = {}) { return dec_add_signed(a, b, true, pos); }

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
  // A product that cannot fit is refused before it is computed: the result has at
  // least da + db - 1 digits and exactly sa + sb fractional ones, so the cap can
  // be decided from the operand sizes (the same E_RANGE the guard raises, at the
  // same position, without spending minutes multiplying a doomed pair). The
  // lower digit bounds prove it without counting.
  if (!dec_is_zero(a) && !dec_is_zero(b)) {
    const long long prod_scale = static_cast<long long>(a.scale) + b.scale;
    const long long min_digits = dec_digit_bounds(a).first + dec_digit_bounds(b).first - 1;
    if (prod_scale > MAX_FRAC_DIGITS) {
      fail("E_RANGE", "number has more than " + std::to_string(MAX_FRAC_DIGITS) + " fractional digits", pos);
    }
    if (min_digits - prod_scale > MAX_INT_DIGITS) {
      fail("E_RANGE", "number has more than " + std::to_string(MAX_INT_DIGITS) + " integer digits", pos);
    }
  }
  const std::vector<std::uint64_t>& wa = dec_get_words(a);
  const std::vector<std::uint64_t>& wb = dec_get_words(b);
  return dec_guard(dec_from_words(a.neg != b.neg, bn::mul(wa, wb), static_cast<long long>(a.scale) + b.scale),
                   pos);
}

int dec_cmp(const Dec& a, const Dec& b) {
  if (dec_is_zero(a) && dec_is_zero(b)) return 0;
  if (a.neg != b.neg) return a.neg ? -1 : 1;
  if (a.small && b.small) {
    __int128_t sa, sb;
    long long target_scale;
    if (align_small(a, b, sa, sb, target_scale)) return (sa > sb) - (sa < sb);
  }
  int c = 0;
  bool decided = false;
  // Across scales the digit bounds decide most pairs without an aligned copy:
  // |a| < 10^(hi_a - scale_a) <= 10^(lo_b - 1 - scale_b) <= |b|.
  if (a.scale != b.scale && !dec_is_zero(a) && !dec_is_zero(b)) {
    const auto [alo, ahi] = dec_digit_bounds(a);
    const auto [blo, bhi] = dec_digit_bounds(b);
    if (ahi - a.scale <= blo - 1 - b.scale) {
      c = -1;
      decided = true;
    } else if (bhi - b.scale <= alo - 1 - a.scale) {
      c = 1;
      decided = true;
    }
  }
  if (!decided) {
    const long long s = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
    std::vector<std::uint64_t> keep_a, keep_b;
    c = bn::cmp(dec_words_scaled(a, s - a.scale, keep_a), dec_words_scaled(b, s - b.scale, keep_b));
  }
  return a.neg ? -c : c;
}

// Whether two magnitudes are equal (scales and signs are the caller's): on the
// digit strings when both have them, else on the words.
bool dec_mag_equal(const Dec& a, const Dec& b) {
  if (a.small && b.small) return a.mantissa == b.mantissa;
  if (!a.small && !b.small && a.words.empty() && b.words.empty() && !a.digits.empty() &&
      !b.digits.empty()) {
    return a.digits == b.digits;
  }
  return dec_get_words(a) == dec_get_words(b);
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
          trim_trailing_zeros(q, scale);
          if (q == 0) scale = 0;
          __int128_t signed_q = neg ? -q : q;
          return dec_guard(dec_from_mantissa(signed_q, scale), pos);
        } else {
          if (r >= den - r) q++;  // 2*r overflows past 2^126
          __int128_t signed_q = neg ? -q : q;
          return dec_guard(dec_from_mantissa(signed_q, DIV_SCALE), pos);
        }
      }
    }
  }
  // |a| 10^(sb - sa + DIV_SCALE) / |b| 10^(sa - sb), whichever exponents are
  // positive.
  const long long sa = a.scale, sb = b.scale;
  std::vector<std::uint64_t> keep_n, keep_d;
  const std::vector<std::uint64_t>& num = dec_words_scaled(a, std::max(sb - sa, 0LL) + DIV_SCALE, keep_n);
  const std::vector<std::uint64_t>& den = dec_words_scaled(b, std::max(sa - sb, 0LL), keep_d);
  auto [q, r] = bn::div_rem(num, den, nullptr);
  const bool neg = a.neg != b.neg;
  if (r.empty()) {
    // An exact quotient takes its minimal scale: drop up to DIV_SCALE zeros.
    const std::size_t zeros = q.empty() ? 0 : bn::trailing_decimal_zeros(q, DIV_SCALE);
    const long long scale = q.empty() ? 0 : DIV_SCALE - static_cast<long long>(zeros);
    if (zeros != 0) q = bn::divmod_pow10(q, zeros).first;
    return dec_guard(dec_from_words(neg, std::move(q), scale), pos);
  }
  // Half away from zero: 2r >= the divisor.
  if (bn::cmp(bn::add(r, r), den) >= 0) q = bn::add(q, std::array<std::uint64_t, 1>{1});
  return dec_guard(dec_from_words(neg, std::move(q), DIV_SCALE), pos);
}

// Remainder of truncated division: takes the sign of the dividend.
Dec dec_mod(const Dec& a, const Dec& b, Pos pos = {}) {
  if (dec_is_zero(b)) fail("E_DIV_ZERO", "modulo by zero", pos);
  // Both at the larger scale, natively when they fit: `%` on the signed
  // mantissas truncates, so the remainder already has the dividend's sign
  // (no mantissa is -2^127, dec_from_mantissa).
  if (a.small && b.small) {
    __int128_t sa, sb;
    long long target_scale;
    if (align_small(a, b, sa, sb, target_scale)) return dec_from_mantissa(sa % sb, target_scale);
  }
  const long long s = std::max(static_cast<long long>(a.scale), static_cast<long long>(b.scale));
  std::vector<std::uint64_t> keep_a, keep_b;
  const std::vector<std::uint64_t>& wa = dec_words_scaled(a, s - a.scale, keep_a);
  const std::vector<std::uint64_t>& wb = dec_words_scaled(b, s - b.scale, keep_b);
  return dec_from_words(a.neg, bn::div_rem(wa, wb, nullptr).second, s);
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
    // Padding the scale is appending zeros: on the digit string when the value
    // has one, without converting it.
    if (!d.small && !d.digits.empty()) {
      return dec_guard(dec_make(d.neg, scale_up(d.digits, n - d.scale), n), pos);
    }
    return dec_guard(dec_from_words(d.neg, bn::mul_pow10(dec_get_words(d), static_cast<std::size_t>(n - d.scale)), n),
                     pos);
  }
  if (d.small && (d.scale - n) <= 38) {
    __int128_t p = POW10_128[d.scale - n];
    __int128_t abs_m = d.mantissa < 0 ? -d.mantissa : d.mantissa;
    __int128_t q = abs_m / p;
    __int128_t r = abs_m % p;
    if (r >= p - r) q++;    // 2*r overflows past 2^126
    __int128_t signed_q = d.neg ? -q : q;
    return dec_guard(dec_from_mantissa(signed_q, n), pos);
  }
  const std::size_t k = static_cast<std::size_t>(d.scale - n);
  auto [q, r] = bn::divmod_pow10(dec_get_words(d), k);
  // Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
  // Half away from zero: 2r >= 10^k.
  if (!r.empty() && bn::cmp(bn::add(r, r), bn::mul_pow10(std::array<std::uint64_t, 1>{1}, k)) >= 0) {
    q = bn::add(q, std::array<std::uint64_t, 1>{1});
  }
  return dec_guard(dec_from_words(d.neg, std::move(q), n), pos);
}

Dec dec_trunc(const Dec& d) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    return dec_from_mantissa(d.mantissa / POW10_128[d.scale], 0);
  }
  return dec_from_words(d.neg, bn::divmod_pow10(dec_get_words(d), static_cast<std::size_t>(d.scale)).first, 0);
}

Dec dec_floor(const Dec& d, Pos pos = {}) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    __int128_t p = POW10_128[d.scale];
    __int128_t q = d.mantissa / p;
    __int128_t r = d.mantissa % p;
    if (d.mantissa < 0 && r != 0) q--;
    return dec_from_mantissa(q, 0);
  }
  auto [q, r] = bn::divmod_pow10(dec_get_words(d), static_cast<std::size_t>(d.scale));
  // A carry can widen the integer part past the cap (FLOOR of -99..9.5): the
  // result is checked where it is built, at the call.
  if (d.neg && !r.empty()) q = bn::add(q, std::array<std::uint64_t, 1>{1});
  return dec_guard(dec_from_words(d.neg, std::move(q), 0), pos);
}

Dec dec_ceil(const Dec& d, Pos pos = {}) {
  if (d.scale == 0) return d;
  if (d.small && d.scale <= 38) {
    __int128_t p = POW10_128[d.scale];
    __int128_t q = d.mantissa / p;
    __int128_t r = d.mantissa % p;
    if (d.mantissa > 0 && r != 0) q++;
    return dec_from_mantissa(q, 0);
  }
  auto [q, r] = bn::divmod_pow10(dec_get_words(d), static_cast<std::size_t>(d.scale));
  if (!d.neg && !r.empty()) q = bn::add(q, std::array<std::uint64_t, 1>{1});
  return dec_guard(dec_from_words(d.neg, std::move(q), 0), pos);
}

// True when the value has no fractional part left after its scale is honoured:
// a multiple of 10^scale, which needs scale factors of two (the low zero bits)
// before it is worth dividing by the power of five.
bool dec_is_integer(const Dec& d) {
  if (d.scale == 0) return true;
  if (d.small && d.scale <= 38) {
    return (d.mantissa % POW10_128[d.scale]) == 0;
  }
  const std::vector<std::uint64_t>& words = dec_get_words(d);
  if (words.empty()) return true;
  if (bn::trailing_zero_bits(words) < static_cast<std::size_t>(d.scale)) return false;
  return bn::divmod_pow10(words, static_cast<std::size_t>(d.scale)).second.empty();
}

// n must be a non-negative integer; the result scale is scale(x) * n, which
// falls out of repeated multiplication.
Dec dec_power(const Dec& a, long long n, Pos pos = {}) {
  // Same early refusal as dec_mul: a^n has exactly n*sa fractional digits and at
  // least n*(da-1)+1 digits, so a result past the caps is known before any
  // squaring (POWER(9999999999999999999999999999, 100000) spent 18 s finding out).
  // A lower bound on da keeps the refusal provable.
  if (n > 0 && !dec_is_zero(a)) {
    const long long da = dec_digit_bounds(a).first;
    const long long sa = a.scale;
    const __int128_t frac = static_cast<__int128_t>(sa) * n;
    if (frac > MAX_FRAC_DIGITS) {
      fail("E_RANGE", "number has more than " + std::to_string(MAX_FRAC_DIGITS) + " fractional digits", pos);
    }
    const __int128_t min_digits = static_cast<__int128_t>(da - 1) * n + 1;
    if (min_digits - frac > MAX_INT_DIGITS) {
      fail("E_RANGE", "number has more than " + std::to_string(MAX_INT_DIGITS) + " integer digits", pos);
    }
  }
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

// Truncates towards zero and converts, saturating at the long long range. Used
// where a built-in needs a count or a length; the caller has already checked the
// range it cares about.
constexpr long long LLONG_MAX_ = std::numeric_limits<long long>::max();
constexpr long long LLONG_MIN_ = std::numeric_limits<long long>::min();
long long dec_to_int(const Dec& d) {
  if (d.small) {
    __int128_t m = d.mantissa;
    if (d.scale > 0) {
      if (d.scale <= 38) m /= POW10_128[d.scale];
      else m = 0;
    }
    if (m > LLONG_MAX_) return LLONG_MAX_;
    if (m < LLONG_MIN_) return LLONG_MIN_;
    return static_cast<long long>(m);
  }
  const Dec t = dec_trunc(d);
  if (t.small) return dec_to_int(t);
  const std::vector<std::uint64_t>& words = dec_get_words(t);
  if (words.empty()) return 0;
  if (words.size() > 1 || words[0] > static_cast<std::uint64_t>(LLONG_MAX_)) {
    return t.neg ? LLONG_MIN_ : LLONG_MAX_;
  }
  const long long v = static_cast<long long>(words[0]);
  return t.neg ? -v : v;
}

// The size argument of ROUND (the scale) and POWER (the exponent), checked once
// for both lanes -- the builtin and the math plan -- with the builtins' wording:
// a whole number (E_NOT_INT), not negative, and at most the §6.4 cap (E_RANGE),
// all reported at the argument.
long long checked_size_arg(const Dec& d, const char* fn, const char* what, long long cap, Pos pos) {
  if (!dec_is_integer(d)) fail("E_NOT_INT", std::string(fn) + " argument 2 must be a whole number", pos);
  const long long n = dec_to_int(d);
  if (n < 0) fail("E_RANGE", std::string(fn) + " argument 2 must not be negative", pos);
  if (n > cap) {
    fail("E_RANGE",
         std::string(fn) + " " + what + " " + std::to_string(n) + " exceeds the maximum of " +
             std::to_string(cap),
         pos);
  }
  return n;
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
  keys.reserve(node.items.size() / 2);
  // A set, not std::find over the growing vector: n literal keys cost n^2/2
  // compares that way, seconds for a 650 KB rule, before anything runs.
  std::unordered_set<std::string_view> seen;
  seen.reserve(node.items.size() / 2);
  for (std::size_t i = 0; i < node.items.size(); i += 2) {
    if (node.items[i]->t != NT::Text) return {};
    const auto& key = node.items[i]->s;
    if (!seen.insert(std::string_view(key)).second) return {};
    keys.push_back(key);
  }
  return intern_record_shape(std::move(keys));
}

// Match Lisp's list-key contract: decimal keys 1..9 digits, no leading zero,
// and at most nine characters.  Keeping this parser on the flat path avoids
// materializing "1", "2", ... entries merely to answer LIST[index].
// The 0-based slot a list key names (list_key_number, sel_ast.hpp).
std::optional<std::size_t> parse_list_slot(const std::string& key) {
  const std::optional<std::size_t> n = list_key_number(key);
  if (!n) return std::nullopt;
  return *n - 1;
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
  static const void* identity(const Value& v) { return v.p_; }
  // A plain record's lookup by position, for Context's name hints: whether `v`
  // is a record whose children are its keys (no shape, not a list), and the
  // child at `index` when its key is `key`. Keys in such a record are unique
  // and keep their positions (Value::set), so a key confirmed there is exactly
  // the child Value::get would find.
  static bool plain_record(const Value& v) {
    const Value::Impl* p = v.p_;
    return p && p->collection && !p->collection->shape && !p->is_list;
  }
  static const Value* child_if_keyed(const Value& v, std::size_t index, const std::string& key) {
    const std::vector<Value::Entry>& children = v.p_->collection->children;
    if (index >= children.size()) return nullptr;
    const std::string& at = children[index].first;
    if (at.size() != key.size()) return nullptr;
    for (std::size_t i = 0; i < key.size(); ++i) {
      if (at[i] != key[i]) return nullptr;
    }
    return &children[index].second;
  }
  // Value::get on a plain record, reporting the child's position.
  static const Value* find_child(const Value& v, const std::string& key, std::size_t& index) {
    const auto it = v.find(key);
    if (it == v.p_->collection->children.end()) return nullptr;
    index = static_cast<std::size_t>(it - v.p_->collection->children.begin());
    return &it->second;
  }
  static Value shaped_direct(std::shared_ptr<const RecordShape> shape, std::size_t reserve_size) {
    Value v(Value::make_collection_impl());
    v.p_->mutable_coll().shape = std::move(shape);
    v.p_->mutable_coll().storage.reserve(reserve_size);
    return v;
  }
  static Value shaped(std::shared_ptr<const RecordShape> shape, std::vector<Value> storage);
  static Value from_dec(Dec d) {
    Value v;
    v.p_->kind = Kind::Text;
    v.p_->decimal = std::make_unique<Dec>(std::move(d));
    v.p_->scalar_computed = false;
    return v;
  }
  // The length of a computed number's text while it has not been formatted
  // (nothing otherwise): digits and scale decide it, so LEN of a large number
  // never writes its digits out. The scalar is resolved as as_text resolves it,
  // so an error is the same error at the same position.
  static std::optional<std::size_t> unformatted_number_len(const Value& v, Pos pos) {
    const Value& s = v.scalar_source(pos);
    if (s.p_->kind == Kind::Text && !s.p_->scalar_computed && s.p_->decimal) {
      return dec_format_len(*s.p_->decimal);
    }
    return std::nullopt;
  }
  static std::vector<Value>& storage(Value& v) {
    return v.p_->mutable_coll().storage;
  }
  // True when nothing outside this tree refers to any node of it: every Impl
  // reachable from `v` has exactly one owner. Mirrors clone_at's shape of the
  // tree (storage for shaped records and lists, children otherwise) and its depth
  // accounting, so a tree deeper than the cap reports false and is cloned -- and
  // refused -- as before.
  static bool unique_tree(const Value& v, int depth) {
    const Value::Impl* p = v.p_;
    if (!p) return true;
    if (p->ref_count != 1 || depth > MAX_DEPTH) return false;
    return unique_children(p, depth);
  }
  static bool unique_children(const Value::Impl* p, int depth) {
    if (!p->collection) return true;
    const Value::Collection& c = *p->collection;
    if (c.shape || (p->is_list && c.children.empty())) {
      for (const Value& x : c.storage) if (!unique_tree(x, depth + 1)) return false;
    } else {
      for (const Value::Entry& e : c.children) if (!unique_tree(e.second, depth + 1)) return false;
    }
    return true;
  }
  // Whether element `index` of `coll` can be KEPT by an aggregate that would
  // otherwise copy it (spec §3.4) without anyone being able to tell: `coll` is a
  // temporary -- this aggregate's own argument and nothing else holds it, which
  // is what a pipeline hands from one stage to the next -- and the element is held
  // by nothing but that temporary's slots, the aggregate's snapshot and the
  // `handles` its walk has on it -- the binder's frame, and the snapshot's unless
  // the walk read the collection in place (Snapshot::live) -- with one owner for
  // every node beneath it. Then the copy would be an independent twin of a value
  // no one can reach any more. Anything else -- a variable's list, an element an
  // earlier step aliased, a result that kept the element as its key -- says no and
  // is copied as before.
  static bool exclusively_held(const Value& coll, std::size_t index, const Value& item,
                               std::uint32_t handles) {
    const Value::Impl* c = coll.p_;
    const Value::Impl* e = item.p_;
    if (!c || !e || c->ref_count != 1 || !c->collection) return false;
    const Value::Collection& col = *c->collection;
    std::uint32_t held = handles;                        // the snapshot's handle, the walk's
    if (index < col.storage.size() && col.storage[index].p_ == e) ++held;
    if (index < col.children.size() && col.children[index].second.p_ == e) ++held;
    if (e->ref_count != held) return false;
    return unique_children(e, 1);
  }
  // The E_DEPTH that clone_at(depth, pos) would raise for `v`, and nothing else:
  // the same tree shape (storage for shaped records and packed lists, children
  // otherwise), the same `depth > MAX_DEPTH` test, the same position, and no
  // allocation. What an aggregate runs instead of a copy it can prove nobody
  // could tell apart from the original (keep_or_alias), so that the error a
  // too-deep element raises is still raised, at the same moment.
  static void check_clone_depth(const Value& v, int depth, Pos pos) {
    // A scalar, or a container of scalars -- a row, nearly always -- is settled
    // here, without the walk's stack.
    if (depth > MAX_DEPTH) fail("E_DEPTH", "value nested too deeply", pos);
    const Value::Impl* root = v.p_;
    if (!root || !root->collection) return;
    {
      const Value::Collection& c = *root->collection;
      const bool stored = c.shape || (root->is_list && c.children.empty());
      bool flat = true;
      if (stored) {
        for (const Value& x : c.storage) {
          if (x.p_ && x.p_->collection) { flat = false; break; }
        }
      } else {
        for (const Value::Entry& e : c.children) {
          if (e.second.p_ && e.second.p_->collection) { flat = false; break; }
        }
      }
      if (flat) {
        const bool empty = stored ? c.storage.empty() : c.children.empty();
        if (!empty && depth + 1 > MAX_DEPTH) fail("E_DEPTH", "value nested too deeply", pos);
        return;
      }
    }
    std::vector<std::pair<const Value::Impl*, int>> stack{{v.p_, depth}};
    while (!stack.empty()) {
      const auto [p, d] = stack.back();
      stack.pop_back();
      if (d > MAX_DEPTH) fail("E_DEPTH", "value nested too deeply", pos);
      if (!p || !p->collection) continue;
      const Value::Collection& c = *p->collection;
      if (c.shape || (p->is_list && c.children.empty())) {
        for (const Value& x : c.storage) stack.emplace_back(x.p_, d + 1);
      } else {
        for (const Value::Entry& e : c.children) stack.emplace_back(e.second.p_, d + 1);
      }
    }
  }

  // Containers have one allocation for their header and payload. Scalars retain
  // the small header and can still acquire a separate payload through an alias.
  // Here rather than at namespace scope because Value::Impl is private to Value.
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

  static void delete_impl(Value::Impl* impl) {
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

  // The decimal cache (Value::dec_val's writer side). Private on Value because
  // it trusts its caller: the interpreter stores only the decimal it has just
  // parsed from, or computed for, the value's own scalar.
  static bool has_dec(const Value& v) { return v.has_dec(); }
  static const Dec& dec_ref(const Value& v) { return v.dec_ref(); }
  static void set_dec(const Value& v, const Dec& d) { v.set_dec(d); }
};

namespace {
// What a collecting operation keeps (spec §3.4: `=`, `,`, LIST, RECORD and the
// aggregates copy what they collect). A value that is a fresh temporary all the
// way down -- nothing else holds any part of it -- is already an independent
// copy, so it is kept as it is instead of being copied again. Anything
// shared with a variable, another collection or the caller is still cloned, and
// so is a tree deeper than the cap, which is how E_DEPTH is still raised.
Value adopt_or_clone(Value&& v, int levels, Pos pos) {
  if (Internals::unique_tree(v, 1 + levels)) return std::move(v);
  return v.clone_below(levels, pos);
}

// What an aggregate that collects ITS ELEMENTS (FILTER, SORT, BUCKET rows) keeps of
// element `index` of `coll`: the element itself when Internals::exclusively_held
// says the copy would be pointless (the pipeline-temporary case), a copy otherwise.
// `handles` is the number of handles the caller's own walk holds on `item`: the
// binder frame's (one) and its snapshot's (one, or none when it read the
// collection in place: walk_handles).
Value keep_element(const Value& coll, std::size_t index, const Value& item, std::uint32_t handles) {
  if (Internals::exclusively_held(coll, index, item, handles)) return item;
  return item.clone();
}

// Whether evaluating `root` can change a value that existed before it ran. SEL
// has exactly two ways to do that: an assignment (anywhere below `root`,
// including inside a nested aggregate's body) and a call to an application's own
// function (may_have_effects: anything SEL does not ship, however it was
// installed), which is handed values and may keep or change them. A body with
// neither leaves every value it reads as it found it.
bool writes_nothing(const Node& root) {
  std::vector<const Node*> stack{&root};
  while (!stack.empty()) {
    const Node* n = stack.back();
    stack.pop_back();
    if (n->t == NT::Assign) return false;
    if (n->t == NT::Call && may_have_effects(n->spec)) return false;
    if (n->l) stack.push_back(n->l.get());
    if (n->r) stack.push_back(n->r.get());
    for (const NodePtr& c : n->items) {
      if (c) stack.push_back(c.get());
    }
  }
  return true;
}

// writes_nothing, asked once per node and kept there (Node::writes_nothing_fact).
[[gnu::noinline]] bool writes_nothing_cached(const Node& node) {
  const signed char known = node.writes_nothing_fact.get();
  if (known >= 0) return known == 1;
  const bool fact = writes_nothing(node);
  node.writes_nothing_fact.set(fact);
  return fact;
}

// keep_element, for an aggregate that has proved the copy unobservable: every
// body it evaluates writes nothing (so no element can change while it runs) and
// nothing it collected can reach its result uncopied. Then an element and its
// copy cannot be told apart, and the element itself is kept -- after the depth
// check the copy would have made, so a too-deep element is still E_DEPTH, at the
// same moment and position (BUCKET copying every joined row was most of
// scale-test scenario 1's time).
Value keep_or_alias(const Value& coll, std::size_t index, const Value& item, std::uint32_t handles) {
  if (Internals::exclusively_held(coll, index, item, handles)) return item;
  Internals::check_clone_depth(item, 1, Pos{});
  return item;
}

// In a program that cannot write (Context::write_free) nothing a collector or a
// constructor collects can change, so no copy of it could be told from it
// (spec §3.4): it is kept as it is, after the depth check the copy would have
// made -- E_DEPTH, at the same position, is the one thing such a program can see
// of a copy. Otherwise these are adopt_or_clone and keep_element.
Value hold_or_adopt(Value&& v, int levels, Pos pos, bool write_free) {
  if (!write_free) return adopt_or_clone(std::move(v), levels, pos);
  Internals::check_clone_depth(v, 1 + levels, pos);
  return std::move(v);
}

Value hold_or_keep(const Value& coll, std::size_t index, const Value& item, std::uint32_t handles,
                   bool write_free) {
  if (!write_free) return keep_element(coll, index, item, handles);
  Internals::check_clone_depth(item, 1, Pos{});
  return item;
}

thread_local Internals::ImplFreelist tl_impl_freelist;
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
  if (size == sizeof(Value::Impl) && ptr && tl_impl_freelist.count < Internals::ImplFreelist::MAX_CACHED) {
    Value::Impl* p = static_cast<Value::Impl*>(ptr);
    p->next_free = tl_impl_freelist.head;
    tl_impl_freelist.head = p;
    ++tl_impl_freelist.count;
    return;
  }
  ::operator delete(ptr);
}

Value::Value() : p_(new Impl()) {}

Value::Impl* Value::make_collection_impl() { return new Internals::CollectionImpl(); }

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
// to release on the way out, so no guard object is needed and every host
// spells it the same way. A value of exactly MAX_DEPTH levels is fine; the level
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
  // A scalar with no payload of its own -- nearly every node of a row -- is done.
  if (!p_->collection) return out;
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

Value Value::clone_below(int levels, Pos pos) const { return clone_at(1 + levels, pos); }

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
  Internals::delete_impl(p);
  while (!pending.empty()) {
    Impl* curr = pending.back();
    pending.pop_back();
    steal(curr);
    Internals::delete_impl(curr);
  }
}

// A Dec from host code (spec §8): any of its
// three forms -- the small mantissa, the digit string, the binary words -- must
// be a decimal, and the value is rebuilt canonical (leading zeros go, a negative
// zero loses its sign) and within the digit caps. Any words are a magnitude, so
// that form needs only rebuilding. The interpreter's own decimals go through
// Internals::from_dec, unchecked.
Value Value::num(const Dec& d) {
  auto bad = [](const std::string& why) -> Value {
    throw SelError("E_BAD_ARG", "not a decimal: " + why, Pos{});
  };
  if (d.scale < 0) return bad("the scale is negative");
  if (d.scale > MAX_FRAC_DIGITS) {
    throw SelError("E_RANGE", "number has more than " + std::to_string(MAX_FRAC_DIGITS) + " fractional digits",
                   Pos{});
  }
  std::string digits;
  if (d.small) {
    if ((d.mantissa < 0) != d.neg && d.mantissa != 0) return bad("the mantissa's sign disagrees with neg");
    const __uint128_t mag = d.mantissa < 0 ? static_cast<__uint128_t>(-(d.mantissa))
                                           : static_cast<__uint128_t>(d.mantissa);
    digits = dec_digits_from_magnitude(mag);
  } else if (!d.digits.empty()) {
    for (const char ch : d.digits) {
      if (ch < '0' || ch > '9') return bad("the digits are not ASCII digits");
    }
    digits = d.digits;
  } else if (!d.words.empty()) {
    return Internals::from_dec(dec_guard(dec_from_words(d.neg, d.words, d.scale), Pos{}));
  } else {
    digits = "0";
  }
  return Internals::from_dec(dec_guard(dec_make(d.neg, std::move(digits), d.scale), Pos{}));
}

namespace {

Value make_text(std::string utf8) { return Internals::raw(Kind::Text, std::move(utf8), false); }
Value make_bin(std::string bytes) { return Internals::raw(Kind::Bin, std::move(bytes), false); }
Value make_num(const Dec& d) {
  return Internals::from_dec(d);
}
Value make_num(Dec&& d) {
  return Internals::from_dec(std::move(d));
}
Value make_int(long long n) {
  return make_num(dec_from_int(n));
}

}  // namespace

Value Value::none() { return Internals::raw(Kind::None, "", false); }

Value Value::null() { return none(); }

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
    throw SelError("E_NOT_NUM", "not a number: " + quote_text(decimal), Pos{});
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
  // Keys and values pair up, and every key is text (spec §8): a record from
  // host code is checked here
  // rather than failing later, in a dump or an INDEXES, far from the input.
  if (keys.size() != values.size()) {
    throw SelError("E_BAD_ARG", std::to_string(keys.size()) + " key(s) and " + std::to_string(values.size()) +
                   " value(s) do not pair up", Pos{});
  }
  for (const std::string& key : keys) {
    if (!sel::is_valid_utf8(key)) throw SelError("E_UTF8", "key is not valid UTF-8", Pos{});
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
  return Internals::shaped(intern_record_shape(std::move(keys)), std::move(values));
}

// A shape from host code is checked like a key list: text, each key once, one
// slot per key (spec §8). The
// interpreter's own shapes go through Internals::shaped.
Value Value::shaped(std::shared_ptr<const RecordShape> shape, std::vector<Value> storage) {
  if (!shape) throw SelError("E_BAD_ARG", "a shaped value needs a shape", Pos{});
  if (shape->keys.size() != storage.size()) {
    throw SelError("E_BAD_ARG", std::to_string(shape->keys.size()) + " key(s) and " +
                   std::to_string(storage.size()) + " value(s) do not pair up", Pos{});
  }
  std::unordered_set<std::string_view> seen;
  for (const std::string& key : shape->keys) {
    if (!sel::is_valid_utf8(key)) throw SelError("E_UTF8", "key is not valid UTF-8", Pos{});
    if (!seen.insert(key).second) {
      throw SelError("E_BAD_ARG", "a record shape cannot hold the key \"" + key + "\" twice", Pos{});
    }
  }
  return Internals::shaped(std::move(shape), std::move(storage));
}

Value Internals::shaped(std::shared_ptr<const RecordShape> shape, std::vector<Value> storage) {
  Value v(Value::make_collection_impl());
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

// The const lookup, handing back a pointer the caller may write through: the
// handle is non-const, and the storage it points into is this value's own.
Value* Value::get(const std::string& key) {
  return const_cast<Value*>(std::as_const(*this).get(key));
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
  // A key is text too (spec §8): ASCII keys, nearly
  // all of them, pass without the full check.
  for (const unsigned char c : key) {
    if (c >= 0x80) {
      if (!sel::is_valid_utf8(key)) throw SelError("E_UTF8", "key is not valid UTF-8", Pos{});
      break;
    }
  }
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
  if (p_->kind == Kind::None && size() == 0) return true;
  if (p_->kind == Kind::Text && size() == 0) {
    const std::string& sc = scalar();
    if (sc.empty()) return true;
    for (const char ch : sc) {
      if (!is_sel_space(static_cast<unsigned char>(ch))) return false;
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
    if (++guard > sel_limits::MAX_DEPTH) throw SelError("E_DEPTH", "scalar context nested too deeply", pos);
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
      if (!dec_mag_equal(a, b)) return false;
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
// one body for every host. A name it does not know is a host's own
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
  // The manifest's name is enough to say the library defined it: no name is
  // defined twice, so a manifest name defined before register_builtins() makes
  // the library's own definition refuse, and no program ever compiles.
  const auto* m = manifest_entry(spec.name);
  spec.shipped = m != nullptr;
  if (m) {
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

// An application's own functions (spec §8.1), apart from the builtins so that
// replacing one never touches a Spec an already-compiled program points at: the
// replaced Spec moves to `retired` and lives as long as the process.
std::map<std::string, std::shared_ptr<const Spec>>& host_table() {
  static std::map<std::string, std::shared_ptr<const Spec>> t;
  return t;
}

std::vector<std::shared_ptr<const Spec>>& retired_host_specs() {
  static std::vector<std::shared_ptr<const Spec>> v;
  return v;
}

// Registering while other threads compile or run is safe (spec §8.1): every
// access to the host table and to the retired list is under this lock. A Spec a
// reader has been handed stays valid after it is replaced, because the replaced
// one is retired rather than freed.
std::shared_mutex& host_table_mutex() {
  static std::shared_mutex m;
  return m;
}

const Spec* registry_lookup(const std::string& name) {
  auto it = table().find(name);
  if (it != table().end()) return &it->second;
  std::shared_lock<std::shared_mutex> lock(host_table_mutex());
  auto h = host_table().find(name);
  return h == host_table().end() ? nullptr : h->second.get();
}

void register_builtins();   // defined after the built-ins themselves
void math_ops_check();      // defined with the math plan; every manifest operation has an opcode
void lexicon_check();       // defined with the operator codes; every lexicon operator has one

// Every entry point that can parse must go through this first: the table has to
// be complete before any source is read.
void ensure_registered() {
  static bool done = [] {
    register_builtins();
    assert_manifest_covered();
    math_ops_check();
    lexicon_check();
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

// The symbol tokens and the reserved words are spec/lexicon.json's
// (sel_lexicon.hpp). Longest match first: `$<=` must not lex as `$<` followed
// by `=`, and the rendering is in that order.
const std::vector<std::string>& operators() {
  static const std::vector<std::string> ops(std::begin(sel_lexicon::SYMBOLS), std::end(sel_lexicon::SYMBOLS));
  return ops;
}

bool is_reserved(const std::string& w) {
  static const std::set<std::string> r(std::begin(sel_lexicon::RESERVED), std::end(sel_lexicon::RESERVED));
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

class Lexer {
 public:
  explicit Lexer(const std::string& source) {
    // Decoding here also validates the source: bad UTF-8 is E_UTF8 at the
    // first invalid unit rather than a silently mangled token.
    chars_ = decode_utf8(source, Pos{}, true);
    n_ = chars_.size();
    brace_ends_.assign(n_, 0);
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
  // brace_ends_[i] is the index just past the '}' matching the '{' at i, once
  // some scan has established it (0 = not yet). See match_brace.
  std::vector<std::uint32_t> brace_ends_;
  // One stack of open '(' / '[' per interpolation body (see lex_tokens), indexed
  // by Task::bal. Grows by one entry per interpolation, so it stays linear.
  std::vector<std::vector<char>> bals_;

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

  // For a range already known to be ASCII (digits, identifiers): no encoder.
  std::string ascii_slice(std::size_t from, std::size_t to) const {
    std::string out(to - from, '\0');
    for (std::size_t k = from; k < to; k++) out[k - from] = static_cast<char>(chars_[k]);
    return out;
  }

  struct Part {
    bool is_expr = false;
    std::string text;
    std::size_t from = 0, to = 0;
  };

  // The kinds of work lex_range keeps on its explicit stack.
  enum class TK { Range, Part, Close, End };
  struct Task {
    TK k = TK::Range;
    std::size_t i = 0, to = 0;   // Range
    Part part;                   // Part, Close
    std::size_t index = 0;       // Part
    std::size_t mark = 0;        // Close
    long bal = -1;               // Range, Close: index into bals_, -1 at top level
    Pos pos;                     // Part, End
  };

  // Lexes chars_[from, to) into `out`. Interpolation nests without bound, so
  // this is a loop over an explicit stack of tasks rather than a recursion: a
  // literal pushes what it still has to emit (its parts, each interior range,
  // the closers) and the loop pops them in source order. Nothing here can
  // therefore reach the host's own stack, however deep the braces go.
  void lex_range(std::size_t from, std::size_t to, std::vector<Token>& out) {
    std::vector<Task> stack;
    {
      Task t;
      t.k = TK::Range; t.i = from; t.to = to;
      stack.push_back(std::move(t));
    }
    bals_.clear();
    while (!stack.empty()) {
      Task task = std::move(stack.back());
      stack.pop_back();
      switch (task.k) {
        case TK::Range: lex_tokens(task.i, task.to, out, stack, task.bal); break;
        case TK::Part: emit_part(task, out, stack); break;
        case TK::Close:
          // An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
          if (out.size() == task.mark + 1) {
            fail("E_SYNTAX", "empty interpolation {}", pos_at(task.part.from));
          }
          // ... and one whose parentheses do not close inside the braces.
          if (!bals_[static_cast<std::size_t>(task.bal)].empty()) {
            fail("E_SYNTAX", "unclosed parenthesis in interpolation", pos_at(task.part.to));
          }
          out.push_back(Token{Tok::Op, ")", pos_at(task.part.to)});
          break;
        case TK::End: out.push_back(Token{Tok::Op, ")", task.pos}); break;
      }
    }
  }

  // The flat part of lex_range. A quoted literal with parts ends the run: the
  // tasks it pushes come first, and the rest of the range resumes after them.
  //
  // `bal` names the stack of parentheses and brackets open so far in an
  // interpolation body (-1 at the top level, where the parser does the
  // balancing). A body is spliced into the surrounding tokens as `( body )`, so
  // a body that closes what it never opened, or leaves something open, would
  // change the meaning of the text around it; each body has to balance inside
  // its own braces. Only the body's own tokens count, not those of nested literals.
  void lex_tokens(std::size_t from, std::size_t to, std::vector<Token>& out,
                  std::vector<Task>& stack, long bal) {
    std::size_t i = from;
    while (i < to) {
      const char32_t c = chars_[i];

      if (is_sel_space(c)) { i++; continue; }

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
        out.push_back(Token{Tok::Num, ascii_slice(i, j), pos});
        i = j;
        continue;
      }

      if (is_alpha(c)) {
        std::size_t j = i;
        while (j < to && is_ident(chars_[j])) j++;
        // Identifiers are ASCII and case-insensitive; upper case is canonical.
        std::string word = ascii_slice(i, j);
        for (char& ch : word) ch = ascii_up(ch);
        out.push_back(Token{Tok::Ident, word, pos});
        i = j;
        continue;
      }

      if (c == U'"') {
        std::size_t next = 0;
        std::vector<Part> parts = scan_quoted(i, to, next);
        if (parts.size() == 1) {
          out.push_back(Token{Tok::Text, parts[0].text, pos});
          i = next;
          continue;
        }
        // `( "seg" & expr & "seg" )`: the opener now, the rest as tasks, the
        // remainder of this range underneath them.
        out.push_back(Token{Tok::Op, "(", pos});
        {
          Task t;
          t.k = TK::Range; t.i = next; t.to = to; t.bal = bal;
          stack.push_back(std::move(t));
        }
        {
          Task t;
          t.k = TK::End; t.pos = pos;
          stack.push_back(std::move(t));
        }
        for (std::size_t k = parts.size(); k-- > 0;) {
          Task t;
          t.k = TK::Part; t.part = std::move(parts[k]); t.index = k; t.pos = pos;
          stack.push_back(std::move(t));
        }
        return;
      }
      if (c == U'\'') { i = lex_raw(i, to, out); continue; }

      const std::string op = match_operator(i, to);
      if (!op.empty()) {
        if (bal >= 0) {
          std::vector<char>& open = bals_[static_cast<std::size_t>(bal)];
          if (op == "(" || op == "[") {
            open.push_back(op[0]);
          } else if (op == ")" || op == "]") {
            if (open.empty() || (open.back() == '(') != (op == ")")) {
              fail("E_SYNTAX", "unbalanced " + op + " in interpolation", pos);
            }
            open.pop_back();
          }
        }
        out.push_back(Token{Tok::Op, op, pos});
        i += op.size();
        continue;
      }

      fail("E_SYNTAX", "unexpected character " + describe_char(chars_[i]), pos);
    }
  }

  // One part of an interpolated literal: the `&` before it, then either its text
  // or `( interior )`, the interior being a range of its own.
  void emit_part(Task& task, std::vector<Token>& out, std::vector<Task>& stack) {
    if (task.index > 0) out.push_back(Token{Tok::Op, "&", task.pos});
    if (!task.part.is_expr) {
      out.push_back(Token{Tok::Text, task.part.text, task.pos});
      return;
    }
    const std::size_t mark = out.size();
    const long bal = static_cast<long>(bals_.size());
    bals_.emplace_back();
    out.push_back(Token{Tok::Op, "(", pos_at(task.part.from)});
    {
      Task t;
      t.k = TK::Close; t.mark = mark; t.bal = bal;
      t.part = Part{true, "", task.part.from, task.part.to};
      stack.push_back(std::move(t));
    }
    {
      Task t;
      t.k = TK::Range; t.i = task.part.from; t.to = task.part.to; t.bal = bal;
      stack.push_back(std::move(t));
    }
  }

  // The operators that start with each ASCII byte, longest first (the order of operators()):
  // one table lookup instead of a scan over all 31 per token.
  static const std::vector<const std::string*>& operators_starting_with(char32_t c) {
    static const std::array<std::vector<const std::string*>, 128> by_first = [] {
      std::array<std::vector<const std::string*>, 128> table;
      for (const std::string& op : operators()) {
        table[static_cast<unsigned char>(op[0])].push_back(&op);
      }
      return table;
    }();
    static const std::vector<const std::string*> none;
    return c < 128 ? by_first[static_cast<std::size_t>(c)] : none;
  }

  std::string match_operator(std::size_t i, std::size_t to) const {
    for (const std::string* candidate : operators_starting_with(chars_[i])) {
      const std::string& op = *candidate;
      if (i + op.size() > to) continue;
      bool ok = true;
      for (std::size_t k = 1; k < op.size(); k++) {   // the first byte is the bucket
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

  // Reads a quoted literal into its parts and the index just past its closing
  // quote (`next`), emitting nothing. Every `{...}` is skipped by match_brace,
  // so the interior is not read here, only located.
  std::vector<Part> scan_quoted(std::size_t start, std::size_t to, std::size_t& next_out) {
    const Pos pos = pos_at(start);
    std::vector<Part> parts;
    std::string buf;
    std::size_t i = start + 1;

    while (i < to) {
      const char32_t c = chars_[i];

      if (c == U'"') {
        parts.push_back(Part{false, buf, 0, 0});
        next_out = i + 1;
        return parts;
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
        fail("E_RANGE", "code point U+" + ascii_upper(hex) + " is not encodable", pos);
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
  //
  // One pass with an explicit stack of what is open (a brace, a string), not a
  // recursion through the strings, and every brace it closes is remembered in
  // brace_ends_. The second half is what keeps the lexer linear: a literal
  // nested d deep is located by its parent and again by each of its own
  // ancestors' interiors being lexed, and without the memo each of those
  // locate-passes re-read everything below it. If anything is unterminated the
  // innermost open construct is the one reported, which is where the recursion
  // used to fail.
  std::size_t match_brace(std::size_t i, std::size_t to) {
    if (brace_ends_[i] != 0) return brace_ends_[i];
    struct Open { bool str; std::size_t at; int depth; };
    std::vector<Open> open;
    open.push_back(Open{false, i, 0});
    std::size_t j = i;
    for (;;) {
      Open& top = open.back();
      if (j >= to) {
        fail("E_UNTERMINATED",
             top.str ? "unterminated text literal" : "unterminated { in text literal",
             pos_at(top.at));
      }
      const char32_t c = chars_[j];
      if (top.str) {
        if (c == U'\\') { j += 2; continue; }
        if (c == U'"') { open.pop_back(); j++; continue; }
        if (c == U'{') { open.push_back(Open{false, j, 0}); continue; }
        j++;
        continue;
      }
      if (c == U'"') { open.push_back(Open{true, j, 0}); j++; continue; }
      if (c == U'\'') { j = skip_raw(j, to); continue; }
      if (c == U'{') { top.depth++; j++; continue; }
      if (c == U'}') {
        top.depth--; j++;
        if (top.depth == 0) {
          brace_ends_[top.at] = static_cast<std::uint32_t>(j);
          open.pop_back();
          if (open.empty()) return j;
        }
        continue;
      }
      if (c == U'#') { while (j < to && chars_[j] != U'\n') j++; continue; }
      j++;
    }
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
};

std::vector<Token> tokenize(const std::string& source) { return Lexer(source).tokenize(); }

}  // namespace

// ============================================================================
// --- parser
//
// Precedence climbing. The sixteen levels of spec/SPEC.md §5 are the table
// below rather than sixteen functions, so adding an operator is adding a row.
// python/sel/parser.py is the reference implementation of this shape and its
// module docstring is the rationale; docs/contributing.md, "Adding an operator",
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

// The operator vocabulary is spec/lexicon.json's (sel_lexicon.hpp): the tokens,
// the reserved words, every infix and prefix operator's binding power and
// associativity, its family and comparison relation. The parser's tables are
// BUILT from it below, and the optimiser, the join pre-filter and the SQL
// translator ask infix_op() (sel_ast.hpp) rather than keeping lists -- so the
// parser and everything after it cannot disagree about what a comparison is.
//
// What stays this host's own is the operators' MEANING: the BinOp codes and
// the evaluator's dispatch on them. lexicon_check() refuses to start when the
// two disagree -- a lexicon binary operator with no code, a code no lexicon
// operator has, a comparison whose code is not at its relation's offset, an
// arithmetic operator outside BO_ADD..BO_MOD -- so an operator added to the
// lexicon and forgotten here fails at startup instead of meaning something else.

// A binary operator as a number, resolved once per node (Node::opc) instead of by
// a chain of string comparisons on every evaluation. The comparison codes
// are contiguous and in the lexicon's relation order (eq ne lt le gt ge), so the
// relation of a comparison is `code - BO_NUM_EQ` / `code - BO_TXT_EQ`.
enum BinOp : unsigned char {
  BO_NONE = 0,
  BO_AND, BO_OR, BO_COALESCE, BO_VACUOUS,
  BO_ADD, BO_SUB, BO_MUL, BO_DIV, BO_MOD, BO_CONCAT,
  BO_EQL, BO_IN, BO_XOR, BO_BAND, BO_BOR, BO_BXOR,
  BO_NUM_EQ, BO_NUM_NE, BO_NUM_LT, BO_NUM_LE, BO_NUM_GT, BO_NUM_GE,
  BO_TXT_EQ, BO_TXT_NE, BO_TXT_LT, BO_TXT_LE, BO_TXT_GT, BO_TXT_GE,
  BO_COUNT
};

unsigned char bin_opcode(const std::string& op) {
  static const std::unordered_map<std::string, unsigned char> table = {
      {"AND", BO_AND}, {"OR", BO_OR}, {"??", BO_COALESCE}, {"???", BO_VACUOUS},
      {"+", BO_ADD}, {"-", BO_SUB}, {"*", BO_MUL}, {"/", BO_DIV}, {"%", BO_MOD}, {"&", BO_CONCAT},
      {"EQL", BO_EQL}, {"IN", BO_IN}, {"XOR", BO_XOR}, {"BAND", BO_BAND}, {"BOR", BO_BOR}, {"BXOR", BO_BXOR},
      {"==", BO_NUM_EQ}, {"!=", BO_NUM_NE}, {"<", BO_NUM_LT}, {"<=", BO_NUM_LE}, {">", BO_NUM_GT}, {">=", BO_NUM_GE},
      {"$==", BO_TXT_EQ}, {"$!=", BO_TXT_NE}, {"$<", BO_TXT_LT}, {"$<=", BO_TXT_LE}, {"$>", BO_TXT_GT}, {"$>=", BO_TXT_GE},
  };
  const auto it = table.find(op);
  return it == table.end() ? static_cast<unsigned char>(BO_NONE) : it->second;
}

// The lexicon record of each BinOp code: the evaluator asks it by code with one
// array read. Filled by lexicon_check(), which ensure_registered() runs before
// anything is parsed -- so before any node exists to ask about, and with no
// dependence on the order static initialisers run in.
std::array<const sel_lexicon::Op*, BO_COUNT> OP_BY_CODE{};

// Whether the binary operator with code `opc` has the lexicon family `f`.
inline bool opc_in(unsigned char opc, sel_lexicon::Family f) {
  const sel_lexicon::Op* op = opc < BO_COUNT ? OP_BY_CODE[opc] : nullptr;
  return op != nullptr && op->family == f;
}

// Whether the right side of the operator with code `opc` may never run (AND,
// OR, `??`, `???`): the lexicon's short-circuit flag, by code.
inline bool opc_short_circuits(unsigned char opc) {
  const sel_lexicon::Op* op = opc < BO_COUNT ? OP_BY_CODE[opc] : nullptr;
  return op != nullptr && op->short_circuit;
}

// Whether the comparison relation `kind` -- the lexicon's relation index, 0 ==
// 1 != 2 < 3 <= 4 > 5 >= -- holds for the three-way result `c`.
bool cmp_holds(int kind, int c) {
  switch (kind) {
    case 0: return c == 0;
    case 1: return c != 0;
    case 2: return c < 0;
    case 3: return c <= 0;
    case 4: return c > 0;
    default: return c >= 0;
  }
}

// The arithmetic operators BO_ADD..BO_MOD on two decimals: the one dispatch the
// binary operators, the compound assignments and the constant folder share.
Dec dec_arith(unsigned char opc, const Dec& a, const Dec& b, Pos pos) {
  switch (opc) {
    case BO_ADD: return dec_add(a, b, pos);
    case BO_SUB: return dec_sub(a, b, pos);
    case BO_MUL: return dec_mul(a, b, pos);
    case BO_DIV: return dec_div(a, b, pos);
    default: return dec_mod(a, b, pos);
  }
}

// The binary operator a compound assignment applies (`+=` is BO_ADD, ...): the
// lexicon's `compound`, indexed by the assignment's first byte -- one array read
// per evaluation. lexicon_check() refuses two compound forms sharing a first byte.
// Filled by lexicon_check(), like OP_BY_CODE.
std::array<unsigned char, 256> COMPOUND_BY_FIRST{};

unsigned char compound_opcode(char first) {
  return COMPOUND_BY_FIRST[static_cast<unsigned char>(first)];
}

// Called once at startup (ensure_registered): fills the by-code tables, then
// requires the host's operator codes and the lexicon to describe the same
// operators.
void lexicon_check() {
  for (const sel_lexicon::Op& op : sel_lexicon::OPS) {
    if (op.compound != nullptr) COMPOUND_BY_FIRST[static_cast<unsigned char>(op.token[0])] = bin_opcode(op.compound);
    if (op.fixity != sel_lexicon::Fixity::Infix || std::strcmp(op.node, "bin") != 0) continue;
    const unsigned char code = bin_opcode(op.token);
    if (code != BO_NONE) OP_BY_CODE[code] = &op;
  }
  const auto refuse = [](const std::string& what) {
    throw std::logic_error("spec/lexicon.json and this host's operator codes disagree: " + what);
  };
  int bin_ops = 0;
  for (const sel_lexicon::Op& op : sel_lexicon::OPS) {
    if (op.compound != nullptr) {
      const unsigned char code = bin_opcode(op.compound);
      if (code != BO_CONCAT && !(code >= BO_ADD && code <= BO_MOD)) refuse(std::string(op.token) + " applies no arithmetic or concatenation code");
      if (compound_opcode(op.token[0]) != code) refuse(std::string(op.token) + " shares its first byte with another compound form");
    }
    if (op.fixity != sel_lexicon::Fixity::Infix || std::strcmp(op.node, "bin") != 0) continue;
    ++bin_ops;
    const unsigned char code = bin_opcode(op.token);
    if (code == BO_NONE) refuse(std::string(op.token) + " has no BinOp code");
    using F = sel_lexicon::Family;
    if (op.family == F::Compare && code != BO_NUM_EQ + op.relation) refuse(std::string(op.token) + " is not at its relation's numeric-comparison code");
    if (op.family == F::TextCompare && code != BO_TXT_EQ + op.relation) refuse(std::string(op.token) + " is not at its relation's text-comparison code");
    if ((op.family == F::Arith) != (code >= BO_ADD && code <= BO_MOD)) refuse(std::string(op.token) + ": arithmetic is exactly BO_ADD..BO_MOD");
  }
  if (bin_ops != BO_COUNT - 1) refuse("the host has " + std::to_string(BO_COUNT - 1) + " codes for " + std::to_string(bin_ops) + " binary operators");
}

// The binding powers, higher binds tighter: the lexicon's levels (SPEC §5 read
// loosest first). `;` and `,` are N-ary loops outside the infix table.
constexpr int BP_ASSIGN = sel_lexicon::BP_ASSIGN;

// An infix operator's binding power and associativity, and its lexicon record.
// 'L' parses its right side at bp + 1, 'R' at bp -- that is what makes it
// right-associative -- and 'N' at bp + 1 and then rejects a second operator at
// the same level.
struct Infix {
  int bp;
  char assoc;
  const sel_lexicon::Op* op;
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
  //
  // Both are built from the lexicon's infix records; `;` and `,` stay out, since
  // parse_sequence and parse_list loop over them as N-ary operators.
  static std::map<std::string, Infix> infix_table(bool words) {
    std::map<std::string, Infix> m;
    for (const sel_lexicon::Op& op : sel_lexicon::OPS) {
      if (op.fixity != sel_lexicon::Fixity::Infix || op.word != words) continue;
      if (op.family == sel_lexicon::Family::List || op.family == sel_lexicon::Family::Sequence) continue;
      m[op.token] = Infix{op.bp, op.assoc, &op};
    }
    return m;
  }

  static const Infix* infix_entry(const Token& t) {
    static const std::map<std::string, Infix> ops = infix_table(false);
    static const std::map<std::string, Infix> words = infix_table(true);

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
      const Token& t = peek();
      const Infix* e = infix_entry(t);
      if (e == nullptr || e->bp < min_bp) return left;

      next();

      if (e->assoc == 'R') {
        if (e->op->family == sel_lexicon::Family::Assign) {
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

        // Counted like an assignment (SPEC §6.4): `??` and `???` are the other
        // right-associative operators, and their right side recurses through
        // neither parse_sequence nor parse_primary.
        enter(t.pos);
        const Leave leave_guard{this};
        auto n = make(NT::Bin, t.pos);
        n->s = t.value;
        n->opc = bin_opcode(n->s);
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
        n->opc = bin_opcode(n->s);
        n->l = left;
        n->r = right;
        left = n;
        continue;
      }

      auto n = make(NT::Bin, t.pos);
      n->s = t.value;
      n->opc = bin_opcode(n->s);
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
  //
  // The prefix operators -- their tokens, binding powers and the names their
  // nodes record (NOT, NEG) -- are the lexicon's.
  static const std::vector<const sel_lexicon::Op*>& prefix_ops() {
    static const std::vector<const sel_lexicon::Op*> ops = [] {
      std::vector<const sel_lexicon::Op*> v;
      for (const sel_lexicon::Op& op : sel_lexicon::OPS) {
        if (op.fixity == sel_lexicon::Fixity::Prefix) v.push_back(&op);
      }
      return v;
    }();
    return ops;
  }

  NodePtr parse_prefix(int min_bp) {
    const Token& t = peek();

    if (t.type == Tok::Ident || t.type == Tok::Op) {
      for (const sel_lexicon::Op* op : prefix_ops()) {
        if (op->word != (t.type == Tok::Ident) || t.value != op->token || min_bp > op->bp) continue;
        next();
        enter(t.pos);
        const Leave leave_guard{this};
        auto n = make(NT::Un, t.pos);
        n->s = op->name;
        n->l = parse_term(op->bp);
        return n;
      }
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

    return finish_call(name_tok, spec, std::move(args));
  }

  NodePtr parse_primary() {
    const Token& t = peek();
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
      // Marked so that F((1,2)) passes one list rather than two arguments. The
      // node the sequence just returned has no other owner in the ordinary case,
      // so it is marked in place: copying it copied the whole `items` vector, once
      // per parenthesis -- ninety of them around a wide list made a parse seven
      // times slower. Node documents the const_cast (created non-const by
      // make_shared, only held as const); a node that somebody else holds is still
      // copied.
      if (inner.use_count() == 1) {
        const_cast<Node&>(*inner).grouped = true;
        return inner;
      }
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
    return finish_call(name_tok, spec, std::move(args));
  }

  // The compile-time arity rule (spec §6.2, SEL-0002) and the call node, in
  // one place for both call forms; the pipeline form has already placed its
  // left operand in `args`. Every refusal reports the name token.
  NodePtr finish_call(const Token& name_tok, const Spec* spec, std::vector<NodePtr> args) {
    const int count = static_cast<int>(args.size());
    // VARIADIC is "no upper bound", not a bound of 2^20: past it the count is the
    // size caps' business (spec §6.4, E_RANGE at the call), never E_ARITY.
    if (count < spec->min || (spec->max < VARIADIC && count > spec->max)) {
      fail("E_ARITY", spec->name + " takes " + arity_text(*spec) + ", got " + std::to_string(count),
           name_tok.pos);
    }
    if (spec->arity_error) {
      const std::string problem = spec->arity_error(count);
      if (!problem.empty()) fail("E_ARITY", problem, name_tok.pos);
    }
    // A regex pattern that is a literal is checked NOW (spec §7.8): a bad one in a
    // branch that never runs is still a bad program. A computed pattern is checked
    // when it is used. The flag argument is read only to know whether `i` is on,
    // which the ambiguity analysis needs; a bad flag is still E_BAD_ARG at run time.
    // Where the pattern and the flags are is the manifest's (REGEX_CALLS).
    if (const sel_builtin_manifest::RegexCall* rx = regex_call(spec->name)) {
      const auto pat_at = static_cast<std::size_t>(rx->pattern);
      const auto flag_at = static_cast<std::size_t>(rx->flags);
      if (args.size() > pat_at && args[pat_at]->t == NT::Text) {
        bool ic = false;
        if (args.size() > flag_at && args[flag_at]->t == NT::Text) ic = args[flag_at]->s.find('i') != std::string::npos;
        (void)validate_pattern(args[pat_at]->s, args[pat_at]->pos, ic);
      }
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

// The join pre-filter's hand-off (SEL-0049, SEL-0050, SEL-0052). A FILTER
// whose source is a LINK hands the join its conjuncts; the join pre-applies to
// its left rows those whose fields no right side carries. Plain data here, so
// that Context can hold it; the logic is beside do_link and FILTER.
struct JoinConjunct {
  const Node* node = nullptr;           // a conjunct of the FILTER body, in the tree
  bool field_only = false;              // reads nothing but fields of the row
  std::unordered_set<std::string> fields;   // upper-cased; `_["orders"]["x"]` reads ORDERS
  bool has_total = false;               // a comparison of literals and bare fields
  std::vector<std::pair<std::string, bool>> total;   // (field as written, numeric)
  std::string binder;                   // the FILTER's element name
};
struct JoinStage {
  std::string binder;
  std::vector<JoinConjunct> conjuncts;
  std::size_t above = 0;                // joins between its FILTER and the join testing it
};
// What the totality check knows about one side's rows: the union of its keys
// (with the names its row is bound under), its first row's keys (a field
// every row carries is on the first one: a cheap refusal before a scan),
// per-field facts on demand, and whether its rows may be null-extended (a
// LINK_LEFT's right).
struct JoinSideFacts {
  Value value;
  std::unordered_set<std::string> keys;
  std::unordered_set<std::string> first;
  bool nullable = false;
  std::unordered_set<std::string> names;   // member names the row is bound under (never `_1`/`_2`)
  std::unordered_map<std::string, bool> facts;
};
// A join's left key, handed down with its conjuncts: the join that drops rows
// must prove it cannot raise on them (join_keys_safe).
struct JoinObligation {
  const Node* key = nullptr;
  std::unordered_set<std::string> row_names;
  std::size_t outer = 0;
};
struct JoinPrefilter {
  std::vector<JoinStage> stages;
  bool deep = false;
  std::vector<std::shared_ptr<JoinSideFacts>> above;
  std::vector<JoinObligation> obligations;
};
struct JoinReport {
  std::unordered_set<const Node*> applied;
  bool errored = false;
  bool dropped = false;
};
// A FILTER directly over a LINK_LEFT whose predicate opens with
// IS_NULL(_["member"]["field"]) hands the join this: the join may skip
// building the joined rows of the right rows that conjunct is FALSE on
// (join_right_null_rejects). Nothing is reported back.
struct JoinRightNull {
  std::string member;
  std::string field;
  bool deep = false;                    // nothing observes the FILTER's keys
};

// Context, Args and eval_node are at sel:: scope rather than in the anonymous
// namespace above, and not by preference: Spec's `fn` is a
// `Value (*)(Args&, Context&)`, Node holds a `const Spec*`, and Node lives in
// sel_ast.hpp so that a second translation unit can walk the tree. A type in an
// anonymous namespace cannot be named across translation units, so naming Spec
// in a header names these two as well. eval_node comes with them because its
// declaration sits between them and has to be on the same side as its
// definition.
// Where a variable was last found in a root record, by the address of the
// name asked for: the AST's or a plan step's string, which live as long as the
// program, so one reference reads the same name every time. Only a hint: the
// key at that position is compared with the name before the child is used
// (Internals::child_if_keyed), and keys in a plain record are unique, so a
// stale entry -- another run's root, another program's string at a reused
// address, a collision -- costs a search and never a wrong value. Per thread,
// not per run: nothing to set up when a program starts, and 256 entries keep a
// program's names from evicting one another (64 thrashed on Mandelbrot's 50).
// Saves the record search -- a scan of the root's index -- on nearly every
// read and write of a variable.
namespace {
struct RootHint {
  const std::string* name = nullptr;
  std::size_t index = 0;
};
thread_local std::array<RootHint, 256> root_hints{};
}  // namespace

struct Context {
  Value* root;
  // Aggregate binders. The only scoping SEL has: one name for the duration of
  // one element, pushed by the aggregates and popped again afterwards.
  std::vector<std::vector<std::pair<std::string, Value>>> frames;
  int depth = 0;
  // A FILTER over a LINK hands the join its conjuncts here; the join reports
  // back which ones every row it emitted has passed, whether it kept a row on
  // an error, and whether it dropped any (SEL-0052, SEL-0054).
  std::optional<JoinPrefilter> join_prefilter;
  std::optional<JoinReport> join_prefilter_report;
  // A FILTER over a LINK_LEFT that opens with IS_NULL of a right member's
  // field hands the join (member, field, keys unobserved) here.
  std::optional<JoinRightNull> join_right_null;
  // Nothing in the tree being evaluated can write: it holds no assignment and
  // no call to an application's function (may_have_effects; Program::run
  // decides it once per program, writes_nothing). Assignment is the one way a
  // program changes a value, so no copy a collector or a constructor makes
  // (spec §3.4) can be told from the value it copies: they keep what they
  // collect as it is, after the depth check the copy would have made. Off
  // unless the Program proved it.
  bool write_free = false;
  // The frames of the math plans running now, one after another. A plan's slots
  // are addressed by index, never by pointer or reference held across a step
  // that can evaluate: a load may run a whole nested plan, which grows both
  // vectors. The raw vector holds what the plan's loads produced until
  // an arithmetic step coerces it.
  std::vector<Dec> math_scratchpad;
  std::vector<std::optional<Value>> math_raw;
  size_t math_scratchpad_top = 0;

  explicit Context(Value& r) : root(&r) {}

  const Value* lookup(const std::string& name) const {
    for (auto it = frames.rbegin(); it != frames.rend(); ++it) {
      for (const auto& e : *it) {
        if (e.first == name) return &e.second;
      }
    }
    return root_find(name);
  }

  // The root record's own `name` (Value::get), through the hints.
  const Value* root_find(const std::string& name) const {
    if (!Internals::plain_record(*root)) return root->get(name);
    RootHint& hint = root_hints[(reinterpret_cast<std::uintptr_t>(&name) * 0x9E3779B97F4A7C15ULL) >> 56];
    if (hint.name == &name) {
      if (const Value* v = Internals::child_if_keyed(*root, hint.index, name)) return v;
    }
    std::size_t index = 0;
    const Value* v = Internals::find_child(*root, name, index);
    if (v) {
      hint.name = &name;
      hint.index = index;
    }
    return v;
  }

  // root->set(name, value) (spec §5.7: a variable that exists keeps its
  // place), straight into the child a hint finds; a new name, or a root that
  // is not a plain record, goes through Value::set.
  void root_store(const std::string& name, const Value& value) {
    if (Internals::plain_record(*root)) {
      if (const Value* v = root_find(name)) {
        *const_cast<Value*>(v) = value;   // root is ours to write: it is non-const
        return;
      }
    }
    root->set(name, value);
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
namespace {
const Dec& as_dec_ref(const Value& v, Pos pos);
}  // namespace

class Args {
 public:
  Args(const Node& node, Context& ctx)
      : nodes_(node.items), record_shape_(node.record_shape), name_(node.s), pos_(node.pos), ctx_(ctx) {
    // Up to kInline arguments live in the object; only a wider call allocates.
    const std::size_t n = node.items.size();
    if (n <= kInline) {
      vals_ = inline_;
    } else {
      heap_ = std::make_unique<std::optional<Value>[]>(n);
      vals_ = heap_.get();
    }
  }
  Args(const Args&) = delete;
  Args& operator=(const Args&) = delete;

  int count() const { return static_cast<int>(nodes_.size()); }
  const Node& node(int i) const { return *nodes_[i]; }
  const std::shared_ptr<const RecordShape>& record_shape() const { return record_shape_; }
  NodePtr node_ptr(int i) const { return nodes_[i]; }
  const std::vector<NodePtr>& nodes() const { return nodes_; }
  Pos pos_of(int i) const { return nodes_[i]->pos; }
  Pos pos() const { return pos_; }
  const std::string& name() const { return name_; }

  // The argument's value, moved out: for built-ins that read each argument once
  // and keep it (LIST, RECORD), so a fresh temporary can be adopted without a copy.
  Value take_val(int i) {
    val(i);
    return std::move(*vals_[i]);   // stays engaged (moved-from): a later val(i) must not re-evaluate
  }

  const Value& val(int i) {
    if (!vals_[i].has_value()) vals_[i] = eval_node(*nodes_[i], ctx_);
    return *vals_[i];
  }

  // For lazy functions re-evaluating a body node under changed bindings.
  Value eval(const Node& n) { return eval_node(n, ctx_); }

  const std::string& text(int i) { return val(i).as_text(pos_of(i)); }
  const std::string& bytes(int i) { return val(i).as_bytes(pos_of(i)); }
  bool boolean(int i) { return val(i).as_bool(pos_of(i)); }

  // The operators' coercion (as_dec_ref), reported at this argument.
  Dec dec(int i) { return as_dec_ref(val(i), pos_of(i)); }

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
  static constexpr std::size_t kInline = 4;
  std::optional<Value> inline_[kInline];
  std::unique_ptr<std::optional<Value>[]> heap_;
  std::optional<Value>* vals_ = nullptr;
};

namespace {

// Byte-level substring search, linear in the haystack for any needle (glibc's memmem
// switches to the two-way algorithm for long needles; std::string::find is O(n*m) on
// aaaa..ab style inputs: a 200 KB needle in 400 KB took 1.6-6.7 s).
// UTF-8 is self-synchronising, so a byte match of a valid needle is a code-point match.
inline std::size_t byte_find(const std::string& hay, const std::string& needle, std::size_t from) {
  if (from > hay.size()) return std::string::npos;
  if (needle.empty()) return from;
  if (needle.size() > hay.size() - from) return std::string::npos;
#if defined(__GLIBC__)
  const void* at = memmem(hay.data() + from, hay.size() - from, needle.data(), needle.size());
  return at == nullptr ? std::string::npos
                       : static_cast<std::size_t>(static_cast<const char*>(at) - hay.data());
#else
  return hay.find(needle, from);   // no two-way memmem here: same answer, worst case O(n*m)
#endif
}

// Code points in a UTF-8 string that is already known to be valid.
std::size_t cp_count(const std::string& s) {
  std::size_t n = 0;
  for (const unsigned char c : s) {
    if ((c & 0xC0) != 0x80) n++;
  }
  return n;
}

// The size caps of spec §6.4: what an operation BUILDS is measured before any of
// it is allocated, and past MAX_TEXT_LEN (code points of TEXT, bytes of BIN) or
// MAX_COLLECTION (children) it is E_RANGE at the node that builds it. Lengths
// arrive as u128 so that `count * length` cannot itself overflow.
void cap_text(u128 n, Pos pos) {
  if (n > static_cast<u128>(MAX_TEXT_LEN)) {
    fail("E_RANGE", "result would be longer than " + std::to_string(MAX_TEXT_LEN) + " units", pos);
  }
}

void cap_collection(u128 n, Pos pos) {
  if (n > static_cast<u128>(MAX_COLLECTION)) {
    fail("E_RANGE", "collection would have more than " + std::to_string(MAX_COLLECTION) + " children",
         pos);
  }
}

// TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2). `opos` is the
// operator, where a result past the length cap is reported.
Value concat(const Value& l, const Value& r, Pos lp, Pos rp, Pos opos) {
  const Value& lv = l.scalar_source(lp);
  const Value& rv = r.scalar_source(rp);
  if (lv.kind() == Kind::Bool) fail("E_NOT_TEXT", "cannot concatenate a boolean", lp);
  if (rv.kind() == Kind::Bool) fail("E_NOT_TEXT", "cannot concatenate a boolean", rp);
  if (lv.kind() == Kind::Text && rv.kind() == Kind::Text) {
    cap_text(static_cast<u128>(cp_count(lv.scalar())) + cp_count(rv.scalar()), opos);
    return make_text(lv.scalar() + rv.scalar());
  }
  const std::string a = l.as_bytes(lp);   // sequenced: left before right
  const std::string b = r.as_bytes(rp);
  cap_text(static_cast<u128>(a.size()) + b.size(), opos);
  return make_bin(a + b);
}

// `pos` is where an E_DEPTH from walking a value nested past the cap is
// reported: the operator, as EQL reports it, never 0:0 (spec §6.3).
bool is_in(const Value& needle, const Value& hay, Pos pos) {
  if (hay.size() == 0) return hay.eql(needle, pos);
  for (const auto& e : hay.entries()) {
    if (e.second.eql(needle, pos)) return true;
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
    // Measured before it is collected (spec §6.4): a chain of `(A, A)` is a
    // doubling and must stop at the cap, not at the host's memory.
    cap_collection(static_cast<u128>(out.size()) +
                       ((v.kind() == Kind::None && v.size() > 0) ? v.size() : 1),
                   node.pos);
    if (v.kind() == Kind::None && v.size() > 0) {
      // Cloned, not aliased: `,` copies what it collects (§5.9), so the list it
      // builds does not share structure with the values that fed it
      // (conformance/25-value-ownership.selt) -- unless nothing can write.
      for (const auto& child : v.entries()) {
        if (ctx.write_free) {
          Internals::check_clone_depth(child.second, 2, node.pos);
          out.push_back(child.second);
        } else {
          out.push_back(child.second.clone_below(1, node.pos));
        }
      }
    } else {
      out.push_back(hold_or_adopt(std::move(v), 1, node.pos, ctx.write_free));
    }
  }
  return Value::list(std::move(out));
}

// Numeric coercion (spec §3.2), the one body every reader of a number shares: the
// operators, Args::dec (at the argument's own position) and Value::as_decimal.
// A view of the cache on the Value the number lives in (filled here on first use),
// valid while `v` is: a Dec owns a digit string and a vector of binary words, so
// a comparison loop that copied both sides on every call -- a sort does n log n
// of them -- spent a third of its time there. as_dec is the copy.
const Dec& as_dec_ref(const Value& v, Pos pos) {
  const Value& src = v.scalar_source(pos);
  if (!Internals::has_dec(src)) {
    if (src.kind() != Kind::Text) {
      fail("E_NOT_NUM",
           std::string("expected a number, got ") +
               (src.kind() == Kind::Bin ? "bin" : src.kind() == Kind::Bool ? "bool" : "none"),
           pos);
    }
    Dec d;
    if (!dec_parse(src.scalar(), d, pos)) fail("E_NOT_NUM", "not a number: " + quote_text(src.scalar()), pos);
    Internals::set_dec(src, std::move(d));
  }
  return Internals::dec_ref(src);
}

Dec as_dec(const Value& v, Pos pos) { return as_dec_ref(v, pos); }

}  // namespace

Dec Value::as_decimal(Pos pos) const { return as_dec_ref(*this, pos); }

std::string dec_digits(const Dec& d) { return dec_get_digits(d); }

namespace {

Value apply_binary(const Node& node, unsigned char opc, const Value& l, const Value& r);

Value apply_unary(const Node& node, const Value& v) {
  if (node.s == "NOT") return Value::boolean(!v.as_bool(node.l->pos));
  const Dec d = as_dec(v, node.l->pos);
  return make_num(dec_negate(d));
}

Value eval_unary(const Node& node, Context& ctx) {
  const Value v = eval_node(*node.l, ctx);
  return apply_unary(node, v);
}

// How many nodes of `n` probe_eval walks itself (the rest it hands to eval_node), counted
// up to `limit`: a bound on the evaluation depth it stands in for.
int probe_size(const Node& n, int limit) {
  if (limit <= 0) return 1;
  switch (n.t) {
    case NT::Var: return 1;
    case NT::Index:
      if (n.r->t != NT::Text) return 1;
      return 1 + (n.l->t == NT::Var ? 0 : probe_size(*n.l, limit - 1));
    case NT::Un: return n.math_plan ? 1 : 1 + probe_size(*n.l, limit - 1);
    case NT::Bin: {
      const unsigned char opc = n.opc ? n.opc : bin_opcode(n.s);
      if (n.math_plan || opc_short_circuits(opc)) return 1;
      const int left = probe_size(*n.l, limit - 1);
      return 1 + left + probe_size(*n.r, limit - 1 - left);
    }
    default: return 1;
  }
}

// An aggregate's binder frame (a binder names one element for the duration of
// that element): pushed for the walk, popped by pop() where the walk is done, or
// by the destructor however else the walk ends -- an error included, which is
// what every hand-written push/try/catch/pop it replaces was for. One that
// pushes nothing (TOP without a body) pops nothing.
class FrameScope {
 public:
  using Frame = std::vector<std::pair<std::string, Value>>;
  explicit FrameScope(Context& ctx) : ctx_(ctx) {}
  FrameScope(Context& ctx, Frame frame) : ctx_(ctx) { push(std::move(frame)); }
  FrameScope(const FrameScope&) = delete;
  FrameScope& operator=(const FrameScope&) = delete;
  ~FrameScope() { pop(); }
  void push(Frame frame) {
    ctx_.frames.push_back(std::move(frame));
    pushed_ = true;
  }
  void pop() {
    if (pushed_) {
      ctx_.frames.pop_back();
      pushed_ = false;
    }
  }

 private:
  Context& ctx_;
  bool pushed_ = false;
};

// Shifts ctx.depth by the levels probe_eval skipped while a subtree that it does not
// walk is evaluated, so E_DEPTH is raised at the node it would have been raised at.
struct DepthShift {
  Context& ctx;
  int by;
  DepthShift(Context& c, int n) : ctx(c), by(n) { ctx.depth += by; }
  ~DepthShift() { ctx.depth -= by; }
};

// eval_node for the small shapes `??` is usually given, with a miss (the two codes `??`
// swallows: E_UNDEF_VAR, E_NO_KEY) reported as `false` rather than thrown. `level` is the
// depth eval_node would have given this node (1 = directly under the `??`). Evaluation
// order, coercion order and every other error are exactly eval_binary's / eval_unary's:
// the operands are evaluated left then right and only then combined by apply_binary.
bool probe_eval(const Node& n, Context& ctx, Value& out, int level) {
  switch (n.t) {
    case NT::Var: {
      const Value* v = ctx.lookup(n.s);
      if (!v) return false;
      out = *v;
      return true;
    }
    case NT::Index: {
      if (n.r->t != NT::Text) break;
      Value obj;
      if (n.l->t == NT::Var) {
        const Value* v = ctx.lookup(n.l->s);
        if (!v) return false;
        obj = *v;
      } else if (!probe_eval(*n.l, ctx, obj, level + 1)) {
        return false;
      }
      const Value* child = obj.get(n.r->s);
      if (!child) return false;
      out = *child;
      return true;
    }
    case NT::Un: {
      if (n.math_plan) break;
      Value v;
      if (!probe_eval(*n.l, ctx, v, level + 1)) return false;
      out = apply_unary(n, v);
      return true;
    }
    case NT::Bin: {
      const unsigned char opc = n.opc ? n.opc : bin_opcode(n.s);
      if (n.math_plan || opc_short_circuits(opc)) break;
      Value l, r;
      if (!probe_eval(*n.l, ctx, l, level + 1)) return false;
      if (!probe_eval(*n.r, ctx, r, level + 1)) return false;
      out = apply_binary(n, opc, l, r);
      return true;
    }
    default: break;
  }
  const DepthShift shift(ctx, level - 1);
  out = eval_node(n, ctx);
  return true;
}

Value eval_binary(const Node& node, Context& ctx) {
  const std::string& op = node.s;
  const unsigned char opc = node.opc ? node.opc : bin_opcode(op);

  // Short-circuit before either side is touched (§5.5).
  if (opc == BO_AND || opc == BO_OR) {
    const bool left = eval_node(*node.l, ctx).as_bool(node.l->pos);
    if (opc == BO_AND && !left) return Value::boolean(false);
    if (opc == BO_OR && left) return Value::boolean(true);
    return Value::boolean(eval_node(*node.r, ctx).as_bool(node.r->pos));
  }

  // ?? falls back on NULL, ??? on any vacuous value; both on a missing key or
  // name.
  if (opc == BO_COALESCE || opc == BO_VACUOUS) {
    // A left operand that is a plain name or a chain of literal-key indexes over
    // one is probed without evaluating it: a miss there is the ordinary case for
    // `??` and used to cost a C++ exception (about 20 us) each. The probe finds
    // exactly what eval_node would (the same lookup, the same get), treats a
    // miss exactly as the two codes the catch below swallows, and is skipped when
    // the depth budget could run out inside the chain, so E_DEPTH is still the
    // evaluator's to raise.
    {
      int links = 0;
      const Node* base = node.l.get();
      while (base->t == NT::Index && base->r->t == NT::Text) { ++links; base = base->l.get(); }
      if (base->t == NT::Var && ctx.depth + links + 2 <= MAX_DEPTH) {
        const Value* cur = ctx.lookup(base->s);
        if (cur) {
          // Walk the chain outermost-last: collect the keys bottom-up.
          std::vector<const Node*> chain;
          for (const Node* n = node.l.get(); n->t == NT::Index; n = n->l.get()) chain.push_back(n);
          for (auto it = chain.rbegin(); cur && it != chain.rend(); ++it) cur = cur->get((*it)->r->s);
        }
        if (cur) {
          if (!(opc == BO_COALESCE ? cur->is_null() : cur->is_vacuous())) return *cur;
        }
        return eval_node(*node.r, ctx);
      }
    }
    // A left operand that is a small tree of operators over names and literal-key
    // indexes (`(_["k"] & "x") ?? 0`, `A["a"] + 1 ??? 0`) is evaluated by probe_eval,
    // which reports a miss instead of throwing it: the exception cost about 10 us a
    // row. Anything it does not understand is evaluated by
    // eval_node inside it, so the try below still catches what that throws.
    const int probed = probe_size(*node.l, 13);
    const bool probe = probed <= 12 && ctx.depth + probed + 1 <= MAX_DEPTH;
    try {
      Value l;
      if (probe) {
        if (probe_eval(*node.l, ctx, l, 1) &&
            !(opc == BO_COALESCE ? l.is_null() : l.is_vacuous())) return l;
      } else {
        l = eval_node(*node.l, ctx);
        if (!(opc == BO_COALESCE ? l.is_null() : l.is_vacuous())) return l;
      }
    } catch (const SelError& e) {
      if (e.code() != "E_NO_KEY" && e.code() != "E_UNDEF_VAR") throw;
    }
    return eval_node(*node.r, ctx);
  }

  const Value l = eval_node(*node.l, ctx);
  const Value r = eval_node(*node.r, ctx);
  return apply_binary(node, opc, l, r);
}

// The operator proper, on operands already evaluated (both, left then right: §6.2).
// Split from eval_binary so the `??` probe (probe_eval) applies exactly the same
// coercions, in the same order, with the same positions.
Value apply_binary(const Node& node, unsigned char opc, const Value& l, const Value& r) {
  const std::string& op = node.s;
  const Pos lp = node.l->pos, rp = node.r->pos;

  // Each pair of coercions below is sequenced through named locals rather than
  // written as two arguments to one call. The order of evaluation of function
  // arguments is unspecified in C++ — GCC evaluates them right to left — and SEL
  // requires strictly left to right (§6.2), which is observable: `TRUE $== FALSE`
  // must report the *left* operand's position. The differential fuzzer found
  // this; do not collapse these back into one expression.
  switch (opc) {
    case BO_ADD: case BO_SUB: case BO_MUL: case BO_DIV: case BO_MOD: {
      const Dec& a = as_dec_ref(l, lp);
      const Dec& b = as_dec_ref(r, rp);
      return make_num(dec_arith(opc, a, b, node.pos));
    }
    case BO_CONCAT: return concat(l, r, lp, rp, node.pos);
    case BO_EQL: return Value::boolean(l.eql(r, node.pos));
    case BO_IN: return Value::boolean(is_in(l, r, node.pos));
    case BO_XOR: {
      const bool a = l.as_bool(lp);
      const bool b = r.as_bool(rp);
      return Value::boolean(a != b);
    }
    case BO_BAND: case BO_BOR: case BO_BXOR: {
      const std::string a = l.as_bytes(lp);
      const std::string b = r.as_bytes(rp);
      return bitwise(op, a, b, node.pos);
    }
    case BO_TXT_EQ: case BO_TXT_NE: case BO_TXT_LT: case BO_TXT_LE: case BO_TXT_GT: case BO_TXT_GE: {
      const std::string a = l.as_bytes(lp);
      const std::string b = r.as_bytes(rp);
      return Value::boolean(cmp_holds(opc - BO_TXT_EQ, bytes_compare(a, b)));
    }
    case BO_NUM_EQ: case BO_NUM_NE: case BO_NUM_LT: case BO_NUM_LE: case BO_NUM_GT: case BO_NUM_GE: {
      const Dec& a = as_dec_ref(l, lp);
      const Dec& b = as_dec_ref(r, rp);
      return Value::boolean(cmp_holds(opc - BO_NUM_EQ, dec_cmp(a, b)));
    }
    default: break;
  }

  fail("E_SYNTAX", "unknown operator " + op, node.pos);
}

// What `target op= rhs` stores: the binary operator applied to the target's value
// as read before the right-hand side ran, coerced target first (§6.2), with the
// operands' errors at their own positions and the result's at the assignment.
Value compound_value(const Node& node, const Value& target, const Value& rhs) {
  const Pos tp = node.l->pos, vp = node.r->pos;
  const unsigned char opc = compound_opcode(node.s[0]);
  if (opc == BO_CONCAT) return concat(target, rhs, tp, vp, node.pos);
  const Dec& a = as_dec_ref(target, tp);
  const Dec& b = as_dec_ref(rhs, vp);
  return make_num(dec_arith(opc, a, b, node.pos));
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
// every evaluation. That is **not** a C++ workaround — every host does it
// (docs/contributing.md, "The traps"). It is
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
      // A fresh temporary is adopted, anything shared is cloned (§3.4): the
      // same rule, and the same E_DEPTH, as the indexed form below.
      value = adopt_or_clone(eval_node(*node.r, ctx), 0, node.pos);
      ctx.root_store(var_name, value);
      return value;
    } else {
      const Value* current = ctx.root_find(var_name);
      if (!current) fail("E_UNDEF_VAR", node.s + " needs an existing target", node.l->pos);
      const Value target_value = *current;

      const Value rhs = eval_node(*node.r, ctx);
      value = compound_value(node, target_value, rhs);
      // Re-derived after the right-hand side ran (§5.7): it may have created a
      // variable, and the root's child vector moved, so the pointer taken before
      // it is dangling. The store lands where the name is now, not where it was.
      ctx.root_store(var_name, value);
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
    // instead of the value that was assigned. Every host clones in exactly
    // this position, for exactly this reason (conformance/25-value-ownership.selt).
    //
    // The depth cap counts the path to the target as well as the value (§6.4):
    // `path.size() - 1` levels of index sit above where it lands, and the error is
    // the target's, not the assignment's.
    value = adopt_or_clone(eval_node(*node.r, ctx), static_cast<int>(path.size()) - 1, node.l->pos);
  } else {
    const Value* current = walk_create(ctx, path, path.size() - 1)->get(key);
    if (!current) fail("E_UNDEF_VAR", node.s + " needs an existing target", node.l->pos);
    const Value target_value = *current;   // copied: the right-hand side may move the tree

    const Value rhs = eval_node(*node.r, ctx);
    value = compound_value(node, target_value, rhs);
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
        return Internals::from_dec(*node.dec);
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
      // An index over a bare variable reads the variable in place: the index
      // costs its level and the variable none, so the limit in
      // `A["a"] AND A["a"] AND ...` sits one level deeper than counting the
      // variable as a node of its own would put it (pinned by
      // lim.eval-depth.aggregate-body-*; every host reads it that way).
      // A literal key -- `_["id"]`, nearly every index -- evaluates nothing
      // between the lookup and the read, so the variable is read where it
      // stands, with no handle of its own (an empty Value allocates one).
      // A computed key may rebind the variable, which must not change the
      // value already read (spec §3.4), so that one is held.
      const Value* var = nullptr;
      if (node.l->t == NT::Var) {
        var = ctx.lookup(node.l->s);
        if (!var) fail("E_UNDEF_VAR", "undefined variable " + node.l->s, node.l->pos);
        if (node.r->t == NT::Text) {
          const Value* child = var->get(node.r->s);
          if (!child) fail("E_NO_KEY", "no key " + quote_dump(node.r->s), node.pos);
          return *child;
        }
      }
      const Value obj = var ? *var : eval_node(*node.l, ctx);
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
      if (node.spec->host) {
        HostArgs host_args(args);
        return (*node.spec->host)(host_args);
      }
      return node.spec->fn(args, ctx);
    }
  }
  fail("E_SYNTAX", "cannot evaluate node", node.pos);
}

// Gives the frame's scratchpad slots back on every exit, a throw included: a
// caught error (`??` swallows E_UNDEF_VAR and E_NO_KEY) used to leave the top
// advanced, so each one permanently consumed a plan's worth of slots.
struct MathFrame {
  Context& ctx;
  const MathPlan& plan;
  size_t base;
  MathFrame(Context& c, const MathPlan& p) : ctx(c), plan(p), base(c.math_scratchpad_top) {
    const size_t need = base + plan.scratchpad_size;
    if (need > ctx.math_scratchpad.size()) {
      const size_t grown = std::max(need, ctx.math_scratchpad.size() * 2 + 32);
      ctx.math_scratchpad.resize(grown);
      ctx.math_raw.resize(grown);
    }
    ctx.math_scratchpad_top = need;
  }
  ~MathFrame() {
    for (const MathStep& step : plan.steps) {
      if (step.op == MathOp::LoadVar || step.op == MathOp::LoadLeaf) ctx.math_raw[base + step.dst].reset();
    }
    ctx.math_scratchpad_top = base;
  }
  MathFrame(const MathFrame&) = delete;
  MathFrame& operator=(const MathFrame&) = delete;
};

Value eval_math_plan(const MathPlan& plan, Context& ctx) {
  const MathFrame frame(ctx, plan);
  const size_t base = frame.base;
  // Every access goes through the context vectors by index: LoadLeaf evaluates
  // a subtree, which may run another plan and reallocate them.
  const auto slot = [&](uint32_t i) -> Dec& { return ctx.math_scratchpad[base + i]; };
  // An operand: the coerced number, or the raw load coerced now (spec §6.2).
  // A raw load is read in place, not copied into its slot: the loaded value
  // stays held in math_raw until the frame ends, every load is read by exactly
  // one step (the plan is a tree), and an arithmetic step evaluates nothing
  // between coercing its operands and using them -- so the reference cannot
  // move or change under the step. Copying it cost a heap copy of every large
  // operand's words.
  const auto operand = [&](uint32_t i, bool raw, const Pos& pos) -> const Dec& {
    if (raw) return as_dec_ref(*ctx.math_raw[base + i], pos);
    return slot(i);
  };

  for (const MathStep& step : plan.steps) {
    switch (step.op) {
      case MathOp::LoadVar: {
        const Value* v = ctx.lookup(step.name);
        if (!v) fail("E_UNDEF_VAR", "undefined variable " + step.name, step.pos);
        ctx.math_raw[base + step.dst] = *v;
        break;
      }
      case MathOp::LoadConst: {
        slot(step.dst) = step.const_val;
        break;
      }
      case MathOp::LoadLeaf: {
        Value val = eval_node(*step.leaf_node, ctx);
        ctx.math_raw[base + step.dst] = std::move(val);
        break;
      }
      case MathOp::Coerce: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        slot(step.dst) = a;
        break;
      }
      case MathOp::Add: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_add(a, b, step.pos);
        break;
      }
      case MathOp::Sub: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_sub(a, b, step.pos);
        break;
      }
      case MathOp::Mul: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_mul(a, b, step.pos);
        break;
      }
      case MathOp::Div: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_div(a, b, step.pos);
        break;
      }
      case MathOp::Mod: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_mod(a, b, step.pos);
        break;
      }
      case MathOp::Neg:
        slot(step.dst) = dec_negate(operand(step.src1, step.raw1, step.src1_pos));
        break;
      case MathOp::Abs:
        slot(step.dst) = dec_abs(operand(step.src1, step.raw1, step.src1_pos));
        break;
      case MathOp::Sign:
        slot(step.dst) = dec_from_int(dec_sign(operand(step.src1, step.raw1, step.src1_pos)));
        break;
      case MathOp::Ceil:
        slot(step.dst) = dec_ceil(operand(step.src1, step.raw1, step.src1_pos), step.pos);
        break;
      case MathOp::Floor:
        slot(step.dst) = dec_floor(operand(step.src1, step.raw1, step.src1_pos), step.pos);
        break;
      case MathOp::Trunc:
        slot(step.dst) = dec_trunc(operand(step.src1, step.raw1, step.src1_pos));
        break;
      case MathOp::Round: {
        // The value first, then the scale: the first argument's coercion error
        // is reported before the second's (spec §6.2, §7.1).
        const Dec& x = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& d2 = operand(step.src2, step.raw2, step.src2_pos);
        const long long n = checked_size_arg(d2, "ROUND", "scale", MAX_SCALE, step.aux_pos);
        slot(step.dst) = dec_round(x, n, step.pos);
        break;
      }
      case MathOp::Power: {
        const Dec& x = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& d2 = operand(step.src2, step.raw2, step.src2_pos);
        const long long n = checked_size_arg(d2, "POWER", "exponent", MAX_POWER, step.aux_pos);
        slot(step.dst) = dec_power(x, n, step.pos);
        break;
      }
      case MathOp::Min: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_cmp(b, a) < 0 ? b : a;
        break;
      }
      case MathOp::Max: {
        const Dec& a = operand(step.src1, step.raw1, step.src1_pos);
        const Dec& b = operand(step.src2, step.raw2, step.src2_pos);
        slot(step.dst) = dec_cmp(b, a) > 0 ? b : a;
        break;
      }
    }
  }
  return make_num(std::move(slot(plan.output_slot)));
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

// What an aggregate visits is a SNAPSHOT of the collection taken when it starts
// (spec §7.3): the element handles, and the keys if a body asks for `_K`. A body
// may assign into the very collection it is walking -- append a key, add one,
// overwrite a later element, replace the variable -- and none of that changes
// what is visited. Holding `const Value&` into the live storage instead was a
// use-after-free the moment the body grew it: the storage moved under the
// reference. Handles are shared, not deep-copied, so mutation *inside* an element
// is still seen, as the spec says.
//
// Where nothing the walk runs can write -- the program cannot (Context::write_free),
// or the body, key or predicate the walk evaluates per element cannot
// (walks_in_place) -- nothing can change the collection while it is walked, so
// the snapshot would be the collection itself: the walk reads the collection in
// place (`live`). Taking it anyway was a pass over every element to count a
// handle and another to drop it -- over a join's rows, two trips to memory per
// row for nothing (scale scenario 6) -- and a program that calls an
// application's function anywhere took it for every walk in it (scenarios 2
// and 3). Internals::exclusively_held counts the snapshot's handle, so a walk
// that read in place says so (walk_handles).
struct Snapshot {
  const Value* live = nullptr;
  std::size_t count = 0;
  std::vector<Value> items;
  std::vector<std::string> keys;   // filled only when asked for
  std::size_t size() const { return count; }
  const Value& item(std::size_t i) const { return live ? collection_item(*live, i) : items[i]; }
  std::string key(std::size_t i) const { return live ? collection_key(*live, i) : keys[i]; }
};

Snapshot take_snapshot(const Value& value, bool want_keys, bool write_free) {
  Snapshot snap;
  const std::size_t count = collection_size(value);
  snap.count = count;
  if (write_free) {
    snap.live = &value;
    return snap;
  }
  snap.items.reserve(count);
  for (std::size_t i = 0; i < count; i++) snap.items.push_back(collection_item(value, i));
  if (want_keys) {
    snap.keys.reserve(count);
    for (std::size_t i = 0; i < count; i++) snap.keys.push_back(collection_key(value, i));
  }
  return snap;
}

// Whether a walk may read its collection in place: nothing it evaluates per
// element -- `per_element`, or nothing at all when that is null -- can write,
// or nothing in the program can.
// Called once per walk, never per element: kept out of line, since sel.cpp is at
// GCC's inline-unit-growth limit and every inlined copy is growth some hot path pays.
[[gnu::noinline]] bool walks_in_place(const Context& ctx, const Node* per_element) {
  return ctx.write_free || !per_element || writes_nothing_cached(*per_element);
}

// The handles a walk holds on each element, for exclusively_held: `held` of its
// own (the binder's frame, a candidate) and the snapshot's, unless it read the
// collection in place.
std::uint32_t walk_handles(const Snapshot& snap, std::uint32_t held) { return held + (snap.live ? 0 : 1); }

template <typename Fn>
void for_each_snapshot_value(const Snapshot& snap, Fn&& fn) {
  for (std::size_t i = 0; i < snap.size(); ++i) fn(snap.item(i));
}

bool is_nested_record(const Value& value) { return value.size() > 0 && !value.is_list(); }

// nullptr means "there is no first element"; a NULL or field-less first
// element is an element like any other (spec §7.4) and comes back as one.
const Value* first_collection_item(const Value& value) {
  if (value.kind() == Kind::None && value.size() == 0) return nullptr;
  return collection_size(value) == 0 ? nullptr : &collection_item(value, 0);
}

struct AliasPlanKey {
  std::shared_ptr<const RecordShape> source;
  std::string table;

  bool operator==(const AliasPlanKey& other) const noexcept {
    return source.get() == other.source.get() && table == other.table;
  }
};

// boost's hash_combine: folds `v` into `h`.
constexpr std::size_t hash_combine(std::size_t h, std::size_t v) {
  return h ^ (v + static_cast<std::size_t>(0x9e3779b9) + (h << 6) + (h >> 2));
}

struct AliasPlanKeyHash {
  std::size_t operator()(const AliasPlanKey& key) const noexcept {
    const std::size_t source_hash = std::hash<const RecordShape*>{}(key.source.get());
    const std::size_t table_hash = std::hash<std::string>{}(key.table);
    return hash_combine(source_hash, table_hash);
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
  const std::string lower = ascii_lower(table);
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

// A shaped row extended with the name as PLAN lays it out.
Value alias_by_plan(const Value& row, const AliasPlan& plan) {
  std::vector<Value> storage;
  storage.reserve(plan.destination->keys.size());
  const auto& old_storage = row.storage();
  storage.insert(storage.end(), old_storage.begin(), old_storage.end());
  storage.push_back(row);
  if (plan.append_lower) storage.push_back(row);
  return Internals::shaped(plan.destination, std::move(storage));
}

Value ensure_row_table_alias(const Value& row, const std::string& table) {
  if (table.empty() || is_positional_binder(table) || row.has(table)) return row;
  if (const auto& old_shape = row.shape()) return alias_by_plan(row, *alias_plan_for(old_shape, table));
  Value out = Value::none();
  for (const auto& [key, value] : row.entries()) out.set(key, value);
  out.set(table, row);
  const std::string lower = ascii_lower(table);
  if (lower != table && !row.has(lower)) out.set(lower, row);
  return out;
}

// ensure_row_table_alias over the rows of one side of a join: what it decides
// of a shaped row -- whether the shape has the name, else the alias plan -- is
// a function of the shape alone, so it is kept for the last shape seen rather
// than asked again for every row (a lock and two string hashes: a tenth of the
// instructions of scale scenario 6's join). The shape is held, so a
// shape freed and another allocated at its address cannot be mistaken for it.
struct RowAliaser {
  const std::string& table;
  std::shared_ptr<const RecordShape> last;
  std::shared_ptr<const AliasPlan> plan;   // null: the shape has the name

  explicit RowAliaser(const std::string& name) : table(name) {}
  Value operator()(const Value& row) {
    const auto& shape = row.shape();
    if (!shape || table.empty() || is_positional_binder(table)) return ensure_row_table_alias(row, table);
    if (shape != last) {
      last = shape;
      plan = shape->key_map.count(table) ? nullptr : alias_plan_for(shape, table);
    }
    return plan ? alias_by_plan(row, *plan) : row;
  }
};

// An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the first
// right element as bound -- SAMPLE, already extended with the name where it
// was -- every field NULL; with no right elements, just the name keys; with no
// name either, NULL.
Value make_null_record(const Value& sample, const std::string& table) {
  Value out = Value::none();
  if (!sample.is_null()) {
    for (const std::string& key : sample.keys()) out.set(key, Value::none());
    return out;
  }
  if (!table.empty() && !is_positional_binder(table)) {
    out.set(table, Value::none());
    const std::string lower = ascii_lower(table);
    if (lower != table) out.set(lower, Value::none());
  }
  return out;
}

// A field's category for the joined row (spec §7.4): a nested record (a record
// with a field) is carried, anything else is a scalar field -- and a right
// scalar that is NULL is not promoted.
enum : char { kJoinScalar = '0', kJoinNull = '1', kJoinNested = '2' };

char join_category(const Value& value) {
  if (value.kind() != Kind::None || value.is_list()) return kJoinScalar;
  return value.size() > 0 ? kJoinNested : kJoinNull;
}

std::vector<std::string> join_binder_keys(const std::string& name, const char* positional) {
  std::vector<std::string> keys{name};
  const std::string lower = ascii_lower(name);
  if (lower != name) keys.push_back(lower);
  if (name != positional) keys.push_back(positional);
  return keys;
}

// One joined row, from THIS pair's two elements (spec §7.4): the nested records
// the left element carries, the left binders, the right binders, then the left
// element's scalar fields whose names (ASCII-case-insensitively) are not the
// right element's, then the right element's non-NULL scalar fields whose names
// are not the left's -- each key once, where it first occurred, a binder key
// holding the row this LINK bound (Value::set on an existing key replaces the
// value in place). RIGHT is null for an unmatched LINK_LEFT row, whose right
// element is NULL_RIGHT and promotes nothing.
Value make_joined_row(const Value& left, const Value* right, const std::string& b1,
                      const std::string& b2, const Value& null_right) {
  Value out = Value::none();
  const auto put = [&out](const std::string& key, const Value& value) {
    if (!out.has(key)) out.set(key, value);
  };
  const auto left_entries = left.entries();
  for (const auto& [key, value] : left_entries) {
    if (join_category(value) == kJoinNested) put(key, value);
  }
  for (const std::string& name : join_binder_keys(b1, "_1")) out.set(name, left);
  const Value rside = right ? *right : null_right;
  for (const std::string& name : join_binder_keys(b2, "_2")) out.set(name, rside);
  std::vector<std::pair<std::string, Value>> right_entries;
  if (rside.size() > 0 && !rside.is_list()) right_entries = rside.entries();
  std::set<std::string> right_names;
  for (const auto& entry : right_entries) right_names.insert(ascii_upper(entry.first));
  for (const auto& [key, value] : left_entries) {
    if (join_category(value) != kJoinNested && !right_names.count(ascii_upper(key))) put(key, value);
  }
  if (right) {
    std::set<std::string> left_names;
    for (const auto& entry : left_entries) left_names.insert(ascii_upper(entry.first));
    for (const auto& [key, value] : right_entries) {
      if (join_category(value) == kJoinScalar && !left_names.count(ascii_upper(key))) put(key, value);
    }
  }
  return out;
}

// The joined row of a pair as a plan over the two elements' storage, for every
// pair whose elements have these shapes and field categories: the output shape
// and, per output key, where its value comes from. make_joined_row is the rule;
// this is it, compiled.
struct JoinPlan {
  // LeftSlot copies a left field that was not a nested record, LeftNested one
  // that was (the projector marks which).
  enum class Op : unsigned char { LeftSlot, LeftNested, RightSlot, Left, Right };
  std::shared_ptr<const RecordShape> shape;
  std::vector<Op> ops;
  std::vector<std::size_t> slots;
  // What the plan assumed and the row does not show: the left fields it leaves
  // out (slot, nested), and the right fields it leaves out for being NULL or
  // a record (see JoinProjector).
  std::vector<std::pair<std::size_t, bool>> lrest;
  std::vector<std::size_t> rkept;
};

JoinPlan make_join_plan(const Value& left, const Value& rside, bool matched, const std::string& b1,
                        const std::string& b2) {
  std::vector<std::string> keys;
  JoinPlan plan;
  std::unordered_map<std::string, std::size_t> at;
  const auto put = [&](const std::string& key, JoinPlan::Op op, std::size_t slot) {
    if (at.count(key)) return;
    at.emplace(key, keys.size());
    keys.push_back(key);
    plan.ops.push_back(op);
    plan.slots.push_back(slot);
  };
  const auto bind = [&](const std::string& key, JoinPlan::Op op) {
    auto it = at.find(key);
    if (it != at.end()) {
      plan.ops[it->second] = op;
    } else {
      put(key, op, 0);
    }
  };
  const auto& lkeys = left.shape()->keys;
  const auto& lstore = left.storage();
  for (std::size_t i = 0; i < lkeys.size(); ++i) {
    if (join_category(lstore[i]) == kJoinNested) put(lkeys[i], JoinPlan::Op::LeftSlot, i);
  }
  for (const std::string& name : join_binder_keys(b1, "_1")) bind(name, JoinPlan::Op::Left);
  for (const std::string& name : join_binder_keys(b2, "_2")) bind(name, JoinPlan::Op::Right);
  const std::vector<std::string> no_keys;
  const auto& rkeys = rside.shape() ? rside.shape()->keys : no_keys;
  std::set<std::string> right_names;
  for (const std::string& key : rkeys) right_names.insert(ascii_upper(key));
  for (std::size_t i = 0; i < lkeys.size(); ++i) {
    if (join_category(lstore[i]) != kJoinNested && !right_names.count(ascii_upper(lkeys[i]))) {
      put(lkeys[i], JoinPlan::Op::LeftSlot, i);
    }
  }
  if (matched) {
    const auto& rstore = rside.storage();
    std::set<std::string> left_names;
    for (const std::string& key : lkeys) left_names.insert(ascii_upper(key));
    for (std::size_t i = 0; i < rkeys.size(); ++i) {
      if (join_category(rstore[i]) == kJoinScalar && !left_names.count(ascii_upper(rkeys[i]))) {
        put(rkeys[i], JoinPlan::Op::RightSlot, i);
      }
    }
  }
  plan.shape = intern_record_shape(std::move(keys));
  return plan;
}

// Only two facts about a field decide a joined row (spec §7.4): on the left,
// whether it is a nested record (carried first) or not; on the right, whether
// it is a non-NULL scalar (promoted).
bool join_left_nested(const Value& v) {
  return v.kind() == Kind::None && !v.is_list() && v.size() > 0;
}

// Whether every field of a row is a non-NULL scalar, but for those named like
// a binder key, which no joined row takes from it. The fields to read are
// worked out once per shape.
struct JoinFlatTest {
  std::set<std::string> binder_names;
  const RecordShape* last_shape = nullptr;
  std::vector<std::size_t> slots;

  JoinFlatTest(const std::string& b1, const std::string& b2) {
    for (const std::string& k : join_binder_keys(b1, "_1")) binder_names.insert(k);
    for (const std::string& k : join_binder_keys(b2, "_2")) binder_names.insert(k);
  }

  bool operator()(const Value& row) {
    if (!row.shape()) return false;
    if (row.shape().get() != last_shape) {
      last_shape = row.shape().get();
      slots.clear();
      const auto& keys = row.shape()->keys;
      for (std::size_t i = 0; i < keys.size(); ++i) {
        if (!binder_names.count(keys[i])) slots.push_back(i);
      }
    }
    const auto& st = row.storage();
    for (std::size_t i : slots) {
      if (st[i].kind() == Kind::None && !st[i].is_list()) return false;
    }
    return true;
  }
};

// projector(left, right): make_joined_row, through compiled plans. For each
// pair of shapes the plans built so far are tried in turn; each checks the two
// facts it assumed of the fields it reads -- every left field, and the right
// fields whose names neither the left nor a binder has -- as it copies them,
// and a pair none fits gets its own plan from the rule (make_join_plan). A
// left row's matches come one after another, so the left checks are made once
// per left row; the last left row is held so its address cannot be reused.
struct JoinProjector {
  std::string b1;
  std::string b2;
  Value null_right;
  bool has_null_right = false;
  // Set by the join when it has seen that every right row is flat
  // (JoinFlatTest): a plan made from a flat row holds for every flat row of
  // its shape, so the right checks are skipped.
  bool right_flat = false;
  struct PairKey {
    const void* l;
    const void* r;
    bool matched;
    bool operator==(const PairKey& o) const { return l == o.l && r == o.r && matched == o.matched; }
  };
  struct PairHash {
    std::size_t operator()(const PairKey& k) const {
      return std::hash<const void*>()(k.l) * 31u + std::hash<const void*>()(k.r) * 2u + (k.matched ? 1u : 0u);
    }
  };
  mutable std::unordered_map<PairKey, std::deque<JoinPlan>, PairHash> plans;
  mutable PairKey last_pair{nullptr, nullptr, false};
  mutable std::deque<JoinPlan>* last_plans = nullptr;
  mutable const JoinPlan* last_plan = nullptr;
  mutable Value last_left;
  mutable bool have_last_left = false;

  // The row into OUT, or false when the pair breaks the plan's assumptions:
  // with CHECK_LEFT, that each left field is (or is not) a nested record as it
  // was; with CHECK_RIGHT, that a right field it copies is a non-NULL scalar
  // and that one it leaves out for being NULL or a record still is one.
  static bool build(const JoinPlan& plan, const Value& left, const Value& rside, bool check_left,
                    bool check_right, Value& out) {
    const auto& ls = left.storage();
    const auto& rs = rside.storage();
    if (check_left) {
      for (const auto& [slot, nested] : plan.lrest) {
        if (join_left_nested(ls[slot]) != nested) return false;
      }
    }
    if (check_right) {
      for (std::size_t slot : plan.rkept) {
        if (rs[slot].kind() != Kind::None || rs[slot].is_list()) return false;
      }
    }
    out = Internals::shaped_direct(plan.shape, plan.ops.size());
    auto& slots = Internals::storage(out);
    for (std::size_t i = 0; i < plan.ops.size(); ++i) {
      switch (plan.ops[i]) {
        case JoinPlan::Op::LeftSlot: {
          const Value& v = ls[plan.slots[i]];
          if (check_left && join_left_nested(v)) return false;
          slots.push_back(v);
          break;
        }
        case JoinPlan::Op::LeftNested: {
          const Value& v = ls[plan.slots[i]];
          if (check_left && !join_left_nested(v)) return false;
          slots.push_back(v);
          break;
        }
        case JoinPlan::Op::RightSlot: {
          const Value& v = rs[plan.slots[i]];
          if (check_right && v.kind() == Kind::None && !v.is_list()) return false;
          slots.push_back(v);
          break;
        }
        case JoinPlan::Op::Left: slots.push_back(left); break;
        case JoinPlan::Op::Right: slots.push_back(rside); break;
      }
    }
    return true;
  }

  void remember(const Value& left, const PairKey& key, std::deque<JoinPlan>* list, const JoinPlan* plan) const {
    if (!have_last_left || Internals::identity(last_left) != Internals::identity(left)) {
      last_left = left;
      have_last_left = true;
    }
    last_pair = key;
    last_plans = list;
    last_plan = plan;
  }

  Value operator()(const Value& left, const Value* right) const {
    const Value* rside = right ? right : (has_null_right ? &null_right : nullptr);
    if (!left.shape() || !rside || !rside->shape()) {
      return make_joined_row(left, right, b1, b2, has_null_right ? null_right : Value::none());
    }
    const PairKey key{left.shape().get(), rside->shape().get(), right != nullptr};
    Value out;
    std::deque<JoinPlan>* list;
    if (last_plan && key == last_pair) {
      // The same shapes as the last pair: its plan first, checking the left
      // row only when it is a new one.
      const bool same_left = have_last_left && Internals::identity(last_left) == Internals::identity(left);
      if (build(*last_plan, left, *rside, !same_left, !right_flat, out)) {
        if (!same_left) remember(left, key, last_plans, last_plan);
        return out;
      }
      list = last_plans;
    } else {
      list = &plans[key];
    }
    for (const JoinPlan& plan : *list) {
      if (build(plan, left, *rside, true, !right_flat, out)) {
        remember(left, key, list, &plan);
        return out;
      }
    }
    JoinPlan plan = make_join_plan(left, *rside, right != nullptr, b1, b2);
    const auto& ls = left.storage();
    std::vector<bool> copied(ls.size(), false);
    for (std::size_t i = 0; i < plan.ops.size(); ++i) {
      if (plan.ops[i] != JoinPlan::Op::LeftSlot) continue;
      copied[plan.slots[i]] = true;
      if (join_left_nested(ls[plan.slots[i]])) plan.ops[i] = JoinPlan::Op::LeftNested;
    }
    for (std::size_t i = 0; i < ls.size(); ++i) {
      if (!copied[i]) plan.lrest.emplace_back(i, join_left_nested(ls[i]));
    }
    // Right fields the left does not name that were left out for being NULL
    // or records must still be, for the plan to hold. (A scalar left out
    // because its name is already a key of the row stays out whatever it
    // holds; so does one named like a binder key, which the binder holds.)
    if (right) {
      std::set<std::string> left_names;
      for (const std::string& k : left.shape()->keys) left_names.insert(ascii_upper(k));
      std::set<std::string> binder_names;
      for (const std::string& k : join_binder_keys(b1, "_1")) binder_names.insert(k);
      for (const std::string& k : join_binder_keys(b2, "_2")) binder_names.insert(k);
      const auto& rkeys = rside->shape()->keys;
      const auto& rs = rside->storage();
      for (std::size_t i = 0; i < rkeys.size(); ++i) {
        if (!left_names.count(ascii_upper(rkeys[i])) && !binder_names.count(rkeys[i])
            && rs[i].kind() == Kind::None && !rs[i].is_list()) {
          plan.rkept.push_back(i);
        }
      }
    }
    list->push_back(std::move(plan));
    const JoinPlan& added = list->back();
    build(added, left, *rside, false, false, out);
    remember(left, key, list, &added);
    return out;
  }
};

// The walkers below run over a body BEFORE the evaluator has looked at it, so
// they see a tree the depth cap has not yet had a chance to refuse: `1+1+...`
// builds a left-deep tree as long as the source with no nesting the parser
// counts (spec §6.4: a walk of the tree counts nodes). They therefore use an
// explicit stack, and answer exactly however deep the tree is; the evaluator
// still raises E_DEPTH at the 201st node if it ever gets that far.
bool expr_depends_only(const Node& root, const std::set<std::string>& allowed) {
  std::vector<const Node*> todo{&root};
  while (!todo.empty()) {
    const Node* node = todo.back();
    todo.pop_back();
    if (!node) continue;
    switch (node->t) {
      case NT::Var:
        if (allowed.count(ascii_upper(node->s)) == 0) return false;
        break;
      case NT::Index: case NT::Bin: case NT::Assign:
        todo.push_back(node->l.get());
        todo.push_back(node->r.get());
        break;
      case NT::Call: case NT::Seq: case NT::List:
        for (const NodePtr& item : node->items) todo.push_back(item.get());
        break;
      case NT::Un:
        todo.push_back(node->l.get());
        break;
      default: break;
    }
  }
  return true;
}

bool node_contains_var(const Node& root, std::string_view wanted) {
  const std::string upper_wanted = ascii_upper(std::string(wanted));
  std::vector<const Node*> todo{&root};
  while (!todo.empty()) {
    const Node* node = todo.back();
    todo.pop_back();
    if (!node) continue;
    if (node->t == NT::Var && node->s.size() == upper_wanted.size()) {
      // Equal without spelling the name in capitals first: this runs for every
      // variable of every body, on every aggregate call, and the copy was the cost.
      bool same = true;
      for (std::size_t i = 0; i < node->s.size(); ++i) {
        if (ascii_up(node->s[i]) != upper_wanted[i]) { same = false; break; }
      }
      if (same) return true;
    }
    if (node->l) todo.push_back(node->l.get());
    if (node->r) todo.push_back(node->r.get());
    for (const NodePtr& item : node->items) if (item) todo.push_back(item.get());
  }
  return false;
}

struct JoinEqui {
  NodePtr left;
  NodePtr right;
  bool numeric = false;
  bool swapped = false;  // the predicate reads the right side on the operator's left
};

std::optional<JoinEqui> extract_join_equi(const Node& node, const std::string& b1,
                                          const std::string& b2) {
  // An equality of either comparison family (relation eq: `==`, `$==`).
  const sel_lexicon::Op* op = node.t == NT::Bin ? infix_op(node.s) : nullptr;
  if (!op || op->relation != 0 ||
      (op->family != sel_lexicon::Family::Compare && op->family != sel_lexicon::Family::TextCompare)) {
    return std::nullopt;
  }
  const bool numeric = op->family == sel_lexicon::Family::Compare;
  // With the same name on both sides the right binder shadows the left (spec
  // §7.4): every read of the name is the RIGHT element, so there is no left key
  // to extract, and the general path -- which binds the right last -- answers.
  if (ascii_upper(b1) == ascii_upper(b2)) return std::nullopt;
  const std::set<std::string> left{ascii_upper(b1), "_1", "_"};
  const std::set<std::string> right{ascii_upper(b2), "_2"};
  if (expr_depends_only(*node.l, left) && expr_depends_only(*node.r, right)) {
    return JoinEqui{node.l, node.r, numeric, false};
  }
  if (expr_depends_only(*node.r, left) && expr_depends_only(*node.l, right)) {
    return JoinEqui{node.r, node.l, numeric, true};
  }
  return std::nullopt;
}

struct FastJoinKey {
  // Bad: a value the comparison rejects -- never bucketed; the pair it meets
  // raises.
  enum class Type : uint8_t { Empty, Int64, SmallDec, BigDec, Text, Bad };
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
      case Type::Bad: return false;
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
        h = hash_combine(h, std::hash<int32_t>{}(k.scale));
        h ^= (k.neg ? 1 : 0);
        return h;
      }
      case FastJoinKey::Type::BigDec: {
        std::size_t h = std::hash<std::string>{}(k.text);
        h = hash_combine(h, std::hash<int32_t>{}(k.scale));
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
      key.type = FastJoinKey::Type::Bad;
      return key;
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
      // The digits of the mantissa AFTER the trailing zeros were dropped to
      // reach `s`: 12345678901234567890.50 and ...890.5 are one number and must
      // be one key. (Dropping the zeros from the scale but not the digits made
      // 1e23 spelled with a `.0` collide with 1e24.)
      key.text = dec_get_digits(d);
      for (int32_t t = d.scale; t > s && !key.text.empty(); --t) key.text.pop_back();
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
  // `$==` compares bytes, as the evaluator does (as_bytes): a BIN meets the
  // TEXT of its bytes and a list its scalar.
  try {
    key.text = value.as_bytes({});
    key.type = FastJoinKey::Type::Text;
  } catch (const SelError&) {
    key.type = FastJoinKey::Type::Bad;
  }
  return key;
}

// What the left keys are checked against: whether any right key is live (not
// NULL), the first live one if it was rejected, and the first rejected one.
struct JoinRightFacts {
  bool live = false;
  std::optional<Value> live_bad;
  std::optional<Value> bad;
};

void note_right_join_key(JoinRightFacts& facts, const std::optional<FastJoinKey>& key, const Value& value) {
  if (!key) return;
  if (key->type == FastJoinKey::Type::Bad) {
    if (!facts.live) facts.live_bad = value;
    if (!facts.bad) facts.bad = value;
  }
  facts.live = true;
}

[[noreturn]] void coerce_join_operand(const JoinEqui& equi, const Value& value, const Node& node) {
  if (equi.numeric) (void)as_dec(value, node.pos);
  else (void)value.as_bytes(node.pos);
  throw std::logic_error("a rejected join key did not raise");
}

// A left key meets the right keys pair by pair, in order, as the comparison
// would (spec §7.4): a rejected left key raises against the first live right
// key, a good one against the first rejected right key -- the operator's left
// operand coerced first. NULLs are never compared.
void check_join_pair(const JoinEqui& equi, const std::optional<FastJoinKey>& key, const Value& value,
                     const JoinRightFacts& facts) {
  if (!key || !facts.live) return;
  if (key->type == FastJoinKey::Type::Bad) {
    if (equi.swapped && facts.live_bad) coerce_join_operand(equi, *facts.live_bad, *equi.right);
    coerce_join_operand(equi, value, *equi.left);
  }
  if (facts.bad) coerce_join_operand(equi, *facts.bad, *equi.right);
}

std::string single_relation_name(const Node& node) {
  if (node.t == NT::Var) return node.s;
  if (node.t == NT::Call && !node.items.empty() && node.s != "LINK" && node.s != "LINK_LEFT") {
    return single_relation_name(*node.items.front());
  }
  return {};
}


// --- the join pre-filter (SEL-0049, SEL-0050, SEL-0052) ----------------------
//
// Decided here from the rows, at run time, so the physical tree stays a
// function of the AST. See docs/contributing.md for the rule in every host.

// Whether evaluating NODE can be observed only through its value: no
// assignment, no sequence, no call outside the shipped builtins (an
// application's own function may do anything, may_have_effects), no ABORT.
// Such a node may run out of order -- the right source of a join before the
// left.
bool join_pure_source(const Node* node) {
  if (!node) return true;
  switch (node->t) {
    case NT::Var: case NT::Num: case NT::Text: case NT::Bool: case NT::Null: return true;
    case NT::Index: case NT::Bin: return join_pure_source(node->l.get()) && join_pure_source(node->r.get());
    case NT::Un: return join_pure_source(node->l.get());
    case NT::List:
      for (const NodePtr& item : node->items) if (!join_pure_source(item.get())) return false;
      return true;
    case NT::Call:
      if (may_have_effects(node->spec) || node->s == "ABORT") return false;
      for (const NodePtr& item : node->items) if (!join_pure_source(item.get())) return false;
      return true;
    default: return false;
  }
}

struct StageStop { std::size_t stage; std::size_t conjunct; };

// The conjuncts a join may test before it joins, in stage order, and where
// the walk stopped. One whose fields are all owned by the left rows is applied
// to them; one that reads only through this join's right binder (right_here,
// `_["products"]["is_active"]`) is applied to the right rows; one that reads
// a field of some right side ends the walk (AND short-circuits left to right)
// UNLESS it is total here, in which case it is passed over for the join
// above; one that reads anything but fields ends it too. A deferral relies on
// no lower relation carrying the field; the join that has those rows repeats
// the walk with them. Each stage is judged against the joins between its
// FILTER and this join (JoinStage::above of them): a FILTER in the middle of a
// chain reads rows no join above it has touched.
struct JoinApplied {
  const JoinConjunct* conjunct;
  const JoinStage* stage;
  bool right;
};
template <typename Owned, typename Total, typename Right>
std::pair<std::vector<JoinApplied>, std::optional<StageStop>> join_stage_walk(
    const std::vector<JoinStage>& stages, Owned&& owned_here, Total&& total_here, Right&& right_here) {
  std::vector<JoinApplied> applied;
  for (std::size_t si = 0; si < stages.size(); ++si) {
    const JoinStage& stage = stages[si];
    for (std::size_t ci = 0; ci < stage.conjuncts.size(); ++ci) {
      const JoinConjunct& c = stage.conjuncts[ci];
      if (c.field_only && owned_here(c.fields, stage)) { applied.push_back({&c, &stage, false}); continue; }
      if (c.field_only && right_here(c.fields, stage)) { applied.push_back({&c, &stage, true}); continue; }
      if (c.has_total && total_here(c.total, stage)) continue;
      return {applied, StageStop{si, ci}};
    }
  }
  return {applied, std::nullopt};
}

std::vector<JoinStage> join_truncate_stages(const std::vector<JoinStage>& stages,
                                            const std::optional<StageStop>& stop) {
  if (!stop) return stages;
  std::vector<JoinStage> out(stages.begin(), stages.begin() + static_cast<std::ptrdiff_t>(stop->stage));
  if (stop->conjunct) {
    JoinStage partial{stages[stop->stage].binder, {}, stages[stop->stage].above};
    partial.conjuncts.assign(stages[stop->stage].conjuncts.begin(),
                             stages[stop->stage].conjuncts.begin() + static_cast<std::ptrdiff_t>(stop->conjunct));
    out.push_back(std::move(partial));
  }
  return out;
}

// NODE with every `_["orders"]` -- a read through the left binder's own name
// (NAMES, upper-cased) -- replaced by `_`: on the left rows themselves the
// joined row's member of that name is the row.
NodePtr join_read_self(const NodePtr& node, const std::unordered_set<std::string>& names,
                       const std::string& binder) {
  if (!node) return node;
  if (node->t == NT::Index && node->l && node->l->t == NT::Var && node->l->s == binder && node->r &&
      node->r->t == NT::Text && names.count(ascii_upper(node->r->s))) {
    auto var = std::make_shared<Node>();
    var->t = NT::Var;
    var->pos = node->pos;
    var->s = binder;
    return var;
  }
  auto copy = std::make_shared<Node>(*node);
  copy->math_plan = nullptr;      // a plan of the original reads the original
  copy->record_shape = nullptr;
  copy->l = join_read_self(node->l, names, binder);
  copy->r = join_read_self(node->r, names, binder);
  for (NodePtr& item : copy->items) item = join_read_self(item, names, binder);
  return copy;
}

// The upper-cased keys of every row (a shape's keys read once), plus the
// names the row is bound under in the joined row.
std::unordered_set<std::string> join_row_keys(const Value& value, const std::vector<std::string>& bound) {
  std::unordered_set<std::string> keys;
  for (const std::string& b : bound) keys.insert(ascii_upper(b));
  std::unordered_set<const RecordShape*> shapes;
  for_each_collection_value(value, [&](const Value& row) {
    if (const auto& shape = row.shape()) {
      if (!shapes.insert(shape.get()).second) return;
      for (const std::string& k : shape->keys) keys.insert(ascii_upper(k));
    } else {
      for (const std::string& k : row.keys()) keys.insert(ascii_upper(k));
    }
  });
  return keys;
}

std::shared_ptr<JoinSideFacts> join_side_facts(const Value& value, std::unordered_set<std::string> keys,
                                               bool nullable, const std::vector<std::string>& bound = {}) {
  auto side = std::make_shared<JoinSideFacts>();
  side->value = value;
  side->keys = std::move(keys);
  side->nullable = nullable;
  for (const std::string& b : bound) {
    if (is_positional_binder(b) || b == "_") continue;
    side->names.insert(b);
    side->names.insert(ascii_lower(b));
  }
  if (const Value* first = first_collection_item(value)) {
    for (const std::string& k : first->keys()) side->first.insert(ascii_upper(k));
  }
  return side;
}

// The field NAME (as written) is on this side's first row, on every row, as
// text (a number is text) or, when NUMERIC, as a number.
bool join_side_total(JoinSideFacts& side, const std::string& name, bool numeric) {
  if (!side.first.count(ascii_upper(name)) || side.nullable) return false;
  const std::string id = (numeric ? "N:" : "T:") + name;
  auto it = side.facts.find(id);
  if (it != side.facts.end()) return it->second;
  bool ok = true;
  const std::size_t n = collection_size(side.value);
  for (std::size_t i = 0; i < n && ok; ++i) {
    const Value* v = collection_item(side.value, i).get(name);
    if (!v || v->kind() != Kind::Text) { ok = false; break; }
    if (numeric) {
      try { (void)as_dec(*v, {}); } catch (const SelError&) { ok = false; }
    }
  }
  side.facts.emplace(id, ok);
  return ok;
}

// The field NAME is a key of every row, whatever its value: reading it
// through the side's member cannot raise.
bool join_side_present(JoinSideFacts& side, const std::string& name) {
  const std::string id = "P:" + name;
  auto it = side.facts.find(id);
  if (it != side.facts.end()) return it->second;
  const std::size_t n = collection_size(side.value);
  bool ok = n > 0;
  for (std::size_t i = 0; i < n && ok; ++i) ok = collection_item(side.value, i).get(name) != nullptr;
  side.facts.emplace(id, ok);
  return ok;
}

// NAME on SIDE's first row and on every row as a non-null scalar: what a
// promoted join key needs to be read without raising.
bool join_side_any(JoinSideFacts& side, const std::string& name) {
  if (!side.first.count(ascii_upper(name)) || side.nullable) return false;
  const std::string id = "A:" + name;
  auto it = side.facts.find(id);
  if (it != side.facts.end()) return it->second;
  bool ok = true;
  const std::size_t n = collection_size(side.value);
  for (std::size_t i = 0; i < n && ok; ++i) {
    const Value* v = collection_item(side.value, i).get(name);
    ok = v && !v->is_null() && !is_nested_record(*v);
  }
  side.facts.emplace(id, ok);
  return ok;
}

// Whether every handed-down join key -- the left key of each join above this
// one that handed its conjuncts down -- cannot raise on a joined row built
// from a left row dropped here. As written those joins compute the key for
// every row they receive; a row dropped below never reaches them, so an
// E_NO_KEY there would be lost. Canonical keys never raise, only the reads
// do, so presence suffices: `r["m"]["f"]` needs f on every row of m's
// relation; `r["f"]` needs f carried, non-null, by every row of the one side
// below the join that has it. Anything else is not proved.
bool join_keys_safe(const std::vector<JoinObligation>& obligations, JoinSideFacts& left, JoinSideFacts& right,
                    const std::vector<std::shared_ptr<JoinSideFacts>>& above) {
  for (const JoinObligation& ob : obligations) {
    const std::size_t n_below = above.size() >= ob.outer ? above.size() - ob.outer : 0;
    const Node* key = ob.key;
    if (!key || key->t != NT::Index || !key->r || key->r->t != NT::Text || !key->l) return false;
    const std::string& field = key->r->s;
    const Node* obj = key->l.get();
    if (obj->t == NT::Index && obj->l && obj->l->t == NT::Var && ob.row_names.count(obj->l->s) && obj->r &&
        obj->r->t == NT::Text) {
      const std::string& member = obj->r->s;
      if (left.names.count(member)) {
        if (!join_side_present(left, field)) return false;
        continue;
      }
      JoinSideFacts* side = right.names.count(member) ? &right : nullptr;
      for (std::size_t i = 0; !side && i < n_below; ++i) {
        if (above[i]->names.count(member)) side = above[i].get();
      }
      if (side) {
        if (!join_side_present(*side, field)) return false;
        continue;
      }
      const std::size_t n = collection_size(left.value);
      for (std::size_t i = 0; i < n; ++i) {
        const Value* inner = collection_item(left.value, i).get(member);
        if (!inner || !inner->get(field)) return false;
      }
      continue;
    }
    if (obj->t == NT::Var && ob.row_names.count(obj->s)) {
      const std::string upper = ascii_upper(field);
      JoinSideFacts* owner = nullptr;
      int owners = 0;
      const auto consider = [&](JoinSideFacts* side) {
        if (side && side->keys.count(upper)) { owner = side; ++owners; }
      };
      consider(&left);
      consider(&right);
      for (std::size_t i = 0; i < n_below; ++i) consider(above[i].get());
      if (owners != 1 || !join_side_any(*owner, field)) return false;
      continue;
    }
    return false;
  }
  return true;
}

// Every (field, numeric) requirement is met over the joined rows of this
// join: exactly one side -- the left rows, this right side, or a right side
// above -- carries the field at all, and that side carries it on every row
// with the kind (a field two sides carry is promoted from neither, §7.4).
bool join_totality(const std::vector<std::pair<std::string, bool>>& reqs, JoinSideFacts* left,
                   JoinSideFacts& right, const std::vector<std::shared_ptr<JoinSideFacts>>& above) {
  for (const auto& [name, numeric] : reqs) {
    const std::string key = ascii_upper(name);
    JoinSideFacts* owner = nullptr;
    int owners = 0;
    const auto consider = [&](JoinSideFacts* side) {
      if (side && side->keys.count(key)) { owner = side; ++owners; }
    };
    consider(left);
    consider(&right);
    for (const auto& side : above) consider(side.get());
    if (owners != 1 || !join_side_total(*owner, name, numeric)) return false;
  }
  return true;
}

// A rejected right row stays in its bucket: it still counts towards numbering,
// and a left row kept on an error must join it. Keep the flag beside the row
// instead of allocating a separate hash-set entry and looking up its address.
struct JoinBucketRow {
  Value value;
  bool rejected = false;

  JoinBucketRow(const Value& row) : value(row) {}
  JoinBucketRow(Value&& row) noexcept : value(std::move(row)) {}
  JoinBucketRow(JoinBucketRow&&) noexcept = default;
  JoinBucketRow& operator=(JoinBucketRow&&) noexcept = default;
  JoinBucketRow(const JoinBucketRow&) = default;
  JoinBucketRow& operator=(const JoinBucketRow&) = default;
};

// Equi-LINK_LEFT: mark the right rows a FILTER that opens with
// IS_NULL(_["member"]["field"]) drops wherever they are joined;
// false when the join cannot tell. MEMBER must be one of this
// join's right binder keys -- the binder, its lower case or `_2`, each bound
// in every joined row to the right row as bucketed (spec §7.4) -- and the two
// binders must not be spelled alike. A row is rejected only when the read
// certainly yields a non-NULL value: one without the field (E_NO_KEY in the
// FILTER) or with a NULL there is kept, for the FILTER to decide. Its joined
// rows are then never built; its left row stays matched, so it gets no
// null-extended row in their place.
template <typename Buckets>
bool join_right_null_rejects(const JoinRightNull& hint, const std::string& b1, const std::string& b2,
                             Buckets& buckets) {
  if (ascii_upper(b1) == ascii_upper(b2)) return false;
  if (hint.member != b2 && hint.member != ascii_lower(b2) && hint.member != "_2") return false;
  for (auto& [key, bucket] : buckets) {
    for (auto& right : bucket) {
      const Value* value = right.value.get(hint.field);
      if (value && !value->is_null()) right.rejected = true;
    }
  }
  return true;
}

Value do_link(Args& a, Context& ctx, bool left_join) {
  // Taken before anything else is evaluated, so a LINK nested in this one's
  // sources cannot pick it up by accident; it is handed down on purpose below.
  std::optional<JoinPrefilter> prefilter = std::move(ctx.join_prefilter);
  ctx.join_prefilter.reset();
  std::optional<JoinRightNull> right_null = std::move(ctx.join_right_null);
  ctx.join_right_null.reset();
  const int count = a.count();
  if (count != 3 && count != 5) unreachable_arity(a.name());
  // A predicate that may write can change a row between an early test and
  // the FILTER's read of it (spec §7.4): then nothing is tested early here,
  // and nothing is handed down.
  if ((prefilter || right_null) && !ctx.write_free && !writes_nothing(a.node(count == 5 ? 4 : 2))) {
    prefilter.reset();
    right_null.reset();
  }
  const Node& left_node = a.node(0);
  const Node& right_node = a.node(1);
  const std::vector<JoinStage> no_stages;
  const std::vector<std::shared_ptr<JoinSideFacts>> no_sides;
  const std::vector<JoinStage>& stages = prefilter ? prefilter->stages : no_stages;
  const bool deep = prefilter && prefilter->deep;
  const std::vector<std::shared_ptr<JoinSideFacts>>& above = prefilter ? prefilter->above : no_sides;
  // The upper-cased keys of the joins between a stage's FILTER and this join,
  // per count of them.
  std::map<std::size_t, std::unordered_set<std::string>> above_keys_cache;
  const auto above_keys = [&](const JoinStage& stage) -> const std::unordered_set<std::string>& {
    auto it = above_keys_cache.find(stage.above);
    if (it == above_keys_cache.end()) {
      std::unordered_set<std::string> keys;
      for (std::size_t i = 0; i < stage.above && i < above.size(); ++i) {
        keys.insert(above[i]->keys.begin(), above[i]->keys.end());
      }
      it = above_keys_cache.emplace(stage.above, std::move(keys)).first;
    }
    return it->second;
  };
  const auto above_of = [&](const JoinStage& stage) {
    return std::vector<std::shared_ptr<JoinSideFacts>>(
        above.begin(), above.begin() + static_cast<std::ptrdiff_t>(std::min(stage.above, above.size())));
  };
  // The keys a side contributes to the joined row include the names its row
  // is bound under: `_["products"]` after LINK(PRODUCTS, ...) is the right
  // row, not a field of the left ones.
  const auto bound_name = [](const Node& n, const char* fallback) {
    std::string name = single_relation_name(n);
    return name.empty() ? std::string(fallback) : name;
  };
  // The two sides' binder names, decided once: the explicit binders of the
  // five-argument form, or each side's relation name (else _1/_2).
  const std::string jb1 = count == 5 ? a.symbol(2) : bound_name(left_node, "_1");
  const std::string jb2 = count == 5 ? a.symbol(3) : bound_name(right_node, "_2");
  const std::vector<std::string> b1_names{jb1, "_1"};
  const std::vector<std::string> b2_names{jb2, "_2"};
  const std::vector<JoinObligation> no_obligations;
  const std::vector<JoinObligation>& obligations = prefilter ? prefilter->obligations : no_obligations;
  const std::optional<JoinEqui> jequi = extract_join_equi(a.node(count == 5 ? 4 : 2), jb1, jb2);
  std::shared_ptr<JoinSideFacts> right_side;
  const auto owned_by_left = [&](const std::unordered_set<std::string>& fields, const JoinStage& stage) {
    const auto& upper = above_keys(stage);
    for (const std::string& f : fields) {
      if (right_side->keys.count(f) || upper.count(f)) return false;
    }
    return true;
  };
  const auto nothing_right = [](const std::unordered_set<std::string>&, const JoinStage&) { return false; };
  // With conjuncts to pre-apply and a left source that is itself a join, the
  // right source is evaluated first -- unobservable when both sources are
  // pure -- so the conjuncts still askable of the rows below travel down to
  // the join below, and from there to the base rows.
  if (deep && !stages.empty() && jequi && left_node.t == NT::Call &&
      (left_node.s == "LINK" || left_node.s == "LINK_LEFT" || left_node.s == "FILTER") &&
      join_pure_source(&left_node) && join_pure_source(&right_node)) {
    // The right source is evaluated first only as an optimisation; a program is what it is
    // as written, where the LEFT source runs first (SPEC 7.4, 6.2). So if the right one
    // raises, the left one (pure: no effects, but errors are observable) is evaluated as
    // written and ITS error wins; only when it evaluates cleanly does the right source's
    // own error stand. eval_node restored the depth while the exception unwound.
    const Value* right_first_ptr = nullptr;
    try {
      right_first_ptr = &a.val(1);
    } catch (const SelError&) {
      ctx.join_prefilter.reset();
      ctx.join_prefilter_report.reset();
      (void)a.eval(a.node(0));
      ctx.join_prefilter.reset();
      ctx.join_prefilter_report.reset();
      throw;
    }
    const Value& right_first = *right_first_ptr;
    right_side = join_side_facts(right_first, join_row_keys(right_first, b2_names), left_join, b2_names);
    const auto total_below = [&](const std::vector<std::pair<std::string, bool>>& reqs, const JoinStage& stage) {
      return join_totality(reqs, nullptr, *right_side, above_of(stage));
    };
    auto walked = join_stage_walk(stages, owned_by_left, total_below, nothing_right);
    std::vector<JoinStage> handed = join_truncate_stages(stages, walked.second);
    // Below this join, every stage has one more join above it: this one.
    for (JoinStage& stage : handed) ++stage.above;
    if (!handed.empty()) {
      std::vector<std::shared_ptr<JoinSideFacts>> sides{right_side};
      sides.insert(sides.end(), above.begin(), above.end());
      // This join computes its left key on every row it receives; a row
      // dropped below never arrives, so the key goes down as an obligation
      // for the join that drops to prove (join_keys_safe).
      std::vector<JoinObligation> obs;
      const std::string lower_b1 = ascii_lower(jb1);
      obs.push_back(JoinObligation{jequi->left.get(), {jb1, lower_b1, "_1", "_"}, above.size() + 1});
      obs.insert(obs.end(), obligations.begin(), obligations.end());
      ctx.join_prefilter = JoinPrefilter{std::move(handed), true, std::move(sides), std::move(obs)};
    }
    try {
      (void)a.val(0);
    } catch (...) {
      ctx.join_prefilter.reset();
      throw;
    }
    ctx.join_prefilter.reset();
  }
  Value left_value = a.val(0);
  const Value right_value = a.val(1);
  // The join below, if it applied some of these conjuncts, says which ones
  // every row that came up has passed; those are skipped here unless a row
  // was kept on an error below.
  std::optional<JoinReport> below = std::move(ctx.join_prefilter_report);
  ctx.join_prefilter_report.reset();
  // Drops below that left this join no left rows: as written it may have had
  // some, and then it computes every right key (and raises where one cannot
  // be) before it finds that no row survives. Only the rows as written can
  // say, so the left side -- pure, or nothing was handed down -- is evaluated
  // again without them, and this join runs as written.
  if (below && below->dropped && first_collection_item(left_value) == nullptr) {
    left_value = a.eval(a.node(0));
    ctx.join_prefilter_report.reset();
    below.reset();
  }
  const std::string& b1 = jb1;
  const std::string& b2 = jb2;
  const NodePtr predicate = a.node_ptr(count == 3 ? 2 : 4);
  if (left_value.is_null()) return Value::list({});

  const Value* first_left = first_collection_item(left_value);
  const Value* first_right = first_collection_item(right_value);
  const bool have_left = first_left != nullptr;
  const bool have_right = first_right != nullptr;
  if (!have_left || (!have_right && !left_join)) return Value::list({});

  const Value sample_right = have_right ? ensure_row_table_alias(*first_right, b2) : Value::none();
  // Every row is built from its own pair (spec §7.4): nothing is decided from
  // a first element except the shape of LINK_LEFT's null record.
  const Value null_right = left_join ? make_null_record(sample_right, b2) : Value::none();
  JoinProjector projector;
  projector.b1 = b1;
  projector.b2 = b2;
  projector.null_right = null_right;
  projector.has_null_right = left_join && !null_right.is_null();
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
    const std::string lower = ascii_lower(name);
    if (lower != name) frame.emplace_back(lower, value);
  };

  if (equi && have_right) {
    std::unordered_map<FastJoinKey, std::vector<JoinBucketRow>, FastJoinKeyHash> buckets;
    buckets.reserve(collection_size(right_value));
    JoinRightFacts right_facts;
    std::vector<std::pair<std::string, Value>> frame;
    add_frame_names(frame, b2, Value::none());
    frame.emplace_back("_2", Value::none());
    FrameScope key_scope(ctx, std::move(frame));
    // The right rows are read in order here, where they are close together,
    // rather than scattered pair by pair in the projector.
    JoinFlatTest flat_test{b1, b2};
    bool right_flat = true;
    {
      RowAliaser alias_right(b2);
      for_each_snapshot_value(take_snapshot(right_value, false, walks_in_place(ctx, equi->right.get())), [&](const Value& item) {
        Value row = alias_right(item);
        set_frame(ctx.frames.back(), b2, row);
        set_frame(ctx.frames.back(), "_2", row);
        const Value key_value = a.eval(*equi->right);
        const auto join_key = make_fast_join_key(key_value, equi->numeric);
        note_right_join_key(right_facts, join_key, key_value);
        if (right_flat && !flat_test(row)) right_flat = false;
        if (join_key && join_key->type != FastJoinKey::Type::Bad) {
          buckets[*join_key].emplace_back(std::move(row));
        }
      });
    }
    key_scope.pop();
    projector.right_flat = right_flat;

    // The pre-filter, decided from the rows themselves (join_stage_walk). On
    // a left row a conjunct evaluates FALSE the row is dropped -- the joined
    // rows it would have produced would all have been dropped by the same
    // conjunct; likewise a right row, whose joined rows are then not built.
    // On an error the row is KEPT: the full predicate runs over the joined
    // rows afterwards and raises there, in row order.
    std::vector<NodePtr> prefix;
    std::vector<NodePtr> right_prefix;
    // How many left conjuncts come before the first right one: with a right
    // row kept on an error, a later left conjunct may not drop a left row --
    // the joined row would have raised in the right conjunct first.
    std::optional<std::size_t> left_before_right;
    std::vector<std::string> binders;
    JoinReport report;
    report.dropped = below && below->dropped;
    // A read through this join's right binder is the right element in every
    // joined row -- the binder is bound last (spec §7.4) -- unless the left
    // binder has the same name, or a join above rebinds it.
    const auto right_names = [&](const JoinStage& stage) {
      std::unordered_set<std::string> names{ascii_upper(b2)};
      if (stage.above == 0) names.insert("_2");
      return names;
    };
    const bool right_ok = !left_join && ascii_upper(b1) != ascii_upper(b2);
    const auto right_here = [&](const std::unordered_set<std::string>& fields, const JoinStage& stage) {
      if (!right_ok) return false;
      const auto names = right_names(stage);
      const auto& upper = above_keys(stage);
      for (const std::string& f : fields) {
        if (!names.count(f) || upper.count(f)) return false;
      }
      return true;
    };
    if (prefilter) {
      if (!right_side) right_side = join_side_facts(right_value, join_row_keys(right_value, b2_names), left_join, b2_names);
      for (const JoinStage& stage : stages) binders.push_back(stage.binder);
      auto left_side = join_side_facts(left_value, join_row_keys(left_value, b1_names), false, b1_names);
      const auto total_here = [&](const std::vector<std::pair<std::string, bool>>& reqs, const JoinStage& stage) {
        return join_totality(reqs, left_side.get(), *right_side, above_of(stage));
      };
      // A joined row carries a left element's field exactly as the element
      // does whenever no right element has the name (§7.4, pair by pair) --
      // and no row depends on another, so a drop below changes nothing above
      // but the rows it drops.
      const auto owned_here = [&](const std::unordered_set<std::string>& fields, const JoinStage& stage) {
        const auto& upper = above_keys(stage);
        for (const std::string& f : fields) {
          if (right_side->keys.count(f) || upper.count(f)) return false;
        }
        return true;
      };
      // A handed-down join key that could raise on a dropped row, and
      // nothing is dropped.
      const bool safe = obligations.empty() || join_keys_safe(obligations, *left_side, *right_side, above);
      const std::unordered_set<std::string> self_names{ascii_upper(b1), "_1"};
      std::vector<JoinApplied> walk_applied;
      if (safe) walk_applied = join_stage_walk(stages, owned_here, total_here, right_here).first;
      for (const JoinApplied& applied : walk_applied) {
        const JoinConjunct* c = applied.conjunct;
        report.applied.insert(c->node);
        if (below && !below->errored && below->applied.count(c->node)) continue;
        if (applied.right) {
          if (!left_before_right) left_before_right = prefix.size();
          right_prefix.push_back(join_read_self(std::make_shared<Node>(*c->node), right_names(*applied.stage), c->binder));
          continue;
        }
        NodePtr node(std::shared_ptr<const Node>{}, c->node);   // non-owning: the tree outlives the run
        for (const std::string& f : c->fields) {
          if (self_names.count(f) && !left_side->first.count(f)) {
            node = join_read_self(std::make_shared<Node>(*c->node), self_names, c->binder);
            break;
          }
        }
        prefix.push_back(std::move(node));
      }
    }
    // 0: keep the row; 1: drop it; 2: keep it, a conjunct raised on it.
    const auto verdict = [&](const std::vector<NodePtr>& conjuncts, const Value& row) {
      for (const std::string& binder : binders) set_frame(ctx.frames.back(), binder, row);
      for (const NodePtr& conjunct : conjuncts) {
        bool keep;
        try {
          keep = a.eval(*conjunct).as_bool(conjunct->pos);
        } catch (const SelError&) {
          report.errored = true;
          return 2;
        }
        if (!keep) return 1;
      }
      return 0;
    };
    // The right rows the right conjuncts reject, once each, after every right
    // key was computed. They stay in their buckets: a left row still counts
    // them towards the numbering, and one kept on an error joins them.
    // A LINK_LEFT rejects right rows only for a FILTER that opens with
    // IS_NULL (join_right_null_rejects), and then drops no left row: the
    // position counts every joined row as written, built or not, and the
    // collection limit is held to that count, where the join as written would
    // have raised (spec §6.4).
    const bool logical = right_null && left_join && !prefilter &&
                         join_right_null_rejects(*right_null, b1, b2, buckets);
    const bool rejecting = !right_prefix.empty() || logical;
    if (!right_prefix.empty()) {
      std::vector<std::pair<std::string, Value>> right_frame;
      for (const std::string& binder : binders) right_frame.emplace_back(binder, Value::none());
      const bool before = report.errored;
      report.errored = false;
      FrameScope right_scope(ctx, std::move(right_frame));
      for (auto& [key, bucket] : buckets) {
        for (auto& right : bucket) {
          if (verdict(right_prefix, right.value) == 1) right.rejected = true;
        }
      }
      right_scope.pop();
      if (report.errored) prefix.resize(*left_before_right);
      report.errored = report.errored || before;
    }
    // A FILTER keeps its input's keys, so the rows dropped here still count
    // towards the numbering of the rows kept (the matches say how many joined
    // rows a dropped row stood for), unless nothing observes it (deep). When
    // the join key is a literal field of the row, a row that HAS it may be
    // rejected before its key is computed.
    const bool numbered = (!prefix.empty() || rejecting) && !(logical ? right_null->deep : deep);
    std::vector<Value::Entry> keyed;
    bool dropped = false;
    std::size_t position = 1;
    std::string fast_field;
    const Node& el = *equi->left;
    if (!prefix.empty() && deep && el.t == NT::Index && el.l && el.l->t == NT::Var && el.r &&
        el.r->t == NT::Text) {
      const std::string owner = ascii_upper(el.l->s);
      if (owner == ascii_upper(b1) || owner == "_1" || owner == "_") fast_field = el.r->s;
    }

    // The binders the conjuncts read come first: a frame is searched in
    // order, once per read, every row.
    frame.clear();
    for (const std::string& binder : binders) {
      bool present = false;
      for (const auto& entry : frame) present = present || entry.first == binder;
      if (!present) frame.emplace_back(binder, Value::none());
    }
    const auto add_once = [&frame](const std::string& name) {
      for (const auto& entry : frame) if (entry.first == name) return;
      frame.emplace_back(name, Value::none());
    };
    {
      std::vector<std::pair<std::string, Value>> names;
      add_frame_names(names, b1, Value::none());
      for (const auto& entry : names) add_once(entry.first);
    }
    add_once("_1");
    add_once("_");
    FrameScope left_scope(ctx, std::move(frame));
    {
      RowAliaser alias_left(b1);
      for_each_snapshot_value(take_snapshot(left_value, false, walks_in_place(ctx, equi->left.get())), [&](const Value& item) {
        const Value row = alias_left(item);
        int asked = -1;
        if (!fast_field.empty() && row.get(fast_field) != nullptr) {
          asked = verdict(prefix, row);
          if (asked == 1) {
            // Dropped before its key was computed -- but the key is this very
            // field, and a rejected one still raises in the join as written.
            const Value field_value = *row.get(fast_field);
            check_join_pair(*equi, make_fast_join_key(field_value, equi->numeric), field_value, right_facts);
            dropped = true;
            return;
          }
        }
        set_frame(ctx.frames.back(), b1, row);
        set_frame(ctx.frames.back(), "_1", row);
        set_frame(ctx.frames.back(), "_", row);
        const Value key_value = a.eval(*equi->left);
        const auto join_key = make_fast_join_key(key_value, equi->numeric);
        check_join_pair(*equi, join_key, key_value, right_facts);
        auto it = join_key ? buckets.find(*join_key) : buckets.end();
        if (asked < 0) asked = prefix.empty() ? 0 : verdict(prefix, row);
        if (asked == 1) {
          dropped = true;
          if (numbered) position += it != buckets.end() ? it->second.size() : (left_join ? 1 : 0);
          return;
        }
        const auto emit = [&](Value joined) {
          // The rows a join builds are capped as they appear (spec §6.4), or,
          // when it skips some it would have built, as the rows as written
          // count (logical, checked below before they are built).
          if (!logical) {
            cap_collection(static_cast<u128>(numbered ? keyed.size() : output.size()) + 1, a.pos());
          }
          if (numbered) keyed.emplace_back(std::to_string(position), std::move(joined));
          else output.push_back(std::move(joined));
          ++position;
        };
        if (it != buckets.end()) {
          // A left row kept on an error meets every right row: its joined
          // rows raise in the FILTER, in order, where they would have.
          const bool skip = rejecting && asked == 0;
          if (logical) cap_collection(static_cast<u128>(position - 1 + it->second.size()), a.pos());
          for (const auto& right : it->second) {
            if (skip && right.rejected) {
              dropped = true;
              ++position;
              continue;
            }
            emit(projector(row, &right.value));
          }
        } else if (left_join) {
          if (logical) cap_collection(static_cast<u128>(position), a.pos());
          emit(projector(row, nullptr));
        }
      });
    }
    left_scope.pop();
    report.dropped = report.dropped || dropped;
    if (prefilter) ctx.join_prefilter_report = std::move(report);
    if (numbered) {
      if (dropped && !keyed.empty()) return Internals::with_children(Kind::None, std::move(keyed), true);
      for (auto& entry : keyed) output.push_back(std::move(entry.second));
    }
  } else {
    std::vector<std::pair<std::string, Value>> frame;
    add_frame_names(frame, b1, Value::none());
    frame.emplace_back("_1", Value::none());
    frame.emplace_back("_", Value::none());
    add_frame_names(frame, b2, Value::none());
    frame.emplace_back("_2", Value::none());
    FrameScope scope(ctx, std::move(frame));
    {
      // Each side is listed ONCE (spec §7.3): the right side is walked again for every
      // left row, and a predicate that grows it must not give later left rows more rows.
      const bool in_place = walks_in_place(ctx, predicate.get());
      const Snapshot general_left = take_snapshot(left_value, false, in_place);
      const Snapshot general_right = take_snapshot(right_value, false, in_place);
      RowAliaser alias_left(b1);
      RowAliaser alias_right(b2);
      for_each_snapshot_value(general_left, [&](const Value& item) {
        const Value left = alias_left(item);
        set_frame(ctx.frames.back(), b1, left);
        set_frame(ctx.frames.back(), "_1", left);
        set_frame(ctx.frames.back(), "_", left);
        bool matched = false;
        for_each_snapshot_value(general_right, [&](const Value& right_item) {
          const Value right = alias_right(right_item);
          set_frame(ctx.frames.back(), b2, right);
          set_frame(ctx.frames.back(), "_2", right);
          if (a.eval(*predicate).as_bool(predicate->pos)) {
            matched = true;
            cap_collection(static_cast<u128>(output.size()) + 1, a.pos());
            output.push_back(projector(left, &right));
          }
        });
        if (left_join && !matched) {
          cap_collection(static_cast<u128>(output.size()) + 1, a.pos());
          output.push_back(projector(left, nullptr));
        }
      });
    }
    scope.pop();
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
  return Internals::shaped(intern_record_shape(std::move(keys)), std::move(values));
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

  define(Spec{"LIST", 0, VARIADIC, false, false, nullptr, [](Args& a, Context& ctx) -> Value {
                cap_collection(static_cast<u128>(a.count()), a.pos());
                std::vector<Value> out;
                out.reserve(a.count());
                for (int i = 0; i < a.count(); i++) {
                  out.push_back(hold_or_adopt(a.take_val(i), 1, a.pos(), ctx.write_free));
                }
                return Value::list(std::move(out));
              }});

  define(Spec{"RECORD", 0, VARIADIC, false, false, nullptr,   // even count: spec/builtins.json
              [](Args& a, Context& ctx) -> Value {
                const int n = a.count();
                cap_collection(static_cast<u128>(n / 2), a.pos());
                // Literal, distinct keys (prepare_record_shape): the keys are the shape's own,
                // a literal key cannot fail to be text, so the values go straight into the
                // shaped storage -- no intermediate record. Same evaluation order:
                // the arguments were evaluated before this call, left to right.
                // The shape is checked against the key NODES as they are now: the planners
                // rewrite argument lists and a stale shape must not name the wrong keys.
                bool literal_keys = a.record_shape() &&
                    a.record_shape()->keys.size() == static_cast<std::size_t>(n / 2);
                for (int i = 0; literal_keys && i < n; i += 2) {
                  const Node& k = a.node(i);
                  literal_keys = k.t == NT::Text && k.s == a.record_shape()->keys[i / 2];
                }
                if (literal_keys) {
                  std::vector<Value> values;
                  values.reserve(static_cast<std::size_t>(n / 2));
                  for (int i = 0; i < n; i += 2) {
                    values.push_back(hold_or_adopt(a.take_val(i + 1), 1, a.pos(), ctx.write_free));
                  }
                  return Internals::shaped(a.record_shape(), std::move(values));
                }
                Value rec = Value::none();
                for (int i = 0; i < n; i += 2) {
                  // The key is coerced into a local first: argument evaluation order is
                  // unspecified, and the copy below can raise E_DEPTH for an over-deep
                  // host value, which must not beat the key's own E_NOT_TEXT.
                  const std::string key = a.text(i);
                  rec.set(key, hold_or_adopt(a.take_val(i + 1), 1, a.pos(), ctx.write_free));
                }
                if (a.record_shape() && a.record_shape()->keys == rec.keys()) {
                  std::vector<Value> values;
                  values.reserve(rec.size());
                  for (const auto& entry : rec.entries()) values.push_back(entry.second);
                  return Internals::shaped(a.record_shape(), std::move(values));
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

  // DISTINCT and DEDUPE are one operation (spec §7.3), so one body: DISTINCT
  // compared every pair, which was O(n^2) and never walked a lone value --
  // one nested past the cap answered where DEDUPE raised E_DEPTH.
  const auto dedupe = [](Args& a, Context&) -> Value {
                const Value& val = a.val(0);
                if (val.is_null()) return Value::list({});
                // Open addressing over indices into `out`: a chained
                // unordered_map<hash, vector<Value>> paid two allocations per distinct
                // value. Slots hold an index into `out`; the parallel `hashes` vector
                // keeps the full hash so a probe compares 64 bits before any eql().
                constexpr std::uint32_t kEmpty = 0xFFFFFFFFu;
                std::vector<std::uint32_t> table(16, kEmpty);
                std::vector<std::uint64_t> hashes;
                std::vector<Value> out;
                const Pos call_pos = a.pos();   // where a too-deep element is reported
                for_each_collection_value(val, [&](const Value& item) {
                  const std::uint64_t hash = item.structural_hash(call_pos);
                  std::size_t mask = table.size() - 1;
                  std::size_t slot = static_cast<std::size_t>(hash) & mask;
                  while (table[slot] != kEmpty) {
                    const std::uint32_t at = table[slot];
                    if (hashes[at] == hash && item.eql(out[at], call_pos)) return;
                    slot = (slot + 1) & mask;
                  }
                  table[slot] = static_cast<std::uint32_t>(out.size());
                  hashes.push_back(hash);
                  out.push_back(item);
                  if (out.size() * 2 > table.size()) {       // keep the load factor <= 1/2
                    std::vector<std::uint32_t> bigger(table.size() * 2, kEmpty);
                    mask = bigger.size() - 1;
                    for (std::size_t k = 0; k < out.size(); k++) {
                      std::size_t at = static_cast<std::size_t>(hashes[k]) & mask;
                      while (bigger[at] != kEmpty) at = (at + 1) & mask;
                      bigger[at] = static_cast<std::uint32_t>(k);
                    }
                    table.swap(bigger);
                  }
                });
                return Value::list(std::move(out));
              };
  define(Spec{"DISTINCT", 1, 1, false, false, nullptr, dedupe});
  define(Spec{"DEDUPE", 1, 1, false, false, nullptr, dedupe});

  // Three or five arguments, refused at compile time like every E_ARITY (spec
  // §7.4); the rule is spec/builtins.json's and define() installs it.
  define(Spec{"LINK", 3, 5, true, true, nullptr,
              [](Args& a, Context& ctx) -> Value { return do_link(a, ctx, false); }});
  define(Spec{"LINK_LEFT", 3, 5, true, true, nullptr,
              [](Args& a, Context& ctx) -> Value { return do_link(a, ctx, true); }});
}

// --- aggregates. These are why SEL needs no loop: each evaluates one argument
// node once per element, which is the same move IF makes, repeated.

// Every AND-conjunct of a FILTER body, in order, for a LINK to pre-apply to
// its left rows (do_link; SEL-0052): the upper-cased fields of the row it
// reads (a nested `_["orders"]["year"]` reads ORDERS) when it reads nothing
// else, and, for a comparison between literals and bare field reads, the
// (field, numeric) requirements under which it cannot raise.
std::vector<JoinConjunct> leading_field_conjuncts(const Node& body, const std::string& binder) {
  std::vector<const Node*> conjuncts;
  const Node* node = &body;
  while (node && node->t == NT::Bin && node->s == "AND") {
    conjuncts.push_back(node->r.get());
    node = node->l.get();
  }
  conjuncts.push_back(node);
  std::reverse(conjuncts.begin(), conjuncts.end());
  // The element is the binder, exactly as named (names are canonical): under
  // an explicit binder `_` is not the element.
  const auto row_var = [&](const Node* n) { return n && n->t == NT::Var && n->s == binder; };
  const auto bare_read = [&](const Node* n) {
    return n && n->t == NT::Index && row_var(n->l.get()) && n->r && n->r->t == NT::Text;
  };
  std::vector<JoinConjunct> out;
  for (const Node* c : conjuncts) {
    JoinConjunct entry;
    entry.node = c;
    entry.binder = binder;
    // Iterative for the reason given at expr_depends_only: a conjunct is an
    // arbitrary expression, and a flat chain in one is as deep as it is long.
    const auto reads_only_fields = [&](const Node* root) -> bool {
      std::vector<const Node*> todo{root};
      while (!todo.empty()) {
        const Node* n = todo.back();
        todo.pop_back();
        if (!n) continue;
        switch (n->t) {
          case NT::Index:
            if (bare_read(n)) { entry.fields.insert(ascii_upper(n->r->s)); break; }
            if (n->l && n->l->t == NT::Index) { todo.push_back(n->l.get()); todo.push_back(n->r.get()); break; }
            return false;
          case NT::Num: case NT::Text: case NT::Bool: break;
          case NT::Bin: todo.push_back(n->l.get()); todo.push_back(n->r.get()); break;
          case NT::Un: todo.push_back(n->l.get()); break;
          default: return false;
        }
      }
      return true;
    };
    entry.field_only = reads_only_fields(c) && !entry.fields.empty();
    if (!entry.field_only) entry.fields.clear();
    const sel_lexicon::Op* cmp = c && c->t == NT::Bin ? infix_op(c->s) : nullptr;
    if (cmp && (cmp->family == sel_lexicon::Family::TextCompare || cmp->family == sel_lexicon::Family::Compare)) {
      const bool numeric = cmp->family == sel_lexicon::Family::Compare;
      entry.has_total = true;
      for (const Node* operand : {c->l.get(), c->r.get()}) {
        if (operand && (operand->t == NT::Num || operand->t == NT::Text)) {
          // A text literal is not a number: that operand raises on every row.
          if (numeric && operand->t != NT::Num) { entry.has_total = false; break; }
          continue;
        }
        if (!bare_read(operand)) { entry.has_total = false; break; }
        entry.total.emplace_back(operand->r->s, numeric);
      }
      if (!entry.has_total) entry.total.clear();
    }
    out.push_back(std::move(entry));
  }
  return out;
}

// What a FILTER over a LINK_LEFT hands its join (JoinRightNull) when CONJUNCT
// is `IS_NULL(binder["member"]["field"])` -- the shipped IS_NULL of a literal
// field of a literal member of the FILTER's element, the element matched as
// leading_field_conjuncts matches it. Over a LINK_LEFT, a member that is one
// of the join's right binder keys is the right row (spec §7.4).
std::optional<JoinRightNull> join_right_null_test(const Node* conjunct, const std::string& binder, bool deep) {
  if (!conjunct || conjunct->t != NT::Call || conjunct->s != "IS_NULL" || conjunct->items.size() != 1 ||
      may_have_effects(conjunct->spec)) {
    return std::nullopt;
  }
  const Node* field = conjunct->items.front().get();
  if (!field || field->t != NT::Index || !field->r || field->r->t != NT::Text) return std::nullopt;
  const Node* member = field->l.get();
  if (!member || member->t != NT::Index || !member->r || member->r->t != NT::Text || !member->l ||
      member->l->t != NT::Var || member->l->s != binder) {
    return std::nullopt;
  }
  return JoinRightNull{member->r->s, field->r->s, deep};
}

// Runs `visit` per element with the binder and _K in scope. Returning a value
// from `visit` stops the walk and becomes the result.
template <typename Visitor>
std::optional<Value> walk(Args& a, Context& ctx, Visitor&& visit, const Node* body_override = nullptr) {
  const bool three = a.count() == 3;
  const std::string binder = three ? a.symbol(1) : std::string("_");
  const Node& body = body_override ? *body_override : a.node(three ? 2 : 1);
  const bool needs_k = node_contains_var(body, "_K");

  const Snapshot snap = take_snapshot(a.val(0), needs_k, walks_in_place(ctx, &body));
  const std::size_t count = snap.size();
  if (count == 0) return std::nullopt;

  std::vector<std::pair<std::string, Value>> frame;
  frame.reserve(needs_k ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k) frame.emplace_back("_K", Value::none());
  FrameScope scope(ctx, std::move(frame));

  std::optional<Value> stopped;
  {
    for (std::size_t i = 0; i < count; ++i) {
      const Value& item = snap.item(i);
      ctx.frames.back()[0].second = item;
      if (needs_k) {
        ctx.frames.back()[1].second = make_text(snap.key(i));
      }
      std::optional<Value> result = visit(a.eval(body), i, item, body);
      if (result.has_value()) {
        stopped = std::move(result);
        break;
      }
    }
  }
  scope.pop();
  return stopped;
}

// The total order of spec §7.3, by KIND first: NULL < BOOL (FALSE < TRUE) <
// numeric-looking text and numbers (by exact decimal value) < every other TEXT
// (bytewise) < BIN (bytewise). Comparing two values by whichever rule happened to
// fit the pair -- numbers by value when both look numeric, bytes otherwise -- was
// not an order at all: "10" < "1a" < "9" < "10" cycles, and what a sort returned
// depended on the input's order. A rank per kind makes it transitive; equal
// values tie, and the callers' stable sort keeps them in input order.
// A value with children and no scalar of its own is ordered by what scalar
// context makes of it (spec §3.2: its first child, recursively): a record sorts by
// its first field and ranks by that field's kind. nullptr when the chain ends in
// nothing (NULL). NULL itself answers without throwing -- a sort key that is NULL
// is common (`_["k"] ?? NULL`) and would otherwise cost an exception per compare.
const Value* sort_leaf(const Value& v) {
  if (v.kind() != Kind::None) return &v;
  if (v.is_null()) return nullptr;
  try {
    return &v.scalar_source(Pos{});
  } catch (const SelError&) {
    return nullptr;
  }
}

int value_rank(const Value* v) {
  if (!v || v->is_null()) return 0;
  if (v->kind() == Kind::Bool) return 1;
  if (v->kind() == Kind::Text && v->looks_numeric()) return 2;
  if (v->kind() == Kind::Text) return 3;
  if (v->kind() == Kind::Bin) return 4;
  return 5;
}

int compare_values(const Value& av_in, const Value& bv_in) {
  const Value* pa = sort_leaf(av_in);
  const Value* pb = sort_leaf(bv_in);
  const int ra = value_rank(pa);
  const int rb = value_rank(pb);
  if (ra != rb) return (ra > rb) - (ra < rb);
  switch (ra) {
    case 1: {
      const int av = pa->boolean_scalar() ? 1 : 0;
      const int bv = pb->boolean_scalar() ? 1 : 0;
      return (av > bv) - (av < bv);
    }
    case 2: return dec_cmp(as_dec_ref(*pa, Pos{}), as_dec_ref(*pb, Pos{}));
    case 3:
    case 4: {
      const std::string& as = pa->as_bytes();
      const std::string& bs = pb->as_bytes();
      if (as < bs) return -1;
      if (as > bs) return 1;
      return 0;
    }
    default: return 0;
  }
}

struct SortEntry {
  Value item;
  Value key;
  std::size_t idx;
};

// The SORT/TOP form of this call (sort_form, sel_ast.hpp). compile() refused
// every count no form takes.
SortForm sort_form_of(const Args& a) {
  if (const auto form = sort_form(a.name(), a.nodes())) return *form;
  unreachable_arity(a.name());
}

Value do_sort(Args& a, Context& ctx, std::optional<std::string> forced_dir) {
  const Value& val = a.val(0);
  // Nothing to sort still means the direction is read and checked (spec §7.4).
  const bool nothing_to_sort = val.is_null() || collection_size(val) == 0;
  const std::size_t source_size = nothing_to_sort ? 0 : collection_size(val);

  const SortForm form = sort_form_of(a);
  std::string direction;
  std::vector<SortEntry> indexed;
  indexed.reserve(source_size);

  if (form.key < 0) {
    direction = forced_dir.value_or("ASC");
    if (nothing_to_sort) return Value::list({});
    // No key: nothing runs per element, so the walk always reads in place.
    const Snapshot snap = take_snapshot(val, false, walks_in_place(ctx, nullptr));
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = snap.item(i);
      Value detached = hold_or_keep(val, i, item, walk_handles(snap, 0), ctx.write_free);
      // The comparator only reads the key. Keep one detached tree and give the
      // output item and comparison key handles to that same immutable snapshot;
      // the old code recursively cloned the item twice.
      indexed.push_back({detached, std::move(detached), i});
    }
  } else {
    const std::string binder = form.binder >= 0 ? a.symbol(form.binder) : "_";
    const Node* body = &a.node(form.key);
    direction = form.dir >= 0 ? ascii_upper(a.text(form.dir)) : forced_dir.value_or("ASC");
    if (direction != "ASC" && direction != "DESC") {
      fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", a.pos_of(form.dir));
    }
    if (nothing_to_sort) return Value::list({});

    const bool needs_k = node_contains_var(*body, "_K");
    const Snapshot snap = take_snapshot(val, needs_k, walks_in_place(ctx, body));
    std::vector<std::pair<std::string, Value>> frame;
    frame.reserve(needs_k ? 2 : 1);
    frame.emplace_back(binder, Value::none());
    if (needs_k) frame.emplace_back("_K", Value::none());
    FrameScope scope(ctx, std::move(frame));

    {
      for (std::size_t i = 0; i < source_size; i++) {
        const Value& item = snap.item(i);
        ctx.frames.back()[0].second = item;
        if (needs_k) {
          ctx.frames.back()[1].second = make_text(snap.key(i));
        }
        Value eval_key = a.eval(*body);
        indexed.push_back({hold_or_keep(val, i, item, walk_handles(snap, 1), ctx.write_free), std::move(eval_key), i});   // collected: copied (§3.4), unless nothing could tell
      }
    }
    scope.pop();
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
  const std::size_t source_size = collection_size(value);
  // An empty result still validates its direction (spec §7.4): the arguments are
  // all evaluated and checked, whatever there is to sort.
  const bool nothing_to_do = limit == 0 || source_size == 0;

  const SortForm form = sort_form_of(a);
  const std::string binder = form.key < 0 ? "" : form.binder >= 0 ? a.symbol(form.binder) : "_";
  const Node* body = form.key >= 0 ? &a.node(form.key) : nullptr;
  const std::string direction =
      form.dir >= 0 ? ascii_upper(a.text(form.dir)) : forced_dir.value_or("ASC");
  if (direction != "ASC" && direction != "DESC") {
    fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", a.pos_of(form.dir));
  }
  if (nothing_to_do) return Value::list({});

  const bool desc = direction == "DESC";
  const auto compare = [desc](const TopEntry& lhs, const TopEntry& rhs) {
    int c = compare_values(lhs.key, rhs.key);
    if (desc) c = -c;
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
  // k >= half the source: nearly every element is admitted (heap.size() < k holds
  // until the last kept one, and with k == the source size always), so the order of
  // the output is just the `compare` order of the first k -- a sort and a cut. The
  // `compare` ties on the source index, so the result is identical either way.
  const bool sort_all = k == source_size || k * 2 >= source_size;
  FrameScope scope(ctx);
  if (body) {
    std::vector<std::pair<std::string, Value>> frame;
    frame.reserve(needs_k ? 2 : 1);
    frame.emplace_back(binder, Value::none());
    if (needs_k) frame.emplace_back("_K", Value::none());
    scope.push(std::move(frame));
  }
  if (sort_all) heap.reserve(source_size);

  std::size_t index = 0;
  const Snapshot snap = take_snapshot(value, needs_k, walks_in_place(ctx, body));
  {
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = snap.item(i);
      TopEntry candidate;
      candidate.item = item;    // copied below, only if the candidate is admitted
      candidate.idx = index;
      if (body) {
        ctx.frames.back()[0].second = item;
        if (needs_k) {
          ctx.frames.back()[1].second = make_text(snap.key(i));
        }
        candidate.key = a.eval(*body);
      } else {
        candidate.key = item;
      }
      // What TOP keeps it collects, so it keeps a copy (§3.4) -- taken here, when
      // the candidate is admitted, not at the end: a later body may assign into
      // the source. Without a body the key is the item, and reads the copy.
      // The candidate holds the item and, bodyless, the key; with a body the
      // binder's frame does: two handles either way beside the snapshot's.
      auto detach = [&body, &value, &snap, &ctx](TopEntry& c) {
        c.item = hold_or_keep(value, c.idx, snap.item(c.idx), walk_handles(snap, 2), ctx.write_free);
        if (!body) c.key = c.item;
      };
      if (sort_all) {
        // A big k keeps most of the source, and a heap then costs more compares than
        // the full sort it stands in for: collect everything, sort once below, cut.
        detach(candidate);
        heap.push_back(std::move(candidate));
      } else if (heap.size() < k) {
        detach(candidate);
        heap.push_back(std::move(candidate));
        sift_up(heap.size() - 1);
      } else if (worse(heap.front(), candidate)) {
        detach(candidate);
        heap.front() = std::move(candidate);
        sift_down(0);
      }
      index++;
    }
  }
  scope.pop();
  std::stable_sort(heap.begin(), heap.end(), compare);
  if (heap.size() > k) heap.erase(heap.begin() + static_cast<std::ptrdiff_t>(k), heap.end());
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
  std::unordered_map<std::string, std::size_t> bare_groups;
  const bool needs_k_key = node_contains_var(*key_node, "_K");
  std::vector<std::pair<std::string, Value>> frame;
  frame.reserve(needs_k_key ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k_key) frame.emplace_back("_K", Value::none());
  FrameScope scope(ctx, std::move(frame));

  // The rows a projected BUCKET collects are read by its projection and by
  // nothing else: its binder cannot be assigned, and a projection that is a
  // RECORD or LIST call copies whatever of a row it keeps (adopt_or_clone), so
  // no row reaches the result uncopied. When neither the key nor the projection
  // writes anything, no row can change while the BUCKET runs either, and the
  // copy spec §3.4 asks for is one nobody could tell from the row itself.
  const bool alias_rows = !ctx.write_free && agg_node && agg_node->t == NT::Call && !may_have_effects(agg_node->spec) &&
                          (agg_node->spec->name == "RECORD" || agg_node->spec->name == "LIST") &&
                          writes_nothing(*key_node) && writes_nothing(*agg_node);
  // Only the key runs while the rows are walked; the projection runs per group,
  // afterwards.
  const Snapshot snap = take_snapshot(val, needs_k_key, walks_in_place(ctx, key_node));
  const auto keep = [&](std::size_t i, const Value& item) {
    return alias_rows ? keep_or_alias(val, i, item, walk_handles(snap, 1))
                      : hold_or_keep(val, i, item, walk_handles(snap, 1), ctx.write_free);
  };
  {
    for (std::size_t i = 0; i < source_size; i++) {
      const Value& item = snap.item(i);
      ctx.frames.back()[0].second = item;
      if (needs_k_key) {
        ctx.frames.back()[1].second = make_text(snap.key(i));
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
      std::uint64_t hash = 0;
      if (!agg_node) {
        // A bare bucket groups by the INDEX KEY, the text -- not by identity: two
        // elements whose keys read "x" share a group whatever else they hold
        // (spec §7.3), and the output map has one entry per key text.
        const auto same = bare_groups.find(key_str);
        if (same != bare_groups.end()) found = static_cast<int>(same->second);
      } else {
        hash = eval_key.structural_hash(key_node->pos);
        const auto candidates = group_buckets.find(hash);
        if (candidates != group_buckets.end()) {
          for (const std::size_t g_idx : candidates->second) {
            if (groups[g_idx].key.eql(eval_key, key_node->pos)) {
              found = static_cast<int>(g_idx);
              break;
            }
          }
        }
      }

      if (found >= 0) {
        groups[found].rows.push_back(keep(i, item));   // collected: copied (§3.4), unless nothing could tell
      } else {
        if (!agg_node) bare_groups.emplace(key_str, groups.size());
        groups.push_back(GroupEntry{std::move(eval_key), std::move(key_str), {keep(i, item)}});
        if (agg_node) group_buckets[hash].push_back(groups.size() - 1);
      }
    }
  }
  scope.pop();

  if (!agg_node) {
    // One entry per key text, each key new by construction (`bare_groups` saw to
    // that): the record is built in one go. Value::set per group looked the key up
    // again and kept a hash index of it in step, for a record nothing has read yet
    // -- the index is built on the first lookup.
    std::vector<Value::Entry> entries;
    entries.reserve(groups.size());
    for (auto& g : groups) entries.emplace_back(std::move(g.key_str), Value::list(std::move(g.rows)));
    return Internals::with_children(Kind::None, std::move(entries));
  }

  std::vector<Value> out;
  out.reserve(groups.size());
  const bool needs_k_agg = node_contains_var(*agg_node, "_K");
  frame.clear();
  frame.reserve(needs_k_agg ? 2 : 1);
  frame.emplace_back(binder, Value::none());
  if (needs_k_agg) frame.emplace_back("_K", Value::none());
  scope.push(std::move(frame));   // the key frame was popped above; this is the projection's
  for (auto& g : groups) {
    ctx.frames.back()[0].second = Value::list(std::move(g.rows));
    if (needs_k_agg) {
      ctx.frames.back()[1].second = std::move(g.key);
    }
    // The projection's value is what this BUCKET collects, so it is copied as a
    // MAP body's is (§3.4) -- unless it is the group list itself, a container
    // this BUCKET built of rows it already collected.
    Value projected = a.eval(*agg_node);
    if (Internals::identity(projected) == Internals::identity(ctx.frames.back()[0].second)) {
      out.push_back(std::move(projected));
    } else {
      out.push_back(hold_or_adopt(std::move(projected), 0, Pos{}, ctx.write_free));
    }
  }
  scope.pop();
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
                const bool hold = ctx.write_free;
                walk(a, ctx, [&out, hold](Value&& r, std::size_t, const Value&,
                                          const Node&) -> std::optional<Value> {
                  // Collected, so copied (§3.4): a body that returns `_` hands
                  // back the source's own element, which the result must not share.
                  // A fresh temporary (RECORD(...), an arithmetic result) is already
                  // its own copy and is kept as it is, and so is everything in a
                  // program that cannot write.
                  out.push_back(hold_or_adopt(std::move(r), 0, Pos{}, hold));
                  return std::nullopt;
                });
                return Value::list(std::move(out));
              }});

  // The one aggregate that preserves keys — a filtered list should still be
  // addressable the way the original was.
  define(Spec{"FILTER", 2, 3, true, true, nullptr, [](Args& a, Context& ctx) -> Value {
                const Node& written = a.node(a.count() - 1);
                // Over a join, the conjuncts are offered to the LINK, which
                // tests what it can on the rows it joins (SEL-0052,
                // SEL-0054): this FILTER's first -- it runs before the FILTER
                // that handed the rest down -- then the handed ones. Deep
                // drops, below the join directly under this FILTER, change
                // its keys, so they are allowed only where nothing observes
                // them (`keys_unobserved`, stamped by the physical optimiser).
                std::optional<JoinPrefilter> handed = std::move(ctx.join_prefilter);
                ctx.join_prefilter.reset();
                const Node& src = a.node(0);
                std::vector<const Node*> own_nodes;
                bool over_join = false;
                // An early test holds only while nothing can change what it
                // read before this FILTER reads it (spec §7.4): a predicate
                // that may write -- an assignment, or a call SEL does not ship
                // -- is offered to no join, and the conjuncts handed from
                // above stop here too.
                if (src.t == NT::Call && (src.s == "LINK" || src.s == "LINK_LEFT") &&
                    (ctx.write_free || writes_nothing(written))) {
                  over_join = true;
                  const bool three = a.count() == 3;
                  const std::string binder = three ? a.symbol(1) : std::string("_");
                  std::vector<JoinConjunct> own = leading_field_conjuncts(written, binder);
                  for (const JoinConjunct& c : own) own_nodes.push_back(c.node);
                  // A first conjunct that is neither a field test nor total
                  // ends every walk before it starts: hand nothing, gather
                  // nothing.
                  const bool blocked = !own.empty() && !own.front().field_only && !own.front().has_total;
                  JoinPrefilter pre;
                  if (!blocked) pre.stages.push_back(JoinStage{binder, std::move(own), 0});
                  if (handed && !blocked) {
                    for (JoinStage& stage : handed->stages) pre.stages.push_back(std::move(stage));
                    pre.above = handed->above;
                    pre.obligations = handed->obligations;
                  }
                  // A body that reads _K observes the keys itself, whatever
                  // the step after it does: rows dropped below must keep
                  // their positions then.
                  pre.deep = (handed ? true : written.keys_unobserved) && !node_contains_var(written, "_K");
                  if (!pre.stages.empty()) {
                    ctx.join_prefilter = std::move(pre);
                  } else if (!handed && src.s == "LINK_LEFT") {
                    // A predicate that opens with IS_NULL of a right member's
                    // field (S6's unsold products): the join may reject the
                    // right rows it is FALSE on before building their joined
                    // rows. Nothing is reported back -- the null-extended rows
                    // were never tested -- so the whole predicate still runs
                    // over every row the join builds.
                    ctx.join_right_null = join_right_null_test(own_nodes.front(), binder, pre.deep);
                  }
                }
                try {
                  (void)a.val(0);
                } catch (...) {
                  ctx.join_prefilter.reset();
                  ctx.join_right_null.reset();
                  throw;
                }
                ctx.join_prefilter.reset();
                ctx.join_right_null.reset();
                // The join's report -- which conjuncts every row that came up
                // has passed, whether a row was kept on an error, and whether
                // any row was dropped -- goes up as it is.
                std::optional<JoinReport> report = std::move(ctx.join_prefilter_report);
                ctx.join_prefilter_report.reset();
                if (report && handed) ctx.join_prefilter_report = report;
                const Value& coll = a.val(0);
                // The conjuncts of this FILTER the join below applied held on
                // every row it built, unless it kept a row on an error: then
                // they are TRUE there, raise nowhere, and only the rest is
                // evaluated, in the source's order (with none left, the
                // join's list is the FILTER's result as it is).
                NodePtr override_body;
                if (over_join && report && !report->errored) {
                  std::vector<const Node*> rest;
                  for (const Node* n : own_nodes) if (!report->applied.count(n)) rest.push_back(n);
                  if (rest.size() < own_nodes.size()) {
                    if (rest.empty()) return coll;
                    override_body = NodePtr(std::shared_ptr<const Node>{}, rest.front());   // non-owning
                    for (std::size_t i = 1; i < rest.size(); ++i) {
                      auto node = std::make_shared<Node>();
                      node->t = NT::Bin;
                      node->pos = override_body->pos;
                      node->s = "AND";
                      node->l = override_body;
                      node->r = NodePtr(std::shared_ptr<const Node>{}, rest[i]);
                      override_body = std::move(node);
                    }
                  }
                }
                // What is kept, with where it stood. A source that is a packed list has
                // the keys 1..n, so while the kept ones are exactly 1, 2, 3, ... -- nothing
                // dropped yet, or only from the end -- the result is the packed list of
                // them and no key is ever spelled; the first gap turns it into the keyed
                // list it always was (keys are kept: FILTER leaves "2","3", not "1","2").
                const bool packed_source = coll.is_list() && coll.size() != 0 && !coll.storage().empty();
                std::vector<Value> kept;
                std::vector<std::size_t> at;
                bool sequential = packed_source;
                // The binder's frame holds each element, and walk()'s snapshot too
                // unless it read the source in place: when the body writes nothing.
                const std::uint32_t handles =
                    walks_in_place(ctx, override_body ? override_body.get() : &written) ? 1 : 2;
                walk(a, ctx, [&](const Value& r, std::size_t idx, const Value& item,
                                 const Node& body) -> std::optional<Value> {
                  if (!r.as_bool(body.pos)) return std::nullopt;
                  if (sequential && idx != kept.size()) sequential = false;
                  // keep_or_alias counts the walk's snapshot, which a write-free
                  // program does not take; there the two keep the element alike
                  // (the depth check made or proved), so the plain one is asked.
                  kept.push_back(written.borrow_rows && !ctx.write_free
                                     ? keep_or_alias(coll, idx, item, handles)
                                     : hold_or_keep(coll, idx, item, handles, ctx.write_free));  // collected: copied (§3.4), unless nothing could tell
                  at.push_back(idx);
                  return std::nullopt;
                }, override_body.get());
                if (kept.empty()) return Value::list({});
                if (sequential) return Value::list(std::move(kept));
                std::vector<Value::Entry> entries;
                entries.reserve(kept.size());
                for (std::size_t n = 0; n < kept.size(); ++n) {
                  entries.emplace_back(collection_key(coll, at[n]), std::move(kept[n]));
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
                // Every item is coerced first, then the result is measured, then
                // it is built (spec §6.4): a 10^7-code-point separator across a
                // hundred items is refused without being assembled.
                std::vector<std::string> items;
                u128 total = 0;
                for_each_collection_value(a.val(0), [&](const Value& item) {
                  items.push_back(item.as_text(a.pos_of(0)));
                  total += cp_count(items.back());
                });
                if (items.size() > 1) total += static_cast<u128>(items.size() - 1) * cp_count(sep);
                cap_text(total, a.pos());
                std::string out;
                bool first = true;
                for (const std::string& item : items) {
                  if (!first) out += sep;
                  out += item;
                  first = false;
                }
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

// Text is valid UTF-8 by construction (every Value::text is validated at the
// boundary), and UTF-8 is self-synchronising: a code point starts at every byte
// that is not 10xxxxxx, and a byte-level match of a valid needle in a valid
// haystack always begins and ends on code point boundaries. So lengths, slices,
// searches and ASCII case maps can all run on the bytes -- identical results,
// without decoding the whole string into a vector<char32_t> first.

// Byte offset reached by moving `k` code points forward from byte `pos`; the end
// of the string if there are fewer.
std::size_t advance_cps(const std::string& s, std::size_t pos, unsigned long long k) {
  const std::size_t n = s.size();
  while (k > 0 && pos < n) {
    ++pos;
    while (pos < n && (static_cast<unsigned char>(s[pos]) & 0xC0) == 0x80) ++pos;
    --k;
  }
  return pos;
}

// Code points in s[from, to).
std::size_t cp_count_range(const std::string& s, std::size_t from, std::size_t to) {
  std::size_t n = 0;
  for (std::size_t i = from; i < to; ++i) {
    if ((static_cast<unsigned char>(s[i]) & 0xC0) != 0x80) ++n;
  }
  return n;
}



std::string trim_text(const std::string& s, bool left, bool right) {
  std::size_t a = 0, b = s.size();
  const auto sp = [&](std::size_t i) { return is_sel_space(static_cast<unsigned char>(s[i])); };
  if (left) while (a < b && sp(a)) a++;
  if (right) while (b > a && sp(b - 1)) b--;
  return s.substr(a, b - a);
}

Value pad(Args& a, bool left) {
  // Byte-level: the old version widened every code point to 4 bytes twice
  // (padding, then the result) and re-encoded; at the 16M-code-point cap that was
  // ~128 MB of scratch for a 16 MB answer.
  const std::string& text = a.text(0);
  const long long width = a.non_neg_int(1);
  const std::string& fill = a.text(2);
  if (fill.empty()) fail("E_BAD_ARG", "pad fill must not be empty", a.pos_of(2));
  const std::size_t have = cp_count(text);
  if (static_cast<unsigned long long>(have) >= static_cast<unsigned long long>(width)) {
    return make_text(text);
  }
  cap_text(static_cast<u128>(width), a.pos());   // the result is exactly `width` long
  const std::size_t need = static_cast<std::size_t>(width) - have;
  const std::size_t fill_cps = cp_count(fill);
  const std::size_t cycles = need / fill_cps;
  const std::size_t rest_bytes = advance_cps(fill, 0, need % fill_cps);
  std::string padding;
  if (fill.size() == 1) {
    padding.assign(need, fill[0]);
  } else {
    padding.reserve(cycles * fill.size() + rest_bytes);
    for (std::size_t i = 0; i < cycles; i++) padding += fill;
    padding.append(fill, 0, rest_bytes);
  }
  std::string out;
  out.reserve(text.size() + padding.size());
  if (left) {
    out += padding;
    out += text;
  } else {
    out += text;
    out += padding;
  }
  return make_text(std::move(out));
}


void register_text() {
  define(Spec{"LEN", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                // A computed number's length follows from its digits and scale;
                // its text is made only if something else asks for it.
                if (const std::optional<std::size_t> len = Internals::unformatted_number_len(a.val(0), a.pos_of(0))) {
                  return make_int(static_cast<long long>(*len));
                }
                return make_int(static_cast<long long>(cp_count(a.text(0))));
              }});

  define(Spec{"LEFT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& s = a.text(0);
                const unsigned long long n = static_cast<unsigned long long>(a.non_neg_int(1));
                return make_text(s.substr(0, advance_cps(s, 0, n)));
              }});

  define(Spec{"RIGHT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& s = a.text(0);
                const unsigned long long n = static_cast<unsigned long long>(a.non_neg_int(1));
                const std::size_t total = cp_count(s);
                if (n >= total) return make_text(s);
                return make_text(s.substr(advance_cps(s, 0, total - n)));
              }});

  define(Spec{"SUBSTR", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& s = a.text(0);
                const long long start = a.integer(1);
                if (start < 1) {
                  fail("E_RANGE", "SUBSTR start is 1-based and must be at least 1", a.pos_of(1));
                }
                const std::size_t from_byte = advance_cps(s, 0, static_cast<unsigned long long>(start - 1));
                if (a.count() == 2) return make_text(s.substr(from_byte));
                // A huge length just runs to the end (advance_cps clamps; nothing
                // is added to `from`, so nothing can overflow).
                const unsigned long long n = static_cast<unsigned long long>(a.non_neg_int(2));
                const std::size_t to_byte = advance_cps(s, from_byte, n);
                return make_text(s.substr(from_byte, to_byte - from_byte));
              }});

  define(Spec{"FIND", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& needle = a.text(0);
                const std::string& hay = a.text(1);
                long long from = 0;
                if (a.count() == 3) {
                  const long long f = a.integer(2);
                  if (f < 1) {
                    fail("E_RANGE", "FIND start is 1-based and must be at least 1", a.pos_of(2));
                  }
                  from = f - 1;
                }
                if (needle.empty()) fail("E_BAD_ARG", "FIND needle must not be empty", a.pos_of(0));
                // `from` counts code points and may be enormous: past the end nothing matches.
                if (static_cast<unsigned long long>(from) > cp_count(hay)) return make_int(0);
                const std::size_t start_byte = advance_cps(hay, 0, static_cast<unsigned long long>(from));
                const std::size_t at = byte_find(hay, needle, start_byte);
                if (at == std::string::npos) return make_int(0);
                return make_int(static_cast<long long>(cp_count_range(hay, 0, at)) + 1);
              }});

  define(Spec{"REPLACE", 3, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& needle = a.text(0);
                const std::string& repl = a.text(1);
                const std::string& hay = a.text(2);
                if (needle.empty()) {
                  fail("E_BAD_ARG", "REPLACE needle must not be empty", a.pos_of(0));
                }
                // Matches are counted first so the result's length is known (and
                // capped, spec §6.4) before any of it is built.
                {
                  u128 matches = 0;
                  std::size_t j = 0;
                  for (;;) {
                    const std::size_t at = byte_find(hay, needle, j);
                    if (at == std::string::npos) break;
                    matches++;
                    j = at + needle.size();
                  }
                  const u128 base = static_cast<u128>(cp_count(hay)) - matches * cp_count(needle);
                  cap_text(base + matches * cp_count(repl), a.pos());
                }
                std::string out;
                std::size_t i = 0;
                for (;;) {
                  const std::size_t at = byte_find(hay, needle, i);
                  if (at == std::string::npos) break;
                  out.append(hay, i, at - i);
                  out += repl;
                  i = at + needle.size();
                }
                out.append(hay, i, std::string::npos);
                return make_text(std::move(out));
              }});

  define(Spec{"SPLIT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& hay = a.text(0);
                const std::string& sep = a.text(1);
                if (sep.empty()) {
                  fail("E_BAD_ARG", "SPLIT separator must not be empty", a.pos_of(1));
                }
                std::vector<Value> parts;
                std::size_t i = 0;
                for (;;) {
                  const std::size_t at = byte_find(hay, sep, i);
                  if (at == std::string::npos) break;
                  cap_collection(static_cast<u128>(parts.size()) + 2, a.pos());
                  parts.push_back(make_text(hay.substr(i, at - i)));
                  i = at + sep.size();
                }
                parts.push_back(make_text(hay.substr(i)));
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
                return make_text(ascii_upper(a.text(0)));
              }});
  define(Spec{"LOWER", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_text(ascii_lower(a.text(0)));
              }});

  define(Spec{"BACKWARDS", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& s = a.text(0);
                std::string out;
                out.reserve(s.size());
                std::size_t end = s.size();
                while (end > 0) {                      // copy code points last to first
                  std::size_t start = end - 1;
                  while (start > 0 && (static_cast<unsigned char>(s[start]) & 0xC0) == 0x80) --start;
                  out.append(s, start, end - start);
                  end = start;
                }
                return make_text(std::move(out));
              }});

  define(Spec{"REPEAT", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string s = a.text(0);
                const long long n = a.non_neg_int(1);   // saturates: a huge count is still 'huge'
                // Measured, not run (spec §6.4). An empty result is never too
                // large, whatever the count: REPEAT("", 10^30) is "" and does not
                // loop 10^30 times.
                if (s.empty() || n == 0) return make_text("");
                cap_text(static_cast<u128>(cp_count(s)) * static_cast<unsigned long long>(n),
                         a.pos());
                std::string out;
                if (s.size() == 1) {
                  out.assign(static_cast<std::size_t>(n), s[0]);
                } else {
                  out.reserve(s.size() * static_cast<std::size_t>(n));
                  for (long long i = 0; i < n; i++) out += s;
                }
                return make_text(std::move(out));
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
                const std::string& s = a.text(0);
                if (s.empty()) fail("E_RANGE", "CODE of empty text", a.pos_of(0));
                const CodePoints first = decode_utf8(s.substr(0, advance_cps(s, 0, 1)));
                return make_int(static_cast<long long>(first[0]));
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
                return make_num(dec_ceil(a.dec(0), a.pos()));
              }});
  define(Spec{"FLOOR", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_floor(a.dec(0), a.pos()));
              }});
  define(Spec{"TRUNC", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_trunc(a.dec(0)));
              }});
  define(Spec{"CANON", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                return make_num(dec_trim_scale(a.dec(0)));
              }});

  define(Spec{"ROUND", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                // Range-checked, not cast. A bare static_cast<int> wraps modulo
                // 2^32, which turned ROUND(1.5, 4294967296) into a confident `2`
                // — a plausible wrong answer, the worst failure this project
                // can produce — and a wrapped negative scale then built a
                // string of length SIZE_MAX and escaped as std::length_error.
                //
                // The first argument is coerced before the second is checked:
                // the strict lane is left to right (spec §6.2), and the planned
                // spelling of the same call already was.
                const Dec x = a.dec(0);
                const long long n = checked_size_arg(a.dec(1), "ROUND", "scale", MAX_SCALE, a.pos_of(1));
                return make_num(dec_round(x, n, a.pos()));
              }});
  define(Spec{"POWER", 2, 2, false, false, nullptr, [](Args& a, Context&) -> Value {
                const Dec x = a.dec(0);
                const long long n = checked_size_arg(a.dec(1), "POWER", "exponent", MAX_POWER, a.pos_of(1));
                return make_num(dec_power(x, n, a.pos()));
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

// One lookup per input byte: -1 for anything outside the alphabet ('=' included,
// which the decoder handles itself). A strchr per character walked the alphabet.
constexpr std::array<signed char, 256> make_b64_table() {
  std::array<signed char, 256> t{};
  for (auto& v : t) v = -1;
  for (int i = 0; i < 64; ++i) t[static_cast<unsigned char>("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"[i])] = static_cast<signed char>(i);
  return t;
}
constexpr std::array<signed char, 256> B64_TABLE = make_b64_table();

int b64_index(char c) { return B64_TABLE[static_cast<unsigned char>(c)]; }

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
                const std::string& b = a.bytes(0);
                cap_text(b.size(), a.pos());   // 4 bytes per astral code point: BIN can outnumber TEXT
                return make_bin(b);
              }});

  define(Spec{"FROM_UTF8", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& b = a.bytes(0);
                decode_utf8(b, a.pos_of(0));   // validates; E_UTF8 if not
                return make_text(b);
              }});

  define(Spec{"TO_HEX", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& b = a.bytes(0);
                cap_text(static_cast<u128>(b.size()) * 2, a.pos());
                return make_text(to_hex(b));
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
                    // The position, not the text: slicing bytes put half a UTF-8
                    // sequence into the message, and the input is data.
                    fail("E_BAD_ARG", "FROM_HEX: byte " + std::to_string(i * 2 + 1) + " is not a hex digit pair",
                         a.pos_of(0));
                  }
                  out[i] = static_cast<char>((hi << 4) | lo);
                }
                return make_bin(std::move(out));
              }});

  define(Spec{"ENCODE_BASE64", 1, 1, false, false, nullptr, [](Args& a, Context&) -> Value {
                const std::string& b = a.bytes(0);
                cap_text(static_cast<u128>((b.size() + 2) / 3) * 4, a.pos());
                std::string out;
                out.reserve(((b.size() + 2) / 3) * 4);
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
                out.reserve(s.size() / 4 * 3);
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
                      fail("E_BAD_ARG", "invalid base64 character at byte " + std::to_string(i + k + 1), pos);
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
                cap_collection(a.bytes(0).size(), a.pos());
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
                } else if (!(v.kind() == Kind::None && v.size() == 0)) {
                  items.push_back(v);   // a scalar is one element; NULL and the empty list are none (§7.7)
                }
                std::string out(items.size(), '\0');
                for (std::size_t i = 0; i < items.size(); i++) {
                  const Dec d = as_dec(items[i], a.pos_of(0));
                  // A byte is an integer *value*: 1.0 and "255.00" qualify, 1.5 does not.
                  if (!dec_is_integer(d)) {
                    fail("E_NOT_INT", "LTB element " + std::to_string(i + 1) + " must be a whole number",
                         a.pos_of(0));
                  }
                  const long long n = dec_to_int(d);
                  if (n < 0 || n > 255) {
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

// The detail every host spells alike (spec/errors.md, "Message conventions"):
// `at` is a code point offset into the pattern, and a pattern longer than 80
// code points is quoted as its first 77 and "...".
[[noreturn]] void bad_regex(const std::string& message, const std::string& pattern, std::size_t at,
                            Pos pos) {
  std::size_t cps = 0, cut = pattern.size();
  for (std::size_t b = 0; b < pattern.size(); ++b) {
    if ((static_cast<unsigned char>(pattern[b]) & 0xc0) == 0x80) continue;
    if (cps == 77) cut = b;
    ++cps;
  }
  const std::string shown = cps > 80 ? pattern.substr(0, cut) + "..." : pattern;
  fail("E_REGEX_SYNTAX",
       message + " (at offset " + std::to_string(at) + " of /" + shown + "/)", pos);
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

// A `[` followed by `:`, `.` or `=` inside a class is refused, closed or not
// (SPEC 7.8): the POSIX bracket forms `[:alpha:]`, `[.x.]` and `[=x=]` are read
// differently by the engines, and so is an unfinished one, so no spelling of the
// prefix is portable.
bool starts_posix_form(const CodePoints& p, std::size_t at) {
  if (at + 1 >= p.size() || p[at] != U'[') return false;
  const char32_t x = p[at + 1];
  return x == U':' || x == U'.' || x == U'=';
}

// Returns the rewritten text; `next` receives the index just past the closing ']'.
std::string validate_class(const CodePoints& p, std::size_t start, const std::string& pattern,
                           Pos pos, std::size_t& next) {
  std::size_t i = start + 1;
  std::string out = "[";
  if (i < p.size() && p[i] == U'^') { out += "^"; i++; }
  // What the previous member of the class was, for the range rule below: a
  // class escape (\d \w \s) cannot be either end of a range -- PCRE takes the
  // hyphen literally and ECMAScript refuses -- while a hyphen first or last is
  // literal and fine.
  enum class Prev { None, Char, ClassEscape };
  Prev prev = Prev::None;
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
    if (starts_posix_form(p, i)) {
      bad_regex("POSIX classes such as [[:alpha:]] are not portable", pattern, i, pos);
    }
    count++;
    if (c == U'-' && prev != Prev::None && i + 1 < p.size() && p[i + 1] != U']') {
      // A range operator. Its left end must be a single character...
      if (prev == Prev::ClassEscape) {
        bad_regex("a class escape cannot start a range — write the hyphen last or first", pattern, i,
                  pos);
      }
      // ...and so must its right end.
      if (p[i + 1] == U'\\' && i + 2 < p.size() && expand_inside().count(p[i + 2])) {
        bad_regex("a class escape cannot end a range — write the hyphen last or first", pattern, i,
                  pos);
      }
    }
    if (c == U'\\') {
      if (i + 1 >= p.size()) {
        bad_regex("trailing backslash in character class", pattern, i, pos);
      }
      const char32_t e = p[i + 1];
      auto it = expand_inside().find(e);
      if (it != expand_inside().end()) {
        out += it->second;
        i += 2;
        prev = Prev::ClassEscape;
        continue;
      }
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
        prev = Prev::Char;
        continue;
      }
      reject_escape(e, pattern, i, pos);
    }
    encode_cp(out, c);
    i++;
    prev = Prev::Char;
  }
  bad_regex("unterminated character class", pattern, start, pos);
}

// --- the pattern tree (spec §7.8) -------------------------------------------
//
// The rewriting pass above and below validates tokens; this parses the same
// pattern into a tree, because three of the rules are about SHAPE -- a loop whose
// body can match the empty string, a capture that need not take part in every
// iteration, groups nested past the cap -- and the exponential-ambiguity analysis
// that follows in a later change needs the tree too. Groups are kept (capturing or
// not); everything a letter-like token stands for is an Atom, an anchor or an
// empty pattern is Empty.
using RxRanges = std::vector<std::pair<char32_t, char32_t>>;   // sorted, merged, inclusive

struct RxNode {
  enum class K { Empty, Anchor, Atom, Cat, Alt, Group, Repeat };
  K k = K::Empty;
  bool capture = false;    // Group
  long long lo = 1;        // Repeat
  long long hi = 1;        // Repeat; -1 = unbounded
  std::size_t at = 0;      // Repeat: the quantifier's code point offset, for messages
  std::vector<int> kids;
  RxRanges set;            // Atom: the code points it reads (folded under `i`)
  bool nullable = true;    // the measures below are filled in by RxParser::measure
  long long minlen = 0;
  long long maxlen = 0;
};

constexpr long long RX_SAT = 1LL << 40;   // saturation of minlen/maxlen (spec §7.8)
constexpr char32_t RX_MAX_CP = 0x10FFFF;

// --- code point sets ------------------------------------------------------------
RxRanges rx_norm(RxRanges rs) {
  std::sort(rs.begin(), rs.end());
  RxRanges out;
  for (const auto& r : rs) {
    if (!out.empty() && r.first <= out.back().second + 1) {
      if (r.second > out.back().second) out.back().second = r.second;
    } else {
      out.push_back(r);
    }
  }
  return out;
}

RxRanges rx_negate(const RxRanges& in) {
  const RxRanges rs = rx_norm(in);
  RxRanges out;
  char32_t next = 0;
  bool done = false;
  for (const auto& r : rs) {
    if (r.first > next) out.emplace_back(next, r.first - 1);
    if (r.second >= RX_MAX_CP) { done = true; break; }
    next = r.second + 1;
  }
  if (!done && next <= RX_MAX_CP) out.emplace_back(next, RX_MAX_CP);
  return out;
}

bool rx_has(const RxRanges& rs, char32_t c) {
  for (const auto& r : rs) if (r.first <= c && c <= r.second) return true;
  return false;
}

// `i`: simple case folding restricted to what an ASCII pattern can reach -- the
// ASCII case mirror, plus U+212A with k/K and U+017F with s/S (spec §7.8).
RxRanges rx_fold(const RxRanges& rs) {
  RxRanges all = rs;
  for (const auto& r : rs) {
    const char32_t ulo = std::max<char32_t>(r.first, 0x41), uhi = std::min<char32_t>(r.second, 0x5A);
    if (ulo <= uhi) all.emplace_back(ulo + 32, uhi + 32);
    const char32_t llo = std::max<char32_t>(r.first, 0x61), lhi = std::min<char32_t>(r.second, 0x7A);
    if (llo <= lhi) all.emplace_back(llo - 32, lhi - 32);
  }
  all = rx_norm(std::move(all));
  if (rx_has(all, U'k') || rx_has(all, U'K')) all.emplace_back(0x212A, 0x212A);
  if (rx_has(all, U's') || rx_has(all, U'S')) all.emplace_back(0x17F, 0x17F);
  return rx_norm(std::move(all));
}

bool rx_intersects(const RxRanges& x, const RxRanges& y) {
  std::size_t i = 0, j = 0;
  while (i < x.size() && j < y.size()) {
    if (x[i].second < y[j].first) i++;
    else if (y[j].second < x[i].first) j++;
    else return true;
  }
  return false;
}

// The largest number of the given sets that share one code point. Closings sort
// before openings at a shared point, so adjacent ranges do not count as overlap.
int rx_max_cover(const std::vector<const RxRanges*>& sets) {
  std::vector<std::pair<char32_t, int>> ev;
  for (const RxRanges* s : sets) {
    for (const auto& r : *s) {
      ev.emplace_back(r.first, 1);
      ev.emplace_back(r.second + 1, -1);
    }
  }
  std::sort(ev.begin(), ev.end());
  int best = 0, cur = 0;
  for (const auto& e : ev) {
    cur += e.second;
    best = std::max(best, cur);
  }
  return best;
}

const RxRanges& rx_class_set(char32_t e) {
  static const RxRanges digit{{U'0', U'9'}};
  static const RxRanges word{{U'0', U'9'}, {U'A', U'Z'}, {U'_', U'_'}, {U'a', U'z'}};
  static const RxRanges space{{0x09, 0x0D}, {0x20, 0x20}};   // [ \t\n\r\f\x0b]
  return e == U'd' ? digit : e == U'w' ? word : space;
}

char32_t rx_control(char32_t e) {
  return e == U'n' ? 10 : e == U'r' ? 13 : e == U't' ? 9 : 12;   // \f
}

struct RxTree {
  std::vector<RxNode> nodes;
  int root = -1;
};

class RxParser {
 public:
  RxParser(const CodePoints& p, const std::string& pattern, Pos pos, bool ignore_case)
      : p_(p), pattern_(pattern), pos_(pos), ic_(ignore_case) {}

  RxTree parse() {
    tree_.root = alternation(0);
    if (i_ < p_.size()) bad_regex("unmatched )", pattern_, i_, pos_);
    return std::move(tree_);
  }

 private:
  const CodePoints& p_;
  const std::string& pattern_;
  Pos pos_;
  bool ic_;
  std::size_t i_ = 0;
  std::size_t groups_ = 0;
  RxTree tree_;

  int make(RxNode::K k) {
    RxNode n;
    n.k = k;
    tree_.nodes.push_back(std::move(n));
    const int id = static_cast<int>(tree_.nodes.size()) - 1;
    if (k == RxNode::K::Empty || k == RxNode::K::Anchor || k == RxNode::K::Atom) measure(id);
    return id;
  }

  // nullable / minlen / maxlen of node `id` from its kids (already measured).
  void measure(int id) {
    RxNode& n = tree_.nodes[static_cast<std::size_t>(id)];
    auto kid = [&](std::size_t j) -> const RxNode& { return tree_.nodes[static_cast<std::size_t>(n.kids[j])]; };
    switch (n.k) {
      case RxNode::K::Empty:
      case RxNode::K::Anchor: n.nullable = true; n.minlen = n.maxlen = 0; break;
      case RxNode::K::Atom: n.nullable = false; n.minlen = n.maxlen = 1; break;
      case RxNode::K::Cat: {
        n.nullable = true;
        long long mn = 0, mx = 0;
        for (std::size_t j = 0; j < n.kids.size(); j++) {
          n.nullable = n.nullable && kid(j).nullable;
          mn = std::min(RX_SAT, mn + kid(j).minlen);
          mx = std::min(RX_SAT, mx + kid(j).maxlen);
        }
        n.minlen = mn;
        n.maxlen = mx;
        break;
      }
      case RxNode::K::Alt: {
        n.nullable = false;
        long long mn = RX_SAT, mx = 0;
        for (std::size_t j = 0; j < n.kids.size(); j++) {
          n.nullable = n.nullable || kid(j).nullable;
          mn = std::min(mn, kid(j).minlen);
          mx = std::max(mx, kid(j).maxlen);
        }
        n.minlen = mn;
        n.maxlen = mx;
        break;
      }
      case RxNode::K::Group:
        n.nullable = kid(0).nullable;
        n.minlen = kid(0).minlen;
        n.maxlen = kid(0).maxlen;
        break;
      case RxNode::K::Repeat: {
        const RxNode& x = kid(0);
        n.nullable = n.lo == 0 || x.nullable;
        n.minlen = std::min(RX_SAT, n.lo * x.minlen);
        if (n.hi == 0 || x.maxlen == 0) n.maxlen = 0;
        else if (n.hi == -1) n.maxlen = RX_SAT;
        else n.maxlen = std::min(RX_SAT, n.hi * x.maxlen);
        break;
      }
    }
  }

  int atom_of(RxRanges set) {
    set = rx_norm(std::move(set));
    if (ic_) set = rx_fold(set);
    const int a = make(RxNode::K::Atom);
    tree_.nodes[static_cast<std::size_t>(a)].set = std::move(set);
    return a;
  }

  // One member of a class, at p_[i_]: a code point (escape or plain), or -1 with
  // `cls` holding \d \w \s. The pattern already passed validate_class.
  long long class_member(RxRanges* cls) {
    const char32_t c = p_[i_];
    if (c != U'\\') { i_++; return static_cast<long long>(c); }
    const char32_t e = p_[i_ + 1];
    i_ += 2;
    if (e == U'd' || e == U'w' || e == U's') {
      const RxRanges& r = rx_class_set(e);
      cls->insert(cls->end(), r.begin(), r.end());
      return -1;
    }
    if (is_control_escape(e)) return static_cast<long long>(rx_control(e));
    return static_cast<long long>(e);
  }

  bool at(char32_t c) const { return i_ < p_.size() && p_[i_] == c; }

  int alternation(int depth) {
    std::vector<int> branches{concatenation(depth)};
    while (at(U'|')) {
      i_++;
      branches.push_back(concatenation(depth));
    }
    if (branches.size() == 1) return branches[0];
    const int alt = make(RxNode::K::Alt);
    tree_.nodes[alt].kids = std::move(branches);
    measure(alt);
    return alt;
  }

  int concatenation(int depth) {
    std::vector<int> items;
    while (i_ < p_.size() && p_[i_] != U'|' && p_[i_] != U')') items.push_back(repeated(depth));
    if (items.empty()) return make(RxNode::K::Empty);
    if (items.size() == 1) return items[0];
    const int cat = make(RxNode::K::Cat);
    tree_.nodes[cat].kids = std::move(items);
    measure(cat);
    return cat;
  }

  static bool is_quantifier_start(char32_t c) {
    return c == U'*' || c == U'+' || c == U'?' || c == U'{';
  }

  // One atom and the quantifier after it, if any.
  int repeated(int depth) {
    if (is_quantifier_start(p_[i_])) bad_regex("nothing to repeat", pattern_, i_, pos_);
    const int atom = primary(depth);
    if (i_ >= p_.size() || !is_quantifier_start(p_[i_])) return atom;
    const std::size_t at_q = i_;
    if (tree_.nodes[atom].k == RxNode::K::Anchor) {
      bad_regex("an anchor cannot be quantified", pattern_, at_q, pos_);
    }
    long long lo = 0, hi = -1;
    const char32_t q = p_[i_];
    if (q == U'*') { i_++; }
    else if (q == U'+') { lo = 1; i_++; }
    else if (q == U'?') { hi = 1; i_++; }
    else {
      i_++;   // '{'; the rewriting pass has already validated the form and the bounds
      lo = read_bound(p_, i_);
      hi = lo;
      if (at(U',')) {
        i_++;
        const std::size_t hs = i_;
        const long long h = read_bound(p_, i_);
        hi = (i_ > hs) ? h : -1;
      }
      i_++;   // '}'
    }
    if (at(U'?')) i_++;   // lazy: which match is found, not which words match
    if (i_ < p_.size() && is_quantifier_start(p_[i_])) {
      bad_regex("nothing to repeat", pattern_, i_, pos_);
    }
    const int rep = make(RxNode::K::Repeat);
    tree_.nodes[rep].lo = lo;
    tree_.nodes[rep].hi = hi;
    tree_.nodes[rep].at = at_q;
    tree_.nodes[rep].kids = {atom};
    measure(rep);
    return rep;
  }

  int primary(int depth) {
    const char32_t c = p_[i_];
    if (c == U'(') {
      if (depth + 1 > MAX_DEPTH) {
        bad_regex("groups nested deeper than " + std::to_string(MAX_DEPTH), pattern_, i_, pos_);
      }
      if (++groups_ > static_cast<std::size_t>(MAX_REGEX_GROUPS)) {
        bad_regex("more than " + std::to_string(MAX_REGEX_GROUPS) + " groups", pattern_, i_, pos_);
      }
      bool capture = true;
      const std::size_t open = i_;
      i_++;
      if (at(U'?')) { capture = false; i_ += 2; }   // `(?:`, the only `(?` the pass above lets by
      const int inner = alternation(depth + 1);
      if (!at(U')')) bad_regex("unterminated group", pattern_, open, pos_);
      i_++;
      const int g = make(RxNode::K::Group);
      tree_.nodes[g].capture = capture;
      tree_.nodes[g].kids = {inner};
      measure(g);
      return g;
    }
    if (c == U'[') {
      // Validated by validate_class already; read its members into a set. The set
      // is folded before it is negated (spec §7.8).
      i_++;
      const bool negated = at(U'^');
      if (negated) i_++;
      RxRanges rs;
      while (i_ < p_.size() && p_[i_] != U']') {
        const long long lo = class_member(&rs);
        if (lo < 0) continue;   // a class escape: a set, never a range end
        if (at(U'-') && i_ + 1 < p_.size() && p_[i_ + 1] != U']') {
          const std::size_t dash = i_;
          i_++;
          const long long hi = class_member(&rs);
          // A range that runs backwards is a compile-time refusal whatever the
          // flags (SPEC 7.8), as it is in every other host.
          if (hi >= 0 && hi < lo) bad_regex("a class range runs backwards", pattern_, dash, pos_);
          rs.emplace_back(static_cast<char32_t>(lo), static_cast<char32_t>(hi < 0 ? lo : hi));
        } else {
          rs.emplace_back(static_cast<char32_t>(lo), static_cast<char32_t>(lo));
        }
      }
      i_++;
      rs = rx_norm(std::move(rs));
      if (ic_) rs = rx_fold(rs);
      if (negated) rs = rx_negate(rs);
      const int a = make(RxNode::K::Atom);
      tree_.nodes[static_cast<std::size_t>(a)].set = std::move(rs);
      return a;
    }
    if (c == U'\\') {
      const char32_t e = p_[i_ + 1];
      i_ += 2;
      if (e == U'd' || e == U'w' || e == U's') return atom_of(rx_class_set(e));
      if (e == U'D' || e == U'W' || e == U'S') {
        return atom_of(rx_negate(rx_class_set(static_cast<char32_t>(e + 32))));
      }
      const char32_t cp = is_control_escape(e) ? rx_control(e) : e;
      return atom_of({{cp, cp}});
    }
    i_++;
    if (c == U'^' || c == U'$') return make(RxNode::K::Anchor);
    if (c == U'.') return atom_of({{0, RX_MAX_CP}});
    return atom_of({{c, c}});
  }
};

// True when a capture inside `n` need not take part every time `n` is entered:
// it sits under an alternation with other branches, or under a quantifier whose
// minimum is 0.
bool rx_optional_capture(const RxTree& t, int n, bool optional) {
  const RxNode& x = t.nodes[static_cast<std::size_t>(n)];
  switch (x.k) {
    case RxNode::K::Empty:
    case RxNode::K::Anchor:
    case RxNode::K::Atom: return false;
    case RxNode::K::Cat:
      for (const int k : x.kids) if (rx_optional_capture(t, k, optional)) return true;
      return false;
    case RxNode::K::Alt:
      for (const int k : x.kids) if (rx_optional_capture(t, k, true)) return true;
      return false;
    case RxNode::K::Group:
      if (x.capture && optional) return true;
      return rx_optional_capture(t, x.kids[0], optional);
    case RxNode::K::Repeat: return rx_optional_capture(t, x.kids[0], optional || x.lo == 0);
  }
  return false;
}

// The shape rules of §7.8, over the whole tree. A loop is a quantifier whose
// maximum is above 1; `?`, `{0,1}` and `{1}` are not loops.
void check_loops(const RxTree& t, const std::string& pattern, Pos pos) {
  // The tree is at most MAX_DEPTH groups deep, but a Cat/Alt/Repeat layer sits
  // between groups, so walk with an explicit stack anyway.
  std::vector<int> work{t.root};
  while (!work.empty()) {
    const int n = work.back();
    work.pop_back();
    const RxNode& x = t.nodes[static_cast<std::size_t>(n)];
    if (x.k == RxNode::K::Repeat && (x.hi == -1 || x.hi > 1)) {
      // RxParser::measure stored it on every node as the tree was built.
      if (t.nodes[static_cast<std::size_t>(x.kids[0])].nullable) {
        bad_regex("a loop whose body can match the empty string is not portable", pattern, x.at, pos);
      }
      if (rx_optional_capture(t, x.kids[0], false)) {
        bad_regex("a capture inside a loop must take part in every iteration", pattern, x.at, pos);
      }
    }
    for (const int k : x.kids) work.push_back(k);
  }
}


// --- exponential ambiguity (spec §7.8 "Refused for its running time") -----------
//
// A Glushkov position automaton over the tree, then the refusals in the order the
// spec gives them. tools/regex-ambiguity-ref.py is the reference this is a
// port of: every count, cap and verdict is the same, and every walk here is a
// loop over explicit work lists or a recursion no deeper than the tree.
constexpr int RX_UNROLL = 8;
// The closed-form caps and the budget of spec §7.8, from spec/limits.json.
constexpr long long RX_P_MAX = sel_limits::REGEX_ANALYSIS_POSITIONS;   // positions
constexpr long long RX_E_MAX = sel_limits::REGEX_ANALYSIS_EDGES;       // follow edges
constexpr long long RX_D_MAX = sel_limits::REGEX_ANALYSIS_RANGES;      // sum over edges of the ranges at the target
constexpr long long RX_Q_MAX = sel_limits::REGEX_ANALYSIS_PAIR_WORK;   // pair-graph work
constexpr long long RX_AMB_MAX = sel_limits::REGEX_AMBIGUITY_BUDGET;

int rx_ceil_log2(long long k) {   // k >= 2
  int c = 0;
  for (long long v = k - 1; v > 0; v >>= 1) c++;
  return c;
}

class RxAnalysis {
 public:
  RxAnalysis(const RxTree& t, const std::string& pattern, Pos pos)
      : t_(t), pattern_(pattern), pos_(pos) {
    cls_.push_back(nullptr);   // position 0 is the start, reading nothing
    succ_.emplace_back();
  }

  void run() {
    Walked w = walk(t_.root, false);
    succ_[0] = w.f;
    build_components();
    // The budget: choices outside every cycle, the start included.
    for (std::size_t p = 0; p < succ_.size(); p++) {
      if (cyc_[p] || succ_[p].size() < 2) continue;
      std::vector<const RxRanges*> sets;
      for (const int q : succ_[p]) sets.push_back(cls_[static_cast<std::size_t>(q)]);
      const int m = rx_max_cover(sets);
      if (m >= 2) {
        amb_ += rx_ceil_log2(m);
        if (amb_ > RX_AMB_MAX) refuse("ambiguity budget exceeded");
      }
    }
    exponential_ambiguity();
  }

 private:
  struct Walked {
    bool nl = true;
    std::vector<int> f, l;
  };
  // An item of a concatenation: a tree node, or (node < 0) the nested optional
  // chain of `chain` more optional copies of node `x` -- the unrolled tail of a
  // counted repeat, OPT(CAT[x, OPT(CAT[x, ...])]).
  struct Item {
    int node;
    int chain;
    int x;
  };

  const RxTree& t_;
  const std::string& pattern_;
  Pos pos_;
  std::vector<const RxRanges*> cls_;
  std::vector<std::vector<int>> succ_;
  std::unordered_map<unsigned long long, bool> tag_;
  long long e_ = 0, d_ = 0, amb_ = 0;
  std::vector<int> comp_;
  std::vector<char> cyc_;

  [[noreturn]] void refuse(const std::string& why) const {
    bad_regex("the pattern is refused for its running time: " + why, pattern_, 0, pos_);
  }

  const RxNode& node(int n) const { return t_.nodes[static_cast<std::size_t>(n)]; }

  void join(const std::vector<int>& L, const std::vector<int>& F, bool sync) {
    e_ += static_cast<long long>(L.size()) * static_cast<long long>(F.size());
    long long ranges = 0;
    for (const int q : F) ranges += static_cast<long long>(cls_[static_cast<std::size_t>(q)]->size());
    d_ += static_cast<long long>(L.size()) * ranges;
    if (e_ > RX_E_MAX || d_ > RX_D_MAX) refuse("the analysis is too large");
    for (const int a : L) {
      for (const int b : F) {
        const unsigned long long key = (static_cast<unsigned long long>(a) << 32) | static_cast<unsigned>(b);
        auto it = tag_.find(key);
        if (it != tag_.end()) {
          if (it->second && sync) continue;
          refuse("the same follow edge is generated twice");
        }
        tag_.emplace(key, sync);
        succ_[static_cast<std::size_t>(a)].push_back(b);
      }
    }
  }

  void eps(int k, bool inloop) {
    if (k <= 0) return;
    if (inloop) refuse("a nullable choice inside a loop");
    amb_ += k;
    if (amb_ > RX_AMB_MAX) refuse("ambiguity budget exceeded");
  }

  Walked walk_items(const std::vector<Item>& items, bool inloop) {
    Walked out;   // nl = true, f = l = {}
    for (const Item& it : items) {
      Walked w = walk_item(it, inloop);
      join(out.l, w.f, false);
      if (out.nl) out.f.insert(out.f.end(), w.f.begin(), w.f.end());
      if (w.nl) out.l.insert(out.l.end(), w.l.begin(), w.l.end());
      else out.l = std::move(w.l);
      out.nl = out.nl && w.nl;
    }
    return out;
  }

  Walked walk_item(const Item& it, bool inloop) {
    if (it.node >= 0) return walk(it.node, inloop);
    if (it.chain == 0) return Walked{};   // EPS
    Walked w = walk_items({Item{it.x, 0, -1}, Item{-1, it.chain - 1, it.x}}, inloop);
    w.nl = true;   // OPT: optional, no cost of its own
    return w;
  }

  Walked walk(int n, bool inloop) {
    const RxNode& x = node(n);
    switch (x.k) {
      case RxNode::K::Empty:
      case RxNode::K::Anchor: return Walked{};
      case RxNode::K::Atom: {
        if (static_cast<long long>(cls_.size()) >= RX_P_MAX) refuse("the analysis is too large");
        cls_.push_back(&x.set);
        succ_.emplace_back();
        Walked w;
        w.nl = false;
        w.f = {static_cast<int>(cls_.size()) - 1};
        w.l = w.f;
        return w;
      }
      case RxNode::K::Group: return walk(x.kids[0], inloop);
      case RxNode::K::Alt: {
        long long k_null = 0;
        for (const int b : x.kids) if (node(b).nullable) k_null++;
        if (k_null >= 2) eps(rx_ceil_log2(k_null), inloop);
        Walked out;
        out.nl = k_null > 0;
        for (const int b : x.kids) {
          Walked w = walk(b, inloop);
          out.f.insert(out.f.end(), w.f.begin(), w.f.end());
          out.l.insert(out.l.end(), w.l.begin(), w.l.end());
        }
        return out;
      }
      case RxNode::K::Cat: {
        std::vector<Item> items;
        items.reserve(x.kids.size());
        for (const int k : x.kids) items.push_back(Item{k, 0, -1});
        return walk_items(items, inloop);
      }
      case RxNode::K::Repeat: {
        const int xi = x.kids[0];
        const RxNode& body = node(xi);
        if (x.hi == 0) return Walked{};
        if (x.hi != -1 && x.hi <= RX_UNROLL) {
          if (x.lo == 0 && x.hi == 1 && body.nullable) eps(1, inloop);
          std::vector<Item> items;
          for (long long j = 0; j < x.lo; j++) items.push_back(Item{xi, 0, -1});
          items.push_back(Item{-1, static_cast<int>(x.hi - x.lo), xi});
          return walk_items(items, inloop);
        }
        Walked w = walk(xi, true);
        join(w.l, w.f, body.minlen == body.maxlen && body.maxlen > 0 && body.maxlen < RX_SAT);
        w.nl = x.lo == 0 || body.nullable;
        return w;
      }
    }
    return Walked{};
  }

  // Iterative Tarjan: component id per position, and whether it lies on a cycle.
  void build_components() {
    const std::size_t n = succ_.size();
    std::vector<int> index(n, -1), low(n, 0), stack;
    std::vector<char> on(n, 0);
    comp_.assign(n, -1);
    int counter = 0, ncomp = 0;
    std::vector<std::pair<int, std::size_t>> work;
    for (std::size_t root = 0; root < n; root++) {
      if (index[root] != -1) continue;
      work.assign(1, {static_cast<int>(root), 0});
      index[root] = low[root] = counter++;
      stack.push_back(static_cast<int>(root));
      on[root] = 1;
      while (!work.empty()) {
        const int v = work.back().first;
        const std::size_t ei = work.back().second;
        const auto vu = static_cast<std::size_t>(v);
        if (ei < succ_[vu].size()) {
          work.back().second = ei + 1;
          const int w = succ_[vu][ei];
          const auto wu = static_cast<std::size_t>(w);
          if (index[wu] == -1) {
            index[wu] = low[wu] = counter++;
            stack.push_back(w);
            on[wu] = 1;
            work.emplace_back(w, 0);
          } else if (on[wu]) {
            low[vu] = std::min(low[vu], index[wu]);
          }
        } else {
          work.pop_back();
          if (!work.empty()) {
            const auto u = static_cast<std::size_t>(work.back().first);
            low[u] = std::min(low[u], low[vu]);
          }
          if (low[vu] == index[vu]) {
            for (;;) {
              const int w = stack.back();
              stack.pop_back();
              on[static_cast<std::size_t>(w)] = 0;
              comp_[static_cast<std::size_t>(w)] = ncomp;
              if (w == v) break;
            }
            ncomp++;
          }
        }
      }
    }
    std::vector<int> size(static_cast<std::size_t>(ncomp), 0);
    for (std::size_t v = 0; v < n; v++) size[static_cast<std::size_t>(comp_[v])]++;
    cyc_.assign(n, 0);
    for (std::size_t v = 0; v < n; v++) {
      bool self = false;
      for (const int w : succ_[v]) if (static_cast<std::size_t>(w) == v) self = true;
      cyc_[v] = size[static_cast<std::size_t>(comp_[v])] > 1 || self;
    }
  }

  // Two different paths from a state back to a state on one word: a pair (p, r)
  // with p != r that a diagonal pair reaches and that reaches a diagonal pair,
  // stepping only inside one cycle on characters both positions read.
  void exponential_ambiguity() {
    const std::size_t n = succ_.size();
    std::vector<long long> insc(n, -1);
    auto d = [&](int p) {
      long long& v = insc[static_cast<std::size_t>(p)];
      if (v < 0) {
        v = 0;
        for (const int q : succ_[static_cast<std::size_t>(p)]) {
          if (comp_[static_cast<std::size_t>(q)] == comp_[static_cast<std::size_t>(p)]) v++;
        }
      }
      return v;
    };
    std::unordered_map<unsigned long long, int> id;
    std::vector<std::pair<int, int>> nodes;
    std::vector<std::vector<int>> fwd;
    auto intern = [&](int p, int r) {
      const unsigned long long key = (static_cast<unsigned long long>(p) << 32) | static_cast<unsigned>(r);
      auto it = id.find(key);
      if (it != id.end()) return std::make_pair(it->second, false);
      const int k = static_cast<int>(nodes.size());
      id.emplace(key, k);
      nodes.emplace_back(p, r);
      fwd.emplace_back();
      return std::make_pair(k, true);
    };
    std::vector<int> todo;
    for (std::size_t q = 0; q < n; q++) {
      if (cyc_[q] && !cls_[q]->empty()) todo.push_back(intern(static_cast<int>(q), static_cast<int>(q)).first);
    }
    long long q_work = 0;
    while (!todo.empty()) {
      const int k = todo.back();
      todo.pop_back();
      const int p = nodes[static_cast<std::size_t>(k)].first, r = nodes[static_cast<std::size_t>(k)].second;
      q_work += d(p) * d(r);
      if (q_work > RX_Q_MAX) refuse("the analysis is too large");
      const int c = comp_[static_cast<std::size_t>(p)];
      for (const int p2 : succ_[static_cast<std::size_t>(p)]) {
        if (comp_[static_cast<std::size_t>(p2)] != c) continue;
        for (const int r2 : succ_[static_cast<std::size_t>(r)]) {
          if (comp_[static_cast<std::size_t>(r2)] != c) continue;
          if (!rx_intersects(*cls_[static_cast<std::size_t>(p2)], *cls_[static_cast<std::size_t>(r2)])) continue;
          const auto [k2, fresh] = intern(p2, r2);
          fwd[static_cast<std::size_t>(k)].push_back(k2);
          if (fresh) todo.push_back(k2);
        }
      }
    }
    std::vector<std::vector<int>> rev(nodes.size());
    for (std::size_t u = 0; u < fwd.size(); u++) {
      for (const int v : fwd[u]) rev[static_cast<std::size_t>(v)].push_back(static_cast<int>(u));
    }
    std::vector<char> back(nodes.size(), 0);
    std::vector<int> stack;
    for (std::size_t k = 0; k < nodes.size(); k++) {
      if (nodes[k].first == nodes[k].second) { back[k] = 1; stack.push_back(static_cast<int>(k)); }
    }
    while (!stack.empty()) {
      const int v = stack.back();
      stack.pop_back();
      for (const int u : rev[static_cast<std::size_t>(v)]) {
        if (!back[static_cast<std::size_t>(u)]) { back[static_cast<std::size_t>(u)] = 1; stack.push_back(u); }
      }
    }
    for (std::size_t k = 0; k < nodes.size(); k++) {
      if (back[k] && nodes[k].first != nodes[k].second) refuse("exponential ambiguity");
    }
  }
};


}  // namespace

// Validates and rewrites in one pass, returning source that means the same thing
// to every engine. Every host runs this, so every host compiles the same
// pattern -- and the SEL→SQL translator is a caller from another
// translation unit, which is why it is declared in sel_ast.hpp and defined at
// namespace scope rather than in the anonymous namespace above.
std::string validate_pattern(const std::string& pattern, Pos pos, bool ignore_case) {
  const CodePoints p = decode_utf8(pattern, pos);
  const std::size_t n = p.size();
  if (n > static_cast<std::size_t>(MAX_REGEX_PATTERN)) {
    bad_regex("pattern longer than " + std::to_string(MAX_REGEX_PATTERN) + " code points", pattern, 0,
              pos);
  }
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
      if (i + 1 < n && p[i + 1] == U'*') {
        bad_regex("PCRE verbs such as (*FAIL) are not portable", pattern, i, pos);
      }
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
  // Token by token the pattern is legal; now its shape (spec §7.8).
  {
    // The `i` fold is analysed only for an ASCII pattern (SPEC 7.8): a non-ASCII
    // pattern under `i` is refused at run time (E_BAD_ARG) whatever else it holds,
    // so the analysis must not turn it into a compile-time refusal of its own.
    bool fold = ignore_case;
    for (const char32_t cp : p) if (cp > 0x7f) { fold = false; break; }
    RxParser parser(p, pattern, pos, fold);
    const RxTree tree = parser.parse();
    check_loops(tree, pattern, pos);
    RxAnalysis(tree, pattern, pos).run();
  }
  return out;
}

namespace {

using Regex = srell::u32regex;

constexpr std::size_t REGEX_CACHE_MAX = 256;   // spec §7.8: bounded pattern cache

// The compiled-pattern cache. A named object rather than function statics so a
// unit test can look at how many entries it holds.
struct RegexCacheState {
  std::mutex mutex;
  // Shared, not owned by the map alone: a call holds the pattern it searches with
  // for as long as it searches, and another thread may evict that entry meanwhile
  // (ThreadSanitizer saw it: four threads cycling 300 patterns through 256 slots
  // destroyed a Regex in use).
  std::map<std::string, std::shared_ptr<const Regex>> map;
  std::deque<std::string> order;   // insertion order, oldest first
};

// The pattern this thread used last, found again without the lock, the key string
// or the map. A rule inside an aggregate asks for the same pattern once per
// element, and those lookups were a third of what a short match cost.
struct RegexLastUsed {
  std::string pattern;
  std::string flags;
  std::shared_ptr<const Regex> re;
};

RegexCacheState& regex_cache_state() {
  static RegexCacheState state;
  return state;
}

// The engine can refuse a search of its own accord (complexity, a program too
// large for it). That is a resource limit, and it must come out as a SEL error
// -- never an uncaught exception that ends the host process, and never a quiet
// FALSE. Everything that searches goes through here.
template <class F>
auto guarded_search(Pos pos, F&& f) -> decltype(f()) {
  try {
    return f();
  } catch (const srell::regex_error& e) {
    fail("E_REGEX_SYNTAX", std::string("the pattern exceeds the regex engine's limits: ") + e.what(), pos);
  }
}

// Compiled patterns are cached: a rule inside an aggregate compiles its pattern
// once per element otherwise.
std::shared_ptr<const Regex> compile_regex(const std::string& pattern, const std::string& flags, Pos flag_pos,
                                           Pos pat_pos) {
  thread_local RegexLastUsed last;
  if (last.re && last.pattern == pattern && last.flags == flags) {
    // Same pattern, same flags as a call that already passed every check below.
    return last.re;
  }
  bool ignore_case = false;
  for (char32_t ch : decode_utf8(flags, flag_pos)) {
    // `i` and nothing else: an uppercase `I` (or U+0130, U+0131, U+212A) is not
    // the flag, whatever a permissive engine would make of it (spec §7.8).
    const char32_t f = ch;
    if (f == U'i') { ignore_case = true; continue; }
    std::string s;
    encode_cp(s, ch);
    if (f == U'm' || f == U's' || f == U'M' || f == U'S') {
      fail("E_BAD_ARG",
           "flag " + quote_text(s) +
               " is not offered — SEL always matches . against any character and anchors ^ $ to "
               "the whole subject",
           flag_pos);
    }
    fail("E_BAD_ARG", "unknown regex flag " + quote_text(s), flag_pos);
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
  RegexCacheState& state = regex_cache_state();
  std::map<std::string, std::shared_ptr<const Regex>>& cache = state.map;
  // Bounded (spec §7.8): patterns come from data, and an unbounded cache is a
  // leak the caller cannot see. At most REGEX_CACHE_MAX, oldest evicted first.
  // What is handed out is a shared_ptr, so an entry evicted under a call that is
  // still searching with it lives until that call lets go.
  std::deque<std::string>& cache_order = state.order;
  std::lock_guard<std::mutex> lock(state.mutex);
  const std::string key = (ignore_case ? "i " : " ") + pattern;
  auto remember = [&](const std::shared_ptr<const Regex>& re) {
    last.pattern = pattern;
    last.flags = flags;
    last.re = re;
    return re;
  };
  auto it = cache.find(key);
  if (it != cache.end()) return remember(it->second);

  const std::string source = validate_pattern(pattern, pat_pos, ignore_case);
  auto opts = srell::regex_constants::ECMAScript | srell::regex_constants::dotall;
  if (ignore_case) opts |= srell::regex_constants::icase;
  try {
    // The source is UTF-8; SRELL's u32regex wants code points.
    const CodePoints cps = decode_utf8(source, pat_pos);
    Regex re(std::u32string(cps.begin(), cps.end()), opts);
    if (cache.size() >= REGEX_CACHE_MAX) {
      cache.erase(cache_order.front());
      cache_order.pop_front();
    }
    cache_order.push_back(key);
    return remember(cache.emplace(key, std::make_shared<const Regex>(std::move(re))).first->second);
  } catch (const srell::regex_error& e) {
    fail("E_REGEX_SYNTAX", std::string(e.what()) + " in /" + pattern + "/", pat_pos);
  }
}

struct RegexCall {
  std::shared_ptr<const Regex> re;
  std::u32string subject;
};

// The subject as SRELL wants it, code points in a u32string. Decoded once, straight
// into the string: going through a vector of code points and copying that into the
// string made two conversions, each the size of the text times four. An all-ASCII
// subject -- most of them -- widens byte for byte.
std::u32string u32_subject(const std::string& s, Pos pos) {
  bool ascii = true;
  for (const unsigned char c : s) {
    if (c >= 0x80) { ascii = false; break; }
  }
  if (ascii) {
    std::u32string out;
    out.resize(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) out[i] = static_cast<char32_t>(static_cast<unsigned char>(s[i]));
    return out;
  }
  const CodePoints cps = decode_utf8(s, pos);
  return std::u32string(cps.begin(), cps.end());
}

RegexCall regex_args(Args& a, int pat_index, int subj_index, int flag_index) {
  const std::string pattern = a.text(pat_index);
  std::u32string subject = u32_subject(a.text(subj_index), a.pos_of(subj_index));
  const std::string flags = a.count() > flag_index ? a.text(flag_index) : "";
  const Pos flag_pos = a.count() > flag_index ? a.pos_of(flag_index) : a.pos();
  return RegexCall{compile_regex(pattern, flags, flag_pos, a.pos_of(pat_index)), std::move(subject)};
}

// RREPLACE's replacement, taken apart once (spec §7.8): runs of literal code
// points and the group numbers $0-$9. "$$" is a dollar and any other "$" is
// literal. Spliced by hand rather than handed to the engine, whose own
// replacement syntax differs between hosts.
struct ReplacementPiece {
  std::size_t begin;   // a literal: the unescaped text's [begin, begin + len)
  std::size_t len;
  int group;           // a group, or -1 for a literal
};

// REPL's pieces; REPL is unescaped in place (each "$$" becomes one dollar and
// each "$n" is cut out), so the literal pieces index the text left in it and a
// dollar does not split a run.
std::vector<ReplacementPiece> split_replacement(CodePoints& repl) {
  std::vector<ReplacementPiece> pieces;
  pieces.reserve(4);
  const std::size_t n = repl.size();
  std::size_t w = 0;     // the unescaped text so far; never ahead of k
  std::size_t run = 0;   // where its literal run not yet taken starts
  for (std::size_t k = 0; k < n; ++k) {
    const char32_t c = repl[k];
    if (c == U'$' && k + 1 < n) {
      const char32_t next = repl[k + 1];
      if (next == U'$') {
        repl[w++] = U'$';
        ++k;
        continue;
      }
      if (next >= U'0' && next <= U'9') {
        if (w > run) pieces.push_back({run, w - run, -1});
        pieces.push_back({0, 0, static_cast<int>(next - U'0')});
        ++k;
        run = w;
        continue;
      }
    }
    repl[w++] = c;
  }
  if (w > run) pieces.push_back({run, w - run, -1});
  repl.resize(w);
  return pieces;
}

// One match's replacement onto OUT; a group that did not take part is "".
// Whether a group exists is only known when a match comes up, and a
// replacement that is never reached must not be refused, so that check is
// made here, per match.
void append_replacement(std::u32string& out, const CodePoints& repl, const std::vector<ReplacementPiece>& pieces,
                        const srell::u32smatch& m, Pos pos) {
  for (const ReplacementPiece& piece : pieces) {
    if (piece.group < 0) {
      out.append(repl.data() + piece.begin, piece.len);
      continue;
    }
    const std::size_t g = static_cast<std::size_t>(piece.group);
    if (g >= m.size()) {
      fail("E_BAD_ARG",
           "replacement refers to $" + std::to_string(g) + " but the pattern has " +
               std::to_string(m.size() - 1) + " groups",
           pos);
    }
    if (m[g].matched) out.append(m[g].first, m[g].second);
  }
}

void register_regex() {
  define(Spec{"RMATCH", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                return Value::boolean(guarded_search(a.pos(), [&] { return srell::regex_search(c.subject, *c.re); }));
              }});

  define(Spec{"RFIND", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                srell::u32smatch m;
                if (!guarded_search(a.pos(), [&] { return srell::regex_search(c.subject, m, *c.re); })) {
                  return make_int(0);
                }
                // Offsets are already code points, because the subject is u32.
                return make_int(static_cast<long long>(m.position(0)) + 1);
              }});

  define(Spec{"RGROUPS", 2, 3, false, false, nullptr, [](Args& a, Context&) -> Value {
                RegexCall c = regex_args(a, 0, 1, 2);
                srell::u32smatch m;
                if (!guarded_search(a.pos(), [&] { return srell::regex_search(c.subject, m, *c.re); })) {
                  return Value::none();
                }
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
                const std::u32string subject = u32_subject(a.text(2), a.pos_of(2));
                const std::string flags = a.count() > 3 ? a.text(3) : "";
                const Pos flag_pos = a.count() > 3 ? a.pos_of(3) : a.pos();
                const std::shared_ptr<const Regex> re_holder = compile_regex(pattern, flags, flag_pos, a.pos_of(0));
                const Regex& re = *re_holder;

                // The walk of spec §7.8: left to right; after an EMPTY match at s the
                // scan resumes at s+1 with the code point at s copied through, after
                // a non-empty one at its end, where an empty match is allowed. A
                // regex_iterator would retry a non-empty match at the position of
                // an empty one, which is the difference the other engines make.
                // Each search starts at `s` but treats everything before it as
                // context (match_prev_avail), so `^` still means the start of the
                // whole subject and not of the rest of it.
                // The replacement is taken apart at the first match, once: a subject
                // with no match pays nothing for it.
                CodePoints replacement;
                std::vector<ReplacementPiece> pieces;
                bool split = false;
                const Pos repl_pos = a.pos_of(1);
                std::u32string out;
                std::size_t last = 0;
                std::size_t s = 0;
                const std::size_t n = subject.size();
                srell::u32smatch m;
                while (s <= n) {
                  const auto match_flags = s > 0 ? srell::regex_constants::match_prev_avail
                                                 : srell::regex_constants::match_default;
                  if (!guarded_search(a.pos(), [&] {
                        return srell::regex_search(subject.cbegin() + static_cast<std::ptrdiff_t>(s),
                                                   subject.cend(), m, re, match_flags);
                      })) {
                    break;
                  }
                  const std::size_t at = s + static_cast<std::size_t>(m.position(0));
                  const std::size_t len = static_cast<std::size_t>(m.length(0));
                  out.append(subject, last, at - last);
                  if (!split) {
                    replacement = decode_utf8(repl, repl_pos);
                    pieces = split_replacement(replacement);
                    split = true;
                  }
                  append_replacement(out, replacement, pieces, m, repl_pos);
                  cap_text(out.size(), a.pos());   // spec §6.4: stop growing at the cap
                  if (len == 0) {
                    if (at >= n) { last = at; break; }
                    out.push_back(subject[at]);
                    last = at + 1;
                    s = at + 1;
                  } else {
                    last = at + len;
                    s = last;
                  }
                }
                out += subject.substr(std::min(last, n));
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
                if (!target.is_null() && target.has(key)) return *target.get(key);
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
                  cur = *cur.get(seg);
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
// every host spells this identically. It is capped at the same MAX_DEPTH the
// evaluator uses and trips at the same node, so a program whose dependencies
// cannot be computed is exactly a program that could not have been evaluated.
//
// What it computes is flow-sensitive (spec/SPEC.md §8): a variable is a
// dependency when some read of it can happen before the program has DEFINITELY
// assigned it, in evaluation order. `definite` is that set as the walk goes: an
// assignment adds its target once its right side has run, but only where the
// assignment is certain to run -- so a branch of IF or COND, the right side of
// AND, OR, `??` and `???`, and an aggregate's body each walk a COPY, and only
// what every branch of an IF/COND assigns survives the join.
void collect(const Node* node, std::set<std::string>& bound, std::set<std::string>& reads,
             std::set<std::string>& definite, int depth) {
  if (!node) return;
  if (depth > MAX_DEPTH) fail("E_DEPTH", "expression nested too deeply", node->pos);
  const auto read = [&](const std::string& name) {
    if (!bound.count(name) && !definite.count(name)) reads.insert(name);
  };
  const auto intersect = [](const std::set<std::string>& a, const std::set<std::string>& b) {
    std::set<std::string> out;
    for (const std::string& x : a) {
      if (b.count(x)) out.insert(x);
    }
    return out;
  };
  switch (node->t) {
    case NT::Var:
      read(node->s);
      return;

    case NT::Assign: {
      const Node* target = node->l.get();
      const Node* t = target;
      std::vector<const Node*> indexes;
      while (t->t == NT::Index) {
        indexes.push_back(t->r.get());
        t = t->l.get();
      }
      // The index expressions run once, left to right, before the right side.
      for (auto it = indexes.rbegin(); it != indexes.rend(); ++it) {
        collect(*it, bound, reads, definite, depth + 1);
      }
      // `A = x` and `A[k] = x` create A (and read only the indexes); `A += x`
      // and `A[k] += x` read the target's current value first.
      if (node->s != "=") read(t->s);
      collect(node->r.get(), bound, reads, definite, depth + 1);
      definite.insert(t->s);
      return;
    }

    case NT::Call: {
      if (node->s == "IF" && node->items.size() >= 2) {
        collect(node->items[0].get(), bound, reads, definite, depth + 1);
        std::set<std::string> then_set = definite;
        collect(node->items[1].get(), bound, reads, then_set, depth + 1);
        std::set<std::string> else_set = definite;
        if (node->items.size() > 2) collect(node->items[2].get(), bound, reads, else_set, depth + 1);
        definite = intersect(then_set, else_set);
        return;
      }
      if (node->s == "COND" && !node->items.empty()) {
        // Conditions run in order, each only when the earlier ones were false;
        // whatever ran so far is definite for what follows. Each value, and the
        // default, runs alone.
        std::set<std::string> joined;
        bool have = false;
        const auto join = [&](const std::set<std::string>& branch) {
          joined = have ? intersect(joined, branch) : branch;
          have = true;
        };
        const std::size_t n = node->items.size();
        std::size_t i = 0;
        for (; i + 1 < n; i += 2) {
          collect(node->items[i].get(), bound, reads, definite, depth + 1);
          std::set<std::string> branch = definite;
          collect(node->items[i + 1].get(), bound, reads, branch, depth + 1);
          join(branch);
        }
        if (i < n) {
          std::set<std::string> branch = definite;
          collect(node->items[i].get(), bound, reads, branch, depth + 1);
          join(branch);
        } else {
          join(definite);   // no default: falling through assigns nothing new
        }
        definite = joined;
        return;
      }
      // Which arguments run inside the binder, and what they see, is decided
      // once, by binding_form() over the manifest's forms (spec/builtins.md).
      // No form -- a strict function, or a count the evaluator would refuse --
      // and every argument is read where the call stands.
      const auto form = binding_form(node->s, node->items, node->spec);
      if (!form) {
        for (std::size_t i = 0; i < node->items.size(); ++i) {
          // The first argument always runs; COALESCE's later ones and GET/PATH's
          // default may not, so nothing they assign is definite afterwards.
          const bool optional = (node->s == "COALESCE" && i > 0) ||
                                ((node->s == "GET" || node->s == "PATH") && i > 1);
          if (optional) {
            auto copy = definite;
            collect(node->items[i].get(), bound, reads, copy, depth + 1);
          } else {
            collect(node->items[i].get(), bound, reads, definite, depth + 1);
          }
        }
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
          // The body may run any number of times, or never: it assigns nothing
          // definite, but reads what has been assigned by now.
          std::set<std::string> body = definite;
          collect(node->items[i].get(), *inner, reads, body, depth + 1);
        } else {
          collect(node->items[i].get(), bound, reads, definite, depth + 1);
        }
      }
      return;
    }

    case NT::Seq:
    case NT::List:
      for (const auto& item : node->items) collect(item.get(), bound, reads, definite, depth + 1);
      return;

    case NT::Index:
      collect(node->l.get(), bound, reads, definite, depth + 1);
      collect(node->r.get(), bound, reads, definite, depth + 1);
      return;

    case NT::Bin: {
      collect(node->l.get(), bound, reads, definite, depth + 1);
      // The right side of AND, OR, `??` and `???` may never run.
      const sel_lexicon::Op* op = infix_op(node->s);
      const bool short_circuit = op != nullptr && op->short_circuit;
      if (short_circuit) {
        std::set<std::string> rhs = definite;
        collect(node->r.get(), bound, reads, rhs, depth + 1);
      } else {
        collect(node->r.get(), bound, reads, definite, depth + 1);
      }
      return;
    }

    case NT::Un:
      collect(node->l.get(), bound, reads, definite, depth + 1);
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

// The pipeline vocabulary (sel_ast.hpp), shared with the SQL planner: the
// manifest's pipeline steps (spec/builtins.json).
bool is_pipeline_op(std::string_view name) { return pipeline_step(name) != nullptr; }

namespace {

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
  auto copy = copy_node(child);
  copy->pos = pos;
  return copy;
}

}  // namespace

std::pair<NodePtr, std::vector<NodePtr>> unwind_pipeline(const NodePtr& root) {
  std::vector<NodePtr> steps;
  NodePtr current = root;
  while (current && current->t == NT::Call && is_pipeline_op(current->s) &&
         !current->items.empty()) {
    steps.push_back(current);
    current = current->items.front();
  }
  std::reverse(steps.begin(), steps.end());
  return {current, steps};
}

NodePtr build_pipeline(NodePtr source, const std::vector<NodePtr>& steps, const Pos* last_pos) {
  NodePtr current = std::move(source);
  for (std::size_t k = 0; k < steps.size(); k++) {
    const NodePtr& step = steps[k];
    auto next = copy_node(step);
    next->items.clear();
    next->items.push_back(current);
    next->items.insert(next->items.end(), step->items.begin() + 1, step->items.end());
    if (last_pos && k + 1 == steps.size()) next->pos = *last_pos;
    current = std::move(next);
  }
  return current;
}

namespace {

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
    // The evaluator's own dispatch (dec_arith, cmp_holds), so a fold cannot
    // answer differently from the operator it replaces.
    const unsigned char opc = node->opc ? node->opc : bin_opcode(node->s);
    if (left->t == NT::Num && right->t == NT::Num && opc_in(opc, sel_lexicon::Family::Arith)) {
      try {
        Dec a, b;
        if (dec_parse(left->s, a, node->pos) && dec_parse(right->s, b, node->pos)) {
          return opt_num(dec_format(dec_arith(opc, a, b, node->pos)), node->pos);
        }
      } catch (const SelError&) {
      }
    }
    if (left->t == NT::Num && right->t == NT::Num && opc_in(opc, sel_lexicon::Family::Compare)) {
      try {
        Dec a, b;
        if (dec_parse(left->s, a, node->pos) && dec_parse(right->s, b, node->pos)) {
          return opt_bool(cmp_holds(opc - BO_NUM_EQ, dec_cmp(a, b)), node->pos);
        }
      } catch (const SelError&) {
      }
    }
    if (left->t == NT::Text && right->t == NT::Text && opc_in(opc, sel_lexicon::Family::TextCompare)) {
      return opt_bool(cmp_holds(opc - BO_TXT_EQ, bytes_compare(left->s, right->s)), node->pos);
    }
    return node;
  }
  // items are the arguments themselves: the condition is items[0]. This arm
  // once tested for four items and never fired, which is how C++ came to
  // report the IF's position while the hosts that folded reported the branch
  // literal's.
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
      const std::string var = ascii_upper(item.l->s);
      if (var == ascii_upper(binder) || var == "_" || var == "_1" || var == "_2") {
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
  for (const std::string& name : names) wanted.insert(ascii_upper(name));
  bool found = false;
  const auto walk = [&](const auto& self, const Node& item) -> void {
    if (found) return;
    if (item.t == NT::Var && wanted.count(ascii_upper(item.s))) { found = true; return; }
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
  // The slot a form is told apart by (a manifest `when` that asks for a text
  // literal) is not folded while another form holds: a fold could turn the
  // call into that form.
  const auto form = sort_form(step.s, step.items);
  if (!form) return true;
  for (int i = 0; i < sel_builtin_manifest::FORM_COUNT; i++) {
    const auto& f = sel_builtin_manifest::FORMS[i];
    if (step.s == f.name && static_cast<std::size_t>(f.count) == step.items.size() &&
        f.when_kind == 2 && static_cast<std::size_t>(f.when_arg) == index && form->dir != f.when_arg) {
      return false;
    }
  }
  return true;
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

// MAP's reading of the arguments, plus whether it is a FILTER form at all.
OptFilterInfo opt_filter_info(const Node& step) {
  OptMapInfo m = opt_map_info(step);
  const bool valid = step.items.size() == 2 || m.explicit_binder;
  return {std::move(m.binder), std::move(m.body), m.explicit_binder, valid};
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
        ascii_upper(value->l->s) == ascii_upper(info.binder) && value->r->s == key->s) {
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
  OptSortInfo info;
  const auto form = sort_form(step.s, step.items);
  if (!form || form->key < 0) return info;
  if (form->binder >= 0) {
    // A binder slot that is not a bare name raises E_EXPECT_SYMBOL when run; no
    // name is bound then, so nothing in the key counts as a read of the row.
    const Node& b = *step.items[static_cast<std::size_t>(form->binder)];
    info.binder = b.t == NT::Var && !b.grouped ? b.s : "";
  }
  info.key = step.items[static_cast<std::size_t>(form->key)];
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
  auto copy = copy_node(node);
  if (copy->t == NT::Var && ascii_upper(copy->s) == ascii_upper(old_name)) copy->s = new_name;
  if (copy->l) copy->l = opt_rename_var(copy->l, old_name, new_name);
  if (copy->r) copy->r = opt_rename_var(copy->r, old_name, new_name);
  for (NodePtr& item : copy->items) item = opt_rename_var(item, old_name, new_name);
  return copy;
}

// Whether evaluating NODE for one row can raise -- conservatively: a rewrite
// that moves a FILTER in front of a step, runs a step on fewer rows, or fuses
// two FILTERs changes which rows reach what, so it may only pass over
// expressions that cannot raise on any of them (spec §7.3). Literals, _K and
// the binder itself never raise. On the
// logical path the rows are a bound relation's, which always carry their typed
// columns, so a field read through the binder cannot raise either, nor a
// comparison, AND/OR/NOT or + - * over such reads; `/` and `%`, calls and
// anything else may. The in-memory path has no schema.
// What the SQL planner tells the LOGICAL optimizer about the rows: the field
// names its source relation declares, and whether the row shape is still the
// source's at the step being considered. A field read `_["f"]` cannot raise only
// for a declared field of an unchanged row; for anything else it can raise
// E_NO_KEY, and a rewrite that stops it being evaluated for some rows (a FILTER
// hoisted above a SORT_BY whose key it is) hides the error `run()` reports
// Unset -- the physical optimizer, or a caller with no relation --
// keeps the historical assumption (a read of the binder is taken as safe).
// Passed down explicitly, as a null pointer when there is nothing to say.
struct OptFields {
  const std::set<std::string>* declared = nullptr;   // upper-cased field names
  bool shape_known = false;   // the row is still the source's at this step
};

// Whether a comparison of either family: `==` ... `>=` and `$==` ... `$>=`.
bool opt_is_comparison(const std::string& op) {
  const sel_lexicon::Op* info = infix_op(op);
  return info != nullptr &&
         (info->family == sel_lexicon::Family::Compare || info->family == sel_lexicon::Family::TextCompare);
}

// The binary operators opt_cannot_raise lets through. A policy, not a family:
// the comparisons, the short-circuit logic operators AND and OR, and the
// arithmetic operators that cannot divide (`+`, `-`, `*`; `/` and `%` raise
// E_DIV_ZERO).
bool opt_safe_binary(const std::string& op) {
  if (opt_is_comparison(op)) return true;
  const sel_lexicon::Op* info = infix_op(op);
  if (info == nullptr) return false;
  if (info->family == sel_lexicon::Family::Logic) return info->short_circuit;
  return info->family == sel_lexicon::Family::Arith && op != "/" && op != "%";
}

bool opt_cannot_raise(const NodePtr& node, const std::string& binder, bool logical,
                      const OptFields* fields) {
  if (!node) return true;
  switch (node->t) {
    case NT::Num: case NT::Text: case NT::Bool: case NT::Null: return true;
    case NT::Var: {
      const std::string name = ascii_upper(node->s);
      return name == "_K" || name == ascii_upper(binder);
    }
    case NT::Index:
      if (!(logical && node->l && node->l->t == NT::Var && ascii_upper(node->l->s) == ascii_upper(binder) &&
            node->r && node->r->t == NT::Text)) {
        return false;
      }
      // With a relation's fields known (the SQL planner), a read is safe only for
      // a declared field of a row whose shape is still the source's. Without them
      // (a caller that only asked for the logical rewrite) the historical
      // assumption stands.
      return !fields || !fields->declared ||
             (fields->shape_known && fields->declared->count(ascii_upper(node->r->s)) > 0);
    case NT::Bin:
      return logical && opt_safe_binary(node->s) && opt_cannot_raise(node->l, binder, logical, fields) &&
             opt_cannot_raise(node->r, binder, logical, fields);
    case NT::Un:
      return logical && node->s == "NOT" && opt_cannot_raise(node->l, binder, logical, fields);
    default:
      return false;
  }
}

// Whether a FILTER predicate cannot raise: opt_cannot_raise says the pieces
// cannot, but a predicate must also BE a boolean, or the filter raises
// E_NOT_BOOL on its first row -- `_`, `_K`, a number, text or NULL all pass
// opt_cannot_raise and all raise there. Only a boolean literal, a comparison
// (logical path) or AND/OR/NOT over such, is safe to move or to fuse.
bool opt_predicate_cannot_raise(const NodePtr& node, const std::string& binder, bool logical,
                                const OptFields* fields) {
  if (!node) return false;
  switch (node->t) {
    case NT::Bool: return true;
    case NT::Bin:
      if (node->s == "AND" || node->s == "OR") {
        return opt_predicate_cannot_raise(node->l, binder, logical, fields) &&
               opt_predicate_cannot_raise(node->r, binder, logical, fields);
      }
      return logical && opt_is_comparison(node->s) && opt_cannot_raise(node->l, binder, logical, fields) &&
             opt_cannot_raise(node->r, binder, logical, fields);
    case NT::Un:
      return node->s == "NOT" && opt_predicate_cannot_raise(node->l, binder, logical, fields);
    default:
      return false;
  }
}

// Every field a MAP computes (or its whole body) cannot raise.
bool opt_map_cannot_raise(const Node& step, bool logical, const OptFields* fields) {
  const OptMapInfo info = opt_map_info(step);
  if (info.body && info.body->t == NT::Call && info.body->s == "RECORD") {
    for (std::size_t i = 0; i < info.body->items.size(); i++) {
      const NodePtr& arg = info.body->items[i];
      if (i % 2 == 0 ? arg->t != NT::Text : !opt_cannot_raise(arg, info.binder, logical, fields)) return false;
    }
    return true;
  }
  return opt_cannot_raise(info.body, info.binder, logical, fields);
}

// How deep an expression goes, its root counted as 1, and never more than `cap`
// + 1 (the walk stops there), so it is bounded whatever the source's length.
int opt_bounded_depth(const NodePtr& root, int cap) {
  int deepest = 0;
  std::vector<const Node*> level{root.get()};
  while (!level.empty() && deepest <= cap) {
    ++deepest;
    std::vector<const Node*> next;
    for (const Node* n : level) {
      if (!n) continue;
      if (n->l) next.push_back(n->l.get());
      if (n->r) next.push_back(n->r.get());
      for (const NodePtr& c : n->items) next.push_back(c.get());
    }
    level = std::move(next);
  }
  return deepest;
}

// Where each step of a pipeline stands in the tree as written (the outermost
// step is the pipeline node's own depth), for the one rule that deepens a
// subtree: FILTER fusion. Keyed by the step node; a fused step keeps the depth
// of the step it was copied from.
using OptStepDepths = std::unordered_map<const Node*, int>;

std::vector<NodePtr> opt_logical_steps(const NodePtr& source, std::vector<NodePtr> current, bool logical,
                                       const std::set<std::string>* declared,
                                       OptStepDepths* step_depths = nullptr) {
  bool changed = true;
  while (changed) {
    changed = false;
    std::vector<NodePtr> next;
    for (std::size_t i = 0; i < current.size();) {
      const NodePtr& first = current[i];
      const NodePtr* second = i + 1 < current.size() ? &current[i + 1] : nullptr;
      const NodePtr* third = i + 2 < current.size() ? &current[i + 2] : nullptr;
      // The row is the source's while every step before this one keeps its rows
      // as they are (the manifest's keepsRows).
      OptFields fields{declared, true};
      for (std::size_t k = 0; k < i; ++k) {
        const sel_builtin_manifest::PipelineStep* step = pipeline_step(current[k]->s);
        if (step == nullptr || !step->keeps_rows) {
          fields.shape_known = false;
          break;
        }
      }
      if (second && (*second)->s == "TAKE" && first->s == "TAKE" && first->items.size() == 2 &&
          (*second)->items.size() == 2) {
        const auto left = opt_numeric_literal(first->items[1]);
        const auto right = opt_numeric_literal((*second)->items[1]);
        if (left && right) {
          auto merged = copy_node(first);
          merged->pos = (*second)->pos;   // the merged step is the result of the later one
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
          auto merged = copy_node(first);
          merged->pos = (*second)->pos;
          merged->items = {first->items[0], opt_num(std::to_string(*left + *right), (*second)->items[1]->pos)};
          next.push_back(std::move(merged));
          i += 2;
          changed = true;
          continue;
        }
      }
      // Fused only for a numeric literal count of at least 1 (spec §6.2): the
      // fused step evaluates its count before the key, so a count that can
      // raise -- or a TAKE(0), which still evaluates the keys unfused -- would
      // change which error is reported.
      if (second && (*second)->s == "TAKE" && (*second)->items.size() == 2 &&
          (first->s == "SORT" || first->s == "SORT_DESC" || first->s == "SORT_BY") &&
          opt_numeric_literal((*second)->items[1]).value_or(0) >= 1) {
        const std::string top_name = first->s == "SORT" ? "TOP" : first->s == "SORT_DESC" ? "TOP_DESC" : "TOP_BY";
        auto fused = copy_node(first);
        fused->pos = (*second)->pos;
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
            opt_keys_renumbered_by(third) && opt_map_cannot_raise(*first, logical, &fields)) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && (first->s == "SORT" || first->s == "SORT_DESC" || first->s == "SORT_BY") &&
          (*second)->s == "FILTER" && !opt_step_reads_key(**second) &&
          opt_keys_renumbered_by(third) &&
          opt_cannot_raise(opt_sort_info(*first).key, opt_sort_info(*first).binder, logical, &fields) &&
          (logical || opt_predicate_cannot_raise(opt_filter_info(**second).predicate, opt_filter_info(**second).binder, false, nullptr))) {
        next.push_back(*second);
        next.push_back(first);
        i += 2;
        changed = true;
        continue;
      }
      if (second && first->s == "SELECT_COLS" && (*second)->s == "FILTER") {
        const OptFilterInfo info = opt_filter_info(**second);
        const auto refs = info.predicate ? opt_field_refs(*info.predicate, info.binder) : std::vector<std::string>{};
        const auto selected = opt_select_fields(*first);
        if (info.valid && !refs.empty() && std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {
              return std::find(selected.begin(), selected.end(), f) != selected.end();
            }) && !opt_reads_row_or_key(*info.predicate, info.binder) &&
            opt_keys_renumbered_by(third)) {
          next.push_back(*second);
          next.push_back(first);
          i += 2;
          changed = true;
          continue;
        }
      }
      if (second && first->s == "MAP" && pipeline_step((*second)->s) != nullptr &&
          pipeline_step((*second)->s)->sorts && opt_map_has_computed(*first)) {
        // Only a key over pass-through fields is the same value before the
        // MAP: a keyless sort compares the MAP's outputs, and a key that
        // reads the whole row or `_K` reads what the MAP changes.
        const OptSortInfo sort = opt_sort_info(**second);
        const auto refs = sort.key ? opt_field_refs(*sort.key, sort.binder) : std::vector<std::string>{};
        const auto passes = opt_map_passthroughs(*first);
        if (sort.key && !refs.empty() && std::all_of(refs.begin(), refs.end(), [&](const std::string& f) {
              return std::find(passes.begin(), passes.end(), f) != passes.end();
            }) && !opt_reads_row_or_key(*sort.key, sort.binder) && opt_map_cannot_raise(*first, logical, &fields) &&
            opt_cannot_raise(sort.key, sort.binder, logical, &fields)) {
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
        // Fused, the second predicate runs on a row before the first has seen
        // the rows after it: only one that cannot raise may be fused. Judged
        // without the declared fields (the historical assumption for a read).
        bool can_fuse = left.valid && right.valid &&
                        opt_predicate_cannot_raise(right.predicate, right.binder, logical, nullptr);
        // Fused, the second predicate sits one level deeper than it did: under
        // the AND that joins them. A fused pair must spend what the two stages
        // spent (spec §6.4), so a predicate that would reach the cap that way
        // stays a second FILTER (plan.pure-sql.fusion-stops-at-the-depth-cap).
        if (can_fuse && step_depths) {
          const auto at = step_depths->find(second->get());
          if (at != step_depths->end() &&
              at->second + opt_bounded_depth(right.predicate, MAX_DEPTH) + 1 > MAX_DEPTH) {
            can_fuse = false;
          }
        }
        if (can_fuse) {
          const NodePtr right_pred = ascii_upper(left.binder) == ascii_upper(right.binder)
              ? right.predicate : opt_rename_var(right.predicate, right.binder, left.binder);
          const NodePtr predicate = opt_combine_and({left.predicate, right_pred}, left.predicate->pos);
          auto merged = copy_node(first);
          merged->pos = (*second)->pos;
          merged->items = left.explicit_binder
              ? std::vector<NodePtr>{first->items[0], first->items[1], predicate}
              : std::vector<NodePtr>{first->items[0], predicate};
          if (step_depths) {
            const auto at = step_depths->find(first.get());
            if (at != step_depths->end()) (*step_depths)[merged.get()] = at->second;
          }
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

std::vector<NodePtr> opt_inmemory_steps(const NodePtr& source, std::vector<NodePtr> steps) {
  steps = opt_logical_steps(source, std::move(steps), false, nullptr);
  // A tree fact the evaluator's join pre-filter needs (SEL-0050): whether
  // anything can see the keys a FILTER's result carries. A following step
  // that renumbers without reading `_K` hides them (opt_keys_renumbered_by,
  // the same notion the logical rewrites use). Stamped on the body of the
  // physical copy, never on the caller's AST.
  std::vector<NodePtr> rewritten;
  rewritten.reserve(steps.size());
  for (std::size_t i = 0; i < steps.size(); ++i) {
    auto copy = copy_node(steps[i]);
    if (copy->s == "FILTER" && !copy->items.empty()) {
      auto body = copy_node(copy->items.back());
      body->keys_unobserved = opt_keys_renumbered_by(i + 1 < steps.size() ? &steps[i + 1] : nullptr);
      // The next step is where what this FILTER keeps is copied: a MAP copies what
      // it collects, a FILTER, a sort or a TOP keeps (and copies) its elements.
      // When neither this body nor anything the next step evaluates can write,
      // nothing kept can change on the way there, and keeping the element itself
      // is indistinguishable from keeping a copy of it.
      if (i + 1 < steps.size()) {
        const Node& after = *steps[i + 1];
        const bool copies_its_elements =
            after.s == "MAP" || after.s == "FILTER" || after.s == "SORT" || after.s == "SORT_DESC" ||
            after.s == "SORT_BY" || after.s == "TOP" || after.s == "TOP_DESC" || after.s == "TOP_BY";
        bool after_writes = false;
        for (std::size_t k = 1; k < after.items.size(); ++k) {
          after_writes = after_writes || (after.items[k] && !writes_nothing(*after.items[k]));
        }
        body->borrow_rows = after.t == NT::Call && !may_have_effects(after.spec) && copies_its_elements &&
                            writes_nothing(*body) && !after_writes;
      }
      copy->items.back() = std::move(body);
    }
    rewritten.push_back(std::move(copy));
  }
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
  uint32_t slot = 0;
  bool is_const = false;
  Dec const_val;
  // The slot still holds what a load produced, not a number; coerced by the
  // first step that reads it, at raw_pos (its own node).
  bool raw = false;
  Pos raw_pos;
};

// More slots than this and the tree evaluator runs the expression instead.
constexpr uint32_t MAX_PLAN_SLOTS = 1u << 22;

std::shared_ptr<const MathPlan> opt_compile_math_plan(const NodePtr& root) {
  if (!root || !is_math_op(*root)) return nullptr;

  auto plan = std::make_shared<MathPlan>();
  uint32_t slot_count = 0;
  const auto alloc_slot = [&slot_count]() -> uint32_t { return slot_count++; };
  // Operand bookkeeping for a step: which operands are still raw loads.
  const auto set_src1 = [](MathStep& st, const EmitResult& r) {
    st.src1 = r.slot; st.raw1 = r.raw; st.src1_pos = r.raw_pos;
  };
  const auto set_src2 = [](MathStep& st, const EmitResult& r) {
    st.src2 = r.slot; st.raw2 = r.raw; st.src2_pos = r.raw_pos;
  };
  // One computing step: a fresh slot, its operands (one or two), its position,
  // pushed onto the plan; the result is that slot, a number.
  const auto push_step = [&](MathOp op, const EmitResult& a, const EmitResult* b, Pos pos,
                             std::optional<Pos> aux = std::nullopt) -> EmitResult {
    const uint32_t dst = alloc_slot();
    MathStep step;
    step.op = op;
    step.dst = dst;
    set_src1(step, a);
    if (b) set_src2(step, *b);
    step.pos = pos;
    if (aux) step.aux_pos = *aux;
    plan->steps.push_back(std::move(step));
    return EmitResult{dst, false, {}, false, {}};
  };

  const auto emit = [&](auto& self, const NodePtr& node, int depth) -> std::optional<EmitResult> {
    if (!node || depth > MAX_DEPTH || slot_count > MAX_PLAN_SLOTS) return std::nullopt;

    if (node->t == NT::Var) {
      const uint32_t slot = alloc_slot();
      MathStep step;
      step.op = MathOp::LoadVar;
      step.dst = slot;
      step.name = node->s;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{slot, false, {}, true, node->pos};
    }

    if (node->t == NT::Num) {
      Dec dec;
      if (node->dec) {
        dec = *node->dec;
      } else if (!dec_parse(node->s, dec, node->pos)) {
        return std::nullopt;
      }
      const uint32_t slot = alloc_slot();
      MathStep step;
      step.op = MathOp::LoadConst;
      step.dst = slot;
      step.const_val = dec;
      step.pos = node->pos;
      plan->steps.push_back(std::move(step));
      return EmitResult{slot, true, dec, false, {}};
    }

    if (node->t == NT::Bin && is_math_op(*node)) {
      if (!node->l || !node->r) return std::nullopt;
      const auto res_l = self(self, node->l, depth + 1);
      if (!res_l) return std::nullopt;
      const auto res_r = self(self, node->r, depth + 1);
      if (!res_r) return std::nullopt;

      const std::string& op = node->s;

      // A copy-propagated operand that is still a raw load would never be
      // looked at by any step; it is coerced here, at the point the plain
      // tree coerces it, so a later operand's error cannot get in front.
      const auto propagate = [&](const EmitResult& kept) -> EmitResult {
        if (!kept.raw) return kept;
        return push_step(MathOp::Coerce, kept, nullptr, node->pos);
      };

      // Copy propagation: x + 0, 0 + x, x - 0, x * 1, 1 * x (an integer 0 or 1,
      // scale 0, so the result's scale is the other operand's).
      const auto is_zero = [](const auto& r) {
        return r.is_const && dec_is_zero(r.const_val) && r.const_val.scale == 0;
      };
      const auto is_one = [](const auto& r) {
        return r.is_const && !r.const_val.neg && dec_get_digits(r.const_val) == "1" && r.const_val.scale == 0;
      };
      if (((op == "+" || op == "-") && is_zero(*res_r)) || (op == "*" && is_one(*res_r))) {
        // The constant's own load step, when it was just emitted, is dropped.
        if (node->r->t == NT::Num && !plan->steps.empty() && plan->steps.back().dst == res_r->slot) {
          plan->steps.pop_back();
        }
        return propagate(*res_l);
      }
      if ((op == "+" && is_zero(*res_l)) || (op == "*" && is_one(*res_l))) {
        return propagate(*res_r);
      }

      return push_step(math_op_native(math_op_for(*node)->name), *res_l, &*res_r, node->pos);
    }

    if (node->t == NT::Un && is_math_op(*node)) {
      if (!node->l) return std::nullopt;
      const auto res_x = self(self, node->l, depth + 1);
      if (!res_x) return std::nullopt;
      return push_step(math_op_native(math_op_for(*node)->name), *res_x, nullptr, node->pos);
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
        return push_step(op_code, *res_arg, nullptr, node->pos);
      }
      if (entry->arity == 2) {
        if (args.size() != 2) return std::nullopt;
        const auto res0 = self(self, args[0], depth + 1);
        if (!res0) return std::nullopt;
        const auto res1 = self(self, args[1], depth + 1);
        if (!res1) return std::nullopt;
        const std::optional<Pos> aux =
            entry->aux >= 0 ? std::optional<Pos>(args[static_cast<std::size_t>(entry->aux)]->pos) : std::nullopt;
        return push_step(op_code, *res0, &*res1, node->pos, aux);
      }
      // fold: one or more operands, combined pairwise left to right. Every
      // argument is evaluated before the first is coerced (a strict function
      // evaluates its arguments, then checks them), so all the loads come first.
      if (args.empty()) return std::nullopt;
      std::vector<EmitResult> operands;
      operands.reserve(args.size());
      for (const NodePtr& arg : args) {
        const auto res = self(self, arg, depth + 1);
        if (!res) return std::nullopt;
        operands.push_back(*res);
      }
      EmitResult curr = operands[0];
      if (operands.size() == 1) {
        // MAX(x) is x, coerced.
        if (!curr.raw) return curr;
        return push_step(MathOp::Coerce, curr, nullptr, node->pos);
      }
      for (std::size_t k = 1; k < operands.size(); k++) {
        curr = push_step(op_code, curr, &operands[k], node->pos);
      }
      return curr;
    }

    if (node->t == NT::Bin || node->t == NT::Un) return std::nullopt;
    if (node->t == NT::Assign || node->t == NT::Seq || node->t == NT::List) return std::nullopt;
    if (node->t == NT::Call && (node->s == "IF" || node->s == "COND")) return std::nullopt;

    const uint32_t slot = alloc_slot();
    MathStep step;
    step.op = MathOp::LoadLeaf;
    step.dst = slot;
    step.leaf_node = node;
    step.pos = node->pos;
    plan->steps.push_back(std::move(step));
    return EmitResult{slot, false, {}, true, node->pos};
  };

  const auto res = emit(emit, root, 1);
  if (!res || plan->steps.empty()) return nullptr;
  plan->output_slot = res->slot;
  plan->scratchpad_size = slot_count;
  return plan;
}

// `fold` is the other hosts' foldConstants option: off for the one slot
// whose shape the evaluator reads (opt_step_arg_folds).
// `declared` is the SQL planner's field list (OptFields), null elsewhere.
NodePtr opt_tree(const NodePtr& node, bool physical, const std::set<std::string>* declared, int depth,
                 bool fold = true, bool in_math = false) {
  if (!node) return node;
  // The evaluator/SQL normaliser owns the public depth error and its source
  // position. opt_root never descends into a tree that reaches the cap; this
  // guard keeps the walk bounded should a rewrite ever deepen one.
  if (depth > MAX_DEPTH) return node;
  if (node->t == NT::Call && is_pipeline_op(node->s) && !node->items.empty()) {
    auto [source, steps] = unwind_pipeline(node);
    NodePtr optimized_source = opt_tree(source, physical, declared, depth + 1, fold, false);
    std::vector<NodePtr> optimized_steps;
    optimized_steps.reserve(steps.size());
    OptStepDepths step_depths;
    for (std::size_t index = 0; index < steps.size(); ++index) {
      const NodePtr& step = steps[index];
      auto copy = copy_node(step);
      step_depths[copy.get()] = depth + static_cast<int>(steps.size() - 1 - index);
      copy->items.clear();
      copy->items.push_back(step->items[0]);
      for (std::size_t i = 1; i < step->items.size(); i++) {
        copy->items.push_back(opt_tree(step->items[i], physical, declared, depth + 1,
                                       fold && opt_step_arg_folds(*step, i), false));
      }
      optimized_steps.push_back(std::move(copy));
    }
    std::vector<NodePtr> final_steps =
        opt_logical_steps(optimized_source, std::move(optimized_steps), !physical, declared, &step_depths);
    if (physical) final_steps = opt_inmemory_steps(optimized_source, std::move(final_steps));
    // Whatever the rewrites did, the pipeline's value is still the value of the
    // node that was written outermost, and a consumer that objects to it (NOT,
    // an index, an assignment) reports that node's position (spec §6.3). A
    // fused, swapped or dropped step must not move it.
    if (final_steps.empty()) {
      auto carrier = std::make_shared<Node>();
      carrier->t = NT::Seq;
      carrier->pos = node->pos;
      carrier->items.push_back(std::move(optimized_source));
      return carrier;
    }
    return build_pipeline(std::move(optimized_source), final_steps, &node->pos);
  }

  const bool is_curr_math = is_math_op(*node);
  const bool next_in_math = is_curr_math;

  auto copy = copy_node(node);
  // An assignment's target is walked iteratively by the evaluator (spec 6.4:
  // a chain of index brackets, not a nesting) and is never charged or folded
  // there, so it is left as written here too, as the other hosts leave it;
  // only the value is optimised.
  if (copy->l && copy->t != NT::Assign) copy->l = opt_tree(copy->l, physical, declared, depth + 1, fold, next_in_math);
  if (copy->r) copy->r = opt_tree(copy->r, physical, declared, depth + 1, fold, next_in_math);
  for (NodePtr& child : copy->items) child = opt_tree(child, physical, declared, depth + 1, fold, next_in_math);
  NodePtr folded = fold ? opt_fold(copy) : copy;
  if (physical && !in_math && is_math_op(*folded)) {
    auto plan = opt_compile_math_plan(folded);
    if (plan) {
      auto copy_with_plan = copy_node(folded);
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
NodePtr opt_root(const NodePtr& ast, bool physical, const std::set<std::string>* declared = nullptr) {
  if (ast && opt_exceeds_depth(*ast, 1)) return ast;
  return opt_tree(ast, physical, declared, 1);
}

}  // namespace

// The optimiser's entry points (sel_ast.hpp): the logical rewrite the SQL
// planner asks for, with or without its relation's declared fields, and the
// in-memory rewrite Program::physical_ast() builds the tree run() evaluates.
NodePtr optimize_ast_logical(const NodePtr& ast) { return opt_root(ast, false); }
NodePtr optimize_ast_logical(const NodePtr& ast, const std::set<std::string>& declared_fields) {
  return opt_root(ast, false, &declared_fields);
}
NodePtr optimize_ast_in_memory(const NodePtr& ast) { return opt_root(ast, true); }

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
  // No assignment and no application's function anywhere in `tree`
  // (Context::write_free), decided with it: such a program makes none of the
  // copies spec §3.4 names.
  bool write_free = false;
};

Program::Program(std::string source, std::shared_ptr<const Node> ast)
    : source_(std::move(source)), ast_(std::move(ast)),
      physical_(std::make_shared<Physical>()) {}

std::shared_ptr<const Node> Program::physical_ast() const {
  std::call_once(physical_->once, [this] {
    physical_->tree = optimize_ast_in_memory(ast_);
    physical_->write_free = physical_->tree && writes_nothing(*physical_->tree);
  });
  return physical_->tree;
}

Value Program::run(Value& context) const {
  const std::shared_ptr<const Node> tree = physical_ast();
  Context ctx(context);
  ctx.write_free = physical_->write_free;
  return eval_node(*tree, ctx);
}

Value Program::run() const {
  Value ctx = Value::none();
  return run(ctx);
}

std::vector<std::string> Program::dependencies() const {
  std::set<std::string> bound, reads, definite;
  collect(ast_.get(), bound, reads, definite, 1);
  return std::vector<std::string>(reads.begin(), reads.end());   // a std::set: already sorted
}

Program compile(const std::string& source) { return Program(source, parse(source)); }

namespace {
// Whether a tree has a call that iterates -- an aggregate or a join, which are the
// calls that bind an element. Without one every node runs once, so the optimiser
// (fusion, math plans, join pushdown) can save nothing the walk it costs does not
// spend: it is about as dear as parsing the rule. Iterative, so the depth of the
// tree does not matter; stops at the first such call.
bool has_iterating_call(const Node& root) {
  std::vector<const Node*> pending{&root};
  while (!pending.empty()) {
    const Node* n = pending.back();
    pending.pop_back();
    if (n->t == NT::Call && n->spec && n->spec->binds) return true;
    if (n->l) pending.push_back(n->l.get());
    if (n->r) pending.push_back(n->r.get());
    for (const auto& item : n->items) {
      if (item) pending.push_back(item.get());
    }
  }
  return false;
}
}  // namespace

// One shot: the program runs once and is dropped. A rule with no loop in it runs
// the tree as parsed -- the evaluator is the authority on what a program means
// and the optimiser is held to its answer -- instead of building a physical tree
// that its single run cannot repay. One that loops keeps the optimiser: its data
// may be as big as you like.
Value evaluate(const std::string& source, Value& context) {
  const Program program = compile(source);
  const auto ast = program.ast();
  if (!has_iterating_call(*ast)) {
    Context ctx(context);
    return eval_node(*ast, ctx);
  }
  return program.run(context);
}

Value evaluate(const std::string& source) {
  Value ctx = Value::none();
  return evaluate(source, ctx);
}

std::vector<std::string> function_names() {
  ensure_registered();
  std::vector<std::string> out;
  for (const auto& [name, spec] : table()) out.push_back(name);
  {
    std::shared_lock<std::shared_mutex> lock(host_table_mutex());
    for (const auto& [name, spec] : host_table()) out.push_back(name);
  }
  std::sort(out.begin(), out.end());
  return out;
}

int HostArgs::count() const { return args_.count(); }

// An argument the call does not have is E_BAD_ARG at the call (spec §8.1), never
// a read past the end of the argument vector: a function registered with
// min < max that reads an optional argument without testing count() used to get
// a heap-buffer-overflow.
namespace {
void host_arg_in_range(const Args& args, int i) {
  if (i < 0 || i >= args.count()) {
    fail("E_BAD_ARG",
         "a host function read argument " + std::to_string(i + 1) + " but the call has " +
             std::to_string(args.count()),
         args.pos());
  }
}
}  // namespace

const Value& HostArgs::val(int i) { host_arg_in_range(args_, i); return args_.val(i); }
const std::string& HostArgs::text(int i) { host_arg_in_range(args_, i); return args_.text(i); }
const std::string& HostArgs::bytes(int i) { host_arg_in_range(args_, i); return args_.bytes(i); }
bool HostArgs::boolean(int i) { host_arg_in_range(args_, i); return args_.boolean(i); }
Dec HostArgs::decimal(int i) { host_arg_in_range(args_, i); return args_.dec(i); }
long long HostArgs::integer(int i) { host_arg_in_range(args_, i); return args_.integer(i); }
long long HostArgs::non_neg_int(int i) { host_arg_in_range(args_, i); return args_.non_neg_int(i); }
Pos HostArgs::pos_of(int i) const {
  host_arg_in_range(args_, i);
  return args_.pos_of(i);
}

void register_function(const std::string& name, int min, int max, HostFunction fn) {
  ensure_registered();
  bool ok = !name.empty() && ((name[0] >= 'A' && name[0] <= 'Z') || (name[0] >= 'a' && name[0] <= 'z'));
  for (char c : name) {
    ok = ok && ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_');
  }
  if (!ok) {
    throw std::invalid_argument(
        "SEL function name must be ASCII letters, digits and _, starting with a letter: " + name);
  }
  const std::string key = ascii_upper(name);
  if (is_reserved(key)) throw std::invalid_argument(key + " is a reserved word");
  if (table().count(key)) throw std::invalid_argument(key + " is a builtin; a host function cannot replace it");
  if (min < 0 || max < min) {
    throw std::invalid_argument("SEL function " + key + ": arity must be whole numbers with 0 <= min <= max");
  }
  if (!fn) throw std::invalid_argument("SEL function " + key + ": fn is empty");
  auto spec = std::make_shared<Spec>();
  spec->name = key;
  spec->min = min;
  spec->max = max;
  spec->host = std::make_shared<const HostFunction>(std::move(fn));
  std::unique_lock<std::shared_mutex> lock(host_table_mutex());
  auto& slot = host_table()[key];
  if (slot) retired_host_specs().push_back(slot);
  slot = std::move(spec);
}

}  // namespace sel
