// SEL — Simple Expression Language, C++23 implementation.
//
// Drop `sel.hpp`, `sel_ast.hpp`, `sel.cpp` and `third_party/srell/` into a
// project and compile sel.cpp. There is nothing else to fetch and nothing to
// build first. You include this file; `sel_ast.hpp` is internal and only has to
// sit beside `sel.cpp`.
//
//     #include "sel.hpp"
//
//     sel::Value ctx = sel::Value::none();
//     ctx.set("TOTAL", sel::Value::num("59.97"));
//     sel::Value r = sel::compile("TOTAL > 10.00").run(ctx);   // BOOL TRUE
//
// The language is specified in spec/SPEC.md, which is normative: where this
// implementation and that document disagree, this implementation is wrong.

#ifndef SEL_HPP
#define SEL_HPP

#include <cstddef>
#include <cstdint>
#include <exception>
#include <memory>
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
inline constexpr int MAX_DEPTH = 200;

// --- values -----------------------------------------------------------------

enum class Kind { None, Text, Bin, Bool };

// One value, used by the interpreter and by host code alike — there is
// deliberately no second representation of state. See spec/SPEC.md §3.
//
// A value may have a scalar, children, both, or neither. TEXT holds validated
// UTF-8 bytes and BIN holds arbitrary bytes: the same C++ type, told apart by
// the kind, which makes as_bytes() on TEXT free.
//
// **`Value` is a handle.** Copying one is cheap and the copies refer to the
// same underlying value, exactly as a `Value` object does in the JS, PHP,
// Python and Lisp hosts. `clone()` is the deep copy. This is not a performance
// decision: spec/SPEC.md §3.4 says evaluating an expression yields a value
// rather than a snapshot of one, so that `A[A["k"] = "k"]` finds the key its
// own index expression just created, and a type with deep-copy assignment
// cannot express that. Assignment is the only operation in the language that
// copies (§5.7), and the interpreter spells that with clone() at the five
// places the other four hosts spell it.
//
//     Value a = ctx.get("A");     // a and A are the same value
//     a.set("k", ...);            // visible through ctx
//     Value b = a.clone();        // b is independent
//
// If you are embedding SEL and were relying on `Value b = a;` to isolate `b`,
// that is the one thing this type changed in 0.3.0: write `a.clone()`.
class Value {
 public:
  using Entry = std::pair<std::string, Value>;

  Value();

  static Value none();
  static Value null();
  static Value text(std::string utf8);        // E_UTF8 if not valid UTF-8
  static Value bin(std::string bytes);
  static Value bin(const std::vector<std::uint8_t>& bytes);
  static Value boolean(bool b);
  // Canonicalises: "007" becomes "7", "-0.00" becomes "0.00". E_NOT_NUM if the
  // text is not a number in the sense of spec/SPEC.md §4.
  static Value num(const std::string& decimal);
  static Value integer(long long n);
  // A list keyed "1".."n", as `,` builds.
  static Value list(std::vector<Value> values);

  Kind kind() const { return p_->kind; }

  // Kind predicates. The recommended way to branch on kind in every host,
  // because it is the one spelling that reads the same in all four: the kind
  // *values* are an enum here, a string in JS, a class constant in PHP and a
  // keyword in Lisp, so only a predicate can be documented uniformly. These
  // test the value's own kind and do not apply scalar context.
  bool is_none() const { return p_->kind == Kind::None; }
  bool is_null() const;
  bool is_vacuous() const;
  bool is_text() const { return p_->kind == Kind::Text; }
  bool is_bin() const { return p_->kind == Kind::Bin; }
  bool is_bool() const { return p_->kind == Kind::Bool; }
  bool is_list() const { return p_->is_list; }
  void set_is_list(bool b) { p_->is_list = b; }

