use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::Arc;

use crate::args::Args;
use crate::ast::{Node, NodeType};
use crate::context::{Context, Frame};
use crate::dec::{dec_add, dec_cmp, dec_format, Dec};
use crate::eval::eval_node;
use crate::join_plan::{is_left_nested, make_joined_row, JoinFlatTest, JoinProjector};
use crate::join_prefilter::{
    join_keys_safe, join_pure_source, join_read_self, join_row_keys, join_stage_walk,
    join_totality, join_truncate_stages, leading_field_conjuncts, new_join_side_facts,
    JoinConjunct, JoinObligation, JoinPrefilter, JoinReport, JoinSideFacts, JoinStage,
    JoinTotalReq,
};
use crate::shape::{unique_record_shape, RecordShape};
use crate::utf8::{cap_collection, cap_text, Pos, SelError};
use crate::text::SelStr;
use crate::value::{ListKeys, Entry, Kind, Value};

fn node_contains_var(node: &Node, name: &str) -> bool {
    match node.t {
        NodeType::Var => node.s.eq_ignore_ascii_case(name),
        NodeType::Index => {
            node.l.as_ref().map_or(false, |l| node_contains_var(l, name))
                || node.r.as_ref().map_or(false, |r| node_contains_var(r, name))
        }
        NodeType::Call => node.items.iter().any(|a| node_contains_var(a, name)),
        NodeType::Bin => {
            node.l.as_ref().map_or(false, |l| node_contains_var(l, name))
                || node.r.as_ref().map_or(false, |r| node_contains_var(r, name))
        }
        NodeType::Un => node.l.as_ref().map_or(false, |l| node_contains_var(l, name)),
        NodeType::Assign => {
            node.l.as_ref().map_or(false, |l| node_contains_var(l, name))
                || node.r.as_ref().map_or(false, |r| node_contains_var(r, name))
        }
        NodeType::Seq | NodeType::List => node.items.iter().any(|item| node_contains_var(item, name)),
        _ => false,
    }
}

pub fn fn_count(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::int(args.val(0)?.size() as i64))
}

pub fn fn_indexes(args: &mut Args) -> Result<Value, SelError> {
    let keys = args.val(0)?.keys();
    let items = keys.into_iter().map(Value::text_owned).collect();
    Ok(Value::list_owned(items))
}

pub fn fn_has(args: &mut Args) -> Result<Value, SelError> {
    let target = args.val(0)?;
    let key = args.text(1)?;
    Ok(Value::bool(target.has(&key)))
}

pub fn fn_list(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    cap_collection(count as u128, args.pos())?;
    let mut items = Vec::with_capacity(count);
    for i in 0..count {
        items.push(args.val(i)?.deep_copy(2, args.pos())?);
    }
    Ok(Value::list_owned(items))
}

pub fn fn_record(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    if count == 0 {
        return Ok(Value::none());
    }
    cap_collection((count / 2) as u128, args.pos())?;
    // Every key a text literal (the parser then built the shape): the keys
    // are known, and evaluating them per row would only rebuild the same
    // strings. A literal has no effect and cannot fail -- except at the depth
    // limit, where the first key raises E_DEPTH, so that case keeps the
    // general path and its error position.
    if let Some(shape) = args.record_shape() {
        if args.ctx.depth < crate::limits::MAX_DEPTH {
            let mut values = Vec::with_capacity(count / 2);
            for i in (1..count).step_by(2) {
                values.push(args.val(i)?.deep_copy(2, args.pos())?);
            }
            return Ok(Value::shaped_record(shape, values));
        }
    }
    let mut keys = Vec::with_capacity(count / 2);
    let mut values = Vec::with_capacity(count / 2);
    for i in (0..count).step_by(2) {
        keys.push(args.text(i)?);
        values.push(args.val(i + 1)?.deep_copy(2, args.pos())?);
    }
    if let Some(shape) = args.record_shape() {
        if shape.keys.as_ref() == keys.as_slice() {
            return Ok(Value::shaped_record(shape, values));
        }
    }
    if let Some(u_shape) = unique_record_shape(&keys) {
        return Ok(Value::shaped_record(u_shape, values));
    }
    let entries = keys
        .into_iter()
        .zip(values.into_iter())
        .map(|(k, v)| Entry { key: k, val: v })
        .collect();
    Ok(Value::record_from_entries(entries))
}

pub fn fn_take(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.non_neg_int(1)? as usize;
    if count == 0 || val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let is_dense_list = {
        let inner = val.0.borrow();
        inner.is_list && inner.storage.is_some() && inner.list_keys.is_none()
    };
    if is_dense_list {
        let inner = val.0.borrow();
        let storage = inner.storage.as_ref().unwrap();
        let take_n = count.min(storage.len());
        return Ok(Value::list_owned(storage[..take_n].to_vec()));
    }
    let ents = val.elems();
    let take_n = count.min(ents.len());
    let items = ents.vals[..take_n].to_vec();
    Ok(Value::list_owned(items))
}

pub fn fn_drop(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.non_neg_int(1)? as usize;
    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let is_dense_list = {
        let inner = val.0.borrow();
        inner.is_list && inner.storage.is_some() && inner.list_keys.is_none()
    };
    if is_dense_list {
        let inner = val.0.borrow();
        let storage = inner.storage.as_ref().unwrap();
        if count >= storage.len() {
            return Ok(Value::list_owned(Vec::new()));
        }
        return Ok(Value::list_owned(storage[count..].to_vec()));
    }
    let ents = val.elems();
    if count >= ents.len() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let items = ents.vals[count..].to_vec();
    Ok(Value::list_owned(items))
}

pub fn fn_select_cols(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let num_cols = args.count() - 1;
    let mut columns = Vec::with_capacity(num_cols);
    for i in 0..num_cols {
        columns.push(args.text(i + 1)?);
    }

    let ents = val.elems();
    let mut rows = Vec::with_capacity(ents.len());
    for row in ents.vals {
        let mut row_entries = Vec::new();
        for col in &columns {
            if row.has(col) {
                row_entries.push(Entry {
                    key: col.clone(),
                    val: row.get(col).unwrap(),
                });
            }
        }
        rows.push(Value::record_from_entries(row_entries));
    }
    Ok(Value::list_owned(rows))
}

pub fn fn_dedupe(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let ents = val.elems();
    let mut buckets: HashMap<u64, Vec<Value>> = HashMap::new();
    let mut out = Vec::new();
    for (ei, ev) in ents.vals.iter().enumerate() {
        let item = ev.clone();
        let h = item.structural_hash()?;
        let bucket = buckets.entry(h).or_default();
        let mut found = false;
        for existing in bucket.iter() {
            if item.eql(existing, 1, Pos::default())? {
                found = true;
                break;
            }
        }
        if !found {
            bucket.push(item.clone());
            out.push(item);
        }
    }
    Ok(Value::list_owned(out))
}

pub fn fn_distinct(args: &mut Args) -> Result<Value, SelError> {
    fn_dedupe(args)
}

pub fn fn_map(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    let (binder, body_idx) = if count == 3 {
        (args.symbol(1)?, 2)
    } else {
        ("_".to_string(), 1)
    };
    let body_node = args.node_at(body_idx).clone();
    let needs_k = node_contains_var(&body_node, "_K");
    // RECORD returns a fresh container with independently copied fields. It
    // cannot publish that container before returning it to this collector.
    let fresh_record = body_node.t == NodeType::Call
        && body_node.s == "RECORD" && body_node.math_plan.is_none()
        && body_node.spec.as_ref().is_none_or(|spec| match &spec.func {
            crate::builtins::SpecFn::Native(function) =>
                std::ptr::fn_addr_eq(*function, fn_record as crate::builtins::BuiltinFn),
            crate::builtins::SpecFn::Host(_) => false,
        });

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let ents = val.elems();
    let mut out = Vec::with_capacity(ents.len());

    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }
    args.ctx.push_frame(frame);

    for (ei, ev) in ents.vals.iter().enumerate() {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, ev.clone());
            if needs_k {
                f.set("_K", Value::text_owned(ents.key(ei)));
            }
        }
        let res = match args.eval_node(&body_node) {
            Ok(v) => v,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        let collected = if fresh_record {
            // MAP adds a copy-depth level even when its allocation is elided.
            res.check_copy_depth(2, args.pos()).map(|()| res)
        } else {
            res.deep_copy(2, args.pos())
        };
        match collected {
            Ok(value) => out.push(value),
            Err(error) => {
                args.ctx.pop_frame();
                return Err(error);
            }
        }
    }
    args.ctx.pop_frame();

    Ok(Value::list_owned(out))
}

