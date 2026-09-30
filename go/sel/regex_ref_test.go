package sel

// The pre-GO-P5 regex walk (rune slices, a per-byte offset table, a folded copy of
// the subject), kept as the reference the byte-offset implementation is held to.

import (
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"
)

func refFoldSubject(runes []rune) []rune {
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

type refRegexMatch struct {
	startCp int
	endCp   int
	groups  [][2]int // startCp, endCp for each submatch (-1 if didn't participate)
}

// refFindMatches walks the subject left to right (§7.8). At each position it takes
// the leftmost match; after an empty match at s the scan resumes at s+1, having
// copied that code point through, and after a non-empty match it resumes at the
// match end, where an empty match is allowed. Go's own FindAll drops an empty
// match that abuts the one before it, which is the one thing this walk differs in.
func refFindMatches(cr *compiledRegex, subjectRunes, searchRunes []rune, limit int) []refRegexMatch {
	searchStr := string(searchRunes)
	// runeAt maps a byte offset of searchStr to its code point index.
	runeAt := make([]int32, len(searchStr)+1)
	curRune := int32(0)
	for byteOff := range searchStr {
		runeAt[byteOff] = curRune
		curRune++
	}
	runeAt[len(searchStr)] = curRune

	var matches []refRegexMatch
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
		matches = append(matches, refRegexMatch{
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

func refExpandRepl(repl string, m refRegexMatch, numGroups int, origRunes []rune, pos Pos) string {
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

// refRegexBuiltins answers the four regex built-ins the way the retired code did.
func refRMatch(cr *compiledRegex, subj string, ic bool) bool {
	orig := []rune(subj)
	search := orig
	if ic {
		search = refFoldSubject(orig)
	}
	return len(refFindMatches(cr, orig, search, 1)) > 0
}

func refRFind(cr *compiledRegex, subj string, ic bool) int {
	orig := []rune(subj)
	search := orig
	if ic {
		search = refFoldSubject(orig)
	}
	ms := refFindMatches(cr, orig, search, 1)
	if len(ms) == 0 {
		return 0
	}
	return ms[0].startCp + 1
}

func refRGroups(cr *compiledRegex, subj string, ic bool) []string {
	orig := []rune(subj)
	search := orig
	if ic {
		search = refFoldSubject(orig)
	}
	ms := refFindMatches(cr, orig, search, 1)
	if len(ms) == 0 {
		return nil
	}
	out := make([]string, len(ms[0].groups))
	for i, g := range ms[0].groups {
		if g[0] >= 0 {
			out[i] = string(orig[g[0]:g[1]])
		}
	}
	return out
}

func refRReplace(cr *compiledRegex, repl, subj string, ic bool) string {
	orig := []rune(subj)
	search := orig
	if ic {
		search = refFoldSubject(orig)
	}
	ms := refFindMatches(cr, orig, search, 0)
	if len(ms) == 0 {
		return subj
	}
	var out strings.Builder
	last := 0
	for _, m := range ms {
		out.WriteString(string(orig[last:m.startCp]))
		out.WriteString(refExpandRepl(repl, m, cr.re.NumSubexp()+1, orig, Pos{}))
		last = m.endCp
	}
	out.WriteString(string(orig[last:]))
	return out.String()
}

// BenchmarkP5Reference is the BEFORE of GO-P5: the retired rune walk on the same
// subjects as BenchmarkP5Regex (which measures the built-ins as they are now).
func BenchmarkP5Reference(b *testing.B) {
	for _, reps := range []int{62500, 125000, 250000} {
		sub := strings.Repeat("123-45,", reps)
		cr, _ := compileRegex(`.`, "", Pos{}, Pos{})
		cf, _ := compileRegex(`5,$`, "", Pos{}, Pos{})
		cg, _ := compileRegex(`(\d+)-(\d+)`, "", Pos{}, Pos{})
		b.Run(fmt.Sprintf("rmatch_dot/chars=%d", len(sub)), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				refRMatch(cr, sub, false)
			}
		})
		b.Run(fmt.Sprintf("rfind_late/chars=%d", len(sub)), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				refRFind(cf, sub, false)
			}
		})
		b.Run(fmt.Sprintf("rgroups/chars=%d", len(sub)), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				refRGroups(cg, sub, false)
			}
		})
	}
	for _, c := range []struct {
		name, pat string
		run       func(cr *compiledRegex)
	}{
		{"short_rmatch", `[0-9]+-[0-9]+`, func(cr *compiledRegex) { refRMatch(cr, "123-45", false) }},
		{"short_rfind", `-`, func(cr *compiledRegex) { refRFind(cr, "123-45", false) }},
		{"short_rgroups", `(\d+)-(\d+)`, func(cr *compiledRegex) { refRGroups(cr, "123-45", false) }},
		{"short_rreplace", `-`, func(cr *compiledRegex) { refRReplace(cr, "+", "123-45", false) }},
	} {
		cr, _ := compileRegex(c.pat, "", Pos{}, Pos{})
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				c.run(cr)
			}
		})
	}
}
