// Focused C++ SQL tests for the advanced relational-plan paths.

#include "../sel.hpp"
#include "../sel_sql.hpp"

#include <iostream>
#include <string>
#include <vector>

namespace {

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Sql;
using sel::sql::SqlKind;

Binding orders() {
  return Binding::relation(
      "orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}});
}

Binding customers() {
  return Binding::relation(
      "customers", "c",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}});
}

}  // namespace

int main() {
  const Bindings bindings({{"ORDERS", orders()}, {"CUSTOMERS", customers()}});
  const sel::Program inner = sel::compile(
      "MAP(LINK(ORDERS, CUSTOMERS, O, C, O[\"CUSTOMER_ID\"] == C[\"ID\"]), "
      "X, RECORD(\"ORDER_ID\", _1[\"ID\"], \"NAME\", _2[\"NAME\"]))");
  const std::string inner_sql =
      Sql::translate_statement(inner, "mariadb", bindings).as_statement();
  const std::string wanted_inner =
      "SELECT `o`.`id` AS `ORDER_ID`, `c`.`name` AS `NAME` FROM `orders` `o` "
      "INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)";
  if (inner_sql != wanted_inner) {
    std::cerr << "join SQL mismatch\n got:  " << inner_sql << "\n want: "
              << wanted_inner << "\n";
    return 1;
  }

  const sel::Program derived = sel::compile(
      "FILTER(MAP(ORDERS, O, RECORD(\"ID\", O[\"ID\"], \"CID\", O[\"CUSTOMER_ID\"])), "
      "R, R[\"ID\"] > 1)");
  const std::string derived_sql =
      Sql::translate_statement(derived, "mariadb", bindings).as_statement();
  const std::string wanted_derived =
      "SELECT `_sub1`.* FROM (SELECT `id` AS `ID`, `customer_id` AS `CID` "
      "FROM `orders` `o`) `_sub1` WHERE (CASE WHEN (`_sub1`.`ID` REGEXP "
      "'\\\\A-?[0-9]+(\\\\.[0-9]+)?\\\\z') THEN CAST(`_sub1`.`ID` AS DECIMAL(65,10)) "
      "ELSE NULL END > 1)";
  if (derived_sql != wanted_derived) {
    std::cerr << "derived SQL mismatch\n got:  " << derived_sql << "\n want: "
              << wanted_derived << "\n";
    return 1;
  }

  const sel::Program hybrid_program =
      sel::compile("MAP(TAKE(ORDERS, 1), O, ABORT(\"not pushed\"))");
  const sel::sql::HybridPlan hybrid =
      Sql::plan_hybrid(hybrid_program, "mariadb", bindings);
  if (!hybrid.is_hybrid || hybrid.pure_sql || hybrid.pure_memory ||
      !hybrid.sql_statement || !hybrid.continuation_program ||
      hybrid.source_tables.size() != 1 || hybrid.source_tables[0] != "orders") {
    std::cerr << "hybrid plan did not split at the unsupported suffix\n";
    return 1;
  }

  // The hybrid planner's contract, the parts a host-local check can see.
  // sql/cases/25-hybrid-plans.sqlt holds the language-neutral version; what is
  // here is what the runner cannot observe: the shared physical tree behind
  // run(), and stage 1 running before the prefix search with the same binding
  // set the other checks use.
  const sel::Program helper = sel::compile("X = ORDERS; X .> TAKE(1)");
  const sel::sql::HybridPlan helper_plan = Sql::plan_hybrid(helper, "mariadb", bindings);
  if (!helper_plan.pure_sql || helper_plan.source_tables != std::vector<std::string>{"orders"}) {
    std::cerr << "a helper assignment must be normalised before planning\n";
    return 1;
  }

  const sel::Program refused = sel::compile("A += 1; ORDERS .> TAKE(1)");
  const sel::sql::HybridPlan refused_plan = Sql::plan_hybrid(refused, "mariadb", bindings);
  if (!refused_plan.pure_memory || refused_plan.sql_statement ||
      !refused_plan.continuation_program ||
      refused_plan.continuation_program->ast() != refused.ast() ||
      refused_plan.continuation_ast != refused.ast() ||
      refused_plan.source_tables != std::vector<std::string>{"orders"}) {
    std::cerr << "a program stage 1 refuses must be a pure-memory plan over the original\n";
    return 1;
  }

  const sel::Program reusable = sel::compile(
      "(3, 1, 2) .> FILTER(NOT (_ < 1 + 1)) .> MAP(RECORD(\"x\", _, \"y\", _ * 2)) .> TAKE(2 * 1)");
  const sel::Program copy = reusable;
  const std::string first = reusable.run().dump();
  if (reusable.physical_ast() != reusable.physical_ast() ||
      copy.physical_ast() != reusable.physical_ast() ||
      reusable.physical_ast() == reusable.ast() || reusable.run().dump() != first) {
    std::cerr << "the physical tree must be built once and shared by copies\n";
    return 1;
  }

  // A plan's continuation reports errors where run() does. The planner folds
  // one tree for both halves of a split, so a hoisted literal in the
  // continuation carries the position the in-memory half will report
  // (spec §6.3; ctl.if.constant-condition-* and op.logic.*-keeps-the-*-position
  // are the run() half). sql/cases/25-hybrid-plans.sqlt pins the SQL side of
  // these; only executing the plan can see the position the memory side reports.
  // This is also the arm that never fired here: the C++ IF fold tested for four
  // items and so reported the IF's column by accident while the four hosts that
  // folded reported the literal's.
  const sel::Value rows = sel::Value::list(
      {sel::Value::record({"id"}, {sel::Value::text("1")}),
       sel::Value::record({"id"}, {sel::Value::text("2")})});
  const auto failure = [](const auto& fn) -> std::string {
    try {
      fn();
      return "no error";
    } catch (const sel::SelError& e) {
      return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
    }
  };
  struct Probe { const char* source; const char* kind; const char* want; };
  const Probe probes[] = {
      {"ORDERS .> TAKE(2) .> MAP(IF(TRUE, \"x\", 1) >= _[\"id\"])", "hybrid", "E_NOT_NUM@1:26"},
      {"ORDERS .> TAKE(2) .> FILTER((FALSE AND TRUE) + _[\"id\"] > 0)", "hybrid", "E_NOT_NUM@1:36"},
      {"ORDERS .> FILTER(IF(TRUE, \"x\", 1) >= _[\"id\"])", "pure_memory", "E_NOT_NUM@1:18"},
  };
  for (const Probe& probe : probes) {
    const sel::Program program = sel::compile(probe.source);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", bindings);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    if (kind != probe.kind) {
      std::cerr << probe.source << ": expected a " << probe.kind << " plan, got " << kind << "\n";
      return 1;
    }
    sel::Value context = sel::Value::none();
    context.set("ORDERS", rows);
    const std::string in_memory = failure([&] { sel::Value c = context.clone(); program.run(c); });
    const std::string executed = failure([&] {
      Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; },
                          context);
    });
    if (in_memory != probe.want || executed != probe.want) {
      std::cerr << probe.source << ": run() reports " << in_memory << ", the executed plan "
                << executed << ", want " << probe.want << "\n";
      return 1;
    }
  }

  std::cout << "cpp SQL advanced: 12/12 checks passed\n";
  return 0;
}
