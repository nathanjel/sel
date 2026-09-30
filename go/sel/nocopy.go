package sel

import "github.com/nathanjel/sel/go/internal/manifest"

// Copy elision for a FILTER that feeds straight into a consumer (GO-REG-1).
//
// SPEC §3.4: FILTER copies the rows it keeps, so its result shares nothing with its
// source. That copy is needed when the result can be held, returned or changed. It is
// not when the FILTER is the first argument of a built-in that only reads the list
// (COUNT, SUM, ALL, ANY) or that copies whatever it keeps itself (MAP, the sorts, the
// TOP family): the intermediate list is then never seen by the program, and the copy
// of every kept row was the whole cost of `COUNT(FILTER(…))`, of `FILTER … .> TOP_BY`
// and the like. The elision is made only when nothing in the expression can change a
// value while the rows are read: no assignment and no registered host function (which
// may mutate what it is handed) anywhere in the consumer's other arguments or in the
// FILTER's own subtree. Then "the row at the time FILTER kept it" and "the row now"
// are the same value, and the result cannot be told apart.
var noCopyConsumers = map[string]bool{
	"COUNT": true, "SUM": true, "ALL": true, "ANY": true,
	"MAP": true, "SORT": true, "SORT_DESC": true, "SORT_BY": true,
	"TOP": true, "TOP_DESC": true, "TOP_BY": true,
}

// disableNoCopy turns the elision off; the tests run every program both ways.
var disableNoCopy bool

// noCopyAllowed reports whether a call of `consumer` with these argument nodes may
// read or copy the kept rows of the FILTER that is its first argument in place.
func noCopyAllowed(consumer string, nodes []*Node) bool {
	if disableNoCopy || !noCopyConsumers[consumer] || len(nodes) == 0 {
		return false
	}
	n0 := nodes[0]
	if n0 == nil || n0.T != NodeCall || n0.S != "FILTER" || !nodesCannotChangeValues(n0) {
		return false
	}
	for _, other := range nodes[1:] {
		if !nodesCannotChangeValues(other) {
			return false
		}
	}
	return true
}

// offerNoCopy marks the FILTER that is this call's first argument as allowed to keep
// its rows aliased, when the conditions above hold. Called just before argument 0 is
// evaluated.
func (a *Args) offerNoCopy() {
	if a.ctx == nil || a.call == nil || !noCopyAllowed(a.name, a.nodes) {
		return
	}
	a.ctx.NoCopy = a.nodes[0]
}

// nodesCannotChangeValues reports that evaluating the subtree changes no value: it
// holds no assignment and calls only built-in functions. Iterative, because a flat
// chain as long as the source is as deep as it is long.
func nodesCannotChangeValues(root *Node) bool {
	stack := []*Node{root}
	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if n == nil {
			continue
		}
		switch n.T {
		case NodeAssign:
			return false
		case NodeCall:
			if _, builtin := manifest.Builtins[n.S]; !builtin {
				return false
			}
		}
		stack = append(stack, n.L, n.R)
		stack = append(stack, n.Items...)
	}
	return true
}

// checkCopyDepth fails as CloneAt(depth) would for a value nested too deeply to be
// copied, without building the copy: a FILTER that keeps its rows aliased must still
// refuse exactly the rows it would have refused to copy (SPEC §6.4).
func (v *Value) checkCopyDepth(depth int, pos Pos) {
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", pos)
	}
	// A leaf (the usual child of a row) needs only the depth test, which the level
	// above has made for it: no call per field.
	if len(v.storage) > 0 {
		if depth+1 > MAX_DEPTH {
			fail("E_DEPTH", "value nested too deeply", pos)
		}
		for _, child := range v.storage {
			if len(child.storage) > 0 || len(child.entries) > 0 {
				child.checkCopyDepth(depth+1, pos)
			}
		}
	} else if len(v.entries) > 0 {
		if depth+1 > MAX_DEPTH {
			fail("E_DEPTH", "value nested too deeply", pos)
		}
		for _, e := range v.entries {
			if c := e.Val; len(c.storage) > 0 || len(c.entries) > 0 {
				c.checkCopyDepth(depth+1, pos)
			}
		}
	}
}

// keepRow is what FILTER puts in its result for a row it keeps: a copy one level down
// (SPEC §3.4), or the row itself when the consumer has been cleared to read it in
// place, after the same depth check the copy would have made.
func keepRow(item *Value, noCopy bool, pos Pos) *Value {
	if !noCopy {
		return item.CloneAt(2, pos)
	}
	item.checkCopyDepth(2, pos)
	return item
}
