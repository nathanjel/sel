// Aggregate and relational built-in functions: ALL, ANY, MAP, FILTER, SUM, JOIN, SORT, TOP, BUCKET, LINK, LINK_LEFT.

package sel

import (
	"bytes"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
)

func nodeContainsVar(node *Node, name string) bool {
	if node == nil {
		return false
	}
	switch node.T {
	case NodeVar:
		return utf8.AsciiUpper(node.S) == utf8.AsciiUpper(name)
	case NodeIndex:
		return nodeContainsVar(node.L, name) || nodeContainsVar(node.R, name)
	case NodeCall:
		for _, a := range node.Items {
			if nodeContainsVar(a, name) {
				return true
			}
		}
	case NodeBin:
		return nodeContainsVar(node.L, name) || nodeContainsVar(node.R, name)
	case NodeUn:
		return nodeContainsVar(node.L, name)
	case NodeAssign:
		return nodeContainsVar(node.L, name) || nodeContainsVar(node.R, name)
	case NodeSeq, NodeList:
		for _, item := range node.Items {
			if nodeContainsVar(item, name) {
				return true
			}
		}
	}
	return false
}

func aggregateShape(args *Args) (string, *Node) {
	if args.Count() == 3 {
		return args.Symbol(1), args.Node(2)
	}
	return "_", args.Node(1)
}

type visitFunc func(r *Value, key string, item *Value, body *Node) *Value

func aggregateWalk(args *Args, ctx *Context, visit visitFunc, bodyOverride *Node) *Value {
	binder, body := aggregateShape(args)
	if bodyOverride != nil {
		body = bodyOverride
	}
	coll := args.Val(0)
	frame := map[string]*Value{binder: nil}
	if nodeContainsVar(body, "_K") {
		frame["_K"] = nil
	}
	ctx.PushFrame(frame)
	defer ctx.PopFrame()

	for _, entry := range coll.Elements() {
		frame[binder] = entry.Val
		if _, ok := frame["_K"]; ok {
			frame["_K"] = NewText(entry.Key)
		}
		res := visit(args.EvalNode(body), entry.Key, entry.Val, body)
		if res != nil {
			return res
		}
	}
	return nil
}

// sortRank is the kind rank of the total order (spec §7.3): NULL < BOOL <
// numeric-looking text and numbers < other TEXT < BIN < lists and records.
func sortRank(v *Value) int {
	if v.IsNull() {
		return 0
	}
	switch v.Kind {
	case KindBool:
		return 1
	case KindBin:
		return 4
	case KindText:
		if v.LooksNumeric() {
			return 2
		}
		return 3
	}
	return 5
}

// compareValues is the total order every sort, TOP and bucket key uses. Values
// of one rank compare within it (numbers by exact decimal value, text and BIN
// bytewise, FALSE before TRUE); values of different ranks compare by rank.
// Equal values tie, and the caller keeps input order for a tie.
func compareValues(a, b *Value) int {
	ra, rb := sortRank(a), sortRank(b)
	if ra != rb {
		if ra < rb {
			return -1
		}
		return 1
	}
	switch ra {
	case 1:
		av, bv := 0, 0
		if a.boolVal {
			av = 1
		}
		if b.boolVal {
			bv = 1
		}
		return av - bv
	case 2:
		return decimal.Cmp(a.AsDecimal(Pos{}), b.AsDecimal(Pos{}))
	case 3, 4:
		return bytes.Compare(a.AsBytes(Pos{}), b.AsBytes(Pos{}))
	}
	return 0
}

type sortItem struct {
	item *Value
	key  *Value
	idx  int
}

func doSort(args *Args, ctx *Context, forcedDir string) *Value {
	val := args.Val(0)

	count := args.Count()
	direction := forcedDir
	if direction == "" {
		direction = "ASC"
	}

	// The form, the binder and the direction are arguments like any other: they
	// are evaluated and checked whether or not the list has anything in it
	// (spec §7.4), so an empty list cannot hide a bad direction.
	binder := "_"
	var body *Node
	if count == 2 {
		body = args.Node(1)
	} else if count == 3 {
		if forcedDir != "" {
			binder = args.Symbol(1)
			body = args.Node(2)
		} else if args.Node(2).T == NodeText {
			body = args.Node(1)
			direction = utf8.AsciiUpper(args.Text(2))
		} else if args.IsSymbol(1) {
			binder = args.Symbol(1)
			body = args.Node(2)
			direction = "ASC"
		} else {
			body = args.Node(1)
			direction = utf8.AsciiUpper(args.Text(2))
		}
	} else if count == 4 {
		binder = args.Symbol(1)
		body = args.Node(2)
		direction = utf8.AsciiUpper(args.Text(3))
	}
	if count > 1 && direction != "ASC" && direction != "DESC" {
		posIdx := 2
		if count == 4 {
			posIdx = 3
		}
		fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args.PosOf(posIdx))
	}

	if val.IsNull() {
		return NewListOwned(nil)
	}
	ents := val.Elements()
	if len(ents) == 0 {
		return NewListOwned(nil)
	}

	var indexed []sortItem
	if count == 1 {
		indexed = make([]sortItem, len(ents))
		for i, e := range ents {
			indexed[i] = sortItem{item: e.Val, key: e.Val, idx: i}
		}
	} else {
		needsK := nodeContainsVar(body, "_K")
		frame := map[string]*Value{binder: nil}
		if needsK {
			frame["_K"] = nil
		}
		indexed = make([]sortItem, len(ents))
		for i, e := range ents {
			frame[binder] = e.Val
			if needsK {
				frame["_K"] = NewText(e.Key)
			}
			ctx.PushFrame(frame)
			evalKey := args.EvalNode(body)
			ctx.PopFrame()
			indexed[i] = sortItem{item: e.Val, key: evalKey, idx: i}
		}
	}

	sort.SliceStable(indexed, func(i, j int) bool {
		c := compareValues(indexed[i].key, indexed[j].key)
		if direction == "DESC" {
			c = -c
		}
		if c != 0 {
			return c < 0
		}
		return indexed[i].idx < indexed[j].idx
	})

	out := make([]*Value, len(indexed))
	for i, x := range indexed {
		// SPEC §3.4: SORT copies what it collects.
		out[i] = x.item.CloneAt(2, args.Pos())
	}
	return NewListOwned(out)
}

