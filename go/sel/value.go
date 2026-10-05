// The SEL value. See spec/SPEC.md §3.

package sel

import (
	"encoding/hex"
	"fmt"
	"math/big"
	"strconv"
	"strings"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
)

// Kind is the kind of a value: SEL has four (spec §3), and a number is text.
type Kind uint8

const (
	KindNone Kind = iota
	KindText
	KindBin
	KindBool
)

func (k Kind) String() string {
	switch k {
	case KindNone:
		return "NONE"
	case KindText:
		return "TEXT"
	case KindBin:
		return "BIN"
	case KindBool:
		return "BOOL"
	default:
		return "UNKNOWN"
	}
}

// Entry is one key and value of a record, in order.
type Entry struct {
	Key string
	Val *Value
}

type Value struct {
	kind    Kind
	boolVal bool
	strVal  string
	binVal  []byte
	decVal  *decimal.Dec

	// Derived caches. strVal and decVal above are fixed when a Value is built;
	// what a READ derives from them (the text of a number, the number in a
	// text) is published here through atomic pointers, so goroutines running
	// programs over one shared read-only context never write a plain field.
	// A duplicate compute is benign: both results are identical.
	strCache atomic.Pointer[string]
	decCache atomic.Pointer[decimal.Dec]

	// Fast path for shaped records and lists
	shape    *RecordShape
	storage  []*Value
	isList   bool
	listKeys []string
	// keyIdx maps each listKeys entry to its slot (first occurrence wins), built
	// lazily by listKeyPos for a keyed list of keyIndexMin or more children and
	// published through an atomic pointer so goroutines sharing a read-only value
	// never write a plain field. listKeys is never mutated in place, only
	// replaced or dropped, so a published index cannot go stale; it is cleared
	// together with listKeys.
	keyIdx atomic.Pointer[map[string]int]

	// Fallback for irregular records / duplicate-preserving join rows
	entries []Entry
	index   map[string]int
}

func (v *Value) Scalar() string {
	if v.kind == KindText && v.strVal == "" && v.decVal != nil {
		if p := v.strCache.Load(); p != nil {
			return *p
		}
		str := decimal.Format(v.decVal)
		v.strCache.Store(&str)
		return str
	}
	return v.strVal
}

func NewNone() *Value {
	return &Value{kind: KindNone}
}

func NewNull() *Value {
	return &Value{kind: KindNone, isList: false}
}

func NewText(s string) *Value {
	utf8.ValidateText(s, Pos{}, fail)
	return &Value{kind: KindText, strVal: s}
}

func newTextOwned(s string) *Value {
	return &Value{kind: KindText, strVal: s}
}

func NewBin(b []byte) *Value {
	cp := make([]byte, len(b))
	copy(cp, b)
	return &Value{kind: KindBin, binVal: cp}
}

func newBinOwned(b []byte) *Value {
	return &Value{kind: KindBin, binVal: b}
}

func NewBool(b bool) *Value {
	return &Value{kind: KindBool, boolVal: b}
}

// NewNum builds a number from the module's internal decimal type.
//
// Deprecated: *decimal.Dec cannot be named outside this module, so the only use
// NewNum ever had outside it was handing back what AsDecimal returned. Use
// NewDecimal and Value.Decimal, or NewText with the number's text.
func NewNum(d *decimal.Dec) *Value {
	return &Value{kind: KindText, decVal: d}
}

// Decimal is a number in SEL's decimal form: Digits × 10^-Scale, negative when
// Neg — the form the other hosts' Value.num({neg, digits, scale}) take. Scale is
// kept: 1.50 is {false, 150, 2}.
type Decimal struct {
	Neg    bool
	Digits *big.Int // a whole number, not negative
	Scale  int      // fraction digits, not negative
}

