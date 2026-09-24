// SQL API parity probe: the planner's contract through every host's own SQL
// binding. tools/check-sqlapi.sh diffs the reports of the hosts that carry the
// SQL layer (the JS bundles do not; they print nothing and are left out).
// Three programs -- one the planner pushes down whole, one it splits into a SQL
// prefix and an in-memory continuation, one it keeps in memory -- and the same
// nine questions about each plan: its classification, dialect, statement, prefix
// and continuation presence, the continuation's dependencies, its source
// variable, the physical source tables and the selected member. The probe NAMES
// are the contract and the VALUES are compared; each host spells its accessors
// its own way (SEL-0044).
#include "../sel.hpp"
#include "../sel_sql.hpp"
#include "../sel_sql_emit.hpp"
#include "../sel_sql_map.hpp"

#include <functional>
#include <iomanip>
#include <memory>
#include <span>
#include <stdexcept>
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
std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string s;
  for (std::size_t i = 0; i < parts.size(); ++i) s += (i ? sep : "") + parts[i];
  return s;
}
}  // namespace

int main() {
  using sel::sql::Binding;
  using sel::sql::SqlKind;
  sel::sql::Bindings bindings({
      {"ORDERS", Binding::relation("orders", "o",
                                  {{"ID", Binding::column("id", "o", SqlKind::Num)},
                                   {"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)},
                                   {"AMOUNT", Binding::column("amount", "o", SqlKind::Num)},
                                   {"NAME", Binding::column("name", "o", SqlKind::Text)}},
                                  std::nullopt, std::nullopt)},
      {"CUSTOMERS", Binding::relation("customers", "c",
                                     {{"ID", Binding::column("id", "c", SqlKind::Num)},
                                      {"NAME", Binding::column("name", "c", SqlKind::Text)}},
                                     std::nullopt, std::nullopt)}});
  auto probe = [&](const std::string& label, const std::string& source) {
    sel::Program program = sel::compile(source);
    sel::sql::HybridPlan plan = sel::sql::Sql::plan_hybrid(program, "mariadb", bindings, sel::sql::Options{});
    say("plan." + label + ".kind", plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid");
    say("plan." + label + ".dialect", plan.dialect.empty() ? "-" : plan.dialect);
    say("plan." + label + ".statement", plan.sql_statement ? plan.sql_statement->as_statement() : "-");
    say("plan." + label + ".prefix.present", b(plan.sql_prefix_ast != nullptr));
    say("plan." + label + ".continuation.present", b(plan.continuation_ast != nullptr));
    say("plan." + label + ".continuation.deps",
        plan.continuation_program ? join(plan.continuation_program->dependencies(), " ") : "-");
    say("plan." + label + ".source.var", plan.continuation_source_var);
    say("plan." + label + ".tables", join(plan.source_tables, ","));
    say("plan." + label + ".selected.member", plan.selected_member ? "present" : "-");
  };
  probe("sql", "ORDERS .> FILTER(_[\"AMOUNT\"] > 10) .> MAP(RECORD(\"id\", _[\"ID\"], \"amount\", _[\"AMOUNT\"]))");
  probe("hybrid", "ORDERS .> SORT_BY(_[\"AMOUNT\"]) .> FILTER(_K > 1)");
  probe("memory", "A += 1; ORDERS .> TAKE(1)");
  // The canonical flag is public: an application (and the SQL oracle) reads it
  // to know the fragment promised a spelling, not only a value (SEL-0058).
  auto fragment_probe = [&](const std::string& label, const std::string& dialect,
                            const std::string& source) {
    sel::sql::Fragment f = sel::sql::Sql::translate(sel::compile(source), dialect, bindings);
    say("fragment." + label + ".kind", std::string(sel::sql::kind_name(f.kind())));
    say("fragment." + label + ".canonical", b(f.canonical()));
    say("fragment." + label + ".caveats", f.caveats().empty() ? "-" : join(f.caveats(), ","));
  };
  fragment_probe("canon.postgresql", "postgresql", "CANON(1.50)");
  fragment_probe("canon.mariadb", "mariadb", "CANON(1.50)");
  fragment_probe("canon.sqlite", "sqlite", "CANON(1.50)");
  fragment_probe("abs.postgresql", "postgresql", "ABS(1.50)");

  // --- host functions with a SQL spelling (spec §8.1, sql/MAP.md §4.7) ----------
  // The registration order, the caveat, strict mode, a LIST argument, a builder,
  // reset() and a re-registration, through this host's own spelling of the API.
  using sel::sql::Fragment;
  using sel::sql::Map;
  using sel::sql::Section;
  using sel::sql::Sql;
  using EntrySpec = sel::sql::EntrySpec;
  const sel::sql::Bindings host({{"T", Binding::column("title", "t", SqlKind::Text)}});
  const auto attempt = [](const std::function<void()>& fn) -> std::string {
    try {
      fn();
      return "accepted";
    } catch (const sel::sql::SqlError& e) {
      return "SqlError " + e.code();
    } catch (const std::runtime_error&) {
      return "refused";
    }
  };
  const auto local_slug = [](sel::HostArgs& a) { return sel::Value::text("local:" + a.text(0)); };

  say("host.spell.before-register", attempt([] {
        Map::define("postgresql", Section::Funcs, "HSLUG", EntrySpec::tpl("slug({0})", "TEXT"));
      }));
  sel::register_function("HSLUG", 1, 1, local_slug);
  Map::define("postgresql", Section::Funcs, "HSLUG",
              EntrySpec::tpl("slug({0})", "TEXT").args({"TEXT"}));
  const Fragment spelled = Sql::translate(sel::compile("HSLUG(T) $== \"x\""), "postgresql", host);
  say("host.spell.condition", spelled.as_condition());
  say("host.spell.caveats", spelled.caveats().empty() ? "-" : join(spelled.caveats(), ","));
  say("host.spell.strict", attempt([&] {
        Sql::translate(sel::compile("HSLUG(T)"), "postgresql", host, sel::sql::Options{.strict = true});
      }));
  say("host.spell.other-dialect",
      attempt([&] { Sql::translate(sel::compile("HSLUG(T)"), "mariadb", host); }));

  sel::register_function("HHAS", 2, 2, [](sel::HostArgs&) { return sel::Value::boolean(false); });
  Map::define("postgresql", Section::Funcs, "HHAS",
              EntrySpec::tpl("({1} = ANY(ARRAY[{0}]))", "BOOL").args({"LIST", "TEXT"}));
  const Fragment listed =
      Sql::translate(sel::compile("HHAS((\"a\", \"b\"), \"c\")"), "postgresql", host);
  say("host.spell.list.params", listed.as_condition(sel::sql::Mode::Params));
  std::vector<std::string> bound;
  for (const sel::Value& v : listed.bindings()) bound.push_back(v.dump());
  say("host.spell.list.bound", join(bound, ","));

  sel::register_function("HWRAP", 1, 1, [](sel::HostArgs& a) { return a.val(0).clone(); });
  Map::define_builder(
      "postgresql", Section::Funcs, "HWRAP",
      std::make_shared<sel::sql::Builder>(
          [](sel::sql::Emit& emit, std::span<const Fragment> args, sel::Pos) {
            std::vector<Fragment::Part> parts;
            parts.push_back({.sql = "wrap("});
            for (const Fragment::Part& part : args[0].parts()) parts.push_back(part);
            parts.push_back({.sql = ")"});
            return Fragment(std::move(parts), SqlKind::Text, emit.dialect());
          }));
  say("host.spell.builder",
      Sql::translate(sel::compile("HWRAP(T)"), "postgresql", host).as_value());

  sel::register_function("HSLUG", 1, 2, local_slug);
  say("host.spell.reregistered-arity",
      attempt([&] { Sql::translate(sel::compile("HSLUG(T)"), "postgresql", host); }));
  sel::register_function("HSLUG", 1, 1, local_slug);
  say("host.spell.arity-restored",
      attempt([&] { Sql::translate(sel::compile("HSLUG(T)"), "postgresql", host); }));
  Map::reset();
  say("host.spell.after-reset",
      attempt([&] { Sql::translate(sel::compile("HSLUG(T)"), "postgresql", host); }));
  say("host.spell.after-reset.local", sel::evaluate("HSLUG(\"A\")").as_text());

  std::cout << join(out, "\n") << "\n";
  return 0;
}
