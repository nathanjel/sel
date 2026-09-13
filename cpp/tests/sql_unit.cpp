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

  std::cout << "cpp SQL advanced: 3/3 checks passed\n";
  return 0;
}