// NewDecimal builds a number from its decimal form. Nil or negative Digits, or a
// negative Scale, panics with a *SelError E_BAD_ARG; a number past the digit caps
// (more than MAX_INT_DIGITS integer or MAX_FRAC_DIGITS fraction digits) with
// E_RANGE. Digits is copied, and a negative zero is zero.
func NewDecimal(d Decimal) *Value {
	if d.Digits == nil || d.Digits.Sign() < 0 || d.Scale < 0 {
		fail("E_BAD_ARG", "not a decimal: Digits must be a whole number that is not negative, and Scale not negative", Pos{})
	}
	if d.Scale > limits.MAX_FRAC_DIGITS {
		fail("E_RANGE", fmt.Sprintf("number has more than %d fractional digits", limits.MAX_FRAC_DIGITS), Pos{})
	}
	dec := decimal.Guard(decimal.Make(d.Neg, d.Digits, int32(d.Scale)), utf8.Pos{}, fail)
	return &Value{kind: KindText, decVal: dec}
}

// Decimal reads the value as a number (as AsText reads it as text), in the
// decimal form; Digits is the caller's own copy. It panics with a *SelError
// E_NOT_NUM when the value is not a number.
func (v *Value) Decimal(pos Pos) Decimal {
	d := v.AsDecimal(pos)
	return Decimal{Neg: d.Neg, Digits: new(big.Int).Set(d.Digits), Scale: int(d.Scale)}
}

func NewInt(n int64) *Value {
	return &Value{kind: KindText, decVal: decimal.FromInt(n)}
}

func NewList(items []*Value) *Value {
	cp := make([]*Value, len(items))
	copy(cp, items)
	return &Value{kind: KindNone, isList: true, storage: cp}
}

func newListOwned(items []*Value) *Value {
	return &Value{kind: KindNone, isList: true, storage: items}
}

func NewListWithKeys(items []*Value, keys []string) *Value {
	// A public constructor: key and value counts that differ are a malformed call
	// (SPEC §8), not something to truncate or index past.
	if len(items) != len(keys) {
		fail("E_BAD_ARG", "list keys and values differ in count", Pos{})
	}
	cp := make([]*Value, len(items))
	copy(cp, items)
	k := make([]string, len(keys))
	copy(k, keys)
	return &Value{kind: KindNone, isList: true, storage: cp, listKeys: k}
}

// NewShapedRecord builds a record of the shape's keys holding these values, in
// order; the slice is copied, the values are not (as NewList). A nil shape, a
// count that is not the shape's or a nil value panics with a *SelError
// E_BAD_ARG.
func NewShapedRecord(shape *RecordShape, values []*Value) *Value {
	if shape == nil || len(values) != len(shape.keys) {
		fail("E_BAD_ARG", "a shaped record needs one value per key of its shape", Pos{})
	}
	for _, v := range values {
		if v == nil {
			fail("E_BAD_ARG", "a record value is nil", Pos{})
		}
	}
	cp := make([]*Value, len(values))
	copy(cp, values)
	return newShapedRecord(shape, cp)
}

// newShapedRecord takes values as they are: the builtins' own constructor.
func newShapedRecord(shape *RecordShape, values []*Value) *Value {
	return &Value{kind: KindNone, shape: shape, storage: values}
}

// NewRecordFromEntries builds a record from key-value pairs in order; a key that
// repeats keeps its first position and its last value, as Set does. A key that
// is not valid UTF-8 panics with a *SelError E_UTF8 (as in Set), a nil value with
// E_BAD_ARG.
func NewRecordFromEntries(entries []Entry) *Value {
	for _, e := range entries {
		utf8.ValidateText(e.Key, Pos{}, fail)
		if e.Val == nil {
			fail("E_BAD_ARG", "a record value is nil", Pos{})
		}
	}
	return newRecordFromEntries(entries)
}

