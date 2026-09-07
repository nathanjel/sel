// The SEL → SQL dialect map: the shipped table, and the runtime registration an
// application extends it with. sql/MAP.md is normative.
//
// Internal to the SQL layer, as sel_ast.hpp is to the evaluator. Consumers
// include sel_sql.hpp; this file is what sel_sql.cpp and the generated table
// beside it agree on.
//
// **No data file is read at run time, here or in any host.** sql/dialects/*.json
// is the single place the map is authored, and tools/gen-sql-map.mjs renders it
// to source: sel_sql_map_data.cpp here, _map.py, MapData.php and _map.mjs
// elsewhere. An application deploying SEL ships an executable, not an
// executable plus a directory of dialect documents.
//
// The generated table is `constexpr` and therefore entirely `.rodata`: no
// static constructor runs to build it, and a link that never calls
// shipped_map() drops all of it under --gc-sections.

#ifndef SEL_SQL_MAP_HPP
#define SEL_SQL_MAP_HPP

#include <cstddef>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "sel_sql.hpp"

namespace sel::sql {

// --- what the map is made of -------------------------------------------------
//
// One set of types, read by the translator, produced by both the generated
// table and a runtime registration. The generated table spells them with
// `constexpr` initialisers over static storage; a registration owns its strings
// and hands back a view into them. The translator cannot tell the two apart,
// which is the point: sql/MAP.md §4.5¼ says anything the shipped map contains,
// an application could have registered.
//
// Every member carries a default initialiser. Designated initialisers leave the
// rest value-initialised, and without a default for each one -Wextra reports
// every generated line as a missing field.

// A value that may be withdrawn. Absent and present-but-null are different
// answers all through the map -- sql/MAP.md §3 gives a null `binaryLiteral` the
// job of *refusing* BIN literals rather than deferring to the base -- so this
// cannot collapse to a bare string_view.
struct Maybe {
  bool present = false;
  std::string_view text{};
};

// One arm of an arity-keyed or variant-keyed template body: the key is an
// argument count, "*", or a variant name.
struct Keyed {
  std::string_view key{};
  std::string_view value{};
};

enum class BodyKind {
  One,        // `tpl` is a string
  ByCount,    // `tpl` is keyed by argument count, or by "*"
  Variants,   // `variants` is keyed by variant name
};

enum class EntryKind {
  Refusal,    // withdrawn: `reason` carries why, when there is a why
  Template,   // rendered from `one` or `keyed`
  Builder,    // host code, registered at run time and never shipped
};

enum class LexKind {
  Text,       // a string
  Escapes,    // a character-to-replacement map, as textEscape is
  Withdrawn,  // present and null: stop walking the chain, answer nothing
};

// One `ops`, `funcs` or `skel` entry.
//
// Field order is load-bearing: tools/gen-sql-map.mjs emits designated
// initialisers, and C++ requires those in declaration order. Reordering these
// members breaks the generated file rather than this one, so the emitter and
// this struct are checked against each other -- see the static_asserts at the
// foot of sel_sql_map_data.cpp.
struct Entry {
  std::string_view key{};
  EntryKind kind = EntryKind::Refusal;

  // The one place in an entry where present-and-null differs from absent:
  // withdrawing with a reason and withdrawing without one give the caller
  // different messages. Every other optional field below has a vocabulary that
  // cannot contain the empty string, so empty means absent and says so.
  Maybe reason{};                    // Refusal

  BodyKind body = BodyKind::One;     // Template
  std::string_view one{};            //   BodyKind::One
  std::span<const Keyed> keyed{};    //   ByCount / Variants

  // A skeleton is a template with NAMED slots and no kind -- the translator
  // decides what a CASE or a subquery yields, not the map -- so `ret` is empty
  // for a `skel` entry and set for every other template.
  std::string_view ret{};
  std::string_view caveat{};
  std::string_view since{};

  // Always a closed range. No dialect document narrows an arity open-ended and
  // no host can register one, so there is no [n, null] here -- that shape
  // belongs to SEL's own arity in Rules, where IN really does have it.
  bool has_arity = false;
  int arity_min = 0;
  int arity_max = 0;

