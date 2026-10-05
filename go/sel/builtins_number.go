// Numeric built-in functions.

package sel

import (
	"github.com/nathanjel/sel/go/internal/decimal"
)

func init() {
	Define(&Spec{
		Name: "ABS",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Abs(args.dec(0)))
		},
	})

	Define(&Spec{
		Name: "SIGN",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(int64(decimal.Sign(args.dec(0))))
		},
	})

	Define(&Spec{
		Name: "CEIL",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Ceil(args.dec(0), args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "FLOOR",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Floor(args.dec(0), args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "TRUNC",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Trunc(args.dec(0)))
		},
	})

	Define(&Spec{
		Name: "CANON",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.TrimScale(args.dec(0)))
		},
	})

	Define(&Spec{
		Name: "ROUND",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			// Argument 1 is coerced before argument 2 (SPEC §6.2: strictly left
			// to right once every argument has been evaluated).
			x := args.dec(0)
			scale := checkSizedInt(args.dec(1), "ROUND", 2, maxScale, "ROUND scale", args.PosOf(1))
			return NewNum(decimal.Round(x, scale, args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "POWER",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			x := args.dec(0)
			exp := checkSizedInt(args.dec(1), "POWER", 2, maxPower, "POWER exponent", args.PosOf(1))
			return NewNum(decimal.Power(x, exp, args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "MIN",
		Min:  1,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			best := args.dec(0)
			for i := 1; i < args.Count(); i++ {
				d := args.dec(i)
				if decimal.Cmp(d, best) < 0 {
					best = d
				}
			}
			return NewNum(best)
		},
	})

	Define(&Spec{
		Name: "MAX",
		Min:  1,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			best := args.dec(0)
			for i := 1; i < args.Count(); i++ {
				d := args.dec(i)
				if decimal.Cmp(d, best) > 0 {
					best = d
				}
			}
			return NewNum(best)
		},
	})

	Define(&Spec{
		Name: "ISNUM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(args.Val(0).LooksNumeric())
		},
	})
}
