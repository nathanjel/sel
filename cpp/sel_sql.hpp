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
#include <map>
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
// column whose binding did not declare a type is one nothing here can vouch for.
// Where the server can be asked the question instead -- is this a number? -- the
// operand is wrapped and translation continues; where it cannot -- is this a
// boolean? -- the expression is refused.
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

  // Usable as a condition. BOOL and nothing else: every other kind is refused,
  // UNKNOWN included, because no dialect can ask whether a value is a boolean
  // and answering anyway matched rows SEL refuses. Silently allowing `WHERE
  // o.total` is how a database turns a validation rule into the truthiness test
  // SEL spent its design avoiding.
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


// --- bindings ----------------------------------------------------------------
//
// How an application says where a SEL variable lives in the schema.
//
// Constructed in code, never decoded from a document. That is the whole point:
// this layer used to take a nested map shaped like JSON and validate it by hand,
// and a cross-host review found the hosts disagreeing about what a malformed one
// meant -- `from: ["order_items"]` was refused by PHP and spliced into an
// identifier by Python; `items` as an object was accepted by one and refused by
// the other. None of that was a decision anybody made; it was json_decode's
// shape rules on one side and Python's on the other, leaking into the
// translator. A typed constructor makes the whole class unrepresentable rather
// than refusable.
//
// See docs/SQL-TRANSLATION.md §5.


// One column. Either a name (optionally qualified by a table) or raw SQL this
// layer will not read.
struct ColumnSpec {
  bool is_raw = false;
  std::string raw;               // when is_raw
  std::string column;            // when !is_raw
  std::string table;             // when !is_raw; empty means unqualified
  // What the kind guards read. Leaving it UNKNOWN is honest and costs: nothing
  // here can vouch for the column, so where the server can be asked the
  // question instead -- is this a number? -- the operand is wrapped, and where
  // it cannot -- is this a boolean? -- the expression is refused.
  SqlKind type = SqlKind::Unknown;
};

struct RelationSpec {
  bool from_is_raw = false;      // a query the application wrote
  std::string from;              // a table name, or that query
  std::optional<std::string> alias;
  // Insertion-ordered, keys already ASCII-upper-cased -- once, here, so every
  // consumer looks one up the same way.
  std::vector<std::pair<std::string, ColumnSpec>> fields;
  // The field a bare reference means. Only a ONE-field relation may declare it:
  // a wider row is a map in SEL, and a map is not the value of one of its
  // fields.
  std::optional<std::string> scalar;
  std::optional<std::string> correlate;   // SQL, joining back to the outer row

  const ColumnSpec* field(std::string_view name) const;
};

// A validated, normalised binding. The factories are the only way in.
class Binding {
 public:
  enum class Kind { Column, Columns, Relation, Value };

  // --- the four kinds. Each throws SqlError E_SQL_BINDING, which is the class
  // an application catches -- and which try_translate() therefore swallows,
  // exactly as it does in the other hosts.

  static Binding column(std::string col, std::optional<std::string> table = std::nullopt,
                        SqlKind type = SqlKind::Unknown);
  // A column expressed as SQL this layer will not read. The one place an
  // application writes SQL here; it is emitted verbatim, so whatever it contains
  // is the application's promise rather than this layer's -- which is exactly
  // why it is a named constructor and not a key somebody can leave in a map by
  // accident.
  static Binding raw(std::string sql, SqlKind type = SqlKind::Unknown);
  // An ordered set of columns, iterated by an aggregate and indexed by position:
  // the first is V[1].
  static Binding columns(std::vector<Binding> items);
  static Binding relation(std::string from,
                          std::optional<std::string> alias = std::nullopt,
                          std::vector<std::pair<std::string, Binding>> fields = {},
                          std::optional<std::string> scalar = std::nullopt,
                          std::optional<std::string> correlate = std::nullopt);
  // The same, over a query the application writes rather than a table.
  static Binding relation_query(std::string query,
                                std::optional<std::string> alias = std::nullopt,
                                std::vector<std::pair<std::string, Binding>> fields = {},
                                std::optional<std::string> scalar = std::nullopt,
                                std::optional<std::string> correlate = std::nullopt);
  // A constant the application supplies, inlined as a literal.
  //
  // Takes a Value, never a native number or string, and that is the fix for the
  // last cross-host divergence here: PHP's json_decode turns a 20-digit integer
  // into a float, Python keeps it exact, and JS cannot tell 1.0 from 1. Asking
  // the caller for a Value moves the decision to the line that knows the answer.
  //
  // `type` is NUM or nothing. It decides whether the value is emitted quoted,
  // which is a question no inspection can settle: SEL numbers ARE text values,
  // so Value::num("5.00") and Value::text("5.00") are one object.
  static Binding value(sel::Value v, std::optional<SqlKind> type = std::nullopt);

