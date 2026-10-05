package main

import (
	"fmt"
	"math/big"
	"os"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "usage: check-decimal <oracle.txt>\n")
		os.Exit(2)
	}

	var lines []string
	for _, text := range strings.Split(harness.ReadFile(os.Args[1]), "\n") {
		if len(text) > 0 {
			lines = append(lines, text)
		}
	}

	failFn := func(code string, msg string, pos utf8.Pos) {
		panic(&sel.SelError{Code: code, Message: msg, Pos: sel.Pos(pos)})
	}

	var failures []string
	mismatches := 0
	// + - * are also run through registers the way a math plan runs them
	// -- one long-lived result register and one scratch, each record's result
	// written over the last -- so the oracle grades that path too.
	reg, scratch := new(big.Int), new(big.Int)
	registers := 0

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

		if op == "+" || op == "-" || op == "*" {
			registers++
			var viaReg string
			func() {
				defer func() {
					if r := recover(); r != nil {
						if se, ok := r.(*sel.SelError); ok {
							viaReg = "THREW " + se.Code
						} else {
							viaReg = fmt.Sprintf("THREW %v", r)
						}
					}
				}()
				pos := utf8.Pos{Line: 1, Col: 1}
				a := decimal.NumOf(decimal.Parse(aStr, pos, failFn))
				b := decimal.NumOf(decimal.Parse(bStr, pos, failFn))
				var r decimal.Num
				switch op {
				case "+":
					r = decimal.AddInto(reg, scratch, a, b, pos, failFn)
				case "-":
					r = decimal.SubInto(reg, scratch, a, b, pos, failFn)
				default:
					r = decimal.MulInto(reg, a, b, pos, failFn)
				}
				viaReg = decimal.Format(r.Dec())
			}()
			if viaReg != want {
				mismatches++
				if len(failures) < 20 {
					failures = append(failures, fmt.Sprintf("%s %s %s => %s through registers, oracle says %s", aStr, op, bStr, viaReg, want))
				}
			}
		}
	}

	fmt.Printf("go: %d cases (%d also through registers), %d mismatches\n", len(lines), registers, mismatches)
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
