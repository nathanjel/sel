// SQL-aimed usage — pushing a rule down to the database, from C++.
//
//   cmake -S cpp -B cpp/build -DSEL_BUILD_TOOLS=ON && cmake --build cpp/build
//   cpp/build/sel-example-sql
//
// The same rule that validates one order in the application can filter a
// million of them in the database. What makes that safe is that the translation
// refuses rather than guesses: if SQL cannot be made to mean what SEL means, no
// SQL is emitted and the rule stays where it already worked.
//
// The four files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.
//
// The visible difference here is that C++ has no untyped map to hand over as
// bindings, and no null to return from a call that declines: a Bindings is a
// constructed object whose members come from typed factory calls, and
// try_translate() answers with an empty std::optional rather than a null. That
// is more typing and exactly the same contract.

#include "../../cpp/sel_sql.hpp"

#include <cstddef>
#include <iostream>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace {

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Fragment;
using sel::sql::Mode;
using sel::sql::Sql;
using sel::sql::SqlError;
using sel::sql::SqlKind;

// Left-pad to a fixed width, so this file's columns line up with the four
// written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) {
    if (i) out += sep;
    out += parts[i];
  }
  return out;
}

}  // namespace

int main() {
  // 1 — a rule, and what the database should call its inputs --------------------
  // dependencies() says exactly what has to be bound. A name the program reads
  // and the bindings do not describe is a refusal, not a guess.

  std::cout << "1. a rule pushed down\n";
  const sel::Program rule = sel::compile("TOTAL > 100.00 AND STATUS $== \"open\"");
  std::cout << "   needs        => " << join(rule.dependencies(), " ") << "\n";

  const Bindings bindings({
      {"TOTAL", Binding::column("total", "o", SqlKind::Num)},
      {"STATUS", Binding::column("status", "o", SqlKind::Text)},
  });
  const Fragment frag = Sql::translate(rule, "mariadb", bindings);
  // Rendering can refuse — as_condition() rejects a NUM or TEXT fragment rather
  // than letting the database decide what truthiness means — so the string is
  // finished before anything is written. `std::cout << "   sql => " << f()`
  // would have flushed the label already when f() threw.
  const std::string inline_sql = frag.as_condition();
  std::cout << "   sql          => " << inline_sql << "\n";

  // 2 — the same rule as a prepared statement -------------------------------------
  // Mode::Inline is for reading and for a query you build once. Mode::Params is
  // what you hand a driver: the literals become placeholders and bindings()
  // gives the values in the order the placeholders appear in the output.

  std::cout << "2. as parameters\n";
  const std::string param_sql = frag.as_condition(Mode::Params);
  std::cout << "   sql          => " << param_sql << "\n";
  std::vector<std::string> values;
  for (const sel::Value& v : frag.bindings()) values.push_back(v.dump());
  std::cout << "   values       => " << join(values, ", ") << "\n";

  // 3 — one rule, every dialect ----------------------------------------------------
  // The differences below are the databases', not the rule's. Nothing in the
  // program changed.

  std::cout << "3. every dialect\n";
  for (const std::string& dialect : Sql::dialects()) {
    const std::string sql = Sql::translate(rule, dialect, bindings).as_condition();
    std::cout << "   " << pad(dialect, 12) << " => " << sql << "\n";
  }

  // 4 — a rule over a related table -------------------------------------------------
  // An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
  // which is the shape a database can actually use an index for.

  std::cout << "4. over a relation\n";
  const sel::Program lines = sel::compile("ALL(ITEMS, I, I[\"QTY\"] > 0)");
  const Bindings item_bindings({
      {"ITEMS", Binding::relation("order_items", "oi",
                                  {{"QTY", Binding::column("qty", std::nullopt, SqlKind::Num)}},
                                  std::nullopt, "`oi`.`order_id` = `o`.`id`")},
  });
  const std::string relation_sql =
      Sql::translate(lines, "mariadb", item_bindings).as_condition();
  std::cout << "   sql          => " << relation_sql << "\n";

  // 5 — refusal is an ordinary answer ------------------------------------------------
  // try_translate returns an empty optional so the caller can fall back to the
  // evaluator without a try/catch. translate() throws the same refusal with the
  // reason written out, which is what you want in a build-time audit of a rule
  // set.

  std::cout << "5. refusal\n";
  const sel::Program unbound = sel::compile("MYSTERY > 1");
  // The label is JS's spelling of the method, in every one of the five files.
  // The outputs have to be byte-identical, so one host's name for the call is
  // what all of them print; this host's is try_translate.
  std::cout << "   tryTranslate => "
            << (Sql::try_translate(unbound, "mariadb", bindings).has_value()
                    ? "translated"
                    : "null — evaluate it in the host instead")
            << "\n";
  try {
    Sql::translate(unbound, "mariadb", bindings);
  } catch (const SqlError& e) {
    // Only SqlError. A bug in the translator, or a malformed registration,
    // is a different type and goes on up — the same line JS draws with its
    // `instanceof` rethrow.
    std::cout << "   translate    => " << e.code() << "\n";
  }

  // 6 — what a fragment knows about itself ---------------------------------------------
  // A caveat is the map saying "this dialect's answer may differ from SEL's here".
  // An empty list is the layer promising it does not.

  std::cout << "6. the fragment\n";
  std::cout << "   kind         => " << sel::sql::kind_name(frag.kind()) << "\n";
  std::cout << "   dialect      => " << frag.dialect() << "\n";
  std::cout << "   exact        => " << (frag.caveats().empty() ? "TRUE" : "FALSE") << "\n";
  return 0;
}
