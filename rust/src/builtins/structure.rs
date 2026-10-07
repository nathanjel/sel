#[cfg(test)]
use std::cell::Cell;
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::rc::Rc;
use std::sync::Arc;

use crate::args::Args;
use crate::ast::{Node, NodeType};
use crate::context::{Context, Frame};
use crate::dec::{dec_add, dec_cmp, dec_format, Dec};
use crate::join_plan::{binder_keys, JoinFlatTest, JoinProjector};
use crate::manifest::{self, builtins::Form, ArgRoles};
use crate::join_prefilter::{
    join_keys_safe, join_pure_source, join_read_self, join_row_keys, join_stage_walk,
    join_totality, join_truncate_stages, leading_field_conjuncts, new_join_side_facts,
    right_null_test, JoinConjunct, JoinObligation, JoinPrefilter, JoinReport, JoinRightNull,
    JoinSideFacts, JoinStage, JoinTotalReq,
};
use crate::shape::{unique_record_shape, RecordShape};
use crate::utf8::{cap_collection, cap_text, Pos, SelError};
use crate::text::SelStr;
use crate::value::{ListKeys, Entry, Kind, Value};

// Frames: an aggregate pushes its binder frame and may leave by `?` at any
// point -- invoke_call (eval.rs) truncates the frame stack to its own depth
// after every builtin, error or not, so no early return pops by hand.

// Whether a body mentions `name` (whether to bind `_K` per element).
// Traversal policy: scope-blind, every child -- an over-approximation, which
// only costs a frame slot.
fn node_contains_var(node: &Node, name: &str) -> bool {
    if node.t == NodeType::Var {
        return node.s.eq_ignore_ascii_case(name);
    }
    node.children().any(|child| node_contains_var(child, name))
}

// The forms of the builtins below that decode their own call
// (spec/builtins.json `forms`), found at compile time.
const MAP_FORMS: &[Form] = manifest::forms("MAP");
const FILTER_FORMS: &[Form] = manifest::forms("FILTER");
const ALL_FORMS: &[Form] = manifest::forms("ALL");
const ANY_FORMS: &[Form] = manifest::forms("ANY");
const SUM_FORMS: &[Form] = manifest::forms("SUM");
const BUCKET_FORMS: &[Form] = manifest::forms("BUCKET");
const LINK_FORMS: &[Form] = manifest::forms("LINK");
const LINK_LEFT_FORMS: &[Form] = manifest::forms("LINK_LEFT");

/// The argument roles of a call, from the manifest form it takes. The arity
/// was checked at compile time, and every accepted count has a form.
fn call_roles(forms: &'static [Form], nodes: &[Node]) -> ArgRoles {
    manifest::roles_in(forms, nodes).expect("every accepted count has a manifest form")
}

/// The binder of a MAP/FILTER/ALL/ANY/SUM call and the index of its body:
/// `_` without a binder slot; a slot that is not a plain name is
/// E_EXPECT_SYMBOL.
fn binder_and_body(args: &Args, forms: &'static [Form]) -> Result<(String, usize), SelError> {
    let roles = call_roles(forms, args.nodes);
    let binder = match roles.binder {
        Some(i) => args.symbol(i)?,
        None => "_".to_string(),
    };
    Ok((binder, roles.body.expect("a binding form has a body")))
}

pub fn fn_count(args: &mut Args) -> Result<Value, SelError> {
    Ok(Value::int(args.val(0)?.size() as i64))
}

pub fn fn_indexes(args: &mut Args) -> Result<Value, SelError> {
    let keys = args.val(0)?.keys();
    let items = keys.into_iter().map(Value::text_owned).collect();
    Ok(Value::list(items))
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
    Ok(Value::list(items))
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
        .zip(values)
        .map(|(k, v)| Entry { key: k, val: v })
        .collect();
    Ok(Value::record_from_entries(entries))
}

pub fn fn_take(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.non_neg_int(1)? as usize;
    if count == 0 || val.is_null() {
        return Ok(Value::list(Vec::new()));
    }
    let is_dense_list = {
        let inner = val.0.borrow();
        inner.is_list && inner.storage.is_some() && inner.list_keys().is_none()
    };
    if is_dense_list {
        let inner = val.0.borrow();
        let storage = inner.storage.as_ref().unwrap();
        let take_n = count.min(storage.len());
        return Ok(Value::list(storage[..take_n].to_vec()));
    }
    let ents = val.elems();
    let take_n = count.min(ents.len());
    let items = ents.vals[..take_n].to_vec();
    Ok(Value::list(items))
}

pub fn fn_drop(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.non_neg_int(1)? as usize;
    if val.is_null() {
        return Ok(Value::list(Vec::new()));
    }
    let is_dense_list = {
        let inner = val.0.borrow();
        inner.is_list && inner.storage.is_some() && inner.list_keys().is_none()
    };
    if is_dense_list {
        let inner = val.0.borrow();
        let storage = inner.storage.as_ref().unwrap();
        if count >= storage.len() {
            return Ok(Value::list(Vec::new()));
        }
        return Ok(Value::list(storage[count..].to_vec()));
    }
    let ents = val.elems();
    if count >= ents.len() {
        return Ok(Value::list(Vec::new()));
    }
    let items = ents.vals[count..].to_vec();
    Ok(Value::list(items))
}

pub fn fn_select_cols(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list(Vec::new()));
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
    Ok(Value::list(rows))
}

pub fn fn_dedupe(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list(Vec::new()));
    }
    let ents = val.elems();
    let mut buckets: HashMap<u64, Vec<Value>> = HashMap::new();
    let mut out = Vec::new();
    for ev in ents.vals.iter() {
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
    Ok(Value::list(out))
}

pub fn fn_distinct(args: &mut Args) -> Result<Value, SelError> {
    fn_dedupe(args)
}

