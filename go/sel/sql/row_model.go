package sql

import (
	"strings"

	"github.com/nathanjel/sel/go/internal/utf8"
)

type RowField struct {
	Spec     ColumnSpec
	Table    string
	Qualify  bool
	Optional bool
}

type RowModel struct {
	Side      bool
	Relation  *RelationSpec
	Table     string
	Qualify   bool
	Names     []string
	Nested    []Pair[string, *RowModel]
	SelfNames []string
	Promoted  []Pair[string, RowField]
	Dropped   map[string]bool
}

type Pair[K comparable, V any] struct {
	Key K
	Val V
}

func (rm *RowModel) Copy() *RowModel {
	if rm == nil {
		return nil
	}
	cp := *rm
	cp.Names = append([]string(nil), rm.Names...)
	cp.Nested = append([]Pair[string, *RowModel](nil), rm.Nested...)
	cp.SelfNames = append([]string(nil), rm.SelfNames...)
	cp.Promoted = append([]Pair[string, RowField](nil), rm.Promoted...)
	if rm.Dropped != nil {
		cp.Dropped = make(map[string]bool)
		for k, v := range rm.Dropped {
			cp.Dropped[k] = v
		}
	}
	return &cp
}

type JoinRowsStep struct {
	Left  *RowModel
	Right *RowModel
}

type JoinRows struct {
	Row   *RowModel
	Steps []JoinRowsStep
}

func orderedSet[V any](list *[]Pair[string, V], k string, v V) {
	for i := range *list {
		if (*list)[i].Key == k {
			(*list)[i].Val = v
			return
		}
	}
	*list = append(*list, Pair[string, V]{Key: k, Val: v})
}

func binderKeys(names []string) []string {
	var out []string
	for _, name := range names {
		lower := strings.ToLower(name)
		for _, k := range []string{name, lower} {
			if !containsString(out, k) {
				out = append(out, k)
			}
		}
	}
	return out
}

func nestedOf(row *RowModel, key string) *RowModel {
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

func rowFieldSpec(row *RowModel, key string) (RowField, bool) {
	u := utf8.AsciiUpper(key)
	if row.Side {
		if row.Relation != nil {
			spec := row.Relation.Field(u)
			if spec != nil {
				return RowField{Spec: *spec, Table: row.Table, Qualify: row.Qualify, Optional: false}, true
			}
		}
		return RowField{}, false
	}
	for _, kv := range row.Promoted {
		if kv.Key == u {
			return kv.Val, true
		}
	}
	return RowField{}, false
}

func withNames(row *RowModel, names []string) *RowModel {
	out := row.Copy()
	if row.Side {
		out.Names = names
	} else {
		for _, k := range names {
			var filtered []Pair[string, *RowModel]
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

func rowKeys(row *RowModel) []string {
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

func scalarFields(row *RowModel) []Pair[string, RowField] {
	if !row.Side {
		return row.Promoted
	}
	var out []Pair[string, RowField]
	if row.Relation != nil {
		if len(row.Relation.FieldOrder) > 0 {
			for _, u := range row.Relation.FieldOrder {
				if spec, ok := row.Relation.Fields[u]; ok {
					out = append(out, Pair[string, RowField]{
						Key: u,
						Val: RowField{Spec: spec, Table: row.Table, Qualify: row.Qualify, Optional: false},
					})
				}
			}
		} else {
			for u, spec := range row.Relation.Fields {
				out = append(out, Pair[string, RowField]{
					Key: u,
					Val: RowField{Spec: spec, Table: row.Table, Qualify: row.Qualify, Optional: false},
				})
			}
		}
	}
	return out
}

func relationAlias(rel *RelationSpec) string {
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

func BuildJoinRows(plan *RelationalPlan) JoinRows {
	qualify := len(plan.Joins) > 0 || plan.SourceSubquery != nil
	side := func(rel *RelationSpec, alias string, names []string) *RowModel {
		t := alias
		if t == "" {
			t = relationAlias(rel)
		}
		return &RowModel{
			Side:     true,
			Relation: rel,
			Table:    t,
			Qualify:  qualify,
			Names:    names,
		}
	}

	var result JoinRows
	var srcRel *RelationSpec
	if plan.SourceRelation != nil {
		srcRel = &plan.SourceRelation.Relation
	}
	left := side(srcRel, plan.SourceAlias, nil)

	for _, join := range plan.Joins {
		leftNames := binderKeys(join.LeftNames)
		rightNames := binderKeys(join.RightNames)
		var joinRel *RelationSpec
		if join.SourceRelation != nil {
			joinRel = &join.SourceRelation.Relation
		}
		right := side(joinRel, join.SourceAlias, rightNames)
		leftEl := withNames(left, leftNames)

		for _, check := range []struct {
			el    *RowModel
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
					Refuse("E_SQL_SHAPE",
						name+" names a LINK side that has a field of that name too, which SEL binds instead of the row; rename the binder",
						join.Pos)
				}
			}
		}

		row := &RowModel{
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

		result.Steps = append(result.Steps, JoinRowsStep{Left: leftEl, Right: right})
		left = row
	}

	result.Row = left
	return result
}
