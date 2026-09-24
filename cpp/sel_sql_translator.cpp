// See sel_sql_translator.hpp.

#include "sel_sql_translator.hpp"

#include <algorithm>
#include <stdexcept>
#include <variant>

namespace sel::sql {
namespace {

// ASCII only, matching PHP's strtoupper. A Unicode upper-caser would fold "ß"
// to "SS" and change a key's length.
std::string ascii_upper(std::string_view s) {
  std::string out(s);
  for (char& c : out) {
    if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
  }
  return out;
}
void frame_set(std::vector<std::pair<std::string, Binder>>& frame,
               const std::string& name, Binder b);
std::string relation_alias(const RelationSpec& rel);

// The literal form of a value the HOST supplied, where no AST node exists to
// say whether the author wrote 5.00 or "5.00".
//
// Never guesses from looks_numeric(): doing so would emit a product code
// "00123" as the number 123, and a `$==` rule over it would then be answered by
// the database instead of by SEL.
SqlKind declared_kind(const Binding& b, const Value& v) {
  if (v.is_bool()) return SqlKind::Bool;
  if (v.is_bin()) return SqlKind::Bin;
  if (v.is_none()) return SqlKind::List;
  const std::optional<SqlKind> t = b.value_type();
  return t && *t == SqlKind::Num ? SqlKind::Num : SqlKind::Text;
}

// Resolve a key against a static-list element. Two different key semantics in
// one function: numeric position for a list, EXACT string for a clist.
SNodePtr child_of(const SNode& n, const std::string& key) {
  if (n.t() == SNode::T::List) {
    const std::optional<int> i = list_key(key);
    if (!i || static_cast<std::size_t>(*i) > n.kids().size()) return nullptr;
    return n.kids()[static_cast<std::size_t>(*i) - 1];
  }
  if (n.t() == SNode::T::CList) {
    // Insertion-ordered, first match. Stage 1 already refused duplicates, so at
    // most one exists.
    for (std::size_t i = 0; i < n.keys().size(); ++i) {
      if (n.keys()[i] == key) return n.kids()[i];
    }
  }
  return nullptr;
}

std::string join_sorted(const std::vector<std::string>& xs) {
  std::vector<std::string> s = xs;
  std::sort(s.begin(), s.end());
  std::string out;
  for (std::size_t i = 0; i < s.size(); ++i) {
    if (i) out += ", ";
    out += s[i];
  }
  return out;
}

}  // namespace

// --- binders -----------------------------------------------------------------

Binder Binder::node(SNodePtr n) {
  Binder b;
  b.shape_ = Shape::Node;
  b.node_ = std::move(n);
  return b;
}
Binder Binder::column(ColumnSpec c) {
  Binder b;
  b.shape_ = Shape::Column;
  b.column_ = std::move(c);
  return b;
}
Binder Binder::row(std::shared_ptr<const RelationSpec> r) {
  Binder b;
  b.shape_ = Shape::Row;
  b.relation_ = std::move(r);
  return b;
}
Binder Binder::none(std::string reason) {
  Binder b;
  b.shape_ = Shape::None;
  b.reason_ = std::move(reason);
  return b;
}
Binder Binder::key(std::string group_binder, SNodePtr group_node,
                   std::shared_ptr<const RelationSpec> row) {
  Binder b;
  b.shape_ = Shape::Key;
  b.reason_ = std::move(group_binder);  // the key's own binder name
  b.node_ = std::move(group_node);
  b.relation_ = std::move(row);
  return b;
}
Binder Binder::group(std::shared_ptr<const RelationSpec> r) {
  Binder b;
  b.shape_ = Shape::Group;
  b.relation_ = std::move(r);
  return b;
}
Binder Binder::projected(std::shared_ptr<const RelationSpec> r,
                         std::shared_ptr<const std::vector<RelationalProjection>> projections) {
  Binder b;
  b.shape_ = Shape::Projected;
  b.relation_ = std::move(r);
  b.projections_ = std::move(projections);
  return b;
}

std::optional<int> list_key(std::string_view k) {
  if (k.empty() || k.size() > 9 || k[0] < '1' || k[0] > '9') return std::nullopt;
  int n = 0;
  for (char c : k) {
    if (c < '0' || c > '9') return std::nullopt;
    n = n * 10 + (c - '0');
  }
  return n;
}

// --- lifecycle ---------------------------------------------------------------

Translator::Translator(std::string dialect, Bindings bindings, Options options)
    : dialect_(std::move(dialect)),
      emit_(dialect_),
      bindings_(std::move(bindings)),
      strict_(options.strict) {}

// What both entry points do before they differ: the dialect and alias checks,
// the per-translation state, the constant scope, stage 1, and the planner's
// look at the result. Returns the normalised tree and the plan, empty where
// the tree is an expression.
Translator::Begun Translator::begin(const NodePtr& ast) {
  // The order is contract: a caller with both a bad dialect and a duplicate
  // alias gets E_SQL_DIALECT, so these two must not be fused into one pass.
  Map::require_target(dialect_);
  bindings_.check_aliases();

  params_.clear();
  param_kinds_.clear();
  caveats_.clear();
  frames_.clear();
  depth_ = 0;
  subquery_counter_ = 0;

  ConstScope scope = const_scope(&bindings_);
  const_names_ = std::move(scope.names);
  const_root_ = std::move(scope.root);

  // Stage 1 and nothing else: the translator renders the tree it is handed;
  // the planner is the one place that optimises first.
  SNodePtr normalised = normalise(ast, const_names_, const_root_);
  auto plan = analyze_pipeline(normalised);
  return {std::move(normalised), std::move(plan)};
}

// The (name, value) pairs of a RECORD(k, v, ...) call, refusing what the
// evaluator would: an odd count at the call, a name that is not a text literal
// at the name. The planner reads RECORD in three places -- a bucket's
// projection, a bucket's key, a MAP's projection -- and each used to walk the
// pairs itself.
static std::vector<std::pair<std::string, SNodePtr>> record_fields(const SNodePtr& node) {
  const auto& args = node->kids();
  if (args.size() % 2 != 0) {
    refuse("E_ARITY", "RECORD takes an even number of arguments", node->pos());
  }
  std::vector<std::pair<std::string, SNodePtr>> fields;
  for (std::size_t i = 0; i < args.size(); i += 2) {
    if (args[i]->t() != SNode::T::Text) {
      refuse("E_BAD_ARG", "RECORD field names must be string literals", args[i]->pos());
    }
    fields.emplace_back(args[i]->s(), args[i + 1]);
  }
  return fields;
}

Fragment Translator::translate(const NodePtr& ast) {
  Begun b = begin(ast);
  if (b.plan) {
    return compile_statement(*b.plan);
  }
  const Fragment f = node(b.norm);

  // Only this final Fragment carries the vectors; every intermediate one built
  // during the walk has none.
  Fragment out(f.parts_, f.kind_, dialect_);
  out.params_ = params_;
  out.param_kinds_ = param_kinds_;
  out.caveats_ = caveats_;
  // Public: it says the value is a canonical number, whose spelling is the
  // contract and not only its value (the SQL oracle compares it as text).
  // Lost here once, in every host at the same time.
  out.canonical_ = f.canonical_;
  return out;
}

Fragment Translator::translate_statement(const NodePtr& ast) {
  Begun b = begin(ast);
  if (!b.plan) {
    refuse("E_SQL_SHAPE", "expected a relational query or pipeline");
  }
  return compile_statement(*b.plan);
}

void Translator::add_caveat(std::string name) {
  if (std::find(caveats_.begin(), caveats_.end(), name) == caveats_.end()) {
    caveats_.push_back(std::move(name));
  }
}

Fragment Translator::node(const SNodePtr& n) {
  // Bounded at the evaluator's own limit. Nothing bounded it, so a flat chain
  // of 201 terms over a column translated -- and the evaluator answers E_DEPTH
  // for that same expression. A rule the database answers and SEL does not is a
  // defect, and it was in every host.
  ++depth_;
  if (depth_ > MAX_DEPTH) {
    --depth_;
    refuse("E_SQL_DEPTH",
           "this expression nests deeper than SEL will evaluate (" +
               std::to_string(MAX_DEPTH) +
               "), so there is nothing to translate; the evaluator answers "
               "E_DEPTH for it",
           n->pos());
  }
  struct Pop {
    int* d;
    ~Pop() { --*d; }
  } pop{&depth_};

  const bool compound = n->t() == SNode::T::Bin || n->t() == SNode::T::Un ||
                        n->t() == SNode::T::Call;
  if (!compound || !is_constant(*n, const_names_)) return dispatch(n);

  // Ask SEL whether the expression is VALID before asking the map whether it is
  // translatable -- and at EVERY compound node, not just the outermost.
  // Checking only the outermost looks like a free optimisation and is not,
  // because SEL is lazy: `FALSE AND (1 / 0 > 0)` is constant and SEL answers
  // FALSE without ever dividing, so validating the AND alone accepts it and
  // `(1 / 0)` goes into the SQL -- while `F AND (1 / 0 > 0)`, with a column in
  // place of the FALSE, was refused. Same division, opposite answer, decided by
  // whether the operand beside it happened to be written down.
  //
  // Validation runs AFTER the dispatch, so every refusal the translator already
  // had keeps its own message: `TRUE + 1` is both an expression SEL rejects and
  // a BOOL where a number is required, and the second is the sentence an author
  // can act on.
  Fragment f = dispatch(n);
  validate(*n, const_root_);
  return f;
}

Fragment Translator::dispatch(const SNodePtr& n) {
  switch (n->t()) {
    case SNode::T::Num: return literal(Value::num(n->s()), SqlKind::Num);
    case SNode::T::Text: return literal(Value::text(n->s()), SqlKind::Text);
    case SNode::T::Bool: return literal(Value::boolean(n->b()), SqlKind::Bool);
    case SNode::T::Var: return variable(*n);
    case SNode::T::Index: return index(*n);
    case SNode::T::Un: return unary(*n);
    case SNode::T::Bin: return binary(*n);
    case SNode::T::List:
    case SNode::T::CList:
      refuse("E_SQL_SHAPE",
             "a list is not a SQL value; a list can only be the thing an "
             "aggregate iterates",
             n->pos());
    case SNode::T::Call: return call(n);
    default: break;
  }
  refuse("E_SQL_SHAPE",
         "cannot translate a " + std::string(node_kind_name(n->t())) + " node",
         n->pos());
}

Fragment Translator::literal(Value v, SqlKind kind) {
  params_.push_back(std::move(v));
  // Never Unknown or List: neither is a literal FORM, and the renderer has to
  // have one. The Fragment's own kind stays UNCLAMPED -- a LIST-kinded literal
  // Fragment exists and is refused later by as_value().
  param_kinds_.push_back(kind == SqlKind::Unknown || kind == SqlKind::List
                             ? SqlKind::Text
                             : kind);
  Fragment f;
  Fragment::Part p;
  p.is_slot = true;
  p.slot = static_cast<int>(params_.size());
  f.parts_.push_back(p);
  f.kind_ = kind;
  f.dialect_ = dialect_;
  return f;
}

const Binder* Translator::binder(const std::string& name) const {
  for (auto frame = frames_.rbegin(); frame != frames_.rend(); ++frame) {
    for (const auto& [k, b] : *frame) {
      if (k == name) return &b;
    }
  }
  return nullptr;
}

// --- values ------------------------------------------------------------------

Fragment Translator::variable(const SNode& n) {
  // A binder wins over the bindings map, without ever consulting it -- the same
  // precedence sel::Context::lookup gives a binder over a variable.
  if (const Binder* bound = binder(n.s())) return from_binder(*bound, n);

  const Binding& b = bindings_.get(n.s(), n.pos());
  switch (b.kind()) {
    case Binding::Kind::Column:
      return column_ref(b.as_column());
    case Binding::Kind::Value: {
      const Value& v = b.as_value();
      // Size before none, so a NONE value WITH children is a list and gets the
      // list message.
      if (v.size() > 0) {
        refuse("E_SQL_SHAPE",
               n.s() + " is bound to a list, and a list is not a SQL value; it "
                       "can only be the thing an aggregate iterates",
               n.pos());
      }
      if (v.is_none()) {
        refuse("E_SQL_SHAPE",
               n.s() + " is bound to an empty value, which is not a SQL value; "
                       "only an aggregate can be given an empty binding",
               n.pos());
      }
      return literal(v, declared_kind(b, v));
    }
    case Binding::Kind::Columns:
    case Binding::Kind::Relation:
      refuse("E_SQL_SHAPE",
             n.s() + " is bound as a " +
                 (b.kind() == Binding::Kind::Columns ? "columns" : "relation") +
                 ", which names a set of values rather than one; use it as the "
                 "first argument of an aggregate, not as a value on its own",
             n.pos());
  }
  refuse("E_SQL_BINDING", "unusable binding for " + n.s(), n.pos());
}

Fragment Translator::column_ref(const ColumnSpec& c) {
  // A raw binding is emitted VERBATIM -- the one place application-written SQL
  // enters, which is why it is a named constructor and not a map key.
  const std::string sql = c.is_raw ? c.raw : emit_.column(c.table, c.column);
  Fragment f;
  Fragment::Part p;
  p.sql = sql;
  f.parts_.push_back(std::move(p));
  f.kind_ = c.type;
  f.dialect_ = dialect_;
  f.exact_ = c.exact;
  f.sargable_ = c.sargable;
  f.guard_ = c.guard;
  if (c.prefilter && *c.prefilter == "separate") {
    f.set_separate_prefilter(true);
  }
  f.set_canonical(c.canonical);
  return f;
}

std::string Translator::constant_index(const SNode& idx) {
  // num and text ONLY. A bool, a variable, a call and an arithmetic expression
  // are all refused -- `A[1+1]` included, because stage 1 does no constant
  // folding, so this is refused even though it is knowable.
  //
  // For a num node this is the parser's already-canonical decimal string, so
  // `A[1]` is "1", `A[1.0]` is "1.0" and `A[007]` is "7". Re-formatting it here
  // would make `A[1.0]` and `A[1]` collide.
  if (idx.t() == SNode::T::Num || idx.t() == SNode::T::Text) return idx.s();
  refuse("E_SQL_SHAPE",
         "an index must be a constant here: the column it names has to be known "
         "before the query runs",
         idx.pos());
}

Fragment Translator::index(const SNode& n) {
  const SNode& obj = *n.l();
  // A joined SEL row exposes both its ordinary fields and the nested table
  // records used by the reference hosts: _["orders"]["id"]. The inner index
  // is a qualifier, not a runtime lookup. Resolve it against the relational
  // plan so the emitted SQL names the owning relation directly; treating it
  // as an index into the left relation would stop hybrid planning at the first
  // multi-table projection.
  // A numeric inner key takes the same path: over a bucket's members or a
  // projected row it is refused at the inner index like the other four hosts
  // do, instead of falling out to the outer one (SEL-0043).
  if (obj.t() == SNode::T::Index && obj.l() && obj.l()->t() == SNode::T::Var &&
      obj.r() && (obj.r()->t() == SNode::T::Text || obj.r()->t() == SNode::T::Num) &&
      statement_plan_) {
    // ... when the inner name is a row. Over a bucket's members or a
    // projected row the inner index is itself the thing to refuse.
    if (const Binder* inner = binder(obj.l()->s())) {
      if (inner->shape() == Binder::Shape::Group || inner->shape() == Binder::Shape::Projected) {
        node(n.l());
      }
    }
    const std::string qualifier = obj.r()->s();
    const auto same_name = [&](std::string_view candidate) {
      return ascii_upper(qualifier) == ascii_upper(candidate);
    };
    const RelationSpec* relation = nullptr;
    // A qualifier names a relation by its binding name, its table, its alias
    // or a binder the join predicate declared -- never by the positional
    // `_`, `_1`, `_2`, which this host alone accepted and the other four plan
    // in memory (SEL-0043).
    if (same_name(statement_plan_->source_name) ||
        same_name(statement_plan_->source_relation.from) ||
        (statement_plan_->source_alias && same_name(*statement_plan_->source_alias))) {
      relation = &statement_plan_->source_relation;
    } else {
      for (std::size_t i = 0; i < statement_plan_->joins.size(); ++i) {
        const RelationalJoin& join = statement_plan_->joins[i];
        if (same_name(join.source_name) || same_name(join.source_relation.from) ||
            (join.source_alias && same_name(*join.source_alias)) ||
            same_name(join.left_binder) || same_name(join.right_binder)) {
          relation = &join.source_relation;
          break;
        }
      }
    }
    if (relation) {
      const std::string key = constant_index(*n.r());
      if (list_key(key)) {
        refuse("E_SQL_SHAPE",
               qualifier + "[" + key + "] asks for a row by position, and a "
                       "joined relation has no positional field",
               n.pos());
      }
      const ColumnSpec* field = relation->field(ascii_upper(key));
      if (!field) {
        refuse("E_SQL_BINDING",
               qualifier + "[\"" + key + "\"] is not a field of that relation",
               n.pos());
      }
      // The qualifier's whole point is to name the relation, so the column
      // is qualified by the alias it renders under, join or no join, as the
      // other four hosts spell it (SEL-0043); a RAW binding stays verbatim.
      if (field->is_raw) return column_ref(*field);
      ColumnSpec qualified = *field;
      qualified.table = relation_table_alias(*relation);
      return column_ref(qualified);
    }
  }
  // A PARENTHESISED variable still has t == Var -- the parser sets only a
  // `grouped` flag -- so `(C)[1]` reaches the same path as `C[1]`.
  if (obj.t() != SNode::T::Var) {
    refuse("E_SQL_SHAPE",
           "only a bound name can be indexed here; SQL has no way to index into "
           "the result of an expression",
           n.pos());
  }
  // On the binder path the key is computed INSIDE the call, so constant_index
  // runs before any binder-shape check.
  if (const Binder* bound = binder(obj.s())) {
    return index_binder(*bound, obj.s(), constant_index(*n.r()), n);
  }
  // Off it, bindings.get runs FIRST: for an unbound name with a non-constant
  // index, E_SQL_UNBOUND wins over E_SQL_SHAPE.
  const Binding& b = bindings_.get(obj.s(), obj.pos());
  const std::string key = constant_index(*n.r());

  switch (b.kind()) {
    case Binding::Kind::Relation:
      refuse("E_SQL_SHAPE",
             obj.s() + " is a relation, which is a list of rows; indexing it "
                       "names no value SEL can produce, so use an aggregate and "
                       "index the row its binder gives you",
             n.pos());
    case Binding::Kind::Columns: {
      const std::optional<int> i = list_key(key);
      const std::size_t count = b.as_columns().size();
      if (!i || static_cast<std::size_t>(*i) > count) {
        refuse("E_SQL_BINDING",
               obj.s() + "[" + key + "] is outside that binding's " +
                   std::to_string(count) + " column(s)",
               n.pos());
      }
      return column_ref(b.as_columns()[static_cast<std::size_t>(*i) - 1]);
    }
    case Binding::Kind::Value: {
      const Value* child = b.as_value().get(key);
      if (!child) {
        refuse("E_SQL_BINDING",
               obj.s() + "[\"" + key + "\"] is not a key of that value", n.pos());
      }
      if (child->size() > 0) {
        refuse("E_SQL_SHAPE",
               obj.s() + "[\"" + key + "\"] is a list, not a SQL value", n.pos());
      }
      return literal(*child, declared_kind(b, *child));
    }
    case Binding::Kind::Column:
      break;
  }
  refuse("E_SQL_SHAPE",
         obj.s() + " is bound as a column, which has no parts to index", n.pos());
}

// A group key, rendered as the GROUP BY expression itself -- wherever it
// appears: the clause, the `_K` projection, a HAVING. A TEXT key is cast and
// collated the way the `$` family compares text, because the evaluator groups
// by the key's exact bytes and a case-insensitive collation would merge groups
// it keeps apart (review 2026-09-15 finding L; MariaDB's default merged 'A'
// and 'a'). The result is marked exact so a comparison over it does not wrap
// it a second time -- MySQL's only_full_group_by accepts a projected or
// compared key only as the identical expression.
Fragment Translator::group_key(const Source& src, const RelationalGroup& gb, bool projected) {
  const Fragment key = with_row(src, gb.binder, [&]() { return node(gb.node); });
  const Fragment identity = identity_group_key(gb.node, key);
  if (projected && key.kind() == SqlKind::Num) {
    std::vector<Fragment::Part> parts{{false, "MIN(", 0}};
    parts.insert(parts.end(), key.parts().begin(), key.parts().end());
    parts.push_back({false, ")", 0});
    return Fragment(parts, SqlKind::Num, dialect_, key.params(), key.param_kinds(), key.caveats());
  }
  return identity;
}

Fragment Translator::identity_group_key(const SNodePtr& n, const Fragment& f) const {
  // One spelling per value, so the value's equality is the spelling's: grouped
  // and compared as the number it is, which keeps it a number for whatever
  // sorts it afterwards.
  if (f.canonical() && f.kind() == SqlKind::Num) return f;
  if (f.kind() == SqlKind::Unknown) refuse("E_SQL_SHAPE", "group keys require proven scalar identity", n->pos());
  if (f.kind() == SqlKind::Num) {
    if (n->t() != SNode::T::Var && n->t() != SNode::T::Index && n->t() != SNode::T::Num) {
      refuse("E_SQL_SHAPE", "computed numeric group keys do not preserve SEL identity", n->pos());
    }
    const Fragment numeric(f.parts(), SqlKind::Num, dialect_, f.params(), f.param_kinds(), f.caveats());
    const Fragment w = emit_.text_operand(numeric);
    return Fragment(w.parts(), SqlKind::Text, dialect_, w.params(), w.param_kinds(), w.caveats(), true, false, false);
  }
  return collated_key(f);
}

// A sort key, as SEL's sort compares it. SEL sorts numbers as numbers and other
// text by its bytes -- and number-shaped TEXT as a number, which SQL's ORDER BY
// cannot: it sorts a text key by its bytes throughout, so "10" comes before "9"
// (SEL-0060). A NUM key sorts as SEL sorts it. A canonical number the dialect
// can only carry as text is always a number to SEL, so sorting it in SQL is
// simply wrong: refused, and the planner sorts in memory (SEL-0058). Any other
// TEXT or UNKNOWN key is sorted anyway, declared text-order, and refused under
// strict.
Fragment Translator::order_key(const Fragment& f, Pos pos) {
  if (f.kind() == SqlKind::Num) return f;
  if (f.canonical()) {
    refuse("E_SQL_UNSUPPORTED",
           "CANON is text on " + dialect_ + ", which SQL sorts by its bytes, and SEL "
           "sorts it as the number it is; sort it in memory",
           pos);
  }
  if (f.kind() == SqlKind::Text || f.kind() == SqlKind::Unknown) {
    if (strict_) {
      refuse("E_SQL_UNSUPPORTED",
             "a text key sorts by its bytes in SQL, where SEL sorts number-shaped "
             "text as numbers (text-order); strict mode refuses that",
             pos);
    }
    add_caveat("text-order");
  }
  return collated_key(f);
}

Fragment Translator::collated_key(const Fragment& f) const {
  if (f.kind() != SqlKind::Text || f.exact()) return f;
  const Fragment wrapped = emit_.text_operand(f);
  Fragment out(wrapped.parts(), SqlKind::Text, dialect_, wrapped.params(),
               wrapped.param_kinds(), wrapped.caveats());
  out.set_exact(true);
  return out;
}

Fragment Translator::from_binder(const Binder& b, const SNode& n) {
  switch (b.shape()) {
    case Binder::Shape::Node:
      // Re-enters the whole walk on the element, so the depth counter and the
      // constant validation apply to the inlined element too.
      return node(b.as_node());
    case Binder::Shape::Key: {
      // Inside a row already: bind the key's own binder to that row and render
      // the key there, then collate it exactly as the GROUP BY does.
      Frame frame;
      frame_set(frame, b.key_binder(), Binder::row(b.as_row_ptr()));
      frames_.push_back(std::move(frame));
      Fragment key;
      try {
        key = node(b.as_node());
      } catch (...) {
        frames_.pop_back();
        throw;
      }
      frames_.pop_back();
      const bool wrapped = key.kind() == SqlKind::Num || (key.kind() == SqlKind::Text && !key.exact());
      Fragment collated = identity_group_key(b.as_node(), key);
      if (key.kind() == SqlKind::Num) {
        std::vector<Fragment::Part> parts{{false, "MIN(", 0}};
        parts.insert(parts.end(), key.parts().begin(), key.parts().end());
        parts.push_back({false, ")", 0});
        Fragment out(parts, SqlKind::Num, dialect_, key.params(), key.param_kinds(), key.caveats());
        out.set_canonical(key.canonical());
        return out;
      }
      // In a HAVING, MariaDB and MySQL resolve a column only against the
      // GROUP BY columns and the select list, not against an equal
      // expression: `HAVING CAST(cat ...) COLLATE ...` is "unknown column
      // cat" once the grouping is the collated expression. The key is
      // constant within its group, so MIN of it IS the key, and an aggregate
      // is what every server lets a HAVING name.
      // Nested _K projections need the same aggregate under ONLY_FULL_GROUP_BY.
      if (wrapped) {
        std::vector<Fragment::Part> parts;
        parts.push_back(Fragment::Part{false, "MIN(", 0});
        for (const auto& p : collated.parts()) parts.push_back(p);
        parts.push_back(Fragment::Part{false, ")", 0});
        Fragment out(std::move(parts), SqlKind::Text, dialect_, collated.params(),
                     collated.param_kinds(), collated.caveats());
        out.set_exact(true);
        out.set_canonical(key.canonical());
        return out;
      }
      return collated;
    }
    case Binder::Shape::Column:
      return column_ref(b.as_column());
    case Binder::Shape::Group:
      refuse("E_SQL_SHAPE",
             n.s() + " is the list of a bucket's members, which is not a value SQL "
                     "has; count it (COUNT), sum over it (SUM), or name the group key (_K)",
             n.pos());
    case Binder::Shape::Projected:
      refuse("E_SQL_SHAPE",
             n.s() + " is the record the projection built, which is a map in SEL and "
                     "not one value; name the field you mean",
             n.pos());
    case Binder::Shape::Row: {
      const RelationSpec& rel = b.as_row();
      // The multi-field check comes FIRST, so a two-field relation that
      // declares a scalar still refuses as multi-field.
      if (rel.fields.size() > 1) {
        refuse("E_SQL_SHAPE",
               n.s() + " is a row of a relation with " +
                   std::to_string(rel.fields.size()) +
                   " fields, which is a map in SEL and not one value; name the "
                   "field you mean",
               n.pos());
      }
      const ColumnSpec* field =
          rel.scalar ? rel.field(ascii_upper(*rel.scalar)) : nullptr;
      if (!field) {
        refuse("E_SQL_SHAPE",
               n.s() + " names a row, and the relation does not say which of its "
                       "fields a bare reference means; give the binding a "
                       "\"scalar\", or index the field you want",
               n.pos());
      }
      return relation_column(rel, *field);
    }
    case Binder::Shape::None:
      // How `_K` inside a relation body reports "a row of a relation has no
      // key" rather than being reported as an unbound variable.
      refuse("E_SQL_SHAPE", b.reason(), n.pos());
  }
  refuse("E_SQL_SHAPE", "unusable binder", n.pos());
}

Fragment Translator::index_binder(const Binder& b, const std::string& name,
                                  const std::string& key, const SNode& n) {
  if (b.shape() == Binder::Shape::Group) {
    // The group is the list of its members: indexing it by a field name is
    // E_NO_KEY in SEL, and by a position asks for a member SQL cannot single
    // out. Either way the field is read inside an aggregate over the
    // members, SUM(g, _["amount"]), and nowhere else.
    refuse("E_SQL_SHAPE",
           name + "[\"" + key + "\"] indexes the list of a bucket's members, which "
                  "SEL refuses (E_NO_KEY); read a member's field inside an "
                  "aggregate over the group, SUM(" + name + ", _[\"" + key + "\"])",
           n.pos());
  }
  if (b.shape() == Binder::Shape::Projected) {
    // After the projection a row is the record it built, and has the
    // projection's fields under their aliases and nothing else -- not the
    // source's columns, which SEL no longer has (E_NO_KEY).
    const RelationalProjection* proj = nullptr;
    for (const auto& candidate : b.projections()) {
      if (candidate.alias && *candidate.alias == key) { proj = &candidate; break; }
    }
    if (!proj) {
      for (const auto& candidate : b.projections()) {
        if (candidate.alias && ascii_upper(*candidate.alias) == ascii_upper(key)) { proj = &candidate; break; }
      }
    }
    if (!proj) {
      std::vector<std::string> known;
      for (const auto& candidate : b.projections()) {
        if (candidate.alias) known.push_back(*candidate.alias);
      }
      std::sort(known.begin(), known.end());
      std::string tail;
      for (std::size_t i = 0; i < known.size(); ++i) tail += (i ? ", " : "; it has ") + known[i];
      refuse("E_SQL_SHAPE", name + "[\"" + key + "\"] is not a field of the projection" + tail, n.pos());
    }
    Source src;
    src.shape = Source::Shape::Relation;
    src.relation = b.as_row_ptr();
    if (proj->group_key) {
      return from_binder(Binder::key(proj->group_key->binder, proj->group_key->node, b.as_row_ptr()), n);
    }
    return with_group(src, proj->binder, [&]() { return node(proj->node); });
  }
  if (b.shape() == Binder::Shape::Row) {
    // The positional check comes before the field lookup, so a relation field
    // literally named "1" is unreachable through I[1] and I["1"] alike.
    if (list_key(key)) {
      refuse("E_SQL_SHAPE",
             name + "[" + key + "] asks for a row by position, and a relation "
                                "has no first row without an ORDER BY that "
                                "nothing here can supply",
             n.pos());
    }
    const RelationSpec& rel = b.as_row();
    std::string field = ascii_upper(key);
    // A joined row has a field only where exactly one side has it (spec §7.4
    // "Joined rows"): a name both sides carry is E_NO_KEY in SEL, so it is
    // refused here BEFORE the left relation's own field is consulted -- the
    // left side's column was returned first, and `_["name"]` after a join
    // translated where run() raises (review 2026-09-15 finding W2, d1). As in
    // the other hosts, the lookup across the sides is for the row binder
    // with_row marks as joined -- whatever it is called -- and never for the
    // LINK predicate's own binders (with_join_binders), which are one side each.
    if (b.joined() && statement_plan_ && !statement_plan_->joins.empty()) {
      std::vector<std::pair<const RelationSpec*, const ColumnSpec*>> matches;
      if (const ColumnSpec* c = statement_plan_->source_relation.field(field)) {
        matches.emplace_back(&statement_plan_->source_relation, c);
      }
      for (const RelationalJoin& join : statement_plan_->joins) {
        if (const ColumnSpec* c = join.source_relation.field(field)) {
          matches.emplace_back(&join.source_relation, c);
        }
      }
      if (matches.size() > 1) {
        refuse("E_SQL_SHAPE", "field \"" + key + "\" is ambiguous across joined relations",
               n.pos());
      }
      if (matches.size() == 1) return relation_column(*matches[0].first, *matches[0].second);
    }
    // A derived table's fields carry the alias the projection gave them
    // (ensure_derived), so a read through any spelling of the name renders
    // that column, as in the other hosts -- not the spelling itself.
    if (const ColumnSpec* f = rel.field(field)) return relation_column(rel, *f);
    std::vector<std::string> known;
    for (const auto& [k, spec] : rel.fields) {
      (void)spec;
      known.push_back(k);
    }
    refuse("E_SQL_BINDING",
           name + "[\"" + key + "\"] is not a field of that relation" +
               (known.empty() ? "; it declares none"
                              : "; it has " + join_sorted(known)),
           n.pos());
  }
  if (b.shape() == Binder::Shape::Node) {
    SNodePtr elem = child_of(*b.as_node(), key);
    if (!elem) {
      refuse("E_SQL_BINDING",
             name + "[\"" + key + "\"] is not a key of that element", n.pos());
    }
    return node(elem);
  }
  // COLUMN, and also NONE -- which therefore does NOT report its reason when
  // indexed. A quirk, preserved: `_K["a"]` inside a relation body says this
  // rather than "a row of a relation has no key".
  refuse("E_SQL_SHAPE",
         name + " names a single column, which has no parts to index", n.pos());
}

std::string Translator::relation_table_alias(const RelationSpec& rel) {
  if (statement_plan_) {
    const auto same = [&rel](const RelationSpec& other) {
      return rel.from == other.from && rel.alias == other.alias &&
             rel.from_is_raw == other.from_is_raw;
    };
    if (same(statement_plan_->source_relation)) {
      return statement_plan_->source_alias.value_or(relation_alias(rel));
    }
    for (const RelationalJoin& join : statement_plan_->joins) {
      if (same(join.source_relation)) {
        return join.source_alias.value_or(relation_alias(rel));
      }
    }
  }
  return relation_alias(rel);
}

Fragment Translator::relation_column(const RelationSpec& rel, const ColumnSpec& c) {
  // Preserve the established single-table SQL spelling.  Qualification is
  // required once a statement has multiple sources (or a derived source), but
  // adding it to ordinary statements changes the public SQL byte contract and
  // provides no semantic value.
  if (c.is_raw || !statement_plan_ ||
      (statement_plan_->joins.empty() && !statement_plan_->source_subquery)) {
    return column_ref(c);
  }
  ColumnSpec qualified = c;
  qualified.table = relation_table_alias(rel);
  return column_ref(qualified);
}

}  // namespace sel::sql

