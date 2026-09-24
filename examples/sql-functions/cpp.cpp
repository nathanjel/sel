// The application's own functions, in memory and in PostgreSQL -- C++.
//
//   tools/check-usage.sh sql-functions         (starts the databases for you)
//
// The application registers five functions of its own. Each has a local
// implementation -- the code register_function runs -- and four also get a SQL
// spelling for PostgreSQL, which the application promises computes the same
// thing (spec §8.1, sql/MAP.md §4.7):
//
//   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
//   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
//   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
//   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
//   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
//   WORDS(title)                returns a list               -> no spelling: stays in memory
//
// Every pipeline prints its plan and its rows, and whether those rows are the rows
// the same program computes in memory -- which is how the example checks that the
// two implementations of each function agree on this data.
//
// The four files beside this one print byte-identical output.
//
// Built by `make -C cpp BUILD=build-usage build-usage/example-sql-functions`,
// which links examples/lib/db.cpp and the PostgreSQL, MariaDB and SQLite
// clients. The SQL spellings are registered through sel_sql_map.hpp, which is
// where this host keeps the map's registration API (see examples/dialect/).

#include "../../cpp/sel.hpp"
#include "../../cpp/sel_sql.hpp"
#include "../../cpp/sel_sql_map.hpp"
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
using sel::sql::EntrySpec;
using sel::sql::HybridPlan;
using sel::sql::Map;
using sel::sql::Section;
using sel::sql::Sql;
using sel::sql::SqlError;
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

Binding relation(const std::string& table, const std::string& alias,
                 const std::vector<std::pair<std::string, SqlKind>>& fields) {
  std::vector<std::pair<std::string, Binding>> columns;
  for (const auto& [name, kind] : fields) columns.emplace_back(name, Binding::column(name, alias, kind));
  return Binding::relation(table, alias, columns);
}

struct Table { std::string name, table, key; };

}  // namespace

