// Compiled program representation, dependency collection, and evaluation entry points.

package sel

import (
	"sort"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/internal/vocab"
)

type Program struct {
	source string
	ast    *Node
	// The optimised tree, built on first use. An atomic pointer rather than a
	// sync.Once: a panic inside the optimizer counts a Once as done and every
	// later Run would evaluate a nil tree. Here a failed build leaves the
	// pointer unset, so the panic reaches the caller and the next Run builds
	// again (two racing first Runs may both build; the trees are equivalent).
	physicalAst atomic.Pointer[Node]
}

// NewProgram wraps a syntax tree as a program.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func NewProgram(source string, ast *Node) *Program {
	return &Program{
		source: source,
		ast:    ast,
	}
}

func Compile(source string) (*Program, error) {
	var ast *Node
	if se := catchSel(func() { ast = parse(source) }); se != nil {
		return nil, se
	}
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

// AST is the program's syntax tree as parsed.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func (p *Program) AST() *Node {
	return p.ast
}

// PhysicalAST is the tree Run evaluates: the parsed tree after the in-memory optimizations.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func (p *Program) PhysicalAST() *Node {
	if t := p.physicalAst.Load(); t != nil {
		return t
	}
	t := OptimizeAstInMemory(p.ast)
	p.physicalAst.CompareAndSwap(nil, t)
	return p.physicalAst.Load()
}

func (p *Program) Run(ctx *Value) (result *Value, err error) {
	return p.runTree(nil, ctx)
}

// RunAsWritten evaluates the program's tree exactly as parsed, with no planning: the
// plain tree is the authority the physical tree is held to (SPEC 6.4), so the answer
// is the one Run gives. It is for a caller that evaluates a small tree once and throws
// the program away (the SQL layer validating a constant sub-expression), where the
// planner's rewrites cost more than the evaluation they would speed up.
func (p *Program) RunAsWritten(ctx *Value) (result *Value, err error) {
	return p.runTree(p.ast, ctx)
}

// runTree evaluates one tree of this program on a fresh context: the given one, or
// the physical tree when target is nil (built inside the recovered region, as Run
// always did, so a panic out of the optimizer is still reported as an error).
func (p *Program) runTree(target *Node, ctx *Value) (*Value, error) {
	var result *Value
	if se := catchSel(func() {
		c := newContext(ctx)
		if target == nil {
			target = p.PhysicalAST()
		}
		result = evalNode(target, c)
	}); se != nil {
		return nil, se
	}
	return result, nil
}

// Dependencies returns the variables the program reads before it has definitely
// assigned them (SPEC 8). The walk follows evaluation order and carries the set of
// names definitely assigned so far; an assignment counts as definite only if it runs
// whatever the data: never inside the right side of AND/OR/??/??? or in an aggregate
// body, and inside IF/COND only when every branch makes it. A program nested
// deeper than MAX_DEPTH compiles but cannot be walked: Dependencies then panics
// with a *SelError E_DEPTH, as the value accessors panic (package documentation).
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
	if depth > maxDepth {
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
		for i := len(idx) - 1; i >= 0; i-- {
			def = collectDependencies(idx[i], bound, reads, def, depth+1)
		}
		// The target of `op=` is read after the index expressions and BEFORE the right
		// side runs, so an assignment on the right is too late (SPEC 8).
		if node.S != "=" {
			if !bound[root.S] && !def[root.S] {
				reads[root.S] = true
			}
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
			for i, a := range node.Items {
				// The first argument always runs; COALESCE's later ones and GET/PATH's
				// default may not, so nothing they assign is definite afterwards.
				optional := (node.S == "COALESCE" && i > 0) || ((node.S == "GET" || node.S == "PATH") && i > 1)
				if optional {
					collectDependencies(a, bound, reads, copyNames(def), depth+1)
				} else {
					def = collectDependencies(a, bound, reads, def, depth+1)
				}
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
		if vocab.IsShortCircuit(node.S) {
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

// Eval compiles and runs a program once. Planning (the logical rewrites, constant
// folding and the math plan) costs about a quarter of a run-once total and only pays
// off when the program runs again or walks a collection, so a one-shot program that
// has neither an aggregate nor a pipeline stage is evaluated as written. The
// physical tree is a function of the AST alone and is held to the plain tree's answer
// (SPEC 6.4), so this changes timing and nothing else.
func Eval(source string, ctx *Value) (*Value, error) {
	prog, err := Compile(source)
	if err != nil {
		return nil, err
	}
	if !plansPay(prog.ast) {
		return prog.runTree(prog.ast, ctx)
	}
	return prog.Run(ctx)
}

// plansPay reports whether the program calls an aggregate or a pipeline operator,
// the only constructs whose planned form can beat evaluating the tree as written.
// Iterative: a flat chain as long as the source can be is as deep as it is long.
func plansPay(root *Node) bool {
	stack := []*Node{root}
	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if n == nil {
			continue
		}
		if n.T == NodeCall {
			if isPipelineOp(n.S) {
				return true
			}
			if spec := lookup(n.S); spec != nil && spec.Binds {
				return true
			}
		}
		stack = append(stack, n.L, n.R)
		stack = append(stack, n.Items...)
	}
	return false
}

func MustEval(source string, ctx *Value) *Value {
	v, err := Eval(source, ctx)
	if err != nil {
		panic(err)
	}
	return v
}

// FunctionNames lists every function a program can call, the builtins and
// those registered with RegisterFunction, sorted.
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