pub fn fn_map(args: &mut Args) -> Result<Value, SelError> {
    let (binder, body_idx) = binder_and_body(args, MAP_FORMS)?;
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
        return Ok(Value::list(Vec::new()));
    }

    let ents = val.elems();
    let mut out = Vec::with_capacity(ents.len());

    let mut frame = Frame::new();
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
        let res = args.eval_node(&body_node)?;
        let collected = if fresh_record {
            // MAP adds a copy-depth level even when its allocation is elided.
            res.check_copy_depth(2, args.pos()).map(|()| res)
        } else {
            res.deep_copy(2, args.pos())
        };
        out.push(collected?);
    }
    args.ctx.pop_frame();

    Ok(Value::list(out))
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
    args.ctx.join_right_null = None;
    filter_rows(args, plan, source)
}

#[inline(never)]
fn filter_plan(args: &mut Args) -> Result<Box<FilterPlan>, SelError> {
    // Over a join, the conjuncts are offered to the LINK, which tests what it
    // can on the rows it joins (spec §7.4): this FILTER's first, then the
    // ones handed down from a FILTER above. Deep drops change this FILTER's
    // keys, so they are allowed only where nothing observes them
    // (`keys_unobserved`, stamped by the physical optimiser).
    let handed = args.ctx.join_prefilter.take();
    let (binder, body_idx) = binder_and_body(args, FILTER_FORMS)?;
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
        } else if !had_handed && src.s == "LINK_LEFT" {
            // A predicate that opens with IS_NULL of a right member's field
            // (S6's unsold products): the join may reject the right rows it
            // is FALSE on before building their joined rows. Nothing is
            // reported back -- the null-extended rows were never tested -- so
            // the whole predicate still runs over every row the join builds.
            if let Some((member, field)) = own.first().and_then(|c| right_null_test(&c.node, &binder)) {
                args.ctx.join_right_null = Some(Box::new(JoinRightNull { member, field, deep }));
            }
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
                let rest: Vec<&JoinConjunct> = own.iter().filter(|c| !r.applied.contains(&c.id)).collect();
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
    let needs_k = node_contains_var(body_node, "_K");

    if val.is_null() {
        return Ok(Value::list(Vec::new()));
    }

    let ents = val.elems();
    let mut storage = Vec::new();
    // Source slots of the kept children: their keys are the output's keys.
    let mut kept: Vec<usize> = Vec::new();

    let mut frame = Frame::new();
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
        // body_pos is body_node's own position: eval_bool is exactly
        // eval_node(..).as_bool(body_pos), without the boolean's value cell.
        let keep = crate::eval::eval_bool(body_node, args.ctx)?;
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
        return Ok(Value::list(storage));
    }
    let positions: Option<Vec<u32>> = kept.iter().map(|&i| ents.index_key(i)).collect();
    let keys = match positions {
        Some(ix) => ListKeys::Index(ix.into()),
        None => ListKeys::Text(kept.iter().map(|&i| ents.key(i)).collect::<Vec<_>>().into()),
    };
    Ok(Value::list_with_list_keys(storage, keys))
}

pub fn fn_all(args: &mut Args) -> Result<Value, SelError> {
    let (binder, body_idx) = binder_and_body(args, ALL_FORMS)?;
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::bool(true));
    }

    let ents = val.elems();
    let mut frame = Frame::new();
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
        let res = args.eval_node(&body_node)?;
        if !res.as_bool(body_pos)? {
            return Ok(Value::bool(false));
        }
    }
    args.ctx.pop_frame();
    Ok(Value::bool(true))
}

pub fn fn_any(args: &mut Args) -> Result<Value, SelError> {
    let (binder, body_idx) = binder_and_body(args, ANY_FORMS)?;
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::bool(false));
    }

    let ents = val.elems();
    let mut frame = Frame::new();
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
        let res = args.eval_node(&body_node)?;
        if res.as_bool(body_pos)? {
            return Ok(Value::bool(true));
        }
    }
    args.ctx.pop_frame();
    Ok(Value::bool(false))
}

pub fn fn_sum(args: &mut Args) -> Result<Value, SelError> {
    let (binder, body_idx) = binder_and_body(args, SUM_FORMS)?;
    let body_node = args.node_at(body_idx).clone();
    let body_pos = body_node.pos;
    let needs_k = node_contains_var(&body_node, "_K");

    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::int(0));
    }

    let ents = val.elems();
    let mut total = Dec::zero();
    let mut frame = Frame::new();
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
        let v = args.eval_node(&body_node)?;
        let d = v.as_decimal(body_pos)?;
        total = dec_add(&total, &d, args.pos())?;
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

/// The argument roles of a SORT/TOP call, from the manifest form it takes
/// (manifest::sort_roles). The arity was checked at compile time, so every
/// count has a form.
fn sort_call_roles(forms: &'static [Form], nodes: &[Node]) -> manifest::SortRoles {
    manifest::sort_roles_in(forms, nodes).expect("every accepted SORT/TOP count has a manifest form")
}

fn do_sort(args: &mut Args, forms: &'static [Form], forced_dir: Option<&str>) -> Result<Value, SelError> {
    let val = args.val(0)?;
    let count = args.count();
    // The call's nodes, borrowed apart from `args`: the key is read from
    // them, never cloned per call.
    let nodes = args.nodes;
    // Which argument is the binder, the key and the direction is the
    // manifest's: for SORT_BY's three arguments a text literal third is a
    // direction even when the second is a bare name (the guarded form comes
    // first), then a bare name second is a binder, else the third is a
    // computed direction.
    let roles = sort_call_roles(forms, nodes);
    let binder = match roles.binder {
        Some(i) => args.symbol(i)?,
        None => "_".to_string(),
    };
    let body_opt: Option<&Node> = roles.key.map(|i| &nodes[i]);
    let direction = match roles.dir {
        Some(i) => args.text(i)?.to_ascii_uppercase(),
        None => forced_dir.unwrap_or("ASC").to_string(),
    };

    if direction != "ASC" && direction != "DESC" {
        return Err(SelError::new(
            "E_BAD_ARG",
            "sort direction must be 'ASC' or 'DESC'",
            args.pos_at(roles.dir.expect("only a direction argument can be bad")),
        ));
    }

    if val.is_null() {
        return Ok(Value::list(Vec::new()));
    }
    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list(Vec::new()));
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
        let needs_k = node_contains_var(body, "_K");
        let mut frame = Frame::new();
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
            let k_val = args.eval_node(body)?;
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
    Ok(Value::list(out))
}

