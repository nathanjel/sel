// A read-eval-print loop -- the whole of it, in Go.
//
//   make -C go examples
//   go/build/example-repl
//   go/build/example-repl < examples/repl/session.txt
//
// One context lives across lines, so a variable assigned on one line is there
// on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
// expression reads, and `:reset` empties the context. Errors print their code
// and position -- the message is human text and may differ between hosts; the
// code and the position may not.
//
// The files beside this one print byte-identical output for the session in
// session.txt.
//
// Compile and Run return a SEL failure as their error; a Value's accessors
// (AsText, AsBool) panic with one, so respond turns that panic back into an
// error too, and the loop goes on either way. What stops the loop is only a
// failure to read standard input, which is not SEL's.

package main

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

// EXAMPLE-BEGIN repl
func show(value *sel.Value) string {
	at := sel.Pos{}
	if value.IsBool() {
		if value.AsBool(at) {
			return "TRUE"
		}
		return "FALSE"
	}
	if value.IsNull() {
		return "NULL"
	}
	if value.Size() > 0 || value.IsBin() {
		return value.Dump()
	}
	return value.AsText(at)
}

// One line: a command, or an expression run against the context. A SEL failure
// comes back as the error, to be printed where the line was read.
func respond(line string, context **sel.Value) (err error) {
	defer func() {
		if r := recover(); r != nil {
			e, ok := r.(*sel.SelError)
			if !ok {
				panic(r)
			}
			err = e
		}
	}()
	if line == ":reset" {
		*context = sel.NewNone()
		return nil
	}
	if expr, ok := strings.CutPrefix(line, ":deps "); ok {
		program, err := sel.Compile(expr)
		if err != nil {
			return err
		}
		fmt.Println(strings.Join(program.Dependencies(), " "))
		return nil
	}
	program, err := sel.Compile(line)
	if err != nil {
		return err
	}
	result, err := program.Run(*context)
	if err != nil {
		return err
	}
	fmt.Println(show(result))
	return nil
}

func main() {
	context := sel.NewNone()
	input := bufio.NewScanner(os.Stdin)
	for input.Scan() {
		line := input.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}
		fmt.Println("sel>", line)
		var e *sel.SelError
		if err := respond(line, &context); errors.As(err, &e) {
			fmt.Printf("%s at %d:%d\n", e.Code, e.Line(), e.Col())
		}
	}
	if err := input.Err(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// EXAMPLE-END repl
