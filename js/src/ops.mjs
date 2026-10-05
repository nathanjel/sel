// What each operator IS, asked in one place.
//
// spec/lexicon.json authors the operators -- precedence, associativity, family
// (arithmetic, numeric / text / deep comparison, logic, coalescing, bitwise,
// concatenation, assignment), a comparison's relation, the binary operator a
// compound assignment applies, whether the right operand may never run -- and
// tools/gen-lexicon.mjs renders it into _lexicon.mjs. Everything below is built
// from that rendering at load, so the parser, the evaluator, the constant
// folder, the optimiser, the join pre-filter, the dependency walker and the SQL
// layer cannot disagree about what a comparison or an arithmetic operator is,
// and a new operator is a row in the lexicon rather than an edit in each of them.

import { OPS, BP, RELATIONS } from './_lexicon.mjs';

export { BP, RELATIONS };

// Token -> record, for the operators the climbing loop sees after an operand.
// Two maps because word operators lex as identifiers and symbol operators as
// `op` tokens, so they cannot share a key space; Map rather than an object so
// a token spelled like a name on Object.prototype cannot answer.
export const INFIX_SYMBOLS = new Map();
export const INFIX_WORDS = new Map();
// Token -> record, for NOT and unary minus.
export const PREFIX = new Map();
// Binary-node operator -> record (the `op` of a `bin`, `assign`, `un` node
// never collides: assignment tokens are not binary operators, and a prefix
// node records its name, NEG or NOT).
const BY_OP = new Map();

for (const o of OPS) {
  if (o.fixity === 'infix') {
    (o.word ? INFIX_WORDS : INFIX_SYMBOLS).set(o.token, o);
    BY_OP.set(o.token, o);
  } else if (o.fixity === 'prefix') {
    PREFIX.set(o.token, o);
  }
}

// The record for a `bin` or `assign` node's operator, or undefined.
export const opInfo = (op) => BY_OP.get(op);

function family(f, node = 'bin') {
  return Object.freeze(new Set(OPS.filter((o) => o.fixity === 'infix' && o.node === node && o.family === f)
    .map((o) => o.token)));
}

export const ARITH_OPS = family('arith');                   // + - * / %
export const NUM_COMPARE_OPS = family('compare');           // == != < <= > >=
export const TEXT_COMPARE_OPS = family('text-compare');     // $== $!= $< $<= $> $>=
export const DEEP_COMPARE_OPS = family('deep-compare');     // EQL IN
export const LOGIC_OPS = family('logic');                   // AND OR XOR
export const BITWISE_OPS = family('bitwise');               // BAND BOR BXOR
export const COALESCE_OPS = family('coalesce');             // ?? ???
export const ASSIGN_OPS = family('assign', 'assign');       // = += -= ...
// Every comparison level operator: the numeric and text families, EQL and IN.
export const COMPARE_OPS = Object.freeze(new Set([...NUM_COMPARE_OPS, ...TEXT_COMPARE_OPS, ...DEEP_COMPARE_OPS]));
// The right operand may never run: AND, OR, ?? and ???.
export const SHORT_CIRCUIT_OPS = Object.freeze(new Set(OPS.filter((o) => o.shortCircuit).map((o) => o.token)));
// A compound assignment -> the binary operator it applies (`+=` -> `+`).
export const COMPOUND = new Map(OPS.filter((o) => o.compound !== null).map((o) => [o.token, o.compound]));
// The binary operators (`bin` nodes): what the evaluator must dispatch.
export const BINARY_OPS = Object.freeze(new Set(OPS.filter((o) => o.fixity === 'infix' && o.node === 'bin')
  .map((o) => o.token)));
