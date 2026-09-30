use crate::args::Args;
use crate::ast::{Node, NodeType, SlotCache};
use crate::builtins::{lookup_spec, BuiltinFn, Spec, SpecFn};
use crate::context::Context;
use crate::dec::{dec_add, dec_cmp, dec_div, dec_mod, dec_mul, dec_negate, dec_sub};
use crate::limits::MAX_DEPTH;
use crate::math_plan::eval_math_plan;
use crate::utf8::{cap_collection, cap_text, Pos, SelError};
use crate::value::{Kind, Value};
use std::sync::Arc;

pub fn eval_node(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    ctx.depth += 1;
    if ctx.depth > MAX_DEPTH {
        ctx.depth -= 1;
        return Err(SelError::depth("evaluation nested too deeply", node.pos));
    }

    let res = if let Some(ref plan) = node.math_plan {
        eval_math_plan(plan, ctx)
    } else {
        dispatch(node, ctx)
    };
    ctx.depth -= 1;
    if res.is_err() {
        // A join's prefilter state is handed from a LINK to its parent on a
        // normal return; on an error that a `??` or a join probe catches,
        // nothing will consume it, and the next unrelated LINK must not find
        // it there.
        ctx.join_prefilter = None;
        ctx.join_prefilter_report = None;
    }
    res
}

#[inline(never)]
fn dispatch(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    match node.t {
        NodeType::Num => Ok(Value::num_exact(
            node.s.clone(),
            node.dec.as_ref().unwrap().clone(),
        )),
        NodeType::Text => Ok(Value::text_owned(node.s.clone())),
        NodeType::Bool => Ok(Value::bool(node.b)),
        NodeType::Null => Ok(Value::null()),
        NodeType::Var => ctx
            .lookup(&node.s)
            .ok_or_else(|| SelError::undef_var(format!("undefined variable {}", node.s), node.pos)),
        NodeType::Index => eval_index(node, ctx),
        NodeType::Seq => {
            let mut last = Value::none();
            for item in &node.items {
                last = eval_node(item, ctx)?;
            }
            Ok(last)
        }
        NodeType::List => eval_list(node, ctx),
        NodeType::Un => eval_unary(node, ctx),
        NodeType::Bin => eval_binary(node, ctx),
        NodeType::Assign => eval_assign(node, ctx),
        NodeType::Call => eval_call(node, ctx),
    }
}

#[inline(never)]
fn eval_index(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let obj_node = node.l.as_ref().unwrap();
    let obj = if obj_node.t == NodeType::Var {
        ctx.lookup(&obj_node.s).ok_or_else(|| {
            SelError::undef_var(format!("undefined variable {}", obj_node.s), obj_node.pos)
        })?
    } else {
        eval_node(obj_node, ctx)?
    };

    let r_node = node.r.as_ref().unwrap();
    let literal = r_node.t == NodeType::Text;
    let key: String;
    if literal {
        if let Some(sc) = node.slot_cache.get() {
            let inner = obj.0.borrow();
            if let Some(ref sh) = inner.shape {
                if sh.id == sc.shape_id {
                    if let Some(ref st) = inner.storage {
                        return Ok(st[sc.slot].clone());
                    }
                }
            }
        }
        key = r_node.s.clone();
    } else {
        key = eval_node(r_node, ctx)?.as_text(r_node.pos)?;
    }

    let inner = obj.0.borrow();
    if let Some(ref sh) = inner.shape {
        if let Some(&idx) = sh.key_map.get(&key) {
            if literal {
                node.slot_cache.set(Some(SlotCache {
                    shape_id: sh.id,
                    slot: idx,
                }));
            }
            return Ok(inner.storage.as_ref().unwrap()[idx].clone());
        }
        return Err(SelError::no_key(format!("no key {:?}", key), node.pos));
    }
    drop(inner);

    obj.get(&key)
        .ok_or_else(|| SelError::no_key(format!("no key {:?}", key), node.pos))
}

