// Conformance test runner for SEL in Go.

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/sel"
)

var (
	headerRegex = regexp.MustCompile(`^###\s+name:\s*(\S+)\s*$`)
	errRegex    = regexp.MustCompile(`^(\S+)(?:\s+at\s+(\d+):(\d+))?$`)
)

func trimWS(s string) string {
	return strings.Trim(s, " \t\r\n")
}

type TestCase struct {
	Name   string
	At     string
	Setup  string
	Source string
	Expect string
}

func parseSelt(text, file string) ([]*TestCase, error) {
	var cases []*TestCase
	var cur *TestCase
	var section string
	var setupLines, sourceLines, expectLines []string

	lines := strings.Split(text, "\n")
	for idx, line := range lines {
		at := fmt.Sprintf("%s:%d", file, idx+1)
		if strings.HasPrefix(line, "### ") {
			m := headerRegex.FindStringSubmatch(line)
			if m == nil {
				return nil, fmt.Errorf("%s: malformed case header", at)
			}
			if cur != nil {
				cur.Setup = trimWS(strings.Join(setupLines, "\n"))
				cur.Source = trimWS(strings.Join(sourceLines, "\n"))
				cur.Expect = trimWS(strings.Join(expectLines, "\n"))
			}
			cur = &TestCase{Name: m[1], At: at}
			cases = append(cases, cur)
			section = ""
			setupLines = nil
			sourceLines = nil
			expectLines = nil
			continue
		}
		if line == "===" {
			if cur != nil {
				cur.Setup = trimWS(strings.Join(setupLines, "\n"))
				cur.Source = trimWS(strings.Join(sourceLines, "\n"))
				cur.Expect = trimWS(strings.Join(expectLines, "\n"))
			}
			cur = nil
			section = ""
			setupLines = nil
			sourceLines = nil
			expectLines = nil
			continue
		}
		if strings.HasPrefix(line, "--- ") {
			if cur == nil {
				return nil, fmt.Errorf("%s: section outside a case", at)
			}
			section = trimWS(line[4:])
			if section != "setup" && section != "source" && section != "expect" && section != "note" {
				return nil, fmt.Errorf("%s: unknown section %s", at, section)
			}
			continue
		}
		if cur == nil || section == "" || section == "note" {
			continue
		}
		switch section {
		case "setup":
			setupLines = append(setupLines, line)
		case "source":
			sourceLines = append(sourceLines, line)
		case "expect":
			expectLines = append(expectLines, line)
		}
	}
	if cur != nil {
		cur.Setup = trimWS(strings.Join(setupLines, "\n"))
		cur.Source = trimWS(strings.Join(sourceLines, "\n"))
		cur.Expect = trimWS(strings.Join(expectLines, "\n"))
	}

	for _, c := range cases {
		if c.Source == "" {
			return nil, fmt.Errorf("%s: case %s has no --- source", c.At, c.Name)
		}
		if c.Expect == "" {
			return nil, fmt.Errorf("%s: case %s has no --- expect", c.At, c.Name)
		}
	}
	return cases, nil
}

func unescape(lit, at string) (string, error) {
	if len(lit) < 2 || lit[0] != '"' || lit[len(lit)-1] != '"' {
		return "", fmt.Errorf("%s: expected a quoted string, got %s", at, lit)
	}
	body := lit[1 : len(lit)-1]
	var out strings.Builder
	i := 0
	for i < len(body) {
		if body[i] != '\\' {
			out.WriteByte(body[i])
			i++
			continue
		}
		i++
		if i >= len(body) {
			return "", fmt.Errorf("%s: trailing backslash", at)
		}
		e := body[i]
		switch e {
		case '\\':
			out.WriteByte('\\')
		case '"':
			out.WriteByte('"')
		case 'n':
			out.WriteByte('\n')
		case 't':
			out.WriteByte('\t')
		case 'r':
			out.WriteByte('\r')
		case 'u':
			if i+5 > len(body) {
				return "", fmt.Errorf("%s: short unicode escape", at)
			}
			cp, err := strconv.ParseInt(body[i+1:i+5], 16, 32)
			if err != nil {
				return "", fmt.Errorf("%s: invalid unicode escape: %v", at, err)
			}
			out.WriteRune(rune(cp))
			i += 4
		default:
			return "", fmt.Errorf("%s: bad escape \\%c", at, e)
		}
		i++
	}
	return out.String(), nil
}

