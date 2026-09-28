package sql

import (
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

type SqlKind int

const (
	KindUnknown SqlKind = iota
	KindNum
	KindText
	KindBool
	KindBin
	KindList
	KindStatement
)

func (k SqlKind) String() string {
	switch k {
	case KindNum:
		return "NUM"
	case KindText:
		return "TEXT"
	case KindBool:
		return "BOOL"
	case KindBin:
		return "BIN"
	case KindList:
		return "LIST"
	case KindStatement:
		return "STATEMENT"
	default:
		return "UNKNOWN"
	}
}

func KindFromName(name string) SqlKind {
	switch strings.ToUpper(strings.TrimSpace(name)) {
	case "NUM":
		return KindNum
	case "TEXT":
		return KindText
	case "BOOL":
		return KindBool
	case "BIN":
		return KindBin
	case "LIST":
		return KindList
	case "STATEMENT":
		return KindStatement
	default:
		return KindUnknown
	}
}

type Mode int

const (
	ModeInline Mode = iota
	ModeParams
	ModeDebug
)

func (m Mode) String() string {
	switch m {
	case ModeParams:
		return "params"
	case ModeDebug:
		return "debug"
	default:
		return "inline"
	}
}

func ModeFromName(name string) Mode {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "params":
		return ModeParams
	case "debug":
		return ModeDebug
	default:
		return ModeInline
	}
}

func SelKindToSqlKind(k sel.Kind) SqlKind {
	switch k {
	case sel.KindText:
		return KindText
	case sel.KindBool:
		return KindBool
	case sel.KindBin:
		return KindBin
	default:
		return KindUnknown
	}
}

func Scope(bindings *Bindings) (map[string]bool, *sel.Value) {
	names := make(map[string]bool)
	root := sel.NewNull()
	if bindings == nil {
		return names, root
	}
	for _, name := range bindings.Names() {
		b := bindings.Get(name, Pos{})
		if b.Kind != BindingKindValue {
			continue
		}
		v := b.Val
		if v == nil || v.IsNone() || v.Size() > 0 {
			continue
		}
		names[name] = true
		root.Set(name, v)
	}
	return names, root
}

func IsBinderName(node *SNode) bool {
	return node != nil && node.T == SNodeVar && !node.Grouped
}

type NeededFields struct {
	All    bool
	Fields map[string]bool
}

func newNeededFields() *NeededFields {
	return &NeededFields{Fields: make(map[string]bool)}
}

func (nf *NeededFields) IsNeeded() bool {
	return nf != nil && (nf.All || len(nf.Fields) > 0)
}

func textLiteralResults(node *SNode, depth int) bool {
	if node == nil || depth >= 180 || node.T != SNodeCall || (node.Str != "IF" && node.Str != "COND") {
		return false
	}
	args := node.Kids
	var results []*SNode
	if node.Str == "IF" {
		if len(args) < 2 {
			return false
		}
		results = args[1:]
	} else {
		if len(args) < 3 || len(args)%2 == 0 {
			return false
		}
		for i := 1; i < len(args); i += 2 {
			results = append(results, args[i])
		}
		results = append(results, args[len(args)-1])
	}
	for _, r := range results {
		if r == nil || (r.T != SNodeText && !textLiteralResults(r, depth+1)) {
			return false
		}
	}
	return true
}

func identityProjection(node *SNode, depth int) bool {
	if node == nil || depth >= 180 {
		return false
	}
	switch node.T {
	case SNodeVar, SNodeNum, SNodeText, SNodeBool, SNodeNull:
		return true
	case SNodeIndex:
		return identityProjection(node.Obj(), depth+1) && identityProjection(node.Idx(), depth+1)
	case SNodeCall:
		name := node.Str
		if name == "COUNT" || name == "LEN" || name == "BLEN" || name == "CANON" {
			return true
		}
		if textLiteralResults(node, depth) {
			return true
		}
		if name == "RECORD" {
			if len(node.Kids)%2 != 0 {
				return false
			}
			for i := 1; i < len(node.Kids); i += 2 {
				if !identityProjection(node.Kids[i], depth+1) {
					return false
				}
			}
			return true
		}
	}
	return false
}

func identityInputs(n *SNode, depth int) *NeededFields {
	if n == nil || depth >= 180 {
		return &NeededFields{All: true}
	}
	switch n.T {
	case SNodeNum, SNodeText, SNodeBool, SNodeNull:
		return newNeededFields()
	case SNodeVar:
		if n.Str == "_K" {
			return newNeededFields()
		}
		return &NeededFields{All: true}
	case SNodeIndex:
		if n.Idx() == nil || n.Idx().T != SNodeText {
			return &NeededFields{All: true}
		}
		if n.Obj() != nil && n.Obj().T == SNodeVar {
			nf := newNeededFields()
			nf.Fields[n.Idx().Str] = true
			return nf
		}
		return identityInputs(n.Obj(), depth+1)
	case SNodeCall:
		name := n.Str
		if name == "COUNT" || name == "LEN" || name == "BLEN" || name == "CANON" {
			return newNeededFields()
		}
		if textLiteralResults(n, depth) {
			return newNeededFields()
		}
		if name == "RECORD" || name == "LIST" {
			var items []*SNode
			if name == "RECORD" {
				for i := 1; i < len(n.Kids); i += 2 {
					items = append(items, n.Kids[i])
				}
			} else {
				items = n.Kids
			}
			out := newNeededFields()
			for _, item := range items {
				f := identityInputs(item, depth+1)
				if f.All {
					return &NeededFields{All: true}
				}
				for k := range f.Fields {
					out.Fields[k] = true
				}
			}
			return out
		}
	case SNodeList:
		out := newNeededFields()
		for _, item := range n.Kids {
			f := identityInputs(item, depth+1)
			if f.All {
				return &NeededFields{All: true}
			}
			for k := range f.Fields {
				out.Fields[k] = true
			}
		}
		return out
	}
	return &NeededFields{All: true}
}

