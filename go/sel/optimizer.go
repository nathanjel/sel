// AST optimizer and physical tree planning.

package sel

import (
	"bytes"
	"math"
	"math/big"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/mathops"
)

var pipelineOps = map[string]bool{
	"FILTER":      true,
	"BUCKET":      true,
	"SELECT_COLS": true,
	"MAP":         true,
	"DISTINCT":    true,
	"DEDUPE":      true,
	"TAKE":        true,
	"DROP":        true,
	"SORT":        true,
	"SORT_DESC":   true,
	"SORT_BY":     true,
	"TOP":         true,
	"TOP_DESC":    true,
	"TOP_BY":      true,
	"LINK":        true,
	"LINK_LEFT":   true,
}

// IsPipelineOp reports whether a function name is one of the pipeline operators.
func IsPipelineOp(name string) bool {
	return pipelineOps[name]
}

func copyNode(n *Node) *Node {
	if n == nil {
		return nil
	}
	cp := *n
	if n.Items != nil {
		cp.Items = make([]*Node, len(n.Items))
		copy(cp.Items, n.Items)
	}
	return &cp
}

// UnwindPipeline decomposes a nested pipeline call into its base source and sequence of steps.
func UnwindPipeline(root *Node) (*Node, []*Node) {
	var steps []*Node
	curr := root
	for curr != nil && curr.T == NodeCall && IsPipelineOp(curr.S) && len(curr.Items) > 0 {
		steps = append(steps, curr)
		curr = curr.Items[0]
	}
	for i, j := 0, len(steps)-1; i < j; i, j = i+1, j-1 {
		steps[i], steps[j] = steps[j], steps[i]
	}
	return curr, steps
}

// BuildPipeline reassembles a pipeline from a source node and step nodes.
func BuildPipeline(source *Node, steps []*Node) *Node {
	curr := source
	for _, step := range steps {
		next := copyNode(step)
		next.Items = make([]*Node, len(step.Items))
		next.Items[0] = curr
		copy(next.Items[1:], step.Items[1:])
		curr = next
	}
	return curr
}

func optBool(val bool, pos Pos) *Node {
	n := NewNode(NodeBool, pos)
	n.B = val
	return n
}

func optNum(val string, dec *decimal.Dec, pos Pos) *Node {
	n := NewNode(NodeNum, pos)
	n.S = val
	n.Dec = dec
	return n
}

func isLiteral(n *Node) bool {
	return n != nil && (n.T == NodeNum || n.T == NodeText || n.T == NodeBool || n.T == NodeNull)
}

func hoistLiteral(child *Node, pos Pos) *Node {
	cp := copyNode(child)
	cp.Pos = pos
	return cp
}

func tryDec(fn func() *decimal.Dec) (res *decimal.Dec) {
	defer func() {
		if r := recover(); r != nil {
			res = nil
		}
	}()
	return fn()
}

