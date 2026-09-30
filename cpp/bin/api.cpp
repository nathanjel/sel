// API parity probe — C++. See tools/api.mjs for what this is and why.
// The four drivers must stay in the same order with the same probe names; the
// diff between their reports is the whole mechanism.

#include "../sel.hpp"

#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
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

std::string repeat(const std::string& unit, int n) {
  std::string s;
  for (int i = 0; i < n; i++) s += unit;
  return s;
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
  // A PARENTHESISED binder is still treated as a binding name here, though the
  // evaluator rejects it: ALL(I, (IT), IT > 0) is E_EXPECT_SYMBOL when run, yet
  // dependencies() answers as if IT were bound. Pinned because all seven hosts
  // agree on it and nothing else records it -- not endorsed. See the note in
  // docs/contributing.md.
  say("program.deps.grouped.binder", join(compile("ALL(I, (IT), IT > 0)").dependencies(), " "));
  // The binding forms of spec/builtins.json (see tools/api.mjs).
  say("program.deps.forms.top.binder-and-limit", join(compile("TOP(L, X, X[\"a\"], N)").dependencies(), " "));
  say("program.deps.forms.top.limit-is-outer", join(compile("TOP(L, COUNT(_))").dependencies(), " "));
  say("program.deps.forms.sort-by.text-direction-wins", join(compile("SORT_BY(L, K, \"DESC\")").dependencies(), " "));
  say("program.deps.forms.top-by.direction-is-outer", join(compile("TOP_BY(L, _[\"a\"], N, D)").dependencies(), " "));
  say("program.deps.forms.bucket.projection-inside", join(compile("BUCKET(L, G, G[\"k\"], COUNT(G) + _K)").dependencies(), " "));
  say("program.deps.forms.link.named-binders", join(compile("LINK(A, B, X, Y, X[\"a\"] == Y[\"b\"] AND Z)").dependencies(), " "));
  Value ctx = Value::none();
  ctx.set("TOTAL", Value::num("59.97"));
  say("program.run.reads.context", evaluate("TOTAL > 10.00", ctx).dump());
  evaluate("SEEN = TOTAL * 2", ctx);
  say("program.run.mutates.context", ctx.get("SEEN")->as_text());

  // A BOOL a host hands in is an ordinary value (see tools/api.mjs, PHP-C1).
  {
    Value a = Value::none(); a.set("FLAG", Value::boolean(true));
    Value bb = Value::none(); bb.set("FLAG", Value::boolean(true));
    compile("FLAG[\"k\"] = 1; 0").run(a);
    say("bool.isolated.between.contexts", std::to_string(bb.get("FLAG")->size()) + " " +
        std::to_string(Value::boolean(true).size()) + " " + evaluate("COUNT(TRUE)").as_text());
  }
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
  // Every character is a digit, so this is E_RANGE and not E_NOT_NUM. Value::num
  // is public API, so an embedding application can reach the numeral cap without
  // compiling a rule at all -- and all six hosts must refuse it the same way.
  try {
    Value::num(std::string(2000001, '1'));
  } catch (const SelError& e) {
    say("error.host.hugenum", e.code());
  }

  // Every public constructor holds the same rules (spec §8, review 2026-09-28):
  // the decimal form within the caps and canonical, keys checked, a malformed
  // call E_BAD_ARG -- each host through its own spelling of the constructor.
  {
    const auto dec = [](bool neg, std::string digits, std::int32_t scale) {
      sel::Dec d; d.neg = neg; d.digits = std::move(digits); d.scale = scale; return d;
    };
    const std::vector<std::pair<std::string, std::function<void()>>> probes = {
      {"error.host.dec.fraccap", [&] { Value::num(dec(false, "1", 1000001)); }},
      {"error.host.dec.negscale", [&] { Value::num(dec(false, "7", -1)); }},
      {"error.host.key.utf8", [] { Value::record({"a\xff"}, {Value::text("1")}); }},
      {"error.host.malformed", [] { Value::record({"a"}, {}); }},
    };
    for (const auto& [name, build] : probes) {
      try { build(); say(name, "no error"); } catch (const SelError& e) { say(name, e.code()); }
    }
    say("ctor.dec.negzero", Value::num(dec(true, "0", 0)).dump());
  }

  // A value nested past the cap is refused by every walk of it. Reachable from the
  // host API with no source involved at all -- set() does not refuse, because a
  // value is built from the leaf up and nothing knows how deep it will end up --
  // so the operations that walk it are where the cap has to hold. The numeral cap
  // probed two lines up is the same shape of rule.
  {
    const auto nest = [](int n) {
      Value v = Value::text("x");
      for (int i = 0; i < n; i++) {
        Value p = Value::none();
        p.set("1", v);
        v = p;
      }
      return v;
    };
    say("value.depth.under", nest(199).dump().empty() ? "no" : "ok");
    try {
      nest(200).dump();
    } catch (const SelError& e) {
      say("value.depth.over", e.code());
    }
  }

  // dependencies() walks the tree without evaluating it, so it is bounded by
  // neither the parser's nesting depth nor the evaluator's -- and in every host it
  // was bounded by nothing at all, until a flat chain of about fifty thousand
  // operators found the host's own stack. It shares the evaluation cap now, and
  // trips at the same node: a program whose dependencies cannot be computed is
  // exactly a program that could not have been evaluated. Both sides are pinned,
  // because a walk that counts twice or not at all fails one of them.
  say("deps.depth.under", join(compile("A" + repeat("+A", 199)).dependencies(), " "));
  try {
    compile("A" + repeat("+A", 200)).dependencies();
  } catch (const SelError& e) {
    say("deps.depth.over", e.code() + " " + std::to_string(e.line()) + ":" +
                              std::to_string(e.col()));
  }

  // --- the physical tree (docs/internals/sql-translation.md §12.1; SEL-0044, SEL-0049).
  // One tree per AST (pointer equality here), the same whatever data ran, the
  // AST untouched, run() the same after an explicit build.
  {
    Program joined = compile("ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"]) .> FILTER(_[\"orders\"][\"amount\"] > 1)");
    const std::string before = join(joined.dependencies(), " ");
    const auto first = joined.physical_ast();
    say("program.physical.built-once", b(joined.physical_ast() == first));
    say("program.physical.keeps.ast", b(join(joined.dependencies(), " ") == before) + " " + before);
    Value data = Value::none();
    compile("ORDERS = LIST(RECORD(\"id\", 1, \"customer_id\", 7, \"amount\", 5), RECORD(\"id\", 2, \"customer_id\", 7, \"amount\", 0), RECORD(\"id\", 3, \"customer_id\", 9, \"amount\", 9)); CUSTOMERS = LIST(RECORD(\"id\", 7, \"name\", \"x\")); 0").run(data);
    say("program.physical.run.agrees", joined.run(data).dump());
    Value empty = Value::none();
    compile("ORDERS = LIST(); CUSTOMERS = LIST(); 0").run(empty);
    joined.run(empty);
    say("program.physical.independent.of.data", b(joined.physical_ast() == first));
  }

  // --- host functions (spec/SPEC.md §8.1). Registered before compiling, called
  // like a builtin, handed evaluated arguments and the typed readers, never
  // allowed to replace a builtin; a compiled program keeps its function.
  register_function("host_join", 1, 3, [](HostArgs& a) {
    std::string joined;
    for (int i = 0; i < a.count(); i++) joined += (i ? "|" : "") + a.text(i);
    return Value::text(joined);
  });
  register_function("HOST_CHECK", 1, 1, [](HostArgs& a) {
    if (a.text(0).empty()) throw SelError("E_BAD_ARG", "must not be empty", a.pos_of(0));
    return Value::boolean(true);
  });
  say("host.fn.call", evaluate("HOST_JOIN(\"a\", 1, \"c\")").as_text());
  say("host.fn.case", evaluate("host_join(\"x\")").as_text());
  {
    const auto names = function_names();
    say("host.fn.listed", b(std::find(names.begin(), names.end(), "HOST_JOIN") != names.end()));
  }
  say("host.fn.deps", join(compile("HOST_JOIN(X, Y)").dependencies(), " "));
  say("host.fn.order", evaluate("A = 1; HOST_JOIN((A = A + 1), (A = A * 10), A)").as_text());
  for (const auto& [name, src] : std::vector<std::pair<std::string, std::string>>{
           {"host.fn.arity", "HOST_JOIN()"}, {"host.fn.type", "HOST_JOIN(\"a\", TRUE)"},
           {"host.fn.error", "HOST_CHECK(\"\")"}}) {
    try {
      evaluate(src);
      say(name, "no error");
    } catch (const SelError& e) {
      say(name, e.code() + " " + std::to_string(e.line()) + ":" + std::to_string(e.col()));
    }
  }
  for (const auto& [name, fname, lo, hi] : std::vector<std::tuple<std::string, std::string, int, int>>{
           {"host.fn.refuse.builtin", "len", 1, 1}, {"host.fn.refuse.reserved", "and", 1, 1},
           {"host.fn.refuse.underscore", "_x", 1, 1}, {"host.fn.refuse.digit", "1x", 1, 1},
           {"host.fn.refuse.dash", "a-b", 1, 1}, {"host.fn.refuse.min-over-max", "bad", 2, 1},
           {"host.fn.refuse.negative", "bad", -1, 0}}) {
    std::string r = "accepted";
    try {
      register_function(fname, lo, hi, [](HostArgs&) { return Value::text(""); });
    } catch (const SelError& e) {
      r = "SelError " + e.code();
    } catch (const std::invalid_argument&) {
      r = "refused";
    }
    say(name, r);
  }
  register_function("HOST_V", 0, 0, [](HostArgs&) { return Value::text("old"); });
  {
    const Program early = compile("HOST_V()");
    register_function("HOST_V", 0, 0, [](HostArgs&) { return Value::text("new"); });
    say("host.fn.replace", early.run().as_text() + " " + evaluate("HOST_V()").as_text());
  }

  // --- T12: dependencies() is FLOW-SENSITIVE (spec/SPEC.md §8): a variable is a
  // dependency when some read of it can happen before the program has definitely
  // assigned it, in evaluation order. Assignments under a condition, a short
  // circuit, `??` or an aggregate body are not definite; `op=` and `A[k] op= x`
  // read their target; a plain `A[k] = x` creates A and reads only the index.
  const auto deps = [](const std::string& src) {
    const std::string joined = join(compile(src).dependencies(), " ");
    return joined.empty() ? std::string("-") : joined;
  };
  say("program.deps.read-before-assign", deps("A + 1; A = 2"));
  say("program.deps.compound-assign-reads", deps("X += 1"));
  say("program.deps.index-compound-reads", deps("A[1] += 1"));
  say("program.deps.index-assign-vivifies", deps("A[1] = 2"));
  say("program.deps.self-assign-reads", deps("A = A + 1"));
  say("program.deps.assign-then-read", deps("A = 1; A + B"));
  say("program.deps.conditional-assign", deps("IF(X, A = 1, 0); A"));
  say("program.deps.both-branches-assign", deps("IF(X, A = 1, A = 2); A"));
  say("program.deps.and-rhs-assign", deps("X AND (A = 1); A"));
  say("program.deps.coalesce-rhs-assign", deps("X ?? (A = 1); A"));
  say("program.deps.aggregate-body-assign", deps("MAP(L, A = _); A"));
  say("program.deps.cond-with-default-assigns", deps("COND(X, A = 1, Y, A = 2, A = 3); A"));
  say("program.deps.assign-in-argument", deps("LEFT(\"abc\", (N = 2)); N"));
  say("program.deps.compound-rhs-assign-is-too-late", deps("A += (A = 1; 2); A"));
  say("program.deps.index-expr-assign-precedes-compound-read", deps("A[(A = RECORD(\"x\", 1); \"x\")] += 2; A[\"x\"]"));
  say("program.deps.get-default-assign-not-definite", deps("GET(R, \"a\", (A = 1)); A"));
  say("program.deps.index-keys-run-in-source-order", deps("A[(K = 1)][K] = B; K"));
  say("program.deps.index-key-read-before-a-later-key-assigns", deps("A[K][(K = 1)] = B; K"));
  say("program.deps.top-arg-is-not-a-binder-in-the-three-argument-form", deps("L = LIST(1,2); TOP(L, A, (A = 1; 1))"));
  say("program.deps.bucket-key-phase-assignment-is-not-definite-for-the-projection", deps("L = LIST(1,2); BUCKET(L, G, (A = G; A), COUNT(G) + A)"));

  // --- T12: a Program is reusable: after a caught error it runs again, and two
  // contexts are independent whatever the interleaving.
  {
    const Program divide = compile("A / B");
    Value bad = Value::none();
    compile("A = 1; B = 0; 0").run(bad);
    Value good = Value::none();
    compile("A = 6; B = 3; 0").run(good);
    const auto attempt = [&](Value& ctx) {
      try {
        return divide.run(ctx).dump();
      } catch (const SelError& e) {
        return e.code() + " " + std::to_string(e.line()) + ":" + std::to_string(e.col());
      }
    };
    const std::string first = attempt(bad);
    const std::string second = attempt(good);
    const std::string third = attempt(bad);
    say("program.reuse.after-error", first + "|" + second + "|" + third);
    const Program bump = compile("X = X + 1");
    Value a = Value::none();
    compile("X = 1; 0").run(a);
    Value c = Value::none();
    compile("X = 10; 0").run(c);
    const std::string r1 = bump.run(a).as_text();
    const std::string r2 = bump.run(c).as_text();
    const std::string r3 = bump.run(a).as_text();
    const std::string r4 = bump.run(c).as_text();
    say("program.reuse.two-contexts", r1 + " " + r2 + " " + r3 + " " + r4);
  }

  // --- T12: input the API cannot take is E_BAD_ARG, never a host exception or a
  // different SEL error (spec/SPEC.md §8). This host is statically typed: source
  // is a std::string and there is no native conversion, so the first three cannot
  // be posed; they print n/a with the reason, and tools/check-api.sh leaves an
  // n/a line out of the diff for that host.
  say("error.compile.non-string", "n/a (source is a std::string)");
  say("value.native.unsupported", "n/a (no native conversion)");
  say("value.native.fraction", "n/a (no native conversion)");
  {
    std::string r = "accepted";
    try {
      register_function("bad", 0, 0, HostFunction{});
    } catch (const SelError& e) {
      r = "SelError " + e.code();
    } catch (const std::exception&) {
      r = "refused";
    }
    say("host.fn.refuse.not-callable", r);
  }
  // Reading an argument the call does not have is undefined behaviour in this host
  // today (CPP-C13) and can take the process down. Ask in a child process, so a crash
  // is reported as this one probe's answer ("host:signal N") and the rest of the report
  // still prints.
  register_function("HOST_OOB", 1, 2, [](HostArgs& a) { return Value::text(a.text(a.count() > 1 ? 1 : 5)); });
  {
    std::string r = "no error";
    int fd[2];
    if (pipe(fd) == 0) {
      std::cout.flush();
      const pid_t pid = fork();
      if (pid == 0) {
        close(fd[0]);
        std::string code = "no error";
        try {
          evaluate("HOST_OOB(\"x\")");
        } catch (const SelError& e) {
          code = e.code();
        } catch (const std::exception&) {
          code = "host:exception";
        }
        const ssize_t w = write(fd[1], code.data(), code.size());
        (void)w;
        _exit(0);
      }
      close(fd[1]);
      char buf[128];
      const ssize_t n = read(fd[0], buf, sizeof buf);
      close(fd[0]);
      int status = 0;
      waitpid(pid, &status, 0);
      if (WIFSIGNALED(status)) r = "host:signal " + std::to_string(WTERMSIG(status));
      else if (n > 0) r.assign(buf, static_cast<std::size_t>(n));
    }
    say("host.fn.arg.out-of-range", r);
  }

  // --- T12 (CPP-C15): a host-supplied value nested past the cap, handed to RECORD beside a
  // key that is not text. Arguments are evaluated first and coerced after (spec/SPEC.md §6.2),
  // so the key's E_NOT_TEXT wins; copying the over-deep value (E_DEPTH) happens only once the
  // arguments are known good. C++ built the pair in one expression and let the copy run first.
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
    say("program.run.over-deep-host-value.key-error-first", at("RECORD(TRUE, V)") + "|" + at("RECORD(\"k\", V)"));
  }

  std::cout << join(out, "\n") << "\n";
  return 0;
}
