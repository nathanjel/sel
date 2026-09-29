// Runs a corpus of SEL programs and prints one canonical line each.
// Matches tools/run-batch.mjs and cpp/bin/batch.cpp.

package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

func readCorpus(scanner *bufio.Scanner) []string {
	var records [][]string
	var cur []string
	started := false

	// A corpus line is a whole program, and the stress corpus has programs of
	// megabytes: the scanner's default 64 KB token limit ended the read silently
	// and every record after it (or all of them) vanished as an E_SYNTAX.
	scanner.Buffer(make([]byte, 0, 1<<20), 1<<30)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "### ") {
			if started {
				records = append(records, cur)
			}
			cur = nil
			started = true
			continue
		}
		if started {
			cur = append(cur, line)
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "cannot read corpus: %v\n", err)
		os.Exit(2)
	}
	if started {
		records = append(records, cur)
	}

	out := make([]string, len(records))
	for i, lines := range records {
		joined := strings.Join(lines, "\n")
		if strings.HasSuffix(joined, "\n") {
			joined = joined[:len(joined)-1]
		}
		out[i] = joined
	}
	return out
}

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

	f, err := os.Open(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "cannot read %s: %v\n", path, err)
		os.Exit(2)
	}
	defer f.Close()

	corpus := readCorpus(bufio.NewScanner(f))
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
