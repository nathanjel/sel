// API parity probe — C++. See tools/api.mjs for what this is and why.
// The four drivers must stay in the same order with the same probe names; the
// diff between their reports is the whole mechanism.

#include "../sel.hpp"

#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::vector<std::string> out;
int counter = 0;

void say(const std::string& name, const std::string& value) {
  std::ostringstream line;
  line << std::setw(2) << std::setfill('0') << ++counter << " " << name << " = " << value;
  out.push_back(line.str());
}

std::string b(bool x) { return x ? "true" : "false"; }

// The kind constants are an enum here; the report prints the same spelling every
// host uses so the diff compares like with like.
std::string kind_name(sel::Kind k) {
  switch (k) {
    case sel::Kind::None: return "NONE";
    case sel::Kind::Text: return "TEXT";
    case sel::Kind::Bin: return "BIN";
    default: return "BOOL";
  }
}

std::string join(const std::vector<std::string>& xs, const std::string& sep) {
  std::string s;
  for (std::size_t i = 0; i < xs.size(); i++) {
    if (i) s += sep;
    s += xs[i];
  }
  return s;
}

}  // namespace

int main() {
  using namespace sel;

  // --- kind constants and predicates
  say("kind.const.none", kind_name(Kind::None));
  say("kind.const.text", kind_name(Kind::Text));
  say("kind.const.bin", kind_name(Kind::Bin));
  say("kind.const.bool", kind_name(Kind::Bool));
  say("kind.static.bool", kind_name(Kind::Bool));
  say("kind.of.text", kind_name(evaluate("\"x\"").kind()));
  say("kind.of.bool", kind_name(evaluate("TRUE").kind()));
  say("kind.of.none", kind_name(evaluate("(1,2)").kind()));
  say("pred.isText", b(evaluate("\"x\"").is_text()));
  say("pred.isBool", b(evaluate("TRUE").is_bool()));
  say("pred.isNone", b(evaluate("(1,2)").is_none()));
  say("pred.isBin", b(evaluate("TO_UTF8(\"x\")").is_bin()));
  say("pred.isText.on.bool", b(evaluate("TRUE").is_text()));

  // --- constructors
  say("ctor.text", Value::text("hi").dump());
  say("ctor.bool", Value::boolean(true).dump());
  say("ctor.none", Value::none().dump());
  say("ctor.num.canonicalises", Value::num("007").dump());
  say("ctor.int", Value::integer(-3).dump());
  say("ctor.list", Value::list({Value::text("a"), Value::text("b")}).dump());

  // --- children, and the ordering rules
  Value v = Value::none();
  v.set("b", Value::text("1"));
  v.set("a", Value::text("2"));
  say("children.size", std::to_string(v.size()));
  say("children.size.is.callable", b(true));   // it is a member function here
  say("children.keys", join(v.keys(), ","));
  v.set("b", Value::text("9"));
  say("children.reassign.keeps.position", join(v.keys(), ","));
  say("children.reassign.no.growth", std::to_string(v.size()));
  say("children.has", b(v.has("a")));
  say("children.has.missing", b(v.has("zz")));
  say("children.get", v.get("b")->dump());

  // --- scalar context
  say("scalar.asText", evaluate("\"héllo\"").as_text());
  say("scalar.asBool", b(evaluate("TRUE").as_bool()));
  say("scalar.takes.first.child", evaluate("(7,8)").as_text());
  say("scalar.looksNumeric", b(evaluate("\"2.50\"").looks_numeric()));
  say("scalar.looksNumeric.no", b(evaluate("\"x\"").looks_numeric()));

  // --- equality and dump
  say("eql.same", b(Value::text("5").eql(Value::text("5"))));
  say("eql.not.normalised", b(Value::text("5.00").eql(Value::text("5"))));
  say("dump.tree", evaluate("A=1; A[2]=\"x\"; A").dump());

  // --- programs
  const Program p = compile("IF(A > B, A, C)");
  say("program.dependencies", join(p.dependencies(), " "));
  say("program.deps.excludes.assigned", join(compile("X = 1; X + Y").dependencies(), " "));
  say("program.deps.excludes.binder", join(compile("ALL(I, IT, IT > 0)").dependencies(), " "));
  Value ctx = Value::none();
  ctx.set("TOTAL", Value::num("59.97"));
  say("program.run.reads.context", evaluate("TOTAL > 10.00", ctx).dump());
  evaluate("SEEN = TOTAL * 2", ctx);
  say("program.run.mutates.context", ctx.get("SEEN")->as_text());
  say("registry.count", std::to_string(function_names().size()));
  say("registry.sorted.first", function_names()[0]);

  // --- errors
  try {
    evaluate("1 +\n  X");
  } catch (const SelError& e) {
    say("error.code", e.code());
    say("error.line", std::to_string(e.line()));
    say("error.col", std::to_string(e.col()));
    say("error.isSelError", b(true));
  }
  try {
    compile("NOPE(1)");
  } catch (const SelError& e) {
    say("error.compile.unknown.func", e.code());
  }
  try {
    Value::num("x");
  } catch (const SelError& e) {
    say("error.host.badnum", e.code());
  }

  std::cout << join(out, "\n") << "\n";
  return 0;
}
