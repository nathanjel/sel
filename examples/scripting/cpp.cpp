// Scripting an application's behaviour -- functions the host provides, from C++.
//
//   cd cpp && make build/example-scripting && cd ..
//   cpp/build/example-scripting              (from the repository root)
//
// SEL has no way to reach the world on its own, and that is the point: the
// application decides what a script may touch by registering functions. Here
// the warehouse gets four -- STOCK and WEIGHT to read the catalogue, RESERVE to
// take stock, NOTIFY to queue a message -- and fulfil.sel, a file the warehouse
// team owns, decides per order whether to ship, how, and whom to tell. The
// application stays the same when the policy changes.
//
// A registered function is strict: its arguments arrive evaluated, left to
// right, through the same typed readers the builtins use, so a script passing
// the wrong kind gets the usual error at the usual position.
//
// The four files beside this one print byte-identical output.
//
// The visible difference here is that C++ has no from_native: each order is
// built into a Value child by child, the way examples/plain/ builds one.

#include "../../cpp/sel.hpp"

#include <cstddef>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

// Left-pad to a fixed width, so this file's columns line up with the four
// written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

std::string read(const std::string& path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw std::runtime_error("cannot read " + path);
  std::ostringstream text;
  text << in.rdbuf();
  return text.str();
}

std::map<std::string, long long> inventory = {{"LAMP-01", 4}, {"DESK-02", 1}, {"CHAIR-03", 6}};
const std::map<std::string, std::string> weights = {
    {"LAMP-01", "1.6"}, {"DESK-02", "28.0"}, {"CHAIR-03", "7.5"}};
std::vector<std::string> outbox;

struct Line { std::string sku, qty; };
struct Order { std::string order, country; std::vector<Line> items; };

}  // namespace

int main() {
  // EXAMPLE-BEGIN register
  sel::register_function("STOCK", 1, 1, [](sel::HostArgs& args) {
    const auto found = inventory.find(args.text(0));
    return sel::Value::integer(found == inventory.end() ? 0 : found->second);
  });

  sel::register_function("RESERVE", 2, 2, [](sel::HostArgs& args) {
    const std::string sku = args.text(0);
    const long long qty = args.non_neg_int(1);
    const auto found = inventory.find(sku);
    if (found == inventory.end() || found->second < qty) return sel::Value::boolean(false);
    found->second -= qty;
    return sel::Value::boolean(true);
  });

  sel::register_function("WEIGHT", 1, 1, [](sel::HostArgs& args) {
    const auto found = weights.find(args.text(0));
    return sel::Value::text(found == weights.end() ? "0" : found->second);
  });

  sel::register_function("NOTIFY", 2, 2, [](sel::HostArgs& args) {
    outbox.push_back(args.text(0) + ": " + args.text(1));
    return sel::Value::boolean(true);
  });
  // EXAMPLE-END register

  // EXAMPLE-BEGIN run
  // After registering: names resolve now.
  const sel::Program fulfil = sel::compile(read("examples/scripting/fulfil.sel"));

  std::cout << "1. the script reads " << join(fulfil.dependencies(), ", ") << "\n";
  std::cout << "2. orders\n";
  const std::vector<Order> orders = {
      {"A-1", "PL", {{"LAMP-01", "2"}, {"CHAIR-03", "1"}}},
      {"A-2", "DE", {{"DESK-02", "1"}, {"CHAIR-03", "2"}}},
      {"A-3", "PL", {{"LAMP-01", "3"}}},
      {"A-4", "PL", {{"LAMP-01", "two"}}},
  };
  for (const Order& order : orders) {
    sel::Value ctx = sel::Value::none();
    ctx.set("ORDER", sel::Value::text(order.order));
    ctx.set("COUNTRY", sel::Value::text(order.country));
    std::vector<sel::Value> items;
    for (const Line& line : order.items) {
      sel::Value item = sel::Value::none();
      item.set("sku", sel::Value::text(line.sku));
      item.set("qty", sel::Value::text(line.qty));
      items.push_back(item);
    }
    ctx.set("ITEMS", sel::Value::list(items));
    std::string decision;
    try {
      decision = fulfil.run(ctx).as_text();
    } catch (const sel::SelError& e) {
      decision = e.code() + " at " + std::to_string(e.line()) + ":" + std::to_string(e.col());
    }
    std::cout << "   " << order.order << "  " << decision << "\n";
  }
  // EXAMPLE-END run

  std::cout << "3. outbox\n";
  for (const std::string& message : outbox) std::cout << "   " << message << "\n";
  std::cout << "4. stock left\n";
  for (const auto& [sku, left] : inventory)   // a std::map is sorted
    std::cout << "   " << pad(sku, 9) << " " << left << "\n";
  return 0;
}
