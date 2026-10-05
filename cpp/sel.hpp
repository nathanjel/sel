// SEL — Simple Expression Language, C++23 implementation.
//
// Drop `sel.hpp`, `sel_ast.hpp`, the three generated headers `sel_limits.hpp`,
// `sel_math_ops.hpp` and `sel_builtin_manifest.hpp`, `sel.cpp` and
// `third_party/srell/` into a project and compile sel.cpp. There is nothing else
// to fetch and nothing to build first. You include this file; the others are
// internal and only have to sit beside `sel.cpp`.
//
// GCC or Clang (or another compiler with their extensions): the decimal core
// uses `__int128`, `__builtin_*` overflow checks and GNU inline assembly. MSVC
// is not supported.
//
//     #include "sel.hpp"
//
//     sel::Value ctx = sel::Value::none();
//     ctx.set("TOTAL", sel::Value::num("59.97"));
//     sel::Value r = sel::compile("TOTAL > 10.00").run(ctx);   // BOOL TRUE
//
// The language is specified in spec/SPEC.md, which is normative: where this
// implementation and that document disagree, this implementation is wrong.
//
// Threads. What may be shared, and what may not:
//
//   * A `Program` is immutable once compile() returns, and may be run on several
//     threads at once -- its first run included, which builds the optimised tree
//     exactly once -- PROVIDED every run gets a context of its own.
//   * A `Value` is not thread-safe, not even for reading: it is a handle whose
//     reference count is a plain integer, and reading a value takes and drops
//     references. A context, and every value reachable from it, belongs to one
//     thread at a time. Two threads running even a read-only rule over ONE
//     context is a data race; give each its own (build it on that thread, or
//     clone() it there from a value no other thread is touching).
//   * register_function() may be called while other threads compile and run
//     programs (spec/SPEC.md §8.1); a program keeps the function it was
//     compiled against. A host function runs on the thread running the program
//     and is handed that thread's values.
//   * The SQL layer states its own rule at the top of sel_sql.hpp: translation
//     may run on several threads once the dialects it uses are registered.
//
// cpp/tests/program_race.cpp holds the first point under the thread sanitizer
// (`make -C cpp tsan`).

#ifndef SEL_HPP
#define SEL_HPP

#include "sel_limits.hpp"

#include <cstddef>
#include <cstdint>
#include <exception>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace sel {

// --- errors -----------------------------------------------------------------

// A source position, 1-based in code points. The default is "no position", used
// for failures raised from host code rather than from a node.
struct Pos {
  int line = 0;
  int col = 0;
  int offset = 0;
};

// Every failure. `code` is a stable identifier from spec/errors.md and part of
// the language's contract; `message` is human text, free to change and to be
// translated. Conformance tests assert on the code and the position only.
class SelError : public std::exception {
 public:
  SelError(std::string code, std::string message, Pos pos);

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

// --- limits -----------------------------------------------------------------

// spec/SPEC.md §6.4's three caps, which are one number: the parser's nesting,
// the evaluator's, and a value's. Each is a recursion over a structure the input
// can grow without bound, and each finds this host's stack instead of an error
// if it is not counted.
//
// Public because it is part of the language's contract rather than this
// implementation's tuning -- every host caps at the same place, and a program
// refused here is refused everywhere. The SEL->SQL translator is the caller that
// made it public: it has a fourth use for the number, refusing to translate an
// expression that nests deeper than SEL will evaluate rather than emitting SQL
// for a rule that could never run. It reads this rather than repeating 200, so
// the two cannot drift.
inline constexpr int MAX_DEPTH = static_cast<int>(sel_limits::MAX_DEPTH);   // spec/limits.json

// --- values -----------------------------------------------------------------

enum class Kind { None, Text, Bin, Bool };

// A shared, immutable record layout.  Records produced by the relational
// builtins can keep their values in the same order as this key list, so a
// compiled projector can address a field by slot rather than repeating a map
// lookup for every joined row.  Ordinary values may continue to use the
// insertion-ordered fallback storage; the shape is an optimization, not a
// second observable representation.
struct RecordShape {
  std::vector<std::string> keys;
  std::unordered_map<std::string, std::size_t> key_map;

