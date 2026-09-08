// Everything that turns a value or a template into characters. The one place
// quoting happens, so there is one place to get it right.
//
// Internal to the SQL layer.

#ifndef SEL_SQL_EMIT_HPP
#define SEL_SQL_EMIT_HPP

#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "sel_sql.hpp"
#include "sel_sql_map.hpp"

namespace sel::sql {

// The argument a template slot names, or nothing when it names none.
//
// Slots are 0-based and canonical: {0}, {1}, {0:}. `{01}` and `{1\n}` are not
// slots, and tools/gen-sql-map.mjs already refuses both -- so without this the
// shipped map and a runtime-registered template would be read by two different
// grammars. One grammar, and no host's integer parser is consulted.
std::optional<int> slot_index(std::string_view s);

// A required lexical string, or a refusal naming the key. Python reaches these
// through `str(map.lexical(...))`, which turns an absent value into the four
// characters "None" and emits them into the query; every dialect supplies the
// keys that matter, so neither host has ever done it, but "unreachable" is not
// a thing to leave a query generator relying on.
std::string_view lex_text(const std::string& dialect, std::string_view key,
                          Pos pos = {});

// --- literals ---------------------------------------------------------------

// A SEL value as a SQL literal, in the form the caller SAYS it has.
//
// Free functions rather than members so Fragment can join without holding an
// emitter, which keeps a Fragment a plain data object.
//
// The form is passed in and never inferred, because it cannot be inferred: SEL
// numbers *are* TEXT values (spec §4), so Value::num("5.00") and
// Value::text("5.00") are the same object and no predicate can tell "the author
// wrote 5.00" from "the author wrote \"5.00\"". Only the AST knows.
//
// Getting this wrong is not cosmetic. Emitted bare, `"5.00" $== "5"` becomes
// `5.00 = 5`, which the database answers TRUE and SEL answers FALSE.
std::string literal(const std::string& dialect, const Value& v,
                    SqlKind form = SqlKind::Text, Pos pos = {});

std::string text_literal(const std::string& dialect, const std::string& text);

// The params-mode placeholder for slot n, 1-based.
std::string placeholder(const std::string& dialect, int n);

// --- the dialect-bound half --------------------------------------------------

class Emit {
 public:
  explicit Emit(std::string dialect) : dialect_(std::move(dialect)) {}

  const std::string& dialect() const { return dialect_; }
  const Lexical* lex(std::string_view key) const {
    return Map::lexical(dialect_, key);
  }

  // A table or column name, quoted. The quote character is doubled -- or
  // whatever identEscape says -- inside the name, which is what stops a binding
  // naming a column `a"b` from ending the identifier early.
  std::string ident(const std::string& name) const;
  // table.column, or just the column when no table was given.
  std::string column(const std::string& table, const std::string& col) const;

  // An operand a numeric context will read as a number, made safe to read.
  //
  // SEL raises E_NOT_NUM for text that is not a number, and the server does
  // not: CAST('x' AS DECIMAL) is 0 on MariaDB, MySQL and SQLite, so a rule
  // comparing against 0 matched every row of a text column. Wrapping the
  // operand so a non-number becomes NULL keeps the warrant -- NULL is not
  // selected, which is what SEL failing has to look like from SQL.
  //
  // Not applied to a NUM operand: the binding said it is a number, and that
  // declaration is where the promise transfers. It is also the only way to keep
  // the index, since the guard is a function of the column.
  //
  // The pattern is SEL's own numeral grammar and lives in the map beside
  // funcs.ISNUM, which asks the same question; tools/gen-sql-map.mjs requires
  // the two to agree. A dialect that cannot ask it -- sqlite has no REGEXP,
  // ansi has no regex -- declares no numericGuard, and this refuses rather than
  // emitting something that answers when SEL would not.
  Fragment numeric_operand(const Fragment& f, Pos pos = {}) const;

  // An operand of a byte comparison: cast to a character type, then given the
  // dialect's binary collation.
  //
  // Both halves are needed and neither is enough alone. Without the collation
  // MariaDB's default is case-insensitive, so `"A" $== "a"` is true there and
  // false in SEL. Without the cast the collation does not stop two numeric
  // operands being compared as numbers, so `3.0 EQL 3` is true there and false
  // in SEL -- EQL is structural and does not normalise numbers.
  Fragment text_operand(const Fragment& f) const;

  // Fill a template with already-rendered arguments, producing a part list.
  //
  // Splicing part lists rather than strings is the whole point: an argument
  // carrying parameter slots keeps them. Concatenating into strings first would
  // work exactly until a literal contained something that looked like a
  // placeholder.
  //
  // Slot numbers are ABSOLUTE from the moment the literal is created -- one
  // translation has one parameter vector, held by the Translator, and an
  // intermediate Fragment carries indices into it rather than a vector of its
  // own. So splicing copies slots verbatim and never renumbers. The
  // alternative, every Fragment owning its own params and being renumbered on
  // each splice, is the same information arranged so that one missed
  // renumbering silently binds the wrong value to the wrong placeholder.
  //
  // `{key}` and `{key:n}` lexical forms are expanded here too. The generator has
  // already done that for the shipped map; this is for entries an application
  // registers at run time, which never pass through it.
  //
  // `expanding` is the set of lexical keys this call is already inside. A
  // lexical value may reference another, and nothing stopped one from
  // referencing itself: a dialect registering {'textCast': 'X({textCast:0})'}
  // recursed until the host died -- a crash through the public API, which is
  // what every other guard in this layer exists to prevent. The cycle is
  // refused rather than a depth capped, because the cycle is the actual mistake
  // and a depth cap would need a number nobody can justify. With cycles refused
  // the chain is bounded by the number of lexical keys, which is fifteen.
  std::vector<Fragment::Part> fill(std::string_view tpl,
                                   std::span<const Fragment> args, Pos pos = {},
                                   const std::set<std::string>& expanding = {}) const;

 private:
  std::string dialect_;
};

}  // namespace sel::sql

#endif  // SEL_SQL_EMIT_HPP
