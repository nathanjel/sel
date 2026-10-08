// A ray tracer in SEL, and the host function it needs -- from Go.
//
//   make -C go examples && go/build/example-raytrace      (from the repository root)
//   go/build/example-raytrace --ppm 640 360 2 > mark.ppm
//   go/build/example-raytrace --bench report.json
//
// raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
// root has no exact decimal result -- so the application gives it one: SQRT(x, n)
// is the square root of x to n fractional digits (10 if n is left out). Like
// `/`, it is exact when it can be: a root with at most n fractional digits comes
// back at its minimal scale, and any other is rounded half away from zero to
// exactly n. It is computed on whole numbers, so every host gives every digit
// the same. For x = m / 10^s, x has an exact root when m, with the scale made
// even, is a perfect square -- which costs what x's size costs, whatever n is.
// Any other root is rounded from
//
//     sqrt(x) * 10^n = sqrt(m * 10^(2n - s))
//
// whose integer square root is the truncated answer; one comparison of whole
// numbers decides the rounding.
//
// With no arguments it prints a few square roots and a small frame; --ppm prints
// a frame of any size as PPM, and --bench times the frame the benchmarks use.
// The files beside this one print byte-identical output.
//
// The visible differences here: Go's Args has no decimal reader of its own, so
// SQRT reads x as args.Val(0).Decimal(args.PosOf(0)) -- the same read, with the
// same errors at the same position -- and gets its magnitude as a *big.Int of its
// own, which math/big's Sqrt takes as it is. There is no fromNative, so each
// context is built key by key. A failing check panics with a *sel.SelError
// (sel.Fail), which Run turns into its error.

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strconv"
	"time"

	"github.com/nathanjel/sel/go/sel"
)

const maxScale = 1000000 // the cap ROUND's scale has (spec/limits.json)

// EXAMPLE-BEGIN sqrt
var (
	one, ten = big.NewInt(1), big.NewInt(10)
	pow10s   = powersOfTen(48) // 10^0 .. 10^47, built once; read-only, so goroutine-safe
)

func powersOfTen(count int) []*big.Int {
	table := make([]*big.Int, count)
	for i, p := 0, big.NewInt(1); i < count; i, p = i+1, new(big.Int).Mul(p, ten) {
		table[i] = p
	}
	return table
}

func pow10(k int64) *big.Int {
	if k < 0 {
		panic("negative exponent in pow10")
	}
	if k < int64(len(pow10s)) {
		return pow10s[k]
	}
	return new(big.Int).Exp(ten, big.NewInt(k), nil)
}

func sqrt(args *sel.Args) *sel.Value {
	x := args.Val(0).Decimal(args.PosOf(0))
	n := int64(10)
	if args.Count() > 1 {
		n = args.NonNegInt(1)
	}
	if n > maxScale {
		sel.Fail("E_RANGE", "SQRT: scale above 1000000", args.PosOf(1))
	}
	if x.Neg {
		sel.Fail("E_RANGE", "SQRT of a negative number", args.PosOf(0))
	}
	// An exact root is taken from x itself, so what it costs depends on x, not
	// on n: with the scale made even, x = m2 / 10^(2t) has a terminating root
	// exactly when m2 is a perfect square, and the root is isqrt(m2) / 10^t.
	m2, t := x.Digits, int64(x.Scale) // x.Digits is this call's own copy
	if t%2 != 0 {
		m2, t = new(big.Int).Mul(m2, ten), t+1
	}
	t /= 2
	r := new(big.Int).Sqrt(m2)
	if sq := new(big.Int).Mul(r, r); sq.Cmp(m2) == 0 {
		// drop the zeros it does not need, in halving chunks -- log t divisions
		// for a root with t of them, not t
		step := int64(1)
		for step*2 <= t {
			step *= 2
		}
		q, rem := new(big.Int), new(big.Int)
		for ; step > 0 && t > 0; step /= 2 {
			if step <= t {
				if q.QuoRem(r, pow10(step), rem); rem.Sign() == 0 {
					r, q = q, r
					t -= step
				}
			}
		}
		if t <= n {
			return sel.NewDecimal(sel.Decimal{Digits: r, Scale: int(t)})
		}
	}
	// Any other root is rounded at scale n. x = m / 10^s, so sqrt(x) * 10^n =
	// sqrt(m * 10^e) with e = 2n - s; when e is negative that is
	// sqrt(m / 10^-e), whose integer part is isqrt(m div 10^-e)
	v, p := x.Digits, one
	if e := 2*n - int64(x.Scale); e >= 0 {
		v.Mul(v, pow10(e))
	} else {
		p = pow10(-e)
	}
	r = new(big.Int).Quo(v, p)
	r.Sqrt(r)
	bound := new(big.Int).Lsh(r, 1) // 2r + 1, then (2r+1)^2 p
	if bound.Add(bound, one).Mul(bound, bound).Mul(bound, p).Cmp(v.Lsh(v, 2)) <= 0 {
		r.Add(r, one) // (2r+1)^2 p <= 4v, at or past the half: away from zero
	}
	return sel.NewDecimal(sel.Decimal{Digits: r, Scale: int(n)})
}

