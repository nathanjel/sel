package sql

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

type SourceShape int

const (
	SourceShapeStatic SourceShape = iota
	SourceShapeColumns
	SourceShapeRelation
)

type SourceFilter struct {
	Binder string
	Node   *SNode
}

type Source struct {
	Shape      SourceShape
	Elements   []Pair[string, Binder]
	Relation   *RelationSpec
	Filters    []SourceFilter
	ScalarRule bool
}

type Frame []Pair[string, Binder]

type Options struct {
	Strict bool
}

type Begun struct {
	Norm *SNode
	Plan *RelationalPlan
}

type Slot struct {
	Str  *string
	Frag *Fragment
}

func StringSlot(s string) Slot {
	return Slot{Str: &s}
}

func FragmentSlot(f *Fragment) Slot {
	return Slot{Frag: f}
}

type SlotMap []Pair[string, []Slot]

type Translator struct {
	dialect         string
	emit            *Emit
	bindings        *Bindings
	strict          bool
	params          []*sel.Value
	paramKinds      []SqlKind
	caveats         []string
	frames          []Frame
	constNames      map[string]bool
	constRoot       *sel.Value
	depth           int
	statementPlan   *RelationalPlan
	inWhere         bool
	subqueryCounter int
}

func NewTranslator(dialect string, bindings *Bindings, options Options) *Translator {
	if bindings == nil {
		bindings = NewBindings(nil)
	}
	return &Translator{
		dialect:  dialect,
		emit:     NewEmit(dialect),
		bindings: bindings,
		strict:   options.Strict,
	}
}

func sameRelation(a, b RelationSpec) bool {
	return a.From.Table == b.From.Table &&
		a.From.Raw == b.From.Raw &&
		a.From.IsRaw == b.From.IsRaw &&
		a.Alias == b.Alias
}

func listKey(k string) *int {
	if len(k) == 0 || len(k) > 9 || k[0] < '1' || k[0] > '9' {
		return nil
	}
	n := 0
	for i := 0; i < len(k); i++ {
		c := k[i]
		if c < '0' || c > '9' {
			return nil
		}
		n = n*10 + int(c-'0')
	}
	return &n
}

func childOf(n *SNode, key string) *SNode {
	if n.T == SNodeList {
		i := listKey(key)
		if i == nil || *i > len(n.Kids) {
			return nil
		}
		return n.Kids[*i-1]
	}
	if n.T == SNodeCList {
		for i, k := range n.Keys {
			if k == key {
				return n.Kids[i]
			}
		}
	}
	return nil
}

func joinSorted(xs []string) string {
	s := append([]string(nil), xs...)
	sort.Strings(s)
	return strings.Join(s, ", ")
}

func frameSet(frame *Frame, name string, b Binder) {
	for i := range *frame {
		if (*frame)[i].Key == name {
			(*frame)[i].Val = b
			return
		}
	}
	*frame = append(*frame, Pair[string, Binder]{Key: name, Val: b})
}

func declaredKind(b *Binding, v *sel.Value) SqlKind {
	if v.IsBool() {
		return KindBool
	}
	if v.IsBin() {
		return KindBin
	}
	if v.IsNone() {
		return KindList
	}
	if b.ValueType != nil && *b.ValueType == KindNum {
		return KindNum
	}
	return KindText
}

func constScope(bindings *Bindings) (map[string]bool, *sel.Value) {
	names := make(map[string]bool)
	root := sel.NewNone()
	if bindings == nil {
		return names, root
	}
	for _, name := range bindings.Names() {
		b := bindings.Get(name, Pos{})
		if b.Kind != BindingKindValue {
			continue
		}
		v := b.Val
		if v == nil || v.IsNone() || v.Size() > 0 {
			continue
		}
		names[name] = true
		root.Set(name, v)
	}
	return names, root
}

func litNode(t sel.NodeType, s string, b bool, pos Pos) *sel.Node {
	return &sel.Node{
		T:   t,
		Pos: pos,
		S:   s,
		B:   b,
	}
}

func (t *Translator) Begin(ast *sel.Node) Begun {
	RequireTarget(t.dialect, ast.Pos)
	t.bindings.CheckAliases(ast.Pos)

	t.params = nil
	t.paramKinds = nil
	t.caveats = nil
	t.frames = nil
	t.depth = 0
	t.subqueryCounter = 0

	constNames, constRoot := constScope(t.bindings)
	t.constNames = constNames
	t.constRoot = constRoot

	normalised := Normalise(ast, t.constNames, t.constRoot)
	plan := t.AnalyzePipeline(normalised)
	return Begun{Norm: normalised, Plan: plan}
}

func (t *Translator) Translate(ast *sel.Node) *Fragment {
	b := t.Begin(ast)
	if b.Plan != nil {
		return t.CompileStatement(b.Plan)
	}
	f := t.node(b.Norm)

	out := NewFragment(f.Parts, f.Kind, t.dialect, t.params, t.paramKinds, t.caveats)
	out.Canonical = f.Canonical
	return out
}

func (t *Translator) TranslateStatement(ast *sel.Node) *Fragment {
	b := t.Begin(ast)
	if b.Plan == nil {
		Refuse("E_SQL_SHAPE", "expected a relational query or pipeline", sel.Pos{})
	}
	return t.CompileStatement(b.Plan)
}

func (t *Translator) addCaveat(name string) {
	if !containsString(t.caveats, name) {
		t.caveats = append(t.caveats, name)
	}
}

func (t *Translator) node(n *SNode) *Fragment {
	t.depth++
	if t.depth > limits.MAX_DEPTH {
		t.depth--
		Refuse("E_SQL_DEPTH",
			fmt.Sprintf("this expression nests deeper than SEL will evaluate (%d), so there is nothing to translate; the evaluator answers E_DEPTH for it", limits.MAX_DEPTH),
			n.Pos)
	}
	defer func() {
		t.depth--
	}()

	compound := n.T == SNodeBin || n.T == SNodeUn || n.T == SNodeCall
	if !compound || !IsConstant(n, t.constNames) {
		return t.dispatch(n)
	}

	f := t.dispatch(n)
	Validate(n, t.constRoot)
	return f
}

func (t *Translator) dispatch(n *SNode) *Fragment {
	switch n.T {
	case SNodeNum:
		var dec *decimal.Dec
		if n.Origin != nil {
			dec = n.Origin.Dec
		}
		return t.literal(sel.NewNumExact(n.Str, dec), KindNum)
	case SNodeText:
		return t.literal(sel.NewTextOwned(n.Str), KindText)
	case SNodeBool:
		return t.literal(sel.NewBool(n.BoolVal), KindBool)
	case SNodeVar:
		return t.variable(n)
	case SNodeIndex:
		return t.index(n)
	case SNodeUn:
		return t.unary(n)
	case SNodeBin:
		return t.binary(n)
	case SNodeList, SNodeCList:
		Refuse("E_SQL_SHAPE", "a list is not a SQL value; a list can only be the thing an aggregate iterates", n.Pos)
	case SNodeCall:
		return t.call(n)
	}
	Refuse("E_SQL_SHAPE", fmt.Sprintf("cannot translate a %s node", n.T), n.Pos)
	return nil
}

func (t *Translator) literal(v *sel.Value, kind SqlKind) *Fragment {
	t.params = append(t.params, v)
	pk := kind
	if kind == KindUnknown || kind == KindList {
		pk = KindText
	}
	t.paramKinds = append(t.paramKinds, pk)
	p := Part{
		IsSlot: true,
		Slot:   len(t.params),
	}
	f := &Fragment{
		Parts:   []Part{p},
		Kind:    kind,
		Dialect: t.dialect,
	}
	return f
}

func (t *Translator) binder(name string) *Binder {
	for i := len(t.frames) - 1; i >= 0; i-- {
		for _, kv := range t.frames[i] {
			if kv.Key == name {
				b := kv.Val
				return &b
			}
		}
	}
	return nil
}

func (t *Translator) variable(n *SNode) *Fragment {
	if bound := t.binder(n.Str); bound != nil {
		return t.fromBinder(bound, n)
	}

	b := t.bindings.Get(n.Str, n.Pos)
	switch b.Kind {
	case BindingKindColumn:
		return t.columnRef(b.Column)
	case BindingKindValue:
		v := b.Val
		if v.Size() > 0 {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is bound to a list, and a list is not a SQL value; it can only be the thing an aggregate iterates", n.Str),
				n.Pos)
		}
		if v.IsNone() {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is bound to an empty value, which is not a SQL value; only an aggregate can be given an empty binding", n.Str),
				n.Pos)
		}
		return t.literal(v, declaredKind(b, v))
	case BindingKindColumns, BindingKindRelation:
		kindStr := "relation"
		if b.Kind == BindingKindColumns {
			kindStr = "columns"
		}
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is bound as a %s, which names a set of values rather than one; use it as the first argument of an aggregate, not as a value on its own", n.Str, kindStr),
			n.Pos)
	}
	Refuse("E_SQL_BINDING", "unusable binding for "+n.Str, n.Pos)
	return nil
}

func (t *Translator) columnRef(c ColumnSpec) *Fragment {
	sqlStr := c.Table
	if c.IsRaw {
		sqlStr = c.Raw
	} else {
		sqlStr = t.emit.Column(c.Table, c.Column)
	}
	f := &Fragment{
		Parts:             []Part{{Sql: sqlStr}},
		Kind:              c.Type,
		Dialect:           t.dialect,
		Exact:             c.Exact,
		Sargable:          c.Sargable,
		Guard:             c.Guard,
		SeparatePrefilter: c.Prefilter == "separate",
		Canonical:         c.Canonical,
	}
	return f
}