#[inline(never)]
fn eval_list(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let mut values = Vec::new();
    for item in &node.items {
        let v = eval_node(item, ctx)?;
        let inner = v.0.borrow();
        let add_count = if inner.kind == Kind::None && inner.size() > 0 {
            inner.size()
        } else {
            1
        };
        cap_collection((values.len() + add_count) as u128, node.pos)?;
        if inner.kind == Kind::None && inner.size() > 0 {
            if let Some(ref storage) = inner.storage {
                for child in storage {
                    values.push(child.deep_copy(2, node.pos)?);
                }
            } else {
                for e in &inner.entries {
                    values.push(e.val.deep_copy(2, node.pos)?);
                }
            }
        } else {
            drop(inner);
            values.push(v.deep_copy(2, node.pos)?);
        }
    }
    Ok(Value::list_owned(values))
}

#[inline(never)]
fn eval_unary(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let l_node = node.l.as_ref().unwrap();
    let v = eval_node(l_node, ctx)?;
    if node.s == "NOT" {
        let b = v.as_bool(l_node.pos)?;
        return Ok(Value::bool(!b));
    }
    let d = v.as_decimal(l_node.pos)?;
    Ok(Value::num_trusted(dec_negate(&d)))
}

#[inline(never)]
fn eval_binary(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let op = &node.s;
    let l_node = node.l.as_ref().unwrap();
    let r_node = node.r.as_ref().unwrap();

    if op == "AND" || op == "OR" {
        let left = eval_node(l_node, ctx)?.as_bool(l_node.pos)?;
        if op == "AND" && !left {
            return Ok(Value::bool(false));
        }
        if op == "OR" && left {
            return Ok(Value::bool(true));
        }
        let right = eval_node(r_node, ctx)?.as_bool(r_node.pos)?;
        return Ok(Value::bool(right));
    }

    if op == "??" || op == "???" {
        let left_res = eval_node(l_node, ctx);
        match left_res {
            Ok(l) => {
                if (op == "??" && l.is_null()) || (op == "???" && l.is_vacuous()) {
                    return eval_node(r_node, ctx);
                }
                return Ok(l);
            }
            Err(e) if e.code == "E_NO_KEY" || e.code == "E_UNDEF_VAR" => {
                return eval_node(r_node, ctx);
            }
            Err(e) => return Err(e),
        }
    }

    let l = eval_node(l_node, ctx)?;
    let r = eval_node(r_node, ctx)?;
    let lp = l_node.pos;
    let rp = r_node.pos;

    apply_binary(op, &l, &r, lp, rp, node.pos)
}

// Arithmetic/conversion temporaries must not live in every recursive frame.
#[inline(never)]
fn apply_binary(
    op: &str,
    l: &Value,
    r: &Value,
    lp: Pos,
    rp: Pos,
    opos: Pos,
) -> Result<Value, SelError> {
    match op {
        "+" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            Ok(Value::num_trusted(dec_add(&a, &b, opos)?))
        }
        "-" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            Ok(Value::num_trusted(dec_sub(&a, &b, opos)?))
        }
        "*" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            Ok(Value::num_trusted(dec_mul(&a, &b, opos)?))
        }
        "/" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            Ok(Value::num_trusted(dec_div(&a, &b, opos)?))
        }
        "%" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            Ok(Value::num_trusted(dec_mod(&a, &b, opos)?))
        }
        "&" => concat(l, r, lp, rp, opos),
        "==" | "!=" | "<" | "<=" | ">" | ">=" => {
            let a = l.as_decimal(lp)?;
            let b = r.as_decimal(rp)?;
            let c = dec_cmp(&a, &b);
            Ok(Value::bool(compare_result(op, c)))
        }
        "$==" => {
            if l.is_text() && r.is_text() && l.size() == 0 && r.size() == 0 {
                return Ok(Value::bool(l.scalar() == r.scalar()));
            }
            let a = l.as_bytes(lp)?;
            let b = r.as_bytes(rp)?;
            Ok(Value::bool(a == b))
        }
        "$!=" => {
            if l.is_text() && r.is_text() && l.size() == 0 && r.size() == 0 {
                return Ok(Value::bool(l.scalar() != r.scalar()));
            }
            let a = l.as_bytes(lp)?;
            let b = r.as_bytes(rp)?;
            Ok(Value::bool(a != b))
        }
        "$<" | "$<=" | "$>" | "$>=" => {
            let a = l.as_bytes(lp)?;
            let b = r.as_bytes(rp)?;
            let c = a.cmp(&b);
            Ok(Value::bool(compare_result(&op[1..], c)))
        }
        "EQL" => {
            let eq = l.eql(r, 1, opos)?;
            Ok(Value::bool(eq))
        }
        "IN" => {
            let res = is_in(l, r, opos)?;
            Ok(Value::bool(res))
        }
        "XOR" => {
            let a = l.as_bool(lp)?;
            let b = r.as_bool(rp)?;
            Ok(Value::bool(a != b))
        }
        "BAND" | "BOR" | "BXOR" => {
            let a = l.as_bytes(lp)?;
            let b = r.as_bytes(rp)?;
            bitwise(op, &a, &b, opos)
        }
        _ => Err(SelError::syntax(format!("unknown operator {}", op), opos)),
    }
}

