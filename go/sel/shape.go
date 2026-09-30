package sel

import (
	"strconv"
	"sync"
	"sync/atomic"
)

type RecordShape struct {
	Keys   []string
	KeyMap map[string]int
	Size   int

	// keyHashes[i] is fnvHash(Keys[i]), built on the first structural hash of a
	// record of this shape and then shared by every such record (GO-P26).
	keyHashes atomic.Pointer[[]uint64]
}

// KeyHashes returns the hash of each key, computed once per shape.
func (s *RecordShape) KeyHashes() []uint64 {
	if p := s.keyHashes.Load(); p != nil {
		return *p
	}
	hs := make([]uint64, len(s.Keys))
	for i, k := range s.Keys {
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

func NewRecordShape(keys []string) *RecordShape {
	k := make([]string, len(keys))
	copy(k, keys)
	km := make(map[string]int, len(keys))
	for i, key := range k {
		km[key] = i
	}
	return &RecordShape{
		Keys:   k,
		KeyMap: km,
		Size:   len(k),
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

func InternRecordShape(keys []string) *RecordShape {
	var stack [192]byte
	sig := shapeSignature(stack[:0], keys)
	if s := cachedShape(sig); s != nil {
		return s
	}

	s := NewRecordShape(keys)

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

// UniqueRecordShape is the shape of keys, or nil when a key repeats. The cache is
// asked first (GO-P14): a hit whose map has as many entries as it has keys was
// built from distinct keys, which answers the uniqueness question without the
// per-call set. A miss checks for a repeat — pairwise for a short key list, with a
// set beyond that — and only then builds and caches the shape.
func UniqueRecordShape(keys []string) *RecordShape {
	var stack [192]byte
	if s := cachedShape(shapeSignature(stack[:0], keys)); s != nil && len(s.KeyMap) == len(s.Keys) {
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
		return InternRecordShape(keys)
	}
	seen := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		if _, exists := seen[k]; exists {
			return nil
		}
		seen[k] = struct{}{}
	}
	return InternRecordShape(keys)
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
