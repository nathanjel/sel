// SEL expression evaluator.

package sel

import (
	"bytes"
	"fmt"
	"math/big"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/mathops"
)

const (
	maxScale = 1000000
	maxPower = 100000
)

func checkSizedInt(d *decimal.Dec, name string, argNum int, limit int64, what string, pos Pos) int {
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

func evalNode(node *Node, ctx *Context) *Value {
	ctx.depth++
	if ctx.depth > maxDepth {
		ctx.depth--
		ctx.noCopy = nil
		fail("E_DEPTH", "evaluation nested too deeply", node.Pos)
	}

	// Restore dynamic evaluation state on every exit, including a panic that
	// a surrounding coalescing operator or join prefilter catches.
	frames := ctx.frames
	completed := false
	defer func() {
		ctx.depth--
		ctx.frames = frames
		if !completed {
			// A join's prefilter state is handed from a LINK to its parent on
			// a normal return; on a panic that a `??` or a join probe catches,
			// nothing will consume it, and the next unrelated LINK must not
			// find it there.
			ctx.joinPrefilter = nil
			ctx.joinPrefilterReport = nil
			ctx.noCopy = nil
		}
	}()
	var res *Value
	if node.mathPlan != nil {
		res = evalMathPlan(node.mathPlan, ctx)
	} else {
		res = dispatch(node, ctx)
	}
	completed = true
	return res
}

func dispatch(node *Node, ctx *Context) *Value {
	switch node.T {
	case NodeNum:
		return &Value{kind: KindText, strVal: node.S, decVal: node.dec}

	case NodeText:
		return newTextOwned(node.S)

	case NodeBool:
		return NewBool(node.B)

	case NodeNull:
		return NewNull()

	case NodeVar:
		v := ctx.lookup(node.S)
		if v == nil {
			fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", node.S), node.Pos)
		}
		return v

	case NodeIndex:
		objNode := node.L
		var obj *Value
		if objNode.T == NodeVar {
			obj = ctx.lookup(objNode.S)
			if obj == nil {
				fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", objNode.S), objNode.Pos)
			}
		} else {
			obj = evalNode(objNode, ctx)
		}

		literal := node.R.T == NodeText
		var key string
		if literal {
			if node.slotCache != nil {
				if cache := node.slotCache.Load(); cache != nil && obj.shape == cache.Shape {
					return obj.storage[cache.Slot]
				}
			}
			key = node.R.S
		} else {
			key = evalNode(node.R, ctx).AsText(node.R.Pos)
		}

		if obj.shape != nil {
			if idx, ok := obj.shape.keyMap[key]; ok {
				if literal && node.slotCache != nil {
					storeSlot(node.slotCache, obj.shape, idx)
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
		last := NewNone()
		for _, item := range node.Items {
			last = evalNode(item, ctx)
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
		args := newArgs(node, ctx)
		if !node.Spec.Lazy {
			if node.shape != nil {
				// A RECORD whose keys are all text literals: only the values
				// are evaluated (GO-P14). The keys cannot fail or have effects.
				for i := 1; i < len(node.Items); i += 2 {
					args.Val(i)
				}
			} else {
				for i := range node.Items {
					args.Val(i)
				}
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
		v := evalNode(item, ctx)
		// The children the list will hold, counted before any are copied
		// (SPEC §6.4): A = (A, A) thirty times must end in E_RANGE, not memory.
		if v.kind == KindNone && v.Size() > 0 {
			checkCollection(satAdd(int64(len(values)), int64(v.Size())), "the list", node.Pos)
		} else {
			checkCollection(int64(len(values))+1, "the list", node.Pos)
		}
		if v.kind == KindNone && v.Size() > 0 {
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
	return newListOwned(values)
}

func evalUnary(node *Node, ctx *Context) *Value {
	v := evalNode(node.L, ctx)
	if node.S == "NOT" {
		return NewBool(!v.AsBool(node.L.Pos))
	}
	return NewNum(decimal.Negate(v.AsDecimal(node.L.Pos)))
}

func evalBinary(node *Node, ctx *Context) *Value {
	op := node.S

	if op == "AND" || op == "OR" {
		left := evalNode(node.L, ctx).AsBool(node.L.Pos)
		if op == "AND" && !left {
			return NewBool(false)
		}
		if op == "OR" && left {
			return NewBool(true)
		}
		return NewBool(evalNode(node.R, ctx).AsBool(node.R.Pos))
	}

	if op == "??" || op == "???" {
		var l *Value
		hasVal := false
		// GO-P7: a plain path (`R["a"]["b"]`, a variable or literal keys) that is
		// missing is the common case of `??`, and raising E_NO_KEY for it costs a
		// message, a panic and a recover (~6x a hit). Resolve such a path without
		// raising; anything else takes the recover path below.
		pv, pmissing, handled := tryLiteralPath(node.L, ctx)
		if handled {
			if pmissing {
				return evalNode(node.R, ctx)
			}
			if (op == "??" && pv.IsNull()) || (op == "???" && pv.IsVacuous()) {
				return evalNode(node.R, ctx)
			}
			return pv
		}
		func() {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*SelError); ok && (se.Code == "E_NO_KEY" || se.Code == "E_UNDEF_VAR") {
						return
					}
					panic(r)
				}
			}()
			l = evalNode(node.L, ctx)
			hasVal = true
		}()
		if !hasVal {
			return evalNode(node.R, ctx)
		}
		if (op == "??" && l.IsNull()) || (op == "???" && l.IsVacuous()) {
			return evalNode(node.R, ctx)
		}
		return l
	}

	l := evalNode(node.L, ctx)
	r := evalNode(node.R, ctx)
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
		if l.kind == KindText && r.kind == KindText && l.Size() == 0 && r.Size() == 0 {
			return NewBool(l.Scalar() == r.Scalar())
		}
		a := l.AsBytes(lp)
		b := r.AsBytes(rp)
		return NewBool(bytes.Equal(a, b))

	case "$!=":
		if l.kind == KindText && r.kind == KindText && l.Size() == 0 && r.Size() == 0 {
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
	if lv.kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", lp)
	}
	if rv.kind == KindBool {
		fail("E_NOT_TEXT", "cannot concatenate a boolean", rp)
	}
	if lv.kind == KindText && rv.kind == KindText {
		ls, rs := lv.Scalar(), rv.Scalar()
		// Measured before it is built (SPEC §6.4). Bytes bound code points from
		// above, so the exact count is only needed when the bytes are past the cap.
		if int64(len(ls))+int64(len(rs)) > maxTextLen {
			checkTextLen(runeLen(ls)+runeLen(rs), "the result of &", at)
		}
		return newTextOwned(ls + rs)
	}
	a := l.AsBytes(lp)
	b := r.AsBytes(rp)
	checkTextLen(int64(len(a))+int64(len(b)), "the result of &", at)
	res := make([]byte, len(a)+len(b))
	copy(res, a)
	copy(res[len(a):], b)
	return newBinOwned(res)
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
	return newBinOwned(out)
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
		rhs := evalNode(node.R, ctx)
		if len(path) == 1 && producesFreshValue(node.R) {
			// A variable takes a result whose every node was built by the call
			// that returned it (GO-P13): the copy would duplicate what nothing
			// else can reach. The constructor already checked that its
			// children fit one level below the list, which for a plain variable
			// is exactly the depth the assignment would check.
			value = rhs
		} else {
			value = rhs.CloneAt(len(path), node.L.Pos)
		}
	} else {
		current := walkCreate(ctx, path, len(path)-1).Get(key)
		if current == nil {
			fail("E_UNDEF_VAR", fmt.Sprintf("%s needs an existing target", node.S), node.L.Pos)
		}
		rhs := evalNode(node.R, ctx)
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

// freshResultFuncs are the built-ins whose result is a new container holding
// COPIES of what it collected (SPEC §3.4 table), so no node of the result is
// reachable from anything else. TAKE, DROP, DISTINCT and DEDUPE alias their
// elements and are deliberately absent.
var freshResultFuncs = map[string]bool{
	"MAP": true, "FILTER": true, "SORT": true, "SORT_DESC": true, "SORT_BY": true,
	"TOP": true, "TOP_DESC": true, "TOP_BY": true, "BUCKET": true, "LIST": true, "RECORD": true,
}

func producesFreshValue(n *Node) bool {
	return n.T == NodeCall && freshResultFuncs[n.S]
}

func walkCreate(ctx *Context, path []string, upto int) *Value {
	cur := ctx.root
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

	if ctx.isBound(n.S) {
		fail("E_BAD_ASSIGN", fmt.Sprintf("%s is an aggregate binder and cannot be assigned", n.S), target.Pos)
	}
	if len(chain)+1 > maxDepth {
		fail("E_DEPTH", "value nested too deeply", target.Pos)
	}
	path := []string{n.S}
	if len(chain) == 0 {
		return path
	}

	if ctx.root.Get(n.S) == nil {
		ctx.root.Set(n.S, NewNone())
	}

	for i := 0; i < len(chain)-1; i++ {
		k := evalNode(chain[i], ctx).AsText(chain[i].Pos)
		cur := walkCreate(ctx, path, len(path))
		if cur.Get(k) == nil {
			cur.Set(k, NewNone())
		}
		path = append(path, k)
	}
	last := chain[len(chain)-1]
	path = append(path, evalNode(last, ctx).AsText(last.Pos))
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
	// n is an ADD, SUB or MUL result kept in a register (n.Mag != nil, item 1):
	// the plan's own magnitude, read by exactly one later ADD, SUB or MUL
	// (assignRegisters) and never seen by anything else.
	n decimal.Num
}

func (s *mathSlot) dec() *decimal.Dec {
	if s.d == nil {
		if s.n.Mag != nil {
			// Not reached by a plan assignRegisters made; a Dec boxed out of a
			// register is a copy, so it keeps its value whatever the register does.
			s.d = s.n.Dec()
		} else {
			s.d = s.v.AsDecimal(s.pos)
		}
	}
	return s.d
}

// num reads the slot in place for ADD, SUB and MUL: a register as it is, and a
// loaded value coerced exactly as dec() coerces it.
func (s *mathSlot) num() decimal.Num {
	if s.n.Mag != nil {
		return s.n
	}
	return decimal.NumOf(s.dec())
}

// mathPlanOps is every operation evalMathPlan carries out. spec/math-ops.md has
// each host check at load time that the manifest names nothing it lacks.
var mathPlanOps = map[string]bool{
	"ADD": true, "SUB": true, "MUL": true, "DIV": true, "MOD": true, "NEG": true, "ABS": true, "SIGN": true,
	"CEIL": true, "FLOOR": true, "TRUNC": true, "ROUND": true, "POWER": true, "MIN": true, "MAX": true,
}

func init() {
	for _, op := range mathops.Ops {
		if !mathPlanOps[op] {
			panic("math plan: the manifest's operation " + op + " has no implementation")
		}
	}
}

func evalMathPlan(plan *mathPlan, ctx *Context) *Value {
	// Up to sixteen slots live in the frame (GO-P12; Mandelbrot's
	// `zr * zr - zi * zi + cr` needs nine), in one of two buffers so that the
	// common plan of a few operations clears only eight: its scratchpad never
	// outlives this call.
	var slots []mathSlot
	switch n := int(plan.ScratchpadSize); {
	case n <= 8:
		var buf [8]mathSlot
		slots = buf[:n]
	case n <= 16:
		var buf [16]mathSlot
		slots = buf[:n]
	default:
		slots = make([]mathSlot, n)
	}
	// ADD, SUB and MUL work in this evaluation's register file: intermediates
	// in its registers, an operand brought to a common scale in its scratch.
	// A plan built by hand rather than by compileMathPlan takes none, and its
	// ADD, SUB and MUL make fresh results.
	var rf *regFile
	var scratch *big.Int
	if plan.UsesRegs {
		rf = ctx.takeRegs()
		defer ctx.releaseRegs(rf)
		scratch = &rf.scratch
	}
	for si := range plan.Steps {
		step := &plan.Steps[si]
		switch step.Op {
		case "LOAD_VAR":
			val := ctx.lookup(step.Name)
			if val == nil {
				fail("E_UNDEF_VAR", fmt.Sprintf("undefined variable %s", step.Name), step.Pos)
			}
			slots[step.Dst] = mathSlot{v: val, pos: step.Pos}

		case "LOAD_CONST":
			slots[step.Dst] = mathSlot{d: step.ConstVal}

		case "LOAD_LEAF":
			val := evalNode(step.LeafNode, ctx)
			slots[step.Dst] = mathSlot{v: val, pos: step.LeafNode.Pos}

		case "COERCE":
			slots[step.Dst].dec()

		case "ADD":
			a, b := slots[step.Src1].num(), slots[step.Src2].num()
			if step.Reg == 0 || rf == nil {
				slots[step.Dst] = mathSlot{d: decimal.AddNew(scratch, a, b, step.Pos, fail)}
			} else {
				slots[step.Dst] = mathSlot{n: decimal.AddInto(rf.reg(step.Reg), scratch, a, b, step.Pos, fail)}
			}

		case "SUB":
			a, b := slots[step.Src1].num(), slots[step.Src2].num()
			if step.Reg == 0 || rf == nil {
				slots[step.Dst] = mathSlot{d: decimal.SubNew(scratch, a, b, step.Pos, fail)}
			} else {
				slots[step.Dst] = mathSlot{n: decimal.SubInto(rf.reg(step.Reg), scratch, a, b, step.Pos, fail)}
			}

		case "MUL":
			a, b := slots[step.Src1].num(), slots[step.Src2].num()
			if step.Reg == 0 || rf == nil {
				slots[step.Dst] = mathSlot{d: decimal.MulNew(a, b, step.Pos, fail)}
			} else {
				slots[step.Dst] = mathSlot{n: decimal.MulInto(rf.reg(step.Reg), a, b, step.Pos, fail)}
			}

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
			scale := checkSizedInt(slots[step.Src2].dec(), "ROUND", 2, maxScale, "ROUND scale", step.AuxPos)
			slots[step.Dst] = mathSlot{d: decimal.Round(x, scale, step.Pos, fail)}

		case "POWER":
			x := slots[step.Src1].dec()
			exp := checkSizedInt(slots[step.Src2].dec(), "POWER", 2, maxPower, "POWER exponent", step.AuxPos)
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

// coalescePathFast switches the GO-P7 walk off, for the test that holds it to the
// raising route it replaces.
var coalescePathFast = true

// tryLiteralPath resolves an expression made only of a variable and literal
// index keys, `A["x"]["y"]`, WITHOUT raising E_UNDEF_VAR / E_NO_KEY: missing
// reports that one of them would have been raised (which `??` swallows). handled
// is false for any other shape, and for a chain deep enough that evaluating it
// would raise E_DEPTH, so the caller's ordinary path answers those exactly as it
// always did. The lookups are the ones dispatch makes, slot cache included.
func tryLiteralPath(node *Node, ctx *Context) (v *Value, missing, handled bool) {
	if !coalescePathFast {
		return nil, false, false
	}
	var chain [8]*Node
	n := 0
	cur := node
	for cur.T == NodeIndex {
		if cur.mathPlan != nil || cur.R == nil || cur.R.T != NodeText || n == len(chain) {
			return nil, false, false
		}
		chain[n] = cur
		n++
		cur = cur.L
	}
	// dispatch reads a variable directly under an index without an EvalNode of its
	// own, so a chain of n index nodes nests n levels and a bare variable nests one.
	need := n
	if need == 0 {
		need = 1
	}
	if cur.T != NodeVar || cur.mathPlan != nil || ctx.depth+need > maxDepth {
		return nil, false, false
	}
	obj := ctx.lookup(cur.S)
	if obj == nil {
		return nil, true, true
	}
	for i := n - 1; i >= 0; i-- {
		ix := chain[i]
		if ix.slotCache != nil {
			if cache := ix.slotCache.Load(); cache != nil && obj.shape == cache.Shape {
				obj = obj.storage[cache.Slot]
				continue
			}
		}
		key := ix.R.S
		if obj.shape != nil {
			idx, ok := obj.shape.keyMap[key]
			if !ok {
				return nil, true, true
			}
			if ix.slotCache != nil {
				storeSlot(ix.slotCache, obj.shape, idx)
			}
			obj = obj.storage[idx]
			continue
		}
		child := obj.Get(key)
		if child == nil {
			return nil, true, true
		}
		obj = child
	}
	return obj, false, true
}
