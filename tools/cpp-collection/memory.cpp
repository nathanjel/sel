// glibc allocator accounting; run each mode in a fresh process.
#include "sel.hpp"
#include <chrono>
#include <iostream>
#include <malloc.h>
#include <vector>

using Clock = std::chrono::steady_clock;
std::size_t bytes() {
  const auto info = mallinfo2();
  return info.uordblks + info.hblkhd;
}

int main(int argc, char** argv) {
  const std::string mode = argc > 1 ? argv[1] : "rows";
  std::vector<sel::Value> values;
  values.reserve(200000);
  const auto initial = bytes();
  const auto start = Clock::now();
  for (int i = 0; i < 200000; ++i) {
    if (mode == "rows") {
      values.push_back(sel::Value::record({"a", "b"},
          {sel::Value::text("short"), sel::Value::integer(i)}));
    } else if (mode == "num") {
      values.push_back(sel::Value::integer(i));
    } else {
      values.push_back(sel::Value::text("short"));
    }
  }
  const auto live = bytes();
  const auto built = Clock::now();
  values.clear();
  const auto end = Clock::now();
  const auto released = bytes();
  std::cout << "{\"mode\":\"" << mode << "\",\"header_bytes\":"
            << sizeof(sel::Value::Impl) << ",\"collection_bytes\":"
            << sizeof(sel::Value::Collection) << ",\"live_bytes\":"
            << live - initial << ",\"after_clear_bytes\":" << released - initial
            << ",\"build_ms\":"
            << std::chrono::duration<double, std::milli>(built - start).count()
            << ",\"destroy_ms\":"
            << std::chrono::duration<double, std::milli>(end - built).count()
            << "}\n";
}
