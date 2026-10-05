package sel

import (
	"regexp/syntax"
	"unicode"
	"unicode/utf8"
)

// A matcher for the patterns RE2 cannot hold (§7.8). Counts nest freely in the
// language — `^(?:(?:(?:ab){1000}){1000}){2}$` is legal, its minimum length within
// MAX_TEXT_LEN — but RE2 writes every counted copy into its program and refuses
// one past a few million instructions. Such a pattern runs here instead: the
// validated tree is walked by a backtracking matcher that keeps a counter per
// repeat rather than copies, with an explicit, persistent task list and a choice
// stack, so it neither recurses nor grows with the counts. Choices are tried in
// the order a backtracking engine tries them (greedy before lazy, the left
// branch first), which picks the same leftmost-first match RE2 reports; the
// §7.8 refusals (no nullable loop body, no capture skipping an iteration, no
// exponential ambiguity) have already run on the tree, so every loop iteration
// consumes text and the walk ends.
//
// A leaf's character set is what RE2 itself makes of the leaf's source under the
// same flags, so the two engines agree on what one character matches.

type ctrNode struct {
	kind   reKind
	subs   []*ctrNode
	idx    int // capture index of a capturing group
	lo, hi int // reRep bounds; hi < 0 is unbounded
	lazy   bool
	minLen int64 // in code points; a lower bound on the bytes too

	any   bool      // reAny: every code point
	ascii [2]uint64 // the leaf's set below 128
	wide  []rng     // the leaf's set from 128 up
}

type counterRegex struct {
	root   *ctrNode
	groups int // capture groups, as Regexp.NumSubexp counts them
}

// newCounterRegex builds the matcher for a validated tree.
func newCounterRegex(tree *reNode, ignoreCase bool) *counterRegex {
	c := &counterRegex{}
	c.root = c.build(tree, ignoreCase)
	return c
}

func (c *counterRegex) build(n *reNode, ic bool) *ctrNode {
	out := &ctrNode{kind: n.kind, idx: n.idx, lo: n.lo, hi: n.hi, lazy: n.lazy, minLen: n.minLen}
	if n.idx > c.groups {
		c.groups = n.idx
	}
	switch n.kind {
	case reAny:
		out.any = true
	case reLit, reClass:
		for _, r := range leafSet(n.text, ic) {
			for ch := r.lo; ch <= r.hi && ch < 128; ch++ {
				out.ascii[ch>>6] |= 1 << (ch & 63)
			}
			if r.hi >= 128 {
				out.wide = append(out.wide, rng{max(r.lo, 128), r.hi})
			}
		}
	}
	for _, s := range n.subs {
		out.subs = append(out.subs, c.build(s, ic))
	}
	return out
}

// leafSet is the set of code points RE2 matches with the leaf's portable source
// under the flags the RE2 path compiles with: (?s), and (?i) for the i flag.
func leafSet(text string, ic bool) []rng {
	flags := syntax.Perl | syntax.DotNL
	if ic {
		flags |= syntax.FoldCase
	}
	re, err := syntax.Parse(text, flags)
	if err != nil {
		panic("sel: a validated regex leaf does not parse: " + err.Error())
	}
	re = re.Simplify()
	switch re.Op {
	case syntax.OpAnyChar:
		return []rng{{0, maxCodePoint}}
	case syntax.OpCharClass:
		out := make([]rng, 0, len(re.Rune)/2)
		for i := 0; i+1 < len(re.Rune); i += 2 {
			out = append(out, rng{re.Rune[i], re.Rune[i+1]})
		}
		return out
	case syntax.OpNoMatch:
		return nil
	case syntax.OpLiteral:
		if len(re.Rune) == 1 {
			r := re.Rune[0]
			out := []rng{{r, r}}
			if re.Flags&syntax.FoldCase != 0 {
				for f := unicode.SimpleFold(r); f != r; f = unicode.SimpleFold(f) {
					out = append(out, rng{f, f})
				}
			}
			return normRanges(out)
		}
	}
	panic("sel: a validated regex leaf is not one character: " + text)
}

func (n *ctrNode) has(ch rune) bool {
	switch {
	case n.any:
		return true
	case ch < 128:
		return n.ascii[ch>>6]&(1<<(ch&63)) != 0
	}
	return hasRune(n.wide, ch)
}

const (
	ctrMatch  = iota // match a node
	ctrEndCap        // close a capture opened at byte offset task.count
	ctrRepeat        // decide on iteration task.count+1 of a repeat
)

