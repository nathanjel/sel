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
			return NewNum(decimal.Abs(args.Dec(0)))
		},
	})

	Define(&Spec{
		Name: "SIGN",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(int64(decimal.Sign(args.Dec(0))))
		},
	})

	Define(&Spec{
		Name: "CEIL",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Ceil(args.Dec(0)))
		},
	})

	Define(&Spec{
		Name: "FLOOR",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Floor(args.Dec(0)))
		},
	})

	Define(&Spec{
		Name: "TRUNC",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.Trunc(args.Dec(0)))
		},
	})

	Define(&Spec{
		Name: "CANON",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewNum(decimal.TrimScale(args.Dec(0)))
		},
	})

	Define(&Spec{
		Name: "ROUND",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			scale := CheckSizedInt(args.Dec(1), "ROUND", 2, MaxScale, "ROUND scale", args.PosOf(1))
			return NewNum(decimal.Round(args.Dec(0), scale, args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "POWER",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			exp := CheckSizedInt(args.Dec(1), "POWER", 2, MaxPower, "POWER exponent", args.PosOf(1))
			return NewNum(decimal.Power(args.Dec(0), exp, args.Pos(), fail))
		},
	})

	Define(&Spec{
		Name: "MIN",
		Min:  1,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			best := args.Dec(0)
			for i := 1; i < args.Count(); i++ {
				d := args.Dec(i)
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
			best := args.Dec(0)
			for i := 1; i < args.Count(); i++ {
				d := args.Dec(i)
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