func optFold(node *Node) *Node {
	if node == nil {
		return nil
	}
	if node.T == NodeUn && node.L != nil {
		if node.S == "NOT" && node.L.T == NodeBool {
			return optBool(!node.L.B, node.Pos)
		}
		if node.S == "NEG" && node.L.T == NodeNum {
			dec := node.L.Dec
			if dec == nil {
				dec = tryDec(func() *decimal.Dec {
					return decimal.Parse(node.L.S, node.Pos, fail)
				})
			}
			if dec != nil {
				neg := decimal.Negate(dec)
				return optNum(decimal.Format(neg), neg, node.Pos)
			}
		}
		return node
	}
	if node.T == NodeBin && node.L != nil && node.R != nil {
		left, right := node.L, node.R
		if node.S == "AND" {
			if left.T == NodeBool && !left.B {
				return optBool(false, node.Pos)
			}
			if left.T == NodeBool && right.T == NodeBool {
				return optBool(left.B && right.B, node.Pos)
			}
		}
		if node.S == "OR" {
			if left.T == NodeBool && left.B {
				return optBool(true, node.Pos)
			}
			if left.T == NodeBool && right.T == NodeBool {
				return optBool(left.B || right.B, node.Pos)
			}
		}
		if left.T == NodeNum && right.T == NodeNum {
			switch node.S {
			case "+", "-", "*", "/", "%":
				decL := left.Dec
				if decL == nil {
					decL = tryDec(func() *decimal.Dec {
						return decimal.Parse(left.S, node.Pos, fail)
					})
				}
				decR := right.Dec
				if decR == nil {
					decR = tryDec(func() *decimal.Dec {
						return decimal.Parse(right.S, node.Pos, fail)
					})
				}
				if decL != nil && decR != nil {
					res := tryDec(func() *decimal.Dec {
						switch node.S {
						case "+":
							return decimal.Add(decL, decR, node.Pos, fail)
						case "-":
							return decimal.Sub(decL, decR, node.Pos, fail)
						case "*":
							return decimal.Mul(decL, decR, node.Pos, fail)
						case "/":
							return decimal.Div(decL, decR, node.Pos, fail)
						case "%":
							return decimal.Mod(decL, decR, node.Pos, fail)
						default:
							return nil
						}
					})
					if res != nil {
						return optNum(decimal.Format(res), res, node.Pos)
					}
				}
			case "==", "!=", "<", "<=", ">", ">=":
				decL := left.Dec
				if decL == nil {
					decL = tryDec(func() *decimal.Dec {
						return decimal.Parse(left.S, node.Pos, fail)
					})
				}
				decR := right.Dec
				if decR == nil {
					decR = tryDec(func() *decimal.Dec {
						return decimal.Parse(right.S, node.Pos, fail)
					})
				}
				if decL != nil && decR != nil {
					c := decimal.Cmp(decL, decR)
					var b bool
					switch node.S {
					case "==":
						b = (c == 0)
					case "!=":
						b = (c != 0)
					case "<":
						b = (c < 0)
					case "<=":
						b = (c <= 0)
					case ">":
						b = (c > 0)
					case ">=":
						b = (c >= 0)
					}
					return optBool(b, node.Pos)
				}
			}
		}
		if left.T == NodeText && right.T == NodeText {
			switch node.S {
			case "$==", "$!=", "$<", "$<=", "$>", "$>=":
				c := bytes.Compare([]byte(left.S), []byte(right.S))
				var b bool
				switch node.S {
				case "$==":
					b = (c == 0)
				case "$!=":
					b = (c != 0)
				case "$<":
					b = (c < 0)
				case "$<=":
					b = (c <= 0)
				case "$>":
					b = (c > 0)
				case "$>=":
					b = (c >= 0)
				}
				return optBool(b, node.Pos)
			}
		}
		return node
	}
	if node.T == NodeCall && node.S == "IF" && len(node.Items) == 3 && node.Items[0].T == NodeBool {
		var branch *Node
		if node.Items[0].B {
			branch = node.Items[1]
		} else {
			branch = node.Items[2]
		}
		if isLiteral(branch) {
			return hoistLiteral(branch, node.Pos)
		}
		return node
	}
	return node
}

func upperName(s string) string {
	return strings.ToUpper(s)
}

func optFieldRefs(node *Node, binder string) []string {
	var refs []string
	seen := make(map[string]bool)
	var walk func(n *Node)
	walk = func(n *Node) {
		if n == nil {
			return
		}
		if n.T == NodeIndex && n.L != nil && n.R != nil && n.L.T == NodeVar && n.R.T == NodeText {
			v := upperName(n.L.S)
			if binder == "" || v == upperName(binder) || v == "_" || v == "_1" || v == "_2" {
				if !seen[n.R.S] {
					seen[n.R.S] = true
					refs = append(refs, n.R.S)
				}
			}
		}
		if n.L != nil {
			walk(n.L)
		}
		if n.R != nil {
			walk(n.R)
		}
		for _, child := range n.Items {
			walk(child)
		}
	}
	walk(node)
	return refs
}

func optReadsVar(node *Node, names []string) bool {
	wanted := make(map[string]bool)
	for _, n := range names {
		wanted[upperName(n)] = true
	}
	found := false
	var walk func(n *Node)
	walk = func(n *Node) {
		if n == nil || found {
			return
		}
		if n.T == NodeVar && wanted[upperName(n.S)] {
			found = true
			return
		}
		if n.T == NodeIndex && n.L != nil && n.R != nil && n.L.T == NodeVar && n.R.T == NodeText {
			return
		}
		if n.L != nil {
			walk(n.L)
		}
		if n.R != nil {
			walk(n.R)
		}
		for _, child := range n.Items {
			walk(child)
		}
	}
	walk(node)
	return found
}

