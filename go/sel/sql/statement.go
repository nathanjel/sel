package sql

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/internal/vocab"
	"github.com/nathanjel/sel/go/sel"
)

func isPipelineOp(name string) bool {
	return vocab.IsPipelineOp(name)
}

type joinedRowField struct {
	Name  string
	Spec  columnSpec
	Table string
}

func joinedRowFields(plan *relationalPlan) []joinedRowField {
	jr := buildJoinRows(plan)
	var out []joinedRowField
	for _, kv := range jr.Row.Promoted {
		if !kv.Val.Optional {
			out = append(out, joinedRowField{
				Name:  kv.Key,
				Spec:  kv.Val.Spec,
				Table: kv.Val.Table,
			})
		}
	}
	return out
}

func recordFields(node *sNode) []pair[string, *sNode] {
	args := node.Kids // an even count: RECORD's arity rule runs at compile time
	var fields []pair[string, *sNode]
	for i := 0; i < len(args); i += 2 {
		if args[i].T != sNodeText {
			refuse("E_BAD_ARG", "RECORD field names must be string literals", args[i].Pos)
		}
		fields = append(fields, pair[string, *sNode]{Key: args[i].Str, Val: args[i+1]})
	}
	return fields
}

// checkAlias holds a name that came from a SEL text literal (a RECORD key, a
// SELECT_COLS column) to what a binding's own names already meet: not empty, no
// NUL. The SQL layer cannot spell either, so it says so at the literal.
func (t *translator) checkAlias(name string, pos Pos) {
	if name == "" {
		refuse("E_SQL_UNSUPPORTED", "an empty name cannot be a SQL identifier", pos)
	}
	if strings.ContainsRune(name, 0) {
		refuse("E_SQL_UNSUPPORTED", "a name containing a NUL cannot be a SQL identifier; no dialect can quote it", pos)
	}
}

// recordFields is the fields of a RECORD call, with each key held to checkAlias,
// and on PostgreSQL to its 63-byte identifier limit: the server truncates a
// longer alias, so two keys sharing their first 63 bytes would name one column
// and SEL's two record keys would become one.
func (t *translator) recordFields(node *sNode) []pair[string, *sNode] {
	fields := recordFields(node)
	pg := false
	for _, d := range Chain(t.dialect) {
		if d == "postgresql" {
			pg = true
		}
	}
	seen := make(map[string]string)
	for i, f := range fields {
		pos := node.Kids[2*i].Pos
		t.checkAlias(f.Key, pos)
		if pg {
			short := f.Key
			if len(short) > 63 {
				short = short[:63]
			}
			if prev, dup := seen[short]; dup && prev != f.Key {
				refuse("E_SQL_UNSUPPORTED",
					fmt.Sprintf("the RECORD key %q collides with an earlier key after PostgreSQL truncates identifiers to 63 bytes", f.Key), pos)
			}
			seen[short] = f.Key
		}
	}
	return fields
}

// hasSort says whether the rows the plan reads were ordered by a SEL sort, in
// this statement or in a derived table under it.
func hasSort(plan *relationalPlan) bool {
	for p := plan; p != nil; p = p.SourceSubquery {
		if len(p.OrderBy) > 0 {
			return true
		}
	}
	return false
}

// requireOrderSurvives refuses a step that would lean on a sort's ORDER BY
// surviving it. SEL's BUCKET lists its groups in the order the sorted input first
// showed them, and its LINK keeps the left order; a GROUP BY or a join is free to
// return its rows in any order, and text that relies on an inner ORDER BY
// surviving an outer clause is not a translation of a stable sort. The planner
// keeps the sort in SQL and the step in memory, where the order is exact
// (docs/internals/sql-translation.md 12.1, "Order").
func (t *translator) requireOrderSurvives(plan *relationalPlan, step string, pos Pos) {
	if plan.OrderLostByJoin && step != "BUCKET" {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s after a join would cut rows in an order the join does not keep from the sort before it", step), pos)
	}
	if step == "BUCKET" && (len(plan.OrderBy) > 0 || plan.OrderDropped) {
		refuse("E_SQL_SHAPE",
			fmt.Sprintf("%s after a sort would depend on a database keeping the sort's order through it, which SQL does not promise", step), pos)
	}
}

func (t *translator) planNeedsWrapBeforeMap(plan *relationalPlan) bool {
	return plan.Projections != nil || plan.SelectCols != nil ||
		plan.GroupBy != nil || plan.Distinct || plan.Limit != nil || plan.Offset != nil
}

func (t *translator) planHasRowsAbove(plan *relationalPlan) bool {
	return plan.Projections != nil || plan.SelectCols != nil ||
		plan.GroupBy != nil || plan.Distinct || plan.Limit != nil || plan.Offset != nil ||
		len(plan.OrderBy) > 0
}

func (t *translator) outputFieldNames(plan *relationalPlan) []string {
	var names []string
	if plan.Projections != nil {
		for i, projection := range plan.Projections {
			if projection.Alias != nil {
				names = append(names, *projection.Alias)
			} else if projection.Node != nil && projection.Node.T == sNodeIndex &&
				projection.Node.Idx() != nil && projection.Node.Idx().T == sNodeText {
				names = append(names, projection.Node.Idx().Str)
			} else {
				names = append(names, fmt.Sprintf("expr%d", i+1))
			}
		}
	} else if plan.SelectCols != nil {
		names = plan.SelectCols
	} else if len(plan.Joins) > 0 {
		for _, f := range joinedRowFields(plan) {
			names = append(names, f.Name)
		}
	} else if plan.SourceRelation != nil {
		names = append(names, relationFieldNames(&plan.SourceRelation.relation)...)
	}
	var unique []string
	seen := make(map[string]bool)
	for _, name := range names {
		upper := utf8.AsciiUpper(name)
		if !seen[upper] {
			seen[upper] = true
			unique = append(unique, name)
		}
	}
	return unique
}

