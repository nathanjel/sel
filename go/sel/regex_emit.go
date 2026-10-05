package sel

import (
	"fmt"
	"strings"
)

// portableSource writes a validated tree back as SEL's portable pattern text:
// the original pattern with \d \w \s expanded to explicit ASCII classes. It is
// what the SQL translator hands a database (§7.8).
func portableSource(n *reNode) string {
	var b strings.Builder
	writePortable(&b, n)
	return b.String()
}

func writePortable(b *strings.Builder, n *reNode) {
	switch n.kind {
	case reEmpty:
	case reLit, reAny, reClass, reBOL, reEOL:
		b.WriteString(n.text)
	case reCat:
		for _, s := range n.subs {
			writePortable(b, s)
		}
	case reAlt:
		for i, s := range n.subs {
			if i > 0 {
				b.WriteByte('|')
			}
			writePortable(b, s)
		}
	case reGroup:
		if n.idx > 0 {
			b.WriteByte('(')
		} else {
			b.WriteString("(?:")
		}
		writePortable(b, n.subs[0])
		b.WriteByte(')')
	case reRep:
		writePortable(b, n.subs[0])
		b.WriteString(n.text)
	}
}

// ---- RE2 source ---------------------------------------------------------------
//
// Go's regexp is RE2: a counted repeat is at most 1000, and nested counts may not
// multiply past 1000, but the language allows counts to 65,535 and nests them
// freely. The emitter therefore writes a large or nested count as a
// concatenation of copies — the program RE2 builds for a counted repeat is the
// same size — and keeps a capture group's number by giving every copy but the
// last non-capturing (a repeated group reports its last iteration).

const (
	re2MaxCount = 1000
	// A pattern whose emitted source would pass this many bytes is not handed to
	// RE2: the program it would build is past what it can compile anyway.
	re2MaxSource = 16 << 20
	// Matches nothing: the class of no code point.
	re2Dead = `[^\x00-\x{10FFFF}]`
)

type re2Emitter struct {
	startDead bool // emit ^ as never matching (a search resumed past the start)
	size      int
	hasStart  bool
}

// re2TooLarge is what grow panics with when the source passes re2MaxSource;
// emitRE2 turns it into ok == false.
type re2TooLarge struct{}

// emitRE2 returns the RE2 source of the tree, with ^ and $ lowered to \A and \z.
// ok is false when the source would pass re2MaxSource: the pattern is legal, but
// RE2 cannot hold it, and it runs on the counter matcher (regex_counter.go).
func emitRE2(n *reNode, startDead bool) (src string, hasStart, ok bool) {
	e := &re2Emitter{startDead: startDead}
	defer func() {
		if r := recover(); r != nil {
			if _, big := r.(re2TooLarge); !big {
				panic(r)
			}
			src, hasStart, ok = "", false, false
		}
	}()
	out, _ := e.node(n, false)
	return out, e.hasStart, true
}

func (e *re2Emitter) grow(k int) {
	e.size += k
	if e.size > re2MaxSource {
		panic(re2TooLarge{})
	}
}

// node returns the source of n and the product of the native counted repeats
// nested in it. strip writes capture groups as non-capturing.
func (e *re2Emitter) node(n *reNode, strip bool) (string, int) {
	switch n.kind {
	case reEmpty:
		return "", 1
	case reLit, reAny, reClass:
		e.grow(len(n.text))
		return n.text, 1
	case reBOL:
		e.hasStart = true
		if e.startDead {
			return re2Dead, 1
		}
		return `\A`, 1
	case reEOL:
		return `\z`, 1
	case reCat:
		var b strings.Builder
		prod := 1
		for _, s := range n.subs {
			t, p := e.node(s, strip)
			b.WriteString(t)
			if p > prod {
				prod = p
			}
		}
		return b.String(), prod
	case reAlt:
		var b strings.Builder
		prod := 1
		for i, s := range n.subs {
			if i > 0 {
				b.WriteByte('|')
			}
			t, p := e.node(s, strip)
			b.WriteString(t)
			if p > prod {
				prod = p
			}
		}
		return b.String(), prod
	case reGroup:
		t, p := e.node(n.subs[0], strip)
		if n.idx > 0 && !strip {
			return "(" + t + ")", p
		}
		return "(?:" + t + ")", p
	case reRep:
		return e.rep(n, strip)
	}
	return "", 1
}

func lazySuffix(n *reNode) string {
	if n.lazy {
		return "?"
	}
	return ""
}