  // Never shipped: no generated entry sets this, and a builder is the one thing
  // a dialect document cannot express. Points into storage the registry owns.
  const Builder* builder = nullptr;
};

struct Escape {
  std::string_view from{};
  std::string_view to{};
};

struct Lexical {
  std::string_view key{};
  LexKind kind = LexKind::Text;
  std::string_view text{};           // Text
  std::span<const Escape> escapes{}; // Escapes
};

struct Dialect {
  std::string_view name{};
  // Empty means a root, as `ansi` is. A dialect that merely *forgot* to say
  // cannot reach this type: DialectSpec::root() and ::extending() are the only
  // two ways to make one, which is how the "must say what it extends" refusal
  // the dynamic hosts raise becomes a thing C++ cannot write.
  std::string_view extends{};
  std::string_view version{};
  // A base is not a target: `ansi` and `mysql-family` name no server anyone
  // runs, and translate() refuses to aim at one.
  bool target = true;
  std::span<const Lexical> lexical{};
  std::span<const Entry> ops{};
  std::span<const Entry> funcs{};
  std::span<const Entry> skel{};
};

// --- the generator's own vocabulary ------------------------------------------
//
// What tools/gen-sql-map.mjs enforces at generation time, generated alongside
// the map so the registry can enforce the same thing at registration time --
// against the vocabulary that file EMITS rather than a second copy of it.

struct Arity {
  std::string_view key{};
  int min = 0;
  int max = 0;
  bool unbounded = false;            // [n, null], as IN has
};

struct Names {
  std::string_view key{};
  std::span<const std::string_view> names{};
};

struct LexType {
  std::string_view key{};
  bool escapes = false;              // "map" in the document; else "string"
};

struct Rules {
  std::span<const std::string_view> caveats{};
  std::span<const std::string_view> ret_kinds{};
  std::span<const std::string_view> template_keys{};
  std::span<const Arity> op_arity{};
  std::span<const Arity> func_arity{};
  std::span<const Names> variants{};
  std::span<const Names> skel_slots{};
  std::span<const LexType> lexical_types{};
};

// Defined by the generated sel_sql_map_data.cpp, and by nothing else.
std::span<const Dialect> shipped_map();
const Rules& shipped_rules();

// --- sections ----------------------------------------------------------------

// An enum rather than a string: the dynamic hosts check `section in SECTIONS`
// at the top of every call, and this is C++ not needing to.
enum class Section { Ops, Funcs, Skel };

std::string_view section_name(Section s);
// For a caller reading a section out of text -- a .sqlt register block, a
// config file. Nullopt for anything that is not a section name.
std::optional<Section> section_from_name(std::string_view name);

// --- writing -----------------------------------------------------------------

// One entry, as an application supplies it.
//
// Built with the named constructors and never by filling fields in. An entry
// with both a template and variants, or with neither, is not something the map
// can hold; the dynamic hosts refuse that pair at run time, and this refuses it
// by having no way to say it.
class EntrySpec {
 public:
  // Withdrawn, with the reason handed to whoever asks for it -- exactly as a
  // reason authored in the JSON is. Same mechanism, no second path.
  static EntrySpec withdraw(std::string reason);
  // Withdrawn without one.
  static EntrySpec withdraw();

  // `ret` is one of the kinds in Rules::ret_kinds, "@concat", or
  // "@unify:<n>[,<n>...]".
  static EntrySpec tpl(std::string tpl, std::string ret);
  // Keyed by argument count, or by "*".
  static EntrySpec by_count(
      std::vector<std::pair<std::string, std::string>> arms, std::string ret);
  // Keyed by variant name, for the families in Rules::variants.
  static EntrySpec variants(
      std::vector<std::pair<std::string, std::string>> arms, std::string ret);
  // A `skel` entry: named slots, and no kind for the same reason Entry::ret is
  // absent on one.
  static EntrySpec skeleton(std::string tpl);
  // Host code. sel_sql.hpp is where an application gets a typed way to make one.
  static EntrySpec builder(std::shared_ptr<const Builder> fn);

  EntrySpec& caveat(std::string name);
  EntrySpec& since(std::string version);
  EntrySpec& arity(int lo, int hi);

 private:
  friend class Map;
  // Private, so that the named constructors above are the only way to make one.
  // A default-constructed EntrySpec would be a silent withdrawal, which is the
  // sort of thing that reads like it worked.
  EntrySpec() = default;

