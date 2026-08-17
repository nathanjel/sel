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

  auto bin = [](const char* a, const char* b, Dec (*op)(const Dec&, const Dec&)) {
    Dec x, y;
    dec_parse(a, x);
    dec_parse(b, y);
    return dec_format(op(x, y));
  };

  selt::eq(bin("2.50", "2.50", dec_add), std::string("5.00"), "money keeps its cents");
  selt::eq(bin("1.5", "1.5", dec_mul), std::string("2.25"), "* adds the scales");
  selt::eq(bin("0.1", "0.2", dec_add), std::string("0.3"), "no binary floating point here");

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

void test_value() {
  selt::section("value");

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
  selt::raises("E_NO_SCALAR", [] { Value::none().as_text(); }, "no scalar and no children");
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

}  // namespace

int main() {
  test_utf8();
  test_decimal();
  test_value();
  test_host_api();
  test_evaluation_order();
  return selt::report("cpp unit");
}