// What a FILTER knows before its source runs. Its source is read in
// `fn_filter`'s own thin frame: over a join that read is recursion (a chain
// of FILTER-over-LINK stages is not a pipeline), so the work before and after
// it is out of line.
struct FilterPlan {
    binder: String,
    body_idx: usize,
    over_join: bool,
    had_handed: bool,
    own: Vec<JoinConjunct>,
}

pub fn fn_filter(args: &mut Args) -> Result<Value, SelError> {
    let plan = filter_plan(args)?;
    let source = args.val(0);
    args.ctx.join_prefilter = None;
    filter_rows(args, plan, source)
}

#[inline(never)]
fn filter_plan(args: &mut Args) -> Result<Box<FilterPlan>, SelError> {
    let count = args.count();
    // Over a join, the conjuncts are offered to the LINK, which tests what it
    // can on the rows it joins (spec §7.4): this FILTER's first, then the
    // ones handed down from a FILTER above. Deep drops change this FILTER's
    // keys, so they are allowed only where nothing observes them
    // (`keys_unobserved`, stamped by the physical optimiser).
    let handed = args.ctx.join_prefilter.take();
    let (binder, body_idx) = if count == 3 {
        (args.symbol(1)?, 2)
    } else {
        ("_".to_string(), 1)
    };
    let nodes: &[Node] = args.nodes;
    let written: &Node = &nodes[body_idx];
    let src = &nodes[0];
    let over_join = src.t == NodeType::Call && (src.s == "LINK" || src.s == "LINK_LEFT");
    let had_handed = handed.is_some();
    let mut own: Vec<JoinConjunct> = Vec::new();
    if over_join {
        own = leading_field_conjuncts(written, &binder);
        // A first conjunct that is neither a field test nor total ends every
        // walk before it starts: hand nothing.
        let blocked = !own.is_empty() && !own[0].field_only && !own[0].has_total;
        let mut stages = Vec::new();
        let mut above = Vec::new();
        let mut obligations = Vec::new();
        if !blocked {
            stages.push(JoinStage {
                binder: binder.clone(),
                conjuncts: own.clone(),
                above: 0,
            });
            if let Some(h) = handed {
                stages.extend(h.stages);
                above = h.above;
                obligations = h.obligations;
            }
        }
        let deep = if had_handed { true } else { written.keys_unobserved };
        if !stages.is_empty() {
            args.ctx.join_prefilter = Some(JoinPrefilter {
                stages,
                deep,
                above,
                obligations,
            });
        }
    }
    Ok(Box::new(FilterPlan { binder, body_idx, over_join, had_handed, own }))
}

#[inline(never)]
fn filter_rows(args: &mut Args, plan: Box<FilterPlan>, source: Result<Value, SelError>) -> Result<Value, SelError> {
    let val = source?;
    let FilterPlan { binder, body_idx, over_join, had_handed, own } = *plan;
    let nodes: &[Node] = args.nodes;
    let written: &Node = &nodes[body_idx];

    // The join's report goes up as it is to a join that handed conjuncts down.
    let report = args.ctx.join_prefilter_report.take();
    // The conjuncts of this FILTER the join below applied held on every row
    // it built, unless it kept a row on an error: only the rest is evaluated,
    // in the source's order (with none left, the join's list is the result).
    let mut override_body: Option<Node> = None;
    let mut all_applied = false;
    if over_join {
        if let Some(ref r) = report {
            if !r.errored {
                let rest: Vec<&JoinConjunct> = own.iter().filter(|c| !r.applied.contains_key(&c.id)).collect();
                if rest.len() < own.len() {
                    if rest.is_empty() {
                        all_applied = true;
                    } else {
                        let mut body = rest[0].node.clone();
                        for c in &rest[1..] {
                            let mut and = Node::new(NodeType::Bin, body.pos);
                            and.s = "AND".to_string();
                            and.l = Some(Box::new(body));
                            and.r = Some(Box::new(c.node.clone()));
                            body = and;
                        }
                        override_body = Some(body);
                    }
                }
            }
        }
    }
    if had_handed {
        args.ctx.join_prefilter_report = report;
    }
    if all_applied {
        return Ok(val);
    }

    let body_node: &Node = override_body.as_ref().unwrap_or(written);
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(body_node, "_K");

    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let ents = val.elems();
    let mut storage = Vec::new();
    // Source slots of the kept children: their keys are the output's keys.
    let mut kept: Vec<usize> = Vec::new();

    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }
    args.ctx.push_frame(frame);

    for (i, item) in ents.vals.iter().enumerate() {
        args.ctx.bind(&binder, item.clone());
        if needs_k {
            args.ctx.bind("_K", Value::text_owned(ents.key(i)));
        }
        let keep = match args.eval_node(&body_node) {
            Ok(v) => match v.as_bool(body_pos) {
                Ok(b) => b,
                Err(err) => {
                    args.ctx.pop_frame();
                    return Err(err);
                }
            },
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        if keep {
            if args.borrowed_filter {
                item.check_copy_depth(2, args.pos())?;
                storage.push(item.clone());
            } else {
                storage.push(item.deep_copy(2, args.pos())?);
            }
            kept.push(i);
        }
    }
    args.ctx.pop_frame();

    // FILTER always answers a list container, whatever its source: a record or
    // a scalar that keeps nothing is an empty list, never NULL. Keys are
    // preserved (spec §7.3): numbered 1..n only when nothing was dropped and
    // the source was numbered so; positions stay numbers, not text.
    let all_kept = kept.len() == ents.len();
    if all_kept && (0..ents.len()).all(|i| ents.keyed_by_position(i)) {
        return Ok(Value::list_owned(storage));
    }
    let positions: Option<Vec<u32>> = kept.iter().map(|&i| ents.index_key(i)).collect();
    let keys = match positions {
        Some(ix) => ListKeys::Index(ix.into()),
        None => ListKeys::Text(kept.iter().map(|&i| ents.key(i)).collect::<Vec<_>>().into()),
    };
    Ok(Value::list_with_list_keys(storage, keys))
}

pub fn fn_all(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    let (binder, body_idx) = if count == 3 {
        (args.symbol(1)?, 2)
    } else {
        ("_".to_string(), 1)
    };
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::bool(true));
    }

    let ents = val.elems();
    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }
    args.ctx.push_frame(frame);

    for (ei, ev) in ents.vals.iter().enumerate() {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, ev.clone());
            if needs_k {
                f.set("_K", Value::text_owned(ents.key(ei)));
            }
        }
        let res = match args.eval_node(&body_node) {
            Ok(v) => v,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        match res.as_bool(body_pos) {
            Ok(b) => {
                if !b {
                    args.ctx.pop_frame();
                    return Ok(Value::bool(false));
                }
            }
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        }
    }
    args.ctx.pop_frame();
    Ok(Value::bool(true))
}

pub fn fn_any(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    let (binder, body_idx) = if count == 3 {
        (args.symbol(1)?, 2)
    } else {
        ("_".to_string(), 1)
    };
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::bool(false));
    }

    let ents = val.elems();
    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }
    args.ctx.push_frame(frame);

    for (ei, ev) in ents.vals.iter().enumerate() {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, ev.clone());
            if needs_k {
                f.set("_K", Value::text_owned(ents.key(ei)));
            }
        }
        let res = match args.eval_node(&body_node) {
            Ok(v) => v,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        match res.as_bool(body_pos) {
            Ok(b) => {
                if b {
                    args.ctx.pop_frame();
                    return Ok(Value::bool(true));
                }
            }
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        }
    }
    args.ctx.pop_frame();
    Ok(Value::bool(false))
}

