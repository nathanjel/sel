use std::collections::HashSet;

use crate::ast::{Node, NodeType};
use crate::dec::{
    dec_add, dec_cmp, dec_div, dec_format, dec_mod, dec_mul, dec_negate, dec_parse, dec_sub, Dec,
};
use crate::limits::MAX_DEPTH;
use crate::math_plan::{compile_math_plan, is_math_op};
use crate::utf8::Pos;

const PIPELINE_OPS: &[&str] = &[
    "FILTER",
    "BUCKET",
    "SELECT_COLS",
    "MAP",
    "DISTINCT",
    "DEDUPE",
    "TAKE",
    "DROP",
    "SORT",
    "SORT_DESC",
    "SORT_BY",
    "TOP",
    "TOP_DESC",
    "TOP_BY",
    "LINK",
    "LINK_LEFT",
];

pub fn is_pipeline_op(name: &str) -> bool {
    PIPELINE_OPS.contains(&name)
}

pub fn unwind_pipeline(root: &Node) -> (&Node, Vec<&Node>) {
    let mut steps = Vec::new();
    let mut curr = root;
    while curr.t == NodeType::Call && is_pipeline_op(&curr.s) && !curr.items.is_empty() {
        steps.push(curr);
        curr = &curr.items[0];
    }
    steps.reverse();
    (curr, steps)
}

// The old input is replaced, so copying it would clone every preceding stage
// once per step. Keep the stage metadata and non-source arguments only.
fn clone_step_with_source(step: &Node, source: Node) -> Node {
    let mut items = Vec::with_capacity(step.items.len().max(1));
    items.push(source);
    items.extend(step.items.iter().skip(1).cloned());
    Node {
        t: step.t,
        pos: step.pos,
        s: step.s.clone(),
        b: step.b,
        grouped: step.grouped,
        sql_binding: step.sql_binding,
        l: step.l.clone(),
        r: step.r.clone(),
        items,
        dec: step.dec.clone(),
        shape: step.shape.clone(),
        slot_cache: step.slot_cache.clone(),
        math_plan: step.math_plan.clone(),
        keys_unobserved: step.keys_unobserved,
        borrowed_filter: step.borrowed_filter,
        spec: step.spec.clone(),
    }
}

pub fn build_pipeline(source: &Node, steps: &[Node]) -> Node {
    let mut curr = source.clone();
    for step in steps {
        curr = clone_step_with_source(step, curr);
    }
    curr
}

fn opt_bool(val: bool, pos: Pos) -> Node {
    let mut n = Node::new(NodeType::Bool, pos);
    n.b = val;
    n
}

fn opt_num(val: String, dec: Dec, pos: Pos) -> Node {
    let mut n = Node::new(NodeType::Num, pos);
    n.s = val;
    n.dec = Some(dec);
    n
}

fn is_literal(n: &Node) -> bool {
    matches!(
        n.t,
        NodeType::Num | NodeType::Text | NodeType::Bool | NodeType::Null
    )
}

fn hoist_literal(child: &Node, pos: Pos) -> Node {
    let mut cp = child.clone();
    cp.pos = pos;
    cp
}

