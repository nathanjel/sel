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
				lines[i] = harness.Show(val)
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