func doTop(args *Args, ctx *Context, forcedDir string) *Value {
	val := args.Val(0)
	limit := int(args.NonNegInt(args.Count() - 1))

	sortCount := args.Count() - 1
	binder := "_"
	var body *Node
	direction := forcedDir
	if direction == "" {
		direction = "ASC"
	}

	if sortCount == 1 {
		binder = ""
	} else if sortCount == 2 {
		body = args.Node(1)
	} else if sortCount == 3 {
		if forcedDir != "" {
			binder = args.Symbol(1)
			body = args.Node(2)
		} else if args.Node(2).T == NodeText {
			body = args.Node(1)
			direction = utf8.AsciiUpper(args.Text(2))
		} else if args.IsSymbol(1) {
			binder = args.Symbol(1)
			body = args.Node(2)
		} else {
			body = args.Node(1)
			direction = utf8.AsciiUpper(args.Text(2))
		}
	} else if sortCount == 4 {
		binder = args.Symbol(1)
		body = args.Node(2)
		direction = utf8.AsciiUpper(args.Text(3))
	} else {
		fail("E_ARITY", fmt.Sprintf("%s has an invalid sort form", args.Name()), args.Pos())
	}

	if direction != "ASC" && direction != "DESC" {
		dirIdx := 2
		if sortCount == 4 {
			dirIdx = 3
		}
		fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args.PosOf(dirIdx))
	}

	// TOP is TAKE(SORT(...), n): the direction is checked and the keys evaluated
	// whatever n is, and only an empty or NULL source has nothing to sort.
	if val.IsNull() {
		return NewListOwned(nil)
	}
	ents := val.Elements()
	if len(ents) == 0 {
		return NewListOwned(nil)
	}
	needsK := body != nil && nodeContainsVar(body, "_K")
	frame := map[string]*Value{}
	if binder != "" {
		frame[binder] = nil
	}
	if needsK {
		frame["_K"] = nil
	}

	items := make([]sortItem, len(ents))
	for i, e := range ents {
		var kVal *Value
		if binder == "" {
			kVal = e.Val
		} else {
			frame[binder] = e.Val
			if needsK {
				frame["_K"] = NewText(e.Key)
			}
			ctx.PushFrame(frame)
			kVal = args.EvalNode(body)
			ctx.PopFrame()
		}
		items[i] = sortItem{item: e.Val, key: kVal, idx: i}
	}

	sort.SliceStable(items, func(i, j int) bool {
		c := compareValues(items[i].key, items[j].key)
		if direction == "DESC" {
			c = -c
		}
		if c != 0 {
			return c < 0
		}
		return items[i].idx < items[j].idx
	})

	if limit > len(items) {
		limit = len(items)
	}
	out := make([]*Value, limit)
	for i := 0; i < limit; i++ {
		// SPEC §3.4: TOP follows SORT and copies what it collects.
		out[i] = items[i].item.CloneAt(2, args.Pos())
	}
	return NewListOwned(out)
}

func bucketKeyText(key *Value, pos Pos) string {
	if key.Kind == KindNone {
		if key.IsNull() {
			fail("E_NULL", "value is NULL", pos)
		}
		fail("E_NOT_TEXT", "a bucket key must be text or a number, got a list or record", pos)
	}
	return key.AsText(pos)
}

type bucketGroup struct {
	key    *Value
	keyStr string
	rows   []*Value
}

func doBucket(args *Args, ctx *Context) *Value {
	val := args.Val(0)
	if val.IsNull() {
		return NewListOwned(nil)
	}
	// A scalar is a one-element list (spec §7.3), so emptiness is asked of the
	// elements and not of the children.
	ents := val.Elements()
	if len(ents) == 0 {
		return NewListOwned(nil)
	}

	count := args.Count()
	binder := "_"
	var keyNode *Node
	var aggNode *Node

	if count == 2 {
		keyNode = args.Node(1)
	} else if count == 3 {
		keyNode = args.Node(1)
		aggNode = args.Node(2)
	} else {
		binder = args.Symbol(1)
		keyNode = args.Node(2)
		aggNode = args.Node(3)
	}

	needsK := nodeContainsVar(keyNode, "_K")
	frame := map[string]*Value{binder: nil}
	if needsK {
		frame["_K"] = nil
	}

	table := make(map[uint64][]*bucketGroup)
	byText := make(map[string]*bucketGroup)
	var groups []*bucketGroup

	ctx.PushFrame(frame)
	for idx, e := range ents {
		frame[binder] = e.Val
		if needsK {
			frame["_K"] = NewText(e.Key)
		}
		groupKey := args.EvalNode(keyNode)
		keyStr := ""
		if aggNode == nil {
			keyStr = bucketKeyText(groupKey, keyNode.Pos)
		}
		if aggNode == nil {
			// The two-argument form keys a RECORD, and a record key is text: two
			// keys with the same text are one group whatever their structure
			// (spec §7.3). Grouping them by identity, as the three-argument form
			// does, made the second group overwrite the first in the result.
			if g, ok := byText[keyStr]; ok {
				g.rows = append(g.rows, e.Val)
			} else {
				g := &bucketGroup{key: groupKey, keyStr: keyStr, rows: []*Value{e.Val}}
				byText[keyStr] = g
				groups = append(groups, g)
			}
			continue
		}
		h := groupKey.StructuralHash()
		bucket := table[h]
		found := false
		for _, g := range bucket {
			if g.key.Eql(groupKey, Pos{}) {
				g.rows = append(g.rows, e.Val)
				found = true
				break
			}
		}
		if !found {
			g := &bucketGroup{key: groupKey, keyStr: keyStr, rows: []*Value{e.Val}}
			table[h] = append(table[h], g)
			groups = append(groups, g)
		}
		_ = idx
	}
	ctx.PopFrame()

	if aggNode == nil {
		out := NewNone()
		for _, g := range groups {
			rowsCopy := make([]*Value, len(g.rows))
			for i, r := range g.rows {
				// The rows sit in a list inside the result record: level 3.
				rowsCopy[i] = r.CloneAt(3, args.Pos())
			}
			out.Set(g.keyStr, NewListOwned(rowsCopy))
		}
		return out
	}

	out := make([]*Value, len(groups))
	aggFrame := map[string]*Value{binder: nil, "_K": nil}
	ctx.PushFrame(aggFrame)
	defer ctx.PopFrame()

	for i, g := range groups {
		aggFrame[binder] = NewListOwned(g.rows)
		aggFrame["_K"] = g.key
		out[i] = args.EvalNode(aggNode).CloneAt(2, args.Pos())
	}
	return NewListOwned(out)
}

