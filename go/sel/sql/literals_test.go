package sql

import (
	"math/rand"
	"sort"
	"strings"
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// refTextLiteral is TextLiteral as it was before the escaper memo: the escape keys sorted
// longest first on every call, a prefix test per key at every byte.
func refTextLiteral(dialect, text string) string {
	quote := "'"
	if s, ok := Lexical(dialect, "textQuote").(string); ok {
		quote = s
	}
	out := text
	var escapeMap map[string]string
	switch m := Lexical(dialect, "textEscape").(type) {
	case map[string]string:
		escapeMap = m
	case map[string]interface{}:
		escapeMap = make(map[string]string)
		for k, v := range m {
			if vs, ok := v.(string); ok {
				escapeMap[k] = vs
			}
		}
	}
	if len(escapeMap) > 0 {
		keys := make([]string, 0, len(escapeMap))
		for k := range escapeMap {
			keys = append(keys, k)
		}
		sort.Slice(keys, func(i, j int) bool { return len(keys[i]) > len(keys[j]) })
		var buf strings.Builder
		i := 0
		for i < len(out) {
			hit := ""
			for _, k := range keys {
				if k != "" && strings.HasPrefix(out[i:], k) {
					hit = k
					break
				}
			}
			if hit != "" {
				buf.WriteString(escapeMap[hit])
				i += len(hit)
			} else {
				buf.WriteByte(out[i])
				i++
			}
		}
		out = buf.String()
	}
	return quote + out + quote
}

func randomText(rnd *rand.Rand) string {
	alphabet := []string{"a", "b", "c", "ab", "abc", "'", "\\", "\"", "é", "😀", "x", " ", "\n", "ab'", "''"}
	var b strings.Builder
	for i, n := 0, rnd.Intn(14); i < n; i++ {
		b.WriteString(alphabet[rnd.Intn(len(alphabet))])
	}
	return b.String()
}

func TestTextLiteralMatchesThePerCallScan(t *testing.T) {
	Reset()
	defer Reset()
	// A dialect with overlapping multi-byte escape keys, registered on top of a shipped one.
	DefineDialect("p28-multi", map[string]interface{}{"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{
		"textEscape": map[string]interface{}{"\\": "\\\\", "'": "''", "ab": "<ab>", "abc": "<abc>", "é": "E'", "😀": ""},
	}})
	rnd := rand.New(rand.NewSource(28))
	for _, d := range []string{"mariadb", "mysql", "postgresql", "sqlite", "p28-multi", "no-such-dialect"} {
		for i := 0; i < 3000; i++ {
			s := randomText(rnd)
			if want, got := refTextLiteral(d, s), textLiteral(d, s); want != got {
				t.Fatalf("%s %q: want %q got %q", d, s, want, got)
			}
		}
	}
	// The longest key wins where two share a prefix, and the output is not rescanned.
	if got := textLiteral("p28-multi", "abc ab a"); got != "'<abc> <ab> a'" {
		t.Errorf("longest-first broken: %s", got)
	}
}

func TestTextLiteralSeesARegistrationAtOnce(t *testing.T) {
	Reset()
	defer Reset()
	spec := func(rep string) map[string]interface{} {
		return map[string]interface{}{"extends": "mariadb", "version": "10.5", "lexical": map[string]interface{}{
			"textEscape": map[string]interface{}{"\\": "\\\\", "'": "''", "x": rep},
		}}
	}
	DefineDialect("p28-redef", spec("1"))
	if got := textLiteral("p28-redef", "axb"); got != "'a1b'" {
		t.Fatalf("first registration: %s", got)
	}
	DefineDialect("p28-redef", spec("22")) // same parent: replaces the registration and the compiled escaper
	if got := textLiteral("p28-redef", "axb"); got != "'a22b'" {
		t.Fatalf("after re-registration: %s", got)
	}
	Reset()
	if got := textLiteral("p28-redef", "axb"); got != "'axb'" {
		t.Fatalf("an unregistered dialect escapes nothing: %s", got)
	}
}

// ToNode is built once per SNode and is stable — the same tree every time,
// and asking a parent first does not change what a child answers.
func TestToNodeIsBuiltOnceAndStable(t *testing.T) {
	prog := sel.MustCompile(`1 + 2 * 3 - 4`)
	root := prog.AST()
	var build func(n *sel.Node) *sNode
	build = func(n *sel.Node) *sNode {
		if n == nil {
			return nil
		}
		var kids []*sNode
		if n.L != nil {
			kids = append(kids, build(n.L))
		}
		if n.R != nil {
			kids = append(kids, build(n.R))
		}
		if len(kids) == 0 {
			return leaf(n)
		}
		return rewritten(n, kids)
	}
	s := build(root)
	a := s.ToNode()
	if a == nil || a != s.ToNode() {
		t.Fatalf("ToNode is not cached: %p vs %p", a, s.ToNode())
	}
	child := s.Kids[0].ToNode()
	if child != a.L {
		t.Errorf("a child's ToNode is not the subtree the parent was built from")
	}
}
