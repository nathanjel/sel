// Precedence climbing. See docs/EXTENDING.md, "Adding an operator", step 5,
// and python/sel/parser.py, whose module docstring is the rationale.
//
// This host used to transcribe spec/grammar.md one function per production —
// parseSequence → parseList → parseAssignment → parseOr → … → parsePrimary,
// seventeen deep, with an arrow thunk at each of the nine binary helpers. That
// is a respectable style and it read as a literal rendering of the grammar, but
// it cost 35 stack frames per level of parenthesis nesting (measured here: 44
// frames at one paren, 1409 at forty, linear at 35.0), and E_DEPTH does not trip
// until 100 nested parens. Invisible on this host; fatal on Python's, whose
// default recursion limit is 1000 — which is why python/sel/parser.py was
// written in this shape first and is the reference for the rest.
//
// The payoff is not speed. It is that adding an operator stops being "a method
// in every parser, wired into the chain in the same place" and becomes a row in
// the table below.
//
// The depth arithmetic is unchanged and is not free to change: conformance/
// 10-limits.selt pins two increments per paren (parseSequence + parsePrimary),
// E_DEPTH at 1:101 for 100 parens, 1:200 for a `-` chain and 1:797 for a NOT
// chain. Prefix operators are counted only when actually consumed.

import { fail, MAX_DEPTH } from './errors.mjs';
import * as D from './decimal.mjs';
import { tokenize, RESERVED } from './lexer.mjs';
import { lookup } from './registry.mjs';


const ASSIGN_OPS = new Set(['=', '+=', '-=', '*=', '/=', '%=', '&=']);
const COMPARE_OPS = new Set([
  '==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
]);
const COMPARE_WORDS = new Set(['EQL', 'IN']);

// spec/SPEC.md §5, as a table. Higher binds tighter. The gaps are the levels
// that are not infix: 16 is postfix/primary, 15 is unary minus, 7 is NOT.
const BP_SEQ = 1;       // ;
const BP_LIST = 2;      // ,
const BP_ASSIGN = 3;    // = += -= *= /= %= &=   (right associative)
const BP_OR = 4;
const BP_XOR = 5;
const BP_AND = 6;
const BP_NOT = 7;       // prefix
const BP_COMPARE = 8;   // non-associative
const BP_COALESCE = 9;  // ?? ??? (right associative)
const BP_BOR = 10;
const BP_BXOR = 11;
const BP_BAND = 12;
const BP_CONCAT = 13;   // &
const BP_ADD = 14;      // + -
const BP_MUL = 15;      // * / %
const BP_NEG = 16;      // prefix

// BP_SEQ and BP_LIST are deliberately unused: `;` and `,` build N-ary nodes, so
// they stay hand-written loops in parseSequence/parseList rather than table
// rows. They are declared anyway so the ladder above reads as spec/SPEC.md §5
// does, with no silent gap at the loose end.

// Operator -> [binding power, associativity]. 'L' left, 'R' right, 'N'
// non-associative. Word operators lex as identifiers and symbol operators as
// `op` tokens, so they are two tables sharing one set of binding powers.
//
// Map rather than a plain object because the key is token text: a bare `{}`
// would answer for every name on Object.prototype, and the parser would then
// have to assume no token can ever be spelled like one. Map has no such chain,
// and `.get` transcribes Python's `dict.get` directly.
const INFIX_OPS = new Map([
  ['??', [BP_COALESCE, 'R']],
  ['???', [BP_COALESCE, 'R']],
  ['&', [BP_CONCAT, 'L']],
  ['+', [BP_ADD, 'L']], ['-', [BP_ADD, 'L']],
  ['*', [BP_MUL, 'L']], ['/', [BP_MUL, 'L']], ['%', [BP_MUL, 'L']],
  ...[...ASSIGN_OPS].map((op) => [op, [BP_ASSIGN, 'R']]),
  ...[...COMPARE_OPS].map((op) => [op, [BP_COMPARE, 'N']]),
]);
const INFIX_WORDS = new Map([
  ['OR', [BP_OR, 'L']], ['XOR', [BP_XOR, 'L']], ['AND', [BP_AND, 'L']],
  ['BOR', [BP_BOR, 'L']], ['BXOR', [BP_BXOR, 'L']], ['BAND', [BP_BAND, 'L']],
  ...[...COMPARE_WORDS].map((w) => [w, [BP_COMPARE, 'N']]),
]);

