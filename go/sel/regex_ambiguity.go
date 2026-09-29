// The exponential-ambiguity rule of SPEC §7.8: a pattern on which a backtracking
// engine can be made to take exponential time is refused, on every host, by one
// static analysis of the parse tree. It is a port of tools/regex-ambiguity-ref.py
// (counts and verdicts identical): a Glushkov position automaton over character
// classes, then five refusal causes — a follow edge generated twice, a nullable
// choice inside a loop, an ambiguity budget above 16, two different paths from a
// state back to itself on one word (EDA), and the analysis's own closed-form caps.
// Polynomial ambiguity is accepted on purpose.

package sel

import (
	"math/bits"
	"sort"
)

type rng struct{ lo, hi rune }

const (
	maxCodePoint = rune(0x10FFFF)

	reUnroll = 8
	reAmbMax = 16
	rePMax   = 1 << 17 // positions
	reEMax   = 1 << 18 // follow edges
	reDMax   = 1 << 21 // sum over edges of the range count at the target
	reQMax   = 1 << 20 // pair-graph work
)

// ---- range sets -------------------------------------------------------------

func normRanges(rs []rng) []rng {
	if len(rs) == 0 {
		return nil
	}
	c := append([]rng(nil), rs...)
	sort.Slice(c, func(i, j int) bool {
		if c[i].lo != c[j].lo {
			return c[i].lo < c[j].lo
		}
		return c[i].hi < c[j].hi
	})
	out := []rng{c[0]}
	for _, r := range c[1:] {
		last := &out[len(out)-1]
		if r.lo <= last.hi+1 {
			if r.hi > last.hi {
				last.hi = r.hi
			}
		} else {
			out = append(out, r)
		}
	}
	return out
}

func negateRanges(rs []rng) []rng {
	var out []rng
	next := rune(0)
	for _, r := range normRanges(rs) {
		if r.lo > next {
			out = append(out, rng{next, r.lo - 1})
		}
		next = r.hi + 1
	}
	if next <= maxCodePoint {
		out = append(out, rng{next, maxCodePoint})
	}
	return out
}

func hasRune(rs []rng, c rune) bool {
	for _, r := range rs {
		if r.lo <= c && c <= r.hi {
			return true
		}
	}
	return false
}

// foldRanges is `i`: simple case folding restricted to what an ASCII pattern can
// reach — the ASCII case mirror, plus U+212A with k/K and U+017F with s/S.
func foldRanges(rs []rng) []rng {
	extra := append([]rng(nil), rs...)
	for _, r := range rs {
		lo, hi := max(r.lo, 0x41), min(r.hi, 0x5A)
		if lo <= hi {
			extra = append(extra, rng{lo + 32, hi + 32})
		}
		lo, hi = max(r.lo, 0x61), min(r.hi, 0x7A)
		if lo <= hi {
			extra = append(extra, rng{lo - 32, hi - 32})
		}
	}
	all := normRanges(extra)
	if hasRune(all, 'k') || hasRune(all, 'K') {
		all = append(all, rng{0x212A, 0x212A})
	}
	if hasRune(all, 's') || hasRune(all, 'S') {
		all = append(all, rng{0x17F, 0x17F})
	}
	return normRanges(all)
}

func rangesIntersect(x, y []rng) bool {
	i, j := 0, 0
	for i < len(x) && j < len(y) {
		if x[i].hi < y[j].lo {
			i++
		} else if y[j].hi < x[i].lo {
			j++
		} else {
			return true
		}
	}
	return false
}

// maxCover is the largest number of the given classes that share one code point.
func maxCover(classes [][]rng) int {
	type ev struct {
		at rune
		d  int
	}
	var evs []ev
	for _, c := range classes {
		for _, r := range c {
			evs = append(evs, ev{r.lo, 1}, ev{r.hi + 1, -1})
		}
	}
	// Closings before openings at a shared point.
	sort.Slice(evs, func(i, j int) bool {
		if evs[i].at != evs[j].at {
			return evs[i].at < evs[j].at
		}
		return evs[i].d < evs[j].d
	})
	best, cur := 0, 0
	for _, e := range evs {
		cur += e.d
		if cur > best {
			best = cur
		}
	}
	return best
}

// escapeSet is the set of \d \w \s (either case: the upper-case forms negate).
func escapeSet(e rune) []rng {
	var base []rng
	switch e {
	case 'd', 'D':
		base = []rng{{0x30, 0x39}}
	case 'w', 'W':
		base = []rng{{0x30, 0x39}, {0x41, 0x5A}, {0x5F, 0x5F}, {0x61, 0x7A}}
	default: // s, S
		base = []rng{{0x09, 0x0D}, {0x20, 0x20}}
	}
	base = normRanges(base)
	if e == 'D' || e == 'W' || e == 'S' {
		return negateRanges(base)
	}
	return base
}