func optReadsRowOrKey(node *Node, binder string) bool {
	return optReadsVar(node, []string{binder, "_", "_1", "_2", "_K"})
}

func optStepReadsKey(step *Node) bool {
	for i := 1; i < len(step.Items); i++ {
		if optReadsVar(step.Items[i], []string{"_K"}) {
			return true
		}
	}
	return false
}

func optKeysRenumberedBy(step *Node) bool {
	return step != nil && step.S != "FILTER" && !optStepReadsKey(step)
}

func optSourceIsList(source *Node) bool {
	return source != nil && (source.T == NodeList || (source.T == NodeCall && (source.S == "LIST" || source.S == "RECORD")))
}

func optStepArgFolds(step *Node, index int) bool {
	sortCount := 0
	if step.S == "SORT_BY" {
		sortCount = len(step.Items)
	} else if step.S == "TOP_BY" {
		sortCount = len(step.Items) - 1
	}
	return !(sortCount == 3 && index == 2 && step.Items[1].T == NodeVar && !step.Items[1].Grouped)
}

type optMapInfo struct {
	binder          string
	body            *Node
	explicitBinder  bool
}

type optFilterInfo struct {
	binder         string
	predicate      *Node
	explicitBinder bool
	valid          bool
}

func getOptMapInfo(step *Node) optMapInfo {
	args := step.Items
	explicit := len(args) == 3 && args[1].T == NodeVar && !args[1].Grouped
	b := "_"
	if explicit {
		b = args[1].S
	}
	var body *Node
	if explicit {
		body = args[2]
	} else if len(args) > 1 {
		body = args[1]
	}
	return optMapInfo{binder: b, body: body, explicitBinder: explicit}
}

func getOptFilterInfo(step *Node) optFilterInfo {
	args := step.Items
	explicit := len(args) == 3 && args[1].T == NodeVar && !args[1].Grouped
	b := "_"
	if explicit {
		b = args[1].S
	}
	var pred *Node
	if explicit {
		pred = args[2]
	} else if len(args) > 1 {
		pred = args[1]
	}
	valid := len(args) == 2 || explicit
	return optFilterInfo{binder: b, predicate: pred, explicitBinder: explicit, valid: valid}
}

func optMapPassthroughs(step *Node) []string {
	info := getOptMapInfo(step)
	if info.body == nil || info.body.T != NodeCall || info.body.S != "RECORD" {
		return nil
	}
	var fields []string
	for i := 0; i+1 < len(info.body.Items); i += 2 {
		k := info.body.Items[i]
		v := info.body.Items[i+1]
		if k.T == NodeText && v.T == NodeIndex && v.L != nil && v.R != nil &&
			v.L.T == NodeVar && v.R.T == NodeText &&
			upperName(v.L.S) == upperName(info.binder) && v.R.S == k.S {
			fields = append(fields, k.S)
		}
	}
	return fields
}

func optMapHasComputed(step *Node) bool {
	info := getOptMapInfo(step)
	if info.body == nil || info.body.T != NodeCall || info.body.S != "RECORD" {
		return true
	}
	return len(optMapPassthroughs(step))*2 != len(info.body.Items)
}

type optSortInfo struct {
	binder string
	key    *Node
}

func getOptSortInfo(step *Node) optSortInfo {
	args := step.Items
	count := len(args)
	info := optSortInfo{binder: "_"}
	if step.S == "SORT" || step.S == "SORT_DESC" {
		if count == 1 {
			return info
		}
		if count == 3 && args[1].T == NodeVar && !args[1].Grouped {
			info.binder = args[1].S
			info.key = args[2]
		} else {
			info.key = args[1]
		}
	} else if step.S == "TOP" || step.S == "TOP_DESC" {
		if count == 2 {
			return info
		}
		sortCount := count - 1
		if sortCount == 3 && args[1].T == NodeVar && !args[1].Grouped {
			info.binder = args[1].S
			info.key = args[2]
		} else {
			info.key = args[1]
		}
	} else if step.S == "SORT_BY" || step.S == "TOP_BY" {
		sortCount := count
		if step.S == "TOP_BY" {
			sortCount = count - 1
		}
		if sortCount == 2 || (sortCount == 3 && args[2].T == NodeText) {
			info.key = args[1]
		} else if count > 2 && args[1].T == NodeVar && !args[1].Grouped {
			info.binder = args[1].S
			info.key = args[2]
		}
	}
	return info
}

