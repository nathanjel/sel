// Complex usage — the language's reach, from C++.
//
//   cmake -S cpp -B cpp/build -DSEL_BUILD_TOOLS=ON && cmake --build cpp/build
//   cpp/build/sel-example-complex
//
// examples/plain/ is the API. This is the language: aggregates, named binders,
// text and regex, structured results, and a rule that refuses. The four files
// beside this one print byte-identical output; tools/check-examples.sh diffs
// them.

#include "../../cpp/sel.hpp"

#include <cstddef>
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
struct Case { const char* label; const char* src; };

}  // namespace

int main() {
  // C++ has no fromNative — there is no native map or array to convert from —
  // so the order is built child by child. Money is TEXT, never a double.
  sel::Value order = sel::Value::none();
  order.set("CUSTOMER", sel::Value::text("Zażółć Gęślą"));
  order.set("POSTCODE", sel::Value::text("31-874"));
  order.set("CREDIT_LIMIT", sel::Value::text("100.00"));
  std::vector<sel::Value> items;
  for (const Item& it : std::vector<Item>{{"AB-1234", "3", "19.99"},
                                          {"CD-5678", "1", "5.01"},
                                          {"EF-9012", "2", "0.50"}}) {
    sel::Value item = sel::Value::none();
    item.set("SKU", sel::Value::text(it.sku));
    item.set("QTY", sel::Value::text(it.qty));
    item.set("PRICE", sel::Value::text(it.price));
    items.push_back(item);
  }
  order.set("ITEMS", sel::Value::list(items));   // a list is keyed "1".."n"

  // By value, not by reference: as_text() hands back a reference into the Value
  // the program returned, and that Value is a temporary here.
  auto ask = [&order](const char* src) -> std::string {
    return sel::compile(src).run(order).as_text();
  };

  // 1 — a rule set, not an expression -------------------------------------------
  // `;` separates statements and the last one is the answer. Intermediate names
  // are ordinary variables, so a long rule reads top to bottom.

  std::cout << "1. a rule set\n";
  std::cout << "   " << sel::compile(join({
      "NET   = SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])",
      "VAT   = ROUND(NET * 0.23, 2)",
      "GROSS = NET + VAT",
      "IF(GROSS > CREDIT_LIMIT, \"refer: \" & GROSS, \"accept: \" & GROSS)",
  }, "; ")).run(order).as_text() << "\n";

  // 2 — aggregates ---------------------------------------------------------------
  // No loops. A body expression is evaluated once per element with `_` bound to
  // the element and `_K` to its key.

  std::cout << "2. aggregates\n";
  std::cout << "   lines        => " << ask("COUNT(ITEMS)") << "\n";
  std::cout << "   net          => " << ask("SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])") << "\n";
  std::cout << "   all in stock => "
            << ask("IF(ALL(ITEMS, _[\"QTY\"] > 0), \"TRUE\", \"FALSE\")") << "\n";
  std::cout << "   any > 10     => "
            << ask("IF(ANY(ITEMS, _[\"PRICE\"] > 10.00), \"TRUE\", \"FALSE\")") << "\n";
  std::cout << "   dearest      => " << ask("MAX(MAP(ITEMS, _[\"PRICE\"]))") << "\n";

  // 3 — MAP renumbers, FILTER keeps the keys --------------------------------------
  // A filtered list stays addressable the way its source was, which is why the
  // dump below has holes in it. That is the contract, not an accident.

  std::cout << "3. map and filter\n";
  std::cout << "   skus         => " << ask("JOIN(MAP(ITEMS, _[\"SKU\"]), \", \")") << "\n";
  std::cout << "   bulk keys    => "
            << join(sel::compile("FILTER(ITEMS, _[\"QTY\"] > 1)").run(order).keys(), ",") << "\n";
  std::cout << "   keyed        => "
            << sel::compile("MAP(FILTER(ITEMS, _[\"QTY\"] > 1), _K & \":\" & _[\"SKU\"])")
                   .run(order).dump() << "\n";

  // 4 — naming the binder, for nesting ---------------------------------------------
  // `_` is the innermost element. The three-argument form names it instead, which
  // is the only way an outer element stays reachable from an inner body.

  std::cout << "4. named binders\n";
  std::cout << "   " << sel::evaluate(
      "R[1] = (1, 2); R[2] = (3, 4); "
      "IF(ALL(R, ROW, ALL(ROW, _ > 0)), \"all positive\", \"no\")").as_text() << "\n";

  // 5 — text and regex ---------------------------------------------------------------
  // Patterns are a portable subset, checked at compile time: a regex that would
  // mean different things on different hosts is refused rather than guessed at.

  std::cout << "5. text and regex\n";
  // UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
  // ę below come back unchanged. That is not a shortcoming, it is the only way
  // five hosts can agree. Measured on a sharp s: JS's toUpperCase and Python's
  // str.upper both answer SS, PHP's strtoupper answers ß, and C's toupper cannot
  // see it at all. SEL answers ß on all five, because it never asks the host.
  std::cout << "   upper        => " << ask("UPPER(CUSTOMER)") << "\n";
  std::cout << "   initials     => "
            << ask("JOIN(MAP(SPLIT(CUSTOMER, \" \"), LEFT(_, 1)), \".\")") << "\n";
  std::cout << "   postcode     => "
            << ask("IF(RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE), \"ok\", \"bad\")") << "\n";
  // RGROUPS puts the WHOLE match at "1", so the first capture is "2".
  std::cout << "   area         => " << ask("RGROUPS('^([0-9]{2})-', POSTCODE)[\"2\"]") << "\n";
  std::cout << "   padded       => " << ask("PADL(COUNT(ITEMS), 3, \"0\")") << "\n";

  // 6 — asking whether a key is there --------------------------------------------------

  std::cout << "6. presence\n";
  std::cout << "   HAS SKU      => "
            << ask("IF(HAS(ITEMS[1], \"SKU\"), \"TRUE\", \"FALSE\")") << "\n";
  std::cout << "   HAS NOTE     => "
            << ask("IF(HAS(ITEMS[1], \"NOTE\"), \"TRUE\", \"FALSE\")") << "\n";
  std::string missing;
  try {
    missing = ask("ITEMS[1][\"NOTE\"]");
  } catch (const sel::SelError& e) {
    missing = e.code() + " at " + std::to_string(e.line()) + ":" + std::to_string(e.col());
  }
  std::cout << "   missing      => " << missing << "\n";

  // 7 — a rule that refuses -------------------------------------------------------------
  // ABORT is how a rule says "this is not valid", as distinct from "this could not
  // be computed". Both arrive as the same error type, told apart by the code.

  std::cout << "7. business refusal\n";
  for (const Case& c : std::vector<Case>{
           {"under limit",
            "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > CREDIT_LIMIT, "
            "ABORT(\"over credit limit\"), \"ok\")"},
           {"over limit",
            "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > 50.00, "
            "ABORT(\"over credit limit\"), \"ok\")"},
           {"blank name", "IF(TRIM(\"   \") $== \"\", ABORT(\"customer required\"), \"ok\")"}}) {
    // The answer is settled before anything is printed: a partly written line
    // cannot be taken back once ABORT has thrown through the middle of it.
    std::string answer;
    try {
      answer = ask(c.src);
    } catch (const sel::SelError& e) {
      answer = e.code() + ": " + e.message();
    }
    std::cout << "   " << pad(c.label, 11) << " => " << answer << "\n";
  }

  // 8 — where a failure actually happened -------------------------------------------------
  // The position is the node that failed, not the statement or the call that
  // contains it. That is what makes a long rule debuggable.

  std::cout << "8. error positions\n";
  for (const char* src : {"1 + ROUND(2 + \"x\", 2)", "SUM(ITEMS, _[\"QTY\"] * _[\"NOPE\"])"}) {
    try {
      sel::compile(src).run(order);
    } catch (const sel::SelError& e) {
      std::cout << "   " << e.code() << " at " << e.line() << ":" << e.col()
                << "  " << src << "\n";
    }
  }
  return 0;
}
