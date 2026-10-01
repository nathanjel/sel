// Goes in go/sel/builtins_aggregate.go, inside its init() beside ALL and ANY.
// Not a runnable file: this is a fragment that compiles only in place, as part
// of package sel. See README.md beside it.
// EXAMPLE-BEGIN
Define(&Spec{
	Name:  "FIRST",
	Min:   2,
	Max:   3,
	Lazy:  true, // receives the nodes and evaluates the body itself
	Binds: true,
	Fn: func(args *Args, ctx *Context) *Value {
		binder, body := "_", args.Node(1)
		if args.Count() == 3 {
			binder, body = args.Symbol(1), args.Node(2)
		}

		// Elements() is spec §7.3's view of a value, point 4: a scalar is a list
		// of itself, a childless NONE is empty.
		for _, e := range args.Val(0).Elements() {
			ctx.PushFrame(map[string]*Value{binder: e.Val, "_K": NewText(e.Key)})
			// A body that raises panics with its *SelError; the frame must not
			// outlive it, so pop it before the check can leave.
			hit := func() bool {
				defer ctx.PopFrame()
				return args.EvalNode(body).AsBool(body.Pos)
			}()
			if hit {
				return e.Val // the element's handle, as GET answers; assignment copies
			}
		}
		return NewText("")
	},
})
// EXAMPLE-END