class Parser {
  constructor(tokens) {
    this.toks = tokens;
    this.i = 0;
    this.depth = 0;
  }

  // The two tables are one lookup. Every question about an operator — what it
  // binds at, how it associates, and whether it may follow a comparison — is
  // answered from here, so adding an operator really is adding a row. Asking a
  // separate list anywhere would put that claim back in doubt.
  infixEntry(t) {
    if (t.type === 'op') return INFIX_OPS.get(t.value);
    if (t.type === 'ident') return INFIX_WORDS.get(t.value);
    return undefined;
  }

  peek() { return this.toks[this.i]; }
  next() { return this.toks[this.i++]; }
  atOp(v) { const t = this.peek(); return t.type === 'op' && t.value === v; }
  atEof() { return this.peek().type === 'eof'; }

  expectOp(v) {
    if (!this.atOp(v)) {
      const t = this.peek();
      fail('E_SYNTAX', `expected ${JSON.stringify(v)}, got ${describe(t)}`, t);
    }
    return this.next();
  }

  enter(pos) {
    if (++this.depth > MAX_DEPTH) fail('E_DEPTH', 'expression nested too deeply', pos);
  }
  leave() { this.depth--; }

  // --- entry ----------------------------------------------------------------

  parseProgram() {
    const node = this.parseSequence();
    if (!this.atEof()) {
      const t = this.peek();
      fail('E_SYNTAX', `unexpected ${describe(t)}`, t);
    }
    return node;
  }

  // sequence = list { ";" list } [ ";" ]
  //
  // The try/finally is new here. It costs nothing — a failing parse abandons the
  // Parser either way — and the Lisp and Python hosts already protect this
  // counter, so this is the shape the five hosts converged on rather than a
  // deviation. All five protect it now.
  parseSequence() {
    const start = this.peek();
    this.enter(start);
    let items;
    try {
      items = [this.parseList()];
      while (this.atOp(';')) {
        this.next();
        // A trailing ';' before a closer or end of input is permitted.
        if (this.atEof() || this.atOp(')') || this.atOp(']')) break;
        items.push(this.parseList());
      }
    } finally {
      this.leave();
    }
    return items.length === 1 ? items[0] : { t: 'seq', items, pos: items[0].pos };
  }

  // list = assignment { "," assignment }
  parseList() {
    const items = [this.parseTerm(BP_ASSIGN)];
    while (this.atOp(',')) {
      this.next();
      items.push(this.parseTerm(BP_ASSIGN));
    }
    return items.length === 1 ? items[0] : { t: 'list', items, pos: items[0].pos };
  }

  // --- the precedence-climbing loop -----------------------------------------

  parseTerm(minBp) {
    let left = this.parsePrefix(minBp);

    for (;;) {
      const t = this.peek();
      const entry = this.infixEntry(t);
      if (entry === undefined) return left;
      const [bp, assoc] = entry;
      if (bp < minBp) return left;

      this.next();

      if (ASSIGN_OPS.has(t.value)) {
        // Assignment. The target is validated against the AST shape, not against
        // a value, which is what makes `(A) = 1` a compile error. Parsing the
        // right side at bp rather than bp + 1 is what makes it right associative.
        checkTarget(left, t);
        // Counted, for the same reason parsePrefix counts: the right side recurses
        // without passing through parseSequence or parsePrimary, so uncounted a
        // chain of assignments is bounded by nothing but the host's own stack.
        // `A=` fifty thousand times segfaulted the C++ host through its public
        // CLI and raised a host RangeError here -- the same hole the prefix
        // operators had, through the one production nobody had counted.
        this.enter(t);
        try {
          const value = this.parseTerm(bp);
          left = { t: 'assign', op: t.value, target: left, value, pos: left.pos };
        } finally {
          this.leave();
        }
        continue;
      }

      if (assoc === 'R') {
        const right = this.parseTerm(bp);
        left = { t: 'bin', op: t.value, l: left, r: right, pos: t };
        continue;
      }

      if (assoc === 'N') {
        const right = this.parseTerm(bp + 1);
        const after = this.peek();
        const afterEntry = this.infixEntry(after);
        if (afterEntry !== undefined && afterEntry[1] === 'N') {
          fail('E_SYNTAX',
            `comparison operators do not chain — parenthesise, as in (a ${t.value} b) AND (b ${after.value} c)`,
            after);
        }
        left = { t: 'bin', op: t.value, l: left, r: right, pos: t };
        continue;
      }

      const right = this.parseTerm(bp + 1);
      left = { t: 'bin', op: t.value, l: left, r: right, pos: t };
    }
  }

