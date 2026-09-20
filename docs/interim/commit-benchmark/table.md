## Primary results

Milliseconds; medians of five samples except the marked old Mandelbrot single executions. Lower is faster. `Δ latest/base` is a latency change, not a speedup ratio.

| Lane | Workload | ef836bf | 1614eed | 619bc31 | c5a8991 | Δ latest/base |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| cpp | scenario1 | 1,254.08 | 525.80 | 581.70 | 581.81 | -53.6% |
| cpp | scenario2 | 13.06 | 8.04 | 5.43 | 5.70 | -56.4% |
| cpp | scenario3 | 348.63 | 205.95 | 192.17 | 185.69 | -46.7% |
| cpp | scenario4 | 21.23 | 11.95 | 9.18 | 8.85 | -58.3% |
| cpp | scenario5 | 2,362.28 | 520.56 | 543.86 | 554.03 | -76.5% |
| cpp | scenario6 | 343.24 | 247.47 | 270.28 | 274.50 | -20.0% |
| cpp | mandelbrot | 15,942.30* | 152.40 | 147.01 | 151.58 | -99.049% |
| js | scenario1 | 713.20 | 687.93 | 688.66 | 605.47 | -15.1% |
| js | scenario2 | 11.65 | 12.72 | 13.24 | 12.40 | +6.4% |
| js | scenario3 | 266.35 | 268.79 | 265.69 | 230.41 | -13.5% |
| js | scenario4 | 12.26 | 10.52 | 10.19 | 9.26 | -24.5% |
| js | scenario5 | 604.21 | 593.34 | 595.58 | 496.50 | -17.8% |
| js | scenario6 | 275.84 | 280.04 | 280.41 | 223.50 | -19.0% |
| js | mandelbrot | 43,442.50* | 95.35 | 64.61 | 65.08 | -99.850% |
| lisp | scenario1 | 709.01 | 494.00 | 499.00 | 503.01 | -29.1% |
| lisp | scenario2 | 14.00 | 12.00 | 13.00 | 12.00 | -14.3% |
| lisp | scenario3 | 368.00 | 247.00 | 243.00 | 227.00 | -38.3% |
| lisp | scenario4 | 25.00 | 16.00 | 16.00 | 16.00 | -36.0% |
| lisp | scenario5 | 514.00 | 428.00 | 424.00 | 426.01 | -17.1% |
| lisp | scenario6 | 213.00 | 216.00 | 214.00 | 271.00 | +27.2% |
| lisp | mandelbrot | 241,445.87* | 78.00 | 79.00 | 80.00 | -99.967% |
| php | scenario1 | 3,879.13 | 3,080.03 | 3,084.45 | 3,063.55 | -21.0% |
| php | scenario2 | 43.17 | 27.18 | 25.86 | 26.08 | -39.6% |
| php | scenario3 | 1,210.14 | 929.74 | 923.28 | 906.80 | -25.1% |
| php | scenario4 | 85.60 | 58.68 | 56.66 | 60.34 | -29.5% |
| php | scenario5 | 14,643.22 | 2,154.75 | 2,140.23 | 2,148.91 | -85.3% |
| php | scenario6 | 990.04 | 1,041.96 | 1,068.98 | 1,074.71 | +8.6% |
| php | mandelbrot | 271,601.45* | 392.26 | 382.86 | 397.58 | -99.854% |
| python | scenario1 | 5,747.64 | 2,792.38 | 2,658.13 | 2,632.86 | -54.2% |
| python | scenario2 | 82.76 | 38.65 | 38.59 | 38.84 | -53.1% |
| python | scenario3 | 2,577.68 | 1,555.56 | 1,524.70 | 1,547.12 | -40.0% |
| python | scenario4 | 144.17 | 68.72 | 66.08 | 65.10 | -54.8% |
| python | scenario5 | 3,997.77 | 505.96 | 509.24 | 502.37 | -87.4% |
| python | scenario6 | 1,842.01 | 710.93 | 740.83 | 733.17 | -60.2% |
| python | mandelbrot | 1,321.11 | 406.94 | 362.51 | 358.57 | -72.858% |

*One execution with no warmup; all other Mandelbrot entries are five-sample medians. The Lisp S6 first-pass median is GC-sensitive; see the repeated measurements before interpreting its delta.
