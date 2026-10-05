// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   cpp/build/batch [--show] corpus.selc

#include "../sel.hpp"
#include "read_file.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

// The rendering bin/sel uses, so a documentation example can be pasted into the
// CLI and produce exactly what the documentation claims.
std::string render(const sel::Value& v) {
  if (v.size() == 0) {
    if (v.kind() == sel::Kind::Text) return v.scalar();
    if (v.kind() == sel::Kind::Bool) return v.boolean_scalar() ? "TRUE" : "FALSE";
    if (v.kind() == sel::Kind::Bin) return "bin:" + v.dump().substr(1);
  }
  return v.dump();
}

}  // namespace

int main(int argc, char** argv) {
  bool show = false;
  std::string path;
  for (int i = 1; i < argc; i++) {
    const std::string a = argv[i];
    if (a == "--show") show = true;
    else path = a;
  }
  // Raw bytes, split by selbin::corpus_records: exactly one trailing newline
  // removed per record, a CR kept as program text (tools/README.md).
  std::string text;
  if (!selbin::read_bytes(path, text)) {
    std::cerr << "cannot read " << path << "\n";
    return 2;
  }
  const std::vector<std::string> corpus = selbin::corpus_records(text);
  if (corpus.empty()) {
    std::cerr << "batch: no programs in " << path << "\n";
    return 1;
  }

  std::vector<std::string> lines;
  for (const std::string& src : corpus) {
    try {
      const sel::Value v = sel::compile(src).run();
      lines.push_back(show ? render(v) : v.dump());
    } catch (const sel::SelError& e) {
      lines.push_back(show ? "!" + e.code()
                           : "!" + e.code() + "@" + std::to_string(e.line()) + ":" +
                                 std::to_string(e.col()));
    } catch (const std::exception& e) {
      lines.push_back(std::string("!HOST ") + typeid(e).name() + ": " + e.what());
    }
  }

  // One line per program is the protocol; a value containing a newline must not
  // be allowed to desynchronise the comparison.
  std::string out;
  for (std::size_t i = 0; i < lines.size(); i++) {
    if (i) out += "\n";
    for (char c : lines[i]) {
      if (c == '\n') out += "\\n";
      else out += c;
    }
  }
  std::cout << out << "\n";
  return 0;
}
