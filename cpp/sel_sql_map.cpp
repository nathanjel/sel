// The dialect map's registry: lookup over the two storage forms, and the
// validation a runtime registration passes. See sel_sql_map.hpp.

#include "sel_sql_map.hpp"

#include <algorithm>
#include <array>
#include <map>
#include <set>
#include <stdexcept>

namespace sel::sql {
namespace {

// --- small helpers -----------------------------------------------------------

// A malformed registration is a mistake in the application's startup, not a
// rule the database cannot run, so this is never SqlError: try_translate()
// catches that and would swallow this.
[[noreturn]] void bad(const std::string& message) {
  throw std::runtime_error(message);
}

// ASCII only, matching sel::registry and PHP's strtoupper. Locale-aware
// upper-casing would fold non-ASCII differently on different hosts, and a
// function name is ASCII by the lexer's rules anyway.
std::string ascii_upper(std::string_view s) {
  std::string out(s);
  for (char& c : out) {
    if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
  }
  return out;
}

std::string join(std::span<const std::string_view> xs) {
  std::string out;
  for (std::size_t i = 0; i < xs.size(); ++i) {
    if (i) out += ", ";
    out.append(xs[i]);
  }
  return out;
}

std::string join(const std::vector<std::string>& xs) {
  std::string out;
  for (std::size_t i = 0; i < xs.size(); ++i) {
    if (i) out += ", ";
    out += xs[i];
  }
  return out;
}

// `[0-9]+(\.[0-9]+)*`, hand-rolled rather than regex: the SQL layer has no
// other use for one, and this is cheaper to read than the pattern.
bool is_dotted(std::string_view s) {
  if (s.empty()) return false;
  std::size_t i = 0;
  while (true) {
    std::size_t start = i;
    while (i < s.size() && s[i] >= '0' && s[i] <= '9') ++i;
    if (i == start) return false;
    if (i == s.size()) return true;
    if (s[i] != '.') return false;
    ++i;
  }
}

// `0|[1-9][0-9]{0,2}` — an argument count, as a template may be keyed by.
bool is_count_key(std::string_view s) {
  if (s == "0") return true;
  if (s.empty() || s.size() > 3 || s[0] < '1' || s[0] > '9') return false;
  return std::all_of(s.begin(), s.end(),
                     [](char c) { return c >= '0' && c <= '9'; });
}

// `@unify:[0-9]+(,[0-9]+)*`
bool is_unify(std::string_view s) {
  constexpr std::string_view prefix = "@unify:";
  if (!s.starts_with(prefix)) return false;
  s.remove_prefix(prefix.size());
  if (s.empty()) return false;
  std::size_t i = 0;
  while (true) {
    std::size_t start = i;
    while (i < s.size() && s[i] >= '0' && s[i] <= '9') ++i;
    if (i == start) return false;
    if (i == s.size()) return true;
    if (s[i] != ',') return false;
    ++i;
  }
}

bool contains(std::span<const std::string_view> xs, std::string_view k) {
  return std::find(xs.begin(), xs.end(), k) != xs.end();
}

const Arity* find_arity(std::span<const Arity> xs, std::string_view k) {
  for (const Arity& a : xs) {
    if (a.key == k) return &a;
  }
  return nullptr;
}

const Names* find_names(std::span<const Names> xs, std::string_view k) {
  for (const Names& n : xs) {
    if (n.key == k) return &n;
  }
  return nullptr;
}

// Every `{slot}` in a template, in order. The renderer reads them the same way.
std::vector<std::string_view> slots_in(std::string_view tpl) {
  std::vector<std::string_view> out;
  for (std::size_t i = 0; i < tpl.size(); ++i) {
    if (tpl[i] != '{') continue;
    std::size_t end = tpl.find('}', i + 1);
    if (end == std::string_view::npos) break;
    out.push_back(tpl.substr(i + 1, end - i - 1));
    i = end;
  }
  return out;
}

// --- owned storage -----------------------------------------------------------
//
// A registration owns its strings and exposes the same Entry / Lexical /
// Dialect the generated table does. Building order matters in both records
// below: the string vectors are filled to their final size FIRST, and only then
// are the view structs built over them. A later push_back would reallocate and
// leave every view pointing at freed bytes -- and short strings, which are most
// of these, move with the vector rather than staying put behind a heap pointer.

struct OwnedEntry {
  std::string key, reason, one, ret, caveat, since;
  std::vector<std::string> arm_keys, arm_values;
  std::vector<Keyed> keyed;
  std::shared_ptr<const Builder> builder;
  Entry view{};
};

struct OwnedDialect {
  std::string name, extends, version;
  std::vector<std::string> lex_keys, lex_texts;
  std::vector<std::string> escape_from, escape_to;
  std::vector<Escape> escapes;
  std::vector<Lexical> lexical;
  bool target = true;
};

struct Registry {
  // An arena, not an owning map. A define() that replaces an existing key
  // leaves the old record in place, so a pointer entry() handed out before the
  // redefinition goes stale rather than dangling. The waste is bounded by the
  // number of registration calls, which is a startup activity; the alternative
  // is a use-after-free in an application that looks an entry up and then
  // overrides it.
  std::vector<std::unique_ptr<OwnedEntry>> entry_arena;
  std::vector<std::unique_ptr<OwnedDialect>> dialect_arena;

