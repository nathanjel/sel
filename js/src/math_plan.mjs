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
  // Not in the manifest: coerce one operand and pass it on (MIN(x), MAX(x)).
  ABS_IDENTITY: 99,
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
      return { slot, constVal: null, raw: node.pos };
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
      return { slot, constVal: dec, raw: null };
    }

    if (node.t === 'bin' && MATH_BINARY_OPS.has(node.op)) {
      if (!node.l || !node.r) return null;
      const resL = emit(node.l, depth + 1);
      if (!resL) return null;
      const resR = emit(node.r, depth + 1);
      if (!resR) return null;

      const op = node.op;

      // Copy propagation, from a slot that already holds a decimal. A load
      // leaves the VALUE in its slot and the operation that consumes it does the
      // coercion (SPEC 6.2: evaluate all operands, then coerce), so propagating
      // `x + 0` to a raw `x` would move the coercion -- and the moment the value
      // is read -- past whatever the rest of the expression evaluates.
      const propagate = (res) => res.raw === null;
      // Rule 1: x + 0 (scale == 0) -> resL
      if (op === '+' && propagate(resL) && resR.constVal && D.isZero(resR.constVal) && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 2: 0 + x (scale == 0) -> resR
      if (op === '+' && propagate(resR) && resL.constVal && D.isZero(resL.constVal) && resL.constVal.scale === 0) {
        return resR;
      }
      // Rule 3: x - 0 (scale == 0) -> resL
      if (op === '-' && propagate(resL) && resR.constVal && D.isZero(resR.constVal) && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 4: x * 1 (scale == 0) -> resL
      if (op === '*' && propagate(resL) && resR.constVal && !resR.constVal.neg && resR.constVal.digits === 1n && resR.constVal.scale === 0) {
        if (node.r.t === 'num' && steps.length && steps[steps.length - 1].dst === resR.slot) {
          steps.pop();
        }
        return resL;
      }
      // Rule 5: 1 * x (scale == 0) -> resR
      if (op === '*' && propagate(resR) && resL.constVal && !resL.constVal.neg && resL.constVal.digits === 1n && resL.constVal.scale === 0) {
        return resR;
      }

      const dst = allocSlot();
      const opCode = NATIVE[MATH_OPERATORS[op]];
      steps.push({ op: opCode, dst, src1: resL.slot, src2: resR.slot, p1: resL.raw, p2: resR.raw, pos: node.pos });
      return { slot: dst, constVal: null, raw: null };
    }

    if (node.t === 'un' && MATH_UNARY_OPS.has(node.op)) {
      if (!node.x) return null;
      const resX = emit(node.x, depth + 1);
      if (!resX) return null;
      const dst = allocSlot();
      steps.push({ op: NATIVE[MATH_PREFIX[node.op]], dst, src1: resX.slot, p1: resX.raw, pos: node.pos });
      return { slot: dst, constVal: null, raw: null };
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
        steps.push({ op: opCode, dst, src1: resArg.slot, p1: resArg.raw, pos: node.pos });
        return { slot: dst, constVal: null, raw: null };
      }
      if (arity === 2) {
        if (args.length !== 2) return null;
        const res0 = emit(args[0], depth + 1);
        if (!res0) return null;
        const res1 = emit(args[1], depth + 1);
        if (!res1) return null;
        const dst = allocSlot();
        const step = { op: opCode, dst, src1: res0.slot, src2: res1.slot, p1: res0.raw, p2: res1.raw, pos: node.pos };
        if (aux !== null) step.auxPos = args[aux].pos;
        steps.push(step);
        return { slot: dst, constVal: null, raw: null };
      }
      // fold: one or more operands, combined pairwise left to right -- after
      // every operand has been evaluated (SPEC 6.2, 7.1), so a later operand's
      // evaluation error is found before an earlier operand's coercion error.
      if (args.length < 1) return null;
      const ops = [];
      for (let k = 0; k < args.length; k++) {
        const r = emit(args[k], depth + 1);
        if (!r) return null;
        ops.push(r);
      }
      let curr = ops[0];
      for (let k = 1; k < ops.length; k++) {
        const dst = allocSlot();
        steps.push({ op: opCode, dst, src1: curr.slot, src2: ops[k].slot, p1: curr.raw, p2: ops[k].raw, pos: node.pos });
        curr = { slot: dst, constVal: null, raw: null };
      }
      if (ops.length === 1) {
        // A one-operand MIN/MAX is that operand, still coerced where the
        // plain tree coerces it.
        const dst = allocSlot();
        steps.push({ op: OpCode.ABS_IDENTITY, dst, src1: curr.slot, p1: curr.raw, pos: node.pos });
        curr = { slot: dst, constVal: null, raw: null };
      }
      return curr;
    }

    if (node.t === 'bin' || node.t === 'un') return null;
    // IF/COND, `,`, `;` and assignment are not arithmetic, but they are operands
    // like any call: loaded (evaluated by the tree evaluator, in operand order,
    // with its own laziness) and coerced where the consuming operation coerces
    // (SPEC 6.2). Refusing them here left the whole expression unplanned.

    const slot = allocSlot();
    steps.push({ op: OpCode.LOAD_LEAF, dst: slot, leafNode: node, pos: node.pos });
    return { slot, constVal: null, raw: node.pos };
  }

  const res = emit(root, 1);
  if (!res || steps.length === 0) return null;
  return { steps, outputSlot: res.slot, scratchpadSize: slotCount };
}
