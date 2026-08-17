// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   cpp/build/batch [--show] corpus.selc

#include "../sel.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

// A line beginning `### ` starts a record; everything after it is source until
// the next marker.
std::vector<std::string> read_corpus(std::istream& in) {
  // Deliberately the same construction as tools/run-batch.mjs: collect a
  // record's lines, join them with a single newline BETWEEN them, then drop one
  // trailing newline. Appending a newline after every line instead produces one
  // separator too many, and a spare trailing newline moves the position SEL
  // reports for an end-of-input error — a phantom disagreement that looks like
  // an interpreter bug. See tools/README.md, which is normative for this.
  std::vector<std::vector<std::string>> records;
  std::string line;
  bool started = false;
  while (std::getline(in, line)) {
    if (line.rfind("### ", 0) == 0) {
      records.emplace_back();
      started = true;
      continue;
    }
    if (started) records.back().push_back(line);
  }

  std::vector<std::string> out;
  out.reserve(records.size());
  for (const auto& lines : records) {
    std::string joined;
    for (std::size_t i = 0; i < lines.size(); i++) {
      if (i) joined += "\n";
      joined += lines[i];
    }
    if (!joined.empty() && joined.back() == '\n') joined.pop_back();
    out.push_back(std::move(joined));
  }
  return out;
}

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
  std::ifstream in(path);
  if (!in) {
    std::cerr << "cannot read " << path << "\n";
    return 2;
  }

  std::vector<std::string> lines;
  for (const std::string& src : read_corpus(in)) {
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