func init() {
	sel.RegisterFunction("SQRT", 1, 2, sqrt)
}

// EXAMPLE-END sqrt

func read(name string) string {
	source, err := os.ReadFile("examples/raytrace/" + name)
	if err != nil {
		panic(err)
	}
	return string(source)
}

func context(w, h, ss string) *sel.Value {
	ctx := sel.NewNone()
	ctx.Set("W", sel.NewText(w))
	ctx.Set("H", sel.NewText(h))
	ctx.Set("SS", sel.NewText(ss))
	return ctx
}

func frame(scene *sel.Program, w, h, ss string) string {
	img, err := scene.Run(context(w, h, ss))
	if err != nil {
		panic(err)
	}
	return img.AsText(sel.Pos{})
}

func crc32(crc *sel.Program, img string) string {
	ctx := sel.NewNone()
	ctx.Set("IMG", sel.NewText(img))
	sum, err := crc.Run(ctx)
	if err != nil {
		panic(err)
	}
	return sum.AsText(sel.Pos{})
}

func mustCompile(source string) *sel.Program {
	program, err := sel.Compile(source)
	if err != nil {
		panic(err)
	}
	return program
}

func run(src string) (string, error) {
	program, err := sel.Compile(src)
	if err != nil {
		return "", err
	}
	result, err := program.Run(sel.NewNone())
	if err != nil {
		return "", err
	}
	return result.AsText(sel.Pos{}), nil
}

func main() {
	os.Exit(exampleMain(os.Args[1:]))
}

func exampleMain(argv []string) int {
	scene := mustCompile(read("raytrace.sel"))
	if len(argv) == 4 && argv[0] == "--ppm" {
		os.Stdout.WriteString(frame(scene, argv[1], argv[2], argv[3]))
		return 0
	}
	if len(argv) == 2 && argv[0] == "--bench" {
		return bench(scene, argv[1])
	}
	if len(argv) > 0 {
		fmt.Fprintln(os.Stderr, "usage: example-raytrace [--ppm W H SS | --bench REPORT.json]")
		return 2
	}

	fmt.Println("1. SQRT, the one function the ray tracer needs from the host")
	for _, src := range []string{"SQRT(2)", "SQRT(2, 40)", "SQRT(2.25)", "SQRT(1000000, 3)", "SQRT(0.000)",
		"SQRT(6.25, 3000)", "SQRT(0.0025, 1)", "SQRT(0.0225, 1)", "SQRT(99.999999, 2)",
		"SQRT(POWER(12345678901234567890, 2))", "SQRT(POWER(10, 41) + 1, 3)",
		"SQRT(-4)", `SQRT("four")`, "SQRT(4, -1)", "SQRT(4, 0.5)", "SQRT(4, 1000001)"} {
		result, err := run(src)
		var e *sel.SelError
		if errors.As(err, &e) {
			fmt.Printf("   %-38s => %s at %d:%d\n", src, e.Code, e.Line(), e.Col())
			continue
		} else if err != nil {
			panic(err)
		}
		fmt.Printf("   %-38s => %s\n", src, result)
	}

	fmt.Println("2. the scene, 64 x 36, one ray per pixel")
	img := frame(scene, "64", "36", "1")
	fmt.Printf("   %d bytes of PPM, CRC32 %s\n", len(img), crc32(mustCompile("CRC32(IMG)"), img))
	return 0
}

// The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
// pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5).
func bench(scene *sel.Program, report string) int {
	warmups, runs := envInt("RAYTRACE_WARMUPS", 2), envInt("RAYTRACE_RUNS", 5)
	crc := mustCompile("CRC32(IMG)")
	samples, outputs := []float64{}, []string{}
	for i := 0; i < warmups+runs; i++ {
		ctx := context("64", "36", "1")
		t := time.Now()
		result, err := scene.Run(ctx)
		if err != nil {
			panic(err)
		}
		img := result.AsText(sel.Pos{})
		elapsed := float64(time.Since(t).Nanoseconds()) / 1e6
		if i >= warmups {
			samples = append(samples, elapsed)
			outputs = append(outputs, crc32(crc, img))
		}
	}
	data, err := json.Marshal(struct {
		SamplesMs []float64 `json:"samples_ms"`
		Outputs   []string  `json:"outputs"`
		Warmups   int       `json:"warmups"`
		Runs      int       `json:"runs"`
	}{samples, outputs, warmups, runs})
	if err == nil {
		err = os.WriteFile(report, data, 0o644)
	}
	if err != nil {
		panic(err)
	}
	return 0
}

func envInt(name string, fallback int) int {
	text, ok := os.LookupEnv(name)
	if !ok {
		return fallback
	}
	n, err := strconv.Atoi(text)
	if err != nil {
		panic(fmt.Sprintf("%s: not a whole number: %q", name, text))
	}
	return n
}
