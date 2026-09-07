// SEL errors. See spec/errors.md — codes are contract, messages are not.

export class SelError extends Error {
  constructor(code, message, pos) {
    super(message);
    this.name = 'SelError';
    this.code = code;
    this.line = pos ? pos.line : 0;
    this.col = pos ? pos.col : 0;
    this.offset = pos ? pos.offset : 0;
  }

  toString() {
    return `${this.code} at ${this.line}:${this.col}: ${this.message}`;
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
export const MAX_DEPTH = 200;
