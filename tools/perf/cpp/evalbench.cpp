// C++ one-shot evaluation benchmark (worklist CPP-P15).
//
//   g++ -std=c++23 -O2 -o cpp/build/evalbench tools/perf/cpp/evalbench.cpp cpp/build/sel.o
//   cpp/build/evalbench 100000
//
// Per call, in microseconds of CPU time: compile(), compile()+run() (what
// evaluate() is made of), and evaluate() itself, for three rules of the kinds a
// validation layer runs once per record. The last column is an FNV checksum of the
// results so a speedup that changes an answer shows.
#include "../../../cpp/sel.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>
#include <vector>

static double cpu_seconds() {
  timespec ts;
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return static_cast<double>(ts.tv_sec) + static_cast<double>(ts.tv_nsec) * 1e-9;
}

static std::uint64_t fnv(const std::string& s, std::uint64_t h) {
  for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
  return h;
}

static sel::Value context() {
  sel::Value c = sel::Value::none();
  c.set("TOTAL", sel::Value::num("15"));
  c.set("STATUS", sel::Value::text("open"));
  c.set("PRICE", sel::Value::num("19.99"));
  c.set("QTY", sel::Value::num("7"));
  c.set("TAX", sel::Value::num("23"));
  c.set("DISC", sel::Value::num("5.5"));
  std::vector<sel::Value> items;
  for (int i = 0; i < 4; ++i) {
    sel::Value it = sel::Value::none();
    it.set("qty", sel::Value::integer(i));
    it.set("sku", sel::Value::text("AB-" + std::to_string(i)));
    items.push_back(std::move(it));
  }
  c.set("ITEMS", sel::Value::list(std::move(items)));
  return c;
}

int main(int argc, char** argv) {
  const int n = argc > 1 ? std::atoi(argv[1]) : 100000;
  const std::vector<std::pair<const char*, std::string>> rules = {
      {"boolean", "TOTAL > 10 AND STATUS $== \"open\""},
      {"arithmetic", "ROUND(PRICE*QTY*(1+TAX/100)-DISC,2) > 100"},
      {"aggregate", "COUNT(FILTER(ITEMS, _[\"qty\"] > 1 AND LEFT(_[\"sku\"], 2) $== \"AB\")) >= 1"},
  };
  std::printf("| rule | compile us | compile+run us | evaluate us | checksum |\n|---|---:|---:|---:|---|\n");
  for (const auto& [name, src] : rules) {
    std::uint64_t sum = 1469598103934665603ULL;
    double t0 = cpu_seconds();
    for (int i = 0; i < n; ++i) { sel::Program p = sel::compile(src); sum = fnv(p.source(), sum); }
    double t_compile = cpu_seconds() - t0;

    sel::Value ctx = context();
    t0 = cpu_seconds();
    for (int i = 0; i < n; ++i) { sel::Program p = sel::compile(src); sum = fnv(p.run(ctx).dump(), sum); }
    double t_both = cpu_seconds() - t0;

    t0 = cpu_seconds();
    for (int i = 0; i < n; ++i) sum = fnv(sel::evaluate(src, ctx).dump(), sum);
    double t_eval = cpu_seconds() - t0;
    std::printf("| %s | %.2f | %.2f | %.2f | %016llx |\n", name, t_compile / n * 1e6, t_both / n * 1e6,
                t_eval / n * 1e6, static_cast<unsigned long long>(sum));
  }
}
