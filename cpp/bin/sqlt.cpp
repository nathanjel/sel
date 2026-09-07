// The SEL->SQL conformance suite for the C++ host.
//
// Run from the repository root:
//
//     cpp/build/sqlt                 every case
//     cpp/build/sqlt bind. agg.      only cases whose name contains one of these
//     cpp/build/sqlt --names         what this host loaded, and stop
//
// The cases live in sql/cases/*.sqlt and reach here through
// tools/gen-sql-cases.mjs, which is the only thing that reads them. Nothing in
// this file parses anything: a case's bindings and registrations arrive as
// typed constructor calls the compiler has already checked.

#include <cstdio>
#include <cstdlib>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "../sel.hpp"
#include "../sel_sql.hpp"
#include "case_data.hpp"

namespace {

using namespace sel::sqlt;
using sel::sql::Fragment;
using sel::sql::Map;
using sel::sql::Mode;
using sel::sql::Sql;
using sel::sql::SqlError;

// A case file names PHP's exception class, because the cases were written for
// one host. The line it draws is the one that matters and it is the same
// everywhere: a malformed map or registration is a mistake in the application's
// startup, not a rule that cannot be translated, so it must NOT be catchable as
// SqlError -- try_translate() swallows the second and must not swallow the
// first. A name with no mapping is a suite error and never a pass: an
// unrecognised expectation must not be satisfied by whatever happened to be
// thrown.
bool throws_is_known(const std::string& name) { return name == "LogicException"; }

const std::map<std::string, std::string> MIRRORS = {{"mariadb", "mysql"}};

struct SuiteError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

// "CODE" or "CODE line:col".
struct Expected {
  std::string code;
  bool has_pos = false;
  int line = 0, col = 0;
};

Expected parse_expected(const std::string& s, const std::string& at) {
  Expected e;
  const std::size_t sp = s.find(' ');
  if (sp == std::string::npos) { e.code = s; return e; }
  e.code = s.substr(0, sp);
  std::string pos = s.substr(sp + 1);
  while (!pos.empty() && pos.front() == ' ') pos.erase(pos.begin());
  const std::size_t colon = pos.find(':');
  if (colon == std::string::npos) throw SuiteError(at + ": malformed error expectation");
  try {
    e.line = std::stoi(pos.substr(0, colon));
    e.col = std::stoi(pos.substr(colon + 1));
  } catch (const std::exception&) {
    throw SuiteError(at + ": malformed error expectation");
  }
  e.has_pos = true;
  return e;
}

// `~1~`, not bare tildes: PostgreSQL's regex operator IS `~`, so counting those
// divided a regex fragment's odd tilde count by two. PHP's runner shipped that
// bug; every host has carried the corrected form since.
int count_slots(const std::string& s) {
  int n = 0;
  for (std::size_t i = 0; i + 2 < s.size(); ++i) {
    if (s[i] != '~') continue;
    std::size_t j = i + 1;
    while (j < s.size() && s[j] >= '0' && s[j] <= '9') ++j;
    if (j > i + 1 && j < s.size() && s[j] == '~') { ++n; i = j; }
  }
  return n;
}

std::string join_dumps(const std::vector<sel::Value>& vs) {
  std::string out;
  for (std::size_t i = 0; i < vs.size(); ++i) {
    if (i) out += ", ";
    out += vs[i].dump();
  }
  return out;
}

// Empty when the case passes, else what went wrong.
std::string run_case(const SqlCase& c, const std::string& dialect) {
  if (dialect.empty()) {
    throw SuiteError(std::string(c.at) + ": case " + c.name + " has no --- dialect");
  }
  const std::string as_ = c.as_ ? c.as_ : "value";
  const std::string mode_name = c.mode ? c.mode : "inline";
  const auto mode = sel::sql::mode_from_name(mode_name);
  if (!mode) throw SuiteError(std::string(c.at) + ": unknown mode " + mode_name);

  bool have_sql = false, have_error = false, have_thrown = false;
  std::string sql, thrown_what;
  SqlError error("", "");
  Fragment frag;

  try {
    // Inside the try: a bad registration is one of the outcomes a case may
    // assert, so it has to be catchable rather than fatal.
    if (c.register_fn) c.register_fn();
    // Inside the try: a Binding constructor refuses a malformed binding at the
    // earliest possible moment, which is construction rather than translation,
    // and that refusal is one of the outcomes a case asserts.
    sel::sql::Bindings bindings(c.bindings_fn());
    sel::Program program = sel::compile(c.source);
    sel::sql::Options options;
    options.strict = c.strict;
    frag = Sql::translate(program, dialect, bindings, options);
    sql = as_ == "condition" ? frag.as_condition(*mode) : frag.as_value(*mode);
    have_sql = true;
  } catch (const SqlError& e) {
    error = e;
    have_error = true;
  } catch (const sel::SelError& e) {
    return "the source did not compile: " + e.str();
  } catch (const SuiteError&) {
    throw;
  } catch (const std::exception& e) {
    thrown_what = e.what();
    have_thrown = true;
  }

  if (c.throws) {
    if (!throws_is_known(c.throws)) {
      throw SuiteError(std::string(c.at) + ": no C++ equivalent is recorded for "
                       "--- throws " + c.throws);
    }
    if (!have_thrown) {
      return std::string("expected ") + c.throws + ", got " +
             (have_error ? error.str() : sql);
    }
    return "";
  }
  if (have_thrown) {
    throw SuiteError(std::string(c.at) + ": unexpected throw: " + thrown_what);
  }

  if (c.error) {
    if (!have_error) return std::string("expected ") + c.error + ", got " + sql;
    const Expected want = parse_expected(c.error, c.at);
    if (error.code() != want.code) {
      return "expected " + want.code + ", got " + error.code() + " (" +
             error.message() + ")";
    }
    if (want.has_pos) {
      const std::string got = std::to_string(error.line()) + ":" +
                              std::to_string(error.col());
      const std::string wanted = std::to_string(want.line) + ":" +
                                 std::to_string(want.col);
      if (got != wanted) {
        return "expected " + want.code + " at " + wanted + ", got it at " + got;
      }
    }
    return "";
  }

  if (have_error) {
    return "expected SQL, got " + error.code() + " (" + error.message() + ")";
  }
  if (!have_sql || sql != std::string(c.expect ? c.expect : "")) {
    return "got:  " + sql + "\n     want: " + std::string(c.expect ? c.expect : "");
  }

  // Checked for every case that produces a fragment, not only those asking
  // about params: every slot in the part list must have a value, and every value
  // must be emitted. A value bound but never emitted means a Fragment was
  // rendered and thrown away -- invisible in inline mode, which is what the rest
  // of the suite asserts.
  std::set<int> seen;
  for (const Fragment::Part& p : frag.parts()) {
    if (!p.is_slot) continue;
    if (p.slot < 1 || static_cast<std::size_t>(p.slot) > frag.params().size()) {
      return "parameter slot " + std::to_string(p.slot) + " has no value in params";
    }
    seen.insert(p.slot);
  }
  std::string orphans;
  for (std::size_t i = 1; i <= frag.params().size(); ++i) {
    if (!seen.count(static_cast<int>(i))) {
      if (!orphans.empty()) orphans += ", ";
      orphans += std::to_string(i);
    }
  }
  if (!orphans.empty()) {
    return "parameter slot(s) [" + orphans +
           "] were bound but never emitted — a fragment was rendered and discarded";
  }
  if (static_cast<int>(frag.bindings().size()) !=
      count_slots(frag.as_value(Mode::Debug))) {
    return "bindings() and the emitted placeholders disagree in count";
  }

  if (c.params) {
    const std::string got = join_dumps(frag.bindings());
    if (got != c.params) {
      return "params got:  " + got + "\n     want: " + c.params;
    }
  }
  return "";
}

}  // namespace