pub fn opt_fold(node: &Node) -> Node {
    if node.t == NodeType::Un {
        if let Some(ref l) = node.l {
            if node.s == "NOT" && l.t == NodeType::Bool {
                return opt_bool(!l.b, node.pos);
            }
            if node.s == "NEG" && l.t == NodeType::Num {
                let dec = l.dec.clone().or_else(|| dec_parse(&l.s, node.pos).ok());
                if let Some(d) = dec {
                    let neg = dec_negate(&d);
                    return opt_num(dec_format(&neg), neg, node.pos);
                }
            }
        }
        return node.clone();
    }

    if node.t == NodeType::Bin {
        if let (Some(ref l), Some(ref r)) = (&node.l, &node.r) {
            if node.s == "AND" {
                if l.t == NodeType::Bool && !l.b {
                    return opt_bool(false, node.pos);
                }
                if l.t == NodeType::Bool && r.t == NodeType::Bool {
                    return opt_bool(l.b && r.b, node.pos);
                }
            }
            if node.s == "OR" {
                if l.t == NodeType::Bool && l.b {
                    return opt_bool(true, node.pos);
                }
                if l.t == NodeType::Bool && r.t == NodeType::Bool {
                    return opt_bool(l.b || r.b, node.pos);
                }
            }
            if l.t == NodeType::Num && r.t == NodeType::Num {
                let dec_l = l.dec.clone().or_else(|| dec_parse(&l.s, node.pos).ok());
                let dec_r = r.dec.clone().or_else(|| dec_parse(&r.s, node.pos).ok());
                if let (Some(dl), Some(dr)) = (dec_l, dec_r) {
                    match node.s.as_str() {
                        "+" => {
                            if let Ok(res) = dec_add(&dl, &dr, node.pos) {
                                return opt_num(dec_format(&res), res, node.pos);
                            }
                        }
                        "-" => {
                            if let Ok(res) = dec_sub(&dl, &dr, node.pos) {
                                return opt_num(dec_format(&res), res, node.pos);
                            }
                        }
                        "*" => {
                            if let Ok(res) = dec_mul(&dl, &dr, node.pos) {
                                return opt_num(dec_format(&res), res, node.pos);
                            }
                        }
                        "/" => {
                            if let Ok(res) = dec_div(&dl, &dr, node.pos) {
                                return opt_num(dec_format(&res), res, node.pos);
                            }
                        }
                        "%" => {
                            if let Ok(res) = dec_mod(&dl, &dr, node.pos) {
                                return opt_num(dec_format(&res), res, node.pos);
                            }
                        }
                        "==" | "!=" | "<" | "<=" | ">" | ">=" => {
                            let c = dec_cmp(&dl, &dr);
                            let b = match node.s.as_str() {
                                "==" => c.is_eq(),
                                "!=" => c.is_ne(),
                                "<" => c.is_lt(),
                                "<=" => c.is_le(),
                                ">" => c.is_gt(),
                                ">=" => c.is_ge(),
                                _ => false,
                            };
                            return opt_bool(b, node.pos);
                        }
                        _ => {}
                    }
                }
            }
            if l.t == NodeType::Text && r.t == NodeType::Text {
                match node.s.as_str() {
                    "$==" => return opt_bool(l.s == r.s, node.pos),
                    "$!=" => return opt_bool(l.s != r.s, node.pos),
                    "$<" => return opt_bool(l.s < r.s, node.pos),
                    "$<=" => return opt_bool(l.s <= r.s, node.pos),
                    "$>" => return opt_bool(l.s > r.s, node.pos),
                    "$>=" => return opt_bool(l.s >= r.s, node.pos),
                    _ => {}
                }
            }
        }
        return node.clone();
    }

    if node.t == NodeType::Call && node.s == "IF" && node.items.len() == 3 && node.items[0].t == NodeType::Bool {
        let branch = if node.items[0].b {
            &node.items[1]
        } else {
            &node.items[2]
        };
        if is_literal(branch) {
            return hoist_literal(branch, node.pos);
        }
    }

    node.clone()
}

fn opt_step_arg_folds(step: &Node, index: usize) -> bool {
    let sort_count = if step.s == "SORT_BY" {
        step.items.len()
    } else if step.s == "TOP_BY" {
        if step.items.len() > 1 {
            step.items.len() - 1
        } else {
            0
        }
    } else {
        0
    };
    !(sort_count == 3
        && index == 2
        && step.items.len() > 1
        && step.items[1].t == NodeType::Var
        && !step.items[1].grouped)
}

fn opt_exceeds_depth(node: &Node, depth: usize) -> bool {
    if depth > MAX_DEPTH {
        return true;
    }
    let next = depth + 1;
    for item in &node.items {
        if opt_exceeds_depth(item, next) {
            return true;
        }
    }
    if node.t != NodeType::Assign {
        if let Some(ref l) = node.l {
            if opt_exceeds_depth(l, next) {
                return true;
            }
        }
    }
    if let Some(ref r) = node.r {
        if opt_exceeds_depth(r, next) {
            return true;
        }
    }
    false
}