pub fn fn_sort(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, const { manifest::forms("SORT") }, None)
}

pub fn fn_sort_desc(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, const { manifest::forms("SORT_DESC") }, Some("DESC"))
}

pub fn fn_sort_by(args: &mut Args) -> Result<Value, SelError> {
    do_sort(args, const { manifest::forms("SORT_BY") }, None)
}

#[cfg(test)]
thread_local! {
    pub(crate) static TOP_COMPARISON_COUNT: Cell<usize> = const { Cell::new(0) };
}

fn compare_sort_keys(
    a_key: &Value,
    a_idx: usize,
    b_key: &Value,
    b_idx: usize,
    descending: bool,
) -> Ordering {
    #[cfg(test)]
    TOP_COMPARISON_COUNT.with(|c| c.set(c.get() + 1));

    let mut c = compare_values(a_key, b_key);
    if descending {
        c = c.reverse();
    }
    if c != Ordering::Equal {
        c
    } else {
        a_idx.cmp(&b_idx)
    }
}

struct TopHeapEntry<'a> {
    item_idx: usize,
    key: &'a Value,
    input_idx: usize,
    descending: bool,
}

impl<'a> PartialEq for TopHeapEntry<'a> {
    fn eq(&self, other: &Self) -> bool {
        self.item_idx == other.item_idx
    }
}

impl<'a> Eq for TopHeapEntry<'a> {}

impl<'a> PartialOrd for TopHeapEntry<'a> {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl<'a> Ord for TopHeapEntry<'a> {
    fn cmp(&self, other: &Self) -> Ordering {
        compare_sort_keys(self.key, self.input_idx, other.key, other.input_idx, self.descending)
    }
}

fn select_top_indices(
    items: &[SortItem],
    limit: usize,
    descending: bool,
) -> Vec<usize> {
    if limit == 0 || items.is_empty() {
        return Vec::new();
    }
    let k = limit.min(items.len());
    let mut heap = BinaryHeap::with_capacity(k);

    for (i, item) in items.iter().enumerate() {
        let entry = TopHeapEntry {
            item_idx: i,
            key: &item.key,
            input_idx: item.idx,
            descending,
        };
        if heap.len() < k {
            heap.push(entry);
        } else if let Some(mut root) = heap.peek_mut() {
            if entry.cmp(&root) == Ordering::Less {
                *root = entry;
            }
        }
    }

    let sorted = heap.into_sorted_vec();
    sorted.into_iter().map(|e| e.item_idx).collect()
}

fn do_top(args: &mut Args, forms: &'static [Form], forced_dir: Option<&str>) -> Result<Value, SelError> {
    // The call's nodes, borrowed apart from `args`: the key is read from
    // them, never cloned per call.
    let nodes = args.nodes;
    // The manifest's roles, as for do_sort; the count is the last argument.
    let roles = sort_call_roles(forms, nodes);
    let val = args.val(0)?;
    let limit = args.non_neg_int(roles.limit.expect("a TOP form has a count"))? as usize;

    // No key: the elements are their own keys, and no frame is pushed.
    let binder = match (roles.binder, roles.key) {
        (Some(i), _) => args.symbol(i)?,
        (None, Some(_)) => "_".to_string(),
        (None, None) => String::new(),
    };
    let body_opt: Option<&Node> = roles.key.map(|i| &nodes[i]);
    let direction = match roles.dir {
        Some(i) => args.text(i)?.to_ascii_uppercase(),
        None => forced_dir.unwrap_or("ASC").to_string(),
    };

    if direction != "ASC" && direction != "DESC" {
        return Err(SelError::new(
            "E_BAD_ARG",
            "sort direction must be 'ASC' or 'DESC'",
            args.pos_at(roles.dir.expect("only a direction argument can be bad")),
        ));
    }

    if limit == 0 || val.is_null() {
        return Ok(Value::list(Vec::new()));
    }

    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list(Vec::new()));
    }

    let eager = body_opt.is_some_and(may_write);

    let needs_k = body_opt.as_ref().is_some_and(|b| node_contains_var(b, "_K"));
    let mut frame = Frame::new();
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
        let item = if eager {
            ev.deep_copy(2, args.pos())?
        } else {
            ev.check_copy_depth(2, args.pos())?;
            ev.clone()
        };

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
            item,
            key: k_val,
            idx: ei,
        });
    }

    if framed {
        args.ctx.pop_frame();
    }
    prepare_sort_keys(&mut indexed, args.pos())?;
    let desc = direction == "DESC";

    let n = indexed.len();
    let out = if limit <= n / 4 {
        let indices = select_top_indices(&indexed, limit, desc);
        let mut out = Vec::with_capacity(indices.len());
        for idx in indices {
            let it = &indexed[idx].item;
            out.push(if eager { it.clone() } else { it.deep_copy(2, args.pos())? });
        }
        out
    } else {
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
        let take_n = limit.min(n);
        let mut out = Vec::with_capacity(take_n);
        for item in &indexed[..take_n] {
            let it = &item.item;
            out.push(if eager { it.clone() } else { it.deep_copy(2, args.pos())? });
        }
        out
    };
    Ok(Value::list(out))
}


