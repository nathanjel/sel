// Core control and null built-in functions.

package sel

import (
	"strings"
)

func init() {
	Define(&Spec{
		Name: "IF",
		Min:  2,
		Max:  3,
		Lazy: true,
		Fn: func(args *Args, ctx *Context) *Value {
			if args.Bool(0) {
				return args.Val(1)
			}
			if args.Count() == 3 {
				return args.Val(2)
			}
			return newTextOwned("")
		},
	})

	Define(&Spec{
		Name: "COND",
		Min:  3,
		Max:  -1,
		Lazy: true,
		Fn: func(args *Args, ctx *Context) *Value {
			last := args.Count() - 1
			for i := 0; i < last; i += 2 {
				if args.Bool(i) {
					return args.Val(i + 1)
				}
			}
			return args.Val(last)
		},
	})

	Define(&Spec{
		Name: "ABORT",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			fail("E_ABORT", args.Text(0), args.PosOf(0))
			return nil
		},
	})

	Define(&Spec{
		Name: "IS_NULL",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(args.Val(0).IsNull())
		},
	})

	Define(&Spec{
		Name: "IS_NOT_NULL",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(!args.Val(0).IsNull())
		},
	})

	Define(&Spec{
		Name: "COALESCE",
		Min:  1,
		Max:  -1,
		Lazy: true,
		Fn: func(args *Args, ctx *Context) *Value {
			for i := 0; i < args.Count(); i++ {
				v := args.Val(i)
				if !v.IsNull() {
					return v
				}
			}
			return NewNull()
		},
	})

	Define(&Spec{
		Name: "GET",
		Min:  2,
		Max:  3,
		Lazy: true,
		Fn: func(args *Args, ctx *Context) *Value {
			target := args.Val(0)
			key := args.Text(1)
			if !target.IsNull() && target.Has(key) {
				return target.Get(key)
			}
			if args.Count() > 2 {
				return args.Val(2)
			}
			return NewNull()
		},
	})

	Define(&Spec{
		Name: "PATH",
		Min:  2,
		Max:  3,
		Lazy: true,
		Fn: func(args *Args, ctx *Context) *Value {
			target := args.Val(0)
			pathStr := args.Text(1)
			if pathStr == "" {
				return target
			}
			segments := strings.Split(pathStr, ".")
			cur := target
			for _, seg := range segments {
				if cur.IsNull() || !cur.Has(seg) {
					if args.Count() > 2 {
						return args.Val(2)
					}
					return NewNull()
				}
				cur = cur.Get(seg)
			}
			return cur
		},
	})

	Define(&Spec{
		Name: "IS_BLANK",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(args.Val(0).IsVacuous())
		},
	})

	Define(&Spec{
		Name: "IS_PRESENT",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(!args.Val(0).IsVacuous())
		},
	})
}