fn opt_numeric_literal(node: &Node) -> Option<i64> {
    if node.t != NodeType::Num {
        return None;
    }
    let dec = match &node.dec {
        Some(d) => d.clone(),
        None => crate::dec::dec_parse(&node.s, node.pos).ok()?,
    };
    if dec.neg || !dec.is_integer() {
        return None;
    }
    let t = crate::dec::dec_trunc(&dec);
    let v = t.to_i64()?;
    if v < 0 {
        return None;
    }
    Some(v)
}

fn opt_positive_literal(node: &Node) -> bool {
    opt_numeric_literal(node).map_or(false, |n| n >= 1)
}

fn opt_rename_var(node: &Node, old_name: &str, new_name: &str) -> Node {
    let mut cp = node.clone();
    cp.math_plan = None;
    if cp.t == NodeType::Var && cp.s.eq_ignore_ascii_case(old_name) {
        cp.s = new_name.to_string();
    }
    if let Some(ref l) = cp.l {
        cp.l = Some(Box::new(opt_rename_var(l, old_name, new_name)));
    }
    if let Some(ref r) = cp.r {
        cp.r = Some(Box::new(opt_rename_var(r, old_name, new_name)));
    }
    for item in &mut cp.items {
        *item = opt_rename_var(item, old_name, new_name);
    }
    cp
}

fn opt_cannot_raise(node: &Node, binder: &str, logical: bool) -> bool {
    match node.t {
        NodeType::Num | NodeType::Text | NodeType::Bool | NodeType::Null => true,
        NodeType::Var => {
            let name = node.s.to_ascii_uppercase();
            name == "_K" || name == binder.to_ascii_uppercase()
        }
        NodeType::Index => {
            logical
                && node.l.as_ref().map_or(false, |l| {
                    l.t == NodeType::Var && l.s.eq_ignore_ascii_case(binder)
                })
                && node.r.as_ref().map_or(false, |r| r.t == NodeType::Text)
        }
        NodeType::Bin => {
            if !logical {
                return false;
            }
            let op = node.s.as_str();
            let is_safe = matches!(
                op,
                "==" | "!=" | "<" | "<=" | ">" | ">="
                    | "$==" | "$!=" | "$<" | "$<=" | "$>" | "$>="
                    | "AND" | "OR" | "+" | "-" | "*"
            );
            is_safe
                && node.l.as_ref().map_or(true, |l| opt_cannot_raise(l, binder, logical))
                && node.r.as_ref().map_or(true, |r| opt_cannot_raise(r, binder, logical))
        }
        NodeType::Un => {
            logical
                && node.s == "NOT"
                && node.l.as_ref().map_or(true, |l| opt_cannot_raise(l, binder, logical))
        }
        _ => false,
    }
}

fn opt_filter_predicate_cannot_raise(node: &Node, binder: &str, logical: bool) -> bool {
    match node.t {
        NodeType::Bool => true,
        NodeType::Num | NodeType::Text | NodeType::Null | NodeType::Var | NodeType::Index => false,
        _ => opt_cannot_raise(node, binder, logical),
    }
}

struct OptMapInfo<'a> {
    binder: String,
    body: Option<&'a Node>,
    #[allow(dead_code)]
    explicit_binder: bool,
}

fn get_opt_map_info(step: &Node) -> OptMapInfo {
    let args = &step.items;
    let explicit = args.len() == 3 && args[1].t == NodeType::Var && !args[1].grouped;
    let b = if explicit {
        args[1].s.clone()
    } else {
        "_".to_string()
    };
    let body = if explicit {
        args.get(2)
    } else if args.len() > 1 {
        args.get(1)
    } else {
        None
    };
    OptMapInfo {
        binder: b,
        body,
        explicit_binder: explicit,
    }
}

struct OptFilterInfo<'a> {
    binder: String,
    predicate: Option<&'a Node>,
    explicit_binder: bool,
    valid: bool,
}

