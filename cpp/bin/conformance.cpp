// Conformance runner. The suite in conformance/ is normative; this program has
// no opinions of its own beyond the file format in conformance/README.md.
//
//   cpp/build/conformance [file...]
//
// Note that expectation strings are unescaped by this file's own tiny escape
// reader, not by SEL's lexer — the suite must not validate the lexer with the
// lexer.

#include "../sel.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Case {
  std::string name;
  std::string at;
  bool has_setup = false;
  std::string setup;
  std::string source;
  std::string expect;
};

std::string trim(const std::string& s) {
  const std::size_t a = s.find_first_not_of(" \t\r\n");
  if (a == std::string::npos) return "";
  const std::size_t b = s.find_last_not_of(" \t\r\n");
  return s.substr(a, b - a + 1);
}

bool starts_with(const std::string& s, const std::string& p) {
  return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}

std::string join(const std::vector<std::string>& lines) {
  std::string out;
  for (std::size_t i = 0; i < lines.size(); i++) {
    if (i) out += "\n";
    out += lines[i];
  }
  return out;
}

std::vector<Case> parse_selt(const std::string& text, const std::string& file) {
  std::vector<Case> cases;
  std::vector<std::vector<std::string>> setups, sources, expects;
  std::vector<bool> saw_setup, saw_source, saw_expect;

  int idx = 0;
  bool in_case = false;
  std::string section;
  std::istringstream in(text);
  std::string line;

  while (std::getline(in, line)) {
    idx++;
    const std::string at = file + ":" + std::to_string(idx);

    if (starts_with(line, "### ")) {
      const std::string rest = trim(line.substr(4));
      if (!starts_with(rest, "name:")) throw std::runtime_error(at + ": malformed case header");
      Case c;
      c.name = trim(rest.substr(5));
      c.at = at;
      cases.push_back(c);
      setups.emplace_back();
      sources.emplace_back();
      expects.emplace_back();
      saw_setup.push_back(false);
      saw_source.push_back(false);
      saw_expect.push_back(false);
      in_case = true;
      section.clear();
      continue;
    }
    if (line == "===") {
      in_case = false;
      section.clear();
      continue;
    }
    if (starts_with(line, "--- ")) {
      if (!in_case) throw std::runtime_error(at + ": section outside a case");
      section = trim(line.substr(4));
      const std::size_t k = cases.size() - 1;
      if (section == "setup") saw_setup[k] = true;
      else if (section == "source") saw_source[k] = true;
      else if (section == "expect") saw_expect[k] = true;
      else if (section != "note") throw std::runtime_error(at + ": unknown section " + section);
      continue;
    }
    if (!in_case || section.empty() || section == "note") continue;

    const std::size_t k = cases.size() - 1;
    if (section == "setup") setups[k].push_back(line);
    else if (section == "source") sources[k].push_back(line);
    else if (section == "expect") expects[k].push_back(line);
  }

  for (std::size_t k = 0; k < cases.size(); k++) {
    if (!saw_source[k]) {
      throw std::runtime_error(cases[k].at + ": case " + cases[k].name + " has no --- source");
    }
    if (!saw_expect[k]) {
      throw std::runtime_error(cases[k].at + ": case " + cases[k].name + " has no --- expect");
    }
    cases[k].has_setup = saw_setup[k];
    cases[k].setup = trim(join(setups[k]));
    cases[k].source = trim(join(sources[k]));
    cases[k].expect = trim(join(expects[k]));
  }
  return cases;
}

// The runner's own escape set, deliberately not SEL's.
std::string unescape(const std::string& lit, const std::string& at) {
  if (lit.size() < 2 || lit.front() != '"' || lit.back() != '"') {
    throw std::runtime_error(at + ": expected a quoted string, got " + lit);
  }
  const std::string body = lit.substr(1, lit.size() - 2);
  std::string out;
  for (std::size_t i = 0; i < body.size(); i++) {
    if (body[i] != '\\') {
      out += body[i];
      continue;
    }
    const char e = body[++i];
    if (e == '\\') out += '\\';
    else if (e == '"') out += '"';
    else if (e == 'n') out += '\n';
    else if (e == 't') out += '\t';
    else if (e == 'r') out += '\r';
    else if (e == 'u') {
      const unsigned cp = std::stoul(body.substr(i + 1, 4), nullptr, 16);
      i += 4;
      // \uXXXX is a BMP code point; encode it as UTF-8.
      if (cp < 0x80) {
        out += static_cast<char>(cp);
      } else if (cp < 0x800) {
        out += static_cast<char>(0xc0 | (cp >> 6));
        out += static_cast<char>(0x80 | (cp & 0x3f));
      } else {
        out += static_cast<char>(0xe0 | (cp >> 12));
        out += static_cast<char>(0x80 | ((cp >> 6) & 0x3f));
        out += static_cast<char>(0x80 | (cp & 0x3f));
      }
    } else {
      throw std::runtime_error(at + ": bad escape \\" + std::string(1, e));
    }
  }
  return out;
}

// Describes an actual result in the same vocabulary the expectations use, so a
// failure report reads as "want X, got Y" in one language.
std::string describe(const sel::Value& v) {
  if (v.size() > 0) return "tree " + v.dump();
  switch (v.kind()) {
    case sel::Kind::Text: return "text " + v.dump().substr(1);
    case sel::Kind::Bin: return "bin " + v.dump().substr(1);
    case sel::Kind::Bool: return std::string("bool ") + (v.boolean_scalar() ? "TRUE" : "FALSE");
    default: return "none";
  }
}

