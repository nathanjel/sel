#include "sel.hpp"
#include <cstdio>
#include <functional>
#include <memory>
using namespace sel;
static void T(const char* n, std::function<Value()> f) {
  std::string o;
  Value v; try { v = f(); } catch (const SelError& e) { std::printf("%-38s ERR(build) %s %s\n", n, e.code().c_str(), e.what()); return; } catch (const std::exception& e) { std::printf("%-38s ERR(build) std %s\n", n, e.what()); return; }
  try { std::string d = v.dump(); o = "built; dump OK " + d.substr(0, 70); }
  catch (const SelError& e) { o = std::string("ERR ") + e.code() + " " + std::string(e.what()).substr(0, 60); }
  catch (const std::exception& e) { o = std::string("ERR std ") + e.what(); }
  std::printf("%-38s %s\n", n, o.c_str());
}
int main() {
  std::string bad = "a\xff";
  T("text(bad) [control]", [&] { return Value::text(bad); });
  T("none().set(bad) [control]", [&] { Value v = Value::none(); v.set(bad, Value::text("1")); return v; });
  T("record([bad],[1])", [&] { return Value::record({bad}, {Value::text("1")}); });
  T("record([bad,bad],[1,2]) dup path", [&] { return Value::record({bad, bad}, {Value::text("1"), Value::text("2")}); });
  T("record([bad]) → INDEXES", [&] { Value ctx = Value::none(); ctx.set("X", Value::record({bad}, {Value::text("1")})); return compile("INDEXES(X)").run(ctx); });
  T("record([bad]) → COUNT", [&] { Value ctx = Value::none(); ctx.set("X", Value::record({bad}, {Value::text("1")})); return compile("COUNT(X)").run(ctx); });
  T("record([bad]) → X[\"x\"]=1,SIZE", [&] { Value ctx = Value::none(); ctx.set("X", Value::record({bad}, {Value::text("1")})); return compile("MAP(X, _)").run(ctx); });
  T("shaped(RecordShape{bad})", [&] { return Value::shaped(std::make_shared<RecordShape>(std::vector<std::string>{bad}), {Value::text("1")}); });
  T("shaped duplicate keys", [&] { return Value::shaped(std::make_shared<RecordShape>(std::vector<std::string>{"a", "a"}), {Value::text("1"), Value::text("2")}); });
  T("record count mismatch", [&] { return Value::record({"a"}, {Value::text("1"), Value::text("2")}); });
  T("record duplicate keys", [&] { return Value::record({"a", "a"}, {Value::text("1"), Value::text("2")}); });
  T("bin(any bytes) [n/a]", [&] { return Value::bin(std::string("\x00\xff", 2)); });
  { Dec d; d.neg = false; d.digits = "1"; d.scale = 1000001; T("num(Dec scale 1000001)", [&] { return Value::num(d); }); }
  { Dec d; d.neg = false; d.digits = std::string(1000001, '1'); d.scale = 0; T("num(Dec 1000001 int digits)", [&] { return Value::num(d); }); }
  { Dec d; d.neg = true; d.digits = "0"; d.scale = 0; T("num(Dec neg zero)", [&] { return Value::num(d); }); }
  { Dec d; d.neg = false; d.digits = "007"; d.scale = 0; T("num(Dec \"007\")", [&] { return Value::num(d); }); }
  { Dec d; d.neg = false; d.digits = "x"; d.scale = 0; T("num(Dec \"x\")", [&] { return Value::num(d); }); }
  { Dec d; d.neg = false; d.digits = "7"; d.scale = -1; T("num(Dec scale -1)", [&] { return Value::num(d); }); }
  { Dec d; T("num(Dec{} default)", [&] { return Value::num(d); }); }
  { Dec d; d.small = true; d.mantissa = 5; d.digits = ""; d.scale = 1000001; T("num(Dec small mantissa scale 1000001)", [&] { return Value::num(d); }); }
  T("num(\"1\"x1000001) [control]", [&] { return Value::num(std::string(1000001, '1')); });
  T("integer(LLONG_MAX) [n/a]", [&] { return Value::integer(9223372036854775807LL); });
  return 0;
}
