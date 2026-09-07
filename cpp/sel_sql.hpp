// SEL → SQL, C++23 implementation.
//
// The translator turns a compiled SEL program into SQL for one dialect, or
// refuses it. Refusal is a first-class answer: a rule that cannot be pushed
// into a database is a normal thing for this layer to say, and saying it is
// more useful than emitting SQL that means something slightly different.
//
// docs/SQL-TRANSLATION.md is the guide; sql/MAP.md specifies the dialect map,
// and sql/errors.md fixes the error codes. Where this implementation and those
// documents disagree, this implementation is wrong.

#ifndef SEL_SQL_HPP
#define SEL_SQL_HPP

#include <exception>
#include <string>

#include "sel.hpp"

namespace sel::sql {

// --- errors -----------------------------------------------------------------

// Unlike SelError the message here is expected to be READ. A translator error
// is not a bug report, it is an answer: *this rule cannot be pushed into this
// database, and here is what stopped it.* Codes are contract, messages are not.
//
// A separate type from SelError on purpose. try_translate() catches this and
// nothing else, so a malformed registration -- which throws std::runtime_error
// from Map -- is never swallowed as if it were a rule the database cannot run.
class SqlError : public std::exception {
 public:
  SqlError(std::string code, std::string message, Pos pos = {});

  const std::string& code() const noexcept { return code_; }
  const std::string& message() const noexcept { return message_; }
  int line() const noexcept { return pos_.line; }
  int col() const noexcept { return pos_.col; }
  int offset() const noexcept { return pos_.offset; }
  Pos pos() const noexcept { return pos_; }

  // "CODE at line:col: message"
  std::string str() const;
  const char* what() const noexcept override { return message_.c_str(); }

 private:
  std::string code_;
  std::string message_;
  Pos pos_;
};

// Raise at the point of failure. Nothing wraps this on the way out, the same
// rule spec/errors.md sets for the evaluator.
[[noreturn]] void refuse(const std::string& code, const std::string& message,
                         Pos pos = {});

// --- the builder escape hatch -----------------------------------------------

// What a template cannot say, an application says in code (sql/MAP.md §4.4).
//
// Declared and not defined here because of who does what: the *map* stores a
// builder and never looks inside one, the *translator* calls it. Keeping the
// type opaque is what lets sel_sql_map.hpp -- and the generated table beside it
// -- know nothing about fragments, emitters or the translator at all.
class Builder;

}  // namespace sel::sql

#endif  // SEL_SQL_HPP