  // --- children. Insertion-ordered; re-assigning a key keeps its position.
  std::size_t size() const { return p_->children.size(); }
  bool has(const std::string& key) const;
  const Value* get(const std::string& key) const;
  Value* get(const std::string& key);
  std::vector<std::string> keys() const;
  const std::vector<Entry>& entries() const { return p_->children; }
  Value& set(std::string key, Value value);

  // --- scalar context (spec/SPEC.md §3.2). Each throws SelError on a mismatch,
  // reporting `pos` when one is supplied.
  const Value& scalar_source(Pos pos = {}) const;
  const std::string& as_text(Pos pos = {}) const;    // TEXT only
  const std::string& as_bytes(Pos pos = {}) const;   // TEXT or BIN, as bytes
  bool as_bool(Pos pos = {}) const;
  // Non-throwing probe, as ISNUM uses.
  bool looks_numeric() const;

  // The raw scalar without applying scalar context. Empty for NONE.
  const std::string& scalar() const { return p_->scalar; }
  bool boolean_scalar() const { return p_->boolean; }

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

 private:
  friend struct Internals;

  // Insertion order is normative, so the children are a vector. Lookup by key
  // would then be a linear scan, which makes building an n-element list O(n²) —
  // the JS and PHP hosts get ordered-plus-O(1) for free from a Map and from
  // PHP's ordered hash array, and this is how C++ gets the same.
  //
  // The index is built only once a value has enough children to be worth it:
  // almost every Value in a program has none, so an unordered_map in each would
  // cost far more than the scan it saves. Positions are stable because nothing
  // ever removes a child.
  struct Impl {
    Kind kind = Kind::None;
    std::string scalar;   // TEXT: UTF-8 bytes. BIN: raw bytes. Otherwise empty.
    bool boolean = false;  // BOOL only.
    bool is_list = false;
    std::vector<Entry> children;
    std::unordered_map<std::string, std::size_t> index;

    // Torn down iteratively, for the reason Node is: destroying a child is
    // usually the last reference to it, so freeing a deep tree recursed once per
    // level and found the stack. The language cannot build one that deep any
    // more, but `set()` is public and an embedding application still can, and a
    // destructor is the one operation it cannot be refused by.
    Impl() = default;
    Impl(const Impl&) = default;
    Impl& operator=(const Impl&) = default;
    ~Impl();
  };

  static constexpr std::size_t INDEX_THRESHOLD = 16;

  // The recursive halves of clone(), eql() and dump(). The public three are one
  // line each; these carry the depth that spec/SPEC.md §6.4 caps, so that a
  // value nested past it is refused rather than answered from a stack that is
  // about to run out. Private because the depth is not the caller's business.
  Value clone_at(int depth, Pos pos) const;
  bool eql_at(const Value& other, int depth, Pos pos) const;
  std::string dump_at(int depth) const;

  // Never null. Shared between handles; clone() is what breaks the sharing.
  //
  // There is no cycle collector behind this, so a value that contained itself
  // would leak. It cannot: every path that stores one value inside another
  // clones first, which is the same five places the other hosts clone. `make
  // asan` runs the suite with the leak checker to keep that true.
  std::shared_ptr<Impl> p_;

  void build_index();
  std::vector<Entry>::iterator find(const std::string& key);
  std::vector<Entry>::const_iterator find(const std::string& key) const;
};

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
  // `$program->ast` in PHP, `program-ast` in Lisp).
  std::shared_ptr<const Node> ast() const { return ast_; }

 private:
  std::string source_;
  std::shared_ptr<const Node> ast_;
};

// Throws SelError on any compile-time failure: syntax, an unknown function, a
// wrong argument count, a non-portable regex literal.
Program compile(const std::string& source);

Value evaluate(const std::string& source, Value& context);
Value evaluate(const std::string& source);

// Every built-in name, sorted. The table is fixed at startup — SEL has no DEFUN.
std::vector<std::string> function_names();

}  // namespace sel

#endif  // SEL_HPP
