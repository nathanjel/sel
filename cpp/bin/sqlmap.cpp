// Dumps the shipped dialect map in a canonical form, for tools/check-sql-map.sh
// to diff against the same dump from python/bin/sqlmap.
//
// The generator renders one source of truth -- sql/dialects/*.json -- into four
// host languages through four separate emitters. `gen-sql-map.mjs --check`
// proves each generated file is what its own emitter produces today; this
// proves the C++ emitter and the Python one produce the same MAP, which is a
// different question and the one that matters. A transcription slip in the
// newest emitter -- a dropped caveat, a template keyed by the wrong count, an
// entry order that quietly reshuffles -- shows up here as a diff.

#include <cstdio>
#include <string>

#include "../sel_sql_map.hpp"

namespace {

// Fields are tab-separated, so a tab or newline inside a template would forge a
// record. None does today; escaping means none can.
std::string esc(std::string_view s) {
  std::string out;
  for (char c : s) {
    if (c == '\\') out += "\\\\";
    else if (c == '\t') out += "\\t";
    else if (c == '\n') out += "\\n";
    else out += c;
  }
  return out;
}

std::string absent(std::string_view s) { return s.empty() ? "-" : esc(s); }

void dump_entries(std::string_view dialect, std::string_view section,
                  std::span<const sel::sql::Entry> entries) {
  using namespace sel::sql;
  for (const Entry& e : entries) {
    std::string kind, body, payload;
    switch (e.kind) {
      case EntryKind::Refusal:
        kind = "refusal";
        body = "-";
        payload = e.reason.present ? esc(e.reason.text) : "-";
        break;
      case EntryKind::Builder:
        kind = "builder";
        body = "-";
        payload = "-";
        break;
      case EntryKind::Template:
        kind = "template";
        if (e.body == BodyKind::One) {
          body = "one";
          payload = esc(e.one);
        } else {
          body = e.body == BodyKind::ByCount ? "bycount" : "variants";
          for (const Keyed& k : e.keyed) {
            if (!payload.empty()) payload += ";";
            // `key!` is a WITHDRAWN arm and `key=text` a present one. sql/MAP.md
            // §2 makes those different answers, so a dump that rendered both as
            // an empty string would be blind to the difference it exists to
            // check. Neither character can occur in a key: they are argument
            // counts, "*", or variant names.
            payload += esc(k.key);
            payload += k.value.present ? "=" + esc(k.value.text) : "!";
          }
        }
        break;
    }
    const std::string arity =
        e.has_arity ? std::to_string(e.arity_min) + "," + std::to_string(e.arity_max)
                    : "-";
    std::printf("E\t%.*s\t%.*s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
                (int)dialect.size(), dialect.data(),
                (int)section.size(), section.data(), esc(e.key).c_str(),
                kind.c_str(), body.c_str(), absent(e.ret).c_str(),
                absent(e.caveat).c_str(), absent(e.since).c_str(), arity.c_str(),
                payload.c_str());
  }
}

std::string names(std::span<const std::string_view> xs) {
  std::string out;
  for (std::string_view x : xs) {
    if (!out.empty()) out += ",";
    out += esc(x);
  }
  return out;
}

}  // namespace

int main() {
  using namespace sel::sql;

  for (const Dialect& d : shipped_map()) {
    std::printf("D\t%.*s\t%s\t%s\t%s\n", (int)d.name.size(), d.name.data(),
                absent(d.extends).c_str(), esc(d.version).c_str(),
                d.target ? "yes" : "no");
    for (const Lexical& lx : d.lexical) {
      std::string kind, value;
      switch (lx.kind) {
        case LexKind::Withdrawn: kind = "withdrawn"; value = "-"; break;
        case LexKind::Text: kind = "text"; value = esc(lx.text); break;
        case LexKind::Escapes:
          kind = "escapes";
          for (const Escape& e : lx.escapes) {
            if (!value.empty()) value += ";";
            value += esc(e.from) + "=>" + esc(e.to);
          }
          break;
      }
      std::printf("L\t%.*s\t%s\t%s\t%s\n", (int)d.name.size(), d.name.data(),
                  esc(lx.key).c_str(), kind.c_str(), value.c_str());
    }
    dump_entries(d.name, "ops", d.ops);
    dump_entries(d.name, "funcs", d.funcs);
    dump_entries(d.name, "skel", d.skel);
  }

  const Rules& r = shipped_rules();
  std::printf("R\tcaveats\t%s\n", names(r.caveats).c_str());
  std::printf("R\tretKinds\t%s\n", names(r.ret_kinds).c_str());
  std::printf("R\ttemplateKeys\t%s\n", names(r.template_keys).c_str());
  for (const auto& [label, table] :
       {std::pair{"opArity", r.op_arity}, std::pair{"funcArity", r.func_arity}}) {
    for (const Arity& a : table) {
      std::printf("R\t%s\t%s\t%d\t%s\n", label, esc(a.key).c_str(), a.min,
                  a.unbounded ? "*" : std::to_string(a.max).c_str());
    }
  }
  for (const auto& [label, table] :
       {std::pair{"variants", r.variants}, std::pair{"skelSlots", r.skel_slots}}) {
    for (const Names& n : table) {
      std::printf("R\t%s\t%s\t%s\n", label, esc(n.key).c_str(),
                  names(n.names).c_str());
    }
  }
  for (const LexType& t : r.lexical_types) {
    std::printf("R\tlexicalTypes\t%s\t%s\n", esc(t.key).c_str(),
                t.escapes ? "map" : "string");
  }
  return 0;
}
