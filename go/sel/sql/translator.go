package sql

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/internal/vocab"
	"github.com/nathanjel/sel/go/sel"
)

type sourceShape int

const (
	sourceShapeStatic sourceShape = iota
	sourceShapeColumns
	sourceShapeRelation
)

type sourceFilter struct {
	Binder string
	Node   *sNode
}

type source struct {
	Shape      sourceShape
	Elements   []pair[string, binder]
	Relation   *relationSpec
	Filters    []sourceFilter
	ScalarRule bool
}

type frame []pair[string, binder]

type Options struct {
	Strict bool
}

type begun struct {
	Norm *sNode
	Plan *relationalPlan
}

type slot struct {
	Str  *string
	Frag *Fragment
}

func stringSlot(s string) slot {
	return slot{Str: &s}
}

func fragmentSlot(f *Fragment) slot {
	return slot{Frag: f}
}

type slotMap []pair[string, []slot]

type translator struct {
	dialect         string
	emit            *Emit
	bindings        *Bindings
	strict          bool
	params          []*sel.Value
	paramKinds      []SqlKind
	caveats         []string
	frames          []frame
	constNames      map[string]bool
	constRoot       *sel.Value
	depth           int
	nodes           int64
	statementPlan   *relationalPlan
	inWhere         bool
	subqueryCounter int
}

func newTranslator(dialect string, bindings *Bindings, options Options) *translator {
	if bindings == nil {
		bindings = NewBindings(nil)
	}
	return &translator{
		dialect:  dialect,
		emit:     newEmit(dialect),
		bindings: bindings,
		strict:   options.Strict,
	}
}

func sameRelation(a, b relationSpec) bool {
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

func childOf(n *sNode, key string) *sNode {
	if n.T == sNodeList {
		i := listKey(key)
		if i == nil || *i > len(n.Kids) {
			return nil
		}
		return n.Kids[*i-1]
	}
	if n.T == sNodeCList {
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

func frameSet(frame *frame, name string, b binder) {
	for i := range *frame {
		if (*frame)[i].Key == name {
			(*frame)[i].Val = b
			return
		}
	}
	*frame = append(*frame, pair[string, binder]{Key: name, Val: b})
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
	if b.valueType != nil && *b.valueType == KindNum {
		return KindNum
	}
	return KindText
}

func litNode(t sel.NodeType, s string, b bool, pos Pos) *sel.Node {
	return &sel.Node{
		T:   t,
		Pos: pos,
		S:   s,
		B:   b,
	}
}

func (t *translator) Begin(ast *sel.Node) begun {
	requireTarget(t.dialect, ast.Pos)
	t.bindings.CheckAliases(ast.Pos)

	t.params = nil
	t.paramKinds = nil
	t.caveats = nil
	t.frames = nil
	t.depth = 0
	t.nodes = 0
	t.subqueryCounter = 0

	constNames, constRoot := scope(t.bindings)
	t.constNames = constNames
	t.constRoot = constRoot

	normalised := normalise(ast, t.constNames, t.constRoot)
	plan := t.AnalyzePipeline(normalised)
	return begun{Norm: normalised, Plan: plan}
}

func (t *translator) Translate(ast *sel.Node) *Fragment {
	b := t.Begin(ast)
	if b.Plan != nil {
		return t.CompileStatement(b.Plan)
	}
	f := t.node(b.Norm)

	out := NewFragment(f.Parts, f.Kind, t.dialect, t.params, t.paramKinds, t.caveats)
	out.Canonical = f.Canonical
	return out
}

func (t *translator) TranslateStatement(ast *sel.Node) *Fragment {
	b := t.Begin(ast)
	if b.Plan == nil {
		refuse("E_SQL_SHAPE", "expected a relational query or pipeline", sel.Pos{})
	}
	return t.CompileStatement(b.Plan)
}

func (t *translator) addCaveat(name string) {
	if !containsString(t.caveats, name) {
		t.caveats = append(t.caveats, name)
	}
}

func (t *translator) node(n *sNode) *Fragment {
	// One per node dispatched, again on every re-entry (an inlined helper read
	// twice, an unrolled element, a binder read), and before any work that costs
	// what the expansion does: a refusal costs at most the budget
	// (docs/internals/sql-translation.md 7.4, MAX_SQL_NODES).
	t.nodes++
	if t.nodes > limits.MAX_SQL_NODES {
		refuse("E_SQL_SIZE",
			fmt.Sprintf("this rule expands to more than %d nodes once its helpers are inlined and its lists unrolled; SEL evaluates it in a fraction of that, but the SQL would be the size of what it expands to", limits.MAX_SQL_NODES),
			n.Pos)
	}
	t.depth++
	if t.depth > limits.MAX_DEPTH {
		t.depth--
		refuse("E_SQL_DEPTH",
			fmt.Sprintf("this expression nests deeper than SEL will evaluate (%d), so there is nothing to translate; the evaluator answers E_DEPTH for it", limits.MAX_DEPTH),
			n.Pos)
	}
	defer func() {
		t.depth--
	}()

	compound := n.T == sNodeBin || n.T == sNodeUn || n.T == sNodeCall
	if !compound || !isConstant(n, t.constNames) {
		return t.dispatch(n)
	}

	f := t.dispatch(n)
	validate(n, t.constRoot)
	return f
}

func (t *translator) dispatch(n *sNode) *Fragment {
	switch n.T {
	case sNodeNum:
		// A number is its text; the parameter parses it when it is read as one.
		return t.literal(sel.NewText(n.Str), KindNum)
	case sNodeText:
		t.requireNoNul(n.Str, n.Pos)
		return t.literal(sel.NewText(n.Str), KindText)
	case sNodeBool:
		return t.literal(sel.NewBool(n.BoolVal), KindBool)
	case sNodeVar:
		return t.variable(n)
	case sNodeIndex:
		return t.index(n)
	case sNodeUn:
		return t.unary(n)
	case sNodeBin:
		return t.binary(n)
	case sNodeList, sNodeCList:
		refuse("E_SQL_SHAPE", "a list is not a SQL value; a list can only be the thing an aggregate iterates", n.Pos)
	case sNodeCall:
		return t.call(n)
	}
	refuse("E_SQL_SHAPE", fmt.Sprintf("cannot translate a %s node", n.T), n.Pos)
	return nil
}

// requireNoNul refuses a text value with a NUL in it, in every render mode. A
// C-string client API truncates an inline statement at the NUL, and a driver
// that sends parameters may do the same; SEL text may hold U+0000, SQL text
// cannot portably.
func (t *translator) requireNoNul(s string, pos Pos) {
	if strings.ContainsRune(s, 0) {
		refuse("E_SQL_UNSUPPORTED", "a text value containing a NUL cannot be sent to a SQL server portably", pos)
	}
}

func (t *translator) literal(v *sel.Value, kind SqlKind) *Fragment {
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

func (t *translator) binder(name string) *binder {
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

func (t *translator) variable(n *sNode) *Fragment {
	if n.VarScope != varScopeFree {
		if bound := t.binder(n.Str); bound != nil {
			return t.fromBinder(bound, n)
		}
	}

	b := t.bindings.Get(n.Str, n.Pos)
	switch b.kind {
	case bindingKindColumn:
		return t.columnRef(b.column)
	case bindingKindValue:
		v := b.val
		if v.Size() > 0 {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is bound to a list, and a list is not a SQL value; it can only be the thing an aggregate iterates", n.Str),
				n.Pos)
		}
		if v.IsNone() {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is bound to an empty value, which is not a SQL value; only an aggregate can be given an empty binding", n.Str),
				n.Pos)
		}
		if v.IsText() {
			t.requireNoNul(v.Scalar(), n.Pos)
		}
		return t.literal(v, declaredKind(b, v))
	case bindingKindColumns, bindingKindRelation:
		kindStr := "relation"
		if b.kind == bindingKindColumns {
			kindStr = "columns"
		}
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is bound as a %s, which names a set of values rather than one; use it as the first argument of an aggregate, not as a value on its own", n.Str, kindStr),
			n.Pos)
	}
	refuse("E_SQL_BINDING", "unusable binding for "+n.Str, n.Pos)
	return nil
}

func (t *translator) columnRef(c columnSpec) *Fragment {
	sqlStr := c.Raw
	if !c.IsRaw {
		sqlStr = t.emit.Column(c.Table, c.Column)
	}
	f := &Fragment{
		Parts:             []Part{{Sql: sqlStr}},
		Kind:              c.Type,
		Dialect:           t.dialect,
		ExactCollation:    c.Exact,
		Sargable:          c.Sargable,
		Guard:             c.Guard,
		SeparatePrefilter: c.Prefilter == "separate",
		Canonical:         c.Canonical,
	}
	return f
}

func (t *translator) constantIndex(idx *sNode) string {
	if idx.T == sNodeNum || idx.T == sNodeText {
		return idx.Str
	}
	refuse("E_SQL_SHAPE",
		"an index must be a constant here: the column it names has to be known before the query runs",
		idx.Pos)
	return ""
}

func (t *translator) index(n *sNode) *Fragment {
	obj := n.L()
	if t.statementPlan != nil && obj.T == sNodeIndex {
		if row := t.rowPath(obj, n); row != nil {
			return t.rowField(row, "the row", t.constantIndex(n.R()), n)
		}
		t.node(n.L())
		refuse("E_SQL_SHAPE",
			"only a bound name can be indexed here; SQL has no way to index into the result of an expression",
			n.Pos)
	}
	if obj.T != sNodeVar {
		refuse("E_SQL_SHAPE",
			"only a bound name can be indexed here; SQL has no way to index into the result of an expression",
			n.Pos)
	}
	if bound := t.binder(obj.Str); bound != nil {
		return t.indexBinder(bound, obj.Str, t.constantIndex(n.R()), n)
	}
	b := t.bindings.Get(obj.Str, obj.Pos)
	key := t.constantIndex(n.R())

	switch b.kind {
	case bindingKindRelation:
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is a relation, which is a list of rows; indexing it names no value SEL can produce, so use an aggregate and index the row its binder gives you", obj.Str),
			n.Pos)
	case bindingKindColumns:
		i := listKey(key)
		count := len(b.columns)
		if i == nil || *i > count {
			refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%s] is outside that binding's %d column(s)", obj.Str, key, count),
				n.Pos)
		}
		return t.columnRef(b.columns[*i-1])
	case bindingKindValue:
		child := b.val.Get(key)
		if child == nil {
			refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%q] is not a key of that value", obj.Str, key),
				n.Pos)
		}
		if child.Size() > 0 {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%q] is a list, not a SQL value", obj.Str, key),
				n.Pos)
		}
		return t.literal(child, declaredKind(b, child))
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s is bound as a column, which has no parts to index", obj.Str),
		n.Pos)
	return nil
}

