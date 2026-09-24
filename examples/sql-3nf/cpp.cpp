// A third-normal-form shop -- joins, grouping and a split, from C++.
//
//   tools/check-usage.sh sql-3nf               (starts the databases for you)
//
// categories, products, customers, orders and order_lines, each fact stored
// once. The first pipeline is SQL from end to end. The second assigns every
// customer to an A/B cohort by CRC32 of their e-mail -- the application's own
// hashing, which PostgreSQL has no spelling for -- so the database joins, filters
// and multiplies, and the cohorts are computed in memory over what it returned.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-3nf`,
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
      {"CUSTOMERS", relation("customers", "c", {{"customer_id", NUM}, {"name", TEXT},
                                                {"email", TEXT}, {"country", TEXT}})},
      {"ORDERS",    relation("orders", "o", {{"order_id", NUM}, {"customer_id", NUM},
                                             {"status", TEXT}, {"ordered_on", TEXT}})},
      {"LINES",     relation("order_lines", "l", {{"order_id", NUM}, {"line_no", NUM},
                                                  {"product_id", NUM}, {"qty", NUM},
                                                  {"unit_price", NUM}})},
      {"PRODUCTS",  relation("products", "p", {{"product_id", NUM}, {"sku", TEXT},
                                               {"title", TEXT}, {"category_id", NUM},
                                               {"list_price", NUM}})},
  });
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const std::vector<std::pair<std::string, std::string>> PIPELINES = {
    {"paid revenue per product since March", "revenue-per-product.sel"},
    {"paid revenue per experiment cohort", "revenue-per-cohort.sel"},
};

struct Table { std::string name, table, key; };

}  // namespace

int main() {
  const Bindings SCHEMA = schema();
  const db::Connection conn = db::connect("postgresql");

  // The same tables in memory, for the comparison at the end of each pipeline.
  sel::Value tables = sel::Value::none();
  for (const Table& t : std::vector<Table>{
           {"CUSTOMERS", "customers", "customer_id"},
           {"ORDERS", "orders", "order_id"},
           {"LINES", "order_lines", "order_id, line_no"},
           {"PRODUCTS", "products", "product_id"},
       })
    tables.set(t.name, db::query(conn, "SELECT * FROM " + t.table + " ORDER BY " + t.key));

  for (std::size_t n = 1; n <= PIPELINES.size(); ++n) {
    const auto& [title, file] = PIPELINES[n - 1];
    // EXAMPLE-BEGIN run
    const sel::Program program = sel::compile(read("examples/sql-3nf/" + file));
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
