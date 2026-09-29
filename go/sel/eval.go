// SEL expression evaluator.

package sel

import (
	"bytes"
	"fmt"

	"github.com/nathanjel/sel/go/internal/decimal"
)

const (
	MaxScale = 1000000
	MaxPower = 100000
)

func CheckSizedInt(d *decimal.Dec, name string, argNum int, limit int64, what string, pos Pos) int {
	if !decimal.IsInteger(d) {
		fail("E_NOT_INT", fmt.Sprintf("%s argument %d must be a whole number", name, argNum), pos)
	}
	n := decimal.ToSafeInt(d)
	if n < 0 {
		fail("E_RANGE", fmt.Sprintf("%s argument %d must not be negative", name, argNum), pos)
	}
	if n > limit {
		fail("E_RANGE", fmt.Sprintf("%s %d exceeds the maximum of %d", what, n, limit), pos)
	}
	return int(n)
}

func EvalNode(node *Node, ctx *Context) *Value {
	ctx.Depth++
	if ctx.Depth > MAX_DEPTH {
		ctx.Depth--
		fail("E_DEPTH", "evaluation nested too deeply", node.Pos)
	}

	var res *Value
	if node.MathPlan != nil {
		res = evalMathPlan(node.MathPlan, ctx)
	} else {
		res = dispatch(node, ctx)
	}
	ctx.Depth--
	return res
}

func dispatch(node *Node, ctx *Context) *Value {
	switch node.T {
	case NodeNum:
		return &Value{Kind: KindText, strVal: node.S, decVal: node.Dec}

	case NodeText:
		return NewTextOwned(node.S)

	case NodeBool:
		return NewBool(node.B)

	case NodeNull:
		return NewNull()

	case NodeVar:
		v := ctx.Lookup(node.S)
		if v == nil {
			fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", node.S), node.Pos)
		}
		return v

	case NodeIndex:
		objNode := node.L
		var obj *Value
		if objNode.T == NodeVar {
			obj = ctx.Lookup(objNode.S)
			if obj == nil {
				fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", objNode.S), objNode.Pos)
			}
		} else {
			obj = EvalNode(objNode, ctx)
		}

		literal := node.R.T == NodeText
		var key string
		if literal {
			if node.SlotCache != nil && obj.shape == node.SlotCache.Shape {
				return obj.storage[node.SlotCache.Slot]
			}
			key = node.R.S
		} else {
			key = EvalNode(node.R, ctx).AsText(node.R.Pos)
		}

		if obj.shape != nil {
			if idx, ok := obj.shape.KeyMap[key]; ok {
				if literal {
					node.SlotCache = &SlotCache{Shape: obj.shape, Slot: idx}
				}
				return obj.storage[idx]
			}
			fail("E_NO_KEY", fmt.Sprintf("no key %q", key), node.Pos)
		}

		child := obj.Get(key)
		if child == nil {
			fail("E_NO_KEY", fmt.Sprintf("no key %q", key), node.Pos)
		}
		return child

	case NodeSeq:
		var last *Value = NewNone()
		for _, item := range node.Items {
			last = EvalNode(item, ctx)
		}
		return last

	case NodeList:
		return evalList(node, ctx)

	case NodeUn:
		return evalUnary(node, ctx)

	case NodeBin:
		return evalBinary(node, ctx)

	case NodeAssign:
		return evalAssign(node, ctx)

	case NodeCall:
		args := NewArgs(node, ctx)
		if !node.Spec.Lazy {
			for i := range node.Items {
				args.Val(i)
			}
		}
		return node.Spec.Fn(args, ctx)

	default:
		fail("E_SYNTAX", fmt.Sprintf("cannot evaluate node %s", node.T), node.Pos)
		return nil
	}
}

func evalList(node *Node, ctx *Context) *Value {
	var values []*Value
	for _, item := range node.Items {
		v := EvalNode(item, ctx)
		if v.Kind == KindNone && v.Size() > 0 {
			if v.storage != nil {
				for _, child := range v.storage {
					values = append(values, child.CloneAt(1, item.Pos))
				}
			} else {
				for _, e := range v.entries {
					values = append(values, e.Val.CloneAt(1, item.Pos))
				}
			}
		} else {
			values = append(values, v.CloneAt(1, item.Pos))
		}
	}
	return NewListOwned(values)
}

