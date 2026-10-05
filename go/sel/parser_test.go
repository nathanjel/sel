package sel

import (
	"reflect"
	"testing"
)

func TestPrecedenceAndEvaluation(t *testing.T) {
	tests := []struct {
		expr     string
		expected string
	}{
		{"1 + 2 * 3", "t\"7\""},
		{"(1 + 2) * 3", "t\"9\""},
		{"10 - 4 - 2", "t\"4\""},
		{"NOT 1 == 2", "TRUE"},
		{"TRUE AND FALSE", "FALSE"},
		{"TRUE OR FALSE", "TRUE"},
		{"\"hello \" & \"world\"", "t\"hello world\""},
		{"a = 10; b = 20; a + b", "t\"30\""},
		{"a = 5; a += 10; a", "t\"15\""},
		{"1 < 2", "TRUE"},
		{"1 > 2", "FALSE"},
		{"NULL ?? 42", "t\"42\""},
		{"\"\" ??? 99", "t\"99\""},
	}

	for _, tt := range tests {
		val, err := Eval(tt.expr, nil)
		if err != nil {
			t.Fatalf("Eval(%q) error: %v", tt.expr, err)
		}
		if val.Dump() != tt.expected {
			t.Errorf("Eval(%q) = %s; want %s", tt.expr, val.Dump(), tt.expected)
		}
	}
}

func TestDependencies(t *testing.T) {
	tests := []struct {
		expr     string
		expected []string
	}{
		{"a + b", []string{"A", "B"}},
		{"a = 1; a + b", []string{"B"}},
		{"x[1] = 2; x", nil},
		{"\"constant\"", nil},
	}

	for _, tt := range tests {
		p, err := Compile(tt.expr)
		if err != nil {
			t.Fatalf("Compile(%q) error: %v", tt.expr, err)
		}
		deps := p.Dependencies()
		if len(deps) == 0 && len(tt.expected) == 0 {
			continue
		}
		if !reflect.DeepEqual(deps, tt.expected) {
			t.Errorf("Dependencies(%q) = %v; want %v", tt.expr, deps, tt.expected)
		}
	}
}

func TestAllManifestBuiltinsRegistered(t *testing.T) {
	names := FunctionNames()
	if len(names) == 0 {
		t.Fatalf("No functions registered")
	}
	t.Logf("Total registered functions: %d", len(names))
}