pub fn fn_sum(args: &mut Args) -> Result<Value, SelError> {
    let count = args.count();
    if count == 1 {
        let val = args.val(0)?;
        if val.is_null() {
            return Ok(Value::int(0));
        }
        let mut total = Dec::zero();
        for item in val.elems().vals {
            let d = item.as_decimal(args.pos_at(0))?;
            total = dec_add(&total, &d, args.pos())?;
        }
        return Ok(Value::num_trusted(total));
    }
    let (binder, body_idx) = if count == 3 {
        (args.symbol(1)?, 2)
    } else {
        ("_".to_string(), 1)
    };
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::int(0));
    }

    let ents = val.elems();
    let mut total = Dec::zero();
    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }
    args.ctx.push_frame(frame);

    for (ei, ev) in ents.vals.iter().enumerate() {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, ev.clone());
            if needs_k {
                f.set("_K", Value::text_owned(ents.key(ei)));
            }
        }
        let v = match args.eval_node(&body_node) {
            Ok(v) => v,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        let d = match v.as_decimal(body_pos) {
            Ok(d) => d,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        };
        match dec_add(&total, &d, args.pos()) {
            Ok(t) => total = t,
            Err(err) => {
                args.ctx.pop_frame();
                return Err(err);
            }
        }
    }
    args.ctx.pop_frame();
    Ok(Value::num_trusted(total))
}

pub fn fn_join(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let sep = args.text(1)?;
    if val.is_null() {
        return Ok(Value::text_owned(String::new()));
    }
    let ents = val.elems();
    let mut parts = Vec::with_capacity(ents.len());
    let mut total: u128 = 0;
    for item in ents.vals {
        let t = item.as_text(args.pos_at(0))?;
        total += t.chars().count() as u128;
        parts.push(t);
    }
    if parts.len() > 1 {
        total += (parts.len() as u128 - 1) * (sep.chars().count() as u128);
    }
    cap_text(total, args.pos())?;
    Ok(Value::text_owned(parts.join(&sep)))
}

fn value_rank(v: &Value) -> i32 {
    if v.is_null() {
        0
    } else if v.kind() == Kind::Bool {
        1
    } else if v.kind() == Kind::Text && v.looks_numeric() {
        2
    } else if v.kind() == Kind::Text {
        3
    } else if v.kind() == Kind::Bin {
        4
    } else {
        5
    }
}

pub fn compare_values(a: &Value, b: &Value) -> Ordering {
    // Sort uses scalar context too: a record/list follows its first child,
    // while a value with its own scalar keeps that scalar. No scalar sorts
    // with NULL, consistently with the other hosts' total-order comparators.
    let a = a.scalar_source(Pos::default()).ok();
    let b = b.scalar_source(Pos::default()).ok();
    let ra = a.as_ref().map_or(0, value_rank);
    let rb = b.as_ref().map_or(0, value_rank);
    if ra != rb {
        return ra.cmp(&rb);
    }
    let (Some(a), Some(b)) = (a, b) else { return Ordering::Equal; };
    match ra {
        1 => {
            let ab = a.0.borrow().bool_val;
            let bb = b.0.borrow().bool_val;
            ab.cmp(&bb)
        }
        2 => {
            let da = a.as_decimal(Pos::default()).unwrap();
            let db = b.as_decimal(Pos::default()).unwrap();
            dec_cmp(&da, &db)
        }
        3 | 4 => {
            let ab = a.as_bytes(Pos::default()).unwrap();
            let bb = b.as_bytes(Pos::default()).unwrap();
            ab.cmp(&bb)
        }
        _ => Ordering::Equal,
    }
}

struct SortItem {
    item: Value,
    key: Value,
    idx: usize,
}

// Resolve after all key expressions have run, preserving mutations observed
// through pending references. Validate even a single key, which sort_by would
// otherwise never compare, and do not turn a depth failure into a NULL key.
fn prepare_sort_keys(items: &mut [SortItem], pos: Pos) -> Result<(), SelError> {
    for item in items {
        item.key = match item.key.scalar_source(pos) {
            Ok(value) => value,
            Err(error) if matches!(error.code, "E_NULL" | "E_NO_SCALAR") => Value::none(),
            Err(error) => return Err(error),
        };
    }
    Ok(())
}

fn do_sort(args: &mut Args, forced_dir: Option<&str>) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.count();
    let mut direction = forced_dir.unwrap_or("ASC").to_string();

    let mut binder = "_".to_string();
    let mut body_opt: Option<Node> = None;

    if count == 1 {
        // no binder, no body
    } else if count == 2 {
        body_opt = Some(args.node_at(1).clone());
    } else if count == 3 {
        if forced_dir.is_some() {
            binder = args.symbol(1)?;
            body_opt = Some(args.node_at(2).clone());
        } else if args.node_at(2).t == NodeType::Text {
            body_opt = Some(args.node_at(1).clone());
            direction = args.text(2)?.to_ascii_uppercase();
        } else if args.is_symbol_at(1) {
            binder = args.symbol(1)?;
            body_opt = Some(args.node_at(2).clone());
            direction = "ASC".to_string();
        } else {
            body_opt = Some(args.node_at(1).clone());
            direction = args.text(2)?.to_ascii_uppercase();
        }
    } else {
        binder = args.symbol(1)?;
        body_opt = Some(args.node_at(2).clone());
        direction = args.text(3)?.to_ascii_uppercase();
    }

    if count > 1 && direction != "ASC" && direction != "DESC" {
        let pos_idx = if count == 4 { 3 } else { 2 };
        return Err(SelError::new(
            "E_BAD_ARG",
            "sort direction must be 'ASC' or 'DESC'",
            args.pos_at(pos_idx),
        ));
    }

    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let mut indexed = Vec::with_capacity(ents.len());
    if count == 1 {
        for (ei, ev) in ents.vals.iter().enumerate() {
            indexed.push(SortItem {
                item: ev.clone().deep_copy(2, args.pos())?,
                key: ev.clone(),
                idx: ei,
            });
        }
    } else {
        let body = body_opt.unwrap();
        let needs_k = node_contains_var(&body, "_K");
        let mut frame = HashMap::new();
        frame.insert(binder.clone(), Value::none());
        if needs_k {
            frame.insert("_K".to_string(), Value::none());
        }

        args.ctx.push_frame(frame);
        for (ei, ev) in ents.vals.iter().enumerate() {
            args.ctx.bind(&binder, ev.clone());
            if needs_k {
                args.ctx.bind("_K", Value::text_owned(ents.key(ei)));
            }
            let k_val = args.eval_node(&body)?;
            indexed.push(SortItem {
                item: ev.clone().deep_copy(2, args.pos())?,
                key: k_val,
                idx: ei,
            });
        }
        args.ctx.pop_frame();
    }

    prepare_sort_keys(&mut indexed, args.pos())?;
    let desc = direction == "DESC";
    indexed.sort_by(|a, b| {
        let mut c = compare_values(&a.key, &b.key);
        if desc {
            c = c.reverse();
        }
        if c != Ordering::Equal {
            c
        } else {
            a.idx.cmp(&b.idx)
        }
    });

    let out = indexed.into_iter().map(|x| x.item).collect();
    Ok(Value::list_owned(out))
}

pub fn fn_sort(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, None)
}

pub fn fn_sort_desc(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, Some("DESC"))
}

pub fn fn_sort_by(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, None)
}

