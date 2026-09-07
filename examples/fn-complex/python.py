# Goes in the matching python/sel/builtins/*.py. Not a runnable file: this is a fragment that
# compiles only in place. See README.md beside it.
# EXAMPLE-BEGIN
def _first(args, ctx):
    three = args.count() == 3
    binder = args.symbol(1) if three else '_'
    body = args.node(2 if three else 1)

    lst = args.val(0)
    items = elements(lst)          # the shared no-children helper, point 4

    for key, item in items:
        ctx.push_frame({binder: item, '_K': Value.text(key)})
        try:
            if args.eval_node(body).as_bool(body.pos):
                return item.clone()
        finally:
            ctx.pop_frame()
    return Value.text('')


define('FIRST', 2, 3, lazy=True, binds=True, fn=_first)
# EXAMPLE-END
