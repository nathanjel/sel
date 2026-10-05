// Goes in rust/src/builtins/structure.rs beside ALL and ANY, and its define()
// line in registry() in rust/src/builtins/mod.rs. Not a runnable file: this is a
// fragment that compiles only in place. See README.md beside it.
// tools/check-rust-fragments.sh puts it there and runs cases.selt.
// EXAMPLE-BEGIN
pub fn fn_first(args: &mut Args) -> Result<Value, SelError> {
    let three = args.count() == 3;
    let binder = if three { args.symbol(1)? } else { "_".to_string() };
    let nodes = args.nodes; // the call's nodes: borrowing them does not borrow `args`
    let body = &nodes[if three { 2 } else { 1 }];

    // elements() is spec §7.3's view of a value, point 4: a scalar is a list of
    // itself, a childless NONE is empty.
    for Entry { key, val: item } in args.val(0)?.elements() {
        let mut frame = Frame::new();
        frame.set(&binder, item.clone());
        frame.set("_K", Value::text_owned(key));
        args.ctx.push_frame(frame);
        // Rust has no `finally`: pop the frame before `?` can leave with the
        // body's error.
        let hit = args.eval_node(body).and_then(|v| v.as_bool(body.pos));
        args.ctx.pop_frame();
        if hit? {
            return Ok(item); // the element's handle, as GET answers; assignment copies
        }
    }
    Ok(Value::text_owned(String::new()))
}

// in registry():
define(&mut m, "FIRST", 2, Some(3), /* lazy */ true, /* binds */ true, structure::fn_first);
// EXAMPLE-END