// newRecordFromEntries is NewRecordFromEntries without the checks, for entries
// the builtins built.
func newRecordFromEntries(entries []Entry) *Value {
	if len(entries) > 0 {
		keys := make([]string, len(entries))
		vals := make([]*Value, len(entries))
		for i, e := range entries {
			keys[i] = e.Key
			vals[i] = e.Val
		}
		if shape := uniqueRecordShape(keys); shape != nil {
			return newShapedRecord(shape, vals)
		}
	}
	v := NewNone()
	for _, e := range entries {
		v.Set(e.Key, e.Val)
	}
	return v
}

// Kind is the value's kind: KindNone (a record, a list, or nothing), KindText,
// KindBin or KindBool. A number is text.
func (v *Value) Kind() Kind { return v.kind }

// Predicates

// IsNone reports a value of kind NONE: a record, a list, or nothing.
func (v *Value) IsNone() bool { return v.kind == KindNone }
func (v *Value) IsNull() bool { return v.kind == KindNone && v.Size() == 0 && !v.isList }
func (v *Value) IsVacuous() bool {
	if v.kind == KindNone && v.Size() == 0 {
		return true
	}
	if v.kind == KindText && v.Size() == 0 {
		s := v.Scalar()
		return strings.Trim(s, " \t\r\n") == ""
	}
	return false
}
func (v *Value) IsText() bool { return v.kind == KindText }
func (v *Value) IsBin() bool  { return v.kind == KindBin }
func (v *Value) IsBool() bool { return v.kind == KindBool }
func (v *Value) IsList() bool { return v.isList }

// Children

// Size is the number of children.
func (v *Value) Size() int {
	if v.storage != nil {
		return len(v.storage)
	}
	return len(v.entries)
}

// keyIndexMin is the keyed-list size from which Get/Has/Set use a hash index
// instead of scanning listKeys. Below it the scan is cheaper than
// building the map.
const keyIndexMin = 16

// listKeyPos is the slot of key in a keyed list, or -1. Linear for a short list,
// indexed (built once, lazily, first occurrence of a duplicate wins) otherwise.
func (v *Value) listKeyPos(key string) int {
	if len(v.listKeys) < keyIndexMin {
		for i, k := range v.listKeys {
			if k == key {
				return i
			}
		}
		return -1
	}
	p := v.keyIdx.Load()
	if p == nil {
		m := make(map[string]int, len(v.listKeys))
		for i, k := range v.listKeys {
			if _, dup := m[k]; !dup {
				m[k] = i
			}
		}
		v.keyIdx.Store(&m)
		p = &m
	}
	if i, ok := (*p)[key]; ok {
		return i
	}
	return -1
}

func (v *Value) Has(key string) bool {
	if v.shape != nil {
		_, ok := v.shape.keyMap[key]
		return ok
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			return v.listKeyPos(key) >= 0
		}
		return parseListSlot(key, len(v.storage)) >= 0
	}
	if v.index != nil {
		_, ok := v.index[key]
		return ok
	}
	for _, e := range v.entries {
		if e.Key == key {
			return true
		}
	}
	return false
}

func (v *Value) Get(key string) *Value {
	if v.shape != nil {
		if idx, ok := v.shape.keyMap[key]; ok {
			return v.storage[idx]
		}
		return nil
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			if i := v.listKeyPos(key); i >= 0 {
				return v.storage[i]
			}
			return nil
		}
		idx := parseListSlot(key, len(v.storage))
		if idx >= 0 {
			return v.storage[idx]
		}
		return nil
	}
	if v.index != nil {
		if idx, ok := v.index[key]; ok {
			return v.entries[idx].Val
		}
		return nil
	}
	for _, e := range v.entries {
		if e.Key == key {
			return e.Val
		}
	}
	return nil
}

