// Focused C++ SQL tests for the advanced relational-plan paths.

#include "../sel.hpp"
#include "../sel_sql.hpp"
#include "../sel_sql_map.hpp"

#include <chrono>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

using sel::sql::Binding;
using sel::sql::Bindings;
using sel::sql::Sql;
using sel::sql::SqlKind;

Binding orders() {
  return Binding::relation(
      "orders", "o",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", std::nullopt, SqlKind::Num)}});
}

Binding customers() {
  return Binding::relation(
      "customers", "c",
      {{"ID", Binding::column("id", std::nullopt, SqlKind::Num)},
       {"NAME", Binding::column("name", std::nullopt, SqlKind::Text)}});
}

}  // namespace

int main() {
  const Bindings bindings({{"ORDERS", orders()}, {"CUSTOMERS", customers()}});
  // A joined row is its promoted fields (spec §7.4): `customer_id` and
  // `name` are on one side each, so `_` reads them; `id` is on both.
  const sel::Program inner = sel::compile(
      "MAP(LINK(ORDERS, CUSTOMERS, O, C, O[\"CUSTOMER_ID\"] == C[\"ID\"]), "
      "RECORD(\"CID\", _[\"CUSTOMER_ID\"], \"NAME\", _[\"NAME\"]))");
  const std::string inner_sql =
      Sql::translate_statement(inner, "mariadb", bindings).as_statement();
  const std::string wanted_inner =
      "SELECT `o`.`customer_id` AS `CID`, `c`.`name` AS `NAME` FROM `orders` `o` "
      "INNER JOIN `customers` `c` ON (`o`.`customer_id` = `c`.`id`)";
  if (inner_sql != wanted_inner) {
    std::cerr << "join SQL mismatch\n got:  " << inner_sql << "\n want: "
              << wanted_inner << "\n";
    return 1;
  }

  // The binders are scoped to the LINK's predicate (spec §7.4): `_1`, `_2`,
  // `O`, `C` and the relations' names are not names in a later step, and a
  // field both sides have is not a field of the row.
  const auto refuses = [&](const std::string& source, const std::string& code) {
    try {
      Sql::translate_statement(sel::compile(source), "mariadb", bindings);
    } catch (const sel::sql::SqlError& e) {
      if (e.code() == code) return true;
      std::cerr << source << "\n refused with " << e.code() << ", want " << code << "\n";
      return false;
    }
    std::cerr << source << "\n translated, want " << code << "\n";
    return false;
  };
  const std::string link = "LINK(ORDERS, CUSTOMERS, O, C, O[\"CUSTOMER_ID\"] == C[\"ID\"])";
  if (!refuses("MAP(" + link + ", RECORD(\"X\", _1[\"ID\"]))", "E_SQL_UNBOUND") ||
      !refuses("MAP(" + link + ", RECORD(\"X\", _2[\"NAME\"]))", "E_SQL_UNBOUND") ||
      !refuses("MAP(" + link + ", RECORD(\"X\", O[\"ID\"]))", "E_SQL_UNBOUND") ||
      !refuses("FILTER(" + link + ", C[\"ID\"] > 1)", "E_SQL_UNBOUND") ||
      !refuses("MAP(" + link + ", RECORD(\"X\", _[\"ID\"]))", "E_SQL_SHAPE")) {
    return 1;
  }

  const sel::Program derived = sel::compile(
      "FILTER(MAP(ORDERS, O, RECORD(\"ID\", O[\"ID\"], \"CID\", O[\"CUSTOMER_ID\"])), "
      "R, R[\"ID\"] > 1)");
  const std::string derived_sql =
      Sql::translate_statement(derived, "mariadb", bindings).as_statement();
  const std::string wanted_derived =
      "SELECT `_sub1`.* FROM (SELECT `id` AS `ID`, `customer_id` AS `CID` "
      "FROM `orders` `o`) `_sub1` WHERE (`_sub1`.`ID` > 1)";
  if (derived_sql != wanted_derived) {
    std::cerr << "derived SQL mismatch\n got:  " << derived_sql << "\n want: "
              << wanted_derived << "\n";
    return 1;
  }

  const sel::Program hybrid_program =
      sel::compile("MAP(TAKE(ORDERS, 1), O, ABORT(\"not pushed\"))");
  const sel::sql::HybridPlan hybrid =
      Sql::plan_hybrid(hybrid_program, "mariadb", bindings);
  if (!hybrid.is_hybrid || hybrid.pure_sql || hybrid.pure_memory ||
      !hybrid.sql_statement || !hybrid.continuation_program ||
      hybrid.source_tables.size() != 1 || hybrid.source_tables[0] != "orders") {
    std::cerr << "hybrid plan did not split at the unsupported suffix\n";
    return 1;
  }

  // The hybrid planner's contract, the parts a host-local check can see.
  // sql/cases/25-hybrid-plans.sqlt holds the language-neutral version; what is
  // here is what the runner cannot observe: the shared physical tree behind
  // run(), and stage 1 running before the prefix search with the same binding
  // set the other checks use.
  const sel::Program helper = sel::compile("X = ORDERS; X .> TAKE(1)");
  const sel::sql::HybridPlan helper_plan = Sql::plan_hybrid(helper, "mariadb", bindings);
  if (!helper_plan.pure_sql || helper_plan.source_tables != std::vector<std::string>{"orders"}) {
    std::cerr << "a helper assignment must be normalised before planning\n";
    return 1;
  }

  const sel::Program refused = sel::compile("A += 1; ORDERS .> TAKE(1)");
  const sel::sql::HybridPlan refused_plan = Sql::plan_hybrid(refused, "mariadb", bindings);
  if (!refused_plan.pure_memory || refused_plan.sql_statement ||
      !refused_plan.continuation_program ||
      refused_plan.continuation_program->ast() != refused.ast() ||
      refused_plan.continuation_ast != refused.ast() ||
      refused_plan.source_tables != std::vector<std::string>{"orders"}) {
    std::cerr << "a program stage 1 refuses must be a pure-memory plan over the original\n";
    return 1;
  }

  const sel::Program reusable = sel::compile(
      "(3, 1, 2) .> FILTER(NOT (_ < 1 + 1)) .> MAP(RECORD(\"x\", _, \"y\", _ * 2)) .> TAKE(2 * 1)");
  const sel::Program copy = reusable;
  const std::string first = reusable.run().dump();
  if (reusable.physical_ast() != reusable.physical_ast() ||
      copy.physical_ast() != reusable.physical_ast() ||
      reusable.physical_ast() == reusable.ast() || reusable.run().dump() != first) {
    std::cerr << "the physical tree must be built once and shared by copies\n";
    return 1;
  }

  // A plan's continuation reports errors where run() does. The planner folds
  // one tree for both halves of a split, so a hoisted literal in the
  // continuation carries the position the in-memory half will report
  // (spec §6.3; ctl.if.constant-condition-* and op.logic.*-keeps-the-*-position
  // are the run() half). sql/cases/25-hybrid-plans.sqlt pins the SQL side of
  // these; only executing the plan can see the position the memory side reports.
  // This is also the arm that never fired here: the C++ IF fold tested for four
  // items and so reported the IF's column by accident while the hosts that
  // folded reported the literal's.
  const sel::Value rows = sel::Value::list(
      {sel::Value::record({"id"}, {sel::Value::text("1")}),
       sel::Value::record({"id"}, {sel::Value::text("2")})});
  const auto failure = [](const auto& fn) -> std::string {
    try {
      fn();
      return "no error";
    } catch (const sel::SelError& e) {
      return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
    }
  };
  // The rows with a helper assignment: the
  // planner used to plan stage 1's tree, in which a helper is inlined at its
  // definition-site position, so the continuation reported `Y = "x"; ... + Y`
  // at 1:5 where run() reports the read at 1:45, and evaluated the helper per
  // row instead of once. LABEL is a context variable, so a helper defined from
  // it is something no fold turns into a literal and the assignment has to be
  // carried as written; the ABORT helper is evaluated once, before the
  // pipeline, which only a pure-memory plan over the original can honour.
  struct Probe { const char* source; const char* kind; const char* want; };
  const Probe probes[] = {
      {"ORDERS .> TAKE(2) .> MAP(IF(TRUE, \"x\", 1) >= _[\"id\"])", "hybrid", "E_NOT_NUM@1:26"},
      {"ORDERS .> TAKE(2) .> FILTER((FALSE AND TRUE) + _[\"id\"] > 0)", "hybrid", "E_NOT_NUM@1:36"},
      {"ORDERS .> FILTER(IF(TRUE, \"x\", 1) >= _[\"id\"])", "pure_memory", "E_NOT_NUM@1:18"},
      {"Y = \"x\"; ORDERS .> TAKE(2) .> MAP(_[\"id\"] + Y)", "hybrid", "E_NOT_NUM@1:45"},
      {"X = ORDERS .> TAKE(2); Y = (FALSE AND TRUE); X .> MAP(Y + _[\"id\"])", "hybrid", "E_NOT_NUM@1:55"},
      {"Y = \"a\" & \"b\"; ORDERS .> TAKE(2) .> MAP(1 + Y)", "hybrid", "E_NOT_NUM@1:45"},
      {"Z = \"abc\"; ORDERS .> TAKE(1) .> FILTER(_[\"id\"] > Z)", "hybrid", "E_NOT_NUM@1:50"},
      {"Y = \"x\"; (ORDERS .> TAKE(2)) .> MAP(_[\"id\"] + Y)", "hybrid", "E_NOT_NUM@1:47"},
      {"Y = LABEL; ORDERS .> TAKE(2) .> MAP(_[\"id\"] + Y)", "hybrid", "E_NOT_NUM@1:47"},
      {"C = COUNT(ORDERS) + LABEL; ORDERS .> TAKE(2) .> MAP(_[\"id\"] + C)", "hybrid", "E_NOT_NUM@1:21"},
      {"X = ORDERS .> TAKE(2); X .> MAP(COUNT(X) + _[\"id\"] + \"x\")", "hybrid", "E_NOT_NUM@1:54"},
      {"Y = ABORT(\"x\"); ORDERS .> TAKE(2) .> MAP(Y)", "pure_memory", "E_ABORT@1:11"},
      // SEL-0047: a continuation on line 3 reports its error on line 3.
      {"X = ORDERS .> TAKE(2);\nX .> MAP(COUNT(X) + _[\"id\"]\n   + \"x\")", "hybrid", "E_NOT_NUM@3:6"},
  };
  for (const Probe& probe : probes) {
    const sel::Program program = sel::compile(probe.source);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", bindings);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    if (kind != probe.kind) {
      std::cerr << probe.source << ": expected a " << probe.kind << " plan, got " << kind << "\n";
      return 1;
    }
    sel::Value context = sel::Value::none();
    context.set("ORDERS", rows);
    context.set("LABEL", sel::Value::text("x"));
    const std::string in_memory = failure([&] { sel::Value c = context.clone(); program.run(c); });
    const std::string executed = failure([&] {
      Sql::execute_hybrid(plan, [&](const std::string&, const std::vector<sel::Value>&) { return rows; },
                          context);
    });
    if (in_memory != probe.want || executed != probe.want) {
      std::cerr << probe.source << ": run() reports " << in_memory << ", the executed plan "
                << executed << ", want " << probe.want << "\n";
      return 1;
    }
  }

  // An executed plan answers what run() answers. Review 2026-09-15 findings
  // I, AI and P: plans that pushed a bare bucket to the end, re-grouped a
  // bucket, or re-applied a MAP's RECORD over rows the SQL had already
  // projected, all answered something else than run(). The database is stood
  // in for by SEL itself: the SQL prefix's own AST evaluated over the same
  // rows is what the SQL would return, which is the planner's premise.
  const Bindings full_orders({{"ORDERS", Binding::relation(
      "orders", "o",
      {{"ID", Binding::column("id", "o", SqlKind::Num)},
       {"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)},
       {"AMOUNT", Binding::column("amount", "o", SqlKind::Num)},
       {"NAME", Binding::column("name", "o", SqlKind::Text)}})}});
  const sel::Value order_rows = sel::evaluate(
      "LIST(RECORD('id', '1', 'customer_id', '7', 'amount', '10', 'name', 'a'), "
      "RECORD('id', '2', 'customer_id', '7', 'amount', '5', 'name', 'b'), "
      "RECORD('id', '3', 'customer_id', '9', 'amount', '7', 'name', 'c'))");
  const auto outcome = [](const auto& fn) -> std::string {
    try {
      return fn().dump();
    } catch (const sel::SelError& e) {
      return e.code() + "@" + std::to_string(e.line()) + ":" + std::to_string(e.col());
    }
  };
  struct Shape { const char* source; const char* kind; };
  const Shape shapes[] = {
      {"ORDERS .> MAP(RECORD(\"cid\", _[\"customer_id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> TAKE(2)", "hybrid"},
      {"ORDERS .> MAP(RECORD(\"plus\", _[\"amount\"] + 1, \"shout\", REPEAT(_[\"name\"], 2))) .> SORT_BY(_[\"plus\"])", "hybrid"},
      {"ORDERS .> MAP(RECORD(\"customer_id\", _[\"customer_id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> BUCKET(_[\"customer_id\"])", "pure_memory"},
      {"ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> FILTER(_[\"name\"] $== \"a\")", "pure_memory"},
      {"ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"row\", REPEAT(GET(_, \"name\"), 2))) .> TAKE(3)", "pure_memory"},
      {"ORDERS .> MAP(RECORD(\"customer_id\", _[\"amount\"], \"tag\", REPEAT(_[\"customer_id\"], 2))) .> TAKE(3)", "pure_memory"},
      {"ORDERS .> TAKE(5) .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2))) .> DEDUPE()", "hybrid"},
      {"ORDERS .> FILTER(_[\"amount\"] > 6) .> BUCKET(_[\"customer_id\"])", "hybrid"},
      {"ORDERS .> BUCKET(_[\"customer_id\"])", "pure_memory"},
      {"ORDERS .> BUCKET(_[\"customer_id\"]) .> TAKE(1)", "pure_memory"},
      {"ORDERS .> BUCKET(_[\"customer_id\"]) .> FILTER(COUNT(_) > 1)", "pure_memory"},
      {"ORDERS .> BUCKET(_[\"customer_id\"]) .> BUCKET(COUNT(_)) .> MAP(RECORD(\"size\", _K, \"n\", COUNT(_)))", "pure_memory"},
      {"ORDERS .> MAP(r, RECORD(\"id\", r[\"id\"], \"shout\", REPEAT(r[\"name\"], 2))) .> SORT_BY(s, s[\"name\"])", "pure_memory"},
      // The FILTER no longer moves in front of the MAP (it would renumber the
      // answer's keys); over the MAP's derived table sqlite cannot render its NUM
      // guard, so nothing pushes down. A later step that renumbers again lets the
      // swap through.
      {"ORDERS .> MAP(r, RECORD(\"id\", r[\"id\"], \"shout\", REPEAT(r[\"name\"], 2))) .> FILTER(s, s[\"id\"] > 1)", "pure_memory"},
      {"ORDERS .> MAP(r, RECORD(\"id\", r[\"id\"], \"shout\", REPEAT(r[\"name\"], 2))) .> FILTER(s, s[\"id\"] > 1) .> TAKE(5)", "pure_memory"},
      // REPEAT can raise, so the FILTER stays behind the MAP (spec §7.3); a MAP
      // that cannot raise lets it through.
      {"ORDERS .> MAP(r, RECORD(\"id\", r[\"id\"], \"plus\", r[\"amount\"] + 1)) .> FILTER(s, s[\"id\"] > 1) .> TAKE(5)", "pure_sql"},
      {"ORDERS .> MAP(RECORD(\"Name\", _[\"name\"], \"shout\", REPEAT(_[\"name\"], 2))) .> TAKE(2)", "pure_memory"},
      {"ORDERS .> MAP(RECORD(\"x\", _[\"id\"], \"X\", REPEAT(_[\"name\"], 2))) .> TAKE(2)", "hybrid"},
      {"ORDERS .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", (REPEAT(_[\"name\"], 2), 1))) .> TAKE(2)", "hybrid"},
      {"ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> TAKE(1) .> FILTER(_[\"n\"] > 1)", "hybrid"},
      {"ORDERS .> BUCKET(_[\"customer_id\"], RECORD(\"cid\", _K, \"n\", COUNT(_))) .> DROP(1) .> FILTER(_[\"n\"] > 1)", "hybrid"},
      {"ORDERS .> BUCKET(RECORD(\"c\", _[\"customer_id\"]), RECORD(\"n\", COUNT(_)))", "pure_sql"},
      {"ORDERS .> BUCKET(RECORD(\"c\", _[\"customer_id\"])) .> MAP(RECORD(\"n\", COUNT(_)))", "pure_memory"},
      // Finding AJ again, on the value side: a folded helper as a TAKE count
      // and as a REPEAT count, a helper as the pipeline's source, and a
      // non-literal helper carried in front of both halves.
      {"N = 1 + 1; X = ORDERS .> TAKE(N) .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2))); X .> FILTER(_[\"id\"] > 1) .> TAKE(5)", "hybrid"},
      {"LIMIT = 2; ORDERS .> TAKE(LIMIT) .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], LIMIT)))", "hybrid"},
      {"C = COUNT(ORDERS); ORDERS .> FILTER(_[\"amount\"] > C) .> MAP(RECORD(\"id\", _[\"id\"], \"shout\", REPEAT(_[\"name\"], 2)))", "hybrid"},
  };
  for (const Shape& shape : shapes) {
    const sel::Program program = sel::compile(shape.source);
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "sqlite", full_orders);
    const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
    if (kind != shape.kind) {
      std::cerr << shape.source << ": expected a " << shape.kind << " plan, got " << kind << "\n";
      return 1;
    }
    const auto context = [&] {
      sel::Value c = sel::Value::none();
      c.set("ORDERS", order_rows);
      return c;
    };
    const auto prefix_in_memory = [&](const std::string&, const std::vector<sel::Value>&) {
      sel::Value c = context();
      return sel::Program("", plan.sql_prefix_ast).run(c);
    };
    const std::string want = outcome([&] { sel::Value c = context(); return program.run(c); });
    const std::string got = outcome([&] { return Sql::execute_hybrid(plan, prefix_in_memory, context()); });
    if (got != want) {
      std::cerr << shape.source << ": the executed plan answers " << got << ", run() " << want << "\n";
      return 1;
    }
  }

  // A joined row names its left side after the variable the pipeline started from
  // (spec 7.4), so a continuation whose LINK is NOT its first step must still be fed
  // the rows under that name -- and a step that also reads the name (a self-join) must
  // not be split off, or it would read the cut rows where run() reads the relation.
  // The database is stood in for by the SQL prefix's own AST evaluated in memory. Also
  // pinned here: a sorted, limited relation is never grouped in SQL (the GROUP BY would
  // lose the groups' order), the grouping runs in memory over rows SQL sorted.
  {
    const Bindings link_bindings({{"ORDERS", Binding::relation(
        "orders", "o",
        {{"ID", Binding::column("id", "o", SqlKind::Num)},
         {"CUSTOMER_ID", Binding::column("customer_id", "o", SqlKind::Num)},
         {"NAME", Binding::column("name", "o", SqlKind::Text)}})},
        {"CUSTOMERS", Binding::relation(
        "customers", "c",
        {{"ID", Binding::column("id", "c", SqlKind::Num)},
         {"NAME", Binding::column("name", "c", SqlKind::Text)}})}});
    const sel::Value link_orders = sel::evaluate(
        "LIST(RECORD('id', '1', 'customer_id', '7', 'name', 'alpha'), "
        "RECORD('id', '2', 'customer_id', '7', 'name', 'gamma'), "
        "RECORD('id', '3', 'customer_id', '9', 'name', 'Alpha'), "
        "RECORD('id', '4', 'customer_id', '9', 'name', 'delta'), "
        "RECORD('id', '5', 'customer_id', '11', 'name', 'zeta'), "
        "RECORD('id', '6', 'customer_id', '11', 'name', 'eta'))");
    const sel::Value link_customers = sel::evaluate(
        "LIST(RECORD('id', '7', 'name', 'ann'), RECORD('id', '9', 'name', 'bob'), "
        "RECORD('id', '11', 'name', 'cy'))");
    const Shape link_shapes[] = {
        {"ORDERS .> SORT_BY(_[\"id\"]) .> TAKE(4) .> FILTER(COUNT(SPLIT(_[\"name\"], \"a\")) > 0) .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"]) .> MAP(RECORD(\"n\", _[\"ORDERS\"][\"name\"], \"c\", _[\"CUSTOMERS\"][\"name\"]))", "hybrid"},
        {"ORDERS .> SORT_BY(_[\"id\"]) .> TAKE(4) .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"] AND COUNT(SPLIT(_1[\"name\"], \"a\")) > 0) .> MAP(RECORD(\"n\", _[\"ORDERS\"][\"name\"], \"c\", _[\"CUSTOMERS\"][\"name\"]))", "hybrid"},
        {"ORDERS .> SORT_BY(_[\"id\"]) .> TAKE(4) .> LINK(ORDERS, O, P, O[\"id\"] + 2 == P[\"id\"] AND COUNT(SPLIT(O[\"name\"], \"a\")) > 0) .> MAP(RECORD(\"o\", _[\"O\"][\"id\"], \"p\", _[\"P\"][\"id\"]))", "hybrid"},
        {"ORDERS .> SORT_BY(_[\"id\"], \"DESC\") .> TAKE(5) .> BUCKET(_[\"customer_id\"], RECORD(\"c\", _K, \"n\", COUNT(_)))", "hybrid"},
    };
    for (const Shape& shape : link_shapes) {
      const sel::Program program = sel::compile(shape.source);
      const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", link_bindings);
      const std::string kind = plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
      if (kind != shape.kind) {
        std::cerr << shape.source << ": expected a " << shape.kind << " plan, got " << kind << "\n";
        return 1;
      }
      const auto context = [&] {
        sel::Value c = sel::Value::none();
        c.set("ORDERS", link_orders);
        c.set("CUSTOMERS", link_customers);
        return c;
      };
      const auto prefix_in_memory = [&](const std::string&, const std::vector<sel::Value>&) {
        sel::Value c = context();
        return sel::Program("", plan.sql_prefix_ast).run(c);
      };
      const std::string want = outcome([&] { sel::Value c = context(); return program.run(c); });
      const std::string got = outcome([&] { return Sql::execute_hybrid(plan, prefix_in_memory, context()); });
      if (got != want) {
        std::cerr << shape.source << ": the executed plan answers " << got << ", run() " << want << "\n";
        return 1;
      }
    }
  }

  // The runner contract: the statement in `params` mode with
  // `bindings()` in placeholder order -- text literals as `?`, numbers inlined
  // -- in every host, so a driver binds what it is handed as it is. Lisp handed
  // the runner inline SQL and its creation-order slot list.
  {
    const sel::Program program = sel::compile(
        "ORDERS .> FILTER(FIND(\"needle\", \"hay-\" & _[\"name\"]) > 0 AND _[\"amount\"] > 5)"
        " .> MAP(RECORD(\"g\", RGROUPS(\"(a)\", _[\"name\"])))");
    const sel::sql::HybridPlan plan = Sql::plan_hybrid(program, "mariadb", full_orders);
    if (plan.pure_sql || plan.pure_memory) {
      std::cerr << "runner contract: expected a hybrid plan\n";
      return 1;
    }
    bool called = false;
    std::string seen_sql;
    std::string seen_params;
    sel::Value context = sel::Value::none();
    context.set("ORDERS", order_rows);
    Sql::execute_hybrid(plan, [&](const std::string& sql, const std::vector<sel::Value>& params) {
      called = true;
      seen_sql = sql;
      for (const sel::Value& v : params) {
        if (!seen_params.empty()) seen_params += ",";
        seen_params += v.dump();
      }
      return sel::Value::list({});
    }, context);
    if (!called) {
      std::cerr << "runner contract: the runner was not called\n";
      return 1;
    }
    const auto has = [&](const char* needle) { return seen_sql.find(needle) != std::string::npos; };
    if (!has("?") || has("'needle'") || has("'hay-'")) {
      std::cerr << "runner contract: text literals must be placeholders, got " << seen_sql << "\n";
      return 1;
    }
    if (has("?, 5") || !has("> 5")) {
      std::cerr << "runner contract: a number is inlined, got " << seen_sql << "\n";
      return 1;
    }
    if (seen_params != "t\"hay-\",t\"needle\"") {
      std::cerr << "runner contract: bindings in placeholder order, got " << seen_params << "\n";
      return 1;
    }
  }

  // --- scope, kinds, rendering and the hybrid planner. Each block says what it
  // holds.
  {
    int wave = 0;
    const auto fail = [&](const std::string& what, const std::string& got, const std::string& want) {
      std::cerr << "wave: " << what << "\n got:  " << got << "\n want: " << want << "\n";
      wave = 1;
    };
    const auto col = [](const char* c, SqlKind k) { return Binding::column(c, std::string("t"), k); };
    const Bindings wb({{"N", col("n", SqlKind::Num)},
                       {"U", col("u", SqlKind::Unknown)},
                       {"Y", col("y", SqlKind::Num)},
                       {"ITEMS", Binding::relation(
                            "oi", std::string("oi"),
                            {{"QTY", Binding::column("qty", std::string("oi"), SqlKind::Unknown)}},
                            std::nullopt, std::string("oi.a=o.id OR oi.b=o.id"))}});
    const auto expr = [&](const std::string& source, const std::string& dialect = "mariadb") {
      try {
        return Sql::translate(sel::compile(source), dialect, wb).as_value();
      } catch (const sel::sql::SqlError& e) {
        return std::string("ERR ") + e.code();
      }
    };
    const auto check = [&](const std::string& what, const std::string& got, const std::string& want) {
      if (got != want) fail(what, got, want);
    };

    // A helper keeps the scope it was written in; a binder that reuses
    // a free name of the helper does not capture it.
    check("def not captured by a binder",
          expr("X = Y + 1; ALL((1, 2), Y, Y > X)"),
          "((1 > (`t`.`y` + 1)) AND (2 > (`t`.`y` + 1)))");
    // A supplied correlate is parenthesised.
    check("correlate parenthesised",
          expr("ANY(ITEMS, I, I[\"QTY\"] > 0)").find("WHERE (oi.a=o.id OR oi.b=o.id) AND") != std::string::npos
              ? "parenthesised" : "bare",
          "parenthesised");
    // UNKNOWN through IF is UNKNOWN, so it is guarded, not laundered.
    check("IF cannot launder", expr("IF(N > 0, U, 1) + 1 == 2").find("REGEXP") != std::string::npos ? "guarded" : "bare",
          "guarded");
    // §5a: an UNKNOWN relation SUM body is guarded all or nothing; SQLite refuses.
    check("SUM guarded as a whole",
          expr("SUM(ITEMS, _[\"QTY\"])").find("COUNT(*) = COUNT(CASE WHEN") != std::string::npos ? "whole" : "plain",
          "whole");
    check("SUM refused on sqlite", expr("SUM(ITEMS, _[\"QTY\"])", "sqlite"), "ERR E_SQL_UNSUPPORTED");
    // docs/internals/sql-translation.md §11.6: counts are exact, clamped at 2^63 - 1, and a scale on a whole
    // number is fine.
    {
      const Bindings rb({{"R", orders()}});
      const auto stmt = [&](const std::string& source) {
        try { return Sql::translate_statement(sel::compile(source), "postgresql", rb).as_statement(); }
        catch (const sel::sql::SqlError& e) { return std::string("ERR ") + e.code(); }
      };
      check("take with a scale", stmt("R .> TAKE(2.0)"), "SELECT \"o\".* FROM \"orders\" \"o\" LIMIT 2");
      check("take clamped", stmt("R .> TAKE(99999999999999999999999)"),
            "SELECT \"o\".* FROM \"orders\" \"o\" LIMIT 9223372036854775807");
      check("drop merged and clamped",
            stmt("R .> DROP(9223372036854775807) .> DROP(1)"),
            "SELECT \"o\".* FROM \"orders\" \"o\" OFFSET 9223372036854775807");
      check("a fractional count is still refused", stmt("R .> TAKE(1.5)"), "ERR E_NOT_INT");
      // DISTINCT after a sort stays in memory (refused here).
      check("distinct after a sort", stmt("R .> SORT_BY(_[\"ID\"]) .> MAP(RECORD(\"a\", _[\"CUSTOMER_ID\"])) .> DISTINCT"),
            "ERR E_SQL_SHAPE");
    }
    // A doubling helper chain is refused by the size budget in
    // bounded time, and a long chain by depth -- neither is exponential or a crash.
    {
      std::string doubling = "X0 = N;";
      for (int i = 1; i <= 40; ++i) doubling += " X" + std::to_string(i) + " = X" + std::to_string(i - 1) + " + X" + std::to_string(i - 1) + ";";
      doubling += " X40 > 0";
      const auto t0 = std::chrono::steady_clock::now();
      check("doubling chain refused", expr(doubling), "ERR E_SQL_SIZE");
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::steady_clock::now() - t0).count();
      if (ms > 20000) fail("doubling chain took too long", std::to_string(ms) + " ms", "< 20000 ms");
      std::string chain = "X0 = 1;";
      for (int i = 1; i <= 250; ++i) chain += " X" + std::to_string(i) + " = X" + std::to_string(i - 1) + " + 1;";
      chain += " X250 > 0";
      check("long constant chain refused by depth", expr(chain), "ERR E_SQL_DEPTH");
    }
    // A bad numericGuard is refused on EVERY use, not just the
    // first, and registration is safe under translation threads (tsan lane).
    {
      sel::sql::Map::define_dialect(
          "wave-evil", sel::sql::DialectSpec::extending("mariadb").lexical(
                           "numericGuard",
                           "CASE WHEN ({0} REGEXP 'x') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END"));
      int refused = 0;
      for (int i = 0; i < 3; ++i) {
        try {
          (void)Sql::translate(sel::compile("U + 1 > 0"), "wave-evil", wb);
        } catch (const std::logic_error&) {
          ++refused;
        } catch (const std::runtime_error&) {
          ++refused;
        }
      }
      if (refused != 3) fail("guard refused on every use", std::to_string(refused), "3");
    }
    // Registration refuses a textEscape that leaves a quote in the literal.
    {
      bool refused = false;
      try {
        sel::sql::Map::define_dialect(
            "wave-noescape", sel::sql::DialectSpec::extending("sqlite").lexical_escapes("textEscape", {}));
      } catch (const std::exception&) {
        refused = true;
      }
      if (!refused) fail("empty textEscape refused at registration", "accepted", "refused");
    }
    // A pure-memory plan runs on a copy of the caller's context.
    {
      const Bindings rb({{"ORDERS", orders()}});
      const sel::Program p = sel::compile("Y = 5; ORDERS .> SORT_BY(REPEAT(\"a\", 2)) .> MAP(RECORD(\"y\", Y))");
      const auto plan = Sql::plan_hybrid(p, "mariadb", rb);
      sel::Value context = sel::Value::none();
      context.set("ORDERS", sel::Value::list({}));
      (void)Sql::execute_hybrid(plan, [](const std::string&, const std::vector<sel::Value>&) {
        return sel::Value::list({});
      }, context);
      if (context.has("Y")) fail("execute_hybrid mutated the caller's context", "Y is set", "Y is not set");
    }
    // execute_hybrid copies only the variables the continuation assigns to.
    // The caller's context is still never written to, however the program writes.
    {
      const Bindings rb({{"ORDERS", orders()}});
      const auto big_context = []() {
        sel::Value context = sel::Value::none();
        std::vector<sel::Value> rows;
        for (int i = 1; i <= 3; i++) {
          sel::Value r = sel::Value::none();
          r.set("id", sel::Value::num(std::to_string(i)));
          rows.push_back(std::move(r));
        }
        context.set("BIG", sel::Value::list(std::move(rows)));
        context.set("KEEP", sel::Value::text("k"));
        return context;
      };
      const auto run = [&](const std::string& source, sel::Value& context) {
        const auto plan = Sql::plan_hybrid(sel::compile(source), "mariadb", rb);
        return Sql::execute_hybrid(plan, [](const std::string&, const std::vector<sel::Value>&) {
          return sel::Value::list({});
        }, context).dump();
      };
      sel::Value c = big_context();
      const std::string before = c.dump();
      check("a helper assignment is not in the caller's context", run("Y = 5; Y + COUNT(BIG)", c), "t\"8\"");
      check("...nor is it after an unrelated read", c.dump(), before);
      check("an indexed write into a variable copies it first", run("BIG[1][\"id\"] = 99; BIG[1][\"id\"]", c), "t\"99\"");
      check("...and the caller's BIG is unchanged", c.dump(), before);
      check("a copy of a variable can be written", run("Z = BIG; Z[2][\"id\"] = 7; Z[2][\"id\"] & \"/\" & BIG[2][\"id\"]", c), "t\"7/2\"");
      check("...caller unchanged again", c.dump(), before);
      check("an unassigned variable is still read", run("KEEP & COUNT(BIG)", c), "t\"k3\"");
      // A scalar or list context is not a variable map: it keeps the whole clone.
      sel::Value odd = sel::Value::text("scalar");
      odd.set("A", sel::Value::num("1"));
      check("a context with a scalar is cloned whole", run("A + 1", odd), "t\"2\"");
    }
    // Hybrid isolation from application functions: a host function is handed
    // values and may change them, so a continuation that calls one runs on a copy
    // of the whole context -- direct, inside IF, inside an aggregate body, and in
    // the continuation of a real hybrid split. The caller's A.k stays "1".
    {
      sel::register_function("POKE", 1, 1, [](sel::HostArgs& args) {
        sel::Value target = args.val(0);   // a handle: set() writes the shared value
        target.set("k", sel::Value::text("9"));
        return sel::Value::text("poked");
      });
      const Bindings rb({{"ORDERS", orders()}});
      const auto fresh = [] {
        sel::Value context = sel::Value::none();
        sel::Value a = sel::Value::none();
        a.set("k", sel::Value::text("1"));
        context.set("A", a);
        context.set("ORDERS", sel::evaluate("LIST(RECORD('ID', '1'), RECORD('ID', '2'))"));
        return context;
      };
      const auto in_memory = [](const sel::sql::HybridPlan& plan) {
        return [&plan](const std::string&, const std::vector<sel::Value>&) {
          sel::Value c = sel::Value::none();
          c.set("ORDERS", sel::evaluate("LIST(RECORD('ID', '1'), RECORD('ID', '2'))"));
          return sel::Program("", plan.sql_prefix_ast).run(c);
        };
      };
      for (const char* source : {"POKE(A)", "IF(TRUE, POKE(A), 0)", "MAP(LIST(1), POKE(A))",
                                 "ORDERS .> FILTER(_[\"ID\"] > 1) .> MAP(RECORD(\"p\", POKE(A), \"r\", REPEAT(\"x\", 2)))"}) {
        const auto plan = Sql::plan_hybrid(sel::compile(source), "sqlite", rb);
        sel::Value context = fresh();
        (void)Sql::execute_hybrid(plan, in_memory(plan), context);
        check(std::string("a host function cannot write the caller's context: ") + source,
              context.get("A")->get("k")->scalar(), "1");
      }
    }
    // A continuation that reads the reassigned source as a value sees the
    // reassigned value, as run() does: n is 4 (six rows, two dropped), three rows,
    // and the SQL prefix is LIMIT 3 OFFSET 2.
    {
      sel::register_function("HOSTF", 1, 1, [](sel::HostArgs& args) { return args.val(0); });
      const Bindings rb({{"ORDERS", Binding::relation(
          "orders", "o", {{"ID", Binding::column("id", "o", SqlKind::Num)}})}});
      const sel::Value rows = sel::evaluate(
          "LIST(RECORD('ID', '1'), RECORD('ID', '2'), RECORD('ID', '3'), RECORD('ID', '4'), "
          "RECORD('ID', '5'), RECORD('ID', '6'))");
      const sel::Program program = sel::compile(
          "ORDERS = ORDERS .> DROP(2); "
          "ORDERS .> TAKE(3) .> MAP(RECORD(\"n\", COUNT(ORDERS), \"x\", HOSTF(_[\"ID\"])))");
      const auto plan = Sql::plan_hybrid(program, "sqlite", rb);
      const auto context = [&] {
        sel::Value c = sel::Value::none();
        c.set("ORDERS", rows);
        return c;
      };
      const auto prefix_in_memory = [&](const std::string&, const std::vector<sel::Value>&) {
        sel::Value c = context();
        return sel::Program("", plan.sql_prefix_ast).run(c);
      };
      sel::Value direct_context = context();
      const std::string want = program.run(direct_context).dump();
      const std::string got = Sql::execute_hybrid(plan, prefix_in_memory, context()).dump();
      check("a reread of the reassigned source agrees with run()", got, want);
      check("...which is n=4 over three rows",
            want, "-{\"1\"=-{\"n\"=t\"4\", \"x\"=t\"3\"}, \"2\"=-{\"n\"=t\"4\", \"x\"=t\"4\"}, "
                  "\"3\"=-{\"n\"=t\"4\", \"x\"=t\"5\"}}");
      check("...and the prefix is LIMIT 3 OFFSET 2",
            plan.sql_statement ? std::to_string(plan.sql_statement->as_statement().find("LIMIT 3 OFFSET 2") !=
                                                std::string::npos)
                               : std::string("no SQL"),
            "1");
    }
    // A FILTER is not hoisted above a SORT_BY whose key can raise.
    {
      const Bindings rb({{"ORDERS", orders()}});
      const auto kind = [&](const std::string& source) {
        const auto plan = Sql::plan_hybrid(sel::compile(source), "mariadb", rb);
        return plan.pure_sql ? "pure_sql" : plan.pure_memory ? "pure_memory" : "hybrid";
      };
      check("undeclared sort key stays in memory",
            kind("ORDERS .> SORT_BY(_[\"nokey\"]) .> FILTER(_[\"ID\"] > 100) .> TAKE(5)"), "pure_memory");
      check("declared sort key still hoists",
            kind("ORDERS .> SORT_BY(_[\"ID\"]) .> FILTER(_[\"ID\"] > 100) .> TAKE(5)"), "pure_sql");
    }
    // The Translator reads the caller's Bindings instead of copying them, and
    // the work done once per set (alias clashes, the value bindings) gives the same
    // answers as it did per call.
    {
      const Bindings clash({{"A", Binding::relation("orders", "o")}, {"B", Binding::relation("customers", "O")}});
      const auto refusal = [&](const Bindings& b, const std::string& dialect) {
        try {
          Sql::translate(sel::compile("TRUE"), dialect, b);
        } catch (const sel::sql::SqlError& e) {
          return e.code();
        } catch (const std::exception& e) {
          return std::string("std:") + e.what();
        }
        return std::string("ok");
      };
      check("two relations sharing an alias, found when the set is built, are refused on translate",
            refusal(clash, "mariadb"), "E_SQL_BINDING");
      check("and the dialect is still judged first", refusal(clash, "nonesuch").substr(0, 4) == "E_SQ" ? std::string("E_SQL") : std::string("?"), "E_SQL");
      std::string dialect_code;
      try { Sql::translate(sel::compile("TRUE"), "nonesuch", clash); } catch (const sel::sql::SqlError& e) { dialect_code = e.code(); } catch (...) {}
      check("a bad dialect wins over the alias clash", dialect_code, "E_SQL_DIALECT");
      const Bindings values({{"N", Binding::value(sel::Value::num("5"))},
                             {"T", Binding::value(sel::Value::text("x"))},
                             {"COL", Binding::column("id", std::nullopt, SqlKind::Num)}});
      const auto sql = [&](const std::string& src) {
        return Sql::translate(sel::compile(src), "mariadb", values).as_condition();
      };
      const std::string with_n = sql("N + 1 == 6 AND COL > N");
      check("a numeric value binding is the constant it names",
            with_n.find("CAST('5' AS DECIMAL(65,10))") != std::string::npos ? "yes" : with_n,
            "yes");
      check("a text one too", sql("T $== \"x\" AND COL > 1").find("CAST('x' AS CHAR)") != std::string::npos ? "yes" : "no", "yes");
      check("translating twice gives one answer", sql("COL > N"), sql("COL > N"));
    }
    if (wave != 0) return 1;
  }

  std::cout << "cpp SQL advanced and remediation wave: all checks passed\n";
  return 0;
}
