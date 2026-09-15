// Maximal SQL-prefix planner for relational SEL pipelines.
//
// The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
// and is pinned by sql/cases/25-hybrid-plans.sqlt. Two parts of it were wrong
// here: the planner unwound the RAW tree, so `X = ORDERS; X .> TAKE(1)` -- a
// seq, not a pipeline -- was classified as pure memory where the three hosts
// that ran stage 1 first pushed it down; and it carried its own copy of the
// pipeline vocabulary and the unwind/build pair, which sel.cpp now shares
// through sel_ast.hpp so this host has one of each.

#include "sel_sql.hpp"

#include "sel_ast.hpp"
#include "sel_sql_map.hpp"
#include "sel_sql_node.hpp"
#include "sel_sql_stage1.hpp"

#include <algorithm>
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

bool contains_unsupported_sql(const NodePtr& node, const std::string& dialect) {
  if (!node) return false;
  if (node->t == NT::Call) {
    const bool special = std::find(std::begin(SQL_SPECIAL_CALLS),
                                   std::end(SQL_SPECIAL_CALLS), node->s) !=
                         std::end(SQL_SPECIAL_CALLS);
    if (!special) {
      const Entry* entry = Map::entry(dialect, Section::Funcs, upper_ascii(node->s));
      if (!entry || entry->kind == EntryKind::Refusal) return true;
    }
  }
  if (node->l && contains_unsupported_sql(node->l, dialect)) return true;
  if (node->r && contains_unsupported_sql(node->r, dialect)) return true;
  for (const NodePtr& item : node->items) {
    if (contains_unsupported_sql(item, dialect)) return true;
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
  return name == "FILTER" || name == "SORT_BY" || name == "TOP_BY" || name == "TAKE" ||
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
  if (!out.body || out.body->t != NT::Call ||
      (out.body->s != "RECORD" && out.body->s != "LAZY_RECORD") ||
      out.body->items.size() % 2 != 0) {
    return std::nullopt;
  }
  for (std::size_t i = 0; i < out.body->items.size(); i += 2) {
    if (out.body->items[i]->t != NT::Text) return std::nullopt;
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

std::optional<HybridPlan> try_plan_fallthrough(
    const NodePtr& source, const std::vector<NodePtr>& steps,
    const std::string& dialect, const Bindings& bindings, const Options& options) {
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
    if (contains_unsupported_sql(pair.second, dialect)) custom.push_back(pair);
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
  const NodePtr rewritten_ast = build_pipeline(source, rewritten_steps);
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

  HybridPlan plan;
  plan.dialect = dialect;
  plan.sql_statement = std::move(*sql);
  plan.sql_prefix_ast = rewritten_ast;
  plan.continuation_ast = continuation_map;
  plan.continuation_program = Program("", continuation_map);
  plan.is_hybrid = true;
  plan.source_tables = source_tables(rewritten_ast, bindings);
  return plan;
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

  // Stage 1 first, exactly as the translator runs it, so the tree unwound
  // below is the one a prefix will be translated from. A program stage 1
  // refuses -- `A += 1; ...`, a bare statement before the result -- is a
  // program no part of which can be pushed down, which is a pure-memory plan
  // and not an exception: "none of it" is one of the planner's answers. So is
  // a tree stage 1 can only express with a clist, which to_node() reports as
  // null: the translator refuses that as a statement shape, and there is no
  // prefix of a keyed list to try.
  NodePtr normalized;
  try {
    ConstScope scope = const_scope(&checked);
    normalized = normalise(program.ast(), scope.names, scope.root)->to_node();
  } catch (const SqlError&) {
    return pure_memory_plan(program, dialect, checked);
  }
  if (!normalized) return pure_memory_plan(program, dialect, checked);

  const NodePtr optimized = optimize_ast_logical(normalized);
  auto [source, steps] = unwind_pipeline(optimized);
  if (!source || source->t != NT::Var || steps.empty() ||
      !checked.has(source->s) ||
      checked.get(source->s, source->pos).kind() != Binding::Kind::Relation) {
    return pure_memory_plan(program, dialect, checked);
  }

  // The whole pipeline, unless its rows would be a bucket's keys: the
  // translator renders a bare bucket as its keys, and a plan that pushes the
  // whole of `... .> BUCKET(k)` would hand them back as the answer.
  const NodePtr full_ast = build_pipeline(source, steps);
  const Program full_program("", full_ast);
  auto full_sql = bucket_rows_are_keys(steps, steps.size())
      ? std::nullopt
      : Sql::try_translate_statement(full_program, dialect, checked, options);
  if (full_sql) {
    HybridPlan plan;
    plan.dialect = dialect;
    plan.sql_statement = std::move(*full_sql);
    plan.sql_prefix_ast = full_ast;
    plan.pure_sql = true;
    plan.source_tables = source_tables(full_ast, checked);
    return plan;
  }

  if (auto fallthrough =
          try_plan_fallthrough(source, steps, dialect, checked, options)) {
    return std::move(*fallthrough);
  }

  // Test prefixes from longest to shortest.  A rejected suffix is normal: the
  // database gets the largest safe prefix and the host keeps the remaining
  // pipeline semantics exactly as written.
  for (std::size_t count = steps.size(); count-- > 0;) {
    if (count == 0) break;
    if (bucket_rows_are_keys(steps, count)) continue;
    const std::vector<NodePtr> prefix_steps(steps.begin(), steps.begin() +
                                                     static_cast<std::ptrdiff_t>(count));
    const NodePtr prefix_ast = build_pipeline(source, prefix_steps);
    const Program prefix_program("", prefix_ast);
    auto sql = Sql::try_translate_statement(prefix_program, dialect, checked, options);
    if (!sql) continue;

    std::vector<NodePtr> remaining(steps.begin() + static_cast<std::ptrdiff_t>(count),
                                   steps.end());
    const Pos continuation_pos = remaining.front()->pos;
    const NodePtr continuation_ast =
        build_pipeline(var_node("_INPUT", continuation_pos), remaining);
    HybridPlan plan;
    plan.dialect = dialect;
    plan.sql_statement = std::move(*sql);
    plan.sql_prefix_ast = prefix_ast;
    plan.continuation_ast = continuation_ast;
    plan.continuation_program = Program("", continuation_ast);
    plan.is_hybrid = true;
    plan.source_tables = source_tables(prefix_ast, checked);
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
