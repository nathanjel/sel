// Prints the parse tree of a SEL source file, one node per line, indented.
//
//   cpp/build/ast rule.sel
//
// A debugging aid for the parser, and the check that `sel_ast.hpp` does what it
// exists for: this is a SEPARATE translation unit that walks the tree, links the
// library, and includes no part of sel.cpp. Nothing else in the repository does
// that yet — the SEL→SQL translator will be the first real one — so without this
// the header could stop being includable and no test would notice.

#include "../sel.hpp"
#include "../sel_ast.hpp"

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

const char* kind_name(sel::NT t) {
  switch (t) {
    case sel::NT::Num: return "num";
    case sel::NT::Text: return "text";
    case sel::NT::Bool: return "bool";
    case sel::NT::Null: return "null";
    case sel::NT::Var: return "var";
    case sel::NT::Index: return "index";
    case sel::NT::Seq: return "seq";
    case sel::NT::List: return "list";
    case sel::NT::Un: return "un";
    case sel::NT::Bin: return "bin";
    case sel::NT::Assign: return "assign";
    default: return "call";
  }
}

void walk(const sel::Node* n, int indent) {
  if (!n) return;
  std::cout << std::string(static_cast<std::size_t>(indent) * 2, ' ') << kind_name(n->t);
  if (!n->s.empty()) std::cout << " " << n->s;
  if (n->t == sel::NT::Bool) std::cout << " " << (n->b ? "TRUE" : "FALSE");
  if (n->grouped) std::cout << " (grouped)";
  std::cout << "  @" << n->pos.line << ":" << n->pos.col << "\n";
  walk(n->l.get(), indent + 1);
  walk(n->r.get(), indent + 1);
  for (const auto& item : n->items) walk(item.get(), indent + 1);
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: ast FILE\n";
    return 2;
  }
  std::ifstream in(argv[1]);
  if (!in) {
    std::cerr << "cannot read " << argv[1] << "\n";
    return 2;
  }
  std::ostringstream buf;
  buf << in.rdbuf();
  try {
    walk(sel::compile(buf.str()).ast().get(), 0);
  } catch (const sel::SelError& e) {
    std::cerr << e.str() << "\n";
    return 1;
  }
  return 0;
}
