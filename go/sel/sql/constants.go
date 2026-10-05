package sql

import (
	"fmt"
	"github.com/nathanjel/sel/go/internal/decimal"
	"strings"

	"github.com/nathanjel/sel/go/internal/limits"
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
	case "inline":
		return ModeInline
	default:
		// A mode nobody named is a mistake in the caller, refused at the name, not
		// rendered as inline SQL (which is what an unknown name used to become,
		// even for a fragment that had no slot to show the difference).
		panic(fmt.Sprintf("unknown render mode %q; the modes are inline, params and debug", name))
	}
}

func scope(bindings *Bindings) (map[string]bool, *sel.Value) {
	names := make(map[string]bool)
	root := sel.NewNull()
	if bindings == nil {
		return names, root
	}
	for _, name := range bindings.Names() {
		b := bindings.Get(name, Pos{})
		if b.kind != bindingKindValue {
			continue
		}
		v := b.val
		if v == nil || v.IsNone() || v.Size() > 0 {
			continue
		}
		names[name] = true
		root.Set(name, v)
	}
	return names, root
}

func isBinderName(node *sNode) bool {
	return node != nil && node.T == sNodeVar && !node.Grouped
}

type neededFields struct {
	All    bool
	Fields map[string]bool
}

func newNeededFields() *neededFields {
	return &neededFields{Fields: make(map[string]bool)}
}

func (nf *neededFields) IsNeeded() bool {
	return nf != nil && (nf.All || len(nf.Fields) > 0)
}

func textLiteralResults(node *sNode, depth int) bool {
	if node == nil || depth >= 180 || node.T != sNodeCall || (node.Str != "IF" && node.Str != "COND") {
		return false
	}
	args := node.Kids
	var results []*sNode
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
		if r == nil || (r.T != sNodeText && !textLiteralResults(r, depth+1)) {
			return false
		}
	}
	return true
}

func identityProjection(node *sNode, depth int) bool {
	if node == nil || depth >= 180 {
		return false
	}
	switch node.T {
	case sNodeVar, sNodeNum, sNodeText, sNodeBool, sNodeNull:
		return true
	case sNodeIndex:
		return identityProjection(node.Obj(), depth+1) && identityProjection(node.Idx(), depth+1)
	case sNodeCall:
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

func identityInputs(n *sNode, depth int) *neededFields {
	if n == nil || depth >= 180 {
		return &neededFields{All: true}
	}
	switch n.T {
	case sNodeNum, sNodeText, sNodeBool, sNodeNull:
		return newNeededFields()
	case sNodeVar:
		if n.Str == "_K" {
			return newNeededFields()
		}
		return &neededFields{All: true}
	case sNodeIndex:
		if n.Idx() == nil || n.Idx().T != sNodeText {
			return &neededFields{All: true}
		}
		if n.Obj() != nil && n.Obj().T == sNodeVar {
			nf := newNeededFields()
			nf.Fields[n.Idx().Str] = true
			return nf
		}
		return identityInputs(n.Obj(), depth+1)
	case sNodeCall:
		name := n.Str
		if name == "COUNT" || name == "LEN" || name == "BLEN" || name == "CANON" {
			return newNeededFields()
		}
		if textLiteralResults(n, depth) {
			return newNeededFields()
		}
		if name == "RECORD" || name == "LIST" {
			var items []*sNode
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
					return &neededFields{All: true}
				}
				for k := range f.Fields {
					out.Fields[k] = true
				}
			}
			return out
		}
	case sNodeList:
		out := newNeededFields()
		for _, item := range n.Kids {
			f := identityInputs(item, depth+1)
			if f.All {
				return &neededFields{All: true}
			}
			for k := range f.Fields {
				out.Fields[k] = true
			}
		}
		return out
	}
	return &neededFields{All: true}
}