// --- kind guards -------------------------------------------------------------

namespace sel::sql {
namespace {

// The RUNTIME kind classes EQL and IN compare, which are not the static kinds.
// A SEL number IS a text value (spec §4), so `1 EQL "1"` is TRUE and NUM and
// TEXT are one class. BOOL and BIN are each their own: `0 EQL FALSE` is FALSE
// because a number is not a boolean, and `TO_UTF8("a") EQL "a"` is FALSE
// because bytes are not text.
//
// UNKNOWN and LIST have no class, and that ABSENCE is the mechanism: either one
// passes. An enum-keyed table must return an optional and treat absent as pass,
// never as a class two absents could compare equal on.
enum class EqlClass { Text, Bool, Bin };

std::optional<EqlClass> eql_class(SqlKind k) {
  switch (k) {
    case SqlKind::Num:
    case SqlKind::Text: return EqlClass::Text;
    case SqlKind::Bool: return EqlClass::Bool;
    case SqlKind::Bin: return EqlClass::Bin;
    default: return std::nullopt;   // Unknown, List
  }
}

bool contains(std::span<const std::string_view> xs, std::string_view k) {
  return std::find(xs.begin(), xs.end(), k) != xs.end();
}

constexpr std::string_view NUMERIC_OPS[] = {"==", "!=", "<", "<=", ">", ">="};
constexpr std::string_view TEXTUAL_OPS[] = {"$==", "$!=", "$<", "$<=",
                                            "$>",  "$>=", "EQL"};
constexpr std::string_view BYTE_COMPARISONS[] = {"$==", "$!=", "$<",  "$<=",
                                                 "$>",  "$>=", "EQL", "IN"};
constexpr std::string_view ARITHMETIC_OPS[] = {"+", "-", "*", "/", "%"};

// Module-level in the Python host too: it needs no translator state, and it is
// void -- it either returns or throws, and never transforms its operands, which
// is why the caller applies text_operand afterwards.
void require_comparable_kinds(const Fragment& l, const Fragment& r,
                              const std::string& op, Pos pos) {
  const std::optional<EqlClass> cl = eql_class(l.kind());
  const std::optional<EqlClass> cr = eql_class(r.kind());
  if (!cl || !cr || *cl == *cr) return;
  // `other` is computed as if l were always the BOOL side, so a TEXT-vs-BIN
  // mismatch says "compares a BOOL with a TEXT". A defect in the message, kept:
  // codes are contract and messages are not, and rewording it here would be the
  // only host that did.
  const SqlKind other = l.kind() == SqlKind::Bool ? r.kind() : l.kind();
  refuse("E_SQL_SHAPE",
         op + " compares a BOOL with a " + std::string(kind_name(other)) +
             ", which SEL answers FALSE for every value because the kinds "
             "differ. SQL has no way to say that: both sides cast to the same "
             "characters",
         pos);
}

// The one kind a set of branches all produce.
//
// UNKNOWN unifies with anything: that is what it is for, and a column whose
// type the binding did not declare is the ordinary case. Two KNOWN kinds that
// differ are another matter, and used to yield UNKNOWN as well. They cannot:
// SQL types the whole CASE, and there is no rendering of the result that agrees
// with SEL's. `IF(TRUE, TRUE, "A-1")` is the one the fuzz lane found -- SEL
// answers the BOOL TRUE, whose text is "TRUE", and the CASE answers 1.
SqlKind unify(std::span<const Fragment> fs, Pos pos) {
  std::optional<SqlKind> kind;
  for (const Fragment& f : fs) {
    if (f.kind() == SqlKind::Unknown) continue;
    if (!kind) { kind = f.kind(); continue; }
    if (*kind != f.kind()) {
      refuse("E_SQL_SHAPE",
             "these branches produce different kinds — " +
                 std::string(kind_name(*kind)) + " and " +
                 std::string(kind_name(f.kind())) +
                 " — and SQL gives the whole expression one type, which cannot "
                 "match SEL's for both",
             pos);
    }
  }
  return kind.value_or(SqlKind::Unknown);
}

SqlKind ret_kind(const Entry& entry, std::span<const Fragment> args, Pos pos) {
  const std::string_view ret = entry.ret;
  if (ret == "@concat") {
    for (const Fragment& a : args) {
      if (a.kind() == SqlKind::Bin) return SqlKind::Bin;
    }
    return SqlKind::Text;
  }
  if (ret.starts_with("@unify:")) {
    std::vector<Fragment> pick;
    std::string_view rest = ret.substr(7);
    while (!rest.empty()) {
      const std::size_t comma = rest.find(',');
      const std::string_view piece =
          comma == std::string_view::npos ? rest : rest.substr(0, comma);
      int i = 0;
      for (char c : piece) i = i * 10 + (c - '0');
      if (static_cast<std::size_t>(i) < args.size()) pick.push_back(args[static_cast<std::size_t>(i)]);
      if (comma == std::string_view::npos) break;
      rest = rest.substr(comma + 1);
    }
    return unify(pick, pos);
  }
  return kind_from_name(ret).value_or(SqlKind::Unknown);
}

}  // namespace

const Fragment& Translator::require_bool(const Fragment& f, Pos pos,
                                         const std::string& where) {
  // UNKNOWN used to pass, on the reasoning that an undeclared column may well
  // be boolean and the database is the one that knows. Measured, the database
  // does not know: MariaDB answers `1 AND TRUE` as TRUE, so an undeclared column
  // holding 1 matched a row SEL refuses with E_NOT_BOOL, and PostgreSQL raises
  // 42804 instead. No dialect can ask "is this a boolean" -- in the MySQL family
  // a boolean IS a TINYINT, so testing IN (0, 1) would also admit a NUM column
  // SEL refuses -- so there is nothing to wrap it in, and refusing is the only
  // answer that keeps the warrant.
  if (f.kind() == SqlKind::Bool) return f;
  refuse("E_SQL_SHAPE",
         where + " needs a BOOL here and this is " +
             std::string(kind_name(f.kind())) +
             "; SEL has no truthiness, so neither does its translation",
         pos);
}

const Fragment& Translator::require_num(const Fragment& f, Pos pos,
                                        const std::string& where) {
  // Stricter than require_not_bool: TEXT refuses here and passes there. A SUM
  // body is NUM or UNKNOWN, and nothing else; an arithmetic operand need only
  // not be BOOL or BIN. UNKNOWN passes for require_not_bool's reason and not
  // require_bool's: an undeclared column is not one of the kinds that cannot be
  // summed. It then goes on UNWRAPPED -- guard_numeric reaches the operands of
  // arithmetic and numeric comparison, never an aggregate body.
  if (f.kind() == SqlKind::Num || f.kind() == SqlKind::Unknown) return f;
  refuse("E_SQL_SHAPE",
         where + " adds its body up, so it needs a number here and this is " +
             std::string(kind_name(f.kind())),
         pos);
}

void Translator::require_not_bool(const Fragment& f, Pos pos,
                                  const std::string& where) {
  // Misnamed in every host, and kept: it refuses BOOL *and* BIN. NUM, TEXT,
  // UNKNOWN and LIST pass -- UNKNOWN because this guard is about the kinds that
  // make a number *impossible*, and an undeclared column is not one of them.
  // What happens to an UNKNOWN operand afterwards is guard_numeric's business,
  // not this one's.
  if (f.kind() != SqlKind::Bool && f.kind() != SqlKind::Bin) return;
  refuse("E_SQL_SHAPE",
         where + " reads its operands as numbers, and " +
             (f.kind() == SqlKind::Bool ? "a BOOL" : "a BIN") +
             " is not one; SEL answers E_NOT_NUM here rather than coercing it",
         pos);
}

void Translator::require_not_bool_operand(const Fragment& f, Pos pos,
                                          const std::string& where) {
  // Distinct from require_not_bool by exactly one kind: BIN passes here.
  // Deliberately NOT applied to EQL or IN, which are structural -- `TRUE EQL
  // TRUE` must stay TRUE.
  if (f.kind() != SqlKind::Bool) return;
  refuse("E_SQL_SHAPE",
         where + " reads its operands as text or bytes, and a BOOL is neither; "
                 "SEL answers E_NOT_TEXT here rather than spelling it 1 or true",
         pos);
}

// An operand in a numeric position whose value is knowable here.
//
// See require_numeric in sel_sql_stage1.hpp for why refusing loses nothing, and
// for why it is never keyed on a declared kind.
void Translator::require_numeric_constant(const SNode& n) {
  if (is_constant(n, const_names_)) require_numeric(n, const_root_);
}

// Wrap an operand the numeric context cannot be sure of.
//
// A constant is skipped, because require_numeric_constant has just proved it IS
// a number -- guarding it would ask the server a question already answered here,
// and would cost a bound value a second parameter for the repeated slot. What is
// left is what could not be settled at translation time: columns, raw, relation
// fields.
Fragment Translator::guard_numeric(const Fragment& f, const SNode& n) {
  if (is_constant(n, const_names_)) return f;
  // numeric_operand hands an unguarded NUM back untouched and wraps anything
  // else; the other hosts test the result's identity, which a value cannot.
  const bool wraps = f.kind() != SqlKind::Num || f.guard();
  Fragment guarded = emit_.numeric_operand(f, n.pos());
  if (wraps) {
    // The guard reads the text as the dialect's numericCast type, and where
    // that type fixes a scale the data's digits past it are gone before
    // anything else sees them (SEL-0059).
    scale_limited(n.pos(), "this operand is read as a number");
  }
  return guarded;
}

// How many fractional digits the dialect's numericCast and numericGuard keep,
// or nullopt where they keep every one (sql/MAP.md §3). A cap too long for an
// int saturates: no constant's scale reaches it either way.
std::optional<std::int32_t> Translator::numeric_cast_scale() const {
  const Lexical* cap = emit_.lex("numericCastScale");
  if (!cap || cap->kind != LexKind::Text || cap->text.empty()) return std::nullopt;
  std::int64_t out = 0;
  for (char c : cap->text) {
    if (c < '0' || c > '9') return std::nullopt;
    out = std::min<std::int64_t>(out * 10 + (c - '0'), INT32_MAX);
  }
  return static_cast<std::int32_t>(out);
}

void Translator::scale_limited(Pos pos, const std::string& what) {
  const std::optional<std::int32_t> cap = numeric_cast_scale();
  if (!cap) return;
  if (strict_) {
    refuse("E_SQL_UNSUPPORTED",
           what + " through a DECIMAL that keeps " + std::to_string(*cap) +
               " fractional digits, and a value with more loses them on " +
               dialect_ + " (scale-limit); strict mode refuses that",
           pos);
  }
  add_caveat("scale-limit");
}

// The coerce variant reads both operands through numericCast. A constant's
// scale is known and only one past the cap is truncated; a column's is not --
// NUM says it is a number, not how many fractional digits it has.
void Translator::coerce_scale_limits(std::span<const SNode* const> operands) {
  const std::optional<std::int32_t> cap = numeric_cast_scale();
  if (!cap) return;
  for (const SNode* operand : operands) {
    if (is_constant(*operand, const_names_)) {
      if (constant_scale(*operand, const_root_) > *cap) {
        scale_limited(operand->pos(), "this constant is read as a number");
      }
    } else {
      scale_limited(operand->pos(), "this operand is read as a number");
    }
  }
}

// --- the map application path ------------------------------------------------

std::optional<std::string> Translator::variant_for(const std::string& op,
                                                   std::span<const Fragment> args) {
  // Fixed by sql/MAP.md §4.3 and identical in every host -- deliberately NOT
  // data in the dialect map. The `num` test is AND and the `bin` test is OR,
  // and the asymmetry is intentional: an unknown numeric operand must be
  // coerced, an unknown concat operand must not be treated as bytes.
  if (contains(NUMERIC_OPS, op)) {
    return args[0].kind() == SqlKind::Num && args[1].kind() == SqlKind::Num
               ? "num"
               : "coerce";
  }
  if (contains(TEXTUAL_OPS, op)) return "text";
  if (op == "&") {
    return args[0].kind() == SqlKind::Bin || args[1].kind() == SqlKind::Bin
               ? "bin"
               : "text";
  }
  return std::nullopt;
}

std::string Translator::template_of(const Entry& entry,
                                    std::span<const Fragment> args,
                                    const std::optional<std::string>& variant,
                                    const std::string& what, Pos pos) {
  if (entry.body == BodyKind::Variants) {
    const Keyed* arm = nullptr;
    if (variant) {
      for (const Keyed& k : entry.keyed) {
        if (k.key == *variant) { arm = &k; break; }
      }
    }
    if (!arm || !arm->value.present) {
      refuse("E_SQL_UNSUPPORTED",
             what + " has no mapping in dialect " + dialect_ + " for " +
                 (variant ? *variant + " operands" : std::string("this shape")),
             pos);
    }
    return std::string(arm->value.text);
  }
  if (entry.body == BodyKind::One) return std::string(entry.one);

  // Arity-keyed. Membership FIRST, then the null test: a named count whose
  // template is null WITHDRAWS that arity, and the `*` fallback must not
  // rescue it. `{"1": null, "*": "LEAST({*})"}` refuses MIN(5) and still
  // answers MIN(5, 3).
  const std::string n = std::to_string(args.size());
  const Keyed* arm = nullptr;
  const Keyed* star = nullptr;
  for (const Keyed& k : entry.keyed) {
    if (k.key == n) arm = &k;
    if (k.key == "*") star = &k;
  }
  if (!arm && star) arm = star;
  if (!arm || !arm->value.present) {
    std::vector<std::string> keys;
    for (const Keyed& k : entry.keyed) keys.emplace_back(k.key);
    std::sort(keys.begin(), keys.end());
    std::string list;
    for (std::size_t i = 0; i < keys.size(); ++i) {
      if (i) list += ", ";
      list += keys[i];
    }
    refuse("E_SQL_UNSUPPORTED",
           what + " has no mapping in dialect " + dialect_ + " for " + n +
               " argument(s); it maps " + list,
           pos);
  }
  return std::string(arm->value.text);
}

Fragment Translator::apply(Section section, const std::string& key,
                           std::span<const Fragment> args, Pos pos,
                           const std::optional<std::string>& variant) {
  const Entry* entry = Map::entry(dialect_, section, key);
  const std::string what =
      section == Section::Ops ? "the " + key + " operator" : key;

  if (!entry) {
    refuse("E_SQL_UNSUPPORTED",
           what + " has no mapping in dialect " + dialect_, pos);
  }
  if (entry->kind == EntryKind::Refusal) {
    // The map's own reason, after an em dash. This is how BAND/BOR/BXOR are
    // refused: the entry is a string saying why no portable spelling exists.
    refuse("E_SQL_UNSUPPORTED",
           what + " has no mapping in dialect " + dialect_ +
               (entry->reason.present
                    ? " — " + std::string(entry->reason.text)
                    : std::string{}),
           pos);
  }
  if (entry->kind == EntryKind::Builder) {
    // Skips arity, since, caveat and the template entirely.
    return (*entry->builder)(emit_, args, pos);
  }
  if (entry->has_arity) {
    const int n = static_cast<int>(args.size());
    if (n < entry->arity_min || n > entry->arity_max) {
      refuse("E_SQL_UNSUPPORTED",
             what + " has no mapping in dialect " + dialect_ + " for " +
                 std::to_string(n) + " argument(s)",
             pos);
    }
  }
  if (!entry->since.empty() &&
      !version_at_least(Map::version(dialect_), entry->since)) {
    refuse("E_SQL_DIALECT",
           what + " needs " + dialect_ + " " + std::string(entry->since) +
               " or newer, and this map says " + Map::version(dialect_),
           pos);
  }
  if (!entry->caveat.empty()) {
    if (strict_) {
      refuse("E_SQL_UNSUPPORTED",
             what + " translates only approximately in dialect " + dialect_ +
                 " (" + std::string(entry->caveat) +
                 "), and strict mode refuses those",
             pos);
    }
    add_caveat(std::string(entry->caveat));
  }
  const std::string tpl = template_of(*entry, args, variant, what, pos);
  return Fragment(emit_.fill(tpl, args, pos), ret_kind(*entry, args, pos),
                  dialect_);
}

Fragment Translator::fold_pairwise(const std::string& op,
                                   std::span<const Fragment> parts, Pos pos) {
  // Never empty: every caller supplies its aggregate's identity literal
  // instead, and those differ per aggregate (TRUE for ALL, FALSE for ANY, 0 for
  // SUM and COUNT, "" for JOIN).
  Fragment acc = parts[0];
  for (std::size_t i = 1; i < parts.size(); ++i) {
    const Fragment pair[] = {acc, parts[i]};
    // Recomputed at EVERY step from the accumulator's CURRENT kind: for `&`
    // the accumulator becomes BIN as soon as any operand is, and stays BIN.
    acc = apply(Section::Ops, op, pair, pos, variant_for(op, pair));
  }
  return acc;
}

// --- operators ---------------------------------------------------------------

Fragment Translator::unary(const SNode& n) {
  Fragment x = node(n.l());
  if (n.s() == "NOT") {
    x = require_bool(x, n.l()->pos(), "NOT");
  } else {
    require_not_bool(x, n.l()->pos(), n.s());
    // No require_numeric_constant here, unlike binary(). A unary node whose
    // operand is constant IS constant, so node()'s own whole-node validate()
    // refuses it regardless, on the way back out of this dispatch -- `-"x" + T`
    // is E_SQL_INVALID with or without a call here, which makes one
    // unwritable-as-a-case and therefore not a check. The guard below is a
    // different matter: it fires on a NON-constant operand, which is exactly
    // what node() cannot see.
    x = guard_numeric(x, *n.l());
  }
  const Fragment one[] = {x};
  // No variant: a unary entry must be a plain template, and a dialect that gave
  // NOT or NEG a variants map would be refused by template_of.
  return apply(Section::Ops, n.s(), one, n.pos());
}

Fragment Translator::binary(const SNode& n) {
  const std::string op = n.s();
  if (op == "IN") return in_operator(n);

  // Strictly left then right: parameter slots are numbered in this order.
  Fragment l = node(n.l());
  Fragment r = node(n.r());

  if (op == "AND" || op == "OR" || op == "XOR") {
    l = require_bool(l, n.l()->pos(), op);
    r = require_bool(r, n.r()->pos(), op);
  }
  if (contains(ARITHMETIC_OPS, op) || contains(NUMERIC_OPS, op)) {
    require_not_bool(l, n.l()->pos(), op);
    require_not_bool(r, n.r()->pos(), op);
    // And an operand whose value is written down has to BE a number. After the
    // BOOL guard, not before: `TRUE + 1` is E_SQL_SHAPE and stays that way.
    require_numeric_constant(*n.l());
    require_numeric_constant(*n.r());
    // And an operand nobody has vouched for is wrapped so that a value SEL
    // would refuse becomes NULL rather than a number the server invented. A NUM
    // operand passes through untouched -- and both operands being NUM by this
    // point is what selects the `num` variant below.
    l = guard_numeric(l, *n.l());
    r = guard_numeric(r, *n.r());
  }
  // BAND/BOR/BXOR get NO kind guard: they are refused by the map entry itself.
  if (op == "&" || (op.size() > 1 && op[0] == '$')) {
    require_not_bool_operand(l, n.l()->pos(), op);
    require_not_bool_operand(r, n.r()->pos(), op);
  }

  // Captured BEFORE the rewrite below, which forces both kinds to TEXT.
  const Fragment before[] = {l, r};
  const std::optional<std::string> variant = variant_for(op, before);
  if (variant == "coerce") {
    const SNode* const operands[] = {n.l().get(), n.r().get()};
    coerce_scale_limits(operands);
  }

  if (contains(BYTE_COMPARISONS, op)) {
    require_comparable_kinds(l, r, op, n.pos());
    const bool l_exact = l.exact();
    const bool r_exact = r.exact();
    const bool l_lit = n.l() && n.l()->t() == SNode::T::Text;
    const bool r_lit = n.r() && n.r()->t() == SNode::T::Text;
    const Lexical* spf = emit_.lex("sargablePrefilter");
    const bool sargable_prefilter = (spf && spf->kind == LexKind::Text && spf->text == "true");
    if ((l_exact && (r_exact || r_lit)) || (r_exact && l_lit)) {
      // bare comparison
    } else if (op == "$==" && ((l.sargable() && r_lit) || (r.sargable() && l_lit))) {
      // A sargable column against a literal, either way round: the coarse
      // comparison the index can serve, AND the exact one.
      if (sargable_prefilter) {
        const Fragment coarse_args[] = {l, r};
        const Fragment coarse = apply(Section::Ops, "$==", coarse_args, n.pos(), variant);
        const Fragment res_args[] = {emit_.text_operand(l), emit_.text_operand(r)};
        const Fragment residual = apply(Section::Ops, "$==", res_args, n.pos(), variant);
        const Fragment and_args[] = {coarse, residual};
        Fragment res = apply(Section::Ops, "AND", and_args, n.pos());
        res.set_prefilter(std::make_shared<Fragment>(coarse));
        res.set_separate_prefilter(l.separate_prefilter() || r.separate_prefilter());
        return res;
      }
    } else if (l.kind() != SqlKind::Bin || r.kind() != SqlKind::Bin) {
      l = emit_.text_operand(l);
      r = emit_.text_operand(r);
    }
  }
  const Fragment args[] = {l, r};
  Fragment res = apply(Section::Ops, op, args, n.pos(), variant);
  if (op == "AND") {
    if (l.prefilter() && r.prefilter()) {
      const Fragment pair[] = {*l.prefilter(), *r.prefilter()};
      res.set_prefilter(std::make_shared<Fragment>(apply(Section::Ops, "AND", pair, n.pos())));
    } else if (l.prefilter()) {
      const Fragment pair[] = {*l.prefilter(), r};
      res.set_prefilter(std::make_shared<Fragment>(apply(Section::Ops, "AND", pair, n.pos())));
    } else if (r.prefilter()) {
      const Fragment pair[] = {l, *r.prefilter()};
      res.set_prefilter(std::make_shared<Fragment>(apply(Section::Ops, "AND", pair, n.pos())));
    }
    if (l.separate_prefilter() || r.separate_prefilter()) {
      res.set_separate_prefilter(true);
    }
  }
  return res;
}

}  // namespace sel::sql