  // NOT and unary minus.
  //
  // Each is accepted only where its own binding power reaches: NOT at 7 cannot
  // appear inside a comparison operand (parsed at 9), so `a == NOT b` falls
  // through to parsePrimary, which sees the bare identifier NOT and raises
  // E_RESERVED — the same error the transcribed parser gave, by a different
  // route. Folding these into parsePrimary, which is where textbook precedence
  // climbing puts prefix operators, would make `NOT a == b` parse as
  // `(NOT a) == b` and would break the depth pins at the same time.
  //
  // Counted, and only when actually consumed: a prefix operator recurses without
  // passing through parseSequence or parsePrimary, and uncounted it reached the
  // host's own stack limit instead of E_DEPTH — a RangeError here and a segfault
  // in C++, from a rule that is just `-` repeated.
  parsePrefix(minBp) {
    const t = this.peek();

    if (t.type === 'ident' && t.value === 'NOT' && minBp <= BP_NOT) {
      this.next();
      this.enter(t);
      try {
        return { t: 'un', op: 'NOT', x: this.parseTerm(BP_NOT), pos: t };
      } finally {
        this.leave();
      }
    }

    if (t.type === 'op' && t.value === '-' && minBp <= BP_NEG) {
      this.next();
      this.enter(t);
      try {
        return { t: 'un', op: 'NEG', x: this.parseTerm(BP_NEG), pos: t };
      } finally {
        this.leave();
      }
    }

    return this.parsePostfix();
  }

  // postfix = primary { "[" sequence "]" }
  //
  // The bracket counts a level of its own. Without it an index is the one
  // nesting door that recurses from outside parsePrimary's enter/leave, so it
  // charged one level per nesting where "(", "f(" and the prefix operators all
  // charge for the frames they actually cost. Five stack frames against one
  // level of the budget put a[a[...]] over CPython's 1000-frame limit before
  // the 200-level guard could fire, which is why the Python host raised
  // RecursionError at 198 while the others still answered.
  parsePostfix() {
    let node = this.parsePrimary();
    while (this.atOp('[') || this.atOp('.>')) {
      if (this.atOp('[')) {
        const br = this.next();
        this.enter(br);
        try {
          const idx = this.parseSequence();
          this.expectOp(']');
          node = { t: 'index', obj: node, idx, pos: br };
        } finally {
          this.leave();
        }
      } else {
        this.next();
        node = this.parsePipeStep(node);
      }
    }
    return node;
  }

  parsePipeStep(left) {
    const t = this.peek();
    if (t.type !== 'ident' || t.value === 'TRUE' || t.value === 'FALSE' || t.value === 'NULL') {
      fail('E_SYNTAX', 'right-hand side of .> must be a function call or function name', t);
    }
    const nameTok = this.next();
    let args = [];
    if (this.atOp('(')) {
      this.next();
      if (this.atOp(')')) {
        this.next();
      } else {
        const inner = this.parseSequence();
        this.expectOp(')');
        args = (inner.t === 'list' && !inner.grouped) ? inner.items : [inner];
      }
    }

    const spec = lookup(nameTok.value);
    if (!spec) fail('E_UNKNOWN_FUNC', `unknown function ${nameTok.value}`, nameTok);

    const count = args.length;
    let hasPlaceholder = false;
    if (count >= spec.min) {
      for (let i = 0; i < args.length; i++) {
        if (args[i].t === 'var' && args[i].name === '_' && !args[i].grouped) {
          args[i] = left;
          hasPlaceholder = true;
        }
      }
    }
    if (!hasPlaceholder) {
      args.unshift(left);
    }

    const finalCount = args.length;
    if (finalCount < spec.min || finalCount > spec.max) {
      fail('E_ARITY', `${spec.name} takes ${arityText(spec)}, got ${finalCount}`, nameTok);
    }
    if (spec.arityError) {
      const problem = spec.arityError(finalCount);
      if (problem) fail('E_ARITY', problem, nameTok);
    }
    return { t: 'call', name: spec.name, spec, args, pos: nameTok };
  }

