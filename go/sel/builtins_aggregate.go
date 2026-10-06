// Aggregate and relational built-in functions: ALL, ANY, MAP, FILTER, SUM, JOIN, SORT, TOP, BUCKET, LINK, LINK_LEFT.

package sel

import (
	"math/big"
	"slices"
	"strconv"
	"strings"
	"sync"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
	"github.com/nathanjel/sel/go/internal/vocab"
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

// condWalk is aggregateWalk for a body read as a condition (FILTER, ALL, ANY):
// the body goes through evalCond, so a comparison builds no BOOL per element.
// visit gets the condition, the element's key and the element, and ends the
// walk by returning true.
func condWalk(args *Args, ctx *Context, visit func(keep bool, key string, item *Value) bool, bodyOverride *Node) {
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
		if visit(evalCond(body, ctx), entry.Key, entry.Val) {
			return
		}
	}
}

// sortRank is the kind rank of the total order (spec §7.3): NULL < BOOL <
// numeric-looking text and numbers < other TEXT < BIN < lists and records.
// sortLeaf is the value scalar context reads (spec §3.2: the first child,
// recursively), so a record sorts by its first field and ranks by that field's
// kind. nil when the chain ends in nothing (NULL). It walks without raising: a
// NULL key is common and a panic per comparison would be slow.
func sortLeaf(v *Value) *Value {
	guard := 0
	for v.kind == KindNone {
		if v.IsNull() || v.Size() == 0 {
			return nil
		}
		if v.storage != nil {
			v = v.storage[0]
		} else {
			v = v.entries[0].Val
		}
		guard++
		if guard > maxDepth { // as ScalarSource
			return nil
		}
	}
	return v
}

