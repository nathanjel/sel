// c++ -std=c++23 -O2 -Icpp tools/metadata/cpp.cpp cpp/sel.cpp -o /tmp/metadata-cpp
#include "sel.hpp"
#include <cassert>
#include <chrono>
#include <iostream>
#if defined(__GLIBC__)
#include <malloc.h>
#endif
using namespace sel;
void churn(int n, const Value& leaf) {
  for(int i=0;i<n;i++) Value::record({"field_"+std::to_string(i),"id"},{leaf,leaf});
}
void aliases(int n, const Value& leaf) {
  auto join=compile("LINK(L, R, O, C, O[\"id\"] == C[\"id\"])");
  Value ctx=Value::none(); ctx.set("R",Value::list({Value::record({"id"},{leaf})}));
  for(int i=0;i<n;i++) {
    ctx.set("L",Value::list({Value::record({"id","alias_"+std::to_string(i)},{leaf,leaf})}));
    join.run(ctx);
  }
}
std::size_t retained() {
#if defined(__GLIBC__)
  auto m=mallinfo2(); return m.uordblks+m.hblkhd;
#else
  return 0; // Retention measurement is glibc-specific; correctness checks are portable.
#endif
}
int main(int argc,char**argv) {
  const Value leaf=Value::text("1");
  Value ctx=Value::none();ctx.set("X",leaf);ctx.set("Y",Value::text("two"));
  auto program=compile("RECORD(\"id\", X, \"name\", Y)");program.run(ctx);
  if(argc>1 && std::string(argv[1])=="bench") {
#if !defined(__GLIBC__)
    std::cerr<<"retained-byte measurements require glibc\n";
    return 2;
#endif
    std::cout<<"{\"runtime\":\"GCC "<<__VERSION__<<"\",\"steady_us\":[";
    for(int r=0;r<5;r++) {
      auto start=std::chrono::steady_clock::now();
      for(int i=0;i<20000;i++) program.run(ctx);
      double us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count()/20000;
      if(r) std::cout<<',';
      std::cout<<us;
    }
    auto start=retained();churn(20000,leaf);auto shapes=retained();aliases(5000,leaf);auto both=retained();
    std::cout<<"],\"shapes_retained\":"<<static_cast<long long>(shapes)-static_cast<long long>(start)
             <<",\"shapes_aliases_retained\":"<<static_cast<long long>(both)-static_cast<long long>(start)<<"}\n";
  } else {
    auto held=Value::record({"held"},{leaf});auto prepared=program.run(ctx).shape();
    churn(2000,leaf);auto again=Value::record({"held"},{leaf});
    assert(held.shape()!=again.shape() && held.eql(again));
    assert(held.structural_hash()==again.structural_hash());
    assert(program.run(ctx).shape()==prepared);
    std::vector<std::string> wide;
    for(int i=0;i<257;i++)wide.push_back("wide_"+std::to_string(i));
    for(const auto&keys: {wide,std::vector<std::string>{std::string(16385,'x')}}) {
      std::vector<Value> values(keys.size(),leaf);
      auto a=Value::record(keys,values),b=Value::record(keys,values);
      assert(a.shape()!=b.shape() && a.eql(b));
    }
    auto dynamic=compile("RECORD(K, X)");
    ctx.set("K",Value::text("first"));assert(dynamic.run(ctx).has("first"));
    ctx.set("K",Value::text("second"));assert(dynamic.run(ctx).has("second"));
    assert(compile("RECORD(\"id\", X, \"id\", Y)").run(ctx).get("id")->scalar()=="two");
    aliases(1000,leaf);
    std::cout<<"C++ metadata checks passed\n";
  }
}