fn do_top(args: &mut Args, forced_dir: Option<&str>) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let limit = args.non_neg_int(args.count() - 1)? as usize;

    let sort_count = args.count() - 1;
    let mut binder = "_".to_string();
    let mut body_opt: Option<Node> = None;
    let mut direction = forced_dir.unwrap_or("ASC").to_string();

    if sort_count == 1 {
        binder = String::new();
    } else if sort_count == 2 {
        body_opt = Some(args.node_at(1).clone());
    } else if sort_count == 3 {
        if forced_dir.is_some() {
            binder = args.symbol(1)?;
            body_opt = Some(args.node_at(2).clone());
        } else if args.node_at(2).t == NodeType::Text {
            body_opt = Some(args.node_at(1).clone());
            direction = args.text(2)?.to_ascii_uppercase();
        } else if args.is_symbol_at(1) {
            binder = args.symbol(1)?;
            body_opt = Some(args.node_at(2).clone());
        } else {
            body_opt = Some(args.node_at(1).clone());
            direction = args.text(2)?.to_ascii_uppercase();
        }
    } else if sort_count == 4 {
        binder = args.symbol(1)?;
        body_opt = Some(args.node_at(2).clone());
        direction = args.text(3)?.to_ascii_uppercase();
    }

    if direction != "ASC" && direction != "DESC" {
        let dir_idx = if sort_count == 4 { 3 } else { 2 };
        return Err(SelError::new(
            "E_BAD_ARG",
            "sort direction must be 'ASC' or 'DESC'",
            args.pos_at(dir_idx),
        ));
    }

    if limit == 0 || val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let needs_k = body_opt.as_ref().map_or(false, |b| node_contains_var(b, "_K"));
    let mut frame = HashMap::new();
    if !binder.is_empty() {
        frame.insert(binder.clone(), Value::none());
    }
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }

    let mut indexed = Vec::with_capacity(ents.len());
    let framed = !binder.is_empty();
    if framed {
        args.ctx.push_frame(frame);
    }
    for (ei, ev) in ents.vals.iter().enumerate() {
        let k_val = if !framed {
            ev.clone()
        } else {
            args.ctx.bind(&binder, ev.clone());
            if needs_k {
                args.ctx.bind("_K", Value::text_owned(ents.key(ei)));
            }
            args.eval_node(body_opt.as_ref().unwrap())?
        };
        indexed.push(SortItem {
            item: ev.clone().deep_copy(2, args.pos())?,
            key: k_val,
            idx: ei,
        });
    }

    if framed {
        args.ctx.pop_frame();
    }
    prepare_sort_keys(&mut indexed, args.pos())?;
    let desc = direction == "DESC";
    indexed.sort_by(|a, b| {
        let mut c = compare_values(&a.key, &b.key);
        if desc {
            c = c.reverse();
        }
        if c != Ordering::Equal {
            c
        } else {
            a.idx.cmp(&b.idx)
        }
    });

    let take_n = limit.min(indexed.len());
    let out = indexed[..take_n].iter().map(|x| x.item.clone()).collect();
    Ok(Value::list_owned(out))
}

pub fn fn_top(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, None)
}

pub fn fn_top_desc(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, Some("DESC"))
}

pub fn fn_top_by(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, None)
}

struct BucketGroup {
    key: Value,
    key_str: String,
    rows: Vec<Value>,
}

pub fn fn_bucket(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }
    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let count = args.count();
    let mut binder = "_".to_string();
    let key_node: Node;
    let agg_node_opt: Option<Node>;

    if count == 2 {
        key_node = args.node_at(1).clone();
        agg_node_opt = None;
    } else if count == 3 {
        key_node = args.node_at(1).clone();
        agg_node_opt = Some(args.node_at(2).clone());
    } else {
        binder = args.symbol(1)?;
        key_node = args.node_at(2).clone();
        agg_node_opt = Some(args.node_at(3).clone());
    }

    let needs_k = node_contains_var(&key_node, "_K");
    let mut frame = HashMap::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }

    let mut table: HashMap<u64, Vec<usize>> = HashMap::new();
    let mut bare_groups: HashMap<String, usize> = HashMap::new();
    let mut groups: Vec<BucketGroup> = Vec::new();

    args.ctx.push_frame(frame);
    for (ei, ev) in ents.vals.iter().enumerate() {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, ev.clone());
            if needs_k {
                f.set("_K", Value::text_owned(ents.key(ei)));
            }
        }
        let group_key = args.eval_node(&key_node)?;
        let key_str = if agg_node_opt.is_none() {
            if group_key.kind() == Kind::None {
                if group_key.is_null() {
                    args.ctx.pop_frame();
                    return Err(SelError::null("value is NULL", key_node.pos));
                }
                args.ctx.pop_frame();
                return Err(SelError::not_text(
                    "a bucket key must be text or a number, got a list or record",
                    key_node.pos,
                ));
            }
            group_key.as_text(key_node.pos)?
        } else {
            String::new()
        };

        if agg_node_opt.is_none() {
            if let Some(&idx) = bare_groups.get(&key_str) {
                groups[idx].rows.push(ev.clone());
            } else {
                let idx = groups.len();
                bare_groups.insert(key_str.clone(), idx);
                groups.push(BucketGroup {
                    key: group_key,
                    key_str,
                    rows: vec![ev.clone()],
                });
            }
        } else {
            let h = group_key.structural_hash()?;
            let bucket = table.entry(h).or_default();
            let mut found = false;
            for &idx in bucket.iter() {
                if groups[idx].key.eql(&group_key, 1, Pos::default())? {
                    groups[idx].rows.push(ev.clone());
                    found = true;
                    break;
                }
            }
            if !found {
                let idx = groups.len();
                groups.push(BucketGroup {
                    key: group_key,
                    key_str,
                    rows: vec![ev.clone()],
                });
                bucket.push(idx);
            }
        }
    }
    args.ctx.pop_frame();

    if agg_node_opt.is_none() {
        let out = Value::none();
        for g in groups {
            let mut rows_copy = Vec::with_capacity(g.rows.len());
            for r in &g.rows {
                rows_copy.push(r.deep_copy(3, args.pos())?);
            }
            out.set(&g.key_str, Value::list_owned(rows_copy), Pos::default())?;
        }
        return Ok(out);
    }

    let agg_node = agg_node_opt.unwrap();
    let mut out = Vec::with_capacity(groups.len());
    let mut agg_frame = HashMap::new();
    agg_frame.insert(binder.clone(), Value::none());
    agg_frame.insert("_K".to_string(), Value::none());
    args.ctx.push_frame(agg_frame);

    for g in groups {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, Value::list_owned(g.rows));
            f.set("_K", g.key);
        }
        out.push(args.eval_node(&agg_node)?.deep_copy(2, args.pos())?);
    }
    args.ctx.pop_frame();

    Ok(Value::list_owned(out))
}

fn single_relation_name(node: &Node) -> String {
    if node.t == NodeType::Var {
        return node.s.clone();
    }
    if node.t == NodeType::Call && !node.items.is_empty() && node.s != "LINK" && node.s != "LINK_LEFT" {
        return single_relation_name(&node.items[0]);
    }
    String::new()
}

fn ensure_row_table_alias(row: Value, table_name: &str) -> Value {
    if table_name.is_empty() || table_name == "_1" || table_name == "_2" || row.has(table_name) {
        return row;
    }
    let lower = table_name.to_ascii_lowercase();
    let mut entries = row.entries();
    entries.push(Entry {
        key: table_name.to_string(),
        val: row.clone(),
    });
    if lower != table_name && !row.has(&lower) {
        entries.push(Entry {
            key: lower,
            val: row,
        });
    }
    Value::record_from_entries(entries)
}

// All rows of a table commonly share a shape. Cache only the layout, never
// values: a predicate may mutate a row between calls in a nested-loop join.
struct RowAlias {
    name: String,
    lower: String,
    layout: Option<(u64, Arc<RecordShape>, usize)>,
}

impl RowAlias {
    fn new(name: &str) -> Self {
        Self { name: name.to_owned(), lower: name.to_ascii_lowercase(), layout: None }
    }

