package sql

import (
	"github.com/nathanjel/sel/go/internal/utf8"
)

type rowField struct {
	Spec     columnSpec
	Table    string
	Qualify  bool
	Optional bool
}

type rowModel struct {
	Side      bool
	Relation  *relationSpec
	Table     string
	Qualify   bool
	Names     []string
	Nested    []pair[string, *rowModel]
	SelfNames []string
	Promoted  []pair[string, rowField]
	Dropped   map[string]bool
}

type pair[K comparable, V any] struct {
	Key K
	Val V
}

func (rm *rowModel) Copy() *rowModel {
	if rm == nil {
		return nil
	}
	cp := *rm
	cp.Names = append([]string(nil), rm.Names...)
	cp.Nested = append([]pair[string, *rowModel](nil), rm.Nested...)
	cp.SelfNames = append([]string(nil), rm.SelfNames...)
	cp.Promoted = append([]pair[string, rowField](nil), rm.Promoted...)
	if rm.Dropped != nil {
		cp.Dropped = make(map[string]bool)
		for k, v := range rm.Dropped {
			cp.Dropped[k] = v
		}
	}
	return &cp
}

type joinRowsStep struct {
	Left  *rowModel
	Right *rowModel
}

type joinRows struct {
	Row   *rowModel
	Steps []joinRowsStep
}

func orderedSet[V any](list *[]pair[string, V], k string, v V) {
	for i := range *list {
		if (*list)[i].Key == k {
			(*list)[i].Val = v
			return
		}
	}
	*list = append(*list, pair[string, V]{Key: k, Val: v})
}

func binderKeys(names []string) []string {
	var out []string
	for _, name := range names {
		lower := utf8.AsciiLower(name)
		for _, k := range []string{name, lower} {
			if !containsString(out, k) {
				out = append(out, k)
			}
		}
	}
	return out
}

func nestedOf(row *rowModel, key string) *rowModel {
	if row == nil {
		return nil
	}
	if row.Side {
		if containsString(row.Names, key) {
			return row
		}
		return nil
	}
	if containsString(row.SelfNames, key) {
		return row
	}
	for _, kv := range row.Nested {
		if kv.Key == key {
			return kv.Val
		}
	}
	return nil
}

func rowFieldSpec(row *rowModel, key string) (rowField, bool) {
	u := utf8.AsciiUpper(key)
	if row.Side {
		if row.Relation != nil {
			spec := row.Relation.Field(u)
			if spec != nil {
				return rowField{Spec: *spec, Table: row.Table, Qualify: row.Qualify, Optional: false}, true
			}
		}
		return rowField{}, false
	}
	for _, kv := range row.Promoted {
		if kv.Key == u {
			return kv.Val, true
		}
	}
	return rowField{}, false
}

func withNames(row *rowModel, names []string) *rowModel {
	out := row.Copy()
	if row.Side {
		out.Names = names
	} else {
		for _, k := range names {
			var filtered []pair[string, *rowModel]
			for _, kv := range out.Nested {
				if kv.Key != k {
					filtered = append(filtered, kv)
				}
			}
			out.Nested = filtered
			if !containsString(out.SelfNames, k) {
				out.SelfNames = append(out.SelfNames, k)
			}
		}
	}
	return out
}

func rowKeys(row *rowModel) []string {
	var out []string
	if row.Side {
		if row.Relation != nil {
			for k := range row.Relation.Fields {
				out = append(out, k)
			}
		}
		out = append(out, row.Names...)
	} else {
		for _, kv := range row.Nested {
			out = append(out, kv.Key)
		}
		out = append(out, row.SelfNames...)
		for _, kv := range row.Promoted {
			out = append(out, kv.Key)
		}
	}
	return out
}

func scalarFields(row *rowModel) []pair[string, rowField] {
	if !row.Side {
		return row.Promoted
	}
	var out []pair[string, rowField]
	if row.Relation != nil {
		// In declared order, as relationFieldNames gives it: ranging over the
		// Fields map gave a different order each run.
		for _, u := range relationFieldNames(row.Relation) {
			out = append(out, pair[string, rowField]{
				Key: u,
				Val: rowField{Spec: row.Relation.Fields[u], Table: row.Table, Qualify: row.Qualify, Optional: false},
			})
		}
	}
	return out
}

func relationAlias(rel *relationSpec) string {
	if rel == nil {
		return ""
	}
	if rel.Alias != "" {
		return rel.Alias
	}
	if rel.From.IsRaw {
		return rel.From.Raw
	}
	return rel.From.Table
}

func buildJoinRows(plan *relationalPlan) joinRows {
	qualify := len(plan.Joins) > 0 || plan.SourceSubquery != nil
	side := func(rel *relationSpec, alias string, names []string) *rowModel {
		t := alias
		if t == "" {
			t = relationAlias(rel)
		}
		return &rowModel{
			Side:     true,
			Relation: rel,
			Table:    t,
			Qualify:  qualify,
			Names:    names,
		}
	}

	var result joinRows
	var srcRel *relationSpec
	if plan.SourceRelation != nil {
		srcRel = &plan.SourceRelation.relation
	}
	left := side(srcRel, plan.SourceAlias, nil)

	for _, join := range plan.Joins {
		leftNames := binderKeys(join.LeftNames)
		rightNames := binderKeys(join.RightNames)
		var joinRel *relationSpec
		if join.SourceRelation != nil {
			joinRel = &join.SourceRelation.relation
		}
		right := side(joinRel, join.SourceAlias, rightNames)
		leftEl := withNames(left, leftNames)

		for _, check := range []struct {
			el    *rowModel
			names []string
		}{
			{leftEl, join.LeftNames},
			{right, join.RightNames},
		} {
			if !check.el.Side || check.el.Relation == nil {
				continue
			}
			for _, name := range check.names {
				if check.el.Relation.Field(utf8.AsciiUpper(name)) != nil {
					refuse("E_SQL_SHAPE",
						name+" names a LINK side that has a field of that name too, which SEL binds instead of the row; rename the binder",
						join.Pos)
				}
			}
		}

		row := &rowModel{
			Side:    false,
			Dropped: make(map[string]bool),
		}
		if !leftEl.Side {
			row.Nested = append(row.Nested, leftEl.Nested...)
			for _, k := range leftEl.SelfNames {
				orderedSet(&row.Nested, k, leftEl)
			}
		}
		orderedSet(&row.Nested, "_1", leftEl)
		for _, k := range leftNames {
			orderedSet(&row.Nested, k, leftEl)
		}
		orderedSet(&row.Nested, "_2", right)
		for _, k := range rightNames {
			orderedSet(&row.Nested, k, right)
		}

		leftKeys := make(map[string]bool)
		for _, k := range rowKeys(leftEl) {
			leftKeys[utf8.AsciiUpper(k)] = true
		}
		rightKeys := make(map[string]bool)
		for _, k := range rowKeys(right) {
			rightKeys[utf8.AsciiUpper(k)] = true
		}

		for _, kv := range scalarFields(leftEl) {
			if rightKeys[kv.Key] {
				row.Dropped[kv.Key] = true
			} else {
				orderedSet(&row.Promoted, kv.Key, kv.Val)
			}
		}
		for _, kv := range scalarFields(right) {
			if leftKeys[kv.Key] {
				row.Dropped[kv.Key] = true
			} else {
				rf := kv.Val
				if join.Type == "LEFT" {
					rf.Optional = true
				}
				orderedSet(&row.Promoted, kv.Key, rf)
			}
		}

		result.Steps = append(result.Steps, joinRowsStep{Left: leftEl, Right: right})
		left = row
	}

	result.Row = left
	return result
}
