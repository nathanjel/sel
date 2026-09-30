use crate::ast::{MathPlan, MathStep, Node, NodeType};
use crate::context::Context;
use crate::dec::{
    dec_abs, dec_add, dec_ceil, dec_cmp, dec_div, dec_floor, dec_mod, dec_mul, dec_negate,
    dec_parse, dec_power, dec_round, dec_sign, dec_sub, dec_trunc, Dec,
};
use crate::eval::eval_node;
use crate::limits::MAX_DEPTH;
use crate::math_ops::{BUILTINS, OPERATORS, PREFIX};
use crate::utf8::{Pos, SelError};
use crate::value::Value;

pub const MAX_SCALE: i64 = 1_000_000;
pub const MAX_POWER: i64 = 100_000;

pub fn check_sized_int(
    d: &Dec,
    name: &str,
    arg_num: usize,
    limit: i64,
    what: &str,
    pos: Pos,
) -> Result<usize, SelError> {
    if !d.is_integer() {
        return Err(SelError::not_int(
            format!("{} argument {} must be a whole number", name, arg_num),
            pos,
        ));
    }
    let n = d
        .to_i64()
        .ok_or_else(|| SelError::range(format!("{} exceeds integer range", what), pos))?;
    if n < 0 {
        return Err(SelError::range(
            format!("{} argument {} must not be negative", name, arg_num),
            pos,
        ));
    }
    if n > limit {
        return Err(SelError::range(
            format!("{} {} exceeds the maximum of {}", what, n, limit),
            pos,
        ));
    }
    Ok(n as usize)
}

pub fn get_operator_name(op: &str) -> Option<&'static str> {
    for &(tok, name) in OPERATORS {
        if tok == op {
            return Some(name);
        }
    }
    None
}

pub fn get_prefix_name(op: &str) -> Option<&'static str> {
    for &(tok, name) in PREFIX {
        if tok == op {
            return Some(name);
        }
    }
    None
}

pub fn is_math_op(node: &Node) -> bool {
    if node.t == NodeType::Bin && get_operator_name(&node.s).is_some() {
        return true;
    }
    if node.t == NodeType::Un && get_prefix_name(&node.s).is_some() {
        return true;
    }
    if node.t == NodeType::Call {
        for &(name, _) in BUILTINS {
            if name == node.s {
                return true;
            }
        }
    }
    false
}

struct EmitResult {
    slot: u16,
}

pub fn compile_math_plan(root: &Node) -> Option<MathPlan> {
    if !is_math_op(root) {
        return None;
    }

    let mut steps = Vec::new();
    let mut slot_count: usize = 0;
    let mut overflow = false;

    let res = emit(root, 1, &mut steps, &mut slot_count, &mut overflow)?;
    if overflow || steps.is_empty() {
        return None;
    }

    Some(MathPlan {
        steps,
        output_slot: res.slot,
        scratchpad_size: slot_count as u16,
    })
}

fn alloc_slot(slot_count: &mut usize, overflow: &mut bool) -> u16 {
    if *slot_count >= u16::MAX as usize {
        *overflow = true;
        return 0;
    }
    let s = *slot_count as u16;
    *slot_count += 1;
    s
}

