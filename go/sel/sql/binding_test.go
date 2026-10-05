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

// Fragment.Exact and Fragment.IsExact are two properties. Exact is about text
// and is what a builder reads off its arguments; IsExact is about the whole
// translation and is what a caller reads off the result.
func TestFragmentExactAndIsExactAreDifferentProperties(t *testing.T) {
	defer Reset()
	DefineDialect("exactness-probe", map[string]interface{}{"extends": "mariadb"})
	var argExact []bool
	DefineBuilder("exactness-probe", "funcs", "LEN", func(emit *Emit, args []*Fragment, _ Pos) *Fragment {
		argExact = append(argExact, args[0].Exact)
		return NewFragment(args[0].Parts, KindNum, emit.Dialect(), nil, nil, nil)
	})
	b := NewBindings(map[string]*Binding{
		"E": ColumnBinding("e", "", KindText, true, false, false, "", "", false),
		"T": ColumnBinding("t", "", KindText, false, false, false, "", "", false),
		"N": ColumnBinding("n", "", KindNum, false, false, false, "", "", false),
	})
	for _, c := range []struct {
		src      string
		argExact bool
		isExact  bool
	}{
		{`LEN(E)`, true, true},           // byte-exact text, nothing inexact
		{`LEN(T)`, false, true},          // collated text, still nothing inexact
		{`LEN(T & N / 3)`, false, false}, // division-scale is a caveat
	} {
		argExact = nil
		f, err := Translate(sel.MustCompile(c.src), "exactness-probe", b, Options{})
		if err != nil {
			t.Fatalf("%s: %v", c.src, err)
		}
		if len(argExact) != 1 || argExact[0] != c.argExact || f.IsExact() != c.isExact {
			t.Errorf("%s: argument Exact %v, result IsExact %v (caveats %v); want %v, %v",
				c.src, argExact, f.IsExact(), f.Caveats, c.argExact, c.isExact)
		}
	}
}
