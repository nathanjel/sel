// Package vocab holds the parts of SEL's vocabulary that the evaluator (package
// sel) and the SQL layer (package sel/sql) both classify by: what each operator
// is (from the lexicon, spec/lexicon.json, rendered into internal/lexicon) and
// which builtins are pipeline steps, sorts or regex calls (from the builtin
// manifest, spec/builtins.json, rendered into internal/manifest). One table
// each, built once at load, so no package keeps a list of its own.
package vocab

import (
	"github.com/nathanjel/sel/go/internal/lexicon"
	"github.com/nathanjel/sel/go/internal/manifest"
)

var (
	// The infix operators by spelling. A word operator lexes as an identifier
	// and a symbol as an op token, so the parser asks the table for its token
	// type; a node's operator (Node.S) is unambiguous either way, since no word
	// is spelled like a symbol.
	infixSymbols = map[string]*lexicon.Op{}
	infixWords   = map[string]*lexicon.Op{}
	infixAll     = map[string]*lexicon.Op{}
	prefixOps    = map[string]*lexicon.Op{}
)

func init() {
	for i := range lexicon.Ops {
		op := &lexicon.Ops[i]
		switch op.Fixity {
		case lexicon.Infix:
			if op.Word {
				infixWords[op.Token] = op
			} else {
				infixSymbols[op.Token] = op
			}
			infixAll[op.Token] = op
		case lexicon.Prefix:
			prefixOps[op.Token] = op
		}
	}
}

// InfixSymbol is the infix operator a symbol token spells, or nil.
func InfixSymbol(tok string) *lexicon.Op { return infixSymbols[tok] }

// InfixWord is the infix operator a word (identifier token) spells, or nil.
func InfixWord(tok string) *lexicon.Op { return infixWords[tok] }

// Prefix is the prefix operator a token spells, or nil.
func Prefix(tok string) *lexicon.Op { return prefixOps[tok] }

// Op is the infix operator a binary or assignment node records, or nil.
func Op(op string) *lexicon.Op { return infixAll[op] }

func family(op string, f lexicon.Family) bool {
	o := infixAll[op]
	return o != nil && o.Family == f
}

// IsNumericComparison reports == != < <= > >=, which compare numbers.
func IsNumericComparison(op string) bool { return family(op, lexicon.FamilyCompare) }

// IsTextComparison reports $== $!= $< $<= $> $>=, which compare text bytewise.
func IsTextComparison(op string) bool { return family(op, lexicon.FamilyTextCompare) }

// IsDeepComparison reports EQL and IN.
func IsDeepComparison(op string) bool { return family(op, lexicon.FamilyDeepCompare) }

// IsArithmetic reports the binary + - * / %.
func IsArithmetic(op string) bool { return family(op, lexicon.FamilyArith) }

// IsLogic reports the binary AND OR XOR.
func IsLogic(op string) bool { return family(op, lexicon.FamilyLogic) }

// IsAssign reports = and the compound assignments.
func IsAssign(op string) bool { return family(op, lexicon.FamilyAssign) }

// IsShortCircuit reports an operator whose right operand may never run.
func IsShortCircuit(op string) bool {
	o := infixAll[op]
	return o != nil && o.ShortCircuit
}

// IsEquality reports the equality of either comparison family (== and $==).
func IsEquality(op string) bool {
	o := infixAll[op]
	return o != nil && o.Relation == 0 &&
		(o.Family == lexicon.FamilyCompare || o.Family == lexicon.FamilyTextCompare)
}

// Compound is the binary operator a compound assignment applies ("+" for
// "+="), and "" for anything else.
func Compound(op string) string {
	if o := infixAll[op]; o != nil {
		return o.Compound
	}
	return ""
}

// IsPipelineOp reports a pipeline operator: a function `.>` chains, whose first
// argument is the rows the step before produced.
func IsPipelineOp(name string) bool {
	_, ok := manifest.PipelineSteps[name]
	return ok
}

// IsSortStep reports a pipeline step that sorts (the SORT and TOP families).
func IsSortStep(name string) bool { return manifest.PipelineSteps[name].Sorts }

// KeepsRows reports a pipeline step every row of whose result is one of its
// input rows, unchanged.
func KeepsRows(name string) bool { return manifest.PipelineSteps[name].KeepsRows }

// RegexFlagsAt is the index of a regex function's optional flags argument (its
// pattern is argument 0), and false for any other function.
func RegexFlagsAt(name string) (int, bool) {
	c, ok := manifest.RegexCalls[name]
	return c.Flags, ok
}

// RegexPatternAt is the index of a regex function's pattern argument, and false
// for any other function.
func RegexPatternAt(name string) (int, bool) {
	c, ok := manifest.RegexCalls[name]
	return c.Pattern, ok
}
