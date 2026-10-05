package harness

import (
	"reflect"
	"testing"
)

// The corpus rule: bytes are kept, and exactly one trailing "\n" goes.
func TestSplitCorpusKeepsBytes(t *testing.T) {
	for _, c := range []struct {
		in   string
		want []string
	}{
		{"### a\n1 +\r\n2\n### b\n(1\n\n", []string{"1 +\r\n2", "(1\n"}},
		{"### a\n1 +\r\n2\n### b\n(1\n", []string{"1 +\r\n2", "(1"}},
		{"### a\n1 +\r\n2\n### b\n(1", []string{"1 +\r\n2", "(1"}},
		{"### 1\nLEN(\"a\r\nb\")\n", []string{"LEN(\"a\r\nb\")"}},
		{"### 1\nx\r\n", []string{"x\r"}},
		{"ignored\n### 1\n\n\n", []string{"\n"}},
		{"### 1", []string{""}},
		{"", []string{}},
	} {
		if got := SplitCorpus(c.in); !reflect.DeepEqual(got, c.want) {
			t.Errorf("SplitCorpus(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}