fn get_opt_filter_info(step: &Node) -> OptFilterInfo {
    let args = &step.items;
    let explicit = args.len() == 3 && args[1].t == NodeType::Var && !args[1].grouped;
    let b = if explicit {
        args[1].s.clone()
    } else {
        "_".to_string()
    };
    let pred = if explicit {
        args.get(2)
    } else if args.len() > 1 {
        args.get(1)
    } else {
        None
    };
    let valid = args.len() == 2 || explicit;
    OptFilterInfo {
        binder: b,
        predicate: pred,
        explicit_binder: explicit,
        valid,
    }
}

struct OptSortInfo<'a> {
    binder: String,
    key: Option<&'a Node>,
}

fn get_opt_sort_info(step: &Node) -> OptSortInfo {
    let args = &step.items;
    let count = args.len();
    let mut info = OptSortInfo {
        binder: "_".to_string(),
        key: None,
    };
    let s = step.s.as_str();
    if s == "SORT" || s == "SORT_DESC" {
        if count == 1 {
            return info;
        }
        if count == 3 && args[1].t == NodeType::Var && !args[1].grouped {
            info.binder = args[1].s.clone();
            info.key = args.get(2);
        } else {
            info.key = args.get(1);
        }
    } else if s == "TOP" || s == "TOP_DESC" {
        if count == 2 {
            return info;
        }
        let sort_count = count - 1;
        if sort_count == 3 && args[1].t == NodeType::Var && !args[1].grouped {
            info.binder = args[1].s.clone();
            info.key = args.get(2);
        } else {
            info.key = args.get(1);
        }
    } else if s == "SORT_BY" || s == "TOP_BY" {
        let mut sort_count = count;
        if s == "TOP_BY" {
            sort_count = count.saturating_sub(1);
        }
        if sort_count == 2 || (sort_count == 3 && args.get(2).map_or(false, |a| a.t == NodeType::Text)) {
            info.key = args.get(1);
        } else if count > 2 && args[1].t == NodeType::Var && !args[1].grouped {
            info.binder = args[1].s.clone();
            info.key = args.get(2);
        }
    }
    info
}

fn opt_map_cannot_raise(step: &Node, logical: bool) -> bool {
    let info = get_opt_map_info(step);
    if let Some(body) = info.body {
        if body.t == NodeType::Call && body.s == "RECORD" {
            for (i, arg) in body.items.iter().enumerate() {
                if i % 2 == 0 {
                    if arg.t != NodeType::Text {
                        return false;
                    }
                } else {
                    if !opt_cannot_raise(arg, &info.binder, logical) {
                        return false;
                    }
                }
            }
            return true;
        }
        return opt_cannot_raise(body, &info.binder, logical);
    }
    true
}

fn opt_map_passthroughs(step: &Node) -> Vec<String> {
    let info = get_opt_map_info(step);
    let mut fields = Vec::new();
    if let Some(body) = info.body {
        if body.t == NodeType::Call && body.s == "RECORD" {
            for i in (0..body.items.len().saturating_sub(1)).step_by(2) {
                let k = &body.items[i];
                let v = &body.items[i + 1];
                if k.t == NodeType::Text && v.t == NodeType::Index {
                    if let (Some(l), Some(r)) = (&v.l, &v.r) {
                        if l.t == NodeType::Var && r.t == NodeType::Text
                            && l.s.eq_ignore_ascii_case(&info.binder)
                            && r.s == k.s
                        {
                            fields.push(k.s.clone());
                        }
                    }
                }
            }
        }
    }
    fields
}

fn opt_map_has_computed(step: &Node) -> bool {
    let info = get_opt_map_info(step);
    if let Some(body) = info.body {
        if body.t == NodeType::Call && body.s == "RECORD" {
            return opt_map_passthroughs(step).len() * 2 != body.items.len();
        }
    }
    true
}

