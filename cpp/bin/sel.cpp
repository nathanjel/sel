// SEL command line: evaluate an expression, a file, or start a REPL.
//
//   sel -e 'EXPR'          evaluate and print
//   sel file.sel           evaluate a file
//   sel --deps -e 'EXPR'   print the variables the expression reads
//   sel --functions        list the function table
//   sel                    REPL, keeping one context across lines

#include "../sel.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

std::string show(const sel::Value& v) {
  if (v.size() == 0) {
    if (v.kind() == sel::Kind::Text) return v.scalar();
    if (v.kind() == sel::Kind::Bool) return v.boolean_scalar() ? "TRUE" : "FALSE";
    if (v.kind() == sel::Kind::Bin) return "bin:" + v.dump().substr(1);
  }
  return v.dump();
}

void report(const sel::SelError& e) {
  std::cerr << e.code() << " at line " << e.line() << " column " << e.col() << ": " << e.message()
            << "\n";
}

}  // namespace

int main(int argc, char** argv) {
  std::vector<std::string> argl(argv + 1, argv + argc);

  bool want_deps = false;
  std::vector<std::string> args;
  for (const std::string& a : argl) {
    if (a == "--deps") want_deps = true;
    else args.push_back(a);
  }

  if (!args.empty() && args[0] == "--functions") {
    for (const std::string& n : sel::function_names()) std::cout << n << "\n";
    return 0;
  }

  bool have_source = false;
  std::string source;
  if (args.size() >= 2 && args[0] == "-e") {
    source = args[1];
    have_source = true;
  } else if (!args.empty()) {
    std::ifstream in(args[0]);
    if (!in) {
      std::cerr << "cannot read " << args[0] << "\n";
      return 1;
    }
    std::ostringstream ss;
    ss << in.rdbuf();
    source = ss.str();
    have_source = true;
  }

  if (have_source) {
    try {
      const sel::Program program = sel::compile(source);
      if (want_deps) {
        for (const std::string& d : program.dependencies()) std::cout << d << "\n";
      } else {
        std::cout << show(program.run()) << "\n";
      }
    } catch (const sel::SelError& e) {
      report(e);
      return 1;
    }
    return 0;
  }

  // REPL: one context for the whole session, so assignments persist.
  sel::Value root = sel::Value::none();
  std::string line;
  std::cout << "sel> " << std::flush;
  while (std::getline(std::cin, line)) {
    if (line.find_first_not_of(" \t\r\n") != std::string::npos) {
      try {
        std::cout << show(sel::compile(line).run(root)) << "\n";
      } catch (const sel::SelError& e) {
        report(e);
      }
    }
    std::cout << "sel> " << std::flush;
  }
  std::cout << "\n";
  return 0;
}
