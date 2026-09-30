// Regular expression built-ins and pattern validator (§7.8).

package sel

import (
	"fmt"
	"regexp"
	"strings"
	"sync"
	"unicode/utf8"
)

// ValidatePattern validates against the SEL portable regex subset and returns
// the portable source: the pattern with \d \w \s expanded to explicit ASCII
// classes (§7.8). It raises E_REGEX_SYNTAX at pos.
func ValidatePattern(pattern string, pos Pos) string {
	return portableSource(parseRegex(pattern, pos))
}

// ValidatePatternFlags is ValidatePattern for a pattern that runs with the `i`
// flag when ignoreCase is set: the exponential-ambiguity rule folds case, so its
// verdict depends on the flag.
func ValidatePatternFlags(pattern string, ignoreCase bool, pos Pos) string {
	return portableSource(parseRegexIC(pattern, ignoreCase, pos))
}

// The compiled-pattern cache is bounded (§7.8): 256 patterns, the oldest
// evicted, so a rule that builds a pattern per row cannot grow it without limit.
const regexCacheSize = 256

type compiledRegex struct {
	re         *regexp.Regexp
	tail       *regexp.Regexp // the same pattern with ^ never matching, for a search resumed past offset 0
	ignoreCase bool
}

// regexKey is the cache key: a struct of a flag and the pattern, so a lookup hashes
// the pattern in place. The key used to be built with a string concatenation per
// call (GO-P19), one allocation for every RMATCH of every element.
type regexKey struct {
	ignoreCase bool
	pattern    string
}

var (
	regexMu    sync.RWMutex
	regexCache = make(map[regexKey]*compiledRegex)
	regexOrder []regexKey
)

func compileRegex(pattern, flags string, flagPos, patPos Pos) (*compiledRegex, bool) {
	ignoreCase := false
	for _, ch := range flags {
		// Only a lowercase i is a flag: not I, not U+0130 or U+0131, not the
		// Kelvin sign that a case-insensitive engine folds to k (§7.8).
		if ch == 'i' {
			ignoreCase = true
			continue
		}
		if ch == 'm' || ch == 's' || ch == 'M' || ch == 'S' {
			fail("E_BAD_ARG", fmt.Sprintf("flag %q is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject", string(ch)), flagPos)
		}
		fail("E_BAD_ARG", fmt.Sprintf("unknown regex flag %q", string(ch)), flagPos)
	}

	if ignoreCase {
		for _, r := range pattern {
			if r > 0x7F {
				fail("E_BAD_ARG", "the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript", flagPos)
			}
		}
	}

	key := regexKey{ignoreCase, pattern}
	regexMu.RLock()
	c, ok := regexCache[key]
	regexMu.RUnlock()
	if ok {
		return c, ignoreCase
	}

	tree := parseRegexIC(pattern, ignoreCase, patPos)
	prefix := "(?s)"
	if ignoreCase {
		prefix += "(?i)"
	}
	compile := func(startDead bool) (*regexp.Regexp, bool) {
		src, hasStart := emitRE2(tree, startDead, pattern, patPos)
		rx, err := regexp.Compile(prefix + src)
		if err != nil {
			fail("E_REGEX_SYNTAX", fmt.Sprintf("this engine cannot compile the pattern (%v) in /%s/", err, clipPattern(pattern)), patPos)
		}
		return rx, hasStart
	}
	rx, hasStart := compile(false)
	cr := &compiledRegex{re: rx, tail: rx, ignoreCase: ignoreCase}
	if hasStart {
		cr.tail, _ = compile(true)
	}

	regexMu.Lock()
	if _, ok := regexCache[key]; !ok {
		if len(regexOrder) >= regexCacheSize {
			delete(regexCache, regexOrder[0])
			regexOrder = regexOrder[1:]
		}
		regexCache[key] = cr
		regexOrder = append(regexOrder, key)
	}
	regexMu.Unlock()

	return cr, ignoreCase
}

// matchSpans walks the subject left to right (§7.8) over BYTE offsets of the
// original string: no rune slices, no offset table, no folded copy. At each
// position it takes the leftmost match; after an empty match at s the scan
// resumes at s+1, having copied that code point through, and after a non-empty
// match it resumes at the match end, where an empty match is allowed. Go's own
// FindAll drops an empty match that abuts the one before it, which is the one
// thing this walk differs in. visit gets the submatch byte offsets (as the
// regexp package reports them, shifted to the whole subject) and returns false to
// stop. Case folding needs no subject copy: the engine's (?i) already folds U+212A
// to k and U+017F to s, which is what the retired foldSubject did by hand.
func matchSpans(cr *compiledRegex, subj string, visit func(m []int) bool) {
	pos := 0
	for pos <= len(subj) {
		re := cr.re
		if pos > 0 {
			re = cr.tail
		}
		m := re.FindStringSubmatchIndex(subj[pos:])
		if m == nil {
			return
		}
		if pos > 0 {
			for i := range m {
				if m[i] >= 0 {
					m[i] += pos
				}
			}
		}
		if !visit(m) {
			return
		}
		start, end := m[0], m[1]
		if end > start {
			pos = end
			continue
		}
		// An empty match: step over one code point.
		if end >= len(subj) {
			return
		}
		_, w := utf8.DecodeRuneInString(subj[end:])
		pos = end + w
	}
}

