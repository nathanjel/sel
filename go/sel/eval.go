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

	// Restore dynamic evaluation state on every exit, including a panic that
	// a surrounding coalescing operator or join prefilter catches.
	frames := ctx.Frames
	completed := false
	defer func() {
		ctx.Depth--
		ctx.Frames = frames
		if !completed {
			// A join's prefilter state is handed from a LINK to its parent on
			// a normal return; on a panic that a `??` or a join probe catches,
			// nothing will consume it, and the next unrelated LINK must not
			// find it there.
			ctx.JoinPrefilter = nil
			ctx.JoinPrefilterReport = nil
		}
	}()
	var res *Value
	if node.MathPlan != nil {
		res = evalMathPlan(node.MathPlan, ctx)
	} else {
		res = dispatch(node, ctx)
	}
	completed = true
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
			if node.SlotCache != nil {
				if cache := node.SlotCache.Load(); cache != nil && obj.shape == cache.Shape {
					return obj.storage[cache.Slot]
				}
			}
			key = node.R.S
		} else {
			key = EvalNode(node.R, ctx).AsText(node.R.Pos)
		}

		if obj.shape != nil {
			if idx, ok := obj.shape.KeyMap[key]; ok {
				if literal && node.SlotCache != nil {
					node.SlotCache.Store(&SlotCache{Shape: obj.shape, Slot: idx})
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
		// The children the list will hold, counted before any are copied
		// (SPEC §6.4): A = (A, A) thirty times must end in E_RANGE, not memory.
		if v.Kind == KindNone && v.Size() > 0 {
			checkCollection(satAdd(int64(len(values)), int64(v.Size())), "the list", node.Pos)
		} else {
			checkCollection(int64(len(values))+1, "the list", node.Pos)
		}
		if v.Kind == KindNone && v.Size() > 0 {
			if v.storage != nil {
				for _, child := range v.storage {
					values = append(values, child.CloneAt(2, node.Pos))
				}
			} else {
				for _, e := range v.entries {
					values = append(values, e.Val.CloneAt(2, node.Pos))
				}
			}
		} else {
			// The list being built is level 1; what it holds starts at level 2, and a
			// too-deep value is reported at the list node (SPEC §3.4, §6.4).
			values = append(values, v.CloneAt(2, node.Pos))
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
		return concat(l, r, lp, rp, node.Pos)

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

func concat(l, r *Value, lp, rp, at Pos) *Value {
	lv := l.ScalarSource(lp)
	rv := r.ScalarSource(rp)
	if lv.Kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", lp)
	}
	if rv.Kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", rp)
	}
	if lv.Kind == KindText && rv.Kind == KindText {
		ls, rs := lv.Scalar(), rv.Scalar()
		// Measured before it is built (SPEC §6.4). Bytes bound code points from
		// above, so the exact count is only needed when the bytes are past the cap.
		if int64(len(ls))+int64(len(rs)) > maxTextLen {
			checkTextLen(runeLen(ls)+runeLen(rs), "the result of &", at)
		}
		return NewTextOwned(ls + rs)
	}
	a := l.AsBytes(lp)
	b := r.AsBytes(rp)
	checkTextLen(int64(len(a))+int64(len(b)), "the result of &", at)
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
		// The stored value sits len(path) levels down (the variable plus each
		// bracket), so its depth is counted from there: path plus value must fit
		// the cap, and the error is reported at the target (SPEC §6.4).
		value = EvalNode(node.R, ctx).CloneAt(len(path), node.L.Pos)
	} else {
		current := walkCreate(ctx, path, len(path)-1).Get(key)
		if current == nil {
			fail("E_UNDEF_VAR", fmt.Sprintf("%s needs an existing target", node.S), node.L.Pos)
		}
		rhs := EvalNode(node.R, ctx)
		binOp := compoundOps[node.S]
		tp, vp := node.L.Pos, node.R.Pos
		if binOp == "&" {
			value = concat(current, rhs, tp, vp, node.Pos)
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

// mathSlot is one scratchpad cell of a math plan. A loaded operand is kept as
// the Value it evaluated to and is coerced only when an operation consumes it:
// SPEC §6.2 evaluates every operand first and coerces afterwards, so the plan
// must not turn a load into a coercion (an error in a later operand comes
// first, and a later operand's side effect on an earlier one's value is seen).
type mathSlot struct {
	d   *decimal.Dec
	v   *Value
	pos Pos
}

func (s *mathSlot) dec() *decimal.Dec {
	if s.d == nil {
		s.d = s.v.AsDecimal(s.pos)
	}
	return s.d
}

func evalMathPlan(plan *MathPlan, ctx *Context) *Value {
	slots := make([]mathSlot, plan.ScratchpadSize)
	for si := range plan.Steps {
		step := &plan.Steps[si]
		switch step.Op {
		case "LOAD_VAR":
			val := ctx.Lookup(step.Name)
			if val == nil {
				fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", step.Name), step.Pos)
			}
			slots[step.Dst] = mathSlot{v: val, pos: step.Pos}

		case "LOAD_CONST":
			slots[step.Dst] = mathSlot{d: step.ConstVal}

		case "LOAD_LEAF":
			val := EvalNode(step.LeafNode, ctx)
			slots[step.Dst] = mathSlot{v: val, pos: step.LeafNode.Pos}

		case "COERCE":
			slots[step.Dst].dec()

		case "ADD":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			slots[step.Dst] = mathSlot{d: decimal.Add(a, b, step.Pos, fail)}

		case "SUB":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			slots[step.Dst] = mathSlot{d: decimal.Sub(a, b, step.Pos, fail)}

		case "MUL":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			slots[step.Dst] = mathSlot{d: decimal.Mul(a, b, step.Pos, fail)}

		case "DIV":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			slots[step.Dst] = mathSlot{d: decimal.Div(a, b, step.Pos, fail)}

		case "MOD":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			slots[step.Dst] = mathSlot{d: decimal.Mod(a, b, step.Pos, fail)}

		case "NEG":
			slots[step.Dst] = mathSlot{d: decimal.Negate(slots[step.Src1].dec())}

		case "ABS":
			slots[step.Dst] = mathSlot{d: decimal.Abs(slots[step.Src1].dec())}

		case "SIGN":
			s := decimal.Sign(slots[step.Src1].dec())
			slots[step.Dst] = mathSlot{d: decimal.FromInt(int64(s))}

		case "CEIL":
			slots[step.Dst] = mathSlot{d: decimal.Ceil(slots[step.Src1].dec(), step.Pos, fail)}

		case "FLOOR":
			slots[step.Dst] = mathSlot{d: decimal.Floor(slots[step.Src1].dec(), step.Pos, fail)}

		case "TRUNC":
			slots[step.Dst] = mathSlot{d: decimal.Trunc(slots[step.Src1].dec())}

		case "ROUND":
			x := slots[step.Src1].dec()
			scale := CheckSizedInt(slots[step.Src2].dec(), "ROUND", 2, MaxScale, "ROUND scale", step.AuxPos)
			slots[step.Dst] = mathSlot{d: decimal.Round(x, scale, step.Pos, fail)}

		case "POWER":
			x := slots[step.Src1].dec()
			exp := CheckSizedInt(slots[step.Src2].dec(), "POWER", 2, MaxPower, "POWER exponent", step.AuxPos)
			slots[step.Dst] = mathSlot{d: decimal.Power(x, exp, step.Pos, fail)}

		case "MIN":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			if decimal.Cmp(b, a) < 0 {
				slots[step.Dst] = mathSlot{d: b}
			} else {
				slots[step.Dst] = mathSlot{d: a}
			}

		case "MAX":
			a, b := slots[step.Src1].dec(), slots[step.Src2].dec()
			if decimal.Cmp(b, a) > 0 {
				slots[step.Dst] = mathSlot{d: b}
			} else {
				slots[step.Dst] = mathSlot{d: a}
			}
		}
	}
	return NewNum(slots[plan.OutputSlot].dec())
}
