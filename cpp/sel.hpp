// SEL — Simple Expression Language, C++23 implementation.
//
// Drop `sel.hpp`, `sel.cpp` and `third_party/srell/` into a project and compile
// sel.cpp. There is nothing else to fetch and nothing to build first.
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

// --- values -----------------------------------------------------------------

enum class Kind { None, Text, Bin, Bool };

// One value, used by the interpreter and by host code alike — there is
// deliberately no second representation of state. See spec/SPEC.md §3.
//
// A value may have a scalar, children, both, or neither. TEXT holds validated
// UTF-8 bytes and BIN holds arbitrary bytes: the same C++ type, told apart by
// the kind, which makes as_bytes() on TEXT free.
class Value {
 public:
  using Entry = std::pair<std::string, Value>;

  Value() : kind_(Kind::None) {}

  static Value none();
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

  Kind kind() const { return kind_; }

  // Kind predicates. The recommended way to branch on kind in every host,
  // because it is the one spelling that reads the same in all four: the kind
  // *values* are an enum here, a string in JS, a class constant in PHP and a
  // keyword in Lisp, so only a predicate can be documented uniformly. These
  // test the value's own kind and do not apply scalar context.
  bool is_none() const { return kind_ == Kind::None; }
  bool is_text() const { return kind_ == Kind::Text; }
  bool is_bin() const { return kind_ == Kind::Bin; }
  bool is_bool() const { return kind_ == Kind::Bool; }

  // --- children. Insertion-ordered; re-assigning a key keeps its position.
  std::size_t size() const { return children_.size(); }
  bool has(const std::string& key) const;
  const Value* get(const std::string& key) const;
  Value* get(const std::string& key);
  std::vector<std::string> keys() const;
  const std::vector<Entry>& entries() const { return children_; }
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
  const std::string& scalar() const { return scalar_; }
  bool boolean_scalar() const { return bool_; }

  // --- structural equality, as EQL uses: same kind, equal scalars with numbers
  // *not* normalised, and children with the same keys in the same order.
  bool eql(const Value& other) const;

  // The canonical dump in conformance/README.md, byte-identical across every
  // implementation. Order is normative, so a dump mismatch caused purely by
  // ordering is a real failure.
  std::string dump() const;

 private:
  friend struct Internals;

  Kind kind_ = Kind::None;
  std::string scalar_;   // TEXT: UTF-8 bytes. BIN: raw bytes. Otherwise empty.
  bool bool_ = false;    // BOOL only.

  // Insertion order is normative, so the children are a vector. Lookup by key
  // would then be a linear scan, which makes building an n-element list O(n²) —
  // the JS and PHP hosts get ordered-plus-O(1) for free from a Map and from
  // PHP's ordered hash array, and this is how C++ gets the same.
  //
  // The index is built only once a value has enough children to be worth it:
  // almost every Value in a program has none, and they are copied constantly,
  // so an unordered_map in each would cost far more than the scan it saves.
  // Positions are stable because nothing ever removes a child.
  static constexpr std::size_t INDEX_THRESHOLD = 16;
  std::vector<Entry> children_;
  std::unordered_map<std::string, std::size_t> index_;

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
