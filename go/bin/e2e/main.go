// Drives examples/order-validation.sel through the Go host API.
// Matches examples/e2e.mjs and cpp/bin/e2e.cpp.

package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

type Item struct {
	sku, qty, price string
}

type Scenario struct {
	name        string
	customer    string
	postcode    string
	creditLimit string
	items       []Item
}

func build(s Scenario) *sel.Value {
	ctx := sel.NewNone()
	ctx.Set("CUSTOMER", sel.NewText(s.customer))
	ctx.Set("POSTCODE", sel.NewText(s.postcode))
	ctx.Set("CREDIT_LIMIT", sel.NewText(s.creditLimit))

	items := make([]*sel.Value, len(s.items))
	for i, it := range s.items {
		item := sel.NewNone()
		item.Set("SKU", sel.NewText(it.sku))
		item.Set("QTY", sel.NewText(it.qty))
		item.Set("PRICE", sel.NewText(it.price))
		items[i] = item
	}
	ctx.Set("ITEMS", sel.NewList(items))
	return ctx
}

func main() {
	path := "examples/order-validation.sel"
	data, err := os.ReadFile(path)
	if err != nil {
		path = "../examples/order-validation.sel"
		data, err = os.ReadFile(path)
		if err != nil {
			fmt.Fprintln(os.Stderr, "run from the repository root: examples/order-validation.sel not found")
			os.Exit(2)
		}
	}

	prog := sel.MustCompile(string(data))

	scenarios := []Scenario{
		{"valid order", "Zażółć Gęślą", "31-874", "1000.00",
			[]Item{{"AB-1234", "3", "19.99"}, {"CD-5678", "1", "5.01"}}},
		{"blank customer", "   ", "31-874", "1000.00", []Item{{"AB-1234", "1", "1.00"}}},
		{"bad postcode", "Anna", "318744", "1000.00", []Item{{"AB-1234", "1", "1.00"}}},
		{"no lines", "Anna", "31-874", "1000.00", nil},
		{"zero quantity", "Anna", "31-874", "1000.00",
			[]Item{{"AB-1234", "1", "1.00"}, {"CD-5678", "0", "2.00"}}},
		{"malformed sku", "Anna", "31-874", "1000.00", []Item{{"oops", "1", "1.00"}}},
		{"over credit limit", "Anna", "31-874", "10.00", []Item{{"AB-1234", "3", "19.99"}}},
		{"exact-cent arithmetic", "Anna", "31-874", "0.30",
			[]Item{{"AB-1234", "1", "0.10"}, {"CD-5678", "1", "0.20"}}},
	}

	var sb strings.Builder
	sb.WriteString("dependencies:")
	for _, d := range prog.Dependencies() {
		sb.WriteString(" " + d)
	}
	sb.WriteByte('\n')

	for _, s := range scenarios {
		var result string
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*sel.SelError); ok {
						result = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
						return
					}
					result = fmt.Sprintf("!HOST panic: %v", r)
				}
			}()

			ctx := build(s)
			v, err := prog.Run(ctx)
			if err != nil {
				se := err.(*sel.SelError)
				result = fmt.Sprintf("!%s@%d:%d", se.Code, se.Line(), se.Col())
				return
			}
			result = v.Dump()
		}()

		name := s.name
		for len(name) < 24 {
			name += " "
		}
		sb.WriteString(name + " " + result + "\n")
	}

	os.Stdout.WriteString(sb.String())
}
