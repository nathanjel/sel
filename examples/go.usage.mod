// go.mod plus the database drivers of the LIVE worked examples
// (examples/lib/db/db.go, built with `-tags usage`). tools/check-usage.sh builds
// them in its image with `go build -modfile=go.usage.mod -tags usage,libsqlite3`;
// keep it tidy with `go mod tidy -modfile=go.usage.mod`. The drivers stay out of
// go.mod so the other examples build offline.

module github.com/nathanjel/sel/examples

go 1.25.0

require (
	github.com/go-sql-driver/mysql v1.10.1
	github.com/jackc/pgx/v5 v5.11.0
	github.com/mattn/go-sqlite3 v1.14.52
	github.com/nathanjel/sel/go v0.0.0
)

require (
	filippo.io/edwards25519 v1.2.0 // indirect
	github.com/jackc/pgpassfile v1.0.0 // indirect
	github.com/jackc/pgservicefile v0.0.0-20240606120523-5a60cdf6a761 // indirect
	golang.org/x/text v0.29.0 // indirect
)

replace github.com/nathanjel/sel/go => ../go
