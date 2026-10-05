// Prints one verdict per pattern read from stdin, for
// tools/check-regex-ambiguity-diff.py: `A` when the pattern is in the portable
// subset, `R` when it is refused (E_REGEX_SYNTAX, or E_BAD_ARG for `i` on a
// non-ASCII pattern). An argument `i` checks the patterns under the i flag.
package main

import (
	"bufio"
	"fmt"
	"os"

	"github.com/nathanjel/sel/go/sel"
)

func verdict(p string, ic bool) (v string) {
	defer func() {
		if r := recover(); r != nil {
			if _, ok := r.(*sel.SelError); ok {
				v = "R"
				return
			}
			panic(r)
		}
	}()
	sel.ValidatePatternFlags(p, ic, sel.Pos{})
	return "A"
}

func main() {
	ic := len(os.Args) > 1 && os.Args[len(os.Args)-1] == "i"
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 0, 1<<20), 1<<28)
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	for sc.Scan() {
		fmt.Fprintln(w, verdict(sc.Text(), ic))
	}
}
