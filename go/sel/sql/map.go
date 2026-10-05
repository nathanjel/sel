package sql

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

var sections = []string{"ops", "funcs", "skel"}

const missingEntry = "\x00missing"

type BuilderFn func(emit *Emit, args []*Fragment, pos Pos) *Fragment

type EntryKind int

const (
	EntryKindRefusal EntryKind = iota
	EntryKindTemplate
	EntryKindBuilder
)

type EntryRecord struct {
	Key      string
	Kind     EntryKind
	Reason   string
	Tpl      interface{} // string or map[string]string
	Variants map[string]string
	Ret      string
	Caveat   string
	Since    string
	Arity    *[2]int
	Args     []string
	Builder  BuilderFn
}

type dialectRecord struct {
	Name    string
	Extends *string
	Version string
	Target  bool
	Lexical map[string]interface{}
	Ops     map[string]*EntryRecord
	Funcs   map[string]*EntryRecord
	Skel    map[string]*EntryRecord
}

type rulesData struct {
	OpArity      map[string][2]int
	FuncArity    map[string][2]*int
	SkelSlots    map[string][]string
	Variants     map[string][]string
	Caveats      []string
	RetKinds     []string
	ArgKinds     []string
	LexicalTypes map[string]string
	TemplateKeys []string
}

var (
	initOnce        sync.Once
	shippedDialects map[string]*dialectRecord
	shippedRules    rulesData

	mapMu        sync.Mutex
	extra        = make(map[string]*dialectRecord)
	overlay      = make(map[string]map[string]map[string]*EntryRecord)
	guardChecked = make(map[string]bool)
	hostArities  = make(map[string]map[string][2]int)
)

func initShipped() {
	var rawDialects map[string]struct {
		Dialect string                 `json:"dialect"`
		Extends *string                `json:"extends"`
		Version string                 `json:"version"`
		Target  bool                   `json:"target"`
		Lexical map[string]interface{} `json:"lexical"`
		Ops     map[string]interface{} `json:"ops"`
		Funcs   map[string]interface{} `json:"funcs"`
		Skel    map[string]interface{} `json:"skel"`
	}

	if err := json.Unmarshal([]byte(shippedDialectsJSON), &rawDialects); err != nil {
		panic("failed to parse shippedDialectsJSON: " + err.Error())
	}

	var rawRules struct {
		OpArity      map[string][2]int        `json:"opArity"`
		FuncArity    map[string][]interface{} `json:"funcArity"`
		SkelSlots    map[string][]string      `json:"skelSlots"`
		Variants     map[string][]string      `json:"variants"`
		Caveats      []string                 `json:"caveats"`
		RetKinds     []string                 `json:"retKinds"`
		ArgKinds     []string                 `json:"argKinds"`
		LexicalTypes map[string]string        `json:"lexicalTypes"`
		TemplateKeys []string                 `json:"templateKeys"`
	}
	if err := json.Unmarshal([]byte(shippedRulesJSON), &rawRules); err != nil {
		panic("failed to parse shippedRulesJSON: " + err.Error())
	}

	shippedRules.OpArity = rawRules.OpArity
	shippedRules.FuncArity = make(map[string][2]*int)
	for k, arr := range rawRules.FuncArity {
		var a [2]*int
		if len(arr) > 0 && arr[0] != nil {
			v := int(arr[0].(float64))
			a[0] = &v
		}
		if len(arr) > 1 && arr[1] != nil {
			v := int(arr[1].(float64))
			a[1] = &v
		}
		shippedRules.FuncArity[k] = a
	}
	shippedRules.SkelSlots = rawRules.SkelSlots
	shippedRules.Variants = rawRules.Variants
	shippedRules.Caveats = rawRules.Caveats
	shippedRules.RetKinds = rawRules.RetKinds
	shippedRules.ArgKinds = rawRules.ArgKinds
	shippedRules.LexicalTypes = rawRules.LexicalTypes
	shippedRules.TemplateKeys = rawRules.TemplateKeys

	shippedDialects = make(map[string]*dialectRecord)
	for name, d := range rawDialects {
		rec := &dialectRecord{
			Name:    d.Dialect,
			Extends: d.Extends,
			Version: d.Version,
			Target:  d.Target,
			Lexical: d.Lexical,
			Ops:     parseSectionEntries(d.Ops),
			Funcs:   parseSectionEntries(d.Funcs),
			Skel:    parseSectionEntries(d.Skel),
		}
		shippedDialects[name] = rec
	}
}

func parseSectionEntries(m map[string]interface{}) map[string]*EntryRecord {
	out := make(map[string]*EntryRecord)
	for k, v := range m {
		out[k] = toEntryRecord(k, v)
	}
	return out
}

