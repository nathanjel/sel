package sel

import (
	"strings"
	"testing"
)

// The ambiguity rule (SPEC §7.8) against the pattern lists of
// tools/regex-ambiguity-ref.py, the reference the conformance cases in
// conformance/28b-regex-ambiguity.selt were checked against.

var ambiguityMustReject = []string{
	`(a+)+$`,
	`(a|aa)+$`,
	`(a|b|ab)*c`,
	`(?:a+|b)*(?:a+)*c`,
	`(.+)+x`,
	`([a-z]+)*$`,
	`(\w+\s?)*$`,
	`(\w+\s*)*$`,
	`([a-zA-Z]+)*\d`,
	`(?:[a-z]+|\d+)*$`,
	`(\d+\d*)+$`,
	`([\w.-]+\.)+$`,
	`(x+x+)+y`,
	`(?:\d|\d\d)+$`,
	`(?:\s|\s\s)+$`,
	`(?:.|\n)*x`,
	`(?:a|a)*$`,
	`(\d{1,3},?)+$`,
	`^(([a-z])+.)+[A-Z]([a-z])+$`,
	`(?:\s*,\s*)*x`,
	`(?:[ab]|[bc])*$`,
	`(\s*\w+\s*)*$`,
	`(?:x|xx|xxx)+y`,
	`^(\w+[-.]?)+@`,
	`(?:[\d.]+,?)+$`,
	`(a+){2,}$`,
	`(?:(?:a|b)+c?)+$`,
	`((a+)b?)*$`,
	`(?:a+)+?b`,
	`(?:a{1,20}){1,20}b`,
	`(?:\s*\w+\s*,?)*x`,
	`(?:(?:a?|b?)c)*d`,
	`(?:(?:a*)?c)*d`,
	`^(?:a+){2,}$`,
	`^(?:a|b|ab)+$`,
}

var ambiguityMustAccept = []string{
	`(\d+,)+`,
	`(?:ab|cd)*`,
	`^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$`,
	`(\w+\s)*`,
	`([a-z]+-)*[a-z]+`,
	`(?:a|b)*`,
	`a*b*c*`,
	`^\d{3}-\d{3}-\d{4}$`,
	`^(\d{1,3}\.){3}\d{1,3}$`,
	`^[+-]?\d+(?:\.\d+)?$`,
	`^"(?:[^"\\]|\\.)*"$`,
	`^[a-z0-9]+(?:[-_.][a-z0-9]+)*$`,
	`^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$`,
	`^(?:[^,]*,)*[^,]*$`,
	`^\s*(\w+)\s*=\s*(.*?)\s*$`,
	`(?:\r\n|\n)*`,
	`^(?:[a-z]+\d+)*$`,
	`^[A-Z]{2}\d{2}(?: ?\d{4}){4,7}$`,
	`^(?:ab|ac)*$`,
	`^(?:ab|a)*$`,
	`(?:foo|foobar)*`,
	`^(?:\d{3}){1,2}$`,
	`(?:a{2}){3}`,
	`^(a{300}){300}$`,
	`(?:a{60000}){60000}`,
	`^[a-z]+(?:[A-Z][a-z]+)*$`,
	`^(?:[A-Z][a-z0-9]+)+$`,
	`(?:[a-z]|[A-Z])+$`,
	`^(?:[0-9]*|[a-z]*)$`,
	`^(\d*)?$`,
	`(^|[^0-9A-Za-z_])foo($|[^0-9A-Za-z_])`,
	`^(?:a+b)+$`,
	`^[a-z]+(\.[a-z]+)*$`,
	`^(a|b)*$`,
	`^(?:a|b)+c?$`,
	`^[^<>]*(?:<[^<>]*>[^<>]*)*$`,
	`^(.*),(.*),(.*),(.*)$`,
	`a*a*$`,
	`^a{65535}$`,
	`^(?:ab){65535}$`,
	`^[a-z]{1,65535}$`,
}

var ambiguityFlagPairs = []string{
	`(?:a|A)+$`,
	`(?:[a-z]|[A-Z])+$`,
	`^[a-z]*(?:[a-c]|[A-C])+$`,
}

var ambiguityStructuralReject = []string{
	`(a`,
	`a)`,
	`*a`,
	`a|*`,
	`^*`,
	`a{2}*`,
	`a**`,
	`()*`,
	`(?:`,
	`[a`,
	`a{2}{3}`,
	`(*)`,
	`(a|`,
	`(?=a)`,
	`(?i)a`,
	`a{2,1}`,
	`a{65536}`,
}

var ambiguityStructuralAccept = []string{
	``,
	`x{0}`,
	`^$`,
	`(a | a)`,
}

func refuses(p string, ic bool) (refused bool) {
	defer func() {
		if r := recover(); r != nil {
			if se, ok := r.(*SelError); ok && se.Code == "E_REGEX_SYNTAX" {
				refused = true
				return
			}
			panic(r)
		}
	}()
	parseRegexIC(p, ic, Pos{})
	return false
}

func TestAmbiguityMustReject(t *testing.T) {
	for _, p := range ambiguityMustReject {
		if !refuses(p, false) {
			t.Errorf("accepted %q, want refused", p)
		}
	}
}

func TestAmbiguityMustAccept(t *testing.T) {
	for _, p := range ambiguityMustAccept {
		if refuses(p, false) {
			t.Errorf("refused %q, want accepted", p)
		}
	}
}

func TestAmbiguityFlagPairs(t *testing.T) {
	for _, p := range ambiguityFlagPairs {
		if refuses(p, false) {
			t.Errorf("refused %q without i, want accepted", p)
		}
		if !refuses(p, true) {
			t.Errorf("accepted %q with i, want refused", p)
		}
	}
}

func TestAmbiguityStructural(t *testing.T) {
	for _, p := range ambiguityStructuralReject {
		if !refuses(p, false) {
			t.Errorf("accepted %q, want refused", p)
		}
	}
	for _, p := range ambiguityStructuralAccept {
		if refuses(p, false) {
			t.Errorf("refused %q, want accepted", p)
		}
	}
}

func TestAmbiguityBudgetBoundaries(t *testing.T) {
	cases := []struct {
		p  string
		ok bool
	}{
		{strings.Repeat("(a|a)", 8) + "x", true}, {strings.Repeat("(a|a)", 9) + "x", false},
		{strings.Repeat("(?:|)", 16) + "x", true}, {strings.Repeat("(?:|)", 17) + "x", false},
		{strings.Repeat("a?", 7) + "b", true}, {strings.Repeat("a?", 8) + "b", false},
		{"(?:a|a){1,8}$", true}, {"(?:a|a){1,9}$", false},
	}
	for _, c := range cases {
		if got := !refuses(c.p, false); got != c.ok {
			t.Errorf("%.40q accepted=%v, want %v", c.p, got, c.ok)
		}
	}
}

// A verdict costs bounded work: a hostile pattern that overruns a cap is refused
// promptly, and a large legal one is analysed once (the compile cache holds it).
func TestAmbiguityAnalysisIsBounded(t *testing.T) {
	hostile := []string{
		strings.Repeat("(a|a)", 200) + "x",
		strings.Repeat("a?", 60) + "b",
		"(?:" + strings.Repeat("a|", 5000) + "a)+",
	}
	for _, p := range hostile {
		if !refuses(p, false) {
			t.Errorf("accepted hostile pattern %.30q", p)
		}
	}
	if refuses(strings.Repeat("a", 60000), false) {
		t.Error("refused a 60,000-literal pattern")
	}
}
