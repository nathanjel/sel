// Text built-in functions. All indexing and lengths are Unicode code points.

package sel

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/internal/utf8"
)

func isSelSpace(r rune) bool {
	return r == ' ' || r == '\t' || r == '\r' || r == '\n'
}

func trimText(s string, left, right bool) string {
	runes := []rune(s)
	a := 0
	b := len(runes)
	if left {
		for a < b && isSelSpace(runes[a]) {
			a++
		}
	}
	if right {
		for b > a && isSelSpace(runes[b-1]) {
			b--
		}
	}
	return string(runes[a:b])
}

func pad(args *Args, left bool) *Value {
	sRunes := []rune(args.Text(0))
	width := int(args.NonNegInt(1))
	fillRunes := []rune(args.Text(2))
	if len(fillRunes) == 0 {
		fail("E_BAD_ARG", "pad fill must not be empty", args.PosOf(2))
	}
	if len(sRunes) >= width {
		return NewTextOwned(string(sRunes))
	}
	checkTextLen(int64(width), args.Name()+"'s result", args.Pos())
	need := width - len(sRunes)
	padding := make([]rune, need)
	for i := 0; i < need; i++ {
		padding[i] = fillRunes[i%len(fillRunes)]
	}
	if left {
		return NewTextOwned(string(append(padding, sRunes...)))
	}
	return NewTextOwned(string(append(sRunes, padding...)))
}

func init() {
	Define(&Spec{
		Name: "LEN",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(int64(len([]rune(args.Text(0)))))
		},
	})

	Define(&Spec{
		Name: "LEFT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			runes := []rune(args.Text(0))
			n := int(args.NonNegInt(1))
			if n > len(runes) {
				n = len(runes)
			}
			return NewTextOwned(string(runes[:n]))
		},
	})

	Define(&Spec{
		Name: "RIGHT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			runes := []rune(args.Text(0))
			n := int(args.NonNegInt(1))
			if n > len(runes) {
				n = len(runes)
			}
			return NewTextOwned(string(runes[len(runes)-n:]))
		},
	})

	Define(&Spec{
		Name: "SUBSTR",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			runes := []rune(args.Text(0))
			start := args.Int(1)
			if start < 1 {
				fail("E_RANGE", "SUBSTR start is 1-based and must be at least 1", args.PosOf(1))
			}
			from := int(start - 1)
			size := len(runes)
			if from >= size {
				return NewTextOwned("")
			}
			if args.Count() == 2 {
				return NewTextOwned(string(runes[from:]))
			}
			n := args.NonNegInt(2)
			to := from + int(n)
			if n > int64(size-from) || to > size {
				to = size
			}
			return NewTextOwned(string(runes[from:to]))
		},
	})

	Define(&Spec{
		Name: "FIND",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			needle := []rune(args.Text(0))
			hay := []rune(args.Text(1))
			if len(needle) == 0 {
				fail("E_BAD_ARG", "FIND needle must not be empty", args.PosOf(0))
			}
			from := 0
			if args.Count() == 3 {
				f := args.Int(2)
				if f < 1 {
					fail("E_RANGE", "FIND start is 1-based and must be at least 1", args.PosOf(2))
				}
				from = int(f - 1)
			}
			if from > len(hay) {
				return NewInt(0)
			}
			for i := from; i <= len(hay)-len(needle); i++ {
				match := true
				for j := range needle {
					if hay[i+j] != needle[j] {
						match = false
						break
					}
				}
				if match {
					return NewInt(int64(i + 1))
				}
			}
			return NewInt(0)
		},
	})

	Define(&Spec{
		Name: "REPLACE",
		Min:  3,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			needle := args.Text(0)
			repl := args.Text(1)
			hay := args.Text(2)
			if needle == "" {
				fail("E_BAD_ARG", "REPLACE needle must not be empty", args.PosOf(0))
			}
			if n := strings.Count(hay, needle); n > 0 && runeLen(repl) > runeLen(needle) {
				// Compared in code points, as the cap is: "ab" is two code points but
				// fewer bytes than "😀", so a byte comparison missed this growth.
				// Length after the replacement, before it is built (SPEC §6.4).
				grown := satAdd(runeLen(hay), satMul(int64(n), runeLen(repl)-runeLen(needle)))
				checkTextLen(grown, "REPLACE's result", args.Pos())
			}
			return NewText(strings.ReplaceAll(hay, needle, repl))
		},
	})

	Define(&Spec{
		Name: "SPLIT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			hay := args.Text(0)
			sep := args.Text(1)
			if sep == "" {
				fail("E_BAD_ARG", "SPLIT separator must not be empty", args.PosOf(1))
			}
			checkCollection(int64(strings.Count(hay, sep))+1, "SPLIT's result", args.Pos())
			parts := strings.Split(hay, sep)
			vals := make([]*Value, len(parts))
			for i, p := range parts {
				vals[i] = NewText(p)
			}
			return NewListOwned(vals)
		},
	})

	Define(&Spec{
		Name: "TRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewTextOwned(trimText(args.Text(0), true, true))
		},
	})

	Define(&Spec{
		Name: "LTRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewTextOwned(trimText(args.Text(0), true, false))
		},
	})

	Define(&Spec{
		Name: "RTRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewTextOwned(trimText(args.Text(0), false, true))
		},
	})

	Define(&Spec{
		Name: "UPPER",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewTextOwned(utf8.AsciiUpper(args.Text(0)))
		},
	})

	Define(&Spec{
		Name: "LOWER",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewTextOwned(utf8.AsciiLower(args.Text(0)))
		},
	})

	Define(&Spec{
		Name: "BACKWARDS",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			runes := []rune(args.Text(0))
			for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
				runes[i], runes[j] = runes[j], runes[i]
			}
			return NewTextOwned(string(runes))
		},
	})

	Define(&Spec{
		Name: "REPEAT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			s := args.Text(0)
			n := args.NonNegInt(1)
			// The result is measured, not the argument: an empty text repeated
			// any number of times is empty (SPEC §6.4).
			if s == "" || n == 0 {
				return NewText("")
			}
			checkTextLen(satMul(runeLen(s), n), "REPEAT's result", args.Pos())
			return NewText(strings.Repeat(s, int(n)))
		},
	})

	Define(&Spec{
		Name: "PADL",
		Min:  3,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			return pad(args, true)
		},
	})

	Define(&Spec{
		Name: "PADR",
		Min:  3,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			return pad(args, false)
		},
	})

	Define(&Spec{
		Name: "CHAR",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			n := args.Int(0)
			if n < 0 || n > 0x10FFFF || (0xD800 <= n && n <= 0xDFFF) {
				fail("E_RANGE", fmt.Sprintf("%d is not an encodable code point", n), args.PosOf(0))
			}
			return NewTextOwned(string(rune(n)))
		},
	})

	Define(&Spec{
		Name: "CODE",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			s := args.Text(0)
			if s == "" {
				fail("E_RANGE", "CODE of empty text", args.PosOf(0))
			}
			runes := []rune(s)
			return NewInt(int64(runes[0]))
		},
	})
}
