// Compiled program representation, dependency collection, and evaluation entry points.

package sel

import (
	"sort"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/manifest"
)

type Program struct {
	source      string
	ast         *Node
	// The optimised tree, built on first use. An atomic pointer rather than a
	// sync.Once: a panic inside the optimizer counts a Once as done and every
	// later Run would evaluate a nil tree. Here a failed build leaves the
	// pointer unset, so the panic reaches the caller and the next Run builds
	// again (two racing first Runs may both build; the trees are equivalent).
	physicalAst atomic.Pointer[Node]
}

func NewProgram(source string, ast *Node) *Program {
	return &Program{
		source: source,
		ast:    ast,
	}
}

func Compile(source string) (prog *Program, err error) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SelError); ok {
				err = se
				return
			}
			panic(r)
		}
	}()
	ast := Parse(source)
	return NewProgram(source, ast), nil
}

func MustCompile(source string) *Program {
	p, err := Compile(source)
	if err != nil {
		panic(err)
	}
	return p
}

func (p *Program) Source() string {
	return p.source
}

func (p *Program) AST() *Node {
	return p.ast
}

func (p *Program) PhysicalAST() *Node {
	if t := p.physicalAst.Load(); t != nil {
		return t
	}
	t := OptimizeAST(p.ast)
	p.physicalAst.CompareAndSwap(nil, t)
	return p.physicalAst.Load()
}

func (p *Program) Run(ctx *Value) (result *Value, err error) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SelError); ok {
				err = se
				return
			}
			panic(r)
		}
	}()
	c := NewContext(ctx)
	target := p.PhysicalAST()
	return EvalNode(target, c), nil
}

// Dependencies returns the variables the program reads before it has definitely
// assigned them (SPEC 8). The walk follows evaluation order and carries the set of
// names definitely assigned so far; an assignment counts as definite only if it runs
// whatever the data: never inside the right side of AND/OR/??/??? or in an aggregate
// body, and inside IF/COND only when every branch makes it.
func (p *Program) Dependencies() []string {
	reads := make(map[string]bool)
	bound := make(map[string]bool)
	collectDependencies(p.ast, bound, reads, map[string]bool{}, 1)

	out := make([]string, 0, len(reads))
	for r := range reads {
		out = append(out, r)
	}
	sort.Strings(out)
	return out
}

func copyNames(m map[string]bool) map[string]bool {
	out := make(map[string]bool, len(m)+2)
	for k := range m {
		out[k] = true
	}
	return out
}

// meetNames is the intersection: what every path definitely assigned.
func meetNames(sets []map[string]bool) map[string]bool {
	out := make(map[string]bool)
	if len(sets) == 0 {
		return out
	}
	for k := range sets[0] {
		all := true
		for _, o := range sets[1:] {
			if !o[k] {
				all = false
				break
			}
		}
		if all {
			out[k] = true
		}
	}
	return out
}

