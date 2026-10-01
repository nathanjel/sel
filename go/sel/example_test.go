package sel_test

import (
	"errors"
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

// Compile once, run per row: a context is built child by child, and money is
// text, never a float64.
func ExampleCompile() {
	rule, err := sel.Compile(`IF(QTY * PRICE > LIMIT, "over budget", "ok")`)
	if err != nil {
		panic(err)
	}
	for _, row := range [][2]string{{"3", "19.99"}, {"1", "5.00"}} {
		ctx := sel.NewNone()
		ctx.Set("QTY", sel.NewText(row[0]))
		ctx.Set("PRICE", sel.NewText(row[1]))
		ctx.Set("LIMIT", sel.NewText("50.00"))
		v, err := rule.Run(ctx)
		if err != nil {
			panic(err)
		}
		fmt.Println(v.AsText(sel.Pos{}))
	}
	// Output:
	// over budget
	// ok
}

// A run assigns into the context it is given, so a rule can hand values back.
func ExampleProgram_Run() {
	ctx := sel.NewNone()
	ctx.Set("QTY", sel.NewText("3"))
	ctx.Set("PRICE", sel.NewText("19.99"))
	if _, err := sel.MustCompile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2)").Run(ctx); err != nil {
		panic(err)
	}
	fmt.Println(ctx.Get("NET").AsText(sel.Pos{}), ctx.Get("VAT").AsText(sel.Pos{}))
	// Output: 59.97 13.79
}

// The inputs a rule reads, found without running it.
func ExampleProgram_Dependencies() {
	deps := sel.MustCompile(`T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""`).Dependencies()
	fmt.Println(strings.Join(deps, " "))
	// Output: CUSTOMER ITEMS LIMIT
}

// Every failure is a *SelError with a stable code and the position of the node
// that failed. E_ABORT is the rule speaking; anything else is the rule failing.
func ExampleSelError() {
	for _, src := range []string{`3 + "A"`, `ABORT("no stock")`} {
		_, err := sel.Eval(src, nil)
		var e *sel.SelError
		if errors.As(err, &e) {
			fmt.Printf("%s at %d:%d\n", e.Code, e.Line(), e.Col())
		}
	}
	// Output:
	// E_NOT_NUM at 1:5
	// E_ABORT at 1:7
}

// The accessors of a Value panic with a *SelError rather than return one; code
// that reads a result it does not control recovers it.
func ExampleValue_AsText() {
	asText := func(v *sel.Value) (text string, err error) {
		defer func() {
			if r := recover(); r != nil {
				e, ok := r.(*sel.SelError)
				if !ok {
					panic(r)
				}
				err = e
			}
		}()
		return v.AsText(sel.Pos{}), nil
	}
	v, _ := sel.Eval("1 < 2", nil) // a BOOL, not text
	_, err := asText(v)
	fmt.Println(err.(*sel.SelError).Code)
	// Output: E_NOT_TEXT
}
