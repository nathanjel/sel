package sql

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

type Part struct {
	IsSlot bool
	Sql    string
	Slot   int // 1-based index into params
}

// Fragment is a translation: SQL text with slots for its parameters, and what
// is known about it.
//
// "Exact" means two different things here, kept apart by their names. The
// Exact FIELD is about text: the fragment's TEXT already compares byte for byte
// (an exact column, a binary collation), so a comparison needs no COLLATE or
// cast around it. It is what a builder (DefineBuilder) reads off the fragments
// it is given; a translation's result does not carry it. IsExact, the METHOD,
// is about a translation as a whole: no map entry it used carries a caveat
// (docs/sql.md, `caveats`). It is what a caller reads off Translate's result;
// the fragments a builder is given carry no caveats of their own.
type Fragment struct {
	Parts      []Part
	Kind       SqlKind
	Dialect    string
	Params     []*sel.Value
	ParamKinds []SqlKind
	// Caveats names every inexact map entry the translation used; empty when
	// IsExact.
	Caveats []string
	// Exact: the fragment's text compares bytes exactly (see the type's
	// documentation); not IsExact.
	Exact             bool
	Sargable          bool
	Guard             bool
	Prefilter         *Fragment
	SeparatePrefilter bool
	Canonical         bool
}

func NewFragment(parts []Part, kind SqlKind, dialect string, params []*sel.Value, paramKinds []SqlKind, caveats []string) *Fragment {
	return &Fragment{
		Parts:      parts,
		Kind:       kind,
		Dialect:    dialect,
		Params:     params,
		ParamKinds: paramKinds,
		Caveats:    caveats,
	}
}

// IsExact reports a translation with no caveats: SQL that means exactly what
// SEL means, whatever the server. It is not the Exact field, which is about text
// comparison only.
func (f *Fragment) IsExact() bool {
	return len(f.Caveats) == 0
}

func (f *Fragment) IsInline(slot int) bool {
	kind := KindText
	if slot-1 < len(f.ParamKinds) {
		kind = f.ParamKinds[slot-1]
	}
	return kind == KindNum || kind == KindBool || kind == KindBin
}

func (f *Fragment) Bindings() []*sel.Value {
	var out []*sel.Value
	for _, p := range f.Parts {
		if p.IsSlot && !f.IsInline(p.Slot) {
			if p.Slot-1 >= 0 && p.Slot-1 < len(f.Params) {
				out = append(out, f.Params[p.Slot-1])
			}
		}
	}
	return out
}

func (f *Fragment) AsValue(mode Mode) string {
	if f.Kind == KindList {
		refuse("E_SQL_SHAPE", "this expression yields a list, and a SQL expression is a scalar", Pos{})
	}
	if f.Kind == KindStatement {
		refuse("E_SQL_SHAPE", "this expression yields a statement, and a SQL expression is a scalar; use asStatement()", Pos{})
	}
	return f.Join(mode)
}

func (f *Fragment) AsStatement(mode Mode) string {
	if f.Kind != KindStatement {
		refuse("E_SQL_SHAPE", fmt.Sprintf("expected STATEMENT fragment, got %s; use asValue() or asCondition()", f.Kind), Pos{})
	}
	return f.Join(mode)
}

func (f *Fragment) AsCondition(mode Mode) string {
	if f.Kind == KindBool {
		return f.Join(mode)
	}
	refuse("E_SQL_SHAPE", fmt.Sprintf("a condition must be BOOL, and this expression is %s; SQL has no truthiness and neither does SEL", f.Kind), Pos{})
	return ""
}

func (f *Fragment) Join(mode Mode) string {
	var sb strings.Builder
	nth := 0

	for _, p := range f.Parts {
		if !p.IsSlot {
			sb.WriteString(p.Sql)
			continue
		}

		kind := KindText
		if p.Slot-1 < len(f.ParamKinds) {
			kind = f.ParamKinds[p.Slot-1]
		}
		var val *sel.Value
		if p.Slot-1 >= 0 && p.Slot-1 < len(f.Params) {
			val = f.Params[p.Slot-1]
		} else {
			val = sel.NewNull()
		}

		if mode != ModeInline && f.IsInline(p.Slot) {
			sb.WriteString(formatLiteral(f.Dialect, val, kind, Pos{}))
			continue
		}

		nth++
		switch mode {
		case ModeInline:
			sb.WriteString(formatLiteral(f.Dialect, val, kind, Pos{}))
		case ModeParams:
			sb.WriteString(placeholder(f.Dialect, nth))
		case ModeDebug:
			sb.WriteString(fmt.Sprintf("~%d~", nth))
		default:
			panic(fmt.Sprintf("unknown render mode: %v", mode))
		}
	}

	return sb.String()
}