func (t *Translator) constantIndex(idx *SNode) string {
	if idx.T == SNodeNum || idx.T == SNodeText {
		return idx.Str
	}
	Refuse("E_SQL_SHAPE",
		"an index must be a constant here: the column it names has to be known before the query runs",
		idx.Pos)
	return ""
}

func (t *Translator) index(n *SNode) *Fragment {
	obj := n.L()
	if t.statementPlan != nil && obj.T == SNodeIndex {
		if row := t.rowPath(obj, n); row != nil {
			return t.rowField(row, "the row", t.constantIndex(n.R()), n)
		}
		t.node(n.L())
		Refuse("E_SQL_SHAPE",
			"only a bound name can be indexed here; SQL has no way to index into the result of an expression",
			n.Pos)
	}
	if obj.T != SNodeVar {
		Refuse("E_SQL_SHAPE",
			"only a bound name can be indexed here; SQL has no way to index into the result of an expression",
			n.Pos)
	}
	if bound := t.binder(obj.Str); bound != nil {
		return t.indexBinder(bound, obj.Str, t.constantIndex(n.R()), n)
	}
	b := t.bindings.Get(obj.Str, obj.Pos)
	key := t.constantIndex(n.R())

	switch b.Kind {
	case BindingKindRelation:
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is a relation, which is a list of rows; indexing it names no value SEL can produce, so use an aggregate and index the row its binder gives you", obj.Str),
			n.Pos)
	case BindingKindColumns:
		i := listKey(key)
		count := len(b.Columns)
		if i == nil || *i > count {
			Refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%s] is outside that binding's %d column(s)", obj.Str, key, count),
				n.Pos)
		}
		return t.columnRef(b.Columns[*i-1])
	case BindingKindValue:
		child := b.Val.Get(key)
		if child == nil {
			Refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%q] is not a key of that value", obj.Str, key),
				n.Pos)
		}
		if child.Size() > 0 {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%q] is a list, not a SQL value", obj.Str, key),
				n.Pos)
		}
		return t.literal(child, declaredKind(b, child))
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s is bound as a column, which has no parts to index", obj.Str),
		n.Pos)
	return nil
}

func (t *Translator) groupKey(src Source, gb RelationalGroup, projected bool) *Fragment {
	key := t.withRow(src, gb.Binder, func() *Fragment {
		return t.node(gb.Node)
	})
	identity := t.identityGroupKey(gb.Node, key)
	if projected && key.Kind == KindNum {
		parts := []Part{{Sql: "MIN("}}
		parts = append(parts, key.Parts...)
		parts = append(parts, Part{Sql: ")"})
		return NewFragment(parts, KindNum, t.dialect, key.Params, key.ParamKinds, key.Caveats)
	}
	return identity
}

func (t *Translator) identityGroupKey(n *SNode, f *Fragment) *Fragment {
	if f.Canonical && f.Kind == KindNum {
		return f
	}
	if f.Kind == KindUnknown {
		Refuse("E_SQL_SHAPE", "group keys require proven scalar identity", n.Pos)
	}
	if f.Kind == KindNum {
		if n.T != SNodeVar && n.T != SNodeIndex && n.T != SNodeNum {
			Refuse("E_SQL_SHAPE", "computed numeric group keys do not preserve SEL identity", n.Pos)
		}
		numeric := NewFragment(f.Parts, KindNum, t.dialect, f.Params, f.ParamKinds, f.Caveats)
		w := t.emit.TextOperand(numeric)
		out := NewFragment(w.Parts, KindText, t.dialect, w.Params, w.ParamKinds, w.Caveats)
		out.Exact = true
		return out
	}
	return t.collatedKey(f)
}

func (t *Translator) orderKey(f *Fragment, pos Pos) *Fragment {
	if f.Kind == KindNum {
		return f
	}
	if f.Canonical {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("CANON is text on %s, which SQL sorts by its bytes, and SEL sorts it as the number it is; sort it in memory", t.dialect),
			pos)
	}
	if f.Kind == KindText || f.Kind == KindUnknown {
		if t.strict {
			Refuse("E_SQL_UNSUPPORTED",
				"a text key sorts by its bytes in SQL, where SEL sorts number-shaped text as numbers (text-order); strict mode refuses that",
				pos)
		}
		t.addCaveat("text-order")
	}
	return t.collatedKey(f)
}

func (t *Translator) collatedKey(f *Fragment) *Fragment {
	if f.Kind != KindText || f.Exact {
		return f
	}
	wrapped := t.emit.TextOperand(f)
	out := NewFragment(wrapped.Parts, KindText, t.dialect, wrapped.Params, wrapped.ParamKinds, wrapped.Caveats)
	out.Exact = true
	return out
}

func (t *Translator) rowPath(node *SNode, outer *SNode) *RowModel {
	if node.T == SNodeVar {
		b := t.binder(node.Str)
		if b != nil && b.Shape == BinderShapeRow {
			return b.Model
		}
		return nil
	}
	if node.T != SNodeIndex {
		return nil
	}
	inner := t.rowPath(node.L(), node)
	if inner == nil {
		return nil
	}
	return t.rowNested(inner, t.constantIndex(node.R()), node, outer)
}

func (t *Translator) rowNested(row *RowModel, key string, n *SNode, outer *SNode) *RowModel {
	if nested := nestedOf(row, key); nested != nil {
		return nested
	}
	if listKey(key) != nil {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", key),
			n.Pos)
	}
	if _, ok := rowFieldSpec(row, key); ok {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("[%q] is a field, which has no parts to index", key),
			outer.Pos)
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("only a bound name can be indexed here; SQL has no way to index into the result of an expression (%s names no record this row carries)", key),
		n.Pos)
	return nil
}

func (t *Translator) rowField(row *RowModel, label string, key string, n *SNode) *Fragment {
	if listKey(key) != nil {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", label, key),
			n.Pos)
	}
	if nestedOf(row, key) != nil {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] is a record, which is a map in SEL and not one value; name the field you mean", label, key),
			n.Pos)
	}
	f, ok := rowFieldSpec(row, key)
	if !ok {
		if !row.Side && row.Dropped != nil && row.Dropped[utf8.AsciiUpper(key)] {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("field %q is ambiguous across joined relations", key),
				n.Pos)
		}
		var known []string
		if row.Side {
			for k := range row.Relation.Fields {
				known = append(known, k)
			}
		} else {
			for _, field := range row.Promoted {
				known = append(known, field.Key)
			}
		}
		relStr := "joined row"
		if row.Side {
			relStr = "relation"
		}
		tail := "; it declares none"
		if len(known) > 0 {
			tail = "; it has " + joinSorted(known)
		}
		Refuse("E_SQL_BINDING",
			fmt.Sprintf("%s[%q] is not a field of that %s%s", label, key, relStr, tail),
			n.Pos)
	}
	if f.Optional {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] is a field of the right side of a LINK_LEFT, which a row with no match does not have; read it through the right binder", label, key),
			n.Pos)
	}
	if f.Spec.IsRaw || !f.Qualify {
		return t.columnRef(f.Spec)
	}
	qualified := f.Spec
	qualified.Table = f.Table
	return t.columnRef(qualified)
}

func (t *Translator) fromBinder(b *Binder, n *SNode) *Fragment {
	switch b.Shape {
	case BinderShapeNode:
		return t.node(b.Node)
	case BinderShapeKey:
		var frame Frame
		frameSet(&frame, b.GroupBinder, BinderRow(b.Relation))
		t.frames = append(t.frames, frame)
		var key *Fragment
		func() {
			defer func() {
				t.frames = t.frames[:len(t.frames)-1]
			}()
			key = t.node(b.Node)
		}()
		wrapped := key.Kind == KindNum || (key.Kind == KindText && !key.Exact)
		collated := t.identityGroupKey(b.Node, key)
		if key.Kind == KindNum {
			parts := []Part{{Sql: "MIN("}}
			parts = append(parts, key.Parts...)
			parts = append(parts, Part{Sql: ")"})
			out := NewFragment(parts, KindNum, t.dialect, key.Params, key.ParamKinds, key.Caveats)
			out.Canonical = key.Canonical
			return out
		}
		if wrapped {
			parts := []Part{{Sql: "MIN("}}
			parts = append(parts, collated.Parts...)
			parts = append(parts, Part{Sql: ")"})
			out := NewFragment(parts, KindText, t.dialect, collated.Params, collated.ParamKinds, collated.Caveats)
			out.Exact = true
			out.Canonical = key.Canonical
			return out
		}
		return collated
	case BinderShapeColumn:
		return t.columnRef(b.Column)
	case BinderShapeGroup:
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is the list of a bucket's members, which is not a value SQL has; count it (COUNT), sum over it (SUM), or name the group key (_K)", n.Str),
			n.Pos)
	case BinderShapeProjected:
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is the record the projection built, which is a map in SEL and not one value; name the field you mean", n.Str),
			n.Pos)
	case BinderShapeRow:
		rel := b.Relation
		if b.Model != nil && !b.Model.Side {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is a joined row, which is a map in SEL and not one value; name the field you mean", n.Str),
				n.Pos)
		}
		if len(rel.Fields) > 1 {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is a row of a relation with %d fields, which is a map in SEL and not one value; name the field you mean", n.Str, len(rel.Fields)),
				n.Pos)
		}
		var field *ColumnSpec
		if rel.Scalar != "" {
			field = rel.Field(utf8.AsciiUpper(rel.Scalar))
		}
		if field == nil {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s names a row, and the relation does not say which of its fields a bare reference means; give the binding a \"scalar\", or index the field you want", n.Str),
				n.Pos)
		}
		if b.Model != nil && !field.IsRaw && t.statementPlan != nil {
			if !b.Model.Qualify {
				return t.columnRef(*field)
			}
			qualified := *field
			qualified.Table = b.Model.Table
			return t.columnRef(qualified)
		}
		return t.relationColumn(rel, *field)
	case BinderShapeNone:
		Refuse("E_SQL_SHAPE", b.Reason, n.Pos)
	}
	Refuse("E_SQL_SHAPE", "unusable binder", n.Pos)
	return nil
}

