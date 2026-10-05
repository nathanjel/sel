package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime/pprof"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/nathanjel/sel/go/internal/harness"
	"github.com/nathanjel/sel/go/sel"
)

func parseWhole(text string, fallback int64) int64 {
	dot := strings.Index(text, ".")
	if dot >= 0 {
		text = text[:dot]
	}
	if val, err := strconv.ParseInt(text, 10, 64); err == nil {
		return val
	}
	return fallback
}

func registerBenchmarkBuiltins() {
	sel.RegisterFunction("CUSTOM_VIP_SCORE", 2, 2, func(a *sel.Args) *sel.Value {
		tier := a.Val(0).AsText(a.PosOf(0))
		year := parseWhole(a.Val(1).AsText(a.PosOf(1)), 2024)
		base := int64(10)
		switch tier {
		case "PLATINUM":
			base = 100
		case "GOLD":
			base = 50
		case "SILVER":
			base = 25
		}
		return sel.NewInt(base + (2026-year)*5)
	})

	sel.RegisterFunction("HOST_RISK_SCORE", 2, 2, func(a *sel.Args) *sel.Value {
		country := a.Val(0).AsText(a.PosOf(0))
		discount := parseWhole(a.Val(1).AsText(a.PosOf(1)), 0)
		base := int64(10)
		if country == "US" {
			base = 30
		}
		return sel.NewInt(base + discount*2)
	})
}

func convertJSONValue(val interface{}) *sel.Value {
	if val == nil {
		return sel.NewNull()
	}
	switch v := val.(type) {
	case bool:
		return sel.NewBool(v)
	case float64:
		if v == math.Floor(v) && !math.IsInf(v, 0) && math.Abs(v) < 1e15 {
			return sel.NewInt(int64(v))
		}
		return sel.NewText(strconv.FormatFloat(v, 'f', -1, 64))
	case string:
		return sel.NewText(v)
	case []interface{}:
		items := make([]*sel.Value, len(v))
		for i, el := range v {
			items[i] = convertJSONValue(el)
		}
		return sel.NewListOwned(items)
	case map[string]interface{}:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		shape := sel.InternRecordShape(keys)
		vals := make([]*sel.Value, len(keys))
		for i, k := range keys {
			vals[i] = convertJSONValue(v[k])
		}
		return sel.NewShapedRecord(shape, vals)
	default:
		return sel.NewText(fmt.Sprint(v))
	}
}

func loadContext(datasetPath string) *sel.Value {
	data := []byte(harness.ReadFile(datasetPath))
	var raw map[string][]map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		panic(fmt.Sprintf("cannot parse dataset: %v", err))
	}

	rootEntries := make([]sel.Entry, 0, len(raw))
	for table, rows := range raw {
		tableName := strings.ToUpper(table)
		if tableName == "CUSTOMERS" {
			// Add dist_berlin to customers
			// latitude & longitude as float32 (single precision)
			customerList := make([]*sel.Value, len(rows))
			if len(rows) > 0 {
				firstRow := rows[0]
				keys := make([]string, 0, len(firstRow)+1)
				for k := range firstRow {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				keys = append(keys, "dist_berlin")
				shape := sel.InternRecordShape(keys)

				for rIdx, row := range rows {
					latVal, _ := strconv.ParseFloat(fmt.Sprint(row["latitude"]), 32)
					lonVal, _ := strconv.ParseFloat(fmt.Sprint(row["longitude"]), 32)
					dx := float64(float32(lonVal)) - 13.404954
					dy := float64(float32(latVal)) - 52.520008
					dist := math.Sqrt(dx*dx + dy*dy)
					distStr := fmt.Sprintf("%.6f", dist)

					rowVals := make([]*sel.Value, len(keys))
					for i, k := range keys {
						if k == "dist_berlin" {
							rowVals[i] = sel.NewText(distStr)
						} else {
							rowVals[i] = convertJSONValue(row[k])
						}
					}
					customerList[rIdx] = sel.NewShapedRecord(shape, rowVals)
				}
			}
			rootEntries = append(rootEntries, sel.Entry{Key: tableName, Val: sel.NewListOwned(customerList)})
		} else {
			rowList := make([]*sel.Value, len(rows))
			if len(rows) > 0 {
				firstRow := rows[0]
				keys := make([]string, 0, len(firstRow))
				for k := range firstRow {
					keys = append(keys, k)
				}
				sort.Strings(keys)
				shape := sel.InternRecordShape(keys)

				for rIdx, row := range rows {
					rowVals := make([]*sel.Value, len(keys))
					for i, k := range keys {
						rowVals[i] = convertJSONValue(row[k])
					}
					rowList[rIdx] = sel.NewShapedRecord(shape, rowVals)
				}
			}
			rootEntries = append(rootEntries, sel.Entry{Key: tableName, Val: sel.NewListOwned(rowList)})
		}
	}
	return sel.NewRecordFromEntries(rootEntries)
}