struct Outcome {
  bool ok = false;
  sel::Value value;
  bool failed = false;
  std::string code;
  std::string message;
  int line = 0, col = 0;
  bool suite_error = false;
  std::string suite_message;
};

// Returns an empty string when the expectation holds, or the problem otherwise.
std::string check(const std::string& expect, const Outcome& r, const std::string& at) {
  const std::size_t space = expect.find(' ');
  const std::string form = space == std::string::npos ? expect : expect.substr(0, space);
  const std::string rest = space == std::string::npos ? "" : trim(expect.substr(space + 1));

  if (form == "error") {
    if (!r.failed) return "expected " + expect + ", got value " + describe(r.value);
    std::istringstream ps(rest);
    std::string want_code, at_word, where;
    ps >> want_code >> at_word >> where;
    if (r.code != want_code) return "expected " + want_code + ", got " + r.code + " (" + r.message + ")";
    if (at_word == "at") {
      const std::string got_at = std::to_string(r.line) + ":" + std::to_string(r.col);
      if (got_at != where) {
        return "expected " + want_code + " at " + where + ", got it at " + got_at;
      }
    }
    return "";
  }

  if (r.failed) return "expected " + expect + ", got " + r.code + " (" + r.message + ")";
  const sel::Value& v = r.value;

  if (form == "text") {
    if (v.kind() != sel::Kind::Text || v.size() > 0) return "wanted text, got " + describe(v);
    return v.scalar() == unescape(rest, at) ? "" : "got " + describe(v);
  }
  if (form == "num") {
    if (v.kind() != sel::Kind::Text || v.size() > 0) return "wanted a number, got " + describe(v);
    return v.scalar() == rest ? "" : "got " + describe(v);
  }
  if (form == "bin") {
    if (v.kind() != sel::Kind::Bin || v.size() > 0) return "wanted binary, got " + describe(v);
    return v.dump().substr(1) == rest ? "" : "got " + describe(v);
  }
  if (form == "bool") {
    if (v.kind() != sel::Kind::Bool || v.size() > 0) return "wanted a boolean, got " + describe(v);
    return (v.boolean_scalar() ? "TRUE" : "FALSE") == rest ? "" : "got " + describe(v);
  }
  if (form == "none") {
    return (v.kind() == sel::Kind::None && v.size() == 0) ? "" : "got " + describe(v);
  }
  if (form == "tree") {
    return v.dump() == rest ? "" : "got tree " + v.dump();
  }
  throw std::runtime_error(at + ": unknown expectation form " + form);
}

Outcome run_case(const Case& c) {
  Outcome r;
  sel::Value root = sel::Value::none();

  if (c.has_setup && !c.setup.empty()) {
    try {
      sel::compile(c.setup).run(root);
    } catch (const sel::SelError& e) {
      // A broken setup is a suite bug, not a failing implementation.
      r.suite_error = true;
      r.suite_message = e.str();
      return r;
    }
  }
  try {
    r.value = sel::compile(c.source).run(root);
  } catch (const sel::SelError& e) {
    r.failed = true;
    r.code = e.code();
    r.message = e.message();
    r.line = e.line();
    r.col = e.col();
  }
  return r;
}

std::string read_file(const std::filesystem::path& p) {
  std::ifstream in(p, std::ios::binary);
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

}  // namespace

int main(int argc, char** argv) {
  namespace fs = std::filesystem;
  const fs::path suite = "conformance";

  std::vector<fs::path> files;
  if (argc > 1) {
    for (int i = 1; i < argc; i++) files.push_back(argv[i]);
  } else {
    for (const auto& e : fs::directory_iterator(suite)) {
      if (e.path().extension() == ".selt") files.push_back(e.path());
    }
    std::sort(files.begin(), files.end());
  }

  int pass = 0;
  std::vector<std::pair<Case, std::string>> failures;
  std::vector<std::string> suite_errors;
  std::map<std::string, std::string> seen;

  for (const auto& file : files) {
    const std::string shortname = file.filename().string();
    std::vector<Case> cases;
    try {
      cases = parse_selt(read_file(file), shortname);
    } catch (const std::exception& e) {
      suite_errors.push_back(e.what());
      continue;
    }
    for (const auto& c : cases) {
      auto it = seen.find(c.name);
      if (it != seen.end()) {
        suite_errors.push_back(c.at + ": duplicate case name " + c.name + " (also " + it->second + ")");
        continue;
      }
      seen[c.name] = c.at;

      const Outcome r = run_case(c);
      if (r.suite_error) {
        suite_errors.push_back(c.at + ": " + c.name + ": setup failed: " + r.suite_message);
        continue;
      }
      std::string problem;
      try {
        problem = check(c.expect, r, c.at);
      } catch (const std::exception& e) {
        suite_errors.push_back(e.what());
        continue;
      }
      if (problem.empty()) pass++;
      else failures.emplace_back(c, problem);
    }
  }

  for (const auto& [c, problem] : failures) {
    std::cout << "FAIL " << c.name << "  (" << c.at << ")\n";
    std::cout << "     source: " << c.source << "\n";
    std::cout << "     want:   " << c.expect << "\n";
    std::cout << "     " << problem << "\n";
  }
  for (const auto& e : suite_errors) std::cout << "SUITE " << e << "\n";

  std::cout << "\n" << pass << " passed, " << failures.size() << " failed, " << suite_errors.size()
            << " suite errors\n";
  return (failures.empty() && suite_errors.empty()) ? 0 : 1;
}
