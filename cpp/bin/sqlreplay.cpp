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
#include <string>
#include <vector>

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

  for (const std::string& p : problems) std::printf("  DIFFERS %s\n", p.c_str());
  std::printf("%d registration calls rebuilt the map, %d lookups compared, "
              "%zu differences\n",
              calls, compared, problems.size());
  return problems.empty() ? 0 : 1;
}
