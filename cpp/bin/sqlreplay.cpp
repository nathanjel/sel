// Rebuild the whole shipped map through the public registration API, then diff
// what the translator can observe against the map beside it.
//
//     cpp/build/sqlreplay
//
// The counterpart of js/bin/sqlreplay.mjs, php/bin/sqlreplay and
// python/bin/sqlreplay; see the first for why this check exists. sql/MAP.md
// §4.5¼ is the property it asserts: anything the shipped map contains, an
// application could have registered.
//
// For C++ it asserts something the other hosts get for free. Their maps are
// language literals, so "could an application have written this?" is nearly
// tautological; here the shipped table is constexpr aggregates and the
// registration path is a different type with different storage, so the two
// really are two implementations of one thing.

#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

#include "../sel.hpp"
#include "../sel_sql.hpp"
#include "../sel_sql_map.hpp"
#include "map_replay.hpp"

namespace {

using namespace sel::sql;

constexpr std::string_view SUF = "~replay";

bool same(const Lexical* a, const Lexical* b) {
  if (!a || !b) return a == b;
  if (a->kind != b->kind || a->text != b->text) return false;
  if (a->escapes.size() != b->escapes.size()) return false;
  for (std::size_t i = 0; i < a->escapes.size(); ++i) {
    if (a->escapes[i].from != b->escapes[i].from ||
        a->escapes[i].to != b->escapes[i].to) {
      return false;
    }
  }
  return true;
}

bool same(const Entry* a, const Entry* b) {
  if (!a || !b) return a == b;
  if (a->kind != b->kind || a->body != b->body) return false;
  if (a->reason.present != b->reason.present ||
      (a->reason.present && a->reason.text != b->reason.text)) {
    return false;
  }
  if (a->one != b->one || a->ret != b->ret || a->caveat != b->caveat ||
      a->since != b->since) {
    return false;
  }
  if (a->has_arity != b->has_arity || a->arity_min != b->arity_min ||
      a->arity_max != b->arity_max) {
    return false;
  }
  if ((a->builder == nullptr) != (b->builder == nullptr)) return false;
  if (a->keyed.size() != b->keyed.size()) return false;
  for (std::size_t i = 0; i < a->keyed.size(); ++i) {
    if (a->keyed[i].key != b->keyed[i].key) return false;
    if (a->keyed[i].value.present != b->keyed[i].value.present) return false;
    if (a->keyed[i].value.present &&
        a->keyed[i].value.text != b->keyed[i].value.text) {
      return false;
    }
  }
  return true;
}

}  // namespace

int main() {
  const int calls = sel::sqlt::replay_register();

  std::vector<std::string> problems;
  int compared = 0;

  for (const Dialect& d : shipped_map()) {
    const std::string name(d.name);
    const std::string twin = name + std::string(SUF);
    ++compared;
    if (Map::version(name) != Map::version(twin)) problems.push_back(name + ": version");

    for (const Lexical& lx : d.lexical) {
      ++compared;
      const std::string key(lx.key);
      if (!same(Map::lexical(name, key), Map::lexical(twin, key))) {
        problems.push_back(name + ".lexical." + key);
      }
    }
    for (const auto& [section, entries] :
         {std::pair{Section::Ops, d.ops}, std::pair{Section::Funcs, d.funcs},
          std::pair{Section::Skel, d.skel}}) {
      for (const Entry& e : entries) {
        ++compared;
        if (!same(Map::entry(name, section, e.key),
                  Map::entry(twin, section, e.key))) {
          problems.push_back(name + "." + std::string(section_name(section)) + "." +
                             std::string(e.key));
        }
      }
    }
  }

  // Rule 10 of sql/MAP.md, at run time. Registering a derived dialect is the
  // documented way to adapt the map to a server -- the first external user of
  // this layer did it on their first day -- and numericGuard is the one lexical
  // key where a wrong override fails SILENTLY: every other one blows up at
  // template expansion, and this one emits SQL that answers where SEL refuses.
  // The generator has enforced the rule since the guard existed; nothing
  // enforced it for a dialect the generator never sees.
  //
  // End to end rather than by calling the check directly, because the check
  // being right is worth nothing if the emit path does not reach it. The bad
  // guard accepts integers only, so it lets through the '2.5' that ISNUM's
  // pattern catches: a guard that asks a narrower question passes what it
  // should have stopped.
  {
    const std::string guard_name = "mariadb" + std::string(SUF) + "~guard";
    Map::define_dialect(
        guard_name,
        DialectSpec::extending("mariadb" + std::string(SUF))
            .lexical("numericGuard",
                     "CASE WHEN ({0} REGEXP '\\\\A-?[0-9]+\\\\z') THEN "
                     "CAST({0} AS DECIMAL(65,10)) ELSE NULL END"));
    bool refused = false;
    try {
      Sql::translate(sel::compile("T == 25"), guard_name,
                     Bindings({{"T", Binding::column("t", std::nullopt,
                                                     SqlKind::Text)}}));
    } catch (const SqlError& e) {
      problems.push_back(std::string("numericGuard disagreeing with ISNUM raised ") +
                         e.code() + " rather than a registration error");
    } catch (const std::runtime_error&) {
      refused = true;
    }
    if (!refused) {
      problems.emplace_back(
          "a numericGuard that disagrees with its funcs.ISNUM was accepted; "
          "sql/MAP.md rule 10 holds at generation time and not at run time");
    }
  }

  // And the memo must not outlive what it vouched for. define() lets the last
  // writer win, so an ISNUM registered AFTER a dialect's guard was checked would
  // never be compared against it. This dialect inherits a good guard, is translated
  // once so the check runs and passes, and then has its ISNUM replaced by one the
  // inherited guard does not carry.
  {
    const std::string memo_name = "mariadb" + std::string(SUF) + "~memo";
    Map::define_dialect(memo_name,
                        DialectSpec::extending("mariadb" + std::string(SUF)));
    const sel::Program program = sel::compile("T == 25");
    const Bindings text_col({{"T", Binding::column("t", std::nullopt,
                                                    SqlKind::Text)}});
    Sql::translate(program, memo_name, text_col);
    Map::define(memo_name, Section::Funcs, "ISNUM",
                EntrySpec::tpl("({0} REGEXP '^[0-9]+$')", "BOOL"));
    bool stale = false;
    try {
      Sql::translate(program, memo_name, text_col);
    } catch (const SqlError& e) {
      problems.push_back(std::string("a redefined ISNUM raised ") + e.code() +
                         " rather than a registration error");
    } catch (const std::runtime_error&) {
      stale = true;
    }
    if (!stale) {
      problems.emplace_back(
          "an ISNUM redefined after the guard was checked was not noticed; "
          "the memo outlived the pairing it vouched for");
    }
  }

  for (const std::string& p : problems) std::printf("  DIFFERS %s\n", p.c_str());
  std::printf("%d registration calls rebuilt the map, %d lookups compared, "
              "%zu differences (and a disagreeing numericGuard is refused)\n",
              calls, compared, problems.size());
  return problems.empty() ? 0 : 1;
}
