// Adding a SQL flavour — teaching the translator about your database, from C++.
//
//   cmake -S cpp -B cpp/build -DSEL_BUILD_TOOLS=ON && cmake --build cpp/build
//   cpp/build/sel-example-dialect
//
// The shipped map covers four targets over two bases. A deployment is rarely
// exactly one of them: a driver wants numbered placeholders, a function is
// spelled differently, an extension is not installed. A dialect is registered
// rather than forked, so what you write is only the difference.
//
// The four files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is where the registration API lives. The other
// hosts import `map` from the same module they import `Sql` from; C++ keeps the
// registry in sel_sql_map.hpp and the emitter in sel_sql_emit.hpp, both marked
// internal to the SQL layer, so an application that registers a dialect
// includes them alongside the public sel_sql.hpp. Registering is a startup-time
// act by the program that ships the translator, not something a consumer of a
// rule does, which is why the public header carries translation and not this.

#include "../../cpp/sel_sql.hpp"
#include "../../cpp/sel_sql_emit.hpp"
#include "../../cpp/sel_sql_map.hpp"

#include <iostream>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

using sel::Pos;
using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Builder;
using sel::sql::DialectSpec;
using sel::sql::Emit;
using sel::sql::EntrySpec;
using sel::sql::Fragment;
using sel::sql::Map;
using sel::sql::Mode;
using sel::sql::Section;
using sel::sql::Sql;
using sel::sql::SqlKind;

// One template over both sequences, because the two calls below answer with
// different string types: dialects() owns its names and chain() hands back
// views into storage the map owns.
template <typename Seq>
std::string join(const Seq& parts, std::string_view sep) {
  std::string out;
  bool first = true;
  for (const auto& part : parts) {
    if (!first) out += sep;
    first = false;
    out += part;
  }
  return out;
}

}  // namespace

int main() {
  const sel::Program rule = sel::compile("NAME $== \"ok\" AND TOTAL > 10.00");
  const Bindings bindings({
      {"NAME", Binding::column("name", "t", SqlKind::Text)},
      {"TOTAL", Binding::column("total", "t", SqlKind::Num)},
  });
  // Finished before anything is written, here and at every other call that can
  // refuse. `std::cout << "   base => " << sql_in(d)` would have flushed the
  // label already when sql_in threw.
  const auto sql_in = [&](const std::string& dialect) {
    return Sql::translate(rule, dialect, bindings).as_condition(Mode::Params);
  };

  // 1 — what ships ---------------------------------------------------------------

  std::cout << "1. what ships\n";
  std::cout << "   targets      => " << join(Sql::dialects(), " ") << "\n";
  std::cout << "   postgresql   => " << join(Map::chain("postgresql"), " -> ") << "\n";

  // 2 — a flavour of your own -------------------------------------------------------
  // `extends` is the whole mechanism: the new dialect answers for what it declares
  // and defers upward for everything else. Two-phase lookup -- the whole overlay
  // chain, then the whole shipped chain -- so an override never half-applies.
  //
  // extending() is how a dialect says what it extends. There is no way to write
  // one that forgot to: the refusal the dynamic hosts raise for a spec with no
  // `extends` is, here, a spec the language cannot express.

  std::cout << "2. a flavour of your own\n";
  Map::define_dialect("pg-libpq",
                      DialectSpec::extending("postgresql")
                          .version("15")
                          .target(true)                     // a base is not a target; this is a server
                          .lexical("placeholder", "${n}")); // libpq numbers its parameters
  const std::string base = sql_in("postgresql");
  const std::string libpq = sql_in("pg-libpq");
  std::cout << "   targets      => " << join(Sql::dialects(), " ") << "\n";
  std::cout << "   chain        => " << join(Map::chain("pg-libpq"), " -> ") << "\n";
  std::cout << "   base         => " << base << "\n";
  std::cout << "   pg-libpq     => " << libpq << "\n";

  // 3 — spelling one function differently ---------------------------------------------
  // {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
  // while every position SEL reports is one-based -- these are template holes, not
  // SEL positions. The entry also says what it returns, because the translator
  // infers kinds and will not guess.

  std::cout << "3. one function, respelled\n";
  Map::define("pg-libpq", Section::Funcs, "UPPER",
              EntrySpec::tpl("UPPER({0} COLLATE \"C\")", "TEXT"));
  const std::string upper =
      Sql::translate(sel::compile("UPPER(NAME)"), "pg-libpq", bindings).as_value();
  std::cout << "   upper        => " << upper << "\n";

  // 4 — withdrawing what a deployment does not have ------------------------------------
  // withdraw() takes the entry away. This is not the same as leaving it
  // unmapped: it is the map saying "not here", and the rule is refused rather
  // than emitted against a function the server does not have. The other hosts
  // pass a null where the entry would go; C++ has no null to pass, so the
  // withdrawal is a named constructor -- and is therefore impossible to write by
  // accident, which is why EntrySpec has no public default constructor.

  std::cout << "4. withdrawing an entry\n";
  Map::define("pg-libpq", Section::Funcs, "RMATCH", EntrySpec::withdraw());
  const sel::Program re = sel::compile("RMATCH('^a', NAME)");
  std::cout << "   postgresql   => "
            << (Sql::try_translate(re, "postgresql", bindings).has_value()
                    ? "translated"
                    : "refused")
            << "\n";
  std::cout << "   pg-libpq     => "
            << (Sql::try_translate(re, "pg-libpq", bindings).has_value()
                    ? "translated"
                    : "refused")
            << "\n";

  // 5 — a builder, for what a template cannot say -----------------------------------------
  // The escape hatch. It receives the emitter and the already-rendered arguments,
  // and returns a fragment, so it can do what no string with holes in it can. The
  // third argument is the call site, and C++ counts parameters, so a builder has
  // to accept it even when it has no use for it -- unnamed, which is also what
  // keeps -Wunused-parameter quiet.
  //
  // Splice the argument's `parts` rather than its rendered SQL. A part list is
  // strings alternating with parameter slots, so splicing keeps a bound value
  // bound; flattening it to a string first would inline whatever the argument
  // carried and quietly turn a prepared statement back into concatenation.
  //
  // Slot numbers are absolute for the whole translation, so a spliced part is
  // copied verbatim and never renumbered.

  std::cout << "5. a builder\n";
  Map::define_builder(
      "pg-libpq", Section::Funcs, "LEN",
      std::make_shared<Builder>(
          [](Emit& emit, std::span<const Fragment> args, Pos) {
            std::vector<Fragment::Part> parts;
            parts.push_back({.sql = "length("});
            for (const Fragment::Part& part : args[0].parts()) parts.push_back(part);
            parts.push_back({.sql = ")"});
            return Fragment(std::move(parts), SqlKind::Num, emit.dialect());
          }));
  const std::string len =
      Sql::translate(sel::compile("LEN(NAME)"), "pg-libpq", bindings).as_value();
  std::cout << "   len          => " << len << "\n";

  // 6 — putting it back ---------------------------------------------------------------------
  // reset() drops every registration and leaves the shipped map. Worth knowing in
  // a test suite: a registration that leaks into the next test is a test that
  // passes for the wrong reason.

  std::cout << "6. reset\n";
  Map::reset();
  std::cout << "   targets      => " << join(Sql::dialects(), " ") << "\n";
  std::cout << "   pg-libpq     => " << (Map::exists("pg-libpq") ? "still there" : "gone")
            << "\n";
  return 0;
}
