// A star schema -- whole pipelines in SQL, and split with memory, from C++.
//
//   tools/check-usage.sh sql-star              (starts the databases for you)
//
// fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
// The application describes each table once, as a relation binding, and then
// hands SEL whole pipelines. plan_hybrid() decides how much of each one the
// database can answer: all of it (pure_sql), a prefix of it (hybrid, the rest
// runs in memory over the rows the prefix returned), or none of it
// (pure_memory). The answer is checked against a run of the same program over
// the tables loaded into memory.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-star`,
// which links examples/lib/db.cpp and the PostgreSQL, MariaDB and SQLite
// clients.

#include "../../cpp/sel.hpp"
#include "../../cpp/sel_sql.hpp"
#include "../lib/db.hpp"

#include <cstddef>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::HybridPlan;
using sel::sql::Sql;
using sel::sql::SqlKind;

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

std::string read(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("cannot read " + path);
  std::ostringstream text;
  text << in.rdbuf();
  return text.str();
}

// EXAMPLE-BEGIN bindings
Binding relation(const std::string& table, const std::string& alias,
                 const std::vector<std::pair<std::string, SqlKind>>& fields) {
  std::vector<std::pair<std::string, Binding>> columns;
  for (const auto& [name, kind] : fields) columns.emplace_back(name, Binding::column(name, alias, kind));
  return Binding::relation(table, alias, columns);
}

Bindings schema() {
  const SqlKind NUM = SqlKind::Num, TEXT = SqlKind::Text;
  return Bindings({
      {"SALES",    relation("fact_sales", "s", {{"sale_id", NUM}, {"date_key", NUM},
                                                {"product_key", NUM}, {"store_key", NUM},
                                                {"qty", NUM}, {"revenue", NUM}})},
      {"DATES",    relation("dim_date", "d", {{"date_key", NUM}, {"year", NUM}, {"quarter", NUM},
                                              {"month", NUM}, {"month_name", TEXT}})},
      {"STORES",   relation("dim_store", "t", {{"store_key", NUM}, {"city", TEXT},
                                               {"region", TEXT}, {"format", TEXT}})},
      {"PRODUCTS", relation("dim_product", "p", {{"product_key", NUM}, {"sku", TEXT},
                                                 {"name", TEXT}, {"category", TEXT},
                                                 {"brand", TEXT}, {"list_price", NUM}})},
  });
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const std::vector<std::pair<std::string, std::string>> PIPELINES = {
    {"revenue by category, first quarter", "revenue-by-category.sel"},
    {"best-selling product per region, stores only", "best-product-per-region.sel"},
};

struct Table { std::string name, table, key; };

}  // namespace

int main() {
  const Bindings SCHEMA = schema();
  const db::Connection conn = db::connect("postgresql");

  // The same tables in memory, for the comparison at the end of each pipeline.
  sel::Value tables = sel::Value::none();
  for (const Table& t : std::vector<Table>{
           {"SALES", "fact_sales", "sale_id"},
           {"DATES", "dim_date", "date_key"},
           {"STORES", "dim_store", "store_key"},
           {"PRODUCTS", "dim_product", "product_key"},
       })
    tables.set(t.name, db::query(conn, "SELECT * FROM " + t.table + " ORDER BY " + t.key));

  for (std::size_t n = 1; n <= PIPELINES.size(); ++n) {
    const auto& [title, file] = PIPELINES[n - 1];
    // EXAMPLE-BEGIN run
    const sel::Program program = sel::compile(read("examples/sql-star/" + file));
    const HybridPlan plan = Sql::plan_hybrid(program, "postgresql", SCHEMA);
    const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                                plan.pure_memory ? tables : sel::Value::none());
    // EXAMPLE-END run
    const char* kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::cout << n << ". " << title << "\n";
    std::cout << "   plan        " << kind << "\n";
    std::cout << "   reads       " << join(plan.source_tables, ", ") << "\n";
    if (plan.sql_statement) std::cout << "   sql         " << plan.sql_statement->as_statement() << "\n";
    std::cout << db::render(rows, "   | ") << "\n";
    sel::Value copy = tables.clone();
    std::cout << "   in memory   "
              << (program.run(copy).dump() == rows.dump() ? "same rows" : "DIFFERENT") << "\n";
  }
  return 0;
}
