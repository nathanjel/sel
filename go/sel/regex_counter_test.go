package sel

import (
	"fmt"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

// The counter matcher must find what RE2 finds — the same leftmost-first match
// and the same captures — on every pattern both can hold, from every start
// offset, with and without the i flag.
func TestCounterRegexAgreesWithRE2(t *testing.T) {
	atoms := []string{"a", "k", "s", "[ab]", "[^a]", "[a-z]", "[^k]", ".", "é", `\d`, `\W`, `\s`, "(a)", "(?:ab|a)", "(?:a|ab)", "(k|b)"}
	reps := []string{"", "?", "*", "+", "{2}", "{1,3}", "{0,2}", "??", "*?", "+?", "{1,3}?", "{2,}"}
	patterns := []string{"", "(a)?(b)?", "^a$", "(a)|(b)", "^(ab)+$", "a$", "^", "$", "x(a{2})+y"}
	for _, atom := range atoms {
		for _, rep := range reps {
			patterns = append(patterns, atom+rep, "("+atom+rep+")b", "^"+atom+rep+"(b)?$", "(?:"+atom+rep+"|b)c")
		}
	}
	subjects := []string{""}
	for i := 0; i < 3; i++ {
		prev := subjects
		for _, s := range prev {
			for _, ch := range []string{"a", "b", "K", "K", "ſ", "é", "\n", "1"} {
				subjects = append(subjects, s+ch)
			}
		}
	}
	checked := 0
	for _, pat := range patterns {
		for _, ic := range []bool{false, true} {
			if ic && strings.ContainsRune(pat, 'é') {
				continue
			}
			tree, ok := tryParse(pat, ic)
			if !ok {
				continue
			}
			prefix := "(?s)"
			if ic {
				prefix += "(?i)"
			}
			src, _, _ := emitRE2(tree, false)
			re := regexp.MustCompile(prefix + src)
			srcDead, _, _ := emitRE2(tree, true)
			tail := regexp.MustCompile(prefix + srcDead)
			ctr := newCounterRegex(tree, ic)
			if ctr.groups != re.NumSubexp() {
				t.Fatalf("/%s/ i=%v: %d groups, RE2 %d", pat, ic, ctr.groups, re.NumSubexp())
			}
			for _, subj := range subjects {
				for from := 0; from <= len(subj); from++ {
					if from < len(subj) && !utf8Start(subj[from]) {
						continue
					}
					var want []int
					if from == 0 {
						want = re.FindStringSubmatchIndex(subj)
					} else if want = tail.FindStringSubmatchIndex(subj[from:]); want != nil {
						for i := range want {
							if want[i] >= 0 {
								want[i] += from
							}
						}
					}
					got := ctr.find(subj, from)
					if !reflect.DeepEqual(got, want) {
						t.Fatalf("/%s/ i=%v on %q from %d: counter %v, RE2 %v", pat, ic, subj, from, got, want)
					}
					checked++
				}
			}
		}
	}
	if checked < 50000 {
		t.Fatalf("only %d comparisons ran", checked)
	}
}

func utf8Start(b byte) bool { return b&0xC0 != 0x80 }

func tryParse(pat string, ic bool) (tree *reNode, ok bool) {
	defer func() {
		if r := recover(); r != nil {
			if _, sel := r.(*SelError); !sel {
				panic(r)
			}
			ok = false
		}
	}()
	return parseRegexIC(pat, ic, Pos{}), true
}

// Legal nested counts past what RE2 can hold run on the counter matcher, keep
// their capture numbers, and answer through every regex built-in.
func TestCounterRegexTakesNestedCounts(t *testing.T) {
	big := "((?:(?:ab){1000}){1000}){2}"
	cr, _ := compileRegex("^x"+big+"(y)$", "", Pos{}, Pos{})
	if cr.ctr == nil {
		t.Fatal("the nested pattern compiled on RE2; the test no longer reaches the counter matcher")
	}
	subj := "x" + strings.Repeat("ab", 2000000) + "y"
	run := func(src string) string {
		ctx := NewNone()
		ctx.Set("S", NewText(subj))
		v, err := MustCompile(src).Run(ctx)
		if err != nil {
			return "error " + err.Error()
		}
		if v.Kind == KindBool {
			return fmt.Sprint(v.AsBool(Pos{}))
		}
		return v.AsText(Pos{})
	}
	for _, c := range []struct{ src, want string }{
		{fmt.Sprintf(`RMATCH('^x%s(y)$', S)`, big), "true"},
		{fmt.Sprintf(`G = RGROUPS('^x%s(y)$', S); COUNT(G) & "|" & LEN(G["2"]) & "|" & G["3"]`, big), "3|2000000|y"},
		{fmt.Sprintf(`RFIND('%s', S)`, big), "2"},
		{fmt.Sprintf(`LEN(RREPLACE('%s', "<$1>", S))`, big), "2000004"},
		{fmt.Sprintf(`RMATCH('^x%s(y)$', "x" & REPEAT("ab", 1999999) & "acy")`, big), "false"},
	} {
		if got := run(c.src); got != c.want {
			t.Errorf("%s: got %s, want %s", c.src[:40], got, c.want)
		}
	}
}