  Kind kind() const { return kind_; }
  const ColumnSpec& as_column() const { return column_; }
  const std::vector<ColumnSpec>& as_columns() const { return columns_; }
  const RelationSpec& as_relation() const { return relation_; }
  const sel::Value& as_value() const { return value_; }
  // Nothing for an untyped value binding, which is emitted quoted.
  std::optional<SqlKind> value_type() const { return value_type_; }

 private:
  Binding() = default;

  Kind kind_ = Kind::Column;
  ColumnSpec column_;
  std::vector<ColumnSpec> columns_;
  RelationSpec relation_;
  sel::Value value_;
  std::optional<SqlKind> value_type_;
};

// Where each SEL variable lives. The host answers dependencies() with one of
// these per name; nothing is inferred, and a name with no binding is
// E_SQL_UNBOUND rather than a guess at a column.
class Bindings {
 public:
  Bindings() = default;
  explicit Bindings(std::vector<std::pair<std::string, Binding>> bindings);

  bool has(std::string_view name) const;
  // Throws E_SQL_UNBOUND when the name has no binding.
  const Binding& get(std::string_view name, Pos pos = {}) const;
  std::vector<std::string> names() const;

  // A relation alias may name only one thing. Two relations sharing an alias in
  // one expression would produce a subquery correlated to the wrong rows, and
  // the host chose the aliases, so the host can fix them.
  void check_aliases(Pos pos = {}) const;

 private:
  // Sorted, which is what names() and the "bound names are ..." message want:
  // the Python host spells those `sorted(self._map)`.
  std::map<std::string, Binding, std::less<>> map_;
  // The caller's own order, which is what check_aliases wants. Python's
  // Bindings is a dict and iterates it insertion-ordered there; iterating the
  // sorted map instead would name a different one of two colliding relations in
  // the refusal. No case asserts that message, so this is closing a divergence
  // before it is load-bearing rather than after.
  std::vector<std::string> order_;
};


// --- the public interface ----------------------------------------------------

// Translation options. One flag today; a struct rather than a bool so that a
// second one does not change every call site.
struct Options {
  // Refuse anything the dialect can only translate approximately -- an entry
  // carrying a caveat. An application that would rather evaluate a rule in the
  // host than push down a near-equivalent turns this on.
  bool strict = false;
};

// See docs/SQL-TRANSLATION.md §10.
class Sql {
 public:
  // Translate a compiled program into a SQL expression for one dialect.
  //
  // Throws SqlError, whose message is written to be READ. Use this when you want
  // to know why a rule cannot be pushed down: during development, in a
  // build-time audit of a rule set, or in a test.
  static Fragment translate(const Program& program, const std::string& dialect,
                            const Bindings& bindings = {},
                            const Options& options = {});

  // The same, returning nothing instead of throwing.
  //
  // Refusal is an expected, ordinary outcome -- "this rule cannot be pushed
  // down, evaluate it here instead" -- and an expected outcome should not need a
  // catch to observe. Only SqlError is caught: a bug in the translator, or a
  // malformed registration, must not be swallowed by the path that exists to
  // handle refusals.
  static std::optional<Fragment> try_translate(const Program& program,
                                               const std::string& dialect,
                                               const Bindings& bindings = {},
                                               const Options& options = {});

  // Every dialect that may be named in a translate() call.
  static std::vector<std::string> dialects();
};

}  // namespace sel::sql

#endif  // SEL_SQL_HPP