func evalUnary(node *Node, ctx *Context) *Value {
	v := EvalNode(node.L, ctx)
	if node.S == "NOT" {
		return NewBool(!v.AsBool(node.L.Pos))
	}
	return NewNum(decimal.Negate(v.AsDecimal(node.L.Pos)))
}

func evalBinary(node *Node, ctx *Context) *Value {
	op := node.S

	if op == "AND" || op == "OR" {
		left := EvalNode(node.L, ctx).AsBool(node.L.Pos)
		if op == "AND" && !left {
			return NewBool(false)
		}
		if op == "OR" && left {
			return NewBool(true)
		}
		return NewBool(EvalNode(node.R, ctx).AsBool(node.R.Pos))
	}

	if op == "??" || op == "???" {
		var l *Value
		hasVal := false
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*SelError); ok && (se.Code == "E_NO_KEY" || se.Code == "E_UNDEF_VAR") {
						return
					}
					panic(r)
				}
			}()
			l = EvalNode(node.L, ctx)
			hasVal = true
		}()
		if !hasVal {
			return EvalNode(node.R, ctx)
		}
		if (op == "??" && l.IsNull()) || (op == "???" && l.IsVacuous()) {
			return EvalNode(node.R, ctx)
		}
		return l
	}

	l := EvalNode(node.L, ctx)
	r := EvalNode(node.R, ctx)
	lp, rp := node.L.Pos, node.R.Pos

	switch op {
	case "+":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		return NewNum(decimal.Add(a, b, node.Pos, fail))

	case "-":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		return NewNum(decimal.Sub(a, b, node.Pos, fail))

	case "*":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		return NewNum(decimal.Mul(a, b, node.Pos, fail))

	case "/":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		return NewNum(decimal.Div(a, b, node.Pos, fail))

	case "%":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		return NewNum(decimal.Mod(a, b, node.Pos, fail))

	case "&":
		return concat(l, r, lp, rp)

	case "==", "!=", "<", "<=", ">", ">=":
		a := l.AsDecimal(lp)
		b := r.AsDecimal(rp)
		if a.Scale == b.Scale {
			var c int
			if a.Neg != b.Neg {
				if a.Neg {
					c = -1
				} else {
					c = 1
				}
			} else {
				c = a.Digits.Cmp(b.Digits)
				if a.Neg {
					c = -c
				}
			}
			return NewBool(compareResult(op, c, node.Pos))
		}
		return NewBool(compareResult(op, decimal.Cmp(a, b), node.Pos))

	case "$==":
		if l.Kind == KindText && r.Kind == KindText && l.Size() == 0 && r.Size() == 0 {
			return NewBool(l.Scalar() == r.Scalar())
		}
		a := l.AsBytes(lp)
		b := r.AsBytes(rp)
		return NewBool(bytes.Equal(a, b))

	case "$!=":
		if l.Kind == KindText && r.Kind == KindText && l.Size() == 0 && r.Size() == 0 {
			return NewBool(l.Scalar() != r.Scalar())
		}
		a := l.AsBytes(lp)
		b := r.AsBytes(rp)
		return NewBool(!bytes.Equal(a, b))

	case "$<", "$<=", "$>", "$>=":
		a := l.AsBytes(lp)
		b := r.AsBytes(rp)
		return NewBool(compareResult(op[1:], bytes.Compare(a, b), node.Pos))

	case "EQL":
		return NewBool(l.Eql(r, node.Pos))

	case "IN":
		return NewBool(isIn(l, r))

	case "XOR":
		a := l.AsBool(lp)
		b := r.AsBool(rp)
		return NewBool(a != b)

	case "BAND", "BOR", "BXOR":
		a := l.AsBytes(lp)
		b := r.AsBytes(rp)
		return bitwise(op, a, b, node.Pos)

	default:
		fail("E_SYNTAX", fmt.Sprintf("unknown operator %s", op), node.Pos)
		return nil
	}
}

