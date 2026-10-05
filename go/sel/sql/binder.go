package sql

type binderShape int

const (
	binderShapeNode binderShape = iota
	binderShapeColumn
	binderShapeRow
	binderShapeNone
	binderShapeKey
	binderShapeGroup
	binderShapeProjected
)

type binder struct {
	Shape       binderShape
	Node        *sNode
	Column      columnSpec
	Relation    *relationSpec
	Reason      string
	GroupBinder string
	Projections []relationalProjection
	Model       *rowModel
	// Scoped says Node was written in the scope that had Scope frames open, and
	// is read there: an element of a static list belongs to the aggregate the
	// list is written in, not to the one that iterates it.
	Scoped bool
	Scope  int
}

func binderNode(n *sNode) binder {
	return binder{Shape: binderShapeNode, Node: n}
}

func binderNodeAt(n *sNode, scope int) binder {
	return binder{Shape: binderShapeNode, Node: n, Scoped: true, Scope: scope}
}

func binderColumn(c columnSpec) binder {
	return binder{Shape: binderShapeColumn, Column: c}
}

func binderRow(r *relationSpec) binder {
	return binder{Shape: binderShapeRow, Relation: r}
}

func binderNone(reason string) binder {
	return binder{Shape: binderShapeNone, Reason: reason}
}

func binderKey(groupBinder string, groupNode *sNode, r *relationSpec) binder {
	return binder{Shape: binderShapeKey, GroupBinder: groupBinder, Node: groupNode, Relation: r}
}

func binderGroup(r *relationSpec) binder {
	return binder{Shape: binderShapeGroup, Relation: r}
}

func binderProjected(r *relationSpec, projections []relationalProjection) binder {
	return binder{Shape: binderShapeProjected, Relation: r, Projections: projections}
}
