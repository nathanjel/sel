package harness

import "github.com/nathanjel/sel/go/sel"

// Show is how `sel` prints a result (docs/usage/repl.md): a scalar text bare, a
// boolean as TRUE or FALSE, a binary as `bin:<hex>`, anything else as its dump.
// One copy, used by bin/sel and by bin/batch --show, so a documentation example
// pasted into the CLI prints exactly what the documentation claims.
func Show(v *sel.Value) string {
	if v.Size() == 0 {
		switch v.Kind() {
		case sel.KindText:
			return v.Scalar()
		case sel.KindBool:
			if v.AsBool(sel.Pos{}) {
				return "TRUE"
			}
			return "FALSE"
		case sel.KindBin:
			return "bin:" + v.Dump()[1:]
		}
	}
	return v.Dump()
}