// ctrTask is one cell of the persistent list of what is left to match: a choice
// point keeps the list it resumes with by pointer, never by copy.
type ctrTask struct {
	n     *ctrNode
	op    int
	count int
	next  *ctrTask
}

type ctrChoice struct {
	tasks *ctrTask
	off   int
	trail int
}

type ctrUndo struct{ slot, old int }

type ctrRun struct {
	subj    string
	groups  []int // byte offsets, two per group, -1 when unset
	trail   []ctrUndo
	choices []ctrChoice
}

func (r *ctrRun) set(slot, v int) {
	r.trail = append(r.trail, ctrUndo{slot, r.groups[slot]})
	r.groups[slot] = v
}

// find returns the leftmost match starting at byte offset from or later, as the
// submatch offsets FindStringSubmatchIndex reports, or nil. ^ and $ are the ends
// of the whole subject.
func (c *counterRegex) find(subj string, from int) []int {
	r := &ctrRun{subj: subj, groups: make([]int, 2*(c.groups+1))}
	for start := from; start <= len(subj); {
		if c.root.minLen > int64(len(subj)-start) {
			return nil
		}
		if m := r.at(c.root, start); m != nil {
			return m
		}
		if start == len(subj) {
			break
		}
		_, w := utf8.DecodeRuneInString(subj[start:])
		start += w
	}
	return nil
}

func (r *ctrRun) at(root *ctrNode, start int) []int {
	for i := range r.groups {
		r.groups[i] = -1
	}
	r.trail = r.trail[:0]
	r.choices = r.choices[:0]
	tasks := &ctrTask{n: root}
	off := start
	subj := r.subj
	for {
		ok := true
		for tasks != nil && ok {
			t := tasks
			tasks = t.next
			remaining := int64(len(subj) - off)
			switch t.op {
			case ctrEndCap:
				r.set(2*t.n.idx, t.count)
				r.set(2*t.n.idx+1, off)
			case ctrRepeat:
				n := t.n
				body := n.subs[0]
				count := t.count
				if need := n.lo - count; need > 0 && satMulLen(body.minLen, int64(need)) > remaining {
					ok = false
					break
				}
				canStop := count >= n.lo
				canRepeat := (n.hi < 0 || count < n.hi) && body.minLen <= remaining
				if !canRepeat {
					ok = canStop
					break
				}
				again := &ctrTask{n: body, next: &ctrTask{n: n, op: ctrRepeat, count: count + 1, next: tasks}}
				if !canStop {
					tasks = again
					break
				}
				if n.lazy {
					r.choices = append(r.choices, ctrChoice{again, off, len(r.trail)})
				} else {
					r.choices = append(r.choices, ctrChoice{tasks, off, len(r.trail)})
					tasks = again
				}
			default:
				n := t.n
				if n.minLen > remaining {
					ok = false
					break
				}
				switch n.kind {
				case reLit, reAny, reClass:
					ch, w := utf8.DecodeRuneInString(subj[off:])
					if !n.has(ch) {
						ok = false
						break
					}
					off += w
				case reBOL:
					ok = off == 0
				case reEOL:
					ok = off == len(subj)
				case reCat:
					for i := len(n.subs) - 1; i >= 0; i-- {
						tasks = &ctrTask{n: n.subs[i], next: tasks}
					}
				case reAlt:
					for i := len(n.subs) - 1; i >= 1; i-- {
						r.choices = append(r.choices, ctrChoice{&ctrTask{n: n.subs[i], next: tasks}, off, len(r.trail)})
					}
					tasks = &ctrTask{n: n.subs[0], next: tasks}
				case reGroup:
					if n.idx > 0 {
						tasks = &ctrTask{n: n, op: ctrEndCap, count: off, next: tasks}
					}
					tasks = &ctrTask{n: n.subs[0], next: tasks}
				case reRep:
					tasks = &ctrTask{n: n, op: ctrRepeat, next: tasks}
				}
			}
		}
		if ok {
			m := append([]int(nil), r.groups...)
			m[0], m[1] = start, off
			return m
		}
		if len(r.choices) == 0 {
			return nil
		}
		ch := r.choices[len(r.choices)-1]
		r.choices = r.choices[:len(r.choices)-1]
		for len(r.trail) > ch.trail {
			u := r.trail[len(r.trail)-1]
			r.groups[u.slot] = u.old
			r.trail = r.trail[:len(r.trail)-1]
		}
		tasks, off = ch.tasks, ch.off
	}
}
