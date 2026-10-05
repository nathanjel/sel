// How `sel` prints a result (docs/usage/repl.md): a scalar text bare, a boolean
// as TRUE or FALSE, a binary as `bin:<hex>`, anything else as its dump.
//
// Harness code, not part of the library: one copy, included by bin/sel.cpp and
// by bin/batch.cpp (--show), so a documentation example pasted into the CLI
// prints exactly what the documentation claims.

#ifndef SEL_BIN_SHOW_HPP
#define SEL_BIN_SHOW_HPP

#include "../sel.hpp"

#include <string>

namespace selbin {

inline std::string show(const sel::Value& v) {
  if (v.size() == 0) {
    if (v.kind() == sel::Kind::Text) return v.scalar();
    if (v.kind() == sel::Kind::Bool) return v.boolean_scalar() ? "TRUE" : "FALSE";
    if (v.kind() == sel::Kind::Bin) return "bin:" + v.dump().substr(1);
  }
  return v.dump();
}

}  // namespace selbin

#endif  // SEL_BIN_SHOW_HPP