// --- skeletons ---------------------------------------------------------------

namespace sel::sql {
namespace {

// A synthetic literal node. `grouped` MUST stay false: is_binder_name rejects a
// grouped var, and a synthetic node accidentally marked grouped would be
// refused as a binder name.
NodePtr lit_node(NT t, std::string s, bool b, Pos pos) {
  auto n = std::make_shared<Node>();
  n->t = t;
  n->pos = pos;
  n->s = std::move(s);
  n->b = b;
  return n;
}

// Merges named slot maps, refusing to let one shadow another.
//
// std::logic_error, NOT SqlError: a collision is a bug in the translator, not a
// refusable property of a rule or a map, so it must escape try_translate() the
// way every other host mistake does. Python's `{**a, **b}` keeps the right
// value and PHP's `+` keeps the left; either would silently drop a slot, which
// is why this checks instead of merging.
template <typename Map>
Map merge_slots(Map a, const Map& b) {
  for (const auto& [k, v] : b) {
    for (const auto& [existing, ignored] : a) {
      (void)ignored;
      if (existing == k) {
        throw std::logic_error("two sources both supply the skeleton slot {" + k +
                               "}; one would silently shadow the other");
      }
    }
    a.emplace_back(k, v);
  }
  return a;
}

}  // namespace

std::string Translator::skeleton(const std::string& name, Pos pos) {
  const Entry* s = Map::entry(dialect_, Section::Skel, name);
  // Absent first, and separately from the string branch: reading the sentinel
  // as a refusal-with-reason would emit its text as the reason.
  if (!s) {
    refuse("E_SQL_UNSUPPORTED",
           "dialect " + dialect_ + " has no " + name + " skeleton", pos);
  }
  if (s->kind == EntryKind::Refusal) {
    if (!s->reason.present) {
      refuse("E_SQL_UNSUPPORTED",
             "dialect " + dialect_ + " has no " + name + " skeleton", pos);
    }
    // The shipped map uses this for `join` in ansi and mysql-family.
    refuse("E_SQL_UNSUPPORTED",
           "dialect " + dialect_ + " cannot express " + name + " — " +
               std::string(s->reason.text),
           pos);
  }
  if (s->kind == EntryKind::Builder) {
    // A hole the Python host has: map validation returns early on a builder
    // BEFORE the skel branch, so define(d, 'skel', k, builder) registers and
    // then hits a bare KeyError on s['tpl'] -- a host crash escaping
    // try_translate rather than a refusal. Refused here instead.
    refuse("E_SQL_UNSUPPORTED",
           "the " + name + " skeleton for " + dialect_ +
               " is a builder, and a skeleton is a template",
           pos);
  }
  if (!s->caveat.empty()) {
    if (strict_) {
      refuse("E_SQL_UNSUPPORTED",
             "the " + name + " skeleton for " + dialect_ +
                 " is not exactly equivalent (" + std::string(s->caveat) +
                 "), and strict mode refuses those",
             pos);
    }
    add_caveat(std::string(s->caveat));
  }
  return std::string(s->one);
}

std::vector<Fragment::Part> Translator::fill_named(std::string_view tpl,
                                                   const SlotMap& slots, Pos pos) {
  std::vector<Fragment::Part> parts;
  const auto push = [&parts](std::string_view s) {
    if (s.empty()) return;
    if (!parts.empty() && !parts.back().is_slot) {
      parts.back().sql.append(s);
      return;
    }
    Fragment::Part p;
    p.sql = std::string(s);
    parts.push_back(std::move(p));
  };

  std::size_t i = 0;
  while (i < tpl.size()) {
    if (tpl[i] != '{') { push(tpl.substr(i, 1)); ++i; continue; }
    const std::size_t end = tpl.find('}', i);
    // An unterminated brace emits the rest of the template literally. No
    // refusal, in either host.
    if (end == std::string_view::npos) { push(tpl.substr(i)); break; }
    const std::string name(tpl.substr(i + 1, end - i - 1));
    i = end + 1;

    const std::vector<Slot>* items = nullptr;
    for (const auto& [k, v] : slots) {
      if (k == name) { items = &v; break; }
    }
    // Membership on the slot map the CALLER passed, not on the vocabulary.
    if (!items) {
      refuse("E_SQL_UNSUPPORTED",
             "a skeleton in dialect " + dialect_ + " uses {" + name +
                 "}, which is not one of its slots",
             pos);
    }
    for (const Slot& item : *items) {
      if (const std::string* s = std::get_if<std::string>(&item)) { push(*s); continue; }
      for (const Fragment::Part& p : std::get<Fragment>(item).parts()) {
        // A slot index is absolute for the whole translation and is never
        // renumbered on splice.
        if (p.is_slot) parts.push_back(p);
        else push(p.sql);
      }
    }
  }
  return parts;
}

Translator::SlotMap Translator::relation_slots(const RelationSpec& rel) {
  std::string from = rel.from_is_raw ? rel.from : emit_.ident(rel.from);
  if (rel.alias && !rel.alias->empty()) from += " " + emit_.ident(*rel.alias);
  // An uncorrelated relation is a subquery over the whole table, which is legal
  // and occasionally what you want.
  const std::string corr =
      rel.correlate ? *rel.correlate : std::string(lex_text(dialect_, "true"));
  return {{"from", {Slot{from}}}, {"corr", {Slot{corr}}}};
}

SNodePtr Translator::value_node(const Value& v, const Binding& b, Pos pos) {
  if (v.size() > 0) {
    std::vector<std::pair<std::string, SNodePtr>> entries;
    for (const auto& [k, child] : v.entries()) {
      entries.emplace_back(k, value_node(child, b, pos));
    }
    return SNode::clist(pos, std::move(entries));
  }
  if (v.is_bool()) return SNode::leaf(lit_node(NT::Bool, "", v.boolean_scalar(), pos));
  if (v.is_bin()) {
    refuse("E_SQL_SHAPE",
           "a BIN element of a value binding has no literal node to become; "
           "bind it as a column, or convert it before translating",
           pos);
  }
  const std::optional<SqlKind> t = b.value_type();
  const bool num = t && *t == SqlKind::Num;
  return SNode::leaf(lit_node(num ? NT::Num : NT::Text, v.as_text(pos), false, pos));
}

std::vector<std::pair<std::string, Binder>> Translator::value_elements(
    const Binding& b, Pos pos) {
  const Value& v = b.as_value();
  if (v.size() == 0) {
    // A NONE with no children is genuinely empty -- what FILTER returns when
    // nothing matched. A scalar is a one-element list of itself.
    if (v.is_none()) return {};
    std::vector<std::pair<std::string, Binder>> one;
    one.emplace_back("1", Binder::node(value_node(v, b, pos)));
    return one;
  }
  std::vector<std::pair<std::string, Binder>> out;
  for (const auto& [k, child] : v.entries()) {
    out.emplace_back(k, Binder::node(value_node(child, b, pos)));
  }
  return out;
}

// --- IN ----------------------------------------------------------------------

Fragment Translator::in_operator(const SNode& n) {
  const SNode& rhs = *n.r();
  // The needle is deliberately NOT rendered up front: rendering it eagerly
  // bound a value the list branch never used, leaving one more entry in params
  // than there were placeholders.

  const bool rhs_is_free_var =
      rhs.t() == SNode::T::Var && binder(rhs.s()) == nullptr;

  // --- branch A: a relation
  if (rhs_is_free_var && bindings_.has(rhs.s())) {
    const Binding& b = bindings_.get(rhs.s(), rhs.pos());
    if (b.kind() == Binding::Kind::Relation) {
      const RelationSpec& rel = b.as_relation();
      const ColumnSpec* scalar =
          rel.scalar ? rel.field(ascii_upper(*rel.scalar)) : nullptr;
      // Fires BEFORE the field count, so a multi-field relation with no scalar
      // gets this message.
      if (!scalar) {
        refuse("E_SQL_SHAPE",
               "IN over " + rhs.s() +
                   " needs the binding to name a \"scalar\" field: that is the "
                   "column the subquery projects",
               rhs.pos());
      }
      if (rel.fields.size() != 1) {
        refuse("E_SQL_SHAPE",
               "IN over " + rhs.s() + " is refused: the relation declares " +
                   std::to_string(rel.fields.size()) +
                   " fields, so SEL reads its rows as maps and a scalar can "
                   "never equal one. Bind the projected column as a relation "
                   "with that one field.",
               rhs.pos());
      }
      // The skeleton FIRST, before the needle is rendered. Python spells this
      // as `_fill_named(self._skeleton(...), _slots(...))` and gets the order
      // from left-to-right argument evaluation; C++ has to say it, because a
      // statement that builds the slots first would render the needle -- and
      // could refuse from inside it -- before discovering the dialect cannot
      // express inRelation at all. A dialect that withdrew the skeleton would
      // then answer E_SQL_UNBOUND for `UNBOUND IN REL` where every other host
      // answers E_SQL_UNSUPPORTED. Same rule conditional() and
      // join_aggregate() already follow.
      const std::string skel = skeleton("inRelation", n.pos());
      // No require_comparable_kinds here, and text_operand is applied
      // unconditionally -- there is no two-BIN skip as in binary().
      SlotMap slots = merge_slots(
          relation_slots(rel),
          SlotMap{{"needle", {Slot{emit_.text_operand(node(n.l()))}}},
                  {"body", {Slot{emit_.text_operand(column_ref(*scalar))}}}});
      return Fragment(fill_named(skel, slots, n.pos()), SqlKind::Bool, dialect_);
    }
  }

  // --- element collection. `absent` and `empty` are different states: absent
  // means no list shape was recognised and the scalar fallback applies, empty
  // means a list with no members and the answer is FALSE.
  std::optional<std::vector<SNodePtr>> elements;
  if (rhs.t() == SNode::T::List) {
    elements = rhs.kids();
  } else if (rhs.t() == SNode::T::CList) {
    elements = rhs.kids();   // keys discarded, entry order kept
  } else if (rhs_is_free_var && bindings_.has(rhs.s())) {
    const Binding& b = bindings_.get(rhs.s(), rhs.pos());
    if (b.kind() == Binding::Kind::Value && b.as_value().size() > 0) {
      std::vector<SNodePtr> out;
      for (auto& [k, bind] : value_elements(b, rhs.pos())) {
        (void)k;
        out.push_back(bind.as_node());
      }
      elements = std::move(out);
    }
  }

  // --- branch B: the scalar fallback
  if (!elements) {
    const Fragment r = node(n.r());   // RIGHT operand rendered FIRST
    const Fragment l = node(n.l());
    require_comparable_kinds(l, r, "IN", n.pos());
    const Fragment args[] = {emit_.text_operand(l), emit_.text_operand(r)};
    return apply(Section::Ops, "IN", args, n.pos(), "scalar");
  }

  // --- branch C: an empty list
  if (elements->empty()) return literal(Value::boolean(false), SqlKind::Bool);

  // --- branch D: a list of elements
  std::vector<Fragment> tests;
  for (const SNodePtr& e : *elements) {
    // Re-rendered once per element, which is not an optimisation to remove:
    // splicing one Fragment N times puts the same slot number in the output N
    // times while params holds one entry.
    const Fragment raw = node(n.l());
    const bool is_exact = raw.exact();
    const Fragment needle = is_exact ? raw : emit_.text_operand(raw);
    const Fragment f = node(e);
    if (f.kind() == SqlKind::List) {
      refuse("E_SQL_SHAPE",
             "IN over a list of lists is structural in SEL and has no SQL "
             "counterpart",
             e->pos());
    }
    // `raw`, not `needle`: needle's kind is always TEXT after the cast.
    require_comparable_kinds(raw, f, "IN", e->pos());
    const Fragment item = is_exact ? f : emit_.text_operand(f);
    const Fragment args[] = {needle, item};
    // The map key is EQL with variant text; the IN entry is not used here.
    tests.push_back(apply(Section::Ops, "EQL", args, e->pos(), "text"));
  }
  return fold_pairwise("OR", tests, n.pos());
}

}  // namespace sel::sql

