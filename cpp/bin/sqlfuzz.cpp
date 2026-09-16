// Translate a corpus of SEL programs and print one canonical line each.
//
//     cpp/build/sqlfuzz corpus.selc [dialect]
//
// The counterpart of js/bin/sqlfuzz.mjs, php/bin/sqlfuzz and python/bin/sqlfuzz;
// see the first for why this lane exists. The corpus format and the
// one-line-per-program protocol are specified in tools/README.md.

#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "../sel.hpp"
#include "../sel_sql.hpp"

namespace {

std::vector<std::string> read_corpus(const std::string& text) {
  std::vector<std::vector<std::string>> records;
  bool started = false;
  // Split on '\n' and nothing else: a splitlines-style break would also cut on
  // \v, \f and U+2028, which the corpus contains deliberately.
  std::size_t i = 0;
  while (i <= text.size()) {
    const std::size_t nl = text.find('\n', i);
    const std::string line =
        text.substr(i, nl == std::string::npos ? std::string::npos : nl - i);
    if (line.rfind("### ", 0) == 0) {
      records.emplace_back();
      started = true;
    } else if (started) {
      records.back().push_back(line);
    }
    if (nl == std::string::npos) break;
    i = nl + 1;
  }
  std::vector<std::string> out;
  for (const auto& lines : records) {
    std::string joined;
    for (std::size_t k = 0; k < lines.size(); ++k) {
      if (k) joined += "\n";
      joined += lines[k];
    }
    if (!joined.empty() && joined.back() == '\n') joined.pop_back();
    out.push_back(std::move(joined));
  }
  return out;
}

std::string escape_newlines(const std::string& s) {
  std::string out;
  for (char c : s) {
    if (c == '\n') out += "\\n";
    else out += c;
  }
  return out;
}

}  // namespace

namespace {
// The relations the corpus's pipelines read (tools/gen-programs.mjs --sql), the
// same in every host's runner: two tables, a NUM join key, a TEXT field whose
// name both sides share.
sel::sql::Bindings fuzz_bindings() {
  using sel::sql::Binding; using sel::sql::SqlKind;
  return sel::sql::Bindings({
      {"ORDERS", Binding::relation("orders", "o", {
          {"ID", Binding::column("id", "o", SqlKind::Num)}, {"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)},
          {"AMOUNT", Binding::column("amount", "o", SqlKind::Num)}, {"NAME", Binding::column("name", "o", SqlKind::Text)}})},
      {"CUSTOMERS", Binding::relation("customers", "c", {
          {"ID", Binding::column("id", "c", SqlKind::Num)}, {"NAME", Binding::column("name", "c", SqlKind::Text)}})}});
}

std::string render(const sel::sql::Fragment& f) {
  std::string binds;
  const std::vector<sel::Value> vs = f.bindings();
  for (std::size_t i = 0; i < vs.size(); ++i) {
    if (i) binds += ",";
    binds += vs[i].dump();
  }
  return f.as_value() + " | " + f.as_value(sel::sql::Mode::Params) + " | " + binds;
}

// Three lanes per program, `||`-separated: translate(), translate_statement()
// and plan_hybrid() (its classification, then its statement in params mode).
template <typename F>
std::string attempt(F fn) {
  try {
    return fn();
  } catch (const sel::sql::SqlError& e) {
    return "!" + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  } catch (const sel::SelError& e) {
    return "!SEL " + e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
  } catch (const std::exception& e) {
    return std::string("!HOST ") + e.what();
  }
}
}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: sqlfuzz corpus.selc [dialect]\n");
    return 2;
  }
  const std::string dialect = argc > 2 ? argv[2] : "mariadb";
  std::ifstream in(argv[1], std::ios::binary);
  std::ostringstream buf;
  buf << in.rdbuf();
  const std::vector<std::string> corpus = read_corpus(buf.str());

  std::string out;
  for (const std::string& src : corpus) {
    std::string line;
    try {
      const sel::Program program = sel::compile(src);
      try {
        // Three renderings, because comparing only the inline one let a
        // mutation that bound a numeric literal as a parameter walk straight
        // through this lane.
        const sel::sql::Bindings bindings = fuzz_bindings();
        line = attempt([&] { return render(sel::sql::Sql::translate(program, dialect, bindings)); })
             + " || " + attempt([&] { return render(sel::sql::Sql::translate_statement(program, dialect, bindings)); })
             + " || " + attempt([&] {
                 const auto plan = sel::sql::Sql::plan_hybrid(program, dialect, bindings);
                 const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
                 return plan.sql_statement ? kind + " " + plan.sql_statement->as_statement(sel::sql::Mode::Params) : kind;
               });
      } catch (const sel::sql::SqlError& e) {
        line = "!" + e.code() + "@" + std::to_string(e.line()) + ":" +
               std::to_string(e.col());
      } catch (const sel::SelError& e) {
        line = "!SEL " + e.code() + "@" + std::to_string(e.line()) + ":" +
               std::to_string(e.col());
      } catch (const std::exception& e) {
        line = std::string("!HOST ") + e.what();
      }
    } catch (const sel::SelError&) {
      line = "-";
    } catch (const std::exception& e) {
      line = std::string("!HOST ") + e.what();
    }
    out += escape_newlines(line);
    out += "\n";
  }
  std::fwrite(out.data(), 1, out.size(), stdout);
  return 0;
}