func (t *translator) groupKey(src source, gb relationalGroup, projected bool) *Fragment {
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

func (t *translator) identityGroupKey(n *sNode, f *Fragment) *Fragment {
	if f.Canonical && f.Kind == KindNum {
		return f
	}
	if f.Kind == KindUnknown {
		refuse("E_SQL_SHAPE", "group keys require proven scalar identity", n.Pos)
	}
	if f.Kind == KindNum {
		if n.T != sNodeVar && n.T != sNodeIndex && n.T != sNodeNum {
			refuse("E_SQL_SHAPE", "computed numeric group keys do not preserve SEL identity", n.Pos)
		}
		numeric := NewFragment(f.Parts, KindNum, t.dialect, f.Params, f.ParamKinds, f.Caveats)
		w := t.emit.TextOperand(numeric)
		out := NewFragment(w.Parts, KindText, t.dialect, w.Params, w.ParamKinds, w.Caveats)
		out.ExactCollation = true
		return out
	}
	return t.collatedKey(f)
}

func (t *translator) orderKey(f *Fragment, pos Pos) *Fragment {
	if f.Kind == KindNum {
		return f
	}
	if f.Canonical {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("CANON is text on %s, which SQL sorts by its bytes, and SEL sorts it as the number it is; sort it in memory", t.dialect),
			pos)
	}
	if f.Kind == KindText || f.Kind == KindUnknown {
		if t.strict {
			refuse("E_SQL_UNSUPPORTED",
				"a text key sorts by its bytes in SQL, where SEL sorts number-shaped text as numbers (text-order); strict mode refuses that",
				pos)
		}
		t.addCaveat("text-order")
	}
	return t.collatedKey(f)
}

func (t *translator) collatedKey(f *Fragment) *Fragment {
	if f.Kind != KindText || f.ExactCollation {
		return f
	}
	wrapped := t.emit.TextOperand(f)
	out := NewFragment(wrapped.Parts, KindText, t.dialect, wrapped.Params, wrapped.ParamKinds, wrapped.Caveats)
	out.ExactCollation = true
	return out
}

func (t *translator) rowPath(node *sNode, outer *sNode) *rowModel {
	if node.T == sNodeVar {
		b := t.binder(node.Str)
		if b != nil && b.Shape == binderShapeRow {
			return b.Model
		}
		return nil
	}
	if node.T != sNodeIndex {
		return nil
	}
	inner := t.rowPath(node.L(), node)
	if inner == nil {
		return nil
	}
	return t.rowNested(inner, t.constantIndex(node.R()), node, outer)
}

func (t *translator) rowNested(row *rowModel, key string, n *sNode, outer *sNode) *rowModel {
	if nested := nestedOf(row, key); nested != nil {
		return nested
	}
	if listKey(key) != nil {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", key),
			n.Pos)
	}
	if _, ok := rowFieldSpec(row, key); ok {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("[%q] is a field, which has no parts to index", key),
			outer.Pos)
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("only a bound name can be indexed here; SQL has no way to index into the result of an expression (%s names no record this row carries)", key),
		n.Pos)
	return nil
}

func (t *translator) rowField(row *rowModel, label string, key string, n *sNode) *Fragment {
	if listKey(key) != nil {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", label, key),
			n.Pos)
	}
	if nestedOf(row, key) != nil {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] is a record, which is a map in SEL and not one value; name the field you mean", label, key),
			n.Pos)
	}
	f, ok := rowFieldSpec(row, key)
	if !ok {
		if !row.Side && row.Dropped != nil && row.Dropped[utf8.AsciiUpper(key)] {
			refuse("E_SQL_SHAPE",
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
		refuse("E_SQL_BINDING",
			fmt.Sprintf("%s[%q] is not a field of that %s%s", label, key, relStr, tail),
			n.Pos)
	}
	if f.Optional {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] is a field of the right side of a LINK_LEFT, which a row with no match does not have; read it through the right binder", label, key),
			n.Pos)
	}
	if f.Spec.Unavailable != "" {
		refuse("E_SQL_SHAPE", f.Spec.Unavailable, n.Pos)
	}
	if f.Spec.IsRaw || !f.Qualify {
		return t.columnRef(f.Spec)
	}
	qualified := f.Spec
	qualified.Table = f.Table
	return t.columnRef(qualified)
}

// atScope renders what a binder holds in the scope it was written in.
func (t *translator) atScope(b *binder, render func() *Fragment) *Fragment {
	if !b.Scoped || b.Scope > len(t.frames) {
		return render()
	}
	saved := t.frames
	t.frames = t.frames[:b.Scope:b.Scope]
	defer func() { t.frames = saved }()
	return render()
}

func (t *translator) fromBinder(b *binder, n *sNode) *Fragment {
	switch b.Shape {
	case binderShapeNode:
		return t.atScope(b, func() *Fragment { return t.node(b.Node) })
	case binderShapeKey:
		var frame frame
		frameSet(&frame, b.GroupBinder, binderRow(b.Relation))
		t.frames = append(t.frames, frame)
		var key *Fragment
		func() {
			defer func() {
				t.frames = t.frames[:len(t.frames)-1]
			}()
			key = t.node(b.Node)
		}()
		wrapped := key.Kind == KindNum || (key.Kind == KindText && !key.ExactCollation)
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
			out.ExactCollation = true
			out.Canonical = key.Canonical
			return out
		}
		return collated
	case binderShapeColumn:
		return t.columnRef(b.Column)
	case binderShapeGroup:
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is the list of a bucket's members, which is not a value SQL has; count it (COUNT), sum over it (SUM), or name the group key (_K)", n.Str),
			n.Pos)
	case binderShapeProjected:
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s is the record the projection built, which is a map in SEL and not one value; name the field you mean", n.Str),
			n.Pos)
	case binderShapeRow:
		rel := b.Relation
		if b.Model != nil && !b.Model.Side {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is a joined row, which is a map in SEL and not one value; name the field you mean", n.Str),
				n.Pos)
		}
		if len(rel.Fields) > 1 {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s is a row of a relation with %d fields, which is a map in SEL and not one value; name the field you mean", n.Str, len(rel.Fields)),
				n.Pos)
		}
		var field *columnSpec
		if rel.Scalar != "" {
			field = rel.Field(utf8.AsciiUpper(rel.Scalar))
		}
		if field == nil {
			refuse("E_SQL_SHAPE",
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
	case binderShapeNone:
		refuse("E_SQL_SHAPE", b.Reason, n.Pos)
	}
	refuse("E_SQL_SHAPE", "unusable binder", n.Pos)
	return nil
}