    fn apply(&mut self, row: Value) -> Value {
        if self.name.is_empty() || self.name == "_1" || self.name == "_2" { return row; }
        let inner = row.0.borrow();
        if let (Some(shape), Some(storage)) = (&inner.shape, &inner.storage) {
            if shape.key_map.contains_key(&self.name) { drop(inner); return row; }
            if self.layout.as_ref().map(|p| p.0) != Some(shape.id) {
                let mut keys = shape.keys.to_vec();
                keys.push(self.name.clone());
                let mut aliases = 1;
                if self.lower != self.name && !shape.key_map.contains_key(&self.lower) {
                    keys.push(self.lower.clone());
                    aliases += 1;
                }
                self.layout = Some((shape.id, unique_record_shape(&keys).unwrap(), aliases));
            }
            let (_, target, aliases) = self.layout.as_ref().unwrap();
            let mut values = Vec::with_capacity(storage.len() + aliases);
            values.extend(storage.iter().cloned());
            for _ in 0..*aliases { values.push(row.clone()); }
            return Value::shaped_record(target.clone(), values);
        }
        drop(inner);
        ensure_row_table_alias(row, &self.name)
    }
}

fn make_null_record(sample: Option<&Value>, table_name: &str) -> Value {
    if let Some(s) = sample {
        if !s.is_null() {
            let mut entries = Vec::new();
            for k in s.keys() {
                entries.push(Entry {
                    key: k,
                    val: Value::null(),
                });
            }
            return Value::record_from_entries(entries);
        }
    }
    let mut entries = Vec::new();
    if !table_name.is_empty() && table_name != "_1" && table_name != "_2" {
        entries.push(Entry {
            key: table_name.to_string(),
            val: Value::null(),
        });
        let lower = table_name.to_ascii_lowercase();
        if lower != table_name {
            entries.push(Entry {
                key: lower,
                val: Value::null(),
            });
        }
    }
    Value::record_from_entries(entries)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum JoinKeyType {
    Null,
    Bad,
    Int64,
    Dec,
    Str,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct JoinKey {
    k_type: usize, // 0: null, 1: bad, 2: int64, 3: str
    int_val: i64,
    str_val: SelStr,
}

fn canonical_join_key(v: &Value, numeric: bool) -> (JoinKey, Option<Value>) {
    if v.is_null() {
        return (JoinKey { k_type: 0, int_val: 0, str_val: SelStr::EMPTY }, None);
    }
    if numeric {
        let d = match v.as_decimal(Pos::default()) {
            Ok(d) => d,
            Err(_) => {
                return (JoinKey { k_type: 1, int_val: 0, str_val: SelStr::EMPTY }, Some(v.clone()));
            }
        };
        if d.is_integer() {
            if let Some(n) = d.to_i64() {
                return (JoinKey { k_type: 2, int_val: n, str_val: SelStr::EMPTY }, None);
            }
        }
        let trimmed = crate::dec::dec_trim_scale(&d);
        if trimmed.is_integer() {
            if let Some(n) = trimmed.to_i64() {
                return (JoinKey { k_type: 2, int_val: n, str_val: SelStr::EMPTY }, None);
            }
        }
        return (JoinKey { k_type: 3, int_val: 0, str_val: SelStr::from(dec_format(&trimmed)) }, None);
    }

    // Text keys by their text, shared rather than copied.
    if let Ok(s) = v.as_text_str(Pos::default()) {
        return (JoinKey { k_type: 3, int_val: 0, str_val: s }, None);
    }
    match v.as_bytes(Pos::default()) {
        Ok(b) => {
            let s = SelStr::from(String::from_utf8_lossy(&b).as_ref());
            (JoinKey { k_type: 3, int_val: 0, str_val: s }, None)
        }
        Err(_) => (JoinKey { k_type: 1, int_val: 0, str_val: SelStr::EMPTY }, Some(v.clone())),
    }
}

struct JoinEqui<'a> {
    left_expr: &'a Node,
    right_expr: &'a Node,
    numeric: bool,
    swapped: bool,
}

fn expr_depends_only_on(node: &Node, allowed: &HashMap<String, bool>) -> bool {
    match node.t {
        NodeType::Var => allowed.contains_key(&node.s.to_ascii_uppercase()),
        NodeType::Index => {
            node.l.as_ref().map_or(true, |l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().map_or(true, |r| expr_depends_only_on(r, allowed))
        }
        NodeType::Call => node.items.iter().all(|a| expr_depends_only_on(a, allowed)),
        NodeType::Bin => {
            node.l.as_ref().map_or(true, |l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().map_or(true, |r| expr_depends_only_on(r, allowed))
        }
        NodeType::Un => node.l.as_ref().map_or(true, |l| expr_depends_only_on(l, allowed)),
        NodeType::Assign => {
            node.l.as_ref().map_or(true, |l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().map_or(true, |r| expr_depends_only_on(r, allowed))
        }
        NodeType::Seq | NodeType::List => node.items.iter().all(|item| expr_depends_only_on(item, allowed)),
        _ => true,
    }
}

fn extract_join_equi<'a>(node: &'a Node, b1: &str, b2: &str) -> Option<JoinEqui<'a>> {
    if node.t != NodeType::Bin || (node.s != "==" && node.s != "$==") {
        return None;
    }
    if b1.eq_ignore_ascii_case(b2) {
        return None;
    }
    let mut left_names = HashMap::new();
    left_names.insert(b1.to_ascii_uppercase(), true);
    left_names.insert("_1".to_string(), true);
    left_names.insert("_".to_string(), true);

    let mut right_names = HashMap::new();
    right_names.insert(b2.to_ascii_uppercase(), true);
    right_names.insert("_2".to_string(), true);

    let l = node.l.as_ref()?;
    let r = node.r.as_ref()?;

    if expr_depends_only_on(l, &left_names) && expr_depends_only_on(r, &right_names) {
        return Some(JoinEqui {
            left_expr: l,
            right_expr: r,
            numeric: node.s == "==",
            swapped: false,
        });
    }
    if expr_depends_only_on(r, &left_names) && expr_depends_only_on(l, &right_names) {
        return Some(JoinEqui {
            left_expr: r,
            right_expr: l,
            numeric: node.s == "==",
            swapped: true,
        });
    }
    None
}

struct JoinFacts {
    live: bool,
    bad: Option<Value>,
    live_bad: Option<Value>,
}

fn check_join_pair(
    equi: &JoinEqui,
    key: &JoinKey,
    l_bad: Option<&Value>,
    facts: &JoinFacts,
) -> Result<(), SelError> {
    if key.k_type == 0 || !facts.live {
        return Ok(());
    }
    if key.k_type == 1 {
        if equi.swapped && facts.live_bad.is_some() {
            coerce_join_operand(equi.numeric, facts.live_bad.as_ref().unwrap(), equi.right_expr.pos)?;
        }
        if let Some(lb) = l_bad {
            coerce_join_operand(equi.numeric, lb, equi.left_expr.pos)?;
        }
    }
    if let Some(ref fb) = facts.bad {
        coerce_join_operand(equi.numeric, fb, equi.right_expr.pos)?;
    }
    Ok(())
}

fn coerce_join_operand(numeric: bool, v: &Value, pos: Pos) -> Result<(), SelError> {
    if numeric {
        v.as_decimal(pos)?;
    } else {
        v.as_bytes(pos)?;
    }
    Ok(())
}

fn is_join_or_filter(n: &Node) -> bool {
    n.t == NodeType::Call && matches!(n.s.as_str(), "LINK" | "LINK_LEFT" | "FILTER")
}

/// Binds `row` under every name of the innermost frame a join's left pass
/// uses (its binders, their aliases, and the handed-down FILTER binders).
fn join_set_row(ctx: &mut Context, names: &[String], row: &Value) {
    if let Some(f) = ctx.frames.last_mut() {
        for name in names {
            f.set(name, row.clone());
        }
    }
}

/// A pre-applied conjunct list on the row bound in the innermost frame:
/// 0 keep, 1 drop, 2 keep because a conjunct raised (the FILTER decides).
fn join_verdict(args: &mut Args, conjuncts: &[Node], errored: &mut bool) -> u8 {
    for conjunct in conjuncts {
        match args.eval_node(conjunct).and_then(|v| v.as_bool(conjunct.pos)) {
            Err(_) => {
                *errored = true;
                return 2;
            }
            Ok(false) => return 1,
            Ok(true) => {}
        }
    }
    0
}

fn fields_all(fields: &HashMap<String, bool>, mut pred: impl FnMut(&str) -> bool) -> bool {
    fields.keys().all(|f| pred(f))
}

// A join's state between the phases of `do_link`. The phases that evaluate
// a source -- recursion, on a chain of joins -- run in `do_link`'s own thin
// frame; the rest are out of line, so each join on a chain costs the stack
// only that frame (the evaluator must reach E_DEPTH on a small stack).
struct LinkState<'a> {
    left_join: bool,
    left_node: &'a Node,
    b1: String,
    b2: String,
    b1_names: Vec<String>,
    b2_names: Vec<String>,
    predicate_node: &'a Node,
    has_prefilter: bool,
    stages: Vec<JoinStage>,
    deep: bool,
    above: Vec<Rc<JoinSideFacts>>,
    obligations: Vec<JoinObligation>,
    // The upper-cased keys of the joins between a stage's FILTER and this
    // join, per count of them.
    above_keys: Vec<HashSet<String>>,
    equi_opt: Option<JoinEqui<'a>>,
    right_side: Option<Rc<JoinSideFacts>>,
    // Conjuncts to pre-apply and a left source that is itself a join: the
    // right source is evaluated first (both sources pure), so that the
    // conjuncts still askable of the rows below can travel down.
    deep_path: bool,
}

impl LinkState<'_> {
    fn above_keys_of(&self, stage: &JoinStage) -> &HashSet<String> {
        &self.above_keys[stage.above.min(self.above.len())]
    }

    fn above_of(&self, stage: &JoinStage) -> &[Rc<JoinSideFacts>] {
        &self.above[..stage.above.min(self.above.len())]
    }
}

