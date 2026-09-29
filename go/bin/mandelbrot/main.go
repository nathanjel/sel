package main

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime/pprof"
	"strconv"
	"time"

	"github.com/nathanjel/sel/go/sel"
)

type MandelReport struct {
	CompileMs float64   `json:"compile_ms"`
	SamplesMs []float64 `json:"samples_ms"`
	Outputs   []string  `json:"outputs"`
	Warmups   int       `json:"warmups"`
	Runs      int       `json:"runs"`
}

func main() {
	outPath := ""
	if len(os.Args) > 1 {
		outPath = os.Args[1]
	}
	if outPath == "" {
		outPath = os.Getenv("MANDEL_OUTPUT")
	}

	sourceBytes, err := os.ReadFile("examples/mandelbrot.sel")
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to read examples/mandelbrot.sel: %v\n", err)
		os.Exit(1)
	}

	t0 := time.Now()
	prog, err := sel.Compile(string(sourceBytes))
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to compile mandelbrot: %v\n", err)
		os.Exit(1)
	}
	compileMs := float64(time.Since(t0).Nanoseconds()) / 1e6

	warmups := 2
	if wStr := os.Getenv("MANDEL_WARMUPS"); wStr != "" {
		if w, err := strconv.Atoi(wStr); err == nil {
			warmups = w
		}
	}
	runs := 5
	if rStr := os.Getenv("MANDEL_RUNS"); rStr != "" {
		if r, err := strconv.Atoi(rStr); err == nil {
			runs = r
		}
	}

	if prof := os.Getenv("MANDEL_CPUPROFILE"); prof != "" {
		f, err := os.Create(prof)
		if err == nil {
			defer f.Close()
			pprof.StartCPUProfile(f)
			defer pprof.StopCPUProfile()
		}
	}

	var samplesMs []float64
	var outputs []string

	for i := 0; i < warmups+runs; i++ {
		tRun := time.Now()
		res, err := prog.Run(nil)
		if err != nil {
			fmt.Fprintf(os.Stderr, "runtime error: %v\n", err)
			os.Exit(1)
		}
		outStr := res.AsText(sel.Pos{})
		elapsedMs := float64(time.Since(tRun).Nanoseconds()) / 1e6

		if i >= warmups {
			samplesMs = append(samplesMs, elapsedMs)
			outputs = append(outputs, outStr)
		}
	}

	report := MandelReport{
		CompileMs: compileMs,
		SamplesMs: samplesMs,
		Outputs:   outputs,
		Warmups:   warmups,
		Runs:      runs,
	}

	reportBytes, err := json.Marshal(report)
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to serialize report: %v\n", err)
		os.Exit(1)
	}

	if outPath != "" {
		if err := os.WriteFile(outPath, reportBytes, 0644); err != nil {
			fmt.Fprintf(os.Stderr, "failed to write %s: %v\n", outPath, err)
			os.Exit(1)
		}
	} else {
		os.Stdout.Write(reportBytes)
		os.Stdout.WriteString("\n")
	}
}
