// Maximal SQL-prefix planner for relational SEL pipelines.

#include "sel_sql.hpp"

#include "sel_ast.hpp"
#include "sel_sql_map.hpp"

#include <algorithm>
#include <set>
#include <stdexcept>

namespace sel::sql {
namespace {

constexpr std::string_view PIPELINE_OPS[] = {
    "FILTER", "GROUP_BY", "BUCKET", "SELECT_COLS", "MAP", "DISTINCT", "DEDUPE",
    "TAKE", "DROP", "SORT", "SORT_DESC", "SORT_BY", "TOP", "TOP_DESC", "TOP_BY",
    "LINK", "LINK_LEFT"};

constexpr std::string_view SQL_SPECIAL_CALLS[] = {
    "IF", "COND", "COALESCE", "COUNT", "SUM", "AVG", "MIN", "MAX", "RECORD",
    "LAZY_RECORD", "LIST"};

std::string upper_ascii(std::string value) {
  for (char& ch : value) {
    if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
  }
  return value;
}

bool pipeline_op(std::string_view name) {
  return std::find(std::begin(PIPELINE_OPS), std::end(PIPELINE_OPS), name) !=
         std::end(PIPELINE_OPS);
}

std::pair<NodePtr, std::vector<NodePtr>> unwind(const NodePtr& root) {
  std::vector<NodePtr> steps;
  NodePtr current = root;
  while (current && current->t == NT::Call && pipeline_op(current->s) &&
         !current->items.empty()) {
    steps.push_back(current);
    current = current->items.front();
  }
  std::reverse(steps.begin(), steps.end());
  return {current, steps};
}

NodePtr build_pipeline(NodePtr source, const std::vector<NodePtr>& steps) {
  NodePtr current = std::move(source);
  for (const NodePtr& step : steps) {
    auto next = std::make_shared<Node>(*step);
    next->items.clear();
    next->items.push_back(current);
    next->items.insert(next->items.end(), step->items.begin() + 1, step->items.end());
    current = std::move(next);
  }
  return current;
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

void collect_field_references(const NodePtr& node, const std::string& binder,
                              std::vector<std::string>& out) {
  if (!node) return;
  if (node->t == NT::Index && node->l && node->l->t == NT::Var &&
      node->r && node->r->t == NT::Text) {
    const std::string object = upper_ascii(node->l->s);
    if (object == upper_ascii(binder) || object == "_" || object == "_1" ||
        object == "_2") {
      const std::string key = node->r->s;
      const bool seen = std::any_of(out.begin(), out.end(), [&](const std::string& value) {
        return upper_ascii(value) == upper_ascii(key);
      });
      if (!seen) out.push_back(key);
    }
  }
  if (node->l) collect_field_references(node->l, binder, out);
  if (node->r) collect_field_references(node->r, binder, out);
  for (const NodePtr& item : node->items) collect_field_references(item, binder, out);
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

void collect_tables(const NodePtr& node, const Bindings& bindings,
                    std::vector<std::string>& out, std::set<std::string>& seen) {
  if (!node) return;
  if (node->t == NT::Var && bindings.has(node->s)) {
    const Binding& binding = bindings.get(node->s, node->pos);
    if (binding.kind() == Binding::Kind::Relation) {
      const std::string& table = binding.as_relation().from;
      if (seen.insert(table).second) out.push_back(table);
      return;
    }
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

  std::vector<std::string> downstream_refs;
  for (std::size_t i = map_index + 1; i < steps.size(); ++i) {
    collect_field_references(steps[i], "_", downstream_refs);
  }
  for (const auto& pair : custom) {
    if (std::any_of(downstream_refs.begin(), downstream_refs.end(),
                    [&](const std::string& ref) {
                      return upper_ascii(ref) == upper_ascii(pair.first->s);
                    })) {
      return std::nullopt;
    }
  }

  std::vector<std::string> projected;
  for (const auto& pair : pushable) projected.push_back(pair.first->s);
  std::vector<std::string> dependencies;
  for (const auto& pair : custom) {
    std::vector<std::string> refs;
    collect_field_references(pair.second, details->binder, refs);
    for (const std::string& ref : refs) {
      const bool already_projected = std::any_of(
          projected.begin(), projected.end(), [&](const std::string& value) {
            return upper_ascii(value) == upper_ascii(ref);
          });
      const bool already_dependency = std::any_of(
          dependencies.begin(), dependencies.end(), [&](const std::string& value) {
            return upper_ascii(value) == upper_ascii(ref);
          });
      if (!already_projected && !already_dependency) dependencies.push_back(ref);
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

  auto continuation_map = copy_node(map_step);
  continuation_map->items.clear();
  continuation_map->items.push_back(var_node("_INPUT", map_step->pos));
  if (details->explicit_binder) continuation_map->items.push_back(map_step->items[1]);
  continuation_map->items.push_back(details->body);

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

  const NodePtr optimized = optimize_ast_logical(program.ast());
  auto [source, steps] = unwind(optimized);
  if (!source || source->t != NT::Var || steps.empty() ||
      !checked.has(source->s) ||
      checked.get(source->s, source->pos).kind() != Binding::Kind::Relation) {
    return pure_memory_plan(program, dialect, checked);
  }

  const NodePtr full_ast = build_pipeline(source, steps);
  const Program full_program("", full_ast);
  if (auto full_sql = Sql::try_translate_statement(full_program, dialect, checked, options)) {
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
