#include "sel_ast.hpp"
#include <iostream>
int main() {
  for (const auto& name : sel::function_names()) {
    const auto* s=sel::lookup_builtin(name);
    std::cout << name << '\t' << s->min << '\t' << (s->max==sel::VARIADIC?-1:s->max)
              << '\t' << s->lazy << '\t' << s->binds << '\t' << (s->arity_error!=nullptr) << '\n';
  }
  for (const std::string name : {"LINK", "LINK_LEFT"}) {
    try { sel::compile(name+"(L, R, TRUE, TRUE)"); std::cout << "PROBE\t" << name << "\taccepted\n"; }
    catch (const sel::SelError& e) { std::cout << "PROBE\t" << name << '\t' << e.code() << '\n'; }
  }
}
