# SQL pipelines

[SQL conditions](sql-conditions.md) push a rule into a `WHERE` clause. This page
goes further: a whole data-processing pipeline — filter, join, group, sort,
project — written once in SEL, answered by the database where it can be and in
memory where it cannot, with the same rows either way.

- [Relations and pipelines](#relations-and-pipelines)
- [Three kinds of plan](#three-kinds-of-plan)
- [Running a plan](#running-a-plan)
- [Where the planner splits, and why](#where-the-planner-splits-and-why)
- [The example data](#the-example-data)
- [Limits worth knowing](#limits-worth-knowing)

---

## Relations and pipelines

In memory, a table is a list of records in the context, and a pipeline is a chain
of ordinary functions over it:

```sel
ORDERS = LIST(RECORD("id", 1, "status", "paid", "total", 120.00), RECORD("id", 2, "status", "open", "total", 80.00), RECORD("id", 3, "status", "paid", "total", 45.50)); ORDERS .> FILTER(_["status"] $== "paid") .> SORT_BY(_["total"]) .> MAP(_["id"]) .> JOIN(",")  => 3,1
```

For the database, the application describes each table once as a **relation
binding** — its name, an alias, and a typed binding per field — and the very
same program becomes a statement. `translate_statement` renders a pipeline over
relations as one `SELECT`: `FILTER` is a `WHERE`, `LINK` an `INNER JOIN`,
`BUCKET` a `GROUP BY` (with a `FILTER` after it a `HAVING`), `MAP` the select
list, the sorts an `ORDER BY`, `TAKE` and `DROP` a `LIMIT` and `OFFSET`.

## Three kinds of plan

Not every pipeline can be SQL. `plan_hybrid` looks at the whole pipeline and
answers *how much* of it the database can take:

| Plan | The database runs | Memory runs |
|---|---|---|
| `pure_sql` | the whole pipeline, as one statement | nothing |
| `hybrid` | the longest prefix it can answer exactly | the rest, over the rows the prefix returned |
| `pure_memory` | nothing the planner can use — at most, the application loads the tables | the whole pipeline |

A plan also names the **physical tables** it reads (`source_tables`), so an
application can pick a connection or check a grant before running anything.
Planning needs the bindings and a dialect name, never a connection.

## Running a plan

`execute_hybrid(plan, runner, context)` runs a plan of any kind. The *runner* is
the application's: a function that takes a statement with `?` placeholders and
the values for them, and returns the rows as a SEL list of records. For a hybrid
plan the rows are handed to the in-memory remainder as `_INPUT`; for a
pure-memory plan nothing reaches the runner, and the program runs over
`context` — which must then hold the tables it reads.

Each host's examples share one small runner, over PostgreSQL, MariaDB and
SQLite, with every column coming back as text — SEL's numbers *are* text, so a
`NUMERIC(10,2)` arrives as `"12.50"` with its scale, and a floating-point column
is refused:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/lib/db.py#runner -->
```python
def connect(dialect):
    if dialect == 'sqlite':
        return sqlite3.connect(ENV('SEL_DB_SQLITE_FILE'))
    common = dict(host=ENV('SEL_DB_HOST', '127.0.0.1'), user=ENV('SEL_DB_USER'),
                  password=ENV('SEL_DB_PASSWORD'))
    if dialect == 'postgresql':
        import psycopg
        return psycopg.connect(port=int(ENV('SEL_DB_POSTGRESQL_PORT')),
                               dbname=ENV('SEL_DB_NAME'), autocommit=True, **common)
    if dialect == 'mariadb':
        import pymysql
        return pymysql.connect(port=int(ENV('SEL_DB_MARIADB_PORT')),
                               database=ENV('SEL_DB_NAME'), autocommit=True, **common)
    raise ValueError(f'no runner for {dialect}')


def placeholders(sql, backslash_escapes):
    out, quote, i = [], None, 0
    while i < len(sql):
        c = sql[i]
        if quote:
            if c == '\\' and quote == "'" and backslash_escapes:
                out.append(sql[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == '?':
            c = '%s'
        out.append('%%' if c == '%' else c)
        i += 1
    return ''.join(out)


def query(conn, sql, params=()):
    if not isinstance(conn, sqlite3.Connection):
        sql = placeholders(sql, backslash_escapes=not hasattr(conn, 'pgconn'))
    cur = conn.cursor()
    cur.execute(sql, [None if p.is_null() else p.as_text() for p in params])
    names = [d[0] for d in cur.description]
    rows = Value.none()
    for n, row in enumerate(cur.fetchall(), 1):
        record = Value.none()
        for name, cell in zip(names, row):
            record.set(name, Value.null() if cell is None else Value.text(_text(cell)))
        rows.set(str(n), record)
    return rows


def _text(cell):
    if isinstance(cell, float):
        raise TypeError('a float reached SEL; declare the column DECIMAL or TEXT')
    return format(cell, 'f') if isinstance(cell, Decimal) else str(cell)


def runner(conn):
    return lambda sql, params: query(conn, sql, params)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/lib/db.mjs#runner -->
```js
// A connection is { run(sql, values) -> { names, rows }, close() }, whatever
// the driver underneath: rows are arrays, so columns keep their order.
export async function connect(dialect) {
  if (dialect === 'sqlite') {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(ENV.SEL_DB_SQLITE_FILE);
    return {
      run: async (sql, values) => {
        const statement = db.prepare(sql);
        statement.setReadBigInts(true);
        statement.setReturnArrays(true);
        return { names: statement.columns().map((c) => c.name), rows: statement.all(...values) };
      },
      close: async () => db.close(),
    };
  }
  const common = { host: ENV.SEL_DB_HOST ?? '127.0.0.1', user: ENV.SEL_DB_USER,
                   password: ENV.SEL_DB_PASSWORD, database: ENV.SEL_DB_NAME };
  if (dialect === 'postgresql') {
    const { Client } = require('pg');
    const FLOATS = [700, 701];                        // float4, float8
    const client = new Client({ ...common, port: Number(ENV.SEL_DB_POSTGRESQL_PORT),
      types: { getTypeParser: (oid) => (FLOATS.includes(oid) ? Number : String) } });
    await client.connect();
    return {
      run: async (sql, values) => {
        const result = await client.query({ text: placeholders(sql), values, rowMode: 'array' });
        return { names: result.fields.map((f) => f.name), rows: result.rows };
      },
      close: () => client.end(),
    };
  }
  if (dialect === 'mariadb') {
    const mariadb = require('mariadb');
    const conn = await mariadb.createConnection({ ...common, port: Number(ENV.SEL_DB_MARIADB_PORT),
      rowsAsArray: true,
      typeCast: (column, next) => (['FLOAT', 'DOUBLE'].includes(column.type) ? next() : column.string()) });
    return {
      run: async (sql, values) => {
        const rows = await conn.query(sql, values);
        return { names: rows.meta.map((m) => m.name()), rows };
      },
      close: () => conn.end(),
    };
  }
  throw new Error(`no runner for ${dialect}`);
}

function placeholders(sql) {
  let out = '', quote = null, n = 0;
  for (const c of sql) {
    if (quote) {
      if (c === quote) quote = null;
    } else if ("'\"`".includes(c)) {
      quote = c;
    } else if (c === '?') {
      out += `$${++n}`;
      continue;
    }
    out += c;
  }
  return out;
}

export async function query(conn, sql, params = []) {
  const { names, rows } = await conn.run(sql, params.map((p) => (p.isNull() ? null : p.asText())));
  const out = Value.none();
  rows.forEach((row, n) => {
    const record = Value.none();
    names.forEach((name, i) => {
      record.set(name, row[i] === null ? Value.null() : Value.text(text(row[i])));
    });
    out.set(String(n + 1), record);
  });
  return out;
}

function text(cell) {
  if (typeof cell === 'number') {
    throw new TypeError('a float reached SEL; declare the column DECIMAL or TEXT');
  }
  return String(cell);
}

// executeHybrid() calls its runner synchronously, and node's drivers answer
// with a promise. A plan runs at most one statement, and the plan says which:
// so run it first, and hand executeHybrid a runner that returns those rows.
export async function runner(conn, plan) {
  const statement = plan.pureMemory ? null : plan.sqlStatement;
  const rows = statement && await query(conn, statement.asStatement('params'), statement.bindings());
  return () => rows;
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/lib/db.php#runner -->
```php
function connect(string $dialect): PDO
{
    if ($dialect === 'sqlite') {
        return new PDO('sqlite:' . env('SEL_DB_SQLITE_FILE'));
    }
    $dsn = match ($dialect) {
        'postgresql' => 'pgsql:port=' . env('SEL_DB_POSTGRESQL_PORT'),
        'mariadb'    => 'mysql:charset=utf8mb4;port=' . env('SEL_DB_MARIADB_PORT'),
        default      => throw new \InvalidArgumentException("no runner for $dialect"),
    };
    return new PDO($dsn . ';host=' . env('SEL_DB_HOST', '127.0.0.1') . ';dbname=' . env('SEL_DB_NAME'),
                   env('SEL_DB_USER'), env('SEL_DB_PASSWORD'));
}

/** @param list<Value> $params */
function query(PDO $conn, string $sql, array $params = []): Value
{
    $stmt = $conn->prepare($sql);
    $stmt->execute(array_map(fn (Value $p) => $p->isNull() ? null : $p->asText(), $params));
    $rows = Value::none();
    $n = 0;
    while (($row = $stmt->fetch(PDO::FETCH_ASSOC)) !== false) {
        $record = Value::none();
        foreach ($row as $name => $cell) {
            $record->set((string) $name, $cell === null ? Value::null() : Value::text(text($cell)));
        }
        $rows->set((string) ++$n, $record);
    }
    return $rows;
}

function text(int|string|float $cell): string
{
    if (is_float($cell)) {
        throw new \TypeError('a float reached SEL; declare the column DECIMAL or TEXT');
    }
    return (string) $cell;
}

function runner(PDO $conn): callable
{
    return fn (string $sql, array $params) => query($conn, $sql, $params);
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/lib/db.cpp#runner -->
```cpp
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
```

</details>
<details>
<summary>Rust</summary>

<!-- from: examples/lib/db.rs#runner -->
```rust
pub enum Connection {
    Postgresql(postgres::Client),
    Mariadb(mysql::Conn),
    Sqlite(rusqlite::Connection),
}

// "postgresql", "mariadb" or "sqlite".
pub fn connect(dialect: &str) -> Result<Connection, DbError> {
    if dialect == "sqlite" {
        return Ok(Connection::Sqlite(rusqlite::Connection::open(var("SEL_DB_SQLITE_FILE")?)?));
    }
    let host = env::var("SEL_DB_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let (user, password, name) = (var("SEL_DB_USER")?, var("SEL_DB_PASSWORD")?, var("SEL_DB_NAME")?);
    match dialect {
        "postgresql" => {
            let client = postgres::Config::new()
                .host(&host)
                .port(var("SEL_DB_POSTGRESQL_PORT")?.parse()?)
                .user(&user)
                .password(&password)
                .dbname(&name)
                .connect(postgres::NoTls)?; // client_encoding is always UTF8
            Ok(Connection::Postgresql(client))
        }
        "mariadb" => {
            let opts = mysql::OptsBuilder::new()
                .ip_or_hostname(Some(host))
                .tcp_port(var("SEL_DB_MARIADB_PORT")?.parse()?)
                .user(Some(user))
                .pass(Some(password))
                .db_name(Some(name))
                .prefer_socket(false)
                .init(vec!["SET NAMES utf8mb4"]);
            Ok(Connection::Mariadb(mysql::Conn::new(opts)?))
        }
        _ => Err(format!("no runner for {dialect}").into()),
    }
}

// The statement cut at every `?` that is a placeholder: n placeholders, n + 1
// pieces.
fn split_at_placeholders(sql: &str, backslash_escapes: bool) -> Vec<String> {
    let mut pieces = vec![String::new()];
    let mut quote: Option<char> = None;
    let mut chars = sql.chars();
    while let Some(c) = chars.next() {
        let piece = pieces.last_mut().expect("never empty");
        match quote {
            Some('\'') if c == '\\' && backslash_escapes => {
                piece.push(c);
                piece.extend(chars.next());
                continue;
            }
            Some(q) if c == q => quote = None,
            Some(_) => {}
            None if matches!(c, '\'' | '"' | '`') => quote = Some(c),
            None if c == '?' => {
                pieces.push(String::new());
                continue;
            }
            None => {}
        }
        piece.push(c);
    }
    pieces
}

fn check_count(pieces: &[String], params: &[Value]) -> Result<(), DbError> {
    if pieces.len() - 1 != params.len() {
        return Err(format!("the statement has {} placeholders and {} values", pieces.len() - 1, params.len()).into());
    }
    Ok(())
}

// Each parameter as its text, NULL as None.
fn texts(params: &[Value]) -> Result<Vec<Option<String>>, SelError> {
    params.iter().map(|p| if p.is_null() { Ok(None) } else { p.as_text(Pos::default()).map(Some) }).collect()
}

// Every cell is TEXT as the database printed it, or NULL.
fn cell(text: Option<&str>, is_float: bool) -> Result<Value, DbError> {
    match text {
        None => Ok(Value::null()),
        Some(_) if is_float => Err("a float reached SEL; declare the column DECIMAL or TEXT".into()),
        Some(text) => Ok(Value::text_owned(text.to_string())),
    }
}

// The pieces joined again, a rendering of each parameter between them.
fn inline(pieces: &[String], params: Vec<Option<String>>, literal: impl Fn(&str) -> String) -> String {
    let mut sql = pieces[0].clone();
    for (param, piece) in params.iter().zip(&pieces[1..]) {
        sql += &param.as_deref().map_or_else(|| "NULL".to_string(), &literal);
        sql += piece;
    }
    sql
}

fn query_postgresql(pg: &mut postgres::Client, sql: &str, params: &[Value]) -> Result<Value, DbError> {
    use postgres::types::Type;
    let pieces = split_at_placeholders(sql, false);
    check_count(&pieces, params)?;
    let mut numbered = pieces[0].clone();
    for (i, piece) in pieces[1..].iter().enumerate() {
        numbered += &format!("${}{piece}", i + 1);
    }
    let statement = pg.prepare(&numbered)?;
    let floats: Vec<bool> =
        statement.columns().iter().map(|c| *c.type_() == Type::FLOAT4 || *c.type_() == Type::FLOAT8).collect();
    let inlined = inline(&pieces, texts(params)?, |text| {
        format!("E'{}'", text.replace('\\', "\\\\").replace('\'', "''"))
    });

    let rows = Value::none();
    let mut n = 0;
    for message in pg.simple_query(&inlined)? {
        if let postgres::SimpleQueryMessage::Row(row) = message {
            let record = Value::none();
            for (c, column) in row.columns().iter().enumerate() {
                record.set(column.name(), cell(row.get(c), floats[c])?, Pos::default())?;
            }
            n += 1;
            rows.set(&n.to_string(), record, Pos::default())?;
        }
    }
    Ok(rows)
}

fn query_mariadb(maria: &mut mysql::Conn, sql: &str, params: &[Value]) -> Result<Value, DbError> {
    use mysql::consts::ColumnType::{MYSQL_TYPE_DOUBLE, MYSQL_TYPE_FLOAT};
    use mysql::prelude::Queryable;
    let pieces = split_at_placeholders(sql, true);
    check_count(&pieces, params)?;
    let no_backslash_escapes = maria.no_backslash_escape();
    let inlined = inline(&pieces, texts(params)?, |text| {
        mysql::Value::from(text).as_sql(no_backslash_escapes)
    });

    let result = maria.query_iter(inlined)?;
    let columns = result.columns().as_ref().to_vec();
    let rows = Value::none();
    let mut n = 0;
    for row in result {
        let row = row?;
        let record = Value::none();
        for (c, column) in columns.iter().enumerate() {
            let text = match row.as_ref(c) {
                Some(mysql::Value::Bytes(bytes)) => Some(std::str::from_utf8(bytes)?),
                Some(mysql::Value::NULL) | None => None,
                Some(other) => return Err(format!("the text protocol returned {other:?}").into()),
            };
            let is_float = matches!(column.column_type(), MYSQL_TYPE_FLOAT | MYSQL_TYPE_DOUBLE);
            record.set(&column.name_str(), cell(text, is_float)?, Pos::default())?;
        }
        n += 1;
        rows.set(&n.to_string(), record, Pos::default())?;
    }
    Ok(rows)
}

fn query_sqlite(lite: &rusqlite::Connection, sql: &str, params: &[Value]) -> Result<Value, DbError> {
    use rusqlite::types::ValueRef;
    let mut statement = lite.prepare(sql)?;
    if statement.parameter_count() != params.len() {
        return Err("the statement's placeholders and values do not match".into());
    }
    let names: Vec<String> = statement.column_names().into_iter().map(String::from).collect();
    let mut result = statement.query(rusqlite::params_from_iter(texts(params)?))?;
    let rows = Value::none();
    let mut n = 0;
    while let Some(row) = result.next()? {
        let record = Value::none();
        for (c, name) in names.iter().enumerate() {
            let value = match row.get_ref(c)? {
                ValueRef::Null => cell(None, false)?,
                ValueRef::Integer(i) => cell(Some(&i.to_string()), false)?,
                ValueRef::Real(r) => cell(Some(&r.to_string()), true)?,
                ValueRef::Text(bytes) | ValueRef::Blob(bytes) => cell(Some(std::str::from_utf8(bytes)?), false)?,
            };
            record.set(name, value, Pos::default())?;
        }
        n += 1;
        rows.set(&n.to_string(), record, Pos::default())?;
    }
    Ok(rows)
}

// Placeholders are `?`; params bind in order, each as its text, NULL as NULL.
pub fn query(conn: &mut Connection, sql: &str, params: &[Value]) -> Result<Value, DbError> {
    match conn {
        Connection::Postgresql(pg) => query_postgresql(pg, sql, params),
        Connection::Mariadb(maria) => query_mariadb(maria, sql, params),
        Connection::Sqlite(lite) => query_sqlite(lite, sql, params),
    }
}

// execute_hybrid() wants its runner's failure as a SelError, which carries a
// catalogued code; a database's is none of them, so it travels as E_BAD_ARG
// with the driver's message.
pub fn runner(conn: &mut Connection) -> impl FnMut(&str, &[Value]) -> Result<Value, SelError> + '_ {
    move |sql, params| {
        query(conn, sql, params)
            .map_err(|e| SelError::new("E_BAD_ARG", format!("the database refused the statement: {e}"), Pos::default()))
    }
}
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/lib/db/db.go#runner -->
```go
// In this file `sql` is the standard library's database/sql; SEL's own SQL
// layer, github.com/nathanjel/sel/go/sel/sql, is imported as selsql.

// Conn is a connection to one of the three databases.
type Conn struct {
	pg                 *pgconn.PgConn // PostgreSQL
	sql                *sql.DB        // MariaDB or SQLite, one connection
	mariadb            bool
	noBackslashEscapes bool
}

// Connect opens "postgresql", "mariadb" or "sqlite".
func Connect(dialect string) (*Conn, error) {
	if dialect == "sqlite" {
		file, err := env("SEL_DB_SQLITE_FILE")
		if err != nil {
			return nil, err
		}
		lite, err := sql.Open("sqlite3", file)
		if err != nil {
			return nil, err
		}
		lite.SetMaxOpenConns(1)
		return &Conn{sql: lite}, nil
	}
	host := os.Getenv("SEL_DB_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	var settings [4]string
	for i, name := range []string{"SEL_DB_USER", "SEL_DB_PASSWORD", "SEL_DB_NAME", "SEL_DB_" + strings.ToUpper(dialect) + "_PORT"} {
		v, err := env(name)
		if err != nil {
			return nil, err
		}
		settings[i] = v
	}
	user, password, name, port := settings[0], settings[1], settings[2], settings[3]
	switch dialect {
	case "postgresql":
		config, err := pgconn.ParseConfig("")
		if err != nil {
			return nil, err
		}
		p, err := strconv.ParseUint(port, 10, 16)
		if err != nil {
			return nil, err
		}
		config.Host, config.Port, config.User, config.Password, config.Database = host, uint16(p), user, password, name
		config.TLSConfig, config.Fallbacks = nil, nil // client_encoding is always UTF8
		pg, err := pgconn.ConnectConfig(context.Background(), config)
		if err != nil {
			return nil, err
		}
		return &Conn{pg: pg}, nil
	case "mariadb":
		config := mysql.NewConfig()
		config.User, config.Passwd, config.DBName = user, password, name
		config.Net, config.Addr = "tcp", host+":"+port
		config.Params = map[string]string{"charset": "utf8mb4"}
		maria, err := sql.Open("mysql", config.FormatDSN())
		if err != nil {
			return nil, err
		}
		maria.SetMaxOpenConns(1)
		var mode string
		if err := maria.QueryRow("SELECT @@SESSION.sql_mode").Scan(&mode); err != nil {
			return nil, err
		}
		return &Conn{sql: maria, mariadb: true,
			noBackslashEscapes: strings.Contains(mode, "NO_BACKSLASH_ESCAPES")}, nil
	}
	return nil, fmt.Errorf("no runner for %s", dialect)
}

// Close ends the connection.
func (c *Conn) Close() error {
	if c.pg != nil {
		return c.pg.Close(context.Background())
	}
	return c.sql.Close()
}

// splitAtPlaceholders cuts the statement at every `?` that is a placeholder:
// n placeholders, n + 1 pieces.
func splitAtPlaceholders(statement string, backslashEscapes bool) []string {
	pieces := []string{""}
	var quote rune
	runes := []rune(statement)
	for i := 0; i < len(runes); i++ {
		c := runes[i]
		last := &pieces[len(pieces)-1]
		switch {
		case quote == '\'' && c == '\\' && backslashEscapes && i+1 < len(runes):
			*last += string(runes[i : i+2])
			i++
			continue
		case quote != 0 && c == quote:
			quote = 0
		case quote == 0 && (c == '\'' || c == '"' || c == '`'):
			quote = c
		case quote == 0 && c == '?':
			pieces = append(pieces, "")
			continue
		}
		*last += string(c)
	}
	return pieces
}

// texts is each parameter's text, nil for NULL.
func texts(params []*sel.Value) []*string {
	out := make([]*string, len(params))
	for i, p := range params {
		if !p.IsNull() {
			text := p.AsText(sel.Pos{})
			out[i] = &text
		}
	}
	return out
}

// cell is a column's value as SEL holds it: TEXT as the database printed it,
// or NULL.
func cell(text *string, isFloat bool) (*sel.Value, error) {
	switch {
	case text == nil:
		return sel.NewNull(), nil
	case isFloat:
		return nil, errors.New("a float reached SEL; declare the column DECIMAL or TEXT")
	}
	return sel.NewText(*text), nil
}

// mariadbLiteral quotes a parameter the way mysql_real_escape_string does.
func mariadbLiteral(text string, noBackslashEscapes bool) string {
	if noBackslashEscapes {
		return "'" + strings.ReplaceAll(text, "'", "''") + "'"
	}
	escaped := strings.NewReplacer("\\", `\\`, "'", `\'`, `"`, `\"`, "\x00", `\0`,
		"\n", `\n`, "\r", `\r`, "\x1a", `\Z`).Replace(text)
	return "'" + escaped + "'"
}

func queryPostgresql(pg *pgconn.PgConn, statement string, params []*sel.Value) (*sel.Value, error) {
	pieces := splitAtPlaceholders(statement, false)
	if len(pieces)-1 != len(params) {
		return nil, fmt.Errorf("the statement has %d placeholders and %d values", len(pieces)-1, len(params))
	}
	numbered := pieces[0]
	for i, piece := range pieces[1:] {
		numbered += "$" + strconv.Itoa(i+1) + piece
	}
	values := make([][]byte, len(params))
	for i, text := range texts(params) {
		if text != nil {
			values[i] = []byte(*text)
		}
	}
	// No parameter types and text in both directions: PQexecParams.
	result := pg.ExecParams(context.Background(), numbered, values, nil, nil, nil).Read()
	if result.Err != nil {
		return nil, result.Err
	}
	rows := sel.NewNone()
	for n, row := range result.Rows {
		record := sel.NewNone()
		for c, column := range result.FieldDescriptions {
			var text *string
			if row[c] != nil {
				s := string(row[c])
				text = &s
			}
			isFloat := column.DataTypeOID == 700 || column.DataTypeOID == 701 // float4, float8
			value, err := cell(text, isFloat)
			if err != nil {
				return nil, err
			}
			record.Set(column.Name, value)
		}
		rows.Set(strconv.Itoa(n+1), record)
	}
	return rows, nil
}

func querySQL(c *Conn, statement string, params []*sel.Value) (*sel.Value, error) {
	var result *sql.Rows
	var err error
	if c.mariadb {
		pieces := splitAtPlaceholders(statement, true)
		if len(pieces)-1 != len(params) {
			return nil, fmt.Errorf("the statement has %d placeholders and %d values", len(pieces)-1, len(params))
		}
		inlined := pieces[0]
		for i, text := range texts(params) {
			if text == nil {
				inlined += "NULL"
			} else {
				inlined += mariadbLiteral(*text, c.noBackslashEscapes)
			}
			inlined += pieces[i+1]
		}
		result, err = c.sql.Query(inlined) // no arguments: the text protocol
	} else {
		args := make([]any, len(params))
		for i, text := range texts(params) {
			if text != nil {
				args[i] = *text
			}
		}
		result, err = c.sql.Query(statement, args...)
	}
	if err != nil {
		return nil, err
	}
	defer result.Close()
	columns, err := result.ColumnTypes()
	if err != nil {
		return nil, err
	}
	rows := sel.NewNone()
	for n := 1; result.Next(); n++ {
		raw := make([]any, len(columns))
		for i := range raw {
			raw[i] = new(any)
		}
		if err := result.Scan(raw...); err != nil {
			return nil, err
		}
		record := sel.NewNone()
		for i, column := range columns {
			var text *string
			isFloat := false
			switch v := (*raw[i].(*any)).(type) {
			case nil:
			case []byte:
				s := string(v)
				text = &s
				kind := column.DatabaseTypeName()
				isFloat = kind == "FLOAT" || kind == "DOUBLE"
			case string:
				text = &v
			case int64:
				s := strconv.FormatInt(v, 10)
				text = &s
			case float64:
				isFloat = true
				s := ""
				text = &s
			case time.Time:
				return nil, errors.New("the driver parsed a date; declare the column TEXT")
			default:
				return nil, fmt.Errorf("the driver returned %T", v)
			}
			value, err := cell(text, isFloat)
			if err != nil {
				return nil, err
			}
			record.Set(column.Name(), value)
		}
		rows.Set(strconv.Itoa(n), record)
	}
	return rows, result.Err()
}

// Query runs a statement whose placeholders are `?`; params bind in order,
// each as its text, NULL as NULL.
func Query(c *Conn, statement string, params []*sel.Value) (*sel.Value, error) {
	if c.pg != nil {
		return queryPostgresql(c.pg, statement, params)
	}
	return querySQL(c, statement, params)
}

// Runner is Query in the shape sql.ExecuteHybrid wants. A database's failure
// has no SEL error code of its own, so it travels as E_BAD_ARG with the
// driver's message.
func Runner(c *Conn) selsql.DbRunner {
	return func(statement string, params []*sel.Value) (*sel.Value, error) {
		rows, err := Query(c, statement, params)
		if err != nil {
			return nil, &sel.SelError{Code: "E_BAD_ARG", Message: "the database refused the statement: " + err.Error()}
		}
		return rows, nil
	}
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/lib/db.lisp#runner -->
```lisp
(defun connect (dialect)
  (let ((host (env "SEL_DB_HOST" "127.0.0.1"))
        (user (env "SEL_DB_USER"))
        (password (env "SEL_DB_PASSWORD"))
        (database (env "SEL_DB_NAME")))
    (cond ((string= dialect "sqlite")
           (sqlite:connect (env "SEL_DB_SQLITE_FILE")))
          ((string= dialect "postgresql")
           (postmodern:connect database user password host
                               :port (parse-integer (env "SEL_DB_POSTGRESQL_PORT"))))
          ((string= dialect "mariadb")
           (cl-mysql:connect :host host :user user :password password :database database
                             :port (parse-integer (env "SEL_DB_MARIADB_PORT"))))
          (t (error "no runner for ~a" dialect)))))

(defun placeholders (sql replacement backslash-escapes)
  "SQL with its Nth placeholder, counting from 0, replaced by (REPLACEMENT N)."
  (with-output-to-string (out)
    (let ((in-quote nil) (n 0) (i 0))
      (loop while (< i (length sql))
            do (let ((c (char sql i)))
                 (cond (in-quote
                        (cond ((and (char= c #\\) (char= in-quote #\') backslash-escapes
                                    (< (1+ i) (length sql)))
                               (write-char c out)
                               (setf c (char sql (incf i))))
                              ((char= c in-quote)
                               (setf in-quote nil)))
                        (write-char c out))
                       ((find c "'\"`")
                        (setf in-quote c)
                        (write-char c out))
                       ((char= c #\?)
                        (write-string (funcall replacement n) out)
                        (incf n))
                       (t (write-char c out)))
                 (incf i))))))