func regexArgs(args *Args, patIdx, subjIdx, flagIdx int) (*compiledRegex, string) {
	pat := args.Text(patIdx)
	subj := args.Text(subjIdx)
	flags := ""
	flagPos := args.Pos()
	if args.Count() > flagIdx {
		flags = args.Text(flagIdx)
		flagPos = args.PosOf(flagIdx)
	}
	cr, _ := compileRegex(pat, flags, flagPos, args.PosOf(patIdx))
	return cr, subj
}

// expandRepl expands $n in the replacement; group spans are byte offsets into subj.
func expandRepl(repl string, m []int, numGroups int, subj string, pos Pos) string {
	if strings.IndexByte(repl, '$') < 0 {
		return repl
	}
	var out strings.Builder
	i := 0
	for i < len(repl) {
		if repl[i] != '$' {
			out.WriteByte(repl[i])
			i++
			continue
		}
		if i+1 < len(repl) {
			nxt := repl[i+1]
			if nxt == '$' {
				out.WriteByte('$')
				i += 2
				continue
			}
			if nxt >= '0' && nxt <= '9' {
				g := int(nxt - '0')
				if g >= numGroups {
					fail("E_BAD_ARG", fmt.Sprintf("replacement refers to $%d but the pattern has %d groups", g, numGroups-1), pos)
				}
				if m[2*g] >= 0 {
					out.WriteString(subj[m[2*g]:m[2*g+1]])
				}
				i += 2
				continue
			}
		}
		out.WriteByte('$')
		i++
	}
	return out.String()
}

func init() {
	Define(&Spec{
		Name: "RMATCH",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, subj := regexArgs(args, 0, 1, 2)
			return NewBool(cr.re.MatchString(subj))
		},
	})

	Define(&Spec{
		Name: "RFIND",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, subj := regexArgs(args, 0, 1, 2)
			loc := cr.re.FindStringIndex(subj)
			if loc == nil {
				return NewInt(0)
			}
			// Only the one offset that is reported is converted to a code point index.
			return NewInt(int64(utf8.RuneCountInString(subj[:loc[0]]) + 1))
		},
	})

	Define(&Spec{
		Name: "RGROUPS",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, subj := regexArgs(args, 0, 1, 2)
			m := cr.re.FindStringSubmatchIndex(subj)
			if m == nil {
				return NewNone()
			}
			items := make([]*Value, len(m)/2)
			for i := range items {
				if m[2*i] < 0 {
					items[i] = NewTextOwned("")
				} else {
					items[i] = NewTextOwned(subj[m[2*i]:m[2*i+1]])
				}
			}
			return NewListOwned(items)
		},
	})

	Define(&Spec{
		Name: "RREPLACE",
		Min:  3,
		Max:  4,
		Fn: func(args *Args, ctx *Context) *Value {
			pat := args.Text(0)
			repl := args.Text(1)
			subj := args.Text(2)
			flags := ""
			flagPos := args.Pos()
			if args.Count() > 3 {
				flags = args.Text(3)
				flagPos = args.PosOf(3)
			}
			cr, _ := compileRegex(pat, flags, flagPos, args.PosOf(0))

			numGroups := 1
			if cr.re != nil {
				numGroups = cr.re.NumSubexp() + 1
			}

			var out strings.Builder
			last := 0         // byte offset of the end of the copied prefix
			built := int64(0) // code points written so far (SPEC §6.4)
			matched := false
			replPos := args.PosOf(1)
			matchSpans(cr, subj, func(m []int) bool {
				matched = true
				seg := subj[last:m[0]]
				out.WriteString(seg)
				built += runeLen(seg)
				piece := expandRepl(repl, m, numGroups, subj, replPos)
				built = satAdd(built, runeLen(piece))
				checkTextLen(built, "RREPLACE's result", args.Pos())
				out.WriteString(piece)
				last = m[1]
				return true
			})
			if !matched {
				return NewTextOwned(subj)
			}
			tail := subj[last:]
			built += runeLen(tail)
			checkTextLen(built, "RREPLACE's result", args.Pos())
			out.WriteString(tail)
			return NewTextOwned(out.String())
		},
	})
}