fn do_link(args: &mut Args, left_join: bool) -> Result<Value, SelError> {
    let mut st = link_head(args, left_join)?;
    if st.deep_path {
        // Reading the right source first is unobservable only when it
        // succeeds: when it raises, the left source -- as written, with
        // nothing handed down -- goes first, and its error wins.
        let right_first = match args.val(1) {
            Ok(v) => v,
            Err(e) => {
                args.val(0)?;
                return Err(e);
            }
        };
        link_hand_down(args, &mut st, right_first);
        let left = args.val(0);
        args.ctx.join_prefilter = None;
        left?;
    }
    let left_val = args.val(0)?;
    let right_val = args.val(1)?;
    link_body(args, st, left_val, right_val)
}

#[inline(never)]
fn link_head<'a>(args: &mut Args<'a>, left_join: bool) -> Result<Box<LinkState<'a>>, SelError> {
    // Taken before anything else is evaluated, so a LINK nested in this one's
    // sources cannot pick it up by accident; it is handed down on purpose.
    let prefilter = args.ctx.join_prefilter.take();
    let count = args.count();
    let nodes: &'a [Node] = args.nodes;
    let left_node = &nodes[0];
    let right_node = &nodes[1];

    let mut b1 = single_relation_name(left_node);
    if b1.is_empty() {
        b1 = "_1".to_string();
    }
    let mut b2 = single_relation_name(right_node);
    if b2.is_empty() {
        b2 = "_2".to_string();
    }
    let predicate_node: &'a Node;
    if count == 5 {
        b1 = args.symbol(2)?;
        b2 = args.symbol(3)?;
        predicate_node = &nodes[4];
    } else {
        predicate_node = &nodes[2];
    }
    let b1_names = vec![b1.clone(), "_1".to_string()];
    let b2_names = vec![b2.clone(), "_2".to_string()];

    let has_prefilter = prefilter.is_some();
    let (stages, deep, above, obligations) = match prefilter {
        Some(p) => (p.stages, p.deep, p.above, p.obligations),
        None => (Vec::new(), false, Vec::new(), Vec::new()),
    };
    let mut above_keys: Vec<HashSet<String>> = vec![HashSet::new()];
    for side in &above {
        let mut next = above_keys.last().unwrap().clone();
        next.extend(side.keys.keys().cloned());
        above_keys.push(next);
    }
    let equi_opt = extract_join_equi(predicate_node, &b1, &b2);
    let deep_path = deep
        && !stages.is_empty()
        && equi_opt.is_some()
        && is_join_or_filter(left_node)
        && join_pure_source(left_node)
        && join_pure_source(right_node);
    Ok(Box::new(LinkState {
        left_join,
        left_node,
        b1,
        b2,
        b1_names,
        b2_names,
        predicate_node,
        has_prefilter,
        stages,
        deep,
        above,
        obligations,
        above_keys,
        equi_opt,
        right_side: None,
        deep_path,
    }))
}

/// The deep path, once the right rows are known: the conjuncts still askable
/// of the rows below go down to the left source, as `ctx.join_prefilter`.
#[inline(never)]
fn link_hand_down(args: &mut Args, st: &mut LinkState, right_first: Value) {
    let rs = Rc::new(new_join_side_facts(
        right_first.clone(),
        join_row_keys(&right_first, &st.b2_names),
        st.left_join,
        &st.b2_names,
    ));
    let stop = {
        let st: &LinkState = st;
        let owned_by_left = |fields: &HashMap<String, bool>, stage: &JoinStage| {
            let upper = st.above_keys_of(stage);
            fields_all(fields, |f| !rs.keys.contains_key(f) && !upper.contains(f))
        };
        let total_below = |reqs: &[JoinTotalReq], stage: &JoinStage| {
            join_totality(reqs, None, &rs, st.above_of(stage))
        };
        let nothing_right = |_: &HashMap<String, bool>, _: &JoinStage| false;
        join_stage_walk(&st.stages, &owned_by_left, &total_below, &nothing_right).1
    };
    let mut handed = join_truncate_stages(&st.stages, stop.as_ref());
    for stage in &mut handed {
        stage.above += 1;
    }
    if !handed.is_empty() {
        let mut sides = Vec::with_capacity(st.above.len() + 1);
        sides.push(rs.clone());
        sides.extend(st.above.iter().cloned());
        let mut row_names = HashMap::new();
        row_names.insert(st.b1.clone(), true);
        row_names.insert(st.b1.to_ascii_lowercase(), true);
        row_names.insert("_1".to_string(), true);
        row_names.insert("_".to_string(), true);
        // This join computes its left key on every row it receives; a row
        // dropped below never arrives, so the key goes down as an
        // obligation for the join that drops to prove.
        let own_key = JoinObligation {
            key: st.equi_opt.as_ref().unwrap().left_expr.clone(),
            row_names,
            outer: st.above.len() + 1,
        };
        let mut obs = Vec::with_capacity(st.obligations.len() + 1);
        obs.push(own_key);
        obs.extend(st.obligations.iter().cloned());
        args.ctx.join_prefilter = Some(JoinPrefilter {
            stages: handed,
            deep: true,
            above: sides,
            obligations: obs,
        });
    }
    st.right_side = Some(rs);
}

