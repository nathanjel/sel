// Context holds evaluation state including the variable root and aggregate scopes.

package sel

type Context struct {
	Root                *Value
	Frames              []map[string]*Value
	Depth               int
	JoinPrefilter       *JoinPrefilter
	JoinPrefilterReport *JoinReport
	// NoCopy names the one FILTER call whose kept rows may stay aliased to the
	// source, because its parent is about to read or copy them itself (GO-REG-1). It
	// is set by the parent just before it evaluates that argument and consumed by the
	// FILTER at once; like the join prefilter state it is cleared when evaluation
	// unwinds, so nothing else can find it there.
	NoCopy *Node
	// regs holds the math plans' register files (plan_regs.go), made on first use.
	regs *regPool
}

func NewContext(root *Value) *Context {
	if root == nil {
		root = NewNone()
	}
	return &Context{
		Root: root,
	}
}

func (c *Context) Lookup(name string) *Value {
	n := len(c.Frames)
	if n == 1 {
		if v, ok := c.Frames[0][name]; ok {
			return v
		}
	} else if n > 1 {
		for i := n - 1; i >= 0; i-- {
			if v, ok := c.Frames[i][name]; ok {
				return v
			}
		}
	}
	return c.Root.Get(name)
}

func (c *Context) IsBound(name string) bool {
	for i := len(c.Frames) - 1; i >= 0; i-- {
		if _, ok := c.Frames[i][name]; ok {
			return true
		}
	}
	return false
}

func (c *Context) PushFrame(mapping map[string]*Value) {
	c.Frames = append(c.Frames, mapping)
}

func (c *Context) PopFrame() {
	if len(c.Frames) > 0 {
		c.Frames = c.Frames[:len(c.Frames)-1]
	}
}
