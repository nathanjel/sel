// Stage 2: the walk that turns one normalised expression into one Fragment.
// Internal to the SQL layer.
//
// Kind inference is folded into this same walk rather than run as a separate
// pass, because post-order means every operand's kind is already known when its
// parent needs it. Nothing becomes characters until Fragment::as_value() or
// ::as_condition() is called, so a kind failure escapes with no partial output.

#ifndef SEL_SQL_TRANSLATOR_HPP
#define SEL_SQL_TRANSLATOR_HPP

#include <functional>
#include <memory>
#include <optional>
#include <span>
#include <variant>
#include <set>
#include <string>
#include <vector>

#include "sel_sql.hpp"
#include "sel_sql_emit.hpp"
#include "sel_sql_node.hpp"
#include "sel_sql_stage1.hpp"
#include "sel_ast.hpp"

namespace sel::sql {

// What an aggregate binder names for the duration of one element. Three shapes
// matching the three iteration shapes of docs/SQL-TRANSLATION.md §7, plus one
// that exists only to carry a refusal -- so that `_K` inside a relation body
// fails saying rows have no key, rather than falling through to the bindings
// map and being reported as an unbound variable.
class Binder {
 public:
  enum class Shape {
    Node,     // an element of a static list: an AST node, re-entered
    Column,   // one column reference, from a `columns` binding
    Row,      // a row of a relation: fields resolve to that relation's columns
    None,     // in scope, but using it is an error with this reason
  };

  static Binder node(SNodePtr n);
  static Binder column(ColumnSpec c);
  static Binder row(std::shared_ptr<const RelationSpec> r);
  static Binder none(std::string reason);

  Shape shape() const { return shape_; }
  const SNodePtr& as_node() const { return node_; }
  const ColumnSpec& as_column() const { return column_; }
  const RelationSpec& as_row() const { return *relation_; }
  const std::string& reason() const { return reason_; }

 private:
  Binder() = default;
  Shape shape_ = Shape::None;
  SNodePtr node_;
  ColumnSpec column_;
  std::shared_ptr<const RelationSpec> relation_;
  std::string reason_;
};

// The 1-based list position a key names, or nothing when it names none.
//
// `[1-9][0-9]{0,8}`, matched WHOLE. SEL list keys are the canonical decimals
// "1", "2", ... -- so "01" is not a key and neither is "1\n", and the evaluator
// answers E_NO_KEY for both. This layer used to answer *element 1* for both, in
// every host, because `^[0-9]+$` accepts a trailing newline (Python's `$`, and
// PHP's without the D modifier) and int()/(int) accept leading zeros.
//
// Nine digits at most, so the conversion is exact in every host that will ever
// implement this: C++'s stoi throws above int32, PHP saturates above int64, JS
// loses precision above 2^53. No list this layer can build has a billion
// elements, so the cap costs nothing and removes the question -- and it stays
// in C++, where int64 would do, because it is part of the cross-host contract.
std::optional<int> list_key(std::string_view k);

class Translator {
 public:
  Translator(std::string dialect, Bindings bindings, Options options);

  Fragment translate(const NodePtr& ast);

 private:
  // One frame per element of an unroll, or one for a whole relation.
  using Frame = std::vector<std::pair<std::string, Binder>>;

  Fragment node(const SNodePtr& n);
  Fragment dispatch(const SNodePtr& n);

  // The ONLY writer of params_ and param_kinds_.
  Fragment literal(sel::Value v, SqlKind kind);

  Fragment variable(const SNode& n);
  Fragment column_ref(const ColumnSpec& c);
  Fragment index(const SNode& n);
  std::string constant_index(const SNode& idx);
  Fragment unary(const SNode& n);
  Fragment binary(const SNode& n);
  Fragment in_operator(const SNode& n);
  Fragment call(const SNodePtr& n);

  // The map application path every operator and call ends in.
  Fragment apply(Section section, const std::string& key,
                 std::span<const Fragment> args, Pos pos,
                 const std::optional<std::string>& variant = std::nullopt);
  std::string template_of(const Entry& entry, std::span<const Fragment> args,
                          const std::optional<std::string>& variant,
                          const std::string& what, Pos pos);

  // Left-associative fold through the operator's OWN template, so an unrolled
  // aggregate and a hand-written chain produce identical bytes. Never called
  // with an empty vector: each caller supplies its aggregate's identity value
  // instead, and those differ per aggregate.
  Fragment fold_pairwise(const std::string& op, std::span<const Fragment> parts,
                         Pos pos);

  std::optional<std::string> variant_for(const std::string& op,
                                         std::span<const Fragment> args);