func sortRank(v *Value) int {
	if v == nil || v.IsNull() {
		return 0
	}
	switch v.kind {
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

// sortKey is a sort key classified ONCE: the rank of the total order every sort,
// TOP and bucket key uses, and the payload its comparison needs. Values of one
// rank compare within it (numbers by exact decimal value, text and BIN bytewise,
// FALSE before TRUE); values of different ranks compare by rank; equal values
// tie, and the caller keeps input order for a tie. Classifying per comparison
// would be n log n classifications of n keys; sort_perf_test.go holds cmpSortKey
// to compareValues, the order written out directly, over mixed-kind lists.
type sortKey struct {
	rank  int8
	b     bool         // rank 1
	small bool         // rank 2: the value is the whole number `i`
	i     int64        //
	dec   *decimal.Dec // rank 2 otherwise
	s     string       // rank 3, 4: the bytes
}

func classifySortKey(v *Value) sortKey {
	v = sortLeaf(v)
	r := sortRank(v)
	k := sortKey{rank: int8(r)}
	switch r {
	case 1:
		k.b = v.boolVal
	case 2:
		d := v.AsDecimal(Pos{})
		// A whole number of at most 18 digits is an int64 (no overflow when two
		// are subtracted or scaled); anything else is compared as a decimal.
		if d.Scale == 0 && d.Digits != nil && d.Digits.IsInt64() {
			if m := d.Digits.Int64(); m < 1e18 {
				k.small = true
				k.i = m
				if d.Neg {
					k.i = -m
				}
				return k
			}
		}
		k.dec = d
	case 3, 4:
		k.s = string(v.AsBytes(Pos{}))
	}
	return k
}

func cmpSortKey(a, b *sortKey) int {
	if a.rank != b.rank {
		if a.rank < b.rank {
			return -1
		}
		return 1
	}
	switch a.rank {
	case 1:
		if a.b == b.b {
			return 0
		}
		if !a.b {
			return -1
		}
		return 1
	case 2:
		if a.small && b.small {
			switch {
			case a.i < b.i:
				return -1
			case a.i > b.i:
				return 1
			}
			return 0
		}
		return decimal.Cmp(a.decOf(), b.decOf())
	case 3, 4:
		return strings.Compare(a.s, b.s)
	}
	return 0
}

func (k *sortKey) decOf() *decimal.Dec {
	if k.dec != nil {
		return k.dec
	}
	return decimal.FromInt(k.i)
}

type sortItem struct {
	item *Value
	key  *Value
	idx  int
}

// keyedItem is a sortItem with its key classified.
type keyedItem struct {
	item *Value
	key  sortKey
	idx  int
}

// classifyItems classifies every key once and returns pointers into one backing
// array: the sort moves 8-byte pointers, not 80-byte items.
func classifyItems(items []sortItem) []*keyedItem {
	backing := make([]keyedItem, len(items))
	out := make([]*keyedItem, len(items))
	for i := range items {
		backing[i] = keyedItem{item: items[i].item, key: classifySortKey(items[i].key), idx: items[i].idx}
		out[i] = &backing[i]
	}
	return out
}

// lessKeyed is the order of the sort: by key (reversed for DESC), then input
// order. The key order is a total preorder (SPEC §7.3), so an unstable sort with
// the input index as tie-break gives exactly what a stable sort gives.
func lessKeyed(desc bool) func(a, b *keyedItem) int {
	return func(a, b *keyedItem) int {
		c := cmpSortKey(&a.key, &b.key)
		if desc {
			c = -c
		}
		if c != 0 {
			return c
		}
		return a.idx - b.idx
	}
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
	// (spec §7.4), so an empty list cannot hide a bad direction. Which argument
	// is which is the manifest's (spec/builtins.json): a text literal last is the
	// direction, whatever the slot before it holds.
	binder := "_"
	var body *Node
	roles := sortRoles(args.name, args.nodes)
	if roles.Binder >= 0 {
		binder = args.Symbol(roles.Binder)
	}
	if roles.Key >= 0 {
		body = args.Node(roles.Key)
	}
	if roles.Dir >= 0 {
		direction = utf8.AsciiUpper(args.Text(roles.Dir))
		if direction != "ASC" && direction != "DESC" {
			fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args.PosOf(roles.Dir))
		}
	}

	if val.IsNull() {
		return newListOwned(nil)
	}
	ents := val.Elements()
	if len(ents) == 0 {
		return newListOwned(nil)
	}

	var indexed []sortItem
	eager := false
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
		// Collected once its key is computed (SPEC §3.4): a key that might write
		// copies the element then, so a later key's write cannot reach it.
		eager = !subtreeIsPure(body)
		indexed = make([]sortItem, len(ents))
		for i, e := range ents {
			frame[binder] = e.Val
			if needsK {
				frame["_K"] = NewText(e.Key)
			}
			ctx.PushFrame(frame)
			evalKey := args.EvalNode(body)
			ctx.PopFrame()
			item := e.Val
			if eager {
				item = e.Val.CloneAt(2, args.Pos())
			}
			indexed[i] = sortItem{item: item, key: evalKey, idx: i}
		}
	}

	keyed := classifyItems(indexed)
	slices.SortFunc(keyed, lessKeyed(direction == "DESC"))

	out := make([]*Value, len(keyed))
	for i := range keyed {
		// SPEC §3.4: SORT copies what it collects (already copied when eager).
		if eager {
			out[i] = keyed[i].item
		} else {
			out[i] = keyed[i].item.CloneAt(2, args.Pos())
		}
	}
	return newListOwned(out)
}

// topSelectFactor: below limit*factor < n the bounded selection beats sorting
// everything; above it the plain sort does.
const topSelectFactor = 8

