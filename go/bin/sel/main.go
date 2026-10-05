// SEL command line: evaluate an expression, a file, or start a REPL. Every host's
// CLI is held to one contract (docs/usage/repl.md, tools/check-cli-source.sh).

package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/internal/version"
	"github.com/nathanjel/sel/go/sel"
)

func report(e *sel.SelError) {
	fmt.Fprintf(os.Stderr, "%s at line %d column %d: %s\n", e.Code, e.Line(), e.Col(), e.Message)
}

const usage = `usage: sel -e EXPR          evaluate EXPR and print the result
       sel FILE             evaluate the program in FILE
       sel --deps -e EXPR   print the variables EXPR reads, one per line
       sel --deps FILE      the same for a file
       sel --functions      print the function table, one name per line
       sel --help, sel -h   print this text
       sel --version        print the version
       sel                  read programs from standard input, one per line
`

// usageError is a malformed command line: one line on stderr, exit status 2.
func usageError(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "sel: "+format+"\n", args...)
	os.Exit(2)
}

// run compiles and runs (or lists the dependencies of) one program, reporting a
// SelError, however it is raised, as the contract's one line.
func run(source string, root *sel.Value, deps bool) (ok bool) {
	defer func() {
		if r := recover(); r != nil {
			se, isSel := r.(*sel.SelError)
			if !isSel {
				panic(r)
			}
			report(se)
			ok = false
		}
	}()
	prog, err := sel.Compile(source)
	if err != nil {
		report(err.(*sel.SelError))
		return false
	}
	if deps {
		for _, d := range prog.Dependencies() {
			fmt.Println(d)
		}
		return true
	}
	val, err := prog.Run(root)
	if err != nil {
		report(err.(*sel.SelError))
		return false
	}
	fmt.Println(harness.Show(val))
	return true
}

// blank is a line of SEL whitespace only (space, TAB, CR, LF): not U+00A0, VT or
// U+3000, which are programs, and E_SYNTAX.
func blank(line string) bool {
	for i := 0; i < len(line); i++ {
		switch line[i] {
		case ' ', '\t', '\r', '\n':
		default:
			return false
		}
	}
	return true
}

func main() {
	deps, functions := false, false
	haveExpr := false
	var expr, file string
	argv := os.Args[1:]
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		switch {
		case a == "-h" || a == "--help":
			fmt.Print(usage)
			os.Exit(0)
		case a == "--version":
			fmt.Println("sel " + version.Version)
			os.Exit(0)
		case a == "--deps":
			deps = true
		case a == "--functions":
			functions = true
		case a == "-e":
			if i+1 >= len(argv) {
				usageError("-e needs an expression")
			}
			i++
			if haveExpr || file != "" {
				usageError("unexpected argument %s", argv[i])
			}
			expr, haveExpr = argv[i], true
		case len(a) > 1 && a[0] == '-':
			usageError("unknown option %s", a)
		default:
			if haveExpr || file != "" {
				usageError("unexpected argument %s", a)
			}
			file = a
		}
	}

	if functions {
		for _, name := range sel.FunctionNames() {
			fmt.Println(name)
		}
		os.Exit(0)
	}

	if file != "" {
		data, err := os.ReadFile(file)
		if err != nil {
			var pe *os.PathError
			if errors.As(err, &pe) {
				err = pe.Err
			}
			fmt.Fprintf(os.Stderr, "sel: cannot read %s: %v\n", file, err)
			os.Exit(1)
		}
		expr, haveExpr = string(data), true
	}
	if haveExpr {
		if !run(expr, sel.NewNone(), deps) {
			os.Exit(1)
		}
		os.Exit(0)
	}

	// REPL: one context for the whole session, so assignments persist. The
	// prompt is for a person at a terminal; on a pipe only results are written.
	tty := false
	if fi, err := os.Stdin.Stat(); err == nil {
		tty = fi.Mode()&os.ModeCharDevice != 0
	}
	root := sel.NewNone()
	in := bufio.NewReader(os.Stdin)
	for {
		if tty {
			fmt.Print("sel> ")
		}
		// ReadString has no line-length limit (a bufio.Scanner stops at 64 KB),
		// and a last line without a newline is still a line.
		line, err := in.ReadString('\n')
		// The line without its terminator: an error at the end of `1 +` is at
		// line 1, not at the start of a line 2 that the newline would open.
		line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")
		if line != "" && !blank(line) {
			run(line, root, deps)
		}
		if err != nil {
			if !errors.Is(err, io.EOF) {
				fmt.Fprintf(os.Stderr, "sel: cannot read standard input: %v\n", err)
				os.Exit(1)
			}
			break
		}
	}
	if tty {
		fmt.Println()
	}
}
