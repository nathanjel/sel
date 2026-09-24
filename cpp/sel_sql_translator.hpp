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

struct RelationalProjection;
struct RelationalGroup;

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
    Key,      // the key of the group being rendered: a group-by entry and the
              // row binder, rendered as the GROUP BY expression itself
    Group,    // a bucket's members, inside the bucket's own body: a list of
              // rows that only COUNT and SUM can read
    Projected,  // the record a bucket's projection built, after it: its
              // fields are the projection's aliases and nothing else
  };

  static Binder node(SNodePtr n);
  static Binder column(ColumnSpec c);
  static Binder row(std::shared_ptr<const RelationSpec> r);
  static Binder none(std::string reason);
  static Binder key(std::string group_binder, SNodePtr group_node,
                    std::shared_ptr<const RelationSpec> row);
  static Binder group(std::shared_ptr<const RelationSpec> r);
  static Binder projected(std::shared_ptr<const RelationSpec> r,
                          std::shared_ptr<const std::vector<RelationalProjection>> projections);

  Shape shape() const { return shape_; }
  const SNodePtr& as_node() const { return node_; }
  const std::string& key_binder() const { return reason_; }
  const ColumnSpec& as_column() const { return column_; }
  const RelationSpec& as_row() const { return *relation_; }
  const std::shared_ptr<const RelationSpec>& as_row_ptr() const { return relation_; }
  const std::string& reason() const { return reason_; }
  const std::vector<RelationalProjection>& projections() const { return *projections_; }
  // Row shape only: the row of a joined statement, bound by with_row -- a
  // field read through it resolves across the sides. The LINK predicate's own
  // binders (with_join_binders) are one side each and never carry it.
  bool joined() const { return joined_; }
  void set_joined(bool joined) { joined_ = joined; }

 private:
  Binder() = default;
  Shape shape_ = Shape::None;
  bool joined_ = false;
  SNodePtr node_;
  ColumnSpec column_;
  std::shared_ptr<const RelationSpec> relation_;
  std::shared_ptr<const std::vector<RelationalProjection>> projections_;
  std::string reason_;
};

struct RelationalProjection {
  std::optional<std::string> alias;
  std::string binder;
  SNodePtr node;
  // Set when the projection IS a group key (a bare bucket's keys, `_K`): it
  // is then rendered as the GROUP BY expression itself.
  std::shared_ptr<const RelationalGroup> group_key;
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

struct RelationalFilter {
  std::string binder;
  SNodePtr node;
  Pos pos;
  // Whether the step was written directly after a bare BUCKET, over its
  // groups (rendered in the bucket's own frame, with_group), rather than
  // after its projection (with_projected).
  bool over_groups = false;
};

struct RelationalOrder {
  std::string binder;
  SNodePtr node;
  std::string dir;
  Pos pos;
  bool over_groups = false;
};

struct RelationalGroup {
  std::optional<std::string> alias;
  std::string binder;
  SNodePtr node;
  Pos pos;
};

struct RelationalJoin {
  std::string type;  // INNER or LEFT
  std::string source_name;
  RelationSpec source_relation;
  bool source_from_raw = false;
  std::string source_table;
  std::optional<std::string> source_alias;
  std::string left_binder = "_1";
  std::string right_binder = "_2";
  SNodePtr on_pred;
  Pos pos;
};

struct RelationalPlan {
  std::string source_name;
  RelationSpec source_relation;
  bool source_from_raw = false;
  std::string source_table;
  std::optional<std::string> source_alias;
  std::shared_ptr<const RelationalPlan> source_subquery;
  std::vector<RelationalJoin> joins;
  std::optional<std::string> correlate;
  bool distinct = false;
  std::optional<std::vector<std::string>> select_cols;
  std::optional<std::vector<RelationalProjection>> projections;
  std::vector<RelationalFilter> filters;
  std::optional<std::vector<RelationalGroup>> group_by;
  // A BUCKET without a projection leaves the plan Open: its SQL rows are the
  // group keys, which is not what SEL's buckets are (a map of member rows), so
  // the next MAP is folded into the bucket as its projection -- the one SQL
  // shape a bucket has. Any other step first turns it Sealed: the members are
  // gone for good, and a MAP after that is refused rather than evaluated over
  // rows SEL would have called groups.
  enum class Bucket { None, Open, Sealed };
  Bucket bucket = Bucket::None;
  // Whether the grouping was written as a bare BUCKET (with or without the
  // MAP that closes it). A bare bucket's key is an index key: SEL refuses a
  // boolean, binary, list or record key, so the translator must too.
  bool bare_key = false;
  std::vector<RelationalFilter> having;
  std::vector<RelationalOrder> order_by;
  std::optional<int64_t> limit;
  std::optional<int64_t> offset;
};

class Translator {
 public:
  Translator(std::string dialect, Bindings bindings, Options options);

