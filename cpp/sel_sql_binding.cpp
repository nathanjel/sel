// See sel_sql_binding.hpp.
//
// The checks are in the bodies rather than in the signatures, deliberately. A
// C++ overload set would refuse a wrong type at compile time and say nothing an
// application could catch; PHP would refuse it with a TypeError, Python's
// annotations enforce nothing at run time, and Lisp's are advisory. A guarantee
// written as a signature is a guarantee most of the hosts do not make. Written
// in the body it is the same refusal, with the same code, everywhere -- and
// SqlError is the class an application catches.

#include "sel_sql_binding.hpp"

#include <algorithm>

namespace sel::sql {
namespace {

std::string ascii_upper(std::string_view s) {
  std::string out(s);
  for (char& c : out) {
    if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
  }
  return out;
}

// An identifier the application supplied has to survive being quoted.
//
// Emit::ident doubles the quote character and passes everything else through,
// which is right for every character but two. A NUL terminates the C string
// libpq and sqlite3 are handed, so `a\0b` is malformed SQL on all four servers
// rather than a column nobody has. An empty name quotes to "", which PostgreSQL
// rejects and the other three accept -- a divergence with no upside.
void check_name(const std::string& what, const std::string& v) {
  if (v.empty()) {
    refuse("E_SQL_BINDING", "a binding has an empty " + what + " name");
  }
  if (v.find('\0') != std::string::npos) {
    refuse("E_SQL_BINDING",
           "a binding has a " + what +
               " name containing a NUL, which no dialect can quote");
  }
}

// Every scalar reachable from a NUM-typed value binding.
void check_numeric(const std::string& where, const sel::Value& v) {
  if (v.size() > 0) {
    for (const auto& [k, child] : v.entries()) {
      check_numeric(where + "[\"" + k + "\"]", child);
    }
    return;
  }
  if (v.is_none()) return;
  if (!v.is_text() || !v.looks_numeric()) {
    const std::string shown =
        v.is_bool() ? (v.boolean_scalar() ? "TRUE" : "FALSE") : v.scalar();
    refuse("E_SQL_BINDING",
           where + " declares type NUM, which asks for it to be emitted unquoted, "
                   "but \"" + shown + "\" is not a number");
  }
  // looks_numeric is broader than canonical, and the emitter writes the
  // canonical form rather than the caller's characters -- correct for the AST
  // path, where the lexer has already canonicalised, and wrong here, where the
  // application supplied the string and the evaluator was handed that same
  // string. "007" translated to 7 while SEL kept "007".
  const std::string text = v.as_text();
  std::string canonical;
  try {
    canonical = sel::Value::num(text).as_text();
  } catch (const SelError&) {
    refuse("E_SQL_BINDING", where + " declares type NUM and \"" + text +
                                "\" is not a number");
  }
  if (canonical != text) {
    refuse("E_SQL_BINDING",
           where + " declares type NUM and is \"" + text +
               "\", which is not how SEL writes that number; a NUM binding is "
               "emitted unquoted and must already be canonical, so pass it as "
               "text or drop the leading zeros");
  }
}

RelationSpec make_relation(bool from_is_raw, std::string from,
                           std::optional<std::string> alias,
                           std::vector<std::pair<std::string, Binding>> fields,
                           std::optional<std::string> scalar,
                           std::optional<std::string> correlate) {
  RelationSpec r;
  r.from_is_raw = from_is_raw;
  r.from = std::move(from);
  r.alias = std::move(alias);
  r.scalar = std::move(scalar);
  r.correlate = std::move(correlate);

  for (auto& [name, b] : fields) {
    if (b.kind() != Binding::Kind::Column) {
      refuse("E_SQL_BINDING",
             "the field " + name +
                 " of a relation binding must be a column binding");
    }
    // ascii_upper, matching PHP's strtoupper: a Unicode upper-caser would fold
    // "ß" to "SS" and change the key's length.
    r.fields.emplace_back(ascii_upper(name), b.as_column());
  }
  if (r.scalar) {
    const std::string want = ascii_upper(*r.scalar);
    const bool found = std::any_of(
        r.fields.begin(), r.fields.end(),
        [&want](const auto& kv) { return kv.first == want; });
    if (!found) {
      refuse("E_SQL_BINDING",
             "a relation binding names " + *r.scalar +
                 " as its scalar, which is not one of its fields");
    }
  }
  return r;
}

}  // namespace

const ColumnSpec* RelationSpec::field(std::string_view name) const {
  for (const auto& [k, spec] : fields) {
    if (k == name) return &spec;
  }
  return nullptr;
}

// --- Binding -----------------------------------------------------------------

Binding Binding::column(std::string col, std::optional<std::string> table,
                        SqlKind type) {
  check_name("column", col);
  if (table) check_name("table", *table);
  Binding b;
  b.kind_ = Kind::Column;
  b.column_.column = std::move(col);
  b.column_.table = table ? *table : std::string{};
  b.column_.type = type;
  return b;
}

Binding Binding::raw(std::string sql, SqlKind type) {
  if (sql.empty()) {
    refuse("E_SQL_BINDING", "a raw column binding cannot be empty");
  }
  Binding b;
  b.kind_ = Kind::Column;
  b.column_.is_raw = true;
  b.column_.raw = std::move(sql);
  b.column_.type = type;
  return b;
}

Binding Binding::columns(std::vector<Binding> items) {
  if (items.empty()) {
    refuse("E_SQL_BINDING", "a columns binding needs at least one column");
  }
  Binding b;
  b.kind_ = Kind::Columns;
  for (std::size_t i = 0; i < items.size(); ++i) {
    if (items[i].kind() != Kind::Column) {
      refuse("E_SQL_BINDING",
             "a columns binding takes column bindings, and item " +
                 std::to_string(i + 1) + " is not one");
    }
    b.columns_.push_back(items[i].as_column());
  }
  return b;
}

Binding Binding::relation(std::string from, std::optional<std::string> alias,
                          std::vector<std::pair<std::string, Binding>> fields,
                          std::optional<std::string> scalar,
                          std::optional<std::string> correlate) {
  check_name("from", from);
  if (alias) check_name("alias", *alias);
  Binding b;
  b.kind_ = Kind::Relation;
  b.relation_ = make_relation(false, std::move(from), std::move(alias),
                              std::move(fields), std::move(scalar),
                              std::move(correlate));
  return b;
}

Binding Binding::relation_query(std::string query, std::optional<std::string> alias,
                                std::vector<std::pair<std::string, Binding>> fields,
                                std::optional<std::string> scalar,
                                std::optional<std::string> correlate) {
  if (query.empty()) {
    refuse("E_SQL_BINDING", "a relation query cannot be empty");
  }
  if (alias) check_name("alias", *alias);
  Binding b;
  b.kind_ = Kind::Relation;
  b.relation_ = make_relation(true, std::move(query), std::move(alias),
                              std::move(fields), std::move(scalar),
                              std::move(correlate));
  return b;
}

Binding Binding::value(sel::Value v, std::optional<SqlKind> type) {
  // The dynamic hosts check here that `type` names a kind at all -- every one of
  // the six, LIST included. SqlKind is an enum, so that check has nothing left
  // to reject and is not written: any value of the type is already a kind. Only
  // NUM changes how the value is emitted; the rest are recorded and ignored,
  // there as here.
  Binding b;
  b.kind_ = Kind::Value;
  b.value_ = std::move(v);
  b.value_type_ = type;
  if (type && *type == SqlKind::Num) check_numeric("this value binding", b.value_);
  return b;
}

// --- Bindings ----------------------------------------------------------------

Bindings::Bindings(std::vector<std::pair<std::string, Binding>> bindings) {
  for (auto& [name, b] : bindings) {
    map_.insert_or_assign(ascii_upper(name), b);
  }
}

bool Bindings::has(std::string_view name) const {
  return map_.find(ascii_upper(name)) != map_.end();
}

const Binding& Bindings::get(std::string_view name, Pos pos) const {
  const std::string key = ascii_upper(name);
  auto it = map_.find(key);
  if (it == map_.end()) {
    std::string tail;
    if (map_.empty()) {
      tail = "; no bindings were given";
    } else {
      tail = "; bound names are ";
      bool first = true;
      for (const auto& [k, v] : map_) {
        (void)v;
        if (!first) tail += ", ";
        first = false;
        tail += k;
      }
    }
    refuse("E_SQL_UNBOUND",
           key + " is read by this rule but no binding says where it lives" + tail,
           pos);
  }
  return it->second;
}

std::vector<std::string> Bindings::names() const {
  std::vector<std::string> out;
  for (const auto& [k, v] : map_) { (void)v; out.push_back(k); }
  return out;
}

void Bindings::check_aliases(Pos pos) const {
  std::map<std::string, std::string> seen;
  for (const auto& [name, b] : map_) {
    if (b.kind() != Binding::Kind::Relation) continue;
    const RelationSpec& r = b.as_relation();
    const std::string alias = r.alias ? *r.alias : r.from;
    auto it = seen.find(alias);
    if (it != seen.end()) {
      refuse("E_SQL_BINDING",
             "relations " + it->second + " and " + name + " share the alias " +
                 alias + "; give each one its own",
             pos);
    }
    seen.emplace(alias, name);
  }
}

}  // namespace sel::sql
