// Complex usage — the language's reach, from Go.
//
//   make -C go examples && go/build/example-complex
//
// examples/plain/ is the API. This is the language: aggregates, named binders,
// text and regex, structured results, and a rule that refuses. The files beside
// this one print byte-identical output; tools/check-examples.sh diffs them.

package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

var at = sel.Pos{}

func main() {
	// Go has no fromNative — there is no native map or slice to convert from — so
	// the order is built child by child. Money is TEXT, never a float64.
	order := sel.NewNone()
	order.Set("CUSTOMER", sel.NewText("Zażółć Gęślą"))
	order.Set("POSTCODE", sel.NewText("31-874"))
	order.Set("CREDIT_LIMIT", sel.NewText("100.00"))
	var items []*sel.Value
	for _, line := range [][3]string{{"AB-1234", "3", "19.99"}, {"CD-5678", "1", "5.01"}, {"EF-9012", "2", "0.50"}} {
		item := sel.NewNone()
		item.Set("SKU", sel.NewText(line[0]))
		item.Set("QTY", sel.NewText(line[1]))
		item.Set("PRICE", sel.NewText(line[2]))
		items = append(items, item)
	}
	order.Set("ITEMS", sel.NewList(items)) // a list is keyed "1".."n"

	// A compile error and a run error arrive the same way, as the error of one
	// call, so a caller that handles one handles both.
	run := func(src string) (*sel.Value, error) {
		program, err := sel.Compile(src)
		if err != nil {
			return nil, err
		}
		return program.Run(order)
	}
	ask := func(src string) (string, error) {
		v, err := run(src)
		if err != nil {
			return "", err
		}
		return v.AsText(at), nil
	}
	// The examples below never fail where they are not meant to.
	must := func(s string, err error) string {
		if err != nil {
			panic(err)
		}
		return s
	}

	// 1 — a rule set, not an expression -------------------------------------------
	// `;` separates statements and the last one is the answer. Intermediate names
	// are ordinary variables, so a long rule reads top to bottom.

	fmt.Println("1. a rule set")
	fmt.Println("  ", must(ask(strings.Join([]string{
		`NET   = SUM(ITEMS, _["QTY"] * _["PRICE"])`,
		`VAT   = ROUND(NET * 0.23, 2)`,
		`GROSS = NET + VAT`,
		`IF(GROSS > CREDIT_LIMIT, "refer: " & GROSS, "accept: " & GROSS)`,
	}, "; "))))

	// 2 — aggregates ---------------------------------------------------------------
	// No loops. A body expression is evaluated once per element with `_` bound to
	// the element and `_K` to its key.

	fmt.Println("2. aggregates")
	fmt.Println("   lines        =>", must(ask(`COUNT(ITEMS)`)))
	fmt.Println("   net          =>", must(ask(`SUM(ITEMS, _["QTY"] * _["PRICE"])`)))
	fmt.Println("   all in stock =>", must(ask(`IF(ALL(ITEMS, _["QTY"] > 0), "TRUE", "FALSE")`)))
	fmt.Println("   any > 10     =>", must(ask(`IF(ANY(ITEMS, _["PRICE"] > 10.00), "TRUE", "FALSE")`)))
	fmt.Println("   dearest      =>", must(ask(`MAX(MAP(ITEMS, _["PRICE"]))`)))

	// 3 — MAP renumbers, FILTER keeps the keys --------------------------------------
	// A filtered list stays addressable the way its source was, which is why the
	// dump below has holes in it. That is the contract, not an accident.

	fmt.Println("3. map and filter")
	fmt.Println("   skus         =>", must(ask(`JOIN(MAP(ITEMS, _["SKU"]), ", ")`)))
	bulk, err := run(`FILTER(ITEMS, _["QTY"] > 1)`)
	if err != nil {
		panic(err)
	}
	fmt.Println("   bulk keys    =>", strings.Join(bulk.Keys(), ","))
	keyed, err := run(`MAP(FILTER(ITEMS, _["QTY"] > 1), _K & ":" & _["SKU"])`)
	if err != nil {
		panic(err)
	}
	fmt.Println("   keyed        =>", keyed.Dump())

	// 4 — naming the binder, for nesting ---------------------------------------------
	// `_` is the innermost element. The three-argument form names it instead, which
	// is the only way an outer element stays reachable from an inner body.

	fmt.Println("4. named binders")
	nested, err := sel.Eval(`R[1] = (1, 2); R[2] = (3, 4); `+
		`IF(ALL(R, ROW, ALL(ROW, _ > 0)), "all positive", "no")`, nil)
	if err != nil {
		panic(err)
	}
	fmt.Println("  ", nested.AsText(at))

	// 5 — text and regex ---------------------------------------------------------------
	// Patterns are a portable subset, checked at compile time: a regex that would
	// mean different things on different hosts is refused rather than guessed at.

	fmt.Println("5. text and regex")
	// UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
	// ę below come back unchanged. That is not a shortcoming, it is the only way
	// every host can agree. Measured on a sharp s: JS's toUpperCase, Python's
	// str.upper and Rust's str::to_uppercase all answer SS, PHP's strtoupper
	// answers ß, and C's toupper cannot see it at all. (Go's strings.ToUpper
	// answers ß too, and turns ż into Ż.) SEL answers ß on every host, because it
	// never asks the host.
	fmt.Println("   upper        =>", must(ask(`UPPER(CUSTOMER)`)))
	fmt.Println("   initials     =>", must(ask(`JOIN(MAP(SPLIT(CUSTOMER, " "), LEFT(_, 1)), ".")`)))
	fmt.Println("   postcode     =>", must(ask(`IF(RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE), "ok", "bad")`)))
	// RGROUPS puts the WHOLE match at "1", so the first capture is "2".
	fmt.Println("   area         =>", must(ask(`RGROUPS('^([0-9]{2})-', POSTCODE)["2"]`)))
	fmt.Println("   padded       =>", must(ask(`PADL(COUNT(ITEMS), 3, "0")`)))

	// 6 — asking whether a key is there --------------------------------------------------

	fmt.Println("6. presence")
	fmt.Println("   HAS SKU      =>", must(ask(`IF(HAS(ITEMS[1], "SKU"), "TRUE", "FALSE")`)))
	fmt.Println("   HAS NOTE     =>", must(ask(`IF(HAS(ITEMS[1], "NOTE"), "TRUE", "FALSE")`)))
	missing, err := ask(`ITEMS[1]["NOTE"]`)
	var e *sel.SelError
	if errors.As(err, &e) {
		missing = fmt.Sprintf("%s at %d:%d", e.Code, e.Line(), e.Col())
	}
	fmt.Println("   missing      =>", missing)

	// 7 — a rule that refuses -------------------------------------------------------------
	// ABORT is how a rule says "this is not valid", as distinct from "this could not
	// be computed". Both arrive as the same error type, told apart by the code.

	fmt.Println("7. business refusal")
	for _, c := range [][2]string{
		{"under limit", `IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > CREDIT_LIMIT, ` +
			`ABORT("over credit limit"), "ok")`},
		{"over limit", `IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > 50.00, ` +
			`ABORT("over credit limit"), "ok")`},
		{"blank name", `IF(TRIM("   ") $== "", ABORT("customer required"), "ok")`},
	} {
		// The answer is settled before anything is printed: a partly written line
		// cannot be taken back once ABORT has failed through the middle of it.
		answer, err := ask(c[1])
		if errors.As(err, &e) {
			answer = fmt.Sprintf("%s: %s", e.Code, e.Message)
		}
		fmt.Printf("   %-11s => %s\n", c[0], answer)
	}

	// 8 — where a failure actually happened -------------------------------------------------
	// The position is the node that failed, not the statement or the call that
	// contains it. That is what makes a long rule debuggable.

	fmt.Println("8. error positions")
	for _, src := range []string{`1 + ROUND(2 + "x", 2)`, `SUM(ITEMS, _["QTY"] * _["NOPE"])`} {
		if _, err := run(src); errors.As(err, &e) {
			fmt.Printf("   %s at %d:%d  %s\n", e.Code, e.Line(), e.Col(), src)
		}
	}
}
