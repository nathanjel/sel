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

// A literal operand is one value per node (Node.lit), so goroutines running one
// program read it together: the number a text literal holds and the text a
// number literal reads as are derived on first use, through the value's atomic
// caches, never a plain field.
func TestConcurrentSharedProgramLiteralOperands(t *testing.T) {
	p := MustCompile(`COUNT(FILTER(L, "2.50" * _ > "7" AND _ $!= "x" AND 3 $< _ & "")) & ("3" + 4) & (5 & "")`)
	concurrentWorkers(t, func(worker int) {
		for i := 0; i < 50; i++ {
			items := make([]*Value, 8)
			for j := range items {
				items[j] = NewInt(int64(j + worker))
			}
			v, err := p.Run(NewNone().Set("L", NewList(items)))
			if err != nil {
				t.Errorf("literal operands: %v", err)
				return
			}
			kept := 0
			for j := range items {
				if n := j + worker; n*5 > 14 && fmt.Sprint(n) > "3" {
					kept++
				}
			}
			if got, want := v.Scalar(), fmt.Sprintf("%d75", kept); got != want {
				t.Errorf("literal operands: got %q, want %q", got, want)
			}
		}
	})
}
