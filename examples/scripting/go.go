// Scripting an application's behaviour -- functions the host provides, from Go.
//
//   make -C go examples && go/build/example-scripting      (from the repository root)
//
// SEL has no way to reach the world on its own, and that is the point: the
// application decides what a script may touch by registering functions. Here
// the warehouse gets four -- STOCK and WEIGHT to read the catalogue, RESERVE to
// take stock, NOTIFY to queue a message -- and fulfil.sel, a file the warehouse
// team owns, decides per order whether to ship, how, and whom to tell. The
// application stays the same when the policy changes.
//
// A registered function is strict: its arguments arrive evaluated, left to
// right, through the same typed readers the builtins use, so a script passing
// the wrong kind gets the usual error at the usual position.
//
// The files beside this one print byte-identical output.
//
// The visible differences here: Go, like C++ and Rust, has no fromNative, so
// each order is built into a Value child by child, the way examples/plain/
// builds one. The function registry is process-wide and a Program may run on
// several goroutines at once, so the state the functions share sits behind a
// mutex. A typed reader (args.Text, args.NonNegInt) that meets the wrong kind
// panics with a *sel.SelError, which Run turns into its error.

package main

import (
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"

	"github.com/nathanjel/sel/go/sel"
)

var (
	mu        sync.Mutex
	inventory = map[string]int64{"LAMP-01": 4, "DESK-02": 1, "CHAIR-03": 6}
	weights   = map[string]string{"LAMP-01": "1.6", "DESK-02": "28.0", "CHAIR-03": "7.5"}
	outbox    []string
)

func main() {
	// EXAMPLE-BEGIN register
	sel.RegisterFunction("STOCK", 1, 1, func(args *sel.Args) *sel.Value {
		sku := args.Text(0)
		mu.Lock()
		defer mu.Unlock()
		return sel.NewInt(inventory[sku]) // a missing SKU reads as 0
	})

	sel.RegisterFunction("RESERVE", 2, 2, func(args *sel.Args) *sel.Value {
		sku := args.Text(0)
		qty := args.NonNegInt(1)
		mu.Lock()
		defer mu.Unlock()
		left, ok := inventory[sku]
		if !ok || left < qty {
			return sel.NewBool(false)
		}
		inventory[sku] = left - qty
		return sel.NewBool(true)
	})

	sel.RegisterFunction("WEIGHT", 1, 1, func(args *sel.Args) *sel.Value {
		if w, ok := weights[args.Text(0)]; ok {
			return sel.NewText(w)
		}
		return sel.NewText("0")
	})

	sel.RegisterFunction("NOTIFY", 2, 2, func(args *sel.Args) *sel.Value {
		message := args.Text(0) + ": " + args.Text(1)
		mu.Lock()
		defer mu.Unlock()
		outbox = append(outbox, message)
		return sel.NewBool(true)
	})
	// EXAMPLE-END register

	// EXAMPLE-BEGIN run
	// After registering: names resolve now.
	source, err := os.ReadFile("examples/scripting/fulfil.sel")
	if err != nil {
		panic(err)
	}
	fulfil, err := sel.Compile(string(source))
	if err != nil {
		panic(err)
	}

	fmt.Println("1. the script reads", strings.Join(fulfil.Dependencies(), ", "))
	fmt.Println("2. orders")
	type line struct{ sku, qty string }
	orders := []struct {
		order, country string
		lines          []line
	}{
		{"A-1", "PL", []line{{"LAMP-01", "2"}, {"CHAIR-03", "1"}}},
		{"A-2", "DE", []line{{"DESK-02", "1"}, {"CHAIR-03", "2"}}},
		{"A-3", "PL", []line{{"LAMP-01", "3"}}},
		{"A-4", "PL", []line{{"LAMP-01", "two"}}},
	}
	for _, o := range orders {
		ctx := sel.NewNone()
		ctx.Set("ORDER", sel.NewText(o.order))
		ctx.Set("COUNTRY", sel.NewText(o.country))
		var items []*sel.Value
		for _, l := range o.lines {
			item := sel.NewNone()
			item.Set("sku", sel.NewText(l.sku))
			item.Set("qty", sel.NewText(l.qty))
			items = append(items, item)
		}
		ctx.Set("ITEMS", sel.NewList(items))
		decision, err := fulfil.Run(ctx)
		var e *sel.SelError
		if errors.As(err, &e) {
			fmt.Printf("   %s  %s at %d:%d\n", o.order, e.Code, e.Line(), e.Col())
			continue
		}
		fmt.Printf("   %s  %s\n", o.order, decision.AsText(sel.Pos{}))
	}
	// EXAMPLE-END run

	fmt.Println("3. outbox")
	for _, message := range outbox {
		fmt.Println("  ", message)
	}
	fmt.Println("4. stock left")
	skus := make([]string, 0, len(inventory))
	for sku := range inventory {
		skus = append(skus, sku)
	}
	sort.Strings(skus) // a Go map has no order of its own
	for _, sku := range skus {
		fmt.Printf("   %-9s %d\n", sku, inventory[sku])
	}
}