// --- conditionals ------------------------------------------------------------

namespace sel::sql {

Fragment Translator::conditional(const SNode& n) {
  const std::string name = n.s();
  std::vector<SNodePtr> args = n.kids();
  // Spec §7.2's default: a TEXT literal of the empty string carrying the CALL's
  // position. There is no separate two-argument template. Because the default
  // is TEXT and not UNKNOWN, `IF(p, 1)` refuses (NUM vs TEXT) and `IF(p, TRUE)`
  // refuses (BOOL vs TEXT) -- only `IF(p, "x")` and an undeclared column
  // survive the two-argument form.
  if (name == "IF" && args.size() == 2) {
    args.push_back(SNode::leaf(lit_node(NT::Text, "", false, n.pos())));
  }
  // BOTH fetched before any argument is walked, so a dialect that cannot
  // express CASE refuses before params grows and before any sub-refusal from
  // the branches can fire.
  const std::string branch_tpl = skeleton("caseBranch", n.pos());
  const std::string case_tpl = skeleton("case", n.pos());

  const std::size_t last = args.size() - 1;
  std::vector<Fragment> results;
  std::vector<Slot> joined;
  // Condition first, then result -- the walk order is part of the output,
  // because slot numbers are assigned in creation order and a {n}-numbered
  // dialect prints them.
  for (std::size_t i = 0; i < last; i += 2) {
    const Fragment cond =
        require_bool(node(args[i]), args[i]->pos(), name);
    Fragment then = node(args[i + 1]);
    results.push_back(then);
    SlotMap slots{{"cond", {Slot{cond}}}, {"then", {Slot{then}}}};
    // The branch's kind is a placeholder: nothing reads it, because the branch
    // is consumed only through its parts by the outer fill.
    Fragment branch(fill_named(branch_tpl, slots, n.pos()), SqlKind::Unknown,
                    dialect_);
    if (!joined.empty()) joined.emplace_back(std::string(" "));
    joined.emplace_back(std::move(branch));
  }
  Fragment els = node(args[last]);
  results.push_back(els);

  SlotMap slots{{"branches", joined}, {"else", {Slot{els}}}};
  // Only the thens and the else; the conditions are BOOL and would poison it.
  return Fragment(fill_named(case_tpl, slots, n.pos()),
                  unify(results, n.pos()), dialect_);
}

Fragment Translator::case_when(const Fragment& cond, const Fragment& then,
                               const Fragment& els, Pos pos) {
  SlotMap branch_slots{{"cond", {Slot{cond}}}, {"then", {Slot{then}}}};
  Fragment branch(fill_named(skeleton("caseBranch", pos), branch_slots, pos),
                  SqlKind::Unknown, dialect_);
  SlotMap slots{{"branches", {Slot{std::move(branch)}}}, {"else", {Slot{els}}}};
  return Fragment(fill_named(skeleton("case", pos), slots, pos),
                  then.kind() == els.kind() ? then.kind() : SqlKind::Unknown,
                  dialect_);
}

}  // namespace sel::sql

// --- calls -------------------------------------------------------------------