func optSelectFields(step *Node) []string {
	var out []string
	for i := 1; i < len(step.Items); i++ {
		arg := step.Items[i]
		if arg.T == NodeList {
			for _, item := range arg.Items {
				if item.T == NodeText {
					out = append(out, item.S)
				}
			}
		} else if arg.T == NodeText {
			out = append(out, arg.S)
		}
	}
	return out
}

func optNumericLiteral(node *Node) (int64, bool) {
	if node == nil || node.T != NodeNum {
		return 0, false
	}
	dec := node.Dec
	if dec == nil {
		dec = tryDec(func() *decimal.Dec {
			return decimal.Parse(node.S, node.Pos, fail)
		})
	}
	if dec == nil || dec.Neg || !decimal.IsInteger(dec) {
		return 0, false
	}
	t := decimal.Trunc(dec)
	if !t.Digits.IsInt64() {
		return 0, false
	}
	v := t.Digits.Int64()
	if v < 0 {
		return 0, false
	}
	return v, true
}

func optRenameVar(node *Node, oldName string, newName string) *Node {
	if node == nil {
		return nil
	}
	cp := copyNode(node)
	if cp.T == NodeVar && upperName(cp.S) == upperName(oldName) {
		cp.S = newName
	}
	if cp.L != nil {
		cp.L = optRenameVar(cp.L, oldName, newName)
	}
	if cp.R != nil {
		cp.R = optRenameVar(cp.R, oldName, newName)
	}
	for i, item := range cp.Items {
		cp.Items[i] = optRenameVar(item, oldName, newName)
	}
	return cp
}

var safeLogicalOps = map[string]bool{
	"==": true, "!=": true, "<": true, "<=": true, ">": true, ">=": true,
	"$==": true, "$!=": true, "$<": true, "$<=": true, "$>": true, "$>=": true,
	"AND": true, "OR": true, "+": true, "-": true, "*": true,
}

func optCannotRaise(node *Node, binder string, logical bool) bool {
	if node == nil {
		return true
	}
	switch node.T {
	case NodeNum, NodeText, NodeBool, NodeNull:
		return true
	case NodeVar:
		name := upperName(node.S)
		return name == "_K" || name == upperName(binder)
	case NodeIndex:
		return logical && node.L != nil && node.L.T == NodeVar && upperName(node.L.S) == upperName(binder) &&
			node.R != nil && node.R.T == NodeText
	case NodeBin:
		return logical && safeLogicalOps[node.S] &&
			optCannotRaise(node.L, binder, logical) &&
			optCannotRaise(node.R, binder, logical)
	case NodeUn:
		return logical && node.S == "NOT" && optCannotRaise(node.L, binder, logical)
	default:
		return false
	}
}

func optMapCannotRaise(step *Node, logical bool) bool {
	info := getOptMapInfo(step)
	if info.body != nil && info.body.T == NodeCall && info.body.S == "RECORD" {
		for i := 0; i < len(info.body.Items); i++ {
			arg := info.body.Items[i]
			if i%2 == 0 {
				if arg.T != NodeText {
					return false
				}
			} else {
				if !optCannotRaise(arg, info.binder, logical) {
					return false
				}
			}
		}
		return true
	}
	return optCannotRaise(info.body, info.binder, logical)
}

