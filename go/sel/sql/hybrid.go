// Maximal SQL-prefix planner for relational SEL pipelines.

package sql

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/sel"
)

type SelectedMember struct {
	PartitionKey string `json:"partition_key"`
	RevisionKey  string `json:"revision_key"`
}

type HybridPlan struct {
	SelectedMember        *SelectedMember `json:"selected_member,omitempty"`
	Dialect               string          `json:"dialect"`
	SqlStatement          *Fragment       `json:"sql_statement,omitempty"`
	SqlPrefixAst          *sel.Node       `json:"sql_prefix_ast,omitempty"`
	ContinuationAst       *sel.Node       `json:"continuation_ast,omitempty"`
	ContinuationProgram   *sel.Program    `json:"continuation_program,omitempty"`
	ContinuationSourceVar string          `json:"continuation_source_var"`
	IsHybrid              bool            `json:"is_hybrid"`
	PureSql               bool            `json:"pure_sql"`
	PureMemory            bool            `json:"pure_memory"`
	SourceTables          []string        `json:"source_tables"`
}

func (p *HybridPlan) SqlQuery() *Fragment {
	return p.SqlStatement
}

type DbRunner func(query string, params []*sel.Value) (*sel.Value, error)

func copyAstNode(n *sel.Node) *sel.Node {
	if n == nil {
		return nil
	}
	cp := *n
	if n.Items != nil {
		cp.Items = make([]*sel.Node, len(n.Items))
		copy(cp.Items, n.Items)
	}
	return &cp
}

func varNode(name string, pos Pos) *sel.Node {
	n := sel.NewNode(sel.NodeVar, pos)
	n.S = name
	return n
}

func textNode(val string, pos Pos) *sel.Node {
	n := sel.NewNode(sel.NodeText, pos)
	n.S = val
	return n
}

func indexNode(obj *sel.Node, idx *sel.Node, pos Pos) *sel.Node {
	n := sel.NewNode(sel.NodeIndex, pos)
	n.L = obj
	n.R = idx
	return n
}

var sqlSpecialCalls = map[string]bool{
	"IF": true, "COND": true, "COALESCE": true, "COUNT": true, "SUM": true,
	"AVG": true, "MIN": true, "MAX": true, "RECORD": true, "LIST": true,
}

func containsUnsupportedSql(node *sel.Node, dialect string, defs map[string]*sel.Node, seen map[string]bool) bool {
	if node == nil {
		return false
	}
	if node.T == sel.NodeVar && defs != nil {
		if def, ok := defs[node.S]; ok && !seen[node.S] {
			innerSeen := make(map[string]bool)
			for k, v := range seen {
				innerSeen[k] = v
			}
			innerSeen[node.S] = true
			return containsUnsupportedSql(def, dialect, defs, innerSeen)
		}
	}
	if node.T == sel.NodeCall {
		if !sqlSpecialCalls[node.S] {
			entry := Entry(dialect, "funcs", utf8.AsciiUpper(node.S))
			if entry == nil || entry == MISSING {
				return true
			}
			if rec, ok := entry.(*EntryRecord); ok {
				if rec.Kind == EntryKindRefusal {
					return true
				}
			} else {
				return true
			}
		}
		for _, item := range node.Items {
			if containsUnsupportedSql(item, dialect, defs, seen) {
				return true
			}
		}
	}
	if node.L != nil && containsUnsupportedSql(node.L, dialect, defs, seen) {
		return true
	}
	if node.R != nil && containsUnsupportedSql(node.R, dialect, defs, seen) {
		return true
	}
	for _, item := range node.Items {
		if containsUnsupportedSql(item, dialect, defs, seen) {
			return true
		}
	}
	return false
}

func bucketRowsAreKeys(steps []*sel.Node, count int) bool {
	open := false
	for i := 0; i < count && i < len(steps); i++ {
		step := steps[i]
		if step.S == "BUCKET" {
			if open {
				return true
			}
			open = len(step.Items) == 2
		} else if open && step.S == "MAP" {
			open = false
		} else if open && step.S != "FILTER" {
			return true
		}
	}
	return open
}