func jsonQuote(s string) string {
	var out strings.Builder
	out.WriteByte('"')
	for _, ch := range s {
		switch ch {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\n':
			out.WriteString(`\n`)
		case '\t':
			out.WriteString(`\t`)
		case '\r':
			out.WriteString(`\r`)
		default:
			if ch < 0x20 {
				out.WriteString(fmt.Sprintf(`\u%04x`, ch))
			} else {
				out.WriteRune(ch)
			}
		}
	}
	out.WriteByte('"')
	return out.String()
}

func describe(v *sel.Value) string {
	if v == nil {
		return "none"
	}
	switch v.Kind {
	case sel.KindText:
		if v.Size() > 0 {
			return fmt.Sprintf("tree %s", v.Dump())
		}
		return fmt.Sprintf("text %s", jsonQuote(v.Scalar()))
	case sel.KindBin:
		if v.Size() > 0 {
			return fmt.Sprintf("tree %s", v.Dump())
		}
		return fmt.Sprintf("bin %s", v.Dump()[1:])
	case sel.KindBool:
		if v.Size() > 0 {
			return fmt.Sprintf("tree %s", v.Dump())
		}
		return fmt.Sprintf("bool %s", v.Dump())
	default:
		if v.Size() > 0 {
			return fmt.Sprintf("tree %s", v.Dump())
		}
		return "none"
	}
}

func checkExpect(expect string, val *sel.Value, err *sel.SelError, at string) string {
	space := strings.IndexByte(expect, ' ')
	form := expect
	rest := ""
	if space >= 0 {
		form = expect[:space]
		rest = strings.TrimSpace(expect[space+1:])
	}

	if form == "error" {
		if err == nil {
			return fmt.Sprintf("expected %s, got value %s", expect, describe(val))
		}
		m := errRegex.FindStringSubmatch(rest)
		if m == nil {
			return fmt.Sprintf("%s: malformed error expectation", at)
		}
		if err.Code != m[1] {
			return fmt.Sprintf("expected %s, got %s (%s)", m[1], err.Code, err.Message)
		}
		if m[2] != "" {
			gotAt := fmt.Sprintf("%d:%d", err.Pos.Line, err.Pos.Col)
			wantAt := fmt.Sprintf("%s:%s", m[2], m[3])
			if gotAt != wantAt {
				return fmt.Sprintf("expected %s at %s, got it at %s", m[1], wantAt, gotAt)
			}
		}
		return ""
	}

	if err != nil {
		return fmt.Sprintf("expected %s, got %s (%s)", expect, err.Code, err.Message)
	}

	switch form {
	case "text":
		if val.Kind != sel.KindText || val.Size() > 0 {
			return fmt.Sprintf("wanted text, got %s", describe(val))
		}
		unquoted, uErr := unescape(rest, at)
		if uErr != nil {
			return uErr.Error()
		}
		if val.Scalar() != unquoted {
			return fmt.Sprintf("got %s", describe(val))
		}
		return ""

	case "num":
		if val.Kind != sel.KindText || val.Size() > 0 {
			return fmt.Sprintf("wanted a number, got %s", describe(val))
		}
		if val.Scalar() != rest {
			return fmt.Sprintf("got %s", describe(val))
		}
		return ""

	case "bin":
		if val.Kind != sel.KindBin || val.Size() > 0 {
			return fmt.Sprintf("wanted binary, got %s", describe(val))
		}
		if val.Dump()[1:] != rest {
			return fmt.Sprintf("got %s", describe(val))
		}
		return ""

	case "bool":
		if val.Kind != sel.KindBool || val.Size() > 0 {
			return fmt.Sprintf("wanted a boolean, got %s", describe(val))
		}
		got := "FALSE"
		if val.AsBool(sel.Pos{}) {
			got = "TRUE"
		}
		if got != rest {
			return fmt.Sprintf("got %s", describe(val))
		}
		return ""

	case "none":
		if val.Kind == sel.KindNone && val.Size() == 0 {
			return ""
		}
		return fmt.Sprintf("got %s", describe(val))

	case "tree":
		if val.Dump() != rest {
			return fmt.Sprintf("got tree %s", val.Dump())
		}
		return ""

	default:
		return fmt.Sprintf("%s: unknown expectation form %s", at, form)
	}
}

