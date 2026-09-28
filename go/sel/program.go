// Compiled program representation, dependency collection, and evaluation entry points.

package sel

import (
	"sort"
	"sync"

	"github.com/nathanjel/sel/go/internal/manifest"
)

type Program struct {
	source      string
	ast         *Node
	physicalAst *Node
	physOnce    sync.Once
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
	p.physOnce.Do(func() {
		p.physicalAst = OptimizeAST(p.ast)
	})
	return p.physicalAst
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

func (p *Program) Dependencies() []string {
	reads := make(map[string]bool)
	assigned := make(map[string]bool)
	bound := make(map[string]bool)
	collectDependencies(p.ast, bound, reads, assigned, 1)

	var out []string
	for r := range reads {
		if !assigned[r] {
			out = append(out, r)
		}
	}
	sort.Strings(out)
	return out
}

func collectDependencies(node *Node, bound map[string]bool, reads map[string]bool, assigned map[string]bool, depth int) {
	if node == nil {
		return
	}
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "expression nested too deeply", node.Pos)
	}

	switch node.T {
	case NodeVar:
		if !bound[node.S] {
			reads[node.S] = true
		}

	case NodeAssign:
		target := node.L
		for target.T == NodeIndex {
			collectDependencies(target.R, bound, reads, assigned, depth+1)
			target = target.L
		}
		if node.L.T != NodeVar || node.S != "=" {
			if !bound[target.S] {
				reads[target.S] = true
			}
		}
		assigned[target.S] = true
		collectDependencies(node.R, bound, reads, assigned, depth+1)

	case NodeCall:
		form := BindingForm(node.S, node.Items, node.Spec)
		if form == nil {
			for _, a := range node.Items {
				collectDependencies(a, bound, reads, assigned, depth+1)
			}
			return
		}
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
				collectDependencies(arg, inner, reads, assigned, depth+1)
			} else {
				collectDependencies(arg, bound, reads, assigned, depth+1)
			}
		}

	case NodeSeq, NodeList:
		for _, item := range node.Items {
			collectDependencies(item, bound, reads, assigned, depth+1)
		}

	case NodeIndex:
		collectDependencies(node.L, bound, reads, assigned, depth+1)
		collectDependencies(node.R, bound, reads, assigned, depth+1)

	case NodeBin:
		collectDependencies(node.L, bound, reads, assigned, depth+1)
		collectDependencies(node.R, bound, reads, assigned, depth+1)

	case NodeUn:
		collectDependencies(node.L, bound, reads, assigned, depth+1)
	}
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
