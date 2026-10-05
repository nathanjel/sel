package sel

import (
	"strings"

	"github.com/nathanjel/sel/go/internal/utf8"
)

type joinTotalReq struct {
	Name    string
	Numeric bool
}

type joinConjunct struct {
	Node      *Node
	Fields    map[string]bool
	FieldOnly bool
	Total     []joinTotalReq
	HasTotal  bool
	Binder    string
}

type joinStage struct {
	Binder    string
	Conjuncts []joinConjunct
	Above     int
}

type joinSideFacts struct {
	Val      *Value
	Keys     map[string]bool
	Nullable bool
	Names    map[string]bool
	First    map[string]bool
	Facts    map[string]bool
}

type joinObligation struct {
	Key      *Node
	RowNames map[string]bool
	Outer    int
}

type joinPrefilter struct {
	Stages      []joinStage
	Deep        bool
	Above       []*joinSideFacts
	Obligations []joinObligation
}

type joinReport struct {
	Applied map[*Node]bool
	Errored bool
	Dropped bool
}

type stageStop struct {
	Stage    int
	Conjunct int
}

type joinApplied struct {
	Conjunct *joinConjunct
	Stage    *joinStage
	Right    bool
}

func joinPureSource(node *Node) bool {
	if node == nil {
		return true
	}
	switch node.T {
	case NodeVar, NodeNum, NodeText, NodeBool, NodeNull:
		return true
	case NodeIndex, NodeBin:
		return joinPureSource(node.L) && joinPureSource(node.R)
	case NodeUn:
		return joinPureSource(node.L)
	case NodeList:
		for _, item := range node.Items {
			if !joinPureSource(item) {
				return false
			}
		}
		return true
	case NodeCall:
		if node.S == "ABORT" {
			return false
		}
		for _, item := range node.Items {
			if !joinPureSource(item) {
				return false
			}
		}
		return true
	default:
		return false
	}
}

var textCompareOps = map[string]bool{
	"$==": true, "$!=": true, "$<": true, "$<=": true, "$>": true, "$>=": true,
}

var numCompareOps = map[string]bool{
	"==": true, "!=": true, "<": true, "<=": true, ">": true, ">=": true,
}

func leadingFieldConjuncts(body *Node, binder string) []joinConjunct {
	var conjuncts []*Node
	curr := body
	for curr != nil && curr.T == NodeBin && curr.S == "AND" {
		conjuncts = append(conjuncts, curr.R)
		curr = curr.L
	}
	if curr != nil {
		conjuncts = append(conjuncts, curr)
	}
	for i, j := 0, len(conjuncts)-1; i < j; i, j = i+1, j-1 {
		conjuncts[i], conjuncts[j] = conjuncts[j], conjuncts[i]
	}

	bareRead := func(n *Node) bool {
		return n != nil && n.T == NodeIndex && n.L != nil && n.L.T == NodeVar && n.L.S == binder &&
			n.R != nil && n.R.T == NodeText
	}

	var out []joinConjunct
	for _, c := range conjuncts {
		entry := joinConjunct{
			Node:   c,
			Fields: make(map[string]bool),
			Binder: binder,
		}

		var readsOnlyFields func(n *Node) bool
		readsOnlyFields = func(n *Node) bool {
			if n == nil {
				return true
			}
			switch n.T {
			case NodeIndex:
				if bareRead(n) {
					entry.Fields[utf8.AsciiUpper(n.R.S)] = true
					return true
				}
				if n.L != nil && n.L.T == NodeIndex {
					return readsOnlyFields(n.L) && readsOnlyFields(n.R)
				}
				return false
			case NodeNum, NodeText, NodeBool:
				return true
			case NodeBin:
				return readsOnlyFields(n.L) && readsOnlyFields(n.R)
			case NodeUn:
				return readsOnlyFields(n.L)
			default:
				return false
			}
		}

		entry.FieldOnly = readsOnlyFields(c) && len(entry.Fields) > 0
		if !entry.FieldOnly {
			entry.Fields = nil
		}

		if c != nil && c.T == NodeBin && (textCompareOps[c.S] || numCompareOps[c.S]) {
			numeric := numCompareOps[c.S]
			entry.HasTotal = true
			for _, operand := range []*Node{c.L, c.R} {
				if operand != nil && (operand.T == NodeNum || operand.T == NodeText) {
					if numeric && operand.T != NodeNum {
						entry.HasTotal = false
						break
					}
					continue
				}
				if !bareRead(operand) {
					entry.HasTotal = false
					break
				}
				entry.Total = append(entry.Total, joinTotalReq{Name: operand.R.S, Numeric: numeric})
			}
			if !entry.HasTotal {
				entry.Total = nil
			}
		}

		out = append(out, entry)
	}
	return out
}

