// Build with -std=c++23 -O2 -Icpp tools/benchmark-cpp-value.cpp cpp/sel.cpp.
// Run each mode in a fresh process to keep allocator retention comparable.
#include "sel.hpp"
#include <chrono>
#include <iostream>
#include <malloc.h>
#include <vector>

int main(int argc, char** argv) {
  using namespace sel;
  const std::string mode = argc > 1 ? argv[1] : "text";
  const int count = 200000;
  std::cout << "Value=" << sizeof(Value) << " Dec=" << sizeof(Dec)
            << " Impl=" << sizeof(Value::Impl) << '\n';
  if (mode == "aggregate") {
    std::vector<Value> items;
    for (int i=0;i<10000;++i) items.push_back(Value::integer(i));
    Value ctx=Value::none(); ctx.set("XS",Value::list(std::move(items)));
    for (const auto* expression : {"SUM(XS, _)", "MAP(XS, _ + 1)",
                                  "FILTER(XS, _ > 5000)", "ALL(XS, _ >= 0)"}) {
      auto program=compile(expression);
      program.run(ctx);
      auto start=std::chrono::steady_clock::now();
      std::size_t check=0;
      for (int r=0;r<100;++r) check+=program.run(ctx).size();
      std::cout<<expression<<" us="<<std::chrono::duration<double,std::micro>(
          std::chrono::steady_clock::now()-start).count()/100<<" check="<<check<<'\n';
    }
    return 0;
  }
  std::vector<Value> values; values.reserve(count);
  auto before=mallinfo2();
  auto start=std::chrono::steady_clock::now();
  for (int i=0;i<count;++i) {
    if (mode=="bool") values.push_back(Value::boolean(i%2));
    else if (mode=="num") values.push_back(Value::integer(i));
    else if (mode=="rows") values.push_back(Value::record({"a","b"},
        {Value::text("short"),Value::integer(i)}));
    else values.push_back(Value::text("short"));
  }
  auto elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
  auto after=mallinfo2();
  std::cout<<mode<<" count="<<count<<" build_ms="<<elapsed<<" retained_bytes="
           <<(after.uordblks+after.hblkhd-before.uordblks-before.hblkhd)<<'\n';
  start=std::chrono::steady_clock::now();
  values.clear();
  std::cout<<"destroy_ms="<<std::chrono::duration<double,std::milli>(
      std::chrono::steady_clock::now()-start).count()<<'\n';
}
