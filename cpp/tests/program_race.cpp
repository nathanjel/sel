// One compiled Program run on several threads at once, each with its own
// context, for the thread sanitizer.
//
// The promise this pins (sel.hpp, "Threads"): a Program is immutable once
// compiled, so it may be shared between threads -- including its first run,
// which builds the physical tree exactly once -- provided every thread runs it
// against a context of its own. A Value is NOT shareable: its reference count is
// not atomic, so a context (or any value reachable from two contexts) must stay
// on one thread; sharing one is a data race even for a read-only rule, and this
// test never does it.
//
// The programs cover the paths with per-node state: a math plan (a loop over
// numeric locals), literal record shapes, a compiled regex, the aggregates and
// a relational pipeline with a join.
//
//   make -C cpp tsan
//
// Without a sanitizer it is an ordinary determinism check.

#include "../sel.hpp"

#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

namespace {

sel::Value make_context(int t) {
  sel::Value ctx = sel::Value::none();
  std::vector<sel::Value> rows;
  for (int i = 1; i <= 40; ++i) {
    rows.push_back(sel::Value::record(
        {"id", "group", "name"},
        {sel::Value::integer(i), sel::Value::integer(i % 4),
         sel::Value::text("n" + std::to_string(i + t))}));
  }
  ctx.set("ROWS", sel::Value::list(std::move(rows)));
  ctx.set("X", sel::Value::num("0.25"));
  ctx.set("Y", sel::Value::num("-0.5"));
  return ctx;
}

}  // namespace

int main() {
  // A Mandelbrot escape count: the math-plan path.
  const sel::Program mandel = sel::compile(
      "ZR = 0; ZI = 0; N = 0; "
      "COND(TRUE, (MAP(LIST(1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16), "
      "IF(ZR * ZR + ZI * ZI <= 4, (T = ZR * ZR - ZI * ZI + X; ZI = 2 * ZR * ZI + Y; ZR = T; "
      "N = N + 1), 0)); N), 0)");
  // Aggregates, records, a regex and a join.
  const sel::Program rows = sel::compile(
      "ROWS .> FILTER(RMATCH(\"^n[0-9]+$\", _[\"name\"])) "
      ".> LINK(ROWS, A, B, A[\"id\"] == B[\"id\"]) "
      ".> MAP(RECORD(\"id\", _[\"A\"][\"id\"], \"g\", _[\"A\"][\"group\"])) "
      ".> BUCKET(_[\"g\"], RECORD(\"g\", _K, \"n\", COUNT(_), \"s\", SUM(_, _[\"id\"])))");

  std::string want_mandel, want_rows;
  {
    sel::Value c = make_context(0);
    want_mandel = mandel.run(c).dump();
    sel::Value d = make_context(0);
    want_rows = rows.run(d).dump();
  }
  // A second pair, compiled now and first run by the threads themselves, so the
  // once-only build of the physical tree is raced as well.
  const sel::Program mandel2 = sel::compile(mandel.source());
  const sel::Program rows2 = sel::compile(rows.source());
  // The same rows in a program that assigns, so is not write-free: each walk
  // decides whether it may read in place from a fact kept on its body's node,
  // which the threads work out on first use, all at once.
  const sel::Program rows3 = sel::compile("Z = 0; " + rows.source());

  std::atomic<int> bad{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < 4; ++t) {
    threads.emplace_back([&, t] {
      for (int i = 0; i < 40; ++i) {
        try {
          sel::Value c = make_context(0);
          if (mandel2.run(c).dump() != want_mandel) ++bad;
          if (mandel.run(c).dump() != want_mandel) ++bad;
          sel::Value d = make_context(0);
          if (rows2.run(d).dump() != want_rows) ++bad;
          sel::Value e = make_context(t);
          if (rows.run(e).dump() != want_rows) ++bad;
          sel::Value f = make_context(0);
          if (rows3.run(f).dump() != want_rows) ++bad;
        } catch (...) {
          ++bad;
        }
      }
    });
  }
  for (auto& th : threads) th.join();
  std::printf("shared program: 4 threads x 40 rounds, own contexts, %d wrong answers\n", bad.load());
  return bad.load() == 0 ? 0 : 1;
}
