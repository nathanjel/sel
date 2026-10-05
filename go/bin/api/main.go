// API parity probe — Go. See tools/api.mjs and cpp/bin/api.cpp.

package main

import (
	"fmt"
	"math/big"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

var out []string
var counter int

// numFromStr is the other hosts' Value.num("1.50"): Go has no number-from-text
// constructor (a number is its text), so the text is read as a number and the
// decimal form built from it, through the public API alone.
func numFromStr(s string) *sel.Value {
	return sel.NewDecimal(sel.NewText(s).Decimal(sel.Pos{}))
}

func say(name, value string) {
	counter++
	out = append(out, fmt.Sprintf("%02d %s = %s", counter, name, value))
}

func b(v bool) string {
	if v {
		return "true"
	}
	return "false"
}

func repeat(unit string, n int) string {
	return strings.Repeat(unit, n)
}

func eval(src string, ctx ...*sel.Value) *sel.Value {
	p := sel.MustCompile(src)
	var root *sel.Value
	if len(ctx) > 0 && ctx[0] != nil {
		root = ctx[0]
	} else {
		root = sel.NewNone()
	}
	v, err := p.Run(root)
	if err != nil {
		panic(err)
	}
	return v
}

func nest(n int) *sel.Value {
	v := sel.NewText("x")
	for i := 0; i < n; i++ {
		p := sel.NewNone()
		p.Set("1", v)
		v = p
	}
	return v
}

func main() {
	// --- kind constants and predicates
	say("kind.const.none", "NONE")
	say("kind.const.text", "TEXT")
	say("kind.const.bin", "BIN")
	say("kind.const.bool", "BOOL")
	say("kind.static.bool", "BOOL")
	say("kind.of.text", eval("\"x\"").Kind().String())
	say("kind.of.bool", eval("TRUE").Kind().String())
	say("kind.of.none", eval("(1,2)").Kind().String())
	say("pred.isText", b(eval("\"x\"").Kind() == sel.KindText))
	say("pred.isBool", b(eval("TRUE").Kind() == sel.KindBool))
	say("pred.isNone", b(eval("(1,2)").Kind() == sel.KindNone))
	say("pred.isBin", b(eval("TO_UTF8(\"x\")").Kind() == sel.KindBin))
	say("pred.isText.on.bool", b(eval("TRUE").Kind() == sel.KindText))

	// --- constructors
	say("ctor.text", sel.NewText("hi").Dump())
	say("ctor.bool", sel.NewBool(true).Dump())
	say("ctor.none", sel.NewNone().Dump())
	say("ctor.num.canonicalises", numFromStr("007").Dump())
	say("ctor.int", sel.NewInt(-3).Dump())
	say("ctor.list", sel.NewList([]*sel.Value{sel.NewText("a"), sel.NewText("b")}).Dump())

	// --- children, and the ordering rules
	v := sel.NewNone()
	v.Set("b", sel.NewText("1"))
	v.Set("a", sel.NewText("2"))
	say("children.size", fmt.Sprintf("%d", v.Size()))
	say("children.size.is.callable", b(true))
	say("children.keys", strings.Join(v.Keys(), ","))
	v.Set("b", sel.NewText("9"))
	say("children.reassign.keeps.position", strings.Join(v.Keys(), ","))
	say("children.reassign.no.growth", fmt.Sprintf("%d", v.Size()))
	say("children.has", b(v.Has("a")))
	say("children.has.missing", b(v.Has("zz")))
	say("children.get", v.Get("b").Dump())

	// --- scalar context
	say("scalar.asText", eval("\"héllo\"").AsText(sel.Pos{}))
	say("scalar.asBool", b(eval("TRUE").AsBool(sel.Pos{})))
	say("scalar.takes.first.child", eval("(7,8)").AsText(sel.Pos{}))
	say("scalar.looksNumeric", b(eval("\"2.50\"").LooksNumeric()))
	say("scalar.looksNumeric.no", b(eval("\"x\"").LooksNumeric()))

	// --- equality and dump
	say("eql.same", b(sel.NewText("5").Eql(sel.NewText("5"), sel.Pos{})))
	say("eql.not.normalised", b(sel.NewText("5.00").Eql(sel.NewText("5"), sel.Pos{})))
	say("dump.tree", eval("A=1; A[2]=\"x\"; A").Dump())

	// --- programs
	p := sel.MustCompile("IF(A > B, A, C)")
	say("program.dependencies", strings.Join(p.Dependencies(), " "))
	say("program.deps.excludes.assigned", strings.Join(sel.MustCompile("X = 1; X + Y").Dependencies(), " "))
	say("program.deps.excludes.binder", strings.Join(sel.MustCompile("ALL(I, IT, IT > 0)").Dependencies(), " "))
	say("program.deps.grouped.binder", strings.Join(sel.MustCompile("ALL(I, (IT), IT > 0)").Dependencies(), " "))
	say("program.deps.forms.top.binder-and-limit", strings.Join(sel.MustCompile("TOP(L, X, X[\"a\"], N)").Dependencies(), " "))
	say("program.deps.forms.top.limit-is-outer", strings.Join(sel.MustCompile("TOP(L, COUNT(_))").Dependencies(), " "))
	say("program.deps.forms.sort-by.text-direction-wins", strings.Join(sel.MustCompile("SORT_BY(L, K, \"DESC\")").Dependencies(), " "))
	say("program.deps.forms.top-by.direction-is-outer", strings.Join(sel.MustCompile("TOP_BY(L, _[\"a\"], N, D)").Dependencies(), " "))
	say("program.deps.forms.bucket.projection-inside", strings.Join(sel.MustCompile("BUCKET(L, G, G[\"k\"], COUNT(G) + _K)").Dependencies(), " "))
	say("program.deps.forms.link.named-binders", strings.Join(sel.MustCompile("LINK(A, B, X, Y, X[\"a\"] == Y[\"b\"] AND Z)").Dependencies(), " "))

	ctx := sel.NewNone()
	ctx.Set("TOTAL", numFromStr("59.97"))
	say("program.run.reads.context", eval("TOTAL > 10.00", ctx).Dump())
	eval("SEEN = TOTAL * 2", ctx)
	say("program.run.mutates.context", ctx.Get("SEEN").AsText(sel.Pos{}))
	// A BOOL a host hands in is an ordinary value (see tools/api.mjs).
	{
		a, bb := sel.NewNone(), sel.NewNone()
		a.Set("FLAG", sel.NewBool(true))
		bb.Set("FLAG", sel.NewBool(true))
		eval(`FLAG["k"] = 1; 0`, a)
		say("bool.isolated.between.contexts", fmt.Sprintf("%d %d %s", bb.Get("FLAG").Size(),
			sel.NewBool(true).Size(), eval("COUNT(TRUE)").AsText(sel.Pos{})))
	}
	say("registry.count", fmt.Sprintf("%d", len(sel.FunctionNames())))
	say("registry.sorted.first", sel.FunctionNames()[0])

	// --- errors
	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("error.code", se.Code)
					say("error.line", fmt.Sprintf("%d", se.Line()))
					say("error.col", fmt.Sprintf("%d", se.Col()))
					say("error.isSelError", b(true))
				}
			}
		}()
		eval("1 +\n  X")
	}()

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("error.compile.unknown.func", se.Code)
				}
			}
		}()
		sel.MustCompile("NOPE(1)")
	}()

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("error.host.badnum", se.Code)
				}
			}
		}()
		numFromStr("x")
	}()

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("error.host.hugenum", se.Code)
				}
			}
		}()
		numFromStr(repeat("1", 2000001))
	}()

	// Constructor error checks
	{
		type decSpec struct {
			neg   bool
			dig   string
			scale int32
		}
		// The decimal form through the public constructor, as the other hosts'
		// Value.num({neg, digits, scale}).
		numFromDec := func(spec decSpec) *sel.Value {
			digits, _ := new(big.Int).SetString(spec.dig, 10)
			return sel.NewDecimal(sel.Decimal{Neg: spec.neg, Digits: digits, Scale: int(spec.scale)})
		}

		probes := []struct {
			name string
			fn   func()
		}{
			{"error.host.dec.fraccap", func() { numFromDec(decSpec{false, "1", 1000001}) }},
			{"error.host.dec.negscale", func() { numFromDec(decSpec{false, "7", -1}) }},
			// Through the public constructors: a record key is validated by Set, and
			// a keyed list whose keys and values differ in count is malformed.
			{"error.host.key.utf8", func() {
				sel.NewNone().Set("a\xff", sel.NewText("1"))
			}},
			{"error.host.malformed", func() {
				sel.NewListWithKeys([]*sel.Value{sel.NewText("1")}, []string{})
			}},
		}

		for _, p := range probes {
			func() {
				defer func() {
					if r := recover(); r != nil {
						if se, ok := r.(*sel.SelError); ok {
							say(p.name, se.Code)
							return
						}
					}
					say(p.name, "no error")
				}()
				p.fn()
				say(p.name, "no error")
			}()
		}

		say("ctor.dec.negzero", numFromDec(decSpec{true, "0", 0}).Dump())
	}

	// Value depth cap
	say("value.depth.under", func() string {
		if len(nest(199).Dump()) > 0 {
			return "ok"
		}
		return "no"
	}())

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("value.depth.over", se.Code)
				}
			}
		}()
		nest(200).Dump()
	}()

	// Dependencies depth cap
	say("deps.depth.under", strings.Join(sel.MustCompile("A"+repeat("+A", 199)).Dependencies(), " "))

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					say("deps.depth.over", fmt.Sprintf("%s %d:%d", se.Code, se.Line(), se.Col()))
				}
			}
		}()
		sel.MustCompile("A" + repeat("+A", 200)).Dependencies()
	}()

	// --- physical tree
	{
		joined := sel.MustCompile("ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"]) .> FILTER(_[\"orders\"][\"amount\"] > 1)")
		before := strings.Join(joined.Dependencies(), " ")
		first := joined.PhysicalAST()
		say("program.physical.built-once", b(joined.PhysicalAST() == first))
		say("program.physical.keeps.ast", b(strings.Join(joined.Dependencies(), " ") == before)+" "+before)
		data := sel.NewNone()
		eval("ORDERS = LIST(RECORD(\"id\", 1, \"customer_id\", 7, \"amount\", 5), RECORD(\"id\", 2, \"customer_id\", 7, \"amount\", 0), RECORD(\"id\", 3, \"customer_id\", 9, \"amount\", 9)); CUSTOMERS = LIST(RECORD(\"id\", 7, \"name\", \"x\")); 0", data)
		res, _ := joined.Run(data)
		say("program.physical.run.agrees", res.Dump())
		empty := sel.NewNone()
		eval("ORDERS = LIST(); CUSTOMERS = LIST(); 0", empty)
		joined.Run(empty)
		say("program.physical.independent.of.data", b(joined.PhysicalAST() == first))
	}

	// --- host functions
	sel.RegisterFunction("host_join", 1, 3, func(a *sel.Args) *sel.Value {
		var parts []string
		for i := 0; i < a.Count(); i++ {
			parts = append(parts, a.Text(i))
		}
		return sel.NewText(strings.Join(parts, "|"))
	})

	sel.RegisterFunction("HOST_CHECK", 1, 1, func(a *sel.Args) *sel.Value {
		if a.Text(0) == "" {
			sel.Fail("E_BAD_ARG", "must not be empty", a.PosOf(0))
		}
		return sel.NewBool(true)
	})

	say("host.fn.call", eval("HOST_JOIN(\"a\", 1, \"c\")").AsText(sel.Pos{}))
	say("host.fn.case", eval("host_join(\"x\")").AsText(sel.Pos{}))

	hasHostJoin := false
	for _, n := range sel.FunctionNames() {
		if n == "HOST_JOIN" {
			hasHostJoin = true
			break
		}
	}
	say("host.fn.listed", b(hasHostJoin))
	say("host.fn.deps", strings.Join(sel.MustCompile("HOST_JOIN(X, Y)").Dependencies(), " "))
	say("host.fn.order", eval("A = 1; HOST_JOIN((A = A + 1), (A = A * 10), A)").AsText(sel.Pos{}))

	for _, tc := range []struct {
		name string
		src  string
	}{
		{"host.fn.arity", "HOST_JOIN()"},
		{"host.fn.type", "HOST_JOIN(\"a\", TRUE)"},
		{"host.fn.error", "HOST_CHECK(\"\")"},
	} {
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*sel.SelError); ok {
						say(tc.name, fmt.Sprintf("%s %d:%d", se.Code, se.Line(), se.Col()))
						return
					}
				}
				say(tc.name, "no error")
			}()
			eval(tc.src)
			say(tc.name, "no error")
		}()
	}

	for _, tc := range []struct {
		name  string
		fname string
		min   int
		max   int
	}{
		{"host.fn.refuse.builtin", "len", 1, 1},
		{"host.fn.refuse.reserved", "and", 1, 1},
		{"host.fn.refuse.underscore", "_x", 1, 1},
		{"host.fn.refuse.digit", "1x", 1, 1},
		{"host.fn.refuse.dash", "a-b", 1, 1},
		{"host.fn.refuse.min-over-max", "bad", 2, 1},
		{"host.fn.refuse.negative", "bad", -1, 0},
	} {
		r := "accepted"
		func() {
			defer func() {
				if rec := recover(); rec != nil {
					if se, ok := rec.(*sel.SelError); ok {
						r = "SelError " + se.Code
					} else {
						r = "refused"
					}
				}
			}()
			sel.RegisterFunction(tc.fname, tc.min, tc.max, func(a *sel.Args) *sel.Value {
				return sel.NewText("")
			})
		}()
		say(tc.name, r)
	}

	sel.RegisterFunction("HOST_V", 0, 0, func(a *sel.Args) *sel.Value {
		return sel.NewText("old")
	})
	{
		early := sel.MustCompile("HOST_V()")
		sel.RegisterFunction("HOST_V", 0, 0, func(a *sel.Args) *sel.Value {
			return sel.NewText("new")
		})
		earlyVal, _ := early.Run(sel.NewNone())
		say("host.fn.replace", earlyVal.AsText(sel.Pos{})+" "+eval("HOST_V()").AsText(sel.Pos{}))
	}

	// --- dependencies() is FLOW-SENSITIVE (spec/SPEC.md §8): a variable is a
	// dependency when some read of it can happen before the program has definitely
	// assigned it, in evaluation order. Assignments under a condition, a short
	// circuit, `??` or an aggregate body are not definite; `op=` and `A[k] op= x`
	// read their target; a plain `A[k] = x` creates A and reads only the index.
	deps := func(src string) string {
		d := strings.Join(sel.MustCompile(src).Dependencies(), " ")
		if d == "" {
			return "-"
		}
		return d
	}
	say("program.deps.read-before-assign", deps("A + 1; A = 2"))
	say("program.deps.compound-assign-reads", deps("X += 1"))
	say("program.deps.index-compound-reads", deps("A[1] += 1"))
	say("program.deps.index-assign-vivifies", deps("A[1] = 2"))
	say("program.deps.self-assign-reads", deps("A = A + 1"))
	say("program.deps.assign-then-read", deps("A = 1; A + B"))
	say("program.deps.conditional-assign", deps("IF(X, A = 1, 0); A"))
	say("program.deps.both-branches-assign", deps("IF(X, A = 1, A = 2); A"))
	say("program.deps.and-rhs-assign", deps("X AND (A = 1); A"))
	say("program.deps.coalesce-rhs-assign", deps("X ?? (A = 1); A"))
	say("program.deps.aggregate-body-assign", deps("MAP(L, A = _); A"))
	say("program.deps.cond-with-default-assigns", deps("COND(X, A = 1, Y, A = 2, A = 3); A"))
	say("program.deps.assign-in-argument", deps("LEFT(\"abc\", (N = 2)); N"))
	say("program.deps.compound-rhs-assign-is-too-late", deps("A += (A = 1; 2); A"))
	say("program.deps.index-expr-assign-precedes-compound-read", deps("A[(A = RECORD(\"x\", 1); \"x\")] += 2; A[\"x\"]"))
	say("program.deps.get-default-assign-not-definite", deps("GET(R, \"a\", (A = 1)); A"))
	say("program.deps.index-keys-run-in-source-order", deps("A[(K = 1)][K] = B; K"))
	say("program.deps.index-key-read-before-a-later-key-assigns", deps("A[K][(K = 1)] = B; K"))
	say("program.deps.top-arg-is-not-a-binder-in-the-three-argument-form", deps("L = LIST(1,2); TOP(L, A, (A = 1; 1))"))
	say("program.deps.bucket-key-phase-assignment-is-not-definite-for-the-projection", deps("L = LIST(1,2); BUCKET(L, G, (A = G; A), COUNT(G) + A)"))

	// --- a Program is reusable: after a caught error it runs again, and two
	// contexts are independent whatever the interleaving.
	{
		divide := sel.MustCompile("A / B")
		bad := sel.NewNone()
		eval("A = 1; B = 0; 0", bad)
		good := sel.NewNone()
		eval("A = 6; B = 3; 0", good)
		attempt := func(ctx *sel.Value) string {
			v, err := divide.Run(ctx)
			if err != nil {
				if se, ok := err.(*sel.SelError); ok {
					return fmt.Sprintf("%s %d:%d", se.Code, se.Line(), se.Col())
				}
				return "host:" + err.Error()
			}
			return v.Dump()
		}
		first := attempt(bad)
		second := attempt(good)
		third := attempt(bad)
		say("program.reuse.after-error", first+"|"+second+"|"+third)
		bump := sel.MustCompile("X = X + 1")
		a := sel.NewNone()
		eval("X = 1; 0", a)
		c := sel.NewNone()
		eval("X = 10; 0", c)
		run := func(ctx *sel.Value) string {
			v, err := bump.Run(ctx)
			if err != nil {
				return "error"
			}
			return v.AsText(sel.Pos{})
		}
		r1, r2, r3, r4 := run(a), run(c), run(a), run(c)
		say("program.reuse.two-contexts", r1+" "+r2+" "+r3+" "+r4)
	}

	// --- input the API cannot take is E_BAD_ARG, never a host panic or a
	// different SEL error (spec/SPEC.md §8). This host is statically typed: source
	// is a string and there is no native conversion, so the first three cannot be
	// posed; they print n/a with the reason, and tools/check-api.sh leaves an n/a
	// line out of the diff for that host.
	say("error.compile.non-string", "n/a (source is a string)")
	say("value.native.unsupported", "n/a (no native conversion)")
	say("value.native.fraction", "n/a (no native conversion)")
	{
		r := "accepted"
		func() {
			defer func() {
				if rec := recover(); rec != nil {
					if se, ok := rec.(*sel.SelError); ok {
						r = "SelError " + se.Code
					} else {
						r = "refused"
					}
				}
			}()
			sel.RegisterFunction("bad", 0, 0, nil)
		}()
		say("host.fn.refuse.not-callable", r)
	}
	sel.RegisterFunction("HOST_OOB", 1, 2, func(a *sel.Args) *sel.Value {
		if a.Count() > 1 {
			return sel.NewText(a.Text(1))
		}
		return sel.NewText(a.Text(5))
	})
	{
		r := "no error"
		func() {
			defer func() {
				if rec := recover(); rec != nil {
					if se, ok := rec.(*sel.SelError); ok {
						r = se.Code
					} else {
						r = "host:panic"
					}
				}
			}()
			v, err := sel.MustCompile("HOST_OOB(\"x\")").Run(sel.NewNone())
			_ = v
			if err != nil {
				if se, ok := err.(*sel.SelError); ok {
					r = se.Code
				} else {
					r = "host:error"
				}
			}
		}()
		say("host.fn.arg.out-of-range", r)
	}

	// --- a host-supplied value nested past the cap, handed to RECORD beside a
	// key that is not text. Arguments are evaluated first and coerced after (spec/SPEC.md §6.2),
	// so the key's E_NOT_TEXT wins; copying the over-deep value (E_DEPTH) happens only once the
	// arguments are known good. C++ built the pair in one expression and let the copy run first.
	{
		ctx := sel.NewNone()
		ctx.Set("V", nest(300))
		at := func(src string) string {
			_, err := sel.MustCompile(src).Run(ctx)
			if err == nil {
				return "no error"
			}
			if se, ok := err.(*sel.SelError); ok {
				return fmt.Sprintf("%s %d:%d", se.Code, se.Line(), se.Col())
			}
			return "host:error"
		}
		say("program.run.over-deep-host-value.key-error-first", at("RECORD(TRUE, V)")+"|"+at("RECORD(\"k\", V)"))
	}

	os.Stdout.WriteString(strings.Join(out, "\n") + "\n")
}
