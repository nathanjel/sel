// SEL — the parse tree, and the rest of the evaluator's surface that a second
// translation unit needs.
//
// **Internal.** This is not what you include to USE SEL — that is sel.hpp, and
// it stays sufficient on its own. This exists because the SEL→SQL translator
// walks the tree the parser builds, and a tree defined inside one .cpp cannot be
// walked from another. If you are embedding SEL, you want sel.hpp.
//
// It carries two things that are not the tree, for the same reason: the regex
// rewriter and the numeric coercion. The other hosts reach theirs as an
// internal module function or a public Value method, and neither belongs in
// the public header here, where they would give C++ an API surface the other
// hosts do not have.
//
// The three names below that are not the tree itself — Spec, and the forward
// declarations of Args and Context — are here because Node holds a `const Spec*`
// and Spec's `fn` names the other two. A type in an anonymous namespace cannot
// be named across translation units, so the four of them come out together or
// none of them does; sel.cpp says the same thing where they are defined.

#ifndef SEL_AST_HPP
#define SEL_AST_HPP

#include "sel.hpp"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <iterator>
#include <memory>
#include <string_view>
#include <optional>
#include <set>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "sel_builtin_manifest.hpp"
#include "sel_lexicon.hpp"

namespace sel {

class Args;
struct Context;
struct MathPlan;

constexpr int VARIADIC = 1 << 20;

struct Spec {
  std::string name;
  int min = 0;
  int max = 0;
  bool lazy = false;
  bool binds = false;   // introduces an element binder; see dependencies()
  // Optional extra arity rule, checked after min/max. Returns a message when the
  // count is wrong and an empty string when it is fine.
  std::string (*arity_error)(int) = nullptr;
  Value (*fn)(Args&, Context&) = nullptr;
  // Set instead of `fn` for a function registered through register_function
  // (spec §8.1).
  std::shared_ptr<const HostFunction> host = nullptr;
  // Set by define() alone, for a name spec/builtins.json lists: a builtin the
  // library itself defines at startup. Whatever the caller passes is replaced.
  bool shipped = false;
};

// The effects classification every analysis asks (spec §8.1): whether a call
// may keep, read or change values beyond its result, so that no copy may be
// left out around it and nothing may be evaluated out of order across it.
// Only a shipped builtin is assumed not to. Every other function is the
// application's, however it was installed: register_function (`host`), or a
// define() outside the manifest, strict, lazy or binding (`fn`) -- defining a
// function below the public API is not a declaration that it is pure.
// Registration is a separate question (replacement, the SQL host-function
// arity), which `host` alone answers.
inline bool may_have_effects(const Spec* spec) { return spec == nullptr || !spec->shipped; }

enum class NT { Num, Text, Bool, Null, Var, Index, Seq, List, Un, Bin, Assign, Call };

// A yes/no fact about a node's subtree, worked out on first use and kept. Relaxed
// atomic, because a physical tree is shared by every thread running its Program
// (and a tree past the depth cap, which the optimiser returns as it is, by the
// AST it came from), and any thread may be the first to ask -- they all compute
// the same answer. A copy starts unknown: copy_node is the start of a rewrite
// that gives the copy children of its own.
class CachedFact {
 public:
  CachedFact() = default;
  CachedFact(const CachedFact&) noexcept {}
  CachedFact& operator=(const CachedFact&) noexcept {
    v_.store(-1, std::memory_order_relaxed);
    return *this;
  }
  // -1 not yet known, else the fact.
  signed char get() const noexcept { return v_.load(std::memory_order_relaxed); }
  void set(bool fact) const noexcept { v_.store(fact ? 1 : 0, std::memory_order_relaxed); }

 private:
  mutable std::atomic<signed char> v_{-1};
};

struct Node {
  NT t = NT::Num;
  Pos pos;

