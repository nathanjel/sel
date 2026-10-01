// Form validation -- one rule set, the same verdicts in every host, from Go.
//
//   make -C go examples && go/build/example-validation
//
// A checkout form's rules, one per field, written in SEL. The server compiles
// them once at start-up, so a rule that does not parse fails the deployment
// rather than a customer. Each rule answers "" when the field is fine and
// ABORT("message") when it is not, so there are two kinds of failure and they
// are told apart by code: E_ABORT is a message for the user, anything else means
// the rule itself is broken and the user should never see it. Dependencies()
// tells a browser which rules to re-run when a field changes -- and the browser
// runs the very same rule text, in JavaScript.
//
// The files beside this one print byte-identical output.
//
// The visible difference here is that Go, like C++ and Rust, has no
// fromNative: a submitted form is a slice of (field, text) pairs, and the
// context is built from it one Set at a time. A compiled *sel.Program is safe
// to share between goroutines, so one rule set serves every request.

package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

// EXAMPLE-BEGIN rules
var rules = []struct{ field, source string }{
	{"name", `IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")`},
	{"email", `IF(RMATCH('^[^@ ]+@[^@ ]+\.[a-z]{2,}$', TRIM(EMAIL), "i"), "",` +
		` ABORT("{EMAIL} does not look like an e-mail address"))`},
	{"postcode", `COND(COUNTRY $== "PL" AND NOT RMATCH('^\d{2}-\d{3}$', POSTCODE),` +
		`       ABORT("Polish postcodes look like 00-000"),` +
		`     COUNTRY $== "DE" AND NOT RMATCH('^\d{5}$', POSTCODE),` +
		`       ABORT("German postcodes have five digits"),` +
		`     "")`},
	{"quantity", `IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,` +
		` ABORT("Choose between 1 and {STOCK}"), "")`},
	{"total", `TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);` +
		` IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")`},
}

// EXAMPLE-END rules

type rule struct {
	field   string
	program *sel.Program
}

// EXAMPLE-BEGIN validate
type form [][2]string // field, what was typed
type problem struct { // field, message
	field, message string
}

func validate(compiled []rule, submitted form) []problem {
	var problems []problem
	for _, r := range compiled {
		ctx := sel.NewNone()
		for _, f := range submitted {
			ctx.Set(f[0], sel.NewText(f[1]))
		}
		var verdict string
		v, err := r.program.Run(ctx)
		var e *sel.SelError
		switch {
		case err == nil:
			verdict = v.AsText(sel.Pos{})
		// E_ABORT is the rule speaking to the user; anything else is a broken
		// rule or data it cannot read -- log it, show a generic line.
		case errors.As(err, &e) && e.Code == "E_ABORT":
			verdict = e.Message
		case errors.As(err, &e):
			verdict = fmt.Sprintf("could not be checked (%s)", e.Code)
		}
		if verdict != "" {
			problems = append(problems, problem{r.field, verdict})
		}
	}
	return problems
}

// EXAMPLE-END validate

var submissions = []form{
	{{"NAME", "Anna Nowak"}, {"EMAIL", "anna@example.pl"}, {"COUNTRY", "PL"}, {"POSTCODE", "31-874"},
		{"QTY", "2"}, {"STOCK", "5"}, {"PRICE", "19.99"}, {"DISCOUNT", "0.10"}, {"CREDIT_LIMIT", "100.00"}},
	{{"NAME", "   "}, {"EMAIL", "bruno(at)example.de"}, {"COUNTRY", "DE"}, {"POSTCODE", "1011"},
		{"QTY", "9"}, {"STOCK", "5"}, {"PRICE", "19.99"}, {"DISCOUNT", "0"}, {"CREDIT_LIMIT", "100.00"}},
	{{"NAME", "Chloé"}, {"EMAIL", "CHLOE@EXAMPLE.FR "}, {"COUNTRY", "FR"}, {"POSTCODE", "69002"},
		{"QTY", "4"}, {"STOCK", "5"}, {"PRICE", "29.99"}, {"DISCOUNT", "0.05"}, {"CREDIT_LIMIT", "100.00"}},
	{{"NAME", "Dawid"}, {"EMAIL", "dawid@example.pl"}, {"COUNTRY", "PL"}, {"POSTCODE", "00-950"},
		{"QTY", "1"}, {"STOCK", "5"}, {"PRICE", "twenty"}, {"DISCOUNT", "0"}, {"CREDIT_LIMIT", "100.00"}},
}

func main() {
	// 1 - compile once, at start-up ----------------------------------------------------

	// EXAMPLE-BEGIN compile
	var compiled []rule
	for _, r := range rules {
		program, err := sel.Compile(r.source)
		if err != nil {
			panic(err) // a rule that does not parse fails the deployment
		}
		compiled = append(compiled, rule{r.field, program})
	}
	// EXAMPLE-END compile
	fmt.Println("1. the rule set")
	for _, r := range compiled {
		fmt.Printf("   %-9s reads %s\n", r.field, strings.Join(r.program.Dependencies(), " "))
	}

	// 2 - what to re-check when a field changes ----------------------------------------

	fmt.Println("2. re-check on change")
	watch := map[string][]string{}
	for _, r := range compiled {
		for _, name := range r.program.Dependencies() {
			watch[name] = append(watch[name], r.field)
		}
	}
	names := make([]string, 0, len(watch))
	for name := range watch {
		names = append(names, name)
	}
	sort.Strings(names) // a map has no order of its own
	for _, name := range names {
		fmt.Printf("   %-13s %s\n", name, strings.Join(watch[name], ", "))
	}

	// 3 - validating submissions -----------------------------------------------------------

	fmt.Println("3. submissions")
	for i, submitted := range submissions {
		n := i + 1
		problems := validate(compiled, submitted)
		if len(problems) == 0 {
			fmt.Printf("   #%d accepted\n", n)
		}
		for _, p := range problems {
			fmt.Printf("   #%d %-9s %s\n", n, p.field, p.message)
		}
	}
}