// selectBest returns the k smallest items under cmp, in cmp order. cmp is a total
// order (it ends in the input index), so the result is unique.
func selectBest(items []*keyedItem, k int, cmp func(a, b *keyedItem) int) []*keyedItem {
	// A max-heap (worst of the best at the root) of k items.
	heap := make([]*keyedItem, 0, k)
	siftDown := func(i int) {
		n := len(heap)
		for {
			l, r, big := 2*i+1, 2*i+2, i
			if l < n && cmp(heap[l], heap[big]) > 0 {
				big = l
			}
			if r < n && cmp(heap[r], heap[big]) > 0 {
				big = r
			}
			if big == i {
				return
			}
			heap[i], heap[big] = heap[big], heap[i]
			i = big
		}
	}
	for i := range items {
		if len(heap) < k {
			heap = append(heap, items[i])
			// sift up
			c := len(heap) - 1
			for c > 0 {
				p := (c - 1) / 2
				if cmp(heap[c], heap[p]) <= 0 {
					break
				}
				heap[c], heap[p] = heap[p], heap[c]
				c = p
			}
			continue
		}
		if cmp(items[i], heap[0]) < 0 {
			heap[0] = items[i]
			siftDown(0)
		}
	}
	slices.SortFunc(heap, cmp)
	return heap
}

func doTop(args *Args, ctx *Context, forcedDir string) *Value {
	val := args.Val(0)
	limit := int(args.NonNegInt(args.Count() - 1))

	binder := "_"
	var body *Node
	direction := forcedDir
	if direction == "" {
		direction = "ASC"
	}
	roles := sortRoles(args.name, args.nodes) // as doSort; the count is last
	if roles.Key < 0 {
		binder = ""
	}
	if roles.Binder >= 0 {
		binder = args.Symbol(roles.Binder)
	}
	if roles.Key >= 0 {
		body = args.Node(roles.Key)
	}
	if roles.Dir >= 0 {
		direction = utf8.AsciiUpper(args.Text(roles.Dir))
		if direction != "ASC" && direction != "DESC" {
			fail("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args.PosOf(roles.Dir))
		}
	}

	// TOP is TAKE(SORT(...), n): the direction is checked and the keys evaluated
	// whatever n is, and only an empty or NULL source has nothing to sort.
	if val.IsNull() {
		return newListOwned(nil)
	}
	ents := val.Elements()
	if len(ents) == 0 {
		return newListOwned(nil)
	}
	needsK := body != nil && nodeContainsVar(body, "_K")
	frame := map[string]*Value{}
	if binder != "" {
		frame[binder] = nil
	}
	if needsK {
		frame["_K"] = nil
	}

	// Collected once its key is computed (SPEC §3.4); see doSort.
	eager := body != nil && !subtreeIsPure(body)
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
		item := e.Val
		if eager {
			item = e.Val.CloneAt(2, args.Pos())
		}
		items[i] = sortItem{item: item, key: kVal, idx: i}
	}

	if limit > len(items) {
		limit = len(items)
	}
	keyed := classifyItems(items)
	cmp := lessKeyed(direction == "DESC")
	var best []*keyedItem
	if limit == 0 {
		// The keys were evaluated (they must be); there is nothing to select.
		best = nil
	} else if limit*topSelectFactor < len(keyed) {
		// Only the `limit` best are wanted. Select them with a bounded
		// heap and sort just those: O(n log k) where the full sort was O(n log n).
		// The comparison includes the input index, so ties resolve to input order
		// exactly as they do in the full sort.
		best = selectBest(keyed, limit, cmp)
	} else {
		slices.SortFunc(keyed, cmp)
		best = keyed[:limit]
	}

	out := make([]*Value, limit)
	for i := 0; i < limit; i++ {
		// SPEC §3.4: TOP follows SORT and copies what it collects.
		if eager {
			out[i] = best[i].item
		} else {
			out[i] = best[i].item.CloneAt(2, args.Pos())
		}
	}
	return newListOwned(out)
}

