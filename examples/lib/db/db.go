//go:build usage

// A database runner for the SQL examples -- Go.
//
//	import "github.com/nathanjel/sel/examples/lib/db"      and build with
//	go build -modfile=go.usage.mod -tags usage,libsqlite3   (from examples/)
//
// Every example that talks to a database goes through the functions below, and
// the files in examples/lib do the same with their own drivers. The contract is
// small on purpose, because it is what makes every host print the same thing:
//
//   - Connect(dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
//     environment (tools/check-usage.sh sets them).
//   - Query(conn, sql, params) runs a statement whose placeholders are `?` --
//     the spelling SEL's params mode emits -- and returns the rows as a SEL
//     Value: a list of records, every column TEXT and SQL NULL as NULL. Money
//     stays text, as it does everywhere in SEL; a float reaching here is an
//     error.
//   - Runner(conn) is Query in the shape sql.ExecuteHybrid wants.
//   - Render(rows, pad), in render.go, prints rows as `field=value` lines.
//
// Three drivers, and each is asked for the database's own text:
//
//   - PostgreSQL goes through pgconn, pgx's wire layer, rather than
//     database/sql: ExecParams with every parameter as untyped text and every
//     result column in text format is exactly libpq's PQexecParams (db.cpp), so
//     the server infers the same types and prints its own NUMERIC and DATE.
//     The field descriptions say which columns are floats, which are refused.
//   - go-sql-driver/mysql sends a statement with no arguments over the text
//     protocol, whose cells are the server's own text. So each parameter is
//     written into the statement as a quoted literal, escaped the way
//     mysql_real_escape_string does it (or doubled quotes only, under
//     NO_BACKSLASH_ESCAPES) -- what db.cpp, PDO's emulated prepares and
//     PyMySQL do. The driver's own interpolation is not used: it counts every
//     `?`, quoted or not.
//   - mattn/go-sqlite3, built with the libsqlite3 tag so it links the system
//     SQLite every other host there shares, binds `?` as it is. An integer
//     cell is printed in decimal; a float, or a date the driver has parsed
//     into a time.Time, is refused rather than reprinted.
//
// For PostgreSQL and MariaDB a `?` counts only outside a quoted literal or
// identifier (a numeric guard's regex literal has `?` in it, which is why this
// cannot be a plain replace), and in MariaDB's single-quoted strings a
// backslash escapes the next character.
package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/go-sql-driver/mysql"
	"github.com/jackc/pgx/v5/pgconn"
	_ "github.com/mattn/go-sqlite3"
	"github.com/nathanjel/sel/go/sel"
	selsql "github.com/nathanjel/sel/go/sel/sql"
)

func env(name string) (string, error) {
	v, ok := os.LookupEnv(name)
	if !ok {
		return "", fmt.Errorf("%s is not set", name)
	}
	return v, nil
}

// EXAMPLE-BEGIN runner
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

// EXAMPLE-END runner