fn opt_field_refs(node: &Node, binder: &str) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut refs = Vec::new();

    fn walk(n: &Node, binder: &str, seen: &mut HashSet<String>, refs: &mut Vec<String>) {
        if n.t == NodeType::Index {
            if let (Some(l), Some(r)) = (&n.l, &n.r) {
                if l.t == NodeType::Var && r.t == NodeType::Text {
                    let v = l.s.to_ascii_uppercase();
                    let b = binder.to_ascii_uppercase();
                    if binder.is_empty() || v == b || v == "_" || v == "_1" || v == "_2" {
                        if seen.insert(r.s.clone()) {
                            refs.push(r.s.clone());
                        }
                    }
                }
            }
        }
        if let Some(ref l) = n.l {
            walk(l, binder, seen, refs);
        }
        if let Some(ref r) = n.r {
            walk(r, binder, seen, refs);
        }
        for child in &n.items {
            walk(child, binder, seen, refs);
        }
    }

    walk(node, binder, &mut seen, &mut refs);
    refs
}

fn opt_reads_var(node: &Node, names: &[&str]) -> bool {
    let wanted: HashSet<String> = names.iter().map(|n| n.to_ascii_uppercase()).collect();
    let mut found = false;

    fn walk(n: &Node, wanted: &HashSet<String>, found: &mut bool) {
        if *found {
            return;
        }
        if n.t == NodeType::Var && wanted.contains(&n.s.to_ascii_uppercase()) {
            *found = true;
            return;
        }
        if n.t == NodeType::Index {
            if let (Some(l), Some(r)) = (&n.l, &n.r) {
                if l.t == NodeType::Var && r.t == NodeType::Text {
                    return;
                }
            }
        }
        if let Some(ref l) = n.l {
            walk(l, wanted, found);
        }
        if let Some(ref r) = n.r {
            walk(r, wanted, found);
        }
        for child in &n.items {
            walk(child, wanted, found);
        }
    }

    walk(node, &wanted, &mut found);
    found
}

fn opt_reads_row_or_key(node: &Node, binder: &str) -> bool {
    opt_reads_var(node, &[binder, "_", "_1", "_2", "_K"])
}

fn opt_step_reads_key(step: &Node) -> bool {
    for arg in step.items.iter().skip(1) {
        if opt_reads_var(arg, &["_K"]) {
            return true;
        }
    }
    false
}

fn opt_keys_renumbered_by(step: Option<&Node>) -> bool {
    match step {
        Some(s) => s.s != "FILTER" && !opt_step_reads_key(s),
        None => false,
    }
}

fn opt_source_is_list(source: &Node) -> bool {
    source.t == NodeType::List
        || (source.t == NodeType::Call && (source.s == "LIST" || source.s == "RECORD"))
}

fn opt_select_fields(step: &Node) -> Vec<String> {
    let mut out = Vec::new();
    for arg in step.items.iter().skip(1) {
        if arg.t == NodeType::List {
            for item in &arg.items {
                if item.t == NodeType::Text {
                    out.push(item.s.clone());
                }
            }
        } else if arg.t == NodeType::Text {
            out.push(arg.s.clone());
        }
    }
    out
}

