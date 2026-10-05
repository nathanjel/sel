// Concurrent registration and compilation, for the thread sanitizer.
//
// `register_function` writes the host-function table while `compile()` and `run()`
// read it. Go guards its table with an RWMutex and Lisp with a lock; here the table
// is a plain std::map, so a server that adds a function while requests compile
// programs is a data race (undefined behaviour), not merely a missing feature.
//
// The contract this pins, the one the other hosts already give: registering (or
// replacing) a host function may happen while other threads compile and run programs;
// a program compiled before the call keeps the function it was compiled against
// (spec/SPEC.md §8.1), so a running program is never handed a half-replaced entry.
// Sharing one Program between threads is tests/program_race.cpp's subject, not this one's.
//
//   make -C cpp tsan-registry
//
// Without a sanitizer it is an ordinary determinism check.

#include "../sel.hpp"

#include <atomic>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

int main() {
  using namespace sel;
  register_function("T12_RACE", 0, 1, [](HostArgs&) { return Value::text("v0"); });

  std::atomic<bool> stop{false};
  std::atomic<int> bad{0};
  std::atomic<int> compiled{0};

  std::vector<std::thread> readers;
  for (int t = 0; t < 3; ++t) {
    readers.emplace_back([&, t] {
      while (!stop.load()) {
        try {
          const Program p = compile("T12_RACE(" + std::to_string(t) + ")");
          Value ctx = Value::none();
          const std::string got = p.run(ctx).as_text();
          // Any generation is fine; a torn or empty answer is not.
          if (got.size() < 2 || got[0] != 'v') bad++;
          compiled++;
        } catch (const std::exception&) {
          bad++;
        }
      }
    });
  }
  std::thread writer([&] {
    for (int i = 1; i <= 300; ++i) {
      const std::string tag = "v" + std::to_string(i);
      register_function("T12_RACE", 0, 1, [tag](HostArgs&) { return Value::text(tag); });
      // and distinct names: a table insert, not only a replace
      register_function("T12_RACE_" + std::to_string(i), 0, 0, [tag](HostArgs&) { return Value::text(tag); });
    }
    stop = true;
  });
  writer.join();
  for (auto& r : readers) r.join();

  if (bad.load() != 0) {
    std::fprintf(stderr, "registry race: %d bad answers in %d compilations\n", bad.load(), compiled.load());
    return 1;
  }
  std::printf("registry race: %d compilations against 300 registrations, all answers whole\n", compiled.load());
  return 0;
}