fn compare_result(op: &str, c: std::cmp::Ordering) -> bool {
    match op {
        "==" => c.is_eq(),
        "!=" => c.is_ne(),
        "<" => c.is_lt(),
        "<=" => c.is_le(),
        ">" => c.is_gt(),
        ">=" => c.is_ge(),
        _ => false,
    }
}

fn concat(l: &Value, r: &Value, lp: Pos, rp: Pos, opos: Pos) -> Result<Value, SelError> {
    let lv = l.scalar_source(lp)?;
    let rv = r.scalar_source(rp)?;
    if lv.kind() == Kind::Bool {
        return Err(SelError::not_text("cannot concatenate a boolean", lp));
    }
    if rv.kind() == Kind::Bool {
        return Err(SelError::not_text("cannot concatenate a boolean", rp));
    }
    if lv.kind() == Kind::Text && rv.kind() == Kind::Text {
        let l_s = lv.scalar();
        let r_s = rv.scalar();
        let l_cps = l_s.chars().count();
        let r_cps = r_s.chars().count();
        cap_text((l_cps as u128) + (r_cps as u128), opos)?;
        return Ok(Value::text_owned(l_s + &r_s));
    }
    let a = l.as_bytes(lp)?;
    let b = r.as_bytes(rp)?;
    cap_text((a.len() as u128) + (b.len() as u128), opos)?;
    let mut res = a;
    res.extend_from_slice(&b);
    Ok(Value::bin_owned(res))
}

