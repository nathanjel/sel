package sql

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

var pipelineOpsSet = map[string]bool{
	"FILTER": true, "BUCKET": true, "SELECT_COLS": true, "MAP": true,
	"DISTINCT": true, "DEDUPE": true, "TAKE": true, "DROP": true,
	"SORT": true, "SORT_DESC": true, "SORT_BY": true, "TOP": true,
	"TOP_DESC": true, "TOP_BY": true, "LINK": true, "LINK_LEFT": true,
}

func isPipelineOp(name string) bool {
	return pipelineOpsSet[name]
}

type JoinedRowField struct {
	Name  string
	Spec  ColumnSpec
	Table string
}

func joinedRowFields(plan *RelationalPlan) []JoinedRowField {
	jr := BuildJoinRows(plan)
	var out []JoinedRowField
	for _, kv := range jr.Row.Promoted {
		if !kv.Val.Optional {
			out = append(out, JoinedRowField{
				Name:  kv.Key,
				Spec:  kv.Val.Spec,
				Table: kv.Val.Table,
			})
		}
	}
	return out
}

func recordFields(node *SNode) []Pair[string, *SNode] {
	args := node.Kids
	if len(args)%2 != 0 {
		Refuse("E_ARITY", "RECORD takes an even number of arguments", node.Pos)
	}
	var fields []Pair[string, *SNode]
	for i := 0; i < len(args); i += 2 {
		if args[i].T != SNodeText {
			Refuse("E_BAD_ARG", "RECORD field names must be string literals", args[i].Pos)
		}
		fields = append(fields, Pair[string, *SNode]{Key: args[i].Str, Val: args[i+1]})
	}
	return fields
}

func (t *Translator) planNeedsWrapBeforeMap(plan *RelationalPlan) bool {
	return plan.Projections != nil || plan.SelectCols != nil ||
		plan.GroupBy != nil || plan.Distinct || plan.Limit != nil || plan.Offset != nil
}

func (t *Translator) planHasRowsAbove(plan *RelationalPlan) bool {
	return plan.Projections != nil || plan.SelectCols != nil ||
		plan.GroupBy != nil || plan.Distinct || plan.Limit != nil || plan.Offset != nil ||
		len(plan.OrderBy) > 0
}

