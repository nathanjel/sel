// Drives examples/order-validation.sel through the C++ host API and prints a
// canonical report. examples/e2e.mjs and examples/e2e.php do the same through
// theirs, and tools/e2e.sh diffs every implementation's output.
//
// This is the test that exercises what the project is actually for — everything
// else tests the language, this tests the promise.
//
// Note that prices are strings, not doubles. C++ has no exact decimal type and
// SEL has no floating point, so the host boundary is where that has to be said
// out loud: 0.1 + 0.2 must not become 0.30000000000000004 on one side of the
// wire and 0.30 on the other.

#include "../sel.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Item {
  std::string sku, qty, price;
};

struct Scenario {
  std::string name;
  std::string customer, postcode, credit_limit;
  std::vector<Item> items;
};

sel::Value build(const Scenario& s) {
  sel::Value ctx = sel::Value::none();
  ctx.set("CUSTOMER", sel::Value::text(s.customer));
  ctx.set("POSTCODE", sel::Value::text(s.postcode));
  ctx.set("CREDIT_LIMIT", sel::Value::text(s.credit_limit));

  // SEL lists are keyed from 1, so the host boundary renumbers rather than
  // exposing a 0-based array — ITEMS[1] must mean the first line everywhere.
  std::vector<sel::Value> items;
  for (const Item& i : s.items) {
    sel::Value item = sel::Value::none();
    item.set("SKU", sel::Value::text(i.sku));
    item.set("QTY", sel::Value::text(i.qty));
    item.set("PRICE", sel::Value::text(i.price));
    items.push_back(std::move(item));
  }
  ctx.set("ITEMS", sel::Value::list(std::move(items)));
  return ctx;
}

}  // namespace

int main() {
  std::ifstream in("examples/order-validation.sel");
  if (!in) {
    std::cerr << "run from the repository root: examples/order-validation.sel not found\n";
    return 2;
  }
  std::ostringstream ss;
  ss << in.rdbuf();

  const sel::Program program = sel::compile(ss.str());

  const std::vector<Scenario> scenarios = {
      {"valid order", "Zażółć Gęślą", "31-874", "1000.00",
       {{"AB-1234", "3", "19.99"}, {"CD-5678", "1", "5.01"}}},
      {"blank customer", "   ", "31-874", "1000.00", {{"AB-1234", "1", "1.00"}}},
      {"bad postcode", "Anna", "318744", "1000.00", {{"AB-1234", "1", "1.00"}}},
      {"no lines", "Anna", "31-874", "1000.00", {}},
      {"zero quantity", "Anna", "31-874", "1000.00",
       {{"AB-1234", "1", "1.00"}, {"CD-5678", "0", "2.00"}}},
      {"malformed sku", "Anna", "31-874", "1000.00", {{"oops", "1", "1.00"}}},
      {"over credit limit", "Anna", "31-874", "10.00", {{"AB-1234", "3", "19.99"}}},
      {"exact-cent arithmetic", "Anna", "31-874", "0.30",
       {{"AB-1234", "1", "0.10"}, {"CD-5678", "1", "0.20"}}},
  };

  std::string out = "dependencies:";
  for (const std::string& d : program.dependencies()) out += " " + d;
  out += "\n";

  for (const Scenario& s : scenarios) {
    std::string result;
    try {
      sel::Value ctx = build(s);
      result = program.run(ctx).dump();
    } catch (const sel::SelError& e) {
      result = "!" + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
    } catch (const std::exception& e) {
      result = std::string("!HOST ") + e.what();
    }
    std::string name = s.name;
    while (name.size() < 24) name += ' ';
    out += name + " " + result + "\n";
  }

  std::cout << out;
  return 0;
}