#[inline(never)]
fn link_body<'a>(
    args: &mut Args<'a>,
    st: Box<LinkState<'a>>,
    mut left_val: Value,
    right_val: Value,
) -> Result<Value, SelError> {
    let LinkState {
        left_join,
        left_node,
        b1,
        b2,
        b1_names,
        b2_names,
        predicate_node,
        has_prefilter,
        stages,
        deep,
        above,
        obligations,
        above_keys,
        equi_opt,
        mut right_side,
        deep_path: _,
    } = *st;
    let above_keys_of = |stage: &JoinStage| &above_keys[stage.above.min(above.len())];
    let above_of = |stage: &JoinStage| &above[..stage.above.min(above.len())];
    let mut below = args.ctx.join_prefilter_report.take();

    // Drops below that left this join no left rows: as written it may have
    // had some, and then it computes every right key before it finds that
    // no row survives. The left side is evaluated again, as written.
    if below.as_ref().is_some_and(|b| b.dropped) && (left_val.is_null() || left_val.elems().len() == 0) {
        left_val = args.eval_node(left_node)?;
        args.ctx.join_prefilter_report = None;
        below = None;
    }

    if left_val.is_null() {
        return Ok(Value::list_owned(Vec::new()));
    }

    let left_ents = left_val.elems().vals;
    let right_ents = right_val.elems().vals;

    if left_ents.is_empty() || (right_ents.is_empty() && !left_join) {
        return Ok(Value::list_owned(Vec::new()));
    }

    let mut left_alias = RowAlias::new(&b1);
    let mut right_alias = RowAlias::new(&b2);
    let sample_right = if !right_ents.is_empty() {
        Some(right_alias.apply(right_ents[0].clone()))
    } else {
        None
    };
    let null_right = if left_join {
        Some(make_null_record(sample_right.as_ref(), &b2))
    } else {
        None
    };

    let mut projector = JoinProjector::new(b1.clone(), b2.clone(), null_right.clone());

    if let Some(ref equi) = equi_opt {
        if !right_ents.is_empty() {
            let mut r_frame = HashMap::new();
            r_frame.insert(b2.clone(), Value::none());
            r_frame.insert("_2".to_string(), Value::none());
            let lower_b2 = b2.to_ascii_lowercase();
            if lower_b2 != b2 {
                r_frame.insert(lower_b2.clone(), Value::none());
            }
            args.ctx.push_frame(r_frame);

            // Each right row carries whether a pre-applied right conjunct
            // rejected it; it stays in its bucket for the numbering.
            let mut buckets: HashMap<JoinKey, Vec<(Value, bool)>> = HashMap::new();
            let mut facts = JoinFacts {
                live: false,
                bad: None,
                live_bad: None,
            };
            let mut flat_test = JoinFlatTest::new(&b1, &b2);
            let mut right_flat = true;

            for r_entry in right_ents {
                let right = right_alias.apply(r_entry);
                if let Some(f) = args.ctx.frames.last_mut() {
                    f.set(&b2, right.clone());
                    f.set("_2", right.clone());
                    if lower_b2 != b2 {
                        f.set(&lower_b2, right.clone());
                    }
                }
                let key_val = args.eval_node(equi.right_expr)?;
                let (k, bad_val) = canonical_join_key(&key_val, equi.numeric);
                if k.k_type != 0 {
                    if k.k_type == 1 {
                        if !facts.live {
                            facts.live_bad = bad_val.clone();
                        }
                        if facts.bad.is_none() {
                            facts.bad = bad_val;
                        }
                    } else {
                        buckets.entry(k).or_default().push((right.clone(), false));
                    }
                    facts.live = true;
                }
                if right_flat && !flat_test.is_flat(&right) {
                    right_flat = false;
                }
            }
            projector.right_flat = right_flat;
            args.ctx.pop_frame();

            let mut prefix: Vec<Node> = Vec::new();
            let mut right_prefix: Vec<Node> = Vec::new();
            let mut left_before_right: Option<usize> = None;
            let mut binders: Vec<String> = Vec::new();
            let mut report = JoinReport {
                applied: HashMap::new(),
                errored: false,
                dropped: below.as_ref().is_some_and(|b| b.dropped),
            };

            let upper_b1 = b1.to_ascii_uppercase();
            let upper_b2 = b2.to_ascii_uppercase();
            // A read through this join's right binder is the right element
            // in every joined row, unless the left binder has the same name,
            // or a join above rebinds it.
            let right_names = |stage_above: usize| {
                let mut names = HashMap::new();
                names.insert(upper_b2.clone(), true);
                if stage_above == 0 {
                    names.insert("_2".to_string(), true);
                }
                names
            };
            let right_ok = !left_join && upper_b1 != upper_b2;

            if has_prefilter {
                let rs = match right_side.take() {
                    Some(rs) => rs,
                    None => Rc::new(new_join_side_facts(
                        right_val.clone(),
                        join_row_keys(&right_val, &b2_names),
                        left_join,
                        &b2_names,
                    )),
                };
                binders = stages.iter().map(|s| s.binder.clone()).collect();
                let left_side = new_join_side_facts(
                    left_val.clone(),
                    join_row_keys(&left_val, &b1_names),
                    false,
                    &b1_names,
                );
                let safe = obligations.is_empty() || join_keys_safe(&obligations, &left_side, &rs, &above);
                let mut self_names = HashMap::new();
                self_names.insert(upper_b1.clone(), true);
                self_names.insert("_1".to_string(), true);
                if safe {
                    let owned_here = |fields: &HashMap<String, bool>, stage: &JoinStage| {
                        let upper = above_keys_of(stage);
                        fields_all(fields, |f| !rs.keys.contains_key(f) && !upper.contains(f))
                    };
                    let total_here = |reqs: &[JoinTotalReq], stage: &JoinStage| {
                        join_totality(reqs, Some(&left_side), &rs, above_of(stage))
                    };
                    let right_here = |fields: &HashMap<String, bool>, stage: &JoinStage| {
                        if !right_ok {
                            return false;
                        }
                        let names = right_names(stage.above);
                        let upper = above_keys_of(stage);
                        fields_all(fields, |f| names.contains_key(f) && !upper.contains(f))
                    };
                    let (walk_applied, _) = join_stage_walk(&stages, &owned_here, &total_here, &right_here);
                    for applied in walk_applied {
                        let c = applied.conjunct;
                        report.applied.insert(c.id, true);
                        if let Some(ref b) = below {
                            if !b.errored && b.applied.contains_key(&c.id) {
                                continue;
                            }
                        }
                        if applied.right {
                            if left_before_right.is_none() {
                                left_before_right = Some(prefix.len());
                            }
                            right_prefix.push(join_read_self(&c.node, &right_names(applied.stage.above), &c.binder));
                            continue;
                        }
                        let reads_self = c
                            .fields
                            .keys()
                            .any(|f| self_names.contains_key(f) && !left_side.first.contains_key(f));
                        if reads_self {
                            prefix.push(join_read_self(&c.node, &self_names, &c.binder));
                        } else {
                            prefix.push(c.node.clone());
                        }
                    }
                }
            }

            // The right rows the right conjuncts reject, once each, after
            // every right key was computed.
            let rejecting = !right_prefix.is_empty();
            if rejecting {
                let mut frame = Frame::new();
                for b in &binders {
                    frame.set(b, Value::none());
                }
                args.ctx.push_frame(frame);
                let before = report.errored;
                report.errored = false;
                for rows in buckets.values_mut() {
                    for (row, rejected) in rows.iter_mut() {
                        join_set_row(args.ctx, &binders, row);
                        if join_verdict(args, &right_prefix, &mut report.errored) == 1 {
                            *rejected = true;
                        }
                    }
                }
                args.ctx.pop_frame();
                if report.errored {
                    if let Some(n) = left_before_right {
                        prefix.truncate(n);
                    }
                }
                report.errored = report.errored || before;
            }

            // A FILTER keeps its input's keys, so the rows dropped here still
            // count towards the numbering of the rows kept.
            let numbered = (!prefix.is_empty() || rejecting) && !deep;
            let mut positions: Vec<u32> = Vec::new();
            let mut dropped = false;
            let mut position: usize = 1;

            // When the join key is a literal field of the row, a row that
            // HAS it may be rejected first and its key never computed. Only
            // where nothing observes the numbering.
            let mut fast_field: Option<String> = None;
            if !prefix.is_empty() && deep {
                let el = equi.left_expr;
                if el.t == NodeType::Index {
                    if let (Some(l), Some(r)) = (el.l.as_deref(), el.r.as_deref()) {
                        if l.t == NodeType::Var && r.t == NodeType::Text {
                            let owner = l.s.to_ascii_uppercase();
                            if owner == upper_b1 || owner == "_1" || owner == "_" {
                                fast_field = Some(r.s.clone());
                            }
                        }
                    }
                }
            }

            let mut output = Vec::new();
            let lower_b1 = b1.to_ascii_lowercase();
            let mut row_slots = vec![b1.clone(), "_1".to_string(), "_".to_string()];
            if lower_b1 != b1 {
                row_slots.push(lower_b1.clone());
            }
            for b in &binders {
                if !row_slots.contains(b) {
                    row_slots.push(b.clone());
                }
            }
            let mut l_frame = Frame::new();
            for name in &row_slots {
                l_frame.set(name, Value::none());
            }
            args.ctx.push_frame(l_frame);

            for l_entry in left_ents {
                let left = left_alias.apply(l_entry);
                let mut asked: i8 = -1;
                if let Some(ref ff) = fast_field {
                    if left.has(ff) {
                        join_set_row(args.ctx, &row_slots, &left);
                        asked = join_verdict(args, &prefix, &mut report.errored) as i8;
                        if asked == 1 {
                            // Dropped before its key was computed -- but the
                            // key is this very field, and a rejected one
                            // still raises in the join as written.
                            let field_val = left.get(ff).unwrap_or_else(Value::null);
                            let (k, bad) = canonical_join_key(&field_val, equi.numeric);
                            check_join_pair(equi, &k, bad.as_ref(), &facts)?;
                            dropped = true;
                            continue;
                        }
                    }
                }

                join_set_row(args.ctx, &row_slots, &left);
                let l_key_val = args.eval_node(equi.left_expr)?;
                let (lk, l_bad) = canonical_join_key(&l_key_val, equi.numeric);
                check_join_pair(equi, &lk, l_bad.as_ref(), &facts)?;
                let r_rows = buckets.get(&lk);

                if asked < 0 {
                    asked = if prefix.is_empty() {
                        0
                    } else {
                        join_verdict(args, &prefix, &mut report.errored) as i8
                    };
                }

                if asked == 1 {
                    dropped = true;
                    if numbered {
                        if let Some(rows) = r_rows {
                            position += rows.len();
                        } else if left_join {
                            position += 1;
                        }
                    }
                    continue;
                }

                if let Some(rows) = r_rows {
                    // A left row kept on an error meets every right row: its
                    // joined rows raise in the FILTER, in order.
                    let skip = rejecting && asked == 0;
                    for (right, rejected) in rows {
                        if skip && *rejected {
                            dropped = true;
                            position += 1;
                            continue;
                        }
                        cap_collection(if numbered { position } else { output.len() + 1 } as u128, args.pos())?;
                        output.push(projector.project(&left, Some(right)));
                        if numbered {
                            positions.push(position as u32);
                        }
                        position += 1;
                    }
                } else if left_join {
                    cap_collection(if numbered { position } else { output.len() + 1 } as u128, args.pos())?;
                    output.push(projector.project(&left, None));
                    if numbered {
                        positions.push(position as u32);
                    }
                    position += 1;
                }
            }
            args.ctx.pop_frame();

            report.dropped = report.dropped || dropped;
            if has_prefilter {
                args.ctx.join_prefilter_report = Some(report);
            }

            if numbered && dropped && !output.is_empty() {
                return Ok(Value::list_with_list_keys(output, ListKeys::Index(positions.into())));
            }
            return Ok(Value::list_owned(output));
        }
    }

    if has_prefilter {
        args.ctx.join_prefilter_report = Some(JoinReport {
            applied: HashMap::new(),
            errored: false,
            dropped: below.as_ref().is_some_and(|b| b.dropped),
        });
    }

    // Nested-loop fallback join
    let mut output = Vec::new();
    let mut frame = HashMap::new();
    let lower_b1 = b1.to_ascii_lowercase();
    let lower_b2 = b2.to_ascii_lowercase();
    frame.insert(b1.clone(), Value::none());
    if lower_b1 != b1 {
        frame.insert(lower_b1.clone(), Value::none());
    }
    frame.insert("_1".to_string(), Value::none());
    frame.insert("_".to_string(), Value::none());
    frame.insert(b2.clone(), Value::none());
    if lower_b2 != b2 {
        frame.insert(lower_b2.clone(), Value::none());
    }
    frame.insert("_2".to_string(), Value::none());
    args.ctx.push_frame(frame);

    for l_entry in left_ents {
        let left = left_alias.apply(l_entry);
        let mut matched = false;
        for r_entry in &right_ents {
            let right = right_alias.apply(r_entry.clone());
            if let Some(f) = args.ctx.frames.last_mut() {
                f.set(&b1, left.clone());
                if lower_b1 != b1 {
                    f.set(&lower_b1, left.clone());
                }
                f.set("_1", left.clone());
                f.set("_", left.clone());

                f.set(&b2, right.clone());
                if lower_b2 != b2 {
                    f.set(&lower_b2, right.clone());
                }
                f.set("_2", right.clone());
            }
            if args.eval_node(predicate_node)?.as_bool(predicate_node.pos)? {
                cap_collection(output.len() as u128 + 1, args.pos())?;
                output.push(projector.project(&left, Some(&right)));
                matched = true;
            }
        }
        if !matched && left_join {
            cap_collection(output.len() as u128 + 1, args.pos())?;
            output.push(projector.project(&left, None));
        }
    }
    args.ctx.pop_frame();

    Ok(Value::list_owned(output))
}

