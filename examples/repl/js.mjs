// A read-eval-print loop -- the whole of it, in JavaScript.
//
//   node examples/repl/js.mjs
//   node examples/repl/js.mjs < examples/repl/session.txt
//
// One context lives across lines, so a variable assigned on one line is there
// on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
// expression reads, and `:reset` empties the context. Errors print their code
// and position -- the message is human text and may differ between hosts; the
// code and the position may not.
//
// The four files beside this one print byte-identical output for the session in
// session.txt.

import { createInterface } from 'node:readline';
import { compile, SelError, Value } from '../../js/src/sel.mjs';

// EXAMPLE-BEGIN repl
function show(value) {
  if (value.isBool()) return value.asBool() ? 'TRUE' : 'FALSE';
  if (value.isNull()) return 'NULL';
  if (value.size() > 0 || value.isBin()) return value.dump();
  return value.asText();
}

let context = Value.none();
for await (const line of createInterface({ input: process.stdin })) {
  if (line.trim() === '') continue;
  console.log('sel>', line);
  try {
    if (line === ':reset') {
      context = Value.none();
    } else if (line.startsWith(':deps ')) {
      console.log(compile(line.slice(6)).dependencies().join(' '));
    } else {
      console.log(show(compile(line).run(context)));
    }
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`${e.code} at ${e.line}:${e.col}`);
  }
}
// EXAMPLE-END repl
