package sql

import (
	"github.com/nathanjel/sel/go/sel"
)

type sNodeType string

const (
	sNodeNum    sNodeType = "num"
	sNodeText   sNodeType = "text"
	sNodeBool   sNodeType = "bool"
	sNodeNull   sNodeType = "null"
	sNodeVar    sNodeType = "var"
	sNodeIndex  sNodeType = "index"
	sNodeSeq    sNodeType = "seq"
	sNodeList   sNodeType = "list"
	sNodeUn     sNodeType = "un"
	sNodeBin    sNodeType = "bin"
	sNodeAssign sNodeType = "assign"
	sNodeCall   sNodeType = "call"
	sNodeCList  sNodeType = "clist"
)

type cListEntry struct {
	Key string
	Val *sNode
}

// varScope says how a variable read found its name. Stage 1 knows the lexical
// scope, and the translator must not have to guess it again: an inlined helper's
// reads are the caller's outer names, whatever binder they end up under.
type varScope uint8

const (
	varScopeUnknown varScope = iota // built outside stage 1: resolve as before
	varScopeFree                    // no binder in scope names it: a binding
	varScopeBound                   // an enclosing binder names it
)

type sNode struct {
	VarScope       varScope
	size           int64 // memo of Size; 0 = not yet computed
	valid, numeric bool  // Validate / RequireNumeric already passed for this big subtree
	constMemo      uint8 // memo of IsConstant for a large shared subtree: 1 yes, 2 no
	toNodeDone     bool  // ToNode already ran for this (immutable) subtree
	toNodeVal      *sel.Node
	T              sNodeType
	Pos            Pos
	Origin         *sel.Node
	Str            string
	BoolVal        bool
	Grouped        bool
	Spec           *sel.Spec
	Kids           []*sNode
	Keys           []string
	Entries        []cListEntry
}

func leaf(n *sel.Node) *sNode {
	if n == nil {
		return nil
	}
	return &sNode{
		T:       sNodeType(n.T),
		Pos:     n.Pos,
		Origin:  n,
		Str:     n.S,
		BoolVal: n.B,
		Grouped: n.Grouped,
		Spec:    n.Spec,
	}
}

func rewritten(shape *sel.Node, kids []*sNode) *sNode {
	return &sNode{
		T:       sNodeType(shape.T),
		Pos:     shape.Pos,
		Origin:  shape,
		Str:     shape.S,
		BoolVal: shape.B,
		Grouped: shape.Grouped,
		Spec:    shape.Spec,
		Kids:    kids,
	}
}

func newCList(pos Pos) *sNode {
	return &sNode{
		T:   sNodeCList,
		Pos: pos,
	}
}

func (s *sNode) Append(key string, val *sNode) {
	s.Keys = append(s.Keys, key)
	s.Kids = append(s.Kids, val)
	s.Entries = append(s.Entries, cListEntry{Key: key, Val: val})
}

func cList(pos Pos, entries []cListEntry) *sNode {
	out := &sNode{
		T:       sNodeCList,
		Pos:     pos,
		Entries: entries,
	}
	for _, e := range entries {
		out.Keys = append(out.Keys, e.Key)
		out.Kids = append(out.Kids, e.Val)
	}
	return out
}

func (s *sNode) L() *sNode {
	if len(s.Kids) > 0 {
		return s.Kids[0]
	}
	return nil
}

func (s *sNode) R() *sNode {
	if len(s.Kids) > 1 {
		return s.Kids[1]
	}
	return nil
}

func (s *sNode) Obj() *sNode {
	return s.L()
}

func (s *sNode) Idx() *sNode {
	return s.R()
}

func (s *sNode) ToNode() *sel.Node {
	if s == nil || s.T == sNodeCList {
		return nil
	}
	// An SNode is not changed once built, so its evaluable form is built once: the
	// translator asks for it at every constant node of a chain, each time for the
	// whole subtree below, which made a chain of n cost n copies of n nodes.
	if s.toNodeDone {
		return s.toNodeVal
	}
	out := s.buildNode()
	s.toNodeVal, s.toNodeDone = out, true
	return out
}

func (s *sNode) buildNode() *sel.Node {
	if len(s.Kids) == 0 {
		return s.Origin
	}

	copyNode := &sel.Node{
		T:       sel.NodeType(s.T),
		Pos:     s.Pos,
		S:       s.Str,
		B:       s.BoolVal,
		Grouped: s.Grouped,
		Spec:    s.Spec,
	}

	switch s.T {
	case sNodeUn:
		child := s.Kids[0].ToNode()
		if child == nil {
			return nil
		}
		copyNode.L = child
	case sNodeBin, sNodeIndex, sNodeAssign:
		l := s.Kids[0].ToNode()
		r := s.Kids[1].ToNode()
		if l == nil || r == nil {
			return nil
		}
		copyNode.L = l
		copyNode.R = r
	default:
		for _, kid := range s.Kids {
			child := kid.ToNode()
			if child == nil {
				return nil
			}
			copyNode.Items = append(copyNode.Items, child)
		}
	}

	return copyNode
}

// sizeSaturation keeps Size finite for a helper chain that doubles at every step:
// such a tree is 2^n nodes, and only its being over the budget matters.
const sizeSaturation = int64(1) << 40

// Size is the number of nodes the tree has when every shared subtree is counted
// once per occurrence, which is what the translator's walk will see (and charge,
// MAX_SQL_NODES). Computed once per node, over the shared structure, so it costs
// what the source costs and not what its expansion does.
func (s *sNode) Size() int64 {
	if s == nil {
		return 0
	}
	if s.size != 0 {
		return s.size
	}
	total := int64(1)
	for _, k := range s.Kids {
		total += k.Size()
		if total > sizeSaturation {
			total = sizeSaturation
			break
		}
	}
	s.size = total
	return total
}
