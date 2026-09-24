// A read-eval-print loop -- the whole of it, in C++.
//
//   cd cpp && make build/example-repl && cd ..
//   cpp/build/example-repl
//   cpp/build/example-repl < examples/repl/session.txt
//
// One context lives across lines, so a variable assigned on one line is there
// on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
// expression reads, and `:reset` empties the context. Errors print their code
// and position -- the message is human text and may differ between hosts; the
// code and the position may not.
//
// The four files beside this one print byte-identical output for the session in
// session.txt.

#include "../../cpp/sel.hpp"

#include <cstddef>
#include <iostream>
#include <string>
#include <vector>

namespace {

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

}  // namespace

// EXAMPLE-BEGIN repl
std::string show(const sel::Value& value) {
  if (value.is_bool()) return value.as_bool() ? "TRUE" : "FALSE";
  if (value.is_null()) return "NULL";
  if (value.size() > 0 || value.is_bin()) return value.dump();
  return value.as_text();
}

int main() {
  sel::Value context = sel::Value::none();
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.find_first_not_of(" \t\r\f\v") == std::string::npos) continue;
    std::cout << "sel> " << line << "\n";
    try {
      if (line == ":reset") {
        context = sel::Value::none();
      } else if (line.starts_with(":deps ")) {
        std::cout << join(sel::compile(line.substr(6)).dependencies(), " ") << "\n";
      } else {
        std::cout << show(sel::compile(line).run(context)) << "\n";
      }
    } catch (const sel::SelError& e) {
      std::cout << e.code() << " at " << e.line() << ":" << e.col() << "\n";
    }
  }
  return 0;
}
// EXAMPLE-END repl
