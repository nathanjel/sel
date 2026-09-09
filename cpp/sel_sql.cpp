// The SEL → SQL layer. See sel_sql.hpp.

#include "sel_sql.hpp"

#include "sel_sql_emit.hpp"
#include "sel_sql_map.hpp"

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

// --- kinds and modes ---------------------------------------------------------

namespace sel::sql {

std::string_view kind_name(SqlKind k) {
  switch (k) {
    case SqlKind::Num: return "NUM";
    case SqlKind::Text: return "TEXT";
    case SqlKind::Bool: return "BOOL";
    case SqlKind::Bin: return "BIN";
    case SqlKind::Unknown: return "UNKNOWN";
    case SqlKind::List: return "LIST";
  }
  return "";
}

std::optional<SqlKind> kind_from_name(std::string_view name) {
  if (name == "NUM") return SqlKind::Num;
  if (name == "TEXT") return SqlKind::Text;
  if (name == "BOOL") return SqlKind::Bool;
  if (name == "BIN") return SqlKind::Bin;
  if (name == "UNKNOWN") return SqlKind::Unknown;
  if (name == "LIST") return SqlKind::List;
  return std::nullopt;
}

std::string_view mode_name(Mode m) {
  switch (m) {
    case Mode::Inline: return "inline";
    case Mode::Params: return "params";
    case Mode::Debug: return "debug";
  }
  return "";
}

std::optional<Mode> mode_from_name(std::string_view name) {
  if (name == "inline") return Mode::Inline;
  if (name == "params") return Mode::Params;
  if (name == "debug") return Mode::Debug;
  return std::nullopt;
}

// --- fragments ---------------------------------------------------------------

Fragment::Fragment(std::vector<Part> parts, SqlKind kind, std::string dialect,
                   std::vector<Value> params,
                   std::vector<SqlKind> param_kinds,
                   std::vector<std::string> caveats,
                   bool exact, bool sargable, bool guard)
    : parts_(std::move(parts)),
      params_(std::move(params)),
      param_kinds_(std::move(param_kinds)),
      kind_(kind),
      dialect_(std::move(dialect)),
      caveats_(std::move(caveats)),
      exact_(exact),
      sargable_(sargable),
      guard_(guard) {}

bool Fragment::is_inline_slot(int slot) const {
  const auto i = static_cast<std::size_t>(slot - 1);
  if (i >= param_kinds_.size()) return false;
  const SqlKind k = param_kinds_[i];
  return k == SqlKind::Num || k == SqlKind::Bool || k == SqlKind::Bin;
}

std::string Fragment::join(Mode mode) const {
  std::string out;
  int nth = 0;
  for (const Part& p : parts_) {
    if (!p.is_slot) { out += p.sql; continue; }
    const auto i = static_cast<std::size_t>(p.slot - 1);
    // Short param_kinds means TEXT, matching the other hosts: a slot whose kind
    // was never recorded is quoted rather than emitted bare.
    const SqlKind form = i < param_kinds_.size() ? param_kinds_[i] : SqlKind::Text;
    const Value& v = params_[i];

    if (mode != Mode::Inline && is_inline_slot(p.slot)) {
      // Never a placeholder; see is_inline_slot(). It does not advance nth
      // either, because it emits no placeholder for a binding to land in.
      out += literal(dialect_, v, form);
      continue;
    }
    ++nth;
    switch (mode) {
      case Mode::Inline: out += literal(dialect_, v, form); break;
      // The ordinal a numbered placeholder carries -- PostgreSQL's $n -- must
      // agree with bindings(), which walks the output. The slot id would not: it
      // is a CREATION number, and a reordering template emits creation numbers
      // out of order.
      case Mode::Params: out += placeholder(dialect_, nth); break;
      case Mode::Debug: out += "~" + std::to_string(nth) + "~"; break;
    }
  }
  return out;
}

std::string Fragment::as_value(Mode mode) const {
  if (kind_ == SqlKind::List) {
    refuse("E_SQL_SHAPE",
           "this expression yields a list, and a SQL expression is a scalar");
  }
  return join(mode);
}

std::string Fragment::as_condition(Mode mode) const {
  if (kind_ == SqlKind::Bool) return join(mode);
  // UNKNOWN was wrapped in isTrue here rather than trusted, which folds NULL to
  // false but not a number: `1 IS TRUE` is TRUE on MariaDB, and SEL raises
  // E_NOT_BOOL for a number in a condition. Wrapping cannot fix that, so an
  // undeclared column is no longer a condition; declare the binding BOOL. Which
  // leaves nothing in this layer reading isTrue, and it stays in the map anyway:
  // it is published vocabulary, and the aggregate skeletons spell the same test
  // on a body already known to be BOOL.
  refuse("E_SQL_SHAPE",
         "a condition must be BOOL, and this expression is " +
             std::string(kind_name(kind_)) +
             "; SQL has no truthiness and neither does SEL");
}

std::vector<Value> Fragment::bindings() const {
  std::vector<Value> out;
  for (const Part& p : parts_) {
    if (!p.is_slot || is_inline_slot(p.slot)) continue;
    out.push_back(params_[static_cast<std::size_t>(p.slot - 1)]);
  }
  return out;
}

}  // namespace sel::sql

// --- the public interface ----------------------------------------------------

#include "sel_sql_translator.hpp"

namespace sel::sql {

Fragment Sql::translate(const Program& program, const std::string& dialect,
                        const Bindings& bindings, const Options& options) {
  // A fresh Translator per call, so the state a refusal leaves behind is
  // unobservable through this API.
  Translator t(dialect, bindings, options);
  return t.translate(program.ast());
}

std::optional<Fragment> Sql::try_translate(const Program& program,
                                           const std::string& dialect,
                                           const Bindings& bindings,
                                           const Options& options) {
  // SqlError ONLY: a bug in the translator, or a malformed registration, must
  // not be swallowed by the path that exists to handle refusals.
  try {
    return translate(program, dialect, bindings, options);
  } catch (const SqlError&) {
    return std::nullopt;
  }
}

std::vector<std::string> Sql::dialects() { return Map::targets(); }

}  // namespace sel::sql