  // Identity-returning: the callers assign the result.
  const Fragment& require_bool(const Fragment& f, Pos pos, const std::string& where);
  const Fragment& require_num(const Fragment& f, Pos pos, const std::string& where);
  // Void: the callers do not rebind.
  void require_not_bool(const Fragment& f, Pos pos, const std::string& where);
  void require_not_bool_operand(const Fragment& f, Pos pos, const std::string& where);
  // Takes the NODE and not the Fragment, which is the whole point of it: the
  // guards above ask what the binding *declared*, and this asks what the
  // constant *is*, which is a different and stronger question wherever the
  // answer is written down.
  void require_numeric_constant(const SNode& n);

  Fragment from_binder(const Binder& b, const SNode& n);
  Fragment index_binder(const Binder& b, const std::string& name,
                        const std::string& key, const SNode& n);

  // --- skeletons. A skeleton's placeholders are NAMED, not numbered, and the
  // grammar is deliberately NOT Emit::fill's: no {{ }} escapes, no {*} or {n:},
  // no lexical expansion, no numeric slot grammar. Each difference is a
  // behaviour a shared implementation would change.
  using Slot = std::variant<std::string, Fragment>;
  using SlotMap = std::vector<std::pair<std::string, std::vector<Slot>>>;

  Fragment conditional(const SNode& n);
  SNodePtr rewrite_regex(const SNodePtr& n);
  void require_argument_kind(const std::string& name, const Fragment& f, Pos pos);
  // What an aggregate iterates, once classified.
  struct Filter {
    std::string binder;
    SNodePtr body;
  };
  struct Source {
    enum class Shape { Static, Columns, Relation };
    Shape shape = Shape::Static;
    // Insertion-ordered: a sorted container would reorder clist keys and change
    // the fold order, and so the emitted bytes.
    std::vector<std::pair<std::string, Binder>> elements;
    std::shared_ptr<const RelationSpec> relation;
    // Innermost first, which is how _agg_body's wrapping puts the OUTERMOST
    // filter outermost.
    std::vector<Filter> filters;
    bool scalar_rule = false;
  };

  Source classify(const SNodePtr& src);
  Fragment aggregate(const SNode& n);
  Fragment agg_body(const std::string& name, const SNodePtr& body,
                    const Source& src, const SNode& n);
  Fragment with_element(const Source& src, const std::string& binder_name,
                        const Binder& elem, const std::string& key, const SNode& n,
                        const std::function<Fragment()>& render);
  Fragment with_row(const Source& src, const std::string& binder_name,
                    const std::function<Fragment()>& render);
  Fragment relation_aggregate(const std::string& name, const RelationSpec& rel,
                              const Fragment& body, const SNode& n);
  Fragment count(const SNode& n);
  Fragment has(const SNode& n);
  Fragment join_aggregate(const SNode& n);
  // The programmatic CASE builder, for SUM over an absorbed FILTER. Its kind
  // rule is deliberately NOT unify's: two differing known kinds yield UNKNOWN
  // here where unify refuses, and an UNKNOWN `then` with a NUM `else` yields
  // UNKNOWN rather than NUM. Reusing unify would change the kind the enclosing
  // SUM reports, and so which downstream guards fire.
  Fragment case_when(const Fragment& cond, const Fragment& then,
                     const Fragment& els, Pos pos);

  std::string skeleton(const std::string& name, Pos pos);
  std::vector<Fragment::Part> fill_named(std::string_view tpl,
                                         const SlotMap& slots, Pos pos);
  SlotMap relation_slots(const RelationSpec& rel);
  // Insertion-ordered: the OR-chain element order, and so the emitted bytes,
  // depend on it.
  std::vector<std::pair<std::string, Binder>> value_elements(const Binding& b,
                                                             Pos pos);
  SNodePtr value_node(const Value& v, const Binding& b, Pos pos);

  // Innermost frame wins, which is the precedence sel::Context::lookup gives a
  // binder over a variable. Null when the name is not bound.
  const Binder* binder(const std::string& name) const;

  void add_caveat(std::string name);

  std::string dialect_;
  Emit emit_;
  Bindings bindings_;
  bool strict_ = false;

  // The single absolute parameter vector. Slot ids are CREATION numbers and are
  // never renumbered: the template decides where a slot lands, so the second
  // slot created can be the first emitted. params_[slot - 1] is the value,
  // forever, whatever the template does.
  std::vector<sel::Value> params_;
  // Parallel to params_, and never Unknown or List: those are clamped to Text,
  // because they are not literal FORMS and the renderer has to have one.
  std::vector<SqlKind> param_kinds_;
  // An insertion-ordered set. A sorted container would reorder what the
  // Fragment reports, and the .sqlt cases can see that.
  std::vector<std::string> caveats_;
  std::vector<Frame> frames_;

  std::set<std::string> const_names_;
  sel::Value const_root_ = sel::Value::none();
  int depth_ = 0;
};

}  // namespace sel::sql

#endif  // SEL_SQL_TRANSLATOR_HPP
