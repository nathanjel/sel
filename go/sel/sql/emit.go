package sql

import (
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

var slotRegex = regexpSlot()

func regexpSlot() func(string) (int, bool) {
	return func(s string) (int, bool) {
		if s == "0" {
			return 0, true
		}
		if s == "" || len(s) > 3 || s[0] < '1' || s[0] > '9' {
			return 0, false
		}
		for i := 0; i < len(s); i++ {
			if s[i] < '0' || s[i] > '9' {
				return 0, false
			}
		}
		n, err := strconv.Atoi(s)
		return n, err == nil
	}
}

func fillSlot(tpl, slot, value string) string {
	return strings.ReplaceAll(tpl, slot, value)
}

func formatLiteral(dialect string, v *sel.Value, form SqlKind, pos Pos) string {
	if form == KindBool || (v != nil && v.Kind() == sel.KindBool) {
		key := "false"
		if v != nil && v.AsBool(pos) {
			key = "true"
		}
		lex := Lexical(dialect, key)
		if s, ok := lex.(string); ok {
			return s
		}
		return key
	}
	if form == KindBin || (v != nil && v.Kind() == sel.KindBin) {
		tplVal := Lexical(dialect, "binaryLiteral")
		tpl, ok := tplVal.(string)
		if !ok || tpl == "" {
			refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s has no binary literal syntax", dialect), pos)
		}
		bytes := v.AsBytes(pos)
		hexStr := utf8.BytesToHex(bytes)
		return fillSlot(tpl, "{hex}", hexStr)
	}
	if v == nil || v.IsNone() {
		refuse("E_SQL_BINDING", "a value binding holding no value cannot be a SQL literal; only an aggregate can be given an empty binding", pos)
	}
	if form == KindNum {
		return numericLiteral(dialect, v, pos)
	}
	return textLiteral(dialect, v.AsText(pos))
}

func numericLiteral(dialect string, v *sel.Value, pos Pos) string {
	text := v.AsText(pos)
	d := decimal.Parse(text, utf8.Pos{}, func(c, m string, p utf8.Pos) {})
	if d == nil {
		refuse("E_SQL_BINDING", fmt.Sprintf("a value bound as NUM must be a number, and %q is not", text), pos)
	}
	n := decimal.Format(d)

	wrapVal := Lexical(dialect, "numericLiteral")
	if wrap, ok := wrapVal.(string); ok && wrap != "" && wrap != "{0}" {
		return fillSlot(wrap, "{0}", n)
	}

	if strings.HasPrefix(n, "-") {
		return "(" + n + ")"
	}
	return n
}

// escaper is a dialect's text quote and its escape map compiled into a replacer.
type escaper struct {
	quote string
	rep   *strings.Replacer // nil when the dialect escapes nothing
}

// escaperFor returns the dialect's escaper, built once per registration state
// (escaperMemo). The replacer is handed the keys longest first, which is what the
// old per-literal scan did: at each position the longest escape key that matches is
// taken, and what it puts out is not scanned again. strings.Replacer compares in
// argument order and never overlaps matches, so the two agree; an empty key was never
// a match and is left out.
func escaperFor(dialect string) *escaper {
	mapMu.Lock()
	defer mapMu.Unlock()
	if e, ok := escaperMemo[dialect]; ok {
		return e
	}
	e := &escaper{quote: "'"}
	if s, ok := lexicalLocked(dialect, "textQuote").(string); ok {
		e.quote = s
	}
	var escapeMap map[string]string
	switch m := lexicalLocked(dialect, "textEscape").(type) {
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
			if k != "" {
				keys = append(keys, k)
			}
		}
		sort.Slice(keys, func(i, j int) bool {
			if len(keys[i]) != len(keys[j]) {
				return len(keys[i]) > len(keys[j])
			}
			return keys[i] < keys[j]
		})
		pairs := make([]string, 0, 2*len(keys))
		for _, k := range keys {
			pairs = append(pairs, k, escapeMap[k])
		}
		if len(pairs) > 0 {
			e.rep = strings.NewReplacer(pairs...)
		}
	}
	// An unregistered dialect has an empty chain: do not remember it, so a later
	// registration of that name is seen at once.
	if len(chainLocked(dialect)) > 0 {
		escaperMemo[dialect] = e
	}
	return e
}

