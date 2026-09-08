// See sel_sql_stage1.hpp.

#include "sel_sql_stage1.hpp"

#include <map>
#include <vector>

namespace sel::sql {
namespace {

bool is_binder_name_impl(NT t, bool grouped) { return t == NT::Var && !grouped; }

// The literal key an index expression names, or nothing when it is not one.
//
// `num` and `text` ONLY. A BOOL index is refused -- `A[TRUE] = 1` is
// E_SQL_ASSIGN -- so spelling this as "is a literal node" would silently accept
// a key this layer does not.
//
// The key is the parser's own characters. It normalises leading zeros and
// nothing else, so `R[1.0]` and `R[1]` are two different keys and SEL agrees:
// `R[1.0] = 1; R[1] = 2; JOIN(MAP(R, _K), "|")` is "1.0|1". Canonicalising here
// would collapse them, or turn a legal program into a duplicate-key refusal.
const std::string* constant_key(const SNode& idx) {
  if (idx.t() == SNode::T::Num || idx.t() == SNode::T::Text) return &idx.s();
  return nullptr;
}

bool constant_call(const SNode& n, const std::set<std::string>& bound);

// --- substitution ------------------------------------------------------------

struct Def {
  // A clist is held by its MUTABLE handle, because a later append has to be
  // visible through a reference an earlier statement already took:
  // `R[1] = 1; A = R; R[2] = 2; COUNT(A)` is 2.
  std::shared_ptr<SNode> clist;
  SNodePtr node;
  bool is_clist() const { return clist != nullptr; }
  SNodePtr get() const { return clist ? SNodePtr(clist) : node; }
};

using Defs = std::map<std::string, Def>;

SNodePtr substitute(const NodePtr& node, Defs& defs,
                    const std::vector<std::string>& bound, int depth);

std::vector<SNodePtr> flatten(const std::vector<NodePtr>& items, Defs& defs,
                              const std::vector<std::string>& bound, int depth) {
  // Spec §5.9: an operand with children and no scalar of its own contributes
  // each of its children, and the keys are renumbered from 1. Both node kinds
  // contribute, and both contribute exactly ONE level -- per application, not
  // net: a nested list literal is already flat by the time this sees it,
  // because substitute() recursed into the inner list first. Only a clist's
  // values stop the recursion, which is the whole reason clist exists.
  std::vector<SNodePtr> out;
  for (const NodePtr& item : items) {
    SNodePtr s = substitute(item, defs, bound, depth);
    if (s->t() == SNode::T::List || s->t() == SNode::T::CList) {
      for (const SNodePtr& kid : s->kids()) out.push_back(kid);
      continue;
    }
    out.push_back(std::move(s));
  }
  return out;
}

SNodePtr substitute(const NodePtr& node, Defs& defs,
                    const std::vector<std::string>& bound, int depth) {
  // Bounded at the EVALUATOR's own limit, because stage 1 walks the tree before
  // the translator's guard can reach it. Without this the deepest expression
  // the layer accepts was decided by the host: PHP recursed as far as it liked
  // and Python died of its own stack at around 510 terms, which is an
  // implementation accident rather than a decision.
  ++depth;
  if (depth > MAX_DEPTH) {
    refuse("E_SQL_DEPTH",
           "this expression nests deeper than SEL will evaluate (" +
               std::to_string(MAX_DEPTH) +
               "), so there is nothing to translate; the evaluator answers "
               "E_DEPTH for it",
           node->pos);
  }
  switch (node->t) {
    case NT::Var: {
      // `bound` before `defs`: an aggregate binder SHADOWS a same-named helper.
      // `B = 7; ALL((1,2), B, B > 0)` translates to (1 > 0) AND (2 > 0) -- the
      // helper is never inlined into the body.
      for (const std::string& b : bound) {
        if (b == node->s) return SNode::leaf(node);
      }
      auto it = defs.find(node->s);
      return it == defs.end() ? SNode::leaf(node) : it->second.get();
    }
    case NT::Num:
    case NT::Text:
    case NT::Bool:
      return SNode::leaf(node);

    case NT::Assign:
      refuse("E_SQL_ASSIGN",
             "an assignment here would have to happen while the query runs, and "
             "a SQL expression cannot assign",
             node->pos);
    case NT::Seq:
      refuse("E_SQL_ASSIGN",
             "a sequence here would evaluate and discard a value, which a SQL "
             "expression cannot do",
             node->pos);

    case NT::Un:
      return SNode::rewritten(node, {substitute(node->l, defs, bound, depth)});
    case NT::Bin:
    case NT::Index:
      return SNode::rewritten(node, {substitute(node->l, defs, bound, depth),
                                     substitute(node->r, defs, bound, depth)});
    case NT::List:
      return SNode::rewritten(node, flatten(node->items, defs, bound, depth));

    case NT::Call: {
      std::vector<std::string> inner = bound;
      const bool binds = node->spec != nullptr && node->spec->binds;
      const std::size_t n = node->items.size();
      if (binds) {
        inner.emplace_back("_K");
        inner.push_back(n == 3 && is_binder_name(*node->items[1])
                            ? node->items[1]->s
                            : std::string("_"));
      }
      std::vector<SNodePtr> args;
      args.reserve(n);
      for (std::size_t i = 0; i < n; ++i) {
        // An aggregate's binder argument is a NAME, not a read of one.
        if (binds && i == 1 && n == 3 && node->items[i]->t == NT::Var) {
          args.push_back(SNode::leaf(node->items[i]));
          continue;
        }
        args.push_back(substitute(node->items[i], defs, i == 0 ? bound : inner, depth));
      }
      return SNode::rewritten(node, std::move(args));
    }
  }
  return SNode::leaf(node);
}

// --- recording ---------------------------------------------------------------

// Fold one leading statement into `defs`, or refuse it. Every refusal reports
// the ASSIGN's position, which is its target's position -- not the `=` and not
// the statement start.
void record(const NodePtr& s, Defs& defs, const std::set<std::string>& const_names,
            sel::Value& root) {
  if (s->t != NT::Assign) {
    refuse("E_SQL_ASSIGN",
           "only assignments may come before the result expression; this "
           "computes a value nothing reads, which SQL has nowhere to put",
           s->pos);
  }
  if (s->s != "=") {
    refuse("E_SQL_ASSIGN",
           s->s + " reads its own target before writing it, and SQL has nowhere "
                  "to put the write; use = and a fresh name",
           s->pos);
  }

  // The target is a bare name, or a name indexed by constant keys. The walk is
  // OUTSIDE-IN, so with two non-constant indices the outermost is refused
  // first: `A[X][Y] = 1` reports the Y bracket.
  std::vector<std::string> keys;
  const Node* t = s->l.get();
  while (t->t == NT::Index) {
    const SNodePtr idx = SNode::leaf(t->r);
    const std::string* k = constant_key(*idx);
    if (!k) {
      refuse("E_SQL_ASSIGN",
             "an assignment target may only be indexed by a constant here, "
             "because the shape has to be known before the query runs",
             t->r->pos);
    }
    keys.insert(keys.begin(), *k);
    t = t->l.get();
  }
  if (t->t != NT::Var) {
    refuse("E_SQL_ASSIGN", "assignment target is not a variable", s->pos);
  }
  const std::string name = t->s;

  SNodePtr value = substitute(s->r, defs, {}, 0);

  // Validated here, and ONLY here, because after this the subtree may be gone:
  // a definition nothing reads is dropped, so `A = 1 / 0; TRUE` translated to
  // `TRUE` and every server answered TRUE where SEL raises E_DIV_ZERO. An
  // indexed assignment builds a clist, which the constant test refuses to walk
  // and COUNT/HAS never render, so `R[1] = 1 / 0; COUNT(R)` was 1.
  if (is_constant(*value, const_names)) validate(*value, root);

  if (keys.empty()) {
    if (defs.count(name)) {
      refuse("E_SQL_ASSIGN",
             name + " is assigned more than once; SQL has no notion of a "
                    "variable changing, so each name may be written once",
             s->pos);
    }
    defs[name].node = std::move(value);
    return;
  }
  if (keys.size() > 1) {
    refuse("E_SQL_ASSIGN",
           "only one level of indexed assignment can be folded into a list here",
           s->pos);
  }
  const std::string& key = keys[0];
  auto it = defs.find(name);
  if (it == defs.end()) {
    // The clist carries the FIRST indexed assignment's position, and it
    // survives every later append.
    it = defs.emplace(name, Def{SNode::new_clist(s->pos), nullptr}).first;
  }
  if (!it->second.is_clist()) {
    refuse("E_SQL_ASSIGN",
           name + " is assigned both as a whole and by index; use one or the other",
           s->pos);
  }
  for (const std::string& existing : it->second.clist->keys()) {
    if (existing == key) {
      refuse("E_SQL_ASSIGN", name + "[" + key + "] is assigned more than once",
             s->pos);
    }
  }
  it->second.clist->append(key, std::move(value));
}

bool constant_call(const SNode& n, const std::set<std::string>& bound) {
  // The binding form is the only reason this is not three lines. `MAP(list, X,
  // X + 1)` names its binder in argument 1 and uses it in argument 2; the
  // two-argument form binds `_` implicitly. Neither name is a free variable, so
  // neither disqualifies the call -- but the SOURCE still has to be constant,
  // or the body has nothing to iterate.
  const auto& args = n.kids();
  if (!(n.spec() != nullptr && n.spec()->binds)) {
    for (const SNodePtr& a : args) {
      if (!is_constant(*a, bound)) return false;
    }
    return true;
  }
  if (args.empty() || !is_constant(*args[0], bound)) return false;
  std::set<std::string> inner = bound;
  std::size_t body = 1;
  if (args.size() >= 3) {
    // Malformed; not constant, and the aggregate refuses it for real.
    if (!is_binder_name(*args[1])) return false;
    inner.insert(args[1]->s());
    body = 2;
  } else {
    inner.insert("_");
  }
  for (std::size_t i = body; i < args.size(); ++i) {
    if (!is_constant(*args[i], inner)) return false;
  }
  return true;
}

// SEL's own refusal, reported as the translator's.
//
// The position is SEL's own -- the innermost node that failed, not the
// outermost one this was entered at -- because that is the character the author
// has to change.
[[noreturn]] void refuse_as_sel(const SelError& e, const SNode& n) {
  refuse("E_SQL_INVALID",
         "SEL rejects this expression (" + e.code() + ": " + e.message() +
             "), so there is nothing to translate; a database would answer "
             "something rather than fail",
         e.line() > 0 ? e.pos() : n.pos());
}

}  // namespace

// --- the interface -----------------------------------------------------------

bool is_binder_name(const Node& n) { return is_binder_name_impl(n.t, n.grouped); }

bool is_binder_name(const SNode& n) {
  return n.t() == SNode::T::Var && !n.grouped();
}

bool is_constant(const SNode& n, const std::set<std::string>& bound) {
  switch (n.t()) {
    case SNode::T::Num:
    case SNode::T::Text:
    case SNode::T::Bool:
      return true;
    case SNode::T::Var:
      return bound.count(n.s()) != 0;
    case SNode::T::Un:
      return is_constant(*n.l(), bound);
    case SNode::T::Bin:
    case SNode::T::Index:
      return is_constant(*n.l(), bound) && is_constant(*n.r(), bound);
    case SNode::T::CList:
      // Stage 1 builds this one; the evaluator has never seen it and cannot
      // evaluate it. Nothing containing one is checkable.
      return false;
    case SNode::T::List:
      for (const SNodePtr& item : n.kids()) {
        if (!is_constant(*item, bound)) return false;
      }
      return true;
    case SNode::T::Call:
      return constant_call(n, bound);
    default:
      // assign and seq are gone by now, and an unknown node is not something to
      // guess about: not constant, so nothing is validated and the walk refuses
      // it in the ordinary way.
      return false;
  }
}

void validate(const SNode& n, sel::Value& root) {
  const NodePtr node = n.to_node();
  if (!node) return;   // contains a clist; there is nothing to ask
  try {
    // Program::run(root) IS `Context ctx(root); eval_node(ast, ctx)`, which is
    // exactly what the Python host calls. No evaluator internals are needed.
    Program("", node).run(root);
  } catch (const SelError& e) {
    refuse_as_sel(e, n);
  }
}

void require_numeric(const SNode& n, sel::Value& root) {
  const NodePtr node = n.to_node();
  if (!node) return;   // contains a clist; there is nothing to ask
  try {
    // The one place the SQL layer reaches past the public header, and it reaches
    // for the coercion the operators themselves use rather than a copy of it:
    // the other hosts write `evalNode(n, ctx)->asDecimal(n.pos)` and this is
    // that line. sel_ast.hpp says why the C++ spelling is a free function.
    require_number(Program("", node).run(root), n.pos());
  } catch (const SelError& e) {
    refuse_as_sel(e, n);
  }
}

ConstScope const_scope(const Bindings* bindings) {
  ConstScope out;
  if (!bindings) return out;
  for (const std::string& name : bindings->names()) {
    const Binding& b = bindings->get(name);
    if (b.kind() != Binding::Kind::Value) continue;
    const sel::Value& v = b.as_value();
    if (v.is_none() || v.size() > 0) continue;
    out.names.insert(name);
    out.root.set(name, v);
  }
  return out;
}

SNodePtr normalise(const NodePtr& ast, const std::set<std::string>& const_names,
                   sel::Value& root) {
  std::vector<NodePtr> stmts;
  if (ast->t == NT::Seq) stmts = ast->items;
  else stmts.push_back(ast);

  // The LAST statement is the result, whatever it is. A trailing assignment is
  // not refused here: it is popped as the result and refused by substitute's
  // assign branch, which is why `A = 1` reports at 1:1.
  const NodePtr result = stmts.back();
  stmts.pop_back();

  Defs defs;
  for (const NodePtr& s : stmts) record(s, defs, const_names, root);
  return substitute(result, defs, {}, 0);
}

}  // namespace sel::sql
