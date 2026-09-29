// The regex validator's verdict on each pattern read from stdin, one per line:
// `A` (accepted) or `R` (refused with E_REGEX_SYNTAX; E_BAD_ARG counts as a refusal
// too). The driver tools/check-regex-ambiguity-diff.py compares this with the
// reference validator, tools/regex-ambiguity-ref.py.
//
//   cpp/build/regex_verdict [i] < patterns.txt

#include "../sel.hpp"
#include "../sel_ast.hpp"

#include <iostream>
#include <string>

int main(int argc, char** argv) {
  const bool ic = argc > 1 && std::string(argv[1]) == "i";
  std::string line;
  while (std::getline(std::cin, line)) {
    try {
      (void)sel::validate_pattern(line, sel::Pos{}, ic);
      std::cout << "A\n";
    } catch (const sel::SelError&) {
      std::cout << "R\n";
    }
  }
  return 0;
}
