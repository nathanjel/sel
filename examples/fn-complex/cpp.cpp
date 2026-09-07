// Goes in the matching register_*() in cpp/sel.cpp. Not a runnable file: this is a fragment that
// compiles only in place. See README.md beside it.
// EXAMPLE-BEGIN
define(Spec{"FIRST", 2, 3, /*lazy=*/true, /*binds=*/true, nullptr,
            [](Args& a, Context& ctx) -> Value {
              const bool three = a.count() == 3;
              const std::string binder = three ? a.symbol(1) : std::string("_");
              const Node& body = a.node(three ? 2 : 1);

              for (const auto& [key, item] : elements(a.val(0))) {
                ctx.frames.push_back({{binder, item}, {"_K", make_text(key)}});
                bool hit = false;
                try {
                  hit = a.eval(body).as_bool(body.pos);
                } catch (...) {
                  ctx.frames.pop_back();   // C++ has no `finally`
                  throw;
                }
                ctx.frames.pop_back();
                if (hit) return item;
              }
              return make_text("");
            }});
// EXAMPLE-END