func (t *translator) outputFieldType(plan *relationalPlan, name string) SqlKind {
	if plan.Projections != nil {
		var projection *relationalProjection
		for i := range plan.Projections {
			p := &plan.Projections[i]
			if p.Alias != nil && *p.Alias == name {
				projection = p
				break
			}
		}
		var n *sNode
		if projection != nil {
			n = projection.Node
		}
		if n == nil || n.T != sNodeIndex || n.Obj() == nil || n.Obj().T != sNodeVar ||
			n.Obj().Str != projection.Binder || n.Idx() == nil || n.Idx().T != sNodeText {
			return KindUnknown
		}
		name = n.Idx().Str
	}
	var match *columnSpec
	if plan.SourceRelation != nil {
		match = plan.SourceRelation.relation.Field(utf8.AsciiUpper(name))
	}
	for _, join := range plan.Joins {
		if join.SourceRelation != nil {
			if field := join.SourceRelation.relation.Field(utf8.AsciiUpper(name)); field != nil {
				if match != nil {
					return KindUnknown
				}
				match = field
			}
		}
	}
	if match != nil && !match.Guard && !match.IsRaw {
		return match.Type
	}
	return KindUnknown
}

func (t *translator) outputCanonKind(plan *relationalPlan, name string) *SqlKind {
	if plan.Projections == nil {
		return nil
	}
	var projection *relationalProjection
	for i := range plan.Projections {
		p := &plan.Projections[i]
		if p.Alias != nil && *p.Alias == name {
			projection = p
			break
		}
	}
	var n *sNode
	if projection != nil {
		n = projection.Node
	}
	if n == nil || n.T != sNodeCall || n.Str != "CANON" {
		return nil
	}
	entry := Entry(t.dialect, "funcs", "CANON")
	if rec, ok := entry.(*EntryRecord); ok && rec.Kind == EntryKindTemplate {
		k := KindFromName(rec.Ret)
		return &k
	}
	return nil
}

func (t *translator) ensureDerived(plan *relationalPlan, needed bool) *relationalPlan {
	if !needed {
		return plan
	}
	t.subqueryCounter++
	alias := fmt.Sprintf("_sub%d", t.subqueryCounter)
	inner := plan

	derived := newRelationalPlan()
	derived.SourceName = alias
	derived.SourceTable = ""
	derived.SourceAlias = alias
	derived.SourceSubquery = inner
	derived.OrderDropped = inner.OrderDropped ||
		(len(inner.OrderBy) > 0 && inner.Limit == nil && inner.Offset == nil)

	var rootName *string
	if len(inner.Joins) == 0 {
		rootName = inner.RootName
	}
	derived.RootName = rootName
	if inner.Bucket != bucketNone {
		derived.Bucket = bucketSealed
	}

	var fieldEntries []FieldEntry
	subquery := derived.SourceSubquery
	for _, name := range t.outputFieldNames(subquery) {
		var sourceField *columnSpec
		if subquery.Projections == nil && subquery.SelectCols == nil {
			if subquery.SourceRelation != nil {
				sourceField = subquery.SourceRelation.relation.Field(utf8.AsciiUpper(name))
			}
			for i := 0; sourceField == nil && i < len(subquery.Joins); i++ {
				if subquery.Joins[i].SourceRelation != nil {
					sourceField = subquery.Joins[i].SourceRelation.relation.Field(utf8.AsciiUpper(name))
				}
			}
		}
		colName := name
		if sourceField != nil && sourceField.Column != "" {
			colName = sourceField.Column
		}
		colType := t.outputFieldType(subquery, name)
		canonical := false
		if canonKind := t.outputCanonKind(subquery, name); canonKind != nil {
			colType = *canonKind
			canonical = true
		}
		b := ColumnBinding(colName, alias, colType, false, false, false, "", "", false)
		b.column.Canonical = canonical
		if sourceField != nil && sourceField.IsRaw {
			b.column.Unavailable = fmt.Sprintf("%s is a raw expression of the relation, and the derived table built by an earlier step does not carry it", name)
		}
		fieldEntries = append(fieldEntries, FieldEntry{Name: name, Binding: b})
	}

	derived.SourceRelation = RelationBinding(alias, alias, fieldEntries, "", "", "", false)
	return derived
}

func (t *translator) bucketProjection(plan *relationalPlan, binder string, aggNode *sNode) {
	if aggNode != nil {
		if aggNode.T == sNodeCall && aggNode.Str == "RECORD" {
			var projections []relationalProjection
			for _, kv := range t.recordFields(aggNode) {
				actualNode := kv.Val
				nodeBinder := binder
				var groupKey *relationalGroup
				if kv.Val.T == sNodeVar && kv.Val.Str == "_K" && plan.GroupBy != nil && len(plan.GroupBy) == 1 {
					actualNode = plan.GroupBy[0].Node
					nodeBinder = plan.GroupBy[0].Binder
					gk := plan.GroupBy[0]
					groupKey = &gk
				}
				aliasCopy := kv.Key
				projections = append(projections, relationalProjection{
					Alias:    &aliasCopy,
					Binder:   nodeBinder,
					Node:     actualNode,
					GroupKey: groupKey,
				})
			}
			plan.Projections = projections
		} else {
			plan.Projections = []relationalProjection{
				{
					Alias:  nil,
					Binder: binder,
					Node:   aggNode,
				},
			}
		}
	} else {
		var projections []relationalProjection
		for i := range plan.GroupBy {
			gb := plan.GroupBy[i]
			projections = append(projections, relationalProjection{
				Alias:    gb.Alias,
				Binder:   gb.Binder,
				Node:     gb.Node,
				GroupKey: &gb,
			})
		}
		plan.Projections = projections
	}
	plan.SelectCols = nil
}

