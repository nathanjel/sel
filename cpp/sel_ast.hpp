// SEL — the parse tree, and the rest of the evaluator's surface that a second
// translation unit needs.
//
// **Internal.** This is not what you include to USE SEL — that is sel.hpp, and
// it stays sufficient on its own. This exists because the SEL→SQL translator
// walks the tree the parser builds, and a tree defined inside one .cpp cannot be
// walked from another. If you are embedding SEL, you want sel.hpp.
//
// It carries one thing that is not the tree, for the same reason: the regex
// rewriter. The other hosts reach theirs as an internal module function, and
// nothing here belongs in the public header, where it would give C++ an API
// surface the other four do not have.
//
// The three names below that are not the tree itself — Spec, and the forward
// declarations of Args and Context — are here because Node holds a `const Spec*`
// and Spec's `fn` names the other two. A type in an anonymous namespace cannot
// be named across translation units, so the four of them come out together or
// none of them does; sel.cpp says the same thing where they are defined.

#ifndef SEL_AST_HPP
#define SEL_AST_HPP

#include "sel.hpp"

#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace sel {

class Args;
struct Context;

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

enum class NT { Num, Text, Bool, Var, Index, Seq, List, Un, Bin, Assign, Call };

struct Node {
  NT t = NT::Num;
  Pos pos;

  std::string s;        // Num/Text: the literal. Var: the name. Un/Bin/Assign: the operator.
  bool b = false;       // Bool: the value.
  bool grouped = false; // came from ( ), so F((1,2)) passes one list not two arguments

  std::shared_ptr<const Node> l, r;       // Bin: operands. Index: obj, idx. Assign: target, value.
  std::vector<std::shared_ptr<const Node>> items;   // Seq/List/Call arguments
  const Spec* spec = nullptr;             // Call

  Node() = default;
  // Kept explicitly: declaring a destructor makes the implicit copy deprecated,
  // and parse_primary copies a node to set `grouped`.
  Node(const Node&) = default;
  Node& operator=(const Node&) = default;
  ~Node();
};

using NodePtr = std::shared_ptr<const Node>;

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

}  // namespace sel

#endif  // SEL_AST_HPP