func optLogicalSteps(source *Node, current []*Node, logical bool) []*Node {
	changed := true
	for changed {
		changed = false
		var next []*Node
		for i := 0; i < len(current); {
			first := current[i]
			var second, third *Node
			if i+1 < len(current) {
				second = current[i+1]
			}
			if i+2 < len(current) {
				third = current[i+2]
			}

			// TAKE + TAKE
			if second != nil && first.S == "TAKE" && second.S == "TAKE" && len(first.Items) == 2 && len(second.Items) == 2 {
				left, okL := optNumericLiteral(first.Items[1])
				right, okR := optNumericLiteral(second.Items[1])
				if okL && okR {
					minVal := left
					if right < minVal {
						minVal = right
					}
					merged := copyNode(first)
					merged.Items = []*Node{first.Items[0], optNum(decimal.Format(decimal.FromInt(minVal)), decimal.FromInt(minVal), second.Items[1].Pos)}
					next = append(next, merged)
					i += 2
					changed = true
					continue
				}
			}

			// DROP + DROP
			if second != nil && first.S == "DROP" && second.S == "DROP" && len(first.Items) == 2 && len(second.Items) == 2 {
				left, okL := optNumericLiteral(first.Items[1])
				right, okR := optNumericLiteral(second.Items[1])
				if okL && okR && left <= math.MaxInt64-right {
					sumVal := left + right
					merged := copyNode(first)
					merged.Items = []*Node{first.Items[0], optNum(decimal.Format(decimal.FromInt(sumVal)), decimal.FromInt(sumVal), second.Items[1].Pos)}
					next = append(next, merged)
					i += 2
					changed = true
					continue
				}
			}

			// SORT... + TAKE -> TOP...
			if second != nil && second.S == "TAKE" && len(second.Items) == 2 &&
				(first.S == "SORT" || first.S == "SORT_DESC" || first.S == "SORT_BY") {
				topName := "TOP"
				if first.S == "SORT_DESC" {
					topName = "TOP_DESC"
				} else if first.S == "SORT_BY" {
					topName = "TOP_BY"
				}
				fused := copyNode(first)
				fused.S = topName
				fused.Spec = Lookup(topName)
				fused.Items = append(fused.Items, second.Items[1])
				next = append(next, fused)
				i += 2
				changed = true
				continue
			}

			// MAP + FILTER
			if second != nil && first.S == "MAP" && second.S == "FILTER" {
				passes := optMapPassthroughs(first)
				passSet := make(map[string]bool)
				for _, p := range passes {
					passSet[p] = true
				}
				info := getOptFilterInfo(second)
				refs := optFieldRefs(info.predicate, info.binder)
				allInPass := len(refs) > 0
				for _, r := range refs {
					if !passSet[r] {
						allInPass = false
						break
					}
				}
				if info.valid && allInPass && !optReadsRowOrKey(info.predicate, info.binder) &&
					optKeysRenumberedBy(third) && optMapCannotRaise(first, logical) {
					next = append(next, second, first)
					i += 2
					changed = true
					continue
				}
			}

			// SORT... + FILTER
			if second != nil && (first.S == "SORT" || first.S == "SORT_DESC" || first.S == "SORT_BY") &&
				second.S == "FILTER" && !optStepReadsKey(second) && optKeysRenumberedBy(third) &&
				optCannotRaise(getOptSortInfo(first).key, getOptSortInfo(first).binder, logical) &&
				(logical || optCannotRaise(getOptFilterInfo(second).predicate, getOptFilterInfo(second).binder, false)) {
				next = append(next, second, first)
				i += 2
				changed = true
				continue
			}

			// SELECT_COLS + FILTER
			if second != nil && first.S == "SELECT_COLS" && second.S == "FILTER" {
				info := getOptFilterInfo(second)
				refs := optFieldRefs(info.predicate, info.binder)
				fields := optSelectFields(first)
				fieldSet := make(map[string]bool)
				for _, f := range fields {
					fieldSet[f] = true
				}
				allInFields := len(refs) > 0
				for _, r := range refs {
					if !fieldSet[r] {
						allInFields = false
						break
					}
				}
				if info.valid && allInFields && !optReadsRowOrKey(info.predicate, info.binder) &&
					optKeysRenumberedBy(third) {
					next = append(next, second, first)
					i += 2
					changed = true
					continue
				}
			}

			// MAP + SORT...
			if second != nil && first.S == "MAP" &&
				(second.S == "TOP" || second.S == "TOP_DESC" || second.S == "TOP_BY" ||
					second.S == "SORT" || second.S == "SORT_DESC" || second.S == "SORT_BY") &&
				optMapHasComputed(first) {
				sort := getOptSortInfo(second)
				refs := optFieldRefs(sort.key, sort.binder)
				passes := optMapPassthroughs(first)
				passSet := make(map[string]bool)
				for _, p := range passes {
					passSet[p] = true
				}
				allInPass := len(refs) > 0
				for _, r := range refs {
					if !passSet[r] {
						allInPass = false
						break
					}
				}
				if sort.key != nil && allInPass && !optReadsRowOrKey(sort.key, sort.binder) &&
					optMapCannotRaise(first, logical) && optCannotRaise(sort.key, sort.binder, logical) {
					next = append(next, second, first)
					i += 2
					changed = true
					continue
				}
			}

			// FILTER + FILTER
			if second != nil && first.S == "FILTER" && second.S == "FILTER" {
				left := getOptFilterInfo(first)
				right := getOptFilterInfo(second)
				if left.valid && right.valid && optCannotRaise(right.predicate, right.binder, logical) {
					rightPred := right.predicate
					if upperName(left.binder) != upperName(right.binder) {
						rightPred = optRenameVar(right.predicate, right.binder, left.binder)
					}
					combined := NewNode(NodeBin, left.predicate.Pos)
					combined.S = "AND"
					combined.L = left.predicate
					combined.R = rightPred

					merged := copyNode(first)
					if left.explicitBinder {
						merged.Items = []*Node{first.Items[0], first.Items[1], combined}
					} else {
						merged.Items = []*Node{first.Items[0], combined}
					}
					next = append(next, merged)
					i += 2
					changed = true
					continue
				}
			}

			// DISTINCT/DEDUPE + DISTINCT/DEDUPE
			if second != nil && (first.S == "DISTINCT" || first.S == "DEDUPE") &&
				(second.S == "DISTINCT" || second.S == "DEDUPE") {
				next = append(next, first)
				i += 2
				changed = true
				continue
			}

			// Drop FILTER(TRUE)
			filter := getOptFilterInfo(first)
			if first.S == "FILTER" && filter.valid && filter.predicate != nil &&
				filter.predicate.T == NodeBool && filter.predicate.B &&
				(len(next) > 0 || i > 0 || optSourceIsList(source)) {
				i++
				changed = true
				continue
			}

			next = append(next, first)
			i++
		}
		current = next
	}
	return current
}