func benchmarkValue(v *sel.Value) interface{} {
	if v == nil {
		return nil
	}
	if v.Size() == 0 {
		if v.IsList() {
			return []interface{}{}
		}
		if v.Kind == sel.KindText || v.Kind == sel.KindBin || v.Kind == sel.KindBool {
			return v.AsText(sel.Pos{})
		}
		return nil
	}
	if v.IsList() {
		out := make([]interface{}, 0, v.Size())
		for _, child := range v.Values() {
			out = append(out, benchmarkValue(child))
		}
		return out
	}
	out := make(map[string]interface{})
	for _, entry := range v.Entries() {
		out[entry.Key] = benchmarkValue(entry.Val)
	}
	if v.Kind == sel.KindText || v.Kind == sel.KindBin || v.Kind == sel.KindBool {
		out["_"] = v.AsText(sel.Pos{})
	}
	return out
}

type ScenarioReference struct {
	ID           string      `json:"id"`
	Query        string      `json:"query"`
	InMemoryRows interface{} `json:"in_memory_rows"`
	SqlPostgres  *string     `json:"sql_postgres"`
	SqlMariaDB   *string     `json:"sql_mariadb"`
}

type Sample struct {
	ProgramRunMs    float64 `json:"program_run_ms"`
	MaterializeMs   float64 `json:"materialize_ms"`
	PreparedTotalMs float64 `json:"prepared_total_ms"`
	ElapsedMs       float64 `json:"elapsed_ms"`
}

type Stats struct {
	Count     int       `json:"count"`
	MeanMs    float64   `json:"mean_ms"`
	MedianMs  float64   `json:"median_ms"`
	MinMs     float64   `json:"min_ms"`
	MaxMs     float64   `json:"max_ms"`
	SamplesMs []float64 `json:"samples_ms"`
}

func computeStats(samples []float64) Stats {
	if len(samples) == 0 {
		return Stats{}
	}
	sorted := make([]float64, len(samples))
	copy(sorted, samples)
	sort.Float64s(sorted)

	sum := 0.0
	for _, s := range sorted {
		sum += s
	}
	mean := sum / float64(len(sorted))
	median := sorted[len(sorted)/2]
	if len(sorted)%2 == 0 {
		median = (sorted[len(sorted)/2-1] + sorted[len(sorted)/2]) / 2.0
	}
	return Stats{
		Count:     len(sorted),
		MeanMs:    mean,
		MedianMs:  median,
		MinMs:     sorted[0],
		MaxMs:     sorted[len(sorted)-1],
		SamplesMs: samples,
	}
}

