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
	call        *Node
	vals        []*Value
	// valsBuf backs vals for a call of up to four arguments, so the Args and its
	// value slots are one allocation instead of two.
	valsBuf [4]*Value
}

func newArgs(node *Node, ctx *Context) *Args {
	a := &Args{
		nodes:       node.Items,
		recordShape: node.shape,
		name:        node.S,
		pos:         node.Pos,
		ctx:         ctx,
		call:        node,
	}
	if n := len(node.Items); n <= len(a.valsBuf) {
		a.vals = a.valsBuf[:n]
	} else {
		a.vals = make([]*Value, n)
	}
	return a
}

// has refuses an argument the call does not have: SPEC 8.1, a registered function
// that reads past the count gets E_BAD_ARG at the call, not an index-out-of-range
// panic that escapes Run.
func (a *Args) has(i int) {
	if i < 0 || i >= len(a.nodes) {
		fail("E_BAD_ARG", fmt.Sprintf("%s was called with %d argument(s) and read argument %d", a.name, len(a.nodes), i+1), a.pos)
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

func (a *Args) Node(i int) *Node {
	a.has(i)
	return a.nodes[i]
}

func (a *Args) PosOf(i int) Pos {
	a.has(i)
	return a.nodes[i].Pos
}

func (a *Args) Val(i int) *Value {
	a.has(i)
	if a.vals[i] == nil {
		if i == 0 {
			a.offerNoCopy()
		}
		a.vals[i] = evalNode(a.nodes[i], a.ctx)
	}
	return a.vals[i]
}

func (a *Args) EvalNode(n *Node) *Value {
	return evalNode(n, a.ctx)
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

func (a *Args) dec(i int) *decimal.Dec {
	return a.Val(i).AsDecimal(a.PosOf(i))
}

func (a *Args) Int(i int) int64 {
	return wholeArgument(a.dec(i), a.name, i+1, a.PosOf(i))
}

func (a *Args) NonNegInt(i int) int64 {
	n := a.Int(i)
	nonNegativeArgument(n, a.name, i+1, a.PosOf(i))
	return n
}

func (a *Args) Symbol(i int) string {
	a.has(i)
	n := a.nodes[i]
	if n.T != NodeVar || n.Grouped {
		fail("E_EXPECT_SYMBOL", fmt.Sprintf("%s argument %d must be a plain name", a.name, i+1), n.Pos)
	}
	return n.S
}

func (a *Args) IsSymbol(i int) bool {
	a.has(i)
	n := a.nodes[i]
	return n.T == NodeVar && !n.Grouped
}

func (a *Args) RecordShape() *RecordShape {
	return a.recordShape
}
