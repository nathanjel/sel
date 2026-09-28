// Function argument handling for built-in and host functions.

package sel

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/decimal"
)

type Args struct {
	nodes       []*Node
	recordShape *RecordShape
	name        string
	pos         Pos
	ctx         *Context
	vals        []*Value
}

func NewArgs(node *Node, ctx *Context) *Args {
	return &Args{
		nodes:       node.Items,
		recordShape: node.Shape,
		name:        node.S,
		pos:         node.Pos,
		ctx:         ctx,
		vals:        make([]*Value, len(node.Items)),
	}
}

func (a *Args) Count() int {
	return len(a.nodes)
}

func (a *Args) Name() string {
	return a.name
}

func (a *Args) Pos() Pos {
	return a.pos
}

func (a *Args) Ctx() *Context {
	return a.ctx
}

func (a *Args) Node(i int) *Node {
	return a.nodes[i]
}

func (a *Args) PosOf(i int) Pos {
	return a.nodes[i].Pos
}

func (a *Args) Val(i int) *Value {
	if a.vals[i] == nil {
		a.vals[i] = EvalNode(a.nodes[i], a.ctx)
	}
	return a.vals[i]
}

func (a *Args) EvalNode(n *Node) *Value {
	return EvalNode(n, a.ctx)
}

func (a *Args) Text(i int) string {
	return a.Val(i).AsText(a.PosOf(i))
}

func (a *Args) Bytes(i int) []byte {
	return a.Val(i).AsBytes(a.PosOf(i))
}

func (a *Args) Bool(i int) bool {
	return a.Val(i).AsBool(a.PosOf(i))
}

func (a *Args) Dec(i int) *decimal.Dec {
	return a.Val(i).AsDecimal(a.PosOf(i))
}

func (a *Args) Int(i int) int64 {
	d := a.Dec(i)
	if !decimal.IsInteger(d) {
		fail("E_NOT_INT", fmt.Sprintf("%s argument %d must be a whole number", a.name, i+1), a.PosOf(i))
	}
	return decimal.ToSafeInt(d)
}

func (a *Args) NonNegInt(i int) int64 {
	n := a.Int(i)
	if n < 0 {
		fail("E_RANGE", fmt.Sprintf("%s argument %d must not be negative", a.name, i+1), a.PosOf(i))
	}
	return n
}

func (a *Args) Symbol(i int) string {
	n := a.nodes[i]
	if n.T != NodeVar || n.Grouped {
		fail("E_EXPECT_SYMBOL", fmt.Sprintf("%s argument %d must be a plain name", a.name, i+1), n.Pos)
	}
	return n.S
}

func (a *Args) IsSymbol(i int) bool {
	n := a.nodes[i]
	return n.T == NodeVar && !n.Grouped
}

func (a *Args) RecordShape() *RecordShape {
	return a.recordShape
}
