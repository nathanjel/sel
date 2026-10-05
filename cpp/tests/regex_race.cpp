// Concurrent regex use against a cache that is being evicted, for the thread
// sanitizer (a race found while measuring the cache's cost).
//
// The compiled-pattern cache is bounded (spec §7.8). Four threads cycling 300
// patterns through its 256 slots evict entries that other threads are still
// searching with; the entries are shared_ptrs precisely so that is harmless, and
// each thread also keeps its last-used pattern without the lock. Without a
// sanitizer this is an ordinary determinism check.
//
//   make -C cpp tsan-regex

#include "../sel.hpp"

#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

int main() {
  std::atomic<int> bad{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < 4; ++t) {
    threads.emplace_back([t, &bad] {
      for (int i = 0; i < 600; ++i) {
        const std::string k = std::to_string((i * 7 + t) % 300);
        try {
          const std::string got =
              sel::evaluate("RMATCH(\"^k" + k + "$\", \"k" + k +
                            "\") AND LEN(RREPLACE(\"a\", \"<$0>\", \"banana\")) == 12").dump();
          if (got != "TRUE") ++bad;
        } catch (...) {
          ++bad;
        }
      }
    });
  }
  for (auto& th : threads) th.join();
  std::printf("regex cache race: 4 threads x 600 rounds over 300 patterns, %d wrong answers\n", bad.load());
  return bad.load() == 0 ? 0 : 1;
}
