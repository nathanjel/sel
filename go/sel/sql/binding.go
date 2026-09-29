package sql

import (
	"fmt"
	"sort"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

type BindingKind int

const (
	BindingKindColumn BindingKind = iota
	BindingKindColumns
	BindingKindRelation
	BindingKindValue
)

type ColumnSpec struct {
	IsRaw         bool
	Raw           string
	Column        string
	Table         string
	Type          SqlKind
	Exact         bool
	Sargable      bool
	Guard         bool
	Collation     string
	Prefilter     string
	SplitSargable bool
	Canonical     bool
	// Unavailable is why a derived table has no column for this field: a raw
	// field is an expression, and a subquery does not carry it forward.
	Unavailable string
}

type RelationFrom struct {
	IsRaw bool
	Raw   string
	Table string
}

type FieldEntry struct {
	Name    string
	Binding *Binding
}

type RelationSpec struct {
	UniqueKey  string
	From       RelationFrom
	Alias      string
	Fields     map[string]ColumnSpec
	FieldOrder []string
	Scalar     string
	Correlate  string
	Prefilter  string
}

func (r *RelationSpec) Field(name string) *ColumnSpec {
	key := utf8.AsciiUpper(name)
	if f, ok := r.Fields[key]; ok {
		return &f
	}
	return nil
}

type Binding struct {
	Kind      BindingKind
	Column    ColumnSpec
	Columns   []ColumnSpec
	Relation  RelationSpec
	Val       *sel.Value
	ValueType *SqlKind
}

func checkName(what, v string) {
	if v == "" {
		Refuse("E_SQL_BINDING", fmt.Sprintf("a binding has an empty %s name", what), Pos{})
	}
	if strings.ContainsRune(v, 0) {
		Refuse("E_SQL_BINDING", fmt.Sprintf("a binding has a %s name containing a NUL, which no dialect can quote", what), Pos{})
	}
}

func checkNumeric(where string, v *sel.Value) {
	if v.Size() > 0 {
		for _, e := range v.Entries() {
			checkNumeric(fmt.Sprintf("%s[%q]", where, e.Key), e.Val)
		}
		return
	}
	if v.IsNone() {
		return
	}
	if v.Kind != sel.KindText || !v.LooksNumeric() {
		shown := v.AsText(Pos{})
		if v.Kind == sel.KindBool {
			if v.AsBool(Pos{}) {
				shown = "TRUE"
			} else {
				shown = "FALSE"
			}
		}
		Refuse("E_SQL_BINDING", fmt.Sprintf("%s declares type NUM, which asks for it to be emitted unquoted, but %q is not a number", where, shown), Pos{})
	}
	text := v.AsText(Pos{})
	d := decimal.Parse(text, utf8.Pos{}, func(c, m string, p utf8.Pos) {})
	if d == nil || decimal.Format(d) != text {
		Refuse("E_SQL_BINDING", fmt.Sprintf("%s declares type NUM and is %q, which is not how SEL canonicalises it; write canonical decimals or omit type: NUM", where, text), Pos{})
	}
}

// columnFlags folds what the application may say about a column's comparison:
// a collation spelling into the exact/sargable flags, a split-sargable request
// into the `separate` prefilter, and validates the spellings, as the dynamic
// hosts' bindings do (GO-C19). Before this the typed constructors stored all of
// it raw: `collation="sargable"` did nothing and `prefilter="bogus"` was accepted.
func columnFlags(exact, sargable bool, collation, prefilter string, splitSargable bool) (bool, bool, string) {
	if collation != "" {
		switch strings.ToLower(collation) {
		case "binary", "exact":
			exact = true
		case "sargable", "prefilter":
			sargable = true
		case "default", "none":
		default:
			Refuse("E_SQL_BINDING", fmt.Sprintf("unknown collation '%s'; use 'binary', 'exact', 'sargable', or 'default'", collation), Pos{})
		}
	}
	if splitSargable && prefilter == "" {
		prefilter = "separate"
	}
	return exact, sargable, checkPrefilter(prefilter)
}

// checkPrefilter normalises the spellings of a prefilter strategy and refuses
// the rest.
func checkPrefilter(p string) string {
	switch strings.ToLower(p) {
	case "":
		return ""
	case "separate", "splitsargable", "split_sargable":
		return "separate"
	case "inline":
		return "inline"
	}
	Refuse("E_SQL_BINDING", fmt.Sprintf("unknown prefilter '%s'; use 'separate' or 'inline'", p), Pos{})
	return ""
}

// checkColumnType: a column holds a scalar. LIST and STATEMENT are kinds of
// expression the translator produces, and no database column has them.
func checkColumnType(typ SqlKind) {
	if typ == KindList || typ == KindStatement {
		Refuse("E_SQL_BINDING", fmt.Sprintf("a column cannot have type %s; a column holds one scalar (NUM, TEXT, BOOL, BIN), or nothing is declared", typ), Pos{})
	}
}

func ColumnBinding(column, table string, typ SqlKind, exact, sargable, guard bool, collation, prefilter string, splitSargable bool) *Binding {
	checkColumnType(typ)
	checkName("column", column)
	if table != "" {
		checkName("table", table)
	}
	exact, sargable, prefilter = columnFlags(exact, sargable, collation, prefilter, splitSargable)
	return &Binding{
		Kind: BindingKindColumn,
		Column: ColumnSpec{
			Column:        column,
			Table:         table,
			Type:          typ,
			Exact:         exact,
			Sargable:      sargable,
			Guard:         guard,
			Collation:     collation,
			Prefilter:     prefilter,
			SplitSargable: splitSargable,
		},
	}
}

func RawBinding(sqlStr string, typ SqlKind, exact, sargable, guard bool, collation, prefilter string, splitSargable bool) *Binding {
	checkColumnType(typ)
	if sqlStr == "" {
		Refuse("E_SQL_BINDING", "a raw column binding cannot be empty", Pos{})
	}
	exact, sargable, prefilter = columnFlags(exact, sargable, collation, prefilter, splitSargable)
	return &Binding{
		Kind: BindingKindColumn,
		Column: ColumnSpec{
			IsRaw:         true,
			Raw:           sqlStr,
			Type:          typ,
			Exact:         exact,
			Sargable:      sargable,
			Guard:         guard,
			Collation:     collation,
			Prefilter:     prefilter,
			SplitSargable: splitSargable,
		},
	}
}

func ColumnsBinding(items []*Binding) *Binding {
	if len(items) == 0 {
		Refuse("E_SQL_BINDING", "a columns binding needs at least one column", Pos{})
	}
	specs := make([]ColumnSpec, len(items))
	for i, item := range items {
		if item == nil || item.Kind != BindingKindColumn {
			Refuse("E_SQL_BINDING", fmt.Sprintf("a columns binding takes column bindings, and item %d is not a column", i+1), Pos{})
		}
		specs[i] = item.Column
	}
	return &Binding{
		Kind:    BindingKindColumns,
		Columns: specs,
	}
}

func RelationBinding(from, alias string, fields interface{}, scalar, correlate, prefilter string, splitSargable bool) *Binding {
	checkName("from", from)
	if alias != "" {
		checkName("alias", alias)
	}
	if splitSargable && prefilter == "" {
		prefilter = "separate"
	}
	return makeRelation(RelationFrom{Table: from}, alias, fields, scalar, correlate, prefilter)
}

func RelationQueryBinding(query, alias string, fields interface{}, scalar, correlate, prefilter string, splitSargable bool) *Binding {
	if query == "" {
		Refuse("E_SQL_BINDING", "a relation query cannot be empty", Pos{})
	}
	if alias != "" {
		checkName("alias", alias)
	}
	if splitSargable && prefilter == "" {
		prefilter = "separate"
	}
	return makeRelation(RelationFrom{IsRaw: true, Raw: query}, alias, fields, scalar, correlate, prefilter)
}

func makeRelation(from RelationFrom, alias string, fields interface{}, scalar, correlate, prefilter string) *Binding {
	prefilter = checkPrefilter(prefilter)
	fieldSpecs := make(map[string]ColumnSpec)
	var fieldOrder []string

	switch f := fields.(type) {
	case []FieldEntry:
		for _, fe := range f {
			if fe.Binding == nil || fe.Binding.Kind != BindingKindColumn {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the field %s of a relation binding must be a column binding", fe.Name), Pos{})
			}
			uc := utf8.AsciiUpper(fe.Name)
			if _, dup := fieldSpecs[uc]; dup {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the fields of a relation binding include %s twice (names are compared upper-cased)", uc), Pos{})
			}
			fieldSpecs[uc] = fe.Binding.Column
			fieldOrder = append(fieldOrder, uc)
		}
	case map[string]*Binding:
		// Sorted, so that which of two colliding names is reported does not
		// depend on map iteration order.
		names := make([]string, 0, len(f))
		for name := range f {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			b := f[name]
			if b == nil || b.Kind != BindingKindColumn {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the field %s of a relation binding must be a column binding", name), Pos{})
			}
			uc := utf8.AsciiUpper(name)
			if _, dup := fieldSpecs[uc]; dup {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the fields of a relation binding include %s twice (names are compared upper-cased)", uc), Pos{})
			}
			fieldSpecs[uc] = b.Column
			fieldOrder = append(fieldOrder, uc)
		}
		sort.Strings(fieldOrder)
	case nil:
		// empty
	default:
		Refuse("E_SQL_BINDING", "relation fields must be a list of field entries or a map", Pos{})
	}

	if scalar != "" {
		if _, ok := fieldSpecs[utf8.AsciiUpper(scalar)]; !ok {
			Refuse("E_SQL_BINDING", fmt.Sprintf("a relation binding names %s as its scalar, which is not one of its fields", scalar), Pos{})
		}
	}

	return &Binding{
		Kind: BindingKindRelation,
		Relation: RelationSpec{
			From:       from,
			Alias:      alias,
			Fields:     fieldSpecs,
			FieldOrder: fieldOrder,
			Scalar:     scalar,
			Correlate:  correlate,
			Prefilter:  prefilter,
		},
	}
}

func ValueBinding(v *sel.Value, typ *SqlKind) *Binding {
	if v == nil {
		Refuse("E_SQL_BINDING", "a value binding takes a Value, and this is nil", Pos{})
	}
	if typ != nil && *typ == KindNum {
		checkNumeric("this value binding", v)
	}
	return &Binding{
		Kind:      BindingKindValue,
		Val:       v,
		ValueType: typ,
	}
}

func (b *Binding) WithUniqueKey(key string) *Binding {
	checkName("unique key", key)
	if b.Kind != BindingKindRelation {
		Refuse("E_SQL_BINDING", "a unique key must name a declared relation field", Pos{})
	}
	if _, ok := b.Relation.Fields[utf8.AsciiUpper(key)]; !ok {
		Refuse("E_SQL_BINDING", "a unique key must name a declared relation field", Pos{})
	}
	cp := *b
	cp.Relation.UniqueKey = key
	return &cp
}

type Bindings struct {
	mapping map[string]*Binding
	order   []string
}

func NewBindings(items map[string]*Binding) *Bindings {
	m := make(map[string]*Binding)
	var order []string
	names := make([]string, 0, len(items))
	for k := range items {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		v := items[k]
		if v == nil {
			Refuse("E_SQL_BINDING", fmt.Sprintf("the binding for %s is nil", k), Pos{})
		}
		upper := utf8.AsciiUpper(k)
		if _, dup := m[upper]; dup {
			Refuse("E_SQL_BINDING", fmt.Sprintf("two bindings are named %s once upper-cased; SEL names are case-insensitive, so they are the same name", upper), Pos{})
		}
		m[upper] = v
		order = append(order, upper)
	}
	return &Bindings{
		mapping: m,
		order:   order,
	}
}

func (b *Bindings) Has(name string) bool {
	if b == nil || b.mapping == nil {
		return false
	}
	_, ok := b.mapping[utf8.AsciiUpper(name)]
	return ok
}

func (b *Bindings) Get(name string, pos Pos) *Binding {
	key := utf8.AsciiUpper(name)
	if b != nil && b.mapping != nil {
		if bind, ok := b.mapping[key]; ok {
			return bind
		}
	}
	known := b.Names()
	tail := "; no bindings were given"
	if len(known) > 0 {
		tail = fmt.Sprintf("; bound names are %s", strings.Join(known, ", "))
	}
	Refuse("E_SQL_UNBOUND", fmt.Sprintf("%s is read by this rule but no binding says where it lives%s", key, tail), pos)
	return nil
}

func (b *Bindings) Names() []string {
	if b == nil || b.mapping == nil {
		return nil
	}
	var out []string
	for k := range b.mapping {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (b *Bindings) CheckAliases(pos Pos) {
	if b == nil || b.mapping == nil {
		return
	}
	seen := make(map[string]string)
	for _, name := range b.order {
		bind := b.mapping[name]
		if bind.Kind != BindingKindRelation {
			continue
		}
		alias := bind.Relation.Alias
		if alias == "" {
			if bind.Relation.From.IsRaw {
				alias = bind.Relation.From.Raw
			} else {
				alias = bind.Relation.From.Table
			}
		}
		if prev, ok := seen[alias]; ok {
			Refuse("E_SQL_BINDING", fmt.Sprintf("relations %s and %s share the alias %s; give each one its own", prev, name, alias), pos)
		}
		seen[alias] = name
	}
}