  std::map<std::string, OwnedDialect*, std::less<>> extra;
  // dialect -> section -> key -> entry
  std::map<std::string, std::array<std::map<std::string, OwnedEntry*, std::less<>>, 3>,
           std::less<>> overlay;

  // Dialects whose numericGuard has been checked against their ISNUM. The answer
  // cannot change once both are registered, and the shipped dialects pass
  // trivially.
  std::set<std::string, std::less<>> guard_checked;
};

// A function-local static: no static constructor, and no dependence on the
// order two translation units happen to be initialised in.
Registry& reg() {
  static Registry r;
  return r;
}

std::size_t index_of(Section s) { return static_cast<std::size_t>(s); }

std::span<const Entry> section_span(const Dialect& d, Section s) {
  switch (s) {
    case Section::Ops: return d.ops;
    case Section::Funcs: return d.funcs;
    case Section::Skel: return d.skel;
  }
  return {};
}

const Dialect* shipped(std::string_view name) {
  for (const Dialect& d : shipped_map()) {
    if (d.name == name) return &d;
  }
  return nullptr;
}

// The two storage forms answer the same five questions. Every lookup goes
// through this rather than branching on which form it found -- which is also
// what keeps the two-phase rule in entry() the only place the difference shows.
struct Record {
  std::string_view name{};
  std::string_view extends{};  // empty == a root
  std::string_view version{};
  bool target = true;
  std::span<const Lexical> lexical{};
};

std::optional<Record> record(std::string_view name) {
  auto it = reg().extra.find(name);
  if (it != reg().extra.end()) {
    const OwnedDialect& d = *it->second;
    return Record{d.name, d.extends, d.version, d.target, d.lexical};
  }
  if (const Dialect* d = shipped(name)) {
    return Record{d->name, d->extends, d->version, d->target, d->lexical};
  }
  return std::nullopt;
}

// --- registration validation -------------------------------------------------
//
// What tools/gen-sql-map.mjs enforces at generation time, enforced here at
// registration time against the vocabulary that file EMITS. Every refusal below
// closes a place where a hand-written entry could differ from one the generator
// would have accepted.

void check_lexical(std::string_view key, LexKind kind, std::string_view text,
                   const std::string& where) {
  const LexType* type = nullptr;
  for (const LexType& t : shipped_rules().lexical_types) {
    if (t.key == key) { type = &t; break; }
  }
  if (!type) {
    std::vector<std::string> known;
    for (const LexType& t : shipped_rules().lexical_types) known.emplace_back(t.key);
    bad(where + " sets the unknown lexical key " + std::string(key) +
        "; known keys are " + join(known));
  }
  // Null is a WITHDRAWAL everywhere in the map, so it is always allowed.
  if (kind == LexKind::Withdrawn) return;
  if (type->escapes != (kind == LexKind::Escapes)) {
    bad(where + " sets " + std::string(key) + " to a " +
        (kind == LexKind::Escapes ? "map of character to replacement"
                                  : "string") +
        "; it must be " +
        (type->escapes ? "a map of character to replacement" : "a string"));
  }
  if (kind == LexKind::Escapes) return;
  // A quote character that is not a character cannot quote. Left through, the
  // hosts disagreed about what it meant. textCollate is legitimately empty
  // (ansi and sqlite ship it that way); these two are not.
  if (text.empty() && (key == "identQuote" || key == "textQuote")) {
    bad(where + " sets " + std::string(key) +
        " to the empty string; a quote character that is not a character "
        "cannot quote");
  }
}

void check_key(Section section, const std::string& key) {
  const Rules& rules = shipped_rules();
  if (section == Section::Ops && !find_arity(rules.op_arity, key)) {
    bad(key + " is not a SEL operator, so an ops entry for it would never be "
               "looked up");
  }
  // `funcs` keys are SEL function names and case-insensitive; ops and skel keys
  // are looked up verbatim, which is why define() upper-cases only the first.
  if (section == Section::Funcs && !find_arity(rules.func_arity, ascii_upper(key))) {
    bad(key + " is not a SEL function this layer maps; the aggregates and "
               "IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by stage 2 and never "
               "reach the funcs table");
  }
  if (section == Section::Skel && !find_names(rules.skel_slots, key)) {
    std::vector<std::string> known;
    for (const Names& n : rules.skel_slots) known.emplace_back(n.key);
    bad(key + " is not a skeleton; known ones are " + join(known));
  }
}

}  // namespace

// --- EntrySpec ---------------------------------------------------------------

EntrySpec EntrySpec::withdraw(std::string reason) {
  EntrySpec s;
  s.kind_ = EntryKind::Refusal;
  s.reason_present_ = true;
  s.reason_ = std::move(reason);
  return s;
}

EntrySpec EntrySpec::withdraw() {
  EntrySpec s;
  s.kind_ = EntryKind::Refusal;
  return s;
}

EntrySpec EntrySpec::tpl(std::string tpl, std::string ret) {
  EntrySpec s;
  s.kind_ = EntryKind::Template;
  s.body_ = BodyKind::One;
  s.one_ = std::move(tpl);
  s.ret_ = std::move(ret);
  return s;
}

EntrySpec EntrySpec::by_count(
    std::vector<std::pair<std::string, std::optional<std::string>>> arms,
    std::string ret) {
  EntrySpec s;
  s.kind_ = EntryKind::Template;
  s.body_ = BodyKind::ByCount;
  s.arms_ = std::move(arms);
  s.ret_ = std::move(ret);
  return s;
}

EntrySpec EntrySpec::variants(
    std::vector<std::pair<std::string, std::optional<std::string>>> arms,
    std::string ret) {
  EntrySpec s;
  s.kind_ = EntryKind::Template;
  s.body_ = BodyKind::Variants;
  s.arms_ = std::move(arms);
  s.ret_ = std::move(ret);
  return s;
}

EntrySpec EntrySpec::skeleton(std::string tpl) {
  EntrySpec s;
  s.kind_ = EntryKind::Template;
  s.body_ = BodyKind::One;
  s.one_ = std::move(tpl);
  return s;
}

EntrySpec EntrySpec::builder(std::shared_ptr<const Builder> fn) {
  if (!fn) bad("a builder entry needs a builder; use EntrySpec::withdraw() to "
               "take an entry away");
  EntrySpec s;
  s.kind_ = EntryKind::Builder;
  s.builder_ = std::move(fn);
  return s;
}

EntrySpec& EntrySpec::caveat(std::string name) {
  caveat_ = std::move(name);
  return *this;
}

EntrySpec& EntrySpec::since(std::string version) {
  since_ = std::move(version);
  return *this;
}

EntrySpec& EntrySpec::arity(int lo, int hi) {
  has_arity_ = true;
  arity_min_ = lo;
  arity_max_ = hi;
  return *this;
}

// --- DialectSpec -------------------------------------------------------------

DialectSpec DialectSpec::root(std::string version) {
  DialectSpec s;
  s.root_ = true;
  s.version_ = std::move(version);
  return s;
}

DialectSpec DialectSpec::extending(std::string parent) {
  DialectSpec s;
  s.root_ = false;
  s.extends_ = std::move(parent);
  return s;
}

DialectSpec& DialectSpec::version(std::string v) {
  version_ = std::move(v);
  return *this;
}

DialectSpec& DialectSpec::target(bool t) {
  target_ = t;
  return *this;
}

DialectSpec& DialectSpec::lexical(std::string key, std::optional<std::string> text) {
  lexical_.emplace_back(std::move(key),
                        text ? LexKind::Text : LexKind::Withdrawn,
                        text ? std::move(*text) : std::string{},
                        std::vector<std::pair<std::string, std::string>>{});
  return *this;
}

DialectSpec& DialectSpec::lexical_escapes(
    std::string key, std::vector<std::pair<std::string, std::string>> escapes) {
  lexical_.emplace_back(std::move(key), LexKind::Escapes, std::string{},
                        std::move(escapes));
  return *this;
}

// --- sections ----------------------------------------------------------------

std::string_view section_name(Section s) {
  switch (s) {
    case Section::Ops: return "ops";
    case Section::Funcs: return "funcs";
    case Section::Skel: return "skel";
  }
  return "";
}

std::optional<Section> section_from_name(std::string_view name) {
  if (name == "ops") return Section::Ops;
  if (name == "funcs") return Section::Funcs;
  if (name == "skel") return Section::Skel;
  return std::nullopt;
}

// --- registration ------------------------------------------------------------

void Map::define_dialect(const std::string& name, const DialectSpec& spec) {
  if (exists(name)) {
    bad("SQL dialect " + name +
        " is already defined; a name means one dialect");
  }
  const std::string where = "SQL dialect " + name;

  std::string version;
  if (spec.root_) {
    // A root inherits nothing, so it has to state its own version. There is no
    // way to build a root without one -- DialectSpec::root() takes it -- so
    // this is only the override path.
    version = spec.version_ ? *spec.version_ : std::string{};
    if (version.empty()) {
      bad(where + " extends nothing, so it must declare a version; there is "
                  "none to inherit");
    }
  } else {
    std::optional<Record> parent = record(spec.extends_);
    if (!parent) {
      bad(where + " extends " + spec.extends_ + ", which does not exist");
    }
    version = spec.version_ ? *spec.version_ : std::string(parent->version);
  }
  // Dotted-numeric, as sql/MAP.md §4.5 says and nothing cleverer. A live server
  // reports "11.8.8-MariaDB", which is the natural thing to pass and is not a
  // version this map can compare. Refused here, at the line that wrote it.
  if (!is_dotted(version)) {
    bad(where + " has version \"" + version +
        "\", which is not dotted-numeric; strip any suffix a server reports "
        "(11.8.8-MariaDB is 11.8.8)");
  }

  for (const auto& [key, kind, text, escapes] : spec.lexical_) {
    (void)escapes;
    check_lexical(key, kind, text, where);
  }

  auto owned = std::make_unique<OwnedDialect>();
  owned->name = name;
  owned->extends = spec.root_ ? std::string{} : spec.extends_;
  owned->version = std::move(version);
  owned->target = spec.target_ ? *spec.target_ : true;

  // Strings to their final size first, views second. See the note on the type.
  std::size_t escape_count = 0;
  for (const auto& [key, kind, text, escapes] : spec.lexical_) {
    (void)key; (void)kind; (void)text;
    escape_count += escapes.size();
  }
  owned->lex_keys.reserve(spec.lexical_.size());
  owned->lex_texts.reserve(spec.lexical_.size());
  owned->escape_from.reserve(escape_count);
  owned->escape_to.reserve(escape_count);
  for (const auto& [key, kind, text, escapes] : spec.lexical_) {
    (void)kind;
    owned->lex_keys.push_back(key);
    owned->lex_texts.push_back(text);
    for (const auto& [from, to] : escapes) {
      owned->escape_from.push_back(from);
      owned->escape_to.push_back(to);
    }
  }
  owned->escapes.reserve(escape_count);
  for (std::size_t i = 0; i < escape_count; ++i) {
    owned->escapes.push_back(Escape{owned->escape_from[i], owned->escape_to[i]});
  }
  owned->lexical.reserve(spec.lexical_.size());
  std::size_t at = 0;
  for (std::size_t i = 0; i < spec.lexical_.size(); ++i) {
    const auto& [key, kind, text, escapes] = spec.lexical_[i];
    (void)key; (void)text;
    Lexical lx{};
    lx.key = owned->lex_keys[i];
    lx.kind = kind;
    lx.text = owned->lex_texts[i];
    if (kind == LexKind::Escapes) {
      lx.escapes = std::span<const Escape>(owned->escapes).subspan(at, escapes.size());
      at += escapes.size();
    }
    owned->lexical.push_back(lx);
  }

  OwnedDialect* raw = owned.get();
  reg().dialect_arena.push_back(std::move(owned));
  reg().extra.emplace(name, raw);
}

void Map::define(const std::string& dialect, Section section,
                 const std::string& key, const EntrySpec& spec) {
  if (!exists(dialect)) bad("SQL dialect " + dialect + " does not exist");
  check_key(section, key);
  Map::check_entry(section, key, spec,
              "the " + std::string(section_name(section)) + " entry for " + key);

  // Only `funcs` keys are SEL function names, which are case-insensitive.
  // `ops` keys are operator tokens and `skel` keys are camel-case names the
  // translator looks up verbatim -- upper-casing those would store a registered
  // skeleton under a key nothing ever reads, which makes the documented escape
  // hatch silently dead.
  const std::string k = section == Section::Funcs ? ascii_upper(key) : key;

  auto owned = std::make_unique<OwnedEntry>();
  owned->key = k;
  owned->reason = spec.reason_;
  owned->one = spec.one_;
  owned->ret = spec.ret_;
  owned->caveat = spec.caveat_;
  owned->since = spec.since_;
  owned->builder = spec.builder_;

  owned->arm_keys.reserve(spec.arms_.size());
  owned->arm_values.reserve(spec.arms_.size());
  for (const auto& [arm_key, arm_value] : spec.arms_) {
    owned->arm_keys.push_back(arm_key);
    owned->arm_values.push_back(arm_value ? *arm_value : std::string{});
  }
  owned->keyed.reserve(spec.arms_.size());
  for (std::size_t i = 0; i < spec.arms_.size(); ++i) {
    owned->keyed.push_back(
        Keyed{owned->arm_keys[i],
              Maybe{spec.arms_[i].second.has_value(), owned->arm_values[i]}});
  }

  Entry& v = owned->view;
  v.key = owned->key;
  v.kind = spec.kind_;
  v.reason = Maybe{spec.reason_present_, owned->reason};
  v.body = spec.body_;
  v.one = owned->one;
  v.keyed = owned->keyed;
  v.ret = owned->ret;
  v.caveat = owned->caveat;
  v.since = owned->since;
  v.has_arity = spec.has_arity_;
  v.arity_min = spec.arity_min_;
  v.arity_max = spec.arity_max_;
  v.builder = owned->builder.get();

  OwnedEntry* raw = owned.get();
  reg().entry_arena.push_back(std::move(owned));
  reg().overlay[dialect][index_of(section)][k] = raw;
}

void Map::define_builder(const std::string& dialect, Section section,
                         const std::string& key,
                         std::shared_ptr<const Builder> fn) {
  define(dialect, section, key, EntrySpec::builder(std::move(fn)));
}

namespace {

// Every single-quoted run in `tpl`, in order.
//
// Every run, not the first: two genuinely different numeral tests that happen to
// share an earlier literal -- a flag, a collation clause -- compare equal if only
// the first is read. The generator learned this from a decoy that defeated it.
// An unterminated quote opens no run, which is what the other hosts' '([^']*)'
// answers.
std::vector<std::string_view> quoted_runs(std::string_view tpl) {
  std::vector<std::string_view> out;
  std::size_t i = 0;
  while (true) {
    const std::size_t open = tpl.find('\'', i);
    if (open == std::string_view::npos) break;
    const std::size_t close = tpl.find('\'', open + 1);
    if (close == std::string_view::npos) break;
    out.push_back(tpl.substr(open + 1, close - open - 1));
    i = close + 1;
  }
  return out;
}

}  // namespace

void Map::check_numeric_guard(const std::string& dialect) {
  Registry& r = reg();
  if (r.guard_checked.find(dialect) != r.guard_checked.end()) return;
  r.guard_checked.insert(dialect);

  const Lexical* guard = lexical(dialect, "numericGuard");
  if (!guard || guard->kind != LexKind::Text) return;

  const Entry* isnum = entry(dialect, Section::Funcs, "ISNUM");
  if (!isnum || isnum->kind != EntryKind::Template || isnum->body != BodyKind::One
      || isnum->one.empty()) {
    bad("SQL dialect " + dialect + " declares a numericGuard but maps no funcs.ISNUM "
        "with a template for it to agree with; the two ask the same question and "
        "sql/MAP.md section 7 rule 10 is that one place defines a thing");
  }
  const std::vector<std::string_view> want = quoted_runs(isnum->one);
  if (want.empty()) {
    bad("SQL dialect " + dialect + " maps a funcs.ISNUM that carries no quoted "
        "pattern, so its numericGuard has nothing to agree with");
  }
  const std::vector<std::string_view> got = quoted_runs(guard->text);
  std::string missing;
  for (const std::string_view w : want) {
    if (std::find(got.begin(), got.end(), w) == got.end()) {
      if (!missing.empty()) missing += ", ";
      missing += "'";
      missing += w;
      missing += "'";
    }
  }
  if (!missing.empty()) {
    bad("SQL dialect " + dialect + " declares a numericGuard that does not carry "
        + missing + ", which its funcs.ISNUM tests; they ask the same question, and "
        "a guard that asks a different one answers for rows SEL refuses");
  }
}

void Map::reset() {
  Registry& r = reg();
  r.overlay.clear();
  r.extra.clear();
  r.entry_arena.clear();
  r.dialect_arena.clear();
  r.guard_checked.clear();
}

// --- lookup ------------------------------------------------------------------

bool Map::exists(const std::string& dialect) {
  return record(dialect).has_value();
}

std::vector<std::string> Map::targets() {
  std::set<std::string> out;
  for (const Dialect& d : shipped_map()) {
    if (d.target) out.emplace(d.name);
  }
  for (const auto& [name, d] : reg().extra) {
    if (d->target) out.insert(name);
  }
  return {out.begin(), out.end()};
}

void Map::require_target(const std::string& dialect, Pos pos) {
  std::optional<Record> r = record(dialect);
  if (!r) {
    refuse("E_SQL_DIALECT",
           "there is no SQL dialect " + dialect + "; known targets are " +
               join(targets()),
           pos);
  }
  if (!r->target) {
    refuse("E_SQL_DIALECT",
           dialect +
               " is a base other dialects inherit from, not a server anyone "
               "runs; translate to one of " +
               join(targets()),
           pos);
  }
}

std::vector<std::string_view> Map::chain(const std::string& dialect) {
  std::vector<std::string_view> out;
  std::optional<Record> r = record(dialect);
  while (r) {
    // A runtime dialect could extend one that extends it back, and a lookup
    // must answer rather than spin.
    if (std::find(out.begin(), out.end(), r->name) != out.end()) break;
    out.push_back(r->name);
    if (r->extends.empty()) break;
    r = record(r->extends);
  }
  return out;
}

std::string Map::version(const std::string& dialect) {
  std::optional<Record> r = record(dialect);
  if (!r) bad("SQL dialect " + dialect + " does not exist");
  return std::string(r->version);
}

const Lexical* Map::lexical(const std::string& dialect, std::string_view key) {
  // Presence, not nullness, and for the reason entry() walks the way it does:
  // sql/MAP.md §3 says a null lexical value is a WITHDRAWAL -- "a null
  // binaryLiteral refuses BIN literals" -- and treating it as absent walks on to
  // the base and hands the withdrawn value back.
  for (std::string_view d : chain(dialect)) {
    std::optional<Record> r = record(d);
    if (!r) continue;
    for (const Lexical& lx : r->lexical) {
      if (lx.key != key) continue;
      return lx.kind == LexKind::Withdrawn ? nullptr : &lx;
    }
  }
  return nullptr;
}

const Entry* Map::entry(const std::string& dialect, Section section,
                        std::string_view key) {
  const std::vector<std::string_view> ch = chain(dialect);

  // The whole overlay chain first, and only then the generated table.
  // Interleaving the two per level would look tidier and would be wrong: the
  // generated tables are already flattened, so a generated hit at the leaf
  // would shadow a runtime entry registered against a base, and registering
  // against `ansi` is documented to reach every dialect.
  for (std::string_view d : ch) {
    auto it = reg().overlay.find(d);
    if (it == reg().overlay.end()) continue;
    const auto& sec = it->second[index_of(section)];
    auto e = sec.find(key);
    if (e != sec.end()) return &e->second->view;
  }
  for (std::string_view d : ch) {
    const Dialect* sd = shipped(d);
    if (!sd) continue;
    for (const Entry& e : section_span(*sd, section)) {
      if (e.key == key) return &e;
    }
  }
  return nullptr;
}

// --- versions ----------------------------------------------------------------

bool version_at_least(std::string_view have, std::string_view want) {
  auto part = [](std::string_view& s) {
    int n = 0;
    while (!s.empty() && s.front() >= '0' && s.front() <= '9') {
      n = n * 10 + (s.front() - '0');
      s.remove_prefix(1);
    }
    if (!s.empty() && s.front() == '.') s.remove_prefix(1);
    return n;
  };
  while (!have.empty() || !want.empty()) {
    const int a = part(have);
    const int b = part(want);
    if (a != b) return a > b;
  }
  return true;
}

// --- entry validation --------------------------------------------------------

void Map::check_entry(Section section, const std::string& key,
                      const EntrySpec& spec, const std::string& where) {
  // A refusal carries its reason or carries nothing, and has nothing else to
  // check. A builder is host code the map never looks inside.
  if (spec.kind_ == EntryKind::Refusal || spec.kind_ == EntryKind::Builder) return;

  const Rules& rules = shipped_rules();

  if (!spec.caveat_.empty() && !contains(rules.caveats, spec.caveat_)) {
    bad(where + " declares the caveat \"" + spec.caveat_ +
        "\", which is not on the closed list in sql/MAP.md §4.6; a caveat an "
        "application cannot branch on is prose");
  }

  // A skeleton is a template with NAMED slots and no kind: the translator
  // decides what a CASE or a subquery yields, not the map. So it is checked for
  // its slots and nothing else.
  if (section == Section::Skel) {
    if (spec.body_ != BodyKind::One) {
      bad(where + " needs a tpl that is a string; use EntrySpec::skeleton()");
    }
    const Names* allowed = find_names(rules.skel_slots, key);
    for (std::string_view slot : slots_in(spec.one_)) {
      if (allowed && !contains(allowed->names, slot)) {
        bad(where + " uses the slot {" + std::string(slot) + "}; " + key +
            " has " + join(allowed->names) +
            " — a typo would survive as literal text in every query");
      }
    }
    return;
  }

  // A skeleton used where a kind is required -- EntrySpec::skeleton() for an
  // ops or funcs entry -- lands here, which is the same refusal the dynamic
  // hosts give for an entry whose ret was never set.
  if (!contains(rules.ret_kinds, spec.ret_) && spec.ret_ != "@concat" &&
      !is_unify(spec.ret_)) {
    bad(where + " has ret \"" + spec.ret_ + "\"; use one of " +
        join(rules.ret_kinds) + ", @concat or @unify:<n>[,<n>...]");
  }
  if (!spec.since_.empty() && !is_dotted(spec.since_)) {
    bad(where + " has a since that is not dotted-numeric");
  }
  if (spec.has_arity_ &&
      (spec.arity_min_ < 0 || spec.arity_max_ < spec.arity_min_)) {
    bad(where + " has an arity that is not [min, max] of two integers");
  }

  if (spec.body_ == BodyKind::Variants) {
    if (spec.arms_.empty()) bad(where + " has variants that are not a map");
    const Names* allowed = find_names(rules.variants, key);
    if (!allowed) {
      bad(where + " uses variants, and " + key + " is not a variant family");
    }
    for (const auto& [name, value] : spec.arms_) {
      (void)value;
      if (!contains(allowed->names, name)) {
        bad(where + " declares the variant " + name + "; " + key + " has " +
            join(allowed->names));
      }
    }
    return;
  }

  if (spec.body_ != BodyKind::ByCount) return;

  // Every key is an argument COUNT the entry can actually be called with,
  // checked against SEL's own arity narrowed by the entry's. A template keyed
  // by a count the entry can never be called with could never be chosen, and
  // saying so at registration is the difference between a typo and a query that
  // silently takes the wrong arm.
  const Arity* sel = section == Section::Ops
                         ? find_arity(rules.op_arity, key)
                         : find_arity(rules.func_arity, ascii_upper(key));
  int lo = sel ? sel->min : 0;
  bool unbounded = sel ? sel->unbounded : true;
  int hi = sel ? sel->max : 0;
  if (spec.has_arity_) {
    lo = std::max(lo, spec.arity_min_);
    hi = unbounded ? spec.arity_max_ : std::min(hi, spec.arity_max_);
    unbounded = false;
  }
  for (const auto& [arm_key, value] : spec.arms_) {
    (void)value;
    if (arm_key == "*") continue;
    if (!is_count_key(arm_key)) {
      bad(where + " keys a template by \"" + arm_key +
          "\"; an arity-keyed template uses a count or *");
    }
    const int c = std::stoi(arm_key);
    if (c < lo || (!unbounded && c > hi)) {
      bad(where + " keys a template by " + arm_key + ", and " + key +
          " takes " + std::to_string(lo) + " to " +
          (unbounded ? std::string("any") : std::to_string(hi)) +
          " argument(s), so that template could never be chosen");
    }
  }
}

}  // namespace sel::sql