pub fn fn_top(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, const { manifest::forms("TOP") }, None)
}

pub fn fn_top_desc(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, const { manifest::forms("TOP_DESC") }, Some("DESC"))
}

pub fn fn_top_by(args: &mut Args) -> Result<Value, SelError> {
    do_top(args, const { manifest::forms("TOP_BY") }, None)
}

struct BucketGroup {
    key: Value,
    key_str: String,
    rows: Vec<Value>,
}

/// Whether evaluating `node` might write: an assignment, or a call of an
/// application's function (the host may do anything) -- anything but a shipped
/// builtin, however it was installed (crate::builtins::call_may_have_effects)
/// -- anywhere inside it. The one answer SORT/TOP keys and BUCKET keys both ask
/// (recursion is bounded by the parse depth cap). Traversal policy:
/// scope-blind, every child.
fn may_write(node: &Node) -> bool {
    if node.t == NodeType::Assign {
        return true;
    }
    if node.t == NodeType::Call && crate::builtins::call_may_have_effects(node) {
        return true;
    }
    node.children().any(may_write)
}

pub fn fn_bucket(args: &mut Args) -> Result<Value, SelError> {
    let val = args.val(0)?;
    if val.is_null() {
        return Ok(Value::list(Vec::new()));
    }
    let ents = val.elems();
    if ents.is_empty() {
        return Ok(Value::list(Vec::new()));
    }

    // The key and the projection are the form's first and second scoped
    // arguments.
    let roles = call_roles(BUCKET_FORMS, args.nodes);
    let binder = match roles.binder {
        Some(i) => args.symbol(i)?,
        None => "_".to_string(),
    };
    let key_node: Node = args.node_at(roles.body.expect("a BUCKET form has a key")).clone();
    let agg_node_opt: Option<Node> = roles.extra.map(|i| args.node_at(i).clone());

    let needs_k = node_contains_var(&key_node, "_K");
    let mut frame = Frame::new();
    frame.insert(binder.clone(), Value::none());
    if needs_k {
        frame.insert("_K".to_string(), Value::none());
    }

    let mut table: HashMap<u64, Vec<usize>> = HashMap::new();
    let mut bare_groups: HashMap<String, usize> = HashMap::new();
    let mut groups: Vec<BucketGroup> = Vec::new();
    // A row is collected when its key is computed and it is grouped (spec
    // §3.4): when the key or the projection might write, it is copied then, so
    // neither a later key nor the projection can change a row already grouped.
    // Otherwise nothing can, and the copy waits for the result.
    let eager = may_write(&key_node) || agg_node_opt.as_ref().is_some_and(may_write);
    // Bare: record -> group list -> row (3 levels); projected: group list -> row (2).
    let row_depth = if agg_node_opt.is_none() { 3 } else { 2 };

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
                    return Err(SelError::null("value is NULL", key_node.pos));
                }
                return Err(SelError::not_text(
                    "a bucket key must be text or a number, got a list or record",
                    key_node.pos,
                ));
            }
            group_key.as_text(key_node.pos)?
        } else {
            String::new()
        };
        let item = if eager {
            ev.deep_copy(row_depth, args.pos())?
        } else {
            ev.clone()
        };

        if agg_node_opt.is_none() {
            if let Some(&idx) = bare_groups.get(&key_str) {
                groups[idx].rows.push(item);
            } else {
                let idx = groups.len();
                bare_groups.insert(key_str.clone(), idx);
                groups.push(BucketGroup {
                    key: group_key,
                    key_str,
                    rows: vec![item],
                });
            }
        } else {
            let h = group_key.structural_hash()?;
            let bucket = table.entry(h).or_default();
            let mut found = false;
            for &idx in bucket.iter() {
                if groups[idx].key.eql(&group_key, 1, Pos::default())? {
                    groups[idx].rows.push(item.clone());
                    found = true;
                    break;
                }
            }
            if !found {
                let idx = groups.len();
                groups.push(BucketGroup {
                    key: group_key,
                    key_str,
                    rows: vec![item],
                });
                bucket.push(idx);
            }
        }
    }
    args.ctx.pop_frame();

    if agg_node_opt.is_none() {
        let out = Value::none();
        for g in groups {
            let rows = if eager {
                g.rows
            } else {
                let mut rows_copy = Vec::with_capacity(g.rows.len());
                for r in &g.rows {
                    rows_copy.push(r.deep_copy(3, args.pos())?);
                }
                rows_copy
            };
            out.set(&g.key_str, Value::list(rows), Pos::default())?;
        }
        return Ok(out);
    }

    let agg_node = agg_node_opt.unwrap();
    let mut out = Vec::with_capacity(groups.len());
    let mut agg_frame = Frame::new();
    agg_frame.insert(binder.clone(), Value::none());
    agg_frame.insert("_K".to_string(), Value::none());
    args.ctx.push_frame(agg_frame);

    for g in groups {
        if let Some(f) = args.ctx.frames.last_mut() {
            f.set(&binder, Value::list(g.rows));
            f.set("_K", g.key);
        }
        out.push(args.eval_node(&agg_node)?.deep_copy(2, args.pos())?);
    }
    args.ctx.pop_frame();

    Ok(Value::list(out))
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

/// An equi-join bucket key: two keys are equal exactly when the join's
/// operator calls the operands equal, because a bucket hit is never
/// re-checked against the predicate.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum JoinKey {
    /// A NULL operand, which joins nothing.
    Null,
    /// An operand the operator refuses; it raises once the pair is live.
    Bad,
    /// `==`: a number that fits an i64 once its fraction zeros are dropped.
    Int(i64),
    /// `==`: any other number, by its scale-trimmed text.
    Dec(SelStr),
    /// `$==`: text, or BIN whose bytes are UTF-8, by those bytes.
    Text(SelStr),
    /// `$==`: BIN whose bytes are not UTF-8 -- equal to no text, and to
    /// another BIN only byte for byte (never through a lossy decode).
    Bytes(Rc<[u8]>),
}

