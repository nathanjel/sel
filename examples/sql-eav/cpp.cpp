// An entity-attribute-value catalogue -- pipelines over EAV rows, from C++.
//
//   tools/check-usage.sh sql-eav               (starts the databases for you)
//
// entities holds one row per product; attributes holds one (entity, name, value)
// row per property, every value TEXT, whatever it means. That shape is flexible
// to write and awkward to ask: "red or blue, and made of steel" is two EXISTS
// subqueries, and a price is a number only when the text says so. SEL pushes
// down what SQLite can answer exactly (text equality, joins, grouping) and keeps
// the rest -- pivoting attributes into records, comparing text as a number -- in
// memory, where ISNUM can say what SQLite cannot.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-eav`,
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
      {"PRODUCTS", relation("entities", "e", {{"id", NUM}, {"sku", TEXT}, {"kind", TEXT}})},
      {"ATTRS",    relation("attributes", "a", {{"entity_id", NUM}, {"name", TEXT},
                                                {"value", TEXT}})},
  });
}
// EXAMPLE-END bindings

// Each pipeline is a .sel file beside this one: (title, file).
const std::vector<std::pair<std::string, std::string>> PIPELINES = {
    {"red or blue, and steel", "red-or-blue-steel.sel"},
    {"products per colour", "products-per-colour.sel"},
    {"priced under 60.00, pivoted", "priced-under-60.sel"},
};

struct Table { std::string name, table, key; };

}  // namespace

int main() {
  const Bindings SCHEMA = schema();
  const db::Connection conn = db::connect("sqlite");

  // The same tables in memory, for the comparison at the end of each pipeline.
  sel::Value tables = sel::Value::none();
  for (const Table& t : std::vector<Table>{
           {"PRODUCTS", "entities", "id"},
           {"ATTRS", "attributes", "entity_id, name"},
       })
    tables.set(t.name, db::query(conn, "SELECT * FROM " + t.table + " ORDER BY " + t.key));

  for (std::size_t n = 1; n <= PIPELINES.size(); ++n) {
    const auto& [title, file] = PIPELINES[n - 1];
    // EXAMPLE-BEGIN run
    const sel::Program program = sel::compile(read("examples/sql-eav/" + file));
    const HybridPlan plan = Sql::plan_hybrid(program, "sqlite", SCHEMA);
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