  EntryKind kind_ = EntryKind::Refusal;
  bool reason_present_ = false;
  std::string reason_;
  BodyKind body_ = BodyKind::One;
  std::string one_;
  std::vector<std::pair<std::string, std::string>> arms_;
  std::string ret_;
  std::string caveat_;
  std::string since_;
  bool has_arity_ = false;
  int arity_min_ = 0;
  int arity_max_ = 0;
  std::shared_ptr<const Builder> builder_;
};

// One dialect, as an application declares it.
class DialectSpec {
 public:
  // A dialect with no parent, which therefore has to state its own version:
  // there is none to inherit.
  static DialectSpec root(std::string version);
  // The usual case, and the reason this exists at all -- an older or newer
  // server than the shipped map assumes needs no special code, because a
  // version is only another link in the chain:
  //
  //     Map::define_dialect("mariadb-11.8",
  //                         DialectSpec::extending("mariadb").version("11.8"));
  static DialectSpec extending(std::string parent);

  DialectSpec& version(std::string v);
  DialectSpec& target(bool t);
  // A lexical string. Nullopt withdraws the key: sql/MAP.md §3 gives a null
  // value the job of refusing, and lexical() looks keys up by presence so it can.
  DialectSpec& lexical(std::string key, std::optional<std::string> text);
  DialectSpec& lexical_escapes(
      std::string key, std::vector<std::pair<std::string, std::string>> escapes);

 private:
  friend class Map;
  DialectSpec() = default;

  bool root_ = false;
  std::string extends_;
  std::optional<std::string> version_;
  std::optional<bool> target_;
  // Insertion-ordered, because a document's lexical block is and the replay
  // check compares the two.
  std::vector<std::tuple<std::string, LexKind, std::string,
                         std::vector<std::pair<std::string, std::string>>>> lexical_;
};

// --- the map -----------------------------------------------------------------

// Static methods over one process-wide map, which is what the other four hosts
// have: `Map::define` in PHP, a module-level `define` in Python and JS, all
// writing state the translator reads. A per-instance map would be a fifth
// answer to a question the language has already answered.
//
// **Lifetimes.** entry(), lexical() and chain() hand back pointers and views
// into storage the map owns. That storage lives until reset(); a registration
// is never freed before then, even when a later define() replaces it, so a
// pointer taken before a redefinition goes stale rather than dangling.
class Map {
 public:
  // --- registration. Every one of these throws std::runtime_error, NOT
  // SqlError: a malformed registration is a mistake in the application's
  // startup, and try_translate() must not swallow it.

  static void define_dialect(const std::string& name, const DialectSpec& spec);

  // Define or withdraw one entry. Unlike sel::registry, redefinition is allowed
  // and the last writer wins: a duplicate SEL function is always a bug, while a
  // duplicate SQL entry is usually an application deliberately overriding a
  // shipped default for its own schema or server build.
  static void define(const std::string& dialect, Section section,
                     const std::string& key, const EntrySpec& spec);

  static void define_builder(const std::string& dialect, Section section,
                             const std::string& key,
                             std::shared_ptr<const Builder> fn);

  // Forget every runtime registration. For tests; nothing else should need it.
  static void reset();

  // --- lookup

  static bool exists(const std::string& dialect);

  // Every dialect that may be named in a translate() call, sorted.
  static std::vector<std::string> targets();

  // Throws SqlError -- E_SQL_DIALECT -- when `dialect` is unknown or is a base.
  static void require_target(const std::string& dialect, Pos pos = {});

  // Self first, then extends, up to the root. Views live until reset().
  static std::vector<std::string_view> chain(const std::string& dialect);

  static std::string version(const std::string& dialect);

  // A lexical value, or nullptr when no dialect in the chain supplies one --
  // which is the same answer for a key nobody mentions and a key a dialect
  // withdrew, exactly as the other hosts give. The difference between the two
  // is that a withdrawal stops the walk, and it is LexKind::Withdrawn in the
  // stored data that does the stopping.
  static const Lexical* lexical(const std::string& dialect, std::string_view key);

  // One entry, or nullptr for "no dialect in the chain mentioned this key" --
  // what the dynamic hosts spell MISSING. A withdrawn entry is not nullptr: it
  // is an Entry whose kind is Refusal, because *refused* and *never heard of*
  // are different answers and the caller reports them differently.
  static const Entry* entry(const std::string& dialect, Section section,
                            std::string_view key);

 private:
  // Registration validation, on the class because it reads an EntrySpec's
  // private state and nothing else should. What tools/gen-sql-map.mjs enforces
  // at generation time, enforced here at registration time.
  static void check_entry(Section section, const std::string& key,
                          const EntrySpec& spec, const std::string& where);
};

// Dotted-numeric, as sql/MAP.md §4.5 specifies and nothing cleverer.
bool version_at_least(std::string_view have, std::string_view want);

}  // namespace sel::sql

#endif  // SEL_SQL_MAP_HPP