func isPositionalBinder(name string) bool {
	return name == "_1" || name == "_2"
}

func singleRelationName(node *Node) string {
	if node == nil {
		return ""
	}
	if node.T == NodeVar {
		return node.S
	}
	if node.T == NodeCall && len(node.Items) > 0 && node.S != "LINK" && node.S != "LINK_LEFT" {
		return singleRelationName(node.Items[0])
	}
	return ""
}

func ensureRowTableAlias(row *Value, tableName string) *Value {
	if tableName == "" || isPositionalBinder(tableName) || row.Has(tableName) {
		return row
	}
	lower := strings.ToLower(tableName)
	if row.shape != nil {
		oldShape := row.shape
		addLower := lower != tableName && !row.Has(lower)
		keys := make([]string, len(oldShape.Keys)+1)
		copy(keys, oldShape.Keys)
		keys[len(oldShape.Keys)] = tableName
		if addLower {
			keys = append(keys, lower)
		}
		targetShape := UniqueRecordShape(keys)
		if targetShape != nil {
			storage := make([]*Value, len(keys))
			copy(storage, row.storage)
			storage[len(oldShape.Keys)] = row
			if addLower {
				storage[len(oldShape.Keys)+1] = row
			}
			return NewShapedRecord(targetShape, storage)
		}
	}
	entries := make([]Entry, len(row.Entries()))
	copy(entries, row.Entries())
	entries = append(entries, Entry{Key: tableName, Val: row})
	if lower != tableName && !row.Has(lower) {
		entries = append(entries, Entry{Key: lower, Val: row})
	}
	return NewRecordFromEntries(entries)
}

func makeNullRecord(sample *Value, tableName string) *Value {
	if sample != nil && !sample.IsNull() {
		var entries []Entry
		for _, k := range sample.Keys() {
			entries = append(entries, Entry{Key: k, Val: NewNull()})
		}
		return NewRecordFromEntries(entries)
	}
	var entries []Entry
	if tableName != "" && !isPositionalBinder(tableName) {
		entries = append(entries, Entry{Key: tableName, Val: NewNull()})
		lower := strings.ToLower(tableName)
		if lower != tableName {
			entries = append(entries, Entry{Key: lower, Val: NewNull()})
		}
	}
	return NewRecordFromEntries(entries)
}

const (
	joinScalar = 0
	joinNull   = 1
	joinNested = 2
)

func joinCategory(v *Value) int {
	if v.Kind != KindNone || v.isList {
		return joinScalar
	}
	if v.Size() > 0 {
		return joinNested
	}
	return joinNull
}

func binderKeys(name, positional string) []string {
	keys := []string{name}
	lower := strings.ToLower(name)
	if lower != name {
		keys = append(keys, lower)
	}
	hasPos := false
	for _, k := range keys {
		if k == positional {
			hasPos = true
			break
		}
	}
	if !hasPos {
		keys = append(keys, positional)
	}
	return keys
}

func makeJoinedRow(left, right *Value, b1, b2 string, nullRight *Value) *Value {
	var entries []Entry
	slot := make(map[string]int)

	put := func(key string, val *Value) {
		if _, ok := slot[key]; ok {
			return
		}
		slot[key] = len(entries)
		entries = append(entries, Entry{Key: key, Val: val})
	}

	bind := func(key string, val *Value) {
		if idx, ok := slot[key]; ok {
			entries[idx] = Entry{Key: key, Val: val}
		} else {
			put(key, val)
		}
	}

	leftEntries := left.Entries()
	for _, e := range leftEntries {
		if joinCategory(e.Val) == joinNested {
			put(e.Key, e.Val)
		}
	}

	for _, name := range binderKeys(b1, "_1") {
		bind(name, left)
	}

	rside := right
	if rside == nil {
		if nullRight != nil {
			rside = nullRight
		} else {
			rside = NewNone()
		}
	}

	for _, name := range binderKeys(b2, "_2") {
		bind(name, rside)
	}

	var rightEntries []Entry
	if rside.Size() > 0 && !rside.isList {
		rightEntries = rside.Entries()
	}
	rightNames := make(map[string]bool, len(rightEntries))
	for _, e := range rightEntries {
		rightNames[utf8.AsciiUpper(e.Key)] = true
	}

	for _, e := range leftEntries {
		if joinCategory(e.Val) != joinNested && !rightNames[utf8.AsciiUpper(e.Key)] {
			put(e.Key, e.Val)
		}
	}

	if right != nil {
		leftNames := make(map[string]bool, len(leftEntries))
		for _, e := range leftEntries {
			leftNames[utf8.AsciiUpper(e.Key)] = true
		}
		for _, e := range rightEntries {
			if joinCategory(e.Val) == joinScalar && !leftNames[utf8.AsciiUpper(e.Key)] {
				put(e.Key, e.Val)
			}
		}
	}

	return NewRecordFromEntries(entries)
}