func canonicalJSON(v interface{}) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func main() {
	datasetFlag := flag.String("dataset", "tools/scale-test/dataset-10x.json", "path to dataset JSON")
	refFlag := flag.String("reference", "tools/scale-test/benchmark_results.json", "path to reference JSON")
	runsFlag := flag.Int("runs", 3, "number of measured runs")
	warmupsFlag := flag.Int("warmups", 1, "number of warmups")
	outputFlag := flag.String("output", "", "optional output report file")
	onlyFlag := flag.String("only", "", "only run scenario with id or number")
	cpuprofileFlag := flag.String("cpuprofile", "", "write cpu profile to file")
	flag.Parse()

	if *cpuprofileFlag != "" {
		f, err := os.Create(*cpuprofileFlag)
		if err != nil {
			fmt.Fprintf(os.Stderr, "could not create CPU profile: %v\n", err)
			os.Exit(1)
		}
		defer f.Close()
		if err := pprof.StartCPUProfile(f); err != nil {
			fmt.Fprintf(os.Stderr, "could not start CPU profile: %v\n", err)
			os.Exit(1)
		}
		defer pprof.StopCPUProfile()
	}

	registerBenchmarkBuiltins()

	datasetPath, _ := filepath.Abs(*datasetFlag)
	context := loadContext(datasetPath)

	refData := []byte(harness.ReadFile(*refFlag))
	var reference []ScenarioReference
	if err := json.Unmarshal(refData, &reference); err != nil {
		fmt.Fprintf(os.Stderr, "failed to parse reference: %v\n", err)
		os.Exit(1)
	}

	type ScenarioReport struct {
		ID         string           `json:"id"`
		Rows       int              `json:"rows"`
		CompileMs  float64          `json:"compile_ms"`
		Samples    []Sample         `json:"samples"`
		Statistics map[string]Stats `json:"statistics"`
		Passed     bool             `json:"passed"`
		Failures   []string         `json:"failures"`
	}

	var scenarioReports []ScenarioReport
	allPassed := true

	for _, sc := range reference {
		if *onlyFlag != "" && sc.ID != *onlyFlag && !strings.HasSuffix(sc.ID, *onlyFlag) {
			continue
		}
		tComp := time.Now()
		prog, err := sel.Compile(sc.Query)
		if err != nil {
			fmt.Printf("FAIL %s: compilation error: %v\n", sc.ID, err)
			allPassed = false
			continue
		}
		compMs := float64(time.Since(tComp).Nanoseconds()) / 1e6

		// Validation run
		valRes, err := prog.Run(context)
		if err != nil {
			fmt.Printf("FAIL %s: runtime error: %v\n", sc.ID, err)
			allPassed = false
			continue
		}
		valRows := benchmarkValue(valRes)
		valJSON := canonicalJSON(valRows)
		expJSON := canonicalJSON(sc.InMemoryRows)

		var failures []string
		if valJSON != expJSON {
			failures = append(failures, "in-memory rows differ from reference")
		}

		// Warmups
		for w := 0; w < *warmupsFlag; w++ {
			fmt.Printf("[go] %s warmup %d/%d\n", sc.ID, w+1, *warmupsFlag)
			prog.Run(context)
		}

		// Measured runs
		var samples []Sample
		var runMsList, matMsList, totMsList []float64

		for r := 0; r < *runsFlag; r++ {
			fmt.Printf("[go] %s measured %d/%d\n", sc.ID, r+1, *runsFlag)

			tPrep := time.Now()
			tRun := time.Now()
			res, err := prog.Run(context)
			if err != nil {
				failures = append(failures, fmt.Sprintf("run %d failed: %v", r+1, err))
			}
			runMs := float64(time.Since(tRun).Nanoseconds()) / 1e6

			tMat := time.Now()
			rows := benchmarkValue(res)
			matMs := float64(time.Since(tMat).Nanoseconds()) / 1e6
			prepMs := float64(time.Since(tPrep).Nanoseconds()) / 1e6

			samples = append(samples, Sample{
				ProgramRunMs:    runMs,
				MaterializeMs:   matMs,
				PreparedTotalMs: prepMs,
				ElapsedMs:       prepMs,
			})
			runMsList = append(runMsList, runMs)
			matMsList = append(matMsList, matMs)
			totMsList = append(totMsList, prepMs)

			_ = rows
		}

		passed := len(failures) == 0
		if !passed {
			allPassed = false
		}

		statsMap := map[string]Stats{
			"program_run_ms":    computeStats(runMsList),
			"materialize_ms":   computeStats(matMsList),
			"prepared_total_ms": computeStats(totMsList),
		}

		rowCount := 0
		if arr, ok := valRows.([]interface{}); ok {
			rowCount = len(arr)
		}

		scenarioReports = append(scenarioReports, ScenarioReport{
			ID:         sc.ID,
			Rows:       rowCount,
			CompileMs:  compMs,
			Samples:    samples,
			Statistics: statsMap,
			Passed:     passed,
			Failures:   failures,
		})

		statusStr := "PASS"
		if !passed {
			statusStr = "FAIL"
		}
		fmt.Printf("%s %s: median total=%.2fms (run=%.2fms, mat=%.2fms)\n",
			statusStr, sc.ID, statsMap["prepared_total_ms"].MedianMs,
			statsMap["program_run_ms"].MedianMs, statsMap["materialize_ms"].MedianMs)
	}

	if *outputFlag != "" {
		outData, _ := json.MarshalIndent(scenarioReports, "", "  ")
		os.WriteFile(*outputFlag, outData, 0644)
	}

	if !allPassed {
		os.Exit(1)
	}
}
