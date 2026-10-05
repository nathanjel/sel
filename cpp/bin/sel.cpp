// SEL command line: evaluate an expression, a file, or start a REPL. The
// contract every host's CLI keeps is docs/usage/repl.md's.
//
//   sel -e 'EXPR'          evaluate and print
//   sel file.sel           evaluate a file
//   sel --deps -e 'EXPR'   print the variables the expression reads
//   sel --functions        list the function table
//   sel --help | --version
//   sel                    REPL, keeping one context across lines

#include "../sel.hpp"
#include "read_file.hpp"
#include "show.hpp"

#include <iostream>
#include <string>
#include <vector>

#if defined(_WIN32)
#include <io.h>
#include <stdio.h>
#define SEL_STDIN_IS_TTY() (_isatty(_fileno(stdin)) != 0)
#else
#include <unistd.h>
#define SEL_STDIN_IS_TTY() (isatty(STDIN_FILENO) != 0)
#endif

// The package version, passed in by the build (cpp/Makefile and CMakeLists.txt
// read it from CMakeLists.txt's project() line, one of the version sources
// tools/check-version.sh relates), so the CLI never carries a copy of its own.
#ifndef SEL_VERSION
#define SEL_VERSION "unknown"
#endif

namespace {

using selbin::show;

constexpr const char* USAGE =
    "usage: sel [--deps] -e EXPR\n"
    "       sel [--deps] FILE\n"
    "       sel                    read one program per line from stdin, one context\n"
    "\n"
    "  -e EXPR      evaluate EXPR and print the result\n"
    "  FILE         evaluate the program in FILE\n"
    "  --deps       print the variables the program reads, one per line, instead\n"
    "  --functions  print every function name\n"
    "  -h, --help   print this text\n"
    "  --version    print the version\n";

void report(const sel::SelError& e) {
  std::cerr << e.code() << " at line " << e.line() << " column " << e.col() << ": " << e.message()
            << "\n";
}

int usage_error(const std::string& message) {
  std::cerr << "sel: " << message << "\n";
  return 2;
}

// SEL whitespace (spec/SPEC.md §2.2): space, TAB, CR, LF and nothing else. A
// line of NBSP or VT is a program, and the lexer says what is wrong with it.
bool is_blank(const std::string& line) {
  return line.find_first_not_of(" \t\r\n") == std::string::npos;
}

}  // namespace

int main(int argc, char** argv) {
  const std::vector<std::string> argl(argv + 1, argv + argc);

  bool want_deps = false;
  bool have_source = false;
  std::string source;
  std::string file;
  for (std::size_t i = 0; i < argl.size(); ++i) {
    const std::string& a = argl[i];
    if (a == "--deps") {
      want_deps = true;
    } else if (a == "-h" || a == "--help") {
      std::cout << USAGE;
      return 0;
    } else if (a == "--version") {
      std::cout << "sel " << SEL_VERSION << "\n";
      return 0;
    } else if (a == "--functions") {
      for (const std::string& n : sel::function_names()) std::cout << n << "\n";
      return 0;
    } else if (a == "-e") {
      if (i + 1 >= argl.size()) return usage_error("-e needs an expression");
      // A second program: the expression is the extra operand (docs/usage/repl.md).
      if (have_source) return usage_error("unexpected argument " + argl[i + 1]);
      source = argl[++i];
      have_source = true;
    } else if (a.size() > 1 && a[0] == '-') {
      return usage_error("unknown option " + a);
    } else {
      if (have_source) return usage_error("unexpected argument " + a);
      file = a;
      have_source = true;
    }
  }

  if (!file.empty()) {
    if (!selbin::read_bytes(file, source)) {
      std::cerr << "sel: cannot read " << file << "\n";
      return 1;
    }
  }

  if (have_source) {
    try {
      const sel::Program program = sel::compile(source);
      if (want_deps) {
        // One name per line; an empty list prints nothing at all.
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

  // REPL: one context for the whole session, so assignments persist. The prompt
  // is for a person at a terminal; a pipe gets results and errors only.
  const bool tty = SEL_STDIN_IS_TTY();
  sel::Value root = sel::Value::none();
  std::string line;
  if (tty) std::cout << "sel> " << std::flush;
  while (std::getline(std::cin, line)) {
    if (!is_blank(line)) {
      try {
        std::cout << show(sel::compile(line).run(root)) << "\n";
      } catch (const sel::SelError& e) {
        std::cout << std::flush;
        report(e);
      }
    }
    if (tty) std::cout << "sel> " << std::flush;
  }
  if (tty) std::cout << "\n";
  return 0;
}
