// A database runner for the SQL examples -- C++.
//
//   #include "../lib/db.hpp"      and link ../lib/db.cpp with -lpq -lmariadb -lsqlite3
//
// Every example that talks to a database goes through the functions below, and
// the four files beside this one do the same with their own drivers. The
// contract is small on purpose, because it is what makes five hosts print the
// same thing:
//
//   - connect(dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
//     environment (tools/check-usage.sh sets them).
//   - query(conn, sql, params) runs a statement whose placeholders are `?` --
//     the spelling SEL's Mode::Params emits -- and returns the rows as a SEL
//     Value: a list of records, every column TEXT and SQL NULL as NULL. Money
//     stays text, as it does everywhere in SEL; a float reaching here is an
//     error.
//   - runner(conn) is query() in the shape Sql::execute_hybrid() wants.
//   - render(rows, pad) prints rows as `field=value` lines. It is written in
//     SEL, so it prints the same bytes on every host by construction.
//
// The three drivers live in db.cpp, and nothing here names them: this header
// needs SEL alone. render() is defined here, inline, for the same reason -- it
// needs nothing but the evaluator, so an example that renders rows it never
// fetched (examples/memory-complex) includes this file and links no database
// client at all.

#ifndef SEL_EXAMPLES_DB_HPP
#define SEL_EXAMPLES_DB_HPP

#include "../../cpp/sel.hpp"
#include "../../cpp/sel_sql.hpp"

#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace db {

// One open database. A handle: copies share the connection, and the last one
// closes it.
class Connection {
 public:
  struct Impl;                  // the driver's handle; defined in db.cpp
  explicit Connection(std::shared_ptr<Impl> impl) : impl_(std::move(impl)) {}
  Impl& impl() const { return *impl_; }

 private:
  std::shared_ptr<Impl> impl_;
};

// "postgresql", "mariadb" or "sqlite". Throws std::runtime_error when the
// server cannot be reached, and std::invalid_argument for any other dialect.
Connection connect(const std::string& dialect);

// Placeholders are `?`; params bind in order, each as its text, NULL as NULL.
sel::Value query(const Connection& conn, const std::string& sql,
                 const std::vector<sel::Value>& params = {});

sel::sql::Sql::DbRunner runner(const Connection& conn);

inline std::string render(const sel::Value& rows, const std::string& pad = "") {
  static const sel::Program program = sel::compile(
      "JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & \"=\" & (_ ?? \"NULL\")), \"  \")), \"\\n\")");
  sel::Value ctx = sel::Value::none();
  ctx.set("ROWS", rows);
  ctx.set("PAD", sel::Value::text(pad));
  return program.run(ctx).as_text();
}

}  // namespace db

#endif  // SEL_EXAMPLES_DB_HPP
