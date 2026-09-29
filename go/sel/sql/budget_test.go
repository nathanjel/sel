package sql

import (
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// GO-C11: a constant the evaluator refuses for size is E_SQL_INVALID, not a
// panic out of the translator.
func TestTranslateRefusesAnOverSizedConstant(t *testing.T) {
	p, err := sel.Compile(`PADL("7", 318446744073709551616, "0") $== NAME`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = Translate(p, "postgresql", nil, Options{})
	if err == nil || !strings.Contains(err.Error(), "E_SQL_INVALID") || !strings.Contains(err.Error(), "E_RANGE") {
		t.Fatalf("want E_SQL_INVALID wrapping E_RANGE, got %v", err)
	}
}