func (t *translator) indexBinder(b *binder, name string, key string, n *sNode) *Fragment {
	if b.Shape == binderShapeGroup {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s[%q] indexes the list of a bucket's members, which SEL refuses (E_NO_KEY); read a member's field inside an aggregate over the group, SUM(%s, _[%q])", name, key, name, key),
			n.Pos)
	}
	if b.Shape == binderShapeProjected {
		var proj *relationalProjection
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
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%q] is not a field of the projection%s", name, key, tail),
				n.Pos)
		}
		src := source{
			Shape:    sourceShapeRelation,
			Relation: b.Relation,
		}
		if proj.GroupKey != nil {
			kb := binderKey(proj.GroupKey.Binder, proj.GroupKey.Node, b.Relation)
			return t.fromBinder(&kb, n)
		}
		return t.withGroup(src, proj.Binder, func() *Fragment {
			return t.node(proj.Node)
		})
	}
	if b.Shape == binderShapeRow {
		if listKey(key) != nil {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s[%s] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply", name, key),
				n.Pos)
		}
		if b.Model != nil {
			return t.rowField(b.Model, name, key, n)
		}
		rel := b.Relation
		field := utf8.AsciiUpper(key)
		if f := rel.Field(field); f != nil {
			if f.Unavailable != "" {
				refuse("E_SQL_SHAPE", f.Unavailable, n.Pos)
			}
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
		refuse("E_SQL_BINDING",
			fmt.Sprintf("%s[%q] is not a field of that relation%s", name, key, tail),
			n.Pos)
	}
	if b.Shape == binderShapeNode {
		elem := childOf(b.Node, key)
		if elem == nil {
			refuse("E_SQL_BINDING",
				fmt.Sprintf("%s[%q] is not a key of that element", name, key),
				n.Pos)
		}
		return t.atScope(b, func() *Fragment { return t.node(elem) })
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s names a single column, which has no parts to index", name),
		n.Pos)
	return nil
}

func (t *translator) relationTableAlias(rel *relationSpec, def string) string {
	if rel == nil {
		return def
	}
	if t.statementPlan != nil {
		if t.statementPlan.SourceRelation != nil && sameRelation(*rel, t.statementPlan.SourceRelation.relation) {
			if t.statementPlan.SourceAlias != "" {
				return t.statementPlan.SourceAlias
			}
			return relationAlias(rel)
		}
		for _, join := range t.statementPlan.Joins {
			if join.SourceRelation != nil && sameRelation(*rel, join.SourceRelation.relation) {
				if join.SourceAlias != "" {
					return join.SourceAlias
				}
				return relationAlias(rel)
			}
		}
	}
	return relationAlias(rel)
}

func (t *translator) relationColumn(rel *relationSpec, c columnSpec) *Fragment {
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
	// Both kinds as they are: a BIN and a TEXT reach here too, and were reported
	// as "a BOOL with a BIN".
	what := fmt.Sprintf("%s compares a %s with a %s", op, l.Kind, r.Kind)
	if strings.HasPrefix(op, "$") {
		// SEL reads a BIN and a TEXT here as bytes, and a BOOL is not an operand
		// of the byte comparisons at all (E_NOT_BIN); SQL would cast both sides
		// to characters, which says neither.
		refuse("E_SQL_SHAPE", what+", which SEL compares as bytes (or refuses, for a BOOL); SQL has no way to say that: both sides cast to the same characters", pos)
	}
	refuse("E_SQL_SHAPE", what+", which SEL answers FALSE for every value because the kinds differ. SQL has no way to say that: both sides cast to the same characters", pos)
}

