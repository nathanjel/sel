package sel

import "math/big"

// A math plan keeps its intermediate ADD, SUB and MUL
// results in registers it reuses, instead of a new Dec, big.Int and digit array
// for every step. A register is a big.Int whose digit array outlives the
// evaluation, so a warm one allocates nothing.

// regFile is one plan evaluation's registers and its scratch, where an operand
// is brought to a common scale. Only the evaluation that took the file reads or
// writes it, and nothing it returns points into it.
type regFile struct {
	r       [4]big.Int
	more    []*big.Int
	scratch big.Int
}

// reg is register i (1-based, as MathStep.Reg numbers them).
func (f *regFile) reg(i uint16) *big.Int {
	if int(i) <= len(f.r) {
		return &f.r[i-1]
	}
	for len(f.more) < int(i)-len(f.r) {
		f.more = append(f.more, new(big.Int))
	}
	return f.more[int(i)-len(f.r)-1]
}

// keepWords bounds what a register keeps between evaluations: one that grew past
// it (a million-digit product) gives its digits back to the collector.
const keepWords = 1 << 14

func (f *regFile) trim() {
	for i := range f.r {
		if cap(f.r[i].Bits()) > keepWords {
			f.r[i].SetBits(nil)
		}
	}
	for _, z := range f.more {
		if cap(z.Bits()) > keepWords {
			z.SetBits(nil)
		}
	}
	if cap(f.scratch.Bits()) > keepWords {
		f.scratch.SetBits(nil)
	}
}

// regPool is a Context's stack of register files: one for each plan evaluation
// in progress, as a leaf of one plan can run another.
type regPool struct {
	first  regFile
	deeper []*regFile
	depth  int
}

// takeRegs gives the plan evaluation about to run a register file nothing else
// is using; releaseRegs gives it back (deferred, so a failure that `??` catches
// gives it back too).
func (c *Context) takeRegs() *regFile {
	p := c.regs
	if p == nil {
		p = new(regPool)
		c.regs = p
	}
	d := p.depth
	p.depth++
	if d == 0 {
		return &p.first
	}
	for len(p.deeper) < d {
		p.deeper = append(p.deeper, new(regFile))
	}
	return p.deeper[d-1]
}

func (c *Context) releaseRegs(f *regFile) {
	c.regs.depth--
	f.trim()
}

func isArith(op string) bool { return op == "ADD" || op == "SUB" || op == "MUL" }

// assignRegisters decides where each ADD, SUB and MUL result of a plan lives.
//
// A result that is the plan's output, or that any other operation reads, stays a
// fresh Dec (Reg 0): it may outlive the evaluation, be returned as it is (MIN,
// MAX, a scale-0 FLOOR) or have its magnitude shared (NEG, ABS). Every other
// result goes to a register, which is free again once the one step that reads it
// has run. ADD and SUB write over a register they consume (math/big allows the
// alias); MUL takes another, as math/big would allocate around a product that
// aliases an operand. A plan in which some slot is read twice gets no registers.
func assignRegisters(plan *mathPlan) {
	n := int(plan.ScratchpadSize)
	reads := make([]int, n)
	readByArith := make([]bool, n)
	for i := range plan.Steps {
		st := &plan.Steps[i]
		switch st.Op {
		case "LOAD_VAR", "LOAD_CONST", "LOAD_LEAF", "COERCE":
			// COERCE coerces its slot in place; it hands nothing on.
		case "NEG", "ABS", "SIGN", "CEIL", "FLOOR", "TRUNC":
			reads[st.Src1]++
		default:
			reads[st.Src1]++
			reads[st.Src2]++
			if isArith(st.Op) {
				readByArith[st.Src1] = true
				readByArith[st.Src2] = true
			}
		}
		if isArith(st.Op) {
			plan.UsesRegs = true
		}
	}
	reads[plan.OutputSlot]++
	for _, r := range reads {
		if r > 1 {
			return
		}
	}
	slotReg := make([]uint16, n)
	var free []uint16
	var count uint16
	take := func() uint16 {
		if len(free) > 0 {
			r := free[len(free)-1]
			free = free[:len(free)-1]
			return r
		}
		count++
		return count
	}
	for i := range plan.Steps {
		st := &plan.Steps[i]
		if !isArith(st.Op) {
			continue
		}
		r1, r2 := slotReg[st.Src1], slotReg[st.Src2]
		if st.Dst != plan.OutputSlot && readByArith[st.Dst] {
			switch {
			case st.Op != "MUL" && r1 != 0:
				st.Reg = r1
			case st.Op != "MUL" && r2 != 0:
				st.Reg = r2
			default:
				// The operands' registers are still held, so take never returns one.
				st.Reg = take()
			}
			slotReg[st.Dst] = st.Reg
		}
		for _, r := range [2]uint16{r1, r2} {
			if r != 0 && r != st.Reg {
				free = append(free, r)
			}
		}
	}
	plan.NumRegs = count
}
