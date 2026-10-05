package sql

import (
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// A NUM-typed value binding of a BOOL or a BIN is the binding's refusal,
// E_SQL_BINDING, like a TEXT that is not a number -- not the value accessor's
// E_NOT_TEXT.
func TestValueBindingOfANonTextDeclaredNum(t *testing.T) {
	num := KindNum
	for _, v := range []*sel.Value{sel.NewBool(true), sel.NewBool(false), sel.NewBin([]byte("ab")), sel.NewText("x")} {
		code := func() (code string) {
			defer func() {
				switch e := recover().(type) {
				case *SqlError:
					code = "SqlError " + e.Code
				case *sel.SelError:
					code = "SelError " + e.Code
				case nil:
					code = "no error"
				default:
					panic(e)
				}
			}()
			ValueBinding(v, &num)
			return
		}()
		if code != "SqlError E_SQL_BINDING" {
			t.Errorf("%s declared NUM: %s", v.Dump(), code)
		}
	}
}