func unify(fs []*Fragment, pos Pos) SqlKind {
	var kind *SqlKind
	sawUnknown := false
	for _, f := range fs {
		if f.Kind == KindUnknown {
			sawUnknown = true
			continue
		}
		if kind == nil {
			k := f.Kind
			kind = &k
			continue
		}
		if *kind != f.Kind {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("these branches produce different kinds — %s and %s — and SQL gives the whole expression one type, which cannot match SEL's for both", *kind, f.Kind),
				pos)
		}
	}
	// UNKNOWN does not unify with anything: a branch nobody vouched for makes the
	// whole conditional something nobody vouched for, so it keeps its guard (or is
	// refused where no guard can be written) instead of inheriting the kind of the
	// branch beside it.
	if kind == nil || sawUnknown {
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

func (t *translator) requireBool(f *Fragment, pos Pos, where string) *Fragment {
	if f.Kind == KindBool {
		return f
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s needs a BOOL here and this is %s; SEL has no truthiness, so neither does its translation", where, f.Kind),
		pos)
	return nil
}

func (t *translator) requireNum(f *Fragment, pos Pos, where string) *Fragment {
	if f.Kind == KindNum || f.Kind == KindUnknown {
		return f
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s adds its body up, so it needs a number here and this is %s", where, f.Kind),
		pos)
	return nil
}

func (t *translator) requireNotBool(f *Fragment, pos Pos, where string) {
	if f.Kind != KindBool && f.Kind != KindBin {
		return
	}
	what := "a BIN"
	if f.Kind == KindBool {
		what = "a BOOL"
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s reads its operands as numbers, and %s is not one; SEL answers E_NOT_NUM here rather than coercing it", where, what),
		pos)
}

func (t *translator) requireNotBoolOperand(f *Fragment, pos Pos, where string) {
	if f.Kind != KindBool {
		return
	}
	refuse("E_SQL_SHAPE",
		fmt.Sprintf("%s reads its operands as text or bytes, and a BOOL is neither; SEL answers E_NOT_TEXT here rather than spelling it 1 or true", where),
		pos)
}

func (t *translator) requireNumericConstant(n *sNode) {
	if isConstant(n, t.constNames) {
		requireNumeric(n, t.constRoot)
	}
}

func (t *translator) guardNumeric(f *Fragment, n *sNode) *Fragment {
	if isConstant(n, t.constNames) {
		return f
	}
	wraps := f.Kind != KindNum || f.Guard
	guarded := t.emit.NumericOperand(f, n.Pos)
	if wraps {
		t.scaleLimited(n.Pos, "this operand is read as a number")
	}
	return guarded
}

func (t *translator) numericCastScale() *int32 {
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

func (t *translator) scaleLimited(pos Pos, what string) {
	limit := t.numericCastScale()
	if limit == nil {
		return
	}
	if t.strict {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s through a DECIMAL that keeps %d fractional digits, and a value with more loses them on %s (scale-limit); strict mode refuses that", what, *limit, t.dialect),
			pos)
	}
	t.addCaveat("scale-limit")
}

func (t *translator) coerceScaleLimits(operands []*sNode) {
	limit := t.numericCastScale()
	if limit == nil {
		return
	}
	for _, operand := range operands {
		if isConstant(operand, t.constNames) {
			if constantScale(operand, t.constRoot) > int(*limit) {
				t.scaleLimited(operand.Pos, "this constant is read as a number")
			}
		} else {
			t.scaleLimited(operand.Pos, "this operand is read as a number")
		}
	}
}

var (
	byteComparisonsSet = map[string]bool{"$==": true, "$!=": true, "$<": true, "$<=": true, "$>": true, "$>=": true, "EQL": true, "IN": true}
	arithmeticOpsSet   = map[string]bool{"+": true, "-": true, "*": true, "/": true, "%": true}
)

func (t *translator) variantFor(op string, args []*Fragment) *string {
	if vocab.IsNumericComparison(op) {
		v := "coerce"
		if args[0].Kind == KindNum && args[1].Kind == KindNum {
			v = "num"
		}
		return &v
	}
	if vocab.IsTextComparison(op) || op == "EQL" {
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

func (t *translator) templateOf(entry *EntryRecord, args []*Fragment, variant *string, what string, pos Pos) string {
	if len(entry.Variants) > 0 {
		var arm *string
		if variant != nil {
			if v, ok := entry.Variants[*variant]; ok && v != missingEntry {
				arm = &v
			}
		}
		if arm == nil {
			varDesc := "this shape"
			if variant != nil {
				varDesc = *variant + " operands"
			}
			refuse("E_SQL_UNSUPPORTED",
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
			if arm != "" && arm != missingEntry {
				chosen = &arm
			}
		} else if hasStar {
			if star != "" && star != missingEntry {
				chosen = &star
			}
		}
		if chosen == nil {
			var keys []string
			for k := range m {
				if m[k] != "" && m[k] != missingEntry {
					keys = append(keys, k)
				}
			}
			sort.Strings(keys)
			refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s has no mapping in dialect %s for %s argument(s); it maps %s", what, t.dialect, n, strings.Join(keys, ", ")),
				pos)
		}
		return *chosen
	}
	refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("%s has no template in dialect %s", what, t.dialect), pos)
	return ""
}

func (t *translator) apply(section string, key string, args []*Fragment, pos Pos, variant *string) *Fragment {
	raw := Entry(t.dialect, section, key)
	what := key
	if section == "ops" {
		what = "the " + key + " operator"
	}
	if raw == missingEntry || raw == nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s", what, t.dialect),
			pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s", what, t.dialect),
			pos)
	}
	if entry.Kind == EntryKindRefusal {
		reason := ""
		if entry.Reason != "" {
			reason = " — " + entry.Reason
		}
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s%s", what, t.dialect, reason),
			pos)
	}
	if entry.Kind == EntryKindBuilder {
		return entry.Builder(t.emit, args, pos)
	}
	if entry.Arity != nil {
		n := len(args)
		if n < entry.Arity[0] || n > entry.Arity[1] {
			refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s has no mapping in dialect %s for %d argument(s)", what, t.dialect, n),
				pos)
		}
	}
	if entry.Since != "" && !versionAtLeast(Version(t.dialect), entry.Since) {
		refuse("E_SQL_DIALECT",
			fmt.Sprintf("%s needs %s %s or newer, and this map says %s", what, t.dialect, entry.Since, Version(t.dialect)),
			pos)
	}
	if entry.Caveat != "" {
		if t.strict {
			refuse("E_SQL_UNSUPPORTED",
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

// foldPairwise combines the operands of an unroll through the operator's own
// binary template: left to right up to 256 operands (byte for byte what a
// hand-written chain renders), and above that a balanced tree, the left half the
// larger, each half folded again. A left fold n deep is a tree servers refuse:
// SQLite stops at depth 1000, MariaDB and MySQL overrun their stack at about 900
// terms, PostgreSQL runs out of memory at 5000 (docs/internals/sql-translation.md
// 7.1).
func (t *translator) foldPairwise(op string, parts []*Fragment, pos Pos) *Fragment {
	if len(parts) > 256 {
		m := (len(parts) + 1) / 2
		pair := []*Fragment{t.foldPairwise(op, parts[:m], pos), t.foldPairwise(op, parts[m:], pos)}
		return t.apply("ops", op, pair, pos, t.variantFor(op, pair))
	}
	acc := parts[0]
	for i := 1; i < len(parts); i++ {
		pair := []*Fragment{acc, parts[i]}
		acc = t.apply("ops", op, pair, pos, t.variantFor(op, pair))
	}
	return acc
}

// arithmeticOperand: an operand that is a constant TEXT holding a number, in an
// arithmetic position, is that number: SEL computes with it exactly, and
// MariaDB and MySQL would read the quoted string as a DOUBLE. It is translated as the
// numeric literal it stands for. The text was translated first (its SQL kind is only
// known then), so the slots it bound are taken back, or `params` mode would report a
// value bound that no placeholder uses.
func (t *translator) arithmeticOperand(n *sNode) *Fragment {
	mark := len(t.params)
	f := t.node(n)
	if f.Kind != KindText || !isConstant(n, t.constNames) {
		return f
	}
	text, ok := numericTextConstant(n, t.constRoot)
	if !ok {
		return f
	}
	t.params = t.params[:mark]
	t.paramKinds = t.paramKinds[:mark]
	return t.node(leaf(litNode(sel.NodeNum, text, false, n.Pos)))
}

func (t *translator) unary(n *sNode) *Fragment {
	var x *Fragment
	if n.Str == "NOT" {
		x = t.node(n.L())
	} else {
		x = t.arithmeticOperand(n.L())
	}
	if n.Str == "NOT" {
		x = t.requireBool(x, n.L().Pos, "NOT")
	} else {
		t.requireNotBool(x, n.L().Pos, n.Str)
		x = t.guardNumeric(x, n.L())
	}
	return t.apply("ops", n.Str, []*Fragment{x}, n.Pos, nil)
}

func (t *translator) binary(n *sNode) *Fragment {
	op := n.Str
	if op == "IN" {
		return t.inOperator(n)
	}

	var l, r *Fragment
	if arithmeticOpsSet[op] {
		l = t.arithmeticOperand(n.L())
		r = t.arithmeticOperand(n.R())
	} else {
		l = t.node(n.L())
		r = t.node(n.R())
	}

	if op == "AND" || op == "OR" || op == "XOR" {
		l = t.requireBool(l, n.L().Pos, op)
		r = t.requireBool(r, n.R().Pos, op)
	}
	if arithmeticOpsSet[op] || vocab.IsNumericComparison(op) {
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
		t.coerceScaleLimits([]*sNode{n.L(), n.R()})
	}

	if byteComparisonsSet[op] {
		requireComparableKinds(l, r, op, n.Pos)
		lExact := l.ExactCollation
		rExact := r.ExactCollation
		lLit := n.L() != nil && n.L().T == sNodeText
		rLit := n.R() != nil && n.R().T == sNodeText
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

func mergeSlots(a slotMap, b slotMap) slotMap {
	out := append(slotMap(nil), a...)
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

func (t *translator) skeleton(name string, pos Pos) string {
	raw := Entry(t.dialect, "skel", name)
	if raw == missingEntry || raw == nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
			pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
			pos)
	}
	if entry.Kind == EntryKindRefusal {
		if entry.Reason == "" {
			refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
				pos)
		}
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("dialect %s cannot express %s — %s", t.dialect, name, entry.Reason),
			pos)
	}
	if entry.Kind == EntryKindBuilder {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("the %s skeleton for %s is a builder, and a skeleton is a template", name, t.dialect),
			pos)
	}
	if entry.Caveat != "" {
		if t.strict {
			refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("the %s skeleton for %s is not exactly equivalent (%s), and strict mode refuses those", name, t.dialect, entry.Caveat),
				pos)
		}
		t.addCaveat(entry.Caveat)
	}
	if s, ok := entry.Tpl.(string); ok {
		return s
	}
	refuse("E_SQL_UNSUPPORTED",
		fmt.Sprintf("dialect %s has no %s skeleton", t.dialect, name),
		pos)
	return ""
}