func joinReadSelf(node *Node, names map[string]bool, binder string) *Node {
	if node == nil {
		return nil
	}
	if node.T == NodeIndex && node.L != nil && node.L.T == NodeVar && node.L.S == binder &&
		node.R != nil && node.R.T == NodeText && names[utf8.AsciiUpper(node.R.S)] {
		v := NewNode(NodeVar, node.Pos)
		v.S = binder
		return v
	}
	cp := copyNode(node)
	cp.mathPlan = nil
	cp.L = joinReadSelf(node.L, names, binder)
	cp.R = joinReadSelf(node.R, names, binder)
	for i, item := range node.Items {
		cp.Items[i] = joinReadSelf(item, names, binder)
	}
	return cp
}

func joinRowKeys(val *Value, bound []string) map[string]bool {
	keys := make(map[string]bool)
	for _, b := range bound {
		keys[utf8.AsciiUpper(b)] = true
	}
	shapes := make(map[*RecordShape]bool)
	if val != nil {
		for _, item := range val.valuesView() {
			if item.shape != nil {
				if shapes[item.shape] {
					continue
				}
				shapes[item.shape] = true
				for _, k := range item.shape.keys {
					keys[utf8.AsciiUpper(k)] = true
				}
			} else {
				for _, k := range item.Keys() {
					keys[utf8.AsciiUpper(k)] = true
				}
			}
		}
	}
	return keys
}

// firstCollectionItem is the first child of val, read in place, or nil.
func firstCollectionItem(val *Value) *Value {
	if val == nil {
		return nil
	}
	if val.storage != nil {
		if len(val.storage) > 0 {
			return val.storage[0]
		}
		return nil
	}
	if len(val.entries) > 0 {
		return val.entries[0].Val
	}
	return nil
}

func newJoinSideFacts(val *Value, keys map[string]bool, nullable bool, bound []string) *joinSideFacts {
	side := &joinSideFacts{
		Val:      val,
		Keys:     keys,
		Nullable: nullable,
		Names:    make(map[string]bool),
		First:    make(map[string]bool),
		Facts:    make(map[string]bool),
	}
	for _, b := range bound {
		if isPositionalBinder(b) || b == "_" {
			continue
		}
		side.Names[b] = true
		side.Names[strings.ToLower(b)] = true
	}
	first := firstCollectionItem(val)
	if first != nil {
		for _, k := range first.Keys() {
			side.First[utf8.AsciiUpper(k)] = true
		}
	}
	return side
}

func joinSideTotal(side *joinSideFacts, name string, numeric bool) bool {
	if !side.First[utf8.AsciiUpper(name)] || side.Nullable {
		return false
	}
	id := "T:" + name
	if numeric {
		id = "N:" + name
	}
	if v, ok := side.Facts[id]; ok {
		return v
	}
	ok := true
	vals := side.Val.valuesView()
	for _, row := range vals {
		v := row.Get(name)
		if v == nil || v.kind != KindText {
			ok = false
			break
		}
		if numeric {
			if !v.LooksNumeric() {
				ok = false
				break
			}
		}
	}
	side.Facts[id] = ok
	return ok
}

func joinSidePresent(side *joinSideFacts, name string) bool {
	id := "P:" + name
	if v, ok := side.Facts[id]; ok {
		return v
	}
	vals := side.Val.valuesView()
	ok := len(vals) > 0
	for _, row := range vals {
		if !row.Has(name) {
			ok = false
			break
		}
	}
	side.Facts[id] = ok
	return ok
}

func joinSideAny(side *joinSideFacts, name string) bool {
	if !side.First[utf8.AsciiUpper(name)] || side.Nullable {
		return false
	}
	id := "A:" + name
	if v, ok := side.Facts[id]; ok {
		return v
	}
	ok := true
	vals := side.Val.valuesView()
	for _, row := range vals {
		v := row.Get(name)
		if v == nil || v.IsNull() || isNestedRecord(v) {
			ok = false
			break
		}
	}
	side.Facts[id] = ok
	return ok
}