func joinRowsLackBinders(steps []*sel.Node, count int) bool {
	joined := false
	for i := 0; i < count && i < len(steps); i++ {
		step := steps[i]
		if step.S == "LINK" || step.S == "LINK_LEFT" {
			joined = true
		} else if step.S == "MAP" || step.S == "SELECT_COLS" || step.S == "BUCKET" {
			joined = false
		}
	}
	return joined
}

func rowsAreNotTheValue(steps []*sel.Node, count int) bool {
	return bucketRowsAreKeys(steps, count) || joinRowsLackBinders(steps, count)
}

func physicalSource(b *Binding) string {
	if b.Relation.From.IsRaw {
		return b.Relation.From.Raw
	}
	return b.Relation.From.Table
}

func sourceTables(ast *sel.Node, bindings *Bindings) []string {
	var out []string
	seen := make(map[string]bool)
	var visit func(n *sel.Node)
	visit = func(n *sel.Node) {
		if n == nil {
			return
		}
		if n.T == sel.NodeVar && bindings.Has(n.S) {
			b := bindings.Get(n.S, n.Pos)
			if b.Kind == BindingKindRelation {
				table := physicalSource(b)
				if !seen[table] {
					seen[table] = true
					out = append(out, table)
				}
			}
			return
		}
		if n.L != nil {
			visit(n.L)
		}
		if n.R != nil {
			visit(n.R)
		}
		for _, child := range n.Items {
			visit(child)
		}
	}
	visit(ast)
	return out
}

func statements(ast *sel.Node) (leading []*sel.Node, result *sel.Node) {
	if ast.T != sel.NodeSeq || len(ast.Items) == 0 {
		return nil, ast
	}
	return ast.Items[:len(ast.Items)-1], ast.Items[len(ast.Items)-1]
}

func assignedName(statement *sel.Node) string {
	target := statement.L
	for target != nil && target.T == sel.NodeIndex {
		target = target.L
	}
	if target != nil {
		return target.S
	}
	return ""
}

func definitions(leading []*sel.Node) map[string]*sel.Node {
	defs := make(map[string]*sel.Node)
	for _, s := range leading {
		if s.T == sel.NodeAssign && s.L != nil && s.L.T == sel.NodeVar {
			defs[s.L.S] = s.R
		}
	}
	return defs
}

func isLiteralType(t sel.NodeType) bool {
	return t == sel.NodeNum || t == sel.NodeText || t == sel.NodeBool || t == sel.NodeNull
}

func inlineLiterals(node *sel.Node, literals map[string]*sel.Node, bound []string) *sel.Node {
	if node == nil {
		return nil
	}
	t := node.T
	if t == sel.NodeVar {
		if containsString(bound, node.S) || literals[node.S] == nil {
			return node
		}
		cp := copyAstNode(literals[node.S])
		cp.Pos = node.Pos
		return cp
	}
	if isLiteralType(t) {
		return node
	}
	inlineChild := func(child *sel.Node, scope []string) *sel.Node {
		return inlineLiterals(child, literals, scope)
	}
	if t == sel.NodeUn {
		cp := copyAstNode(node)
		cp.L = inlineChild(node.L, bound)
		return cp
	}
	if t == sel.NodeBin || t == sel.NodeIndex {
		cp := copyAstNode(node)
		cp.L = inlineChild(node.L, bound)
		cp.R = inlineChild(node.R, bound)
		return cp
	}
	if t == sel.NodeList || t == sel.NodeSeq {
		cp := copyAstNode(node)
		cp.Items = make([]*sel.Node, len(node.Items))
		for i, item := range node.Items {
			cp.Items[i] = inlineChild(item, bound)
		}
		return cp
	}
	if t == sel.NodeAssign {
		cp := copyAstNode(node)
		cp.R = inlineChild(node.R, bound)
		return cp
	}
	if t == sel.NodeCall {
		inner := append([]string{}, bound...)
		binds := node.Spec != nil && node.Spec.Binds
		namedBinder := len(node.Items) == 3 && node.Items[1] != nil && node.Items[1].T == sel.NodeVar && !node.Items[1].Grouped
		if binds {
			inner = append(inner, "_K")
			if namedBinder {
				inner = append(inner, node.Items[1].S)
			} else {
				inner = append(inner, "_")
			}
		}
		cp := copyAstNode(node)
		cp.Items = make([]*sel.Node, len(node.Items))
		for i, item := range node.Items {
			if binds && i == 1 && namedBinder {
				cp.Items[i] = item
				continue
			}
			scope := bound
			if i > 0 {
				scope = inner
			}
			cp.Items[i] = inlineChild(item, scope)
		}
		return cp
	}
	return node
}

