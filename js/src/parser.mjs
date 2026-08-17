// Recursive descent, one function per precedence level, mirroring spec/grammar.md
// exactly so the PHP port can be read side by side with it.

import { fail } from './errors.mjs';
import * as D from './decimal.mjs';
import { tokenize, RESERVED } from './lexer.mjs';
import { lookup } from './registry.mjs';

const MAX_DEPTH = 200;

const ASSIGN_OPS = new Set(['=', '+=', '-=', '*=', '/=', '%=', '&=']);
const COMPARE_OPS = new Set([
  '==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
]);
const COMPARE_WORDS = new Set(['EQL', 'IN']);

class Parser {
  constructor(tokens) {
    this.toks = tokens;
    this.i = 0;
    this.depth = 0;
  }

  peek() { return this.toks[this.i]; }
  next() { return this.toks[this.i++]; }
  atOp(v) { const t = this.peek(); return t.type === 'op' && t.value === v; }
  atWord(v) { const t = this.peek(); return t.type === 'ident' && t.value === v; }
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
  parseSequence() {
    const start = this.peek();
    this.enter(start);
    const items = [this.parseList()];
    while (this.atOp(';')) {
      this.next();
      // A trailing ';' before a closer or end of input is permitted.
      if (this.atEof() || this.atOp(')') || this.atOp(']')) break;
      items.push(this.parseList());
    }
    this.leave();
    return items.length === 1 ? items[0] : { t: 'seq', items, pos: items[0].pos };
  }

  // list = assignment { "," assignment }
  parseList() {
    const items = [this.parseAssignment()];
    while (this.atOp(',')) {
      this.next();
      items.push(this.parseAssignment());
    }
    return items.length === 1 ? items[0] : { t: 'list', items, pos: items[0].pos };
  }

  // assignment = disjunction [ assign_op assignment ]   (right associative)
  parseAssignment() {
    const left = this.parseOr();
    const t = this.peek();
    if (t.type === 'op' && ASSIGN_OPS.has(t.value)) {
      this.next();
      checkTarget(left, t);
      const value = this.parseAssignment();
      return { t: 'assign', op: t.value, target: left, value, pos: left.pos };
    }
    return left;
  }

  parseOr() { return this.parseWordBinary('OR', () => this.parseXor()); }
  parseXor() { return this.parseWordBinary('XOR', () => this.parseAnd()); }
  parseAnd() { return this.parseWordBinary('AND', () => this.parseNot()); }

  parseWordBinary(word, sub) {
    let left = sub();
    while (this.atWord(word)) {
      const op = this.next();
      const right = sub();
      left = { t: 'bin', op: word, l: left, r: right, pos: op };
    }
    return left;
  }

  // negation = "NOT" negation | comparison
  parseNot() {
    if (this.atWord('NOT')) {
      const op = this.next();
      return { t: 'un', op: 'NOT', x: this.parseNot(), pos: op };
    }
    return this.parseComparison();
  }

  // comparison = bit_or [ compare_op bit_or ]   — deliberately non-associative
  parseComparison() {
    const left = this.parseBitOr();
    const t = this.peek();
    const isOp = t.type === 'op' && COMPARE_OPS.has(t.value);
    const isWord = t.type === 'ident' && COMPARE_WORDS.has(t.value);
    if (!isOp && !isWord) return left;

    this.next();
    const right = this.parseBitOr();
    const after = this.peek();
    if ((after.type === 'op' && COMPARE_OPS.has(after.value))
      || (after.type === 'ident' && COMPARE_WORDS.has(after.value))) {
      fail('E_SYNTAX',
        `comparison operators do not chain — parenthesise, as in (a ${t.value} b) AND (b ${after.value} c)`,
        after);
    }
    return { t: 'bin', op: t.value, l: left, r: right, pos: t };
  }

  parseBitOr() { return this.parseWordBinary('BOR', () => this.parseBitXor()); }
  parseBitXor() { return this.parseWordBinary('BXOR', () => this.parseBitAnd()); }
  parseBitAnd() { return this.parseWordBinary('BAND', () => this.parseConcat()); }

  parseConcat() { return this.parseOpBinary(['&'], () => this.parseAdditive()); }
  parseAdditive() { return this.parseOpBinary(['+', '-'], () => this.parseMultiplicative()); }
  parseMultiplicative() { return this.parseOpBinary(['*', '/', '%'], () => this.parseUnary()); }

  parseOpBinary(ops, sub) {
    let left = sub();
    for (;;) {
      const t = this.peek();
      if (t.type !== 'op' || !ops.includes(t.value)) return left;
      this.next();
      left = { t: 'bin', op: t.value, l: left, r: sub(), pos: t };
    }
  }

  // unary = "-" unary | postfix
  parseUnary() {
    if (this.atOp('-')) {
      const op = this.next();
      return { t: 'un', op: 'NEG', x: this.parseUnary(), pos: op };
    }
    return this.parsePostfix();
  }

  // postfix = primary { "[" sequence "]" }
  parsePostfix() {
    let node = this.parsePrimary();
    while (this.atOp('[')) {
      const br = this.next();
      const idx = this.parseSequence();
      this.expectOp(']');
      node = { t: 'index', obj: node, idx, pos: br };
    }
    return node;
  }

  parsePrimary() {
    const t = this.peek();
    this.enter(t);
    try {
      if (t.type === 'num') {
        this.next();
        // Canonicalised once, here: the literal 007 is the value 7.
        return { t: 'num', v: D.format(D.parse(t.value)), pos: t };
      }
      if (t.type === 'text') { this.next(); return { t: 'text', v: t.value, pos: t }; }

      if (t.type === 'ident') {
        if (t.value === 'TRUE' || t.value === 'FALSE') {
          this.next();
          return { t: 'bool', v: t.value === 'TRUE', pos: t };
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
