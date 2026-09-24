// A database runner for the SQL examples -- C++. See db.hpp for the contract.
//
// Links libpq, libmariadb and libsqlite3 (-lpq -lmariadb -lsqlite3); cpp/Makefile
// builds the examples that need them only on request, because a development box
// is not expected to carry the three client libraries (tools/usage.Dockerfile
// does).
//
// None of the three C clients takes a `?` the same way. SQLite binds it as it
// is. libpq wants $1..$n, so the statement is renumbered. libmariadb's text
// protocol takes no parameters at all, so each one is sent as a quoted literal
// the client library escapes -- what PDO's emulated prepares and PyMySQL do. In
// both rewrites a `?` counts only outside a quoted literal or identifier (a
// numeric guard's regex literal has `?` in it, which is why this cannot be a
// plain replace), and in MariaDB's single-quoted strings a backslash escapes
// the next character.

#include "db.hpp"

#include <libpq-fe.h>
#include <mysql/mysql.h>
#include <sqlite3.h>

#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace db {

// EXAMPLE-BEGIN runner
struct Connection::Impl {
  PGconn* pg = nullptr;
  MYSQL* maria = nullptr;
  sqlite3* lite = nullptr;

  ~Impl() {
    if (pg) PQfinish(pg);
    if (maria) mysql_close(maria);
    if (lite) sqlite3_close(lite);
  }
};

namespace {

std::string env(const char* name, const char* fallback = nullptr) {
  const char* value = std::getenv(name);
  if (value) return value;
  if (fallback) return fallback;
  throw std::runtime_error(std::string(name) + " is not set");
}

}  // namespace

Connection connect(const std::string& dialect) {
  auto impl = std::make_shared<Connection::Impl>();
  if (dialect == "sqlite") {
    if (sqlite3_open(env("SEL_DB_SQLITE_FILE").c_str(), &impl->lite) != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(impl->lite));
    return Connection(impl);
  }
  const std::string host = env("SEL_DB_HOST", "127.0.0.1"), user = env("SEL_DB_USER"),
                    password = env("SEL_DB_PASSWORD"), name = env("SEL_DB_NAME");
  if (dialect == "postgresql") {
    const std::string port = env("SEL_DB_POSTGRESQL_PORT");
    const char* keys[] = {"host", "port", "user", "password", "dbname", "client_encoding",
                          nullptr};
    const char* values[] = {host.c_str(), port.c_str(), user.c_str(), password.c_str(),
                            name.c_str(), "UTF8", nullptr};
    impl->pg = PQconnectdbParams(keys, values, 0);
    if (PQstatus(impl->pg) != CONNECTION_OK) throw std::runtime_error(PQerrorMessage(impl->pg));
    return Connection(impl);
  }
  if (dialect == "mariadb") {
    impl->maria = mysql_init(nullptr);
    mysql_options(impl->maria, MYSQL_SET_CHARSET_NAME, "utf8mb4");
    if (!mysql_real_connect(impl->maria, host.c_str(), user.c_str(), password.c_str(),
                            name.c_str(), std::stoi(env("SEL_DB_MARIADB_PORT")), nullptr, 0))
      throw std::runtime_error(mysql_error(impl->maria));
    return Connection(impl);
  }
  throw std::invalid_argument("no runner for " + dialect);
}