func (t *Translator) indexBinder(b *Binder, name string, key string, n *SNode) *Fragment {
	if b.Shape == BinderShapeGroup {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] indexes the list of a bucket's members, which SEL refuses (E_NO_KEY); read a member's field inside an aggregate over the group, SUM(%s, _[%q])", name, key, name, key),
			n.Pos)
	}
	if b.Shape == BinderShapeProjected {
		var proj *RelationalProjection
		for i := range b.Projections {
			if b.Projections[i].Alias != nil && *b.Projections[i].Alias == key {
				proj = &b.Projections[i]
				break
			}
		}
		if proj == nil {
			for i := range b.Projections {
				if b.Projections[i].Alias != nil && utf8.AsciiUpper(*b.Projections[i].Alias) == utf8.AsciiUpper(key) {
					proj = &b.Projections[i]
					break
				}
			}
		}
		if proj == nil {
			var known []string
			for _, candidate := range b.Projections {
				if candidate.Alias != nil {
					known = append(known, *candidate.Alias)
				}
			}
			sort.Strings(known)
			tail := ""
			if len(known) > 0 {
				tail = "; it has " + strings.Join(known, ", ")
			}
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%q] is not a field of the projection%s", name, key, tail),
				n.Pos)
		}
		src := Source{
			Shape:    SourceShapeRelation,
			Relation: b.Relation,
		}
		if proj.GroupKey != nil {
			kb := BinderKey(proj.GroupKey.Binder, proj.GroupKey.Node, b.Relation)
			return t.fromBinder(&kb, n)
		}
		return t.withGroup(src, proj.Binder, func() *Fragment {
			return t.node(proj.Node)
		})
	}
	if b.Shape == BinderShapeRow {
		if listKey(key) != nil {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", name, key),
				n.Pos)
		}
		if b.Model != nil {
			return t.rowField(b.Model, name, key, n)
		}
		rel := b.Relation
		field := utf8.AsciiUpper(key)
		if f := rel.Field(field); f != nil {
			return t.relationColumn(rel, *f)
		}
		var known []string
		for k := range rel.Fields {
			known = append(known, k)
		}
		tail := "; it declares none"
		if len(known) > 0 {
			tail = "; it has " + joinSorted(known)
		}
		Refuse("E_SQL_BINDING",
			fmt.Sprintf("%s[%q] is not a field of that relation%s", name, key, tail),
			n.Pos)
	}
	if b.Shape == BinderShapeNode {
		elem := childOf(b.Node, key)
		if elem == nil {
			Refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%q] is not a key of that element", name, key),
				n.Pos)
		}
		return t.node(elem)
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s names a single column, which has no parts to index", name),
		n.Pos)
	return nil
}

func (t *Translator) relationTableAlias(rel *RelationSpec, def string) string {
	if rel == nil {
		return def
	}
	if t.statementPlan != nil {
		if t.statementPlan.SourceRelation != nil && sameRelation(*rel, t.statementPlan.SourceRelation.Relation) {
			if t.statementPlan.SourceAlias != "" {
				return t.statementPlan.SourceAlias
			}
			return relationAlias(rel)
		}
		for _, join := range t.statementPlan.Joins {
			if join.SourceRelation != nil && sameRelation(*rel, join.SourceRelation.Relation) {
				if join.SourceAlias != "" {
					return join.SourceAlias
				}
				return relationAlias(rel)
			}
		}
	}
	return relationAlias(rel)
}

func (t *Translator) relationColumn(rel *RelationSpec, c ColumnSpec) *Fragment {
	if c.IsRaw || t.statementPlan == nil ||
		(len(t.statementPlan.Joins) == 0 && t.statementPlan.SourceSubquery == nil) {
		return t.columnRef(c)
	}
	qualified := c
	qualified.Table = t.relationTableAlias(rel, "")
	return t.columnRef(qualified)
}

type eqlClass int

const (
	eqlClassText eqlClass = iota + 1
	eqlClassBool
	eqlClassBin
)

func getEqlClass(k SqlKind) *eqlClass {
	var c eqlClass
	switch k {
	case KindNum, KindText:
		c = eqlClassText
	case KindBool:
		c = eqlClassBool
	case KindBin:
		c = eqlClassBin
	default:
		return nil
	}
	return &c
}

func requireComparableKinds(l, r *Fragment, op string, pos Pos) {
	cl := getEqlClass(l.Kind)
	cr := getEqlClass(r.Kind)
	if cl == nil || cr == nil || *cl == *cr {
		return
	}
	other := l.Kind
	if l.Kind == KindBool {
		other = r.Kind
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s compares a BOOL with a %s, which SEL answers FALSE for every value because the kinds differ. SQL has no way to say that: both sides cast to the same characters", op, other),
		pos)
}

func unify(fs []*Fragment, pos Pos) SqlKind {
	var kind *SqlKind
	for _, f := range fs {
		if f.Kind == KindUnknown {
			continue
		}
		if kind == nil {
			k := f.Kind
			kind = &k
			continue
		}
		if *kind != f.Kind {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("these branches produce different kinds — %s and %s — and SQL gives the whole expression one type, which cannot match SEL's for both", *kind, f.Kind),
				pos)
		}
	}
	if kind == nil {
		return KindUnknown
	}
	return *kind
}

func retKind(entry *EntryRecord, args []*Fragment, pos Pos) SqlKind {
	ret := entry.Ret
	if ret == "@concat" {
		for _, a := range args {
			if a.Kind == KindBin {
				return KindBin
			}
		}
		return KindText
	}
	if strings.HasPrefix(ret, "@unify:") {
		var pick []*Fragment
		rest := ret[7:]
		for len(rest) > 0 {
			comma := strings.IndexByte(rest, ',')
			piece := rest
			if comma >= 0 {
				piece = rest[:comma]
			}
			var i int
			for j := 0; j < len(piece); j++ {
				i = i*10 + int(piece[j]-'0')
			}
			if i < len(args) {
				pick = append(pick, args[i])
			}
			if comma < 0 {
				break
			}
			rest = rest[comma+1:]
		}
		return unify(pick, pos)
	}
	return KindFromName(ret)
}

func (t *Translator) requireBool(f *Fragment, pos Pos, where string) *Fragment {
	if f.Kind == KindBool {
		return f
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s needs a BOOL here and this is %s; SEL has no truthiness, so neither does its translation", where, f.Kind),
		pos)
	return nil
}

func (t *Translator) requireNum(f *Fragment, pos Pos, where string) *Fragment {
	if f.Kind == KindNum || f.Kind == KindUnknown {
		return f
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s adds its body up, so it needs a number here and this is %s", where, f.Kind),
		pos)
	return nil
}

func (t *Translator) requireNotBool(f *Fragment, pos Pos, where string) {
	if f.Kind != KindBool && f.Kind != KindBin {
		return
	}
	what := "a BIN"
	if f.Kind == KindBool {
		what = "a BOOL"
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s reads its operands as numbers, and %s is not one; SEL answers E_NOT_NUM here rather than coercing it", where, what),
		pos)
}

func (t *Translator) requireNotBoolOperand(f *Fragment, pos Pos, where string) {
	if f.Kind != KindBool {
		return
	}
	Refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s reads its operands as text or bytes, and a BOOL is neither; SEL answers E_NOT_TEXT here rather than spelling it 1 or true", where),
		pos)
}

func (t *Translator) requireNumericConstant(n *SNode) {
	if IsConstant(n, t.constNames) {
		RequireNumeric(n, t.constRoot)
	}
}

func (t *Translator) guardNumeric(f *Fragment, n *SNode) *Fragment {
	if IsConstant(n, t.constNames) {
		return f
	}
	wraps := f.Kind != KindNum || f.Guard
	guarded := t.emit.NumericOperand(f, n.Pos)
	if wraps {
		t.scaleLimited(n.Pos, "this operand is read as a number")
	}
	return guarded
}

func (t *Translator) numericCastScale() *int32 {
	capVal := t.emit.Lex("numericCastScale")
	capStr, ok := capVal.(string)
	if !ok || capStr == "" {
		return nil
	}
	var out int64
	for i := 0; i < len(capStr); i++ {
		c := capStr[i]
		if c < '0' || c > '9' {
			return nil
		}
		out = out*10 + int64(c-'0')
		if out > math.MaxInt32 {
			out = math.MaxInt32
		}
	}
	val := int32(out)
	return &val
}