func literalHelpers(leading []*sel.Node) map[string]*sel.Node {
	literals := make(map[string]*sel.Node)
	for _, s := range leading {
		if s.T != sel.NodeAssign || s.L == nil || s.L.T != sel.NodeVar {
			continue
		}
		folded := sel.OptimizeAstLogical(inlineLiterals(s.R, literals, nil))
		if folded != nil && isLiteralType(folded.T) {
			literals[s.L.S] = folded
		}
	}
	return literals
}

func unwindThroughHelpers(result *sel.Node, defs map[string]*sel.Node, literals map[string]*sel.Node) (*sel.Node, []*sel.Node) {
	source, steps := sel.UnwindPipeline(inlineLiterals(result, literals, nil))
	seen := make(map[string]bool)
	for source != nil && source.T == sel.NodeVar && defs[source.S] != nil && !seen[source.S] {
		seen[source.S] = true
		innerSrc, innerSteps := sel.UnwindPipeline(inlineLiterals(defs[source.S], literals, nil))
		source = innerSrc
		steps = append(innerSteps, steps...)
	}
	return source, steps
}

func readNames(node *sel.Node, out map[string]bool) {
	if node == nil {
		return
	}
	if node.T == sel.NodeVar {
		out[node.S] = true
		return
	}
	if node.L != nil {
		readNames(node.L, out)
	}
	if node.R != nil {
		readNames(node.R, out)
	}
	for _, item := range node.Items {
		readNames(item, out)
	}
}

func referencedAssignments(leading []*sel.Node, node *sel.Node) []*sel.Node {
	needed := make(map[string]bool)
	readNames(node, needed)
	grew := true
	for grew {
		grew = false
		for _, s := range leading {
			targetName := assignedName(s)
			if !needed[targetName] {
				continue
			}
			reads := make(map[string]bool)
			readNames(s.R, reads)
			for name := range reads {
				if !needed[name] {
					needed[name] = true
					grew = true
				}
			}
		}
	}
	var kept []*sel.Node
	for _, s := range leading {
		if needed[assignedName(s)] {
			kept = append(kept, s)
		}
	}
	return kept
}

func withHelpers(leading []*sel.Node, node *sel.Node) *sel.Node {
	kept := referencedAssignments(leading, node)
	if len(kept) == 0 {
		return node
	}
	seq := sel.NewNode(sel.NodeSeq, kept[0].Pos)
	seq.Items = make([]*sel.Node, len(kept)+1)
	copy(seq.Items, kept)
	seq.Items[len(kept)] = node
	return seq
}

type helpersContext struct {
	leading   []*sel.Node
	defs      map[string]*sel.Node
	bindings  *Bindings
	names     map[string]bool
	constRoot *sel.Value
}

func (h *helpersContext) wrap(node *sel.Node) *sel.Node {
	return withHelpers(h.leading, node)
}

func (h *helpersContext) tables(wrapped *sel.Node) []string {
	normalized := Normalise(wrapped, h.names, h.constRoot).ToNode()
	if normalized == nil {
		normalized = wrapped
	}
	return sourceTables(normalized, h.bindings)
}

func pureMemoryPlan(program *sel.Program, dialect string, bindings *Bindings) *HybridPlan {
	return &HybridPlan{
		Dialect:               dialect,
		PureMemory:            true,
		ContinuationProgram:   program,
		ContinuationAst:       program.AST(),
		ContinuationSourceVar: "_INPUT",
		SourceTables:          sourceTables(program.AST(), bindings),
	}
}