fn emit(
    node: &Node,
    depth: usize,
    steps: &mut Vec<MathStep>,
    slot_count: &mut usize,
    overflow: &mut bool,
) -> Option<EmitResult> {
    if depth > MAX_DEPTH {
        return None;
    }

    if node.t == NodeType::Var {
        let slot = alloc_slot(slot_count, overflow);
        steps.push(MathStep {
            op: "LOAD_VAR",
            dst: slot,
            src1: 0,
            src2: 0,
            pos: node.pos,
            aux_pos: Pos::default(),
            name: node.s.clone(),
            const_val: None,
            leaf_node: None,
        });
        return Some(EmitResult { slot });
    }

    if node.t == NodeType::Num {
        let dec = match node.dec {
            Some(ref d) => d.clone(),
            None => dec_parse(&node.s, node.pos).ok()?,
        };
        let slot = alloc_slot(slot_count, overflow);
        steps.push(MathStep {
            op: "LOAD_CONST",
            dst: slot,
            src1: 0,
            src2: 0,
            pos: node.pos,
            aux_pos: Pos::default(),
            name: String::new(),
            const_val: Some(dec.clone()),
            leaf_node: None,
        });
        return Some(EmitResult { slot });
    }

    if node.t == NodeType::Bin && get_operator_name(&node.s).is_some() {
        let l = node.l.as_ref()?;
        let r = node.r.as_ref()?;
        let res_l = emit(l, depth + 1, steps, slot_count, overflow)?;
        let res_r = emit(r, depth + 1, steps, slot_count, overflow)?;
        let op_name = get_operator_name(&node.s)?;

        let dst = alloc_slot(slot_count, overflow);
        steps.push(MathStep {
            op: op_name,
            dst,
            src1: res_l.slot,
            src2: res_r.slot,
            pos: node.pos,
            aux_pos: Pos::default(),
            name: String::new(),
            const_val: None,
            leaf_node: None,
        });
        return Some(EmitResult { slot: dst });
    }

    if node.t == NodeType::Un && get_prefix_name(&node.s).is_some() {
        let l = node.l.as_ref()?;
        let res_x = emit(l, depth + 1, steps, slot_count, overflow)?;
        let dst = alloc_slot(slot_count, overflow);
        steps.push(MathStep {
            op: get_prefix_name(&node.s)?,
            dst,
            src1: res_x.slot,
            src2: 0,
            pos: node.pos,
            aux_pos: Pos::default(),
            name: String::new(),
            const_val: None,
            leaf_node: None,
        });
        return Some(EmitResult { slot: dst });
    }

    if node.t == NodeType::Call {
        for &(name, ref b_spec) in BUILTINS {
            if name == node.s {
                let args = &node.items;
                match b_spec.arity {
                    crate::math_ops::MathArity::One => {
                        if args.len() != 1 {
                            return None;
                        }
                        let res_arg = emit(&args[0], depth + 1, steps, slot_count, overflow)?;
                        let dst = alloc_slot(slot_count, overflow);
                        steps.push(MathStep {
                            op: b_spec.op,
                            dst,
                            src1: res_arg.slot,
                            src2: 0,
                            pos: node.pos,
                            aux_pos: Pos::default(),
                            name: String::new(),
                            const_val: None,
                            leaf_node: None,
                        });
                        return Some(EmitResult { slot: dst });
                    }
                    crate::math_ops::MathArity::Two => {
                        if args.len() != 2 {
                            return None;
                        }
                        let res0 = emit(&args[0], depth + 1, steps, slot_count, overflow)?;
                        let res1 = emit(&args[1], depth + 1, steps, slot_count, overflow)?;
                        let dst = alloc_slot(slot_count, overflow);
                        let aux_pos = if let Some(aux) = b_spec.aux {
                            if aux < args.len() {
                                args[aux].pos
                            } else {
                                Pos::default()
                            }
                        } else {
                            Pos::default()
                        };
                        steps.push(MathStep {
                            op: b_spec.op,
                            dst,
                            src1: res0.slot,
                            src2: res1.slot,
                            pos: node.pos,
                            aux_pos,
                            name: String::new(),
                            const_val: None,
                            leaf_node: None,
                        });
                        return Some(EmitResult { slot: dst });
                    }
                    crate::math_ops::MathArity::Fold => {
                        if args.is_empty() {
                            return None;
                        }
                        let mut res_args = Vec::with_capacity(args.len());
                        for arg in args {
                            res_args.push(emit(arg, depth + 1, steps, slot_count, overflow)?);
                        }
                        let mut curr_slot = res_args[0].slot;
                        for res_next in &res_args[1..] {
                            let dst = alloc_slot(slot_count, overflow);
                            steps.push(MathStep {
                                op: b_spec.op,
                                dst,
                                src1: curr_slot,
                                src2: res_next.slot,
                                pos: node.pos,
                                aux_pos: Pos::default(),
                                name: String::new(),
                                const_val: None,
                                leaf_node: None,
                            });
                            curr_slot = dst;
                        }
                        return Some(EmitResult { slot: curr_slot });
                    }
                }
            }
        }
    }

    if matches!(
        node.t,
        NodeType::Bin | NodeType::Un | NodeType::Assign | NodeType::Seq | NodeType::List
    ) {
        return None;
    }
    if node.t == NodeType::Call && (node.s == "IF" || node.s == "COND") {
        return None;
    }

    let slot = alloc_slot(slot_count, overflow);
    steps.push(MathStep {
        op: "LOAD_LEAF",
        dst: slot,
        src1: 0,
        src2: 0,
        pos: node.pos,
        aux_pos: Pos::default(),
        name: String::new(),
        const_val: None,
        leaf_node: Some(Box::new(node.clone())),
    });
    Some(EmitResult { slot })
}

