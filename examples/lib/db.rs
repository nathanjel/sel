// A database runner for the SQL examples -- Rust.
//
//   #[path = "../lib/db.rs"] mod db;      and build with `-p sel-lang-dev --features usage`
//
// Every example that talks to a database goes through the functions below, and
// the files beside this one do the same with their own drivers. The contract is
// small on purpose, because it is what makes every host print the same thing:
//
//   - connect(dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
//     environment (tools/check-usage.sh sets them).
//   - query(conn, sql, params) runs a statement whose placeholders are `?` --
//     the spelling SEL's Mode::Params emits -- and returns the rows as a SEL
//     Value: a list of records, every column TEXT and SQL NULL as NULL. Money
//     stays text, as it does everywhere in SEL; a float reaching here is an
//     error.
//   - runner(conn) is query() in the shape sql::execute_hybrid() wants.
//   - render(rows, pad) prints rows as `field=value` lines. It is written in
//     SEL, so it prints the same bytes on every host by construction. It lives
//     in render.rs, which needs no driver (examples/memory-complex uses it
//     alone), and is re-exported here.
//
// Three crates, three ways to be handed parameters, and each is asked for the
// database's own text:
//
//   - rusqlite binds `?` as it is; an integer is printed in decimal.
//   - The postgres crate reads every result column in binary, and a NUMERIC or
//     a DATE has no text in it that is the server's. So the statement is sent
//     through simple_query, whose rows are the server's own text, as libpq's
//     are, and each parameter is written into it as an E'...' literal. A
//     quoted literal is of unknown type, exactly like an untyped PQexecParams
//     parameter, so PostgreSQL infers the same types either way. The $n form is
//     prepared first, which is how the column types are known (a float is
//     refused) and checks the statement the way a parameterised one is.
//   - The mysql crate's text protocol takes no parameters at all, so each one is
//     sent as a quoted literal escaped by the crate's own escaper -- what
//     db.cpp's mysql_real_escape_string, PDO's emulated prepares and PyMySQL do.
//
// In both rewrites a `?` counts only outside a quoted literal or identifier (a
// numeric guard's regex literal has `?` in it, which is why this cannot be a
// plain replace), and in MariaDB's single-quoted strings a backslash escapes the
// next character.

// Every example uses its own part of the runner.
#![allow(dead_code, unused_imports)]

#[path = "render.rs"]
mod render;
pub use render::render;

use std::env;
use std::error::Error;

use sel_lang::{Pos, SelError, Value};

pub type DbError = Box<dyn Error>;

fn var(name: &str) -> Result<String, DbError> {
    env::var(name).map_err(|_| format!("{name} is not set").into())
}

// EXAMPLE-BEGIN runner
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
// EXAMPLE-END runner