type runResult struct {
	val        *sel.Value
	err        *sel.SelError
	suiteError string
}

func runCase(c *TestCase) runResult {
	root := sel.NewNone()
	if c.Setup != "" {
		prog, err := sel.Compile(c.Setup)
		if err != nil {
			return runResult{suiteError: fmt.Sprintf("setup compile failed: %v", err)}
		}
		_, err = prog.Run(root)
		if err != nil {
			return runResult{suiteError: fmt.Sprintf("setup run failed: %v", err)}
		}
	}

	prog, err := sel.Compile(c.Source)
	if err != nil {
		if se, ok := err.(*sel.SelError); ok {
			return runResult{err: se}
		}
		return runResult{suiteError: fmt.Sprintf("compile error: %v", err)}
	}

	res, err := prog.Run(root)
	if err != nil {
		if se, ok := err.(*sel.SelError); ok {
			return runResult{err: se}
		}
		return runResult{suiteError: fmt.Sprintf("run error: %v", err)}
	}
	return runResult{val: res}
}

func main() {
	var files []string
	given := map[string]string{} // absolute path -> the path as it was given
	if len(os.Args) > 1 {
		for _, arg := range os.Args[1:] {
			abs, err := filepath.Abs(arg)
			if err != nil {
				fmt.Fprintf(os.Stderr, "error resolving %s: %v\n", arg, err)
				os.Exit(1)
			}
			files = append(files, abs)
			given[abs] = arg
		}
	} else {
		root := "conformance"
		entries, err := os.ReadDir(root)
		if err != nil {
			root = "../../conformance"
			entries, err = os.ReadDir(root)
			if err != nil {
				fmt.Fprintf(os.Stderr, "could not locate conformance directory: %v\n", err)
				os.Exit(1)
			}
		}
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".selt") {
				files = append(files, filepath.Join(root, e.Name()))
			}
		}
	}

	nPass := 0
	type failure struct {
		c       *TestCase
		problem string
	}
	var failures []failure
	var suiteErrors []string
	seen := make(map[string]string)

	for _, path := range files {
		data, err := os.ReadFile(path)
		if err != nil {
			if pe, ok := err.(*os.PathError); ok {
				err = pe.Err
			}
			shown := path
			if g, ok := given[path]; ok {
				shown = g
			}
			fmt.Fprintf(os.Stderr, "cannot read %s: %v\n", shown, err)
			suiteErrors = append(suiteErrors, fmt.Sprintf("cannot read %s: %v", shown, err))
			continue
		}
		cases, err := parseSelt(string(data), path)
		if err != nil {
			suiteErrors = append(suiteErrors, fmt.Sprintf("%s: parse error: %v", path, err))
			continue
		}
		for _, c := range cases {
			if prev, ok := seen[c.Name]; ok {
				suiteErrors = append(suiteErrors, fmt.Sprintf("%s: duplicate case name %s (also %s)", c.At, c.Name, prev))
				continue
			}
			seen[c.Name] = c.At

			res := runCase(c)
			if res.suiteError != "" {
				suiteErrors = append(suiteErrors, fmt.Sprintf("%s: %s: setup failed: %s", c.At, c.Name, res.suiteError))
				continue
			}
			problem := checkExpect(c.Expect, res.val, res.err, c.At)
			if problem == "" {
				nPass++
			} else {
				failures = append(failures, failure{c: c, problem: problem})
			}
		}
	}

	for _, f := range failures {
		indent := "\n             "
		fmt.Printf("FAIL %s  (%s)\n", f.c.Name, f.c.At)
		fmt.Printf("     source: %s\n", strings.ReplaceAll(f.c.Source, "\n", indent))
		fmt.Printf("     want:   %s\n", f.c.Expect)
		fmt.Printf("     %s\n", f.problem)
	}
	for _, e := range suiteErrors {
		fmt.Printf("SUITE %s\n", e)
	}

	fmt.Printf("\n%d passed, %d failed, %d suite errors\n", nPass, len(failures), len(suiteErrors))
	if nPass+len(failures) == 0 {
		// An empty file or a wrong path is not a passing suite.
		fmt.Fprintln(os.Stderr, "conformance: no case ran")
		os.Exit(1)
	}
	if len(failures) > 0 || len(suiteErrors) > 0 {
		os.Exit(1)
	}
}
