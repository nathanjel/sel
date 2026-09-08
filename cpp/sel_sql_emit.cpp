// See sel_sql_emit.hpp.

#include "sel_sql_emit.hpp"

#include <algorithm>

namespace sel::sql {
namespace {

// `0|[1-9][0-9]{0,2}` — a canonical, three-digit-capped slot index.
bool is_slot(std::string_view s) {
  if (s == "0") return true;
  if (s.empty() || s.size() > 3 || s[0] < '1' || s[0] > '9') return false;
  return std::all_of(s.begin(), s.end(),
                     [](char c) { return c >= '0' && c <= '9'; });
}

std::string replace_all(std::string_view in, std::string_view from,
                        std::string_view to) {
  std::string out;
  std::size_t i = 0;
  while (i < in.size()) {
    if (!from.empty() && in.compare(i, from.size(), from) == 0) {
      out.append(to);
      i += from.size();
    } else {
      out += in[i++];
    }
  }
  return out;
}

std::string to_hex(std::string_view bytes) {
  static constexpr char DIGITS[] = "0123456789abcdef";
  std::string out;
  out.reserve(bytes.size() * 2);
  for (unsigned char b : bytes) {
    out += DIGITS[b >> 4];
    out += DIGITS[b & 0x0f];
  }
  return out;
}

// The only unquoted output in the layer, and therefore the one thing that has to
// be a number.
//
// The AST path arrives already parsed, but a `value` binding declaring NUM
// reaches here straight from host data, and "1 OR 1=1 -- " would go out
// verbatim. Fragment's part list keeps a literal from being confused with SQL;
// it cannot keep a literal from BEING SQL.
//
// What is emitted is what the parse RECOVERED -- Value::num's canonical form --
// rather than the text the caller supplied. The two agree for everything the
// parser produces, and the difference is the point: proving a string is a number
// and then emitting a different string is a gap, however small, and the gap is
// where "1 OR 1=1" lived. After this the characters that can leave here are
// digits, one `.` and a leading `-`, by construction.
std::string numeric_literal(const std::string& dialect, const Value& v, Pos pos) {
  const std::string text = v.as_text(pos);
  std::string n;
  try {
    n = Value::num(text).as_text();
  } catch (const SelError& e) {
    // Only "that is not a number" becomes a binding refusal. E_RANGE -- a
    // numeral past spec/SPEC.md §6.4's million-digit cap -- is the EVALUATOR's
    // answer about the value itself, and Python lets it out the same way:
    // decimal.parse returns None for the first and RAISES for the second, and
    // nothing there catches the second either.
    //
    // Not reachable through the public API today, and kept anyway because it is
    // the parity that matters if it becomes reachable. Both doors are shut
    // upstream: Binding::value refuses a huge numeral at construction (its
    // looks_numeric test answers false), and the parser refuses a huge literal
    // at compile time. Folding E_RANGE into a SqlError here would put it inside
    // what try_translate() swallows, so a rule the LANGUAGE refuses would come
    // back as "cannot be pushed down" rather than as an error.
    if (e.code() != "E_NOT_NUM") throw;
    refuse("E_SQL_BINDING",
           "a value bound as NUM must be a number, and \"" + text + "\" is not",
           pos);
  }

  // How the dialect spells a number is the dialect's business, and one of them
  // has to spell it as text. SQLite has no exact decimal: 2.50 is a REAL that
  // prints as 2.5, so `2.50 $== 2.5` would be TRUE there and FALSE in SEL.
  // Quoted, the exact characters survive, and SQLite's dynamic typing reads them
  // as a number wherever a number is wanted. Applied AFTER canonicalisation, so
  // the digits-by-construction guarantee is unaffected.
  const Lexical* wrap = Map::lexical(dialect, "numericLiteral");
  if (wrap && wrap->kind == LexKind::Text && wrap->text != "{0}") {
    return replace_all(wrap->text, "{0}", n);
  }
  // A negative number is parenthesised so that a unary minus in front of it
  // cannot produce `--`. MariaDB reads that as double negation and gets the
  // right answer by luck; PostgreSQL and SQLite read it as the start of a line
  // comment and the rest of the expression disappears. Only reachable through a
  // `value` binding, since the parser never produces a signed num node.
  return n.starts_with('-') ? "(" + n + ")" : n;
}

}  // namespace

std::optional<int> slot_index(std::string_view s) {
  if (!is_slot(s)) return std::nullopt;
  int n = 0;
  for (char c : s) n = n * 10 + (c - '0');
  return n;
}

std::string_view lex_text(const std::string& dialect, std::string_view key,
                          Pos pos) {
  const Lexical* lx = Map::lexical(dialect, key);
  if (!lx || lx->kind != LexKind::Text) {
    refuse("E_SQL_UNSUPPORTED",
           "dialect " + dialect + " has no " + std::string(key) +
               ", which this expression needs to be written at all",
           pos);
  }
  return lx->text;
}

// --- literals ---------------------------------------------------------------

std::string literal(const std::string& dialect, const Value& v, SqlKind form,
                    Pos pos) {
  if (form == SqlKind::Bool || v.is_bool()) {
    return std::string(lex_text(dialect, v.as_bool(pos) ? "true" : "false", pos));
  }
  if (form == SqlKind::Bin || v.is_bin()) {
    const Lexical* tpl = Map::lexical(dialect, "binaryLiteral");
    if (!tpl || tpl->kind != LexKind::Text) {
      refuse("E_SQL_UNSUPPORTED",
             "dialect " + dialect + " has no binary literal syntax", pos);
    }
    return replace_all(tpl->text, "{hex}", to_hex(v.as_bytes(pos)));
  }
  // A NONE value has no characters, and asking for them raises a SelError --
  // which try_translate() does not catch, so a host using the refusal-tolerant
  // API got a fatal out of as_value() rather than a refusal. Reachable from
  // ordinary host data: a value binding holding an empty list is an empty result
  // set. Standing alone the variable is refused as a LIST, but as an operand the
  // result kind comes from the template and the LIST-ness is gone by the time
  // anything looks.
  if (v.is_none()) {
    refuse("E_SQL_BINDING",
           "a value binding holding no value cannot be a SQL literal; only an "
           "aggregate can be given an empty binding",
           pos);
  }
  if (form == SqlKind::Num) return numeric_literal(dialect, v, pos);
  return text_literal(dialect, v.as_text(pos));
}

std::string text_literal(const std::string& dialect, const std::string& text) {
  const std::string quote(lex_text(dialect, "textQuote"));
  const Lexical* escape = Map::lexical(dialect, "textEscape");
  if (!escape || escape->kind != LexKind::Escapes) return quote + text + quote;

  // Longest first, so a rule for "\\" is applied before one for "\". A single
  // left-to-right pass, never one replace per rule: replacing "'" with "''" and
  // then "\" with "\\" would rewrite the output of the first rule.
  std::vector<const Escape*> rules;
  for (const Escape& e : escape->escapes) rules.push_back(&e);
  std::stable_sort(rules.begin(), rules.end(),
                   [](const Escape* a, const Escape* b) {
                     return a->from.size() > b->from.size();
                   });

  std::string out;
  std::size_t i = 0;
  while (i < text.size()) {
    bool matched = false;
    for (const Escape* r : rules) {
      if (r->from.empty()) continue;
      if (text.compare(i, r->from.size(), r->from) == 0) {
        out.append(r->to);
        i += r->from.size();
        matched = true;
        break;
      }
    }
    if (!matched) out += text[i++];
  }
  return quote + out + quote;
}

std::string placeholder(const std::string& dialect, int n) {
  const std::string tpl(lex_text(dialect, "placeholder"));
  return tpl.find("{n}") != std::string::npos
             ? replace_all(tpl, "{n}", std::to_string(n))
             : tpl;
}

// --- identifiers -------------------------------------------------------------

std::string Emit::ident(const std::string& name) const {
  const std::string q(lex_text(dialect_, "identQuote"));
  const std::string e(lex_text(dialect_, "identEscape"));
  return q + replace_all(name, q, e) + q;
}

std::string Emit::column(const std::string& table, const std::string& col) const {
  if (table.empty()) return ident(col);
  return ident(table) + "." + ident(col);
}

Fragment Emit::numeric_operand(const Fragment& f, Pos pos) const {
  if (f.kind() == SqlKind::Num) return f;
  const Lexical* guard = lex("numericGuard");
  if (!guard || guard->kind != LexKind::Text) {
    refuse("E_SQL_UNSUPPORTED",
           "dialect " + dialect_ +
               " has no way to ask whether a value is a number, so an operand "
               "it has not been told is one cannot be read as one here; declare "
               "the binding NUM if the column really is numeric",
           pos);
  }
  const Fragment one[] = {f};
  return Fragment(fill(guard->text, one, pos), SqlKind::Num, dialect_);
}

Fragment Emit::text_operand(const Fragment& f) const {
  const Lexical* cast = lex("textCast");
  const Lexical* collate = lex("textCollate");

  std::vector<Fragment::Part> parts = f.parts_;
  if (cast && cast->kind == LexKind::Text && cast->text != "{0}") {
    const Fragment one[] = {f};
    parts = fill(cast->text, one);
  }
  if (collate && collate->kind == LexKind::Text && !collate->text.empty()) {
    Fragment::Part p;
    p.sql = std::string(collate->text);
    parts.push_back(p);
  }
  return Fragment(std::move(parts), SqlKind::Text, dialect_);
}

// --- templates ---------------------------------------------------------------

std::vector<Fragment::Part> Emit::fill(std::string_view tpl,
                                       std::span<const Fragment> args, Pos pos,
                                       const std::set<std::string>& expanding) const {
  std::vector<Fragment::Part> parts;

  const auto push = [&parts](std::string_view s) {
    if (s.empty()) return;
    if (!parts.empty() && !parts.back().is_slot) {
      parts.back().sql.append(s);
      return;
    }
    Fragment::Part p;
    p.sql = std::string(s);
    parts.push_back(std::move(p));
  };
  const auto splice = [&](const Fragment& f) {
    for (const Fragment::Part& p : f.parts_) {
      if (p.is_slot) parts.push_back(p);   // absolute already; see the header
      else push(p.sql);
    }
  };
  const auto join_from = [&](std::size_t from) {
    for (std::size_t k = from; k < args.size(); ++k) {
      if (k != from) push(", ");
      splice(args[k]);
    }
  };

  std::size_t i = 0;
  while (i < tpl.size()) {
    if (tpl[i] == '{' && i + 1 < tpl.size() && tpl[i + 1] == '{') {
      push("{"); i += 2; continue;
    }
    if (tpl[i] == '}' && i + 1 < tpl.size() && tpl[i + 1] == '}') {
      push("}"); i += 2; continue;
    }
    if (tpl[i] != '{') { push(tpl.substr(i, 1)); ++i; continue; }
    const std::size_t end = tpl.find('}', i);
    if (end == std::string_view::npos) { push(tpl.substr(i)); break; }
    const std::string_view slot = tpl.substr(i + 1, end - i - 1);
    i = end + 1;

    if (slot == "*") { join_from(0); continue; }
    if (slot.ends_with(':')) {
      if (std::optional<int> from = slot_index(slot.substr(0, slot.size() - 1))) {
        join_from(static_cast<std::size_t>(*from));
        continue;
      }
    }
    if (std::optional<int> k = slot_index(slot)) {
      if (static_cast<std::size_t>(*k) >= args.size()) {
        refuse("E_SQL_UNSUPPORTED",
               "the mapping for this expression asks for argument " +
                   std::to_string(*k) + ", which it was not given",
               pos);
      }
      splice(args[static_cast<std::size_t>(*k)]);
      continue;
    }

    // A lexical reference, from a runtime-registered template.
    const std::size_t colon = slot.find(':');
    const std::string key(colon == std::string_view::npos ? slot
                                                          : slot.substr(0, colon));
    const std::string arg(colon == std::string_view::npos
                              ? std::string{}
                              : std::string(slot.substr(colon + 1)));
    const Lexical* val = lex(key);
    if (!val || val->kind != LexKind::Text) {
      refuse("E_SQL_UNSUPPORTED",
             "a template used {" + std::string(slot) +
                 "}, which is neither an argument nor a lexical entry of dialect " +
                 dialect_,
             pos);
    }
    if (arg.empty()) { push(val->text); continue; }
    if (expanding.count(key)) {
      refuse("E_SQL_UNSUPPORTED",
             "the " + key + " lexical entry of dialect " + dialect_ +
                 " expands into itself, so filling it would never finish",
             pos);
    }
    // binaryCast converts a TEXT or NUM operand to bytes. An operand that is
    // already BIN needs no conversion, and on PostgreSQL converting it is
    // destructive: text::bytea parses its input as a bytea LITERAL, where \ is
    // one backslash and \x41 is a byte, so the round trip changes the bytes or
    // fails the query. Every other cast is idempotent and applied
    // unconditionally; this is the one whose input kind decides whether it means
    // anything.
    if (key == "binaryCast") {
      if (std::optional<int> ca = slot_index(arg)) {
        const auto at = static_cast<std::size_t>(*ca);
        if (at < args.size() && args[at].kind() == SqlKind::Bin) {
          splice(args[at]);
          continue;
        }
      }
    }
    std::set<std::string> deeper = expanding;
    deeper.insert(key);
    for (const Fragment::Part& p :
         fill(replace_all(val->text, "{0}", "{" + arg + "}"), args, pos, deeper)) {
      if (p.is_slot) parts.push_back(p);
      else push(p.sql);
    }
  }
  return parts;
}

}  // namespace sel::sql