func optInmemorySteps(source *Node, steps []*Node) []*Node {
	steps = optLogicalSteps(source, steps, false)
	rewritten := make([]*Node, len(steps))
	for i, step := range steps {
		cp := copyNode(step)
		if cp.S == "FILTER" && len(cp.Items) > 0 {
			body := copyNode(cp.Items[len(cp.Items)-1])
			var nextStep *Node
			if i+1 < len(steps) {
				nextStep = steps[i+1]
			}
			body.KeysUnobserved = optKeysRenumberedBy(nextStep)
			cp.Items[len(cp.Items)-1] = body
		}
		rewritten[i] = cp
	}
	return rewritten
}

func isMathOp(node *Node) bool {
	if node == nil {
		return false
	}
	if node.T == NodeBin && mathops.Operators[node.S] != "" {
		return true
	}
	if node.T == NodeUn && mathops.Prefix[node.S] != "" {
		return true
	}
	if node.T == NodeCall && mathops.Builtins[node.S].Op != "" {
		return true
	}
	return false
}

type emitResult struct {
	slot     uint16
	isConst  bool
	constVal *decimal.Dec
}

func compileMathPlan(root *Node) *MathPlan {
	if !isMathOp(root) {
		return nil
	}

	plan := &MathPlan{}
	var slotCount uint16
	allocSlot := func() uint16 {
		s := slotCount
		slotCount++
		return s
	}

	var emit func(node *Node, depth int) *emitResult
	emit = func(node *Node, depth int) *emitResult {
		if node == nil || depth > MAX_DEPTH {
			return nil
		}

		if node.T == NodeVar {
			slot := allocSlot()
			plan.Steps = append(plan.Steps, MathStep{
				Op:   "LOAD_VAR",
				Dst:  slot,
				Name: node.S,
				Pos:  node.Pos,
			})
			return &emitResult{slot: slot, isConst: false}
		}

		if node.T == NodeNum {
			dec := node.Dec
			if dec == nil {
				dec = tryDec(func() *decimal.Dec {
					return decimal.Parse(node.S, node.Pos, fail)
				})
				if dec == nil {
					return nil
				}
			}
			slot := allocSlot()
			plan.Steps = append(plan.Steps, MathStep{
				Op:       "LOAD_CONST",
				Dst:      slot,
				ConstVal: dec,
				Pos:      node.Pos,
			})
			return &emitResult{slot: slot, isConst: true, constVal: dec}
		}

		if node.T == NodeBin && mathops.Operators[node.S] != "" {
			if node.L == nil || node.R == nil {
				return nil
			}
			resL := emit(node.L, depth+1)
			if resL == nil {
				return nil
			}
			resR := emit(node.R, depth+1)
			if resR == nil {
				return nil
			}

			op := node.S

			// Copy propagation:
			// Rule 1: x + 0
			if op == "+" && resR.isConst && decimal.IsZero(resR.constVal) && resR.constVal.Scale == 0 {
				if node.R.T == NodeNum && len(plan.Steps) > 0 && plan.Steps[len(plan.Steps)-1].Dst == resR.slot {
					plan.Steps = plan.Steps[:len(plan.Steps)-1]
				}
				return resL
			}
			// Rule 2: 0 + x
			if op == "+" && resL.isConst && decimal.IsZero(resL.constVal) && resL.constVal.Scale == 0 {
				return resR
			}
			// Rule 3: x - 0
			if op == "-" && resR.isConst && decimal.IsZero(resR.constVal) && resR.constVal.Scale == 0 {
				if node.R.T == NodeNum && len(plan.Steps) > 0 && plan.Steps[len(plan.Steps)-1].Dst == resR.slot {
					plan.Steps = plan.Steps[:len(plan.Steps)-1]
				}
				return resL
			}
			// Rule 4: x * 1
			if op == "*" && resR.isConst && !resR.constVal.Neg && resR.constVal.Digits.Cmp(big.NewInt(1)) == 0 && resR.constVal.Scale == 0 {
				if node.R.T == NodeNum && len(plan.Steps) > 0 && plan.Steps[len(plan.Steps)-1].Dst == resR.slot {
					plan.Steps = plan.Steps[:len(plan.Steps)-1]
				}
				return resL
			}
			// Rule 5: 1 * x
			if op == "*" && resL.isConst && !resL.constVal.Neg && resL.constVal.Digits.Cmp(big.NewInt(1)) == 0 && resL.constVal.Scale == 0 {
				return resR
			}

			dst := allocSlot()
			plan.Steps = append(plan.Steps, MathStep{
				Op:   mathops.Operators[op],
				Dst:  dst,
				Src1: resL.slot,
				Src2: resR.slot,
				Pos:  node.Pos,
			})
			return &emitResult{slot: dst, isConst: false}
		}

		if node.T == NodeUn && mathops.Prefix[node.S] != "" {
			if node.L == nil {
				return nil
			}
			resX := emit(node.L, depth+1)
			if resX == nil {
				return nil
			}
			dst := allocSlot()
			plan.Steps = append(plan.Steps, MathStep{
				Op:   mathops.Prefix[node.S],
				Dst:  dst,
				Src1: resX.slot,
				Pos:  node.Pos,
			})
			return &emitResult{slot: dst, isConst: false}
		}

		if node.T == NodeCall && mathops.Builtins[node.S].Op != "" {
			bSpec := mathops.Builtins[node.S]
			args := node.Items
			if bSpec.Arity == "1" {
				if len(args) != 1 {
					return nil
				}
				resArg := emit(args[0], depth+1)
				if resArg == nil {
					return nil
				}
				dst := allocSlot()
				plan.Steps = append(plan.Steps, MathStep{
					Op:   bSpec.Op,
					Dst:  dst,
					Src1: resArg.slot,
					Pos:  node.Pos,
				})
				return &emitResult{slot: dst, isConst: false}
			}
			if bSpec.Arity == "2" {
				if len(args) != 2 {
					return nil
				}
				res0 := emit(args[0], depth+1)
				if res0 == nil {
					return nil
				}
				res1 := emit(args[1], depth+1)
				if res1 == nil {
					return nil
				}
				dst := allocSlot()
				step := MathStep{
					Op:   bSpec.Op,
					Dst:  dst,
					Src1: res0.slot,
					Src2: res1.slot,
					Pos:  node.Pos,
				}
				if bSpec.Aux >= 0 && bSpec.Aux < len(args) {
					step.AuxPos = args[bSpec.Aux].Pos
				}
				plan.Steps = append(plan.Steps, step)
				return &emitResult{slot: dst, isConst: false}
			}
			if bSpec.Arity == "fold" {
				if len(args) < 1 {
					return nil
				}
				res0 := emit(args[0], depth+1)
				if res0 == nil {
					return nil
				}
				currSlot := res0.slot
				for k := 1; k < len(args); k++ {
					resNext := emit(args[k], depth+1)
					if resNext == nil {
						return nil
					}
					dst := allocSlot()
					plan.Steps = append(plan.Steps, MathStep{
						Op:   bSpec.Op,
						Dst:  dst,
						Src1: currSlot,
						Src2: resNext.slot,
						Pos:  node.Pos,
					})
					currSlot = dst
				}
				return &emitResult{slot: currSlot, isConst: false}
			}
		}

		if node.T == NodeBin || node.T == NodeUn || node.T == NodeAssign || node.T == NodeSeq || node.T == NodeList {
			return nil
		}
		if node.T == NodeCall && (node.S == "IF" || node.S == "COND") {
			return nil
		}

		slot := allocSlot()
		plan.Steps = append(plan.Steps, MathStep{
			Op:       "LOAD_LEAF",
			Dst:      slot,
			LeafNode: node,
			Pos:      node.Pos,
		})
		return &emitResult{slot: slot, isConst: false}
	}

	res := emit(root, 1)
	if res == nil || len(plan.Steps) == 0 {
		return nil
	}
	plan.OutputSlot = res.slot
	plan.ScratchpadSize = slotCount
	return plan
}