namespace sel::sql {
namespace {

// Measured, not written: every non-lazy name in the registry was called with
// TO_UTF8("a") and with TRUE, and these are the ones SEL did not answer
// E_NOT_* for. Writing them by hand would be the second copy of SEL's argument
// rules that §11.4 exists to avoid. sql/oracle/ re-measures them.
constexpr std::string_view BIN_ARGUMENT_OK[] = {
    "BLEN", "CRC32", "ENCODE_BASE64", "FROM_UTF8", "ISNUM", "TO_HEX", "TO_UTF8"};
constexpr std::string_view BOOL_ARGUMENT_OK[] = {"ISNUM"};

constexpr std::string_view AGGREGATES[] = {"ALL", "ANY", "MAP",
                                           "FILTER", "SUM", "JOIN"};

// Which funcs take a regex, and at which 0-based argument. All four name index
// 0; the shape exists so a function taking a regex elsewhere is one entry
// rather than a code change.
std::optional<int> regex_at(std::string_view name) {
  if (name == "RMATCH" || name == "RFIND" || name == "RREPLACE" ||
      name == "RGROUPS") {
    return 0;
  }
  return std::nullopt;
}

bool is_numeric_argument(std::string_view name, size_t i) {
  if (name == "MIN" || name == "MAX") return true;
  if (i == 0) {
    return name == "ABS" || name == "SIGN" || name == "CEIL" ||
           name == "FLOOR" || name == "TRUNC" || name == "ROUND" ||
           name == "POWER" || name == "CHAR" || name == "CANON";
  }
  if (i == 1) {
    return name == "ROUND" || name == "POWER" || name == "LEFT" ||
           name == "RIGHT" || name == "SUBSTR" || name == "REPEAT" ||
           name == "PADL" || name == "PADR";
  }
  if (i == 2) {
    return name == "SUBSTR" || name == "FIND";
  }
  return false;
}

}  // namespace

void Translator::require_argument_kind(const std::string& name, const Fragment& f,
                                       Pos pos) {
  if (f.kind() == SqlKind::Bool && !contains(BOOL_ARGUMENT_OK, name)) {
    refuse("E_SQL_SHAPE",
           name + " does not take a BOOL argument; SEL raises here rather than "
                  "reading a boolean as text or as 1",
           pos);
  }
  if (f.kind() == SqlKind::Bin && !contains(BIN_ARGUMENT_OK, name)) {
    refuse("E_SQL_SHAPE",
           name + " reads its argument as text, and this is BIN; SEL raises "
                  "here rather than reinterpreting bytes as characters",
           pos);
  }
}

SNodePtr Translator::rewrite_regex(const SNodePtr& n) {
  const std::optional<int> at = regex_at(n->s());
  if (!at) return n;

  std::vector<SNodePtr> args = n->kids();
  const std::size_t pat_at = static_cast<std::size_t>(*at);
  const SNodePtr pat = pat_at < args.size() ? args[pat_at] : nullptr;
  // Both the pattern and the flags must be literals: a pattern read from a
  // column cannot be rewritten, and the flag selects the template.
  if (!pat || pat->t() != SNode::T::Text) {
    refuse("E_SQL_UNSUPPORTED",
           n->s() + " needs a literal pattern here: SEL rewrites \\d, \\w and "
                    "\\s into explicit ASCII classes before matching, and a "
                    "pattern that is not known until the query runs cannot be "
                    "rewritten",
           pat ? pat->pos() : n->pos());
  }

  std::string source;
  try {
    // The language's OWN rewriter. A copy here would be a second thing to keep
    // in step, and it would fail silently when they drifted.
    source = validate_pattern(pat->s(), pat->pos());
  } catch (const SelError& e) {
    // SEL raises this too, but only when the call is REACHED. Translation walks
    // every branch, so a pattern in a branch the evaluator never takes arrives
    // here anyway -- and a SelError escaping translate() would break the one
    // thing try_translate promises. Found by the fuzz lane, as a fatal in the
    // middle of a run.
    refuse("E_SQL_UNSUPPORTED",
           n->s() + "'s pattern is not in SEL's portable subset, so there is "
                    "nothing to translate: " + e.message(),
           pat->pos());
  }

  // Dotall is permanently on in SEL (spec §7.8) and off by default in the
  // server, so every pattern carries (?s). The modifier goes in the PATTERN
  // rather than the template: selecting an arity-keyed template by argument
  // count gave every three-argument call the case-insensitive form, and left
  // the flag bound as a parameter nothing emitted.
  std::string inline_flags = "(?s)";

  const std::size_t flag_at = n->s() == "RREPLACE" ? 3 : 2;
  if (flag_at >= args.size()) {
    args[pat_at] =
        SNode::leaf(lit_node(NT::Text, inline_flags + source, false, pat->pos()));
    return SNode::rewritten(n->origin(), std::move(args));
  }
  const SNodePtr flags = args[flag_at];
  if (flags->t() != SNode::T::Text) {
    refuse("E_SQL_UNSUPPORTED",
           n->s() + " needs literal flags here: their content selects the "
                    "mapping, so they have to be known before the query runs",
           flags->pos());
  }
  // The flag string's CONTENT chooses the template. Choosing by argument count
  // meant every three-argument call got the case-insensitive form, so
  // RMATCH(p, s, "") matched case-insensitively where SEL does not, and
  // RMATCH(p, s, "zzz") compiled happily where SEL raises E_BAD_ARG.
  //
  // Spelled as the two strings that pass rather than as a case fold: an
  // ASCII-only lower would admit exactly these two anyway, and naming them is
  // byte-exact and needs no helper.
  const std::string& text = flags->s();
  if (text != "" && text != "i" && text != "I") {
    refuse("E_SQL_UNSUPPORTED",
           n->s() + " accepts only the i flag here, and SEL accepts only i at "
                    "all; \"" + text + "\" is not it",
           flags->pos());
  }
  if (!text.empty()) {
    // The evaluator refuses i on a pattern with non-ASCII literals, because
    // case folding above ASCII is the one thing PCRE and ECMAScript cannot be
    // made to agree on. A translation that accepted it would disagree with the
    // host that refused it. Any byte >= 0x80 is a code point above 0x7f.
    for (unsigned char c : source) {
      if (c > 0x7f) {
        refuse("E_SQL_UNSUPPORTED",
               "the i flag needs an ASCII-only pattern, which SEL requires for "
               "the same reason and refuses here too",
               flags->pos());
      }
    }
    inline_flags = "(?si)";
  }
  args[pat_at] =
      SNode::leaf(lit_node(NT::Text, inline_flags + source, false, pat->pos()));
  args.erase(args.begin() + static_cast<std::ptrdiff_t>(flag_at));
  return SNode::rewritten(n->origin(), std::move(args));
}

Fragment Translator::call(const SNodePtr& n) {
  // Captured before the rewrite, which preserves the name but rebinds the node.
  const std::string name = n->s();

  // The two aggregates over a bucket's members -- COUNT(g) is COUNT(*) and
  // SUM(g, [x,] body) is SUM over the grouped rows -- fire on the Group
  // binder alone: over a relation row, COUNT(_) is the row's number of
  // fields in SEL (review 2026-09-15 finding X), and SEL has no per-group
  // MIN or MAX (finding J). The body binds the member row, as the
  // evaluator's walk does: `_` for the two-argument form, the name given
  // for the three-argument one.
  if (statement_plan_ && !n->kids().empty() && n->kids()[0]->t() == SNode::T::Var) {
    const Binder* group = binder(n->kids()[0]->s());
    if (group && group->shape() == Binder::Shape::Group) {
      if (name == "COUNT" && n->kids().size() == 1) {
        std::vector<Fragment::Part> p;
        p.push_back({false, "COUNT(*)"});
        return Fragment(p, SqlKind::Num, dialect_);
      }
      if (name == "SUM" && n->kids().size() >= 2) {
        const bool has_custom_binder = n->kids().size() == 3 && is_binder_name(*n->kids()[1]);
        const auto& body_node = has_custom_binder ? n->kids()[2] : n->kids()[1];
        Source src;
        src.shape = Source::Shape::Relation;
        src.relation = group->as_row_ptr();
        const Fragment inner = with_row(src, has_custom_binder ? n->kids()[1]->s() : "_",
                                        [&]() { return node(body_node); });
        std::string sql = "COALESCE(SUM(";
        for (const auto& pt : inner.parts()) sql += pt.sql;
        sql += "), 0)";
        std::vector<Fragment::Part> p;
        p.push_back({false, std::move(sql)});
        return Fragment(p, SqlKind::Num, dialect_);
      }
    }
  }

  // Each of these short-circuits before the next, and none reaches the funcs
  // table: the generator rejects a dialect document that lists one, and the
  // registry refuses one.
  if (contains(AGGREGATES, name)) return aggregate(*n);
  if (name == "COUNT") return count(*n);
  if (name == "HAS") return has(*n);
  if (name == "INDEXES") {
    refuse("E_SQL_SHAPE",
           "INDEXES yields a list of keys, and a SQL expression is a scalar",
           n->pos());
  }
  if (name == "ABORT") {
    refuse("E_SQL_UNSUPPORTED",
           "ABORT raises an error, which is a control-flow effect and not a "
           "value a SQL expression can be",
           n->pos());
  }
  if (name == "IF" || name == "COND") return conditional(*n);
  if (host_arity(name)) return host_call(*n);

  // Before any argument is rendered, so the deleted flag node never becomes a
  // parameter slot.
  const SNodePtr rewritten = rewrite_regex(n);

  std::vector<Fragment> args;
  // Left to right, and the order is load-bearing: slot numbers are allocated in
  // render order.
  for (size_t i = 0; i < rewritten->kids().size(); ++i) {
    const SNodePtr& arg = rewritten->kids()[i];
    Fragment f = node(arg);
    if (f.kind() == SqlKind::List) {
      refuse("E_SQL_SHAPE",
             "argument to " + name + " is a list, and a SQL expression is a scalar",
             arg->pos());
    }
    require_argument_kind(name, f, arg->pos());
    if (is_numeric_argument(name, i)) {
      require_numeric_constant(*arg);
      f = guard_numeric(f, *arg);
    }
    args.push_back(std::move(f));
  }
  // No variant is ever passed for funcs. SEL's own arity was enforced at parse
  // time, so apply() defends only the dialect's optional narrowing.
  Fragment out = apply(Section::Funcs, name, args, n->pos());
  if (name == "CANON") out.set_canonical(true);
  return out;
}

// A call to an application's own function (sql/MAP.md §4.7).
//
// The entry is looked up first, because its `args` say how each argument
// renders: so a function with no spelling in this dialect is refused at the
// call before any argument is examined. None of the builtins' own argument
// rules apply -- which of them take a BOOL, which read a number -- only what
// the entry declares. And the fragment says whose promise it is: every use
// carries the caveat host-function, which strict mode refuses.
Fragment Translator::host_call(const SNode& n) {
  const std::string name = n.s();
  const Entry* entry = Map::entry(dialect_, Section::Funcs, name);
  if (!entry || (entry->kind == EntryKind::Refusal && !entry->reason.present)) {
    refuse("E_SQL_UNSUPPORTED",
           name + " is a host function with no SQL spelling in dialect " + dialect_ +
               "; register one with the map, or evaluate it here",
           n.pos());
  }
  if (entry->kind == EntryKind::Refusal) {
    refuse("E_SQL_UNSUPPORTED",
           name + " has no mapping in dialect " + dialect_ + " — " +
               std::string(entry->reason.text),
           n.pos());
  }
  const std::optional<std::pair<int, int>> recorded = Map::host_spelling_arity(dialect_, name);
  const std::optional<std::pair<int, int>> current = host_arity(name);
  if (recorded && recorded != current) {
    const auto list = [](const std::pair<int, int>& a) {
      return "[" + std::to_string(a.first) + ", " + std::to_string(a.second) + "]";
    };
    refuse("E_SQL_UNSUPPORTED",
           name + " was registered again with the arity " + list(*current) +
               " after its SQL spelling was defined for " + list(*recorded) +
               "; define the spelling again",
           n.pos());
  }
  if (strict_) {
    refuse("E_SQL_UNSUPPORTED",
           name + " is spelled by the application, which SEL cannot check "
                  "(host-function), and strict mode refuses that",
           n.pos());
  }
  add_caveat("host-function");

  std::vector<Fragment> args;
  // Left to right: slot numbers are allocated in render order.
  for (std::size_t i = 0; i < n.kids().size(); ++i) {
    const SNodePtr& arg = n.kids()[i];
    const std::string_view kind = i < entry->args.size() ? entry->args[i] : "ANY";
    if (kind == "LIST") {
      args.push_back(host_list_argument(name, arg));
      continue;
    }
    Fragment f = node(arg);
    if (f.kind() == SqlKind::List) {
      refuse("E_SQL_SHAPE",
             "argument to " + name + " is a list, and its spelling does not "
                                     "declare a LIST there",
             arg->pos());
    }
    const std::string got(kind_name(f.kind()));
    if ((kind == "NUM" || kind == "TEXT") &&
        (f.kind() == SqlKind::Bool || f.kind() == SqlKind::Bin)) {
      refuse("E_SQL_SHAPE",
             name + " declares this argument " + std::string(kind) + ", and this is a " + got,
             arg->pos());
    }
    if ((kind == "BOOL" || kind == "BIN") && got != kind) {
      refuse("E_SQL_SHAPE",
             name + " declares this argument " + std::string(kind) + ", and this is " +
                 (f.kind() == SqlKind::Unknown ? std::string("not one this layer can prove")
                                               : "a " + got),
             arg->pos());
    }
    if (kind == "NUM") {
      require_numeric_constant(*arg);
      if (f.kind() != SqlKind::Num) f = guard_numeric(f, *arg);
    }
    args.push_back(std::move(f));
  }
  return apply(Section::Funcs, name, args, n.pos());
}

// A LIST argument: a list known when translating, its elements rendered and
// joined with ', ' -- the template supplies the brackets.
Fragment Translator::host_list_argument(const std::string& name, const SNodePtr& arg) {
  const Source src = classify(arg);
  if (src.shape == Source::Shape::Relation) {
    refuse("E_SQL_SHAPE",
           "argument to " + name + " is a relation, rows the query has not read "
                                   "yet; a LIST argument is a list known when translating",
           arg->pos());
  }
  if (!src.filters.empty()) {
    refuse("E_SQL_SHAPE",
           "argument to " + name + " is a filtered list, whose elements are decided "
                                   "when it is evaluated; a template cannot express that",
           arg->pos());
  }
  if (src.elements.empty()) {
    refuse("E_SQL_SHAPE",
           "argument to " + name + " is an empty list, which has nothing for the "
                                   "template to hold",
           arg->pos());
  }
  std::vector<Fragment::Part> parts;
  for (std::size_t i = 0; i < src.elements.size(); ++i) {
    const Fragment f = from_binder(src.elements[i].second, *arg);
    if (f.kind() == SqlKind::List) {
      refuse("E_SQL_SHAPE",
             "argument to " + name + " has a list as an element, which has no "
                                     "scalar rendering",
             arg->pos());
    }
    if (i) parts.push_back({false, ", "});
    parts.insert(parts.end(), f.parts().begin(), f.parts().end());
  }
  return Fragment(std::move(parts), SqlKind::Unknown, dialect_);
}

}  // namespace sel::sql

// --- aggregates --------------------------------------------------------------