func (v *Value) Set(key string, val *Value) *Value {
	utf8.ValidateText(key, Pos{}, fail)

	if v.shape != nil {
		if idx, ok := v.shape.keyMap[key]; ok {
			v.storage[idx] = val
			return v
		}
		entries := v.Entries()
		v.shape = nil
		v.storage = nil
		v.entries = entries
		v.rebuildIndex()
	} else if v.isList && v.storage != nil {
		if v.listKeys != nil {
			if i := v.listKeyPos(key); i >= 0 {
				v.storage[i] = val
				return v
			}
		} else {
			idx := parseListSlot(key, len(v.storage))
			if idx >= 0 {
				v.storage[idx] = val
				return v
			}
		}
		entries := v.Entries()
		v.storage = nil
		v.listKeys = nil
		v.keyIdx.Store(nil)
		v.entries = entries
		v.rebuildIndex()
	}

	if v.index != nil {
		if idx, ok := v.index[key]; ok {
			v.entries[idx].Val = val
			return v
		}
		v.index[key] = len(v.entries)
		v.entries = append(v.entries, Entry{Key: key, Val: val})
		return v
	}

	for i := range v.entries {
		if v.entries[i].Key == key {
			v.entries[i].Val = val
			return v
		}
	}
	v.entries = append(v.entries, Entry{Key: key, Val: val})
	if len(v.entries) >= 16 {
		v.rebuildIndex()
	}
	return v
}

func (v *Value) rebuildIndex() {
	v.index = make(map[string]int, len(v.entries))
	for i, e := range v.entries {
		v.index[e.Key] = i
	}
}

// denseEntries is the entry list of a positional list: key i+1 for child i. The
// keys of a list past 99 children are strconv.Itoa strings, one allocation each;
// here they are written once into one blob and sliced out of it, so the
// whole list costs one string allocation. Identical keys, identical order.
func denseEntries(vals []*Value) []Entry {
	n := len(vals)
	out := make([]Entry, n)
	if n <= 99 {
		for i, v := range vals {
			out[i] = Entry{Key: strconv.Itoa(i + 1), Val: v}
		}
		return out
	}
	total := 9 + 2*90
	for d, lo := 3, 100; lo <= n; d, lo = d+1, lo*10 {
		hi := lo*10 - 1
		if hi > n {
			hi = n
		}
		total += d * (hi - lo + 1)
	}
	var sb strings.Builder
	sb.Grow(total)
	var buf [20]byte
	ends := make([]int, n)
	for i := 0; i < n; i++ {
		sb.Write(strconv.AppendInt(buf[:0], int64(i+1), 10))
		ends[i] = sb.Len()
	}
	blob := sb.String()
	start := 0
	for i, v := range vals {
		out[i] = Entry{Key: blob[start:ends[i]], Val: v}
		start = ends[i]
	}
	return out
}

// denseKeys is denseEntries' keys alone.
func denseKeys(n int) []string {
	es := denseEntries(make([]*Value, n))
	res := make([]string, n)
	for i := range es {
		res[i] = es[i].Key
	}
	return res
}

func (v *Value) Keys() []string {
	if v.shape != nil {
		res := make([]string, len(v.shape.keys))
		copy(res, v.shape.keys)
		return res
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			res := make([]string, len(v.listKeys))
			copy(res, v.listKeys)
			return res
		}
		return denseKeys(len(v.storage))
	}
	res := make([]string, len(v.entries))
	for i, e := range v.entries {
		res[i] = e.Key
	}
	return res
}

// valuesView is Values without the copy when the children are kept in a slice:
// for a caller that only reads it, and keeps it no longer than the value.
func (v *Value) valuesView() []*Value {
	if v.storage != nil {
		return v.storage
	}
	return v.Values()
}

func (v *Value) Values() []*Value {
	if v.storage != nil {
		res := make([]*Value, len(v.storage))
		copy(res, v.storage)
		return res
	}
	res := make([]*Value, len(v.entries))
	for i, e := range v.entries {
		res[i] = e.Val
	}
	return res
}