func latestFieldName(n *sel.Node) *string {
	if n != nil && n.T == sel.NodeIndex && n.L != nil && n.L.T == sel.NodeVar && n.L.S == "_" &&
		n.R != nil && n.R.T == sel.NodeText {
		s := n.R.S
		return &s
	}
	return nil
}

func tryLatestMember(source *sel.Node, steps []*sel.Node, dialect string, catalog *Bindings, opts Options, helpers helpersContext) *HybridPlan {
	if dialect != "mariadb" && dialect != "mysql" && dialect != "postgresql" && dialect != "sqlite" {
		return nil
	}
	if !catalog.Has(source.S) {
		return nil
	}
	binding := catalog.Get(source.S, source.Pos)
	if binding.Kind != BindingKindRelation {
		return nil
	}
	rel := binding.Relation
	if rel.UniqueKey == "" || rel.From.IsRaw || rel.Correlate != "" {
		return nil
	}
	at := -1
	for i, s := range steps {
		if s.S == "BUCKET" {
			at = i
			break
		}
	}
	if at < 0 {
		return nil
	}
	revision := rel.UniqueKey
	ba := steps[at].Items
	var partition *string
	if len(ba) == 2 || len(ba) == 3 {
		partition = latestFieldName(ba[1])
	}
	if partition == nil {
		return nil
	}
	var body *sel.Node
	if len(ba) == 3 {
		body = ba[2]
	}
	if body == nil && at+1 < len(steps) && steps[at+1].S == "MAP" && len(steps[at+1].Items) == 2 {
		body = steps[at+1].Items[1]
	}
	pf := rel.Field(*partition)
	rf := rel.Field(revision)
	if pf == nil || rf == nil || body == nil || body.T != sel.NodeCall || body.S != "RECORD" || len(body.Items) != 4 ||
		(pf.Type != KindNum && pf.Type != KindText) || rf.Type != KindNum ||
		pf.Column != *partition || rf.Column != revision || pf.IsRaw || rf.IsRaw || rf.Guard {
		return nil
	}
	ra := body.Items
	if ra[0].T != sel.NodeText || ra[2].T != sel.NodeText || ra[0].S == ra[2].S {
		return nil
	}
	var top *sel.Node
	hasKey := false
	for _, v := range []*sel.Node{ra[1], ra[3]} {
		if v.T == sel.NodeCall && v.S == "TOP_BY" {
			top = v
		}
		if v.T == sel.NodeVar && v.S == "_K" {
			hasKey = true
		}
	}
	if top == nil || !hasKey {
		return nil
	}
	ta := top.Items
	if len(ta) != 4 || ta[0].T != sel.NodeVar || ta[0].S != "_" {
		return nil
	}
	lfn := latestFieldName(ta[1])
	if lfn == nil || *lfn != revision || ta[2].T != sel.NodeText || ta[2].S != "DESC" || ta[3].T != sel.NodeNum || ta[3].S != "1" {
		return nil
	}
	for i := 0; i < at; i++ {
		s := steps[i]
		if s.S == "FILTER" {
			continue
		}
		if s.S != "SORT_BY" || (len(s.Items) != 2 && len(s.Items) != 3) {
			return nil
		}
		slfn := latestFieldName(s.Items[1])
		if slfn == nil || *slfn != revision {
			return nil
		}
		if len(s.Items) == 3 && (s.Items[2].T != sel.NodeText || s.Items[2].S != "ASC") {
			return nil
		}
	}

	inputSteps := make([]*sel.Node, at)
	copy(inputSteps, steps[:at])
	if len(inputSteps) == 0 {
		truth := sel.NewNode(sel.NodeBool, source.Pos)
		truth.B = true
		dummy := sel.NewNode(sel.NodeCall, source.Pos)
		dummy.S = "FILTER"
		dummy.Items = []*sel.Node{source, truth}
		inputSteps = []*sel.Node{dummy}
	}
	prefix := helpers.wrap(sel.BuildPipeline(source, inputSteps))
	prefixProg := sel.NewProgram("", prefix)
	sql := TryTranslateStatement(prefixProg, dialect, catalog, opts)
	if sql == nil {
		return nil
	}

	emit := NewEmit(dialect)
	input := "_sel_input"
	groups := "_sel_latest"
	fromTable := physicalSource(binding)
	for utf8.AsciiUpper(input) == utf8.AsciiUpper(fromTable) {
		input += "_"
	}
	for utf8.AsciiUpper(groups) == utf8.AsciiUpper(fromTable) || utf8.AsciiUpper(groups) == utf8.AsciiUpper(input) {
		groups += "_"
	}
	qi := emit.Ident(input)
	qg := emit.Ident(groups)
	qr := emit.Ident(revision)
	qmax := emit.Ident("_sel_revision")
	qfirst := emit.Ident("_sel_first")
	keyFrag := emit.TextOperand(NewFragment([]Part{{false, emit.Ident(*partition), 0}}, pf.Type, dialect, nil, nil, nil))
	keyStr := keyFrag.AsValue(ModeInline)

	parts := []Part{{false, "WITH " + qi + " AS (", 0}}
	parts = append(parts, sql.Parts...)
	parts = append(parts, Part{false, "), " + qg + " AS (SELECT MAX(" + qr + ") AS " + qmax + ", MIN(" + qr + ") AS " + qfirst + " FROM " + qi + " GROUP BY " + keyStr + ") SELECT " + qi + ".* FROM " + qi + " JOIN " + qg + " ON " + qi + "." + qr + " = " + qg + "." + qmax + " ORDER BY " + qg + "." + qfirst + " ASC", 0})

	remaining := steps[at:]
	continuation := helpers.wrap(sel.BuildPipeline(varNode("_INPUT", steps[at].Pos), remaining))
	continuationProg := sel.NewProgram("", continuation)

	return &HybridPlan{
		Dialect:               dialect,
		IsHybrid:              true,
		SqlStatement:          NewFragment(parts, KindStatement, dialect, sql.Params, sql.ParamKinds, sql.Caveats),
		SqlPrefixAst:          prefix,
		ContinuationAst:       continuation,
		ContinuationProgram:   continuationProg,
		ContinuationSourceVar: "_INPUT",
		SourceTables:          []string{fromTable},
		SelectedMember:        &SelectedMember{PartitionKey: *partition, RevisionKey: revision},
	}
}

