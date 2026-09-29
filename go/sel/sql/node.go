package sql

import (
	"github.com/nathanjel/sel/go/sel"
)

type SNodeType string

const (
	SNodeNum    SNodeType = "num"
	SNodeText   SNodeType = "text"
	SNodeBool   SNodeType = "bool"
	SNodeNull   SNodeType = "null"
	SNodeVar    SNodeType = "var"
	SNodeIndex  SNodeType = "index"
	SNodeSeq    SNodeType = "seq"
	SNodeList   SNodeType = "list"
	SNodeUn     SNodeType = "un"
	SNodeBin    SNodeType = "bin"
	SNodeAssign SNodeType = "assign"
	SNodeCall   SNodeType = "call"
	SNodeCList  SNodeType = "clist"
)

type CListEntry struct {
	Key string
	Val *SNode
}

// VarScope says how a variable read found its name. Stage 1 knows the lexical
// scope, and the translator must not have to guess it again: an inlined helper's
// reads are the caller's outer names, whatever binder they end up under.
type VarScope uint8

const (
	VarScopeUnknown VarScope = iota // built outside stage 1: resolve as before
	VarScopeFree                    // no binder in scope names it: a binding
	VarScopeBound                   // an enclosing binder names it
)

type SNode struct {
	VarScope       VarScope
	size           int64 // memo of Size; 0 = not yet computed
	valid, numeric bool  // Validate / RequireNumeric already passed for this big subtree
	constMemo      uint8 // memo of IsConstant for a large shared subtree: 1 yes, 2 no
	T              SNodeType
	Pos            Pos
	Origin         *sel.Node
	Str            string
	BoolVal        bool
	Grouped        bool
	Spec           *sel.Spec
	Kids           []*SNode
	Keys           []string
	Entries        []CListEntry
}

func Leaf(n *sel.Node) *SNode {
	if n == nil {
		return nil
	}
	return &SNode{
		T:       SNodeType(n.T),
		Pos:     n.Pos,
		Origin:  n,
		Str:     n.S,
		BoolVal: n.B,
		Grouped: n.Grouped,
		Spec:    n.Spec,
	}
}

func Rewritten(shape *sel.Node, kids []*SNode) *SNode {
	return &SNode{
		T:       SNodeType(shape.T),
		Pos:     shape.Pos,
		Origin:  shape,
		Str:     shape.S,
		BoolVal: shape.B,
		Grouped: shape.Grouped,
		Spec:    shape.Spec,
		Kids:    kids,
	}
}

func NewCList(pos Pos) *SNode {
	return &SNode{
		T:   SNodeCList,
		Pos: pos,
	}
}

func (s *SNode) Append(key string, val *SNode) {
	s.Keys = append(s.Keys, key)
	s.Kids = append(s.Kids, val)
	s.Entries = append(s.Entries, CListEntry{Key: key, Val: val})
}

func CList(pos Pos, entries []CListEntry) *SNode {
	out := &SNode{
		T:       SNodeCList,
		Pos:     pos,
		Entries: entries,
	}
	for _, e := range entries {
		out.Keys = append(out.Keys, e.Key)
		out.Kids = append(out.Kids, e.Val)
	}
	return out
}

func (s *SNode) L() *SNode {
	if len(s.Kids) > 0 {
		return s.Kids[0]
	}
	return nil
}

func (s *SNode) R() *SNode {
	if len(s.Kids) > 1 {
		return s.Kids[1]
	}
	return nil
}

func (s *SNode) Obj() *SNode {
	return s.L()
}

func (s *SNode) Idx() *SNode {
	return s.R()
}

func (s *SNode) Target() *SNode {
	return s.L()
}

func (s *SNode) Val() *SNode {
	return s.R()
}

func (s *SNode) Args() []*SNode {
	return s.Kids
}

func (s *SNode) Items() []*SNode {
	return s.Kids
}

func (s *SNode) ToNode() *sel.Node {
	if s == nil || s.T == SNodeCList {
		return nil
	}
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
	if s.Origin != nil {
		copyNode.Dec = s.Origin.Dec
	}

	switch s.T {
	case SNodeUn:
		child := s.Kids[0].ToNode()
		if child == nil {
			return nil
		}
		copyNode.L = child
	case SNodeBin, SNodeIndex, SNodeAssign:
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
func (s *SNode) Size() int64 {
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
