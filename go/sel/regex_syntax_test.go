package sel

import (
	"strings"
	"testing"
)

func regexCode(pattern string) (code string) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SelError); ok {
				code = se.Code
				return
			}
			panic(r)
		}
	}()
	parseRegex(pattern, Pos{})
	return ""
}

func TestRegexValidatorRejections(t *testing.T) {
	for _, p := range []string{
		`^*`, `$+`, `^{2}`, `^+?`, `a$+`, `$?`, `${0}`, // quantified anchors
		`[+-\d]`, `[\d-z]`, `[\s-x]`, `[\w-a]`, `[a-\s]`, `[\w-.]`, `[!-\w]`, `[a--b]`, `[z-a]`, // ranges
		`[a[:alpha:]]`, `[a[:digit:]`, `[[:alpha:]a]`, `[a[.x.]]`, `[[=x=]]`, // POSIX forms
		`(*FAIL)`, `a(*ACCEPT)b`, `(*UTF8)a`, // PCRE verbs
		`(a*)*`, `(?:a?)+`, `(|a)+`, `(a*?)+`, `(?:(a)|b?)*`, `(a|)+b`, `(?:a?){2}`, // nullable loop bodies
		`(?:(a)|b)*`, `(?:(a)|(b))+`, `((a)|(b))*`, `(?:(a)?b)+`, `(?:(a)|b){2}`, // optional captures in loops
		`a**`, `a+*`, `*a`, `(?=a)`, `(?<n>a)`, `(?>a)`, `a++`, `a{2}{3}`, `(a`, `a)`, `[a`, `a\`, `\b`, `\1`, `\pL`,
		`a{65536}`, `a{1,65536}`, `a{3,2}`,
		strings.Repeat("(?:", 201) + "a" + strings.Repeat(")", 201),
		"^" + strings.Repeat("(a)", 1001) + "$",
		"^" + strings.Repeat("a", 65535) + "$",
	} {
		if regexCode(p) != "E_REGEX_SYNTAX" {
			t.Errorf("/%.60s/ must be E_REGEX_SYNTAX", p)
		}
	}
}

func TestRegexValidatorAccepts(t *testing.T) {
	for _, p := range []string{
		`^[\d-]$`, `^[-\d]$`, `^[\w.-]$`, `^[\d.]$`, `^[[]$`, `^[[.]$`, `^[[=]$`, `^[.[]$`, `^[a&&b]$`,
		`(a|b)+`, `((a)b)+`, `(\d)-(\d)`, `^(a)?b$`, `^(?:a|)b$`, `(?:a{1000}){2}`, `^(a{300}){300}$`,
		`(?:a{60000}){60000}`, `a{00001}`, `a{0}`, ``, `x{0}`, `^$`, `$.*`,
		strings.Repeat("(?:", 200) + "a" + strings.Repeat(")", 200),
		"^" + strings.Repeat("(a)", 1000) + "$",
		"^" + strings.Repeat("a", 65533) + "$",
	} {
		if c := regexCode(p); c != "" {
			t.Errorf("/%.60s/ must be accepted, got %s", p, c)
		}
	}
}

func TestPortableSourceExpandsEscapesAndNothingElse(t *testing.T) {
	for in, want := range map[string]string{
		`\d+`: `[0-9]+`, `[\d.-]`: `[0-9.-]`, `\W`: `[^0-9A-Za-z_]`, `(a|b)*?c`: `(a|b)*?c`,
		`(?:x{2,3})`: `(?:x{2,3})`, `\.\(`: `\.\(`, `[é-ü]`: `[é-ü]`,
	} {
		if got := ValidatePattern(in, Pos{}); got != want {
			t.Errorf("ValidatePattern(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestLargeCountsRunOnRE2(t *testing.T) {
	long := func(n int) string { return `"` + strings.Repeat("a", n) + `"` }
	for _, c := range []struct{ src, want string }{
		{`RMATCH('^a{1001}$', ` + long(1001) + `)`, `b1`},
		{`RMATCH('^a{1001}$', ` + long(1000) + `)`, `b0`},
		{`RMATCH('^a{0,2000}$', "")`, `b1`},
		{`RFIND('b{2,5000}', "abb")`, `n2`},
		{`RMATCH('^a{65535}$', REPEAT("a", 65535))`, `b1`},
		{`RMATCH('^a{65535}$', REPEAT("a", 65534))`, `b0`},
		{`RMATCH('^(a{300}){300}$', REPEAT("a", 90000))`, `b1`},
		{`RMATCH('^(?:a{1000}){2}$', REPEAT("a", 2000))`, `b1`},
		{`RMATCH('^(a){20000}$', REPEAT("a", 20000))`, `b1`},
		{`RMATCH('^(a){20000}$', REPEAT("a", 19999))`, `b0`},
		{`RMATCH('(?:a{60000}){60000}', "a")`, `b0`},
		{`RMATCH('^[a-z]{1,65535}$', REPEAT("q", 65535))`, `b1`},
		{`RMATCH('^a{1001,}$', REPEAT("a", 5000))`, `b1`},
	} {
		want := map[string]string{"b1": "TRUE", "b0": "FALSE", "n2": `t"2"`}[c.want]
		expectDump(t, c.src, want)
	}
}

// A repeated group reports its last iteration, and its number is the pattern's.
func TestExpandedRepeatKeepsGroupNumbers(t *testing.T) {
	g := dumpOf(`RGROUPS('^(?:x)(a|b){1500}(c)$', "x" & REPEAT("a", 1499) & "b" & "c")`)
	if !strings.Contains(g, `"2"=t"b"`) || !strings.Contains(g, `"3"=t"c"`) {
		t.Errorf("groups after an expanded repeat: %s", g)
	}
	g = dumpOf(`RGROUPS('^((a)b){1200,1300}$', REPEAT("ab", 1250))`)
	if !strings.Contains(g, `"2"=t"ab"`) || !strings.Contains(g, `"3"=t"a"`) {
		t.Errorf("nested captures in an expanded counted loop: %s", g)
	}
}

// §7.8: after an empty match the scan resumes one code point on; after a
// non-empty match an empty match is allowed where it ends.
func TestReplaceWalksEmptyMatchesOneCodePointAtATime(t *testing.T) {
	for _, c := range []struct{ src, want string }{
		{`RREPLACE('a*', "-", "baac")`, `t"-b--c-"`},
		{`RREPLACE('\s*', "_", "a b")`, `t"_a__b_"`},
		{`RREPLACE('b*?', "-", "abb")`, `t"-a-b-b-"`},
		{`RREPLACE('(?:|a)', "-", "aa")`, `t"-a-a-"`},
		{`RREPLACE('a*?', "-", "aab")`, `t"-a-a-b-"`},
		{`RREPLACE('(b*|[é-ü])', "<$0|$1>", "baac")`, `t"<b|b><|>a<|>a<|>c<|>"`},
		{`RREPLACE('$.*', "!", "abc")`, `t"abc!"`},
		{`RREPLACE('x*', "-", "żż")`, `t"-ż-ż-"`},
	} {
		expectDump(t, c.src, c.want)
	}
}

func TestOnlyLowercaseIIsAFlag(t *testing.T) {
	for _, f := range []string{`"I"`, `"\u{130}"`, `"\u{131}"`, `"\u{212A}"`, `"m"`, `"s"`, `"x"`} {
		got := dumpOf(`RMATCH('a', "A", ` + f + `)`)
		if !strings.HasPrefix(got, "!E_BAD_ARG") {
			t.Errorf("flag %s: %s", f, got)
		}
	}
	expectDump(t, `RMATCH('^a+$', "AaA", "i")`, `TRUE`)
}

func TestLiteralPatternsAreCheckedWhenTheProgramIsCompiled(t *testing.T) {
	for _, src := range []string{
		`IF(FALSE, RMATCH('(?=a)', "a"), 1)`,
		`IF(FALSE, RREPLACE("(?=a)", "-", "a"), 1)`,
	} {
		if _, err := Compile(src); err == nil {
			t.Errorf("%s compiled", src)
		}
	}
	// A computed pattern is checked only when it is used.
	expectDump(t, `IF(FALSE, RMATCH("(?=" & "a)", "a"), 1)`, `t"1"`)
	if g := dumpOf(`IF(TRUE, RMATCH("(?=" & "a)", "a"), 1)`); !strings.HasPrefix(g, "!E_REGEX_SYNTAX") {
		t.Errorf("computed pattern at run time: %s", g)
	}
}

func TestPatternCacheIsBounded(t *testing.T) {
	for i := 0; i < regexCacheSize*3; i++ {
		compileRegex("a"+strings.Repeat("b", i%50)+string(rune('A'+i%26))+strings.Repeat("c", i/26), "", Pos{}, Pos{})
	}
	regexMu.Lock()
	n, o := len(regexCache), len(regexOrder)
	regexMu.Unlock()
	if n > regexCacheSize || o > regexCacheSize || n != o {
		t.Errorf("cache holds %d patterns (%d in order), limit %d", n, o, regexCacheSize)
	}
}
