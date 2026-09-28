package sel

import (
	"github.com/nathanjel/sel/go/internal/decimal"
)

type NodeType string

const (
	NodeNum    NodeType = "num"
	NodeText   NodeType = "text"
	NodeBool   NodeType = "bool"
	NodeNull   NodeType = "null"
	NodeVar    NodeType = "var"
	NodeIndex  NodeType = "index"
	NodeSeq    NodeType = "seq"
	NodeList   NodeType = "list"
	NodeUn     NodeType = "un"
	NodeBin    NodeType = "bin"
	NodeAssign NodeType = "assign"
	NodeCall   NodeType = "call"
)

type SlotCache struct {
	Shape *RecordShape
	Slot  int
}

type Spec struct {
	Name       string
	Min        int
	Max        int
	Lazy       bool
	Binds      bool
	ArityError func(n int) string
	Fn         func(args *Args, ctx *Context) *Value
}

type MathStep struct {
	Op       string
	Dst      uint16
	Src1     uint16
	Src2     uint16
	Pos      Pos
	AuxPos   Pos
	Name     string
	ConstVal *decimal.Dec
	LeafNode *Node
}

type MathPlan struct {
	Steps          []MathStep
	OutputSlot     uint16
	ScratchpadSize uint16
}

type Node struct {
	T       NodeType
	Pos     Pos
	S       string
	B       bool
	Grouped bool

	L     *Node
	R     *Node
	Items []*Node

	Dec       *decimal.Dec
	Shape     *RecordShape
	Spec      *Spec
	SlotCache *SlotCache

	MathPlan *MathPlan

	KeysUnobserved bool
}

func NewNode(t NodeType, pos Pos) *Node {
	return &Node{T: t, Pos: pos}
}