func textLiteral(dialect string, text string) string {
	e := escaperFor(dialect)
	out := text
	if e.rep != nil {
		out = e.rep.Replace(text)
	}
	return e.quote + out + e.quote
}

func placeholder(dialect string, n int) string {
	tplVal := Lexical(dialect, "placeholder")
	tpl := "?"
	if s, ok := tplVal.(string); ok && s != "" {
		tpl = s
	}
	if strings.Contains(tpl, "{n}") {
		return fillSlot(tpl, "{n}", strconv.Itoa(n))
	}
	return tpl
}

type Emit struct {
	dialect string
}

func newEmit(dialect string) *Emit {
	return &Emit{dialect: dialect}
}

func (e *Emit) Dialect() string {
	return e.dialect
}

func (e *Emit) Lex(key string) interface{} {
	return Lexical(e.dialect, key)
}

func (e *Emit) NumericOperand(f *Fragment, pos Pos) *Fragment {
	if f.Kind == KindNum && !f.Guard {
		return f
	}
	checkNumericGuard(e.dialect)
	guardVal := e.Lex("numericGuard")
	guard, ok := guardVal.(string)
	if !ok || guard == "" {
		refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric", e.dialect), pos)
	}
	parts := e.Fill(guard, []*Fragment{f}, pos, nil)
	return rewrap(f, parts, KindNum, e.dialect)
}

// rewrap is f's SQL replaced by parts of the given kind, its parameters and
// everything known about it kept.
func rewrap(f *Fragment, parts []Part, kind SqlKind, dialect string) *Fragment {
	res := NewFragment(parts, kind, dialect, f.Params, f.ParamKinds, f.Caveats)
	res.ExactCollation = f.ExactCollation
	res.Sargable = f.Sargable
	res.Guard = f.Guard
	res.Prefilter = f.Prefilter
	res.SeparatePrefilter = f.SeparatePrefilter
	res.Canonical = f.Canonical
	return res
}

func (e *Emit) TextOperand(f *Fragment) *Fragment {
	if f.ExactCollation {
		return f
	}
	castVal := e.Lex("textCast")
	collateVal := e.Lex("textCollate")
	collate := ""
	if s, ok := collateVal.(string); ok {
		collate = s
	}

	parts := f.Parts
	if cast, ok := castVal.(string); ok && cast != "" && cast != "{0}" {
		parts = e.Fill(cast, []*Fragment{f}, Pos{}, nil)
	}
	if collate != "" {
		parts = append(parts, Part{Sql: collate})
	}

	return rewrap(f, parts, KindText, e.dialect)
}

func (e *Emit) Ident(name string) string {
	qVal := e.Lex("identQuote")
	q := `"`
	if s, ok := qVal.(string); ok && s != "" {
		q = s
	}
	escVal := e.Lex("identEscape")
	esc := `""`
	if s, ok := escVal.(string); ok && s != "" {
		esc = s
	}
	return q + strings.ReplaceAll(name, q, esc) + q
}

func (e *Emit) Column(table, column string) string {
	if table == "" {
		return e.Ident(column)
	}
	return e.Ident(table) + "." + e.Ident(column)
}