namespace {

// The statement cut at every `?` that is a placeholder: n placeholders, n + 1
// pieces.
std::vector<std::string> split_at_placeholders(const std::string& sql, bool backslash_escapes) {
  std::vector<std::string> pieces(1);
  char quote = 0;
  for (std::size_t i = 0; i < sql.size(); ++i) {
    const char c = sql[i];
    if (quote) {
      if (c == '\\' && quote == '\'' && backslash_escapes && i + 1 < sql.size()) {
        pieces.back() += sql.substr(i++, 2);
        continue;
      }
      if (c == quote) quote = 0;
    } else if (c == '\'' || c == '"' || c == '`') {
      quote = c;
    } else if (c == '?') {
      pieces.emplace_back();
      continue;
    }
    pieces.back() += c;
  }
  return pieces;
}

void check_count(const std::vector<std::string>& pieces, const std::vector<sel::Value>& params) {
  if (pieces.size() - 1 != params.size())
    throw std::runtime_error("the statement has " + std::to_string(pieces.size() - 1) +
                             " placeholders and " + std::to_string(params.size()) + " values");
}

// Every cell is TEXT as the database printed it, or NULL.
sel::Value cell(const char* text, std::size_t length, bool is_float) {
  if (!text) return sel::Value::null();
  if (is_float) throw std::runtime_error("a float reached SEL; declare the column DECIMAL or TEXT");
  return sel::Value::text(std::string(text, length));
}

sel::Value query_postgresql(PGconn* pg, const std::string& sql,
                            const std::vector<sel::Value>& params) {
  const std::vector<std::string> pieces = split_at_placeholders(sql, false);
  check_count(pieces, params);
  std::string numbered = pieces[0];
  for (std::size_t i = 1; i < pieces.size(); ++i)
    numbered += "$" + std::to_string(i) + pieces[i];
  std::vector<const char*> values;
  for (const sel::Value& p : params) values.push_back(p.is_null() ? nullptr : p.as_text().c_str());

  std::unique_ptr<PGresult, void (*)(PGresult*)> result(
      PQexecParams(pg, numbered.c_str(), static_cast<int>(values.size()), nullptr,
                   values.data(), nullptr, nullptr, 0),
      PQclear);
  if (PQresultStatus(result.get()) != PGRES_TUPLES_OK)
    throw std::runtime_error(PQresultErrorMessage(result.get()));
  const int columns = PQnfields(result.get());
  sel::Value rows = sel::Value::none();
  for (int r = 0; r < PQntuples(result.get()); ++r) {
    sel::Value record = sel::Value::none();
    for (int c = 0; c < columns; ++c) {
      const Oid type = PQftype(result.get(), c);   // float4, float8
      record.set(PQfname(result.get(), c),
                 cell(PQgetisnull(result.get(), r, c) ? nullptr : PQgetvalue(result.get(), r, c),
                      PQgetlength(result.get(), r, c), type == 700 || type == 701));
    }
    rows.set(std::to_string(r + 1), record);
  }
  return rows;
}

sel::Value query_mariadb(MYSQL* maria, const std::string& sql,
                         const std::vector<sel::Value>& params) {
  const std::vector<std::string> pieces = split_at_placeholders(sql, true);
  check_count(pieces, params);
  std::string inlined = pieces[0];
  for (std::size_t i = 1; i < pieces.size(); ++i) {
    const sel::Value& p = params[i - 1];
    if (p.is_null()) {
      inlined += "NULL";
    } else {
      const std::string& text = p.as_text();
      std::string escaped(text.size() * 2 + 1, '\0');
      escaped.resize(mysql_real_escape_string(maria, escaped.data(), text.data(), text.size()));
      inlined += "'" + escaped + "'";
    }
    inlined += pieces[i];
  }

  if (mysql_real_query(maria, inlined.data(), inlined.size()) != 0)
    throw std::runtime_error(mysql_error(maria));
  std::unique_ptr<MYSQL_RES, void (*)(MYSQL_RES*)> result(mysql_store_result(maria),
                                                          mysql_free_result);
  if (!result) throw std::runtime_error(mysql_error(maria));
  const unsigned columns = mysql_num_fields(result.get());
  const MYSQL_FIELD* fields = mysql_fetch_fields(result.get());
  sel::Value rows = sel::Value::none();
  std::size_t n = 0;
  while (MYSQL_ROW row = mysql_fetch_row(result.get())) {
    const unsigned long* lengths = mysql_fetch_lengths(result.get());
    sel::Value record = sel::Value::none();
    for (unsigned c = 0; c < columns; ++c)
      record.set(fields[c].name, cell(row[c], lengths[c], fields[c].type == MYSQL_TYPE_FLOAT ||
                                                          fields[c].type == MYSQL_TYPE_DOUBLE));
    rows.set(std::to_string(++n), record);
  }
  return rows;
}

sel::Value query_sqlite(sqlite3* lite, const std::string& sql,
                        const std::vector<sel::Value>& params) {
  sqlite3_stmt* raw = nullptr;
  if (sqlite3_prepare_v2(lite, sql.c_str(), static_cast<int>(sql.size()), &raw, nullptr) != SQLITE_OK)
    throw std::runtime_error(sqlite3_errmsg(lite));
  std::unique_ptr<sqlite3_stmt, int (*)(sqlite3_stmt*)> stmt(raw, sqlite3_finalize);
  if (static_cast<std::size_t>(sqlite3_bind_parameter_count(raw)) != params.size())
    throw std::runtime_error("the statement's placeholders and values do not match");
  for (std::size_t i = 0; i < params.size(); ++i) {
    const int slot = static_cast<int>(i + 1);
    if (params[i].is_null()) {
      sqlite3_bind_null(raw, slot);
    } else {
      const std::string& text = params[i].as_text();
      sqlite3_bind_text(raw, slot, text.data(), static_cast<int>(text.size()), SQLITE_TRANSIENT);
    }
  }
  const int columns = sqlite3_column_count(raw);
  sel::Value rows = sel::Value::none();
  std::size_t n = 0;
  int step;
  while ((step = sqlite3_step(raw)) == SQLITE_ROW) {
    sel::Value record = sel::Value::none();
    for (int c = 0; c < columns; ++c) {
      const int type = sqlite3_column_type(raw, c);
      const auto* text = reinterpret_cast<const char*>(sqlite3_column_text(raw, c));
      record.set(sqlite3_column_name(raw, c),
                 cell(type == SQLITE_NULL ? nullptr : text,
                      static_cast<std::size_t>(sqlite3_column_bytes(raw, c)),
                      type == SQLITE_FLOAT));
    }
    rows.set(std::to_string(++n), record);
  }
  if (step != SQLITE_DONE) throw std::runtime_error(sqlite3_errmsg(lite));
  return rows;
}

}  // namespace

sel::Value query(const Connection& conn, const std::string& sql,
                 const std::vector<sel::Value>& params) {
  Connection::Impl& c = conn.impl();
  if (c.pg) return query_postgresql(c.pg, sql, params);
  if (c.maria) return query_mariadb(c.maria, sql, params);
  return query_sqlite(c.lite, sql, params);
}

sel::sql::Sql::DbRunner runner(const Connection& conn) {
  return [conn](const std::string& sql, const std::vector<sel::Value>& params) {
    return query(conn, sql, params);
  };
}
// EXAMPLE-END runner

}  // namespace db