func bucketKeyText(key *Value, pos Pos) string {
	if key.kind == KindNone {
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
		return newListOwned(nil)
	}
	// A scalar is a one-element list (spec §7.3), so emptiness is asked of the
	// elements and not of the children.
	ents := val.Elements()
	if len(ents) == 0 {
		return newListOwned(nil)
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
	// A row is collected when its key is computed and it is grouped (SPEC §3.4):
	// when the key or the projection might write, it is copied then, so neither a
	// later key nor the projection can change a row already grouped.
	eager := !subtreeIsPure(keyNode) || (aggNode != nil && !subtreeIsPure(aggNode))
	rowLevels := 3
	if aggNode != nil {
		rowLevels = 2
	}

	ctx.PushFrame(frame)
	for _, e := range ents {
		frame[binder] = e.Val
		if needsK {
			frame["_K"] = NewText(e.Key)
		}
		groupKey := args.EvalNode(keyNode)
		row := e.Val
		if eager {
			row = e.Val.CloneAt(rowLevels, args.Pos())
		}
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
				g.rows = append(g.rows, row)
			} else {
				g := &bucketGroup{key: groupKey, keyStr: keyStr, rows: []*Value{row}}
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
				g.rows = append(g.rows, row)
				found = true
				break
			}
		}
		if !found {
			g := &bucketGroup{key: groupKey, keyStr: keyStr, rows: []*Value{row}}
			table[h] = append(table[h], g)
			groups = append(groups, g)
		}
	}
	ctx.PopFrame()

	if aggNode == nil {
		out := NewNone()
		for _, g := range groups {
			rowsCopy := make([]*Value, len(g.rows))
			for i, r := range g.rows {
				// The rows sit in a list inside the result record: level 3.
				if eager {
					rowsCopy[i] = r
				} else {
					rowsCopy[i] = r.CloneAt(3, args.Pos())
				}
			}
			out.Set(g.keyStr, newListOwned(rowsCopy))
		}
		return out
	}

	out := make([]*Value, len(groups))
	aggFrame := map[string]*Value{binder: nil, "_K": nil}
	ctx.PushFrame(aggFrame)
	defer ctx.PopFrame()

	for i, g := range groups {
		aggFrame[binder] = newListOwned(g.rows)
		aggFrame["_K"] = g.key
		out[i] = args.EvalNode(aggNode).CloneAt(2, args.Pos())
	}
	return newListOwned(out)
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

// aliasKey and aliasPlan memoise what ensureRowTableAlias computes for a row of a
// given shape and table name: the target shape is a pure function of the
// two, and recomputing it built a keys slice, a set and a signature for every row
// of every LINK. Bounded; reset when full (like the shape cache it feeds on).
type aliasKey struct {
	shape *RecordShape
	name  string
}

type aliasPlan struct {
	target   *RecordShape // nil: fall back to entries
	addLower bool
}

const aliasMemoEntries = 1024

var (
	aliasMu   sync.RWMutex
	aliasMemo = make(map[aliasKey]aliasPlan)
)

func planRowTableAlias(oldShape *RecordShape, tableName string) aliasPlan {
	k := aliasKey{oldShape, tableName}
	aliasMu.RLock()
	p, ok := aliasMemo[k]
	aliasMu.RUnlock()
	if ok {
		return p
	}
	lower := utf8.AsciiLower(tableName)
	_, lowerTaken := oldShape.keyMap[lower]
	addLower := lower != tableName && !lowerTaken
	keys := make([]string, len(oldShape.keys)+1, len(oldShape.keys)+2)
	copy(keys, oldShape.keys)
	keys[len(oldShape.keys)] = tableName
	if addLower {
		keys = append(keys, lower)
	}
	p = aliasPlan{target: uniqueRecordShape(keys), addLower: addLower}
	aliasMu.Lock()
	if len(aliasMemo) >= aliasMemoEntries {
		aliasMemo = make(map[aliasKey]aliasPlan)
	}
	aliasMemo[k] = p
	aliasMu.Unlock()
	return p
}

func ensureRowTableAlias(row *Value, tableName string) *Value {
	if tableName == "" || isPositionalBinder(tableName) || row.Has(tableName) {
		return row
	}
	lower := utf8.AsciiLower(tableName)
	if row.shape != nil {
		oldShape := row.shape
		if plan := planRowTableAlias(oldShape, tableName); plan.target != nil {
			n := len(oldShape.keys)
			storage := make([]*Value, plan.target.size)
			copy(storage, row.storage)
			storage[n] = row
			if plan.addLower {
				storage[n+1] = row
			}
			return newShapedRecord(plan.target, storage)
		}
	}
	entries := make([]Entry, len(row.Entries()))
	copy(entries, row.Entries())
	entries = append(entries, Entry{Key: tableName, Val: row})
	if lower != tableName && !row.Has(lower) {
		entries = append(entries, Entry{Key: lower, Val: row})
	}
	return newRecordFromEntries(entries)
}

