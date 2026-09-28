// Regular expression built-ins and pattern validator (§7.8).

package sel

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

const maxQuantifier = 65535

var expandOutside = map[byte]string{
	'd': "[0-9]", 'D': "[^0-9]",
	'w': "[0-9A-Za-z_]", 'W': "[^0-9A-Za-z_]",
	's': "[ \\t\\n\\r\\f\\x0b]", 'S': "[^ \\t\\n\\r\\f\\x0b]",
}

var expandInside = map[byte]string{
	'd': "0-9", 'w': "0-9A-Za-z_", 's': " \\t\\n\\r\\f\\x0b",
}

var controlEscapes = map[byte]bool{
	'n': true, 'r': true, 't': true, 'f': true,
}

var syntaxChars = map[byte]bool{
	'^': true, '$': true, '\\': true, '.': true, '*': true, '+': true, '?': true,
	'(': true, ')': true, '[': true, ']': true, '{': true, '}': true, '|': true, '/': true,
}

func badRegex(message, pattern string, at int, pos Pos) {
	fail("E_REGEX_SYNTAX", fmt.Sprintf("%s (at offset %d of /%s/)", message, at, pattern), pos)
}

func rejectEscape(e byte, pattern string, at int, pos Pos) {
	if e == 'b' || e == 'B' {
		badRegex(fmt.Sprintf("\\%c is not portable — word boundaries depend on the engine's idea of a word character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])", e), pattern, at, pos)
	}
	if e == 'v' {
		badRegex("\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B", pattern, at, pos)
	}
	if e >= '0' && e <= '9' {
		badRegex("backreferences are not portable", pattern, at, pos)
	}
	if e == 'p' || e == 'P' {
		badRegex("\\p{...} is not portable", pattern, at, pos)
	}
	if e == 'A' || e == 'z' || e == 'Z' || e == 'G' || e == 'K' {
		badRegex(fmt.Sprintf("\\%c is not portable — use ^ and $", e), pattern, at, pos)
	}
	badRegex(fmt.Sprintf("unsupported escape \\%c", e), pattern, at, pos)
}

// ValidatePattern validates against the SEL portable regex subset and expands character classes.
func ValidatePattern(pattern string, pos Pos) string {
	n := len(pattern)
	var out strings.Builder
	i := 0

	for i < n {
		c := pattern[i]

		if c == '\\' {
			if i+1 >= n {
				badRegex("trailing backslash", pattern, i, pos)
			}
			e := pattern[i+1]
			if exp, ok := expandOutside[e]; ok {
				out.WriteString(exp)
				i += 2
				continue
			}
			if controlEscapes[e] || syntaxChars[e] {
				out.WriteByte(c)
				out.WriteByte(e)
				i += 2
				continue
			}
			rejectEscape(e, pattern, i, pos)
		}

		if c == '[' {
			text, nxt := validateClass(pattern, i, pos)
			out.WriteString(text)
			i = nxt
			continue
		}

		if c == '(' {
			if i+1 < n && pattern[i+1] == '?' {
				nxt := byte(0)
				if i+2 < n {
					nxt = pattern[i+2]
				}
				if nxt == ':' {
					out.WriteString("(?:")
					i += 3
					continue
				}
				kind := "this group type"
				if nxt == '=' || nxt == '!' {
					kind = "lookahead"
				} else if nxt == '<' {
					kind = "lookbehind and named groups"
				} else if nxt == '>' {
					kind = "atomic groups"
				}
				badRegex(fmt.Sprintf("%s is not portable — only (?: ) is", kind), pattern, i, pos)
			}
			out.WriteByte('(')
			i++
			continue
		}

		if c == '{' {
			end := afterQuantifier(pattern, validateBraces(pattern, i, pos), pos)
			out.WriteString(pattern[i:end])
			i = end
			continue
		}

		if c == '*' || c == '+' || c == '?' {
			end := afterQuantifier(pattern, i+1, pos)
			out.WriteString(pattern[i:end])
			i = end
			continue
		}

		if c == '}' {
			badRegex("unmatched } — escape it as \\}", pattern, i, pos)
		}
		if c == ']' {
			badRegex("unmatched ] — escape it as \\]", pattern, i, pos)
		}

		out.WriteByte(c)
		i++
	}
	return out.String()
}

