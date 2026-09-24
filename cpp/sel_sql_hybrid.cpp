// Maximal SQL-prefix planner for relational SEL pipelines.
//
// The contract every host's planner meets is in docs/internals/sql-translation.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt. Three parts of it were wrong
// here: the planner unwound the RAW tree, so `X = ORDERS; X .> TAKE(1)` -- a
// seq, not a pipeline -- was classified as pure memory where the three hosts
// that ran stage 1 first pushed it down; it then planned stage 1's tree, in
// which every helper is inlined the way translate() wants it, so a
// continuation reported an error at a helper's definition where run() reports
// its use -- see "helper assignments" below for what is done instead; and it
// carried its own copy of the pipeline vocabulary and the unwind/build pair,
// which sel.cpp now shares through sel_ast.hpp so this host has one of each.

#include "sel_sql.hpp"

#include "sel_ast.hpp"
#include "sel_sql_map.hpp"
#include "sel_sql_node.hpp"
#include "sel_sql_stage1.hpp"
#include "sel_sql_emit.hpp"

#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>

namespace sel::sql {
namespace {

constexpr std::string_view SQL_SPECIAL_CALLS[] = {
    "IF", "COND", "COALESCE", "COUNT", "SUM", "AVG", "MIN", "MAX", "RECORD", "LIST"};

std::string upper_ascii(std::string value) {
  for (char& ch : value) {
    if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
  }
  return value;
}

std::shared_ptr<Node> copy_node(const NodePtr& node) {
  if (!node) return nullptr;
  auto copy = std::make_shared<Node>(*node);
  copy->l = node->l;
  copy->r = node->r;
  copy->items = node->items;
  return copy;
}

NodePtr text_node(std::string value, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Text;
  node->s = std::move(value);
  node->pos = pos;
  return node;
}

NodePtr index_node(NodePtr object, NodePtr key, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Index;
  node->l = std::move(object);
  node->r = std::move(key);
  node->pos = pos;
  return node;
}

// Whether the SQL rows for this step list are a bucket's KEYS rather than the
// value SEL would have produced. A BUCKET without a projection is open: the
// translator projects its keys, and SEL's value is a map of member rows. The
// next MAP closes it -- it becomes the bucket's projection, one statement, one
// value in both lanes -- and a FILTER between them is a HAVING. Any other step
// seals it: the members are gone, and no continuation can get them back. So a
// prefix that is open or sealed is not a split point, whatever the translator
// says about it, and the MAP fall-through must not fire on a MAP that closes
// one -- its custom half would be evaluated over key rows.
bool bucket_rows_are_keys(const std::vector<NodePtr>& steps, std::size_t count) {
  bool open = false;
  for (std::size_t i = 0; i < count && i < steps.size(); ++i) {
    const NodePtr& step = steps[i];
    if (step->s == "BUCKET") {
      if (open) return true;
      open = step->items.size() == 2;
    } else if (open && step->s == "MAP") {
      open = false;
    } else if (open && step->s != "FILTER") {
      return true;
    }
  }
  return open;
}

// Whether the SQL rows for this step list are a join's rows without the
// binders SEL's rows carry. A LINK's row in SEL holds each side under its
// binders and the promoted fields beside them (spec §7.4); SQL carries the
// promoted fields alone. A MAP, a SELECT_COLS or a projected BUCKET after the
// LINK makes the rows exact again -- what they compute is over the promoted
// fields, or is refused -- so a prefix whose LINK nothing has projected is
// not a split point and not a full pushdown (finding Y, lanes): its
// continuation would read `_["C"]` where the database sent nothing.
bool join_rows_lack_binders(const std::vector<NodePtr>& steps, std::size_t count) {
  bool joined = false;
  for (std::size_t i = 0; i < count && i < steps.size(); ++i) {
    const NodePtr& step = steps[i];
    if (step->s == "LINK" || step->s == "LINK_LEFT") joined = true;
    else if (step->s == "MAP" || step->s == "SELECT_COLS" || step->s == "BUCKET") joined = false;
  }
  return joined;
}

// The two together: a prefix whose SQL rows are not the value SEL would have
// produced for it, whatever the translator says about it.
bool rows_are_not_the_value(const std::vector<NodePtr>& steps, std::size_t count) {
  return bucket_rows_are_keys(steps, count) || join_rows_lack_binders(steps, count);
}

// The whole-name helper definitions of a program, by name.
using Definitions = std::map<std::string, NodePtr>;

// `defs` are the helper definitions: a read of one is as unsupported as its
// definition, since the translator will inline it.
bool contains_unsupported_sql(const NodePtr& node, const std::string& dialect,
                              const Definitions* defs = nullptr,
                              const std::set<std::string>& seen = {}) {
  if (!node) return false;
  if (node->t == NT::Var && defs != nullptr && defs->count(node->s) != 0 &&
      seen.count(node->s) == 0) {
    std::set<std::string> inner_seen = seen;
    inner_seen.insert(node->s);
    return contains_unsupported_sql(defs->at(node->s), dialect, defs, inner_seen);
  }
  if (node->t == NT::Call) {
    const bool special = std::find(std::begin(SQL_SPECIAL_CALLS),
                                   std::end(SQL_SPECIAL_CALLS), node->s) !=
                         std::end(SQL_SPECIAL_CALLS);
    if (!special) {
      const Entry* entry = Map::entry(dialect, Section::Funcs, upper_ascii(node->s));
      if (!entry || entry->kind == EntryKind::Refusal) return true;
    }
  }
  const auto inner = [&](const NodePtr& item) {
    return contains_unsupported_sql(item, dialect, defs, seen);
  };
  if (node->l && inner(node->l)) return true;
  if (node->r && inner(node->r)) return true;
  for (const NodePtr& item : node->items) {
    if (inner(item)) return true;
  }
  return false;
}

// The field names read as `binder["field"]` in `node`, first seen first and
// compared exactly: SEL's record keys are case-sensitive, so `name` and `Name`
// are two fields. An empty `binder` means a read under ANY name counts -- a
// downstream step binds the row however it likes (`SORT_BY(s, s["name"])`).
void collect_field_references(const NodePtr& node, const std::string& binder,
                              std::vector<std::string>& out) {
  if (!node) return;
  if (node->t == NT::Index && node->l && node->l->t == NT::Var &&
      node->r && node->r->t == NT::Text) {
    const std::string object = upper_ascii(node->l->s);
    if (binder.empty() || object == upper_ascii(binder) || object == "_" ||
        object == "_1" || object == "_2") {
      const std::string key = node->r->s;
      if (std::find(out.begin(), out.end(), key) == out.end()) out.push_back(key);
    }
  }
  if (node->l) collect_field_references(node->l, binder, out);
  if (node->r) collect_field_references(node->r, binder, out);
  for (const NodePtr& item : node->items) collect_field_references(item, binder, out);
}

// The steps the MAP fall-through may push past the MAP. Each keeps the rows as
// they are -- the same records, fewer or reordered -- so the custom half of the
// projection still runs over its own input. A step that changes the row shape
// (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and the
// whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare the
// dependency columns SQL carries where SEL compares the custom values.
bool fallthrough_downstream(const std::string& name) {
  // FILTER retains ordinal keys that SQL rows plus the local MAP cannot restore.
  return name == "SORT_BY" || name == "TOP_BY" || name == "TAKE" ||
         name == "DROP";
}

// Whether `node` reads the row itself -- the binder outside an index with a
// text key, as in `GET(_, "name")` or `COUNT(_)` -- which no projected column
// can stand in for.
bool reads_whole_row(const NodePtr& node, const std::string& binder) {
  if (!node) return false;
  if (node->t == NT::Var) {
    const std::string name = upper_ascii(node->s);
    return name == upper_ascii(binder) || name == "_" || name == "_1" || name == "_2";
  }
  if (node->t == NT::Index && node->l && node->l->t == NT::Var && node->r &&
      node->r->t == NT::Text) {
    // A field read; the object is not a whole-row read.
    return reads_whole_row(node->r, binder);
  }
  if (reads_whole_row(node->l, binder) || reads_whole_row(node->r, binder)) return true;
  for (const NodePtr& item : node->items) {
    if (reads_whole_row(item, binder)) return true;
  }
  return false;
}

// Whether a pushable pair is the plain field read `binder[key]` of its own key,
// so that a dependency of the same name may share its column.
bool is_own_field_read(const std::pair<NodePtr, NodePtr>& pair, const std::string& binder) {
  const NodePtr& value = pair.second;
  return value && value->t == NT::Index && value->l && value->l->t == NT::Var && value->r &&
         value->r->t == NT::Text && upper_ascii(value->l->s) == upper_ascii(binder) &&
         value->r->s == pair.first->s;
}

struct MapRecordDetails {
  bool explicit_binder = false;
  std::string binder = "_";
  NodePtr body;
  std::vector<std::pair<NodePtr, NodePtr>> pairs;
};

std::optional<MapRecordDetails> map_record_details(const NodePtr& step) {
  if (!step || step->t != NT::Call || step->s != "MAP") return std::nullopt;
  const auto& args = step->items;
  MapRecordDetails out;
  out.explicit_binder = args.size() == 3 && args[1]->t == NT::Var && !args[1]->grouped;
  out.binder = out.explicit_binder ? args[1]->s : "_";
  if (out.explicit_binder) out.body = args[2];
  else if (args.size() == 2) out.body = args[1];
  else return std::nullopt;
  if (!out.body || out.body->t != NT::Call || out.body->s != "RECORD" ||
      out.body->items.size() % 2 != 0) {
    return std::nullopt;
  }
  std::set<std::string> seen;
  for (std::size_t i = 0; i < out.body->items.size(); i += 2) {
    if (out.body->items[i]->t != NT::Text) return std::nullopt;
    if (!seen.insert(out.body->items[i]->s).second) return std::nullopt;
    out.pairs.emplace_back(out.body->items[i], out.body->items[i + 1]);
  }
  return out;
}

NodePtr var_node(std::string name, Pos pos) {
  auto node = std::make_shared<Node>();
  node->t = NT::Var;
  node->s = std::move(name);
  node->pos = pos;
  return node;
}

// Every physical source the tree reads, first use first, each once. The
// physical source is the relation's table, or for a relation query the query
// text exactly as the application wrote it; keyed by that, so two bindings
// over one table are one source.
void collect_tables(const NodePtr& node, const Bindings& bindings,
                    std::vector<std::string>& out, std::set<std::string>& seen) {
  if (!node) return;
  if (node->t == NT::Var && bindings.has(node->s)) {
    const Binding& binding = bindings.get(node->s, node->pos);
    if (binding.kind() == Binding::Kind::Relation) {
      const std::string& table = binding.as_relation().from;
      if (seen.insert(table).second) out.push_back(table);
    }
    return;
  }
  if (node->l) collect_tables(node->l, bindings, out, seen);
  if (node->r) collect_tables(node->r, bindings, out, seen);
  for (const NodePtr& item : node->items) collect_tables(item, bindings, out, seen);
}

std::vector<std::string> source_tables(const NodePtr& ast, const Bindings& bindings) {
  std::vector<std::string> out;
  std::set<std::string> seen;
  collect_tables(ast, bindings, out, seen);
  return out;
}


// --- helper assignments -----------------------------------------------------
//
// Stage 1 inlines a helper assignment for translate(): `Y = "x"; ... + Y` is
// rendered as `... + "x"`, the literal keeping its definition-site position,
// which is right for a refusal message. It is wrong for the memory half of a
// plan, because that half is a program run() evaluates and §12.1 promises it
// reports errors where run() would: run() evaluates the READ of Y at the use
// site and reports `+`'s operand there, and it evaluates the definition once,
// before the pipeline, not once per row (review 2026-09-15 finding AJ). So
// the planner does not inline. It plans the program as written, three ways:
//
//   * A helper that IS a literal -- after inlining earlier such helpers and
//     folding, `N = 1 + 1` as much as `N = 2` -- is inlined at its reads,
//     stamped with the read's position. That is invisible: a leaf literal
//     cannot fail, and neither can the read, since the definition exists. It
//     keeps `TAKE(N)` a `LIMIT 2` rather than a helper the SQL has to carry.
//   * A helper read as the pipeline's SOURCE is unwound through: `X = ORDERS
//     .> TAKE(2); X .> MAP(...)` is one pipeline over ORDERS, so the prefix
//     search sees every step. Only the source position looks through a
//     helper; a read anywhere else stays a read.
//   * What is handed to the translator, and what is kept for the
//     continuation, carries in front of it the assignments it still reads and
//     the ones those read, in program order, as the program wrote them. The
//     translator runs its own stage 1 over that seq and inlines; the
//     continuation evaluates them once, before its steps, as run() does. An
//     assignment nothing after the split reads is dropped, as stage 1 drops
//     it for translate() -- the one departure, and the same one.
//
// This section sits before the fall-through rather than after it, as the
// other hosts have it, only because C++ does not hoist.

bool is_literal_type(NT t) {
  return t == NT::Num || t == NT::Text || t == NT::Bool || t == NT::Null;
}

// The leading statements and the result expression of a program.
struct Statements {
  std::vector<NodePtr> leading;
  NodePtr result;
};

Statements statements(const NodePtr& ast) {
  if (ast->t != NT::Seq || ast->items.empty()) return {{}, ast};
  return {std::vector<NodePtr>(ast->items.begin(), ast->items.end() - 1), ast->items.back()};
}

// The name a leading statement assigns. Stage 1 has accepted every statement
// by the time this runs, so each is an assignment whose target is a name, or
// a name indexed by constants.
std::string assigned_name(const NodePtr& statement) {
  NodePtr target = statement->l;
  while (target && target->t == NT::Index) target = target->l;
  return target ? target->s : std::string();
}

// The whole-name definitions, by name. Stage 1 refuses a name assigned twice,
// or both whole and by index, so each name here has exactly one.
Definitions definitions(const std::vector<NodePtr>& leading) {
  Definitions defs;
  for (const NodePtr& s : leading) {
    if (s->t == NT::Assign && s->l && s->l->t == NT::Var) defs[s->l->s] = s->r;
  }
  return defs;
}

// `node` with every read of a literal helper replaced by the literal, stamped
// with the read's position. Binder scoping is stage 1's: a binder shadows a
// same-named helper inside its body. Copies on the way down, never writes.
NodePtr inline_literals(const NodePtr& node, const Definitions& literals,
                        const std::vector<std::string>& bound = {}) {
  if (!node) return node;
  const NT t = node->t;
  if (t == NT::Var) {
    if (std::find(bound.begin(), bound.end(), node->s) != bound.end() ||
        literals.count(node->s) == 0) {
      return node;
    }
    auto stamped = copy_node(literals.at(node->s));
    stamped->pos = node->pos;
    return stamped;
  }
  if (is_literal_type(t)) return node;
  const auto inline_child = [&](const NodePtr& child, const std::vector<std::string>& scope) {
    return inline_literals(child, literals, scope);
  };
  if (t == NT::Un) {
    auto copy = copy_node(node);
    copy->l = inline_child(node->l, bound);
    return copy;
  }
  if (t == NT::Bin || t == NT::Index) {
    auto copy = copy_node(node);
    copy->l = inline_child(node->l, bound);
    copy->r = inline_child(node->r, bound);
    return copy;
  }
  if (t == NT::List || t == NT::Seq) {
    auto copy = copy_node(node);
    for (NodePtr& item : copy->items) item = inline_child(item, bound);
    return copy;
  }
  if (t == NT::Assign) {
    auto copy = copy_node(node);
    copy->r = inline_child(node->r, bound);
    return copy;
  }
  if (t == NT::Call) {
    std::vector<std::string> inner = bound;
    const bool binds = node->spec != nullptr && node->spec->binds;
    const bool named_binder = node->items.size() == 3 && node->items[1] &&
                              is_binder_name(*node->items[1]);
    if (binds) {
      inner.push_back("_K");
      inner.push_back(named_binder ? node->items[1]->s : "_");
    }
    auto copy = copy_node(node);
    for (std::size_t i = 0; i < copy->items.size(); ++i) {
      if (binds && i == 1 && named_binder) continue;
      copy->items[i] = inline_child(node->items[i], i == 0 ? bound : inner);
    }
    return copy;
  }
  return node;
}

// The literal helpers: each whole-name definition, after the earlier literal
// helpers are inlined into it and it is folded, when what is left is a leaf.
Definitions literal_helpers(const std::vector<NodePtr>& leading) {
  Definitions literals;
  for (const NodePtr& s : leading) {
    if (s->t != NT::Assign || !s->l || s->l->t != NT::Var) continue;
    const NodePtr folded = optimize_ast_logical(inline_literals(s->r, literals));
    if (folded && is_literal_type(folded->t)) literals[s->l->s] = folded;
  }
  return literals;
}

// The pipeline the planner probes: the result unwound, and where its source
// is a helper, that helper's definition unwound in turn.
std::pair<NodePtr, std::vector<NodePtr>> unwind_through_helpers(
    const NodePtr& result, const Definitions& defs, const Definitions& literals) {
  auto [source, steps] = unwind_pipeline(inline_literals(result, literals));
  std::set<std::string> seen;
  while (source && source->t == NT::Var && defs.count(source->s) != 0 &&
         seen.count(source->s) == 0) {
    seen.insert(source->s);
    auto inner = unwind_pipeline(inline_literals(defs.at(source->s), literals));
    source = inner.first;
    steps.insert(steps.begin(), inner.second.begin(), inner.second.end());
  }
  return {source, steps};
}

// The names a tree reads, binders included: an over-approximation that can
// only keep an assignment the tree does not need, never drop one it does.
void read_names(const NodePtr& node, std::set<std::string>& out) {
  if (!node) return;
  if (node->t == NT::Var) {
    out.insert(node->s);
    return;
  }
  read_names(node->l, out);
  read_names(node->r, out);
  for (const NodePtr& item : node->items) read_names(item, out);
}

// The leading assignments `node` depends on, in program order: those whose
// name it reads, and those THEY read, transitively.
std::vector<NodePtr> referenced_assignments(const std::vector<NodePtr>& leading,
                                            const NodePtr& node) {
  std::set<std::string> needed;
  read_names(node, needed);
  bool grew = true;
  while (grew) {
    grew = false;
    for (const NodePtr& s : leading) {
      if (needed.count(assigned_name(s)) == 0) continue;
      std::set<std::string> reads;
      read_names(s->r, reads);
      for (const std::string& name : reads) {
        if (needed.insert(name).second) grew = true;
      }
    }
  }
  std::vector<NodePtr> kept;
  for (const NodePtr& s : leading) {
    if (needed.count(assigned_name(s)) != 0) kept.push_back(s);
  }
  return kept;
}

// `node` behind the assignments it depends on, as the program wrote them -- a
// seq the translator's stage 1 inlines and the evaluator runs in order -- or
// `node` itself when it depends on none.
NodePtr with_helpers(const std::vector<NodePtr>& leading, const NodePtr& node) {
  const std::vector<NodePtr> kept = referenced_assignments(leading, node);
  if (kept.empty()) return node;
  auto seq = std::make_shared<Node>();
  seq->t = NT::Seq;
  seq->items = kept;
  seq->items.push_back(node);
  seq->pos = kept.front()->pos;
  return seq;
}

// What the prefix search carries about the program's helpers: the definitions
// the fall-through classifies a read by, the wrap every tree handed to the
// translator or kept for the continuation goes through, and the source tables
// of a wrapped tree.
struct Helpers {
  const std::vector<NodePtr>& leading;
  const Definitions& defs;
  const Bindings& bindings;
  ConstScope& scope;