func collectFieldReferences(node *sel.Node, binder string, out *[]string) {
	if node == nil {
		return
	}
	if node.T == sel.NodeIndex && node.L != nil && node.L.T == sel.NodeVar && node.R != nil && node.R.T == sel.NodeText {
		object := utf8.AsciiUpper(node.L.S)
		if binder == "" || object == utf8.AsciiUpper(binder) || object == "_" || object == "_1" || object == "_2" {
			key := node.R.S
			if !containsString(*out, key) {
				*out = append(*out, key)
			}
		}
	}
	if node.L != nil {
		collectFieldReferences(node.L, binder, out)
	}
	if node.R != nil {
		collectFieldReferences(node.R, binder, out)
	}
	for _, item := range node.Items {
		collectFieldReferences(item, binder, out)
	}
}

var fallthroughDownstreamOps = map[string]bool{
	"SORT_BY": true, "TOP_BY": true, "TAKE": true, "DROP": true,
}

func readsWholeRow(node *sel.Node, binder string) bool {
	if node == nil {
		return false
	}
	if node.T == sel.NodeVar {
		name := utf8.AsciiUpper(node.S)
		return name == utf8.AsciiUpper(binder) || name == "_" || name == "_1" || name == "_2"
	}
	if node.T == sel.NodeIndex && node.L != nil && node.L.T == sel.NodeVar && node.R != nil && node.R.T == sel.NodeText {
		return readsWholeRow(node.R, binder)
	}
	if readsWholeRow(node.L, binder) || readsWholeRow(node.R, binder) {
		return true
	}
	for _, item := range node.Items {
		if readsWholeRow(item, binder) {
			return true
		}
	}
	return false
}

func isOwnFieldRead(key *sel.Node, val *sel.Node, binder string) bool {
	return val != nil && val.T == sel.NodeIndex && val.L != nil && val.L.T == sel.NodeVar && val.R != nil &&
		val.R.T == sel.NodeText && utf8.AsciiUpper(val.L.S) == utf8.AsciiUpper(binder) && val.R.S == key.S
}