func identityLossBeforeGrouping(node *sNode, needed *neededFields) bool {
	for node != nil && node.T == sNodeCall && len(node.Kids) > 0 {
		name := node.Str
		if needed.IsNeeded() && (name == "MAP" || (name == "BUCKET" && len(node.Kids) > 2)) {
			body := node.Kids[len(node.Kids)-1]
			values := []*sNode{body}
			if !needed.All && body.T == sNodeCall && body.Str == "RECORD" {
				found := make(map[string]bool)
				values = nil
				for i := 0; i+1 < len(body.Kids); i += 2 {
					k := body.Kids[i]
					if k.T == sNodeText && needed.Fields[k.Str] {
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
			needed = &neededFields{All: true}
		}
		if needed != nil && !needed.All && (name == "LINK" || name == "LINK_LEFT") && len(node.Kids) > 1 && node.Kids[1].T == sNodeVar {
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

// binderConstant prefixes the names of binders a constant aggregate introduced.
const binderConstant = "\x00"

func isConstant(n *sNode, bound map[string]bool) bool {
	if n == nil {
		return false
	}
	// A big subtree is a shared structure walked as a tree: a helper chain that
	// doubles at every step is 2^n nodes to this walk while being n to the
	// program. The answer for such a node is remembered (it depends only on the
	// names, and a caller asks with the same names throughout a translation).
	big := n.Size() > 4096
	if big && n.constMemo != 0 {
		return n.constMemo == 1
	}
	yes := isConstantNode(n, bound)
	if big {
		if yes {
			n.constMemo = 1
		} else {
			n.constMemo = 2
		}
	}
	return yes
}

func isConstantNode(n *sNode, bound map[string]bool) bool {
	switch n.T {
	case sNodeNum, sNodeText, sNodeBool:
		return true
	case sNodeVar:
		if bound == nil {
			return false
		}
		// A binder is not the value binding that happens to share its name: a
		// read stage 1 found bound is constant only if a constant aggregate bound
		// it (binderConstant), never because a binding of that name is a value.
		if n.VarScope == varScopeBound {
			return bound[binderConstant+n.Str]
		}
		return bound[n.Str]
	case sNodeUn:
		return isConstant(n.L(), bound)
	case sNodeBin:
		return isConstant(n.L(), bound) && isConstant(n.R(), bound)
	case sNodeIndex:
		return isConstant(n.Obj(), bound) && isConstant(n.Idx(), bound)
	case sNodeCList:
		return false
	case sNodeList:
		for _, item := range n.Kids {
			if !isConstant(item, bound) {
				return false
			}
		}
		return true
	case sNodeCall:
		return constantCall(n, bound)
	default:
		return false
	}
}

func constantCall(n *sNode, bound map[string]bool) bool {
	args := n.Kids
	if n.Spec == nil || !n.Spec.Binds {
		for _, a := range args {
			if !isConstant(a, bound) {
				return false
			}
		}
		return true
	}

	if len(args) == 0 || !isConstant(args[0], bound) {
		return false
	}

	inner := make(map[string]bool)
	for k, v := range bound {
		inner[k] = v
	}
	body := 1
	if len(args) >= 3 {
		if !isBinderName(args[1]) {
			return false
		}
		inner[args[1].Str] = true
		inner[binderConstant+args[1].Str] = true
		body = 2
	} else {
		inner["_"] = true
		inner[binderConstant+"_"] = true
	}

	for i := body; i < len(args); i++ {
		if !isConstant(args[i], inner) {
			return false
		}
	}
	return true
}

func validate(n *sNode, root *sel.Value) {
	// A shared subtree passes once (the walk meets the same node from both
	// operands of every doubling step): repeating SEL's evaluation of it costs
	// what its expansion does each time.
	if n.valid {
		return
	}
	validateNode(n, root)
	n.valid = true
}

func validateNode(n *sNode, root *sel.Value) {
	node := n.ToNode()
	if node == nil {
		return
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	_, err := prog.RunAsWritten(root)
	if err != nil {
		refuseAsSel(err, n)
	}
}

func requireNumeric(n *sNode, root *sel.Value) {
	if n.numeric {
		return
	}
	requireNumericNode(n, root)
	n.numeric = true
}

func requireNumericNode(n *sNode, root *sel.Value) {
	node := n.ToNode()
	if node == nil {
		return
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.RunAsWritten(root)
	if err != nil {
		refuseAsSel(err, n)
	}
	if refusal, selErr := catch(func() { val.Decimal(n.Pos) }); selErr != nil {
		refuseAsSel(selErr, n)
	} else if refusal != nil {
		panic(refusal)
	}
}

// numericTextConstant is the canonical spelling of a constant that is TEXT holding a
// number, and "" with false when it is anything else (or SEL refuses it: the operand's
// own translation reports that). In arithmetic SEL computes with such a text
// exactly, and MariaDB and MySQL would convert the quoted string to DOUBLE
// (`'0.1' + '0.2' = 0.3` is false there), so the translator spells it as the exact
// numeric literal it stands for.
func numericTextConstant(n *sNode, root *sel.Value) (text string, ok bool) {
	node := n.ToNode()
	if node == nil {
		return "", false
	}
	if root == nil {
		root = sel.NewNull()
	}
	val, err := sel.NewProgram("", node).RunAsWritten(root)
	if err != nil {
		return "", false
	}
	if !val.IsText() || !val.LooksNumeric() {
		return "", false
	}
	var d sel.Decimal
	if refusal, selErr := catch(func() { d = val.Decimal(n.Pos) }); refusal != nil {
		panic(refusal)
	} else if selErr != nil {
		return "", false
	}
	return decimal.Format(decimal.Make(d.Neg, d.Digits, int32(d.Scale))), true
}

func constantScale(n *sNode, root *sel.Value) int {
	node := n.ToNode()
	if node == nil {
		return 0
	}
	prog := sel.NewProgram("", node)
	if root == nil {
		root = sel.NewNull()
	}
	val, err := prog.RunAsWritten(root)
	if err != nil {
		refuseAsSel(err, n)
	}
	return val.Decimal(n.Pos).Scale
}

func refuseAsSel(err error, n *sNode) {
	if selErr, ok := err.(*sel.SelError); ok {
		pos := n.Pos
		if selErr.Line() > 0 {
			pos = sel.Pos{Line: selErr.Line(), Col: selErr.Col(), Offset: selErr.Offset()}
		}
		if selErr.Code == "E_DEPTH" {
			// The nesting is of the translated expression, which inlining built;
			// SEL evaluates the program as written. Blaming SEL would be false.
			refuse("E_SQL_DEPTH",
				fmt.Sprintf("this expression nests deeper than SEL will evaluate (%d) once its helpers are inlined, so there is nothing to translate", limits.MAX_DEPTH), pos)
		}
		refuse("E_SQL_INVALID",
			fmt.Sprintf("SEL rejects this expression (%s: %s), so there is nothing to translate; a database would answer something rather than fail", selErr.Code, selErr.Message),
			pos)
	}
	panic(err)
}