fn opt_logical_steps(source: &Node, mut current: Vec<Node>, logical: bool) -> Vec<Node> {
    let mut changed = true;
    while changed {
        changed = false;
        let mut next = Vec::new();
        let mut i = 0;
        while i < current.len() {
            let first = &current[i];
            let second = current.get(i + 1);
            let third = current.get(i + 2);

            // TAKE + TAKE
            if let Some(s2) = second {
                if first.s == "TAKE" && s2.s == "TAKE" && first.items.len() == 2 && s2.items.len() == 2 {
                    if let (Some(left), Some(right)) = (
                        opt_numeric_literal(&first.items[1]),
                        opt_numeric_literal(&s2.items[1]),
                    ) {
                        let min_val = left.min(right);
                        let mut merged = s2.clone();
                        let dec = Dec::from_i64(min_val);
                        merged.items = vec![
                            first.items[0].clone(),
                            opt_num(crate::dec::dec_format(&dec), dec, s2.items[1].pos),
                        ];
                        next.push(merged);
                        i += 2;
                        changed = true;
                        continue;
                    }
                }
            }

            // DROP + DROP
            if let Some(s2) = second {
                if first.s == "DROP" && s2.s == "DROP" && first.items.len() == 2 && s2.items.len() == 2 {
                    if let (Some(left), Some(right)) = (
                        opt_numeric_literal(&first.items[1]),
                        opt_numeric_literal(&s2.items[1]),
                    ) {
                        if left <= i64::MAX - right {
                            let sum_val = left + right;
                            let mut merged = s2.clone();
                            let dec = Dec::from_i64(sum_val);
                            merged.items = vec![
                                first.items[0].clone(),
                                opt_num(crate::dec::dec_format(&dec), dec, s2.items[1].pos),
                            ];
                            next.push(merged);
                            i += 2;
                            changed = true;
                            continue;
                        }
                    }
                }
            }

            // SORT... + TAKE -> TOP...
            if let Some(s2) = second {
                if s2.s == "TAKE" && s2.items.len() == 2
                    && opt_positive_literal(&s2.items[1])
                    && (first.s == "SORT" || first.s == "SORT_DESC" || first.s == "SORT_BY")
                {
                    let top_name = match first.s.as_str() {
                        "SORT_DESC" => "TOP_DESC",
                        "SORT_BY" => "TOP_BY",
                        _ => "TOP",
                    };
                    let mut fused = first.clone();
                    fused.pos = s2.pos;
                    fused.s = top_name.to_string();
                    fused.spec = crate::builtins::lookup_spec(top_name);
                    fused.items.push(s2.items[1].clone());
                    next.push(fused);
                    i += 2;
                    changed = true;
                    continue;
                }
            }

            // MAP + FILTER
            if let Some(s2) = second {
                if first.s == "MAP" && s2.s == "FILTER" {
                    let passes = opt_map_passthroughs(first);
                    let pass_set: HashSet<String> = passes.into_iter().collect();
                    let info = get_opt_filter_info(s2);
                    if let Some(pred) = info.predicate {
                        let refs = opt_field_refs(pred, &info.binder);
                        let all_in_pass = !refs.is_empty() && refs.iter().all(|r| pass_set.contains(r));
                        if info.valid && all_in_pass && !opt_reads_row_or_key(pred, &info.binder)
                            && opt_keys_renumbered_by(third) && opt_map_cannot_raise(first, logical)
                        {
                            next.push(s2.clone());
                            next.push(first.clone());
                            i += 2;
                            changed = true;
                            continue;
                        }
                    }
                }
            }

            // SORT... + FILTER
            if let Some(s2) = second {
                if (first.s == "SORT" || first.s == "SORT_DESC" || first.s == "SORT_BY")
                    && s2.s == "FILTER" && !opt_step_reads_key(s2) && opt_keys_renumbered_by(third)
                {
                    let sort_info = get_opt_sort_info(first);
                    let filter_info = get_opt_filter_info(s2);
                    let sort_safe = sort_info.key.map_or(true, |k| opt_cannot_raise(k, &sort_info.binder, logical));
                    let filter_safe = logical || filter_info.predicate.map_or(true, |p| opt_cannot_raise(p, &filter_info.binder, false));
                    if sort_safe && filter_safe {
                        next.push(s2.clone());
                        next.push(first.clone());
                        i += 2;
                        changed = true;
                        continue;
                    }
                }
            }

            // SELECT_COLS + FILTER
            if let Some(s2) = second {
                if first.s == "SELECT_COLS" && s2.s == "FILTER" {
                    let info = get_opt_filter_info(s2);
                    if let Some(pred) = info.predicate {
                        let refs = opt_field_refs(pred, &info.binder);
                        let fields = opt_select_fields(first);
                        let field_set: HashSet<String> = fields.into_iter().collect();
                        let all_in_fields = !refs.is_empty() && refs.iter().all(|r| field_set.contains(r));
                        if info.valid && all_in_fields && !opt_reads_row_or_key(pred, &info.binder)
                            && opt_keys_renumbered_by(third)
                        {
                            next.push(s2.clone());
                            next.push(first.clone());
                            i += 2;
                            changed = true;
                            continue;
                        }
                    }
                }
            }

            // MAP + SORT...
            if let Some(s2) = second {
                if first.s == "MAP"
                    && (s2.s == "TOP" || s2.s == "TOP_DESC" || s2.s == "TOP_BY"
                        || s2.s == "SORT" || s2.s == "SORT_DESC" || s2.s == "SORT_BY")
                    && opt_map_has_computed(first)
                {
                    let sort = get_opt_sort_info(s2);
                    if let Some(key) = sort.key {
                        let refs = opt_field_refs(key, &sort.binder);
                        let passes = opt_map_passthroughs(first);
                        let pass_set: HashSet<String> = passes.into_iter().collect();
                        let all_in_pass = !refs.is_empty() && refs.iter().all(|r| pass_set.contains(r));
                        if all_in_pass && !opt_reads_row_or_key(key, &sort.binder)
                            && opt_map_cannot_raise(first, logical) && opt_cannot_raise(key, &sort.binder, logical)
                        {
                            next.push(s2.clone());
                            next.push(first.clone());
                            i += 2;
                            changed = true;
                            continue;
                        }
                    }
                }
            }

            // FILTER + FILTER
            if let Some(s2) = second {
                if first.s == "FILTER" && s2.s == "FILTER" {
                    let left = get_opt_filter_info(first);
                    let right = get_opt_filter_info(s2);
                    if left.valid && right.valid {
                        if let (Some(l_pred), Some(r_pred)) = (left.predicate, right.predicate) {
                            if opt_filter_predicate_cannot_raise(r_pred, &right.binder, logical) {
                                let right_pred = if !left.binder.eq_ignore_ascii_case(&right.binder) {
                                    opt_rename_var(r_pred, &right.binder, &left.binder)
                                } else {
                                    r_pred.clone()
                                };
                                let mut combined = Node::new(NodeType::Bin, l_pred.pos);
                                combined.s = "AND".to_string();
                                combined.l = Some(Box::new(l_pred.clone()));
                                combined.r = Some(Box::new(right_pred));

                                let mut merged = first.clone();
                                merged.pos = s2.pos;
                                if left.explicit_binder {
                                    merged.items = vec![first.items[0].clone(), first.items[1].clone(), combined];
                                } else {
                                    merged.items = vec![first.items[0].clone(), combined];
                                }
                                next.push(merged);
                                i += 2;
                                changed = true;
                                continue;
                            }
                        }
                    }
                }
            }

            // DISTINCT/DEDUPE + DISTINCT/DEDUPE
            if let Some(s2) = second {
                if (first.s == "DISTINCT" || first.s == "DEDUPE")
                    && (s2.s == "DISTINCT" || s2.s == "DEDUPE")
                {
                    let mut kept = first.clone();
                    kept.pos = s2.pos;
                    next.push(kept);
                    i += 2;
                    changed = true;
                    continue;
                }
            }

            // Drop FILTER(TRUE)
            let filter = get_opt_filter_info(first);
            if first.s == "FILTER" && filter.valid {
                if let Some(pred) = filter.predicate {
                    if pred.t == NodeType::Bool && pred.b && (!next.is_empty() || i > 0 || opt_source_is_list(source)) {
                        if i == current.len() - 1 {
                            if let Some(prev) = next.last_mut() {
                                prev.pos = first.pos;
                                i += 1;
                                changed = true;
                                continue;
                            }
                        } else {
                            i += 1;
                            changed = true;
                            continue;
                        }
                    }
                }
            }

            next.push(first.clone());
            i += 1;
        }
        current = next;
    }
    current
}

