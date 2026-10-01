// Package db is what the Go worked examples share: Render, below, and the
// database runner in db.go.
//
// Render(rows, pad) prints rows as `field=value` lines. It is written in SEL,
// so it prints the same bytes on every host by construction. This file needs
// no database driver, so examples/memory-complex builds without them; db.go is
// built only with `-tags usage` (tools/check-usage.sh does that, with
// examples/go.usage.mod).
//
// A Go package is a directory, and examples/lib holds the other hosts' versions
// (db.cpp among them, which Go would take for a cgo source), so the Go one is a
// directory of its own: import "github.com/nathanjel/sel/examples/lib/db".
package db

import "github.com/nathanjel/sel/go/sel"

// A Program is immutable and safe to share, so it is compiled once.
var render = sel.MustCompile(`JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & "=" & (_ ?? "NULL")), "  ")), "\n")`)

// Render prints rows -- a list of records -- one `field=value` line per row,
// each line starting with pad.
func Render(rows *sel.Value, pad string) (string, error) {
	ctx := sel.NewNone()
	ctx.Set("ROWS", rows)
	ctx.Set("PAD", sel.NewText(pad))
	out, err := render.Run(ctx)
	if err != nil {
		return "", err
	}
	return out.AsText(sel.Pos{}), nil
}
