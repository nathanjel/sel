package sel

import (
	"github.com/nathanjel/sel/go/internal/utf8"
)

type joinPlanOp int

const (
	opLeftSlot joinPlanOp = iota
	opLeftNested
	opRightSlot
	opLeft
	opRight
)

type joinPlanSlot struct {
	op   joinPlanOp
	slot int
}

type joinRestCheck struct {
	slot   int
	nested bool
}

type joinPlan struct {
	shape *RecordShape
	slots []joinPlanSlot
	lrest []joinRestCheck
	rkept []int
}

type joinPlanKey struct {
	leftShape  *RecordShape
	rightShape *RecordShape
	matched    bool
}

type joinFlatTest struct {
	binderNames map[string]bool
	lastShape   *RecordShape
	slots       []int
}

func newJoinFlatTest(b1, b2 string) *joinFlatTest {
	bm := make(map[string]bool)
	for _, k := range binderKeys(b1, "_1") {
		bm[k] = true
	}
	for _, k := range binderKeys(b2, "_2") {
		bm[k] = true
	}
	return &joinFlatTest{binderNames: bm}
}

func (t *joinFlatTest) isFlat(row *Value) bool {
	if row.shape == nil {
		return false
	}
	if row.shape != t.lastShape {
		t.lastShape = row.shape
		t.slots = t.slots[:0]
		for i, k := range row.shape.keys {
			if !t.binderNames[k] {
				t.slots = append(t.slots, i)
			}
		}
	}
	st := row.storage
	for _, i := range t.slots {
		if st[i].kind == KindNone && !st[i].isList {
			return false
		}
	}
	return true
}

func isLeftNested(v *Value) bool {
	return v.kind == KindNone && !v.isList && len(v.storage) > 0
}

type joinProjector struct {
	b1        string
	b2        string
	nullRight *Value
	rightFlat bool

	plans    map[joinPlanKey][]*joinPlan
	lastPair joinPlanKey
	lastPlan *joinPlan
	lastLeft *Value
}

func newJoinProjector(b1, b2 string, nullRight *Value) *joinProjector {
	return &joinProjector{
		b1:        b1,
		b2:        b2,
		nullRight: nullRight,
		plans:     make(map[joinPlanKey][]*joinPlan),
	}
}

func compileJoinPlan(left, rside *Value, b1, b2 string, matched bool) *joinPlan {
	if left.shape == nil || rside == nil || rside.shape == nil {
		return nil
	}

	var keys []string
	var slots []joinPlanSlot
	slotMap := make(map[string]int)

	put := func(key string, op joinPlanOp, s int) {
		if _, ok := slotMap[key]; ok {
			return
		}
		slotMap[key] = len(keys)
		keys = append(keys, key)
		slots = append(slots, joinPlanSlot{op: op, slot: s})
	}

	bind := func(key string, op joinPlanOp) {
		if idx, ok := slotMap[key]; ok {
			slots[idx] = joinPlanSlot{op: op, slot: 0}
		} else {
			put(key, op, 0)
		}
	}

	lKeys := left.shape.keys
	for i, k := range lKeys {
		if isLeftNested(left.storage[i]) {
			put(k, opLeftNested, i)
		}
	}

	for _, name := range binderKeys(b1, "_1") {
		bind(name, opLeft)
	}

	for _, name := range binderKeys(b2, "_2") {
		bind(name, opRight)
	}

	rKeys := rside.shape.keys
	rightNames := make(map[string]bool, len(rKeys))
	for _, k := range rKeys {
		rightNames[utf8.AsciiUpper(k)] = true
	}

	for i, k := range lKeys {
		if !isLeftNested(left.storage[i]) && !rightNames[utf8.AsciiUpper(k)] {
			put(k, opLeftSlot, i)
		}
	}

	if matched {
		leftNames := make(map[string]bool, len(lKeys))
		for _, k := range lKeys {
			leftNames[utf8.AsciiUpper(k)] = true
		}
		for j, k := range rKeys {
			if (rside.storage[j].kind != KindNone || rside.storage[j].isList) && !leftNames[utf8.AsciiUpper(k)] {
				put(k, opRightSlot, j)
			}
		}
	}

	resultShape := internRecordShape(keys)
	plan := &joinPlan{
		shape: resultShape,
		slots: slots,
	}

	ls := left.storage
	copied := make([]bool, len(ls))
	for _, s := range plan.slots {
		if s.op == opLeftSlot || s.op == opLeftNested {
			copied[s.slot] = true
		}
	}
	for i := range ls {
		if !copied[i] {
			plan.lrest = append(plan.lrest, joinRestCheck{slot: i, nested: isLeftNested(ls[i])})
		}
	}

	if matched {
		leftNames := make(map[string]bool, len(lKeys))
		for _, k := range lKeys {
			leftNames[utf8.AsciiUpper(k)] = true
		}
		binderNames := make(map[string]bool)
		for _, k := range binderKeys(b1, "_1") {
			binderNames[k] = true
		}
		for _, k := range binderKeys(b2, "_2") {
			binderNames[k] = true
		}
		rs := rside.storage
		for j, k := range rKeys {
			if !leftNames[utf8.AsciiUpper(k)] && !binderNames[k] && (rs[j].kind == KindNone && !rs[j].isList) {
				plan.rkept = append(plan.rkept, j)
			}
		}
	}

	return plan
}

