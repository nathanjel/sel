// The shape tools/gen-sql-cases.mjs emits into case_data.cpp, and that
// cpp/bin/sqlt reads.
//
// Harness, not library: this lives under bin/ for the same reason
// python/bin/case_data.py does. Nothing an application links carries it.

#ifndef SEL_SQLT_CASE_DATA_HPP
#define SEL_SQLT_CASE_DATA_HPP

#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "../sel_sql.hpp"
#include "../sel_sql_map.hpp"

namespace sel::sqlt {

using sel::sql::Binding;
using sel::sql::DialectSpec;
using sel::sql::EntrySpec;
using sel::sql::Map;
using sel::sql::Section;
using sel::sql::SqlKind;

// One case from sql/cases/*.sqlt. Absent optional fields are nullptr rather than
// an empty string: `--- expect` with nothing after it is a case expecting empty
// SQL, which is not the same as a case with no expectation at all.
struct SqlCase {
  const char* name = nullptr;
  const char* at = nullptr;         // "file.sqlt:line"
  const char* dialect = nullptr;
  const char* source = nullptr;
  const char* expect = nullptr;
  const char* error = nullptr;      // "CODE" or "CODE line:col"
  const char* throws = nullptr;     // a registration mistake, not a refusal
  const char* params = nullptr;
  const char* as_ = nullptr;        // "value" (default) or "condition"
  const char* mode = nullptr;       // "inline" (default), "params" or "debug"
  bool strict = false;

  // Set when this case's bindings or registrations cannot be SPELLED with the
  // typed C++ constructors -- a JSON array where a name belongs, a section that
  // is not one of the three, a dialect declaration carrying `ops`. The dynamic
  // hosts refuse those at run time and the case asserts the refusal; here the
  // compiler refuses them, which is the same answer one stage earlier. The
  // generator will not let a case be unrepresentable AND expect a translation,
  // so reaching this is never lost coverage.
  const char* unrepresentable = nullptr;

  void (*register_fn)() = nullptr;  // null when the case registers nothing
  std::vector<std::pair<std::string, Binding>> (*bindings_fn)() = nullptr;
};

std::span<const SqlCase> sql_cases();

// --- helpers the generated file calls ----------------------------------------

// A list of Value children, keyed as SEL keys them: an item with no key takes
// the next 1-based position, and a keyed one takes its key.
struct TreeItem {
  std::optional<std::string> key;
  sel::Value value;
};

inline sel::Value value_tree(std::vector<TreeItem> items) {
  sel::Value v = sel::Value::list({});
  int i = 0;
  for (TreeItem& item : items) {
    v.set(item.key ? *item.key : std::to_string(++i), std::move(item.value));
  }
  return v;
}

// JSON has no byte string, so the corpus spells one as {"bin": "<hex>"}. The
// generator has already checked the hex is well formed.
inline std::string bin_from_hex(std::string_view hex) {
  const auto nib = [](char c) -> int {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return c - 'A' + 10;
  };
  std::string out;
  out.reserve(hex.size() / 2);
  for (std::size_t i = 0; i + 1 < hex.size(); i += 2) {
    out += static_cast<char>((nib(hex[i]) << 4) | nib(hex[i + 1]));
  }
  return out;
}

}  // namespace sel::sqlt

#endif  // SEL_SQLT_CASE_DATA_HPP
