// Checks the C++ decimal core against the Python oracle.
//
//   cpp/build/check-decimal oracle.txt
//
// The decimal core lives in an anonymous namespace inside sel.cpp, because it is
// not part of the public interface — nothing outside the language should be
// doing arithmetic on SEL's internal representation. Including the translation
// unit is how this whitebox check reaches it; it links as its own binary, so
// there is no duplicate-symbol problem.

#include "../sel.cpp"

#include <fstream>
#include <iostream>

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "usage: check-decimal oracle.txt\n";
    return 2;
  }
  std::ifstream in(argv[1]);
  if (!in) {
    std::cerr << "cannot read " << argv[1] << "\n";
    return 2;
  }

  using namespace sel;

  std::size_t cases = 0;
  std::size_t mismatches = 0;   // counted in full; `failures` is only the display
  std::vector<std::string> failures;
  std::string line;

  while (std::getline(in, line)) {
    if (line.empty()) continue;
    // op|a|b|want — none of the fields can contain a `|`.
    const std::size_t p1 = line.find('|');
    const std::size_t p2 = line.find('|', p1 + 1);
    const std::size_t p3 = line.find('|', p2 + 1);
    const std::string op = line.substr(0, p1);
    const std::string as = line.substr(p1 + 1, p2 - p1 - 1);
    const std::string bs = line.substr(p2 + 1, p3 - p2 - 1);
    const std::string want = line.substr(p3 + 1);
    cases++;

    Dec a, b;
    dec_parse(as, a);
    dec_parse(bs, b);

    std::string got;
    try {
      if (op == "+") got = dec_format(dec_add(a, b));
      else if (op == "-") got = dec_format(dec_sub(a, b));
      else if (op == "*") got = dec_format(dec_mul(a, b));
      else if (op == "/") got = dec_format(dec_div(a, b));
      else if (op == "%") got = dec_format(dec_mod(a, b));
      else if (op == "cmp") got = std::to_string(dec_cmp(a, b));
      else if (op == "round") got = dec_format(dec_round(a, static_cast<int>(dec_to_int(b))));
      else if (op == "floor") got = dec_format(dec_floor(a));
      else if (op == "ceil") got = dec_format(dec_ceil(a));
      else if (op == "trunc") got = dec_format(dec_trunc(a));
      else {
        std::cerr << "unknown op " << op << "\n";
        return 2;
      }
    } catch (const SelError& e) {
      got = "THREW " + e.code();
    }

    if (got != want) {
      mismatches++;
      if (failures.size() < 20) {
        failures.push_back(as + " " + op + " " + bs + " => " + got + ", oracle says " + want);
      }
    }
  }

  std::cout << "cpp: " << cases << " cases, " << mismatches << " mismatches\n";
  for (const auto& f : failures) std::cout << "  " << f << "\n";
  if (mismatches > failures.size()) {
    std::cout << "  ... and " << (mismatches - failures.size()) << " more\n";
  }
  return mismatches ? 1 : 0;
}