int main() {
  const db::Connection conn = db::connect("postgresql");

  // 1 - the local implementations ------------------------------------------------------
  // What register_function runs: plain code, or -- where exact decimal arithmetic
  // matters -- a SEL expression, so the local answer has SEL's numbers.

  // EXAMPLE-BEGIN local
  const auto slug = [](const std::string& text) {
    std::string out;
    bool dash = false;
    for (char c : text) {
      if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);     // ASCII only, as SQL's
      if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {       // [^a-z0-9]+ sees it
        if (dash && !out.empty()) out += '-';
        out += c;
        dash = false;
      } else {
        dash = true;
      }
    }
    return out;
  };

  const sel::Program margin = sel::compile("ROUND((PRICE - COST) * 100 / PRICE, 1)");

  const sel::Value vat_rates = db::query(conn, "SELECT * FROM vat_rates");
  std::map<std::pair<std::string, std::string>, sel::Value> rates;
  for (const auto& [n, r] : vat_rates.entries())
    rates[{r.get("country")->as_text(), r.get("category")->as_text()}] = *r.get("rate");

  const sel::Program shipping = sel::compile(
      "COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)"
      " * IF(COUNTRY $== \"PL\", 1, 2)");

  sel::register_function("SLUG", 1, 1, [&](sel::HostArgs& args) {
    return sel::Value::text(slug(args.text(0)));
  });
  sel::register_function("MARGIN_PCT", 2, 2, [&](sel::HostArgs& args) {
    sel::Value ctx = sel::Value::none();
    ctx.set("PRICE", args.val(0));
    ctx.set("COST", args.val(1));
    return margin.run(ctx);
  });
  sel::register_function("VAT_RATE", 2, 2, [&](sel::HostArgs& args) {
    const std::string country = args.text(0), category = args.text(1);
    auto rate = rates.find({country, category});
    if (rate == rates.end()) rate = rates.find({country, "*"});   // find(), never the Value's truth
    return rate != rates.end() ? rate->second.clone() : sel::Value::text("0");
  });
  sel::register_function("SHIPPING_COST", 2, 2, [&](sel::HostArgs& args) {
    sel::Value ctx = sel::Value::none();
    ctx.set("KG", args.val(0));
    ctx.set("COUNTRY", args.val(1));
    return shipping.run(ctx);
  });
  sel::register_function("HAS_TAG", 2, 2, [](sel::HostArgs& args) {
    const sel::Value& tags = args.val(0);
    const std::string tag = args.text(1);
    if (tags.size() == 0) return sel::Value::boolean(tags.as_text() == tag);   // a scalar is a list of one
    for (const auto& [key, v] : tags.entries())
      if (v.as_text() == tag) return sel::Value::boolean(true);
    return sel::Value::boolean(false);
  });
  sel::register_function("WORDS", 1, 1, [&](sel::HostArgs& args) {
    sel::Value out = sel::Value::none();
    std::istringstream words(slug(args.text(0)));
    for (std::string w; std::getline(words, w, '-');)
      if (!w.empty()) out.set(std::to_string(out.size() + 1), sel::Value::text(w));
    return out;
  });
  // EXAMPLE-END local

  // 2 - the SQL spellings ------------------------------------------------------------------
  // After the functions: a spelling for a name that is not registered is refused.

  // EXAMPLE-BEGIN spell
  Map::define("postgresql", Section::Funcs, "SLUG",
              EntrySpec::tpl("slug({0})", "TEXT").args({"TEXT"}));
  Map::define("postgresql", Section::Funcs, "MARGIN_PCT",
              EntrySpec::tpl("margin_pct({0}, {1})", "NUM").args({"NUM", "NUM"}));
  Map::define("postgresql", Section::Funcs, "VAT_RATE",
              EntrySpec::tpl("vat_rate({0}, {1})", "NUM").args({"TEXT", "TEXT"}));
  Map::define("postgresql", Section::Funcs, "SHIPPING_COST",
              EntrySpec::tpl("shipping_cost({0}, {1})", "NUM").args({"NUM", "TEXT"}));
  Map::define("postgresql", Section::Funcs, "HAS_TAG",
              EntrySpec::tpl("({1} = ANY(ARRAY[{0}]))", "BOOL").args({"LIST", "TEXT"}));
  // WORDS returns a list: no spelling can say that, so it has none.
  // EXAMPLE-END spell

  const SqlKind NUM = SqlKind::Num, TEXT = SqlKind::Text;
  const Bindings schema({
      {"PRODUCTS", relation("products", "p", {{"product_id", NUM}, {"title", TEXT},
                                              {"category", TEXT}, {"price", NUM},
                                              {"cost", NUM}, {"weight_kg", NUM},
                                              {"tag1", TEXT}, {"tag2", TEXT}, {"tag3", TEXT}})},
      {"ORDERS",   relation("orders", "o", {{"order_id", NUM}, {"country", TEXT}})},
      {"LINES",    relation("order_lines", "l", {{"order_id", NUM}, {"line_no", NUM},
                                                 {"product_id", NUM}, {"qty", NUM}})},
  });

  sel::Value tables = sel::Value::none();
  for (const Table& t : std::vector<Table>{
           {"PRODUCTS", "products", "product_id"},
           {"ORDERS", "orders", "order_id"},
           {"LINES", "order_lines", "order_id, line_no"},
       })
    tables.set(t.name, db::query(conn, "SELECT * FROM " + t.table + " ORDER BY " + t.key));

  // Each pipeline is a .sel file beside this one: (title, file).
  const std::vector<std::pair<std::string, std::string>> pipelines = {
      {"gifts with a margin of 40% or more", "gifts-by-margin.sel"},
      {"gross revenue and shipping per country", "gross-per-country.sel"},
      {"words in the titles of the better-margin products", "title-words.sel"},
  };

  for (std::size_t n = 1; n <= pipelines.size(); ++n) {
    const auto& [title, file] = pipelines[n - 1];
    // EXAMPLE-BEGIN run
    const sel::Program program = sel::compile(read("examples/sql-functions/" + file));
    const HybridPlan plan = Sql::plan_hybrid(program, "postgresql", schema);
    const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                                plan.pure_memory ? tables : sel::Value::none());
    // EXAMPLE-END run
    const char* kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    std::cout << n << ". " << title << "\n";
    std::cout << "   plan        " << kind << "\n";
    if (plan.sql_statement) {
      std::cout << "   sql         " << plan.sql_statement->as_statement() << "\n";
      const std::vector<std::string>& caveats = plan.sql_statement->caveats();
      std::cout << "   caveats     " << (caveats.empty() ? "(none)" : join(caveats, ", ")) << "\n";
    }
    std::cout << db::render(rows, "   | ") << "\n";
    sel::Value copy = tables.clone();
    std::cout << "   in memory   "
              << (program.run(copy).dump() == rows.dump() ? "same rows" : "DIFFERENT") << "\n";
  }

  // 4 - what strict translation says ---------------------------------------------------------
  // A spelling is the application's promise, not this layer's, so strict mode --
  // exact or nothing -- refuses it.

  std::cout << pipelines.size() + 1 << ". strict translation\n";
  // EXAMPLE-BEGIN strict
  const sel::Program rule = sel::compile("SLUG(TITLE) $== \"cast-iron-pan\"");
  const Bindings title({{"TITLE", Binding::column("title", "p", SqlKind::Text)}});
  std::cout << "   caveats     " << join(Sql::translate(rule, "postgresql", title).caveats(), ", ")
            << "\n";
  try {
    Sql::translate(rule, "postgresql", title, sel::sql::Options{.strict = true});
  } catch (const SqlError& e) {
    std::cout << "   strict      " << e.code() << "\n";
  }
  // EXAMPLE-END strict
  return 0;
}
