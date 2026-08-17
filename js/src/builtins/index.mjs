// Importing this module populates the function table. The table must be complete
// before any source is parsed, since unknown names and bad arity are compile-time
// errors.

import './control.mjs';
import './structure.mjs';
import './aggregate.mjs';
import './text.mjs';
import './number.mjs';
import './binary.mjs';
import './regex.mjs';
