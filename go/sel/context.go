// Context holds evaluation state including the variable root and aggregate scopes.

package sel

type PrefilterState struct {
	Conjuncts []*Node
	Dropped   bool
	Errored   bool
	Passed    []bool
}

type Context struct {
	Root                *Value
	Frames              []map[string]*Value
	Depth               int
	JoinPrefilter       *PrefilterState
	JoinPrefilterReport *PrefilterState
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