func optTree(node *Node, physical bool, depth int, fold bool, inMath bool) *Node {
	if node == nil || depth > MAX_DEPTH {
		return node
	}
	if node.T == NodeCall && IsPipelineOp(node.S) && len(node.Items) > 0 {
		source, steps := UnwindPipeline(node)
		optimizedSource := optTree(source, physical, depth+1, fold, false)
		optimizedSteps := make([]*Node, len(steps))
		for sIdx, step := range steps {
			cp := copyNode(step)
			cp.Items = make([]*Node, len(step.Items))
			cp.Items[0] = step.Items[0]
			for i := 1; i < len(step.Items); i++ {
				foldArg := fold && optStepArgFolds(step, i)
				cp.Items[i] = optTree(step.Items[i], physical, depth+1, foldArg, false)
			}
			optimizedSteps[sIdx] = cp
		}
		finalSteps := optLogicalSteps(optimizedSource, optimizedSteps, !physical)
		if physical {
			finalSteps = optInmemorySteps(optimizedSource, finalSteps)
		}
		return BuildPipeline(optimizedSource, finalSteps)
	}

	isCurrMath := isMathOp(node)
	nextInMath := isCurrMath

	cp := copyNode(node)
	if cp.L != nil && cp.T != NodeAssign {
		cp.L = optTree(cp.L, physical, depth+1, fold, nextInMath)
	}
	if cp.R != nil {
		cp.R = optTree(cp.R, physical, depth+1, fold, nextInMath)
	}
	for i, child := range cp.Items {
		cp.Items[i] = optTree(child, physical, depth+1, fold, nextInMath)
	}

	folded := cp
	if fold {
		folded = optFold(cp)
	}
	if physical && !inMath && isMathOp(folded) {
		plan := compileMathPlan(folded)
		if plan != nil {
			cpPlan := copyNode(folded)
			cpPlan.MathPlan = plan
			return cpPlan
		}
	}
	return folded
}

func optExceedsDepth(node *Node, depth int) bool {
	if depth > MAX_DEPTH {
		return true
	}
	next := depth + 1
	for _, item := range node.Items {
		if item != nil && optExceedsDepth(item, next) {
			return true
		}
	}
	if node.T != NodeAssign && node.L != nil && optExceedsDepth(node.L, next) {
		return true
	}
	if node.R != nil && optExceedsDepth(node.R, next) {
		return true
	}
	return false
}

func optRoot(ast *Node, physical bool) *Node {
	if ast != nil && optExceedsDepth(ast, 1) {
		return ast
	}
	return optTree(ast, physical, 1, true, false)
}

// OptimizeAstLogical runs logical optimization (pipeline rewrites, constant folding) without physical tree plan.
func OptimizeAstLogical(ast *Node) *Node {
	return optRoot(ast, false)
}

// OptimizeAstInMemory runs physical in-memory optimizations (compiles math plan and marks keysUnobserved).
func OptimizeAstInMemory(ast *Node) *Node {
	return optRoot(ast, true)
}

// OptimizeAST applies logical and in-memory physical optimizations.
func OptimizeAST(ast *Node) *Node {
	return OptimizeAstInMemory(ast)
}
