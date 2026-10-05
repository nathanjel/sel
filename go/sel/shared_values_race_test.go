//go:build race

package sel

import (
	"fmt"
	"sync"
	"testing"
)

// Reading is not writing. A Value the caller hands to Run and does not
// change must be safe to hand to several goroutines at once: evaluation over it may
// not write a lazy cache (parsed decimal, text form, shape slot) into the shared
// Value. concurrency_test.go shares Programs and gives every worker its own Values;
// these share the VALUES, which is the case that used to report data races even
// though every result was right. Runs under `go test -race` (the build tag), where
// the race report is the failure.

func sharedWorkers(t *testing.T, n int, work func(int)) {
	t.Helper()
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < n; i++ {
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

func sharedRoot() *Value {
	items := make([]*Value, 0, 200)
	for i := 0; i < 200; i++ {
		items = append(items, NewRecordFromEntries([]Entry{{Key: "v", Val: NewText(fmt.Sprint(i))}, {Key: "k", Val: NewText("k")}}))
	}
	root := NewNone()
	root.Set("R", NewRecordFromEntries([]Entry{{Key: "x", Val: NewText("41")}, {Key: "name", Val: NewText("n")}}))
	root.Set("L", NewList(items))
	return root
}

func TestSharedReadOnlyRootAcrossGoroutines(t *testing.T) {
	root := sharedRoot()
	want := map[string]string{
		`R["x"] + 1`:                          "42",
		`SUM(L, _["v"])`:                      "19900",
		`COUNT(SORT_BY(L, _["v"]))`:           "200",
		`R["name"] & R["x"]`:                  "n41",
		`R["x"] * 2 & "!"`:                    "82!",
		`SUM(L, _["v"] + 1) - SUM(L, 1)`:      "19900",
		`MAX(R["x"], 7) & LEFT(R["name"], 1)`: "41n",
	}
	sources := make([]string, 0, len(want))
	for s := range want {
		sources = append(sources, s)
	}
	sharedWorkers(t, 8, func(worker int) {
		for i := 0; i < 40; i++ {
			src := sources[(worker+i)%len(sources)]
			v, err := MustCompile(src).Run(root)
			if err != nil {
				t.Errorf("%s: %v", src, err)
				return
			}
			if got := v.AsText(Pos{}); got != want[src] {
				t.Errorf("%s: got %q, want %q", src, got, want[src])
			}
		}
	})
}

// A number a previous program left in the context: its decimal is parsed lazily by
// the first program that needs it.
func TestSharedNumberLeftByAnEarlierProgram(t *testing.T) {
	root := NewNone()
	if _, err := MustCompile(`X = 1.5; N = X * 3; 0`).Run(root); err != nil {
		t.Fatal(err)
	}
	sharedWorkers(t, 8, func(int) {
		for i := 0; i < 50; i++ {
			v, err := MustCompile(`N & "x"`).Run(root)
			if err != nil {
				t.Errorf("N & x: %v", err)
				return
			}
			if got := v.AsText(Pos{}); got != "4.5x" {
				t.Errorf("N & x: got %q", got)
			}
			if v, err = MustCompile(`N + 1`).Run(root); err != nil || v.AsText(Pos{}) != "5.5" {
				t.Errorf("N + 1: got %v, %v", v, err)
			}
		}
	})
}

// Text items whose numeric form has not been parsed yet, sorted and summed from
// four goroutines that alternate.
func TestSharedTextItemsSortedAndSummed(t *testing.T) {
	items := make([]*Value, 0, 200)
	for i := 0; i < 200; i++ {
		items = append(items, NewText(fmt.Sprint(i)))
	}
	root := NewNone().Set("L", NewList(items))
	sharedWorkers(t, 4, func(worker int) {
		for i := 0; i < 30; i++ {
			src, want := `SORT(L) .> COUNT()`, "200"
			if (worker+i)%2 == 1 {
				src, want = `SUM(L, _ + 1)`, "20100"
			}
			v, err := MustCompile(src).Run(root)
			if err != nil {
				t.Errorf("%s: %v", src, err)
				return
			}
			if got := v.AsText(Pos{}); got != want {
				t.Errorf("%s: got %q, want %q", src, got, want)
			}
		}
	})
}

// The same Program and the same Value at once: what an HTTP handler that compiles
// its rules once and validates a cached document per request does.
func TestSharedProgramAndSharedValue(t *testing.T) {
	root := sharedRoot()
	prog := MustCompile(`SUM(L, _["v"]) + R["x"] & "|" & COUNT(SORT_BY(L, _["v"], "DESC"))`)
	sharedWorkers(t, 8, func(int) {
		for i := 0; i < 30; i++ {
			v, err := prog.Run(root)
			if err != nil {
				t.Errorf("shared run: %v", err)
				return
			}
			if got := v.AsText(Pos{}); got != "19941|200" {
				t.Errorf("shared run: got %q", got)
			}
		}
	})
}