func (t *translator) fillNamed(tpl string, slots slotMap, pos Pos) []Part {
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

		var items []slot
		found := false
		for _, kv := range slots {
			if kv.Key == name {
				items = kv.Val
				found = true
				break
			}
		}
		if !found {
			refuse("E_SQL_UNSUPPORTED",
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

func (t *translator) relationSlots(rel relationSpec) slotMap {
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
		// An application expression: a top-level OR in it must not change the
		// meaning of the AND the template puts after it (sql/MAP.md §5).
		corr = "(" + rel.Correlate + ")"
	}
	return slotMap{
		pair[string, []slot]{Key: "from", Val: []slot{stringSlot(from)}},
		pair[string, []slot]{Key: "corr", Val: []slot{stringSlot(corr)}},
	}
}

func (t *translator) valueNode(v *sel.Value, b *Binding, pos Pos) *sNode {
	if v.Size() > 0 {
		var entries []cListEntry
		for _, e := range v.Entries() {
			entries = append(entries, cListEntry{
				Key: e.Key,
				Val: t.valueNode(e.Val, b, pos),
			})
		}
		return cList(pos, entries)
	}
	if v.IsBool() {
		return leaf(litNode(sel.NodeBool, "", v.AsBool(Pos{}), pos))
	}
	if v.IsBin() {
		refuse("E_SQL_SHAPE",
			"a BIN element of a value binding has no literal node to become; bind it as a column, or convert it before translating",
			pos)
	}
	if v.IsNone() {
		// A NULL element: AsText would raise E_NULL, a SelError that TryTranslate does not
		// catch. It is a binding problem, and a refusal.
		refuse("E_SQL_BINDING", "a value binding holds a NULL element, which has no SQL literal", pos)
	}
	num := b.valueType != nil && *b.valueType == KindNum
	nodeType := sel.NodeText
	if num {
		nodeType = sel.NodeNum
	}
	return leaf(litNode(nodeType, v.AsText(pos), false, pos))
}

func (t *translator) valueElements(b *Binding, pos Pos) []pair[string, binder] {
	v := b.val
	if v.Size() == 0 {
		if v.IsNone() {
			return nil
		}
		return []pair[string, binder]{{Key: "1", Val: binderNode(t.valueNode(v, b, pos))}}
	}
	var out []pair[string, binder]
	for _, e := range v.Entries() {
		out = append(out, pair[string, binder]{
			Key: e.Key,
			Val: binderNode(t.valueNode(e.Val, b, pos)),
		})
	}
	return out
}

func (t *translator) inOperator(n *sNode) *Fragment {
	rhs := n.R()
	rhsIsFreeVar := rhs.T == sNodeVar && (rhs.VarScope == varScopeFree || t.binder(rhs.Str) == nil)

	if rhsIsFreeVar && t.bindings.Has(rhs.Str) {
		b := t.bindings.Get(rhs.Str, rhs.Pos)
		if b.kind == bindingKindRelation {
			rel := b.relation
			var scalar *columnSpec
			if rel.Scalar != "" {
				scalar = rel.Field(utf8.AsciiUpper(rel.Scalar))
			}
			if scalar == nil {
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("IN over %s needs the binding to name a \"scalar\" field: that is the column the subquery projects", rhs.Str),
					rhs.Pos)
			}
			if len(rel.Fields) != 1 {
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("IN over %s is refused: the relation declares %d fields, so SEL reads its rows as maps and a scalar can never equal one. Bind the projected column as a relation with that one field.", rhs.Str, len(rel.Fields)),
					rhs.Pos)
			}
			skel := t.skeleton("inRelation", n.Pos)
			needleFrag := t.node(n.L())
			column := t.columnRef(*scalar)
			// The kinds have to be comparable, as they do for `x IN (list)`: a BOOL
			// or BIN needle against a TEXT column matches the rows whose text is
			// '1' or 'true', or spells the same bytes.
			if needleFrag.Kind == KindBool || needleFrag.Kind == KindBin {
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("IN over %s compares a %s with its rows, which SEL answers FALSE for every text; SQL would compare spellings", rhs.Str, needleFrag.Kind),
					n.L().Pos)
			}
			requireComparableKinds(needleFrag, column, "IN", n.Pos)
			slots := mergeSlots(
				t.relationSlots(rel),
				slotMap{
					pair[string, []slot]{Key: "needle", Val: []slot{fragmentSlot(t.emit.TextOperand(needleFrag))}},
					pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(t.emit.TextOperand(column))}},
				},
			)
			parts := t.fillNamed(skel, slots, n.Pos)
			return NewFragment(parts, KindBool, t.dialect, nil, nil, nil)
		}
	}

	var elements []*sNode
	hasElements := false
	if rhs.T == sNodeList || rhs.T == sNodeCList {
		elements = rhs.Kids
		hasElements = true
	} else if rhsIsFreeVar && t.bindings.Has(rhs.Str) {
		b := t.bindings.Get(rhs.Str, rhs.Pos)
		if b.kind == bindingKindValue && b.val.Size() > 0 {
			elems := t.valueElements(b, rhs.Pos)
			elements = make([]*sNode, len(elems))
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
		isExact := raw.ExactCollation
		needle := raw
		if !isExact {
			needle = t.emit.TextOperand(raw)
		}
		f := t.node(e)
		if f.Kind == KindList {
			refuse("E_SQL_SHAPE",
				"IN over a list of lists is structural in SEL and has no SQL counterpart",
				e.Pos)
		}
		requireComparableKinds(raw, f, "IN", e.Pos)
		item := f
		if !isExact || f.Kind != KindText {
			// An exact column compares as itself, but a NUMBER beside it is
			// still spelled as text, or the server would compare by numeric
			// prefix ('25/298' = 25).
			item = t.emit.TextOperand(f)
		}
		args := []*Fragment{needle, item}
		tests = append(tests, t.apply("ops", "EQL", args, e.Pos, &textVar))
	}
	return t.foldPairwise("OR", tests, n.Pos)
}

func (t *translator) conditional(n *sNode) *Fragment {
	name := n.Str
	args := append([]*sNode(nil), n.Kids...)
	if name == "IF" && len(args) == 2 {
		args = append(args, leaf(litNode(sel.NodeText, "", false, n.Pos)))
	}
	branchTpl := t.skeleton("caseBranch", n.Pos)
	caseTpl := t.skeleton("case", n.Pos)

	last := len(args) - 1
	var results []*Fragment
	var joined []slot

	for i := 0; i < last; i += 2 {
		cond := t.requireBool(t.node(args[i]), args[i].Pos, name)
		then := t.node(args[i+1])
		results = append(results, then)
		slots := slotMap{
			pair[string, []slot]{Key: "cond", Val: []slot{fragmentSlot(cond)}},
			pair[string, []slot]{Key: "then", Val: []slot{fragmentSlot(then)}},
		}
		branch := NewFragment(t.fillNamed(branchTpl, slots, n.Pos), KindUnknown, t.dialect, nil, nil, nil)
		if len(joined) > 0 {
			joined = append(joined, stringSlot(" "))
		}
		joined = append(joined, fragmentSlot(branch))
	}
	els := t.node(args[last])
	results = append(results, els)

	slots := slotMap{
		pair[string, []slot]{Key: "branches", Val: joined},
		pair[string, []slot]{Key: "else", Val: []slot{fragmentSlot(els)}},
	}
	return NewFragment(t.fillNamed(caseTpl, slots, n.Pos), unify(results, n.Pos), t.dialect, nil, nil, nil)
}

func (t *translator) caseWhen(cond, then, els *Fragment, pos Pos) *Fragment {
	branchSlots := slotMap{
		pair[string, []slot]{Key: "cond", Val: []slot{fragmentSlot(cond)}},
		pair[string, []slot]{Key: "then", Val: []slot{fragmentSlot(then)}},
	}
	branch := NewFragment(t.fillNamed(t.skeleton("caseBranch", pos), branchSlots, pos), KindUnknown, t.dialect, nil, nil, nil)
	slots := slotMap{
		pair[string, []slot]{Key: "branches", Val: []slot{fragmentSlot(branch)}},
		pair[string, []slot]{Key: "else", Val: []slot{fragmentSlot(els)}},
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

func (t *translator) requireArgumentKind(name string, f *Fragment, pos Pos) {
	if f.Kind == KindBool && !boolArgumentOkSet[name] {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s does not take a BOOL argument; SEL raises here rather than reading a boolean as text or as 1", name),
			pos)
	}
	if f.Kind == KindBin && !binArgumentOkSet[name] {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s reads its argument as text, and this is BIN; SEL raises here rather than reinterpreting bytes as characters", name),
			pos)
	}
}

func regexAt(name string) *int {
	if _, ok := vocab.RegexFlagsAt(name); ok {
		zero := 0 // the pattern
		return &zero
	}
	return nil
}

func (t *translator) rewriteRegex(n *sNode) *sNode {
	at := regexAt(n.Str)
	if at == nil {
		return n
	}
	args := append([]*sNode(nil), n.Kids...)
	patAt := *at
	var pat *sNode
	if patAt < len(args) {
		pat = args[patAt]
	}
	if pat == nil || pat.T != sNodeText {
		pPos := n.Pos
		if pat != nil {
			pPos = pat.Pos
		}
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s needs a literal pattern here: SEL rewrites \\d, \\w and \\s into explicit ASCII classes before matching, and a pattern that is not known until the query runs cannot be rewritten", n.Str),
			pPos)
	}

	var source string
	if refusal, se := catch(func() { source = sel.ValidatePattern(pat.Str, pat.Pos) }); se != nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s's pattern is not in SEL's portable subset, so there is nothing to translate: %s", n.Str, se.Message),
			pat.Pos)
	} else if refusal != nil {
		panic(refusal)
	}

	inlineFlags := "(?s)"
	flagAt, _ := vocab.RegexFlagsAt(n.Str)
	if flagAt >= len(args) {
		args[patAt] = leaf(litNode(sel.NodeText, inlineFlags+source, false, pat.Pos))
		return rewritten(n.Origin, args)
	}
	flags := args[flagAt]
	if flags.T != sNodeText {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s needs literal flags here: their content selects the mapping, so they have to be known before the query runs", n.Str),
			flags.Pos)
	}
	text := flags.Str
	if text != "" && text != "i" && text != "I" {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s accepts only the i flag here, and SEL accepts only i at all; %q is not it", n.Str, text),
			flags.Pos)
	}
	if text != "" {
		for i := 0; i < len(source); i++ {
			if source[i] > 0x7f {
				refuse("E_SQL_UNSUPPORTED",
					"the i flag needs an ASCII-only pattern, which SEL requires for the same reason and refuses here too",
					flags.Pos)
			}
		}
		inlineFlags = "(?si)"
	}
	args[patAt] = leaf(litNode(sel.NodeText, inlineFlags+source, false, pat.Pos))
	args = append(args[:flagAt], args[flagAt+1:]...)
	return rewritten(n.Origin, args)
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

