// Text built-in functions. All indexing and lengths are Unicode code points.

package sel

import (
	"fmt"
	"strings"
	ustd "unicode/utf8"

	"github.com/nathanjel/sel/go/internal/utf8"
)

// isSelSpace is SEL's whitespace (spec §2): the lexer's isSpace.
func isSelSpace(r rune) bool { return isSpace(r) }

// Code-point indexing without a []rune round trip. Text is valid UTF-8,
// so a byte offset is found by walking boundaries; ASCII bytes take the one-step
// branch. A result far smaller than its source is copied, so a one-character LEFT
// of a huge text does not keep the huge text alive.

// runeOffset is the byte offset after the first n code points of s (len(s) when s
// has fewer).
func runeOffset(s string, n int) int {
	i := 0
	for i < len(s) && n > 0 {
		if s[i] < utf8Self {
			i++
		} else {
			_, w := ustd.DecodeRuneInString(s[i:])
			i += w
		}
		n--
	}
	return i
}

// runeOffsetFromEnd is the byte offset where the last n code points of s begin
// (0 when s has fewer).
func runeOffsetFromEnd(s string, n int) int {
	i := len(s)
	for i > 0 && n > 0 {
		if s[i-1] < utf8Self {
			i--
		} else {
			_, w := ustd.DecodeLastRuneInString(s[:i])
			i -= w
		}
		n--
	}
	return i
}

const utf8Self = 0x80

// sliceText is s[a:b] as a string that does not pin a much larger source.
func sliceText(s string, a, b int) string {
	if b-a < len(s)/2 {
		return strings.Clone(s[a:b])
	}
	return s[a:b]
}

// trimText trims the four characters SEL calls whitespace, all ASCII, so a byte
// scan from each end is exact for any valid text.
func trimText(s string, left, right bool) string {
	a, b := 0, len(s)
	if left {
		for a < b && isSelSpace(rune(s[a])) {
			a++
		}
	}
	if right {
		for b > a && isSelSpace(rune(s[b-1])) {
			b--
		}
	}
	return sliceText(s, a, b)
}

func pad(args *Args, left bool) *Value {
	s := args.Text(0)
	width := int(args.NonNegInt(1))
	fill := args.Text(2)
	if fill == "" {
		fail("E_BAD_ARG", "pad fill must not be empty", args.PosOf(2))
	}
	have := ustd.RuneCountInString(s)
	if have >= width {
		return newTextOwned(s)
	}
	checkTextLen(int64(width), args.Name()+"'s result", args.Pos())
	need := width - have
	// The padding is the fill repeated, cut after `need` code points: whole
	// copies by strings.Repeat, then the leading code points of one more.
	fillLen := ustd.RuneCountInString(fill)
	var padding string
	if need <= fillLen {
		padding = fill[:runeOffset(fill, need)]
	} else {
		padding = strings.Repeat(fill, need/fillLen) + fill[:runeOffset(fill, need%fillLen)]
	}
	if left {
		return newTextOwned(padding + s)
	}
	return newTextOwned(s + padding)
}

// clampInt narrows a count to an int, saturating: a larger count than any text can
// have behaves as "all of it".
func clampInt(n int64) int {
	if n > int64(1<<31-1) {
		return 1<<31 - 1
	}
	return int(n)
}

func init() {
	Define(&Spec{
		Name: "LEN",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(runeLen(args.Text(0)))
		},
	})

	Define(&Spec{
		Name: "LEFT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			str := args.Text(0)
			n := int(args.NonNegInt(1))
			return newTextOwned(sliceText(str, 0, runeOffset(str, n)))
		},
	})

	Define(&Spec{
		Name: "RIGHT",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			str := args.Text(0)
			n := int(args.NonNegInt(1))
			return newTextOwned(sliceText(str, runeOffsetFromEnd(str, n), len(str)))
		},
	})

	Define(&Spec{
		Name: "SUBSTR",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			str := args.Text(0)
			start := args.Int(1)
			if start < 1 {
				fail("E_RANGE", "SUBSTR start is 1-based and must be at least 1", args.PosOf(1))
			}
			// `start-1` code points are skipped by walking, clamped to the text:
			// a start past the end is the empty text.
			from := runeOffset(str, clampInt(start-1))
			if from >= len(str) {
				return newTextOwned("")
			}
			if args.Count() == 2 {
				return newTextOwned(sliceText(str, from, len(str)))
			}
			n := args.NonNegInt(2)
			to := from + runeOffset(str[from:], clampInt(n))
			return newTextOwned(sliceText(str, from, to))
		},
	})

	Define(&Spec{
		Name: "FIND",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			needle := args.Text(0)
			hay := args.Text(1)
			if needle == "" {
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
			// The search runs on bytes: strings.Index instead of a
			// rune-by-rune comparison over two []rune copies. The 1-based start is
			// a code-point index, so it is walked to a byte offset once, and the
			// answer is converted back by counting the code points before the hit.
			byteFrom := 0
			if from > 0 {
				i, n := 0, 0
				for i < len(hay) && n < from {
					_, w := ustd.DecodeRuneInString(hay[i:])
					i += w
					n++
				}
				if n < from {
					return NewInt(0) // the start is past the end
				}
				byteFrom = i
			}
			idx := strings.Index(hay[byteFrom:], needle)
			if idx < 0 {
				return NewInt(0)
			}
			return NewInt(int64(from + ustd.RuneCountInString(hay[byteFrom:byteFrom+idx]) + 1))
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
			// hay, needle and repl are valid text, and a replacement only swaps whole
			// code points for whole code points: the result needs no second scan.
			return newTextOwned(strings.ReplaceAll(hay, needle, repl))
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
				vals[i] = newTextOwned(p) // a piece between two valid separators is valid
			}
			return newListOwned(vals)
		},
	})

	Define(&Spec{
		Name: "TRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return newTextOwned(trimText(args.Text(0), true, true))
		},
	})

	Define(&Spec{
		Name: "LTRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return newTextOwned(trimText(args.Text(0), true, false))
		},
	})

	Define(&Spec{
		Name: "RTRIM",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return newTextOwned(trimText(args.Text(0), false, true))
		},
	})

	Define(&Spec{
		Name: "UPPER",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return newTextOwned(utf8.AsciiUpper(args.Text(0)))
		},
	})

	Define(&Spec{
		Name: "LOWER",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return newTextOwned(utf8.AsciiLower(args.Text(0)))
		},
	})

	Define(&Spec{
		Name: "BACKWARDS",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			str := args.Text(0)
			var sb strings.Builder
			sb.Grow(len(str))
			for i := len(str); i > 0; {
				if str[i-1] < utf8Self {
					i--
					sb.WriteByte(str[i])
					continue
				}
				r, w := ustd.DecodeLastRuneInString(str[:i])
				i -= w
				sb.WriteRune(r)
			}
			return newTextOwned(sb.String())
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
			return newTextOwned(strings.Repeat(s, int(n)))
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
			return newTextOwned(string(rune(n)))
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
			r, _ := ustd.DecodeRuneInString(s)
			return NewInt(int64(r))
		},
	})
}