func (t *translator) AnalyzePipeline(ast *sNode) *relationalPlan {
	if identityLossBeforeGrouping(ast, nil) {
		refuse("E_SQL_SHAPE", "grouping depends on a computed projection without identity preservation", ast.Pos)
	}
	var steps []*sNode
	curr := ast

	for curr != nil && curr.T == sNodeCall && isPipelineOp(curr.Str) && len(curr.Kids) > 0 {
		steps = append(steps, curr)
		curr = curr.Kids[0]
	}

	if curr == nil || curr.T != sNodeVar {
		return nil
	}

	if !t.bindings.Has(curr.Str) {
		return nil
	}

	b := t.bindings.Get(curr.Str, curr.Pos)
	if b.kind != bindingKindRelation {
		return nil
	}

	plan := newRelationalPlan()
	plan.SourceName = curr.Str
	root := curr.Str
	plan.RootName = &root
	plan.SourceRelation = b
	plan.SourceFromRaw = b.relation.From.IsRaw
	if b.relation.From.IsRaw {
		plan.SourceTable = b.relation.From.Raw
	} else {
		plan.SourceTable = b.relation.From.Table
	}
	plan.SourceAlias = b.relation.Alias
	plan.Correlate = b.relation.Correlate

	// reverse steps to process from root to tip
	for i, j := 0, len(steps)-1; i < j; i, j = i+1, j-1 {
		steps[i], steps[j] = steps[j], steps[i]
	}

	for _, step := range steps {
		name := step.Str
		args := step.Kids

		overGroups := plan.Bucket == bucketOpen
		if plan.Bucket == bucketOpen && name != "FILTER" && name != "MAP" {
			plan.Bucket = bucketSealed
		}

		switch name {
		case "FILTER":
			if plan.Bucket == bucketSealed {
				refuse("E_SQL_SHAPE",
					"a FILTER over buckets must follow the BUCKET directly: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			needDerived := plan.Limit != nil || plan.Offset != nil ||
				(plan.GroupBy == nil && (plan.Projections != nil || plan.SelectCols != nil || plan.Distinct))
			// (A sort does NOT force the wrap: the WHERE goes in the same SELECT, beside the
			// ORDER BY, because a derived table does not keep an ORDER BY that has no LIMIT
			// beside it and the rows would come back in no order; a filter commutes with a
			// stable sort, so the rows and their order are the same.)
			plan = t.ensureDerived(plan, needDerived)

			binder := "_"
			var pred *sNode
			if len(args) == 2 {
				binder = "_"
				pred = args[1]
			} else { // 3: the parser enforced FILTER's arity
				if !isBinderName(args[1]) {
					refuse("E_SQL_SHAPE", "the binder of FILTER must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				pred = args[2]
			}

			if plan.GroupBy != nil {
				plan.Having = append(plan.Having, relationalFilter{
					Binder:     binder,
					Node:       pred,
					Pos:        step.Pos,
					OverGroups: overGroups,
				})
			} else {
				plan.Filters = append(plan.Filters, relationalFilter{
					Binder: binder,
					Node:   pred,
					Pos:    step.Pos,
				})
			}

		case "BUCKET":
			if plan.Bucket != bucketNone {
				refuse("E_SQL_SHAPE",
					"a BUCKET over buckets: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			t.requireOrderSurvives(plan, "BUCKET", step.Pos)
			needDerived := t.planHasRowsAbove(plan)
			plan = t.ensureDerived(plan, needDerived)

			binder := "_"
			var keyNode *sNode
			var aggNode *sNode
			if len(args) == 2 {
				binder = "_"
				keyNode = args[1]
			} else if len(args) == 3 {
				binder = "_"
				keyNode = args[1]
				aggNode = args[2]
			} else { // 4: the parser enforced BUCKET's arity
				if !isBinderName(args[1]) {
					refuse("E_SQL_SHAPE", "the binder of BUCKET must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				keyNode = args[2]
				aggNode = args[3]
			}

			severalKeys := (keyNode.T == sNodeCall && (keyNode.Str == "LIST" || keyNode.Str == "RECORD")) ||
				keyNode.T == sNodeList
			if aggNode == nil && severalKeys {
				refuse("E_SQL_SHAPE",
					"a bare BUCKET groups by one text or number key, as an index does; BUCKET(src, key, proj) groups by several",
					keyNode.Pos)
			}

			var groupBy []relationalGroup
			if (keyNode.T == sNodeCall && keyNode.Str == "LIST") || keyNode.T == sNodeList {
				for _, kArg := range keyNode.Kids {
					groupBy = append(groupBy, relationalGroup{
						Alias:  nil,
						Binder: binder,
						Node:   kArg,
						Pos:    kArg.Pos,
					})
				}
			} else if keyNode.T == sNodeCall && keyNode.Str == "RECORD" {
				for _, kv := range t.recordFields(keyNode) {
					aliasCopy := kv.Key
					groupBy = append(groupBy, relationalGroup{
						Alias:  &aliasCopy,
						Binder: binder,
						Node:   kv.Val,
						Pos:    kv.Val.Pos,
					})
				}
			} else {
				groupBy = append(groupBy, relationalGroup{
					Alias:  nil,
					Binder: binder,
					Node:   keyNode,
					Pos:    keyNode.Pos,
				})
			}

			plan.GroupBy = groupBy
			if aggNode == nil {
				plan.Bucket = bucketOpen
				plan.BareKey = true
			} else {
				plan.Bucket = bucketNone
				plan.BareKey = false
			}
			t.bucketProjection(plan, binder, aggNode)

		case "SELECT_COLS":
			needDerived := t.planNeedsWrapBeforeMap(plan)
			plan = t.ensureDerived(plan, needDerived)

			var items []*sNode
			if len(args) == 2 && args[1].T == sNodeList {
				items = args[1].Kids
			} else {
				items = args[1:]
			}

			var cols []string
			for _, item := range items {
				if item.T != sNodeText {
					refuse("E_BAD_ARG", "SELECT_COLS column names must be string literals", item.Pos)
				}
				col := item.Str
				t.checkAlias(col, item.Pos)
				uc := utf8.AsciiUpper(col)
				matches := 0
				if plan.SourceRelation != nil && plan.SourceRelation.relation.Field(uc) != nil {
					matches++
				}
				for _, join := range plan.Joins {
					if join.SourceRelation != nil && join.SourceRelation.relation.Field(uc) != nil {
						matches++
					}
				}
				if matches > 1 {
					refuse("E_SQL_SHAPE",
						fmt.Sprintf("column '%s' is ambiguous across joined tables; qualify with a table alias", col),
						item.Pos)
				}
				if plan.SourceRelation != nil && len(plan.SourceRelation.relation.Fields) > 0 && matches == 0 {
					var declared []string
					for k := range plan.SourceRelation.relation.Fields {
						declared = append(declared, k)
					}
					sort.Strings(declared)
					refuse("E_SQL_SHAPE",
						fmt.Sprintf("relation %s has no field '%s'; the relation declares %s",
							plan.SourceName, col, strings.Join(declared, ", ")),
						item.Pos)
				}
				if plan.SourceRelation != nil {
					if f := plan.SourceRelation.relation.Field(uc); f != nil && f.IsRaw {
						refuse("E_SQL_SHAPE",
							fmt.Sprintf("column '%s' is a raw expression of the relation, which has no column of that name to select", col),
							item.Pos)
					}
				}
				cols = append(cols, col)
			}
			plan.SelectCols = cols
			plan.Projections = nil

		case "MAP":
			if plan.Bucket == bucketSealed {
				refuse("E_SQL_SHAPE",
					"a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			binder := "_"
			var expr *sNode
			if len(args) == 2 {
				binder = "_"
				expr = args[1]
			} else { // 3: the parser enforced MAP's arity
				if !isBinderName(args[1]) {
					refuse("E_SQL_SHAPE", "the binder of MAP must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				expr = args[2]
			}

			if plan.Bucket == bucketOpen {
				plan.Bucket = bucketNone
				t.bucketProjection(plan, binder, expr)
				continue
			}

			needDerived := t.planNeedsWrapBeforeMap(plan)
			plan = t.ensureDerived(plan, needDerived)

			if expr.T == sNodeCall && expr.Str == "RECORD" {
				var projections []relationalProjection
				for _, kv := range t.recordFields(expr) {
					aliasCopy := kv.Key
					projections = append(projections, relationalProjection{
						Alias:  &aliasCopy,
						Binder: binder,
						Node:   kv.Val,
					})
				}
				plan.Projections = projections
			} else {
				plan.Projections = []relationalProjection{
					{
						Alias:  nil,
						Binder: binder,
						Node:   expr,
					},
				}
			}
			plan.SelectCols = nil

		case "DISTINCT", "DEDUPE":
			needDerived := plan.Limit != nil || plan.Offset != nil
			plan = t.ensureDerived(plan, needDerived)
			if plan.Projections == nil && plan.SelectCols == nil {
				refuse("E_SQL_SHAPE", "DISTINCT requires an explicit typed projection", step.Pos)
			}
			// DISTINCT keeps the FIRST element of each run in sorted order; SQL's `SELECT DISTINCT proj ... ORDER BY <column not in proj>` is refused by PostgreSQL (42P10) and MySQL 8 (3065) and answers with an unspecified representative row on MariaDB. A loud refusal is acceptable and a silent misordering is not, so the step stays in memory.
			if len(plan.OrderBy) > 0 {
				refuse("E_SQL_SHAPE", "DISTINCT after a sort keeps the first of each run in sorted order, which SELECT DISTINCT ... ORDER BY does not promise; run the DISTINCT in memory", step.Pos)
			}
			plan.Distinct = true

		case "TAKE":
			t.requireOrderSurvives(plan, "TAKE", step.Pos)
			lim := t.evalIntParam(args[1], "TAKE")
			if plan.Limit == nil {
				plan.Limit = &lim
			} else if lim < *plan.Limit {
				plan.Limit = &lim
			}

		case "DROP":
			t.requireOrderSurvives(plan, "DROP", step.Pos)
			off := t.evalIntParam(args[1], "DROP")
			skipped := off
			if plan.Limit != nil && *plan.Limit < skipped {
				skipped = *plan.Limit
			}
			currOff := int64(0)
			if plan.Offset != nil {
				currOff = *plan.Offset
			}
			// Merged exactly, and clamped like a single count: two offsets that
			// add past what a server takes are an offset past every row.
			if plan.Limit != nil {
				*plan.Limit -= skipped
			}
			newOff := currOff
			if skipped > math.MaxInt64-currOff {
				newOff = math.MaxInt64
			} else {
				newOff = currOff + skipped
			}
			plan.Offset = &newOff

		case "SORT", "SORT_DESC", "SORT_BY", "TOP", "TOP_DESC", "TOP_BY":
			// Sorts are stable, so an earlier sort is the later one's tie-break; a derived
			// table with no LIMIT beside its ORDER BY does not keep it, and its keys may
			// not even be columns the outer level can name.
			wraps := plan.Limit != nil || plan.Offset != nil ||
				(plan.GroupBy == nil && (plan.Projections != nil || plan.SelectCols != nil || plan.Distinct))
			if (wraps && len(plan.OrderBy) > 0 && plan.Limit == nil && plan.Offset == nil) || plan.OrderDropped {
				refuse("E_SQL_SHAPE", "a sort over a projection of sorted rows loses the earlier sort, which is its tie-break: a derived table does not keep an ORDER BY", step.Pos)
			}
			needDerived := plan.Limit != nil || plan.Offset != nil ||
				(plan.GroupBy == nil && (plan.Projections != nil || plan.SelectCols != nil || plan.Distinct))
			plan = t.ensureDerived(plan, needDerived)
			before := len(plan.OrderBy)
			t.analyzeSortStep(step, plan)
			added := plan.OrderBy[before:]
			for i := range added {
				added[i].OverGroups = overGroups
			}
			plan.OrderBy = append(added, plan.OrderBy[:before]...)

		case "LINK", "LINK_LEFT":
			if len(plan.OrderBy) > 0 || plan.Projections != nil || plan.SelectCols != nil || plan.GroupBy != nil {
				// Compiled only to see whether the prefix can be spelled; the
				// fragment is discarded, and so must be the parameter slots its
				// literals took, or they stay in `params` with nowhere to go.
				nParams := len(t.params)
				_ = t.CompileStatement(plan)
				t.params = t.params[:nParams]
				t.paramKinds = t.paramKinds[:nParams]
			}
			// A join returns its rows in no order, and SEL's are the left list's: rows
			// sorted with no LIMIT beside the ORDER BY (which a derived table drops)
			// cannot pass through a JOIN carrying their sort. After the earlier steps'
			// own refusals, which come first as written.
			if plan.OrderDropped || (len(plan.OrderBy) > 0 && plan.Limit == nil && plan.Offset == nil) {
				refuse("E_SQL_SHAPE",
					fmt.Sprintf("a %s over sorted rows would return them in no order, where SEL has the left list's order", name),
					step.Pos)
			}
			needDerived := t.planHasRowsAbove(plan)
			plan = t.ensureDerived(plan, needDerived)
			if plan.SourceSubquery != nil && hasSort(plan.SourceSubquery) {
				// The sort sits in a derived table under the join, and a join does
				// not keep its left input's order: whatever cuts the rows next
				// would cut different ones.
				plan.OrderLostByJoin = true
			}

			rightNode := args[1]
			if rightNode.T != sNodeVar || !t.bindings.Has(rightNode.Str) {
				refuse("E_SQL_SHAPE", fmt.Sprintf("%s requires a bound relation as its right side", name), rightNode.Pos)
			}
			rightBinding := t.bindings.Get(rightNode.Str, rightNode.Pos)
			if rightBinding.kind != bindingKindRelation {
				refuse("E_SQL_SHAPE", fmt.Sprintf("%s is not bound as a relation", rightNode.Str), rightNode.Pos)
			}

			joinType := "INNER"
			if name == "LINK_LEFT" {
				joinType = "LEFT"
			}
			fromRaw := rightBinding.relation.From.IsRaw
			table := rightBinding.relation.From.Table
			if fromRaw {
				table = rightBinding.relation.From.Raw
			}

			join := relationalJoin{
				Type:           joinType,
				SourceName:     rightNode.Str,
				SourceRelation: rightBinding,
				SourceFromRaw:  fromRaw,
				SourceTable:    table,
				SourceAlias:    rightBinding.relation.Alias,
				Pos:            step.Pos,
			}

			if len(args) == 5 {
				if !isBinderName(args[2]) || !isBinderName(args[3]) {
					refuse("E_SQL_SHAPE", "join binders must be bare names", args[2].Pos)
				}
				join.LeftNames = []string{args[2].Str}
				join.RightNames = []string{args[3].Str}
				join.OnPred = args[4]
			} else {
				if len(plan.Joins) == 0 && plan.RootName != nil {
					join.LeftNames = []string{*plan.RootName}
				}
				join.RightNames = []string{rightNode.Str}
				join.OnPred = args[2]
			}

			if join.SourceAlias == "" {
				if len(args) == 5 {
					join.SourceAlias = join.RightNames[0]
				} else {
					join.SourceAlias = "_2"
				}
			}

			openAliases := []string{plan.SourceAlias}
			if openAliases[0] == "" && plan.SourceRelation != nil {
				openAliases[0] = relationAlias(&plan.SourceRelation.relation)
			}
			for _, j := range plan.Joins {
				if j.SourceAlias != "" {
					openAliases = append(openAliases, j.SourceAlias)
				}
			}
			mine := utf8.AsciiUpper(join.SourceAlias)
			for _, a := range openAliases {
				if utf8.AsciiUpper(a) == mine {
					refuse("E_SQL_SHAPE",
						fmt.Sprintf("%s would be joined under the table alias %s, which this statement already uses; bind the relation a second time under another alias", rightNode.Str, join.SourceAlias),
						rightNode.Pos)
				}
			}

			plan.Joins = append(plan.Joins, join)
		}
	}

	// A derived table with no LIMIT beside its ORDER BY does not keep the order, and this
	// statement has no other ORDER BY: the rows would come back in no order, where SEL's
	// are the sorted list's. Refused at the last step (sql-translation 12.1: a sort's
	// ORDER BY survives every step after it, or the plan is not SQL).
	if plan.OrderDropped && len(steps) > 0 {
		refuse("E_SQL_SHAPE",
			"these rows come from a sorted derived table, which does not keep its order, and nothing after it sorts them again",
			steps[len(steps)-1].Pos)
	}
	return plan
}

func (t *translator) evalIntParam(n *sNode, op string) int64 {
	node := n.ToNode()
	if node == nil {
		refuse("E_SQL_SHAPE", fmt.Sprintf("%s count cannot contain dynamic lists", op), n.Pos)
	}
	prog := sel.NewProgram("", node)
	root := t.constRoot
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.Run(root)
	if err != nil {
		refuseAsSel(err, n)
	}
	if !val.LooksNumeric() || val.IsNull() {
		refuse("E_NOT_NUM", fmt.Sprintf("%s count must be a number", op), n.Pos)
	}
	text := val.AsText(n.Pos)
	neg := strings.HasPrefix(text, "-")
	if neg {
		text = text[1:]
	}
	// A whole number written with a scale is that number (2.0, 0.0): only a
	// fractional count is not an integer.
	whole, frac := text, ""
	if dot := strings.IndexByte(text, '.'); dot >= 0 {
		whole, frac = text[:dot], text[dot+1:]
	}
	if strings.Trim(frac, "0") != "" {
		refuse("E_NOT_INT", fmt.Sprintf("%s count must be an integer", op), n.Pos)
	}
	whole = strings.TrimLeft(whole, "0")
	if neg && whole != "" {
		refuse("E_RANGE", fmt.Sprintf("%s count cannot be negative", op), n.Pos)
	}
	// Past what the servers take, a count is all of a list (TAKE) or none of it
	// (DROP) in SEL: clamped, on every dialect, to what all of them accept
	// (docs/internals/sql-translation.md 11.6).
	const maxCount = "9223372036854775807"
	if len(whole) > len(maxCount) || (len(whole) == len(maxCount) && whole > maxCount) {
		return math.MaxInt64
	}
	if whole == "" {
		return 0
	}
	num, err := strconv.ParseInt(whole, 10, 64)
	if err != nil {
		refuse("E_RANGE", fmt.Sprintf("%s count is out of range", op), n.Pos)
	}
	return num
}

func (t *translator) analyzeSortStep(step *sNode, plan *relationalPlan) {
	name := step.Str
	args := step.Kids
	top := name == "TOP" || name == "TOP_DESC" || name == "TOP_BY"
	count := len(args)
	if top {
		count = len(args) - 1
	}

	if top {
		lim := t.evalIntParam(args[len(args)-1], name)
		if plan.Limit == nil {
			plan.Limit = &lim
		} else if lim < *plan.Limit {
			plan.Limit = &lim
		}
	}

	if name == "SORT" || name == "SORT_DESC" || name == "TOP" || name == "TOP_DESC" {
		dir := "ASC"
		if name == "SORT_DESC" || name == "TOP_DESC" {
			dir = "DESC"
		}
		if count == 1 {
			if plan.SourceRelation != nil && plan.SourceRelation.relation.Scalar != "" {
				scalarCol := plan.SourceRelation.relation.Scalar
				varNode := leaf(&sel.Node{T: sel.NodeVar, S: "_", Pos: step.Pos})
				idxNode := leaf(&sel.Node{T: sel.NodeText, S: scalarCol, Pos: step.Pos})
				indexNode := rewritten(&sel.Node{T: sel.NodeIndex, Pos: step.Pos}, []*sNode{varNode, idxNode})
				plan.OrderBy = append(plan.OrderBy, relationalOrder{
					Binder: "_",
					Node:   indexNode,
					Dir:    dir,
					Pos:    step.Pos,
				})
				return
			}
			if plan.SourceRelation != nil && len(plan.SourceRelation.relation.Fields) == 1 {
				var fieldName string
				for k := range plan.SourceRelation.relation.Fields {
					fieldName = k
					break
				}
				varNode := leaf(&sel.Node{T: sel.NodeVar, S: "_", Pos: step.Pos})
				idxNode := leaf(&sel.Node{T: sel.NodeText, S: fieldName, Pos: step.Pos})
				indexNode := rewritten(&sel.Node{T: sel.NodeIndex, Pos: step.Pos}, []*sNode{varNode, idxNode})
				plan.OrderBy = append(plan.OrderBy, relationalOrder{
					Binder: "_",
					Node:   indexNode,
					Dir:    dir,
					Pos:    step.Pos,
				})
				return
			}
			refuse("E_SQL_SHAPE", "SORT on a multi-field relation requires a key expression; use SORT_BY", step.Pos)
		} else if count == 2 {
			plan.OrderBy = append(plan.OrderBy, relationalOrder{
				Binder: "_",
				Node:   args[1],
				Dir:    dir,
				Pos:    step.Pos,
			})
		} else { // 3: the parser enforced the arity
			if !isBinderName(args[1]) {
				refuse("E_SQL_SHAPE", fmt.Sprintf("the binder of %s must be a bare name", name), args[1].Pos)
			}
			plan.OrderBy = append(plan.OrderBy, relationalOrder{
				Binder: args[1].Str,
				Node:   args[2],
				Dir:    dir,
				Pos:    step.Pos,
			})
		}
		return
	}

	// SORT_BY or TOP_BY: which argument is which is the manifest's
	// (spec/builtins.json), a text literal last being the direction whatever the
	// slot before it holds.
	roles, _ := manifest.Sort(utf8.AsciiUpper(name), len(args), sNodeShape(args))
	binder := "_"
	key := args[roles.Key]
	dir := "ASC"
	dirPos := step.Pos
	if roles.Binder >= 0 {
		if !isBinderName(args[roles.Binder]) {
			refuse("E_SQL_SHAPE", fmt.Sprintf("the binder of %s must be a bare name", name), args[roles.Binder].Pos)
		}
		binder = args[roles.Binder].Str
	}
	if roles.Dir >= 0 {
		d := args[roles.Dir]
		if d.T != sNodeText {
			refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", d.Pos)
		}
		dir, dirPos = utf8.AsciiUpper(d.Str), d.Pos
	}

	if dir != "ASC" && dir != "DESC" {
		refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", dirPos)
	}

	plan.OrderBy = append(plan.OrderBy, relationalOrder{
		Binder: binder,
		Node:   key,
		Dir:    dir,
		Pos:    step.Pos,
	})
}

func (t *translator) CompileStatement(plan *relationalPlan) *Fragment {
	checkAliasesProj := func(entries []relationalProjection) {
		seen := make(map[string]bool)
		for _, entry := range entries {
			if entry.Alias != nil {
				upper := utf8.AsciiUpper(*entry.Alias)
				if seen[upper] {
					refuse("E_SQL_SHAPE", "duplicate or case-colliding RECORD fields require local evaluation", entry.Node.Pos)
				}
				seen[upper] = true
			}
		}
	}
	checkAliasesGroup := func(entries []relationalGroup) {
		seen := make(map[string]bool)
		for _, entry := range entries {
			if entry.Alias != nil {
				upper := utf8.AsciiUpper(*entry.Alias)
				if seen[upper] {
					refuse("E_SQL_SHAPE", "duplicate or case-colliding RECORD fields require local evaluation", entry.Node.Pos)
				}
				seen[upper] = true
			}
		}
	}

	if plan.Projections != nil {
		checkAliasesProj(plan.Projections)
	}
	if plan.GroupBy != nil {
		checkAliasesGroup(plan.GroupBy)
	}

	prevPlan := t.statementPlan
	t.statementPlan = plan
	defer func() {
		t.statementPlan = prevPlan
	}()

	var parts []Part
	addSql := func(sqlStr string) {
		if sqlStr != "" {
			parts = append(parts, Part{Sql: sqlStr})
		}
	}

	if plan.Distinct {
		addSql("SELECT DISTINCT ")
	} else {
		addSql("SELECT ")
	}

	var srcRel *relationSpec
	if plan.SourceRelation != nil {
		srcRel = &plan.SourceRelation.relation
	}
	src := source{
		Shape:    sourceShapeRelation,
		Relation: srcRel,
	}
	for _, f := range plan.Filters {
		src.Filters = append(src.Filters, sourceFilter{Binder: f.Binder, Node: f.Node})
	}

	// 1. SELECT list (Projections)
	if plan.Projections != nil {
		first := true
		for _, proj := range plan.Projections {
			if !first {
				addSql(", ")
			}
			first = false
			var pFrag *Fragment
			if proj.GroupKey != nil {
				pFrag = t.groupKey(src, *proj.GroupKey, true)
			} else if plan.GroupBy != nil {
				pFrag = t.withGroup(src, proj.Binder, func() *Fragment {
					return t.node(proj.Node)
				})
			} else {
				pFrag = t.withRow(src, proj.Binder, func() *Fragment {
					return t.node(proj.Node)
				})
			}
			if plan.Distinct {
				if (pFrag.Kind == KindUnknown || pFrag.Kind == KindNum) && !pFrag.Canonical {
					refuse("E_SQL_SHAPE", "DISTINCT requires proven structural output identity", proj.Node.Pos)
				}
				pFrag = t.identityGroupKey(proj.Node, pFrag)
			}
			parts = append(parts, pFrag.Parts...)
			if proj.Alias != nil {
				addSql(" AS " + t.emit.Ident(*proj.Alias))
			}
		}
	} else if plan.SelectCols != nil {
		first := true
		for _, col := range plan.SelectCols {
			if !first {
				addSql(", ")
			}
			first = false
			owner := srcRel
			var fSpec *columnSpec
			if srcRel != nil {
				fSpec = srcRel.Field(utf8.AsciiUpper(col))
			}
			for _, join := range plan.Joins {
				if fSpec == nil && join.SourceRelation != nil {
					fSpec = join.SourceRelation.relation.Field(utf8.AsciiUpper(col))
					if fSpec != nil {
						owner = &join.SourceRelation.relation
					}
				}
			}
			column := col
			if fSpec != nil && fSpec.Column != "" {
				column = fSpec.Column
			}
			if plan.Distinct && (fSpec == nil || fSpec.Type == KindUnknown || fSpec.Type == KindNum) {
				refuse("E_SQL_SHAPE", "DISTINCT requires known output kinds", Pos{})
			}
			var sqlStr string
			if len(plan.Joins) == 0 && plan.SourceSubquery == nil {
				table := ""
				if fSpec != nil && fSpec.Table != "" {
					table = fSpec.Table
				} else if plan.SourceAlias != "" {
					table = plan.SourceAlias
				}
				sqlStr = t.emit.Column(table, column)
			} else {
				sqlStr = t.emit.Column(t.relationTableAlias(owner, ""), column)
			}
			if plan.Distinct && fSpec != nil && (fSpec.Type == KindText || fSpec.Type == KindNum) {
				frag := t.emit.TextOperand(NewFragment([]Part{{Sql: sqlStr}}, fSpec.Type, t.dialect, nil, nil, nil))
				parts = append(parts, frag.Parts...)
				addSql(" AS " + t.emit.Ident(column))
			} else {
				addSql(sqlStr)
			}
		}
	} else if len(plan.Joins) > 0 {
		fields := joinedRowFields(plan)
		if len(fields) == 0 {
			last := plan.Joins[len(plan.Joins)-1]
			refuse("E_SQL_SHAPE",
				"the joined row has no field SQL can carry: every field is on both sides, and the binders are nested records",
				last.Pos)
		}
		first := true
		for _, f := range fields {
			if !first {
				addSql(", ")
			}
			first = false
			col := f.Name
			if f.Spec.Column != "" {
				col = f.Spec.Column
			}
			addSql(t.emit.Column(f.Table, col))
		}
	} else {
		if plan.SourceAlias != "" {
			addSql(t.emit.Ident(plan.SourceAlias) + ".*")
		} else {
			addSql("*")
		}
	}

	// 2. FROM clause
	addSql(" FROM ")
	if plan.SourceSubquery != nil {
		subquery := t.CompileStatement(plan.SourceSubquery)
		addSql("(")
		parts = append(parts, subquery.Parts...)
		addSql(")")
		if plan.SourceAlias != "" {
			addSql(" " + t.emit.Ident(plan.SourceAlias))
		}
	} else {
		from := plan.SourceTable
		if !plan.SourceFromRaw {
			from = t.emit.Ident(plan.SourceTable)
		}
		if plan.SourceAlias != "" {
			from += " " + t.emit.Ident(plan.SourceAlias)
		}
		addSql(from)
	}

	// Joins
	for _, join := range plan.Joins {
		if join.Type == "LEFT" {
			addSql(" LEFT JOIN ")
		} else {
			addSql(" INNER JOIN ")
		}
		right := join.SourceTable
		if !join.SourceFromRaw {
			right = t.emit.Ident(join.SourceTable)
		}
		if join.SourceAlias != "" {
			right += " " + t.emit.Ident(join.SourceAlias)
		}
		addSql(right + " ON ")
		on := t.withJoinBinders(plan, join, func() *Fragment {
			return t.requireBool(t.node(join.OnPred), join.Pos, "LINK")
		})
		parts = append(parts, on.Parts...)
	}

	// 3. WHERE clause
	var condParts [][]Part
	if plan.Correlate != "" {
		condParts = append(condParts, []Part{{Sql: "(" + plan.Correlate + ")"}})
	}
	t.inWhere = true
	for _, filter := range plan.Filters {
		cFrag := t.withRow(src, filter.Binder, func() *Fragment {
			return t.requireBool(t.node(filter.Node), filter.Pos, "FILTER")
		})
		condParts = append(condParts, cFrag.Parts)
	}
	t.inWhere = false

	if len(condParts) > 0 {
		addSql(" WHERE ")
		for i, cp := range condParts {
			if i > 0 {
				addSql(" AND ")
			}
			parts = append(parts, cp...)
		}
	}

	// 4. GROUP BY clause
	if len(plan.GroupBy) > 0 {
		addSql(" GROUP BY ")
		first := true
		for _, gb := range plan.GroupBy {
			if !first {
				addSql(", ")
			}
			first = false
			gFrag := t.groupKey(src, gb, false)
			if plan.BareKey && (gFrag.Kind == KindBool || gFrag.Kind == KindBin) {
				refuse("E_SQL_SHAPE",
					"a bare BUCKET groups by one text or number key, as an index does; SEL refuses a boolean or binary key (E_NOT_TEXT)",
					gb.Pos)
			}
			parts = append(parts, gFrag.Parts...)
		}
	}

	// 5. HAVING clause
	if len(plan.Having) > 0 {
		addSql(" HAVING ")
		var hParts [][]Part
		for _, hav := range plan.Having {
			render := func() *Fragment {
				return t.requireBool(t.node(hav.Node), hav.Pos, "FILTER")
			}
			var hFrag *Fragment
			if hav.OverGroups {
				hFrag = t.withGroup(src, hav.Binder, render)
			} else {
				hFrag = t.withProjected(src, hav.Binder, render)
			}
			hParts = append(hParts, hFrag.Parts)
		}
		for i, hp := range hParts {
			if i > 0 {
				addSql(" AND ")
			}
			parts = append(parts, hp...)
		}
	}

	// 6. ORDER BY clause
	if len(plan.OrderBy) > 0 {
		addSql(" ORDER BY ")
		first := true
		for _, ord := range plan.OrderBy {
			if !first {
				addSql(", ")
			}
			first = false
			render := func() *Fragment {
				return t.node(ord.Node)
			}
			var oFrag *Fragment
			if ord.OverGroups {
				oFrag = t.orderKey(t.withGroup(src, ord.Binder, render), ord.Node.Pos)
			} else if plan.GroupBy != nil {
				oFrag = t.orderKey(t.withProjected(src, ord.Binder, render), ord.Node.Pos)
			} else {
				oFrag = t.orderKey(t.withRow(src, ord.Binder, render), ord.Node.Pos)
			}
			parts = append(parts, oFrag.Parts...)
			addSql(" " + ord.Dir)
		}
	}

	// 7. LIMIT / OFFSET clause
	if plan.Limit != nil && plan.Offset != nil {
		addSql(fmt.Sprintf(" LIMIT %d OFFSET %d", *plan.Limit, *plan.Offset))
	} else if plan.Limit != nil {
		addSql(fmt.Sprintf(" LIMIT %d", *plan.Limit))
	} else if plan.Offset != nil {
		ch := Chain(t.dialect)
		hasTarget := func(target string) bool {
			return containsString(ch, target)
		}
		if hasTarget("mariadb") || hasTarget("mysql") || hasTarget("mysql-family") {
			addSql(fmt.Sprintf(" LIMIT 18446744073709551615 OFFSET %d", *plan.Offset))
		} else if hasTarget("sqlite") {
			addSql(fmt.Sprintf(" LIMIT -1 OFFSET %d", *plan.Offset))
		} else {
			addSql(fmt.Sprintf(" OFFSET %d", *plan.Offset))
		}
	}

	out := NewFragment(parts, KindStatement, t.dialect, t.params, t.paramKinds, t.caveats)
	return out
}

// relationFieldNames lists a relation's fields in a fixed order: the order the
// binding declared them in, or sorted names when it declared none. Ranging over
// the Fields map gave a different select list on different runs.
func relationFieldNames(rel *relationSpec) []string {
	if len(rel.FieldOrder) > 0 {
		seen := make(map[string]bool, len(rel.FieldOrder))
		var out []string
		for _, n := range rel.FieldOrder {
			if _, ok := rel.Fields[n]; ok && !seen[n] {
				seen[n] = true
				out = append(out, n)
			}
		}
		if len(out) == len(rel.Fields) {
			return out
		}
		for n := range rel.Fields {
			if !seen[n] {
				out = append(out, n)
			}
		}
		sort.Strings(out[len(seen):])
		return out
	}
	out := make([]string, 0, len(rel.Fields))
	for n := range rel.Fields {
		out = append(out, n)
	}
	sort.Strings(out)
	return out
}
