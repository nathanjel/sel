package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "usage: check-decimal <oracle.txt>\n")
		os.Exit(2)
	}

	file, err := os.Open(os.Args[1])
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to open %s: %v\n", os.Args[1], err)
		os.Exit(2)
	}
	defer file.Close()

	var lines []string
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		text := scanner.Text()
		if len(text) > 0 {
			lines = append(lines, text)
		}
	}
	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "error reading file: %v\n", err)
		os.Exit(2)
	}

	failFn := func(code string, msg string, pos utf8.Pos) {
		panic(&sel.SelError{Code: code, Message: msg, Pos: sel.Pos{Line: pos.Line, Col: pos.Col, Offset: pos.Offset}})
	}

	var failures []string
	mismatches := 0

	for _, line := range lines {
		parts := strings.Split(line, "|")
		if len(parts) != 4 {
			continue
		}
		op, aStr, bStr, want := parts[0], parts[1], parts[2], parts[3]

		var got string
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*sel.SelError); ok {
						got = "THREW " + se.Code
					} else {
						got = fmt.Sprintf("THREW %v", r)
					}
				}
			}()

			dummyPos := utf8.Pos{Line: 1, Col: 1}
			a := decimal.Parse(aStr, dummyPos, failFn)
			b := decimal.Parse(bStr, dummyPos, failFn)

			switch op {
			case "+":
				got = decimal.Format(decimal.Add(a, b, dummyPos, failFn))
			case "-":
				got = decimal.Format(decimal.Sub(a, b, dummyPos, failFn))
			case "*":
				got = decimal.Format(decimal.Mul(a, b, dummyPos, failFn))
			case "/":
				got = decimal.Format(decimal.Div(a, b, dummyPos, failFn))
			case "%":
				got = decimal.Format(decimal.Mod(a, b, dummyPos, failFn))
			case "cmp":
				got = strconv.Itoa(decimal.Cmp(a, b))
			case "round":
				places, err := strconv.Atoi(bStr)
				if err != nil {
					panic(err)
				}
				got = decimal.Format(decimal.Round(a, places, dummyPos, failFn))
			case "floor":
				got = decimal.Format(decimal.Floor(a, dummyPos, failFn))
			case "ceil":
				got = decimal.Format(decimal.Ceil(a, dummyPos, failFn))
			case "trunc":
				got = decimal.Format(decimal.Trunc(a))
			default:
				panic("unknown op " + op)
			}
		}()

		if got != want {
			mismatches++
			if len(failures) < 20 {
				failures = append(failures, fmt.Sprintf("%s %s %s => %s, oracle says %s", aStr, op, bStr, got, want))
			}
		}
	}

	fmt.Printf("go: %d cases, %d mismatches\n", len(lines), mismatches)
	for _, f := range failures {
		fmt.Printf("  %s\n", f)
	}
	if mismatches > len(failures) {
		fmt.Printf("  ... and %d more\n", mismatches-len(failures))
	}

	if mismatches > 0 {
		os.Exit(1)
	}
}