  RecordShape() = default;
  explicit RecordShape(std::vector<std::string> names);
};

// The small-mantissa form is 128 bits wide, and sel.cpp relies on it throughout
// (the decimal core's fast paths assume 127 bits of magnitude). There is no
// narrower fallback: a compiler without __int128 cannot build SEL.
#if !defined(__SIZEOF_INT128__)
#error "SEL needs a compiler with __int128 (GCC or Clang); MSVC is not supported"
#endif
using dec_mantissa_t = __int128_t;

// A decimal: (neg ? -1 : 1) * magnitude / 10^scale.
//
// To BUILD one for Value::num(const Dec&), set `neg` and `scale` and give the
// magnitude in ONE of three forms: `small` with `mantissa` (signed, its sign
// agreeing with `neg`), or `digits` (ASCII digits), or `words` (binary,
// little-endian 64-bit). Value::num checks and canonicalises whichever it gets.
//
// To READ one, do not read the fields: a decimal the evaluator computed keeps
// whichever form the arithmetic produced -- `digits` is empty for every small
// value and for a computed big one, whose magnitude is in `words` -- and the
// other forms are caches filled on demand. dec_digits() gives the magnitude's
// digits whatever the form, and Value::as_text() the canonical number.
struct Dec {
  bool neg = false;
  mutable std::string digits;
  std::int32_t scale = 0;
  bool small = false;
  dec_mantissa_t mantissa = 0;
  mutable std::vector<std::uint64_t> words;
};

// The magnitude of `d` as ASCII digits, without sign or decimal point ("150"
// for 1.50), whichever form it is held in. "0" for zero.
std::string dec_digits(const Dec& d);

// One value, used by the interpreter and by host code alike — there is
// deliberately no second representation of state. See spec/SPEC.md §3.
//
// A value may have a scalar, children, both, or neither. TEXT holds validated
// UTF-8 bytes and BIN holds arbitrary bytes: the same C++ type, told apart by
// the kind, which makes as_bytes() on TEXT free.
//
// **`Value` is a handle.** Copying one is cheap and the copies refer to the
// same underlying value, exactly as a `Value` object does in every other host.
// `clone()` is the deep copy. This is not a performance decision: spec/SPEC.md
// §3.4 says evaluating an expression yields a value rather than a snapshot of
// one, so that `A[A["k"] = "k"]` finds the key its own index expression just
// created, and a type with deep-copy assignment cannot express that. The
// language copies only where §3.4's table says (`=`, `,`, LIST, RECORD and the
// aggregates), and the interpreter spells each of those with clone().
//
//     Value a = ctx.get("A");     // a and A are the same value
//     a.set("k", ...);            // visible through ctx
//     Value b = a.clone();        // b is independent
//
// If you are embedding SEL and were relying on `Value b = a;` to isolate `b`,
// that is the one thing this type changed in 0.3.0: write `a.clone()`.
//
// A moved-from Value holds nothing. It may be destroyed, assigned to (after
// which it is an ordinary value again), copied, or asked kind() and the is_*()
// predicates, which answer as for NONE. Any other member on it is undefined
// behaviour, as for a moved-from object generally: the accessors do not test
// for it, because they are what the evaluator calls per field and per row.
class Value {
 public:
  using Entry = std::pair<std::string, Value>;

  Value();
  Value(const Value& other);
  Value(Value&& other) noexcept;
  Value& operator=(const Value& other);
  Value& operator=(Value&& other) noexcept;
  ~Value();

