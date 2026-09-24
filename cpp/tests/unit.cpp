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
  selt::eq(phys_id->math_plan->steps.size(), 1u, "1 step for x + 0");
  selt::eq(phys_id->math_plan->steps[0].name, std::string("X"), "name is X");
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

}  // namespace

int main() {
  test_utf8();
  test_decimal();
  test_value();
  test_host_api();
  test_evaluation_order();
  test_relational_optimizations();
  test_structural_hash_identity();
  test_math_plan();
  return selt::report("cpp unit");
}
