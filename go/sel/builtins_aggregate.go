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

func compareValues(a, b *Value) int {
	aNull := a.IsNull()
	bNull := b.IsNull()
	if aNull && bNull {
		return 0
	}
	if aNull {
		return -1
	}
	if bNull {
		return 1
	}

	aNum := a.LooksNumeric()
	bNum := b.LooksNumeric()
	if aNum && bNum {
		return decimal.Cmp(a.AsDecimal(Pos{}), b.AsDecimal(Pos{}))
	}

	if a.Kind == KindBool && b.Kind == KindBool {
		av := 0
		if a.boolVal {
			av = 1
		}
		bv := 0
		if b.boolVal {
			bv = 1
		}
		if av < bv {
			return -1
		}
		if av > bv {
			return 1
		}
		return 0
	}

	if (a.Kind == KindText || a.Kind == KindBin) && (b.Kind == KindText || b.Kind == KindBin) {
		return bytes.Compare(a.AsBytes(Pos{}), b.AsBytes(Pos{}))
	}

	rank := func(v *Value) int {
		if v.IsNull() {
			return 0
		}
		if v.Kind == KindBool {
			return 1
		}
		if v.LooksNumeric() {
			return 2
		}
		if v.Kind == KindText {
			return 3
		}
		if v.Kind == KindBin {
			return 4
		}
		return 5
	}
	ra := rank(a)
	rb := rank(b)
	if ra < rb {
		return -1
	}
	if ra > rb {
		return 1
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
	if val.IsNull() {
		return NewListOwned(nil)
	}
	ents := val.Elements()
	if len(ents) == 0 {
		return NewListOwned(nil)
	}

	count := args.Count()
	direction := forcedDir
	if direction == "" {
		direction = "ASC"
	}

	var indexed []sortItem
	if count == 1 {
		indexed = make([]sortItem, len(ents))
		for i, e := range ents {
			indexed[i] = sortItem{item: e.Val, key: e.Val, idx: i}
		}
	} else {
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
		} else { // 4
			binder = args.Symbol(1)
			body = args.Node(2)
			direction = utf8.AsciiUpper(args.Text(3))
		}

		if direction != "ASC" && direction != "DESC" {
			posIdx := 2
			if count == 4 {
				posIdx = 3
			}
			fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args.PosOf(posIdx))
		}

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
		out[i] = x.item
	}
	return NewListOwned(out)
}

func doTop(args *Args, ctx *Context, forcedDir string) *Value {
	val := args.Val(0)
	limit := int(args.NonNegInt(args.Count() - 1))
	if limit == 0 || val.IsNull() || val.Size() == 0 {
		return NewListOwned(nil)
	}

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

	ents := val.Elements()
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
		out[i] = items[i].item
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
	if val.IsNull() || val.Size() == 0 {
		return NewListOwned(nil)
	}
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
				rowsCopy[i] = r.Clone()
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
		out[i] = args.EvalNode(aggNode)
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

type joinKey struct {
	isBad   bool
	badVal  *Value
	isNull  bool
	numVal  *decimal.Dec
	byteVal string
}

func canonicalJoinKey(v *Value, numeric bool) joinKey {
	if v == nil || v.IsNull() {
		return joinKey{isNull: true}
	}
	if numeric {
		var d *decimal.Dec
		func() {
			defer func() {
				if r := recover(); r != nil {
					d = nil
				}
			}()
			d = v.AsDecimal(Pos{})
		}()
		if d == nil {
			return joinKey{isBad: true, badVal: v}
		}
		return joinKey{numVal: decimal.TrimScale(d)}
	}
	var b []byte
	func() {
		defer func() {
			if r := recover(); r != nil {
				b = nil
			}
		}()
		b = v.AsBytes(Pos{})
	}()
	if b == nil {
		return joinKey{isBad: true, badVal: v}
	}
	return joinKey{byteVal: string(b)}
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

func doLink(args *Args, ctx *Context, leftJoin bool) *Value {
	count := args.Count()
	if count != 3 && count != 5 {
		fail("E_ARITY", fmt.Sprintf("%s takes 3 or 5 arguments, got %d", args.Name(), count), args.Pos())
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

	leftVal := args.Val(0)
	rightVal := args.Val(1)

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

	equi := extractJoinEqui(predicate, b1, b2)
	if equi != nil && len(rightEnts) > 0 {
		rFrame := map[string]*Value{
			b2:                  nil,
			"_2":                nil,
			strings.ToLower(b2): nil,
		}
		ctx.PushFrame(rFrame)
		buckets := make(map[string][]*Value)
		var facts joinFacts
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
					keyStr := k.byteVal
					if equi.numeric {
						keyStr = decimal.Format(k.numVal)
					}
					buckets[keyStr] = append(buckets[keyStr], right)
				}
				facts.live = true
			}
		}
		ctx.PopFrame()

		var output []*Value
		lFrame := map[string]*Value{
			b1:                  nil,
			"_1":                nil,
			"_":                 nil,
			strings.ToLower(b1): nil,
		}
		ctx.PushFrame(lFrame)
		defer ctx.PopFrame()

		for _, lEntry := range leftEnts {
			left := ensureRowTableAlias(lEntry.Val, b1)
			lFrame[b1] = left
			lFrame["_1"] = left
			lFrame["_"] = left
			if lowerB1 := strings.ToLower(b1); lowerB1 != b1 {
				lFrame[lowerB1] = left
			}
			lKeyVal := args.EvalNode(equi.leftExpr)
			lk := canonicalJoinKey(lKeyVal, equi.numeric)
			checkJoinPair(equi, lk, facts)

			matched := false
			if !lk.isNull && !lk.isBad {
				keyStr := lk.byteVal
				if equi.numeric {
					keyStr = decimal.Format(lk.numVal)
				}
				if rRows, ok := buckets[keyStr]; ok {
					matched = true
					for _, rRow := range rRows {
						output = append(output, makeJoinedRow(left, rRow, b1, b2, nullRight))
					}
				}
			}
			if leftJoin && !matched {
				output = append(output, makeJoinedRow(left, nil, b1, b2, nullRight))
			}
		}
		return NewListOwned(output)
	}

	var output []*Value
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
				output = append(output, makeJoinedRow(left, right, b1, b2, nullRight))
			}
		}

		if leftJoin && !matched {
			output = append(output, makeJoinedRow(left, nil, b1, b2, nullRight))
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
				out = append(out, r)
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
			inVal := args.Val(0)
			isDense := inVal.isList && inVal.storage != nil && inVal.listKeys == nil
			var storage []*Value
			var keys []string
			needsCustomKeys := false
			origIdx := 1

			if isDense {
				aggregateWalk(args, ctx, func(r *Value, key string, item *Value, body *Node) *Value {
					if r.AsBool(body.Pos) {
						storage = append(storage, item)
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
				}, nil)
			} else {
				expectedIndex := 1
				aggregateWalk(args, ctx, func(r *Value, key string, item *Value, body *Node) *Value {
					if r.AsBool(body.Pos) {
						storage = append(storage, item)
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
				}, nil)
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
			for i, e := range ents {
				parts[i] = e.Val.AsText(args.PosOf(0))
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
