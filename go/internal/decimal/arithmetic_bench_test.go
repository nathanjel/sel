package decimal

import (
	"strings"
	"testing"
)

// GO-P9 / GO-P10 micro-benchmarks (the end-to-end workloads are in go/sel/workloads_bench_test.go).

var sinkDec *Dec

func fail0(code, msg string, pos Pos) { panic(code + ": " + msg) }

func mustParse(s string) *Dec { return Parse(s, Pos{}, fail0) }

func BenchmarkP9(b *testing.B) {
	x, y := mustParse("12345.67"), mustParse("8.9")
	i1, i2 := FromInt(123456), FromInt(789)
	cases := []struct {
		name string
		run  func()
	}{
		{"AddSmallInt", func() { sinkDec = Add(i1, i2, Pos{}, fail0) }},
		{"AddSmallDec", func() { sinkDec = Add(x, y, Pos{}, fail0) }},
		{"SubSmallInt", func() { sinkDec = Sub(i1, i2, Pos{}, fail0) }},
		{"MulSmallInt", func() { sinkDec = Mul(i1, i2, Pos{}, fail0) }},
		{"MulSmallDec", func() { sinkDec = Mul(x, y, Pos{}, fail0) }},
		{"Negate", func() { sinkDec = Negate(i1) }},
		{"Abs", func() { sinkDec = Abs(x) }},
		{"ParseInt", func() { sinkDec = mustParse("123456") }},
		{"ParseDec", func() { sinkDec = mustParse("12345.67") }},
		{"Div", func() { sinkDec = Div(i1, i2, Pos{}, fail0) }},
		{"TrimScale", func() { sinkDec = TrimScale(mustParse("12.3400")) }},
		{"CmpSameScale", func() { _ = Cmp(i1, i2) }},
		{"CmpDiffScale", func() { _ = Cmp(x, y) }},
		{"FromInt", func() { sinkDec = FromInt(987654) }},
		{"Format", func() { _ = Format(x) }},
		{"Round", func() { sinkDec = Round(x, 1, Pos{}, fail0) }},
		{"Mod", func() { sinkDec = Mod(i1, i2, Pos{}, fail0) }},
	}
	for _, c := range cases {
		b.Run(c.name, func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				c.run()
			}
		})
	}
}

// GO-P10: a large numeral.
func BenchmarkP10Parse(b *testing.B) {
	for _, n := range []int{10000, 100000, 250000, 999999} {
		s := strings.Repeat("7", n)
		b.Run(itoa(n), func(b *testing.B) {
			b.ReportAllocs()
			for i := 0; i < b.N; i++ {
				sinkDec = mustParse(s)
			}
		})
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var d []byte
	for n > 0 {
		d = append([]byte{byte('0' + n%10)}, d...)
		n /= 10
	}
	return string(d)
}
