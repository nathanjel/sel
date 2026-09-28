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

type SNode struct {
	T       SNodeType
	Pos     Pos
	Origin  *sel.Node
	Str     string
	BoolVal bool
	Grouped bool
	Spec    *sel.Spec
	Kids    []*SNode
	Keys    []string
	Entries []CListEntry
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
