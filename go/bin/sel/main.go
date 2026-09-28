// SEL command line: evaluate an expression, a file, or start a REPL.
// Matches js/bin/sel.mjs and cpp/bin/sel.cpp.

package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

func show(v *sel.Value) string {
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

func report(e *sel.SelError) {
	fmt.Fprintf(os.Stderr, "%s at line %d column %d: %s\n", e.Code, e.Line(), e.Col(), e.Message)
}

func main() {
	argl := os.Args[1:]
	wantDeps := false
	var args []string

	for _, a := range argl {
		if a == "--deps" {
			wantDeps = true
		} else {
			args = append(args, a)
		}
	}

	if len(args) > 0 && args[0] == "--functions" {
		for _, name := range sel.FunctionNames() {
			fmt.Println(name)
		}
		os.Exit(0)
	}

	haveSource := false
	source := ""

	if len(args) >= 2 && args[0] == "-e" {
		source = args[1]
		haveSource = true
	} else if len(args) > 0 {
		data, err := os.ReadFile(args[0])
		if err != nil {
			fmt.Fprintf(os.Stderr, "cannot read %s: %v\n", args[0], err)
			os.Exit(1)
		}
		source = string(data)
		haveSource = true
	}

	if haveSource {
		var prog *sel.Program
		var err error

		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					report(se)
					os.Exit(1)
				}
				panic(r)
			}
		}()

		prog, err = sel.Compile(source)
		if err != nil {
			report(err.(*sel.SelError))
			os.Exit(1)
		}

		if wantDeps {
			for _, d := range prog.Dependencies() {
				fmt.Println(d)
			}
		} else {
			val, err := prog.Run(sel.NewNone())
			if err != nil {
				report(err.(*sel.SelError))
				os.Exit(1)
			}
			fmt.Println(show(val))
		}
		os.Exit(0)
	}

	// REPL: one context for the whole session, so assignments persist.
	root := sel.NewNone()
	scanner := bufio.NewScanner(os.Stdin)
	fmt.Print("sel> ")

	for scanner.Scan() {
		line := scanner.Text()
		if strings.TrimSpace(line) != "" {
			func() {
				defer func() {
					if r := recover(); r != nil {
						if se, ok := r.(*sel.SelError); ok {
							report(se)
							return
						}
						fmt.Fprintf(os.Stderr, "panic: %v\n", r)
					}
				}()

				p, err := sel.Compile(line)
				if err != nil {
					report(err.(*sel.SelError))
					return
				}
				val, err := p.Run(root)
				if err != nil {
					report(err.(*sel.SelError))
					return
				}
				fmt.Println(show(val))
			}()
		}
		fmt.Print("sel> ")
	}
	fmt.Println()
}
