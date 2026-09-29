package sql

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/sel"
)

func Normalise(ast *sel.Node, constNames map[string]bool, root *sel.Value) *SNode {
	var stmts []*sel.Node
	if ast.T == sel.NodeSeq {
		stmts = append(stmts, ast.Items...)
	} else {
		stmts = []*sel.Node{ast}
	}
	result := stmts[len(stmts)-1]
	stmts = stmts[:len(stmts)-1]

	defs := make(map[string]*SNode)
	base := 0
	if ast.T == sel.NodeSeq {
		base = 1
	}

	for _, s := range stmts {
		recordStmt(s, defs, constNames, root, base+1)
	}

	return substituteNode(result, defs, nil, base)
}

func recordStmt(s *sel.Node, defs map[string]*SNode, constNames map[string]bool, root *sel.Value, depth int) {
	if s.T != sel.NodeAssign {
		Refuse("E_SQL_ASSIGN",
			"only assignments may come before the result expression; this computes a value nothing reads, which SQL has nowhere to put", s.Pos)
	}
	if s.S != "=" {
		Refuse("E_SQL_ASSIGN",
			fmt.Sprintf("%s reads its own target before writing it, and SQL has nowhere to put the write; use = and a fresh name", s.S), s.Pos)
	}

	var keys []string
	t := s.L
	for t.T == sel.NodeIndex {
		k := constantKey(t.R)
		if k == "" {
			Refuse("E_SQL_ASSIGN",
				"an assignment target may only be indexed by a constant here, because the shape has to be known before the query runs", t.R.Pos)
		}
		keys = append([]string{k}, keys...)
		t = t.L
	}

	if t.T != sel.NodeVar {
		Refuse("E_SQL_ASSIGN", "assignment target is not a variable", s.Pos)
	}
	name := t.S

	value := substituteNode(s.R, defs, nil, depth)

	// A helper over the node budget is not walked here: IsConstant and SEL's
	// evaluator both cost what its expansion does. Reading it is what refuses
	// (E_SQL_SIZE, in the translator's walk); defining it is free.
	if value.Size() <= limits.MAX_SQL_NODES && IsConstant(value, constNames) {
		Validate(value, root)
	}

	if len(keys) == 0 {
		if defs[name] != nil {
			Refuse("E_SQL_ASSIGN",
				fmt.Sprintf("%s is assigned more than once; SQL has no notion of a variable changing, so each name may be written once", name), s.Pos)
		}
		defs[name] = value
		return
	}

	if len(keys) > 1 {
		Refuse("E_SQL_ASSIGN", "only one level of indexed assignment can be folded into a list here", s.Pos)
	}
	key := keys[0]
	if defs[name] == nil {
		defs[name] = NewCList(s.Pos)
	}
	clist := defs[name]
	if clist.T != SNodeCList {
		Refuse("E_SQL_ASSIGN",
			fmt.Sprintf("%s is assigned both as a whole and by index; use one or the other", name), s.Pos)
	}
	for _, entry := range clist.Entries {
		if entry.Key == key {
			Refuse("E_SQL_ASSIGN", fmt.Sprintf("%s[%s] is assigned more than once", name, key), s.Pos)
		}
	}
	clist.Append(key, value)
}

func constantKey(idx *sel.Node) string {
	if idx.T == sel.NodeNum || idx.T == sel.NodeText {
		return idx.S
	}
	return ""
}

func substituteNode(node *sel.Node, defs map[string]*SNode, bound []string, depth int) *SNode {
	d := depth + 1
	if d > limits.MAX_DEPTH {
		Refuse("E_SQL_DEPTH",
			fmt.Sprintf("this expression nests deeper than SEL will evaluate (%d), so there is nothing to translate; the evaluator answers E_DEPTH for it", limits.MAX_DEPTH), node.Pos)
	}

	switch node.T {
	case sel.NodeVar:
		for _, b := range bound {
			if b == node.S {
				leaf := Leaf(node)
				leaf.VarScope = VarScopeBound
				return leaf
			}
		}
		if def, ok := defs[node.S]; ok {
			return snapshot(def)
		}
		leaf := Leaf(node)
		leaf.VarScope = VarScopeFree
		return leaf

	case sel.NodeNum, sel.NodeText, sel.NodeBool, sel.NodeNull:
		return Leaf(node)

	case sel.NodeAssign:
		Refuse("E_SQL_ASSIGN",
			"an assignment here would have to happen while the query runs, and a SQL expression cannot assign", node.Pos)

	case sel.NodeSeq:
		Refuse("E_SQL_ASSIGN",
			"a sequence here would evaluate and discard a value, which a SQL expression cannot do", node.Pos)

	case sel.NodeUn:
		return Rewritten(node, []*SNode{substituteNode(node.L, defs, bound, d)})

	case sel.NodeBin:
		return Rewritten(node, []*SNode{
			substituteNode(node.L, defs, bound, d),
			substituteNode(node.R, defs, bound, d),
		})

	case sel.NodeIndex:
		return Rewritten(node, []*SNode{
			substituteNode(node.L, defs, bound, d),
			substituteNode(node.R, defs, bound, d),
		})

	case sel.NodeList:
		return Rewritten(node, flattenNodes(node.Items, defs, bound, d))

	case sel.NodeCall:
		form := sel.BindingForm(node.S, node.Items, node.Spec)
		var inner []string
		if form != nil {
			inner = append(inner, bound...)
			inner = append(inner, form.Binds...)
		} else {
			inner = bound
		}

		var args []*SNode
		for i, arg := range node.Items {
			scope := manifest.ScopeOuter
			if form != nil && i < len(form.Scopes) {
				scope = form.Scopes[i]
			}
			if scope == manifest.ScopeBinder {
				// A bare name stays as written. Anything else is kept whole, so
				// the translator can refuse it where it stands: a child-less copy
				// of an index or call reached the translator as a node with no
				// parts and crashed it (GO-C2).
				if arg.T == sel.NodeVar {
					args = append(args, Leaf(arg))
				} else {
					args = append(args, substituteNode(arg, defs, bound, d))
				}
			} else if scope == manifest.ScopeInner {
				args = append(args, substituteNode(arg, defs, inner, d))
			} else {
				args = append(args, substituteNode(arg, defs, bound, d))
			}
		}
		return Rewritten(node, args)
	}

	return Leaf(node)
}

func flattenNodes(items []*sel.Node, defs map[string]*SNode, bound []string, depth int) []*SNode {
	var out []*SNode
	for _, item := range items {
		s := substituteNode(item, defs, bound, depth)
		if s.T == SNodeList {
			out = append(out, s.Kids...)
		} else if s.T == SNodeCList {
			for _, e := range s.Entries {
				out = append(out, e.Val)
			}
		} else {
			out = append(out, s)
		}
	}
	return out
}

// snapshot is what reading a helper yields. An indexed assignment appends to
// the list it names in place, so a read that kept the pointer would see writes
// made after it; SEL's assignment copies (spec §3.4/§5.7), and so does this
// (GO-C4). The entries are values nobody mutates, so the copy is one level.
func snapshot(def *SNode) *SNode {
	if def == nil || def.T != SNodeCList {
		return def
	}
	entries := make([]CListEntry, len(def.Entries))
	copy(entries, def.Entries)
	return CList(def.Pos, entries)
}