func makeNullRecord(sample *Value, tableName string) *Value {
	if sample != nil && !sample.IsNull() {
		var entries []Entry
		for _, k := range sample.Keys() {
			entries = append(entries, Entry{Key: k, Val: NewNull()})
		}
		return newRecordFromEntries(entries)
	}
	var entries []Entry
	if tableName != "" && !isPositionalBinder(tableName) {
		entries = append(entries, Entry{Key: tableName, Val: NewNull()})
		lower := utf8.AsciiLower(tableName)
		if lower != tableName {
			entries = append(entries, Entry{Key: lower, Val: NewNull()})
		}
	}
	return newRecordFromEntries(entries)
}

const (
	joinScalar = 0
	joinNull   = 1
	joinNested = 2
)

func joinCategory(v *Value) int {
	if v.kind != KindNone || v.isList {
		return joinScalar
	}
	if v.Size() > 0 {
		return joinNested
	}
	return joinNull
}

func binderKeys(name, positional string) []string {
	keys := []string{name}
	lower := utf8.AsciiLower(name)
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

	return newRecordFromEntries(entries)
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
	if node == nil || node.T != NodeBin || !vocab.IsEquality(node.S) {
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

// `equality AND residual…` used to fall back to the O(n*m) nested loop
// because extractJoinEqui wants the whole predicate to be one `==`. A LEADING
// equality conjunct can be the hash key instead, with the remaining conjuncts
// evaluated per bucket pair: AND short-circuits left to right, so a pair whose
// equality is false never reaches the residual in the nested loop either, and the
// matched pairs are visited in the same (left row, right row) order.
//
// What the nested loop would ALSO do is raise on a pair whose equality raises
// (a NULL, unparseable or missing key), in pair order, and the hash path cannot
// reproduce that order. So it is used only when every key of both sides evaluates
// cleanly to a plain scalar: any error, NULL, bad or nested key hands the whole
// join back to the nested loop, which then raises exactly what it always did. The
// key expressions are evaluated speculatively, which is unobservable because they
// are refused unless assignment-free and free of host functions.
func extractJoinEquiResidual(node *Node, b1, b2 string) (*joinEqui, []*Node) {
	var rev []*Node
	for node != nil && node.T == NodeBin && node.S == "AND" {
		rev = append(rev, node.R)
		node = node.L
	}
	if len(rev) == 0 {
		return nil, nil
	}
	equi := extractJoinEqui(node, b1, b2)
	if equi == nil || !subtreeIsPure(equi.leftExpr) || !subtreeIsPure(equi.rightExpr) {
		return nil, nil
	}
	rest := make([]*Node, len(rev))
	for i, c := range rev {
		rest[len(rev)-1-i] = c
	}
	return equi, rest
}

// plainJoinKey evaluates a key expression and returns its canonical key, or ok =
// false if the expression raises or the key is NULL, unparseable or nested.
func plainJoinKey(args *Args, expr *Node, ctx *Context, numeric bool) (k joinKey, ok bool) {
	d := ctx.depth
	defer func() {
		if r := recover(); r != nil {
			if !isSelPanic(r) {
				panic(r)
			}
			ctx.depth = d
			ok = false
		}
	}()
	v := args.EvalNode(expr)
	if v == nil || v.IsNull() || joinCategory(v) != joinScalar {
		return joinKey{}, false
	}
	k = canonicalJoinKey(v, numeric)
	if k.isNull || k.isBad {
		return joinKey{}, false
	}
	return k, true
}

func evalBoolSafely(args *Args, conjunct *Node, ctx *Context) (keep bool, errOccurred bool) {
	d := ctx.depth
	defer func() {
		if r := recover(); r != nil {
			if !isSelPanic(r) {
				panic(r)
			}
			ctx.depth = d
			errOccurred = true
		}
	}()
	return args.EvalNode(conjunct).AsBool(conjunct.Pos), false
}

func doLink(args *Args, ctx *Context, leftJoin bool) *Value {
	prefilter := ctx.joinPrefilter
	ctx.joinPrefilter = nil

	count := args.Count() // 3 or 5: arity_LINK runs at compile time

	leftNode := args.Node(0)
	rightNode := args.Node(1)

	var stages []joinStage
	var above []*joinSideFacts
	var obligations []joinObligation
	deep := false
	if prefilter != nil {
		stages = prefilter.Stages
		deep = prefilter.Deep
		above = prefilter.Above
		obligations = prefilter.Obligations
	}

	aboveKeysCache := make(map[int]map[string]bool)
	aboveKeys := func(stage joinStage) map[string]bool {
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

	aboveOf := func(stage joinStage) []*joinSideFacts {
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

	// The row binders: named in the five-argument form, else the sources'
	// relation names, else _1 and _2.
	jb1 := boundName(leftNode, "_1")
	jb2 := boundName(rightNode, "_2")
	predNode := args.Node(2)
	if count == 5 {
		jb1 = args.Symbol(2)
		jb2 = args.Symbol(3)
		predNode = args.Node(4)
	}
	b1Names := []string{jb1, "_1"}
	b2Names := []string{jb2, "_2"}

	jequi := extractJoinEqui(predNode, jb1, jb2)
	var rightSide *joinSideFacts

	ownedByLeft := func(fields map[string]bool, stage joinStage) bool {
		upper := aboveKeys(stage)
		for f := range fields {
			if rightSide.Keys[f] || upper[f] {
				return false
			}
		}
		return true
	}
	isJoinOrFilter := func(n *Node) bool {
		return n != nil && n.T == NodeCall && (n.S == "LINK" || n.S == "LINK_LEFT" || n.S == "FILTER")
	}

	if deep && len(stages) > 0 && jequi != nil && isJoinOrFilter(leftNode) && joinPureSource(leftNode) && joinPureSource(rightNode) {
		var rightFirst *Value
		func() {
			depth := ctx.depth
			defer func() {
				if r := recover(); r != nil {
					if !isSelPanic(r) {
						panic(r)
					}
					// The right source went first for the prefilter's sake, which is
					// only unobservable while neither source raises (SPEC 7.4: as
					// written, the left source runs first). The left source is pure
					// too: run it now with nothing handed down. If it raises, ITS
					// error is the one as written; if it does not, the right
					// source's error stands.
					ctx.depth = depth
					args.Val(0)
					panic(r)
				}
			}()
			rightFirst = args.Val(1)
		}()
		rightSide = newJoinSideFacts(rightFirst, joinRowKeys(rightFirst, b2Names), leftJoin, b2Names)
		totalBelow := func(reqs []joinTotalReq, stage joinStage) bool {
			return joinTotality(reqs, nil, rightSide, aboveOf(stage))
		}
		_, stop := joinStageWalk(stages, ownedByLeft, totalBelow, neverOnTheRight)
		handed := joinTruncateStages(stages, stop)
		for i := range handed {
			handed[i].Above++
		}
		if len(handed) > 0 {
			sides := append([]*joinSideFacts{rightSide}, above...)
			lowerB1 := utf8.AsciiLower(jb1)
			rowNames := map[string]bool{jb1: true, lowerB1: true, "_1": true, "_": true}
			ownKey := joinObligation{
				Key:      jequi.leftExpr,
				RowNames: rowNames,
				Outer:    len(above) + 1,
			}
			obs := append([]joinObligation{ownKey}, obligations...)
			ctx.joinPrefilter = &joinPrefilter{
				Stages:      handed,
				Deep:        true,
				Above:       sides,
				Obligations: obs,
			}
		}
		func() {
			defer func() {
				ctx.joinPrefilter = nil
			}()
			args.Val(0)
		}()
	}

	leftVal := args.Val(0)
	rightVal := args.Val(1)
	below := ctx.joinPrefilterReport
	ctx.joinPrefilterReport = nil

	if below != nil && below.Dropped && (leftVal.IsNull() || len(leftVal.Elements()) == 0) {
		leftVal = args.EvalNode(leftNode)
		below = nil
	}

	b1, b2, predicate := jb1, jb2, predNode

	if leftVal.IsNull() {
		return newListOwned(nil)
	}

	leftEnts := leftVal.Elements()
	rightEnts := rightVal.Elements()

	if len(leftEnts) == 0 || (len(rightEnts) == 0 && !leftJoin) {
		return newListOwned(nil)
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
			utf8.AsciiLower(b2): nil,
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
			if lowerB2 := utf8.AsciiLower(b2); lowerB2 != b2 {
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
		report := &joinReport{
			Applied: make(map[*Node]bool),
			Dropped: below != nil && below.Dropped,
		}

		rightNames := func(stage joinStage) map[string]bool {
			names := map[string]bool{utf8.AsciiUpper(b2): true}
			if stage.Above == 0 {
				names["_2"] = true
			}
			return names
		}
		rightOk := !leftJoin && utf8.AsciiUpper(b1) != utf8.AsciiUpper(b2)
		rightHere := func(fields map[string]bool, stage joinStage) bool {
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
			totalHere := func(reqs []joinTotalReq, stage joinStage) bool {
				return joinTotality(reqs, leftSide, rightSide, aboveOf(stage))
			}
			safe := len(obligations) == 0 || joinKeysSafe(obligations, leftSide, rightSide, above)
			selfNames := map[string]bool{utf8.AsciiUpper(b1): true, "_1": true}
			var walkApplied []joinApplied
			if safe {
				walkApplied, _ = joinStageWalk(stages, ownedByLeft, totalHere, rightHere)
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
			utf8.AsciiLower(b1): nil,
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
			if lowerB1 := utf8.AsciiLower(b1); lowerB1 != b1 {
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
			ctx.joinPrefilterReport = report
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
		return newListOwned(output)
	}

	report := &joinReport{
		Dropped: below != nil && below.Dropped,
	}
	if prefilter != nil {
		ctx.joinPrefilterReport = report
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
		utf8.AsciiLower(b1): nil,
		utf8.AsciiLower(b2): nil,
	}
	ctx.PushFrame(frame)
	defer ctx.PopFrame()

	lowerB1, lowerB2 := utf8.AsciiLower(b1), utf8.AsciiLower(b2)
	setLeft := func(left *Value) {
		frame[b1] = left
		frame["_1"] = left
		frame["_"] = left
		if lowerB1 != b1 {
			frame[lowerB1] = left
		}
	}
	setRight := func(right *Value) {
		frame[b2] = right
		frame["_2"] = right
		if lowerB2 != b2 {
			frame[lowerB2] = right
		}
	}

	// A leading equality conjunct is the hash key (see extractJoinEquiResidual).
	if resEqui, residual := extractJoinEquiResidual(predicate, b1, b2); resEqui != nil {
		lrows := make([]*Value, len(leftEnts))
		lkeys := make([]joinKey, len(leftEnts))
		usable := true
		for i, lEntry := range leftEnts {
			left := ensureRowTableAlias(lEntry.Val, b1)
			lrows[i] = left
			setLeft(left)
			k, ok := plainJoinKey(args, resEqui.leftExpr, ctx, resEqui.numeric)
			if !ok {
				usable = false
				break
			}
			lkeys[i] = k
		}
		var buckets map[joinKey][]*Value
		if usable {
			buckets = make(map[joinKey][]*Value)
			for _, rEntry := range rightEnts {
				right := ensureRowTableAlias(rEntry.Val, b2)
				setRight(right)
				k, ok := plainJoinKey(args, resEqui.rightExpr, ctx, resEqui.numeric)
				if !ok {
					usable = false
					break
				}
				buckets[k] = append(buckets[k], right)
			}
		}
		if usable {
			for i, left := range lrows {
				setLeft(left)
				matched := false
				for _, right := range buckets[lkeys[i]] {
					setRight(right)
					keep := true
					for _, c := range residual {
						if !args.EvalNode(c).AsBool(c.Pos) {
							keep = false
							break
						}
					}
					if keep {
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
			return newListOwned(output)
		}
		// Not usable: the nested loop below answers, errors and all. Leave the
		// binders as they were (the loop sets every one it reads).
		frame[b1], frame["_1"], frame["_"] = nil, nil, nil
		frame[b2], frame["_2"] = nil, nil
		frame[lowerB1] = nil
		frame[lowerB2] = nil
	}

	// The right rows' alias records are built once, not once per (left,
	// right) pair, and the binder's lower-cased spelling once, not per pair.
	// Only for a predicate that cannot write: an assignment into Y acts on the
	// alias record, and a shared record would carry it from one pair to the next.
	var rightRows []*Value
	hoistRight := subtreeIsPure(predicate)
	if hoistRight {
		rightRows = make([]*Value, len(rightEnts))
		for i, rEntry := range rightEnts {
			rightRows[i] = ensureRowTableAlias(rEntry.Val, b2)
		}
	}

	for _, lEntry := range leftEnts {
		left := ensureRowTableAlias(lEntry.Val, b1)
		setLeft(left)

		matched := false
		for ri, rEntry := range rightEnts {
			var right *Value
			if hoistRight {
				right = rightRows[ri]
			} else {
				right = ensureRowTableAlias(rEntry.Val, b2)
			}
			setRight(right)

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

	return newListOwned(output)
}

func init() {
	Define(&Spec{
		Name:  "ALL",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			all := true
			condWalk(args, ctx, func(keep bool, _ string, _ *Value) bool {
				all = keep
				return !keep
			}, nil)
			return NewBool(all)
		},
	})

	Define(&Spec{
		Name:  "ANY",
		Min:   2,
		Max:   3,
		Lazy:  true,
		Binds: true,
		Fn: func(args *Args, ctx *Context) *Value {
			found := false
			condWalk(args, ctx, func(keep bool, _ string, _ *Value) bool {
				found = keep
				return keep
			}, nil)
			return NewBool(found)
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
			return newListOwned(out)
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
			handed := ctx.joinPrefilter
			ctx.joinPrefilter = nil
			// The parent reads or copies what this FILTER keeps (nocopy.go): the kept
			// rows stay aliased, checked for depth as the copy would have been.
			noCopy := ctx.noCopy != nil && ctx.noCopy == args.call
			ctx.noCopy = nil

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
				var pre joinPrefilter
				if !blocked {
					pre.Stages = append(pre.Stages, joinStage{Binder: binder, Conjuncts: own, Above: 0})
				}
				if handed != nil && !blocked {
					pre.Stages = append(pre.Stages, handed.Stages...)
					pre.Above = handed.Above
					pre.Obligations = handed.Obligations
				}
				if handed != nil {
					pre.Deep = true
				} else {
					pre.Deep = written.keysUnobserved
				}
				if len(pre.Stages) > 0 {
					ctx.joinPrefilter = &pre
				}
			}

			var inVal *Value
			func() {
				defer func() {
					ctx.joinPrefilter = nil
				}()
				inVal = args.Val(0)
			}()

			report := ctx.joinPrefilterReport
			ctx.joinPrefilterReport = nil
			if report != nil && handed != nil {
				ctx.joinPrefilterReport = report
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
				condWalk(args, ctx, func(keep bool, key string, item *Value) bool {
					if keep {
						storage = append(storage, keepRow(item, noCopy, args.Pos()))
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
					return false
				}, overrideBody)
			} else {
				expectedIndex := 1
				condWalk(args, ctx, func(keep bool, key string, item *Value) bool {
					if keep {
						storage = append(storage, keepRow(item, noCopy, args.Pos()))
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
					return false
				}, overrideBody)
			}
			if needsCustomKeys {
				return NewListWithKeys(storage, keys)
			}
			return newListOwned(storage)
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
			return newTextOwned(strings.Join(parts, sep))
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
