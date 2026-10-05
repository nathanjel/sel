// Top-level SQL translation, hybrid planning, and execution API.

package sql

import (
	"github.com/nathanjel/sel/go/sel"
)

// Translate translates a compiled program into a SQL expression or statement for the given dialect.
func Translate(program *sel.Program, dialect string, bindings *Bindings, options Options) (*Fragment, error) {
	var frag *Fragment
	refusal, selErr := catch(func() {
		catalog := bindings
		if catalog == nil {
			catalog = NewBindings(nil)
		}
		frag = newTranslator(dialect, catalog, options).Translate(program.AST())
	})
	if selErr != nil {
		panic(selErr) // as before: only a refusal is this call's error
	}
	if refusal != nil {
		return nil, refusal
	}
	return frag, nil
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
func TranslateStatement(program *sel.Program, dialect string, bindings *Bindings, options Options) (*Fragment, error) {
	var frag *Fragment
	refusal, selErr := catch(func() {
		catalog := bindings
		if catalog == nil {
			catalog = NewBindings(nil)
		}
		frag = newTranslator(dialect, catalog, options).TranslateStatement(program.AST())
	})
	if selErr != nil {
		panic(selErr) // as before: only a refusal is this call's error
	}
	if refusal != nil {
		return nil, refusal
	}
	return frag, nil
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
