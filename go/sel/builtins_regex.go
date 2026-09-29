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

var (
	regexMu    sync.Mutex
	regexCache = make(map[string]*compiledRegex)
	regexOrder []string
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

	key := fmt.Sprintf("%v:%s", ignoreCase, pattern)
	regexMu.Lock()
	if c, ok := regexCache[key]; ok {
		regexMu.Unlock()
		return c, ignoreCase
	}
	regexMu.Unlock()

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

func foldSubject(runes []rune) []rune {
	out := make([]rune, len(runes))
	for i, r := range runes {
		if r == 0x212A { // Kelvin sign
			out[i] = 'k'
		} else if r == 0x017F { // Latin small letter sharp s
			out[i] = 's'
		} else {
			out[i] = r
		}
	}
	return out
}

type regexMatch struct {
	startCp int
	endCp   int
	groups  [][2]int // startCp, endCp for each submatch (-1 if didn't participate)
}

// findMatches walks the subject left to right (§7.8). At each position it takes
// the leftmost match; after an empty match at s the scan resumes at s+1, having
// copied that code point through, and after a non-empty match it resumes at the
// match end, where an empty match is allowed. Go's own FindAll drops an empty
// match that abuts the one before it, which is the one thing this walk differs in.
func findMatches(cr *compiledRegex, subjectRunes, searchRunes []rune, limit int) []regexMatch {
	searchStr := string(searchRunes)
	// runeAt maps a byte offset of searchStr to its code point index.
	runeAt := make([]int32, len(searchStr)+1)
	curRune := int32(0)
	for byteOff := range searchStr {
		runeAt[byteOff] = curRune
		curRune++
	}
	runeAt[len(searchStr)] = curRune

	var matches []regexMatch
	pos := 0 // byte offset of the current scan position
	for pos <= len(searchStr) {
		re := cr.re
		if pos > 0 {
			re = cr.tail
		}
		m := re.FindStringSubmatchIndex(searchStr[pos:])
		if m == nil {
			break
		}
		numGroups := len(m) / 2
		groups := make([][2]int, numGroups)
		for g := 0; g < numGroups; g++ {
			gs, ge := m[2*g], m[2*g+1]
			if gs < 0 || ge < 0 {
				groups[g] = [2]int{-1, -1}
			} else {
				groups[g] = [2]int{int(runeAt[pos+gs]), int(runeAt[pos+ge])}
			}
		}
		start, end := pos+m[0], pos+m[1]
		matches = append(matches, regexMatch{
			startCp: int(runeAt[start]),
			endCp:   int(runeAt[end]),
			groups:  groups,
		})
		if limit > 0 && len(matches) >= limit {
			break
		}
		if end > start {
			pos = end
			continue
		}
		// An empty match: step over one code point.
		if end >= len(searchStr) {
			break
		}
		_, w := utf8.DecodeRuneInString(searchStr[end:])
		pos = end + w
	}
	return matches
}

func regexArgs(args *Args, patIdx, subjIdx, flagIdx int) (*compiledRegex, []rune, []rune) {
	pat := args.Text(patIdx)
	subj := args.Text(subjIdx)
	flags := ""
	flagPos := args.Pos()
	if args.Count() > flagIdx {
		flags = args.Text(flagIdx)
		flagPos = args.PosOf(flagIdx)
	}
	cr, ignoreCase := compileRegex(pat, flags, flagPos, args.PosOf(patIdx))
	origRunes := []rune(subj)
	searchRunes := origRunes
	if ignoreCase {
		searchRunes = foldSubject(origRunes)
	}
	return cr, origRunes, searchRunes
}

func expandRepl(repl string, m regexMatch, numGroups int, origRunes []rune, pos Pos) string {
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
				span := m.groups[g]
				if span[0] >= 0 {
					out.WriteString(string(origRunes[span[0]:span[1]]))
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
			cr, orig, search := regexArgs(args, 0, 1, 2)
			matches := findMatches(cr, orig, search, 1)
			return NewBool(len(matches) > 0)
		},
	})

	Define(&Spec{
		Name: "RFIND",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, orig, search := regexArgs(args, 0, 1, 2)
			matches := findMatches(cr, orig, search, 1)
			if len(matches) == 0 {
				return NewInt(0)
			}
			return NewInt(int64(matches[0].startCp + 1))
		},
	})

	Define(&Spec{
		Name: "RGROUPS",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, orig, search := regexArgs(args, 0, 1, 2)
			matches := findMatches(cr, orig, search, 1)
			if len(matches) == 0 {
				return NewNone()
			}
			first := matches[0]
			items := make([]*Value, len(first.groups))
			for i, g := range first.groups {
				if g[0] < 0 {
					items[i] = NewTextOwned("")
				} else {
					items[i] = NewTextOwned(string(orig[g[0]:g[1]]))
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
			cr, ignoreCase := compileRegex(pat, flags, flagPos, args.PosOf(0))
			origRunes := []rune(subj)
			searchRunes := origRunes
			if ignoreCase {
				searchRunes = foldSubject(origRunes)
			}
			matches := findMatches(cr, origRunes, searchRunes, 0)
			if len(matches) == 0 {
				return NewTextOwned(subj)
			}

			numGroups := 1
			if cr.re != nil {
				numGroups = cr.re.NumSubexp() + 1
			}

			var out strings.Builder
			last := 0
			built := int64(0) // code points written so far (SPEC §6.4)
			for _, m := range matches {
				out.WriteString(string(origRunes[last:m.startCp]))
				built += int64(m.startCp - last)
				piece := expandRepl(repl, m, numGroups, origRunes, args.PosOf(1))
				built = satAdd(built, runeLen(piece))
				checkTextLen(built, "RREPLACE's result", args.Pos())
				out.WriteString(piece)
				last = m.endCp
			}
			built += int64(len(origRunes) - last)
			checkTextLen(built, "RREPLACE's result", args.Pos())
			out.WriteString(string(origRunes[last:]))
			return NewTextOwned(out.String())
		},
	})
}