func (e *Emit) Fill(tpl string, args []*Fragment, pos Pos, expanding map[string]bool) []Part {
	// Sized for what the arguments contribute plus the template's own runs: every
	// spliced fragment used to grow the slice by doubling.
	hint := 4
	for _, a := range args {
		if a != nil {
			hint += len(a.Parts)
		}
	}
	parts := make([]Part, 0, hint)

	push := func(s string) {
		if s == "" {
			return
		}
		if len(parts) > 0 && !parts[len(parts)-1].IsSlot {
			parts[len(parts)-1].Sql += s
		} else {
			parts = append(parts, Part{Sql: s})
		}
	}

	splice := func(f *Fragment) {
		for _, p := range f.Parts {
			if !p.IsSlot {
				push(p.Sql)
			} else {
				parts = append(parts, p)
			}
		}
	}

	joinSub := func(subset []*Fragment) {
		first := true
		for _, f := range subset {
			if !first {
				push(", ")
			}
			first = false
			splice(f)
		}
	}

	i := 0
	nTpl := len(tpl)
	for i < nTpl {
		if tpl[i] == '{' && i+1 < nTpl && tpl[i+1] == '{' {
			push("{")
			i += 2
			continue
		}
		if tpl[i] == '}' && i+1 < nTpl && tpl[i+1] == '}' {
			push("}")
			i += 2
			continue
		}
		if tpl[i] != '{' {
			// The whole run up to the next brace, as bytes: converting one byte
			// at a time re-encoded every byte of a multi-byte character as if it
			// were a code point.
			j := i + 1
			for j < nTpl && tpl[j] != '{' && tpl[j] != '}' {
				j++
			}
			push(tpl[i:j])
			i = j
			continue
		}
		end := strings.IndexByte(tpl[i:], '}')
		if end == -1 {
			push(tpl[i:])
			break
		}
		end = i + end
		slot := tpl[i+1 : end]
		i = end + 1

		if slot == "*" {
			joinSub(args)
			continue
		}
		if strings.HasSuffix(slot, ":") {
			frmStr := slot[:len(slot)-1]
			if frm, ok := slotRegex(frmStr); ok && frm >= 0 {
				// A tail starting past the last argument is the empty list, and
				// emits nothing.
				if frm > len(args) {
					frm = len(args)
				}
				joinSub(args[frm:])
				continue
			}
		}
		if k, ok := slotRegex(slot); ok {
			if k >= len(args) {
				refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("the mapping for this expression asks for argument %d, which it was not given", k), pos)
			}
			splice(args[k])
			continue
		}

		at := strings.IndexByte(slot, ':')
		key := slot
		argStr := ""
		hasArg := false
		if at != -1 {
			key = slot[:at]
			argStr = slot[at+1:]
			hasArg = true
		}

		val := e.Lex(key)
		valStr, ok := val.(string)
		if !ok {
			refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("a template used {%s}, which is neither an argument nor a lexical entry of dialect %s", slot, e.dialect), pos)
		}

		if !hasArg || argStr == "" {
			push(valStr)
			continue
		}

		if expanding != nil && expanding[key] {
			refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("the %s lexical entry of dialect %s expands into itself, so filling it would never finish", key, e.dialect), pos)
		}

		// {key:*} is {key:n} for every argument, joined with ", " (sql/MAP.md 4.2).
		each := []string{argStr}
		if argStr == "*" {
			each = each[:0]
			for n := range args {
				each = append(each, fmt.Sprintf("%d", n))
			}
		}
		for at, one := range each {
			if at > 0 {
				push(", ")
			}
			castArg, isCastArg := slotRegex(one)
			if key == "binaryCast" && isCastArg && castArg < len(args) && args[castArg].Kind == KindBin {
				splice(args[castArg])
				continue
			}

			deeper := make(map[string]bool)
			for k := range expanding {
				deeper[k] = true
			}
			deeper[key] = true

			subTpl := fillSlot(valStr, "{0}", "{"+one+"}")
			subParts := e.Fill(subTpl, args, pos, deeper)
			for _, p := range subParts {
				if !p.IsSlot {
					push(p.Sql)
				} else {
					parts = append(parts, p)
				}
			}
		}
	}

	return parts
}
