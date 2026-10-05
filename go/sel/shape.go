package sel

import (
	"strconv"
	"sync"
	"sync/atomic"

	"github.com/nathanjel/sel/go/internal/utf8"
)

// RecordShape is the key list of a record, shared by every record built with it
// (InternRecordShape, NewShapedRecord). It is opaque and immutable.
type RecordShape struct {
	keys   []string
	keyMap map[string]int
	size   int

	// keyHashes[i] is fnvHash(Keys[i]), built on the first structural hash of a
	// record of this shape and then shared by every such record.
	keyHashes atomic.Pointer[[]uint64]
}

// hashes returns the hash of each key, computed once per shape.
func (s *RecordShape) hashes() []uint64 {
	if p := s.keyHashes.Load(); p != nil {
		return *p
	}
	hs := make([]uint64, len(s.keys))
	for i, k := range s.keys {
		hs[i] = fnvHash(k)
	}
	s.keyHashes.CompareAndSwap(nil, &hs)
	return *s.keyHashes.Load()
}

const (
	shapeCacheEntries = 256
	shapeCacheMaxKeys = 256
	shapeCacheMaxChar = 16384
)

var (
	shapeMu    sync.RWMutex
	shapeCache = make(map[string]*RecordShape)
)

func newRecordShape(keys []string) *RecordShape {
	k := make([]string, len(keys))
	copy(k, keys)
	km := make(map[string]int, len(keys))
	for i, key := range k {
		km[key] = i
	}
	return &RecordShape{
		keys:   k,
		keyMap: km,
		size:   len(k),
	}
}

// shapeSignature appends the length-prefixed signature of keys to buf. Length
// prefixes preserve boundaries even when keys contain NUL or colons.
func shapeSignature(buf []byte, keys []string) []byte {
	for _, key := range keys {
		buf = strconv.AppendInt(buf, int64(len(key)), 10)
		buf = append(buf, ':')
		buf = append(buf, key...)
	}
	return buf
}

// cachedShape looks keys up without allocating: the signature is built on the
// stack, and a map lookup keyed by string(bytes) does not copy the bytes.
func cachedShape(sig []byte) *RecordShape {
	shapeMu.RLock()
	s := shapeCache[string(sig)]
	shapeMu.RUnlock()
	return s
}

// InternRecordShape returns the shape of a record with these keys in this order,
// for NewShapedRecord: build it once, then every row of that shape shares it.
// The keys must be distinct and valid UTF-8; otherwise it panics with a
// *SelError, E_BAD_ARG or E_UTF8. Interning is cached, so a second call with the same keys
// returns the same shape.
func InternRecordShape(keys []string) *RecordShape {
	for _, k := range keys {
		utf8.ValidateText(k, Pos{}, fail)
	}
	s := uniqueRecordShape(keys)
	if s == nil {
		fail("E_BAD_ARG", "a record shape's keys must be distinct", Pos{})
	}
	return s
}

// internRecordShape is the shape of keys, built once and cached; keys are not
// checked (a repeated key builds a shape uniqueRecordShape will not hand out).
func internRecordShape(keys []string) *RecordShape {
	var stack [192]byte
	sig := shapeSignature(stack[:0], keys)
	if s := cachedShape(sig); s != nil {
		return s
	}

	s := newRecordShape(keys)

	totalLen := 0
	for _, key := range keys {
		totalLen += len(key)
	}

	if len(keys) <= shapeCacheMaxKeys && totalLen <= shapeCacheMaxChar {
		shapeMu.Lock()
		if len(shapeCache) >= shapeCacheEntries {
			shapeCache = make(map[string]*RecordShape)
		}
		shapeCache[string(sig)] = s
		shapeMu.Unlock()
	}
	return s
}

// uniqueRecordShape is the shape of keys, or nil when a key repeats. The cache is
// asked first: a hit whose map has as many entries as it has keys was
// built from distinct keys, which answers the uniqueness question without the
// per-call set. A miss checks for a repeat — pairwise for a short key list, with a
// set beyond that — and only then builds and caches the shape.
func uniqueRecordShape(keys []string) *RecordShape {
	var stack [192]byte
	if s := cachedShape(shapeSignature(stack[:0], keys)); s != nil && len(s.keyMap) == len(s.keys) {
		return s
	}
	if len(keys) <= 8 {
		for i := 1; i < len(keys); i++ {
			for j := 0; j < i; j++ {
				if keys[i] == keys[j] {
					return nil
				}
			}
		}
		return internRecordShape(keys)
	}
	seen := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		if _, exists := seen[k]; exists {
			return nil
		}
		seen[k] = struct{}{}
	}
	return internRecordShape(keys)
}

func parseListSlot(key string, length int) int {
	if len(key) == 0 || len(key) > 9 || key[0] < '1' || key[0] > '9' {
		return -1
	}
	val := int(key[0] - '0')
	for i := 1; i < len(key); i++ {
		c := key[i]
		if c < '0' || c > '9' {
			return -1
		}
		val = val*10 + int(c-'0')
	}
	if val <= length {
		return val - 1
	}
	return -1
}