func (t *Translator) scaleLimited(pos Pos, what string) {
	cap := t.numericCastScale()
	if cap == nil {
		return
	}
	if t.strict {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s through a DECIMAL that keeps %d fractional digits, and a value with more loses them on %s (scale-limit); strict mode refuses that", what, *cap, t.dialect),
			pos)
	}
	t.addCaveat("scale-limit")
}

func (t *Translator) coerceScaleLimits(operands []*SNode) {
	cap := t.numericCastScale()
	if cap == nil {
		return
	}
	for _, operand := range operands {
		if IsConstant(operand, t.constNames) {
			if ConstantScale(operand, t.constRoot) > int(*cap) {
				t.scaleLimited(operand.Pos, "this constant is read as a number")
			}
		} else {
			t.scaleLimited(operand.Pos, "this operand is read as a number")
		}
	}
}

var (
	numericOpsSet      = map[string]bool{"==": true, "!=": true, "<": true, "<=": true, ">": true, ">=": true}
	textualOpsSet      = map[string]bool{"$==": true, "$!=": true, "$<": true, "$<=": true, "$>": true, "$>=": true, "EQL": true}
	byteComparisonsSet = map[string]bool{"$==": true, "$!=": true, "$<": true, "$<=": true, "$>": true, "$>=": true, "EQL": true, "IN": true}
	arithmeticOpsSet   = map[string]bool{"+": true, "-": true, "*": true, "/": true, "%": true}
)

func (t *Translator) variantFor(op string, args []*Fragment) *string {
	if numericOpsSet[op] {
		v := "coerce"
		if args[0].Kind == KindNum && args[1].Kind == KindNum {
			v = "num"
		}
		return &v
	}
	if textualOpsSet[op] {
		v := "text"
		return &v
	}
	if op == "&" {
		v := "text"
		if args[0].Kind == KindBin || args[1].Kind == KindBin {
			v = "bin"
		}
		return &v
	}
	return nil
}

func (t *Translator) templateOf(entry *EntryRecord, args []*Fragment, variant *string, what string, pos Pos) string {
	if len(entry.Variants) > 0 {
		var arm *string
		if variant != nil {
			if v, ok := entry.Variants[*variant]; ok && v != MISSING {
				arm = &v
			}
		}
		if arm == nil {
			varDesc := "this shape"
			if variant != nil {
				varDesc = *variant + " operands"
			}
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s has no mapping in dialect %s for %s", what, t.dialect, varDesc),
				pos)
		}
		return *arm
	}
	if s, ok := entry.Tpl.(string); ok {
		return s
	}
	if m, ok := entry.Tpl.(map[string]string); ok {
		n := strconv.Itoa(len(args))
		arm, hasN := m[n]
		star, hasStar := m["*"]
		var chosen *string
		if hasN {
			if arm != "" && arm != MISSING {
				chosen = &arm
			}
		} else if hasStar {
			if star != "" && star != MISSING {
				chosen = &star
			}
		}
		if chosen == nil {
			var keys []string
			for k := range m {
				if m[k] != "" && m[k] != MISSING {
					keys = append(keys, k)
				}
			}
			sort.Strings(keys)
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s has no mapping in dialect %s for %s argument(s); it maps %s", what, t.dialect, n, strings.Join(keys, ", ")),
				pos)
		}
		return *chosen
	}
	Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("%s has no template in dialect %s", what, t.dialect), pos)
	return ""
}

func (t *Translator) apply(section string, key string, args []*Fragment, pos Pos, variant *string) *Fragment {
	raw := Entry(t.dialect, section, key)
	what := key
	if section == "ops" {
		what = "the " + key + " operator"
	}
	if raw == MISSING || raw == nil {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s", what, t.dialect),
			pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s", what, t.dialect),
			pos)
	}
	if entry.Kind == EntryKindRefusal {
		reason := ""
		if entry.Reason != "" {
			reason = " — " + entry.Reason
		}
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s%s", what, t.dialect, reason),
			pos)
	}
	if entry.Kind == EntryKindBuilder {
		return entry.Builder(t.emit, args, pos)
	}
	if entry.Arity != nil {
		n := len(args)
		if n < entry.Arity[0] || n > entry.Arity[1] {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s has no mapping in dialect %s for %d argument(s)", what, t.dialect, n),
				pos)
		}
	}
	if entry.Since != "" && !VersionAtLeast(Version(t.dialect), entry.Since) {
		Refuse("E_SQL_DIALECT",
			fmt.Sprintf("%s needs %s %s or newer, and this map says %s", what, t.dialect, entry.Since, Version(t.dialect)),
			pos)
	}
	if entry.Caveat != "" {
		if t.strict {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s translates only approximately in dialect %s (%s), and strict mode refuses those", what, t.dialect, entry.Caveat),
				pos)
		}
		t.addCaveat(entry.Caveat)
	}
	tpl := t.templateOf(entry, args, variant, what, pos)
	parts := t.emit.Fill(tpl, args, pos, nil)
	rk := retKind(entry, args, pos)
	return NewFragment(parts, rk, t.dialect, nil, nil, nil)
}

func (t *Translator) foldPairwise(op string, parts []*Fragment, pos Pos) *Fragment {
	acc := parts[0]
	for i := 1; i < len(parts); i++ {
		pair := []*Fragment{acc, parts[i]}
		acc = t.apply("ops", op, pair, pos, t.variantFor(op, pair))
	}
	return acc
}

func (t *Translator) unary(n *SNode) *Fragment {
	x := t.node(n.L())
	if n.Str == "NOT" {
		x = t.requireBool(x, n.L().Pos, "NOT")
	} else {
		t.requireNotBool(x, n.L().Pos, n.Str)
		x = t.guardNumeric(x, n.L())
	}
	return t.apply("ops", n.Str, []*Fragment{x}, n.Pos, nil)
}

func (t *Translator) binary(n *SNode) *Fragment {
	op := n.Str
	if op == "IN" {
		return t.inOperator(n)
	}

	l := t.node(n.L())
	r := t.node(n.R())

	if op == "AND" || op == "OR" || op == "XOR" {
		l = t.requireBool(l, n.L().Pos, op)
		r = t.requireBool(r, n.R().Pos, op)
	}
	if arithmeticOpsSet[op] || numericOpsSet[op] {
		t.requireNotBool(l, n.L().Pos, op)
		t.requireNotBool(r, n.R().Pos, op)
		t.requireNumericConstant(n.L())
		t.requireNumericConstant(n.R())
		l = t.guardNumeric(l, n.L())
		r = t.guardNumeric(r, n.R())
	}
	if op == "&" || (len(op) > 1 && op[0] == '$') {
		t.requireNotBoolOperand(l, n.L().Pos, op)
		t.requireNotBoolOperand(r, n.R().Pos, op)
	}

	before := []*Fragment{l, r}
	variant := t.variantFor(op, before)
	if variant != nil && *variant == "coerce" {
		t.coerceScaleLimits([]*SNode{n.L(), n.R()})
	}

	if byteComparisonsSet[op] {
		requireComparableKinds(l, r, op, n.Pos)
		lExact := l.Exact
		rExact := r.Exact
		lLit := n.L() != nil && n.L().T == SNodeText
		rLit := n.R() != nil && n.R().T == SNodeText
		spfVal := t.emit.Lex("sargablePrefilter")
		sargablePrefilter := false
		if s, ok := spfVal.(string); ok && s == "true" {
			sargablePrefilter = true
		}

		if (lExact && (rExact || rLit)) || (rExact && lLit) {
			// bare comparison
		} else if op == "$==" && ((l.Sargable && rLit) || (r.Sargable && lLit)) {
			if sargablePrefilter {
				coarseArgs := []*Fragment{l, r}
				coarse := t.apply("ops", "$==", coarseArgs, n.Pos, variant)
				resArgs := []*Fragment{t.emit.TextOperand(l), t.emit.TextOperand(r)}
				residual := t.apply("ops", "$==", resArgs, n.Pos, variant)
				andArgs := []*Fragment{coarse, residual}
				res := t.apply("ops", "AND", andArgs, n.Pos, nil)
				res.Prefilter = coarse
				res.SeparatePrefilter = l.SeparatePrefilter || r.SeparatePrefilter
				return res
			}
		} else if l.Kind != KindBin || r.Kind != KindBin {
			l = t.emit.TextOperand(l)
			r = t.emit.TextOperand(r)
		}
	}
	args := []*Fragment{l, r}
	res := t.apply("ops", op, args, n.Pos, variant)
	if op == "AND" {
		if l.Prefilter != nil && r.Prefilter != nil {
			pair := []*Fragment{l.Prefilter, r.Prefilter}
			res.Prefilter = t.apply("ops", "AND", pair, n.Pos, nil)
		} else if l.Prefilter != nil {
			pair := []*Fragment{l.Prefilter, r}
			res.Prefilter = t.apply("ops", "AND", pair, n.Pos, nil)
		} else if r.Prefilter != nil {
			pair := []*Fragment{l, r.Prefilter}
			res.Prefilter = t.apply("ops", "AND", pair, n.Pos, nil)
		}
		if l.SeparatePrefilter || r.SeparatePrefilter {
			res.SeparatePrefilter = true
		}
	}
	return res
}

