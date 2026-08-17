// A test harness small enough not to be a dependency.
//
// The conformance suite is what proves this implementation correct; these unit
// tests exist for the layers underneath it, where a bug shows up as a hundred
// confusing conformance failures instead of one clear message. That job does not
// justify pulling in a framework.

#ifndef SEL_TEST_HARNESS_HPP
#define SEL_TEST_HARNESS_HPP

#include <iostream>
#include <string>
#include <vector>

namespace selt {

inline int checks = 0;
inline std::vector<std::string> failures;
inline std::string group;

inline void section(const std::string& name) { group = name; }

inline void ok(bool cond, const std::string& what) {
  checks++;
  if (!cond) failures.push_back(group + ": " + what);
}

template <typename A, typename B>
void eq(const A& got, const B& want, const std::string& what) {
  checks++;
  if (!(got == want)) {
    std::ostringstream ss;
    ss << group << ": " << what << "\n       got:  " << got << "\n       want: " << want;
    failures.push_back(ss.str());
  }
}

// Runs `body` and checks it raises a SelError with this code.
template <typename F>
void raises(const std::string& code, F body, const std::string& what) {
  checks++;
  try {
    body();
    failures.push_back(group + ": " + what + "\n       expected " + code + ", nothing was thrown");
  } catch (const sel::SelError& e) {
    if (e.code() != code) {
      failures.push_back(group + ": " + what + "\n       expected " + code + ", got " + e.code());
    }
  }
}

inline int report(const char* name) {
  for (const std::string& f : failures) std::cout << "FAIL " << f << "\n";
  std::cout << name << ": " << (checks - static_cast<int>(failures.size())) << "/" << checks
            << " checks passed\n";
  return failures.empty() ? 0 : 1;
}

}  // namespace selt

#endif
