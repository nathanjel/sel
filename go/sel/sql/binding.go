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

func ColumnBinding(column, table string, typ SqlKind, exact, sargable, guard bool, collation, prefilter string, splitSargable bool) *Binding {
	checkName("column", column)
	if table != "" {
		checkName("table", table)
	}
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
	if sqlStr == "" {
		Refuse("E_SQL_BINDING", "a raw column binding cannot be empty", Pos{})
	}
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
	fieldSpecs := make(map[string]ColumnSpec)
	var fieldOrder []string

	switch f := fields.(type) {
	case []FieldEntry:
		for _, fe := range f {
			if fe.Binding == nil || fe.Binding.Kind != BindingKindColumn {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the field %s of a relation binding must be a column binding", fe.Name), Pos{})
			}
			uc := utf8.AsciiUpper(fe.Name)
			fieldSpecs[uc] = fe.Binding.Column
			fieldOrder = append(fieldOrder, uc)
		}
	case map[string]*Binding:
		for name, b := range f {
			if b == nil || b.Kind != BindingKindColumn {
				Refuse("E_SQL_BINDING", fmt.Sprintf("the field %s of a relation binding must be a column binding", name), Pos{})
			}
			uc := utf8.AsciiUpper(name)
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
	for k, v := range items {
		if v == nil {
			Refuse("E_SQL_BINDING", fmt.Sprintf("the binding for %s is nil", k), Pos{})
		}
		upper := utf8.AsciiUpper(k)
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