  std::string s;        // Num/Text: the literal. Var: the name. Un/Bin/Assign: the operator.
  bool b = false;       // Bool: the value.
  bool grouped = false; // came from ( ), so F((1,2)) passes one list not two arguments
  // On a FILTER body: whether the step after the FILTER renumbers without
  // reading `_K`, so nothing observes the keys its result carries and the
  // evaluator's join pre-filter may drop rows below the join (SEL-0050/0052).
  bool keys_unobserved = false;
  // On a FILTER body: the FILTER may keep its elements uncopied (keep_or_alias).
  // Stamped by the physical optimiser when this body and the next pipeline step
  // (a MAP, a FILTER, a sort or a TOP) write nothing, so no kept element can
  // change before the next step has copied what it keeps (the Rust host's
  // borrowed_filter).
  bool borrow_rows = false;
  // On a Var, set by the hybrid planner only: this read is of the catalogue's
  // binding, not of a same-named helper. `ORDERS = ORDERS .> DROP(2); ORDERS .> ...`
  // unwinds to the binding ORDERS with the DROP among the steps; the source read
  // so marked is not a read of the helper ORDERS (which must not be inlined into
  // it a second time), while another read of ORDERS in a later step still is.
  // The evaluator ignores it.
  bool binding_read = false;
  // Bin: the operator resolved once, at parse time, to a BinOp code (0 = not yet
  // resolved; eval_binary then derives it from `s`). Set only where `s` is, and
  // `s` of a Bin node never changes afterwards.
  unsigned char opc = 0;
  // Whether evaluating this subtree writes nothing (writes_nothing_cached): an
  // aggregate asks it of the body it walks, on every call, and the answer never
  // changes -- the subtree is immutable, and so is the Spec each of its calls was
  // compiled against. One byte, in the padding beside `opc`: every
  // evaluation reads nodes, and a bigger Node costs every program.
  CachedFact writes_nothing_fact;

  std::shared_ptr<const Node> l, r;       // Bin: operands. Index: obj, idx. Assign: target, value.
  std::vector<std::shared_ptr<const Node>> items;   // Seq/List/Call arguments
  const Spec* spec = nullptr;             // Call
  std::shared_ptr<const MathPlan> math_plan;
  std::shared_ptr<const Dec> dec;
  std::shared_ptr<const RecordShape> record_shape;

