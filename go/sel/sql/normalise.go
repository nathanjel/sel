package sql

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/sel"
)

func normalise(ast *sel.Node, constNames map[string]bool, root *sel.Value) *sNode {
	var stmts []*sel.Node
	if ast.T == sel.NodeSeq {
		stmts = append(stmts, ast.Items...)
	} else {
		stmts = []*sel.Node{ast}
	}
	result := stmts[len(stmts)-1]
	stmts = stmts[:len(stmts)-1]

	defs := make(map[string]*sNode)
	base := 0
	if ast.T == sel.NodeSeq {
		base = 1
	}

	// The names a statement may read as constants: the value bindings, then each
	// whole definition recorded as constant. Constant statements run in scratch,
	// a copy of the value bindings, as SEL runs them.
	constant := make(map[string]bool, len(constNames))
	for k, v := range constNames {
		constant[k] = v
	}
	scratch := sel.NewNull()
	if root != nil {
		scratch = root.Clone()
	}
	for _, s := range stmts {
		recordStmt(s, defs, constant, scratch, base+1)
	}

	return substituteNode(result, defs, nil, base)
}

func recordStmt(s *sel.Node, defs map[string]*sNode, constant map[string]bool, scratch *sel.Value, depth int) {
	if s.T != sel.NodeAssign {
		refuse("E_SQL_ASSIGN",
			"only assignments may come before the result expression; this computes a value nothing reads, which SQL has nowhere to put", s.Pos)
	}
	if s.S != "=" {
		refuse("E_SQL_ASSIGN",
			fmt.Sprintf("%s reads its own target before writing it, and SQL has nowhere to put the write; use = and a fresh name", s.S), s.Pos)
	}

	var keys []string
	t := s.L
	for t.T == sel.NodeIndex {
		k, isConst := constantKey(t.R)
		if !isConst {
			refuse("E_SQL_ASSIGN",
				"an assignment target may only be indexed by a constant here, because the shape has to be known before the query runs", t.R.Pos)
		}
		keys = append([]string{k}, keys...)
		t = t.L
	}

	if t.T != sel.NodeVar {
		refuse("E_SQL_ASSIGN", "assignment target is not a variable", s.Pos)
	}
	name := t.S

	value := substituteNode(s.R, defs, nil, depth)

	// Validated here, and only here, because after this the subtree may be gone:
	// a definition nothing reads is dropped, so `A = 1 / 0; TRUE` would translate
	// to TRUE where SEL raises E_DIV_ZERO. Every constant definition, whatever its
	// expanded size: its size is charged only where it is read (E_SQL_SIZE, in the
	// translator's walk; docs/internals/sql-translation.md §7.4), but SEL's verdict
	// on it is the program's (§11.4). So the statement is asked AS WRITTEN, run in
	// scratch, where every earlier constant definition already holds its value:
	// SEL reads a helper, it does not re-expand it, so this is linear where the
	// inlined tree is exponential in a doubling chain
	// (norm.size.unread-definition-past-the-budget-*).
	written := substituteNode(s.R, nil, nil, depth)
	isConst := isConstant(written, constant)
	if isConst {
		if _, err := sel.NewProgram("", s).RunAsWritten(scratch); err != nil {
			refuseAsSel(err, written)
		}
		// The inlined tree has the same verdict: the walk need not ask again.
		value.valid = true
	}
	if len(keys) == 0 && isConst {
		constant[name] = true
	} else {
		delete(constant, name)
	}

	if len(keys) == 0 {
		if defs[name] != nil {
			refuse("E_SQL_ASSIGN",
				fmt.Sprintf("%s is assigned more than once; SQL has no notion of a variable changing, so each name may be written once", name), s.Pos)
		}
		defs[name] = value
		return
	}

	if len(keys) > 1 {
		refuse("E_SQL_ASSIGN", "only one level of indexed assignment can be folded into a list here", s.Pos)
	}
	key := keys[0]
	if defs[name] == nil {
		defs[name] = newCList(s.Pos)
	}
	clist := defs[name]
	if clist.T != sNodeCList {
		refuse("E_SQL_ASSIGN",
			fmt.Sprintf("%s is assigned both as a whole and by index; use one or the other", name), s.Pos)
	}
	for _, entry := range clist.Entries {
		if entry.Key == key {
			refuse("E_SQL_ASSIGN", fmt.Sprintf("%s[%s] is assigned more than once", name, key), s.Pos)
		}
	}
	clist.Append(key, value)
}