// ---- the position automaton -------------------------------------------------

type reAmb struct {
	cls  [][]rng
	succ [][]int
	tag  map[[2]int]bool
	e, d int
	amb  int

	pattern string
	pos     Pos
	ic      bool
}

func (a *reAmb) refuse(why string) {
	fail("E_REGEX_SYNTAX", "the pattern can take exponential time on some subject ("+why+") — rewrite it so that no two paths through it match the same text", a.pos)
}

// letterSet is the class of a leaf under the flag: `i` folds the members, and a
// bracket class negates after folding.
func (a *reAmb) letterSet(n *reNode) []rng {
	rs := normRanges(n.set)
	if a.ic {
		rs = foldRanges(rs)
	}
	if n.bracket && n.neg {
		rs = negateRanges(rs)
	}
	return rs
}

func (a *reAmb) join(last, first []int, sync bool) {
	a.e += len(last) * len(first)
	sum := 0
	for _, q := range first {
		sum += len(a.cls[q])
	}
	a.d += len(last) * sum
	if a.e > reEMax || a.d > reDMax {
		a.refuse("analysis edge cap")
	}
	for _, x := range last {
		for _, y := range first {
			key := [2]int{x, y}
			if t, ok := a.tag[key]; ok {
				if t && sync {
					continue
				}
				a.refuse("a follow edge is generated twice")
			}
			a.tag[key] = sync
			a.succ[x] = append(a.succ[x], y)
		}
	}
}

func (a *reAmb) eps(k int, inLoop bool) {
	if k > 0 {
		if inLoop {
			a.refuse("a nullable choice inside a loop")
		}
		a.amb += k
		if a.amb > reAmbMax {
			a.refuse("ambiguity budget")
		}
	}
}

// Internal node kinds used only while unrolling a bounded repeat.
const reOpt reKind = -1

func ceilLog2(k int) int { return bits.Len(uint(k - 1)) }

// walk returns (nullable, first, last) of a node, allocating positions for its
// letters. The tree is at most MAX_DEPTH groups deep, and a bounded repeat is
// unrolled to at most reUnroll copies, so the recursion is bounded.
func (a *reAmb) walk(n *reNode, inLoop bool) (bool, []int, []int) {
	switch n.kind {
	case reEmpty, reBOL, reEOL:
		return true, nil, nil
	case reLit, reAny, reClass:
		if len(a.cls) >= rePMax {
			a.refuse("position cap")
		}
		a.cls = append(a.cls, a.letterSet(n))
		a.succ = append(a.succ, nil)
		i := len(a.cls) - 1
		return false, []int{i}, []int{i}
	case reGroup:
		return a.walk(n.subs[0], inLoop)
	case reOpt:
		_, f, l := a.walk(n.subs[0], inLoop)
		return true, f, l
	case reAlt:
		nulls := 0
		for _, b := range n.subs {
			if b.nullable {
				nulls++
			}
		}
		if nulls >= 2 {
			a.eps(ceilLog2(nulls), inLoop)
		}
		var f, l []int
		for _, b := range n.subs {
			_, f2, l2 := a.walk(b, inLoop)
			f = append(f, f2...)
			l = append(l, l2...)
		}
		return nulls > 0, f, l
	case reCat:
		nl := true
		var f, l []int
		for _, it := range n.subs {
			n2, f2, l2 := a.walk(it, inLoop)
			a.join(l, f2, false)
			if nl {
				f = append(f[:len(f):len(f)], f2...)
			}
			if n2 {
				l = append(l[:len(l):len(l)], l2...)
			} else {
				l = l2
			}
			nl = nl && n2
		}
		return nl, f, l
	case reRep:
		x, lo, hi := n.subs[0], n.lo, n.hi
		if hi == 0 {
			return true, nil, nil
		}
		if hi >= 0 && hi <= reUnroll {
			if lo == 0 && hi == 1 && x.nullable {
				a.eps(1, inLoop)
			}
			tail := &reNode{kind: reEmpty}
			for i := 0; i < hi-lo; i++ {
				tail = &reNode{kind: reOpt, subs: []*reNode{{kind: reCat, subs: []*reNode{x, tail}}}}
			}
			items := make([]*reNode, 0, lo+1)
			for i := 0; i < lo; i++ {
				items = append(items, x)
			}
			items = append(items, tail)
			return a.walk(&reNode{kind: reCat, subs: items}, inLoop)
		}
		_, f, l := a.walk(x, true)
		a.join(l, f, x.minLen == x.maxLen && x.maxLen > 0 && x.maxLen < regexSat)
		return lo == 0 || x.nullable, f, l
	}
	panic("regex ambiguity: unknown node")
}