func (t *translator) call(n *sNode) *Fragment {
	name := n.Str

	if t.statementPlan != nil && len(n.Kids) > 0 && n.Kids[0].T == sNodeVar {
		group := t.binder(n.Kids[0].Str)
		if group != nil && group.Shape == binderShapeGroup {
			if name == "COUNT" && len(n.Kids) == 1 {
				return NewFragment([]Part{{Sql: "COUNT(*)"}}, KindNum, t.dialect, nil, nil, nil)
			}
			if name == "SUM" && len(n.Kids) >= 2 {
				// SUM(group, binder, body): a binder slot that is not a bare name
				// is E_EXPECT_SYMBOL in SEL, so it is refused, not summed as the body.
				binderName, bodyNode := aggShape(n)
				src := source{
					Shape:    sourceShapeRelation,
					Relation: group.Relation,
				}
				inner := t.withRow(src, binderName, func() *Fragment {
					return t.node(bodyNode)
				})
				t.requireNumericConstant(bodyNode)
				t.requireNum(inner, bodyNode.Pos, "SUM")
				if inner.Kind == KindUnknown {
					return t.allOrNothingSum(inner, bodyNode.Pos, n.Pos)
				}
				// The body's parts are spliced, slots and all: joining their SQL
				// text dropped every literal in it.
				parts := []Part{{Sql: "COALESCE(SUM("}}
				parts = append(parts, inner.Parts...)
				parts = append(parts, Part{Sql: "), 0)"})
				return NewFragment(parts, KindNum, t.dialect, inner.Params, inner.ParamKinds, inner.Caveats)
			}
		}
	}

	t.requireBinderName(n)
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
		refuse("E_SQL_SHAPE", "INDEXES yields a list of keys, and a SQL expression is a scalar", n.Pos)
	}
	if name == "ABORT" {
		refuse("E_SQL_UNSUPPORTED", "ABORT raises an error, which is a control-flow effect and not a value a SQL expression can be", n.Pos)
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
		// MIN and MAX compare their arguments as numbers: a numeric text constant is
		// the number, as in arithmetic.
		var f *Fragment
		if name == "MIN" || name == "MAX" {
			f = t.arithmeticOperand(arg)
		} else {
			f = t.node(arg)
		}
		if f.Kind == KindList {
			refuse("E_SQL_SHAPE",
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

// requireBinderName refuses, where it stands, a binding function whose binder
// position holds something that is not a name. SEL reaches the same program at
// run time as E_EXPECT_SYMBOL, so it compiles and arrives here; the answer is a
// refusal at that expression, whatever the statement form around it.
func (t *translator) requireBinderName(n *sNode) {
	if n.Origin == nil || n.Spec == nil || !n.Spec.Binds {
		return
	}
	form := sel.BindingForm(n.Str, n.Origin.Items, n.Spec)
	if form == nil {
		return
	}
	for i, sc := range form.Scopes {
		if sc == manifest.ScopeBinder && i < len(n.Kids) && !isBinderName(n.Kids[i]) {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("the binder of %s must be a bare name", n.Str), n.Kids[i].Pos)
		}
	}
}

func (t *translator) hostCall(n *sNode) *Fragment {
	name := n.Str
	raw := Entry(t.dialect, "funcs", name)
	if raw == missingEntry || raw == nil {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s is a host function with no SQL spelling in dialect %s; register one with the map, or evaluate it here", name, t.dialect),
			n.Pos)
	}
	entry, ok := raw.(*EntryRecord)
	if !ok || entry == nil || (entry.Kind == EntryKindRefusal && entry.Reason == "") {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s is a host function with no SQL spelling in dialect %s; register one with the map, or evaluate it here", name, t.dialect),
			n.Pos)
	}
	if entry.Kind == EntryKindRefusal {
		refuse("E_SQL_UNSUPPORTED",
			fmt.Sprintf("%s has no mapping in dialect %s — %s", name, t.dialect, entry.Reason),
			n.Pos)
	}
	recorded := hostSpellingArity(t.dialect, name)
	minA, maxA, hasCurrent := sel.HostArity(name)
	if recorded != nil && hasCurrent {
		if recorded[0] != minA || recorded[1] != maxA {
			refuse("E_SQL_UNSUPPORTED",
				fmt.Sprintf("%s was registered again with the arity [%d, %d] after its SQL spelling was defined for [%d, %d]; define the spelling again", name, minA, maxA, recorded[0], recorded[1]),
				n.Pos)
		}
	}
	if t.strict {
		refuse("E_SQL_UNSUPPORTED",
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
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("argument to %s is a list, and its spelling does not declare a LIST there", name),
				arg.Pos)
		}
		got := f.Kind.String()
		if (kind == "NUM" || kind == "TEXT") && (f.Kind == KindBool || f.Kind == KindBin) {
			refuse("E_SQL_SHAPE",
				fmt.Sprintf("%s declares this argument %s, and this is a %s", name, kind, got),
				arg.Pos)
		}
		if (kind == "BOOL" || kind == "BIN") && got != kind {
			valDesc := "a " + got
			if f.Kind == KindUnknown {
				valDesc = "not one this layer can prove"
			}
			refuse("E_SQL_SHAPE",
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

func (t *translator) hostListArgument(name string, arg *sNode) *Fragment {
	src := t.classify(arg)
	if src.Shape == sourceShapeRelation {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is a relation, rows the query has not read yet; a LIST argument is a list known when translating", name),
			arg.Pos)
	}
	if len(src.Filters) > 0 {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is a filtered list, whose elements are decided when it is evaluated; a template cannot express that", name),
			arg.Pos)
	}
	if len(src.Elements) == 0 {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("argument to %s is an empty list, which has nothing for the template to hold", name),
			arg.Pos)
	}
	var parts []Part
	for i, elem := range src.Elements {
		f := t.fromBinder(&elem.Val, arg)
		if f.Kind == KindList {
			refuse("E_SQL_SHAPE",
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

func aggShape(n *sNode) (string, *sNode) {
	args := n.Kids
	if len(args) == 3 {
		if !isBinderName(args[1]) {
			refuse("E_SQL_SHAPE", fmt.Sprintf("the binder of %s must be a bare name", n.Str), args[1].Pos)
		}
		return args[1].Str, args[2]
	}
	return "_", args[1]
}

var yieldsListSet = map[string]bool{"BTL": true, "INDEXES": true, "RGROUPS": true, "SPLIT": true}

func (t *translator) classify(src *sNode) source {
	var out source
	if src.T == sNodeCall {
		name := src.Str
		if name == "FILTER" {
			binderName, body := aggShape(src)
			inner := t.classify(src.Kids[0])
			inner.Filters = append(inner.Filters, sourceFilter{Binder: binderName, Node: body})
			return inner
		}
		if name == "MAP" {
			refuse("E_SQL_UNSUPPORTED",
				"MAP as the thing an aggregate iterates is not translated: unlike FILTER, which only decides whether an element takes part, MAP changes what the element is, so the two binders mean different things and binding both to one element is not enough. See docs/internals/sql-translation.md 7.5",
				src.Pos)
		}
	}
	if src.T == sNodeList {
		for i, kid := range src.Kids {
			out.Elements = append(out.Elements, pair[string, binder]{
				Key: strconv.Itoa(i + 1),
				Val: binderNodeAt(kid, len(t.frames)),
			})
		}
		return out
	}
	if src.T == sNodeCList {
		for i, kid := range src.Kids {
			out.Elements = append(out.Elements, pair[string, binder]{
				Key: src.Keys[i],
				Val: binderNodeAt(kid, len(t.frames)),
			})
		}
		return out
	}
	if src.T == sNodeVar {
		if bound := t.binder(src.Str); bound != nil {
			switch bound.Shape {
			case binderShapeNode:
				if bound.Scoped && bound.Scope <= len(t.frames) {
					saved := t.frames
					t.frames = t.frames[:bound.Scope:bound.Scope]
					defer func() { t.frames = saved }()
				}
				return t.classify(bound.Node)
			case binderShapeNone:
				refuse("E_SQL_SHAPE", bound.Reason, src.Pos)
			case binderShapeGroup:
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("%s is the list of a bucket's members, over which only COUNT and SUM are translated", src.Str),
					src.Pos)
			case binderShapeProjected:
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("%s is the record the projection built, a map with one child per field; SQL has no way to iterate or count that", src.Str),
					src.Pos)
			case binderShapeRow:
				if len(bound.Relation.Fields) > 1 {
					refuse("E_SQL_SHAPE",
						fmt.Sprintf("%s is a row of a multi-field relation, which is a map with one child per field; SQL has no way to iterate or count that", src.Str),
						src.Pos)
				}
			case binderShapeColumn, binderShapeKey:
				// scalar rule below
			}
			out.Elements = append(out.Elements, pair[string, binder]{Key: "1", Val: *bound})
			out.ScalarRule = true
			return out
		}
		b := t.bindings.Get(src.Str, src.Pos)
		if b.kind == bindingKindRelation {
			out.Shape = sourceShapeRelation
			out.Relation = &b.relation
			return out
		}
		if b.kind == bindingKindColumns {
			out.Shape = sourceShapeColumns
			for i, col := range b.columns {
				out.Elements = append(out.Elements, pair[string, binder]{
					Key: strconv.Itoa(i + 1),
					Val: binderColumn(col),
				})
			}
			return out
		}
		if b.kind == bindingKindValue {
			v := b.val
			out.Elements = t.valueElements(b, src.Pos)
			out.ScalarRule = v.Size() == 0 && !v.IsNone()
			return out
		}
	}
	if src.T == sNodeCall && (yieldsListSet[src.Str] || src.Str == "LIST" || src.Str == "RECORD" || isPipelineOp(src.Str)) {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s yields a list, and the scalar rule does not apply to it; SQL has no way to count or index what it produces", src.Str),
			src.Pos)
	}
	if src.T == sNodeCall {
		t.node(src)
	}
	out.Elements = append(out.Elements, pair[string, binder]{Key: "1", Val: binderNodeAt(src, len(t.frames))})
	out.ScalarRule = true
	return out
}

func (t *translator) withElement(src source, binderName string, elem binder, key string, n *sNode, render func() *Fragment) *Fragment {
	var frame frame
	frameSet(&frame, binderName, elem)
	frameSet(&frame, "_K", binderNode(leaf(litNode(sel.NodeText, key, false, n.Pos))))
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *translator) withRow(src source, binderName string, render func() *Fragment) *Fragment {
	alias := relationAlias(src.Relation)
	for _, frame := range t.frames {
		for _, kv := range frame {
			if kv.Val.Shape == binderShapeRow && relationAlias(kv.Val.Relation) == alias {
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("this relation is already open as %s further out, and a subquery reusing its own alias shadows the outer row rather than comparing against it; the correlation names the alias, so it cannot be renamed here", alias),
					Pos{})
			}
		}
	}
	row := binderRow(src.Relation)
	if t.statementPlan != nil && t.statementPlan.SourceRelation != nil && sameRelation(*src.Relation, t.statementPlan.SourceRelation.relation) {
		row.Model = buildJoinRows(t.statementPlan).Row
	}
	var frame frame
	frameSet(&frame, binderName, row)
	frameSet(&frame, "_K", binderNone("a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does"))
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *translator) withGroup(src source, binderName string, render func() *Fragment) *Fragment {
	var frame frame
	frameSet(&frame, binderName, binderGroup(src.Relation))
	if t.statementPlan != nil && t.statementPlan.GroupBy != nil && len(t.statementPlan.GroupBy) == 1 {
		gb := t.statementPlan.GroupBy[0]
		frameSet(&frame, "_K", binderKey(gb.Binder, gb.Node, src.Relation))
	} else {
		frameSet(&frame, "_K", binderNone("the key of a bucket over several keys is a list, which SQL has no value for; name one key"))
	}
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *translator) withProjected(src source, binderName string, render func() *Fragment) *Fragment {
	var projections []relationalProjection
	if t.statementPlan != nil && t.statementPlan.Projections != nil {
		projections = t.statementPlan.Projections
	}
	var frame frame
	frameSet(&frame, binderName, binderProjected(src.Relation, projections))
	frameSet(&frame, "_K", binderNone("after a projection the rows are a list renumbered from \"1\", and SQL has no row position to compare against"))
	t.frames = append(t.frames, frame)
	defer func() {
		t.frames = t.frames[:len(t.frames)-1]
	}()
	return render()
}