  static Value none();
  // The same value as none(): SEL has one empty value (spec/SPEC.md §3.1), and
  // NULL is its literal spelling. Both names are kept for embedders.
  static Value null();
  static Value text(std::string utf8);        // E_UTF8 if not valid UTF-8
  static Value bin(std::string bytes);
  static Value bin(const std::vector<std::uint8_t>& bytes);
  static Value boolean(bool b);
  // Canonicalises: "007" becomes "7", "-0.00" becomes "0.00". E_NOT_NUM if the
  // text is not a number in the sense of spec/SPEC.md §4.
  static Value num(const std::string& decimal);
  static Value num(const Dec& d);
  static Value integer(long long n);
  // A list keyed "1".."n", as `,` builds.
  static Value list(std::vector<Value> values);
  // Build a regular record with an interned shared shape. Duplicate keys are
  // retained through the ordinary fallback semantics used by set().
  static Value record(std::vector<std::string> keys, std::vector<Value> values);
  static Value shaped(std::shared_ptr<const RecordShape> shape,
                      std::vector<Value> storage);

  Kind kind() const;

  // Kind predicates. The recommended way to branch on kind in every host,
  // because it is the one spelling that reads the same in all four: the kind
  // *values* are an enum here, a string in JS, a class constant in PHP and a
  // keyword in Lisp, so only a predicate can be documented uniformly. These
  // test the value's own kind and do not apply scalar context.
  bool is_none() const;
  bool is_null() const;
  bool is_vacuous() const;
  bool is_text() const;
  bool is_bin() const;
  bool is_bool() const;
  bool is_list() const;

  // --- children. Insertion-ordered; re-assigning a key keeps its position.
  std::size_t size() const;
  bool has(const std::string& key) const;
  const Value* get(const std::string& key) const;
  Value* get(const std::string& key);
  std::vector<std::string> keys() const;
  const std::vector<Entry>& entries() const;
  Value& set(std::string key, Value value);

  const std::shared_ptr<const RecordShape>& shape() const;
  const std::vector<Value>& storage() const;
  const Value* slot(std::size_t index) const;

  // --- scalar context (spec/SPEC.md §3.2). Each throws SelError on a mismatch,
  // reporting `pos` when one is supplied.
  const Value& scalar_source(Pos pos = {}) const;
  const std::string& as_text(Pos pos = {}) const;    // TEXT only
  const std::string& as_bytes(Pos pos = {}) const;   // TEXT or BIN, as bytes
  bool as_bool(Pos pos = {}) const;
  // Non-throwing probe, as ISNUM uses.
  bool looks_numeric() const;

  // The raw scalar without applying scalar context. Empty for NONE.
  const std::string& scalar() const;
  bool boolean_scalar() const;

  // A stable structural hash used by DEDUPE and BUCKET.  It is deliberately
  // computed from kinds, scalars, keys, and child structure rather than from
  // the printable dump, so dump formatting can evolve independently.
  std::uint64_t structural_hash(Pos pos = {}) const;

  // --- structural equality, as EQL uses: same kind, equal scalars with numbers
  // *not* normalised, and children with the same keys in the same order.
  //
  // Throws E_DEPTH past the value-nesting cap, reporting `pos` when one is
  // supplied — the same convention as as_text() and the rest.
  bool eql(const Value& other, Pos pos = {}) const;

  // The canonical dump in conformance/README.md, byte-identical across every
  // implementation. Order is normative, so a dump mismatch caused purely by
  // ordering is a real failure.
  std::string dump() const;

  // A deep copy, sharing nothing with this value. What `=` does in the language
  // (§5.7), and what `,` and the aggregates do with what they collect. Copying
  // a Value does *not* do this — see the note on the class.
  //
  // Throws E_DEPTH past the value-nesting cap of spec/SPEC.md §6.4, as dump()
  // and eql() do: these three walk the tree recursively, and a value nested past
  // the cap would find this host's own stack rather than an error. `pos` is
  // reported when one is supplied, the same convention as as_text().
  Value clone(Pos pos = {}) const;

  // clone(), for a value that is about to be stored `levels` levels below the
  // root of another: the depth cap counts the whole path, so a value that is
  // legal alone can be one level too deep where it is going. E_DEPTH is
  // reported at `pos`, the node that would build the too-deep value.
  Value clone_below(int levels, Pos pos = {}) const;

