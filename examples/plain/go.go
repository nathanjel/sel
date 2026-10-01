// Plain usage — calling SEL from Go.
//
//   make -C go examples && go/build/example-plain
//
// The files beside this one do the same thing through their own host API and
// print byte-identical output; tools/check-examples.sh diffs them. That is the
// point of the example as much as the code is: the differences you see between
// these files are the languages', never SEL's.
//
// Go, like C++ and Rust, has no fromNative: a context is built child by child.
// Compile, Run and Eval return an error, which is always a *sel.SelError; the
// accessors of a Value (AsText, AsBool, ...) panic with one instead, the way an
// index out of range does. A position argument says where a host-built value
// comes from (sel.Pos{} for "the host").

package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

var at = sel.Pos{}

// check stops the example on an error a well-formed program never has.
func check(err error) {
	if err != nil {
		panic(err)
	}
}

func eval(src string) *sel.Value {
	v, err := sel.Eval(src, nil)
	check(err)
	return v
}

func main() {
	// 1 — evaluate something ------------------------------------------------------

	fmt.Println("1. one-off")
	fmt.Println("   2.50 + 2.50 =>", eval("2.50 + 2.50").AsText(at))

	// 2 — compile once, run per request ---------------------------------------------
	// Parsing is cheap but not free, and a Program is reusable.

	fmt.Println("2. compile once, run many")
	// EXAMPLE-BEGIN compile
	rule, err := sel.Compile(`IF(QTY * PRICE > LIMIT, "over budget", "ok")`)
	check(err)
	for _, row := range [][2]string{{"3", "19.99"}, {"1", "5.00"}} {
		ctx := sel.NewNone()
		ctx.Set("QTY", sel.NewText(row[0]))
		ctx.Set("PRICE", sel.NewText(row[1]))
		ctx.Set("LIMIT", sel.NewText("50.00"))
		v, err := rule.Run(ctx)
		check(err)
		fmt.Printf("   QTY=%s PRICE=%s => %s\n", row[0], row[1], v.AsText(at))
	}
	// EXAMPLE-END compile

	// 3 — building a context ----------------------------------------------------------
	// Money is TEXT, never a float64. SEL has no floating point, so the host
	// boundary is where that is said out loud.

	fmt.Println("3. structured context")
	// EXAMPLE-BEGIN context
	order := sel.NewNone()
	order.Set("CUSTOMER", sel.NewText("Zażółć"))
	var items []*sel.Value
	for _, line := range [][3]string{{"AB-1234", "3", "19.99"}, {"CD-5678", "1", "5.01"}} {
		item := sel.NewNone()
		item.Set("SKU", sel.NewText(line[0]))
		item.Set("QTY", sel.NewText(line[1]))
		item.Set("PRICE", sel.NewText(line[2]))
		items = append(items, item)
	}
	order.Set("ITEMS", sel.NewList(items)) // a list is keyed "1".."n"
	first, err := sel.MustCompile(`ITEMS[1]["SKU"]`).Run(order)
	check(err)
	total, err := sel.MustCompile(`SUM(ITEMS, _["QTY"] * _["PRICE"])`).Run(order)
	check(err)
	fmt.Println("   first SKU =>", first.AsText(at))
	fmt.Println("   total     =>", total.AsText(at))
	fmt.Println("   0.10+0.20 =>", eval("0.10 + 0.20").AsText(at))
	// EXAMPLE-END context

	// 4 — reading results back ----------------------------------------------------------
	// A result is a Value: a scalar, children, both or neither.

	fmt.Println("4. reading results")
	v := eval(`SPLIT("a,b,c", ",")`)
	fmt.Println("   size   =>", v.Size())
	fmt.Println("   keys   =>", strings.Join(v.Keys(), ","))
	fmt.Println("   [2]    =>", v.Get("2").AsText(at))
	fmt.Println("   scalar =>", v.AsText(at)) // scalar context: first child
	// A host bool prints differently in every language (true/1/True/T), and this
	// file's output has to be byte-identical to its siblings, so say it in SEL's
	// own spelling rather than the host's.
	yes := "FALSE"
	if eval("1 < 2").AsBool(at) {
		yes = "TRUE"
	}
	fmt.Println("   bool   =>", yes)

	// 5 — the context is mutated, so rules hand values back -----------------------------

	fmt.Println("5. variables the rule set")
	// EXAMPLE-BEGIN variables
	ctx := sel.NewNone()
	ctx.Set("QTY", sel.NewText("3"))
	ctx.Set("PRICE", sel.NewText("19.99"))
	// A *sel.Value is a handle: the run assigns into the same record.
	_, err = sel.MustCompile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT").Run(ctx)
	check(err)
	for _, name := range []string{"NET", "VAT", "GROSS"} {
		fmt.Printf("   %-5s => %s\n", name, ctx.Get(name).AsText(at))
	}
	// EXAMPLE-END variables

	// 6 — errors ---------------------------------------------------------------------------
	// Every failure is a *sel.SelError carrying a stable code and the position of
	// the node that actually failed. Match on the code, never on the message.

	fmt.Println("6. errors")
	// EXAMPLE-BEGIN errors
	for _, src := range []string{`3 + "A"`, "NOSUCH(1)", `IF(1, "a", "b")`, `ABORT("no stock")`} {
		_, err := sel.Eval(src, nil)
		var e *sel.SelError
		if errors.As(err, &e) {
			fmt.Printf("   %-17s => %s at %d:%d\n", src, e.Code, e.Line(), e.Col())
		} else {
			fmt.Printf("   %-17s => no error\n", src)
		}
	}
	// EXAMPLE-END errors

	// 7 — which fields does this rule read? ------------------------------------------------
	// Found statically, without running it.

	fmt.Println("7. dependencies")
	// EXAMPLE-BEGIN dependencies
	deps := sel.MustCompile(`T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""`).Dependencies()
	fmt.Println("  ", strings.Join(deps, " "))
	// EXAMPLE-END dependencies
}
