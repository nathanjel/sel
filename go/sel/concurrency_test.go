//go:build race

package sel

import (
	"fmt"
	"strings"
	"sync"
	"testing"
)

// These tests run under both make test and the repository's Go unit lane.
// Each worker owns its context and values; only runtime caches are shared.
func concurrentWorkers(t *testing.T, work func(int)) {
	t.Helper()
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			work(i)
		}(i)
	}
	close(start)
	wg.Wait()
}

func TestConcurrentIndependentDecimalEvaluations(t *testing.T) {
	concurrentWorkers(t, func(worker int) {
		for i := 0; i < 30; i++ {
			scale := 20 + worker + i
			v, err := Eval(fmt.Sprintf("ROUND(1, %d)", scale), nil)
			if err != nil {
				t.Errorf("ROUND scale %d: %v", scale, err)
				return
			}
			want := "1." + strings.Repeat("0", scale)
			if got := v.Scalar(); got != want {
				t.Errorf("ROUND scale %d: got %q, want %q", scale, got, want)
			}
		}
	})
}

func TestConcurrentSharedProgramFieldCache(t *testing.T) {
	p := MustCompile(`R["x"]`)
	concurrentWorkers(t, func(worker int) {
		for i := 0; i < 100; i++ {
			entries := []Entry{{Key: fmt.Sprintf("other%d", worker), Val: NewInt(-1)}, {Key: "x", Val: NewInt(42)}}
			// Vary both shape identity and the position of x. A stale slot must not
			// merely race silently: it must also be caught as an incorrect result.
			if worker%2 == 0 {
				entries[0], entries[1] = entries[1], entries[0]
			}
			ctx := NewNone().Set("R", NewRecordFromEntries(entries))
			v, err := p.Run(ctx)
			if err != nil {
				t.Errorf("field lookup: %v", err)
				return
			}
			if got := v.Scalar(); got != "42" {
				t.Errorf("field lookup: got %q, want 42", got)
			}
		}
	})
}