func toEntryRecord(key string, v interface{}) *EntryRecord {
	if v == nil {
		return &EntryRecord{
			Key:  key,
			Kind: EntryKindRefusal,
		}
	}
	if s, ok := v.(string); ok {
		return &EntryRecord{
			Key:    key,
			Kind:   EntryKindRefusal,
			Reason: s,
		}
	}
	if m, ok := v.(map[string]interface{}); ok {
		rec := &EntryRecord{
			Key:  key,
			Kind: EntryKindTemplate,
		}
		if b, ok := m["builder"].(BuilderFn); ok {
			rec.Kind = EntryKindBuilder
			rec.Builder = b
		} else if b, ok := m["builder"].(func(*Emit, []*Fragment, Pos) *Fragment); ok {
			rec.Kind = EntryKindBuilder
			rec.Builder = b
		}
		if t, ok := m["tpl"]; ok {
			if s, ok := t.(string); ok {
				rec.Tpl = s
			} else if sm, ok := t.(map[string]interface{}); ok {
				tpls := make(map[string]string)
				for tk, tv := range sm {
					if tvs, ok := tv.(string); ok {
						tpls[tk] = tvs
					} else if tv == nil {
						tpls[tk] = ""
					}
				}
				rec.Tpl = tpls
			} else if sm, ok := t.(map[string]string); ok {
				rec.Tpl = sm
			}
		}
		if vs, ok := m["variants"].(map[string]interface{}); ok {
			variants := make(map[string]string)
			for vk, vv := range vs {
				if vvs, ok := vv.(string); ok {
					variants[vk] = vvs
				} else if vv == nil {
					variants[vk] = ""
				}
			}
			rec.Variants = variants
		} else if vs, ok := m["variants"].(map[string]string); ok {
			rec.Variants = vs
		}
		if r, ok := m["ret"].(string); ok {
			rec.Ret = r
		}
		if c, ok := m["caveat"].(string); ok {
			rec.Caveat = c
		}
		if s, ok := m["since"].(string); ok {
			rec.Since = s
		}
		if a, ok := m["arity"].([]interface{}); ok && len(a) == 2 {
			lo := int(a[0].(float64))
			hi := int(a[1].(float64))
			rec.Arity = &[2]int{lo, hi}
		} else if a, ok := m["arity"].([2]int); ok {
			rec.Arity = &[2]int{a[0], a[1]}
		} else if a, ok := m["arity"].([]int); ok && len(a) == 2 {
			rec.Arity = &[2]int{a[0], a[1]}
		}
		if args, ok := m["args"].([]interface{}); ok {
			for _, arg := range args {
				if s, ok := arg.(string); ok {
					rec.Args = append(rec.Args, s)
				}
			}
		} else if args, ok := m["args"].([]string); ok {
			rec.Args = args
		}
		return rec
	}
	if rec, ok := v.(*EntryRecord); ok {
		return rec
	}
	return &EntryRecord{Key: key, Kind: EntryKindRefusal}
}

func ensureInit() {
	initOnce.Do(initShipped)
}

func Reset() {
	mapMu.Lock()
	defer mapMu.Unlock()
	ensureInit()
	extra = make(map[string]*dialectRecord)
	chainMemo = make(map[string][]string)
	escaperMemo = make(map[string]*escaper)
	overlay = make(map[string]map[string]map[string]*EntryRecord)
	guardChecked = make(map[string]bool)
	hostArities = make(map[string]map[string][2]int)
}

func Exists(dialect string) bool {
	mapMu.Lock()
	defer mapMu.Unlock()
	ensureInit()
	return extra[dialect] != nil || shippedDialects[dialect] != nil
}

func getRecord(dialect string) *dialectRecord {
	if r, ok := extra[dialect]; ok {
		return r
	}
	return shippedDialects[dialect]
}