  parsePrimary() {
    const t = this.peek();
    this.enter(t);
    try {
      if (t.type === 'num') {
        this.next();
        // Canonicalised once, here: the literal 007 is the value 7.
        return { t: 'num', v: D.format(D.parse(t.value, t)), pos: t };
      }
      if (t.type === 'text') { this.next(); return { t: 'text', v: t.value, pos: t }; }

      if (t.type === 'ident') {
        if (t.value === 'TRUE' || t.value === 'FALSE') {
          this.next();
          return { t: 'bool', v: t.value === 'TRUE', pos: t };
        }
        if (t.value === 'NULL') {
          this.next();
          return { t: 'null', pos: t };
        }
        const after = this.toks[this.i + 1];
        if (after && after.type === 'op' && after.value === '(') return this.parseCall();
        if (RESERVED.has(t.value)) {
          fail('E_RESERVED', `${t.value} is a reserved word and cannot be a variable`, t);
        }
        this.next();
        return { t: 'var', name: t.value, pos: t };
      }

      if (t.type === 'op' && t.value === '(') {
        this.next();
        if (this.atOp(')')) fail('E_SYNTAX', 'empty parentheses', t);
        const inner = this.parseSequence();
        this.expectOp(')');
        // Marked so that F((1,2)) passes one list rather than two arguments.
        inner.grouped = true;
        return inner;
      }

      fail('E_SYNTAX', `unexpected ${describe(t)}`, t);
    } finally {
      this.leave();
    }
  }

  parseCall() {
    const nameTok = this.next();
    this.expectOp('(');
    let args;
    if (this.atOp(')')) {
      this.next();
      args = [];
    } else {
      const inner = this.parseSequence();
      this.expectOp(')');
      args = (inner.t === 'list' && !inner.grouped) ? inner.items : [inner];
    }

    const spec = lookup(nameTok.value);
    if (!spec) fail('E_UNKNOWN_FUNC', `unknown function ${nameTok.value}`, nameTok);
    if (args.length < spec.min || args.length > spec.max) {
      fail('E_ARITY', `${spec.name} takes ${arityText(spec)}, got ${args.length}`, nameTok);
    }
    if (spec.arityError) {
      const problem = spec.arityError(args.length);
      if (problem) fail('E_ARITY', problem, nameTok);
    }
    return { t: 'call', name: spec.name, spec, args, pos: nameTok };
  }
}

function arityText(spec) {
  if (spec.max === Infinity) return `at least ${spec.min} argument${spec.min === 1 ? '' : 's'}`;
  if (spec.min === spec.max) return `${spec.min} argument${spec.min === 1 ? '' : 's'}`;
  return `${spec.min} to ${spec.max} arguments`;
}

function describe(t) {
  if (t.type === 'eof') return 'end of input';
  if (t.type === 'text') return 'a text literal';
  if (t.type === 'num') return `number ${t.value}`;
  return JSON.stringify(t.value);
}

// The target must be an identifier followed by zero or more index operations.
function checkTarget(node, opTok) {
  let n = node;
  while (n.t === 'index') n = n.obj;
  if (n.t !== 'var' || node.grouped) {
    fail('E_BAD_ASSIGN', `cannot assign with ${opTok.value} to this expression`, node.pos);
  }
}

export function parse(source) {
  return new Parser(tokenize(source)).parseProgram();
}
