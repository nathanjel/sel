package sel

import (
	"strings"
	"sync"
)

type RecordShape struct {
	Keys   []string
	KeyMap map[string]int
	Size   int
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

func InternRecordShape(keys []string) *RecordShape {
	sig := strings.Join(keys, "\x00")
	shapeMu.RLock()
	if s, ok := shapeCache[sig]; ok {
		shapeMu.RUnlock()
		return s
	}
	shapeMu.RUnlock()

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
		shapeCache[sig] = s
		shapeMu.Unlock()
	}
	return s
}

func UniqueRecordShape(keys []string) *RecordShape {
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
