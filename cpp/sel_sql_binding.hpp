// How an application says where a SEL variable lives in the schema, and what an
// aggregate binder names for the duration of one element.
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

#ifndef SEL_SQL_BINDING_HPP
#define SEL_SQL_BINDING_HPP

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "sel_sql.hpp"

namespace sel::sql {

// One column. Either a name (optionally qualified by a table) or raw SQL this
// layer will not read.
struct ColumnSpec {
  bool is_raw = false;
  std::string raw;               // when is_raw
  std::string column;            // when !is_raw
  std::string table;             // when !is_raw; empty means unqualified
  // What the kind guards read. Leaving it UNKNOWN is honest and costs the
  // guards: an UNKNOWN operand passes every check, because the binding did not
  // say and nothing here can either.
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
  // Ordered, so names() and the "bound names are ..." message are stable.
  std::map<std::string, Binding, std::less<>> map_;
};

}  // namespace sel::sql

#endif  // SEL_SQL_BINDING_HPP