namespace sel::sql {
namespace {

// The funcs whose SEL result has children, so the scalar rule does not apply.
// Measured -- every non-lazy registry name called, the results with size() > 0
// kept -- not designed. Each is already refused by the dialect documents; the
// list exists so the aggregate-source path consults the map instead of falling
// through to the scalar branch, where COUNT and HAS folded to 0 and FALSE.
constexpr std::string_view YIELDS_LIST[] = {"BTL", "INDEXES", "RGROUPS", "SPLIT"};

std::string_view agg_fold(const std::string& name) {
  if (name == "ALL") return "AND";
  if (name == "ANY") return "OR";
  if (name == "SUM") return "+";
  return {};   // JOIN, MAP and FILTER fold by other means
}

std::string_view agg_skeleton(const std::string& name) {
  if (name == "ALL") return "all";
  if (name == "ANY") return "any";
  if (name == "SUM") return "sum";
  if (name == "JOIN") return "join";
  return {};
}

SqlKind agg_returns(const std::string& name) {
  if (name == "ALL" || name == "ANY") return SqlKind::Bool;
  if (name == "SUM") return SqlKind::Num;
  if (name == "JOIN") return SqlKind::Text;
  return SqlKind::List;   // MAP, FILTER -- refused before they reach here
}

// The alias a relation renders under: its own, or the table name when it
// declares none. The same rule Bindings::check_aliases applies.
std::string relation_alias(const RelationSpec& rel) {
  if (rel.alias && !rel.alias->empty()) return *rel.alias;
  return rel.from;
}

// The 2- and 3-argument forms: `_` by default, a bare name when given.
struct AggShape {
  std::string binder;
  SNodePtr body;
};

AggShape agg_shape(const SNode& n) {
  const auto& args = n.kids();
  if (args.size() == 3) {
    // BOTH halves of is_binder_name matter. `(C)` parses as a var carrying the
    // parser's grouped flag, and the evaluator refuses it with
    // E_EXPECT_SYMBOL; testing only the kind accepted a binder the language
    // rejects, in all three hosts.
    if (!is_binder_name(*args[1])) {
      refuse("E_SQL_SHAPE", "the binder of " + n.s() + " must be a bare name",
             args[1]->pos());
    }
    return {args[1]->s(), args[2]};
  }
  return {"_", args[1]};
}

// Assign, never insert-if-absent: the write ORDER is semantics. If the
// aggregate's binder is itself named `_K`, the later `_K` write wins and `_K`
// resolves to the key -- which is what the evaluator does.
void frame_set(std::vector<std::pair<std::string, Binder>>& frame,
               const std::string& name, Binder b) {
  for (auto& [k, v] : frame) {
    if (k == name) { v = std::move(b); return; }
  }
  frame.emplace_back(name, std::move(b));
}

}  // namespace

Translator::Source Translator::classify(const SNodePtr& src) {
  Source out;
  if (src->t() == SNode::T::Call) {
    const std::string name = src->s();
    if (name == "FILTER") {
      // Refuses FIRST if the FILTER's binder is not a bare name, then recurses
      // so nested FILTERs conjoin -- innermost first.
      const AggShape f = agg_shape(*src);
      Source inner = classify(src->kids()[0]);
      inner.filters.push_back({f.binder, f.body});
      return inner;
    }
    if (name == "MAP") {
      refuse("E_SQL_UNSUPPORTED",
             "MAP as the thing an aggregate iterates is not translated: unlike "
             "FILTER, which only decides whether an element takes part, MAP "
             "changes what the element is, so the two binders mean different "
             "things and binding both to one element is not enough. See "
             "docs/internals/sql-translation.md 7.5",
             src->pos());
    }
  }
  if (src->t() == SNode::T::List) {
    for (std::size_t i = 0; i < src->kids().size(); ++i) {
      out.elements.emplace_back(std::to_string(i + 1), Binder::node(src->kids()[i]));
    }
    return out;
  }
  if (src->t() == SNode::T::CList) {
    for (std::size_t i = 0; i < src->kids().size(); ++i) {
      out.elements.emplace_back(src->keys()[i], Binder::node(src->kids()[i]));
    }
    return out;
  }
  if (src->t() == SNode::T::Var) {
    if (const Binder* bound = binder(src->s())) {
      switch (bound->shape()) {
        case Binder::Shape::Node:
          return classify(bound->as_node());
        case Binder::Shape::None:
          refuse("E_SQL_SHAPE", bound->reason(), src->pos());
        // A bucket's members are iterated by COUNT and SUM alone (call()), as
        // one aggregate over the grouped rows; ALL, ANY and the rest would
        // each need a correlated subquery this layer does not build.
        case Binder::Shape::Group:
          refuse("E_SQL_SHAPE",
                 src->s() + " is the list of a bucket's members, over which only "
                            "COUNT and SUM are translated",
                 src->pos());
        case Binder::Shape::Projected:
          refuse("E_SQL_SHAPE",
                 src->s() + " is the record the projection built, a map with one "
                            "child per field; SQL has no way to iterate or count that",
                 src->pos());
        case Binder::Shape::Row:
          if (bound->as_row().fields.size() > 1) {
            refuse("E_SQL_SHAPE",
                   src->s() + " is a row of a multi-field relation, which is a "
                              "map with one child per field; SQL has no way to "
                              "iterate or count that",
                   src->pos());
          }
          break;
        case Binder::Shape::Column:
          break;
        // The key of the group being rendered is one value, like a column:
        // the scalar rule below. Spelled out so -Wswitch can see every shape.
        case Binder::Shape::Key:
          break;
      }
      // A COLUMN, a KEY, or a ROW with exactly one field: the scalar rule.
      out.elements.emplace_back("1", *bound);
      out.scalar_rule = true;
      return out;
    }
    const Binding& b = bindings_.get(src->s(), src->pos());
    if (b.kind() == Binding::Kind::Relation) {
      out.shape = Source::Shape::Relation;
      out.relation = std::make_shared<RelationSpec>(b.as_relation());
      return out;
    }
    if (b.kind() == Binding::Kind::Columns) {
      out.shape = Source::Shape::Columns;
      const auto& items = b.as_columns();
      for (std::size_t i = 0; i < items.size(); ++i) {
        out.elements.emplace_back(std::to_string(i + 1), Binder::column(items[i]));
      }
      return out;
    }
    if (b.kind() == Binding::Kind::Value) {
      const Value& v = b.as_value();
      out.elements = value_elements(b, src->pos());
      // A scalar value gets the scalar rule; a list does not, and an empty NONE
      // gets no elements and no scalar rule.
      out.scalar_rule = v.size() == 0 && !v.is_none();
      return out;
    }
    // A plain `column` binding falls through, exactly as in Python.
  }
  // The four text functions that yield a list, the constructors, and every
  // pipeline step -- the optimiser's vocabulary, so a new step is covered by
  // being one (review 2026-09-15 finding X: COUNT(LIST(1, 2, 3)) was 0).
  if (src->t() == SNode::T::Call &&
      (contains(YIELDS_LIST, src->s()) || src->s() == "LIST" || src->s() == "RECORD" ||
       sel::is_pipeline_op(src->s()))) {
    refuse("E_SQL_SHAPE",
           src->s() + " yields a list, and the scalar rule does not apply to "
                      "it; SQL has no way to count or index what it produces",
           src->pos());
  }
  // And a call is one value only once it has rendered as one: COUNT folds a
  // scalar to 0 without rendering it, and IF(TRUE, LIST(1, 2), 3) is a list
  // SEL counts as 2 -- a refusal, not a 0.
  if (src->t() == SNode::T::Call) node(src);
  // The scalar rule for any other expression node.
  out.elements.emplace_back("1", Binder::node(src));
  out.scalar_rule = true;
  return out;
}

Fragment Translator::with_element(const Source& src, const std::string& binder_name,
                                  const Binder& elem, const std::string& key,
                                  const SNode& n,
                                  const std::function<Fragment()>& render) {
  std::vector<std::pair<std::string, Binder>> frame;
  frame_set(frame, binder_name, elem);
  // `_K` names a TEXT literal of the element's key, which becomes a parameter
  // slot when rendered.
  frame_set(frame, "_K",
            Binder::node(SNode::leaf(lit_node(NT::Text, key, false, n.pos()))));
  // Every absorbed FILTER's binder names the SAME element, which is what makes
  // absorption three lines rather than a substitution pass.
  for (const Filter& f : src.filters) frame_set(frame, f.binder, elem);

  frames_.push_back(std::move(frame));
  struct Pop {
    std::vector<Frame>* f;
    ~Pop() { f->pop_back(); }
  } pop{&frames_};
  return render();
}

Fragment Translator::with_row(const Source& src, const std::string& binder_name,
                              const std::function<Fragment()>& render) {
  const std::string alias = relation_alias(*src.relation);
  // A relation nested inside itself reuses its own fixed alias, and the inner
  // FROM shadows the outer one, so the predicate is constantly false and every
  // server answered [] where SEL answers rows. Bindings::check_aliases dedupes
  // across DISTINCT binding names, and this is one name, so it could never fire.
  for (const Frame& frame : frames_) {
    for (const auto& [k, b] : frame) {
      (void)k;
      if (b.shape() == Binder::Shape::Row &&
          relation_alias(b.as_row()) == alias) {
        // No position: the source record carries none in any host, so this
        // reports 0:0. Reproduced rather than improved -- case files assert it.
        refuse("E_SQL_SHAPE",
               "this relation is already open as " + alias +
                   " further out, and a subquery reusing its own alias shadows "
                   "the outer row rather than comparing against it; the "
                   "correlation names the alias, so it cannot be renamed here",
               {});
      }
    }
  }
  Binder row = Binder::row(src.relation);
  // The row of a joined statement: a field read through it resolves across
  // the sides (ambiguous when both have it), whatever the binder is called.
  // Gating that on the name `_` let `MAP(r, RECORD("name", r["name"]))`
  // after a LINK resolve to the left side where `run()` raises E_NO_KEY.
  if (statement_plan_ && !statement_plan_->joins.empty()) row.set_joined(true);
  std::vector<std::pair<std::string, Binder>> frame;
  frame_set(frame, binder_name, row);
  frame_set(frame, "_K",
            Binder::none("a row of a relation has no key: SQL rows are "
                         "unordered and unkeyed unless the schema says "
                         "otherwise, and guessing which column is the key is "
                         "not something this layer does"));
  for (const Filter& f : src.filters) frame_set(frame, f.binder, row);
  // After a LINK only the row is in scope (spec §7.4): the binders are scoped
  // to its predicate, and the evaluator raises E_UNDEF_VAR for `C["id"]` in
  // a later step -- the joined row carries them as keys, not as names. This
  // frame used to bind `_1`, `_2`, the relations' names and both binders of
  // every join for every later step, so `FILTER(C["id"] > 1)` translated
  // where `run()` fails (review 2026-09-15 finding W2).

  frames_.push_back(std::move(frame));
  struct Pop {
    std::vector<Frame>* f;
    ~Pop() { f->pop_back(); }
  } pop{&frames_};
  return render();
}

// The frame for a bucket's own body: the projection, and a FILTER or a sort
// over the groups before it. The binder is the group -- the list of its
// members, which only COUNT and SUM read (call()) -- and `_K` is the group
// key, when there is one key to be it. This is the one place `_K` is a group
// key: before the bucket it is a source row's position, after the projection
// the projected row's, and SQL has neither (review 2026-09-15 finding K).
Fragment Translator::with_group(const Source& src, const std::string& binder_name,
                                const std::function<Fragment()>& render) {
  std::vector<std::pair<std::string, Binder>> frame;
  frame_set(frame, binder_name, Binder::group(src.relation));
  if (statement_plan_ && statement_plan_->group_by && statement_plan_->group_by->size() == 1) {
    const RelationalGroup& gb = (*statement_plan_->group_by)[0];
    frame_set(frame, "_K", Binder::key(gb.binder, gb.node, src.relation));
  } else {
    frame_set(frame, "_K",
              Binder::none("the key of a bucket over several keys is a list, which "
                           "SQL has no value for; name one key"));
  }
  frames_.push_back(std::move(frame));
  struct Pop {
    std::vector<Frame>* f;
    ~Pop() { f->pop_back(); }
  } pop{&frames_};
  return render();
}

// The frame for a step after a bucket's projection: a FILTER (HAVING) or a
// sort over the projected rows. The binder is the record the projection
// built, whose fields are the projection's aliases; `_K` is its position in
// the renumbered list, which SQL does not have.
Fragment Translator::with_projected(const Source& src, const std::string& binder_name,
                                    const std::function<Fragment()>& render) {
  auto projections = std::make_shared<const std::vector<RelationalProjection>>(
      statement_plan_ && statement_plan_->projections ? *statement_plan_->projections
                                                        : std::vector<RelationalProjection>{});
  std::vector<std::pair<std::string, Binder>> frame;
  frame_set(frame, binder_name, Binder::projected(src.relation, std::move(projections)));
  frame_set(frame, "_K",
            Binder::none("after a projection the rows are a list renumbered from \"1\", "
                         "and SQL has no row position to compare against"));
  frames_.push_back(std::move(frame));
  struct Pop {
    std::vector<Frame>* f;
    ~Pop() { f->pop_back(); }
  } pop{&frames_};
  return render();
}

Fragment Translator::with_join_binders(const RelationalPlan& plan,
                                       const RelationalJoin& join,
                                       const std::function<Fragment()>& render) {
  std::vector<std::pair<std::string, Binder>> frame;
  const Binder left = Binder::row(std::make_shared<RelationSpec>(plan.source_relation));
  const Binder right = Binder::row(std::make_shared<RelationSpec>(join.source_relation));
  frame_set(frame, "_", left);
  frame_set(frame, "_1", left);
  frame_set(frame, plan.source_name, left);
  if (plan.source_alias) frame_set(frame, *plan.source_alias, left);
  frame_set(frame, "_2", right);
  frame_set(frame, join.left_binder, left);
  frame_set(frame, join.right_binder, right);
  frame_set(frame, join.source_name, right);
  if (join.source_alias) frame_set(frame, *join.source_alias, right);
  frames_.push_back(std::move(frame));
  struct Pop {
    std::vector<Frame>* f;
    ~Pop() { f->pop_back(); }
  } pop{&frames_};
  return render();
}

Fragment Translator::agg_body(const std::string& name, const SNodePtr& body,
                              const Source& src, const SNode& n) {
  Fragment q = node(body);
  if (name == "SUM") require_num(q, body->pos(), name);
  else require_bool(q, body->pos(), name);

  // Filters are innermost-first and each wraps the accumulator, so the
  // OUTERMOST filter ends up the OUTERMOST wrapper.
  //
  // The body is rendered, and its literals numbered, BEFORE each predicate --
  // but every rewrite EMITS the predicate first. Slot ids are creation-ordered
  // and bindings() walks the text, so the two orders differ here by
  // construction.
  for (const Filter& f : src.filters) {
    const Fragment p = require_bool(node(f.body), f.body->pos(), "FILTER");
    if (name == "SUM") {
      q = case_when(p, q, literal(Value::num("0"), SqlKind::Num), n.pos());
    } else if (name == "ALL") {
      // (NOT p) OR q, and not an implication or a CASE: the exact shape is what
      // makes it NULL-safe.
      const Fragment one[] = {p};
      const Fragment pair[] = {apply(Section::Ops, "NOT", one, n.pos()), q};
      q = apply(Section::Ops, "OR", pair, n.pos());
    } else {
      const Fragment pair[] = {p, q};
      q = apply(Section::Ops, "AND", pair, n.pos());
    }
  }
  return q;
}

Fragment Translator::relation_aggregate(const std::string& name,
                                        const RelationSpec& rel,
                                        const Fragment& body, const SNode& n) {
  const bool is_separate = (rel.prefilter && *rel.prefilter == "separate") ||
                           (!rel.prefilter && body.separate_prefilter());
  if (name == "ANY" && body.prefilter() && is_separate) {
    SlotMap pre_slots =
        merge_slots(relation_slots(rel), SlotMap{{"body", {Slot{*body.prefilter()}}}});
    Fragment pre = Fragment(
        fill_named(skeleton("prefilter", n.pos()), pre_slots, n.pos()),
        agg_returns(name), dialect_);
    SlotMap main_slots =
        merge_slots(relation_slots(rel), SlotMap{{"body", {Slot{body}}}});
    Fragment main = Fragment(
        fill_named(skeleton(std::string(agg_skeleton(name)), n.pos()), main_slots, n.pos()),
        agg_returns(name), dialect_);
    const Fragment pair[] = {pre, main};
    return apply(Section::Ops, "AND", pair, n.pos());
  }
  SlotMap slots =
      merge_slots(relation_slots(rel), SlotMap{{"body", {Slot{body}}}});
  return Fragment(
      fill_named(skeleton(std::string(agg_skeleton(name)), n.pos()), slots, n.pos()),
      agg_returns(name), dialect_);
}

Fragment Translator::aggregate(const SNode& n) {
  const std::string name = n.s();
  if (name == "MAP" || name == "FILTER") {
    refuse("E_SQL_SHAPE",
           name + " yields a list, and a SQL expression is a scalar; it can only "
                  "be the thing another aggregate iterates",
           n.pos());
  }
  if (name == "JOIN") return join_aggregate(n);

  // Refuses a non-bare binder BEFORE the source is classified, so
  // ALL(UNBOUND, (C), p) is E_SQL_SHAPE and not E_SQL_UNBOUND.
  const AggShape shape = agg_shape(n);
  const Source src = classify(n.kids()[0]);

  if (src.shape == Source::Shape::Relation) {
    const Fragment rendered = with_row(src, shape.binder, [&] {
      return agg_body(name, shape.body, src, n);
    });
    return relation_aggregate(name, *src.relation, rendered, n);
  }

  std::vector<Fragment> parts;
  for (const auto& [key, elem] : src.elements) {
    parts.push_back(with_element(src, shape.binder, elem, key, n, [&] {
      return agg_body(name, shape.body, src, n);
    }));
  }
  // Spec §7.3's empty cases.
  if (parts.empty()) {
    if (name == "ALL") return literal(Value::boolean(true), SqlKind::Bool);
    if (name == "ANY") return literal(Value::boolean(false), SqlKind::Bool);
    return literal(Value::num("0"), SqlKind::Num);
  }
  // Unwrapped: no fold and no parentheses. A port that always folds emits extra
  // parentheses and breaks the byte-exact cases.
  if (parts.size() == 1) return parts[0];
  return fold_pairwise(std::string(agg_fold(name)), parts, n.pos());
}

Fragment Translator::count(const SNode& n) {
  const Source src = classify(n.kids()[0]);

  // THE SCALAR RULE IS INVERTED FOR COUNT: spec §7.4 says a value with no
  // children counts 0, where §7.3's one-element rule is about what an aggregate
  // ITERATES. So COUNT of a column, of a scalar value binding, of a one-field
  // relation row, or of any other scalar expression is 0.
  if (src.shape != Source::Shape::Relation && src.scalar_rule && src.filters.empty()) {
    return literal(Value::num("0"), SqlKind::Num);
  }

  if (!src.filters.empty()) {
    // COUNT(FILTER(L, p)) is SUM(L, CASE WHEN p THEN 1 ELSE 0 END).
    const SNodePtr body = SNode::leaf(lit_node(NT::Num, "1", false, n.pos()));
    if (src.shape == Source::Shape::Relation) {
      const Fragment rendered = with_row(src, "_", [&] {
        return agg_body("SUM", body, src, n);
      });
      return relation_aggregate("SUM", *src.relation, rendered, n);
    }
    std::vector<Fragment> parts;
    for (const auto& [key, elem] : src.elements) {
      parts.push_back(with_element(src, "_", elem, key, n, [&] {
        return agg_body("SUM", body, src, n);
      }));
    }
    if (parts.empty()) return literal(Value::num("0"), SqlKind::Num);
    if (parts.size() == 1) return parts[0];
    return fold_pairwise("+", parts, n.pos());
  }

  if (src.shape == Source::Shape::Relation) {
    // The count skeleton takes only {from} and {corr} -- no {body} -- so there
    // is nothing to merge. This path pushes NO frame, and so skips with_row's
    // self-nesting check: an asymmetry the other hosts have too.
    return Fragment(fill_named(skeleton("count", n.pos()),
                               relation_slots(*src.relation), n.pos()),
                    SqlKind::Num, dialect_);
  }
  // Decided at translation time.
  return literal(Value::num(std::to_string(src.elements.size())), SqlKind::Num);
}

Fragment Translator::has(const SNode& n) {
  const SNodePtr key_node = n.kids()[1];
  // Runs BEFORE the source is classified, so HAS(UNBOUND, SOMEVAR) is
  // E_SQL_SHAPE and not E_SQL_UNBOUND.
  if (key_node->t() != SNode::T::Text && key_node->t() != SNode::T::Num) {
    refuse("E_SQL_SHAPE",
           "HAS needs a constant key here: which column it asks about has to be "
           "known before the query runs",
           key_node->pos());
  }
  const std::string key = key_node->s();
  const Source src = classify(n.kids()[0]);
  if (!src.filters.empty()) {
    refuse("E_SQL_SHAPE",
           "HAS over a FILTER would have to know at translation time which "
           "elements the filter kept",
           n.pos());
  }
  if (src.shape == Source::Shape::Relation) {
    refuse("E_SQL_SHAPE",
           "HAS over a relation asks whether it has a key, and a relation is a "
           "list of rows whose keys are positions; the answer needs the row "
           "count, which no expression here knows",
           n.pos());
  }
  // A scalar always answers FALSE -- a scalar has no children -- even though
  // its element map carries the synthetic key "1". Exact string match: no case
  // folding and no numeric normalisation.
  bool found = false;
  if (!src.scalar_rule) {
    for (const auto& [k, elem] : src.elements) {
      (void)elem;
      if (k == key) { found = true; break; }
    }
  }
  return literal(Value::boolean(found), SqlKind::Bool);
}

Fragment Translator::join_aggregate(const SNode& n) {
  const Source src = classify(n.kids()[0]);

  if (src.shape == Source::Shape::Relation) {
    const RelationSpec& rel = *src.relation;
    const ColumnSpec* scalar =
        rel.scalar ? rel.field(ascii_upper(*rel.scalar)) : nullptr;
    // Before the skeleton, so a no-scalar relation reports this rather than the
    // dialect's join refusal.
    if (!scalar) {
      refuse("E_SQL_SHAPE",
             "JOIN over a relation needs the binding to name a \"scalar\" field",
             n.pos());
    }
    const Fragment body = column_ref(*scalar);
    const std::string skel = skeleton("join", n.pos());
    // The separator is rendered only after the skeleton is known to exist.
    SlotMap slots = merge_slots(
        relation_slots(rel),
        SlotMap{{"body", {Slot{body}}}, {"sep", {Slot{node(n.kids()[1])}}}});
    return Fragment(fill_named(skel, slots, n.pos()), SqlKind::Text, dialect_);
  }

  std::vector<Fragment> parts;
  for (const auto& [key, elem] : src.elements) {
    // Rendered afresh per gap, never spliced twice: N-1 separators means N-1
    // identical bound values, which is correct.
    if (!parts.empty()) parts.push_back(node(n.kids()[1]));
    const Binder held = elem;
    parts.push_back(with_element(src, "_", held, key, n, [&] {
      return from_binder(held, n);
    }));
  }
  if (parts.empty()) return literal(Value::text(""), SqlKind::Text);
  if (parts.size() == 1) return parts[0];
  // The dialect's own concatenation template, pairwise-left, because `&` is
  // what SEL's JOIN is; a variadic concat would need a lexical key spelled two
  // ways for one function.
  return fold_pairwise("&", parts, n.pos());
}

// --- relational pipeline statement compiler ---------------------------------

// The optimizer's list (sel_ast.hpp), not a second copy: one vocabulary of
// pipeline operators per host, or the planner and the translator drift apart.
bool pipeline_op_name(std::string_view name) { return sel::is_pipeline_op(name); }

// Whether a MAP must wrap the plan first. An ORDER BY alone does not: the
// projection and the sort can share one statement (ORDER BY may name the
// input's columns), and a derived table is where MariaDB DROPS an ORDER BY
// that has no LIMIT beside it -- the statement oracle's sorted rows came back
// in table order. Everything else above the rows still wraps.
bool Translator::plan_needs_wrap_before_map(const RelationalPlan& plan) const {
  return plan.projections.has_value() || plan.select_cols.has_value() ||
         plan.group_by.has_value() || plan.distinct || plan.limit.has_value() ||
         plan.offset.has_value();
}

bool Translator::plan_has_rows_above(const RelationalPlan& plan) const {
  return plan.projections.has_value() || plan.select_cols.has_value() ||
         plan.group_by.has_value() || plan.distinct || plan.limit.has_value() ||
         plan.offset.has_value() || !plan.order_by.empty();
}

// The fields of a joined row that SQL can carry (spec §7.4 "Joined rows"):
// the promoted ones -- a side's fields whose names, compared
// ASCII-case-insensitively, do not occur on the other side -- accumulated
// join by join as the evaluator promotes them. The binders (`_1`, `_2`, the
// relations' names) are nested records with no column, and a name both
// sides carry is E_NO_KEY in SEL; neither is projected, so a read of either
// over the derived table is refused where `run()` raises. `SELECT o.*` was
// the row before: the left table's columns, which a continuation read where
// SEL has no key, and which made a derived table over a join name columns
// it did not have (finding Y, lanes).
struct JoinedRowField {
  std::string name;          // ASCII-upper, as the relation keys it
  const ColumnSpec* spec;
  const RelationSpec* owner;
};

std::vector<JoinedRowField> joined_row_fields(const RelationalPlan& plan) {
  const auto entries = [](const RelationSpec& rel) {
    std::vector<JoinedRowField> out;
    for (const auto& [name, spec] : rel.fields) out.push_back({name, &spec, &rel});
    return out;
  };
  const auto names_of = [](const std::vector<JoinedRowField>& fields) {
    std::set<std::string> out;
    for (const JoinedRowField& f : fields) out.insert(ascii_upper(f.name));
    return out;
  };
  std::vector<JoinedRowField> acc = entries(plan.source_relation);
  for (const RelationalJoin& join : plan.joins) {
    const std::vector<JoinedRowField> right = entries(join.source_relation);
    const std::set<std::string> left_names = names_of(acc);
    const std::set<std::string> right_names = names_of(right);
    std::vector<JoinedRowField> next;
    for (const JoinedRowField& f : acc) {
      if (!right_names.count(ascii_upper(f.name))) next.push_back(f);
    }
    for (const JoinedRowField& f : right) {
      if (!left_names.count(ascii_upper(f.name))) next.push_back(f);
    }
    acc = std::move(next);
  }
  return acc;
}

std::vector<std::string> Translator::output_field_names(const RelationalPlan& plan) const {
  std::vector<std::string> names;
  if (plan.projections) {
    for (std::size_t i = 0; i < plan.projections->size(); ++i) {
      const RelationalProjection& projection = (*plan.projections)[i];
      if (projection.alias) {
        names.push_back(*projection.alias);
      } else if (projection.node && projection.node->t() == SNode::T::Index &&
                 projection.node->r()->t() == SNode::T::Text) {
        names.push_back(projection.node->r()->s());
      } else {
        names.push_back("expr" + std::to_string(i + 1));
      }
    }
  } else if (plan.select_cols) {
    names = *plan.select_cols;
  } else if (!plan.joins.empty()) {
    for (const JoinedRowField& f : joined_row_fields(plan)) names.push_back(f.name);
  } else {
    for (const auto& [name, ignored] : plan.source_relation.fields) {
      (void)ignored;
      names.push_back(name);
    }
  }
  std::vector<std::string> unique;
  std::set<std::string> seen;
  for (const std::string& name : names) {
    const std::string upper = ascii_upper(name);
    if (seen.insert(upper).second) unique.push_back(name);
  }
  return unique;
}

SqlKind Translator::output_field_type(const RelationalPlan& plan, std::string name) const {
  // Computed projections remain UNKNOWN; only direct reads retain a type.
  if (plan.projections) {
    const RelationalProjection* projection = nullptr;
    for (const auto& p : *plan.projections) {
      if (p.alias && *p.alias == name) { projection = &p; break; }
    }
    const SNodePtr n = projection ? projection->node : nullptr;
    if (!n || n->t() != SNode::T::Index || n->l()->t() != SNode::T::Var ||
        n->l()->s() != projection->binder || n->r()->t() != SNode::T::Text) return SqlKind::Unknown;
    name = n->r()->s();
  }
  const ColumnSpec* match = plan.source_relation.field(ascii_upper(name));
  for (const auto& join : plan.joins) {
    if (const auto* field = join.source_relation.field(ascii_upper(name))) {
      if (match) return SqlKind::Unknown;
      match = field;
    }
  }
  return match && !match->guard && !match->is_raw ? match->type : SqlKind::Unknown;
}

// The kind of a projected CANON(...), which a derived table's column keeps: the
// dialect's CANON kind -- the map entry's ret, NUM or TEXT.
std::optional<SqlKind> Translator::output_canon_kind(const RelationalPlan& plan,
                                                     const std::string& name) const {
  if (!plan.projections) return std::nullopt;
  const RelationalProjection* projection = nullptr;
  for (const auto& p : *plan.projections) {
    if (p.alias && *p.alias == name) { projection = &p; break; }
  }
  const SNodePtr n = projection ? projection->node : nullptr;
  if (!n || n->t() != SNode::T::Call || n->s() != "CANON") return std::nullopt;
  const Entry* entry = Map::entry(dialect_, Section::Funcs, "CANON");
  if (!entry || entry->kind != EntryKind::Template) return std::nullopt;
  return kind_from_name(entry->ret);
}

RelationalPlan Translator::ensure_derived(RelationalPlan plan, bool needed) {
  if (!needed) return plan;
  const std::string alias = "_sub" + std::to_string(++subquery_counter_);
  auto inner = std::make_shared<RelationalPlan>(std::move(plan));

  RelationalPlan derived;
  derived.source_name = alias;
  derived.source_table = "";
  derived.source_alias = alias;
  derived.source_relation.from = alias;
  derived.source_relation.alias = alias;
  derived.source_subquery = std::move(inner);
  if (derived.source_subquery->bucket != RelationalPlan::Bucket::None) {
    derived.bucket = RelationalPlan::Bucket::Sealed;
  }

  const RelationalPlan& subquery = *derived.source_subquery;
  for (const std::string& name : output_field_names(subquery)) {
    // A `SELECT *` passes the source's columns through under their own
    // names; a projection names its columns by alias.
    const ColumnSpec* source_field = nullptr;
    if (!subquery.projections && !subquery.select_cols) {
      source_field = subquery.source_relation.field(ascii_upper(name));
      for (std::size_t i = 0; !source_field && i < subquery.joins.size(); ++i) {
        source_field = subquery.joins[i].source_relation.field(ascii_upper(name));
      }
    }
    ColumnSpec field;
    field.column = source_field ? source_field->column : name;
    field.table = alias;
    field.type = output_field_type(subquery, name);
    if (const std::optional<SqlKind> canon_kind = output_canon_kind(subquery, name)) {
      field.type = *canon_kind;
      field.canonical = true;
    }
    derived.source_relation.fields.emplace_back(ascii_upper(name), std::move(field));
  }
  return derived;
}

void Translator::bucket_projection(RelationalPlan& plan, const std::string& binder,
                                   const SNodePtr& agg_node) {
  if (agg_node) {
    if (agg_node->t() == SNode::T::Call && agg_node->s() == "RECORD") {
      std::vector<RelationalProjection> projections;
      for (const auto& [alias, v_node] : record_fields(agg_node)) {
        SNodePtr actual_node = v_node;
        // _K is the key, which was written against the KEY's binder -- the MAP
        // spelling may name the group differently, so the projection keeps the
        // binder the key node was written for.
        // It is rendered as the group key itself (group_key).
        std::string node_binder = binder;
        std::shared_ptr<const RelationalGroup> group_key;
        if (v_node->t() == SNode::T::Var && v_node->s() == "_K" && plan.group_by->size() == 1) {
          actual_node = (*plan.group_by)[0].node;
          node_binder = (*plan.group_by)[0].binder;
          group_key = std::make_shared<const RelationalGroup>((*plan.group_by)[0]);
        }
        projections.push_back({alias, node_binder, actual_node, group_key});
      }
      plan.projections = std::move(projections);
    } else {
      std::vector<RelationalProjection> projections;
      projections.push_back({std::nullopt, binder, agg_node, {}});
      plan.projections = std::move(projections);
    }
  } else {
    std::vector<RelationalProjection> projections;
    for (const auto& gb : *plan.group_by) {
      projections.push_back({gb.alias, gb.binder, gb.node,
                             std::make_shared<const RelationalGroup>(gb)});
    }
    plan.projections = std::move(projections);
  }
  plan.select_cols = std::nullopt;
}

std::optional<RelationalPlan> Translator::analyze_pipeline(const SNodePtr& ast) {
  if (identity_loss_before_grouping(ast)) {
    refuse("E_SQL_SHAPE", "grouping depends on a computed projection without identity preservation", ast->pos());
  }
  std::vector<SNodePtr> steps;
  SNodePtr curr = ast;

  while (curr && curr->t() == SNode::T::Call && pipeline_op_name(curr->s()) && !curr->kids().empty()) {
    steps.push_back(curr);
    curr = curr->kids()[0];
  }

  if (!curr || curr->t() != SNode::T::Var) {
    return std::nullopt;
  }

  if (!bindings_.has(curr->s())) {
    return std::nullopt;
  }

  const Binding& b = bindings_.get(curr->s());
  if (b.kind() != Binding::Kind::Relation) {
    return std::nullopt;
  }

  RelationalPlan plan;
  plan.source_name = curr->s();
  const RelationSpec& rel = b.as_relation();
  plan.source_relation = rel;
  plan.source_from_raw = rel.from_is_raw;
  plan.source_table = rel.from;
  plan.source_alias = rel.alias;
  plan.correlate = rel.correlate;

  std::reverse(steps.begin(), steps.end());

  for (const auto& step : steps) {
    const std::string& name = step->s();
    const auto& args = step->kids();

    // A FILTER after an open bucket is a HAVING and a MAP is the bucket's
    // projection; anything else spends the members. See RelationalPlan.
    // Either way a step written directly after the bare bucket runs over its
    // groups, and is rendered in the bucket's own frame (with_group).
    const bool over_groups = plan.bucket == RelationalPlan::Bucket::Open;
    if (plan.bucket == RelationalPlan::Bucket::Open && name != "FILTER" && name != "MAP") {
      plan.bucket = RelationalPlan::Bucket::Sealed;
    }

    if (name == "FILTER") {
      // A FILTER over a bare bucket whose members are spent: SQL has only the
      // keys left, and SEL's value is still a map of groups.
      if (plan.bucket == RelationalPlan::Bucket::Sealed) {
        refuse("E_SQL_SHAPE",
               "a FILTER over buckets must follow the BUCKET directly: SQL keeps a "
               "bucket's members only for the projection that ends the grouping",
               step->pos());
      }
      // A FILTER after a LIMIT or OFFSET is a WHERE over the rows that
      // survived them, grouped or not -- SEL applies the TAKE first, and a
      // HAVING would run before it. Otherwise a FILTER directly after a
      // grouping is its HAVING, and an ORDER BY in between changes nothing
      // (HAVING then ORDER BY is sort-then-filter's rows).
      const bool need_derived =
          plan.limit.has_value() || plan.offset.has_value() ||
          (!plan.group_by.has_value() &&
           (plan.projections.has_value() || plan.select_cols.has_value() ||
            plan.distinct || !plan.order_by.empty()));
      plan = ensure_derived(std::move(plan), need_derived);
      std::string binder;
      SNodePtr pred;
      if (args.size() == 2) {
        binder = "_";
        pred = args[1];
      } else if (args.size() == 3) {
        if (!is_binder_name(*args[1])) {
          refuse("E_SQL_SHAPE", "the binder of FILTER must be a bare name", args[1]->pos());
        }
        binder = args[1]->s();
        pred = args[2];
      } else {
        refuse("E_ARITY", "FILTER takes 2 or 3 arguments", step->pos());
      }
      if (plan.group_by.has_value()) {
        plan.having.push_back({binder, pred, step->pos(), over_groups});
      } else {
        plan.filters.push_back({binder, pred, step->pos()});
      }
    } else if (name == "BUCKET") {
      // A bucket over a bare bucket's rows: SQL has only the keys (open) or
      // has spent the members (sealed); either way SEL's value is a map of
      // groups and re-grouping it is a different program.
      if (plan.bucket != RelationalPlan::Bucket::None) {
        refuse("E_SQL_SHAPE",
               "a BUCKET over buckets: SQL keeps a bucket's members only for the "
               "projection that ends the grouping",
               step->pos());
      }
      const bool need_derived = plan_has_rows_above(plan);
      plan = ensure_derived(std::move(plan), need_derived);
      std::string binder;
      SNodePtr key_node;
      SNodePtr agg_node = nullptr;
      if (args.size() == 2) {
        binder = "_";
        key_node = args[1];
      } else if (args.size() == 3) {
        binder = "_";
        key_node = args[1];
        agg_node = args[2];
      } else if (args.size() == 4) {
        if (!is_binder_name(*args[1])) {
          refuse("E_SQL_SHAPE", "the binder of BUCKET must be a bare name", args[1]->pos());
        }
        binder = args[1]->s();
        key_node = args[2];
        agg_node = args[3];
      } else {
        refuse("E_ARITY", "BUCKET takes 2 to 4 arguments", step->pos());
      }

      // A bare bucket's key is an index key (spec §7.4): one text or number.
      // A list or record key is refused by the evaluator, and the boolean and
      // binary kinds are refused below, once known.
      const bool several_keys =
          (key_node->t() == SNode::T::Call && (key_node->s() == "LIST" || key_node->s() == "RECORD")) ||
          key_node->t() == SNode::T::List;
      if (!agg_node && several_keys) {
        refuse("E_SQL_SHAPE",
               "a bare BUCKET groups by one text or number key, as an index does; "
               "BUCKET(src, key, proj) groups by several",
               key_node->pos());
      }
      std::vector<RelationalGroup> group_by;
      if ((key_node->t() == SNode::T::Call && key_node->s() == "LIST") || key_node->t() == SNode::T::List) {
        for (const auto& k_arg : key_node->kids()) {
          group_by.push_back({std::nullopt, binder, k_arg, k_arg->pos()});
        }
      } else if (key_node->t() == SNode::T::Call && key_node->s() == "RECORD") {
        for (const auto& [alias, value] : record_fields(key_node)) {
          group_by.push_back({alias, binder, value, value->pos()});
        }
      } else {
        group_by.push_back({std::nullopt, binder, key_node, key_node->pos()});
      }
      plan.group_by = std::move(group_by);
      plan.bucket = agg_node ? RelationalPlan::Bucket::None : RelationalPlan::Bucket::Open;
      plan.bare_key = !agg_node;
      bucket_projection(plan, binder, agg_node);
    } else if (name == "SELECT_COLS") {
      // The same rule as a MAP's: an ORDER BY alone does not wrap (a derived
      // table is where MariaDB drops an ORDER BY with no LIMIT beside it),
      // everything else above the rows does (SEL-0048).
      const bool need_derived = plan_needs_wrap_before_map(plan);
      plan = ensure_derived(std::move(plan), need_derived);
      std::vector<SNodePtr> items;
      if (args.size() == 2 && args[1]->t() == SNode::T::List) {
        items = args[1]->kids();
      } else {
        for (std::size_t i = 1; i < args.size(); ++i) {
          items.push_back(args[i]);
        }
      }
      std::vector<std::string> cols;
      for (const auto& item : items) {
        if (item->t() != SNode::T::Text) {
          refuse("E_BAD_ARG", "SELECT_COLS column names must be string literals", item->pos());
        }
        const std::string& col = item->s();
        const std::string uc = ascii_upper(col);
        int matches = plan.source_relation.field(uc) ? 1 : 0;
        for (const RelationalJoin& join : plan.joins) {
          if (join.source_relation.field(uc)) ++matches;
        }
        if (matches > 1) {
          refuse("E_SQL_SHAPE",
                 "column '" + col +
                     "' is ambiguous across joined tables; qualify with a table alias",
                 item->pos());
        }
        if (!plan.source_relation.fields.empty() && matches == 0) {
          std::string declared;
          for (std::size_t i = 0; i < plan.source_relation.fields.size(); ++i) {
            if (i > 0) declared += ", ";
            declared += plan.source_relation.fields[i].first;
          }
          refuse("E_SQL_SHAPE",
                 "relation " + plan.source_name + " has no field '" + col +
                     "'; the relation declares " + declared,
                 item->pos());
        }
        cols.push_back(col);
      }
      plan.select_cols = std::move(cols);
      plan.projections = std::nullopt;
    } else if (name == "MAP") {
      if (plan.bucket == RelationalPlan::Bucket::Sealed) {
        refuse("E_SQL_SHAPE",
               "a MAP over buckets must follow the BUCKET, with at most a FILTER "
               "between: SQL keeps a bucket's members only for the projection that "
               "ends the grouping",
               step->pos());
      }
      std::string binder;
      SNodePtr expr;
      if (args.size() == 2) {
        binder = "_";
        expr = args[1];
      } else if (args.size() == 3) {
        if (!is_binder_name(*args[1])) {
          refuse("E_SQL_SHAPE", "the binder of MAP must be a bare name", args[1]->pos());
        }
        binder = args[1]->s();
        expr = args[2];
      } else {
        refuse("E_ARITY", "MAP takes 2 or 3 arguments", step->pos());
      }
      // BUCKET(src, key) .> MAP(proj) is BUCKET(src, key, proj): the MAP's body
      // is evaluated once per group, so it is the bucket's projection.
      if (plan.bucket == RelationalPlan::Bucket::Open) {
        plan.bucket = RelationalPlan::Bucket::None;
        bucket_projection(plan, binder, expr);
        continue;
      }
      const bool need_derived = plan_needs_wrap_before_map(plan);
      plan = ensure_derived(std::move(plan), need_derived);

      if (expr->t() == SNode::T::Call && expr->s() == "RECORD") {
        std::vector<RelationalProjection> projections;
        for (const auto& [alias, value] : record_fields(expr)) {
          projections.push_back({alias, binder, value, {}});
        }
        plan.projections = std::move(projections);
      } else {
        std::vector<RelationalProjection> projections;
        projections.push_back({std::nullopt, binder, expr, {}});
        plan.projections = std::move(projections);
      }
      plan.select_cols = std::nullopt;
    } else if (name == "DISTINCT" || name == "DEDUPE") {
      const bool need_derived = plan.limit.has_value() || plan.offset.has_value();
      plan = ensure_derived(std::move(plan), need_derived);
      if (!plan.projections && !plan.select_cols) {
        refuse("E_SQL_SHAPE", "DISTINCT requires an explicit typed projection", step->pos());
      }
      plan.distinct = true;
    } else if (name == "TAKE") {
      if (args.size() != 2) {
        refuse("E_ARITY", "TAKE takes 2 arguments", step->pos());
      }
      int64_t lim = eval_int_param(args[1], "TAKE");
      plan.limit = !plan.limit.has_value() ? lim : std::min(*plan.limit, lim);
    } else if (name == "DROP") {
      if (args.size() != 2) {
        refuse("E_ARITY", "DROP takes 2 arguments", step->pos());
      }
      int64_t off = eval_int_param(args[1], "DROP");
      // Consume the bounded slice; retain a SQL boundary for large sums.
      const int64_t skipped = plan.limit ? std::min(off, *plan.limit) : off;
      if (plan.offset.value_or(0) > 9007199254740991LL - skipped) {
        plan = ensure_derived(std::move(plan), true);
        plan.offset = off;
      } else {
        if (plan.limit) *plan.limit -= skipped;
        plan.offset = plan.offset.value_or(0) + skipped;
      }
    } else if (name == "SORT" || name == "SORT_DESC" || name == "SORT_BY" ||
               name == "TOP" || name == "TOP_DESC" || name == "TOP_BY") {
      // A sort after a LIMIT or OFFSET sorts the rows that survived them,
      // grouped or not, so those wrap; a sort over a projection or a DISTINCT
      // wraps so its key can name what they produced. A sort after a sort
      // does not wrap: the sorts are stable, so the earlier one is the later
      // one's tie-breaker, and the later one's keys go FIRST in the ORDER BY
      // (review 2026-09-15 finding V).
      const bool need_derived =
          plan.limit.has_value() || plan.offset.has_value() ||
          (!plan.group_by.has_value() &&
           (plan.projections.has_value() || plan.select_cols.has_value() || plan.distinct));
      plan = ensure_derived(std::move(plan), need_derived);
      const std::size_t before = plan.order_by.size();
      analyze_sort_step(step, plan);
      std::vector<RelationalOrder> added(plan.order_by.begin() + static_cast<std::ptrdiff_t>(before),
                                         plan.order_by.end());
      for (auto& entry : added) entry.over_groups = over_groups;
      plan.order_by.erase(plan.order_by.begin() + static_cast<std::ptrdiff_t>(before), plan.order_by.end());
      added.insert(added.end(), plan.order_by.begin(), plan.order_by.end());
      plan.order_by = std::move(added);
    } else if (name == "LINK" || name == "LINK_LEFT") {
      const bool need_derived = plan_has_rows_above(plan);
      plan = ensure_derived(std::move(plan), need_derived);
      if (args.size() != 3 && args.size() != 5) {
        refuse("E_ARITY", name + " takes 3 or 5 arguments", step->pos());
      }
      const SNodePtr& right_node = args[1];
      if (right_node->t() != SNode::T::Var || !bindings_.has(right_node->s())) {
        refuse("E_SQL_SHAPE", name + " requires a bound relation as its right side",
               right_node->pos());
      }
      const Binding& right_binding = bindings_.get(right_node->s(), right_node->pos());
      if (right_binding.kind() != Binding::Kind::Relation) {
        refuse("E_SQL_SHAPE", right_node->s() + " is not bound as a relation",
               right_node->pos());
      }
      RelationalJoin join;
      join.type = name == "LINK_LEFT" ? "LEFT" : "INNER";
      join.source_name = right_node->s();
      join.source_relation = right_binding.as_relation();
      join.source_from_raw = join.source_relation.from_is_raw;
      join.source_table = join.source_relation.from;
      join.source_alias = join.source_relation.alias;
      if (args.size() == 5) {
        if (!is_binder_name(*args[2]) || !is_binder_name(*args[3])) {
          refuse("E_SQL_SHAPE", "join binders must be bare names", args[2]->pos());
        }
        join.left_binder = args[2]->s();
        join.right_binder = args[3]->s();
        join.on_pred = args[4];
      } else {
        join.left_binder = plan.source_alias.value_or("_1");
        join.right_binder = join.source_alias.value_or("_2");
        join.on_pred = args[2];
      }
      if (!join.source_alias) join.source_alias = join.right_binder;
      join.pos = step->pos();
      plan.joins.push_back(std::move(join));
    }
  }

  return plan;
}

int64_t Translator::eval_int_param(const SNodePtr& n, const std::string& op) {
  NodePtr node = n->to_node();
  if (!node) {
    refuse("E_SQL_SHAPE", op + " count cannot contain dynamic lists", n->pos());
  }
  Value val;
  try {
    val = Program("", node).run(const_root_);
  } catch (const SelError& e) {
    refuse_as_sel(e, *n);
  }
  if (!val.looks_numeric() || val.is_null()) {
    refuse("E_NOT_NUM", op + " count must be a number", n->pos());
  }
  try {
    require_number(val, n->pos());
  } catch (const SelError& e) {
    refuse_as_sel(e, *n);
  }
  const std::string& s = val.scalar();
  if (s.find('.') != std::string::npos) {
    refuse("E_NOT_INT", op + " count must be an integer", n->pos());
  }
  if (!s.empty() && s[0] == '-') {
    refuse("E_RANGE", op + " count cannot be negative", n->pos());
  }
  try {
    return std::stoll(s);
  } catch (const std::exception&) {
    refuse("E_RANGE", op + " count is out of range", n->pos());
  }
}

void Translator::analyze_sort_step(const SNodePtr& step, RelationalPlan& plan) {
  const std::string& name = step->s();
  const auto& args = step->kids();
  const bool top = name == "TOP" || name == "TOP_DESC" || name == "TOP_BY";
  if (top && args.empty()) {
    refuse("E_ARITY", name + " has an invalid sort form", step->pos());
  }
  const auto count = top ? args.size() - 1 : args.size();

  if (top) {
    const int64_t limit = eval_int_param(args.back(), name);
    plan.limit = !plan.limit.has_value() ? limit : std::min(*plan.limit, limit);
  }

  if (name == "SORT" || name == "SORT_DESC" || name == "TOP" || name == "TOP_DESC") {
    std::string dir = (name == "SORT" || name == "TOP") ? "ASC" : "DESC";
    if (count == 1) {
      if (plan.source_relation.scalar) {
        const std::string& scalar_col = *plan.source_relation.scalar;
        auto shape = std::make_shared<Node>();
        shape->t = NT::Index;
        shape->pos = step->pos();
        SNodePtr var_n = SNode::leaf(lit_node(NT::Var, "_", false, step->pos()));
        SNodePtr idx_n = SNode::leaf(lit_node(NT::Text, scalar_col, false, step->pos()));
        SNodePtr index_node = SNode::rewritten(shape, {var_n, idx_n});
        plan.order_by.push_back({"_", index_node, dir, step->pos()});
        return;
      }
      if (plan.source_relation.fields.size() == 1) {
        const std::string& field_name = plan.source_relation.fields[0].first;
        auto shape = std::make_shared<Node>();
        shape->t = NT::Index;
        shape->pos = step->pos();
        SNodePtr var_n = SNode::leaf(lit_node(NT::Var, "_", false, step->pos()));
        SNodePtr idx_n = SNode::leaf(lit_node(NT::Text, field_name, false, step->pos()));
        SNodePtr index_node = SNode::rewritten(shape, {var_n, idx_n});
        plan.order_by.push_back({"_", index_node, dir, step->pos()});
        return;
      }
      refuse("E_SQL_SHAPE", "SORT on a multi-field relation requires a key expression; use SORT_BY", step->pos());
    } else if (count == 2) {
      plan.order_by.push_back({"_", args[1], dir, step->pos()});
    } else if (count == 3) {
      if (!is_binder_name(*args[1])) {
        refuse("E_SQL_SHAPE", "the binder of " + name + " must be a bare name", args[1]->pos());
      }
      plan.order_by.push_back({args[1]->s(), args[2], dir, step->pos()});
    } else {
      refuse("E_ARITY", name + " takes 1 to 3 arguments", step->pos());
    }
    return;
  }

  // SORT_BY
  std::string binder = "_";
  SNodePtr key;
  std::string dir = "ASC";
  Pos dir_pos = step->pos();

  if (count == 2) {
    binder = "_";
    key = args[1];
    dir = "ASC";
  } else if (count == 3) {
    if (args[2]->t() == SNode::T::Text) {
      binder = "_";
      key = args[1];
      dir = ascii_upper(args[2]->s());
      dir_pos = args[2]->pos();
    } else if (is_binder_name(*args[1])) {
      binder = args[1]->s();
      key = args[2];
      dir = "ASC";
    } else {
      // Neither form: the third slot is a direction the evaluator would
      // compute, and SQL cannot -- the four-argument form's refusal.
      refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args[2]->pos());
    }
  } else if (count == 4) {
    if (!is_binder_name(*args[1])) {
      refuse("E_SQL_SHAPE", "the binder of SORT_BY must be a bare name", args[1]->pos());
    }
    binder = args[1]->s();
    key = args[2];
    if (args[3]->t() != SNode::T::Text) {
      refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args[3]->pos());
    }
    dir = ascii_upper(args[3]->s());
    dir_pos = args[3]->pos();
  } else {
    refuse("E_ARITY", "SORT_BY takes 2 to 4 arguments", step->pos());
  }

  if (dir != "ASC" && dir != "DESC") {
    refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", dir_pos);
  }

  plan.order_by.push_back({binder, key, dir, step->pos()});
}