  Fragment translate(const NodePtr& ast);
  Fragment translate_statement(const NodePtr& ast);
  // What both entry points do before they differ; see the definition.
  struct Begun { SNodePtr norm; std::optional<RelationalPlan> plan; };
  Begun begin(const NodePtr& ast);

  std::optional<RelationalPlan> analyze_pipeline(const SNodePtr& ast);
  Fragment compile_statement(const RelationalPlan& plan);
  int64_t eval_int_param(const SNodePtr& n, const std::string& op);
  void analyze_sort_step(const SNodePtr& step, RelationalPlan& plan);

 private:
  // One frame per element of an unroll, or one for a whole relation.
  using Frame = std::vector<std::pair<std::string, Binder>>;

  Fragment node(const SNodePtr& n);
  Fragment dispatch(const SNodePtr& n);

  // The ONLY writer of params_ and param_kinds_.
  Fragment literal(sel::Value v, SqlKind kind);

  Fragment variable(const SNode& n);
  Fragment column_ref(const ColumnSpec& c);
  Fragment collated_key(const Fragment& f) const;
  // Refuses or records a caveat, so neither const nor static.
  Fragment order_key(const Fragment& f, Pos pos);
  Fragment identity_group_key(const SNodePtr& n, const Fragment& f) const;
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
  // Transforms rather than checks: the operand comes back wrapped, so every
  // caller has to assign the result.
  Fragment guard_numeric(const Fragment& f, const SNode& n);
  // scale-limit (sql/MAP.md §3): where the dialect's numericCast keeps a fixed
  // number of fractional digits, a value read through it may lose some.
  std::optional<std::int32_t> numeric_cast_scale() const;
  void scale_limited(Pos pos, const std::string& what);
  void coerce_scale_limits(std::span<const SNode* const> operands);

  Fragment from_binder(const Binder& b, const SNode& n);
  Fragment index_binder(const Binder& b, const std::string& name,
                        const std::string& key, const SNode& n);
  Fragment relation_column(const RelationSpec& rel, const ColumnSpec& c);
  std::string relation_table_alias(const RelationSpec& rel);

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
  Fragment group_key(const Source& src, const RelationalGroup& gb, bool projected = false);
  Fragment with_group(const Source& src, const std::string& binder_name,
                      const std::function<Fragment()>& render);
  Fragment with_projected(const Source& src, const std::string& binder_name,
                          const std::function<Fragment()>& render);
  Fragment with_row(const Source& src, const std::string& binder_name,
                    const std::function<Fragment()>& render);
  Fragment relation_aggregate(const std::string& name, const RelationSpec& rel,
                              const Fragment& body, const SNode& n);
  Fragment count(const SNode& n);
  Fragment has(const SNode& n);
  Fragment join_aggregate(const SNode& n);
  Fragment with_join_binders(const RelationalPlan& plan, const RelationalJoin& join,
                             const std::function<Fragment()>& render);
  bool plan_has_rows_above(const RelationalPlan& plan) const;
  bool plan_needs_wrap_before_map(const RelationalPlan& plan) const;
  // The projection of a bucket: the RECORD (or single expression) evaluated
  // once per group, with `binder` bound to the group and _K to its key. Shared
  // by the two spellings SEL has for it -- BUCKET(src, key, proj) and
  // BUCKET(src, key) .> MAP(proj) -- which are one value in the evaluator and
  // have to be one statement here. With no projection at all the keys are
  // projected, which is the most SQL can say about a bucket on its own.
  void bucket_projection(RelationalPlan& plan, const std::string& binder,
                         const SNodePtr& agg_node);
  std::vector<std::string> output_field_names(const RelationalPlan& plan) const;
  SqlKind output_field_type(const RelationalPlan& plan, std::string name) const;
  std::optional<SqlKind> output_canon_kind(const RelationalPlan& plan,
                                           const std::string& name) const;
  RelationalPlan ensure_derived(RelationalPlan plan, bool needed);
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
  const RelationalPlan* statement_plan_ = nullptr;
  bool in_where_ = false;
  bool in_having_ = false;
  int subquery_counter_ = 0;
};

}  // namespace sel::sql

#endif  // SEL_SQL_TRANSLATOR_HPP