func compareResult(op string, c int, pos Pos) bool {
	switch op {
	case "==":
		return c == 0
	case "!=":
		return c != 0
	case "<":
		return c < 0
	case "<=":
		return c <= 0
	case ">":
		return c > 0
	case ">=":
		return c >= 0
	default:
		fail("E_SYNTAX", fmt.Sprintf("unknown comparison operator %s", op), pos)
		return false
	}
}

func concat(l, r *Value, lp, rp Pos) *Value {
	lv := l.ScalarSource(lp)
	rv := r.ScalarSource(rp)
	if lv.Kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", lp)
	}
	if rv.Kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", rp)
	}
	if lv.Kind == KindText && rv.Kind == KindText {
		return NewTextOwned(lv.Scalar() + rv.Scalar())
	}
	a := l.AsBytes(lp)
	b := r.AsBytes(rp)
	res := make([]byte, len(a)+len(b))
	copy(res, a)
	copy(res[len(a):], b)
	return NewBinOwned(res)
}

func isIn(needle, hay *Value) bool {
	if hay.Size() == 0 {
		return hay.Eql(needle, Pos{})
	}
	if hay.storage != nil {
		for _, child := range hay.storage {
			if child.Eql(needle, Pos{}) {
				return true
			}
		}
		return false
	}
	for _, e := range hay.entries {
		if e.Val.Eql(needle, Pos{}) {
			return true
		}
	}
	return false
}

func bitwise(op string, a, b []byte, pos Pos) *Value {
	if len(a) != len(b) {
		fail("E_LEN_MISMATCH", fmt.Sprintf("%s needs operands of equal length (%d vs %d)", op, len(a), len(b)), pos)
	}
	out := make([]byte, len(a))
	switch op {
	case "BAND":
		for i := range a {
			out[i] = a[i] & b[i]
		}
	case "BOR":
		for i := range a {
			out[i] = a[i] | b[i]
		}
	case "BXOR":
		for i := range a {
			out[i] = a[i] ^ b[i]
		}
	}
	return NewBinOwned(out)
}

var compoundOps = map[string]string{
	"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%", "&=": "&",
}

func evalAssign(node *Node, ctx *Context) *Value {
	path := resolveTarget(node.L, ctx)
	key := path[len(path)-1]

	var value *Value
	if node.S == "=" {
		value = EvalNode(node.R, ctx).CloneAt(1, node.Pos)
	} else {
		current := walkCreate(ctx, path, len(path)-1).Get(key)
		if current == nil {
			fail("E_UNDEF_VAR", fmt.Sprintf("%s needs an existing target", node.S), node.L.Pos)
		}
		rhs := EvalNode(node.R, ctx)
		binOp := compoundOps[node.S]
		tp, vp := node.L.Pos, node.R.Pos
		if binOp == "&" {
			value = concat(current, rhs, tp, vp)
		} else {
			a := current.AsDecimal(tp)
			b := rhs.AsDecimal(vp)
			var res *decimal.Dec
			switch binOp {
			case "+":
				res = decimal.Add(a, b, node.Pos, fail)
			case "-":
				res = decimal.Sub(a, b, node.Pos, fail)
			case "*":
				res = decimal.Mul(a, b, node.Pos, fail)
			case "/":
				res = decimal.Div(a, b, node.Pos, fail)
			case "%":
				res = decimal.Mod(a, b, node.Pos, fail)
			}
			value = NewNum(res)
		}
	}

	walkCreate(ctx, path, len(path)-1).Set(key, value)
	return value
}

func walkCreate(ctx *Context, path []string, upto int) *Value {
	cur := ctx.Root
	for i := 0; i < upto; i++ {
		nxt := cur.Get(path[i])
		if nxt == nil {
			nxt = NewNone()
			cur.Set(path[i], nxt)
		}
		cur = nxt
	}
	return cur
}