func buildJoinPlan(plan *joinPlan, left, rside *Value, checkLeft, checkRight bool) (*Value, bool) {
	ls := left.storage
	rs := rside.storage

	if checkLeft {
		for _, lr := range plan.lrest {
			if isLeftNested(ls[lr.slot]) != lr.nested {
				return nil, false
			}
		}
	}

	if checkRight {
		for _, rk := range plan.rkept {
			if rs[rk].kind != KindNone || rs[rk].isList {
				return nil, false
			}
		}
	}

	storage := make([]*Value, len(plan.slots))
	for i, s := range plan.slots {
		switch s.op {
		case opLeftSlot:
			v := ls[s.slot]
			if checkLeft && isLeftNested(v) {
				return nil, false
			}
			storage[i] = v
		case opLeftNested:
			v := ls[s.slot]
			if checkLeft && !isLeftNested(v) {
				return nil, false
			}
			storage[i] = v
		case opRightSlot:
			v := rs[s.slot]
			if checkRight && (v.kind == KindNone && !v.isList) {
				return nil, false
			}
			storage[i] = v
		case opLeft:
			storage[i] = left
		case opRight:
			storage[i] = rside
		}
	}
	return newShapedRecord(plan.shape, storage), true
}

func (p *joinProjector) project(left, right *Value) *Value {
	rside := right
	if rside == nil {
		if p.nullRight != nil {
			rside = p.nullRight
		} else {
			rside = NewNone()
		}
	}

	if left.shape == nil || rside == nil || rside.shape == nil {
		return makeJoinedRow(left, right, p.b1, p.b2, p.nullRight)
	}

	matched := right != nil
	key := joinPlanKey{leftShape: left.shape, rightShape: rside.shape, matched: matched}

	sameLeft := p.lastLeft != nil && p.lastLeft == left
	checkRight := !p.rightFlat

	if p.lastPlan != nil && key == p.lastPair {
		checkLeft := !sameLeft
		if out, ok := buildJoinPlan(p.lastPlan, left, rside, checkLeft, checkRight); ok {
			if !sameLeft {
				p.lastLeft = left
			}
			return out
		}
	}

	list := p.plans[key]
	for _, plan := range list {
		if out, ok := buildJoinPlan(plan, left, rside, true, checkRight); ok {
			p.lastPair = key
			p.lastPlan = plan
			p.lastLeft = left
			return out
		}
	}

	plan := compileJoinPlan(left, rside, p.b1, p.b2, matched)
	if plan == nil {
		return makeJoinedRow(left, right, p.b1, p.b2, p.nullRight)
	}

	p.plans[key] = append(list, plan)
	p.lastPair = key
	p.lastPlan = plan
	p.lastLeft = left

	out, _ := buildJoinPlan(plan, left, rside, false, false)
	return out
}
