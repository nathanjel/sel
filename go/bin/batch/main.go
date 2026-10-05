// Runs a corpus of SEL programs and prints one canonical line each.
// Matches tools/run-batch.mjs and cpp/bin/batch.cpp.

package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/sel"
)

func render(v *sel.Value) string {
	if v.Size() == 0 {
		if v.Kind == sel.KindText {
			return v.Scalar()
		}
		if v.Kind == sel.KindBool {
			if v.AsBool(sel.Pos{}) {
				return "TRUE"
			}
			return "FALSE"
		}
		if v.Kind == sel.KindBin {
			return "bin:" + v.Dump()[1:]
		}
	}
	return v.Dump()
}

func main() {
	show := false
	path := ""
	for _, arg := range os.Args[1:] {
		if arg == "--show" {
			show = true
		} else {
			path = arg
		}
	}

	if path == "" {
		fmt.Fprintln(os.Stderr, "usage: batch [--show] corpus.selc")
		os.Exit(2)
	}

	corpus := harness.SplitCorpus(harness.ReadFile(path))
	if len(corpus) == 0 {
		harness.Fatalf("batch: %s holds no records (a record starts with a line beginning \"### \")", path)
	}
	lines := make([]string, len(corpus))

	for i, src := range corpus {
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*sel.SelError); ok {
						if show {
							lines[i] = "!" + se.Code
						} else {
							lines[i] = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
						}
						return
					}
					lines[i] = fmt.Sprintf("!HOST panic: %v", r)
				}
			}()

			prog, err := sel.Compile(src)
			if err != nil {
				se := err.(*sel.SelError)
				if show {
					lines[i] = "!" + se.Code
				} else {
					lines[i] = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
				}
				return
			}
			val, err := prog.Run(sel.NewNone())
			if err != nil {
				se := err.(*sel.SelError)
				if show {
					lines[i] = "!" + se.Code
				} else {
					lines[i] = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
				}
				return
			}
			if show {
				lines[i] = render(val)
			} else {
				lines[i] = val.Dump()
			}
		}()
	}

	var sb strings.Builder
	for i, l := range lines {
		if i > 0 {
			sb.WriteByte('\n')
		}
		sb.WriteString(strings.ReplaceAll(l, "\n", "\\n"))
	}
	sb.WriteByte('\n')
	os.Stdout.WriteString(sb.String())
}
