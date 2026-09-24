// SQL conditions -- one rule as a WHERE clause, from C++.
//
//   tools/check-usage.sh sql-conditions        (starts the databases for you)
//
// A rule written for the application can filter rows where they live. Part 1 is
// the naive integration: the host knows nothing about the schema except that a
// variable is a column of the same name. Part 2 describes the schema -- types,
// a list of columns, a related table, a parameter -- and gets SQL that is both
// tighter and able to say more. Either way a rule SQL cannot express is refused
// whole, and runs in memory instead; every answer below is checked against the
// in-memory one.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-conditions`,
// which links examples/lib/db.cpp and the PostgreSQL, MariaDB and SQLite
// clients.

#include "../../cpp/sel.hpp"
#include "../../cpp/sel_sql.hpp"
#include "../lib/db.hpp"

#include <cstddef>
#include <functional>
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
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

// SEL names are upper case and these columns are lower case; both are ASCII.
std::string ascii_case(std::string s, bool upper) {
  for (char& c : s) {
    if (upper && c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
    if (!upper && c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
  }
  return s;
}

std::string ids(const sel::Value& records) {
  std::vector<std::string> out;
  for (const auto& [key, record] : records.entries()) out.push_back(record.get("id")->as_text());
  return out.empty() ? "(none)" : join(out, ", ");
}

// translate() says why try_translate() returned nothing.
std::string refusal(const sel::Program& rule, const std::string& dialect, const Bindings& bindings) {
  try {
    Sql::translate(rule, dialect, bindings);
    return "translated";
  } catch (const SqlError& e) {
    return e.code();
  }
}

// A row as a rule's context: SEL names are upper case, columns are not.
sel::Value context_of(const sel::Value& row) {
  sel::Value ctx = sel::Value::none();
  for (const auto& [column, value] : row.entries()) ctx.set(ascii_case(column, true), value);
  return ctx;
}

// The records a rule accepts, run in memory; keys kept.
sel::Value run_in_memory(const sel::Program& rule, const sel::Value& rows,
                         const std::function<sel::Value(const sel::Value&)>& context) {
  sel::Value kept = sel::Value::none();
  for (const auto& [key, row] : rows.entries()) {
    sel::Value ctx = context(row);
    if (rule.run(ctx).as_bool()) kept.set(key, row);
  }
  return kept;
}

const std::vector<std::string> RULES = {
    R"(COUNTRY $== "PL" AND TIER $!= "standard")",
    R"(COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000)",
    R"(RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE))",
    R"(IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL))",
    R"(ANY(SPLIT(NAME, " "), LEN(_) > 9))",
};

// What the rule sees in memory: the same names, as values.
sel::Value order_context(const sel::Value& order, const sel::Value& items) {
  sel::Value ctx = sel::Value::none();
  for (const char* name : {"status", "total", "channel"})
    ctx.set(ascii_case(name, true), *order.get(name));
  sel::Value tags = sel::Value::none();
  int n = 0;
  for (const char* column : {"tag1", "tag2", "tag3"}) tags.set(std::to_string(++n), *order.get(column));
  ctx.set("TAGS", tags);
  sel::Value lines = sel::Value::none();
  for (const auto& [key, item] : items.entries())
    if (item.get("order_id")->as_text() == order.get("id")->as_text())
      lines.set(std::to_string(lines.size() + 1), item);
  ctx.set("ITEMS", lines);
  ctx.set("MIN_TOTAL", sel::Value::text("100.00"));
  return ctx;
}

}  // namespace

int main() {
  // 1 - naive: a column per variable, nothing else known ---------------------------

  std::cout << "1. naive bindings\n";
  for (const std::string dialect : {"sqlite", "mariadb"}) {
    const db::Connection conn = db::connect(dialect);
    const sel::Value everyone = db::query(conn, "SELECT * FROM customers ORDER BY id");
    for (const std::string& source : RULES) {
      // EXAMPLE-BEGIN naive
      const sel::Program rule = sel::compile(source);
      std::vector<std::pair<std::string, Binding>> columns;
      for (const std::string& name : rule.dependencies())
        columns.emplace_back(name, Binding::column(ascii_case(name, false)));
      const Bindings bindings(columns);
      const std::optional<Fragment> where = Sql::try_translate(rule, dialect, bindings);
      sel::Value rows = sel::Value::none();
      if (where) {
        const std::string sql =
            "SELECT id FROM customers WHERE " + where->as_condition(Mode::Params) + " ORDER BY id";
        rows = db::query(conn, sql, where->bindings());
      } else {
        // refused: the rule stays in the application, over rows it loads
        for (const auto& [key, row] : everyone.entries()) {
          sel::Value ctx = context_of(row);
          if (rule.run(ctx).as_bool()) rows.set(key, row);
        }
      }
      // EXAMPLE-END naive
      const sel::Value in_memory = run_in_memory(rule, everyone, context_of);
      std::cout << "   " << pad(dialect, 8) << " " << source << "\n";
      std::cout << "             "
                << (where ? "sql    " + where->as_condition()
                          : "memory (" + refusal(rule, dialect, bindings) + ")")
                << "\n";
      std::cout << "             rows   " << ids(rows) << " | same as in memory: "
                << (ids(rows) == ids(in_memory) ? "TRUE" : "FALSE") << "\n";
    }
  }

  // 2 - involved: the host describes its schema ------------------------------------

  std::cout << "2. described bindings\n";
  const db::Connection conn = db::connect("postgresql");
  // EXAMPLE-BEGIN involved
  const Bindings bindings({
      {"STATUS",    Binding::column("status", "o", SqlKind::Text, /*exact=*/true)},
      {"TOTAL",     Binding::column("total", "o", SqlKind::Num)},
      {"CHANNEL",   Binding::column("channel", "o", SqlKind::Text, /*exact=*/true)},
      {"TAGS",      Binding::columns({Binding::column("tag1", "o", SqlKind::Text),
                                      Binding::column("tag2", "o", SqlKind::Text),
                                      Binding::column("tag3", "o", SqlKind::Text)})},
      {"ITEMS",     Binding::relation("order_items", "i", {
                        {"sku",   Binding::column("sku", "i", SqlKind::Text)},
                        {"qty",   Binding::column("qty", "i", SqlKind::Num)},
                        {"price", Binding::column("price", "i", SqlKind::Num)},
                    }, /*scalar=*/std::nullopt, /*correlate=*/R"("i"."order_id" = "o"."id")")},
      {"MIN_TOTAL", Binding::value(sel::Value::text("100.00"))},
  });
  // EXAMPLE-END involved

  const sel::Value orders = db::query(conn, "SELECT * FROM orders ORDER BY id");
  const sel::Value items = db::query(conn, "SELECT * FROM order_items ORDER BY order_id, line_no");

  for (const std::string source : {
           R"(STATUS $== "paid" AND TOTAL >= MIN_TOTAL)",
           R"(ANY(TAGS, _ $== "gift") AND CHANNEL $== "web")",
           R"(COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0))",
           R"(SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL)",
           R"(ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-"))",
       }) {
    // EXAMPLE-BEGIN involved-run
    const sel::Program rule = sel::compile(source);
    const Fragment where = Sql::translate(rule, "postgresql", bindings);
    const std::string sql =
        "SELECT id FROM orders o WHERE " + where.as_condition(Mode::Params) + " ORDER BY id";
    const sel::Value rows = db::query(conn, sql, where.bindings());
    // EXAMPLE-END involved-run
    const sel::Value in_memory = run_in_memory(
        rule, orders, [&](const sel::Value& order) { return order_context(order, items); });
    std::cout << "   " << source << "\n";
    std::cout << "             sql    " << where.as_condition() << "\n";
    if (!where.bindings().empty()) {
      std::vector<std::string> params;
      for (const sel::Value& v : where.bindings()) params.push_back(v.as_text());
      std::cout << "             params " << join(params, ", ") << "\n";
    }
    std::cout << "             rows   " << ids(rows) << " | same as in memory: "
              << (ids(rows) == ids(in_memory) ? "TRUE" : "FALSE") << "\n";
  }
  return 0;
}
