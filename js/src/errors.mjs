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
