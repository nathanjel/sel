// Package harness holds what the runners under go/bin share: reading an input
// file under the runner contract (tools/README.md) and splitting a corpus into
// its records. It is not part of the module's API.
package harness

import (
	"fmt"
	"os"
	"strings"
)

// ReadFile returns the file's bytes as text, untranslated. A path that cannot be
// read — missing, unreadable, a directory — ends the run: exit status 1 and one
// line, `cannot read <path>: <reason>`, on stderr.
func ReadFile(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		Fatalf("cannot read %s: %v", path, unwrapPath(err))
	}
	return string(data)
}

// Fatalf writes one line to stderr, prefixed with nothing, and exits 1.
func Fatalf(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

// unwrapPath drops the path os repeats in its own message.
func unwrapPath(err error) error {
	if pe, ok := err.(*os.PathError); ok {
		return pe.Err
	}
	return err
}

// SplitCorpus splits a corpus into its record bodies (tools/README.md). A line
// that begins `### ` opens a record; its body is every line up to the next
// marker, joined with "\n", with exactly one trailing "\n" removed. The text is
// split on "\n" alone: a CR anywhere, before an LF included, is program text,
// and a record that ends in a blank line keeps the line break before it.
func SplitCorpus(text string) []string {
	var records [][]string
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "### ") {
			records = append(records, []string{})
			continue
		}
		if len(records) > 0 {
			last := len(records) - 1
			records[last] = append(records[last], line)
		}
	}
	out := make([]string, len(records))
	for i, lines := range records {
		out[i] = strings.TrimSuffix(strings.Join(lines, "\n"), "\n")
	}
	return out
}
