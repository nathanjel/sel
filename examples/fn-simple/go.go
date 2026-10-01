// Goes in go/sel/builtins_text.go, inside its init() beside the other text
// builtins. Not a runnable file: this fragment compiles only in place, as part
// of package sel.
// EXAMPLE-BEGIN
Define(&Spec{
	Name: "ORD_SUFFIX",
	Min:  1,
	Max:  1,
	Fn: func(args *Args, ctx *Context) *Value {
		n := args.NonNegInt(0)
		suffix := "th"
		if tens := n % 100; tens < 11 || tens > 13 {
			switch n % 10 {
			case 1:
				suffix = "st"
			case 2:
				suffix = "nd"
			case 3:
				suffix = "rd"
			}
		}
		return NewText(fmt.Sprintf("%d%s", n, suffix))
	},
})
// EXAMPLE-END