func (t *Translator) outputFieldNames(plan *RelationalPlan) []string {
	var names []string
	if plan.Projections != nil {
		for i, projection := range plan.Projections {
			if projection.Alias != nil {
				names = append(names, *projection.Alias)
			} else if projection.Node != nil && projection.Node.T == SNodeIndex &&
				projection.Node.Idx() != nil && projection.Node.Idx().T == SNodeText {
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
		for name := range plan.SourceRelation.Relation.Fields {
			names = append(names, name)
		}
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

func (t *Translator) outputFieldType(plan *RelationalPlan, name string) SqlKind {
	if plan.Projections != nil {
		var projection *RelationalProjection
		for i := range plan.Projections {
			p := &plan.Projections[i]
			if p.Alias != nil && *p.Alias == name {
				projection = p
				break
			}
		}
		var n *SNode
		if projection != nil {
			n = projection.Node
		}
		if n == nil || n.T != SNodeIndex || n.Obj() == nil || n.Obj().T != SNodeVar ||
			n.Obj().Str != projection.Binder || n.Idx() == nil || n.Idx().T != SNodeText {
			return KindUnknown
		}
		name = n.Idx().Str
	}
	var match *ColumnSpec
	if plan.SourceRelation != nil {
		match = plan.SourceRelation.Relation.Field(utf8.AsciiUpper(name))
	}
	for _, join := range plan.Joins {
		if join.SourceRelation != nil {
			if field := join.SourceRelation.Relation.Field(utf8.AsciiUpper(name)); field != nil {
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

func (t *Translator) outputCanonKind(plan *RelationalPlan, name string) *SqlKind {
	if plan.Projections == nil {
		return nil
	}
	var projection *RelationalProjection
	for i := range plan.Projections {
		p := &plan.Projections[i]
		if p.Alias != nil && *p.Alias == name {
			projection = p
			break
		}
	}
	var n *SNode
	if projection != nil {
		n = projection.Node
	}
	if n == nil || n.T != SNodeCall || n.Str != "CANON" {
		return nil
	}
	entry := Entry(t.dialect, "funcs", "CANON")
	if rec, ok := entry.(*EntryRecord); ok && rec.Kind == EntryKindTemplate {
		k := KindFromName(rec.Ret)
		return &k
	}
	return nil
}

func (t *Translator) ensureDerived(plan *RelationalPlan, needed bool) *RelationalPlan {
	if !needed {
		return plan
	}
	t.subqueryCounter++
	alias := fmt.Sprintf("_sub%d", t.subqueryCounter)
	inner := plan

	derived := NewRelationalPlan()
	derived.SourceName = alias
	derived.SourceTable = ""
	derived.SourceAlias = alias
	derived.SourceSubquery = inner

	var rootName *string
	if len(inner.Joins) == 0 {
		rootName = inner.RootName
	}
	derived.RootName = rootName
	if inner.Bucket != BucketNone {
		derived.Bucket = BucketSealed
	}

	var fieldEntries []FieldEntry
	subquery := derived.SourceSubquery
	for _, name := range t.outputFieldNames(subquery) {
		var sourceField *ColumnSpec
		if subquery.Projections == nil && subquery.SelectCols == nil {
			if subquery.SourceRelation != nil {
				sourceField = subquery.SourceRelation.Relation.Field(utf8.AsciiUpper(name))
			}
			for i := 0; sourceField == nil && i < len(subquery.Joins); i++ {
				if subquery.Joins[i].SourceRelation != nil {
					sourceField = subquery.Joins[i].SourceRelation.Relation.Field(utf8.AsciiUpper(name))
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
		b.Column.Canonical = canonical
		fieldEntries = append(fieldEntries, FieldEntry{Name: name, Binding: b})
	}

	derived.SourceRelation = RelationBinding(alias, alias, fieldEntries, "", "", "", false)
	return derived
}

func (t *Translator) bucketProjection(plan *RelationalPlan, binder string, aggNode *SNode) {
	if aggNode != nil {
		if aggNode.T == SNodeCall && aggNode.Str == "RECORD" {
			var projections []RelationalProjection
			for _, kv := range recordFields(aggNode) {
				actualNode := kv.Val
				nodeBinder := binder
				var groupKey *RelationalGroup
				if kv.Val.T == SNodeVar && kv.Val.Str == "_K" && plan.GroupBy != nil && len(plan.GroupBy) == 1 {
					actualNode = plan.GroupBy[0].Node
					nodeBinder = plan.GroupBy[0].Binder
					gk := plan.GroupBy[0]
					groupKey = &gk
				}
				aliasCopy := kv.Key
				projections = append(projections, RelationalProjection{
					Alias:    &aliasCopy,
					Binder:   nodeBinder,
					Node:     actualNode,
					GroupKey: groupKey,
				})
			}
			plan.Projections = projections
		} else {
			plan.Projections = []RelationalProjection{
				{
					Alias:  nil,
					Binder: binder,
					Node:   aggNode,
				},
			}
		}
	} else {
		var projections []RelationalProjection
		for i := range plan.GroupBy {
			gb := plan.GroupBy[i]
			projections = append(projections, RelationalProjection{
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

func (t *Translator) AnalyzePipeline(ast *SNode) *RelationalPlan {
	if IdentityLossBeforeGrouping(ast, nil) {
		Refuse("E_SQL_SHAPE", "grouping depends on a computed projection without identity preservation", ast.Pos)
	}
	var steps []*SNode
	curr := ast

	for curr != nil && curr.T == SNodeCall && isPipelineOp(curr.Str) && len(curr.Kids) > 0 {
		steps = append(steps, curr)
		curr = curr.Kids[0]
	}

	if curr == nil || curr.T != SNodeVar {
		return nil
	}

	if !t.bindings.Has(curr.Str) {
		return nil
	}

	b := t.bindings.Get(curr.Str, curr.Pos)
	if b.Kind != BindingKindRelation {
		return nil
	}

	plan := NewRelationalPlan()
	plan.SourceName = curr.Str
	root := curr.Str
	plan.RootName = &root
	plan.SourceRelation = b
	plan.SourceFromRaw = b.Relation.From.IsRaw
	if b.Relation.From.IsRaw {
		plan.SourceTable = b.Relation.From.Raw
	} else {
		plan.SourceTable = b.Relation.From.Table
	}
	plan.SourceAlias = b.Relation.Alias
	plan.Correlate = b.Relation.Correlate

	// reverse steps to process from root to tip
	for i, j := 0, len(steps)-1; i < j; i, j = i+1, j-1 {
		steps[i], steps[j] = steps[j], steps[i]
	}

	for _, step := range steps {
		name := step.Str
		args := step.Kids

		overGroups := plan.Bucket == BucketOpen
		if plan.Bucket == BucketOpen && name != "FILTER" && name != "MAP" {
			plan.Bucket = BucketSealed
		}

		switch name {
		case "FILTER":
			if plan.Bucket == BucketSealed {
				Refuse("E_SQL_SHAPE",
					"a FILTER over buckets must follow the BUCKET directly: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			needDerived := plan.Limit != nil || plan.Offset != nil ||
				(plan.GroupBy == nil && (plan.Projections != nil || plan.SelectCols != nil || plan.Distinct || len(plan.OrderBy) > 0))
			plan = t.ensureDerived(plan, needDerived)

			binder := "_"
			var pred *SNode
			if len(args) == 2 {
				binder = "_"
				pred = args[1]
			} else if len(args) == 3 {
				if !IsBinderName(args[1]) {
					Refuse("E_SQL_SHAPE", "the binder of FILTER must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				pred = args[2]
			} else {
				Refuse("E_ARITY", "FILTER takes 2 or 3 arguments", step.Pos)
			}

			if plan.GroupBy != nil {
				plan.Having = append(plan.Having, RelationalFilter{
					Binder:     binder,
					Node:       pred,
					Pos:        step.Pos,
					OverGroups: overGroups,
				})
			} else {
				plan.Filters = append(plan.Filters, RelationalFilter{
					Binder: binder,
					Node:   pred,
					Pos:    step.Pos,
				})
			}

		case "BUCKET":
			if plan.Bucket != BucketNone {
				Refuse("E_SQL_SHAPE",
					"a BUCKET over buckets: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			needDerived := t.planHasRowsAbove(plan)
			plan = t.ensureDerived(plan, needDerived)

			binder := "_"
			var keyNode *SNode
			var aggNode *SNode
			if len(args) == 2 {
				binder = "_"
				keyNode = args[1]
			} else if len(args) == 3 {
				binder = "_"
				keyNode = args[1]
				aggNode = args[2]
			} else if len(args) == 4 {
				if !IsBinderName(args[1]) {
					Refuse("E_SQL_SHAPE", "the binder of BUCKET must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				keyNode = args[2]
				aggNode = args[3]
			} else {
				Refuse("E_ARITY", "BUCKET takes 2 to 4 arguments", step.Pos)
			}

			severalKeys := (keyNode.T == SNodeCall && (keyNode.Str == "LIST" || keyNode.Str == "RECORD")) ||
				keyNode.T == SNodeList
			if aggNode == nil && severalKeys {
				Refuse("E_SQL_SHAPE",
					"a bare BUCKET groups by one text or number key, as an index does; BUCKET(src, key, proj) groups by several",
					keyNode.Pos)
			}

			var groupBy []RelationalGroup
			if (keyNode.T == SNodeCall && keyNode.Str == "LIST") || keyNode.T == SNodeList {
				for _, kArg := range keyNode.Kids {
					groupBy = append(groupBy, RelationalGroup{
						Alias:  nil,
						Binder: binder,
						Node:   kArg,
						Pos:    kArg.Pos,
					})
				}
			} else if keyNode.T == SNodeCall && keyNode.Str == "RECORD" {
				for _, kv := range recordFields(keyNode) {
					aliasCopy := kv.Key
					groupBy = append(groupBy, RelationalGroup{
						Alias:  &aliasCopy,
						Binder: binder,
						Node:   kv.Val,
						Pos:    kv.Val.Pos,
					})
				}
			} else {
				groupBy = append(groupBy, RelationalGroup{
					Alias:  nil,
					Binder: binder,
					Node:   keyNode,
					Pos:    keyNode.Pos,
				})
			}

			plan.GroupBy = groupBy
			if aggNode == nil {
				plan.Bucket = BucketOpen
				plan.BareKey = true
			} else {
				plan.Bucket = BucketNone
				plan.BareKey = false
			}
			t.bucketProjection(plan, binder, aggNode)

		case "SELECT_COLS":
			needDerived := t.planNeedsWrapBeforeMap(plan)
			plan = t.ensureDerived(plan, needDerived)

			var items []*SNode
			if len(args) == 2 && args[1].T == SNodeList {
				items = args[1].Kids
			} else {
				items = args[1:]
			}

			var cols []string
			for _, item := range items {
				if item.T != SNodeText {
					Refuse("E_BAD_ARG", "SELECT_COLS column names must be string literals", item.Pos)
				}
				col := item.Str
				uc := utf8.AsciiUpper(col)
				matches := 0
				if plan.SourceRelation != nil && plan.SourceRelation.Relation.Field(uc) != nil {
					matches++
				}
				for _, join := range plan.Joins {
					if join.SourceRelation != nil && join.SourceRelation.Relation.Field(uc) != nil {
						matches++
					}
				}
				if matches > 1 {
					Refuse("E_SQL_SHAPE",
						fmt.Sprintf("column '%s' is ambiguous across joined tables; qualify with a table alias", col),
						item.Pos)
				}
				if plan.SourceRelation != nil && len(plan.SourceRelation.Relation.Fields) > 0 && matches == 0 {
					var declared []string
					for k := range plan.SourceRelation.Relation.Fields {
						declared = append(declared, k)
					}
					sort.Strings(declared)
					Refuse("E_SQL_SHAPE",
						fmt.Sprintf("relation %s has no field '%s'; the relation declares %s",
							plan.SourceName, col, strings.Join(declared, ", ")),
						item.Pos)
				}
				cols = append(cols, col)
			}
			plan.SelectCols = cols
			plan.Projections = nil

		case "MAP":
			if plan.Bucket == BucketSealed {
				Refuse("E_SQL_SHAPE",
					"a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping",
					step.Pos)
			}
			binder := "_"
			var expr *SNode
			if len(args) == 2 {
				binder = "_"
				expr = args[1]
			} else if len(args) == 3 {
				if !IsBinderName(args[1]) {
					Refuse("E_SQL_SHAPE", "the binder of MAP must be a bare name", args[1].Pos)
				}
				binder = args[1].Str
				expr = args[2]
			} else {
				Refuse("E_ARITY", "MAP takes 2 or 3 arguments", step.Pos)
			}

			if plan.Bucket == BucketOpen {
				plan.Bucket = BucketNone
				t.bucketProjection(plan, binder, expr)
				continue
			}

			needDerived := t.planNeedsWrapBeforeMap(plan)
			plan = t.ensureDerived(plan, needDerived)

			if expr.T == SNodeCall && expr.Str == "RECORD" {
				var projections []RelationalProjection
				for _, kv := range recordFields(expr) {
					aliasCopy := kv.Key
					projections = append(projections, RelationalProjection{
						Alias:  &aliasCopy,
						Binder: binder,
						Node:   kv.Val,
					})
				}
				plan.Projections = projections
			} else {
				plan.Projections = []RelationalProjection{
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
				Refuse("E_SQL_SHAPE", "DISTINCT requires an explicit typed projection", step.Pos)
			}
			plan.Distinct = true

		case "TAKE":
			if len(args) != 2 {
				Refuse("E_ARITY", "TAKE takes 2 arguments", step.Pos)
			}
			lim := t.evalIntParam(args[1], "TAKE")
			if plan.Limit == nil {
				plan.Limit = &lim
			} else if lim < *plan.Limit {
				plan.Limit = &lim
			}

		case "DROP":
			if len(args) != 2 {
				Refuse("E_ARITY", "DROP takes 2 arguments", step.Pos)
			}
			off := t.evalIntParam(args[1], "DROP")
			skipped := off
			if plan.Limit != nil && *plan.Limit < skipped {
				skipped = *plan.Limit
			}
			currOff := int64(0)
			if plan.Offset != nil {
				currOff = *plan.Offset
			}
			if currOff > 9007199254740991-skipped {
				plan = t.ensureDerived(plan, true)
				plan.Offset = &off
			} else {
				if plan.Limit != nil {
					*plan.Limit -= skipped
				}
				newOff := currOff + skipped
				plan.Offset = &newOff
			}

		case "SORT", "SORT_DESC", "SORT_BY", "TOP", "TOP_DESC", "TOP_BY":
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
				_ = t.CompileStatement(plan)
			}
			needDerived := t.planHasRowsAbove(plan)
			plan = t.ensureDerived(plan, needDerived)

			if len(args) != 3 && len(args) != 5 {
				Refuse("E_ARITY", fmt.Sprintf("%s takes 3 or 5 arguments", name), step.Pos)
			}
			rightNode := args[1]
			if rightNode.T != SNodeVar || !t.bindings.Has(rightNode.Str) {
				Refuse("E_SQL_SHAPE", fmt.Sprintf("%s requires a bound relation as its right side", name), rightNode.Pos)
			}
			rightBinding := t.bindings.Get(rightNode.Str, rightNode.Pos)
			if rightBinding.Kind != BindingKindRelation {
				Refuse("E_SQL_SHAPE", fmt.Sprintf("%s is not bound as a relation", rightNode.Str), rightNode.Pos)
			}

			joinType := "INNER"
			if name == "LINK_LEFT" {
				joinType = "LEFT"
			}
			fromRaw := rightBinding.Relation.From.IsRaw
			table := rightBinding.Relation.From.Table
			if fromRaw {
				table = rightBinding.Relation.From.Raw
			}

			join := RelationalJoin{
				Type:           joinType,
				SourceName:     rightNode.Str,
				SourceRelation: rightBinding,
				SourceFromRaw:  fromRaw,
				SourceTable:    table,
				SourceAlias:    rightBinding.Relation.Alias,
				Pos:            step.Pos,
			}

			if len(args) == 5 {
				if !IsBinderName(args[2]) || !IsBinderName(args[3]) {
					Refuse("E_SQL_SHAPE", "join binders must be bare names", args[2].Pos)
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
				openAliases[0] = relationAlias(&plan.SourceRelation.Relation)
			}
			for _, j := range plan.Joins {
				if j.SourceAlias != "" {
					openAliases = append(openAliases, j.SourceAlias)
				}
			}
			mine := utf8.AsciiUpper(join.SourceAlias)
			for _, a := range openAliases {
				if utf8.AsciiUpper(a) == mine {
					Refuse("E_SQL_SHAPE",
						fmt.Sprintf("%s would be joined under the table alias %s, which this statement already uses; bind the relation a second time under another alias", rightNode.Str, join.SourceAlias),
						rightNode.Pos)
				}
			}

			plan.Joins = append(plan.Joins, join)
		}
	}

	return plan
}

func (t *Translator) evalIntParam(n *SNode, op string) int64 {
	node := n.ToNode()
	if node == nil {
		Refuse("E_SQL_SHAPE", fmt.Sprintf("%s count cannot contain dynamic lists", op), n.Pos)
	}
	prog := sel.NewProgram("", node)
	root := t.constRoot
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.Run(root)
	if err != nil {
		RefuseAsSel(err, n)
	}
	if !val.LooksNumeric() || val.IsNull() {
		Refuse("E_NOT_NUM", fmt.Sprintf("%s count must be a number", op), n.Pos)
	}
	text := val.AsText(n.Pos)
	if strings.Contains(text, ".") {
		Refuse("E_NOT_INT", fmt.Sprintf("%s count must be an integer", op), n.Pos)
	}
	if strings.HasPrefix(text, "-") {
		Refuse("E_RANGE", fmt.Sprintf("%s count cannot be negative", op), n.Pos)
	}
	num, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		Refuse("E_RANGE", fmt.Sprintf("%s count is out of range", op), n.Pos)
	}
	return num
}

func (t *Translator) analyzeSortStep(step *SNode, plan *RelationalPlan) {
	name := step.Str
	args := step.Kids
	top := name == "TOP" || name == "TOP_DESC" || name == "TOP_BY"
	if top && len(args) == 0 {
		Refuse("E_ARITY", fmt.Sprintf("%s has an invalid sort form", name), step.Pos)
	}
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
			if plan.SourceRelation != nil && plan.SourceRelation.Relation.Scalar != "" {
				scalarCol := plan.SourceRelation.Relation.Scalar
				varNode := Leaf(&sel.Node{T: sel.NodeVar, S: "_", Pos: step.Pos})
				idxNode := Leaf(&sel.Node{T: sel.NodeText, S: scalarCol, Pos: step.Pos})
				indexNode := Rewritten(&sel.Node{T: sel.NodeIndex, Pos: step.Pos}, []*SNode{varNode, idxNode})
				plan.OrderBy = append(plan.OrderBy, RelationalOrder{
					Binder: "_",
					Node:   indexNode,
					Dir:    dir,
					Pos:    step.Pos,
				})
				return
			}
			if plan.SourceRelation != nil && len(plan.SourceRelation.Relation.Fields) == 1 {
				var fieldName string
				for k := range plan.SourceRelation.Relation.Fields {
					fieldName = k
					break
				}
				varNode := Leaf(&sel.Node{T: sel.NodeVar, S: "_", Pos: step.Pos})
				idxNode := Leaf(&sel.Node{T: sel.NodeText, S: fieldName, Pos: step.Pos})
				indexNode := Rewritten(&sel.Node{T: sel.NodeIndex, Pos: step.Pos}, []*SNode{varNode, idxNode})
				plan.OrderBy = append(plan.OrderBy, RelationalOrder{
					Binder: "_",
					Node:   indexNode,
					Dir:    dir,
					Pos:    step.Pos,
				})
				return
			}
			Refuse("E_SQL_SHAPE", "SORT on a multi-field relation requires a key expression; use SORT_BY", step.Pos)
		} else if count == 2 {
			plan.OrderBy = append(plan.OrderBy, RelationalOrder{
				Binder: "_",
				Node:   args[1],
				Dir:    dir,
				Pos:    step.Pos,
			})
		} else if count == 3 {
			if !IsBinderName(args[1]) {
				Refuse("E_SQL_SHAPE", fmt.Sprintf("the binder of %s must be a bare name", name), args[1].Pos)
			}
			plan.OrderBy = append(plan.OrderBy, RelationalOrder{
				Binder: args[1].Str,
				Node:   args[2],
				Dir:    dir,
				Pos:    step.Pos,
			})
		} else {
			Refuse("E_ARITY", fmt.Sprintf("%s takes 1 to 3 arguments", name), step.Pos)
		}
		return
	}

	// SORT_BY or TOP_BY
	binder := "_"
	var key *SNode
	dir := "ASC"
	dirPos := step.Pos

	if count == 2 {
		binder = "_"
		key = args[1]
		dir = "ASC"
	} else if count == 3 {
		if args[2].T == SNodeText {
			binder = "_"
			key = args[1]
			dir = utf8.AsciiUpper(args[2].Str)
			dirPos = args[2].Pos
		} else if IsBinderName(args[1]) {
			binder = args[1].Str
			key = args[2]
			dir = "ASC"
		} else {
			Refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args[2].Pos)
		}
	} else if count == 4 {
		if !IsBinderName(args[1]) {
			Refuse("E_SQL_SHAPE", "the binder of SORT_BY must be a bare name", args[1].Pos)
		}
		binder = args[1].Str
		key = args[2]
		if args[3].T != SNodeText {
			Refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args[3].Pos)
		}
		dir = utf8.AsciiUpper(args[3].Str)
		dirPos = args[3].Pos
	} else {
		Refuse("E_ARITY", "SORT_BY takes 2 to 4 arguments", step.Pos)
	}

	if dir != "ASC" && dir != "DESC" {
		Refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", dirPos)
	}

	plan.OrderBy = append(plan.OrderBy, RelationalOrder{
		Binder: binder,
		Node:   key,
		Dir:    dir,
		Pos:    step.Pos,
	})
}

func (t *Translator) CompileStatement(plan *RelationalPlan) *Fragment {
	checkAliasesProj := func(entries []RelationalProjection) {
		seen := make(map[string]bool)
		for _, entry := range entries {
			if entry.Alias != nil {
				upper := utf8.AsciiUpper(*entry.Alias)
				if seen[upper] {
					Refuse("E_SQL_SHAPE", "duplicate or case-colliding RECORD fields require local evaluation", entry.Node.Pos)
				}
				seen[upper] = true
			}
		}
	}
	checkAliasesGroup := func(entries []RelationalGroup) {
		seen := make(map[string]bool)
		for _, entry := range entries {
			if entry.Alias != nil {
				upper := utf8.AsciiUpper(*entry.Alias)
				if seen[upper] {
					Refuse("E_SQL_SHAPE", "duplicate or case-colliding RECORD fields require local evaluation", entry.Node.Pos)
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

	var srcRel *RelationSpec
	if plan.SourceRelation != nil {
		srcRel = &plan.SourceRelation.Relation
	}
	src := Source{
		Shape:    SourceShapeRelation,
		Relation: srcRel,
	}
	for _, f := range plan.Filters {
		src.Filters = append(src.Filters, SourceFilter{Binder: f.Binder, Node: f.Node})
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
					Refuse("E_SQL_SHAPE", "DISTINCT requires proven structural output identity", proj.Node.Pos)
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
			var fSpec *ColumnSpec
			if srcRel != nil {
				fSpec = srcRel.Field(utf8.AsciiUpper(col))
			}
			for _, join := range plan.Joins {
				if fSpec == nil && join.SourceRelation != nil {
					fSpec = join.SourceRelation.Relation.Field(utf8.AsciiUpper(col))
					if fSpec != nil {
						owner = &join.SourceRelation.Relation
					}
				}
			}
			column := col
			if fSpec != nil && fSpec.Column != "" {
				column = fSpec.Column
			}
			if plan.Distinct && (fSpec == nil || fSpec.Type == KindUnknown || fSpec.Type == KindNum) {
				Refuse("E_SQL_SHAPE", "DISTINCT requires known output kinds", Pos{})
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
			Refuse("E_SQL_SHAPE",
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
	if plan.GroupBy != nil && len(plan.GroupBy) > 0 {
		addSql(" GROUP BY ")
		first := true
		for _, gb := range plan.GroupBy {
			if !first {
				addSql(", ")
			}
			first = false
			gFrag := t.groupKey(src, gb, false)
			if plan.BareKey && (gFrag.Kind == KindBool || gFrag.Kind == KindBin) {
				Refuse("E_SQL_SHAPE",
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
