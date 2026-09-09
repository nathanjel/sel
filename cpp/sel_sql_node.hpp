// The tree stage 1 produces, and the translator consumes. Internal to the SQL
// layer.
//
// Mostly the language's own nodes, plus ONE the language has never seen: a
// `clist`, the keyed list that indexed assignment builds.
//
// Why a separate type at all. Stage 1 rewrites the tree -- it inlines a helper
// variable as the expression it held -- and two of those rewrites cannot be a
// sel::Node:
//
//   * `R[1] = (1, 2); R[2] = (3, 4); COUNT(R)` substitutes R with a clist,
//     which must NOT be flattened. Assignment stores a list as a child rather
//     than contributing its children -- spec §5.9 applies to `,` and not to `=`
//     -- so that is two pairs, not four scalars. A `list` node would have been
//     renumbered flat by the time an aggregate iterated it, and only this node
//     can say otherwise. It carries the real keys too, so `R["a"] = 1` gives
//     `_K` of "a".
//   * a clist can then appear as a CHILD -- the argument of COUNT above -- and
//     sel::Node::items holds NodePtr, which has nowhere to put one.
//
// The Python host duck-types a small class into the same position, reading `.t`
// and `.pos` off it. C++ has no duck typing, so the wrapper is explicit; the
// shape of the tree is the same one, node for node.

#ifndef SEL_SQL_NODE_HPP
#define SEL_SQL_NODE_HPP

#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "sel.hpp"
#include "sel_ast.hpp"

namespace sel::sql {

class SNode;
using SNodePtr = std::shared_ptr<const SNode>;

class SNode {
 public:
  enum class T { Num, Text, Bool, Null, Var, Index, Seq, List, Un, Bin, Assign, Call, CList };

  // A leaf, unchanged: num, text, bool, or a var stage 1 did not inline.
  static SNodePtr leaf(NodePtr n);
  // A compound whose children stage 1 rewrote. `shape` supplies everything but
  // the children -- the kind, the position, the operator or name, the Call's
  // Spec, and `grouped`.
  //
  // Kids follow the language's own field order: Un {l}, Bin {l, r},
  // Index {obj, idx}, Assign {target, value}, Seq/List/Call {items...}.
  static SNodePtr rewritten(NodePtr shape, std::vector<SNodePtr> kids);
  // The SQL layer's own node, empty and MUTABLE.
  //
  // Returned non-const because stage 1 appends to it after an earlier statement
  // may already have substituted a reference to it, and that aliasing is
  // observable: `R[1] = 1; A = R; R[2] = 2; COUNT(A)` is 2, because A holds the
  // same list R does. Copying on append would answer 1. The Python host shares
  // a mutable object here for the same reason; this is that object.
  //
  // Validates NOTHING: the duplicate-key refusal belongs to the one caller that
  // can say which statement collided, and putting it here would report it
  // against the wrong position.
  //
  // `pos` is the FIRST indexed assignment's, and it survives every later append
  // -- `R[1] = 1; R[2] = 2; R` reports against the first bracket.
  static std::shared_ptr<SNode> new_clist(Pos pos);
  // Non-const on purpose: only the handle stage 1 kept can grow the list.
  void append(std::string key, SNodePtr value);
  // A clist built in one go, for a caller with nothing left to append.
  static SNodePtr clist(Pos pos, std::vector<std::pair<std::string, SNodePtr>> entries);

  T t() const { return t_; }
  Pos pos() const { return pos_; }

  // The language node this stands for, or null for a clist. The only thing that
  // reads Spec, and what to_node() rebuilds from.
  const NodePtr& origin() const { return origin_; }

  // Num/Text: the literal, EXACTLY as the parser stored it. The parser
  // normalises leading zeros and nothing else, so "01" arrives as "1" while
  // "1.0" and "2.50" arrive unchanged -- and `R[1.0]` and `R[1]` are therefore
  // two different keys, which SEL agrees with. Canonicalising here would
  // collapse them, or turn a legal program into a duplicate-key refusal.
  // Var: the name. Un/Bin/Assign: the operator.
  const std::string& s() const;
  bool b() const;                    // Bool
  bool grouped() const;              // came from ( )
  const Spec* spec() const;          // Call, else null

  const std::vector<SNodePtr>& kids() const { return kids_; }
  // clist only, parallel to kids().
  const std::vector<std::string>& keys() const { return keys_; }

  // Convenience, in the language's field names.
  const SNodePtr& l() const { return kids_.at(0); }
  const SNodePtr& r() const { return kids_.at(1); }

  // Rebuild a real sel::Node so the evaluator can be asked about this subtree,
  // which is what constants::validate does through Program::run.
  //
  // Null when any descendant is a clist: the evaluator has never seen one and
  // there is nothing to ask. is_constant() refuses to walk a clist, so
  // validate() never reaches one -- this answers null rather than resting on
  // that staying true.
  NodePtr to_node() const;

 private:
  SNode() = default;

  T t_ = T::Num;
  Pos pos_;
  NodePtr origin_;
  std::vector<SNodePtr> kids_;
  std::vector<std::string> keys_;
};

std::string_view node_kind_name(SNode::T t);

}  // namespace sel::sql

#endif  // SEL_SQL_NODE_HPP