func afterQuantifier(p string, i int, pos Pos) int {
	if i < len(p) && p[i] == '+' {
		badRegex("possessive quantifiers are not portable", p, i, pos)
	}
	if i < len(p) && p[i] == '?' {
		return i + 1
	}
	return i
}

func validateBraces(p string, start int, pos Pos) int {
	i := start + 1
	loStart := i
	for i < len(p) && p[i] >= '0' && p[i] <= '9' {
		i++
	}
	if i == loStart {
		badRegex("{ must begin a quantifier such as {2,4} — escape it as \\{", p, start, pos)
	}
	lo, _ := strconv.Atoi(p[loStart:i])
	hasHi := false
	hi := 0
	if i < len(p) && p[i] == ',' {
		i++
		hiStart := i
		for i < len(p) && p[i] >= '0' && p[i] <= '9' {
			i++
		}
		if i > hiStart {
			hasHi = true
			hi, _ = strconv.Atoi(p[hiStart:i])
		}
	}
	if i >= len(p) || p[i] != '}' {
		badRegex("malformed quantifier", p, start, pos)
	}
	if lo > maxQuantifier || (hasHi && hi > maxQuantifier) {
		badRegex(fmt.Sprintf("quantifier bound exceeds the maximum of %d", maxQuantifier), p, start, pos)
	}
	if hasHi && hi < lo {
		badRegex(fmt.Sprintf("quantifier {%d,%d} is empty — the upper bound is below the lower one", lo, hi), p, start, pos)
	}
	return i + 1
}

func validateClass(p string, start int, pos Pos) (string, int) {
	i := start + 1
	var out strings.Builder
	out.WriteByte('[')
	if i < len(p) && p[i] == '^' {
		out.WriteByte('^')
		i++
	}
	if i+1 < len(p) && p[i] == '[' && p[i+1] == ':' {
		badRegex("POSIX classes such as [[:alpha:]] are not portable", p, i, pos)
	}
	count := 0
	for i < len(p) {
		c := p[i]
		if c == ']' {
			if count == 0 {
				badRegex("empty character class — write \\] for a literal bracket", p, start, pos)
			}
			out.WriteByte(']')
			return out.String(), i + 1
		}
		count++
		if c == '\\' {
			if i+1 >= len(p) {
				badRegex("trailing backslash in character class", p, i, pos)
			}
			e := p[i+1]
			if exp, ok := expandInside[e]; ok {
				out.WriteString(exp)
				i += 2
				continue
			}
			if e == 'D' || e == 'W' || e == 'S' {
				badRegex(fmt.Sprintf("\\%c inside a character class cannot be expressed portably — negate the whole class instead", e), p, i, pos)
			}
			if controlEscapes[e] || syntaxChars[e] || e == '-' {
				out.WriteByte(c)
				out.WriteByte(e)
				i += 2
				continue
			}
			rejectEscape(e, p, i, pos)
		}
		out.WriteByte(c)
		i++
	}
	badRegex("unterminated character class", p, start, pos)
	return "", i
}

func lowerAnchors(src string) string {
	var out strings.Builder
	i := 0
	n := len(src)
	inClass := false
	for i < n {
		c := src[i]
		if c == '\\' && i+1 < n {
			out.WriteString(src[i : i+2])
			i += 2
			continue
		}
		if inClass {
			if c == ']' {
				inClass = false
			}
			out.WriteByte(c)
			i++
			continue
		}
		if c == '[' {
			inClass = true
			out.WriteByte(c)
			i++
			if i < n && src[i] == '^' {
				out.WriteByte('^')
				i++
			}
			continue
		}
		if c == '^' {
			out.WriteString("\\A")
			i++
			continue
		}
		if c == '$' {
			out.WriteString("\\z")
			i++
			continue
		}
		out.WriteByte(c)
		i++
	}
	return out.String()
}