(defun param-text (p)
  "A bound SEL value as the driver takes it: its text, or NIL for NULL."
  (unless (sel:value-null-p p) (sel:as-text p)))

(defgeneric fetch (conn sql params)
  (:documentation "The rows of SQL, each an alist of column name and cell, where
a cell is a string, an integer or :NULL."))

(defmethod fetch ((db sqlite:sqlite-handle) sql params)
  (let ((statement (sqlite:prepare-statement db sql)))   ; `?` is SQLite's own
    (unwind-protect
         (let ((names (sqlite:statement-column-names statement)))
           (loop for p in params
                 for i from 1
                 do (sqlite:bind-parameter statement i (param-text p)))
           (loop while (sqlite:step-statement statement)
                 collect (loop for name in names
                               for i from 0
                               collect (cons name (or (sqlite:statement-column-value statement i)
                                                      :null)))))
      (sqlite:finalize-statement statement))))

(defparameter *pg-readtable*
  (let ((table (cl-postgres:copy-sql-readtable)))
    (dolist (oid (list cl-postgres-oid:+int2+ cl-postgres-oid:+int4+ cl-postgres-oid:+int8+
                       cl-postgres-oid:+numeric+ cl-postgres-oid:+date+))
      (cl-postgres:set-sql-reader oid nil :table table))   ; NIL: read it as text
    table)
  "cl-postgres's readers, less the ones that would turn a number or a date into
something other than the text PostgreSQL printed.")

(defmethod fetch ((conn cl-postgres:database-connection) sql params)
  (let ((cl-postgres:*sql-readtable* *pg-readtable*))
    (cl-postgres:prepare-query conn "" (placeholders sql (lambda (n) (format nil "$~D" (1+ n))) nil))
    (cl-postgres:exec-prepared conn "" (mapcar (lambda (p) (or (param-text p) :null)) params)
                               'cl-postgres:alist-row-reader)))

;;; cl-mysql reads a zero-length cell as NIL, so '' and NULL would come back
;;; alike. The rows are read here instead, where a NULL cell is a null pointer.
(cffi:defcfun ("mysql_fetch_row" mysql-fetch-row) :pointer (result :pointer))
(cffi:defcfun ("mysql_fetch_lengths" mysql-fetch-lengths) :pointer (result :pointer))

(defun mysql-literal (pool p)
  (if (sel:value-null-p p)
      "NULL"
      (format nil "'~a'" (cl-mysql:escape-string (sel:as-text p) :database pool))))

(defmethod fetch ((pool cl-mysql-system:connection-pool) sql params)
  (let ((conn (cl-mysql:query (placeholders sql (lambda (n) (mysql-literal pool (nth n params))) t)
                              :database pool :store nil)))
    (unwind-protect
         (progn
           (cl-mysql:next-result-set conn :store t :dont-release t)
           (let ((result (cl-mysql-system:result-set conn))
                 (fields (first (cl-mysql:result-set-fields conn))))   ; (name type flags)
             (loop for (name type) in fields
                   when (member type '(:float :double))
                     do (error "a float reached SEL (~a); declare the column DECIMAL or TEXT" name))
             (loop for row = (mysql-fetch-row result)
                   until (cffi:null-pointer-p row)
                   collect (loop with lengths = (mysql-fetch-lengths result)
                                 for (name) in fields
                                 for i from 0
                                 for cell = (cffi:mem-aref row :pointer i)
                                 collect (cons name
                                               (if (cffi:null-pointer-p cell)
                                                   :null
                                                   (cffi:foreign-string-to-lisp
                                                    cell :count (cffi:mem-aref lengths :unsigned-long i)
                                                         :encoding :utf-8)))))))
      (cl-mysql-system:release conn))))

(defun cell-text (cell)
  (etypecase cell
    (string cell)
    (integer (format nil "~D" cell))
    (float (error "a float reached SEL; declare the column DECIMAL or TEXT"))))

(defun query (conn sql &optional params)
  (let ((rows (sel:make-none)))
    (loop for row in (fetch conn sql params)
          for n from 1
          do (let ((record (sel:make-none)))
               (loop for (name . cell) in row
                     do (sel:value-set record name (if (eq cell :null)
                                                       (sel:make-null)
                                                       (sel:make-text (cell-text cell)))))
               (sel:value-set rows (format nil "~D" n) record)))
    rows))

(defun runner (conn)
  (lambda (sql params) (query conn sql params)))
```

</details>
<!-- /tabs -->

Two things differ by host, and the runners show both:

- **Placeholders.** SEL emits `?`. PDO, SQLite and the Node MariaDB driver take
  `?` as it is; libpq, node-postgres and postmodern want `$1`, `$2`, …; psycopg
  and PyMySQL want `%s`; libmariadb's text protocol and cl-mysql take no
  parameters at all, so the values are escaped into the statement the way PDO's
  emulated prepares do it. Rust and Go do the same for MariaDB (the text
  protocol is what returns the server's own text), and rusqlite and go-sqlite3
  take `?` as it is. For PostgreSQL, Go's pgconn takes `$1`, `$2`, … with
  untyped text values, as libpq does; the Rust runner prepares the `$n` form to
  learn the column types, then sends the statement with each value as an escaped
  `E'…'` literal through its simple-query protocol, which is where the postgres
  crate returns the server's own text. Each runner finds the placeholders with a
  scan that skips quoted text, because a translated statement can contain a `?`
  inside a literal — the numeric guard's regular expression has one.
- **Synchronous runners.** `execute_hybrid` calls the runner synchronously in
  every host. Node's database drivers are asynchronous, so the JavaScript runner
  runs the plan's one statement first and hands `executeHybrid` a function that
  returns the rows it already has.

## Where the planner splits, and why

The planner pushes a step down only when the database's answer is exactly SEL's.
Most of what keeps a step in memory is one of these:

- **No SQL spelling.** `SPLIT` and `RGROUPS` yield lists; `CRC32` has no
  PostgreSQL spelling; a regex has none on SQLite; a host function has none
  until the application [gives it one](sql-functions.md). The split lands before
  the step.
- **Rows SQL does not have.** After a `LINK`, SEL's row holds each side under its
  binder as well as the promoted fields; SQL's row has only the fields. So a
  joined row is only a split point once a `MAP` (or `SELECT_COLS`, or a
  projected `BUCKET`) has said which fields to keep — project before the split.
- **Groups SQL cannot see into.** Inside a `BUCKET` projection SQL can count and
  sum the group, nothing else; a projection that sorts, filters or picks within
  the group keeps the bucket in memory, over rows the database filtered and
  joined.
- **Identity.** Grouping and de-duplication compare values by SEL's identity —
  exact bytes, and `1` differs from `1.0`. A database compares by its collation
  and its numeric equality. When a value computed in SQL could be merged or split
  differently, the planner keeps the computation in memory (`CANON` and
  text-literal `IF` branches are the ways a rule proves identity).
- **Helper variables.** A pipeline may be written through helper assignments
  (`X = ORDERS; X .> TAKE(1)`); literal helpers are inlined, others are
  evaluated once, in memory, before the steps that read them.

A **pure-memory** plan still reports the tables it reads, and an application
that wants the database's data but not its opinion loads them — which is what
[the in-memory example](in-memory.md) does.

## The example data

Six small datasets — one per shape of schema, and one for the application's
own functions — each in the database the brief
asks of it and each with its own page. The seeds are in the example directories;
`tools/check-usage.sh` loads them into throwaway servers and runs every example
in every host against them.

| Page | Schema | Database | What it shows |
|---|---|---|---|
| [Star schema](sql-star.md) | a sales fact table and date, store and product dimensions | PostgreSQL | joins to dimensions and a `GROUP BY` in one statement; a per-group "best of" computed in memory over joined rows |
| [Entity–attribute–value](sql-eav.md) | entities and `(entity, name, value)` rows, every value text | SQLite | `EXISTS` subqueries per attribute; counting by value in SQL; pivoting and numeric tests on text in memory |
| [Third normal form](sql-3nf.md) | categories, products, customers, orders, order lines | PostgreSQL | a three-table join and aggregate in SQL; an application-defined hash (CRC32 cohorts) in memory |
| [Unnormalised](sql-flat.md) | one wide export table with repeated customers and tags in one column | MariaDB | grouping in SQL; normalisation and de-duplication kept out of MariaDB's collation; splitting a list column in memory |
| [Your own functions](sql-functions.md) | a shop with VAT rates, and the application's functions spelled as PostgreSQL stored functions | PostgreSQL | a list argument, SQL and PL/pgSQL functions inside joins and aggregates, a list-returning function kept in memory, strict mode |
| [Complex, in memory](in-memory.md) | a support desk: teams, customers, SLAs, tickets, events | PostgreSQL, then no database at all | a report no database can take a share of: SQL loads, SEL computes — and the same report over generated data |

Every example prints, for each pipeline, the plan, the tables it reads, the SQL,
the rows — and whether those rows are the rows the same program computes in
memory over the same tables. They always are; that line is how the examples
check themselves.

## Limits worth knowing

The planner refuses rather than guesses, so each of these is a pipeline that runs
correctly in memory, not a wrong answer — but knowing them helps a pipeline stay
in SQL:

- A row is qualified by the keys it has: its binders (`_["O"]["amount"]`,
  `_["_2"]["name"]`), never a relation's SQL alias or table name, and after a
  five-argument `LINK` not the relation's own name. Anything else is refused,
  as `run()` raises `E_NO_KEY` for it.
- After a `LINK_LEFT`, read a field only the right side has through the right
  binder (`_["C"]["name"]`, `NULL` where nothing matched). The promoted
  `_["name"]` is refused, because a row that matched nothing does not have it.
- A relation joined a second time under the alias its binding gives it — a
  self-join, or a chain back to it — is refused: SQL would see one alias twice.
  Bind the relation a second time under another alias.
- A group key of several fields (`BUCKET(RECORD("a", …, "b", …), proj)`) groups
  in SQL, but its parts cannot be projected from `_K`; group by one field, or
  project the parts in memory.
- A sort by the *text* group key directly after a `BUCKET` over a `LINK` is
  refused; sort by an aggregate, or let that last sort run in memory.

The binding constructors, the dialects, the output modes and every refusal code
are in the [SQL reference](../sql.md); the planner's full contract is
[its design document](../internals/sql-translation.md#121-hybrid-planning-the-contract).
