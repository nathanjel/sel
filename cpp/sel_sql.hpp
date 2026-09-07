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
#include <functional>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

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

// --- kinds ------------------------------------------------------------------

// The static kind a fragment produces. UNKNOWN is honest rather than absent: a
// column whose binding did not declare a type passes every kind guard, because
// the binding did not say and nothing here can either.
enum class SqlKind { Num, Text, Bool, Bin, Unknown, List };

std::string_view kind_name(SqlKind k);
std::optional<SqlKind> kind_from_name(std::string_view name);

// How a fragment is rendered. Inline puts literals in the SQL; Params puts
// placeholders there and hands the values back from bindings(). Debug numbers
// the slots as ~1~, ~2~ — the .sqlt suite asserts on that form, because it shows
// where the placeholders land without depending on how a dialect spells one.
enum class Mode { Inline, Params, Debug };

std::string_view mode_name(Mode m);
std::optional<Mode> mode_from_name(std::string_view name);

// --- fragments ---------------------------------------------------------------

// A rendered SQL expression.
//
// `parts` alternates finished SQL and parameter slots. The renderer never
// concatenates a literal into a string, so inline and params output are two ways
// of joining ONE structure rather than two code paths. A part list cannot be
// confused about where a literal ends, whatever the literal contains, and that
// is the class of bug this shape exists to make unreachable.
class Fragment {
 public:
  // Either finished SQL, or the 1-based index of a value in `params`.
  struct Part {
    bool is_slot = false;
    std::string sql;   // when !is_slot
    int slot = 0;      // when is_slot
  };

  Fragment() = default;
  Fragment(std::vector<Part> parts, SqlKind kind, std::string dialect);

  // Usable in a select list, GROUP BY, ORDER BY or HAVING. Any kind but LIST,
  // which is not a SQL value at all.
  std::string as_value(Mode mode = Mode::Inline) const;

  // Usable as a condition. BOOL as it stands; UNKNOWN wrapped in the dialect's
  // IS TRUE test, since a column of unknown type may be NULL and SEL has no
  // third truth value to give back. A NUM or TEXT fragment is refused rather
  // than accepted: silently allowing `WHERE o.total` is how a database turns a
  // validation rule into the truthiness test SEL spent its design avoiding.
  std::string as_condition(Mode mode = Mode::Inline) const;

  // The bound values for Params mode, in PLACEHOLDER order -- derived from the
  // part list rather than returned as stored, because the two orders are not the
  // same. A slot is numbered when it is created, and the template decides where
  // it lands: FIND(needle, hay) maps to INSTR({1}, {0}), so the second slot
  // created is the first one emitted. A positional `?` carries no number, so a
  // driver binds the first value to the first placeholder -- right only if this
  // walks the output. A slot appearing twice yields its value twice, which is
  // also right.
  std::vector<Value> bindings() const;

  SqlKind kind() const { return kind_; }
  const std::string& dialect() const { return dialect_; }
  const std::vector<std::string>& caveats() const { return caveats_; }
  const std::vector<Part>& parts() const { return parts_; }
  const std::vector<Value>& params() const { return params_; }

 private:
  friend class Translator;
  friend class Emit;

  std::string join(Mode mode) const;
  // True for a slot rendered as a literal in every mode, never as a parameter.
  // Three forms qualify, for one underlying reason: none carries any character
  // the caller chose, so there is nothing for a placeholder to protect, and each
  // is damaged by being sent as a string. See the Python host's Fragment for the
  // full account -- NUM because no coercion of a bound string reproduces a bare
  // numeric literal, BOOL because the token comes out of the dialect document,
  // BIN because a driver sends bytes through the connection encoding.
  bool is_inline_slot(int slot) const;

  std::vector<Part> parts_;
  std::vector<Value> params_;
  // The literal form of each slot, parallel to params_. Kept beside the values
  // rather than derived from them because it cannot be derived: SEL numbers ARE
  // text values, so Value::num("5.00") and Value::text("5.00") are one object.
  std::vector<SqlKind> param_kinds_;
  SqlKind kind_ = SqlKind::Unknown;
  std::string dialect_;
  std::vector<std::string> caveats_;
};

// --- the builder escape hatch -----------------------------------------------

// What a template cannot say, an application says in code (sql/MAP.md §4.4).
//
// A class rather than a bare std::function so that the map can hold one behind a
// pointer to an incomplete type: sel_sql_map.hpp and the generated table beside
// it know nothing about fragments, emitters or the translator, and this is what
// keeps that true.
class Emit;
class Builder {
 public:
  using Fn = std::function<Fragment(Emit&, std::span<const Fragment>, Pos)>;

  explicit Builder(Fn fn) : fn_(std::move(fn)) {}
  Fragment operator()(Emit& emit, std::span<const Fragment> args, Pos pos) const {
    return fn_(emit, args, pos);
  }

 private:
  Fn fn_;
};

}  // namespace sel::sql

#endif  // SEL_SQL_HPP
