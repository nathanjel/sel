// Concurrent translation, for the thread sanitizer (T10, CPP-C12).
//
// `Map::check_numeric_guard` recorded the dialects it had checked in a plain
// std::set, written on the FIRST numeric-guard use from translate(), so two
// threads translating at once raced on it (TSan: 4 threads x 200 translations of
// `X > 5` on mariadb reported a data race in sel_sql_map.cpp). The contract this
// pins: once the map is registered, translate() may be called from several
// threads, each with its own program and bindings; the map is read-only from then
// on, and every thread gets the same bytes. Sharing ONE Bindings or Program
// between threads is not promised (the Value handle's reference count is not
// atomic) and is not done here.
//
//   make -C cpp tsan
//
// Run without a sanitizer it is an ordinary determinism check.

#include "../sel.hpp"
#include "../sel_sql.hpp"
#include "../sel_sql_map.hpp"

#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

int main() {
  using sel::sql::Binding;
  using sel::sql::Sql;
  using sel::sql::SqlKind;

  const int threads = 4;
  const int rounds = 200;
  const char* dialects[] = {"mariadb", "mysql", "postgresql"};
  std::vector<std::string> first(threads);
  std::atomic<int> bad{0};
  std::atomic<bool> go{false};

  std::vector<std::thread> pool;
  for (int t = 0; t < threads; ++t) {
    pool.emplace_back([&, t] {
      while (!go.load()) std::this_thread::yield();
      std::string mine;
      for (int r = 0; r < rounds; ++r) {
        for (const char* dialect : dialects) {
          sel::sql::Bindings bindings({{"X", Binding::column("x", "o", SqlKind::Unknown)}});
          std::string s = Sql::translate(sel::compile("X > 5"), dialect, bindings).as_value();
          if (r == 0) mine += s + "\n";
          else if (mine.find(s) == std::string::npos) ++bad;
        }
      }
      first[t] = mine;
    });
  }
  go.store(true);
  for (auto& th : pool) th.join();

  for (int t = 1; t < threads; ++t) {
    if (first[t] != first[0]) ++bad;
  }
  if (bad.load() != 0) {
    std::printf("FAIL: %d disagreeing translation(s)\n", bad.load());
    return 1;
  }
  std::printf("ok: %d threads x %d rounds x 3 dialects agree\n", threads, rounds);
  return 0;
}