  // The value as a number, in scalar context: what the operators read, so a
  // TEXT "2.50" is parsed (and the parse kept), and anything that is not a
  // number is E_NOT_NUM at `pos`. The equivalent of the other hosts'
  // asDecimal(); read its digits with dec_digits().
  Dec as_decimal(Pos pos = {}) const;

  // The parsed decimal this value already holds, or null when it holds none
  // (a TEXT nobody has read as a number yet, a BOOL, a list). A read of a cache:
  // it never parses and never throws. Valid while this value is.
  const Dec* dec_val() const;

 private:
  friend struct Internals;

  // The decimal cache. Private: writing one skips every check Value::num(const
  // Dec&) makes, and a TEXT given a decimal it does not spell stops being the
  // text it shows. The interpreter fills it through Internals.
  bool has_dec() const;
  const Dec& dec_ref() const;
  void set_dec(const Dec& d) const;

  // Optional state belongs to the shared implementation, not to a handle:
  // allocating it through any alias must remain visible through every alias.
  struct Collection {
    std::vector<Entry> children;
    std::unordered_map<std::string, std::size_t> index;
    std::shared_ptr<const RecordShape> shape;
    std::vector<Value> storage;
  };

  struct Impl {
    uint32_t ref_count = 1;
    Kind kind = Kind::None;
    mutable std::string scalar;
    mutable bool scalar_computed = false;
    bool boolean = false;
    bool is_list = false;
    bool inline_collection = false;  // Dispatch destruction without a vptr.
    mutable std::unique_ptr<Dec> decimal;
    std::unique_ptr<Collection> collection;
    Impl* next_free = nullptr;

    const Collection& coll() const {
      // Read-only leaf access must not allocate collection containers.
      if (collection) return *collection;
      static const Collection empty;
      return empty;
    }
    Collection& mutable_coll() {
      if (!collection) collection = std::make_unique<Collection>();
      return *collection;
    }

    static void* operator new(std::size_t size);
    static void operator delete(void* ptr, std::size_t size) noexcept;
  };

  explicit Value(Impl* impl) : p_(impl) {}
  static Impl* make_collection_impl();

  static constexpr std::size_t INDEX_THRESHOLD = 4;

  static void destroy(Impl* p);

  // The recursive halves of clone(), eql() and dump(). The public three are one
  // line each; these carry the depth that spec/SPEC.md §6.4 caps, so that a
  // value nested past it is refused rather than answered from a stack that is
  // about to run out. Private because the depth is not the caller's business.
  Value clone_at(int depth, Pos pos) const;
  bool eql_at(const Value& other, int depth, Pos pos) const;
  std::string dump_at(int depth) const;

  Impl* p_ = nullptr;

  // Shaped records and vector-backed lists keep their ordered-entry view lazy.
  // The vector is still available to the public entries() API, but hot field
  // access and collection sizing can stay on the flat storage without copying
  // keys or Value handles first.
  void ensure_children() const;
  void build_index() const;
  std::vector<Entry>::iterator find(const std::string& key);
  std::vector<Entry>::const_iterator find(const std::string& key) const;
};

inline Value::Value(const Value& other) : p_(other.p_) {
  if (p_) ++p_->ref_count;
}

inline Value::Value(Value&& other) noexcept : p_(other.p_) {
  other.p_ = nullptr;
}

inline Value& Value::operator=(const Value& other) {
  if (this != &other && p_ != other.p_) {
    if (p_ && --p_->ref_count == 0) destroy(p_);
    p_ = other.p_;
    if (p_) ++p_->ref_count;
  }
  return *this;
}

inline Value& Value::operator=(Value&& other) noexcept {
  if (this != &other) {
    if (p_ && --p_->ref_count == 0) destroy(p_);
    p_ = other.p_;
    other.p_ = nullptr;
  }
  return *this;
}

inline Value::~Value() {
  if (p_ && --p_->ref_count == 0) destroy(p_);
}

inline bool Value::has_dec() const { return p_ && bool(p_->decimal); }
inline const Dec& Value::dec_ref() const { return *p_->decimal; }
inline void Value::set_dec(const Dec& d) const {
  if (p_) {
    if (p_->decimal) *p_->decimal = d;
    else p_->decimal = std::make_unique<Dec>(d);
  }
}
inline const Dec* Value::dec_val() const {
  return p_ ? p_->decimal.get() : nullptr;
}

// --- programs ---------------------------------------------------------------

struct Node;

class Program {
 public:
  Program(std::string source, std::shared_ptr<const Node> ast);