// This proof concerns mutation, not errors. FILTER still completes and checks
// every selected row's copy depth before MAP starts, preserving error order.
fn read_only_expression(node: &Node) -> bool {
    if node.t == NodeType::Assign { return false; }
    if node.t == NodeType::Call && !matches!(node.s.as_str(),
        "RECORD" | "LIST" | "IF" | "COND" | "COALESCE" | "ABS" | "SIGN" |
        "ROUND" | "CEIL" | "FLOOR" | "TRUNC" | "POWER" | "MIN" | "MAX") {
        return false;
    }
    node.l.as_ref().map_or(true, |n| read_only_expression(n))
        && node.r.as_ref().map_or(true, |n| read_only_expression(n))
        && node.items.iter().all(read_only_expression)
}

fn opt_inmemory_steps(source: &Node, steps: Vec<Node>) -> Vec<Node> {
    let steps = opt_logical_steps(source, steps, false);
    let len = steps.len();
    let mut rewritten = Vec::with_capacity(len);
    for i in 0..len {
        let mut cp = steps[i].clone();
        if cp.s == "FILTER" && !cp.items.is_empty() {
            let last_idx = cp.items.len() - 1;
            let next_step = if i + 1 < len { Some(&steps[i + 1]) } else { None };
            cp.items[last_idx].keys_unobserved = opt_keys_renumbered_by(next_step);
            cp.borrowed_filter = read_only_expression(&cp.items[last_idx])
                && next_step.map_or(false, |next| matches!(next.s.as_str(), "MAP" | "FILTER")
                    && next.items.last().map_or(false, read_only_expression));
        }
        rewritten.push(cp);
    }
    rewritten
}

