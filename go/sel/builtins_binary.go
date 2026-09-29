// Binary built-in functions.

package sel

import (
	"encoding/hex"
	"fmt"
	"hash/crc32"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
)

const b64Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

var b64Index map[byte]int

func init() {
	b64Index = make(map[byte]int, len(b64Alphabet))
	for i := 0; i < len(b64Alphabet); i++ {
		b64Index[b64Alphabet[i]] = i
	}

	Define(&Spec{
		Name: "BLEN",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(int64(len(args.Bytes(0))))
		},
	})

	Define(&Spec{
		Name: "TO_UTF8",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			checkTextLen(int64(len(b)), "TO_UTF8's result", args.Pos())
			return NewBin(b)
		},
	})

	Define(&Spec{
		Name: "FROM_UTF8",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			s := string(b)
			utf8.ValidateText(s, args.PosOf(0), fail)
			return NewTextOwned(s)
		},
	})

	Define(&Spec{
		Name: "TO_HEX",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			checkTextLen(satMul(int64(len(b)), 2), "TO_HEX's result", args.Pos())
			return NewTextOwned(hex.EncodeToString(b))
		},
	})

	Define(&Spec{
		Name: "FROM_HEX",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			s := args.Text(0)
			pos := args.PosOf(0)
			if len(s)%2 != 0 {
				fail("E_BAD_ARG", "FROM_HEX needs an even number of digits", pos)
			}
			for i := 0; i < len(s); i++ {
				c := s[i]
				if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
					fail("E_BAD_ARG", fmt.Sprintf("FROM_HEX: %q is not hex", s[i&^1:i&^1+2]), pos)
				}
			}
			decoded, err := hex.DecodeString(s)
			if err != nil {
				fail("E_BAD_ARG", err.Error(), pos)
			}
			return NewBinOwned(decoded)
		},
	})

	Define(&Spec{
		Name: "ENCODE_BASE64",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			checkTextLen(satMul((int64(len(b))+2)/3, 4), "ENCODE_BASE64's result", args.Pos())
			var out []byte
			for i := 0; i < len(b); i += 3 {
				b0 := uint32(b[i])
				b1 := uint32(0)
				if i+1 < len(b) {
					b1 = uint32(b[i+1])
				}
				b2 := uint32(0)
				if i+2 < len(b) {
					b2 = uint32(b[i+2])
				}
				n := (b0 << 16) | (b1 << 8) | b2
				out = append(out, b64Alphabet[(n>>18)&63])
				out = append(out, b64Alphabet[(n>>12)&63])
				if i+1 < len(b) {
					out = append(out, b64Alphabet[(n>>6)&63])
				} else {
					out = append(out, '=')
				}
				if i+2 < len(b) {
					out = append(out, b64Alphabet[n&63])
				} else {
					out = append(out, '=')
				}
			}
			return NewTextOwned(string(out))
		},
	})

	Define(&Spec{
		Name: "DECODE_BASE64",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			s := args.Text(0)
			pos := args.PosOf(0)
			if len(s)%4 != 0 {
				fail("E_BAD_ARG", "DECODE_BASE64 needs a length that is a multiple of 4", pos)
			}
			var out []byte
			for i := 0; i < len(s); i += 4 {
				var quad [4]int
				padding := 0
				for k := 0; k < 4; k++ {
					ch := s[i+k]
					if ch == '=' {
						if i+4 < len(s) || k < 2 {
							fail("E_BAD_ARG", "misplaced base64 padding", pos)
						}
						padding++
						quad[k] = 0
						continue
					}
					if padding > 0 {
						fail("E_BAD_ARG", "misplaced base64 padding", pos)
					}
					v, ok := b64Index[ch]
					if !ok {
						fail("E_BAD_ARG", fmt.Sprintf("invalid base64 character %q", ch), pos)
					}
					quad[k] = v
				}
				n := (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3]
				out = append(out, byte((n>>16)&255))
				if padding < 2 {
					out = append(out, byte((n>>8)&255))
				}
				if padding < 1 {
					out = append(out, byte(n&255))
				}
			}
			return NewBinOwned(out)
		},
	})

	Define(&Spec{
		Name: "CRC32",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			crc := crc32.ChecksumIEEE(b)
			return NewTextOwned(fmt.Sprintf("%08x", crc))
		},
	})

	Define(&Spec{
		Name: "BTL",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			b := args.Bytes(0)
			checkCollection(int64(len(b)), "BTL's result", args.Pos())
			items := make([]*Value, len(b))
			for i, v := range b {
				items[i] = NewInt(int64(v))
			}
			return NewListOwned(items)
		},
	})

	Define(&Spec{
		Name: "LTB",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			v := args.Val(0)
			var items []*Value
			if v.Size() > 0 {
				items = v.Values()
			} else if v.Kind == KindNone {
				// An empty list (or NULL, which is the same value) is the empty BIN,
				// so LTB(BTL(x)) returns x for every BIN x (SPEC §7.7).
				items = nil
			} else {
				items = []*Value{v}
			}
			out := make([]byte, len(items))
			for i, item := range items {
				d := item.AsDecimal(args.PosOf(0))
				// An integral value of any scale is a whole number (1.0, "1.0");
				// a fractional one is not an integer at all.
				if !decimal.IsInteger(d) {
					fail("E_NOT_INT", fmt.Sprintf("LTB element %d must be a whole number", i+1), args.PosOf(0))
				}
				n := decimal.ToSafeInt(d)
				if n < 0 || n > 255 {
					fail("E_RANGE", fmt.Sprintf("LTB element %d is not a byte value", i+1), args.PosOf(0))
				}
				out[i] = byte(n)
			}
			return NewBinOwned(out)
		},
	})
}
