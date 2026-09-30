// C++ SQL-layer benchmark driver (worklist CPP-P3, CPP-P14).
//
//   g++ -std=c++23 -O2 -o cpp/build/sqlbench tools/perf/cpp/sqlbench.cpp cpp/build/sel_sql*.o cpp/build/sel.o
//   cpp/build/sqlbench fold 1000 4000 16000
//   cpp/build/sqlbench bindings 20000 2 51 501
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
  return 2;
}