func (t *translator) withJoinBinders(plan *relationalPlan, join relationalJoin, render func() *Fragment) *Fragment {
	jr := buildJoinRows(plan)
	// `join` arrives by value, so its address is never one of plan.Joins': the join is
	// found by what names it (one table alias per occurrence, so this is unique).
	joinIdx := -1
	for i := range plan.Joins {
		if plan.Joins[i].SourceName == join.SourceName && plan.Joins[i].SourceTable == join.SourceTable && plan.Joins[i].SourceAlias == join.SourceAlias {
			joinIdx = i
			break
		}
	}
	step := jr.Steps[joinIdx]
	var frame frame
	left := binderRow(&plan.SourceRelation.relation)
	left.Model = step.Left
	right := binderRow(&join.SourceRelation.relation)
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
	return utf8.AsciiLower(name)
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

// filterPredicate renders a FILTER's predicate in its own scope: the enclosing
// scope plus the FILTER's binder for the element, and nothing of the aggregate
// it feeds. Every binder is local to the expression it is written for; folding
// them all into one frame let a predicate read the body's binder, and the body
// read the predicate's.
func (t *translator) filterPredicate(f sourceFilter, binderName string) *Fragment {
	top := t.frames[len(t.frames)-1]
	var frame frame
	for _, kv := range top {
		if kv.Key == binderName {
			frameSet(&frame, f.Binder, kv.Val)
		}
	}
	for _, kv := range top {
		if kv.Key == "_K" {
			frameSet(&frame, "_K", kv.Val)
		}
	}
	saved := t.frames
	n := len(saved) - 1
	t.frames = append(saved[:n:n], frame)
	defer func() { t.frames = saved }()
	return t.node(f.Node)
}

func (t *translator) aggBody(name string, binderName string, body *sNode, src source, n *sNode) *Fragment {
	q := t.node(body)
	if name == "SUM" {
		t.requireNum(q, body.Pos, name)
		// A body nobody vouched for is checked like any operand of `+` when the
		// sum is an unroll. Over a relation the whole sum is guarded instead, all
		// or nothing (sql-kinds.md §5a), by relationAggregate.
		if q.Kind == KindUnknown && src.Shape != sourceShapeRelation {
			q = t.guardNumeric(q, body)
		}
	} else {
		t.requireBool(q, body.Pos, name)
	}

	for _, f := range src.Filters {
		p := t.requireBool(t.filterPredicate(f, binderName), f.Node.Pos, "FILTER")
		if name == "SUM" {
			q = t.caseWhen(p, q, t.literal(sel.NewText("0"), KindNum), n.Pos)
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

// numericTestAndCast is the pair the numeric guard is made of, for a body that
// has to be tested once for the whole aggregate and cast once per element.
func (t *translator) numericTestAndCast(body *Fragment, pos Pos) (*Fragment, *Fragment) {
	checkNumericGuard(t.dialect)
	test := t.apply("funcs", "ISNUM", []*Fragment{body}, pos, nil)
	tpl, ok := t.emit.Lex("numericCast").(string)
	if !ok || tpl == "" {
		refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s has no numeric cast", t.dialect), pos)
	}
	cast := NewFragment(t.emit.Fill(tpl, []*Fragment{body}, pos, nil), KindNum, t.dialect, body.Params, body.ParamKinds, body.Caveats)
	return test, cast
}

// allOrNothingSum is the SUM of a body nobody vouched for (sql-kinds.md §5a),
// the dialect's guardedSum skeleton (sql/MAP.md §5.1):
//
//	CASE WHEN COUNT(*) = COUNT(CASE WHEN <test> THEN 1 END)
//	     THEN COALESCE(SUM(<cast>), 0) ELSE NULL END
//
// SUM skips NULL, and COALESCE turns an empty sum into 0, so guarding each element
// would make a refused element vanish; one that fails the test, or is NULL, makes
// the whole value NULL, where SEL raises. Refused where the dialect cannot test
// (at pos, the body), or does not spell the skeleton (at sumPos, the SUM). The
// skeleton says whether the cast inside SUM needs a guard of its own.
func (t *translator) allOrNothingSum(body *Fragment, pos, sumPos Pos) *Fragment {
	test, cast := t.numericTestAndCast(body, pos)
	parts := t.fillNamed(t.skeleton("guardedSum", sumPos), slotMap{
		pair[string, []slot]{Key: "test", Val: []slot{fragmentSlot(test)}},
		pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(cast)}},
	}, sumPos)
	var params []*sel.Value
	var kinds []SqlKind
	params = append(params, test.Params...)
	params = append(params, cast.Params...)
	kinds = append(kinds, test.ParamKinds...)
	kinds = append(kinds, cast.ParamKinds...)
	return NewFragment(parts, KindNum, t.dialect, params, kinds, test.Caveats)
}

func (t *translator) relationAggregate(name string, rel relationSpec, body *Fragment, n *sNode) *Fragment {
	if name == "SUM" && body.Kind == KindUnknown {
		whole := t.allOrNothingSum(body, n.Pos, n.Pos)
		skel := t.skeleton("sum", n.Pos)
		const plain = "COALESCE(SUM({body}), 0)"
		if !strings.Contains(skel, plain) {
			refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s spells SUM in a way the all-or-nothing guard cannot be written into", t.dialect), n.Pos)
		}
		skel = strings.Replace(skel, plain, "{body}", 1)
		slots := mergeSlots(
			t.relationSlots(rel),
			slotMap{pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(whole)}}},
		)
		return NewFragment(t.fillNamed(skel, slots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
	}
	isSeparate := (rel.Prefilter == "separate") || (rel.Prefilter == "" && body.SeparatePrefilter)
	if name == "ANY" && body.Prefilter != nil && isSeparate {
		preSlots := mergeSlots(
			t.relationSlots(rel),
			slotMap{pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(body.Prefilter)}}},
		)
		pre := NewFragment(t.fillNamed(t.skeleton("prefilter", n.Pos), preSlots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
		mainSlots := mergeSlots(
			t.relationSlots(rel),
			slotMap{pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(body)}}},
		)
		main := NewFragment(t.fillNamed(t.skeleton(aggSkeleton(name), n.Pos), mainSlots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
		return t.apply("ops", "AND", []*Fragment{pre, main}, n.Pos, nil)
	}
	slots := mergeSlots(
		t.relationSlots(rel),
		slotMap{pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(body)}}},
	)
	return NewFragment(t.fillNamed(t.skeleton(aggSkeleton(name), n.Pos), slots, n.Pos), aggReturns(name), t.dialect, nil, nil, nil)
}