// collectDependencies walks node in evaluation order. def is the set of names
// definitely assigned before it; the return value is the set after it. def is
// never mutated in place, so a caller that needs the "before" set keeps it.
func collectDependencies(node *Node, bound map[string]bool, reads map[string]bool, def map[string]bool, depth int) map[string]bool {
	if node == nil {
		return def
	}
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "expression nested too deeply", node.Pos)
	}

	switch node.T {
	case NodeVar:
		if !bound[node.S] && !def[node.S] {
			reads[node.S] = true
		}
		return def

	case NodeAssign:
		// The index expressions of a target run once, in order, before the right side
		// (SPEC 5.7); a compound assignment also reads its target first.
		root := node.L
		var idx []*Node
		for root.T == NodeIndex {
			idx = append(idx, root.R)
			root = root.L
		}
		if node.S != "=" {
			if !bound[root.S] && !def[root.S] {
				reads[root.S] = true
			}
		}
		for i := len(idx) - 1; i >= 0; i-- {
			def = collectDependencies(idx[i], bound, reads, def, depth+1)
		}
		def = collectDependencies(node.R, bound, reads, def, depth+1)
		if !def[root.S] {
			def = copyNames(def)
			def[root.S] = true
		}
		return def

	case NodeCall:
		switch node.S {
		case "IF":
			// IF(cond, then [, else]): the condition always runs; a branch is definite
			// only if both make the assignment (a missing else makes none).
			if len(node.Items) >= 2 {
				def = collectDependencies(node.Items[0], bound, reads, def, depth+1)
				thenDef := collectDependencies(node.Items[1], bound, reads, def, depth+1)
				elseDef := def
				if len(node.Items) >= 3 {
					elseDef = collectDependencies(node.Items[2], bound, reads, def, depth+1)
				}
				return meetNames([]map[string]bool{thenDef, elseDef})
			}
		case "COND":
			// COND(c1, v1, c2, v2, ..., [default]): conditions run in order until one
			// matches; with no default, the fall-through path assigns only what the
			// conditions did.
			var paths []map[string]bool
			cur := def
			n := len(node.Items)
			for i := 0; i+1 < n; i += 2 {
				cur = collectDependencies(node.Items[i], bound, reads, cur, depth+1)
				paths = append(paths, collectDependencies(node.Items[i+1], bound, reads, cur, depth+1))
			}
			if n%2 == 1 {
				paths = append(paths, collectDependencies(node.Items[n-1], bound, reads, cur, depth+1))
			} else {
				paths = append(paths, cur)
			}
			if len(paths) > 0 {
				return meetNames(paths)
			}
			return cur
		}

		form := BindingForm(node.S, node.Items, node.Spec)
		if form == nil {
			for _, a := range node.Items {
				def = collectDependencies(a, bound, reads, def, depth+1)
			}
			return def
		}
		// Which arguments run inside the binder, and what they see, is decided once, by
		// the manifest's forms. An argument outside the binder runs once, in order; one
		// inside runs per element (possibly zero times), so what it assigns is never
		// definite afterwards.
		var inner map[string]bool
		for i, arg := range node.Items {
			scope := form.Scopes[i]
			if scope == manifest.ScopeBinder {
				continue
			}
			if scope == manifest.ScopeInner {
				if inner == nil {
					inner = make(map[string]bool, len(bound)+len(form.Binds))
					for k, v := range bound {
						inner[k] = v
					}
					for _, b := range form.Binds {
						inner[b] = true
					}
				}
				collectDependencies(arg, inner, reads, copyNames(def), depth+1)
			} else {
				def = collectDependencies(arg, bound, reads, def, depth+1)
			}
		}
		return def

	case NodeSeq, NodeList:
		for _, item := range node.Items {
			def = collectDependencies(item, bound, reads, def, depth+1)
		}
		return def

	case NodeIndex:
		def = collectDependencies(node.L, bound, reads, def, depth+1)
		return collectDependencies(node.R, bound, reads, def, depth+1)

	case NodeBin:
		def = collectDependencies(node.L, bound, reads, def, depth+1)
		switch node.S {
		case "AND", "OR", "??", "???":
			// The right side may not run, so nothing it assigns is definite.
			collectDependencies(node.R, bound, reads, copyNames(def), depth+1)
			return def
		}
		return collectDependencies(node.R, bound, reads, def, depth+1)

	case NodeUn:
		return collectDependencies(node.L, bound, reads, def, depth+1)
	}
	return def
}

func Eval(source string, ctx *Value) (*Value, error) {
	prog, err := Compile(source)
	if err != nil {
		return nil, err
	}
	return prog.Run(ctx)
}

func MustEval(source string, ctx *Value) *Value {
	v, err := Eval(source, ctx)
	if err != nil {
		panic(err)
	}
	return v
}

func FunctionNames() []string {
	registryMu.RLock()
	defer registryMu.RUnlock()
	names := make([]string, 0, len(funcTable))
	for name := range funcTable {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
