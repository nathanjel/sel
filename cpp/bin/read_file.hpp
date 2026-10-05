// File input for the harness binaries under bin/ (not part of the library).
//
// Two rules every runner must keep, in every host (tools/README.md):
//
//   * A file is read as raw bytes. No newline translation, no line reader that
//     drops a CR before LF or the empty line after a final LF: a CR in a corpus
//     or a .selt case is program text, and the lexer cases that carry one are
//     testing exactly that.
//   * A path that cannot be read -- missing, unreadable, a directory -- is an
//     error the runner reports as `cannot read <path>`, never an empty input.
//     On Linux an std::ifstream opens a directory without complaint and reads
//     nothing, which is how `sel /tmp` once evaluated "" and the conformance
//     runner reported "0 passed, 0 failed" for a mistyped file name.

#ifndef SEL_BIN_READ_FILE_HPP
#define SEL_BIN_READ_FILE_HPP

#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

namespace selbin {

// The whole of `path`, as bytes. False when it is not a readable file.
inline bool read_bytes(const std::string& path, std::string& out) {
  std::error_code ec;
  if (std::filesystem::is_directory(path, ec)) return false;
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  std::ostringstream ss;
  ss << in.rdbuf();
  if (in.bad()) return false;
  out = std::move(ss).str();
  return true;
}

// The corpus format (tools/README.md, "The corpus format"): a line beginning
// `### ` starts a record, and the record is the lines after it joined with one
// LF between them, with EXACTLY ONE trailing LF then removed. Lines are split on
// LF and nothing else -- not CR, not \v, \f or U+2028, which corpora carry on
// purpose. The same five steps as tools/run-batch.mjs's readCorpus.
inline std::vector<std::string> corpus_records(std::string_view text) {
  std::vector<std::vector<std::string_view>> records;
  std::size_t i = 0;
  for (;;) {
    const std::size_t nl = text.find('\n', i);
    const std::string_view line =
        text.substr(i, nl == std::string_view::npos ? std::string_view::npos : nl - i);
    if (line.substr(0, 4) == "### ") records.emplace_back();
    else if (!records.empty()) records.back().push_back(line);
    if (nl == std::string_view::npos) break;
    i = nl + 1;
  }
  std::vector<std::string> out;
  out.reserve(records.size());
  for (const auto& lines : records) {
    std::string joined;
    for (std::size_t k = 0; k < lines.size(); ++k) {
      if (k) joined += '\n';
      joined += lines[k];
    }
    if (!joined.empty() && joined.back() == '\n') joined.pop_back();
    out.push_back(std::move(joined));
  }
  return out;
}

}  // namespace selbin

#endif  // SEL_BIN_READ_FILE_HPP