fn canonical_join_key(v: &Value, numeric: bool) -> (JoinKey, Option<Value>) {
    if v.is_null() {
        return (JoinKey::Null, None);
    }
    if numeric {
        let d = match v.as_decimal(Pos::default()) {
            Ok(d) => d,
            Err(_) => return (JoinKey::Bad, Some(v.clone())),
        };
        if d.is_integer() {
            if let Some(n) = d.to_i64() {
                return (JoinKey::Int(n), None);
            }
        }
        let trimmed = crate::dec::dec_trim_scale(&d);
        if trimmed.is_integer() {
            if let Some(n) = trimmed.to_i64() {
                return (JoinKey::Int(n), None);
            }
        }
        return (JoinKey::Dec(SelStr::from(dec_format(&trimmed))), None);
    }

    // Text keys by their text, shared rather than copied.
    if let Ok(s) = v.as_text_str(Pos::default()) {
        return (JoinKey::Text(s), None);
    }
    match v.as_bytes(Pos::default()) {
        Ok(b) => match std::str::from_utf8(&b) {
            Ok(s) => (JoinKey::Text(SelStr::from(s)), None),
            Err(_) => (JoinKey::Bytes(Rc::from(b.as_ref())), None),
        },
        Err(_) => (JoinKey::Bad, Some(v.clone())),
    }
}

struct JoinEqui<'a> {
    left_expr: &'a Node,
    right_expr: &'a Node,
    numeric: bool,
    swapped: bool,
}