func (v *Value) Entries() []Entry {
	if v.shape != nil {
		res := make([]Entry, len(v.shape.keys))
		for i, k := range v.shape.keys {
			res[i] = Entry{Key: k, Val: v.storage[i]}
		}
		return res
	}
	if v.isList && v.storage != nil {
		if v.listKeys == nil {
			return denseEntries(v.storage)
		}
		res := make([]Entry, len(v.storage))
		for i := range v.storage {
			res[i] = Entry{Key: v.listKeys[i], Val: v.storage[i]}
		}
		return res
	}
	res := make([]Entry, len(v.entries))
	copy(res, v.entries)
	return res
}

// Scalar Context (§3.2)

// ScalarSource is the value that stands for v in scalar context (§3.2): v when
// it is not NONE, otherwise its first child, followed down; E_NULL for NULL
// and E_NO_SCALAR for a childless NONE.
func (v *Value) ScalarSource(pos Pos) *Value {
	if v.kind != KindNone {
		return v
	}
	cur := v
	guard := 0
	for cur.kind == KindNone {
		if cur.IsNull() {
			fail("E_NULL", "value is NULL", pos)
		}
		if cur.Size() == 0 {
			fail("E_NO_SCALAR", "value has no scalar and no children", pos)
		}
		if cur.storage != nil {
			cur = cur.storage[0]
		} else {
			cur = cur.entries[0].Val
		}
		guard++
		if guard > 1000 {
			fail("E_DEPTH", "scalar context nested too deeply", pos)
		}
	}
	return cur
}

func (v *Value) AsText(pos Pos) string {
	s := v.ScalarSource(pos)
	if s.kind == KindText {
		return s.Scalar()
	}
	if s.kind == KindBin {
		fail("E_NOT_TEXT", "expected text, got binary (use FROM_UTF8)", pos)
	}
	fail("E_NOT_TEXT", "expected text, got boolean", pos)
	return ""
}

func (v *Value) AsBytes(pos Pos) []byte {
	s := v.ScalarSource(pos)
	if s.kind == KindBin {
		return s.binVal
	}
	if s.kind == KindText {
		return []byte(s.Scalar())
	}
	fail("E_NOT_BIN", "expected binary or text, got boolean", pos)
	return nil
}

func (v *Value) AsBool(pos Pos) bool {
	s := v.ScalarSource(pos)
	if s.kind == KindBool {
		return s.boolVal
	}
	fail("E_NOT_BOOL", "expected a boolean — SEL has no truthiness", pos)
	return false
}

// AsDecimal reads the value as a number, in the module's internal decimal type.
//
// Deprecated: *decimal.Dec cannot be named outside this module; use
// Value.Decimal.
func (v *Value) AsDecimal(pos Pos) *decimal.Dec {
	s := v.ScalarSource(pos)
	if s.kind != KindText {
		fail("E_NOT_NUM", fmt.Sprintf("expected a number, got %s", strings.ToLower(s.kind.String())), pos)
	}
	if s.decVal != nil {
		return s.decVal
	}
	if d := s.decCache.Load(); d != nil {
		return d
	}
	d := decimal.Parse(s.strVal, utf8.Pos(pos), fail)
	if d == nil {
		fail("E_NOT_NUM", fmt.Sprintf("not a number: %q", s.strVal), pos)
	}
	s.decCache.Store(d)
	return d
}

// notNumeric is what LooksNumeric's parse aborts with; nothing else escapes it.
type notNumeric struct{}

func (v *Value) LooksNumeric() bool {
	if v.kind == KindNone && v.Size() == 0 {
		return false
	}
	defer func() {
		if r := recover(); r != nil {
			if _, ok := r.(notNumeric); !ok && !isSelPanic(r) {
				panic(r)
			}
		}
	}()
	s := v.ScalarSource(Pos{})
	if s.kind != KindText {
		return false
	}
	if s.decVal != nil || s.decCache.Load() != nil {
		return true
	}
	d := decimal.Parse(s.strVal, utf8.Pos{}, func(code, msg string, pos utf8.Pos) {
		panic(notNumeric{})
	})
	if d != nil {
		s.decCache.Store(d)
		return true
	}
	return false
}

