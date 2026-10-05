// How the runners in this directory read the files they are given.
//
// Bytes, decoded as UTF-8 strictly and with nothing translated: a CR is program
// text, a byte-order mark is kept (the lexer decides what it means), and an
// invalid byte is a reason the file cannot be read rather than a U+FFFD the
// suite then tests. A path that cannot be read -- missing, a directory, no
// permission, not UTF-8 -- is one line on stderr and exit status 1, never a
// stack trace.

import { readFileSync } from 'node:fs';

const UTF8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

// `shown` is how the caller named it, when that is not `path` itself.
export function readTextOrExit(path, shown = path) {
  let bytes;
  try {
    bytes = readFileSync(path);
  } catch (e) {
    process.stderr.write(`cannot read ${shown}: ${e && e.code ? e.code : String(e)}\n`);
    process.exit(1);
  }
  try {
    return UTF8.decode(bytes);
  } catch {
    process.stderr.write(`cannot read ${shown}: not UTF-8\n`);
    process.exit(1);
  }
}
