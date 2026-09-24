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
// four do not have.
//
// The three names below that are not the tree itself — Spec, and the forward
// declarations of Args and Context — are here because Node holds a `const Spec*`
// and Spec's `fn` names the other two. A type in an anonymous namespace cannot
// be named across translation units, so the four of them come out together or
// none of them does; sel.cpp says the same thing where they are defined.

#ifndef SEL_AST_HPP
#define SEL_AST_HPP

#include "sel.hpp"

#include <cstring>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "sel_builtin_manifest.hpp"

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
};

enum class NT { Num, Text, Bool, Null, Var, Index, Seq, List, Un, Bin, Assign, Call };

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

// Internal services shared by the evaluator and the SQL translation unit.
// They are declared here rather than in sel.hpp so consumers still only need
// the public Value/Program API.
const Spec* lookup_builtin(const std::string& name);
NodePtr optimize_ast_logical(const NodePtr& ast);
NodePtr optimize_ast_in_memory(const NodePtr& ast);
NodePtr optimize_ast(const NodePtr& ast);

// The relational pipeline vocabulary, owned by the optimizer and shared with
// the SQL planner so there is one list of pipeline operators in this host and
// one way to take a pipeline apart and put it back together. The planner used
// to carry copies of all three, which is how a host drifts from itself.
//
// unwind_pipeline() peels `X .> A(...) .> B(...)` into the source X and the
// steps [A, B], outermost last; build_pipeline() is its inverse over a possibly
// different source or step list, copying each step so the input tree is never
// touched.
bool is_pipeline_op(std::string_view name);
std::pair<NodePtr, std::vector<NodePtr>> unwind_pipeline(const NodePtr& root);
NodePtr build_pipeline(NodePtr source, const std::vector<NodePtr>& steps);

// Validates and rewrites a regex in one pass, returning source that means the
// same thing to every engine. Throws SelError for a pattern outside the
// portable subset of spec/SPEC.md §7.8.
//
// Every host runs this, so every host compiles the same pattern -- and the
// SEL→SQL translator is the fifth caller: it puts a pattern through the
// language's own rewriter before emitting it, so a translated `\d` means what
// SEL means by it rather than what the server's engine happens to. MariaDB 11.8
// answers 1 for '٣' REGEXP '^\d$' where SEL answers FALSE. A second copy in the
// SQL layer would be a second thing to keep in step, and it would fail silently
// when the two drifted.
std::string validate_pattern(const std::string& pattern, Pos pos);

// Read a value in numeric context, and raise what SEL raises when it is not a
// number: E_NOT_NUM for a non-TEXT scalar or a text that is not a numeral,
// E_RANGE for a well-formed numeral too big to hold.
//
// The other four hosts spell this `Value::asDecimal(pos)` and it is public
// there. Here the result cannot be: the decimal type lives in sel.cpp and does
// not leave it. So what crosses is the CHECK rather than the number — which is
// all the one caller outside the evaluator wants. The SEL→SQL translator asks
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

}  // namespace sel

#endif  // SEL_AST_HPP