// ShippedDialectNames returns the names of all shipped dialects.
func ShippedDialectNames() []string {
	ensureInit()
	mapMu.Lock()
	defer mapMu.Unlock()
	var out []string
	for k := range shippedDialects {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ShippedLexicalKeys returns the lexical keys defined for a dialect.
func ShippedLexicalKeys(dialect string) []string {
	ensureInit()
	mapMu.Lock()
	defer mapMu.Unlock()
	var out []string
	if d, ok := shippedDialects[dialect]; ok {
		for k := range d.Lexical {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

// ShippedSectionKeys returns keys defined in a section for a dialect.
func ShippedSectionKeys(dialect, section string) []string {
	ensureInit()
	mapMu.Lock()
	defer mapMu.Unlock()
	var out []string
	if d, ok := shippedDialects[dialect]; ok {
		var sec map[string]*EntryRecord
		switch section {
		case "ops":
			sec = d.Ops
		case "funcs":
			sec = d.Funcs
		case "skel":
			sec = d.Skel
		}
		for k := range sec {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

// Dialects returns the sorted names of the dialects a translation can target:
// the shipped ones and those an application defined, without the bases
// (ansi, mysql-family) that only exist to be inherited from.
func Dialects() []string {
	mapMu.Lock()
	defer mapMu.Unlock()
	ensureInit()
	set := make(map[string]bool)
	for d, r := range shippedDialects {
		if r.Target {
			set[d] = true
		}
	}
	for d, r := range extra {
		if r.Target {
			set[d] = true
		}
	}
	out := make([]string, 0, len(set))
	for d := range set {
		out = append(out, d)
	}
	sort.Strings(out)
	return out
}

func requireTarget(dialect string, pos Pos) {
	if !Exists(dialect) {
		refuse("E_SQL_DIALECT", fmt.Sprintf("there is no SQL dialect %s; known targets are %s", dialect, strings.Join(Dialects(), ", ")), pos)
	}
	mapMu.Lock()
	rec := getRecord(dialect)
	mapMu.Unlock()
	if !rec.Target {
		refuse("E_SQL_DIALECT", fmt.Sprintf("%s is a base other dialects inherit from, not a server anyone runs; translate to one of %s", dialect, strings.Join(Dialects(), ", ")), pos)
	}
}

// chainMemo caches each dialect's inheritance chain: Chain ran for every
// identifier, literal, placeholder and template slot, each time allocating a set
// and a slice and taking the lock. The chain is a function of the registered
// dialects only, so it is cleared whenever extra changes (Reset, DefineDialect and
// its rollback). Guarded by mapMu; the slices in it are never handed out.
var chainMemo = make(map[string][]string)

// escaperMemo caches each dialect's text quote and compiled escape replacer (without it,
// TextLiteral sorted the escape keys and scanned with a prefix test per key on every
// literal). Like chainMemo it is a function of the registered dialects only, so it
// is cleared wherever chainMemo is. Guarded by mapMu.
var escaperMemo = make(map[string]*escaper)

// chainLocked is Chain with mapMu already held. Its result is shared: do not modify.
func chainLocked(dialect string) []string {
	ensureInit()
	if out, ok := chainMemo[dialect]; ok {
		return out
	}
	out := buildChain(dialect)
	// An unknown dialect has an empty chain; do not remember it, so a later
	// registration of that name is seen at once.
	if len(out) > 0 {
		chainMemo[dialect] = out
	}
	return out
}

func Chain(dialect string) []string {
	mapMu.Lock()
	defer mapMu.Unlock()
	out := chainLocked(dialect)
	cp := make([]string, len(out))
	copy(cp, out)
	return cp
}

func buildChain(dialect string) []string {
	var out []string
	seen := make(map[string]bool)
	cur := dialect
	for cur != "" && (extra[cur] != nil || shippedDialects[cur] != nil) && !seen[cur] {
		seen[cur] = true
		out = append(out, cur)
		rec := getRecord(cur)
		if rec.Extends == nil {
			break
		}
		cur = *rec.Extends
	}
	return out
}

func Version(dialect string) string {
	mapMu.Lock()
	defer mapMu.Unlock()
	ensureInit()
	rec := getRecord(dialect)
	if rec == nil {
		return ""
	}
	return rec.Version
}

func Lexical(dialect, key string) interface{} {
	mapMu.Lock()
	defer mapMu.Unlock()
	return lexicalLocked(dialect, key)
}

// lexicalLocked is Lexical with mapMu already held.
func lexicalLocked(dialect, key string) interface{} {
	for _, d := range chainLocked(dialect) {
		rec := getRecord(d)
		if rec != nil && rec.Lexical != nil {
			if v, ok := rec.Lexical[key]; ok {
				return v
			}
		}
	}
	return nil
}

func Entry(dialect, section, key string) interface{} {
	checkSection(section)

	mapMu.Lock()
	defer mapMu.Unlock()
	ch := chainLocked(dialect)

	// Overlay chain first
	for _, d := range ch {
		if secMap, ok := overlay[d]; ok {
			if bySec, ok := secMap[section]; ok {
				if e, ok := bySec[key]; ok {
					return e
				}
			}
		}
	}

	// Shipped table
	for _, d := range ch {
		if dRec, ok := shippedDialects[d]; ok {
			var secMap map[string]*EntryRecord
			switch section {
			case "ops":
				secMap = dRec.Ops
			case "funcs":
				secMap = dRec.Funcs
			case "skel":
				secMap = dRec.Skel
			}
			if e, ok := secMap[key]; ok {
				return e
			}
		}
	}

	return missingEntry
}

var dottedRe = regexp.MustCompile(`^[0-9]+(\.[0-9]+)*$`)
var tplKeyRe = regexp.MustCompile(`^(0|[1-9][0-9]{0,2})$`)
var unifyRe = regexp.MustCompile(`^@unify:[0-9]+(,[0-9]+)*$`)
var slotInTplRe = regexp.MustCompile(`\{([^}]*)\}`)

// DefineDialect registers a dialect. Registration errors are panics, the
// host's startup-error class, never a SqlError: TryTranslate must not swallow
// them. A name registered again under the SAME parent replaces the earlier
// registration; under another parent it is refused, because cases already
// translated through it would silently change meaning.
func DefineDialect(name string, spec map[string]interface{}) {
	prev, hadPrev := defineDialectLocked(name, spec)
	ok := false
	defer func() {
		if ok {
			return
		}
		r := recover()
		mapMu.Lock()
		if hadPrev {
			extra[name] = prev
		} else {
			delete(extra, name)
		}
		chainMemo = make(map[string][]string)
		escaperMemo = make(map[string]*escaper)
		guardChecked = make(map[string]bool)
		mapMu.Unlock()
		panic(r)
	}()
	checkQuoting(name)
	ok = true
}

// checkQuoting holds a registered dialect's quoting to what an inline literal
// needs (sql/MAP.md §3.1): a one-character text quote that is not the
// identifier quote, and an escape map that covers the quote, and covers the
// backslash whenever a backslash is what escapes it. Refused here, once, and
// not at every use, which a hostile program could then keep trying.
func checkQuoting(name string) {
	tq, _ := Lexical(name, "textQuote").(string)
	iq, _ := Lexical(name, "identQuote").(string)
	if tq == "" {
		return
	}
	if len([]rune(tq)) != 1 {
		panic(fmt.Sprintf("SQL dialect %s sets textQuote to %q; a quote is one character", name, tq))
	}
	if tq == iq {
		panic(fmt.Sprintf("SQL dialect %s uses %q for both text and identifiers; a text literal would read as a quoted identifier", name, tq))
	}
	var esc map[string]interface{}
	switch m := Lexical(name, "textEscape").(type) {
	case map[string]interface{}:
		esc = m
	case map[string]string:
		esc = make(map[string]interface{}, len(m))
		for k, v := range m {
			esc[k] = v
		}
	}
	rep, ok := esc[tq].(string)
	if !ok || rep == "" {
		panic(fmt.Sprintf("SQL dialect %s has no textEscape entry for its text quote %s, so a value containing one would end the literal; map it (doubling it is the portable spelling)", name, tq))
	}
	// The only escape character a SQL server has is the backslash, so the quote is kept
	// inside the literal by doubling it or by a backslash before it (sql/MAP.md 3.1).
	if rep != tq+tq && rep != "\\"+tq {
		panic(fmt.Sprintf("SQL dialect %s escapes its text quote as %q, which does not keep the quote inside the literal: it must be the quote doubled, or a backslash followed by the quote", name, rep))
	}
	// A value that introduces a backslash escape presupposes a server that reads
	// backslashes, so a backslash in the data must be doubled too, or it swallows the
	// character after it.
	for _, v := range esc {
		if str, ok := v.(string); ok && strings.HasPrefix(str, "\\") {
			if bs, ok := esc["\\"].(string); !ok || bs != "\\\\" {
				panic(fmt.Sprintf("SQL dialect %s has a textEscape that uses a backslash escape but does not map the backslash to two", name))
			}
			break
		}
	}
}

func defineDialectLocked(name string, spec map[string]interface{}) (*dialectRecord, bool) {
	mapMu.Lock()
	defer mapMu.Unlock()
	ensureInit()

	if shippedDialects[name] != nil {
		panic(fmt.Sprintf("SQL dialect %s is already defined; a name means one dialect", name))
	}
	prev := extra[name]

	var unknown []string
	allowed := map[string]bool{"extends": true, "version": true, "target": true, "lexical": true}
	for k := range spec {
		if !allowed[k] {
			unknown = append(unknown, k)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		panic(fmt.Sprintf("SQL dialect %s declares %s, which a dialect declaration does not carry; ops, funcs and skel entries are defined one at a time with Define", name, strings.Join(unknown, ", ")))
	}

	if _, ok := spec["extends"]; !ok {
		panic(fmt.Sprintf("SQL dialect %s must say what it extends; write extends: null for a dialect with no parent, as ansi has", name))
	}

	var ext *string
	if spec["extends"] != nil {
		var s string
		if str, ok := spec["extends"].(string); ok {
			s = str
			ext = &s
		} else if pstr, ok := spec["extends"].(*string); ok {
			if pstr != nil {
				s = *pstr
				ext = &s
			}
		} else {
			panic(fmt.Sprintf("SQL dialect %s extends must be a string or null", name))
		}
		if ext != nil && extra[s] == nil && shippedDialects[s] == nil {
			panic(fmt.Sprintf("SQL dialect %s extends %s, which does not exist", name, s))
		}
	}

	var version string
	if ext == nil {
		if spec["version"] == nil {
			panic(fmt.Sprintf("SQL dialect %s extends nothing, so it must declare a version; there is none to inherit", name))
		}
		s, ok := spec["version"].(string)
		if !ok {
			panic(fmt.Sprintf("SQL dialect %s has version %v, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)", name, spec["version"]))
		}
		version = s
	} else {
		if spec["version"] != nil {
			s, ok := spec["version"].(string)
			if !ok {
				panic(fmt.Sprintf("SQL dialect %s has version %v, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)", name, spec["version"]))
			}
			version = s
		} else {
			version = getRecord(*ext).Version
		}
	}

	if !dottedRe.MatchString(version) {
		panic(fmt.Sprintf("SQL dialect %s has version %q, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)", name, version))
	}

	target := true
	if spec["target"] != nil {
		b, ok := spec["target"].(bool)
		if !ok {
			panic(fmt.Sprintf("SQL dialect %s has a target that is not a boolean; truthiness differs between hosts and must not decide this", name))
		}
		target = b
	}

	lexical := make(map[string]interface{})
	if spec["lexical"] != nil {
		lm, ok := spec["lexical"].(map[string]interface{})
		if !ok {
			panic(fmt.Sprintf("SQL dialect %s has a lexical that is not a map", name))
		}
		lexical = lm
	}

	for k, v := range lexical {
		checkLexical(k, v, fmt.Sprintf("SQL dialect %s", name))
	}

	if prev != nil {
		same := (prev.Extends == nil && ext == nil) || (prev.Extends != nil && ext != nil && *prev.Extends == *ext)
		if !same {
			panic(fmt.Sprintf("SQL dialect %s is already registered under another parent; a name means one dialect", name))
		}
		delete(overlay, name)
		delete(hostArities, name)
		guardChecked = make(map[string]bool)
	}
	chainMemo = make(map[string][]string)
	escaperMemo = make(map[string]*escaper)
	extra[name] = &dialectRecord{
		Name:    name,
		Extends: ext,
		Version: version,
		Target:  target,
		Lexical: lexical,
		Ops:     make(map[string]*EntryRecord),
		Funcs:   make(map[string]*EntryRecord),
		Skel:    make(map[string]*EntryRecord),
	}
	return prev, prev != nil
}

func Define(dialect, section, key string, entry interface{}) {
	checkSection(section)
	if !Exists(dialect) {
		panic(fmt.Sprintf("SQL dialect %s does not exist", dialect))
	}
	checkKey(section, key)
	checkEntry(section, key, entry)

	k := key
	if section == "funcs" {
		k = utf8.AsciiUpper(key)
	}

	mapMu.Lock()
	defer mapMu.Unlock()

	if overlay[dialect] == nil {
		overlay[dialect] = make(map[string]map[string]*EntryRecord)
	}
	if overlay[dialect][section] == nil {
		overlay[dialect][section] = make(map[string]*EntryRecord)
	}
	rec := toEntryRecord(k, entry)
	overlay[dialect][section][k] = rec

	var arity *[2]int
	if section == "funcs" {
		if lo, hi, ok := sel.HostArity(k); ok {
			arity = &[2]int{lo, hi}
		}
	}
	if arity != nil {
		if hostArities[dialect] == nil {
			hostArities[dialect] = make(map[string][2]int)
		}
		hostArities[dialect][k] = *arity
	} else if hostArities[dialect] != nil {
		delete(hostArities[dialect], k)
	}

	if section == "funcs" && k == "ISNUM" {
		guardChecked = make(map[string]bool)
	}
}

func DefineBuilder(dialect, section, key string, fn BuilderFn) {
	Define(dialect, section, key, map[string]interface{}{"builder": fn})
}

func hostSpellingArity(dialect, key string) *[2]int {
	ch := Chain(dialect)
	mapMu.Lock()
	defer mapMu.Unlock()
	for _, d := range ch {
		if overlay[d] != nil && overlay[d]["funcs"] != nil {
			if _, ok := overlay[d]["funcs"][key]; ok {
				if ha, ok := hostArities[d][key]; ok {
					return &[2]int{ha[0], ha[1]}
				}
				return nil
			}
		}
	}
	return nil
}

func quotedRuns(tpl string) []string {
	var out []string
	start := -1
	for i := 0; i < len(tpl); i++ {
		if tpl[i] == '\'' {
			if start == -1 {
				start = i + 1
			} else {
				out = append(out, tpl[start:i])
				start = -1
			}
		}
	}
	return out
}

func checkNumericGuard(dialect string) {
	mapMu.Lock()
	if guardChecked[dialect] {
		mapMu.Unlock()
		return
	}
	mapMu.Unlock()

	guard := Lexical(dialect, "numericGuard")
	guardStr, ok := guard.(string)
	if !ok {
		markGuardChecked(dialect)
		return
	}

	isnum := Entry(dialect, "funcs", "ISNUM")
	var tplStr string
	if rec, ok := isnum.(*EntryRecord); ok {
		if s, ok := rec.Tpl.(string); ok {
			tplStr = s
		}
	}
	if tplStr == "" {
		panic(fmt.Sprintf("SQL dialect %s declares a numericGuard but maps no funcs.ISNUM with a template for it to agree with; the two ask the same question and sql/MAP.md §7 rule 10 is that one place defines a thing", dialect))
	}

	want := quotedRuns(tplStr)
	if len(want) == 0 {
		panic(fmt.Sprintf("SQL dialect %s maps a funcs.ISNUM that carries no quoted pattern, so its numericGuard has nothing to agree with", dialect))
	}

	gotRuns := quotedRuns(guardStr)
	gotSet := make(map[string]bool)
	for _, g := range gotRuns {
		gotSet[g] = true
	}

	var missing []string
	for _, w := range want {
		if !gotSet[w] {
			missing = append(missing, fmt.Sprintf("'%s'", w))
		}
	}
	if len(missing) > 0 {
		panic(fmt.Sprintf("SQL dialect %s declares a numericGuard that does not carry %s, which its funcs.ISNUM tests; they ask the same question, and a guard that asks a different one answers for rows SEL refuses", dialect, strings.Join(missing, ", ")))
	}
	// Marked only once it has passed: a dialect that fails the check fails it on
	// every use, not just the first.
	markGuardChecked(dialect)
}

func markGuardChecked(dialect string) {
	mapMu.Lock()
	guardChecked[dialect] = true
	mapMu.Unlock()
}

func checkLexical(key string, v interface{}, where string) {
	ensureInit()
	expectedType, ok := shippedRules.LexicalTypes[key]
	if !ok {
		var known []string
		for k := range shippedRules.LexicalTypes {
			known = append(known, k)
		}
		sort.Strings(known)
		panic(fmt.Sprintf("%s sets the unknown lexical key %s; known keys are %s", where, key, strings.Join(known, ", ")))
	}
	if v == nil {
		return
	}
	if expectedType == "map" {
		m, ok := v.(map[string]interface{})
		if !ok {
			m2, ok2 := v.(map[string]string)
			if !ok2 {
				panic(fmt.Sprintf("%s sets %s to a %s; it must be a map of character to replacement", where, key, typeName(v)))
			}
			m = make(map[string]interface{})
			for k, val := range m2 {
				m[k] = val
			}
		}
		for from, to := range m {
			if from == "" || to == nil {
				panic(fmt.Sprintf("%s's %s maps %q to something that is not a string", where, key, from))
			}
			if _, ok := to.(string); !ok {
				panic(fmt.Sprintf("%s's %s maps %q to something that is not a string", where, key, from))
			}
		}
		return
	}

	s, ok := v.(string)
	if !ok {
		panic(fmt.Sprintf("%s sets %s to a %s; it must be a string", where, key, typeName(v)))
	}
	if s == "" && (key == "identQuote" || key == "textQuote") {
		panic(fmt.Sprintf("%s sets %s to the empty string; a quote character that is not a character cannot quote", where, key))
	}
}

func checkKey(section, key string) {
	ensureInit()
	if section == "ops" {
		if _, ok := shippedRules.OpArity[key]; !ok {
			panic(fmt.Sprintf("%s is not a SEL operator, so an ops entry for it would never be looked up", key))
		}
	} else if section == "funcs" {
		upper := utf8.AsciiUpper(key)
		_, isSelFunc := shippedRules.FuncArity[upper]
		_, _, isHostFunc := sel.HostArity(key)
		if !isSelFunc && !isHostFunc {
			panic(fmt.Sprintf("%s is neither a SEL function this layer maps nor a registered host function. A host function is registered (sel.RegisterFunction) before it is given a SQL spelling; the aggregates and IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by stage 2 and never reach the funcs table", key))
		}
	} else if section == "skel" {
		if _, ok := shippedRules.SkelSlots[key]; !ok {
			var known []string
			for k := range shippedRules.SkelSlots {
				known = append(known, k)
			}
			sort.Strings(known)
			panic(fmt.Sprintf("%s is not a skeleton; known ones are %s", key, strings.Join(known, ", ")))
		}
	}
}

func checkEntry(section, key string, e interface{}) {
	where := fmt.Sprintf("the %s entry for %s", section, key)
	if e == nil {
		return
	}
	if _, ok := e.(string); ok {
		return
	}

	var m map[string]interface{}
	if rec, ok := e.(*EntryRecord); ok {
		m = make(map[string]interface{})
		if rec.Kind == EntryKindBuilder {
			m["builder"] = rec.Builder
		}
		if rec.Tpl != nil {
			m["tpl"] = rec.Tpl
		}
		if rec.Variants != nil {
			m["variants"] = rec.Variants
		}
		if rec.Ret != "" {
			m["ret"] = rec.Ret
		}
		if rec.Caveat != "" {
			m["caveat"] = rec.Caveat
		}
		if rec.Since != "" {
			m["since"] = rec.Since
		}
		if rec.Arity != nil {
			m["arity"] = *rec.Arity
		}
		if rec.Args != nil {
			m["args"] = rec.Args
		}
	} else if mm, ok := e.(map[string]interface{}); ok {
		m = mm
	} else {
		panic(fmt.Sprintf("%s must be a map, a string or null, and is %s", where, typeName(e)))
	}

	var host *[2]int
	if section == "funcs" {
		if lo, hi, ok := sel.HostArity(key); ok {
			host = &[2]int{lo, hi}
		}
	}

	if argsVal, ok := m["args"]; ok && argsVal != nil {
		checkArgs(key, argsVal, host, where)
	}

	if bVal, ok := m["builder"]; ok && bVal != nil {
		if _, ok := bVal.(BuilderFn); !ok {
			if _, ok2 := bVal.(func(*Emit, []*Fragment, Pos) *Fragment); !ok2 {
				panic(fmt.Sprintf("%s has a builder that is not callable; use DefineBuilder", where))
			}
		}
		return
	}

	if section == "skel" {
		tplVal, ok := m["tpl"]
		if !ok || tplVal == nil {
			panic(fmt.Sprintf("%s needs a tpl that is a string", where))
		}
		tplStr, ok := tplVal.(string)
		if !ok {
			panic(fmt.Sprintf("%s needs a tpl that is a string", where))
		}
		allowed := shippedRules.SkelSlots[key]
		allowedMap := make(map[string]bool)
		for _, a := range allowed {
			allowedMap[a] = true
		}
		matches := slotInTplRe.FindAllStringSubmatch(tplStr, -1)
		for _, match := range matches {
			slotName := match[1]
			if !allowedMap[slotName] {
				panic(fmt.Sprintf("%s uses the slot {%s}; %s has %s — a typo would survive as literal text in every query", where, slotName, key, strings.Join(allowed, ", ")))
			}
		}
		if caveatVal, ok := m["caveat"]; ok && caveatVal != nil {
			cStr, ok := caveatVal.(string)
			if !ok || !containsString(shippedRules.Caveats, cStr) {
				panic(fmt.Sprintf("%s declares the caveat %q, which is not on the closed list in sql/MAP.md §4.6", where, caveatVal))
			}
		}
		return
	}

	hasTpl := m["tpl"] != nil
	hasVariants := m["variants"] != nil
	if hasTpl == hasVariants {
		panic(fmt.Sprintf("%s needs exactly one of tpl and variants", where))
	}

	retVal, ok := m["ret"]
	if !ok || retVal == nil {
		panic(fmt.Sprintf("%s has ret null; use one of %s, @concat or @unify:<n>[,<n>...]", where, strings.Join(shippedRules.RetKinds, ", ")))
	}
	retStr, ok := retVal.(string)
	if !ok || (!containsString(shippedRules.RetKinds, retStr) && retStr != "@concat" && !unifyRe.MatchString(retStr)) {
		panic(fmt.Sprintf("%s has ret %q; use one of %s, @concat or @unify:<n>[,<n>...]", where, retVal, strings.Join(shippedRules.RetKinds, ", ")))
	}

	if caveatVal, ok := m["caveat"]; ok && caveatVal != nil {
		cStr, ok := caveatVal.(string)
		if !ok || !containsString(shippedRules.Caveats, cStr) {
			panic(fmt.Sprintf("%s declares the caveat %q, which is not on the closed list in sql/MAP.md §4.6; a caveat an application cannot branch on is prose", where, caveatVal))
		}
	}

	if sinceVal, ok := m["since"]; ok && sinceVal != nil {
		sStr, ok := sinceVal.(string)
		if !ok || !dottedRe.MatchString(sStr) {
			panic(fmt.Sprintf("%s has a since that is not dotted-numeric", where))
		}
	}

	var entryArity *[2]int
	if arityVal, ok := m["arity"]; ok && arityVal != nil {
		var a [2]int
		ok := false
		if arr, isArr := arityVal.([]interface{}); isArr && len(arr) == 2 {
			if n1, ok1 := arr[0].(float64); ok1 {
				if n2, ok2 := arr[1].(float64); ok2 {
					a[0] = int(n1)
					a[1] = int(n2)
					ok = true
				}
			}
		} else if arr, isArr := arityVal.([]int); isArr && len(arr) == 2 {
			a[0] = arr[0]
			a[1] = arr[1]
			ok = true
		} else if arr, isArr := arityVal.([2]int); isArr {
			a = arr
			ok = true
		}
		if !ok || a[0] < 0 || a[1] < a[0] {
			panic(fmt.Sprintf("%s has an arity that is not [min, max] of two integers", where))
		}
		entryArity = &a
	}

	if entryArity != nil && host != nil {
		if entryArity[0] < host[0] || entryArity[1] > host[1] {
			panic(fmt.Sprintf("%s has the arity [%d, %d], which is wider than %s's registered [%d, %d]; an entry may only narrow it", where, entryArity[0], entryArity[1], key, host[0], host[1]))
		}
	}

	if hasVariants {
		vsVal := m["variants"]
		var vsMap map[string]interface{}
		if vm, ok := vsVal.(map[string]interface{}); ok {
			vsMap = vm
		} else if vm, ok := vsVal.(map[string]string); ok {
			vsMap = make(map[string]interface{})
			for vk, vv := range vm {
				vsMap[vk] = vv
			}
		} else {
			panic(fmt.Sprintf("%s has variants that are not a map", where))
		}
		if len(vsMap) == 0 {
			panic(fmt.Sprintf("%s has variants that are not a map", where))
		}
		allowed, ok := shippedRules.Variants[key]
		if !ok {
			panic(fmt.Sprintf("%s uses variants, and %s is not a variant family", where, key))
		}
		for name := range vsMap {
			if !containsString(allowed, name) {
				panic(fmt.Sprintf("%s declares the variant %s; %s has %s", where, name, key, strings.Join(allowed, ", ")))
			}
		}
	}

	if hasTpl {
		tplVal := m["tpl"]
		var tplMap map[string]interface{}
		if tm, ok := tplVal.(map[string]interface{}); ok {
			tplMap = tm
		} else if tm, ok := tplVal.(map[string]string); ok {
			tplMap = make(map[string]interface{})
			for tk, tv := range tm {
				tplMap[tk] = tv
			}
		} else if arr, ok := tplVal.([]interface{}); ok {
			tplMap = make(map[string]interface{})
			for idx, tv := range arr {
				tplMap[strconv.Itoa(idx)] = tv
			}
		} else if arr, ok := tplVal.([]string); ok {
			tplMap = make(map[string]interface{})
			for idx, tv := range arr {
				tplMap[strconv.Itoa(idx)] = tv
			}
		}

		if tplMap != nil {
			var baseLo int
			var baseHi *int
			if section == "ops" {
				ar := shippedRules.OpArity[key]
				baseLo = ar[0]
				h := ar[1]
				baseHi = &h
			} else if host != nil {
				baseLo = host[0]
				h := host[1]
				baseHi = &h
			} else {
				ar := shippedRules.FuncArity[utf8.AsciiUpper(key)]
				if ar[0] != nil {
					baseLo = *ar[0]
				}
				baseHi = ar[1]
			}

			lo := baseLo
			hi := baseHi
			if entryArity != nil {
				if entryArity[0] > lo {
					lo = entryArity[0]
				}
				if hi == nil || entryArity[1] < *hi {
					h := entryArity[1]
					hi = &h
				}
			}

			for n := range tplMap {
				if n == "*" {
					continue
				}
				if !tplKeyRe.MatchString(n) {
					panic(fmt.Sprintf("%s keys a template by %q; an arity-keyed template uses a count or *", where, n))
				}
				c, _ := strconv.Atoi(n)
				if c < lo || (hi != nil && c > *hi) {
					hiStr := "any"
					if hi != nil {
						hiStr = strconv.Itoa(*hi)
					}
					panic(fmt.Sprintf("%s keys a template by %d, and %s takes %d to %s argument(s), so that template could never be chosen", where, c, key, lo, hiStr))
				}
			}
		}
	}
}

func checkArgs(key string, argsVal interface{}, host *[2]int, where string) {
	if host == nil {
		panic(fmt.Sprintf("%s declares args, and %s is not a host function: a builtin's argument rules are SEL's own", where, key))
	}
	var args []string
	if arr, ok := argsVal.([]interface{}); ok {
		for _, a := range arr {
			s, ok := a.(string)
			if !ok {
				panic(fmt.Sprintf("%s has args that are not a list of kinds", where))
			}
			args = append(args, s)
		}
	} else if arr, ok := argsVal.([]string); ok {
		args = arr
	} else {
		panic(fmt.Sprintf("%s has args that are not a list of kinds", where))
	}

	for _, a := range args {
		if !containsString(shippedRules.ArgKinds, a) {
			panic(fmt.Sprintf("%s declares the argument kind %q; use one of %s", where, a, strings.Join(shippedRules.ArgKinds, ", ")))
		}
	}
	if len(args) > host[1] {
		panic(fmt.Sprintf("%s declares %d argument kinds, and %s takes at most %d", where, len(args), key, host[1]))
	}
}

func checkSection(section string) {
	if !containsString(sections, section) {
		panic(fmt.Sprintf("unknown map section %s; use %s", section, strings.Join(sections, ", ")))
	}
}

func containsString(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func typeName(v interface{}) string {
	if v == nil {
		return "null"
	}
	switch v.(type) {
	case []interface{}, []string, []int:
		return "list"
	case map[string]interface{}, map[string]string:
		return "map"
	case string:
		return "string"
	case bool:
		return "boolean"
	case float64, int, int64:
		return "number"
	default:
		return fmt.Sprintf("%T", v)
	}
}

func versionAtLeast(have, want string) bool {
	parse := func(s string) []int {
		parts := strings.Split(s, ".")
		out := make([]int, len(parts))
		for i, p := range parts {
			out[i], _ = strconv.Atoi(p)
		}
		return out
	}
	a := parse(have)
	b := parse(want)
	maxLen := len(a)
	if len(b) > maxLen {
		maxLen = len(b)
	}
	for i := 0; i < maxLen; i++ {
		x := 0
		if i < len(a) {
			x = a[i]
		}
		y := 0
		if i < len(b) {
			y = b[i]
		}
		if x != y {
			return x > y
		}
	}
	return true
}
