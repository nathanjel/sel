// The SEL value. See spec/SPEC.md §3.

package sel

import (
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/decimal"
	"github.com/nathanjel/sel/go/internal/utf8"
)

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

type Entry struct {
	Key string
	Val *Value
}

type Value struct {
	Kind    Kind
	boolVal bool
	strVal  string
	binVal  []byte
	decVal  *decimal.Dec

	// Derived caches. strVal and decVal above are fixed when a Value is built;
	// what a READ derives from them (the text of a number, the number in a
	// text) is published here through atomic pointers, so goroutines running
	// programs over one shared read-only context never write a plain field
	// (GO-C6). A duplicate compute is benign: both results are identical.
	strCache atomic.Pointer[string]
	decCache atomic.Pointer[decimal.Dec]

	// Fast path for shaped records and lists
	shape    *RecordShape
	storage  []*Value
	isList   bool
	listKeys []string

	// Fallback for irregular records / duplicate-preserving join rows
	entries []Entry
	index   map[string]int
}

func (v *Value) Scalar() string {
	if v.Kind == KindText && v.strVal == "" && v.decVal != nil {
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
	return &Value{Kind: KindNone}
}

func NewNull() *Value {
	return &Value{Kind: KindNone, isList: false}
}

func NewText(s string) *Value {
	utf8.ValidateText(s, Pos{}, fail)
	return &Value{Kind: KindText, strVal: s}
}

func NewTextOwned(s string) *Value {
	return &Value{Kind: KindText, strVal: s}
}

func NewBin(b []byte) *Value {
	cp := make([]byte, len(b))
	copy(cp, b)
	return &Value{Kind: KindBin, binVal: cp}
}

func NewBinOwned(b []byte) *Value {
	return &Value{Kind: KindBin, binVal: b}
}

func NewBool(b bool) *Value {
	return &Value{Kind: KindBool, boolVal: b}
}

func NewNum(d *decimal.Dec) *Value {
	return &Value{Kind: KindText, decVal: d}
}

func NewNumExact(s string, d *decimal.Dec) *Value {
	return &Value{Kind: KindText, strVal: s, decVal: d}
}

func NewInt(n int64) *Value {
	return &Value{Kind: KindText, decVal: decimal.FromInt(n)}
}

func NewList(items []*Value) *Value {
	cp := make([]*Value, len(items))
	copy(cp, items)
	return &Value{Kind: KindNone, isList: true, storage: cp}
}

func NewListOwned(items []*Value) *Value {
	return &Value{Kind: KindNone, isList: true, storage: items}
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
	return &Value{Kind: KindNone, isList: true, storage: cp, listKeys: k}
}

func NewShapedRecord(shape *RecordShape, values []*Value) *Value {
	return &Value{Kind: KindNone, shape: shape, storage: values}
}

func NewRecordFromEntries(entries []Entry) *Value {
	if len(entries) > 0 {
		keys := make([]string, len(entries))
		vals := make([]*Value, len(entries))
		for i, e := range entries {
			keys[i] = e.Key
			vals[i] = e.Val
		}
		if shape := UniqueRecordShape(keys); shape != nil {
			return NewShapedRecord(shape, vals)
		}
	}
	v := NewNone()
	for _, e := range entries {
		v.Set(e.Key, e.Val)
	}
	return v
}

// Predicates
func (v *Value) IsNone() bool { return v.Kind == KindNone }
func (v *Value) IsNull() bool { return v.Kind == KindNone && v.Size() == 0 && !v.isList }
func (v *Value) IsVacuous() bool {
	if v.Kind == KindNone && v.Size() == 0 {
		return true
	}
	if v.Kind == KindText && v.Size() == 0 {
		s := v.Scalar()
		return strings.Trim(s, " \t\r\n") == ""
	}
	return false
}
func (v *Value) IsText() bool { return v.Kind == KindText }
func (v *Value) IsBin() bool  { return v.Kind == KindBin }
func (v *Value) IsBool() bool { return v.Kind == KindBool }
func (v *Value) IsList() bool { return v.isList }

// Children
func (v *Value) Size() int {
	if v.storage != nil {
		return len(v.storage)
	}
	return len(v.entries)
}

func (v *Value) Has(key string) bool {
	if v.shape != nil {
		_, ok := v.shape.KeyMap[key]
		return ok
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			for _, k := range v.listKeys {
				if k == key {
					return true
				}
			}
			return false
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
		if idx, ok := v.shape.KeyMap[key]; ok {
			return v.storage[idx]
		}
		return nil
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			for i, k := range v.listKeys {
				if k == key {
					return v.storage[i]
				}
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
		if idx, ok := v.shape.KeyMap[key]; ok {
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
			for i, k := range v.listKeys {
				if k == key {
					v.storage[i] = val
					return v
				}
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

func (v *Value) Keys() []string {
	if v.shape != nil {
		res := make([]string, len(v.shape.Keys))
		copy(res, v.shape.Keys)
		return res
	}
	if v.isList && v.storage != nil {
		if v.listKeys != nil {
			res := make([]string, len(v.listKeys))
			copy(res, v.listKeys)
			return res
		}
		res := make([]string, len(v.storage))
		for i := range v.storage {
			res[i] = strconv.Itoa(i + 1)
		}
		return res
	}
	res := make([]string, len(v.entries))
	for i, e := range v.entries {
		res[i] = e.Key
	}
	return res
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
		res := make([]Entry, len(v.shape.Keys))
		for i, k := range v.shape.Keys {
			res[i] = Entry{Key: k, Val: v.storage[i]}
		}
		return res
	}
	if v.isList && v.storage != nil {
		res := make([]Entry, len(v.storage))
		if v.listKeys != nil {
			for i := range v.storage {
				res[i] = Entry{Key: v.listKeys[i], Val: v.storage[i]}
			}
		} else {
			for i := range v.storage {
				res[i] = Entry{Key: strconv.Itoa(i + 1), Val: v.storage[i]}
			}
		}
		return res
	}
	res := make([]Entry, len(v.entries))
	copy(res, v.entries)
	return res
}

// Scalar Context (§3.2)
func (v *Value) ScalarSource(pos Pos) *Value {
	if v.Kind != KindNone {
		return v
	}
	cur := v
	guard := 0
	for cur.Kind == KindNone {
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
	if s.Kind == KindText {
		return s.Scalar()
	}
	if s.Kind == KindBin {
		fail("E_NOT_TEXT", "expected text, got binary (use FROM_UTF8)", pos)
	}
	fail("E_NOT_TEXT", "expected text, got boolean", pos)
	return ""
}

func (v *Value) AsBytes(pos Pos) []byte {
	s := v.ScalarSource(pos)
	if s.Kind == KindBin {
		return s.binVal
	}
	if s.Kind == KindText {
		return []byte(s.Scalar())
	}
	fail("E_NOT_BIN", "expected binary or text, got boolean", pos)
	return nil
}

func (v *Value) AsBool(pos Pos) bool {
	s := v.ScalarSource(pos)
	if s.Kind == KindBool {
		return s.boolVal
	}
	fail("E_NOT_BOOL", "expected a boolean — SEL has no truthiness", pos)
	return false
}

func (v *Value) AsDecimal(pos Pos) *decimal.Dec {
	s := v.ScalarSource(pos)
	if s.Kind != KindText {
		fail("E_NOT_NUM", fmt.Sprintf("expected a number, got %s", strings.ToLower(s.Kind.String())), pos)
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
	if v.Kind == KindNone && v.Size() == 0 {
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
	if s.Kind != KindText {
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
func (v *Value) Clone() *Value {
	return v.CloneAt(1, Pos{})
}

func (v *Value) CloneAt(depth int, pos Pos) *Value {
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", pos)
	}
	out := &Value{
		Kind:    v.Kind,
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
func (v *Value) Eql(other *Value, pos Pos) bool {
	return v.EqlAt(other, 1, pos)
}

func (v *Value) EqlAt(other *Value, depth int, pos Pos) bool {
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", pos)
	}
	if v.Kind != other.Kind {
		return false
	}
	switch v.Kind {
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
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", Pos{})
	}
	var s string
	switch v.Kind {
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
	if depth > MAX_DEPTH {
		fail("E_DEPTH", "value nested too deeply", Pos{})
	}
	var h uint64
	switch v.Kind {
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
	for _, e := range v.Entries() {
		kh := fnvHash(e.Key)
		ch := e.Val.structuralHashAt(depth + 1)
		h = (h * 1000003) ^ kh ^ ch
	}
	return h
}

func (v *Value) Elements() []Entry {
	if v.shape != nil {
		out := make([]Entry, len(v.shape.Keys))
		for i, k := range v.shape.Keys {
			out[i] = Entry{Key: k, Val: v.storage[i]}
		}
		return out
	}
	if v.isList && v.storage != nil {
		out := make([]Entry, len(v.storage))
		if v.listKeys != nil {
			for i, item := range v.storage {
				out[i] = Entry{Key: v.listKeys[i], Val: item}
			}
		} else {
			for i, item := range v.storage {
				out[i] = Entry{Key: strconv.Itoa(i + 1), Val: item}
			}
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
	if v.Kind != KindNone {
		return []Entry{{Key: "1", Val: v}}
	}
	return nil
}

