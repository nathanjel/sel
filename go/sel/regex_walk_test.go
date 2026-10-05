package sel

import (
	"fmt"
	"strings"
	"testing"
)

// The byte-offset implementation answers exactly what the retired rune
// implementation answered, for every built-in, with and without the i flag, on
// subjects that put multi-byte characters, the Kelvin sign and the long s in the way.
func TestRegexBuiltinsMatchTheRuneReference(t *testing.T) {
	patterns := []string{
		`a`, `.`, `x*`, `a*`, `b*?`, `(a)|b`, `(a)?b`, `(\d+)-(\d+)`, `[^k]`, `[k-l]`, `[^a-z]`, `\w+`, `\W`, `\s+`,
		`^a`, `a$`, `^$`, `é+`, `(?:ab)+`, `(a*)(b*)`, `k`, `s`, `S+`, `(k)(s)?`, `[a-z]+`, `[[:alpha:]]`,
		`(😀)`, `😀?`, `é`, `a{2,3}`, `(x)(y)?(z)?`, `\d{3}`, `\d`, `.$`, `^.`, `(?:a|ab)(c|bcd)`,
	}
	subjects := []string{
		"", "a", "aaa", "baac", "ab", "abab", "123-45,67-8", "K", "K", "ſ", "kKks", "Sſs", "é", "aéa", "😀a😀", "x😀y",
		"a\nb", "hello world", "  spaced  ", "ab😀éKſ", strings.Repeat("ab", 40), "zzz", "xyz", "abcd",
	}
	repls := []string{"-", "", "<$0>", "$1$1", "[$1|$2]", "$$", "é$0😀"}
	for _, pat := range patterns {
		for _, flags := range []string{"", "i"} {
			if flags == "i" && strings.ContainsAny(pat, "é😀") {
				continue // the i flag needs an ASCII pattern
			}
			var cr *compiledRegex
			bad := false
			func() {
				defer func() {
					if r := recover(); r != nil {
						if !isSelPanic(r) {
							panic(r)
						}
						bad = true
					}
				}()
				cr = compileRegex(pat, flags, Pos{}, Pos{})
			}()
			if bad {
				continue
			}
			ic := flags == "i"
			for _, subj := range subjects {
				q := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" } // raw: {n} in "" is an interpolation
				ctx := func() *Value { return ctxWith("S", NewText(subj)) }
				call := func(fn string) string {
					if flags == "" {
						return fmt.Sprintf(`%s(%s, S)`, fn, q(pat))
					}
					return fmt.Sprintf(`%s(%s, S, %q)`, fn, q(pat), flags)
				}
				if got, want := runOnce(call("RMATCH"), ctx()), fmt.Sprintf("%v", map[bool]string{true: "TRUE", false: "FALSE"}[refRMatch(cr, subj, ic)]); got != want {
					t.Errorf("RMATCH %q /%s/%s: got %s want %s", subj, pat, flags, got, want)
				}
				if got, want := runOnce(call("RFIND"), ctx()), fmt.Sprintf("t\"%d\"", refRFind(cr, subj, ic)); got != want {
					t.Errorf("RFIND %q /%s/%s: got %s want %s", subj, pat, flags, got, want)
				}
				g := refRGroups(cr, subj, ic)
				wantG := `E_NO`
				if g != nil {
					wantG = strings.Join(g, "|")
				}
				gotG := runOnce(fmt.Sprintf(`IF(COUNT(%s) == 0, "E_NO", JOIN(%s, "|"))`, call("RGROUPS"), call("RGROUPS")), ctx())
				if gotG != fmt.Sprintf("t%q", wantG) {
					t.Errorf("RGROUPS %q /%s/%s: got %s want %q", subj, pat, flags, gotG, wantG)
				}
				for _, repl := range repls {
					if strings.Contains(repl, "$1") && cr.re.NumSubexp() < 1 || strings.Contains(repl, "$2") && cr.re.NumSubexp() < 2 {
						continue
					}
					var src string
					if flags == "" {
						src = fmt.Sprintf(`RREPLACE(%s, %s, S)`, q(pat), q(repl))
					} else {
						src = fmt.Sprintf(`RREPLACE(%s, %s, S, %q)`, q(pat), q(repl), flags)
					}
					if got, want := runOnce(src, ctx()), fmt.Sprintf("t%q", refRReplace(cr, repl, subj, ic)); got != want {
						t.Errorf("RREPLACE %q /%s/%s -> %q: got %s want %s", subj, pat, flags, repl, got, want)
					}
				}
			}
		}
	}
}
