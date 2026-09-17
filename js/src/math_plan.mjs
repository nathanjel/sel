import * as D from './decimal.mjs';
import { MAX_DEPTH } from './errors.mjs';

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

const MATH_BINARY_OPS = new Set(['+', '-', '*', '/', '%']);
const MATH_UNARY_OPS = new Set(['NEG']);
const MATH_BUILTINS = new Set([
  'ROUND', 'ABS', 'SIGN', 'CEIL', 'FLOOR', 'TRUNC', 'POWER', 'MIN', 'MAX'
]);

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
      let opCode;
      switch (op) {
        case '+': opCode = OpCode.ADD; break;
        case '-': opCode = OpCode.SUB; break;
        case '*': opCode = OpCode.MUL; break;
        case '/': opCode = OpCode.DIV; break;
        case '%': opCode = OpCode.MOD; break;
      }
      steps.push({ op: opCode, dst, src1: resL.slot, src2: resR.slot, pos: node.pos });
      return { slot: dst, constVal: null };
    }

    if (node.t === 'un' && node.op === 'NEG') {
      if (!node.x) return null;
      const resX = emit(node.x, depth + 1);
      if (!resX) return null;
      const dst = allocSlot();
      steps.push({ op: OpCode.NEG, dst, src1: resX.slot, pos: node.pos });
      return { slot: dst, constVal: null };
    }

    if (node.t === 'call' && MATH_BUILTINS.has(node.name)) {
      const name = node.name;
      if (name === 'ABS' || name === 'SIGN' || name === 'CEIL' || name === 'FLOOR' || name === 'TRUNC') {
        if (!node.args || node.args.length !== 1) return null;
        const resArg = emit(node.args[0], depth + 1);
        if (!resArg) return null;
        const dst = allocSlot();
        let opCode;
        switch (name) {
          case 'ABS': opCode = OpCode.ABS; break;
          case 'SIGN': opCode = OpCode.SIGN; break;
          case 'CEIL': opCode = OpCode.CEIL; break;
          case 'FLOOR': opCode = OpCode.FLOOR; break;
          case 'TRUNC': opCode = OpCode.TRUNC; break;
        }
        steps.push({ op: opCode, dst, src1: resArg.slot, pos: node.pos });
        return { slot: dst, constVal: null };
      }
      if (name === 'ROUND' || name === 'POWER') {
        if (!node.args || node.args.length !== 2) return null;
        const res0 = emit(node.args[0], depth + 1);
        if (!res0) return null;
        const res1 = emit(node.args[1], depth + 1);
        if (!res1) return null;
        const dst = allocSlot();
        const opCode = name === 'ROUND' ? OpCode.ROUND : OpCode.POWER;
        steps.push({ op: opCode, dst, src1: res0.slot, src2: res1.slot, pos: node.pos, auxPos: node.args[1].pos });
        return { slot: dst, constVal: null };
      }
      if (name === 'MIN' || name === 'MAX') {
        if (!node.args || node.args.length < 1) return null;
        const res0 = emit(node.args[0], depth + 1);
        if (!res0) return null;
        let currSlot = res0.slot;
        const opCode = name === 'MIN' ? OpCode.MIN : OpCode.MAX;
        for (let k = 1; k < node.args.length; k++) {
          const resNext = emit(node.args[k], depth + 1);
          if (!resNext) return null;
          const dst = allocSlot();
          steps.push({ op: opCode, dst, src1: currSlot, src2: resNext.slot, pos: node.pos });
          currSlot = dst;
        }
        return { slot: currSlot, constVal: null };
      }
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