type mapRecordDetails struct {
	explicit bool
	binder   string
	body     *sel.Node
	pairs    []Pair[*sel.Node, *sel.Node]
}

func getMapRecordDetails(step *sel.Node) *mapRecordDetails {
	if step == nil || step.T != sel.NodeCall || step.S != "MAP" {
		return nil
	}
	args := step.Items
	explicit := len(args) == 3 && args[1].T == sel.NodeVar && !args[1].Grouped
	binder := "_"
	if explicit {
		binder = args[1].S
	}
	var body *sel.Node
	if explicit {
		body = args[2]
	} else if len(args) == 2 {
		body = args[1]
	} else {
		return nil
	}
	if body == nil || body.T != sel.NodeCall || body.S != "RECORD" || len(body.Items)%2 != 0 {
		return nil
	}
	seen := make(map[string]bool)
	var pairs []Pair[*sel.Node, *sel.Node]
	for i := 0; i < len(body.Items); i += 2 {
		k := body.Items[i]
		if k.T != sel.NodeText || seen[k.S] {
			return nil
		}
		seen[k.S] = true
		pairs = append(pairs, Pair[*sel.Node, *sel.Node]{Key: k, Val: body.Items[i+1]})
	}
	return &mapRecordDetails{explicit: explicit, binder: binder, body: body, pairs: pairs}
}

func tryPlanFallthrough(source *sel.Node, steps []*sel.Node, dialect string, catalog *Bindings, options Options, helpers helpersContext) *HybridPlan {
	mapIndex := -1
	for i, step := range steps {
		if step.S == "MAP" {
			mapIndex = i
			break
		}
	}
	if mapIndex < 0 || bucketRowsAreKeys(steps, mapIndex) {
		return nil
	}
	mapStep := steps[mapIndex]
	details := getMapRecordDetails(mapStep)
	if details == nil {
		return nil
	}

	var pushable []Pair[*sel.Node, *sel.Node]
	var custom []Pair[*sel.Node, *sel.Node]
	for _, pair := range details.pairs {
		if containsUnsupportedSql(pair.Val, dialect, helpers.defs, make(map[string]bool)) {
			custom = append(custom, pair)
		} else {
			pushable = append(pushable, pair)
		}
	}
	if len(pushable) == 0 || len(custom) == 0 {
		return nil
	}
	for _, pair := range custom {
		if readsWholeRow(pair.Val, details.binder) {
			return nil
		}
	}

	var projected []string
	for _, pair := range pushable {
		projected = append(projected, pair.Key.S)
	}

	for i := mapIndex + 1; i < len(steps); i++ {
		if !fallthroughDownstreamOps[steps[i].S] {
			return nil
		}
		var refs []string
		for a := 1; a < len(steps[i].Items); a++ {
			collectFieldReferences(steps[i].Items[a], "", &refs)
		}
		for _, ref := range refs {
			if !containsString(projected, ref) {
				return nil
			}
		}
	}

	var own []string
	for _, pair := range pushable {
		if isOwnFieldRead(pair.Key, pair.Val, details.binder) {
			own = append(own, pair.Key.S)
		}
	}

	var dependencies []string
	var dependenciesFolded []string
	for _, pair := range custom {
		var refs []string
		collectFieldReferences(pair.Val, details.binder, &refs)
		for _, ref := range refs {
			if containsString(projected, ref) {
				if !containsString(own, ref) {
					return nil
				}
			} else {
				refUpper := utf8.AsciiUpper(ref)
				for _, proj := range projected {
					if utf8.AsciiUpper(proj) == refUpper {
						return nil
					}
				}
				if !containsString(dependencies, ref) {
					if containsString(dependenciesFolded, refUpper) {
						return nil
					}
					dependenciesFolded = append(dependenciesFolded, refUpper)
					dependencies = append(dependencies, ref)
				}
			}
		}
	}

	rewrittenRecord := copyAstNode(details.body)
	rewrittenRecord.Items = nil
	for _, pair := range pushable {
		rewrittenRecord.Items = append(rewrittenRecord.Items, pair.Key, pair.Val)
	}
	for _, dep := range dependencies {
		k := textNode(dep, mapStep.Pos)
		rewrittenRecord.Items = append(rewrittenRecord.Items, k, indexNode(varNode(details.binder, mapStep.Pos), k, mapStep.Pos))
	}

	rewrittenMap := copyAstNode(mapStep)
	rewrittenMap.Items = []*sel.Node{mapStep.Items[0]}
	if details.explicit {
		rewrittenMap.Items = append(rewrittenMap.Items, mapStep.Items[1])
	}
	rewrittenMap.Items = append(rewrittenMap.Items, rewrittenRecord)

	rewrittenSteps := make([]*sel.Node, 0, len(steps))
	rewrittenSteps = append(rewrittenSteps, steps[:mapIndex]...)
	rewrittenSteps = append(rewrittenSteps, rewrittenMap)
	rewrittenSteps = append(rewrittenSteps, steps[mapIndex+1:]...)

	rewrittenAst := helpers.wrap(sel.BuildPipeline(source, rewrittenSteps))
	rewrittenProg := sel.NewProgram("", rewrittenAst)
	sql := TryTranslateStatement(rewrittenProg, dialect, catalog, options)
	if sql == nil {
		return nil
	}

	continuationRecord := copyAstNode(details.body)
	continuationRecord.Items = nil
	for _, pair := range details.pairs {
		continuationRecord.Items = append(continuationRecord.Items, pair.Key)
		isPushable := false
		for _, p := range pushable {
			if p.Key == pair.Key {
				isPushable = true
				break
			}
		}
		if isPushable {
			continuationRecord.Items = append(continuationRecord.Items, indexNode(varNode(details.binder, pair.Val.Pos), pair.Key, pair.Val.Pos))
		} else {
			continuationRecord.Items = append(continuationRecord.Items, pair.Val)
		}
	}

	continuationMap := copyAstNode(mapStep)
	continuationMap.Items = []*sel.Node{varNode("_INPUT", mapStep.Pos)}
	if details.explicit {
		continuationMap.Items = append(continuationMap.Items, mapStep.Items[1])
	}
	continuationMap.Items = append(continuationMap.Items, continuationRecord)

	continuationAst := helpers.wrap(continuationMap)
	continuationProg := sel.NewProgram("", continuationAst)

	return &HybridPlan{
		Dialect:               dialect,
		IsHybrid:              true,
		SqlStatement:          sql,
		SqlPrefixAst:          rewrittenAst,
		ContinuationAst:       continuationAst,
		ContinuationProgram:   continuationProg,
		ContinuationSourceVar: "_INPUT",
		SourceTables:          helpers.tables(rewrittenAst),
	}
}

