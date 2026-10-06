package sel

import (
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/decimal"
)

// NodeType is the kind of a syntax tree node.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
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

type slotCache struct {
	Shape *RecordShape
	Slot  int
	// Misses counts how many times this site replaced its entry because the row had
	// another shape; a site that keeps alternating stops storing.
	Misses int32
}

// slotCacheMissLimit is how many replacements a site makes before it is treated as
// polymorphic: its last entry stays (still a hit for that shape) and further misses
// take the shape's key map without allocating a new entry or storing anything.
const slotCacheMissLimit = 8

// storeSlot records shape→slot at a site, unless the site has proved polymorphic.
func storeSlot(holder *atomic.Pointer[slotCache], shape *RecordShape, slot int) {
	var misses int32
	if old := holder.Load(); old != nil {
		if old.Misses >= slotCacheMissLimit {
			return
		}
		misses = old.Misses + 1
	}
	holder.Store(&slotCache{Shape: shape, Slot: slot, Misses: misses})
}

// Spec describes a builtin for Define: its name, its argument counts, and Fn. A
// lazy builtin receives the argument nodes unevaluated (Args.Node,
// Args.EvalNode); Binds says it binds names in its arguments.
type Spec struct {
	Name       string
	Min        int
	Max        int
	Lazy       bool
	Binds      bool
	ArityError func(n int) string
	Fn         func(args *Args, ctx *Context) *Value
}

type mathStep struct {
	Op   string
	Dst  uint16
	Src1 uint16
	Src2 uint16
	// Reg is where an ADD, SUB or MUL result lives: 0 for a fresh Dec, i for
	// register i of the evaluation's register file (assignRegisters).
	Reg      uint16
	Pos      Pos
	AuxPos   Pos
	Name     string
	ConstVal *decimal.Dec
	LeafNode *Node
}

type mathPlan struct {
	Steps          []mathStep
	OutputSlot     uint16
	ScratchpadSize uint16
	// UsesRegs: the plan has an ADD, SUB or MUL, so an evaluation takes a
	// register file -- registers 1..NumRegs and the alignment scratch.
	UsesRegs bool
	NumRegs  uint16
}

// Node is a node of a compiled program's syntax tree.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
type Node struct {
	T       NodeType
	Pos     Pos
	S       string
	B       bool
	Grouped bool
	// BindingRead marks a NodeVar that reads the SQL catalogue's binding of its
	// name although a helper assignment of the same name shadows it: the hybrid
	// planner sets it on the source it reaches by unwinding `ORDERS = ORDERS .>
	// DROP(2)`, so neither the helper's dependency walk nor the translator's
	// inlining reads that source as the helper. The evaluator ignores it.
	BindingRead bool
	// stepDepth is where a pipeline step stood in the tree as written (the
	// outermost step at its pipeline's own depth), 0 when unknown: the optimiser
	// sets it for the rules that would deepen a subtree (FILTER fusion).
	stepDepth int32

	L     *Node
	R     *Node
	Items []*Node

	dec   *decimal.Dec
	shape *RecordShape
	Spec  *Spec
	// The holder is shared safely when optimizers copy a node. Each load must
	// use one immutable snapshot for both the shape check and slot lookup.
	slotCache *atomic.Pointer[slotCache]

	mathPlan *mathPlan

	// lit is the value a number or text literal evaluates to, made once with the
	// node (parser, constant folding): what an operator reads for a literal
	// operand (operand). An operator only reads its operands and builds a fresh
	// result, so the one value cannot be told from a fresh one; anything that may
	// keep or hand on what it evaluated (an argument, a branch, a result) still
	// gets a fresh value from evalNode. nil on a node made elsewhere.
	lit *Value

	keysUnobserved bool
}

// NewNode makes a node of type t.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
// Copy is a shallow copy of the node: its own Items slice, the same children
// (and the same slot-cache holder, which is safe to share).
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func (n *Node) Copy() *Node {
	if n == nil {
		return nil
	}
	cp := *n
	if n.Items != nil {
		cp.Items = make([]*Node, len(n.Items))
		copy(cp.Items, n.Items)
	}
	return &cp
}

func NewNode(t NodeType, pos Pos) *Node {
	n := &Node{T: t, Pos: pos}
	if t == NodeIndex {
		n.slotCache = new(atomic.Pointer[slotCache])
	}
	return n
}
