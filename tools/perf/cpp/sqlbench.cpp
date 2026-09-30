// C++ SQL-layer benchmark driver (worklist CPP-P3, CPP-P14).
//
//   g++ -std=c++23 -O2 -o cpp/build/sqlbench tools/perf/cpp/sqlbench.cpp cpp/build/sel_sql*.o cpp/build/sel.o
//   cpp/build/sqlbench fold 1000 4000 16000
//   cpp/build/sqlbench bindings 20000 2 51 501
//   cpp/build/sqlbench plan 20 160 640      # plan_hybrid: an unsupported step, then N FILTERs (CPP-P24)
//   cpp/build/sqlbench hybrid 200 50000      # execute_hybrid against an unrelated N-row context (CPP-P24)
//
// Prints CPU microseconds per call and an FNV checksum of the emitted SQL, so a
// speedup that changes a byte is visible. `fold` translates literal-list programs
// (ANY/IN/JOIN over n elements); `bindings` translates one small rule many times
// against binding sets of different sizes.
#include "../../../cpp/sel_sql.hpp"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <iostream>
#include <string>
#include <vector>

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Sql;
using sel::sql::SqlKind;

static double cpu_seconds() {
  timespec ts;
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return static_cast<double>(ts.tv_sec) + static_cast<double>(ts.tv_nsec) * 1e-9;
}

static std::uint64_t fnv(const std::string& s, std::uint64_t h = 1469598103934665603ULL) {
  for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
  return h;
}

static std::string list(int n) {
  std::string s = "(";
  for (int i = 0; i < n; ++i) { if (i) s += ", "; s += std::to_string(i); }
  return s + ")";
}

int main(int argc, char** argv) {
  if (argc < 3) { std::cerr << "usage: sqlbench fold|bindings ...\n"; return 2; }
  const std::string mode = argv[1];
  if (mode == "fold") {
    const Bindings b({{"X", Binding::column("x", "o", SqlKind::Num)}});
    const char* shapes[] = {"any", "in", "join"};
    for (const char* shape : shapes) {
      for (int a = 2; a < argc; ++a) {
        const int n = std::atoi(argv[a]);
        std::string src;
        if (std::string(shape) == "any") src = "ANY(" + list(n) + ", _ == X)";
        else if (std::string(shape) == "in") src = "X IN " + list(n);
        else { std::string parts = "JOIN((\"a\""; for (int i = 1; i < n; ++i) parts += ", \"b\""; src = parts + "), \",\") $== \"a\""; }
        const sel::Program prog = sel::compile(src);
        const double t0 = cpu_seconds();
        const std::string sql = Sql::translate(prog, "mariadb", b).as_condition();
        const double dt = cpu_seconds() - t0;
        std::printf("| fold-%s | %d | %.4f | %zu | %016llx |\n", shape, n, dt, sql.size(),
                    static_cast<unsigned long long>(fnv(sql)));
      }
    }
    return 0;
  }
  if (mode == "bindings") {
    const int iterations = std::atoi(argv[2]);
    for (int a = 3; a < argc; ++a) {
      const int count = std::atoi(argv[a]);
      std::vector<std::pair<std::string, Binding>> items;
      items.push_back({"COL0", Binding::column("col0", "o", SqlKind::Num)});
      for (int i = 1; i < count; ++i)
        items.push_back({"COL" + std::to_string(i), Binding::column("col" + std::to_string(i), "o", SqlKind::Num)});
      const Bindings b(items);
      const sel::Program prog = sel::compile("COL0 > 5 AND COL0 < 10");
      std::string sql;
      const double t0 = cpu_seconds();
      for (int i = 0; i < iterations; ++i) sql = Sql::translate(prog, "mariadb", b).as_condition();
      const double dt = cpu_seconds() - t0;
      std::printf("| bindings-%d | %d | %.2f us/call | %zu | %016llx |\n", count, iterations,
                  dt * 1e6 / iterations, sql.size(), static_cast<unsigned long long>(fnv(sql)));
    }
    return 0;
  }
  if (mode == "plan") {
    const int iterations = std::atoi(argv[2]);
    const Bindings rb({{"ORDERS", Binding::relation("orders", "o", {{"id", Binding::column("id", "o", SqlKind::Num)}, {"amount", Binding::column("amount", "o", SqlKind::Num)}, {"name", Binding::column("name", "o", SqlKind::Text)}})}});
    for (int a = 3; a < argc; ++a) {
      const int n = std::atoi(argv[a]);
      // An unsupported step (a host-side ABORT in the predicate) after the first 10 FILTERs:
      // the prefix search has to try the long prefixes first and fail.
      std::string src = "ORDERS";
      for (int i = 0; i < 10; ++i) src += " .> FILTER(_[\"amount\"] > " + std::to_string(i) + ")";
      src += " .> FILTER(ABORT(\"x\") == 1)";
      for (int i = 0; i < n; ++i) src += " .> FILTER(_[\"amount\"] > " + std::to_string(i) + ")";
      const sel::Program prog = sel::compile(src);
      std::string kind;
      const double t0 = cpu_seconds();
      for (int i = 0; i < iterations; ++i) {
        const auto plan = Sql::plan_hybrid(prog, "mariadb", rb);
        kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
      }
      const double dt = cpu_seconds() - t0;
      std::printf("| plan-%d | %d | %.3f ms/call | %s |\n", n, iterations, dt * 1e3 / iterations, kind.c_str());
    }
    return 0;
  }
  if (mode == "hybrid") {
    // execute_hybrid over a context holding a big variable the program never touches:
    // the caller's context must not be written to, but it need not be deep-copied.
    const int iterations = std::atoi(argv[2]);
    for (int a = 3; a < argc; ++a) {
      const int rows = std::atoi(argv[a]);
      sel::Value ctx = sel::Value::none();
      std::vector<sel::Value> big;
      big.reserve(rows);
      for (int i = 0; i < rows; ++i) {
        sel::Value r = sel::Value::none();
        r.set("id", sel::Value::num(std::to_string(i)));
        r.set("name", sel::Value::text("row" + std::to_string(i)));
        big.push_back(std::move(r));
      }
      ctx.set("BIG", sel::Value::list(std::move(big)));
      const sel::Program prog = sel::compile("Y = 5; Y + 1");
      const auto plan = Sql::plan_hybrid(prog, "mariadb");
      const Sql::DbRunner runner = [](const std::string&, const std::vector<sel::Value>&) { return sel::Value::none(); };
      std::string out;
      const double t0 = cpu_seconds();
      for (int i = 0; i < iterations; ++i) out = Sql::execute_hybrid(plan, runner, ctx).dump();
      const double dt = cpu_seconds() - t0;
      const bool leaked = ctx.has("Y");
      std::printf("| hybrid-%d | %d | %.2f us/call | caller-written=%d | %016llx |\n", rows, iterations,
                  dt * 1e6 / iterations, leaked ? 1 : 0, static_cast<unsigned long long>(fnv(out)));
    }
    return 0;
  }
  return 2;
}