// PlanHybrid splits a relational pipeline at the longest SQL-translatable prefix.
func PlanHybrid(program *sel.Program, dialect string, bindings *Bindings, options Options) *HybridPlan {
	RequireTarget(dialect, sel.Pos{})
	checked := bindings
	if checked == nil {
		checked = NewBindings(nil)
	}
	checked.CheckAliases(sel.Pos{})

	constNames, constRoot := Scope(checked)
	identityBarrier := false
	var earlyPureMemory bool
	func() {
		defer func() {
			if r := recover(); r != nil {
				if _, ok := r.(*SqlError); ok {
					earlyPureMemory = true
				} else if _, ok := r.(SqlError); ok {
					earlyPureMemory = true
				} else {
					panic(r)
				}
			}
		}()
		normalized := Normalise(program.AST(), constNames, constRoot)
		identityBarrier = IdentityLossBeforeGrouping(normalized, nil)
	}()
	if earlyPureMemory {
		return pureMemoryPlan(program, dialect, checked)
	}

	partsLeading, partsResult := statements(program.AST())
	literals := literalHelpers(partsLeading)
	defs := definitions(partsLeading)

	isRelation := func(node *sel.Node) bool {
		return node != nil && node.T == sel.NodeVar && checked.Has(node.S) && checked.Get(node.S, node.Pos).Kind == BindingKindRelation
	}

	unwoundSource, unwoundSteps := unwindThroughHelpers(partsResult, defs, literals)
	if len(unwoundSteps) == 0 || !isRelation(unwoundSource) {
		return pureMemoryPlan(program, dialect, checked)
	}

	optimized := sel.OptimizeAstLogical(sel.BuildPipeline(unwoundSource, unwoundSteps))
	source, steps := sel.UnwindPipeline(optimized)
	if len(steps) == 0 || !isRelation(source) {
		return pureMemoryPlan(program, dialect, checked)
	}

	helpers := helpersContext{
		leading:   partsLeading,
		defs:      defs,
		bindings:  checked,
		names:     constNames,
		constRoot: constRoot,
	}

	fullAst := helpers.wrap(sel.BuildPipeline(source, steps))
	fullProg := sel.NewProgram("", fullAst)
	var fullSql *Fragment
	if !identityBarrier && !rowsAreNotTheValue(steps, len(steps)) {
		fullSql = TryTranslateStatement(fullProg, dialect, checked, options)
	}
	if fullSql != nil {
		return &HybridPlan{
			Dialect:               dialect,
			SqlStatement:          fullSql,
			SqlPrefixAst:          fullAst,
			PureSql:               true,
			ContinuationSourceVar: "_INPUT",
			SourceTables:          helpers.tables(fullAst),
		}
	}

	if latest := tryLatestMember(source, steps, dialect, checked, options, helpers); latest != nil {
		return latest
	}

	if !identityBarrier {
		if ft := tryPlanFallthrough(source, steps, dialect, checked, options, helpers); ft != nil {
			return ft
		}
	}

	for count := len(steps) - 1; count >= 1; count-- {
		if rowsAreNotTheValue(steps, count) {
			continue
		}
		prefixAst := helpers.wrap(sel.BuildPipeline(source, steps[:count]))
		if identityBarrier {
			skip := false
			func() {
				defer func() {
					if r := recover(); r != nil {
						skip = true
					}
				}()
				norm := Normalise(prefixAst, constNames, constRoot)
				if IdentityLossBeforeGrouping(norm, &NeededFields{All: true}) {
					skip = true
				}
			}()
			if skip {
				continue
			}
		}
		prefixProg := sel.NewProgram("", prefixAst)
		sql := TryTranslateStatement(prefixProg, dialect, checked, options)
		if sql == nil {
			continue
		}

		remaining := steps[count:]
		continuationAst := helpers.wrap(sel.BuildPipeline(varNode("_INPUT", remaining[0].Pos), remaining))
		continuationProg := sel.NewProgram("", continuationAst)

		return &HybridPlan{
			Dialect:               dialect,
			SqlStatement:          sql,
			SqlPrefixAst:          prefixAst,
			ContinuationAst:       continuationAst,
			ContinuationProgram:   continuationProg,
			ContinuationSourceVar: "_INPUT",
			IsHybrid:              true,
			SourceTables:          helpers.tables(prefixAst),
		}
	}

	return pureMemoryPlan(program, dialect, checked)
}

// ExecuteHybrid runs a hybrid plan, evaluating SQL prefixes through dbRunner and remaining steps in memory.
func ExecuteHybrid(plan *HybridPlan, dbRunner DbRunner, context *sel.Value) (*sel.Value, error) {
	if plan.PureMemory {
		if plan.ContinuationProgram == nil {
			return nil, fmt.Errorf("pure-memory hybrid plan has no continuation program")
		}
		return plan.ContinuationProgram.Run(context)
	}
	if plan.SqlStatement == nil {
		return nil, fmt.Errorf("SQL hybrid plan has no SQL statement")
	}
	rows, err := dbRunner(plan.SqlStatement.AsStatement(ModeParams), plan.SqlStatement.Bindings())
	if err != nil {
		return nil, err
	}
	if plan.PureSql {
		return rows, nil
	}
	if plan.ContinuationProgram == nil {
		return nil, fmt.Errorf("hybrid plan has no continuation program")
	}
	var continuationContext *sel.Value
	if context == nil || context.IsNone() {
		continuationContext = sel.NewRecordFromEntries(nil)
	} else {
		continuationContext = context.Clone()
	}
	continuationContext.Set(plan.ContinuationSourceVar, rows)
	return plan.ContinuationProgram.Run(continuationContext)
}
