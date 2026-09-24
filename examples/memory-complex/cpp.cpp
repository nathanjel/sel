// The same report with no database at all -- generated data, in memory, from C++.
//
//   cd cpp && make build/example-memory-complex && cd ..
//   cpp/build/example-memory-complex         (from the repository root)
//
// examples/sql-complex loads the support desk from PostgreSQL. Here the same
// rows come from examples/lib/tickets-generate.sel -- a SEL program that builds
// them deterministically, and the source the PostgreSQL seed was rendered from
// -- and the same report runs over them. Nothing below opens a connection:
// the plan for MariaDB is computed from the bindings alone, and it says what it
// said for PostgreSQL, that none of this report is SQL's to answer.
//
// The four files beside this one print byte-identical output.
//
// db.hpp is included for render() alone, which is defined in the header and
// needs nothing but SEL; this example links no database client.

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

// Planning needs the schema, not a server: the bindings sql-complex describes
// PostgreSQL with, asked about MariaDB this time.
Binding relation(const std::string& table, const std::string& alias,
                 const std::vector<std::pair<std::string, SqlKind>>& fields) {
  std::vector<std::pair<std::string, Binding>> columns;
  for (const auto& [name, kind] : fields) columns.emplace_back(name, Binding::column(name, alias, kind));
  return Binding::relation(table, alias, columns);
}

}  // namespace

int main() {
  // EXAMPLE-BEGIN generate
  sel::Value data = sel::evaluate(read("tickets-generate.sel"));
  std::cout << "1. generated in memory\n";
  for (const std::string& name : data.keys())
    std::cout << "   " << pad(name, 10) << "  " << rpad(std::to_string(data.get(name)->size()), 3)
              << " rows\n";

  const sel::Program report = sel::compile(read("tickets-report.sel"));
  std::cout << "2. the report\n";
  std::cout << db::render(report.run(data), "   | ") << "\n";
  // EXAMPLE-END generate

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
  const HybridPlan plan = Sql::plan_hybrid(report, "mariadb", schema);
  std::cout << "3. planned for MariaDB, without connecting\n";
  std::cout << "   plan        " << (plan.pure_memory ? "pure_memory" : "pushed down") << "\n";
  std::cout << "   reads       " << join(plan.source_tables, ", ") << "\n";
  return 0;
}
