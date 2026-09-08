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

Fragment Translator::translate(const NodePtr& ast) {
  // The order is contract: a caller with both a bad dialect and a duplicate
  // alias gets E_SQL_DIALECT, so these two must not be fused into one pass.
  Map::require_target(dialect_);
  bindings_.check_aliases();

  params_.clear();
  param_kinds_.clear();
  caveats_.clear();
  frames_.clear();
  depth_ = 0;

  ConstScope scope = const_scope(&bindings_);
  const_names_ = std::move(scope.names);
  const_root_ = std::move(scope.root);

  const SNodePtr normalised = normalise(ast, const_names_, const_root_);
  const Fragment f = node(normalised);

  // Only this final Fragment carries the vectors; every intermediate one built
  // during the walk has none.
  Fragment out(f.parts_, f.kind_, dialect_);
  out.params_ = params_;
  out.param_kinds_ = param_kinds_;
  out.caveats_ = caveats_;
  return out;
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

Fragment Translator::from_binder(const Binder& b, const SNode& n) {
  switch (b.shape()) {
    case Binder::Shape::Node:
      // Re-enters the whole walk on the element, so the depth counter and the
      // constant validation apply to the inlined element too.
      return node(b.as_node());
    case Binder::Shape::Column:
      return column_ref(b.as_column());
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
      return column_ref(*field);
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
    if (const ColumnSpec* f = rel.field(ascii_upper(key))) return column_ref(*f);
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
  return emit_.numeric_operand(f, n.pos());
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

  if (contains(BYTE_COMPARISONS, op)) {
    require_comparable_kinds(l, r, op, n.pos());
    // The cast and collate are skipped only when BOTH operands are already BIN.
    if (l.kind() != SqlKind::Bin || r.kind() != SqlKind::Bin) {
      l = emit_.text_operand(l);
      r = emit_.text_operand(r);
    }
  }
  const Fragment args[] = {l, r};
  return apply(Section::Ops, op, args, n.pos(), variant);
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
    const Fragment needle = emit_.text_operand(raw);
    const Fragment f = node(e);
    if (f.kind() == SqlKind::List) {
      refuse("E_SQL_SHAPE",
             "IN over a list of lists is structural in SEL and has no SQL "
             "counterpart",
             e->pos());
    }
    // `raw`, not `needle`: needle's kind is always TEXT after the cast.
    require_comparable_kinds(raw, f, "IN", e->pos());
    const Fragment args[] = {needle, emit_.text_operand(f)};
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
           name == "POWER" || name == "CHAR";
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
  return apply(Section::Funcs, name, args, n->pos());
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
             "docs/SQL-TRANSLATION.md 7.5",
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
      }
      // A COLUMN, or a ROW with exactly one field: the scalar rule.
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
  if (src->t() == SNode::T::Call && contains(YIELDS_LIST, src->s())) {
    refuse("E_SQL_SHAPE",
           src->s() + " yields a list, and the scalar rule does not apply to "
                      "it; SQL has no way to count or index what it produces",
           src->pos());
  }
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
  const Binder row = Binder::row(src.relation);
  std::vector<std::pair<std::string, Binder>> frame;
  frame_set(frame, binder_name, row);
  frame_set(frame, "_K",
            Binder::none("a row of a relation has no key: SQL rows are "
                         "unordered and unkeyed unless the schema says "
                         "otherwise, and guessing which column is the key is "
                         "not something this layer does"));
  for (const Filter& f : src.filters) frame_set(frame, f.binder, row);

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

}  // namespace sel::sql