func mergeSlots(a SlotMap, b SlotMap) SlotMap {
	out := append(SlotMap(nil), a...)
	for _, kvB := range b {
		for _, kvA := range out {
			if kvA.Key == kvB.Key {
				panic(fmt.Sprintf("two sources both supply the skeleton slot {%s}; one would silently shadow the other", kvB.Key))
			}
		}
		out = append(out, kvB)
	}
	return out
}

func (t *Translator) skeleton(name string, pos Pos) string {
	raw := Entry(t.dialect, "skel", name)
	if raw == MISSING || raw == nil {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
			pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
			pos)
	}
	if entry.Kind == EntryKindRefusal {
		if entry.Reason == "" {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
				pos)
		}
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s cannot express %s — %s", t.dialect, name, entry.Reason),
			pos)
	}
	if entry.Kind == EntryKindBuilder {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("the %s skeleton for %s is a builder, and a skeleton is a template", name, t.dialect),
			pos)
	}
	if entry.Caveat != "" {
		if t.strict {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("the %s skeleton for %s is not exactly equivalent (%s), and strict mode refuses those", name, t.dialect, entry.Caveat),
				pos)
		}
		t.addCaveat(entry.Caveat)
	}
	if s, ok := entry.Tpl.(string); ok {
		return s
	}
	Refuse("E_SQL_UNSUPPORTED",
		fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
		pos)
	return ""
}

func (t *Translator) fillNamed(tpl string, slots SlotMap, pos Pos) []Part {
	var parts []Part
	push := func(s string) {
		if s == "" {
			return
		}
		if len(parts) > 0 && !parts[len(parts)-1].IsSlot {
			parts[len(parts)-1].Sql += s
			return
		}
		parts = append(parts, Part{Sql: s})
	}
	i := 0
	for i < len(tpl) {
		if tpl[i] != '{' {
			push(tpl[i : i+1])
			i++
			continue
		}
		end := strings.IndexByte(tpl[i:], '}')
		if end == -1 {
			push(tpl[i:])
			break
		}
		name := tpl[i+1 : i+end]
		i = i + end + 1

		var items []Slot
		found := false
		for _, kv := range slots {
			if kv.Key == name {
				items = kv.Val
				found = true
				break
			}
		}
		if !found {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("a skeleton in dialect %s uses {%s}, which is not one of its slots", t.dialect, name),
				pos)
		}
		for _, item := range items {
			if item.Str != nil {
				push(*item.Str)
			} else if item.Frag != nil {
				for _, p := range item.Frag.Parts {
					if p.IsSlot {
						parts = append(parts, p)
					} else {
						push(p.Sql)
					}
				}
			}
		}
	}
	return parts
}

func (t *Translator) relationSlots(rel RelationSpec) SlotMap {
	from := rel.From.Table
	if rel.From.IsRaw {
		from = rel.From.Raw
	} else {
		from = t.emit.Ident(from)
	}
	if rel.Alias != "" {
		from += " " + t.emit.Ident(rel.Alias)
	}
	corr := "true"
	if s, ok := t.emit.Lex("true").(string); ok && s != "" {
		corr = s
	}
	if rel.Correlate != "" {
		corr = rel.Correlate
	}
	return SlotMap{
		Pair[string, []Slot]{Key: "from", Val: []Slot{StringSlot(from)}},
		Pair[string, []Slot]{Key: "corr", Val: []Slot{StringSlot(corr)}},
	}
}

func (t *Translator) valueNode(v *sel.Value, b *Binding, pos Pos) *SNode {
	if v.Size() > 0 {
		var entries []CListEntry
		for _, e := range v.Entries() {
			entries = append(entries, CListEntry{
				Key: e.Key,
				Val: t.valueNode(e.Val, b, pos),
			})
		}
		return CList(pos, entries)
	}
	if v.IsBool() {
		return Leaf(litNode(sel.NodeBool, "", v.AsBool(Pos{}), pos))
	}
	if v.IsBin() {
		Refuse("E_SQL_SHAPE",
			"a BIN element of a value binding has no literal node to become; bind it as a column, or convert it before translating",
			pos)
	}
	num := b.ValueType != nil && *b.ValueType == KindNum
	nodeType := sel.NodeText
	if num {
		nodeType = sel.NodeNum
	}
	return Leaf(litNode(nodeType, v.AsText(pos), false, pos))
}

func (t *Translator) valueElements(b *Binding, pos Pos) []Pair[string, Binder] {
	v := b.Val
	if v.Size() == 0 {
		if v.IsNone() {
			return nil
		}
		return []Pair[string, Binder]{{Key: "1", Val: BinderNode(t.valueNode(v, b, pos))}}
	}
	var out []Pair[string, Binder]
	for _, e := range v.Entries() {
		out = append(out, Pair[string, Binder]{
			Key: e.Key,
			Val: BinderNode(t.valueNode(e.Val, b, pos)),
		})
	}
	return out
}

func (t *Translator) inOperator(n *SNode) *Fragment {
	rhs := n.R()
	rhsIsFreeVar := rhs.T == SNodeVar && t.binder(rhs.Str) == nil

	if rhsIsFreeVar && t.bindings.Has(rhs.Str) {
		b := t.bindings.Get(rhs.Str, rhs.Pos)
		if b.Kind == BindingKindRelation {
			rel := b.Relation
			var scalar *ColumnSpec
			if rel.Scalar != "" {
				scalar = rel.Field(utf8.AsciiUpper(rel.Scalar))
			}
			if scalar == nil {
				Refuse("E_SQL_SHAPE",
					fmt.Sprintf("IN over %s needs the binding to name a \"scalar\" field: that is the column the subquery projects", rhs.Str),
					rhs.Pos)
			}
			if len(rel.Fields) != 1 {
				Refuse("E_SQL_SHAPE",
					fmt.Sprintf("IN over %s is refused: the relation declares %d fields, so SEL reads its rows as maps and a scalar can never equal one. Bind the projected column as a relation with that one field.", rhs.Str, len(rel.Fields)),
					rhs.Pos)
			}
			skel := t.skeleton("inRelation", n.Pos)
			slots := mergeSlots(
				t.relationSlots(rel),
				SlotMap{
					Pair[string, []Slot]{Key: "needle", Val: []Slot{FragmentSlot(t.emit.TextOperand(t.node(n.L())))}},
					Pair[string, []Slot]{Key: "body", Val: []Slot{FragmentSlot(t.emit.TextOperand(t.columnRef(*scalar)))}},
				},
			)
			parts := t.fillNamed(skel, slots, n.Pos)
			return NewFragment(parts, KindBool, t.dialect, nil, nil, nil)
		}
	}

	var elements []*SNode
	hasElements := false
	if rhs.T == SNodeList || rhs.T == SNodeCList {
		elements = rhs.Kids
		hasElements = true
	} else if rhsIsFreeVar && t.bindings.Has(rhs.Str) {
		b := t.bindings.Get(rhs.Str, rhs.Pos)
		if b.Kind == BindingKindValue && b.Val.Size() > 0 {
			elems := t.valueElements(b, rhs.Pos)
			elements = make([]*SNode, len(elems))
			for i, e := range elems {
				elements[i] = e.Val.Node
			}
			hasElements = true
		}
	}

	if !hasElements {
		r := t.node(n.R())
		l := t.node(n.L())
		requireComparableKinds(l, r, "IN", n.Pos)
		args := []*Fragment{t.emit.TextOperand(l), t.emit.TextOperand(r)}
		scalarVar := "scalar"
		return t.apply("ops", "IN", args, n.Pos, &scalarVar)
	}

	if len(elements) == 0 {
		return t.literal(sel.NewBool(false), KindBool)
	}

	var tests []*Fragment
	textVar := "text"
	for _, e := range elements {
		raw := t.node(n.L())
		isExact := raw.Exact
		needle := raw
		if !isExact {
			needle = t.emit.TextOperand(raw)
		}
		f := t.node(e)
		if f.Kind == KindList {
			Refuse("E_SQL_SHAPE",
				"IN over a list of lists is structural in SEL and has no SQL counterpart",
				e.Pos)
		}
		requireComparableKinds(raw, f, "IN", e.Pos)
		item := f
		if !isExact {
			item = t.emit.TextOperand(f)
		}
		args := []*Fragment{needle, item}
		tests = append(tests, t.apply("ops", "EQL", args, e.Pos, &textVar))
	}
	return t.foldPairwise("OR", tests, n.Pos)
}

func (t *Translator) conditional(n *SNode) *Fragment {
	name := n.Str
	args := append([]*SNode(nil), n.Kids...)
	if name == "IF" && len(args) == 2 {
		args = append(args, Leaf(litNode(sel.NodeText, "", false, n.Pos)))
	}
	branchTpl := t.skeleton("caseBranch", n.Pos)
	caseTpl := t.skeleton("case", n.Pos)

	last := len(args) - 1
	var results []*Fragment
	var joined []Slot

	for i := 0; i < last; i += 2 {
		cond := t.requireBool(t.node(args[i]), args[i].Pos, name)
		then := t.node(args[i+1])
		results = append(results, then)
		slots := SlotMap{
			Pair[string, []Slot]{Key: "cond", Val: []Slot{FragmentSlot(cond)}},
			Pair[string, []Slot]{Key: "then", Val: []Slot{FragmentSlot(then)}},
		}
		branch := NewFragment(t.fillNamed(branchTpl, slots, n.Pos), KindUnknown, t.dialect, nil, nil, nil)
		if len(joined) > 0 {
			joined = append(joined, StringSlot(" "))
		}
		joined = append(joined, FragmentSlot(branch))
	}
	els := t.node(args[last])
	results = append(results, els)

	slots := SlotMap{
		Pair[string, []Slot]{Key: "branches", Val: joined},
		Pair[string, []Slot]{Key: "else", Val: []Slot{FragmentSlot(els)}},
	}
	return NewFragment(t.fillNamed(caseTpl, slots, n.Pos), unify(results, n.Pos), t.dialect, nil, nil, nil)
}

