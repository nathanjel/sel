// Plain usage — calling SEL from C++.
//
//   cmake -S cpp -B cpp/build -DSEL_BUILD_TOOLS=ON && cmake --build cpp/build
//   cpp/build/sel-example-plain
//
// The four files beside this one do the same thing through their own host API
// and print byte-identical output; tools/check-examples.sh diffs them. That is
// the point of the example as much as the code is: the differences you see
// between these files are the languages', never SEL's.
//
// The visible difference here is that C++ has no fromNative: there is no native
// map or array to convert from, so a context is built child by child. That is
// more typing and exactly the same value model.

#include "../../cpp/sel.hpp"

#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

namespace {

// Left-pad to a fixed width, so this file's columns line up with the four
// written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) {
    if (i) out += sep;
    out += parts[i];
  }
  return out;
}

struct Item { std::string sku, qty, price; };

}  // namespace

int main() {
  // 1 — evaluate something ----------------------------------------------------

  std::cout << "1. one-off\n";
  std::cout << "   2.50 + 2.50 => " << sel::evaluate("2.50 + 2.50").as_text() << "\n";

  // 2 — compile once, run per request -----------------------------------------
  // Parsing is cheap but not free, and a Program is immutable and reusable.

  std::cout << "2. compile once, run many\n";
  // EXAMPLE-BEGIN
  const sel::Program rule = sel::compile("IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")");
  for (const auto& row : std::vector<std::pair<std::string, std::string>>{
           {"3", "19.99"}, {"1", "5.00"}}) {
    sel::Value ctx = sel::Value::none();
    ctx.set("QTY", sel::Value::text(row.first));
    ctx.set("PRICE", sel::Value::text(row.second));
    ctx.set("LIMIT", sel::Value::text("50.00"));
    std::cout << "   QTY=" << row.first << " PRICE=" << row.second
              << " => " << rule.run(ctx).as_text() << "\n";
  }
  // EXAMPLE-END

  // 3 — building a context ------------------------------------------------------
  // Money is TEXT, never a double. C++ has no exact decimal type and SEL has no
  // floating point, so the host boundary is where that is said out loud.

  std::cout << "3. structured context\n";
  sel::Value order = sel::Value::none();
  order.set("CUSTOMER", sel::Value::text("Zażółć"));
  std::vector<sel::Value> items;
  for (const Item& it : std::vector<Item>{{"AB-1234", "3", "19.99"},
                                          {"CD-5678", "1", "5.01"}}) {
    sel::Value item = sel::Value::none();
    item.set("SKU", sel::Value::text(it.sku));
    item.set("QTY", sel::Value::text(it.qty));
    item.set("PRICE", sel::Value::text(it.price));
    items.push_back(item);
  }
  order.set("ITEMS", sel::Value::list(items));   // a list is keyed "1".."n"
  std::cout << "   first SKU => "
            << sel::compile("ITEMS[1][\"SKU\"]").run(order).as_text() << "\n";
  std::cout << "   total     => "
            << sel::compile("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])").run(order).as_text() << "\n";
  std::cout << "   0.10+0.20 => " << sel::evaluate("0.10 + 0.20").as_text() << "\n";

  // 4 — reading results back -----------------------------------------------------
  // A result is a Value: a scalar, children, both or neither.

  std::cout << "4. reading results\n";
  const sel::Value v = sel::evaluate("SPLIT(\"a,b,c\", \",\")");
  std::cout << "   size   => " << v.size() << "\n";
  std::cout << "   keys   => " << join(v.keys(), ",") << "\n";
  std::cout << "   [2]    => " << v.get("2")->as_text() << "\n";
  std::cout << "   scalar => " << v.as_text() << "\n";   // scalar context: first child
  // A host bool prints differently in all five languages (true/1/True/T), and
  // this file's output has to be byte-identical to its four siblings, so say it
  // in SEL's own spelling rather than the host's.
  std::cout << "   bool   => " << (sel::evaluate("1 < 2").as_bool() ? "TRUE" : "FALSE") << "\n";

  // 5 — the context is mutated, so rules hand values back -------------------------

  std::cout << "5. variables the rule set\n";
  sel::Value ctx = sel::Value::none();
  ctx.set("QTY", sel::Value::text("3"));
  ctx.set("PRICE", sel::Value::text("19.99"));
  sel::compile("NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT").run(ctx);
  for (const char* name : {"NET", "VAT", "GROSS"}) {
    std::cout << "   " << pad(name, 5) << " => " << ctx.get(name)->as_text() << "\n";
  }

  // 6 — errors ---------------------------------------------------------------------
  // Every failure is a SelError carrying a stable code and the position of the
  // node that actually failed. Assert on code(), never on the message.

  std::cout << "6. errors\n";
  for (const char* src : {"3 + \"A\"", "NOSUCH(1)", "IF(1, \"a\", \"b\")",
                          "ABORT(\"no stock\")"}) {
    try {
      sel::evaluate(src);
      std::cout << "   " << pad(src, 17) << " => no error\n";
    } catch (const sel::SelError& e) {
      std::cout << "   " << pad(src, 17) << " => " << e.code()
                << " at " << e.line() << ":" << e.col() << "\n";
    }
  }

  // 7 — which fields does this rule read? -------------------------------------------
  // Found statically, without running it.

  std::cout << "7. dependencies\n";
  std::cout << "   " << join(sel::compile(
      "T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"").dependencies(), " ") << "\n";
  return 0;
}