type compiledRegex struct {
	re         *regexp.Regexp
	ignoreCase bool
	unmatchable bool
	minLen     int
}

var (
	regexMu    sync.RWMutex
	regexCache = make(map[string]*compiledRegex)
)

func compileRegex(pattern, flags string, flagPos, patPos Pos) (*compiledRegex, bool) {
	ignoreCase := false
	for i := 0; i < len(flags); i++ {
		ch := flags[i]
		f := ch
		if f >= 'A' && f <= 'Z' {
			f += 32
		}
		if f == 'i' {
			ignoreCase = true
			continue
		}
		if f == 'm' || f == 's' {
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
	regexMu.RLock()
	if c, ok := regexCache[key]; ok {
		regexMu.RUnlock()
		return c, ignoreCase
	}
	regexMu.RUnlock()

	validated := ValidatePattern(pattern, patPos)
	lowered := lowerAnchors(validated)

	prefix := "(?s)"
	if ignoreCase {
		prefix += "(?i)"
	}
	rx, err := regexp.Compile(prefix + lowered)
	var cr *compiledRegex
	if err != nil {
		// Check if it's due to huge repetition count that RE2 refuses but SEL validated
		if strings.Contains(err.Error(), "repeat count") {
			cr = &compiledRegex{
				unmatchable: true,
				minLen:      1001,
				ignoreCase:  ignoreCase,
			}
		} else {
			fail("E_REGEX_SYNTAX", fmt.Sprintf("%v in /%s/", err, pattern), patPos)
		}
	} else {
		cr = &compiledRegex{
			re:         rx,
			ignoreCase: ignoreCase,
		}
	}

	regexMu.Lock()
	regexCache[key] = cr
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

func findMatches(cr *compiledRegex, subjectRunes, searchRunes []rune) []regexMatch {
	if cr.unmatchable {
		return nil
	}
	searchStr := string(searchRunes)
	allIdx := cr.re.FindAllStringSubmatchIndex(searchStr, -1)
	if len(allIdx) == 0 {
		return nil
	}

	// Build byte-offset to rune-index map for searchStr
	byteToRune := make([]int, len(searchStr)+1)
	curRune := 0
	for byteOff := range searchStr {
		byteToRune[byteOff] = curRune
		curRune++
	}
	byteToRune[len(searchStr)] = curRune

	var matches []regexMatch
	lastEnd := -1
	for _, m := range allIdx {
		start := byteToRune[m[0]]
		end := byteToRune[m[1]]
		if end == start && start == lastEnd {
			continue
		}
		lastEnd = end

		numGroups := len(m) / 2
		groups := make([][2]int, numGroups)
		for g := 0; g < numGroups; g++ {
			gs := m[2*g]
			ge := m[2*g+1]
			if gs < 0 || ge < 0 {
				groups[g] = [2]int{-1, -1}
			} else {
				groups[g] = [2]int{byteToRune[gs], byteToRune[ge]}
			}
		}
		matches = append(matches, regexMatch{
			startCp: start,
			endCp:   end,
			groups:  groups,
		})
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
			matches := findMatches(cr, orig, search)
			return NewBool(len(matches) > 0)
		},
	})

	Define(&Spec{
		Name: "RFIND",
		Min:  2,
		Max:  3,
		Fn: func(args *Args, ctx *Context) *Value {
			cr, orig, search := regexArgs(args, 0, 1, 2)
			matches := findMatches(cr, orig, search)
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
			matches := findMatches(cr, orig, search)
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
			matches := findMatches(cr, origRunes, searchRunes)
			if len(matches) == 0 {
				return NewTextOwned(subj)
			}

			numGroups := 1
			if cr.re != nil {
				numGroups = cr.re.NumSubexp() + 1
			}

			var out strings.Builder
			last := 0
			for _, m := range matches {
				out.WriteString(string(origRunes[last:m.startCp]))
				out.WriteString(expandRepl(repl, m, numGroups, origRunes, args.PosOf(1)))
				last = m.endCp
			}
			out.WriteString(string(origRunes[last:]))
			return NewTextOwned(out.String())
		},
	})
}
