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

func FillSlot(tpl, slot, value string) string {
	return strings.ReplaceAll(tpl, slot, value)
}

func FormatLiteral(dialect string, v *sel.Value, form SqlKind, pos Pos) string {
	if form == KindBool || (v != nil && v.Kind == sel.KindBool) {
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
	if form == KindBin || (v != nil && v.Kind == sel.KindBin) {
		tplVal := Lexical(dialect, "binaryLiteral")
		tpl, ok := tplVal.(string)
		if !ok || tpl == "" {
			Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s has no binary literal syntax", dialect), pos)
		}
		bytes := v.AsBytes(pos)
		hexStr := utf8.BytesToHex(bytes)
		return FillSlot(tpl, "{hex}", hexStr)
	}
	if v == nil || v.IsNone() {
		Refuse("E_SQL_BINDING", "a value binding holding no value cannot be a SQL literal; only an aggregate can be given an empty binding", pos)
	}
	if form == KindNum {
		return NumericLiteral(dialect, v, pos)
	}
	return TextLiteral(dialect, v.AsText(pos))
}

func NumericLiteral(dialect string, v *sel.Value, pos Pos) string {
	text := v.AsText(pos)
	d := decimal.Parse(text, utf8.Pos{}, func(c, m string, p utf8.Pos) {})
	if d == nil {
		Refuse("E_SQL_BINDING", fmt.Sprintf("a value bound as NUM must be a number, and %q is not", text), pos)
	}
	n := decimal.Format(d)

	wrapVal := Lexical(dialect, "numericLiteral")
	if wrap, ok := wrapVal.(string); ok && wrap != "" && wrap != "{0}" {
		return FillSlot(wrap, "{0}", n)
	}

	if strings.HasPrefix(n, "-") {
		return "(" + n + ")"
	}
	return n
}

func TextLiteral(dialect string, text string) string {
	quoteVal := Lexical(dialect, "textQuote")
	quote := "'"
	if s, ok := quoteVal.(string); ok {
		quote = s
	}

	escapeVal := Lexical(dialect, "textEscape")
	out := text
	if escapeVal != nil {
		var escapeMap map[string]string
		if m, ok := escapeVal.(map[string]string); ok {
			escapeMap = m
		} else if m, ok := escapeVal.(map[string]interface{}); ok {
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
			sort.Slice(keys, func(i, j int) bool {
				return len(keys[i]) > len(keys[j])
			})

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
	}

	return quote + out + quote
}

func Placeholder(dialect string, n int) string {
	tplVal := Lexical(dialect, "placeholder")
	tpl := "?"
	if s, ok := tplVal.(string); ok && s != "" {
		tpl = s
	}
	if strings.Contains(tpl, "{n}") {
		return FillSlot(tpl, "{n}", strconv.Itoa(n))
	}
	return tpl
}

type Emit struct {
	dialect string
}

func NewEmit(dialect string) *Emit {
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
	CheckNumericGuard(e.dialect)
	guardVal := e.Lex("numericGuard")
	guard, ok := guardVal.(string)
	if !ok || guard == "" {
		Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("dialect %s has no way to ask whether a value is a number, so an operand it has not been told is one cannot be read as one here; declare the binding NUM if the column really is numeric", e.dialect), pos)
	}
	parts := e.Fill(guard, []*Fragment{f}, pos, nil)
	res := NewFragment(parts, KindNum, e.dialect, f.Params, f.ParamKinds, f.Caveats)
	res.Exact = f.Exact
	res.Sargable = f.Sargable
	res.Guard = f.Guard
	res.Prefilter = f.Prefilter
	res.SeparatePrefilter = f.SeparatePrefilter
	res.Canonical = f.Canonical
	return res
}

func (e *Emit) TextOperand(f *Fragment) *Fragment {
	if f.Exact {
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

	res := NewFragment(parts, KindText, e.dialect, f.Params, f.ParamKinds, f.Caveats)
	res.Exact = f.Exact
	res.Sargable = f.Sargable
	res.Guard = f.Guard
	res.Prefilter = f.Prefilter
	res.SeparatePrefilter = f.SeparatePrefilter
	res.Canonical = f.Canonical
	return res
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
	var parts []Part

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
			// were a code point (GO-C24).
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
				// emits nothing (GO-C39).
				if frm > len(args) {
					frm = len(args)
				}
				joinSub(args[frm:])
				continue
			}
		}
		if k, ok := slotRegex(slot); ok {
			if k >= len(args) {
				Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("the mapping for this expression asks for argument %d, which it was not given", k), pos)
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
			Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("a template used {%s}, which is neither an argument nor a lexical entry of dialect %s", slot, e.dialect), pos)
		}

		if !hasArg || argStr == "" {
			push(valStr)
			continue
		}

		if expanding != nil && expanding[key] {
			Refuse("E_SQL_UNSUPPORTED", fmt.Sprintf("the %s lexical entry of dialect %s expands into itself, so filling it would never finish", key, e.dialect), pos)
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

			subTpl := FillSlot(valStr, "{0}", "{"+one+"}")
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
