// SEL errors. See spec/errors.md — codes are contract, messages are not.

// What SelError and the SQL layer's SqlError share: a stable code and the
// position of the node responsible. Neither is an instance of the other.
export class CodedError extends Error {
  constructor(name, code, message, pos) {
    super(message);
    this.name = name;            // given, not new.target.name: a minifier renames classes
    this.code = code;
    this.line = pos ? pos.line : 0;
    this.col = pos ? pos.col : 0;
    this.offset = pos ? pos.offset : 0;
  }

  toString() {
    return `${this.code} at ${this.line}:${this.col}: ${this.message}`;
  }
}

export class SelError extends CodedError {
  constructor(code, message, pos) {
    super('SelError', code, message, pos);
  }
}

// Raise at the innermost point of failure. Nothing wraps this on the way out.
export function fail(code, message, pos) {
  throw new SelError(code, message, pos);
}

// spec/SPEC.md §6.4's three caps, which are one number. The parser's nesting,
// the evaluator's, and a value's -- each is a recursion over a structure the
// input can grow without bound, and each finds this host's own stack instead of
// an error if it is not counted. It lives here, with fail(), because this module
// is the one every other imports and none imports back, and because the number
// and the E_DEPTH it raises are the same fact.
import { MAX_DEPTH as LIMIT_MAX_DEPTH } from './_limits.mjs';

export const MAX_DEPTH = LIMIT_MAX_DEPTH;   // spec/limits.json, checked against the spec text