func IdentityLossBeforeGrouping(node *SNode, needed *NeededFields) bool {
	for node != nil && node.T == SNodeCall && len(node.Kids) > 0 {
		name := node.Str
		if needed.IsNeeded() && (name == "MAP" || (name == "BUCKET" && len(node.Kids) > 2)) {
			body := node.Kids[len(node.Kids)-1]
			values := []*SNode{body}
			if !needed.All && body.T == SNodeCall && body.Str == "RECORD" {
				found := make(map[string]bool)
				values = nil
				for i := 0; i+1 < len(body.Kids); i += 2 {
					k := body.Kids[i]
					if k.T == SNodeText && needed.Fields[k.Str] {
						found[k.Str] = true
						values = append(values, body.Kids[i+1])
					}
				}
				for k := range needed.Fields {
					if !found[k] {
						return true
					}
				}
			}
			for _, v := range values {
				if !identityProjection(v, 0) {
					return true
				}
			}
			nextNeeded := newNeededFields()
			for _, v := range values {
				f := identityInputs(v, 0)
				if f.All {
					nextNeeded.All = true
				} else if !nextNeeded.All {
					for k := range f.Fields {
						nextNeeded.Fields[k] = true
					}
				}
			}
			needed = nextNeeded
		}

		if name == "BUCKET" {
			argIdx := 1
			if len(node.Kids) == 4 {
				argIdx = 2
			}
			needed = identityInputs(node.Kids[argIdx], 0)
		}
		if name == "DISTINCT" || name == "DEDUPE" {
			needed = &NeededFields{All: true}
		}
		if needed != nil && !needed.All && (name == "LINK" || name == "LINK_LEFT") && len(node.Kids) > 1 && node.Kids[1].T == SNodeVar {
			right := node.Kids[1].Str
			if len(node.Kids) == 5 {
				right = node.Kids[3].Str
			}
			rightUpper := utf8.AsciiUpper(right)
			filtered := make(map[string]bool)
			for k := range needed.Fields {
				if utf8.AsciiUpper(k) != rightUpper {
					filtered[k] = true
				}
			}
			needed.Fields = filtered
		}
		node = node.Kids[0]
	}
	return false
}

func IsConstant(n *SNode, bound map[string]bool) bool {
	if n == nil {
		return false
	}
	switch n.T {
	case SNodeNum, SNodeText, SNodeBool:
		return true
	case SNodeVar:
		return bound != nil && bound[n.Str]
	case SNodeUn:
		return IsConstant(n.L(), bound)
	case SNodeBin:
		return IsConstant(n.L(), bound) && IsConstant(n.R(), bound)
	case SNodeIndex:
		return IsConstant(n.Obj(), bound) && IsConstant(n.Idx(), bound)
	case SNodeCList:
		return false
	case SNodeList:
		for _, item := range n.Kids {
			if !IsConstant(item, bound) {
				return false
			}
		}
		return true
	case SNodeCall:
		return constantCall(n, bound)
	default:
		return false
	}
}

func constantCall(n *SNode, bound map[string]bool) bool {
	args := n.Kids
	if n.Spec == nil || !n.Spec.Binds {
		for _, a := range args {
			if !IsConstant(a, bound) {
				return false
			}
		}
		return true
	}

	if len(args) == 0 || !IsConstant(args[0], bound) {
		return false
	}

	inner := make(map[string]bool)
	for k, v := range bound {
		inner[k] = v
	}
	body := 1
	if len(args) >= 3 {
		if !IsBinderName(args[1]) {
			return false
		}
		inner[args[1].Str] = true
		body = 2
	} else {
		inner["_"] = true
	}

	for i := body; i < len(args); i++ {
		if !IsConstant(args[i], inner) {
			return false
		}
	}
	return true
}

func Validate(n *SNode, root *sel.Value) {
	node := n.ToNode()
	if node == nil {
		return
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	_, err := prog.Run(root)
	if err != nil {
		RefuseAsSel(err, n)
	}
}

func RequireNumeric(n *SNode, root *sel.Value) {
	node := n.ToNode()
	if node == nil {
		return
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.Run(root)
	if err != nil {
		RefuseAsSel(err, n)
	}
	func() {
		defer func() {
			if r := recover(); r != nil {
				if selErr, ok := r.(*sel.SelError); ok {
					RefuseAsSel(selErr, n)
				}
				panic(r)
			}
		}()
		val.AsDecimal(n.Pos)
	}()
}

func ConstantScale(n *SNode, root *sel.Value) int {
	node := n.ToNode()
	if node == nil {
		return 0
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.Run(root)
	if err != nil {
		RefuseAsSel(err, n)
	}
	dec := val.AsDecimal(n.Pos)
	if dec == nil {
		return 0
	}
	return int(dec.Scale)
}

func RefuseAsSel(err error, n *SNode) {
	if selErr, ok := err.(*sel.SelError); ok {
		pos := n.Pos
		if selErr.Line() > 0 {
			pos = sel.Pos{Line: selErr.Line(), Col: selErr.Col(), Offset: selErr.Offset()}
		}
		Refuse("E_SQL_INVALID",
			fmt.Sprintf("SEL rejects this expression (%s: %s), so there is nothing to translate; a database would answer something rather than fail", selErr.Code, selErr.Message),
			pos)
	}
	panic(err)
}
