package sql

type BinderShape int

const (
	BinderShapeNode BinderShape = iota
	BinderShapeColumn
	BinderShapeRow
	BinderShapeNone
	BinderShapeKey
	BinderShapeGroup
	BinderShapeProjected
)

type Binder struct {
	Shape       BinderShape
	Node        *SNode
	Column      ColumnSpec
	Relation    *RelationSpec
	Reason      string
	GroupBinder string
	GroupNode   *SNode
	Projections []RelationalProjection
	Model       *RowModel
}

func NewBinderNode(n *SNode) *Binder {
	return &Binder{
		Shape: BinderShapeNode,
		Node:  n,
	}
}

func NewBinderColumn(c ColumnSpec) *Binder {
	return &Binder{
		Shape:  BinderShapeColumn,
		Column: c,
	}
}

func NewBinderRow(r *RelationSpec) *Binder {
	return &Binder{
		Shape:    BinderShapeRow,
		Relation: r,
	}
}

func NewBinderNone(reason string) *Binder {
	return &Binder{
		Shape:  BinderShapeNone,
		Reason: reason,
	}
}

func NewBinderKey(groupBinder string, groupNode *SNode, r *RelationSpec) *Binder {
	return &Binder{
		Shape:       BinderShapeKey,
		GroupBinder: groupBinder,
		GroupNode:   groupNode,
		Relation:    r,
	}
}

func NewBinderGroup(r *RelationSpec) *Binder {
	return &Binder{
		Shape:    BinderShapeGroup,
		Relation: r,
	}
}

func NewBinderProjected(r *RelationSpec, projections []RelationalProjection) *Binder {
	return &Binder{
		Shape:       BinderShapeProjected,
		Relation:    r,
		Projections: projections,
	}
}

func BinderNode(n *SNode) Binder {
	return Binder{Shape: BinderShapeNode, Node: n}
}

func BinderColumn(c ColumnSpec) Binder {
	return Binder{Shape: BinderShapeColumn, Column: c}
}

func BinderRow(r *RelationSpec) Binder {
	return Binder{Shape: BinderShapeRow, Relation: r}
}

func BinderNone(reason string) Binder {
	return Binder{Shape: BinderShapeNone, Reason: reason}
}

func BinderKey(groupBinder string, groupNode *SNode, r *RelationSpec) Binder {
	return Binder{Shape: BinderShapeKey, GroupBinder: groupBinder, Node: groupNode, Relation: r}
}

func BinderGroup(r *RelationSpec) Binder {
	return Binder{Shape: BinderShapeGroup, Relation: r}
}

func BinderProjected(r *RelationSpec, projections []RelationalProjection) Binder {
	return Binder{Shape: BinderShapeProjected, Relation: r, Projections: projections}
}