// Loaded values retain identity until their consuming operation. A later
// operand may mutate the value, or fail before the earlier value is coerced.
#[derive(Clone)]
enum MathValue {
    Number(Dec),
    Reference(Value, Pos),
}

impl MathValue {
    fn decimal(&self) -> Result<Dec, SelError> {
        match self {
            Self::Number(d) => Ok(d.clone()),
            Self::Reference(v, pos) => v.as_decimal(*pos),
        }
    }
}

pub fn eval_math_plan(plan: &MathPlan, ctx: &mut Context) -> Result<Value, SelError> {
    let mut scratchpad = vec![MathValue::Number(Dec::zero()); plan.scratchpad_size as usize];
    for step in &plan.steps {
        let dst = step.dst as usize;
        match step.op {
            "LOAD_VAR" => {
                let val = ctx.lookup(&step.name).ok_or_else(|| {
                    SelError::undef_var(format!("undefined variable {}", step.name), step.pos)
                })?;
                scratchpad[dst] = MathValue::Reference(val, step.pos);
                continue;
            }
            "LOAD_CONST" => {
                scratchpad[dst] = MathValue::Number(step.const_val.as_ref().unwrap().clone());
                continue;
            }
            "LOAD_LEAF" => {
                let leaf = step.leaf_node.as_ref().unwrap();
                scratchpad[dst] = MathValue::Reference(eval_node(leaf, ctx)?, leaf.pos);
                continue;
            }
            _ => {}
        }
        // Each operand subtree has finished by this point. Coercion occurs at
        // this operation, left to right, just as in the unoptimized evaluator.
        let a = scratchpad[step.src1 as usize].decimal()?;
        let result = match step.op {
            "NEG" => dec_negate(&a),
            "ABS" => dec_abs(&a),
            "SIGN" => Dec::from_i64(dec_sign(&a)),
            "CEIL" => dec_ceil(&a, step.pos)?,
            "FLOOR" => dec_floor(&a, step.pos)?,
            "TRUNC" => dec_trunc(&a),
            op => {
                let b = scratchpad[step.src2 as usize].decimal()?;
                match op {
                    "ADD" => dec_add(&a, &b, step.pos)?,
                    "SUB" => dec_sub(&a, &b, step.pos)?,
                    "MUL" => dec_mul(&a, &b, step.pos)?,
                    "DIV" => dec_div(&a, &b, step.pos)?,
                    "MOD" => dec_mod(&a, &b, step.pos)?,
                    "ROUND" => {
                        let scale = check_sized_int(
                            &b,
                            "ROUND",
                            2,
                            MAX_SCALE,
                            "ROUND scale",
                            step.aux_pos,
                        )?;
                        dec_round(&a, scale, step.pos)?
                    }
                    "POWER" => {
                        let exp = check_sized_int(
                            &b,
                            "POWER",
                            2,
                            MAX_POWER,
                            "POWER exponent",
                            step.aux_pos,
                        )?;
                        dec_power(&a, exp, step.pos)?
                    }
                    "MIN" => {
                        if dec_cmp(&b, &a).is_lt() {
                            b
                        } else {
                            a
                        }
                    }
                    "MAX" => {
                        if dec_cmp(&b, &a).is_gt() {
                            b
                        } else {
                            a
                        }
                    }
                    _ => {
                        return Err(SelError::syntax(
                            format!("unknown math step op {}", op),
                            step.pos,
                        ))
                    }
                }
            }
        };
        scratchpad[dst] = MathValue::Number(result);
    }
    Ok(Value::num_trusted(scratchpad[plan.output_slot as usize].decimal()?))
}