pub fn fn_link(args: &mut Args) -> Result<Value, SelError> {
    do_link(args, false)
}

pub fn fn_link_left(args: &mut Args) -> Result<Value, SelError> {
    do_link(args, true)
}

#[cfg(test)]
mod alias_tests {
    use super::*;

    #[test]
    fn cached_alias_layout_preserves_values_collisions_and_shape_changes() {
        let pos = Pos::default();
        let text = |s: &str| Value::text_owned(s.into());
        let row = Value::record_from_entries(vec![Entry { key: "id".into(), val: text("1") }]);
        let mut cached = RowAlias::new("ITEMS");
        let first = cached.apply(row.clone());
        assert!(std::rc::Rc::ptr_eq(&first.get("ITEMS").unwrap().0, &row.0));
        assert!(std::rc::Rc::ptr_eq(&first.get("items").unwrap().0, &row.0));
        for value in ["2", "3"] {
            row.set("id", text(value), pos).unwrap();
            let actual = cached.apply(row.clone());
            assert!(actual.eql(&ensure_row_table_alias(row.clone(), "ITEMS"), 1, pos).unwrap());
            assert_eq!(actual.get("id").unwrap().scalar(), value);
        }
        // Adding a key removes the dense shape; the fallback must see it.
        row.set("items", text("collision"), pos).unwrap();
        let actual = cached.apply(row.clone());
        assert!(actual.eql(&ensure_row_table_alias(row.clone(), "ITEMS"), 1, pos).unwrap());
        assert_eq!(actual.get("items").unwrap().scalar(), "collision");
        let shaped_collision = Value::record_from_entries(vec![Entry { key: "items".into(), val: text("kept") }]);
        let actual = cached.apply(shaped_collision.clone());
        assert!(actual.eql(&ensure_row_table_alias(shaped_collision, "ITEMS"), 1, pos).unwrap());
        row.set("ITEMS", text("existing"), pos).unwrap();
        assert!(std::rc::Rc::ptr_eq(&cached.apply(row.clone()).0, &row.0));
        // Earlier wrappers retain their direct field handles, and their alias
        // still refers to the source row, just as the uncached construction does.
        assert_eq!(first.get("id").unwrap().scalar(), "1");
        assert_eq!(first.get("ITEMS").unwrap().get("id").unwrap().scalar(), "3");
    }
}