type joinEqui struct {
	leftExpr  *Node
	rightExpr *Node
	numeric   bool
	swapped   bool
}

func exprDependsOnlyOn(node *Node, allowed map[string]bool) bool {
	if node == nil {
		return true
	}
	switch node.T {
	case NodeVar:
		return allowed[utf8.AsciiUpper(node.S)]
	case NodeIndex:
		return exprDependsOnlyOn(node.L, allowed) && exprDependsOnlyOn(node.R, allowed)
	case NodeCall:
		for _, a := range node.Items {
			if !exprDependsOnlyOn(a, allowed) {
				return false
			}
		}
		return true
	case NodeBin:
		return exprDependsOnlyOn(node.L, allowed) && exprDependsOnlyOn(node.R, allowed)
	case NodeUn:
		return exprDependsOnlyOn(node.L, allowed)
	case NodeAssign:
		return exprDependsOnlyOn(node.L, allowed) && exprDependsOnlyOn(node.R, allowed)
	case NodeSeq, NodeList:
		for _, item := range node.Items {
			if !exprDependsOnlyOn(item, allowed) {
				return false
			}
		}
		return true
	}
	return true
}

func extractJoinEqui(node *Node, b1, b2 string) *joinEqui {
	if node == nil || node.T != NodeBin || (node.S != "==" && node.S != "$==") {
		return nil
	}
	// One name for both sides: the right binder shadows the left (spec §7.4), so
	// every read of it is the right row and there is no left-only key to extract.
	// The general path answers that; the fast path must not.
	if utf8.AsciiUpper(b1) == utf8.AsciiUpper(b2) {
		return nil
	}
	leftNames := map[string]bool{
		utf8.AsciiUpper(b1): true,
		"_1":                true,
		"_":                 true,
	}
	rightNames := map[string]bool{
		utf8.AsciiUpper(b2): true,
		"_2":                true,
	}
	if exprDependsOnlyOn(node.L, leftNames) && exprDependsOnlyOn(node.R, rightNames) {
		return &joinEqui{leftExpr: node.L, rightExpr: node.R, numeric: node.S == "==", swapped: false}
	}
	if exprDependsOnlyOn(node.R, leftNames) && exprDependsOnlyOn(node.L, rightNames) {
		return &joinEqui{leftExpr: node.R, rightExpr: node.L, numeric: node.S == "==", swapped: true}
	}
	return nil
}

type joinKeyType int

const (
	keyTypeNull joinKeyType = iota
	keyTypeBad
	keyTypeInt64
	keyTypeDec
	keyTypeStr
)

type joinKey struct {
	kType  joinKeyType
	intVal int64
	strVal string
	scale  int32
	isBad  bool
	isNull bool
	badVal *Value
}

func canonicalJoinKey(v *Value, numeric bool) joinKey {
	if v == nil || v.IsNull() {
		return joinKey{isNull: true, kType: keyTypeNull}
	}
	if numeric {
		var d *decimal.Dec
		func() {
			defer func() {
				if r := recover(); r != nil {
					if !isSelPanic(r) {
						panic(r)
					}
					d = nil
				}
			}()
			d = v.AsDecimal(Pos{})
		}()
		if d == nil {
			return joinKey{isBad: true, kType: keyTypeBad, badVal: v}
		}
		if d.Digits != nil && d.Digits.IsInt64() {
			m := d.Digits.Int64()
			s := d.Scale
			if m == 0 {
				return joinKey{kType: keyTypeInt64, intVal: 0}
			}
			for s > 0 && m%10 == 0 {
				m /= 10
				s--
			}
			if d.Neg {
				m = -m
			}
			if s == 0 {
				return joinKey{kType: keyTypeInt64, intVal: m}
			}
			return joinKey{kType: keyTypeDec, intVal: m, scale: s}
		}
		trimmed := decimal.TrimScale(d)
		if trimmed.Digits != nil && trimmed.Digits.IsInt64() && trimmed.Scale == 0 {
			m := trimmed.Digits.Int64()
			if trimmed.Neg {
				m = -m
			}
			return joinKey{kType: keyTypeInt64, intVal: m}
		}
		return joinKey{kType: keyTypeStr, strVal: decimal.Format(trimmed)}
	}
	var b []byte
	func() {
		defer func() {
			if r := recover(); r != nil {
				if !isSelPanic(r) {
					panic(r)
				}
				b = nil
			}
		}()
		b = v.AsBytes(Pos{})
	}()
	if b == nil {
		return joinKey{isBad: true, kType: keyTypeBad, badVal: v}
	}
	return joinKey{kType: keyTypeStr, strVal: string(b)}
}

type joinFacts struct {
	live    bool
	bad     *Value
	liveBad *Value
}

func checkJoinPair(equi *joinEqui, key joinKey, facts joinFacts) {
	if key.isNull || !facts.live {
		return
	}
	if key.isBad {
		if equi.swapped && facts.liveBad != nil {
			coerceJoinOperand(equi.numeric, facts.liveBad, equi.rightExpr)
		}
		coerceJoinOperand(equi.numeric, key.badVal, equi.leftExpr)
	}
	if facts.bad != nil {
		coerceJoinOperand(equi.numeric, facts.bad, equi.rightExpr)
	}
}

func coerceJoinOperand(numeric bool, v *Value, node *Node) {
	if numeric {
		v.AsDecimal(node.Pos)
	} else {
		v.AsBytes(node.Pos)
	}
}

func evalBoolSafely(args *Args, conjunct *Node, ctx *Context) (keep bool, errOccurred bool) {
	d := ctx.Depth
	defer func() {
		if r := recover(); r != nil {
			if !isSelPanic(r) {
				panic(r)
			}
			ctx.Depth = d
			errOccurred = true
		}
	}()
	return args.EvalNode(conjunct).AsBool(conjunct.Pos), false
}