  NodePtr wrap(const NodePtr& node) const { return with_helpers(leading, node); }

  // The physical sources of a wrapped tree are read off what the translator
  // renders: stage 1's tree, where an assignment a binder shadows is gone.
  // Only called once the translator has accepted `wrapped`, so stage 1 cannot
  // refuse it here; a clist result (to_node() null) is not a statement the
  // translator accepts either, and the wrapped tree itself stands in for it.
  std::vector<std::string> tables(const NodePtr& wrapped) const {
    const NodePtr normalized = normalise(wrapped, scope.names, scope.root)->to_node();
    return source_tables(normalized ? normalized : wrapped, bindings);
  }
};

std::optional<HybridPlan> try_plan_fallthrough(
    const NodePtr& source, const std::vector<NodePtr>& steps,
    const std::string& dialect, const Bindings& bindings, const Options& options,
    const Helpers& helpers) {
  const auto map_it = std::find_if(steps.begin(), steps.end(), [](const NodePtr& step) {
    return step && step->s == "MAP";
  });
  if (map_it == steps.end()) return std::nullopt;
  const std::size_t map_index = static_cast<std::size_t>(map_it - steps.begin());
  if (bucket_rows_are_keys(steps, map_index)) return std::nullopt;
  const NodePtr& map_step = *map_it;
  const auto details = map_record_details(map_step);
  if (!details) return std::nullopt;

  std::vector<std::pair<NodePtr, NodePtr>> pushable;
  std::vector<std::pair<NodePtr, NodePtr>> custom;
  for (const auto& pair : details->pairs) {
    if (contains_unsupported_sql(pair.second, dialect, &helpers.defs)) custom.push_back(pair);
    else pushable.push_back(pair);
  }
  if (pushable.empty() || custom.empty()) return std::nullopt;
  // The custom half runs over the rows the SQL returns; a read of the row
  // itself cannot be served by any column.
  for (const auto& pair : custom) {
    if (reads_whole_row(pair.second, details->binder)) return std::nullopt;
  }

  // Every step after the MAP goes into the SQL, so each must keep the rows as
  // they are, and may read only what SEL's rows have after the MAP: the
  // pushable keys. The custom keys are not in the SQL; a dependency column is
  // in the SQL but not in SEL's row.
  const auto has_name = [](const std::vector<std::string>& names, const std::string& name) {
    return std::find(names.begin(), names.end(), name) != names.end();
  };
  const auto has_name_folded = [](const std::vector<std::string>& names, const std::string& name) {
    return std::any_of(names.begin(), names.end(), [&](const std::string& value) {
      return upper_ascii(value) == upper_ascii(name);
    });
  };
  std::vector<std::string> projected;
  for (const auto& pair : pushable) projected.push_back(pair.first->s);
  for (std::size_t i = map_index + 1; i < steps.size(); ++i) {
    if (!steps[i] || !fallthrough_downstream(steps[i]->s)) return std::nullopt;
    // items[0] is the step's input -- the pipeline so far -- not its own text;
    // the step binds the row under a name of its own, so any read counts.
    std::vector<std::string> refs;
    for (std::size_t a = 1; a < steps[i]->items.size(); ++a) {
      collect_field_references(steps[i]->items[a], "", refs);
    }
    for (const std::string& ref : refs) {
      if (!has_name(projected, ref)) return std::nullopt;
    }
  }

  // A dependency may share a projected column only when that column IS the
  // field: `"customer_id", _["amount"]` projects amount under the name the
  // custom half would read customer_id by. Names are compared exactly, as SEL
  // compares them; and a dependency that differs from a projected key only by
  // case is not projected beside it, because SQL aliases are not
  // case-sensitive everywhere.
  std::vector<std::string> own;
  for (const auto& pair : pushable) {
    if (is_own_field_read(pair, details->binder)) own.push_back(pair.first->s);
  }
  std::vector<std::string> dependencies;
  for (const auto& pair : custom) {
    std::vector<std::string> refs;
    collect_field_references(pair.second, details->binder, refs);
    for (const std::string& ref : refs) {
      if (has_name(projected, ref)) {
        if (!has_name(own, ref)) return std::nullopt;
      } else if (has_name_folded(projected, ref)) {
        return std::nullopt;
      } else if (!has_name(dependencies, ref)) {
        // Two dependencies must not differ only by case either.
        if (has_name_folded(dependencies, ref)) return std::nullopt;
        dependencies.push_back(ref);
      }
    }
  }

  auto rewritten_record = copy_node(details->body);
  rewritten_record->items.clear();
  for (const auto& pair : pushable) {
    rewritten_record->items.push_back(pair.first);
    rewritten_record->items.push_back(pair.second);
  }
  for (const std::string& dependency : dependencies) {
    const NodePtr key = text_node(dependency, map_step->pos);
    rewritten_record->items.push_back(key);
    rewritten_record->items.push_back(
        index_node(var_node(details->binder, map_step->pos), key, map_step->pos));
  }

  auto rewritten_map = copy_node(map_step);
  rewritten_map->items.clear();
  rewritten_map->items.push_back(map_step->items[0]);
  if (details->explicit_binder) rewritten_map->items.push_back(map_step->items[1]);
  rewritten_map->items.push_back(std::move(rewritten_record));

  std::vector<NodePtr> rewritten_steps;
  rewritten_steps.reserve(steps.size());
  rewritten_steps.insert(rewritten_steps.end(), steps.begin(), map_it);
  rewritten_steps.push_back(std::move(rewritten_map));
  rewritten_steps.insert(rewritten_steps.end(), map_it + 1, steps.end());
  const NodePtr rewritten_ast = helpers.wrap(build_pipeline(source, rewritten_steps));
  const Program rewritten_program("", rewritten_ast);
  auto sql = Sql::try_translate_statement(rewritten_program, dialect, bindings, options);
  if (!sql) return std::nullopt;

  // The continuation re-applies the projection to the rows that come back: a
  // pushable pair is passed through BY KEY -- the SQL already computed it,
  // under that name -- and a custom pair is evaluated as written, over the
  // dependency columns projected beside it.
  auto continuation_record = copy_node(details->body);
  continuation_record->items.clear();
  for (const auto& pair : details->pairs) {
    continuation_record->items.push_back(pair.first);
    const bool is_pushable = std::any_of(pushable.begin(), pushable.end(), [&](const auto& p) {
      return p.first == pair.first;
    });
    if (is_pushable) {
      continuation_record->items.push_back(index_node(
          var_node(details->binder, pair.second->pos), pair.first, pair.second->pos));
    } else {
      continuation_record->items.push_back(pair.second);
    }
  }
  auto continuation_map = copy_node(map_step);
  continuation_map->items.clear();
  continuation_map->items.push_back(var_node("_INPUT", map_step->pos));
  if (details->explicit_binder) continuation_map->items.push_back(map_step->items[1]);
  continuation_map->items.push_back(std::move(continuation_record));
  const NodePtr continuation_ast = helpers.wrap(continuation_map);

  HybridPlan plan;
  plan.dialect = dialect;
  plan.sql_statement = std::move(*sql);
  plan.sql_prefix_ast = rewritten_ast;
  plan.continuation_ast = continuation_ast;
  plan.continuation_program = Program("", continuation_ast);
  plan.is_hybrid = true;
  plan.source_tables = helpers.tables(rewritten_ast);
  return plan;
}

std::optional<std::string> latest_field_name(const NodePtr& n) {
  if (n && n->t == NT::Index && n->l && n->l->t == NT::Var && n->l->s == "_"
      && n->r && n->r->t == NT::Text) return n->r->s;
  return std::nullopt;
}

std::optional<HybridPlan> try_latest_member(const NodePtr& source, const std::vector<NodePtr>& steps,
    const std::string& dialect, const Bindings& catalog, const Options& opts, const Helpers& helpers) {
  if (dialect != "mariadb" && dialect != "mysql" && dialect != "postgresql" && dialect != "sqlite") return std::nullopt;
  const auto& rel = catalog.get(source->s, source->pos).as_relation();
  const auto it = std::find_if(steps.begin(), steps.end(), [](const auto& s) { return s->s == "BUCKET"; });
  if (!rel.unique_key || it == steps.end() || rel.from_is_raw || (rel.correlate && !rel.correlate->empty())) return std::nullopt;
  const auto at = static_cast<std::size_t>(it - steps.begin());
  const std::string& revision = *rel.unique_key;
  const auto& ba = steps[at]->items;
  const auto partition = ba.size() == 2 || ba.size() == 3 ? latest_field_name(ba[1]) : std::nullopt;
  NodePtr body = ba.size() == 3 ? ba[2] : nullptr;
  if (!body && at + 1 < steps.size() && steps[at + 1]->s == "MAP" && steps[at + 1]->items.size() == 2)
    body = steps[at + 1]->items[1];
  const auto* pf = rel.field(upper_ascii(partition.value_or("")));
  const auto* rf = rel.field(upper_ascii(revision));
  if (!partition || !body || body->t != NT::Call || body->s != "RECORD" || body->items.size() != 4
      || !pf || !rf || (pf->type != SqlKind::Num && pf->type != SqlKind::Text) || rf->type != SqlKind::Num
      || pf->column != *partition || rf->column != revision || pf->is_raw || rf->is_raw || rf->guard) return std::nullopt;
  const auto& ra = body->items;
  if (ra[0]->t != NT::Text || ra[2]->t != NT::Text || ra[0]->s == ra[2]->s) return std::nullopt;
  NodePtr top; bool has_key = false;
  for (const auto& v : {ra[1], ra[3]}) {
    if (v->t == NT::Call && v->s == "TOP_BY") top = v;
    if (v->t == NT::Var && v->s == "_K") has_key = true;
  }
  if (!top || !has_key) return std::nullopt;
  const auto& ta = top->items;
  if (ta.size() != 4 || ta[0]->t != NT::Var || ta[0]->s != "_" || latest_field_name(ta[1]) != revision
      || ta[2]->t != NT::Text || ta[2]->s != "DESC" || ta[3]->t != NT::Num || ta[3]->s != "1") return std::nullopt;
  for (std::size_t i = 0; i < at; ++i) {
    const auto& s = steps[i];
    if (s->s == "FILTER") continue;
    if (s->s != "SORT_BY" || (s->items.size() != 2 && s->items.size() != 3) || latest_field_name(s->items[1]) != revision
        || (s->items.size() == 3 && (s->items[2]->t != NT::Text || s->items[2]->s != "ASC"))) return std::nullopt;
  }
  std::vector<NodePtr> input_steps(steps.begin(), it);
  if (input_steps.empty()) {
    auto truth = std::make_shared<Node>(); truth->t = NT::Bool; truth->b = true; truth->pos = source->pos;
    auto dummy = std::make_shared<Node>(); dummy->t = NT::Call; dummy->s = "FILTER"; dummy->pos = source->pos;
    dummy->items = {source, truth}; input_steps.push_back(dummy);
  }
  const NodePtr prefix = helpers.wrap(build_pipeline(source, input_steps));
  auto sql = Sql::try_translate_statement(Program("", prefix), dialect, catalog, opts);
  if (!sql) return std::nullopt;
  try {
    Emit emit(dialect);
    std::string input = "_sel_input", groups = "_sel_latest";
    while (upper_ascii(input) == upper_ascii(rel.from)) input += "_";
    while (upper_ascii(groups) == upper_ascii(rel.from) || upper_ascii(groups) == upper_ascii(input)) groups += "_";
    const auto qi = emit.ident(input), qg = emit.ident(groups), qr = emit.ident(revision),
      qmax = emit.ident("_sel_revision"), qfirst = emit.ident("_sel_first");
    auto key = emit.text_operand(Fragment({{false, emit.ident(*partition), 0}}, pf->type, dialect)).as_value();
    std::vector<Fragment::Part> parts{{false, "WITH " + qi + " AS (", 0}};
    parts.insert(parts.end(), sql->parts().begin(), sql->parts().end());
    parts.push_back({false, "), " + qg + " AS (SELECT MAX(" + qr + ") AS " + qmax + ", MIN(" + qr + ") AS " + qfirst
      + " FROM " + qi + " GROUP BY " + key + ") SELECT " + qi + ".* FROM " + qi + " JOIN " + qg
      + " ON " + qi + "." + qr + " = " + qg + "." + qmax + " ORDER BY " + qg + "." + qfirst + " ASC", 0});
    const std::vector<NodePtr> remaining(it, steps.end());
    const auto continuation = helpers.wrap(build_pipeline(var_node("_INPUT", steps[at]->pos), remaining));
    HybridPlan plan;
    plan.dialect = dialect; plan.is_hybrid = true;
    plan.sql_statement = Fragment(parts, SqlKind::Statement, dialect, sql->params(), sql->param_kinds(), sql->caveats());
    plan.sql_prefix_ast = prefix; plan.continuation_ast = continuation; plan.continuation_program = Program("", continuation);
    plan.source_tables = {rel.from}; plan.selected_member = SelectedMember{*partition, revision};
    return plan;
  } catch (const SqlError&) { return std::nullopt; }
}

// The plan for a program nothing of which reaches the database. The
// continuation is the program itself, and the AST it exposes is the program's
// own, so a caller sees the same tree whichever way the plan went.
HybridPlan pure_memory_plan(const Program& program, std::string dialect,
                            const Bindings& bindings) {
  HybridPlan plan;
  plan.dialect = std::move(dialect);
  plan.pure_memory = true;
  plan.continuation_program = program;
  plan.continuation_ast = program.ast();
  plan.source_tables = source_tables(program.ast(), bindings);
  return plan;
}

}  // namespace

HybridPlan Sql::plan_hybrid(const Program& program, const std::string& dialect,
                            const Bindings& bindings, const Options& options) {
  Map::require_target(dialect);
  Bindings checked = bindings;
  checked.check_aliases();

  // Stage 1 first, exactly as the translator runs it, for its verdict. A
  // program stage 1 refuses -- `A += 1; ...`, a bare statement before the
  // result -- is a program no part of which can be pushed down, which is a
  // pure-memory plan and not an exception: "none of it" is one of the
  // planner's answers. Its TREE is not what is planned, though: see "helper
  // assignments" above -- and a tree stage 1 can only express with a clist
  // (`R[1] = 5; ORDERS .> ... .> MAP(_["id"] + R[1])`) is no longer read as
  // a refusal of the whole program: the other four hosts push the prefix
  // before the MAP down, and the translator refuses the keyed list only where
  // it is rendered (plan.helper.indexed-helper-is-carried-as-written).
  ConstScope scope;
  bool identity_barrier = false;
  try {
    scope = const_scope(&checked);
    const auto normalized = normalise(program.ast(), scope.names, scope.root);
    identity_barrier = identity_loss_before_grouping(normalized);
  } catch (const SqlError&) {
    return pure_memory_plan(program, dialect, checked);
  }

  const Statements parts = statements(program.ast());
  const Definitions literals = literal_helpers(parts.leading);
  const Definitions defs = definitions(parts.leading);
  const auto is_relation = [&](const NodePtr& node) {
    return node && node->t == NT::Var && checked.has(node->s) &&
           checked.get(node->s, node->pos).kind() == Binding::Kind::Relation;
  };
  const auto unwound = unwind_through_helpers(parts.result, defs, literals);
  if (unwound.second.empty() || !is_relation(unwound.first)) {
    return pure_memory_plan(program, dialect, checked);
  }
  const NodePtr optimized =
      optimize_ast_logical(build_pipeline(unwound.first, unwound.second));
  auto [source, steps] = unwind_pipeline(optimized);
  if (steps.empty() || !is_relation(source)) return pure_memory_plan(program, dialect, checked);

  const Helpers helpers{parts.leading, defs, checked, scope};

  // The whole pipeline, unless its rows would be a bucket's keys: the
  // translator renders a bare bucket as its keys, and a plan that pushes the
  // whole of `... .> BUCKET(k)` would hand them back as the answer.
  const NodePtr full_ast = helpers.wrap(build_pipeline(source, steps));
  const Program full_program("", full_ast);
  auto full_sql = identity_barrier || rows_are_not_the_value(steps, steps.size())
      ? std::nullopt
      : Sql::try_translate_statement(full_program, dialect, checked, options);
  if (full_sql) {
    HybridPlan plan;
    plan.dialect = dialect;
    plan.sql_statement = std::move(*full_sql);
    plan.sql_prefix_ast = full_ast;
    plan.pure_sql = true;
    plan.source_tables = helpers.tables(full_ast);
    return plan;
  }

  if (auto latest = try_latest_member(source, steps, dialect, checked, options, helpers)) return std::move(*latest);
  if (!identity_barrier) {
  if (auto fallthrough =
          try_plan_fallthrough(source, steps, dialect, checked, options, helpers)) {
    return std::move(*fallthrough);
  }
  }

  // Test prefixes from longest to shortest.  A rejected suffix is normal: the
  // database gets the largest safe prefix and the host keeps the remaining
  // pipeline semantics exactly as written.
  for (std::size_t count = steps.size(); count-- > 0;) {
    if (count == 0) break;
    if (rows_are_not_the_value(steps, count)) continue;
    const std::vector<NodePtr> prefix_steps(steps.begin(), steps.begin() +
                                                     static_cast<std::ptrdiff_t>(count));
    const NodePtr prefix_ast = helpers.wrap(build_pipeline(source, prefix_steps));
    if (identity_barrier) {
      try {
        if (identity_loss_before_grouping(normalise(prefix_ast, scope.names, scope.root), true)) continue;
      } catch (const SqlError&) {
        continue;
      }
    }
    const Program prefix_program("", prefix_ast);
    auto sql = Sql::try_translate_statement(prefix_program, dialect, checked, options);
    if (!sql) continue;

    std::vector<NodePtr> remaining(steps.begin() + static_cast<std::ptrdiff_t>(count),
                                   steps.end());
    const Pos continuation_pos = remaining.front()->pos;
    const NodePtr continuation_ast =
        helpers.wrap(build_pipeline(var_node("_INPUT", continuation_pos), remaining));
    HybridPlan plan;
    plan.dialect = dialect;
    plan.sql_statement = std::move(*sql);
    plan.sql_prefix_ast = prefix_ast;
    plan.continuation_ast = continuation_ast;
    plan.continuation_program = Program("", continuation_ast);
    plan.is_hybrid = true;
    plan.source_tables = helpers.tables(prefix_ast);
    return plan;
  }

  return pure_memory_plan(program, dialect, checked);
}

Value Sql::execute_hybrid(const HybridPlan& plan, const DbRunner& db_runner,
                          Value context) {
  if (plan.pure_memory) {
    if (!plan.continuation_program) {
      throw std::logic_error("pure-memory hybrid plan has no continuation program");
    }
    return plan.continuation_program->run(context);
  }
  if (!plan.sql_statement) {
    throw std::logic_error("SQL hybrid plan has no SQL statement");
  }
  const Value rows = db_runner(plan.sql_statement->as_statement(Mode::Params),
                               plan.sql_statement->bindings());
  if (plan.pure_sql) return rows;
  if (!plan.continuation_program) {
    throw std::logic_error("hybrid plan has no continuation program");
  }
  Value continuation_context = context.clone();
  continuation_context.set(plan.continuation_source_var, rows);
  return plan.continuation_program->run(continuation_context);
}

}  // namespace sel::sql
