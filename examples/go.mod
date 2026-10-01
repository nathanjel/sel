// The worked examples in Go: examples/<category>/go.go, each a package main,
// and examples/lib, what the database-backed ones share. The library is the one
// in this repository (the replace below), so the examples build against the
// code they document.
//
// The database drivers are NOT required here: go.usage.mod lists them, and
// tools/check-usage.sh builds the LIVE categories with `-modfile=go.usage.mod
// -tags usage` in its image. Everything else builds offline with this file
// alone (go/Makefile's examples target). So do not `go mod tidy` with this
// file -- tidy reads every build tag and would add the drivers here; run it as
// `go mod tidy -modfile=go.usage.mod` instead.

module github.com/nathanjel/sel/examples

go 1.22

require github.com/nathanjel/sel/go v0.0.0

replace github.com/nathanjel/sel/go => ../go