func doLink(args *Args, ctx *Context, leftJoin bool) *Value {
	prefilter := ctx.JoinPrefilter
	ctx.JoinPrefilter = nil

	count := args.Count()
	if count != 3 && count != 5 {
		fail("E_ARITY", fmt.Sprintf("%s takes 3 or 5 arguments, got %d", args.Name(), count), args.Pos())
	}

	leftNode := args.Node(0)
	rightNode := args.Node(1)

	var stages []JoinStage
	var above []*JoinSideFacts
	var obligations []JoinObligation
	deep := false
	if prefilter != nil {
		stages = prefilter.Stages
		deep = prefilter.Deep
		above = prefilter.Above
		obligations = prefilter.Obligations
	}

	aboveKeysCache := make(map[int]map[string]bool)
	aboveKeys := func(stage JoinStage) map[string]bool {
		if k, ok := aboveKeysCache[stage.Above]; ok {
			return k
		}
		k := make(map[string]bool)
		for i := 0; i < stage.Above && i < len(above); i++ {
			for name := range above[i].Keys {
				k[name] = true
			}
		}
		aboveKeysCache[stage.Above] = k
		return k
	}

	aboveOf := func(stage JoinStage) []*JoinSideFacts {
		n := stage.Above
		if n > len(above) {
			n = len(above)
		}
		return above[:n]
	}

	boundName := func(n *Node, fallback string) string {
		name := singleRelationName(n)
		if name == "" {
			return fallback
		}
		return name
	}

	var b1Names, b2Names []string
	if count == 5 {
		b1Names = []string{args.Symbol(2), "_1"}
		b2Names = []string{args.Symbol(3), "_2"}
	} else {
		b1Names = []string{boundName(leftNode, "_1"), "_1"}
		b2Names = []string{boundName(rightNode, "_2"), "_2"}
	}

	jb1 := boundName(leftNode, "_1")
	jb2 := boundName(rightNode, "_2")
	predNode := args.Node(2)
	if count == 5 {
		jb1 = args.Symbol(2)
		jb2 = args.Symbol(3)
		predNode = args.Node(4)
	}

	jequi := extractJoinEqui(predNode, jb1, jb2)
	var rightSide *JoinSideFacts

	ownedByLeft := func(fields map[string]bool, stage JoinStage) bool {
		upper := aboveKeys(stage)
		for f := range fields {
			if rightSide.Keys[f] || upper[f] {
				return false
			}
		}
		return true
	}
	nothingRight := func(fields map[string]bool, stage JoinStage) bool {
		return false
	}

	isJoinOrFilter := func(n *Node) bool {
		return n != nil && n.T == NodeCall && (n.S == "LINK" || n.S == "LINK_LEFT" || n.S == "FILTER")
	}

	if deep && len(stages) > 0 && jequi != nil && isJoinOrFilter(leftNode) && joinPureSource(leftNode) && joinPureSource(rightNode) {
		rightFirst := args.Val(1)
		rightSide = newJoinSideFacts(rightFirst, joinRowKeys(rightFirst, b2Names), leftJoin, b2Names)
		totalBelow := func(reqs []JoinTotalReq, stage JoinStage) bool {
			return joinTotality(reqs, nil, rightSide, aboveOf(stage))
		}
		_, stop := joinStageWalk(stages, ownedByLeft, totalBelow, nothingRight)
		handed := joinTruncateStages(stages, stop)
		for i := range handed {
			handed[i].Above++
		}
		if len(handed) > 0 {
			sides := append([]*JoinSideFacts{rightSide}, above...)
			lowerB1 := strings.ToLower(jb1)
			rowNames := map[string]bool{jb1: true, lowerB1: true, "_1": true, "_": true}
			ownKey := JoinObligation{
				Key:      jequi.leftExpr,
				RowNames: rowNames,
				Outer:    len(above) + 1,
			}
			obs := append([]JoinObligation{ownKey}, obligations...)
			ctx.JoinPrefilter = &JoinPrefilter{
				Stages:      handed,
				Deep:        true,
				Above:       sides,
				Obligations: obs,
			}
		}
		func() {
			defer func() {
				ctx.JoinPrefilter = nil
			}()
			args.Val(0)
		}()
	}

	leftVal := args.Val(0)
	rightVal := args.Val(1)
	below := ctx.JoinPrefilterReport
	ctx.JoinPrefilterReport = nil

	if below != nil && below.Dropped && (leftVal.IsNull() || len(leftVal.Elements()) == 0) {
		leftVal = args.EvalNode(leftNode)
		below = nil
	}

	b1 := "_1"
	b2 := "_2"
	var predicate *Node
	if count == 3 {
		b1 = singleRelationName(args.Node(0))
		if b1 == "" {
			b1 = "_1"
		}
		b2 = singleRelationName(args.Node(1))
		if b2 == "" {
			b2 = "_2"
		}
		predicate = args.Node(2)
	} else {
		b1 = args.Symbol(2)
		b2 = args.Symbol(3)
		predicate = args.Node(4)
	}

	if leftVal.IsNull() {
		return NewListOwned(nil)
	}

	leftEnts := leftVal.Elements()
	rightEnts := rightVal.Elements()

	if len(leftEnts) == 0 || (len(rightEnts) == 0 && !leftJoin) {
		return NewListOwned(nil)
	}

	var sampleRight *Value
	if len(rightEnts) > 0 {
		sampleRight = ensureRowTableAlias(rightEnts[0].Val, b2)
	}
	var nullRight *Value
	if leftJoin {
		nullRight = makeNullRecord(sampleRight, b2)
	}

	projector := newJoinProjector(b1, b2, nullRight)
	equi := extractJoinEqui(predicate, b1, b2)
	if equi != nil && len(rightEnts) > 0 {
		rFrame := map[string]*Value{
			b2:                  nil,
			"_2":                nil,
			strings.ToLower(b2): nil,
		}
		ctx.PushFrame(rFrame)
		buckets := make(map[joinKey][]*Value)
		var facts joinFacts
		flatTest := newJoinFlatTest(b1, b2)
		rightFlat := true
		for _, rEntry := range rightEnts {
			right := ensureRowTableAlias(rEntry.Val, b2)
			rFrame[b2] = right
			rFrame["_2"] = right
			if lowerB2 := strings.ToLower(b2); lowerB2 != b2 {
				rFrame[lowerB2] = right
			}
			keyVal := args.EvalNode(equi.rightExpr)
			k := canonicalJoinKey(keyVal, equi.numeric)
			if !k.isNull {
				if k.isBad {
					if !facts.live {
						facts.liveBad = k.badVal
					}
					if facts.bad == nil {
						facts.bad = k.badVal
					}
				} else {
					buckets[k] = append(buckets[k], right)
				}
				facts.live = true
			}
			if rightFlat && !flatTest.isFlat(right) {
				rightFlat = false
			}
		}
		projector.rightFlat = rightFlat
		ctx.PopFrame()

		var prefix []*Node
		var rightPrefix []*Node
		leftBeforeRight := -1
		var binders []string
		report := &JoinReport{
			Applied: make(map[*Node]bool),
			Dropped: below != nil && below.Dropped,
		}

		rightNames := func(stage JoinStage) map[string]bool {
			names := map[string]bool{utf8.AsciiUpper(b2): true}
			if stage.Above == 0 {
				names["_2"] = true
			}
			return names
		}
		rightOk := !leftJoin && utf8.AsciiUpper(b1) != utf8.AsciiUpper(b2)
		rightHere := func(fields map[string]bool, stage JoinStage) bool {
			if !rightOk {
				return false
			}
			names := rightNames(stage)
			upper := aboveKeys(stage)
			for f := range fields {
				if !names[f] || upper[f] {
					return false
				}
			}
			return true
		}

		if prefilter != nil {
			if rightSide == nil {
				rightSide = newJoinSideFacts(rightVal, joinRowKeys(rightVal, b2Names), leftJoin, b2Names)
			}
			for _, stage := range stages {
				binders = append(binders, stage.Binder)
			}
			leftSide := newJoinSideFacts(leftVal, joinRowKeys(leftVal, b1Names), false, b1Names)
			totalHere := func(reqs []JoinTotalReq, stage JoinStage) bool {
				return joinTotality(reqs, leftSide, rightSide, aboveOf(stage))
			}
			ownedHere := func(fields map[string]bool, stage JoinStage) bool {
				upper := aboveKeys(stage)
				for f := range fields {
					if rightSide.Keys[f] || upper[f] {
						return false
					}
				}
				return true
			}
			safe := len(obligations) == 0 || joinKeysSafe(obligations, leftSide, rightSide, above)
			selfNames := map[string]bool{utf8.AsciiUpper(b1): true, "_1": true}
			var walkApplied []JoinApplied
			if safe {
				walkApplied, _ = joinStageWalk(stages, ownedHere, totalHere, rightHere)
			}
			for _, applied := range walkApplied {
				c := applied.Conjunct
				report.Applied[c.Node] = true
				if below != nil && !below.Errored && below.Applied[c.Node] {
					continue
				}
				if applied.Right {
					if leftBeforeRight < 0 {
						leftBeforeRight = len(prefix)
					}
					rightPrefix = append(rightPrefix, joinReadSelf(c.Node, rightNames(*applied.Stage), c.Binder))
					continue
				}
				node := c.Node
				for f := range c.Fields {
					if selfNames[f] && !leftSide.First[f] {
						node = joinReadSelf(c.Node, selfNames, c.Binder)
						break
					}
				}
				prefix = append(prefix, node)
			}
		}

		rejected := make(map[*Value]bool)
		rejecting := len(rightPrefix) > 0
		if rejecting {
			rFilterFrame := make(map[string]*Value, len(binders))
			for _, b := range binders {
				rFilterFrame[b] = nil
			}
			ctx.PushFrame(rFilterFrame)
			before := report.Errored
			report.Errored = false
			for _, rRows := range buckets {
				for _, right := range rRows {
					for _, b := range binders {
						rFilterFrame[b] = right
					}
					for _, conjunct := range rightPrefix {
						keep, errOccurred := evalBoolSafely(args, conjunct, ctx)
						if errOccurred {
							report.Errored = true
							break
						}
						if !keep {
							rejected[right] = true
							break
						}
					}
				}
			}
			ctx.PopFrame()
			if report.Errored && leftBeforeRight >= 0 {
				prefix = prefix[:leftBeforeRight]
			}
			report.Errored = report.Errored || before
		}

		numbered := (len(prefix) > 0 || rejecting) && !deep
		var keyedEntries []Entry
		dropped := false
		position := 1

		var fastField string
		if len(prefix) > 0 && deep {
			el := equi.leftExpr
			if el.T == NodeIndex && el.L != nil && el.L.T == NodeVar && el.R != nil && el.R.T == NodeText {
				owner := utf8.AsciiUpper(el.L.S)
				if owner == utf8.AsciiUpper(b1) || owner == "_1" || owner == "_" {
					fastField = el.R.S
				}
			}
		}

		var output []*Value
		lFrame := map[string]*Value{
			b1:                  nil,
			"_1":                nil,
			"_":                 nil,
			strings.ToLower(b1): nil,
		}
		for _, b := range binders {
			lFrame[b] = nil
		}
		ctx.PushFrame(lFrame)
		defer ctx.PopFrame()

		setRow := func(row *Value) {
			lFrame[b1] = row
			lFrame["_1"] = row
			lFrame["_"] = row
			if lowerB1 := strings.ToLower(b1); lowerB1 != b1 {
				lFrame[lowerB1] = row
			}
			for _, b := range binders {
				lFrame[b] = row
			}
		}

		verdict := func(conjuncts []*Node, row *Value) int {
			setRow(row)
			for _, conjunct := range conjuncts {
				keep, errOccurred := evalBoolSafely(args, conjunct, ctx)
				if errOccurred {
					report.Errored = true
					return 2
				}
				if !keep {
					return 1
				}
			}
			return 0
		}

		emit := func(joined *Value) {
			// A join's rows are a collection an operation builds (SPEC §6.4).
			checkCollection(int64(position), "the join", args.Pos())
			if numbered {
				keyedEntries = append(keyedEntries, Entry{Key: strconv.Itoa(position), Val: joined})
			} else {
				output = append(output, joined)
			}
			position++
		}

		for _, lEntry := range leftEnts {
			left := ensureRowTableAlias(lEntry.Val, b1)
			asked := -1
			if fastField != "" && left.Has(fastField) {
				asked = verdict(prefix, left)
				if asked == 1 {
					fieldVal := left.Get(fastField)
					checkJoinPair(equi, canonicalJoinKey(fieldVal, equi.numeric), facts)
					dropped = true
					continue
				}
			}

			setRow(left)
			lKeyVal := args.EvalNode(equi.leftExpr)
			lk := canonicalJoinKey(lKeyVal, equi.numeric)
			checkJoinPair(equi, lk, facts)

			var rRows []*Value
			var hasBucket bool
			if !lk.isNull && !lk.isBad {
				rRows, hasBucket = buckets[lk]
			}

			if asked < 0 {
				if len(prefix) > 0 {
					asked = verdict(prefix, left)
				} else {
					asked = 0
				}
			}

			if asked == 1 {
				dropped = true
				if numbered {
					if hasBucket {
						position += len(rRows)
					} else if leftJoin {
						position++
					}
				}
				continue
			}

			if hasBucket {
				skip := rejecting && asked == 0
				for _, rRow := range rRows {
					if skip && rejected[rRow] {
						dropped = true
						position++
						continue
					}
					emit(projector.project(left, rRow))
				}
			} else if leftJoin {
				emit(projector.project(left, nil))
			}
		}

		report.Dropped = report.Dropped || dropped
		if prefilter != nil {
			ctx.JoinPrefilterReport = report
		}

		if numbered {
			if dropped && len(keyedEntries) > 0 {
				items := make([]*Value, len(keyedEntries))
				keys := make([]string, len(keyedEntries))
				for i, e := range keyedEntries {
					items[i] = e.Val
					keys[i] = e.Key
				}
				return NewListWithKeys(items, keys)
			}
			if !dropped {
				for _, e := range keyedEntries {
					output = append(output, e.Val)
				}
			}
		}
		return NewListOwned(output)
	}

	report := &JoinReport{
		Dropped: below != nil && below.Dropped,
	}
	if prefilter != nil {
		ctx.JoinPrefilterReport = report
	}

	var output []*Value
	flatTest := newJoinFlatTest(b1, b2)
	rightFlat := true
	for _, rEntry := range rightEnts {
		right := ensureRowTableAlias(rEntry.Val, b2)
		if rightFlat && !flatTest.isFlat(right) {
			rightFlat = false
			break
		}
	}
	projector.rightFlat = rightFlat

	frame := map[string]*Value{
		b1:                  nil,
		"_1":                nil,
		"_":                 nil,
		b2:                  nil,
		"_2":                nil,
		strings.ToLower(b1): nil,
		strings.ToLower(b2): nil,
	}
	ctx.PushFrame(frame)
	defer ctx.PopFrame()

	for _, lEntry := range leftEnts {
		left := ensureRowTableAlias(lEntry.Val, b1)
		frame[b1] = left
		frame["_1"] = left
		frame["_"] = left
		if lowerB1 := strings.ToLower(b1); lowerB1 != b1 {
			frame[lowerB1] = left
		}

		matched := false
		for _, rEntry := range rightEnts {
			right := ensureRowTableAlias(rEntry.Val, b2)
			frame[b2] = right
			frame["_2"] = right
			if lowerB2 := strings.ToLower(b2); lowerB2 != b2 {
				frame[lowerB2] = right
			}

			if args.EvalNode(predicate).AsBool(predicate.Pos) {
				matched = true
				checkCollection(int64(len(output))+1, "the join", args.Pos())
				output = append(output, projector.project(left, right))
			}
		}

		if leftJoin && !matched {
			checkCollection(int64(len(output))+1, "the join", args.Pos())
			output = append(output, projector.project(left, nil))
		}
	}

	return NewListOwned(output)
}