func (t *Translator) caseWhen(cond, then, els *Fragment, pos Pos) *Fragment {
	branchSlots := SlotMap{
		Pair[string, []Slot]{Key: "cond", Val: []Slot{FragmentSlot(cond)}},
		Pair[string, []Slot]{Key: "then", Val: []Slot{FragmentSlot(then)}},
	}
	branch := NewFragment(t.fillNamed(t.skeleton("caseBranch", pos), branchSlots, pos), KindUnknown, t.dialect, nil, nil, nil)
	slots := SlotMap{
		Pair[string, []Slot]{Key: "branches", Val: []Slot{FragmentSlot(branch)}},
		Pair[string, []Slot]{Key: "else", Val: []Slot{FragmentSlot(els)}},
	}
	resKind := KindUnknown
	if then.Kind == els.Kind {
		resKind = then.Kind
	}
	return NewFragment(t.fillNamed(t.skeleton("case", pos), slots, pos), resKind, t.dialect, nil, nil, nil)
}

var (
	binArgumentOkSet  = map[string]bool{"BLEN": true, "CRC32": true, "ENCODE_BASE64": true, "FROM_UTF8": true, "ISNUM": true, "TO_HEX": true, "TO_UTF8": true}
	boolArgumentOkSet = map[string]bool{"ISNUM": true}
)

func (t *Translator) requireArgumentKind(name string, f *Fragment, pos Pos) {
	if f.Kind == KindBool && !boolArgumentOkSet[name] {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s does not take a BOOL argument; SEL raises here rather than reading a boolean as text or as 1", name),
			pos)
	}
	if f.Kind == KindBin && !binArgumentOkSet[name] {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s reads its argument as text, and this is BIN; SEL raises here rather than reinterpreting bytes as characters", name),
			pos)
	}
}

func regexAt(name string) *int {
	if name == "RMATCH" || name == "RFIND" || name == "RREPLACE" || name == "RGROUPS" {
		zero := 0
		return &zero
	}
	return nil
}

func (t *Translator) rewriteRegex(n *SNode) *SNode {
	at := regexAt(n.Str)
	if at == nil {
		return n
	}
	args := append([]*SNode(nil), n.Kids...)
	patAt := *at
	var pat *SNode
	if patAt < len(args) {
		pat = args[patAt]
	}
	if pat == nil || pat.T != SNodeText {
		pPos := n.Pos
		if pat != nil {
			pPos = pat.Pos
		}
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s needs a literal pattern here: SEL rewrites \\d, \\w and \\s into explicit ASCII classes before matching, and a pattern that is not known until the query runs cannot be rewritten", n.Str),
			pPos)
	}

	var source string
	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sel.SelError); ok {
					Refuse("E_SQL_UNSUPPORTED",
						fmt.Sprintf("%s's pattern is not in SEL's portable subset, so there is nothing to translate: %s", n.Str, se.Message),
						pat.Pos)
				}
				panic(r)
			}
		}()
		source = sel.ValidatePattern(pat.Str, pat.Pos)
	}()

	inlineFlags := "(?s)"
	flagAt := 2
	if n.Str == "RREPLACE" {
		flagAt = 3
	}
	if flagAt >= len(args) {
		args[patAt] = Leaf(litNode(sel.NodeText, inlineFlags+source, false, pat.Pos))
		return Rewritten(n.Origin, args)
	}
	flags := args[flagAt]
	if flags.T != SNodeText {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s needs literal flags here: their content selects the mapping, so they have to be known before the query runs", n.Str),
			flags.Pos)
	}
	text := flags.Str
	if text != "" && text != "i" && text != "I" {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s accepts only the i flag here, and SEL accepts only i at all; %q is not it", n.Str, text),
			flags.Pos)
	}
	if text != "" {
		for i := 0; i < len(source); i++ {
			if source[i] > 0x7f {
				Refuse("E_SQL_UNSUPPORTED",
					"the i flag needs an ASCII-only pattern, which SEL requires for the same reason and refuses here too",
					flags.Pos)
			}
		}
		inlineFlags = "(?si)"
	}
	args[patAt] = Leaf(litNode(sel.NodeText, inlineFlags+source, false, pat.Pos))
	args = append(args[:flagAt], args[flagAt+1:]...)
	return Rewritten(n.Origin, args)
}

func isNumericArgument(name string, i int) bool {
	if name == "MIN" || name == "MAX" {
		return true
	}
	if i == 0 {
		return name == "ABS" || name == "SIGN" || name == "CEIL" ||
			name == "FLOOR" || name == "TRUNC" || name == "ROUND" ||
			name == "POWER" || name == "CHAR" || name == "CANON"
	}
	if i == 1 {
		return name == "ROUND" || name == "POWER" || name == "LEFT" ||
			name == "RIGHT" || name == "SUBSTR" || name == "REPEAT" ||
			name == "PADL" || name == "PADR"
	}
	if i == 2 {
		return name == "SUBSTR" || name == "FIND"
	}
	return false
}

var aggregatesSet = map[string]bool{"ALL": true, "ANY": true, "MAP": true, "FILTER": true, "SUM": true, "JOIN": true}

func (t *Translator) call(n *SNode) *Fragment {
	name := n.Str

	if t.statementPlan != nil && len(n.Kids) > 0 && n.Kids[0].T == SNodeVar {
		group := t.binder(n.Kids[0].Str)
		if group != nil && group.Shape == BinderShapeGroup {
			if name == "COUNT" && len(n.Kids) == 1 {
				return NewFragment([]Part{{Sql: "COUNT(*)"}}, KindNum, t.dialect, nil, nil, nil)
			}
			if name == "SUM" && len(n.Kids) >= 2 {
				hasCustomBinder := len(n.Kids) == 3 && IsBinderName(n.Kids[1])
				bodyNode := n.Kids[1]
				binderName := "_"
				if hasCustomBinder {
					bodyNode = n.Kids[2]
					binderName = n.Kids[1].Str
				}
				src := Source{
					Shape:    SourceShapeRelation,
					Relation: group.Relation,
				}
				inner := t.withRow(src, binderName, func() *Fragment {
					return t.node(bodyNode)
				})
				var sqlBuilder strings.Builder
				sqlBuilder.WriteString("COALESCE(SUM(")
				for _, pt := range inner.Parts {
					sqlBuilder.WriteString(pt.Sql)
				}
				sqlBuilder.WriteString("), 0)")
				return NewFragment([]Part{{Sql: sqlBuilder.String()}}, KindNum, t.dialect, nil, nil, nil)
			}
		}
	}

	if aggregatesSet[name] {
		return t.aggregate(n)
	}
	if name == "COUNT" {
		return t.count(n)
	}
	if name == "HAS" {
		return t.has(n)
	}
	if name == "INDEXES" {
		Refuse("E_SQL_SHAPE", "INDEXES yields a list of keys, and a SQL expression is a scalar", n.Pos)
	}
	if name == "ABORT" {
		Refuse("E_SQL_UNSUPPORTED", "ABORT raises an error, which is a control-flow effect and not a value a SQL expression can be", n.Pos)
	}
	if name == "IF" || name == "COND" {
		return t.conditional(n)
	}
	if _, _, ok := sel.HostArity(name); ok {
		return t.hostCall(n)
	}

	rewritten := t.rewriteRegex(n)
	var args []*Fragment
	for i, arg := range rewritten.Kids {
		f := t.node(arg)
		if f.Kind == KindList {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("argument to %s is a list, and a SQL expression is a scalar", name),
				arg.Pos)
		}
		t.requireArgumentKind(name, f, arg.Pos)
		if isNumericArgument(name, i) {
			t.requireNumericConstant(arg)
			f = t.guardNumeric(f, arg)
		}
		args = append(args, f)
	}
	out := t.apply("funcs", name, args, n.Pos, nil)
	if name == "CANON" {
		out.Canonical = true
	}
	return out
}

