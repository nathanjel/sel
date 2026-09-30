// render() for the examples -- the half of examples/lib/db.rs that needs no
// database driver.
//
//   #[path = "../lib/render.rs"] mod render;       render::render(&rows, "   | ")
//
// render(rows, pad) prints rows as `field=value` lines. It is written in SEL, so
// it prints the same bytes on every host by construction. db.rs re-exports it as
// db::render, so an example that talks to a database includes db.rs alone; this
// file stands on its own because examples/memory-complex renders rows without
// linking any driver (its Cargo target needs only the `sql` feature).

use sel_lang::{compile, Pos, SelError, Value};

const RENDER: &str =
    r#"JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & "=" & (_ ?? "NULL")), "  ")), "\n")"#;

pub fn render(rows: &Value, pad: &str) -> Result<String, SelError> {
    let at = Pos::default();
    let ctx = Value::none();
    ctx.set("ROWS", rows.clone(), at)?;
    ctx.set("PAD", Value::text_owned(pad.to_string()), at)?;
    // A Program is not Sync, so there is no static one to share; compiling this
    // line is cheap next to what it prints.
    compile(RENDER)?.run(Some(ctx))?.as_text(at)
}
