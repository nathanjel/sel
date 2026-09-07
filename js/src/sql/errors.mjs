// Translator errors. See sql/errors.md — codes are contract, messages are not.
//
// Unlike SelError the message here is expected to be read: a translator error is
// not a bug report, it is an answer. *This rule cannot be pushed into this
// database, and here is what stopped it.*

export class SqlError extends Error {
  // One class, one message. No diagnostics object, no `explain()` API.
  constructor(code, message, pos) {
    super(message);
    this.name = 'SqlError';
    this.code = code;
    this.line = pos ? pos.line : 0;
    this.col = pos ? pos.col : 0;
    this.offset = pos ? pos.offset : 0;
  }

  toString() {
    return `${this.code} at ${this.line}:${this.col}: ${this.message}`;
  }
}

// Raise at the point of failure. Nothing wraps this on the way out, the same
// rule spec/errors.md sets for the evaluator.
export function refuse(code, message, pos) {
  throw new SqlError(code, message, pos);
}