// Cloning (§3.4, §5.7)

// Clone is a deep copy (§3.4): what an assignment stores.
func (v *Value) Clone() *Value {
	return v.CloneAt(1, Pos{})
}

func (v *Value) CloneAt(depth int, pos Pos) *Value {
	if depth > maxDepth {
		fail("E_DEPTH", "value nested too deeply", pos)
	}
	out := &Value{
		kind:    v.kind,
		boolVal: v.boolVal,
		strVal:  v.strVal,
		isList:  v.isList,
		decVal:  v.decVal,
	}
	if v.binVal != nil {
		out.binVal = make([]byte, len(v.binVal))
		copy(out.binVal, v.binVal)
	}
	if v.shape != nil {
		out.shape = v.shape
		out.storage = make([]*Value, len(v.storage))
		for i, child := range v.storage {
			out.storage[i] = child.CloneAt(depth+1, pos)
		}
	} else if v.storage != nil {
		out.storage = make([]*Value, len(v.storage))
		for i, child := range v.storage {
			out.storage[i] = child.CloneAt(depth+1, pos)
		}
		if v.listKeys != nil {
			out.listKeys = make([]string, len(v.listKeys))
			copy(out.listKeys, v.listKeys)
		}
	} else if len(v.entries) > 0 {
		out.entries = make([]Entry, len(v.entries))
		for i, e := range v.entries {
			out.entries[i] = Entry{Key: e.Key, Val: e.Val.CloneAt(depth+1, pos)}
		}
		if v.index != nil {
			out.rebuildIndex()
		}
	}
	return out
}

// Equality (§5.4)

// Eql is structural equality (§5.4): the same kind, the same scalar, the same
// children in the same order. Text is compared as written: "5.00" is not "5".
func (v *Value) Eql(other *Value, pos Pos) bool {
	return v.EqlAt(other, 1, pos)
}

func (v *Value) EqlAt(other *Value, depth int, pos Pos) bool {
	if depth > maxDepth {
		fail("E_DEPTH", "value nested too deeply", pos)
	}
	if v.kind != other.kind {
		return false
	}
	switch v.kind {
	case KindText:
		if v.strVal == "" && other.strVal == "" && v.decVal != nil && other.decVal != nil {
			if v.decVal.Neg != other.decVal.Neg || v.decVal.Scale != other.decVal.Scale || v.decVal.Digits.Cmp(other.decVal.Digits) != 0 {
				return false
			}
		} else if v.Scalar() != other.Scalar() {
			return false
		}
	case KindBin:
		if utf8.BytesCompare(v.binVal, other.binVal) != 0 {
			return false
		}
	case KindBool:
		if v.boolVal != other.boolVal {
			return false
		}
	}
	if v.Size() != other.Size() {
		return false
	}
	if v.Size() == 0 {
		return true
	}
	if v.isList && other.isList && v.storage != nil && other.storage != nil && v.listKeys == nil && other.listKeys == nil {
		for i := range v.storage {
			if !v.storage[i].EqlAt(other.storage[i], depth+1, pos) {
				return false
			}
		}
		return true
	}
	a, b := v.Entries(), other.Entries()
	for i := range a {
		if a[i].Key != b[i].Key { // key order is normative
			return false
		}
		if !a[i].Val.EqlAt(b[i].Val, depth+1, pos) {
			return false
		}
	}
	return true
}

