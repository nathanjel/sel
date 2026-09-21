import * as D from './decimal.mjs';
import { MAX_DEPTH } from './errors.mjs';
import { MATH_OPERATORS, MATH_PREFIX, MATH_BUILTINS as MANIFEST_BUILTINS, MATH_OPS } from './_math_ops.mjs';

export const OpCode = {
  LOAD_VAR: 1,
  LOAD_CONST: 2,
  LOAD_LEAF: 3,
  ADD: 4,
  SUB: 5,
  MUL: 6,
  DIV: 7,
  MOD: 8,
  NEG: 9,
  ABS: 10,
  SIGN: 11,
  CEIL: 12,
  FLOOR: 13,
  TRUNC: 14,
  ROUND: 15,
  POWER: 16,
  MIN: 17,
  MAX: 18,
};

// The vocabulary -- which source nodes compile, to which operation, with how
// many operands and which error positions -- is spec/math-ops.json's, rendered
// into _math_ops.mjs. The opcode NUMBERS are this host's own (OpCode above);
// the manifest names each operation and this maps the name to the number,
// refusing to load if the executor lacks one.
const NATIVE = Object.freeze(Object.fromEntries(MATH_OPS.map((name) => {
  if (!(name in OpCode)) throw new Error(`spec/math-ops.json names ${name}, which this host's math plan has no opcode for`);
  return [name, OpCode[name]];
})));
const MATH_BINARY_OPS = new Set(Object.keys(MATH_OPERATORS));
const MATH_UNARY_OPS = new Set(Object.keys(MATH_PREFIX));
const MATH_BUILTINS = new Set(Object.keys(MANIFEST_BUILTINS));

export function isMathOp(node) {
  if (!node || typeof node !== 'object') return false;
  if (node.t === 'bin' && MATH_BINARY_OPS.has(node.op)) return true;
  if (node.t === 'un' && MATH_UNARY_OPS.has(node.op)) return true;
  if (node.t === 'call' && MATH_BUILTINS.has(node.name)) return true;
  return false;
}

export function compileMathPlan(root) {
  if (!isMathOp(root)) return null;

  const steps = [];
  let slotCount = 0;
  const allocSlot = () => slotCount++;

  function emit(node, depth) {
    if (depth > MAX_DEPTH) return null;

    if (node.t === 'var') {
      const slot = allocSlot();
      steps.push({ op: OpCode.LOAD_VAR, dst: slot, name: node.name, pos: node.pos });
      return { slot, constVal: null };
    }

    if (node.t === 'num') {
      let dec = node.dec || null;
      if (!dec) {
        try {
          dec = D.parse(node.v, node.pos);
        } catch {
          return null;
        }
      }
      const slot = allocSlot();
      steps.push({ op: OpCode.LOAD_CONST, dst: slot, constVal: dec, pos: node.pos });
      return { slot, constVal: dec };
    }

    if (node.t === 'bin' && MATH_BINARY_OPS.has(node.op)) {
      if (!node.l || !node.r) return null;
      const resL = emit(node.l, depth + 1);
      if (!resL) return null;
      const resR = emit(node.r, depth + 1);
      if (!resR) return null;

      const op = node.op;

      // Copy propagation:
      // Rule 1: x + 0 (scale == 0) -> resL
      if (op === '+' && resR.constVal && D.isZero(resR.constVal) && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 2: 0 + x (scale == 0) -> resR
      if (op === '+' && resL.constVal && D.isZero(resL.constVal) && resL.constVal.scale === 0) {
        return resR;
      }
      // Rule 3: x - 0 (scale == 0) -> resL
      if (op === '-' && resR.constVal && D.isZero(resR.constVal) && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 4: x * 1 (scale == 0) -> resL
      if (op === '*' && resR.constVal && !resR.constVal.neg && (resR.constVal.digits === 1n || resR.constVal.digits === '1') && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 5: 1 * x (scale == 0) -> resR
      if (op === '*' && resL.constVal && !resL.constVal.neg && (resL.constVal.digits === 1n || resL.constVal.digits === '1') && resL.constVal.scale === 0) {
        return resR;
      }

      const dst = allocSlot();
      const opCode = NATIVE[MATH_OPERATORS[op]];
      steps.push({ op: opCode, dst, src1: resL.slot, src2: resR.slot, pos: node.pos });
      return { slot: dst, constVal: null };
    }

    if (node.t === 'un' && MATH_UNARY_OPS.has(node.op)) {
      if (!node.x) return null;
      const resX = emit(node.x, depth + 1);
      if (!resX) return null;
      const dst = allocSlot();
      steps.push({ op: NATIVE[MATH_PREFIX[node.op]], dst, src1: resX.slot, pos: node.pos });
      return { slot: dst, constVal: null };
    }

    // Math builtins: operand count, fold and error positions from the manifest
    // entry; a count the entry cannot serve derails the plan.
    if (node.t === 'call' && MATH_BUILTINS.has(node.name)) {
      const { op, arity, aux } = MANIFEST_BUILTINS[node.name];
      const opCode = NATIVE[op];
      const args = node.args || [];
      if (arity === 1) {
        if (args.length !== 1) return null;
        const resArg = emit(args[0], depth + 1);
        if (!resArg) return null;
        const dst = allocSlot();
        steps.push({ op: opCode, dst, src1: resArg.slot, pos: node.pos });
        return { slot: dst, constVal: null };
      }
      if (arity === 2) {
        if (args.length !== 2) return null;
        const res0 = emit(args[0], depth + 1);
        if (!res0) return null;
        const res1 = emit(args[1], depth + 1);
        if (!res1) return null;
        const dst = allocSlot();
        const step = { op: opCode, dst, src1: res0.slot, src2: res1.slot, pos: node.pos };
        if (aux !== null) step.auxPos = args[aux].pos;
        steps.push(step);
        return { slot: dst, constVal: null };
      }
      // fold: one or more operands, combined pairwise left to right
      if (args.length < 1) return null;
      const res0 = emit(args[0], depth + 1);
      if (!res0) return null;
      let currSlot = res0.slot;
      for (let k = 1; k < args.length; k++) {
        const resNext = emit(args[k], depth + 1);
        if (!resNext) return null;
        const dst = allocSlot();
        steps.push({ op: opCode, dst, src1: currSlot, src2: resNext.slot, pos: node.pos });
        currSlot = dst;
      }
      return { slot: currSlot, constVal: null };
    }

    if (node.t === 'bin' || node.t === 'un') return null;
    if (node.t === 'assign' || node.t === 'seq' || node.t === 'list') return null;
    if (node.t === 'call' && (node.name === 'IF' || node.name === 'COND')) return null;

    const slot = allocSlot();
    steps.push({ op: OpCode.LOAD_LEAF, dst: slot, leafNode: node, pos: node.pos });
    return { slot, constVal: null };
  }

  const res = emit(root, 1);
  if (!res || steps.length === 0) return null;
  return { steps, outputSlot: res.slot, scratchpadSize: slotCount };
}