fn is_in(needle: &Value, hay: &Value, pos: Pos) -> Result<bool, SelError> {
    if hay.size() == 0 {
        return hay.eql(needle, 1, pos);
    }
    let inner = hay.0.borrow();
    if let Some(ref storage) = inner.storage {
        let st = storage.clone();
        drop(inner);
        for child in st {
            if child.eql(needle, 1, pos)? {
                return Ok(true);
            }
        }
        return Ok(false);
    }
    let entries = inner.entries.clone();
    drop(inner);
    for e in entries {
        if e.val.eql(needle, 1, pos)? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn bitwise(op: &str, a: &[u8], b: &[u8], pos: Pos) -> Result<Value, SelError> {
    if a.len() != b.len() {
        return Err(SelError::new(
            "E_LEN_MISMATCH",
            format!(
                "{} needs operands of equal length ({} vs {})",
                op,
                a.len(),
                b.len()
            ),
            pos,
        ));
    }
    let mut out = vec![0u8; a.len()];
    match op {
        "BAND" => {
            for i in 0..a.len() {
                out[i] = a[i] & b[i];
            }
        }
        "BOR" => {
            for i in 0..a.len() {
                out[i] = a[i] | b[i];
            }
        }
        "BXOR" => {
            for i in 0..a.len() {
                out[i] = a[i] ^ b[i];
            }
        }
        _ => unreachable!(),
    }
    Ok(Value::bin_owned(out))
}

#[inline(never)]
fn eval_assign(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let l_node = node.l.as_ref().unwrap();
    let r_node = node.r.as_ref().unwrap();
    let path = resolve_target(l_node, ctx)?;
    let key = path.last().unwrap().clone();

    let value = if node.s == "=" {
        eval_node(r_node, ctx)?.deep_copy(path.len(), l_node.pos)?
    } else {
        let target_obj = walk_create(ctx, &path, path.len() - 1);
        let current = target_obj.get(&key).ok_or_else(|| {
            SelError::undef_var(format!("{} needs an existing target", node.s), l_node.pos)
        })?;
        let rhs = eval_node(r_node, ctx)?;
        let op = node
            .s
            .strip_suffix('=')
            .expect("assignment operator suffix");
        apply_binary(op, &current, &rhs, l_node.pos, r_node.pos, node.pos)?
    };

    let target_obj = walk_create(ctx, &path, path.len() - 1);
    target_obj.set(&key, value.clone(), node.pos)?;
    Ok(value)
}

fn walk_create(ctx: &mut Context, path: &[String], upto: usize) -> Value {
    let mut cur = ctx.root.clone();
    for item in &path[..upto] {
        let nxt = match cur.get(item) {
            Some(v) => v,
            None => {
                let v = Value::none();
                let _ = cur.set(item, v.clone(), Pos::default());
                v
            }
        };
        cur = nxt;
    }
    cur
}

fn resolve_target(target: &Node, ctx: &mut Context) -> Result<Vec<String>, SelError> {
    let mut chain = Vec::new();
    let mut n = target;
    while n.t == NodeType::Index {
        chain.push(n.r.as_ref().unwrap().as_ref());
        n = n.l.as_ref().unwrap().as_ref();
    }
    chain.reverse();

    if ctx.is_bound(&n.s) {
        return Err(SelError::new(
            "E_BAD_ASSIGN",
            format!("{} is an aggregate binder and cannot be assigned", n.s),
            target.pos,
        ));
    }
    if chain.len() + 1 > MAX_DEPTH {
        return Err(SelError::depth("value nested too deeply", target.pos));
    }
    let mut path = vec![n.s.clone()];
    if chain.is_empty() {
        return Ok(path);
    }
    if ctx.root.get(&n.s).is_none() {
        let _ = ctx.root.set(&n.s, Value::none(), Pos::default());
    }
    for item in &chain[..chain.len() - 1] {
        let k = eval_node(item, ctx)?.as_text(item.pos)?;
        let cur = walk_create(ctx, &path, path.len());
        if cur.get(&k).is_none() {
            let _ = cur.set(&k, Value::none(), Pos::default());
        }
        path.push(k);
    }
    let last = chain.last().unwrap();
    path.push(eval_node(last, ctx)?.as_text(last.pos)?);
    Ok(path)
}

fn call_spec(node: &Node) -> Result<Arc<Spec>, SelError> {
    node.spec
        .clone()
        .or_else(|| lookup_spec(&node.s))
        .ok_or_else(|| {
            SelError::new(
                "E_UNKNOWN_FUNC",
                format!("unknown function {}", node.s),
                node.pos,
            )
        })
}

// Eager calls always evaluate their first argument first. For lazy calls,
// only these native implementations guarantee that order; a host callback
// with the same AST name need not do so.
fn source_first_call(node: &Node, spec: &Spec) -> bool {
    use crate::builtins::structure::*;
    if node.items.is_empty() {
        return false;
    }
    if !spec.lazy {
        return true;
    }
    let expected: BuiltinFn = match node.s.as_str() {
        "MAP" => fn_map,
        "FILTER" => fn_filter,
        "BUCKET" => fn_bucket,
        "SELECT_COLS" => fn_select_cols,
        "DISTINCT" => fn_distinct,
        "DEDUPE" => fn_dedupe,
        "TAKE" => fn_take,
        "DROP" => fn_drop,
        "SORT" => fn_sort,
        "SORT_DESC" => fn_sort_desc,
        "SORT_BY" => fn_sort_by,
        "TOP" => fn_top,
        "TOP_DESC" => fn_top_desc,
        "TOP_BY" => fn_top_by,
        "LINK" => fn_link,
        "LINK_LEFT" => fn_link_left,
        _ => return false,
    };
    if !matches!(&spec.func, SpecFn::Native(f) if std::ptr::fn_addr_eq(*f, expected)) {
        return false;
    }
    // These lazy functions validate explicit binders before reading the source.
    // Leave an invalid binder on the ordinary call path so its error wins.
    if spec.lazy {
        let is_symbol = |i: usize| {
            node.items
                .get(i)
                .is_some_and(|n| n.t == NodeType::Var && !n.grouped)
        };
        if matches!(node.s.as_str(), "MAP" | "FILTER") && node.items.len() == 3 {
            return is_symbol(1);
        }
        if matches!(node.s.as_str(), "LINK" | "LINK_LEFT") && node.items.len() == 5 {
            return is_symbol(2) && is_symbol(3);
        }
    }
    true
}

// A FILTER over a join hands its conjuncts to the LINK before the LINK runs
// (spec §7.4), so it evaluates its source itself, never as a pipeline stage.
fn filter_over_join(node: &Node) -> bool {
    node.t == NodeType::Call
        && node.s == "FILTER"
        && node.items.first().is_some_and(|source| {
            source.t == NodeType::Call && (source.s == "LINK" || source.s == "LINK_LEFT")
        })
}

#[inline(never)]
fn eval_call(node: &Node, ctx: &mut Context) -> Result<Value, SelError> {
    let spec = call_spec(node)?;
    // A call holding handed-down join state (a LINK below a FILTER or a join)
    // must see it before its sources run: no pipeline.
    let chain = ctx.join_prefilter.is_none()
        && !filter_over_join(node)
        && source_first_call(node, &spec)
        && node.items.first().is_some_and(|source| {
            source.t == NodeType::Call
                && source.math_plan.is_none()
                && call_spec(source).is_ok_and(|spec| source_first_call(source, &spec))
        });
    if chain {
        return eval_pipeline(node, spec, ctx);
    }
    invoke_call(node, &spec, ctx, None)
}

#[inline(never)]
fn eval_pipeline(node: &Node, spec: Arc<Spec>, ctx: &mut Context) -> Result<Value, SelError> {
    // eval_node has already charged the outer call. Pending calls retain their
    // logical depth while their source and subsequent arguments are evaluated.
    let entry_depth = ctx.depth;
    let result = (|| {
        let mut pending = vec![(node, spec)];
        let mut source = &node.items[0];
        loop {
            if ctx.depth >= MAX_DEPTH {
                return eval_node(source, ctx);
            }
            if source.t != NodeType::Call || source.math_plan.is_some() || filter_over_join(source) {
                break;
            }
            let next_spec = call_spec(source)?;
            if !source_first_call(source, &next_spec) {
                break;
            }
            ctx.depth += 1;
            pending.push((source, next_spec));
            source = &source.items[0];
        }
        let mut value = eval_node(source, ctx)?;
        while let Some((stage, stage_spec)) = pending.pop() {
            value = invoke_call(stage, &stage_spec, ctx, Some(value))?;
            if !pending.is_empty() {
                ctx.depth -= 1;
            }
        }
        Ok(value)
    })();
    ctx.depth = entry_depth;
    result
}

#[inline(never)]
fn invoke_call(
    node: &Node,
    spec: &Spec,
    ctx: &mut Context,
    source: Option<Value>,
) -> Result<Value, SelError> {
    let frame_depth = ctx.frames.len();
    let mut args = Args::new(node, ctx);
    if let Some(value) = source {
        args.preset(0, value);
    }
    if !spec.lazy {
        for i in 0..args.count() {
            if let Err(e) = args.val(i) {
                args.ctx.frames.truncate(frame_depth);
                return Err(e);
            }
        }
    }
    let res = spec.call(&mut args);
    args.ctx.frames.truncate(frame_depth);
    res
}