func init() {
	Define(&Spec{
		Name:  "ALL",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			short := aggregateWalk(args, ctx, func(r *Value, k string, item *Value, body *Node) *Value {
				if !r.AsBool(body.Pos) {
					return NewBool(false)
				}
				return nil
			}, nil)
			if short != nil {
				return short
			}
			return NewBool(true)
		},
	})

	Define(&Spec{
		Name:  "ANY",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			short := aggregateWalk(args, ctx, func(r *Value, k string, item *Value, body *Node) *Value {
				if r.AsBool(body.Pos) {
					return NewBool(true)
				}
				return nil
			}, nil)
			if short != nil {
				return short
			}
			return NewBool(false)
		},
	})

	Define(&Spec{
		Name:  "MAP",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			var out []*Value
			aggregateWalk(args, ctx, func(r *Value, k string, item *Value, body *Node) *Value {
				// SPEC §3.4: an aggregate copies what it collects. The copy sits one
				// level down, inside the list being built.
				out = append(out, r.CloneAt(2, args.Pos()))
				return nil
			}, nil)
			return NewListOwned(out)
		},
	})

	Define(&Spec{
		Name:  "FILTER",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			written := args.Node(args.Count() - 1)
			handed := ctx.JoinPrefilter
			ctx.JoinPrefilter = nil

			src := args.Node(0)
			var ownNodes []*Node
			overJoin := false
			if src != nil && src.T == NodeCall && (src.S == "LINK" || src.S == "LINK_LEFT") {
				overJoin = true
				three := args.Count() == 3
				binder := "_"
				if three {
					binder = args.Symbol(1)
				}
				own := leadingFieldConjuncts(written, binder)
				for _, c := range own {
					ownNodes = append(ownNodes, c.Node)
				}
				blocked := len(own) > 0 && !own[0].FieldOnly && !own[0].HasTotal
				var pre JoinPrefilter
				if !blocked {
					pre.Stages = append(pre.Stages, JoinStage{Binder: binder, Conjuncts: own, Above: 0})
				}
				if handed != nil && !blocked {
					pre.Stages = append(pre.Stages, handed.Stages...)
					pre.Above = handed.Above
					pre.Obligations = handed.Obligations
				}
				if handed != nil {
					pre.Deep = true
				} else {
					pre.Deep = written.KeysUnobserved
				}
				if len(pre.Stages) > 0 {
					ctx.JoinPrefilter = &pre
				}
			}

			var inVal *Value
			func() {
				defer func() {
					ctx.JoinPrefilter = nil
				}()
				inVal = args.Val(0)
			}()

			report := ctx.JoinPrefilterReport
			ctx.JoinPrefilterReport = nil
			if report != nil && handed != nil {
				ctx.JoinPrefilterReport = report
			}

			var overrideBody *Node
			if overJoin && report != nil && !report.Errored {
				var rest []*Node
				for _, n := range ownNodes {
					if !report.Applied[n] {
						rest = append(rest, n)
					}
				}
				if len(rest) < len(ownNodes) {
					if len(rest) == 0 {
						return inVal
					}
					overrideBody = rest[0]
					for i := 1; i < len(rest); i++ {
						andNode := NewNode(NodeBin, overrideBody.Pos)
						andNode.S = "AND"
						andNode.L = overrideBody
						andNode.R = rest[i]
						overrideBody = andNode
					}
				}
			}

			isDense := inVal.isList && inVal.storage != nil && inVal.listKeys == nil
			var storage []*Value
			var keys []string
			needsCustomKeys := false
			origIdx := 1

			if isDense {
				aggregateWalk(args, ctx, func(r *Value, key string, item *Value, body *Node) *Value {
					if r.AsBool(body.Pos) {
						storage = append(storage, item.CloneAt(2, args.Pos()))
						if needsCustomKeys {
							keys = append(keys, strconv.Itoa(origIdx))
						}
					} else {
						if !needsCustomKeys {
							needsCustomKeys = true
							keys = make([]string, len(storage))
							for j := range storage {
								keys[j] = strconv.Itoa(j + 1)
							}
						}
					}
					origIdx++
					return nil
				}, overrideBody)
			} else {
				expectedIndex := 1
				aggregateWalk(args, ctx, func(r *Value, key string, item *Value, body *Node) *Value {
					if r.AsBool(body.Pos) {
						storage = append(storage, item.CloneAt(2, args.Pos()))
						if !needsCustomKeys && key != strconv.Itoa(expectedIndex) {
							needsCustomKeys = true
							keys = make([]string, len(storage)-1)
							for j := 0; j < len(storage)-1; j++ {
								keys[j] = strconv.Itoa(j + 1)
							}
						}
						if needsCustomKeys {
							keys = append(keys, key)
						}
						expectedIndex++
					}
					return nil
				}, overrideBody)
			}
			if needsCustomKeys {
				return NewListWithKeys(storage, keys)
			}
			return NewListOwned(storage)
		},
	})

	Define(&Spec{
		Name:  "SUM",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			total := decimal.Make(false, big.NewInt(0), 0)
			aggregateWalk(args, ctx, func(r *Value, k string, item *Value, body *Node) *Value {
				total = decimal.Add(total, r.AsDecimal(body.Pos), body.Pos, fail)
				return nil
			}, nil)
			return NewNum(total)
		},
	})

	Define(&Spec{
		Name: "JOIN",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			sep := args.Text(1)
			ents := args.Val(0).Elements()
			parts := make([]string, len(ents))
			total := int64(0)
			sepLen := runeLen(sep)
			for i, e := range ents {
				parts[i] = e.Val.AsText(args.PosOf(0))
				total = satAdd(total, int64(len(parts[i])))
				if i > 0 {
					total = satAdd(total, int64(len(sep)))
				}
			}
			// Bytes bound code points from above: count exactly only past the cap.
			if total > maxTextLen {
				exact := satMul(int64(len(ents)-1), sepLen)
				for _, p := range parts {
					exact = satAdd(exact, runeLen(p))
				}
				checkTextLen(exact, "JOIN's result", args.Pos())
			}
			return NewTextOwned(strings.Join(parts, sep))
		},
	})

	Define(&Spec{
		Name:  "SORT",
		Min:   1,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doSort(args, ctx, "ASC")
		},
	})

	Define(&Spec{
		Name:  "SORT_DESC",
		Min:   1,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doSort(args, ctx, "DESC")
		},
	})

	Define(&Spec{
		Name:  "SORT_BY",
		Min:   2,
		Max:   4,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doSort(args, ctx, "")
		},
	})

	Define(&Spec{
		Name:  "TOP",
		Min:   2,
		Max:   4,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doTop(args, ctx, "ASC")
		},
	})

	Define(&Spec{
		Name:  "TOP_DESC",
		Min:   2,
		Max:   4,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doTop(args, ctx, "DESC")
		},
	})

	Define(&Spec{
		Name:  "TOP_BY",
		Min:   3,
		Max:   5,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doTop(args, ctx, "")
		},
	})

	Define(&Spec{
		Name:  "BUCKET",
		Min:   2,
		Max:   4,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doBucket(args, ctx)
		},
	})

	Define(&Spec{
		Name:  "LINK",
		Min:   3,
		Max:   5,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doLink(args, ctx, false)
		},
	})

	Define(&Spec{
		Name:  "LINK_LEFT",
		Min:   3,
		Max:   5,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			return doLink(args, ctx, true)
		},
	})
}
