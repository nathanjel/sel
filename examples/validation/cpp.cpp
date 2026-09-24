// Form validation -- one rule set, the same verdicts in every host, from C++.
//
//   cd cpp && make build/example-validation && cd ..
//   cpp/build/example-validation
//
// A checkout form's rules, one per field, written in SEL. The server compiles
// them once at start-up, so a rule that does not parse fails the deployment
// rather than a customer. Each rule answers "" when the field is fine and
// ABORT("message") when it is not, so there are two kinds of failure and they
// are told apart by code: E_ABORT is a message for the user, anything else means
// the rule itself is broken and the user should never see it. dependencies()
// tells a browser which rules to re-run when a field changes -- and the browser
// runs the very same rule text, in JavaScript.
//
// The four files beside this one print byte-identical output.
//
// The visible difference here is that C++ has no from_native: a submitted form
// is a list of (field, text) pairs, and the context is built from it one set()
// at a time. The rules are compiled in main() rather than at namespace scope,
// because SEL's builtins are registered by another translation unit's static
// initialisers, and the order of those is not this file's to choose.

#include "../../cpp/sel.hpp"

#include <cstddef>
#include <iostream>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace {

// Left-pad to a fixed width, so this file's columns line up with the four
// written in languages that have printf-style padding built in.
std::string pad(const std::string& s, std::size_t n) {
  return s.size() >= n ? s : s + std::string(n - s.size(), ' ');
}

std::string join(const std::vector<std::string>& parts, const std::string& sep) {
  std::string out;
  for (std::size_t i = 0; i < parts.size(); ++i) out += (i ? sep : "") + parts[i];
  return out;
}

// EXAMPLE-BEGIN rules
const std::vector<std::pair<std::string, std::string>> RULES = {
    {"name",     R"(IF(IS_BLANK(NAME), ABORT("Please tell us your name"), ""))"},
    {"email",    R"(IF(RMATCH('^[^@ ]+@[^@ ]+\.[a-z]{2,}$', TRIM(EMAIL), "i"), "",)"
                 R"( ABORT("{EMAIL} does not look like an e-mail address")))"},
    {"postcode", R"(COND(COUNTRY $== "PL" AND NOT RMATCH('^\d{2}-\d{3}$', POSTCODE),)"
                 R"(       ABORT("Polish postcodes look like 00-000"),)"
                 R"(     COUNTRY $== "DE" AND NOT RMATCH('^\d{5}$', POSTCODE),)"
                 R"(       ABORT("German postcodes have five digits"),)"
                 R"(     ""))"},
    {"quantity", R"(IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,)"
                 R"( ABORT("Choose between 1 and {STOCK}"), ""))"},
    {"total",    R"(TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);)"
                 R"( IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), ""))"},
};
// EXAMPLE-END rules

using Rules = std::vector<std::pair<std::string, sel::Program>>;

// EXAMPLE-BEGIN validate
using Form = std::vector<std::pair<std::string, std::string>>;       // field, what was typed
using Problems = std::vector<std::pair<std::string, std::string>>;   // field, message

Problems validate(const Rules& compiled, const Form& form) {
  Problems problems;
  for (const auto& [field, rule] : compiled) {
    sel::Value ctx = sel::Value::none();
    for (const auto& [name, typed] : form) ctx.set(name, sel::Value::text(typed));
    std::string verdict;
    try {
      verdict = rule.run(ctx).as_text();
    } catch (const sel::SelError& e) {
      // E_ABORT is the rule speaking to the user; anything else is a
      // broken rule or data it cannot read -- log it, show a generic line.
      verdict = e.code() == "E_ABORT" ? e.message() : "could not be checked (" + e.code() + ")";
    }
    if (!verdict.empty()) problems.emplace_back(field, verdict);
  }
  return problems;
}
// EXAMPLE-END validate

const std::vector<Form> SUBMISSIONS = {
    {{"NAME", "Anna Nowak"}, {"EMAIL", "anna@example.pl"}, {"COUNTRY", "PL"}, {"POSTCODE", "31-874"},
     {"QTY", "2"}, {"STOCK", "5"}, {"PRICE", "19.99"}, {"DISCOUNT", "0.10"}, {"CREDIT_LIMIT", "100.00"}},
    {{"NAME", "   "}, {"EMAIL", "bruno(at)example.de"}, {"COUNTRY", "DE"}, {"POSTCODE", "1011"},
     {"QTY", "9"}, {"STOCK", "5"}, {"PRICE", "19.99"}, {"DISCOUNT", "0"}, {"CREDIT_LIMIT", "100.00"}},
    {{"NAME", "Chloé"}, {"EMAIL", "CHLOE@EXAMPLE.FR "}, {"COUNTRY", "FR"}, {"POSTCODE", "69002"},
     {"QTY", "4"}, {"STOCK", "5"}, {"PRICE", "29.99"}, {"DISCOUNT", "0.05"}, {"CREDIT_LIMIT", "100.00"}},
    {{"NAME", "Dawid"}, {"EMAIL", "dawid@example.pl"}, {"COUNTRY", "PL"}, {"POSTCODE", "00-950"},
     {"QTY", "1"}, {"STOCK", "5"}, {"PRICE", "twenty"}, {"DISCOUNT", "0"}, {"CREDIT_LIMIT", "100.00"}},
};

}  // namespace

int main() {
  // 1 - compile once, at start-up ----------------------------------------------------

  // EXAMPLE-BEGIN compile
  Rules compiled;
  for (const auto& [field, source] : RULES) compiled.emplace_back(field, sel::compile(source));
  // EXAMPLE-END compile
  std::cout << "1. the rule set\n";
  for (const auto& [field, rule] : compiled)
    std::cout << "   " << pad(field, 9) << " reads " << join(rule.dependencies(), " ") << "\n";

  // 2 - what to re-check when a field changes ----------------------------------------

  std::cout << "2. re-check on change\n";
  std::map<std::string, std::vector<std::string>> watch;   // sorted by name
  for (const auto& [field, rule] : compiled)
    for (const std::string& name : rule.dependencies()) watch[name].push_back(field);
  for (const auto& [name, fields] : watch)
    std::cout << "   " << pad(name, 13) << " " << join(fields, ", ") << "\n";

  // 3 - validating submissions -----------------------------------------------------------

  std::cout << "3. submissions\n";
  for (std::size_t n = 1; n <= SUBMISSIONS.size(); ++n) {
    const Problems problems = validate(compiled, SUBMISSIONS[n - 1]);
    if (problems.empty()) std::cout << "   #" << n << " accepted\n";
    for (const auto& [field, message] : problems)
      std::cout << "   #" << n << " " << pad(field, 9) << " " << message << "\n";
  }
  return 0;
}