// sccs is an iterative Tarjan: a component id per node and whether the node
// lies on a cycle.
func sccs(succ [][]int) (comp []int, cyc []bool) {
	n := len(succ)
	index := make([]int, n)
	low := make([]int, n)
	on := make([]bool, n)
	comp = make([]int, n)
	for i := range index {
		index[i] = -1
		comp[i] = -1
	}
	var stack []int
	counter, ncomp := 0, 0
	type frame struct{ v, ei int }
	for root := 0; root < n; root++ {
		if index[root] != -1 {
			continue
		}
		work := []frame{{root, 0}}
		index[root], low[root] = counter, counter
		counter++
		stack = append(stack, root)
		on[root] = true
		for len(work) > 0 {
			top := &work[len(work)-1]
			v := top.v
			if top.ei < len(succ[v]) {
				w := succ[v][top.ei]
				top.ei++
				if index[w] == -1 {
					index[w], low[w] = counter, counter
					counter++
					stack = append(stack, w)
					on[w] = true
					work = append(work, frame{w, 0})
				} else if on[w] && index[w] < low[v] {
					low[v] = index[w]
				}
			} else {
				work = work[:len(work)-1]
				if len(work) > 0 {
					u := work[len(work)-1].v
					if low[v] < low[u] {
						low[u] = low[v]
					}
				}
				if low[v] == index[v] {
					for {
						w := stack[len(stack)-1]
						stack = stack[:len(stack)-1]
						on[w] = false
						comp[w] = ncomp
						if w == v {
							break
						}
					}
					ncomp++
				}
			}
		}
	}
	size := make([]int, ncomp)
	for v := 0; v < n; v++ {
		size[comp[v]]++
	}
	cyc = make([]bool, n)
	for v := 0; v < n; v++ {
		c := size[comp[v]] > 1
		if !c {
			for _, w := range succ[v] {
				if w == v {
					c = true
					break
				}
			}
		}
		cyc[v] = c
	}
	return comp, cyc
}

// checkAmbiguity applies §7.8's exponential-ambiguity rule to a parsed pattern.
func checkAmbiguity(tree *reNode, ignoreCase bool, pattern string, pos Pos) {
	a := &reAmb{cls: [][]rng{nil}, succ: [][]int{nil}, tag: map[[2]int]bool{}, pattern: pattern, pos: pos, ic: ignoreCase}
	_, first, _ := a.walk(tree, false)
	a.succ[0] = append([]int(nil), first...)
	succ, cls := a.succ, a.cls
	comp, cyc := sccs(succ)

	// (d) the budget: choices outside every cycle.
	for p := range succ {
		if !cyc[p] && len(succ[p]) >= 2 {
			classes := make([][]rng, len(succ[p]))
			for i, q := range succ[p] {
				classes[i] = cls[q]
			}
			if m := maxCover(classes); m >= 2 {
				a.amb += ceilLog2(m)
				if a.amb > reAmbMax {
					a.refuse("ambiguity budget")
				}
			}
		}
	}

	// (b) EDA: two different paths from a state to a state on one word.
	type pair struct{ p, r int }
	inSCC := map[int]int{}
	d := func(p int) int {
		if v, ok := inSCC[p]; ok {
			return v
		}
		c := 0
		for _, q := range succ[p] {
			if comp[q] == comp[p] {
				c++
			}
		}
		inSCC[p] = c
		return c
	}
	reach := map[pair]bool{}
	var order []pair
	for q := range succ {
		if cyc[q] && len(cls[q]) > 0 {
			pr := pair{q, q}
			reach[pr] = true
			order = append(order, pr)
		}
	}
	work := 0
	fwd := map[pair][]pair{}
	for len(order) > 0 {
		node := order[len(order)-1]
		order = order[:len(order)-1]
		work += d(node.p) * d(node.r)
		if work > reQMax {
			a.refuse("pair-graph cap")
		}
		var outs []pair
		for _, p2 := range succ[node.p] {
			if comp[p2] != comp[node.p] {
				continue
			}
			for _, r2 := range succ[node.r] {
				if comp[r2] != comp[node.p] || !rangesIntersect(cls[p2], cls[r2]) {
					continue
				}
				nx := pair{p2, r2}
				outs = append(outs, nx)
				if !reach[nx] {
					reach[nx] = true
					order = append(order, nx)
				}
			}
		}
		fwd[node] = outs
	}
	rev := map[pair][]pair{}
	for u, outs := range fwd {
		for _, v := range outs {
			rev[v] = append(rev[v], u)
		}
	}
	back := map[pair]bool{}
	var todo []pair
	for n := range reach {
		if n.p == n.r {
			back[n] = true
			todo = append(todo, n)
		}
	}
	for len(todo) > 0 {
		v := todo[len(todo)-1]
		todo = todo[:len(todo)-1]
		for _, u := range rev[v] {
			if !back[u] {
				back[u] = true
				todo = append(todo, u)
			}
		}
	}
	for n := range back {
		if n.p != n.r {
			a.refuse("exponential ambiguity")
		}
	}
}