  // Evaluates against `context`, whose direct children are the variables. The
  // context is mutated in place by any assignment the program performs.
  Value run(Value& context) const;
  // Convenience for a program that needs no inputs.
  Value run() const;

  // Every variable the program reads without having assigned it first, found
  // statically and returned sorted in upper case. Possible only because SEL has
  // no dynamic symbol operator; this is how a frontend knows which inputs should
  // re-trigger which rule.
  std::vector<std::string> dependencies() const;

  const std::string& source() const { return source_; }

  // The parse tree. `Node` is opaque through this header — `sel_ast.hpp` is what
  // defines it, and only code that walks the tree needs that. Public because the
  // SEL→SQL translator is a separate translation unit and the tree is its input;
  // every other host exposes the same thing (`program.ast` in Python and JS,
  // `$program->ast` in PHP, `program-ast` in Lisp). Immutable: it is a pointer
  // to const all the way down, which is the same contract the other four hold
  // by convention.
  std::shared_ptr<const Node> ast() const { return ast_; }

  // The optimised tree run() evaluates, built once from ast() on the first
  // run and shared by every copy of this Program. Public for the same reason
  // ast() is; SQL translation never reads it.
  std::shared_ptr<const Node> physical_ast() const;

 private:
  struct Physical;   // the once-built physical tree; defined in sel.cpp
  std::string source_;
  std::shared_ptr<const Node> ast_;
  // Copies share the cell, as they share ast_. A once_flag is not copyable,
  // and there is nothing to copy: the cell holds one immutable tree.
  std::shared_ptr<Physical> physical_;
};

// Throws SelError on any compile-time failure: syntax, an unknown function, a
// wrong argument count, a non-portable regex literal.
Program compile(const std::string& source);

// compile(source).run(context) in one call, for a rule that runs once. It
// answers exactly what that answers (tests/unit.cpp holds the two side by side);
// a rule with no aggregate or other binding call runs the parsed tree without
// building the optimised one, which a single run cannot repay.
Value evaluate(const std::string& source, Value& context);
Value evaluate(const std::string& source);

// Every function name — the builtins and any registered below — sorted.
std::vector<std::string> function_names();

// --- host functions (spec/SPEC.md §8.1) --------------------------------------

class Args;   // the evaluator's argument vector; internal

// What a host function is handed: its arguments, already evaluated once, left to
// right, with the typed readers every builtin uses. Each reader raises the usual
// SelError (E_NOT_TEXT, E_NOT_BOOL, E_NOT_NUM, E_NOT_INT, E_RANGE, E_NULL) at
// that argument's own position.
class HostArgs {
 public:
  explicit HostArgs(Args& args) : args_(args) {}
  int count() const;
  const Value& val(int i);
  const std::string& text(int i);
  const std::string& bytes(int i);   // TEXT or BIN, as bytes
  bool boolean(int i);
  Dec decimal(int i);                // a number, as Value::as_decimal reads it
  long long integer(int i);
  long long non_neg_int(int i);
  Pos pos_of(int i) const;

 private:
  Args& args_;
};

using HostFunction = std::function<Value(HostArgs&)>;

// Adds an application's own strict function; register it before compiling a
// program that calls it. A host function adds to the language and never changes
// it: std::invalid_argument for a malformed or reserved name, a builtin's name,
// an arity outside 0 <= min <= max, or an empty `fn`. Registering a host
// function's name again replaces it for programs compiled afterwards.
void register_function(const std::string& name, int min, int max, HostFunction fn);

}  // namespace sel

#endif  // SEL_HPP
