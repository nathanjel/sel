// The SEL → SQL layer. See sel_sql.hpp.

#include "sel_sql.hpp"

namespace sel::sql {

SqlError::SqlError(std::string code, std::string message, Pos pos)
    : code_(std::move(code)), message_(std::move(message)), pos_(pos) {}

std::string SqlError::str() const {
  return code_ + " at " + std::to_string(pos_.line) + ":" +
         std::to_string(pos_.col) + ": " + message_;
}

void refuse(const std::string& code, const std::string& message, Pos pos) {
  throw SqlError(code, message, pos);
}

}  // namespace sel::sql