func (t *Translator) hostCall(n *SNode) *Fragment {
	name := n.Str
	raw := Entry(t.dialect, "funcs", name)
	if raw == MISSING || raw == nil {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s is a host function with no SQL spelling in dialect %s; register one with the map, or evaluate it here", name, t.dialect),
			n.Pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil || (entry.Kind == EntryKindRefusal && entry.Reason == "") {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s is a host function with no SQL spelling in dialect %s; register one with the map, or evaluate it here", name, t.dialect),
			n.Pos)
	}
	if entry.Kind == EntryKindRefusal {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s — %s", name, t.dialect, entry.Reason),
			n.Pos)
	}
	recorded := HostSpellingArity(t.dialect, name)
	minA, maxA, hasCurrent := sel.HostArity(name)
	if recorded != nil && hasCurrent {
		if recorded[0] != minA || recorded[1] != maxA {
			Refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s was registered again with the arity [%d, %d] after its SQL spelling was defined for [%d, %d]; define the spelling again", name, minA, maxA, recorded[0], recorded[1]),
				n.Pos)
		}
	}
	if t.strict {
		Refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s is spelled by the application, which SEL cannot check (host-function), and strict mode refuses that", name),
			n.Pos)
	}
	t.addCaveat("host-function")

	var args []*Fragment
	for i, arg := range n.Kids {
		kind := "ANY"
		if i < len(entry.Args) {
			kind = entry.Args[i]
		}
		if kind == "LIST" {
			args = append(args, t.hostListArgument(name, arg))
			continue
		}
		f := t.node(arg)
		if f.Kind == KindList {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("argument to %s is a list, and its spelling does not declare a LIST there", name),
				arg.Pos)
		}
		got := f.Kind.String()
		if (kind == "NUM" || kind == "TEXT") && (f.Kind == KindBool || f.Kind == KindBin) {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s declares this argument %s, and this is a %s", name, kind, got),
				arg.Pos)
		}
		if (kind == "BOOL" || kind == "BIN") && got != kind {
			valDesc := "a " + got
			if f.Kind == KindUnknown {
				valDesc = "not one this layer can prove"
			}
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s declares this argument %s, and this is %s", name, kind, valDesc),
				arg.Pos)
		}
		if kind == "NUM" {
			t.requireNumericConstant(arg)
			if f.Kind != KindNum {
				f = t.guardNumeric(f, arg)
			}
		}
		args = append(args, f)
	}
	return t.apply("funcs", name, args, n.Pos, nil)
}

func (t *Translator) hostListArgument(name string, arg *SNode) *Fragment {
	src := t.classify(arg)
	if src.Shape == SourceShapeRelation {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is a relation, rows the query has not read yet; a LIST argument is a list known when translating", name),
			arg.Pos)
	}
	if len(src.Filters) > 0 {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is a filtered list, whose elements are decided when it is evaluated; a template cannot express that", name),
			arg.Pos)
	}
	if len(src.Elements) == 0 {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is an empty list, which has nothing for the template to hold", name),
			arg.Pos)
	}
	var parts []Part
	for i, elem := range src.Elements {
		f := t.fromBinder(&elem.Val, arg)
		if f.Kind == KindList {
			Refuse("E_SQL_SHAPE",
				fmt.Sprintf("argument to %s has a list as an element, which has no scalar rendering", name),
				arg.Pos)
		}
		if i > 0 {
			parts = append(parts, Part{Sql: ", "})
		}
		parts = append(parts, f.Parts...)
	}
	return NewFragment(parts, KindUnknown, t.dialect, nil, nil, nil)
}

func aggShape(n *SNode) (string, *SNode) {
	args := n.Kids
	if len(args) == 3 {
		if !IsBinderName(args[1]) {
			Refuse("E_SQL_SHAPE", fmt.Sprintf("the binder of %s must be a bare name", n.Str), args[1].Pos)
		}
		return args[1].Str, args[2]
	}
	return "_", args[1]
}

var yieldsListSet = map[string]bool{"BTL": true, "INDEXES": true, "RGROUPS": true, "SPLIT": true}

func (t *Translator) classify(src *SNode) Source {
	var out Source
	if src.T == SNodeCall {
		name := src.Str
		if name == "FILTER" {
			binderName, body := aggShape(src)
			inner := t.classify(src.Kids[0])
			inner.Filters = append(inner.Filters, SourceFilter{Binder: binderName, Node: body})
			return inner
		}
		if name == "MAP" {
			Refuse("E_SQL_UNSUPPORTED",
				"MAP as the thing an aggregate iterates is not translated: unlike FILTER, which only decides whether an element takes part, MAP changes what the element is, so the two binders mean different things and binding both to one element is not enough. See docs/internals/sql-translation.md 7.5",
				src.Pos)
		}
	}
	if src.T == SNodeList {
		for i, kid := range src.Kids {
			out.Elements = append(out.Elements, Pair[string, Binder]{
				Key: strconv.Itoa(i + 1),
				Val: BinderNode(kid),
			})
		}
		return out
	}
	if src.T == SNodeCList {
		for i, kid := range src.Kids {
			out.Elements = append(out.Elements, Pair[string, Binder]{
				Key: src.Keys[i],
				Val: BinderNode(kid),
			})
		}
		return out
	}
	if src.T == SNodeVar {
		if bound := t.binder(src.Str); bound != nil {
			switch bound.Shape {
			case BinderShapeNode:
				return t.classify(bound.Node)
			case BinderShapeNone:
				Refuse("E_SQL_SHAPE", bound.Reason, src.Pos)
			case BinderShapeGroup:
				Refuse("E_SQL_SHAPE",
					fmt.Sprintf("%s is the list of a bucket's members, over which only COUNT and SUM are translated", src.Str),
					src.Pos)
			case BinderShapeProjected:
				Refuse("E_SQL_SHAPE",
					fmt.Sprintf("%s is the record the projection built, a map with one child per field; SQL has no way to iterate or count that", src.Str),
					src.Pos)
			case BinderShapeRow:
				if len(bound.Relation.Fields) > 1 {
					Refuse("E_SQL_SHAPE",
						fmt.Sprintf("%s is a row of a multi-field relation, which is a map with one child per field; SQL has no way to iterate or count that", src.Str),
						src.Pos)
				}
			case BinderShapeColumn, BinderShapeKey:
				// scalar rule below
			}
			out.Elements = append(out.Elements, Pair[string, Binder]{Key: "1", Val: *bound})
			out.ScalarRule = true
			return out
		}
		b := t.bindings.Get(src.Str, src.Pos)
		if b.Kind == BindingKindRelation {
			out.Shape = SourceShapeRelation
			out.Relation = &b.Relation
			return out
		}
		if b.Kind == BindingKindColumns {
			out.Shape = SourceShapeColumns
			for i, col := range b.Columns {
				out.Elements = append(out.Elements, Pair[string, Binder]{
					Key: strconv.Itoa(i + 1),
					Val: BinderColumn(col),
				})
			}
			return out
		}
		if b.Kind == BindingKindValue {
			v := b.Val
			out.Elements = t.valueElements(b, src.Pos)
			out.ScalarRule = v.Size() == 0 && !v.IsNone()
			return out
		}
	}
	if src.T == SNodeCall && (yieldsListSet[src.Str] || src.Str == "LIST" || src.Str == "RECORD" || isPipelineOp(src.Str)) {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s yields a list, and the scalar rule does not apply to it; SQL has no way to count or index what it produces", src.Str),
			src.Pos)
	}
	if src.T == SNodeCall {
		t.node(src)
	}
	out.Elements = append(out.Elements, Pair[string, Binder]{Key: "1", Val: BinderNode(src)})
	out.ScalarRule = true
	return out
}

