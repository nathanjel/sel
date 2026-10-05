// Translator errors. See sql/errors.md — codes are contract, messages are not.
//
// Unlike SelError the message here is expected to be read: a translator error is
// not a bug report, it is an answer. *This rule cannot be pushed into this
// database, and here is what stopped it.*

import { CodedError } from '../errors.mjs';

// One class, one message. No diagnostics object, no `explain()` API.
export class SqlError extends CodedError {
  constructor(code, message, pos) {
    super('SqlError', code, message, pos);
  }
}

// What an application handed a registration or a constructor, for its message.
export function typeName(v) {
  if (v === null) return 'null';
  if (v === undefined) return 'undefined';
  if (Array.isArray(v)) return 'list';
  if (typeof v === 'object') return v.constructor ? v.constructor.name : 'object';
  return typeof v;
}

// Raise at the point of failure. Nothing wraps this on the way out, the same
// rule spec/errors.md sets for the evaluator.
export function refuse(code, message, pos) {
  throw new SqlError(code, message, pos);
}