// constantKey is the key a constant index spells, and whether the index is one:
// the empty text `""` is a constant key like any other, so "no key" is a
// separate result and not the empty string.
func constantKey(idx *sel.Node) (string, bool) {
	if idx.T == sel.NodeNum || idx.T == sel.NodeText {
		return idx.S, true
	}
	return "", false
}

func substituteNode(node *sel.Node, defs map[string]*sNode, bound []string, depth int) *sNode {
	d := depth + 1
	if d > limits.MAX_DEPTH {
		refuse("E_SQL_DEPTH",
			fmt.Sprintf("this expression nests deeper than SEL will evaluate (%d), so there is nothing to translate; the evaluator answers E_DEPTH for it", limits.MAX_DEPTH), node.Pos)
	}

	switch node.T {
	case sel.NodeVar:
		if node.BindingRead {
			leaf := leaf(node)
			leaf.VarScope = varScopeFree
			return leaf
		}
		for _, b := range bound {
			if b == node.S {
				leaf := leaf(node)
				leaf.VarScope = varScopeBound
				return leaf
			}
		}
		if def, ok := defs[node.S]; ok {
			return snapshot(def)
		}
		leaf := leaf(node)
		leaf.VarScope = varScopeFree
		return leaf

	case sel.NodeNum, sel.NodeText, sel.NodeBool, sel.NodeNull:
		return leaf(node)

	case sel.NodeAssign:
		refuse("E_SQL_ASSIGN",
			"an assignment here would have to happen while the query runs, and a SQL expression cannot assign", node.Pos)

	case sel.NodeSeq:
		refuse("E_SQL_ASSIGN",
			"a sequence here would evaluate and discard a value, which a SQL expression cannot do", node.Pos)

	case sel.NodeUn:
		return rewritten(node, []*sNode{substituteNode(node.L, defs, bound, d)})

	case sel.NodeBin:
		return rewritten(node, []*sNode{
			substituteNode(node.L, defs, bound, d),
			substituteNode(node.R, defs, bound, d),
		})

	case sel.NodeIndex:
		return rewritten(node, []*sNode{
			substituteNode(node.L, defs, bound, d),
			substituteNode(node.R, defs, bound, d),
		})

	case sel.NodeList:
		return rewritten(node, flattenNodes(node.Items, defs, bound, d))

	case sel.NodeCall:
		form := sel.BindingForm(node.S, node.Items, node.Spec)
		var inner []string
		if form != nil {
			inner = append(inner, bound...)
			inner = append(inner, form.Binds...)
		} else {
			inner = bound
		}

		var args []*sNode
		for i, arg := range node.Items {
			scope := manifest.ScopeOuter
			if form != nil && i < len(form.Scopes) {
				scope = form.Scopes[i]
			}
			if scope == manifest.ScopeBinder {
				// A bare name stays as written. Anything else is kept whole, so
				// the translator can refuse it where it stands: a child-less copy
				// of an index or call reached the translator as a node with no
				// parts and crashed it.
				if arg.T == sel.NodeVar {
					args = append(args, leaf(arg))
				} else {
					args = append(args, substituteNode(arg, defs, bound, d))
				}
			} else if scope == manifest.ScopeInner {
				args = append(args, substituteNode(arg, defs, inner, d))
			} else {
				args = append(args, substituteNode(arg, defs, bound, d))
			}
		}
		return rewritten(node, args)
	}

	return leaf(node)
}

func flattenNodes(items []*sel.Node, defs map[string]*sNode, bound []string, depth int) []*sNode {
	var out []*sNode
	for _, item := range items {
		s := substituteNode(item, defs, bound, depth)
		if s.T == sNodeList {
			out = append(out, s.Kids...)
		} else if s.T == sNodeCList {
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
// made after it; SEL's assignment copies (spec §3.4/§5.7), and so does this.
// The entries are values nobody mutates, so the copy is one level.
func snapshot(def *sNode) *sNode {
	if def == nil || def.T != sNodeCList {
		return def
	}
	entries := make([]cListEntry, len(def.Entries))
	copy(entries, def.Entries)
	return cList(def.Pos, entries)
}
