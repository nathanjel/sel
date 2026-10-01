package sel

import (
	"sync/atomic"

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
	// Misses counts how many times this site replaced its entry because the row had
	// another shape; a site that keeps alternating stops storing (GO-P25).
	Misses int32
}

// slotCacheMissLimit is how many replacements a site makes before it is treated as
// polymorphic: its last entry stays (still a hit for that shape) and further misses
// take the shape's key map without allocating a new entry or storing anything.
const slotCacheMissLimit = 8

// storeSlot records shape→slot at a site, unless the site has proved polymorphic.
func storeSlot(holder *atomic.Pointer[SlotCache], shape *RecordShape, slot int) {
	var misses int32
	if old := holder.Load(); old != nil {
		if old.Misses >= slotCacheMissLimit {
			return
		}
		misses = old.Misses + 1
	}
	holder.Store(&SlotCache{Shape: shape, Slot: slot, Misses: misses})
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
	Op   string
	Dst  uint16
	Src1 uint16
	Src2 uint16
	// Reg is where an ADD, SUB or MUL result lives: 0 for a fresh Dec, i for
	// register i of the evaluation's register file (assignRegisters, item 1).
	Reg      uint16
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
	// UsesRegs: the plan has an ADD, SUB or MUL, so an evaluation takes a
	// register file -- registers 1..NumRegs and the alignment scratch.
	UsesRegs bool
	NumRegs  uint16
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

	Dec   *decimal.Dec
	Shape *RecordShape
	Spec  *Spec
	// The holder is shared safely when optimizers copy a node. Each load must
	// use one immutable snapshot for both the shape check and slot lookup.
	SlotCache *atomic.Pointer[SlotCache]

	MathPlan *MathPlan

	KeysUnobserved bool
}

func NewNode(t NodeType, pos Pos) *Node {
	n := &Node{T: t, Pos: pos}
	if t == NodeIndex {
		n.SlotCache = new(atomic.Pointer[SlotCache])
	}
	return n
}
