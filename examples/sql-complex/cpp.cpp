// A report no database can take a share of -- SQL loads, SEL computes, from C++.
//
//   tools/check-usage.sh sql-complex           (starts the databases for you)
//
// The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
// numbers out of subjects with RGROUPS and searches the event log for each
// ticket's first answer. plan_hybrid() finds no step of it PostgreSQL can
// answer, and says so: pure_memory. So the database's job shrinks to handing
// over the tables -- with the SELECTs written by SEL too, from the same bindings
// -- and the report runs in memory over what came back. The last line checks it
// against the report over the data generated in memory (examples/memory-complex),
// which is where the rows in this database came from.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-complex`,
// which links examples/lib/db.cpp and the PostgreSQL, MariaDB and SQLite
// clients.

#include "../../cpp/sel.hpp"
#include "../../cpp/sel_sql.hpp"
#include "../lib/db.hpp"

#include <cstddef>
#include <fstream>
#include <iostream>
#include <map>
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

// Left- and right-pad to a fixed width, so this file's columns line up with the
// four written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string rpad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : std::string(n - s.size(), ' ') + s;
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

std::string read(const std::string& name) {
  const std::string path = "examples/lib/" + name;
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("cannot read " + path);
  std::ostringstream text;
  text << in.rdbuf();
  return text.str();
}

Binding relation(const std::string& table, const std::string& alias,
                 const std::vector<std::pair<std::string, SqlKind>>& fields) {
  std::vector<std::pair<std::string, Binding>> columns;
  for (const auto& [name, kind] : fields) columns.emplace_back(name, Binding::column(name, alias, kind));
  return Binding::relation(table, alias, columns);
}

}  // namespace

int main() {
  // EXAMPLE-BEGIN load
  const SqlKind NUM = SqlKind::Num, TEXT = SqlKind::Text;
  const Bindings schema({
      {"TEAMS",     relation("teams", "g", {{"team_id", NUM}, {"team", TEXT}})},
      {"CUSTOMERS", relation("customers", "c", {{"customer_id", NUM}, {"customer", TEXT},
                                                {"plan", TEXT}})},
      {"SLA",       relation("sla", "s", {{"plan", TEXT}, {"priority", TEXT},
                                          {"respond_within", NUM}, {"resolve_within", NUM}})},
      {"TICKETS",   relation("tickets", "t", {{"ticket_id", NUM}, {"customer_id", NUM},
                                              {"team_id", NUM}, {"priority", TEXT},
                                              {"subject", TEXT}, {"opened_at", NUM},
                                              {"closed_at", NUM}})},
      {"EVENTS",    relation("events", "e", {{"event_id", NUM}, {"ticket_id", NUM},
                                             {"seq", NUM}, {"at", NUM}, {"kind", TEXT},
                                             {"actor", TEXT}})},
  });
  const std::map<std::string, std::string> keys = {
      {"TEAMS", "team_id"}, {"CUSTOMERS", "customer_id"}, {"SLA", "plan"},
      {"TICKETS", "ticket_id"}, {"EVENTS", "event_id"}};

  const sel::Program report = sel::compile(read("tickets-report.sel"));
  const HybridPlan plan = Sql::plan_hybrid(report, "postgresql", schema);
  std::cout << "1. the report, planned for PostgreSQL\n";
  std::cout << "   plan        " << (plan.pure_memory ? "pure_memory" : "pushed down") << "\n";
  std::cout << "   reads       " << join(plan.source_tables, ", ") << "\n";

  std::cout << "2. so SQL only loads the tables it reads\n";
  const db::Connection conn = db::connect("postgresql");
  sel::Value tables = sel::Value::none();
  for (const std::string& name : report.dependencies()) {
    const sel::Program load = sel::compile(name + " .> SORT_BY(_[\"" + keys.at(name) + "\"])");
    const std::string sql = Sql::translate_statement(load, "postgresql", schema).as_statement();
    tables.set(name, db::query(conn, sql));
    std::cout << "   " << pad(name, 10) << "  " << rpad(std::to_string(tables.get(name)->size()), 3)
              << " rows  " << sql << "\n";
  }

  std::cout << "3. and SEL computes the report over them\n";
  const sel::Value result = report.run(tables);
  std::cout << db::render(result, "   | ") << "\n";
  // EXAMPLE-END load

  sel::Value generated = sel::evaluate(read("tickets-generate.sel"));
  std::cout << "   over the generated rows: "
            << (report.run(generated).dump() == result.dump() ? "same report" : "DIFFERENT") << "\n";
  return 0;
}