fn expr_depends_only_on(node: &Node, allowed: &HashSet<String>) -> bool {
    match node.t {
        NodeType::Var => allowed.contains(&node.s.to_ascii_uppercase()),
        NodeType::Index => {
            node.l.as_ref().is_none_or(|l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().is_none_or(|r| expr_depends_only_on(r, allowed))
        }
        NodeType::Call => node.items.iter().all(|a| expr_depends_only_on(a, allowed)),
        NodeType::Bin => {
            node.l.as_ref().is_none_or(|l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().is_none_or(|r| expr_depends_only_on(r, allowed))
        }
        NodeType::Un => node.l.as_ref().is_none_or(|l| expr_depends_only_on(l, allowed)),
        NodeType::Assign => {
            node.l.as_ref().is_none_or(|l| expr_depends_only_on(l, allowed))
                && node.r.as_ref().is_none_or(|r| expr_depends_only_on(r, allowed))
        }
        NodeType::Seq | NodeType::List => node.items.iter().all(|item| expr_depends_only_on(item, allowed)),
        _ => true,
    }
}

fn extract_join_equi<'a>(node: &'a Node, b1: &str, b2: &str) -> Option<JoinEqui<'a>> {
    // An equality of either comparison family (`==` or `$==`).
    if node.t != NodeType::Bin
        || !crate::ops::is_comparison(&node.s)
        || crate::ops::relation(&node.s) != Some(crate::lexicon::Relation::Eq)
    {
        return None;
    }
    let numeric = crate::ops::is_numeric_comparison(&node.s);
    if b1.eq_ignore_ascii_case(b2) {
        return None;
    }
    let mut left_names = HashSet::new();
    left_names.insert(b1.to_ascii_uppercase());
    left_names.insert("_1".to_string());
    left_names.insert("_".to_string());

    let mut right_names = HashSet::new();
    right_names.insert(b2.to_ascii_uppercase());
    right_names.insert("_2".to_string());

    let l = node.l.as_ref()?;
    let r = node.r.as_ref()?;

    if expr_depends_only_on(l, &left_names) && expr_depends_only_on(r, &right_names) {
        return Some(JoinEqui {
            left_expr: l,
            right_expr: r,
            numeric,
            swapped: false,
        });
    }
    if expr_depends_only_on(r, &left_names) && expr_depends_only_on(l, &right_names) {
        return Some(JoinEqui {
            left_expr: r,
            right_expr: l,
            numeric,
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
    if *key == JoinKey::Null || !facts.live {
        return Ok(());
    }
    if *key == JoinKey::Bad {
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

#[cfg(test)]
thread_local! {
    // Per right_null_rejects call: how many right rows it rejected, or None
    // when it declined.
    pub(crate) static RIGHT_NULL_SEEN: std::cell::RefCell<Vec<Option<usize>>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// Equi-LINK_LEFT, once every right key was computed: marks rejected the right
/// rows a FILTER that opens with IS_NULL(_["member"]["field"]) drops wherever
/// they are joined, and answers whether the join could tell. The member must
/// be one of this join's right binder keys -- the binder, its lower case or
/// `_2`, each bound in every joined row to the right row as bucketed (spec
/// §7.4, join_plan's joined_layout) -- and the two binders must not be spelled
/// alike. A row is rejected only when the read certainly yields a non-NULL
/// value: one without the field (E_NO_KEY in the FILTER) or with a NULL there
/// is kept, for the FILTER to decide. Its joined rows are then never built;
/// its left row stays matched, so it gets no null-extended row in their place.
fn right_null_rejects(
    b1: &str,
    b2: &str,
    right_null: &JoinRightNull,
    buckets: &mut HashMap<JoinKey, Vec<(Value, bool)>>,
) -> bool {
    if b1.eq_ignore_ascii_case(b2) || !binder_keys(b2, "_2").contains(&right_null.member) {
        #[cfg(test)]
        RIGHT_NULL_SEEN.with(|seen| seen.borrow_mut().push(None));
        return false;
    }
    for rows in buckets.values_mut() {
        for (right, rejected) in rows.iter_mut() {
            if right.get(&right_null.field).is_some_and(|value| !value.is_null()) {
                *rejected = true;
            }
        }
    }
    #[cfg(test)]
    RIGHT_NULL_SEEN.with(|seen| seen.borrow_mut().push(Some(buckets.values().flatten().filter(|row| row.1).count())));
    true
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

/// What a pre-applied conjunct list says of the row bound in the innermost
/// frame.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Verdict {
    Keep,
    Drop,
    /// A conjunct raised: the row is kept, and the FILTER decides.
    KeepOnError,
}

fn join_verdict(args: &mut Args, conjuncts: &[Node], errored: &mut bool) -> Verdict {
    for conjunct in conjuncts {
        match crate::eval::eval_bool(conjunct, args.ctx) {
            Err(_) => {
                *errored = true;
                return Verdict::KeepOnError;
            }
            Ok(false) => return Verdict::Drop,
            Ok(true) => {}
        }
    }
    Verdict::Keep
}

fn fields_all(fields: &HashSet<String>, mut pred: impl FnMut(&str) -> bool) -> bool {
    fields.iter().all(|f| pred(f))
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
    // Taken with the pre-filter, before anything is evaluated (link_head),
    // and kept here rather than in the state: a pointer in this frame.
    let right_null = args.ctx.join_right_null.take();
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
    link_body(args, st, left_val, right_val, right_null)
}

#[inline(never)]
fn link_head<'a>(args: &mut Args<'a>, left_join: bool) -> Result<Box<LinkState<'a>>, SelError> {
    // Taken before anything else is evaluated, so a LINK nested in this one's
    // sources cannot pick it up by accident; it is handed down on purpose.
    let prefilter = args.ctx.join_prefilter.take();
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
    // Explicit binders name the sides; without them each side is named
    // after the variable it reads (spec §7.4).
    let roles = call_roles(if left_join { LINK_LEFT_FORMS } else { LINK_FORMS }, nodes);
    if let Some(i) = roles.binder {
        b1 = args.symbol(i)?;
    }
    if let Some(i) = roles.binder2 {
        b2 = args.symbol(i)?;
    }
    let predicate_node: &'a Node = &nodes[roles.body.expect("a LINK form has a predicate")];
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
        next.extend(side.keys.iter().cloned());
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
        let owned_by_left = |fields: &HashSet<String>, stage: &JoinStage| {
            let upper = st.above_keys_of(stage);
            fields_all(fields, |f| !rs.keys.contains(f) && !upper.contains(f))
        };
        let total_below = |reqs: &[JoinTotalReq], stage: &JoinStage| {
            join_totality(reqs, None, &rs, st.above_of(stage))
        };
        let nothing_right = |_: &HashSet<String>, _: &JoinStage| false;
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
        let mut row_names = HashSet::new();
        row_names.insert(st.b1.clone());
        row_names.insert(st.b1.to_ascii_lowercase());
        row_names.insert("_1".to_string());
        row_names.insert("_".to_string());
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
    // What a FILTER over this LINK_LEFT that opens with IS_NULL of a right
    // member's field left (right_null_rejects).
    right_null: Option<Box<JoinRightNull>>,
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
    if below.as_ref().is_some_and(|b| b.dropped) && (left_val.is_null() || left_val.elems().is_empty()) {
        left_val = args.eval_node(left_node)?;
        args.ctx.join_prefilter_report = None;
        below = None;
    }

    if left_val.is_null() {
        return Ok(Value::list(Vec::new()));
    }

    let left_ents = left_val.elems().vals;
    let right_ents = right_val.elems().vals;

    if left_ents.is_empty() || (right_ents.is_empty() && !left_join) {
        return Ok(Value::list(Vec::new()));
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
            let mut r_frame = Frame::new();
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
                if k != JoinKey::Null {
                    if k == JoinKey::Bad {
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
                applied: HashSet::new(),
                errored: false,
                dropped: below.as_ref().is_some_and(|b| b.dropped),
            };

            let upper_b1 = b1.to_ascii_uppercase();
            let upper_b2 = b2.to_ascii_uppercase();
            // A read through this join's right binder is the right element
            // in every joined row, unless the left binder has the same name,
            // or a join above rebinds it.
            let right_names = |stage_above: usize| {
                let mut names = HashSet::new();
                names.insert(upper_b2.clone());
                if stage_above == 0 {
                    names.insert("_2".to_string());
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
                let mut self_names = HashSet::new();
                self_names.insert(upper_b1.clone());
                self_names.insert("_1".to_string());
                if safe {
                    let owned_here = |fields: &HashSet<String>, stage: &JoinStage| {
                        let upper = above_keys_of(stage);
                        fields_all(fields, |f| !rs.keys.contains(f) && !upper.contains(f))
                    };
                    let total_here = |reqs: &[JoinTotalReq], stage: &JoinStage| {
                        join_totality(reqs, Some(&left_side), &rs, above_of(stage))
                    };
                    let right_here = |fields: &HashSet<String>, stage: &JoinStage| {
                        if !right_ok {
                            return false;
                        }
                        let names = right_names(stage.above);
                        let upper = above_keys_of(stage);
                        fields_all(fields, |f| names.contains(f) && !upper.contains(f))
                    };
                    let (walk_applied, _) = join_stage_walk(&stages, &owned_here, &total_here, &right_here);
                    for applied in walk_applied {
                        let c = applied.conjunct;
                        report.applied.insert(c.id);
                        if let Some(ref b) = below {
                            if !b.errored && b.applied.contains(&c.id) {
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
                            .iter()
                            .any(|f| self_names.contains(f) && !left_side.first.contains(f));
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
            let mut rejecting = !right_prefix.is_empty();
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
                        if join_verdict(args, &right_prefix, &mut report.errored) == Verdict::Drop {
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

            // A LINK_LEFT rejects right rows only for a FILTER that opens with
            // IS_NULL (right_null_rejects), and then drops no left row:
            // `position` counts every joined row as written, built or not, and
            // the collection limit is held to that count, where the join as
            // written would have raised (spec §6.4).
            let mut logical = false;
            let mut deep = deep;
            if let Some(ref rn) = right_null {
                if left_join && !has_prefilter && right_null_rejects(&b1, &b2, rn, &mut buckets) {
                    logical = true;
                    rejecting = true;
                    // Whether nothing observes the FILTER's keys: then the
                    // kept rows need not carry the positions the rows as
                    // written had.
                    deep = rn.deep;
                }
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
                let mut asked: Option<Verdict> = None;
                if let Some(ref ff) = fast_field {
                    if left.has(ff) {
                        join_set_row(args.ctx, &row_slots, &left);
                        asked = Some(join_verdict(args, &prefix, &mut report.errored));
                        if asked == Some(Verdict::Drop) {
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

                let asked = match asked {
                    Some(verdict) => verdict,
                    None if prefix.is_empty() => Verdict::Keep,
                    None => join_verdict(args, &prefix, &mut report.errored),
                };

                if asked == Verdict::Drop {
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
                    let skip = rejecting && asked == Verdict::Keep;
                    // Its matches are all joined rows as written, rejected or
                    // not: a left row whose matches are all rejected is still
                    // matched, and gets no null-extended row.
                    if logical {
                        cap_collection((position - 1 + rows.len()) as u128, args.pos())?;
                    }
                    for (right, rejected) in rows {
                        if skip && *rejected {
                            dropped = true;
                            position += 1;
                            continue;
                        }
                        if !logical {
                            cap_collection(if numbered { position } else { output.len() + 1 } as u128, args.pos())?;
                        }
                        output.push(projector.project(&left, Some(right)));
                        if numbered {
                            positions.push(position as u32);
                        }
                        position += 1;
                    }
                } else if left_join {
                    cap_collection(if numbered || logical { position } else { output.len() + 1 } as u128, args.pos())?;
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
            return Ok(Value::list(output));
        }
    }

    if has_prefilter {
        args.ctx.join_prefilter_report = Some(JoinReport {
            applied: HashSet::new(),
            errored: false,
            dropped: below.as_ref().is_some_and(|b| b.dropped),
        });
    }

    // Nested-loop fallback join
    let mut output = Vec::new();
    let mut frame = Frame::new();
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

    Ok(Value::list(output))
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

    #[test]
    fn test_selector_comparison_count_bounded() {
        let n = 10000;
        let mut keys = Vec::with_capacity(n);
        let mut state: u64 = 123456789;
        for i in 0..n {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let val = (state >> 33) as i64;
            keys.push(SortItem {
                item: Value::none(),
                key: Value::int(val),
                idx: i,
            });
        }

        TOP_COMPARISON_COUNT.with(|c| c.set(0));
        let winners = select_top_indices(&keys, 1, false);
        let comps = TOP_COMPARISON_COUNT.with(|c| c.get());

        assert_eq!(winners.len(), 1);
        eprintln!("Selector comparisons for {n} keys: {comps} (budget <= {})", 2 * n);
        assert!(
            comps <= 2 * n,
            "comparisons {comps} exceeded budget of {}",
            2 * n
        );
    }
}


#[cfg(test)]
mod right_null_tests {
    use super::*;

    const CONTEXT: &str = r#"RECORD(
        "P", LIST(RECORD("id", 1, "name", "a"), RECORD("id", 2, "name", "b"), RECORD("id", 3, "name", 5),
                  RECORD("id", 4, "name", "d")),
        "I", LIST(RECORD("id", 10, "product_id", 1), RECORD("id", NULL, "product_id", 1),
                  RECORD("id", 12, "product_id", 3), RECORD("product_id", 9)))"#;

    fn seen() -> Vec<Option<usize>> {
        RIGHT_NULL_SEEN.with(|seen| std::mem::take(&mut *seen.borrow_mut()))
    }

    /// What `source` answers, as written and with its join bound to a helper
    /// variable first (`J = …; J .> FILTER(…)`, where no FILTER sits on a LINK):
    /// the dump, or the error code and where it points -- in the join or in the
    /// FILTER, by its offset there, since the two forms differ in length.
    fn both_forms(source: &str) -> (String, String) {
        both_forms_in(CONTEXT, source)
    }

    fn both_forms_in(context: &str, source: &str) -> (String, String) {
        let (head, tail) = source.rsplit_once(" .> FILTER(").unwrap();
        let go = |src: &str, head_at: usize| {
            let context = crate::evaluate(context, None).unwrap();
            match crate::compile(src).and_then(|mut program| program.run(Some(context))) {
                Ok(value) => format!("ok {}", value.dump().unwrap()),
                Err(error) => {
                    let filter_at = src.rfind(&format!("FILTER({tail}")).unwrap();
                    let at = error.pos.offset;
                    let place = if at >= filter_at {
                        format!("filter+{}", at - filter_at)
                    } else {
                        format!("join+{}", at - head_at)
                    };
                    format!("err {} {}:{place}", error.code, error.pos.line)
                }
            }
        };
        (go(&format!("{head} .> FILTER({tail}"), 0), go(&format!("J = {head}; J .> FILTER({tail}"), 4))
    }

    const LEFT: &str = r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"])"#;

    #[test]
    fn rejection_keeps_results_keys_and_errors() {
        // A FILTER that opens with IS_NULL of a LINK_LEFT's right member lets
        // the join skip building the joined rows of the right rows that
        // conjunct is FALSE on; every shape answers -- rows, keys, errors and
        // their places -- what the join bound to a helper variable answers.
        for filter in [
            r#"FILTER(IS_NULL(_["i"]["id"]))"#,
            r#"FILTER(IS_NULL(_["I"]["id"])) .> MAP(_K)"#,
            r#"FILTER(IS_NULL(_["_2"]["id"]) AND _K $!= "2")"#,
            r#"FILTER(r, IS_NULL(r["i"]["id"]) AND r["p"]["name"] > 1)"#,
            r#"FILTER(IS_NULL(_["i"]["id"]) AND _["p"]["name"] $!= "b") .> MAP(_["p"]["id"])"#,
            r#"FILTER(IS_NULL(_["i"]["product_id"]))"#,
            r#"FILTER(IS_NULL(_["i"]["sku"]))"#,
        ] {
            let source = format!("{LEFT} .> {filter}");
            seen();
            let (as_written, through_a_variable) = both_forms(&source);
            assert_eq!(as_written, through_a_variable, "{source}");
            // The path was taken; it rejects rows only where the field is
            // there and not NULL (no item has a sku: every row is the FILTER's
            // to raise on).
            let seen = seen();
            assert!(matches!(seen.first(), Some(Some(n)) if (*n > 0) == !source.contains("sku")), "{source}: {seen:?}");
        }
    }

    #[test]
    fn the_hint_is_the_join_under_the_filter_s_alone() {
        // `_2` is a right binder key of both joins: the hint is the outer
        // one's, and the inner one -- its left source, which the evaluator
        // would otherwise run as the first stage of a pipeline -- never sees
        // it.
        let context = r#"RECORD(
            "A", LIST(RECORD("id", 2, "bid", 4, "cid", 2), RECORD("id", 4, "bid", 3, "cid", 3)),
            "B", LIST(RECORD("id", 2, "cid", 1), RECORD("id", 4, "cid", 3), RECORD("id", 4, "cid", 4)),
            "C", LIST(RECORD("id", 3, "sku", "P")))"#;
        let source = r#"A .> LINK_LEFT(B, _1["bid"] == _2["id"]) .> LINK_LEFT(C, L, R, L["B"]["cid"] == R["id"]) .> FILTER(IS_NULL(_["_2"]["id"]))"#;
        seen();
        let (as_written, through_a_variable) = both_forms_in(context, source);
        assert_eq!(as_written, through_a_variable);
        assert_eq!(seen(), [Some(1)]);
    }

    #[test]
    fn declines_what_is_not_the_right_row() {
        for source in [
            // not a right binder key of this join: the left one, a mixed-case
            // name, a relation name under explicit binders
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["p"]["id"]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["iI"]["id"]))"#,
            r#"LINK_LEFT(P, I, L, R, L["id"] == R["product_id"]) .> FILTER(IS_NULL(_["I"]["id"]))"#,
            // binders spelled alike
            r#"LINK_LEFT(P, I, X, x, X["id"] == x["product_id"]) .> FILTER(IS_NULL(_["x"]["id"]))"#,
        ] {
            seen();
            let (as_written, through_a_variable) = both_forms(source);
            assert_eq!(as_written, through_a_variable, "{source}");
            let seen = seen();
            assert!(seen.is_empty() || seen == [None], "{source}: {seen:?}");
        }
    }

    #[test]
    fn is_offered_only_for_its_shape() {
        for source in [
            // not IS_NULL first, not a literal member or field, not the
            // FILTER's own element
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(TRUE AND IS_NULL(_["i"]["id"]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(NOT IS_NULL(_["i"]["id"]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_[LOWER("I")]["id"]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"][LOWER("ID")]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(r, IS_NULL(_["i"]["id"]))"#,
            // an inner join, and a FILTER handed conjuncts by a join above
            r#"P .> LINK(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"]))"#,
            r#"P .> LINK_LEFT(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"])) .> LINK(I, L, R, L["p"]["id"] == R["product_id"]) .> FILTER(_["p"]["id"] > 0) .> MAP(1)"#,
        ] {
            seen();
            let context = crate::evaluate(CONTEXT, None).unwrap();
            let _ = crate::compile(source).and_then(|mut program| program.run(Some(context)));
            assert_eq!(seen(), [], "{source}");
        }
    }

    #[test]
    fn holds_the_real_collection_limit() {
        // The join as written builds every matched row before the FILTER
        // drops it, and raises E_RANGE when they are more than MAX_COLLECTION:
        // a row the rejection never builds still counts, at the same place.
        let side = 1000;
        assert_eq!(side * side, crate::limits::MAX_COLLECTION);
        let rows = |n: usize, fields: &[(&str, i64)]| {
            Value::list((0..n).map(|_| Value::record_from_entries(
                fields.iter().map(|(k, v)| Entry { key: k.to_string(), val: Value::int(*v) }).collect(),
            )).collect())
        };
        let source = r#"P .> LINK_LEFT(I, _1["id"] == _2["k"]) .> FILTER(IS_NULL(_["i"]["id"]))"#;
        let run = |left: usize| {
            let context = Value::record_from_entries(vec![
                Entry { key: "I".into(), val: rows(side, &[("id", 5), ("k", 1)]) },
                Entry { key: "P".into(), val: rows(left, &[("id", 1)]) },
            ]);
            crate::compile(source).unwrap().run(Some(context))
        };
        assert_eq!(run(side).unwrap().dump().unwrap(), "-");
        let error = run(side + 1).unwrap_err();
        assert_eq!((error.code, error.pos.col), ("E_RANGE", source.find("LINK_LEFT").unwrap() + 1));
    }

    #[test]
    fn a_key_writes_only_through_a_function_that_is_not_shipped() {
        // SORT/TOP/BUCKET copy what they collect before the next key may
        // write (may_write) only when a key can: never over shipped builtins.
        let key = |source: &str| crate::compile(source).unwrap().ast().clone();
        assert!(!may_write(&key(r#"ABS(_["k"]) + COUNT(LIST(1))"#)));
        assert!(may_write(&key(r#"ABS(T_POKE())"#)));
        assert!(may_write(&key(r#"(X = 1; 2)"#)));
    }
}