func (t *Translator) withElement(src Source, binderName string, elem Binder, key string, n *SNode, render func() *Fragment) *Fragment {
	var frame Frame
	frameSet(&frame, binderName, elem)
	frameSet(&frame, "_K", BinderNode(Leaf(litNode(sel.NodeText, key, false, n.Pos))))
	for _, f := range src.Filters {
		frameSet(&frame, f.Binder, elem)
	}
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *Translator) withRow(src Source, binderName string, render func() *Fragment) *Fragment {
	alias := relationAlias(src.Relation)
	for _, frame := range t.frames {
		for _, kv := range frame {
			if kv.Val.Shape == BinderShapeRow && relationAlias(kv.Val.Relation) == alias {
				Refuse("E_SQL_SHAPE",
					fmt.Sprintf("this relation is already open as %s further out, and a subquery reusing its own alias shadows the outer row rather than comparing against it; the correlation names the alias, so it cannot be renamed here", alias),
					Pos{})
			}
		}
	}
	row := BinderRow(src.Relation)
	if t.statementPlan != nil && t.statementPlan.SourceRelation != nil && sameRelation(*src.Relation, t.statementPlan.SourceRelation.Relation) {
		row.Model = BuildJoinRows(t.statementPlan).Row
	}
	var frame Frame
	frameSet(&frame, binderName, row)
	frameSet(&frame, "_K", BinderNone("a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does"))
	for _, f := range src.Filters {
		frameSet(&frame, f.Binder, row)
	}
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *Translator) withGroup(src Source, binderName string, render func() *Fragment) *Fragment {
	var frame Frame
	frameSet(&frame, binderName, BinderGroup(src.Relation))
	if t.statementPlan != nil && t.statementPlan.GroupBy != nil && len(t.statementPlan.GroupBy) == 1 {
		gb := t.statementPlan.GroupBy[0]
		frameSet(&frame, "_K", BinderKey(gb.Binder, gb.Node, src.Relation))
	} else {
		frameSet(&frame, "_K", BinderNone("the key of a bucket over several keys is a list, which SQL has no value for; name one key"))
	}
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *Translator) withProjected(src Source, binderName string, render func() *Fragment) *Fragment {
	var projections []RelationalProjection
	if t.statementPlan != nil && t.statementPlan.Projections != nil {
		projections = t.statementPlan.Projections
	}
	var frame Frame
	frameSet(&frame, binderName, BinderProjected(src.Relation, projections))
	frameSet(&frame, "_K", BinderNone("after a projection the rows are a list renumbered from \"1\", and SQL has no row position to compare against"))
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *Translator) withJoinBinders(plan *RelationalPlan, join RelationalJoin, render func() *Fragment) *Fragment {
	jr := BuildJoinRows(plan)
	joinIdx := -1
	for i := range plan.Joins {
		if &plan.Joins[i] == &join {
			joinIdx = i
			break
		}
	}
	if joinIdx == -1 {
		for i := range plan.Joins {
			if plan.Joins[i].SourceName == join.SourceName && plan.Joins[i].SourceTable == join.SourceTable && plan.Joins[i].SourceAlias == join.SourceAlias {
				joinIdx = i
				break
			}
		}
	}
	step := jr.Steps[joinIdx]
	var frame Frame
	left := BinderRow(&plan.SourceRelation.Relation)
	left.Model = step.Left
	right := BinderRow(&join.SourceRelation.Relation)
	right.Model = step.Right
	frameSet(&frame, "_", left)
	frameSet(&frame, "_1", left)
	frameSet(&frame, "_2", right)
	for _, name := range join.LeftNames {
		frameSet(&frame, name, left)
	}
	for _, name := range join.RightNames {
		frameSet(&frame, name, right)
	}
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func aggFold(name string) string {
	switch name {
	case "ALL":
		return "AND"
	case "ANY":
		return "OR"
	case "SUM":
		return "+"
	}
	return ""
}

func aggSkeleton(name string) string {
	return strings.ToLower(name)
}

func aggReturns(name string) SqlKind {
	switch name {
	case "ALL", "ANY":
		return KindBool
	case "SUM":
		return KindNum
	case "JOIN":
		return KindText
	}
	return KindList
}

func (t *Translator) aggBody(name string, body *SNode, src Source, n *SNode) *Fragment {
	q := t.node(body)
	if name == "SUM" {
		t.requireNum(q, body.Pos, name)
	} else {
		t.requireBool(q, body.Pos, name)
	}

	for _, f := range src.Filters {
		p := t.requireBool(t.node(f.Node), f.Node.Pos, "FILTER")
		if name == "SUM" {
			q = t.caseWhen(p, q, t.literal(sel.NewTextOwned("0"), KindNum), n.Pos)
		} else if name == "ALL" {
			one := []*Fragment{p}
			pair := []*Fragment{t.apply("ops", "NOT", one, n.Pos, nil), q}
			q = t.apply("ops", "OR", pair, n.Pos, nil)
		} else {
			pair := []*Fragment{p, q}
			q = t.apply("ops", "AND", pair, n.Pos, nil)
		}
	}
	return q
}

func (t *Translator) relationAggregate(name string, rel RelationSpec, body *Fragment, n *SNode) *Fragment {
	isSeparate := (rel.Prefilter == "separate") || (rel.Prefilter == "" && body.SeparatePrefilter)
	if name == "ANY" && body.Prefilter != nil && isSeparate {
		preSlots := mergeSlots(
			t.relationSlots(rel),
			SlotMap{Pair[string, []Slot]{Key: "body", Val: []Slot{FragmentSlot(body.Prefilter)}}},
		)
		pre := NewFragment(t.fillNamed(t.skeleton("prefilter", n.Pos), preSlots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
		mainSlots := mergeSlots(
			t.relationSlots(rel),
			SlotMap{Pair[string, []Slot]{Key: "body", Val: []Slot{FragmentSlot(body)}}},
		)
		main := NewFragment(t.fillNamed(t.skeleton(aggSkeleton(name), n.Pos), mainSlots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
		return t.apply("ops", "AND", []*Fragment{pre, main}, n.Pos, nil)
	}
	slots := mergeSlots(
		t.relationSlots(rel),
		SlotMap{Pair[string, []Slot]{Key: "body", Val: []Slot{FragmentSlot(body)}}},
	)
	return NewFragment(t.fillNamed(t.skeleton(aggSkeleton(name), n.Pos), slots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
}

func (t *Translator) aggregate(n *SNode) *Fragment {
	name := n.Str
	if name == "MAP" || name == "FILTER" {
		Refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s yields a list, and a SQL expression is a scalar; it can only be the thing another aggregate iterates", name),
			n.Pos)
	}
	if name == "JOIN" {
		return t.joinAggregate(n)
	}

	binderName, bodyNode := aggShape(n)
	src := t.classify(n.Kids[0])

	if src.Shape == SourceShapeRelation {
		rendered := t.withRow(src, binderName, func() *Fragment {
			return t.aggBody(name, bodyNode, src, n)
		})
		return t.relationAggregate(name, *src.Relation, rendered, n)
	}

	var parts []*Fragment
	for _, kv := range src.Elements {
		key := kv.Key
		elem := kv.Val
		parts = append(parts, t.withElement(src, binderName, elem, key, n, func() *Fragment {
			return t.aggBody(name, bodyNode, src, n)
		}))
	}

	if len(parts) == 0 {
		if name == "ALL" {
			return t.literal(sel.NewBool(true), KindBool)
		}
		if name == "ANY" {
			return t.literal(sel.NewBool(false), KindBool)
		}
		return t.literal(sel.NewTextOwned("0"), KindNum)
	}
	if len(parts) == 1 {
		return parts[0]
	}
	return t.foldPairwise(aggFold(name), parts, n.Pos)
}

func (t *Translator) count(n *SNode) *Fragment {
	src := t.classify(n.Kids[0])

	if src.Shape != SourceShapeRelation && src.ScalarRule && len(src.Filters) == 0 {
		return t.literal(sel.NewTextOwned("0"), KindNum)
	}

	if len(src.Filters) > 0 {
		body := Leaf(litNode(sel.NodeNum, "1", false, n.Pos))
		if src.Shape == SourceShapeRelation {
			rendered := t.withRow(src, "_", func() *Fragment {
				return t.aggBody("SUM", body, src, n)
			})
			return t.relationAggregate("SUM", *src.Relation, rendered, n)
		}
		var parts []*Fragment
		for _, kv := range src.Elements {
			key := kv.Key
			elem := kv.Val
			parts = append(parts, t.withElement(src, "_", elem, key, n, func() *Fragment {
				return t.aggBody("SUM", body, src, n)
			}))
		}
		if len(parts) == 0 {
			return t.literal(sel.NewTextOwned("0"), KindNum)
		}
		if len(parts) == 1 {
			return parts[0]
		}
		return t.foldPairwise("+", parts, n.Pos)
	}

	if src.Shape == SourceShapeRelation {
		return NewFragment(
			t.fillNamed(t.skeleton("count", n.Pos), t.relationSlots(*src.Relation), n.Pos),
			KindNum,
			t.dialect,
			nil, nil, nil,
		)
	}
	return t.literal(sel.NewTextOwned(strconv.Itoa(len(src.Elements))), KindNum)
}

func (t *Translator) has(n *SNode) *Fragment {
	keyNode := n.Kids[1]
	if keyNode.T != SNodeText && keyNode.T != SNodeNum {
		Refuse("E_SQL_SHAPE",
			"HAS needs a constant key here: which column it asks about has to be known before the query runs",
			keyNode.Pos)
	}
	key := keyNode.Str
	src := t.classify(n.Kids[0])
	if len(src.Filters) > 0 {
		Refuse("E_SQL_SHAPE",
			"HAS over a FILTER would have to know at translation time which elements the filter kept",
			n.Pos)
	}
	if src.Shape == SourceShapeRelation {
		Refuse("E_SQL_SHAPE",
			"HAS over a relation asks whether it has a key, and a relation is a list of rows whose keys are positions; the answer needs the row count, which no expression here knows",
			n.Pos)
	}
	found := false
	if !src.ScalarRule {
		for _, elem := range src.Elements {
			if elem.Key == key {
				found = true
				break
			}
		}
	}
	return t.literal(sel.NewBool(found), KindBool)
}

func (t *Translator) joinAggregate(n *SNode) *Fragment {
	src := t.classify(n.Kids[0])

	if src.Shape == SourceShapeRelation {
		rel := *src.Relation
		var scalar *ColumnSpec
		if rel.Scalar != "" {
			scalar = rel.Field(utf8.AsciiUpper(rel.Scalar))
		}
		if scalar == nil {
			Refuse("E_SQL_SHAPE",
				"JOIN over a relation needs the binding to name a \"scalar\" field",
				n.Pos)
		}
		body := t.columnRef(*scalar)
		skel := t.skeleton("join", n.Pos)
		slots := mergeSlots(
			t.relationSlots(rel),
			SlotMap{
				Pair[string, []Slot]{Key: "body", Val: []Slot{FragmentSlot(body)}},
				Pair[string, []Slot]{Key: "sep", Val: []Slot{FragmentSlot(t.node(n.Kids[1]))}},
			},
		)
		return NewFragment(t.fillNamed(skel, slots, n.Pos), KindText, t.dialect, nil, nil, nil)
	}

	var parts []*Fragment
	for _, kv := range src.Elements {
		key := kv.Key
		elem := kv.Val
		if len(parts) > 0 {
			parts = append(parts, t.node(n.Kids[1]))
		}
		held := elem
		parts = append(parts, t.withElement(src, "_", held, key, n, func() *Fragment {
			return t.fromBinder(&held, n)
		}))
	}
	if len(parts) == 0 {
		return t.literal(sel.NewTextOwned(""), KindText)
	}
	if len(parts) == 1 {
		return parts[0]
	}
	return t.foldPairwise("&", parts, n.Pos)
}
