//go:build race

package sel

import (
	"sync"
	"testing"
)

// Item 1: math plan registers belong to one evaluation of one Context. One
// Program with big constants and plans, run on eight goroutines at once, must
// give every goroutine the answer it gives alone (run under -race).
func TestPlanRegistersAreNotSharedBetweenRuns(t *testing.T) {
	src := planSetup + `X = A * B + C * 2.5; Y = (C - B * 3) * A - 7.25; MAP(LIST(1, 2, 3), K, X * K - Y * K + A * A)`
	prog := MustCompile(src)
	want, err := prog.Run(NewNone())
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	errs := make(chan string, 8)
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 20; i++ {
				got, err := prog.Run(NewNone())
				if err != nil || got.Dump() != want.Dump() {
					errs <- clip(got.Dump())
					return
				}
			}
		}()
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		t.Fatalf("a concurrent run answered %s", e)
	}
}