pub fn opt_tree(node: &Node, physical: bool, depth: usize, fold: bool, in_math: bool) -> Node {
    if depth > MAX_DEPTH {
        return node.clone();
    }

    if node.t == NodeType::Call && is_pipeline_op(&node.s) && !node.items.is_empty() {
        let (source, steps) = unwind_pipeline(node);
        let optimized_source = opt_tree(source, physical, depth + 1, fold, false);
        let mut optimized_steps = Vec::with_capacity(steps.len());
        for step in steps {
            // Rewrites inspect stage arguments, never the old input. Reconnect
            // the optimized source only after all stage rewrites complete.
            let mut cp = clone_step_with_source(
                step,
                Node::new(NodeType::Null, step.items[0].pos),
            );
            for i in 1..cp.items.len() {
                let fold_arg = fold && opt_step_arg_folds(&step, i);
                cp.items[i] = opt_tree(&cp.items[i], physical, depth + 1, fold_arg, false);
            }
            optimized_steps.push(cp);
        }
        let final_steps = opt_logical_steps(&optimized_source, optimized_steps, !physical);
        let final_steps = if physical {
            opt_inmemory_steps(&optimized_source, final_steps)
        } else {
            final_steps
        };
        return build_pipeline(&optimized_source, &final_steps);
    }

    let is_curr_math = is_math_op(node);
    let next_in_math = is_curr_math;

    let mut cp = node.clone();
    if cp.t != NodeType::Assign {
        if let Some(ref l) = cp.l {
            cp.l = Some(Box::new(opt_tree(l, physical, depth + 1, fold, next_in_math)));
        }
    }
    if let Some(ref r) = cp.r {
        cp.r = Some(Box::new(opt_tree(r, physical, depth + 1, fold, next_in_math)));
    }
    for (i, item) in cp.items.iter_mut().enumerate() {
        let fold_arg = fold && opt_step_arg_folds(node, i);
        *item = opt_tree(item, physical, depth + 1, fold_arg, next_in_math);
    }

    let folded = if fold { opt_fold(&cp) } else { cp };

    if physical && !in_math && is_math_op(&folded) {
        if let Some(plan) = compile_math_plan(&folded) {
            let mut cp_plan = folded;
            cp_plan.math_plan = Some(plan);
            return cp_plan;
        }
    }

    folded
}

pub fn optimize_ast(ast: &Node) -> Node {
    if opt_exceeds_depth(ast, 1) {
        return ast.clone();
    }
    opt_tree(ast, true, 1, true, false)
}

pub fn optimize_ast_logical(ast: &Node) -> Node {
    opt_tree(ast, false, 1, true, false)
}

pub fn optimize_ast_in_memory(ast: &Node) -> Node {
    opt_tree(ast, true, 1, true, false)
}