func resolveTarget(target *Node, ctx *Context) []string {
	var chain []*Node
	n := target
	for n.T == NodeIndex {
		chain = append([]*Node{n.R}, chain...)
		n = n.L
	}

	if ctx.IsBound(n.S) {
		fail("E_BAD_ASSIGN", fmt.Sprintf("%s is an aggregate binder and cannot be assigned", n.S), target.Pos)
	}
	if len(chain)+1 > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", target.Pos)
	}
	path := []string{n.S}
	if len(chain) == 0 {
		return path
	}

	if ctx.Root.Get(n.S) == nil {
		ctx.Root.Set(n.S, NewNone())
	}

	for i := 0; i < len(chain)-1; i++ {
		k := EvalNode(chain[i], ctx).AsText(chain[i].Pos)
		cur := walkCreate(ctx, path, len(path))
		if cur.Get(k) == nil {
			cur.Set(k, NewNone())
		}
		path = append(path, k)
	}
	last := chain[len(chain)-1]
	path = append(path, EvalNode(last, ctx).AsText(last.Pos))
	return path
}

func evalMathPlan(plan *MathPlan, ctx *Context) *Value {
	scratchpad := make([]*decimal.Dec, plan.ScratchpadSize)
	for _, step := range plan.Steps {
		switch step.Op {
		case "LOAD_VAR":
			val := ctx.Lookup(step.Name)
			if val == nil {
				fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", step.Name), step.Pos)
			}
			scratchpad[step.Dst] = val.AsDecimal(step.Pos)

		case "LOAD_CONST":
			scratchpad[step.Dst] = step.ConstVal

		case "LOAD_LEAF":
			val := EvalNode(step.LeafNode, ctx)
			scratchpad[step.Dst] = val.AsDecimal(step.LeafNode.Pos)

		case "ADD":
			scratchpad[step.Dst] = decimal.Add(scratchpad[step.Src1], scratchpad[step.Src2], step.Pos, fail)

		case "SUB":
			scratchpad[step.Dst] = decimal.Sub(scratchpad[step.Src1], scratchpad[step.Src2], step.Pos, fail)

		case "MUL":
			scratchpad[step.Dst] = decimal.Mul(scratchpad[step.Src1], scratchpad[step.Src2], step.Pos, fail)

		case "DIV":
			scratchpad[step.Dst] = decimal.Div(scratchpad[step.Src1], scratchpad[step.Src2], step.Pos, fail)

		case "MOD":
			scratchpad[step.Dst] = decimal.Mod(scratchpad[step.Src1], scratchpad[step.Src2], step.Pos, fail)

		case "NEG":
			scratchpad[step.Dst] = decimal.Negate(scratchpad[step.Src1])

		case "ABS":
			scratchpad[step.Dst] = decimal.Abs(scratchpad[step.Src1])

		case "SIGN":
			s := decimal.Sign(scratchpad[step.Src1])
			scratchpad[step.Dst] = decimal.FromInt(int64(s))

		case "CEIL":
			scratchpad[step.Dst] = decimal.Ceil(scratchpad[step.Src1])

		case "FLOOR":
			scratchpad[step.Dst] = decimal.Floor(scratchpad[step.Src1])

		case "TRUNC":
			scratchpad[step.Dst] = decimal.Trunc(scratchpad[step.Src1])

		case "ROUND":
			scale := CheckSizedInt(scratchpad[step.Src2], "ROUND", 2, MaxScale, "ROUND scale", step.AuxPos)
			scratchpad[step.Dst] = decimal.Round(scratchpad[step.Src1], scale, step.Pos, fail)

		case "POWER":
			exp := CheckSizedInt(scratchpad[step.Src2], "POWER", 2, MaxPower, "POWER exponent", step.AuxPos)
			scratchpad[step.Dst] = decimal.Power(scratchpad[step.Src1], exp, step.Pos, fail)

		case "MIN":
			a := scratchpad[step.Src1]
			b := scratchpad[step.Src2]
			if decimal.Cmp(b, a) < 0 {
				scratchpad[step.Dst] = b
			} else {
				scratchpad[step.Dst] = a
			}

		case "MAX":
			a := scratchpad[step.Src1]
			b := scratchpad[step.Src2]
			if decimal.Cmp(b, a) > 0 {
				scratchpad[step.Dst] = b
			} else {
				scratchpad[step.Dst] = a
			}
		}
	}
	return NewNum(scratchpad[plan.OutputSlot])
}
