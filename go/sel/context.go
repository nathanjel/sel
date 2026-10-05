// Context holds evaluation state including the variable root and aggregate scopes.

package sel

// Context is the evaluation state a builtin defined with Define receives: the
// variables, and the binder frames a lazy builtin opens with PushFrame and
// closes with PopFrame. It is opaque otherwise.
type Context struct {
	root                *Value
	frames              []map[string]*Value
	depth               int
	joinPrefilter       *joinPrefilter
	joinPrefilterReport *joinReport
	// noCopy names the one FILTER call whose kept rows may stay aliased to the
	// source, because its parent is about to read or copy them itself. It
	// is set by the parent just before it evaluates that argument and consumed by the
	// FILTER at once; like the join prefilter state it is cleared when evaluation
	// unwinds, so nothing else can find it there.
	noCopy *Node
	// regs holds the math plans' register files (plan_regs.go), made on first use.
	regs *regPool
}

func newContext(root *Value) *Context {
	if root == nil {
		root = NewNone()
	}
	return &Context{
		root: root,
	}
}

func (c *Context) lookup(name string) *Value {
	n := len(c.frames)
	if n == 1 {
		if v, ok := c.frames[0][name]; ok {
			return v
		}
	} else if n > 1 {
		for i := n - 1; i >= 0; i-- {
			if v, ok := c.frames[i][name]; ok {
				return v
			}
		}
	}
	return c.root.Get(name)
}

func (c *Context) isBound(name string) bool {
	for i := len(c.frames) - 1; i >= 0; i-- {
		if _, ok := c.frames[i][name]; ok {
			return true
		}
	}
	return false
}

func (c *Context) PushFrame(mapping map[string]*Value) {
	c.frames = append(c.frames, mapping)
}

func (c *Context) PopFrame() {
	if len(c.frames) > 0 {
		c.frames = c.frames[:len(c.frames)-1]
	}
}