func isNestedRecord(v *Value) bool {
	return v.kind == KindNone && !v.isList && len(v.storage) > 0
}

func joinKeysSafe(obligations []joinObligation, left, right *joinSideFacts, above []*joinSideFacts) bool {
	for _, ob := range obligations {
		nBelow := 0
		if len(above) >= ob.Outer {
			nBelow = len(above) - ob.Outer
		}
		key := ob.Key
		if key == nil || key.T != NodeIndex || key.R == nil || key.R.T != NodeText || key.L == nil {
			return false
		}
		field := key.R.S
		obj := key.L
		if obj.T == NodeIndex && obj.L != nil && obj.L.T == NodeVar && ob.RowNames[obj.L.S] && obj.R != nil && obj.R.T == NodeText {
			member := obj.R.S
			if left.Names[member] {
				if !joinSidePresent(left, field) {
					return false
				}
				continue
			}
			var side *joinSideFacts
			if right.Names[member] {
				side = right
			}
			for i := 0; side == nil && i < nBelow; i++ {
				if above[i].Names[member] {
					side = above[i]
				}
			}
			if side != nil {
				if !joinSidePresent(side, field) {
					return false
				}
				continue
			}
			vals := left.Val.valuesView()
			allPresent := true
			for _, item := range vals {
				inner := item.Get(member)
				if inner == nil || inner.Get(field) == nil {
					allPresent = false
					break
				}
			}
			if !allPresent {
				return false
			}
			continue
		}
		if obj.T == NodeVar && ob.RowNames[obj.S] {
			upper := utf8.AsciiUpper(field)
			var owner *joinSideFacts
			owners := 0
			consider := func(s *joinSideFacts) {
				if s != nil && s.Keys[upper] {
					owner = s
					owners++
				}
			}
			consider(left)
			consider(right)
			for i := 0; i < nBelow; i++ {
				consider(above[i])
			}
			if owners != 1 || !joinSideAny(owner, field) {
				return false
			}
			continue
		}
		return false
	}
	return true
}

func joinTotality(reqs []joinTotalReq, left, right *joinSideFacts, above []*joinSideFacts) bool {
	for _, req := range reqs {
		key := utf8.AsciiUpper(req.Name)
		var owner *joinSideFacts
		owners := 0
		consider := func(s *joinSideFacts) {
			if s != nil && s.Keys[key] {
				owner = s
				owners++
			}
		}
		consider(left)
		consider(right)
		for _, s := range above {
			consider(s)
		}
		if owners != 1 || !joinSideTotal(owner, req.Name, req.Numeric) {
			return false
		}
	}
	return true
}

func joinStageWalk(
	stages []joinStage,
	ownedHere func(fields map[string]bool, stage joinStage) bool,
	totalHere func(reqs []joinTotalReq, stage joinStage) bool,
	rightHere func(fields map[string]bool, stage joinStage) bool,
) ([]joinApplied, *stageStop) {
	var applied []joinApplied
	for si := range stages {
		stage := &stages[si]
		for ci := range stage.Conjuncts {
			c := &stage.Conjuncts[ci]
			if c.FieldOnly && ownedHere(c.Fields, *stage) {
				applied = append(applied, joinApplied{Conjunct: c, Stage: stage, Right: false})
				continue
			}
			if c.FieldOnly && rightHere(c.Fields, *stage) {
				applied = append(applied, joinApplied{Conjunct: c, Stage: stage, Right: true})
				continue
			}
			if c.HasTotal && totalHere(c.Total, *stage) {
				continue
			}
			return applied, &stageStop{Stage: si, Conjunct: ci}
		}
	}
	return applied, nil
}

func joinTruncateStages(stages []joinStage, stop *stageStop) []joinStage {
	if stop == nil {
		return stages
	}
	out := make([]joinStage, stop.Stage)
	copy(out, stages[:stop.Stage])
	if stop.Conjunct > 0 {
		partial := joinStage{
			Binder:    stages[stop.Stage].Binder,
			Above:     stages[stop.Stage].Above,
			Conjuncts: make([]joinConjunct, stop.Conjunct),
		}
		copy(partial.Conjuncts, stages[stop.Stage].Conjuncts[:stop.Conjunct])
		out = append(out, partial)
	}
	return out
}
