// Package vocab holds the parts of SEL's vocabulary that the evaluator (package
// sel) and the SQL layer (package sel/sql) both classify by: the pipeline
// operators, the comparison operators and where a regex function takes its
// flags. One list each, so the two packages cannot disagree.
package vocab

var pipelineOps = map[string]bool{
	"FILTER":      true,
	"BUCKET":      true,
	"SELECT_COLS": true,
	"MAP":         true,
	"DISTINCT":    true,
	"DEDUPE":      true,
	"TAKE":        true,
	"DROP":        true,
	"SORT":        true,
	"SORT_DESC":   true,
	"SORT_BY":     true,
	"TOP":         true,
	"TOP_DESC":    true,
	"TOP_BY":      true,
	"LINK":        true,
	"LINK_LEFT":   true,
}

// IsPipelineOp reports a pipeline operator: a function `.>` chains, whose first
// argument is the rows the step before produced.
func IsPipelineOp(name string) bool { return pipelineOps[name] }

// IsNumericComparison reports == != < <= > >=, which compare numbers.
func IsNumericComparison(op string) bool {
	switch op {
	case "==", "!=", "<", "<=", ">", ">=":
		return true
	}
	return false
}

// IsTextComparison reports $== $!= $< $<= $> $>=, which compare text bytewise.
func IsTextComparison(op string) bool {
	return len(op) > 1 && op[0] == '$' && IsNumericComparison(op[1:])
}

// RegexFlagsAt is the index of a regex function's optional flags argument (its
// pattern is argument 0), and false for any other function.
func RegexFlagsAt(name string) (int, bool) {
	switch name {
	case "RMATCH", "RFIND", "RGROUPS":
		return 2, true
	case "RREPLACE":
		return 3, true
	}
	return 0, false
}
