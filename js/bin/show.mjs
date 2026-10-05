// How `sel` prints a result (docs/usage/repl.md): a scalar text bare, a boolean
// as TRUE or FALSE, a binary as `bin:<hex>`, anything else as its dump.
//
// One copy, imported by bin/sel.mjs and by tools/run-batch.mjs (--show), so a
// documentation example pasted into the CLI prints exactly what the
// documentation claims. Duck-typed on purpose: the batch runner may be aimed at
// a bundle (SEL_JS_ENTRY), whose Value is not this tree's class.
export function show(v) {
  if (v.size() === 0) {
    if (v.kind === 'TEXT') return v.scalar;
    if (v.kind === 'BOOL') return v.scalar ? 'TRUE' : 'FALSE';
    if (v.kind === 'BIN') return `bin:${v.dump().slice(1)}`;
  }
  return v.dump();
}