func (e *re2Emitter) rep(n *reNode, strip bool) (string, int) {
	child := n.subs[0]
	lo, hi := n.lo, n.hi
	// A repeat that cannot be satisfied by any text a value may hold matches
	// nothing (a host never hands a rule text past the text cap).
	if lo > 0 && satMulLen(child.minLen, int64(lo)) > maxTextLen {
		return re2Dead, 1
	}
	body, prod := e.node(child, strip)
	switch {
	case lo == 0 && hi == 0:
		return "", 1
	case lo == 1 && hi == 1:
		return body, prod
	case lo == 0 && hi < 0:
		return body + "*" + lazySuffix(n), prod
	case lo == 1 && hi < 0:
		return body + "+" + lazySuffix(n), prod
	case lo == 0 && hi == 1:
		return body + "?" + lazySuffix(n), prod
	}
	top := hi
	if top < 0 || lo > top {
		top = lo
	}
	if top <= re2MaxCount && prod*top <= re2MaxCount {
		var q string
		switch {
		case hi < 0:
			q = fmt.Sprintf("{%d,}", lo)
		case lo == hi:
			q = fmt.Sprintf("{%d}", lo)
		default:
			q = fmt.Sprintf("{%d,%d}", lo, hi)
		}
		e.grow(len(body) + len(q))
		return body + q + lazySuffix(n), prod * top
	}

	// Expanded. When the body holds captures only the LAST iteration may keep
	// them: the leading copies are written non-capturing, so the group numbers
	// stay what the pattern says.
	last := body
	lead := body
	if child.hasCap && !strip {
		lead, _ = e.node(child, true)
	}
	// wrap makes a copy a single quantifiable unit.
	wrap := func(s string) string { return "(?:" + s + ")" }
	chunk := re2MaxCount / prod
	if chunk < 1 {
		chunk = 1
	}
	outProd := prod // the largest product of native counts inside what is written
	// mand writes c repeated k times, as chunks the engine accepts.
	mand := func(c string, k int) string {
		var b strings.Builder
		w := wrap(c)
		for k > 0 {
			step := k
			if step > chunk {
				step = chunk
			}
			e.grow(len(w) + 8)
			if step == 1 {
				b.WriteString(w)
			} else {
				fmt.Fprintf(&b, "%s{%d}", w, step)
				if step*prod > outProd {
					outProd = step * prod
				}
			}
			k -= step
		}
		return b.String()
	}
	// opt writes c repeated 0..k times. A run of k optional copies in one piece
	// would let the engine keep one thread alive per way of splitting the count
	// (quadratic work on a long subject), so the count is nested instead: after j
	// full blocks the match either takes one more full block or finishes with a
	// partial one, and exactly one chain of blocks is ever live. Greedy tries the
	// longer alternative first and lazy the shorter, which is the order a plain
	// counted repeat tries its counts in.
	opt := func(c string, k int) string {
		if k <= 0 {
			return ""
		}
		w := wrap(c)
		lz := lazySuffix(n)
		count := func(m int) string {
			switch {
			case m <= 0:
				return ""
			case m == 1:
				return w + "?" + lz
			}
			if m*prod > outProd {
				outProd = m * prod
			}
			return fmt.Sprintf("%s{0,%d}%s", w, m, lz)
		}
		full := func(inner string) string {
			if chunk == 1 {
				return w + inner
			}
			if chunk*prod > outProd {
				outProd = chunk * prod
			}
			return fmt.Sprintf("%s{%d}%s", w, chunk, inner)
		}
		levels := 0
		for rem := k; rem > chunk; rem -= chunk {
			levels++
		}
		rem := k - levels*chunk
		e.grow((levels*2 + 2) * (len(w) + 16))
		out := count(rem)
		for l := 0; l < levels; l++ {
			partial := count(chunk - 1)
			if n.lazy {
				out = "(?:" + partial + "|" + full(out) + ")"
			} else {
				out = "(?:" + full(out) + "|" + partial + ")"
			}
		}
		return out
	}

	if child.hasCap && !strip {
		// (x){lo,hi}  ==  (?:x'){lo-1,hi-1}(x)   for lo >= 1
		//              ==  (?:(?:x'){0,hi-1}(x))?  for lo == 0
		if lo >= 1 {
			var b strings.Builder
			b.WriteString(mand(lead, lo-1))
			switch {
			case hi < 0:
				e.grow(len(lead) + 8)
				b.WriteString(wrap(lead) + "*" + lazySuffix(n))
			default:
				b.WriteString(opt(lead, hi-lo))
			}
			b.WriteString(wrap(last))
			return b.String(), outProd
		}
		// lo == 0, hi >= 2 (hi < 0 is the native *)
		return "(?:" + opt(lead, hi-1) + wrap(last) + ")?" + lazySuffix(n), outProd
	}

	var b strings.Builder
	b.WriteString(mand(lead, lo))
	if hi < 0 {
		e.grow(len(lead) + 8)
		b.WriteString(wrap(lead) + "*" + lazySuffix(n))
	} else {
		b.WriteString(opt(lead, hi-lo))
	}
	return b.String(), outProd
}