func quoteDump(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, ch := range s {
		switch ch {
		case '\\':
			b.WriteString(`\\`)
		case '"':
			b.WriteString(`\"`)
		case '\n':
			b.WriteString(`\n`)
		case '\t':
			b.WriteString(`\t`)
		case '\r':
			b.WriteString(`\r`)
		default:
			if ch < 0x20 {
				b.WriteString(fmt.Sprintf(`\u%04x`, ch))
			} else {
				b.WriteRune(ch)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// Dump (conformance/README.md)
func (v *Value) Dump() string {
	return v.DumpAt(1)
}

func (v *Value) DumpAt(depth int) string {
	if depth > maxDepth {
		fail("E_DEPTH", "value nested too deeply", Pos{})
	}
	var s string
	switch v.kind {
	case KindNone:
		s = "-"
	case KindText:
		s = "t" + quoteDump(v.Scalar())
	case KindBin:
		s = "b" + hex.EncodeToString(v.binVal)
	case KindBool:
		if v.boolVal {
			s = "TRUE"
		} else {
			s = "FALSE"
		}
	}
	if v.Size() == 0 {
		return s
	}
	entries := v.Entries()
	parts := make([]string, len(entries))
	for i, e := range entries {
		parts[i] = fmt.Sprintf("%s=%s", quoteDump(e.Key), e.Val.DumpAt(depth+1))
	}
	return s + "{" + strings.Join(parts, ", ") + "}"
}

func fnvHash(s string) uint64 {
	h := uint64(14695981039346656037)
	for i := 0; i < len(s); i++ {
		h ^= uint64(s[i])
		h *= 1099511628211
	}
	return h
}

func (v *Value) StructuralHash() uint64 {
	return v.structuralHashAt(1)
}

func (v *Value) structuralHashAt(depth int) uint64 {
	if depth > maxDepth {
		fail("E_DEPTH", "value nested too deeply", Pos{})
	}
	var h uint64
	switch v.kind {
	case KindText:
		h = fnvHash(v.Scalar()) ^ 1000003
	case KindBool:
		if v.boolVal {
			h = 12345
		} else {
			h = 67890
		}
	case KindBin:
		h = fnvHash(string(v.binVal)) ^ 2000003
	default:
		h = 0
	}
	if v.Size() == 0 {
		return h
	}
	// The children are read in place: Entries() would build a slice of (key, value)
	// pairs, and for a dense list a string per key, only to hash them. The
	// combination is the same for every representation, so equal values still hash
	// equal however they were built.
	switch {
	case v.shape != nil:
		kh := v.shape.hashes()
		for i, c := range v.storage {
			h = (h * 1000003) ^ kh[i] ^ c.structuralHashAt(depth+1)
		}
	case v.isList && v.storage != nil && v.listKeys == nil:
		var buf [20]byte
		for i, c := range v.storage {
			key := strconv.AppendInt(buf[:0], int64(i+1), 10)
			kh := uint64(14695981039346656037)
			for _, b := range key {
				kh ^= uint64(b)
				kh *= 1099511628211
			}
			h = (h * 1000003) ^ kh ^ c.structuralHashAt(depth+1)
		}
	case v.isList && v.storage != nil:
		for i, c := range v.storage {
			h = (h * 1000003) ^ fnvHash(v.listKeys[i]) ^ c.structuralHashAt(depth+1)
		}
	default:
		for _, e := range v.entries {
			h = (h * 1000003) ^ fnvHash(e.Key) ^ e.Val.structuralHashAt(depth+1)
		}
	}
	return h
}

func (v *Value) Elements() []Entry {
	if v.shape != nil {
		out := make([]Entry, len(v.shape.keys))
		for i, k := range v.shape.keys {
			out[i] = Entry{Key: k, Val: v.storage[i]}
		}
		return out
	}
	if v.isList && v.storage != nil {
		if v.listKeys == nil {
			return denseEntries(v.storage)
		}
		out := make([]Entry, len(v.storage))
		for i, item := range v.storage {
			out[i] = Entry{Key: v.listKeys[i], Val: item}
		}
		return out
	}
	if len(v.entries) > 0 {
		// A snapshot, like the two shaped forms above: a body that assigns
		// into the record it is iterating must not change what is visited.
		out := make([]Entry, len(v.entries))
		copy(out, v.entries)
		return out
	}
	if v.kind != KindNone {
		return []Entry{{Key: "1", Val: v}}
	}
	return nil
}