Fragment Translator::compile_statement(const RelationalPlan& plan) {
  // Do not turn RECORD writes into duplicate SQL columns or discard evaluation.
  const auto check_aliases = [](const auto& entries) {
    std::set<std::string> seen;
    if (entries) for (const auto& entry : *entries) {
      if (entry.alias && !seen.insert(ascii_upper(*entry.alias)).second)
        refuse("E_SQL_SHAPE", "duplicate or case-colliding RECORD fields require local evaluation", entry.node->pos());
    }
  };
  check_aliases(plan.projections);
  check_aliases(plan.group_by);
  const RelationalPlan* previous_plan = statement_plan_;
  statement_plan_ = &plan;
  struct ResetPlan {
    const RelationalPlan** p;
    const RelationalPlan* previous;
    ~ResetPlan() { *p = previous; }
  } reset_plan{&statement_plan_, previous_plan};

  std::vector<Fragment::Part> parts;
  auto add_sql = [&](std::string sql) {
    if (!sql.empty()) {
      Fragment::Part p;
      p.is_slot = false;
      p.sql = std::move(sql);
      parts.push_back(std::move(p));
    }
  };

  add_sql(plan.distinct ? "SELECT DISTINCT " : "SELECT ");

  Source src;
  src.shape = Source::Shape::Relation;
  src.relation = std::make_shared<RelationSpec>(plan.source_relation);
  for (const auto& f : plan.filters) {
    src.filters.push_back({f.binder, f.node});
  }

  // 1. SELECT list (Projections)
  if (plan.projections) {
    bool first = true;
    for (const auto& proj : *plan.projections) {
      if (!first) add_sql(", ");
      first = false;
      Fragment p_frag = proj.group_key
          ? group_key(src, *proj.group_key, true)
          : plan.group_by
              ? with_group(src, proj.binder, [&]() { return node(proj.node); })
              : with_row(src, proj.binder, [&]() { return node(proj.node); });
      if (plan.distinct) {
        if ((p_frag.kind() == SqlKind::Unknown || p_frag.kind() == SqlKind::Num) && !p_frag.canonical()) refuse("E_SQL_SHAPE", "DISTINCT requires proven structural output identity", proj.node->pos());
        p_frag = identity_group_key(proj.node, p_frag);
      }
      for (const auto& p : p_frag.parts()) {
        parts.push_back(p);
      }
      if (proj.alias) {
        add_sql(" AS " + emit_.ident(*proj.alias));
      }
    }
  } else if (plan.select_cols) {
    bool first = true;
    for (const auto& col : *plan.select_cols) {
      if (!first) add_sql(", ");
      first = false;
      const RelationSpec* owner = &plan.source_relation;
      const ColumnSpec* f_spec = plan.source_relation.field(ascii_upper(col));
      for (const RelationalJoin& join : plan.joins) {
        if (!f_spec) {
          f_spec = join.source_relation.field(ascii_upper(col));
          if (f_spec) owner = &join.source_relation;
        }
      }
      const std::string column = f_spec && !f_spec->column.empty() ? f_spec->column : col;
      if (plan.distinct && (!f_spec || f_spec->type == SqlKind::Unknown || f_spec->type == SqlKind::Num)) {
        refuse("E_SQL_SHAPE", "DISTINCT requires known output kinds");
      }
      std::string sql;
      if (plan.joins.empty() && !plan.source_subquery) {
        const std::string table = f_spec && !f_spec->table.empty()
                                      ? f_spec->table
                                      : (plan.source_alias ? *plan.source_alias : "");
        sql = emit_.column(table, column);
      } else {
        sql = emit_.column(relation_table_alias(*owner), column);
      }
      if (plan.distinct && f_spec && (f_spec->type == SqlKind::Text || f_spec->type == SqlKind::Num)) {
        const Fragment frag = emit_.text_operand(Fragment({{false, sql, 0}}, f_spec->type, dialect_));
        parts.insert(parts.end(), frag.parts().begin(), frag.parts().end());
        add_sql(" AS " + emit_.ident(column));
      } else add_sql(sql);
    }
  } else if (!plan.joins.empty()) {
    // A joined row is its promoted fields (spec §7.4); see joined_row_fields.
    const std::vector<JoinedRowField> fields = joined_row_fields(plan);
    if (fields.empty()) {
      const RelationalJoin& last = plan.joins.back();
      refuse("E_SQL_SHAPE",
             "the joined row has no field SQL can carry: every field is on both "
             "sides, and the binders are nested records",
             last.pos);
    }
    bool first = true;
    for (const JoinedRowField& f : fields) {
      if (!first) add_sql(", ");
      first = false;
      const std::string column = f.spec->column.empty() ? f.name : f.spec->column;
      add_sql(emit_.column(relation_table_alias(*f.owner), column));
    }
  } else {
    if (plan.source_alias && !plan.source_alias->empty()) {
      add_sql(emit_.ident(*plan.source_alias) + ".*");
    } else {
      add_sql("*");
    }
  }

  // 2. FROM clause
  add_sql(" FROM ");
  if (plan.source_subquery) {
    const Fragment subquery = compile_statement(*plan.source_subquery);
    add_sql("(");
    for (const Fragment::Part& p : subquery.parts()) parts.push_back(p);
    add_sql(")");
    if (plan.source_alias && !plan.source_alias->empty()) {
      add_sql(" " + emit_.ident(*plan.source_alias));
    }
  } else {
    std::string from = plan.source_from_raw ? plan.source_table : emit_.ident(plan.source_table);
    if (plan.source_alias && !plan.source_alias->empty()) {
      from += " " + emit_.ident(*plan.source_alias);
    }
    add_sql(from);
  }

  // Joins are emitted before WHERE so a predicate that references both sides
  // is rendered in the ON frame, where each binder resolves to its own table
  // alias.  This is also what keeps a left join's unmatched rows from being
  // accidentally filtered by an ON condition moved into WHERE.
  for (const RelationalJoin& join : plan.joins) {
    add_sql(join.type == "LEFT" ? " LEFT JOIN " : " INNER JOIN ");
    std::string right = join.source_from_raw ? join.source_table : emit_.ident(join.source_table);
    if (join.source_alias && !join.source_alias->empty()) {
      right += " " + emit_.ident(*join.source_alias);
    }
    add_sql(right + " ON ");
    const Fragment on = with_join_binders(plan, join, [&]() {
      return require_bool(node(join.on_pred), join.pos, "LINK");
    });
    for (const Fragment::Part& p : on.parts()) parts.push_back(p);
  }

  // 3. WHERE clause
  std::vector<std::vector<Fragment::Part>> cond_parts;
  if (plan.correlate && !plan.correlate->empty()) {
    Fragment::Part cp;
    cp.is_slot = false;
    cp.sql = *plan.correlate;
    cond_parts.push_back({cp});
  }
  in_where_ = true;
  struct ResetWhere {
    bool* w;
    ~ResetWhere() { *w = false; }
  } reset_where{&in_where_};
  for (const auto& filter : plan.filters) {
    Fragment c_frag = with_row(src, filter.binder, [&]() {
      return require_bool(node(filter.node), filter.pos, "FILTER");
    });
    cond_parts.push_back(c_frag.parts());
  }
  in_where_ = false;

  if (!cond_parts.empty()) {
    add_sql(" WHERE ");
    for (std::size_t i = 0; i < cond_parts.size(); ++i) {
      if (i > 0) add_sql(" AND ");
      for (const auto& p : cond_parts[i]) {
        parts.push_back(p);
      }
    }
  }

  // 4. GROUP BY clause
  if (plan.group_by && !plan.group_by->empty()) {
    add_sql(" GROUP BY ");
    bool first = true;
    for (const auto& gb : *plan.group_by) {
      if (!first) add_sql(", ");
      first = false;
      Fragment g_frag = group_key(src, gb);
      if (plan.bare_key && (g_frag.kind() == SqlKind::Bool || g_frag.kind() == SqlKind::Bin)) {
        refuse("E_SQL_SHAPE",
               "a bare BUCKET groups by one text or number key, as an index does; SEL "
               "refuses a boolean or binary key (E_NOT_TEXT)",
               gb.pos);
      }
      for (const auto& p : g_frag.parts()) {
        parts.push_back(p);
      }
    }
  }

  // 5. HAVING clause
  if (!plan.having.empty()) {
    add_sql(" HAVING ");
    std::vector<std::vector<Fragment::Part>> h_cond_parts;
    in_having_ = true;
    struct ResetHaving {
      bool* h;
      ~ResetHaving() { *h = false; }
    } reset_having{&in_having_};
    for (const auto& hav : plan.having) {
      const auto render = [&]() { return require_bool(node(hav.node), hav.pos, "FILTER"); };
      Fragment h_frag = hav.over_groups ? with_group(src, hav.binder, render)
                                        : with_projected(src, hav.binder, render);
      h_cond_parts.push_back(h_frag.parts());
    }
    for (std::size_t i = 0; i < h_cond_parts.size(); ++i) {
      if (i > 0) add_sql(" AND ");
      for (const auto& p : h_cond_parts[i]) {
        parts.push_back(p);
      }
    }
  }

  // 6. ORDER BY clause
  if (!plan.order_by.empty()) {
    add_sql(" ORDER BY ");
    bool first = true;
    for (const auto& ord : plan.order_by) {
      if (!first) add_sql(", ");
      first = false;
      // A TEXT sort key is collated like a group key: SEL sorts text by its
      // bytes, and a server's default collation would not. order_key says what
      // SQL's sort cannot promise.
      const auto render = [&]() { return node(ord.node); };
      Fragment o_frag = order_key(ord.over_groups ? with_group(src, ord.binder, render)
                                  : plan.group_by ? with_projected(src, ord.binder, render)
                                                  : with_row(src, ord.binder, render),
                                  ord.node->pos());
      for (const auto& p : o_frag.parts()) {
        parts.push_back(p);
      }
      add_sql(" " + ord.dir);
    }
  }

  // 7. LIMIT / OFFSET clause
  if (plan.limit && plan.offset) {
    add_sql(" LIMIT " + std::to_string(*plan.limit) + " OFFSET " + std::to_string(*plan.offset));
  } else if (plan.limit) {
    add_sql(" LIMIT " + std::to_string(*plan.limit));
  } else if (plan.offset) {
    auto chain = Map::chain(dialect_);
    auto has_target = [&](std::string_view t) {
      return std::find(chain.begin(), chain.end(), t) != chain.end();
    };
    if (has_target("mariadb") || has_target("mysql") || has_target("mysql-family")) {
      add_sql(" LIMIT 18446744073709551615 OFFSET " + std::to_string(*plan.offset));
    } else if (has_target("sqlite")) {
      add_sql(" LIMIT -1 OFFSET " + std::to_string(*plan.offset));
    } else {
      add_sql(" OFFSET " + std::to_string(*plan.offset));
    }
  }

  Fragment out(parts, SqlKind::Statement, dialect_);
  out.params_ = params_;
  out.param_kinds_ = param_kinds_;
  out.caveats_ = caveats_;
  return out;
}


}  // namespace sel::sql