  Node() = default;
  // Kept explicitly: declaring a destructor makes the implicit copy deprecated,
  // and parse_primary copies a node to set `grouped`.
  Node(const Node&) = default;
  Node& operator=(const Node&) = default;
  ~Node();
};

using NodePtr = std::shared_ptr<const Node>;

// A shallow copy of `node` that a rewrite may change: the fields are its own,
// the children are shared (the tree is immutable, so sharing them is safe). The
// one copy the optimiser and the SQL planner both make.
inline std::shared_ptr<Node> copy_node(const NodePtr& node) {
  if (!node) return nullptr;
  return std::make_shared<Node>(*node);
}

// ASCII case, for SEL names, option words and keywords, and for UPPER/LOWER
// (ASCII by decision): A-Z and a-z and nothing else, whatever the locale.
// std::toupper/tolower follow the C locale -- an application that calls
// setlocale() would change which bytes move -- and a Unicode case mapping would
// change a key's length ("ß" is "SS"), so nothing in this host uses either.
// One set, for the evaluator and the SQL layer alike. Bytes of a multi-byte
// UTF-8 sequence are all >= 0x80 and are never touched.
constexpr char ascii_up(char c) { return c >= 'a' && c <= 'z' ? static_cast<char>(c - 'a' + 'A') : c; }
constexpr char ascii_down(char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a') : c; }
inline std::string ascii_upper(std::string_view s) {
  std::string out(s);
  for (char& c : out) c = ascii_up(c);
  return out;
}
inline std::string ascii_lower(std::string_view s) {
  std::string out(s);
  for (char& c : out) c = ascii_down(c);
  return out;
}

// Small shared helpers, one copy each for the evaluator and the SQL layer.

// Whether `x` is among `xs` (any range: a vector, a span, a constexpr array).
template <class Range, class T>
bool contains(const Range& xs, const T& x) {
  return std::find(std::begin(xs), std::end(xs), x) != std::end(xs);
}

// Lower-case hex of every byte, as HEX and a BIN dump spell it.
inline std::string to_hex(std::string_view bytes) {
  static constexpr char DIGITS[] = "0123456789abcdef";
  std::string out;
  out.reserve(bytes.size() * 2);
  for (const unsigned char b : bytes) {
    out += DIGITS[b >> 4];
    out += DIGITS[b & 0x0f];
  }
  return out;
}

// The position a list key names (spec §3.3): "1".."999999999", no sign, no
// leading zero. Empty for any other text, which is a record key instead.
inline std::optional<std::size_t> list_key_number(std::string_view k) {
  if (k.empty() || k.size() > 9 || k[0] < '1' || k[0] > '9') return std::nullopt;
  std::size_t n = 0;
  for (const char c : k) {
    if (c < '0' || c > '9') return std::nullopt;
    n = n * 10 + static_cast<std::size_t>(c - '0');
  }
  return n;
}

// Internal services shared by the evaluator and the SQL translation unit.
// They are declared here rather than in sel.hpp so consumers still only need
// the public Value/Program API.
const Spec* lookup_builtin(const std::string& name);
NodePtr optimize_ast_logical(const NodePtr& ast);
// The planner's spelling: `declared_fields` are the (upper-cased) field names the
// pipeline's source relation declares, which is what lets a field read count as
// unable to raise (see OptFields in sel.cpp).
NodePtr optimize_ast_logical(const NodePtr& ast, const std::set<std::string>& declared_fields);
// The rewrite run() evaluates (Program::physical_ast).
NodePtr optimize_ast_in_memory(const NodePtr& ast);

// The relational pipeline vocabulary, owned by the optimizer and shared with
// the SQL planner so there is one list of pipeline operators in this host and
// one way to take a pipeline apart and put it back together. The planner used
// to carry copies of all three, which is how a host drifts from itself.
//
// unwind_pipeline() peels `X .> A(...) .> B(...)` into the source X and the
// steps [A, B], outermost last; build_pipeline() is its inverse over a possibly
// different source or step list, copying each step so the input tree is never
// touched. `last_pos`, when given, is stamped on the outermost step: the
// optimiser keeps a rewritten pipeline reporting the position written.
//
// Which names are steps, which keep their rows and which sort is the manifest's
// (spec/builtins.json, "Classification"), not a list kept here.
bool is_pipeline_op(std::string_view name);
std::pair<NodePtr, std::vector<NodePtr>> unwind_pipeline(const NodePtr& root);
NodePtr build_pipeline(NodePtr source, const std::vector<NodePtr>& steps,
                       const Pos* last_pos = nullptr);

// --- the lexicon and the builtin classification ------------------------------
//
// What an operator IS -- its family (arithmetic, numeric or text comparison,
// logic, ...), its comparison relation, whether its right side may not run, the
// operator a compound assignment applies -- is spec/lexicon.json's, rendered into
// sel_lexicon.hpp. The lexer, the parser, the evaluator's checks, the optimiser,
// the join pre-filter and the SQL translator all ask these instead of keeping
// string lists of their own, so a new operator is one data edit.

// The infix operator spelled `token` (a symbol or a reserved word), or nullptr.
inline const sel_lexicon::Op* infix_op(std::string_view token) {
  static const std::unordered_map<std::string_view, const sel_lexicon::Op*> table = [] {
    std::unordered_map<std::string_view, const sel_lexicon::Op*> m;
    for (const sel_lexicon::Op& op : sel_lexicon::OPS) {
      if (op.fixity == sel_lexicon::Fixity::Infix) m.emplace(op.token, &op);
    }
    return m;
  }();
  const auto it = table.find(token);
  return it == table.end() ? nullptr : it->second;
}

// Whether `token` is an infix operator of family `f`.
inline bool infix_in(std::string_view token, sel_lexicon::Family f) {
  const sel_lexicon::Op* op = infix_op(token);
  return op != nullptr && op->family == f;
}

// The manifest's classification of a builtin name (spec/builtins.json): the
// pipeline step, the regex call, the SQL argument typing; nullptr when the name
// has none.
template <typename T, std::size_t N>
const T* manifest_entry(const T (&table)[N], std::string_view name) {
  static const std::unordered_map<std::string_view, const T*> index = [&table] {
    std::unordered_map<std::string_view, const T*> m;
    for (const T& e : table) m.emplace(e.name, &e);
    return m;
  }();
  const auto it = index.find(name);
  return it == index.end() ? nullptr : it->second;
}
inline const sel_builtin_manifest::PipelineStep* pipeline_step(std::string_view name) {
  return manifest_entry(sel_builtin_manifest::PIPELINE_STEPS, name);
}
inline const sel_builtin_manifest::RegexCall* regex_call(std::string_view name) {
  return manifest_entry(sel_builtin_manifest::REGEX_CALLS, name);
}
inline const sel_builtin_manifest::SqlArgs* sql_args(std::string_view name) {
  return manifest_entry(sel_builtin_manifest::SQL_ARGS, name);
}
// Whether a builtin's result is a list whatever its arguments.
inline bool yields_list(std::string_view name) {
  return contains(sel_builtin_manifest::YIELDS_LIST, name);
}

// Validates and rewrites a regex in one pass, returning source that means the
// same thing to every engine. Throws SelError for a pattern outside the
// portable subset of spec/SPEC.md §7.8.
//
// Every host runs this, so every host compiles the same pattern -- and the
// SEL→SQL translator is another caller: it puts a pattern through the
// language's own rewriter before emitting it, so a translated `\d` means what
// SEL means by it rather than what the server's engine happens to. MariaDB 11.8
// answers 1 for '٣' REGEXP '^\d$' where SEL answers FALSE. A second copy in the
// SQL layer would be a second thing to keep in step, and it would fail silently
// when the two drifted.
std::string validate_pattern(const std::string& pattern, Pos pos, bool ignore_case = false);

// Read a value in numeric context, and raise what SEL raises when it is not a
// number: E_NOT_NUM for a non-TEXT scalar or a text that is not a numeral,
// E_RANGE for a well-formed numeral too big to hold.
//
// Value::as_decimal(pos) is the public read that returns the number; this is
// the CHECK alone, which is all the one caller outside the evaluator wants. The SEL→SQL translator asks
// whether a constant sitting in a numeric operand position is a number at all,
// and the answer has to be the language's own: a second numeral grammar in the
// SQL layer would be a second thing to keep in step, and it would drift
// silently. Declared here and defined at namespace scope for the same reason
// validate_pattern is.
void require_number(const Value& v, Pos pos);

// Teardown is iterative, and it has to be.
//
// The compiler-generated destructor destroys `l`, which is usually the last
// reference to that child, whose destructor destroys ITS `l` -- so freeing the
// tree recursed once per level, about 64 bytes of stack each. A left-leaning
// chain is as deep as the source is long: `1+1+1...` is one level per operator,
// and parse_term builds it in a loop, so the parser's nesting counter (which
// counts nesting, and a flat chain nests nothing) never sees it. At about
// 131,000 operators -- 262KB of source -- this host segfaulted, and it did so
// while UNWINDING the E_DEPTH the evaluator had correctly just raised, so the
// process died with no output at all rather than printing the error. Chained
// trailing brackets, `A[1][1][1]...`, build the same shape and did the same.
//
// So the chain is walked instead of recursed: take this node's children into a
// worklist, and pop from it, taking a popped node's own children first WHEN we
// are its last owner and it is therefore about to be destroyed. Its destructor
// then finds nothing left to walk and the loop stays flat, whatever the depth.
//
// The `use_count() == 1` test is what makes this safe rather than merely
// shallow: a node with another owner is only released here, never emptied.
// parse_primary's `grouped` copy is exactly that case -- two nodes holding one
// set of children -- and it is why the test cannot be skipped.
inline Node::~Node() {
  std::vector<std::shared_ptr<const Node>> pending;

  // const_cast is safe here and only here: every node is created non-const by
  // make_shared and only ever HELD as const, so the object itself is not const
  // and mutating it is defined. Nothing else in this file may do this.
  const auto steal = [&pending](const Node& node) {
    Node& n = const_cast<Node&>(node);
    if (n.l) pending.push_back(std::move(n.l));
    if (n.r) pending.push_back(std::move(n.r));
    for (auto& item : n.items) {
      if (item) pending.push_back(std::move(item));
    }
    n.items.clear();
  };

  steal(*this);
  while (!pending.empty()) {
    const std::shared_ptr<const Node> held = std::move(pending.back());
    pending.pop_back();
    if (held.use_count() == 1) steal(*held);
    // `held` is released here. Either it was the last reference, and the node is
    // freed with its children already taken, or another owner keeps it alive.
  }
}

// Which argument of a binding call runs where (spec/builtins.md, "Binding
// forms"): per argument Outer (evaluated where the call is), Binder (a bare
// name, never evaluated) or Inner (once per element, with `binds` and every
// binder's name in scope). Empty when the call is not a binding builtin or no
// form takes this count -- the evaluator would refuse it, and a static
// consumer reads every argument where the call stands. The dependency walker
// and the SQL layer's stage 1 both classify through here, so they cannot
// disagree. Inline in the shared header because those two live in different
// translation units.
struct BindingForm {
  std::vector<sel_builtin_manifest::Scope> scopes;
  std::vector<std::string> binds;
};

// A host's own binding function (define with binds outside the manifest,
// examples/fn-complex) has no manifest forms; it gets the two classic shapes.
inline const sel_builtin_manifest::Form GENERIC_BINDING_FORMS[] = {
  {"", 2, {sel_builtin_manifest::Scope::Outer, sel_builtin_manifest::Scope::Inner}, -1, 0, {"_", "_K"}, 2},
  {"", 3, {sel_builtin_manifest::Scope::Outer, sel_builtin_manifest::Scope::Binder, sel_builtin_manifest::Scope::Inner}, 1, 1, {"_K"}, 1},
};

inline std::optional<BindingForm> binding_form(const std::string& name, const std::vector<NodePtr>& args,
                                               const Spec* spec) {
  using namespace sel_builtin_manifest;
  const Form* first = nullptr;
  const Form* last = nullptr;
  for (int i = 0; i < FORM_COUNT; i++) {
    if (std::strcmp(FORMS[i].name, name.c_str()) == 0) {
      if (!first) first = &FORMS[i];
      last = &FORMS[i] + 1;
    } else if (first) {
      break;
    }
  }
  if (!first) {
    if (!spec || !spec->binds) return std::nullopt;
    first = GENERIC_BINDING_FORMS;
    last = GENERIC_BINDING_FORMS + 2;
  }
  for (const Form* f = first; f != last; ++f) {
    if (static_cast<std::size_t>(f->count) != args.size()) continue;
    if (f->when_arg >= 0) {
      const Node& a = *args[static_cast<std::size_t>(f->when_arg)];
      const bool ok = f->when_kind == 1 ? (a.t == NT::Var && !a.grouped) : a.t == NT::Text;
      if (!ok) continue;
    }
    BindingForm out;
    out.scopes.assign(f->scopes, f->scopes + f->count);
    for (int b = 0; b < f->bind_count; b++) out.binds.emplace_back(f->binds[b]);
    for (int i = 0; i < f->count; i++) {
      if (f->scopes[i] == Scope::Binder && args[static_cast<std::size_t>(i)]->t == NT::Var) {
        out.binds.push_back(args[static_cast<std::size_t>(i)]->s);
      }
    }
    return out;
  }
  return std::nullopt;
}

// The argument form of a SORT/TOP-family call (SORT, SORT_DESC, SORT_BY, TOP,
// TOP_DESC, TOP_BY), decided from the manifest's forms as binding_form decides
// them, and decided ONCE for every reader: the evaluator, the optimiser and the
// SQL translator all ask here which argument is the binder, the key and the
// direction. The forms are tried in the manifest's order, which is what makes
// a text literal in the third slot a direction before a bare name in the
// second is a binder (`text-direction-wins-over-bare-name`). The key is the
// form's Inner argument; the direction is the Outer argument after it (a TOP's
// last argument is its count, never a direction). -1 for a slot the form does
// not have: no binder means `_` is bound, no key means the element is its own
// key, no direction means the call's own (SORT_DESC/TOP_DESC) or ASC. Empty
// when no form takes this count. No allocation: the evaluator asks per call.
struct SortForm {
  int binder = -1;
  int key = -1;
  int dir = -1;
};

inline std::optional<SortForm> sort_form(std::string_view name, const std::vector<NodePtr>& args) {
  using namespace sel_builtin_manifest;
  const bool top = name.starts_with("TOP");
  const int last = static_cast<int>(args.size()) - (top ? 1 : 0);
  bool seen = false;
  for (int i = 0; i < FORM_COUNT; i++) {
    const Form& f = FORMS[i];
    if (name != f.name) {
      if (seen) break;
      continue;
    }
    seen = true;
    if (static_cast<std::size_t>(f.count) != args.size()) continue;
    if (f.when_arg >= 0) {
      const Node& a = *args[static_cast<std::size_t>(f.when_arg)];
      const bool ok = f.when_kind == 1 ? (a.t == NT::Var && !a.grouped) : a.t == NT::Text;
      if (!ok) continue;
    }
    SortForm out;
    for (int k = 0; k < f.count; k++) {
      if (f.scopes[k] == Scope::Binder) out.binder = k;
      if (f.scopes[k] == Scope::Inner) out.key = k;
    }
    if (out.key >= 0 && out.key + 1 < last) out.dir = out.key + 1;
    return out;
  }
  return std::nullopt;
}

}  // namespace sel

#endif  // SEL_AST_HPP