func (t *translator) aggregate(n *sNode) *Fragment {
	name := n.Str
	if name == "MAP" || name == "FILTER" {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s yields a list, and a SQL expression is a scalar; it can only be the thing another aggregate iterates", name),
			n.Pos)
	}
	if name == "JOIN" {
		return t.joinAggregate(n)
	}

	binderName, bodyNode := aggShape(n)
	src := t.classify(n.Kids[0])

	if src.Shape == sourceShapeRelation {
		rendered := t.withRow(src, binderName, func() *Fragment {
			return t.aggBody(name, binderName, bodyNode, src, n)
		})
		return t.relationAggregate(name, *src.Relation, rendered, n)
	}

	var parts []*Fragment
	for _, kv := range src.Elements {
		key := kv.Key
		elem := kv.Val
		parts = append(parts, t.withElement(src, binderName, elem, key, n, func() *Fragment {
			return t.aggBody(name, binderName, bodyNode, src, n)
		}))
	}

	if len(parts) == 0 {
		if name == "ALL" {
			return t.literal(sel.NewBool(true), KindBool)
		}
		if name == "ANY" {
			return t.literal(sel.NewBool(false), KindBool)
		}
		return t.literal(sel.NewText("0"), KindNum)
	}
	if len(parts) == 1 {
		return parts[0]
	}
	return t.foldPairwise(aggFold(name), parts, n.Pos)
}

func (t *translator) count(n *sNode) *Fragment {
	src := t.classify(n.Kids[0])

	if src.Shape != sourceShapeRelation && src.ScalarRule && len(src.Filters) == 0 {
		return t.literal(sel.NewText("0"), KindNum)
	}

	if len(src.Filters) > 0 {
		body := leaf(litNode(sel.NodeNum, "1", false, n.Pos))
		if src.Shape == sourceShapeRelation {
			rendered := t.withRow(src, "_", func() *Fragment {
				return t.aggBody("SUM", "_", body, src, n)
			})
			return t.relationAggregate("SUM", *src.Relation, rendered, n)
		}
		var parts []*Fragment
		for _, kv := range src.Elements {
			key := kv.Key
			elem := kv.Val
			parts = append(parts, t.withElement(src, "_", elem, key, n, func() *Fragment {
				return t.aggBody("SUM", "_", body, src, n)
			}))
		}
		if len(parts) == 0 {
			return t.literal(sel.NewText("0"), KindNum)
		}
		if len(parts) == 1 {
			return parts[0]
		}
		return t.foldPairwise("+", parts, n.Pos)
	}

	if src.Shape == sourceShapeRelation {
		return NewFragment(
			t.fillNamed(t.skeleton("count", n.Pos), t.relationSlots(*src.Relation), n.Pos),
			KindNum,
			t.dialect,
			nil, nil, nil,
		)
	}
	return t.literal(sel.NewText(strconv.Itoa(len(src.Elements))), KindNum)
}

func (t *translator) has(n *sNode) *Fragment {
	keyNode := n.Kids[1]
	if keyNode.T != sNodeText && keyNode.T != sNodeNum {
		refuse("E_SQL_SHAPE",
			"HAS needs a constant key here: which column it asks about has to be known before the query runs",
			keyNode.Pos)
	}
	key := keyNode.Str
	src := t.classify(n.Kids[0])
	if len(src.Filters) > 0 {
		refuse("E_SQL_SHAPE",
			"HAS over a FILTER would have to know at translation time which elements the filter kept",
			n.Pos)
	}
	if src.Shape == sourceShapeRelation {
		refuse("E_SQL_SHAPE",
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

// requireJoinText: JOIN takes text, under `&`'s rules (spec §5.2/§7.5); a BOOL or
// a BIN, as an element or as the separator, is E_NOT_TEXT in SEL and refused here
// rather than concatenated as whatever a server spells it.
func (t *translator) requireJoinText(f *Fragment, pos Pos, what string) {
	if f.Kind == KindBool || f.Kind == KindBin {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("JOIN takes text and this %s is a %s; SEL answers E_NOT_TEXT rather than spelling it", what, f.Kind), pos)
	}
}

func (t *translator) joinAggregate(n *sNode) *Fragment {
	src := t.classify(n.Kids[0])
	if len(src.Filters) > 0 {
		refuse("E_SQL_SHAPE",
			"JOIN over a FILTER would have to know which elements the filter kept; FILTER is absorbed by ALL, ANY, SUM and COUNT, and JOIN is not one of them",
			n.Kids[0].Pos)
	}

	if src.Shape == sourceShapeRelation {
		rel := *src.Relation
		var scalar *columnSpec
		if rel.Scalar != "" {
			scalar = rel.Field(utf8.AsciiUpper(rel.Scalar))
		}
		if scalar == nil {
			refuse("E_SQL_SHAPE",
				"JOIN over a relation needs the binding to name a \"scalar\" field",
				n.Pos)
		}
		body := t.columnRef(*scalar)
		t.requireJoinText(body, n.Pos, "element")
		sep := t.node(n.Kids[1])
		t.requireJoinText(sep, n.Kids[1].Pos, "separator")
		skel := t.skeleton("join", n.Pos)
		slots := mergeSlots(
			t.relationSlots(rel),
			slotMap{
				pair[string, []slot]{Key: "body", Val: []slot{fragmentSlot(body)}},
				pair[string, []slot]{Key: "sep", Val: []slot{fragmentSlot(sep)}},
			},
		)
		return NewFragment(t.fillNamed(skel, slots, n.Pos), KindText, t.dialect, nil, nil, nil)
	}

	var parts []*Fragment
	for _, kv := range src.Elements {
		key := kv.Key
		elem := kv.Val
		if len(parts) > 0 {
			sep := t.node(n.Kids[1])
			t.requireJoinText(sep, n.Kids[1].Pos, "separator")
			parts = append(parts, sep)
		}
		held := elem
		piece := t.withElement(src, "_", held, key, n, func() *Fragment {
			return t.fromBinder(&held, n)
		})
		t.requireJoinText(piece, n.Pos, "element")
		parts = append(parts, piece)
	}
	if len(parts) == 0 {
		return t.literal(sel.NewText(""), KindText)
	}
	if len(parts) == 1 {
		return parts[0]
	}
	return t.foldPairwise("&", parts, n.Pos)
}
