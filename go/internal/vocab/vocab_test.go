package vocab

import (
	"sort"
	"strings"
	"testing"
)

// The families the evaluator and the SQL layer classify by, pinned as the spec
// writes them (spec/SPEC.md §5): a lexicon edit that moves an operator between
// families has to move this test too.
func TestFamilies(t *testing.T) {
	cases := []struct {
		name string
		pred func(string) bool
		want string
	}{
		{"numeric comparison", IsNumericComparison, "!= < <= == > >="},
		{"text comparison", IsTextComparison, "$!= $< $<= $== $> $>="},
		{"deep comparison", IsDeepComparison, "EQL IN"},
		{"arithmetic", IsArithmetic, "% * + - /"},
		{"logic", IsLogic, "AND OR XOR"},
		{"short circuit", IsShortCircuit, "??? ?? AND OR"},
		{"equality", IsEquality, "$== =="},
		{"assignment", IsAssign, "%= &= *= += -= /= ="},
	}
	for _, c := range cases {
		var got []string
		for op := range infixAll {
			if c.pred(op) {
				got = append(got, op)
			}
		}
		sort.Strings(got)
		want := strings.Fields(c.want)
		sort.Strings(want)
		if strings.Join(got, " ") != strings.Join(want, " ") {
			t.Errorf("%s: %v, want %v", c.name, got, want)
		}
	}
	if Compound("&=") != "&" || Compound("=") != "" || Compound("+") != "" {
		t.Error("Compound")
	}
	if Prefix("-") == nil || Prefix("-").Name != "NEG" || Prefix("NOT") == nil || Prefix("NOT").Name != "NOT" {
		t.Error("Prefix")
	}
	if InfixWord("AND") == nil || InfixSymbol("AND") != nil || InfixSymbol(",") == nil {
		t.Error("InfixWord / InfixSymbol")
	}
}

func TestBuiltinClasses(t *testing.T) {
	if !IsPipelineOp("LINK_LEFT") || IsPipelineOp("COUNT") {
		t.Error("IsPipelineOp")
	}
	if !IsSortStep("TOP_BY") || IsSortStep("TAKE") || !KeepsRows("TAKE") || KeepsRows("MAP") {
		t.Error("IsSortStep / KeepsRows")
	}
	if at, ok := RegexFlagsAt("RREPLACE"); !ok || at != 3 {
		t.Error("RegexFlagsAt")
	}
	if at, ok := RegexPatternAt("RMATCH"); !ok || at != 0 {
		t.Error("RegexPatternAt")
	}
	if _, ok := RegexFlagsAt("SPLIT"); ok {
		t.Error("RegexFlagsAt(SPLIT)")
	}
}