int main(int argc, char** argv) {
  std::vector<std::string> filters(argv + 1, argv + argc);
  const auto cases = sql_cases();

  if (filters.size() == 1 && filters[0] == "--names") {
    for (const SqlCase& c : cases) std::printf("%s\t%s\n", c.at, c.name);
    return 0;
  }

  int passed = 0, mirrored = 0, compile_refused = 0, suite_errors = 0;
  std::vector<std::pair<const SqlCase*, std::string>> failures;

  for (const SqlCase& c : cases) {
    if (!filters.empty()) {
      bool wanted = false;
      for (const std::string& f : filters) {
        if (std::string(c.name).find(f) != std::string::npos) { wanted = true; break; }
      }
      if (!wanted) continue;
    }

    // The case asserts that this shape is refused, and here it was refused by
    // the compiler: the typed constructors cannot express it at all. Counted
    // and named rather than skipped, so the total stays honest.
    if (c.unrepresentable) {
      ++passed;
      ++compile_refused;
      continue;
    }

    Map::reset();   // no case may leak a registration into another
    std::string problem;
    try {
      problem = run_case(c, c.dialect);
    } catch (const SuiteError& e) {
      std::printf("SUITE ERROR %s\n", e.what());
      ++suite_errors;
      continue;
    }
    if (!problem.empty()) { failures.emplace_back(&c, problem); continue; }
    ++passed;

    // The same case under the mirrored dialect. A registration case is exempt:
    // it names its dialect in the register data, so re-running it under another
    // name would be testing something it does not claim.
    auto m = MIRRORS.find(c.dialect);
    if (m == MIRRORS.end() || c.register_fn) continue;
    Map::reset();
    try {
      problem = run_case(c, m->second);
    } catch (const SuiteError& e) {
      std::printf("SUITE ERROR (mirrored to %s) %s\n", m->second.c_str(), e.what());
      ++suite_errors;
      continue;
    }
    if (problem.empty()) ++mirrored;
    else {
      failures.emplace_back(&c, "mirrored to " + m->second +
                                    ", which must agree with " + c.dialect + ": " +
                                    problem);
    }
  }

  for (const auto& [c, problem] : failures) {
    std::printf("FAIL %s  (%s)\n     %s\n", c->name, c->at, problem.c_str());
  }
  std::printf("\n%d passed (%d also checked against a mirrored dialect, "
              "%d refused by the type system), %d failed, %d suite errors\n",
              passed, mirrored, compile_refused,
              static_cast<int>(failures.size()), suite_errors);
  return failures.empty() && suite_errors == 0 ? 0 : 1;
}
