// Top-level SQL translation, hybrid planning, and execution API.

package sql

import (
	"github.com/nathanjel/sel/go/sel"
)

// Translate translates a compiled program into a SQL expression or statement for the given dialect.
func Translate(program *sel.Program, dialect string, bindings *Bindings, options Options) (frag *Fragment, err error) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SqlError); ok {
				err = se
				frag = nil
				return
			}
			panic(r)
		}
	}()
	catalog := bindings
	if catalog == nil {
		catalog = NewBindings(nil)
	}
	t := NewTranslator(dialect, catalog, options)
	return t.Translate(program.AST()), nil
}

// TryTranslate is Translate, returning nil if translation is refused.
func TryTranslate(program *sel.Program, dialect string, bindings *Bindings, options Options) *Fragment {
	frag, err := Translate(program, dialect, bindings, options)
	if err != nil {
		return nil
	}
	return frag
}

// TranslateStatement translates a compiled relational program into a SQL statement (SELECT ...).
func TranslateStatement(program *sel.Program, dialect string, bindings *Bindings, options Options) (frag *Fragment, err error) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SqlError); ok {
				err = se
				frag = nil
				return
			}
			panic(r)
		}
	}()
	catalog := bindings
	if catalog == nil {
		catalog = NewBindings(nil)
	}
	t := NewTranslator(dialect, catalog, options)
	return t.TranslateStatement(program.AST()), nil
}

// TryTranslateStatement is TranslateStatement, returning nil if translation is refused.
func TryTranslateStatement(program *sel.Program, dialect string, bindings *Bindings, options Options) *Fragment {
	frag, err := TranslateStatement(program, dialect, bindings, options)
	if err != nil {
		return nil
	}
	return frag
}

// MustTranslate is Translate, panicking if translation is refused.
func MustTranslate(program *sel.Program, dialect string, bindings *Bindings, options Options) *Fragment {
	frag, err := Translate(program, dialect, bindings, options)
	if err != nil {
		panic(err)
	}
	return frag
}

// MustTranslateStatement is TranslateStatement, panicking if translation is refused.
func MustTranslateStatement(program *sel.Program, dialect string, bindings *Bindings, options Options) *Fragment {
	frag, err := TranslateStatement(program, dialect, bindings, options)
	if err != nil {
		panic(err)
	}
	return frag
}

// Dialects returns the sorted list of supported target SQL dialect names.
func Dialects() []string {
	return Targets()
}
