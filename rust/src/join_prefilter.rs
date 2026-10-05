use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use crate::ast::{Node, NodeType};
use crate::value::{Kind, Value};

#[derive(Clone, Debug)]
pub struct JoinTotalReq {
    pub name: String,
    pub numeric: bool,
}

#[derive(Clone, Debug)]
pub struct JoinConjunct {
    /// The conjunct's identity: its address in the FILTER's program AST
    /// (Go keys its report by the `*Node`). Copies keep it.
    pub id: usize,
    pub node: Node,
    pub fields: HashSet<String>,
    pub field_only: bool,
    pub total: Vec<JoinTotalReq>,
    pub has_total: bool,
    pub binder: String,
}

#[derive(Clone, Debug)]
pub struct JoinStage {
    pub binder: String,
    pub conjuncts: Vec<JoinConjunct>,
    pub above: usize,
}

#[derive(Clone, Debug)]
pub struct JoinSideFacts {
    pub val: Value,
    pub keys: HashSet<String>,
    pub nullable: bool,
    pub names: HashSet<String>,
    pub first: HashSet<String>,
    /// Per-field facts, computed on demand and cached. Shared (through
    /// `Rc`) by every join below that received this side, as Go shares it.
    pub facts: RefCell<HashMap<String, bool>>,
}

#[derive(Clone, Debug)]
pub struct JoinObligation {
    pub key: Node,
    pub row_names: HashSet<String>,
    pub outer: usize,
}

#[derive(Clone, Debug)]
pub struct JoinPrefilter {
    pub stages: Vec<JoinStage>,
    pub deep: bool,
    pub above: Vec<Rc<JoinSideFacts>>,
    pub obligations: Vec<JoinObligation>,
}

#[derive(Clone, Debug)]
pub struct JoinReport {
    pub applied: HashSet<usize>,
    pub errored: bool,
    pub dropped: bool,
}

impl JoinReport {
    pub fn new() -> Self {
        Self {
            applied: HashSet::new(),
            errored: false,
            dropped: false,
        }
    }
}

pub struct StageStop {
    pub stage: usize,
    pub conjunct: usize,
}

pub struct JoinApplied<'a> {
    pub conjunct: &'a JoinConjunct,
    pub stage: &'a JoinStage,
    pub right: bool,
}

pub fn join_pure_source(node: &Node) -> bool {
    match node.t {
        NodeType::Var | NodeType::Num | NodeType::Text | NodeType::Bool | NodeType::Null => true,
        NodeType::Index | NodeType::Bin => {
            node.l.as_ref().is_none_or(|l| join_pure_source(l))
                && node.r.as_ref().is_none_or(|r| join_pure_source(r))
        }
        NodeType::Un => node.l.as_ref().is_none_or(|l| join_pure_source(l)),
        NodeType::List => node.items.iter().all(join_pure_source),
        NodeType::Call => {
            // A host's own function may do anything (JS, Python): only the
            // shipped builtins are pure.
            if node.s == "ABORT" {
                return false;
            }
            let spec = node.spec.clone().or_else(|| crate::builtins::lookup_spec(&node.s));
            match spec {
                Some(spec) if matches!(spec.func, crate::builtins::SpecFn::Native(_)) => {}
                _ => return false,
            }
            node.items.iter().all(join_pure_source)
        }
        _ => false,
    }
}

fn is_text_compare_op(op: &str) -> bool {
    crate::ops::is_text_comparison(op)
}

fn is_num_compare_op(op: &str) -> bool {
    crate::ops::is_numeric_comparison(op)
}

pub fn leading_field_conjuncts(body: &Node, binder: &str) -> Vec<JoinConjunct> {
    let mut conjuncts = Vec::new();
    let mut curr = Some(body);
    while let Some(c) = curr {
        if c.t == NodeType::Bin && c.s == "AND" {
            if let Some(ref r) = c.r {
                conjuncts.push(r.as_ref());
            }
            curr = c.l.as_deref();
        } else {
            conjuncts.push(c);
            break;
        }
    }
    conjuncts.reverse();

    let bare_read = |n: &Node| -> bool {
        if n.t == NodeType::Index {
            if let (Some(ref l), Some(ref r)) = (&n.l, &n.r) {
                return l.t == NodeType::Var && l.s == binder && r.t == NodeType::Text;
            }
        }
        false
    };

    let mut out = Vec::new();
    for c in conjuncts {
        let mut fields = HashSet::new();

        fn reads_only_fields<'a>(
            n: &'a Node,
            fields: &mut HashSet<String>,
            bare_read: &impl Fn(&'a Node) -> bool,
        ) -> bool {
            match n.t {
                NodeType::Index => {
                    if bare_read(n) {
                        fields.insert(n.r.as_ref().unwrap().s.to_ascii_uppercase());
                        return true;
                    }
                    if let Some(ref l) = n.l {
                        if l.t == NodeType::Index {
                            return reads_only_fields(l, fields, bare_read)
                                && n.r.as_ref().is_none_or(|r| reads_only_fields(r, fields, bare_read));
                        }
                    }
                    false
                }
                NodeType::Num | NodeType::Text | NodeType::Bool => true,
                NodeType::Bin => {
                    n.l.as_ref().is_none_or(|l| reads_only_fields(l, fields, bare_read))
                        && n.r.as_ref().is_none_or(|r| reads_only_fields(r, fields, bare_read))
                }
                NodeType::Un => {
                    n.l.as_ref().is_none_or(|l| reads_only_fields(l, fields, bare_read))
                }
                _ => false,
            }
        }

        let field_only = reads_only_fields(c, &mut fields, &bare_read) && !fields.is_empty();
        let final_fields = if field_only { fields } else { HashSet::new() };

        let mut total = Vec::new();
        let mut has_total = false;

        if c.t == NodeType::Bin && (is_text_compare_op(&c.s) || is_num_compare_op(&c.s)) {
            let numeric = is_num_compare_op(&c.s);
            has_total = true;
            let operands = [c.l.as_deref(), c.r.as_deref()];
            for op in operands.into_iter().flatten() {
                if op.t == NodeType::Num || op.t == NodeType::Text {
                    if numeric && op.t != NodeType::Num {
                        has_total = false;
                        break;
                    }
                    continue;
                }
                if !bare_read(op) {
                    has_total = false;
                    break;
                }
                total.push(JoinTotalReq {
                    name: op.r.as_ref().unwrap().s.clone(),
                    numeric,
                });
            }
            if !has_total {
                total.clear();
            }
        }

        out.push(JoinConjunct {
            id: c as *const Node as usize,
            node: c.clone(),
            fields: final_fields,
            field_only,
            total,
            has_total,
            binder: binder.to_string(),
        });
    }
    out
}

pub fn join_read_self(node: &Node, names: &HashSet<String>, binder: &str) -> Node {
    if node.t == NodeType::Index {
        if let (Some(ref l), Some(ref r)) = (&node.l, &node.r) {
            if l.t == NodeType::Var
                && l.s == binder
                && r.t == NodeType::Text
                && names.contains(&r.s.to_ascii_uppercase())
            {
                let mut v = Node::new(NodeType::Var, node.pos);
                v.s = binder.to_string();
                return v;
            }
        }
    }
    let mut cp = node.clone();
    cp.math_plan = None;
    if let Some(ref l) = node.l {
        cp.l = Some(Box::new(join_read_self(l, names, binder)));
    }
    if let Some(ref r) = node.r {
        cp.r = Some(Box::new(join_read_self(r, names, binder)));
    }
    for item in &mut cp.items {
        *item = join_read_self(item, names, binder);
    }
    cp
}

pub fn join_row_keys(val: &Value, bound: &[String]) -> HashSet<String> {
    let mut keys = HashSet::new();
    for b in bound {
        keys.insert(b.to_ascii_uppercase());
    }
    // Rows of a dense list mostly share one record shape: read its keys once.
    let mut shapes: HashSet<usize> = HashSet::new();
    // Read in place: copying the row handles out first cost a vector of them
    // per join side.
    val.all_children(|item| {
        let inner = item.0.borrow();
        if let Some(ref shape) = inner.shape {
            if shapes.insert(std::sync::Arc::as_ptr(shape) as usize) {
                for k in shape.keys.iter() {
                    keys.insert(k.to_ascii_uppercase());
                }
            }
        } else {
            drop(inner);
            for k in item.keys() {
                keys.insert(k.to_ascii_uppercase());
            }
        }
        true
    });
    keys
}

pub fn new_join_side_facts(
    val: Value,
    keys: HashSet<String>,
    nullable: bool,
    bound: &[String],
) -> JoinSideFacts {
    let mut names = HashSet::new();
    for b in bound {
        if b == "_1" || b == "_2" || b == "_" {
            continue;
        }
        names.insert(b.clone());
        names.insert(b.to_ascii_lowercase());
    }
    let mut first = HashSet::new();
    if let Some(row) = val.first_child() {
        for k in row.keys() {
            first.insert(k.to_ascii_uppercase());
        }
    }
    JoinSideFacts {
        val,
        keys,
        nullable,
        names,
        first,
        facts: RefCell::new(HashMap::new()),
    }
}

pub fn join_side_total(side: &JoinSideFacts, name: &str, numeric: bool) -> bool {
    let upper = name.to_ascii_uppercase();
    if !side.first.contains(&upper) || side.nullable {
        return false;
    }
    let id = if numeric {
        format!("N:{}", name)
    } else {
        format!("T:{}", name)
    };
    if let Some(&v) = side.facts.borrow().get(&id) {
        return v;
    }
    let ok = side.val.all_children(|row| match row.get(name) {
        Some(ref val) if val.kind() == Kind::Text => !numeric || val.looks_numeric(),
        _ => false,
    });
    side.facts.borrow_mut().insert(id, ok);
    ok
}

pub fn join_side_present(side: &JoinSideFacts, name: &str) -> bool {
    let id = format!("P:{}", name);
    if let Some(&v) = side.facts.borrow().get(&id) {
        return v;
    }
    let ok = side.val.size() > 0 && side.val.all_children(|row| row.has(name));
    side.facts.borrow_mut().insert(id, ok);
    ok
}

pub fn join_side_any(side: &JoinSideFacts, name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    if !side.first.contains(&upper) || side.nullable {
        return false;
    }
    let id = format!("A:{}", name);
    if let Some(&v) = side.facts.borrow().get(&id) {
        return v;
    }
    let ok = side.val.all_children(|row| match row.get(name) {
        Some(ref v) if !v.is_null() => !crate::join_plan::is_left_nested(v),
        _ => false,
    });
    side.facts.borrow_mut().insert(id, ok);
    ok
}

pub fn join_keys_safe(
    obligations: &[JoinObligation],
    left: &JoinSideFacts,
    right: &JoinSideFacts,
    above: &[Rc<JoinSideFacts>],
) -> bool {
    for ob in obligations {
        let n_below = if above.len() >= ob.outer {
            above.len() - ob.outer
        } else {
            0
        };
        let key = &ob.key;
        if key.t != NodeType::Index || key.r.is_none() || key.l.is_none() {
            return false;
        }
        let r_node = key.r.as_ref().unwrap();
        if r_node.t != NodeType::Text {
            return false;
        }
        let field = &r_node.s;
        let obj = key.l.as_ref().unwrap();

        if obj.t == NodeType::Index {
            if let (Some(ref obj_l), Some(ref obj_r)) = (&obj.l, &obj.r) {
                if obj_l.t == NodeType::Var && ob.row_names.contains(&obj_l.s) && obj_r.t == NodeType::Text {
                    let member = &obj_r.s;
                    if left.names.contains(member) {
                        if !join_side_present(left, field) {
                            return false;
                        }
                        continue;
                    }
                    let mut side: Option<&JoinSideFacts> = None;
                    if right.names.contains(member) {
                        side = Some(right);
                    }
                    let mut i = 0;
                    while side.is_none() && i < n_below {
                        if above[i].names.contains(member) {
                            side = Some(&above[i]);
                        }
                        i += 1;
                    }
                    if let Some(side) = side {
                        if !join_side_present(side, field) {
                            return false;
                        }
                        continue;
                    }
                    let all_present = left.val.all_children(|item| {
                        matches!(item.get(member), Some(ref inner) if inner.get(field).is_some())
                    });
                    if !all_present {
                        return false;
                    }
                    continue;
                }
            }
        }

        if obj.t == NodeType::Var && ob.row_names.contains(&obj.s) {
            let upper = field.to_ascii_uppercase();
            let mut owner: Option<&JoinSideFacts> = None;
            let mut owners = 0;
            let consider = |s: &JoinSideFacts| s.keys.contains(&upper);
            if consider(left) {
                owner = Some(left);
                owners += 1;
            }
            if consider(right) {
                owner = Some(right);
                owners += 1;
            }
            for s in &above[..n_below] {
                if consider(s) {
                    owner = Some(s);
                    owners += 1;
                }
            }
            if owners != 1 || !join_side_any(owner.unwrap(), field) {
                return false;
            }
            continue;
        }

        return false;
    }
    true
}

pub fn join_totality(
    reqs: &[JoinTotalReq],
    left: Option<&JoinSideFacts>,
    right: &JoinSideFacts,
    above: &[Rc<JoinSideFacts>],
) -> bool {
    for req in reqs {
        let key = req.name.to_ascii_uppercase();
        let mut owner: Option<&JoinSideFacts> = None;
        let mut owners = 0;
        if let Some(l) = left {
            if l.keys.contains(&key) {
                owner = Some(l);
                owners += 1;
            }
        }
        if right.keys.contains(&key) {
            owner = Some(right);
            owners += 1;
        }
        for s in above {
            if s.keys.contains(&key) {
                owner = Some(s);
                owners += 1;
            }
        }
        if owners != 1 || !join_side_total(owner.unwrap(), &req.name, req.numeric) {
            return false;
        }
    }
    true
}

pub fn join_stage_walk<'a>(
    stages: &'a [JoinStage],
    owned_here: &impl Fn(&HashSet<String>, &JoinStage) -> bool,
    total_here: &impl Fn(&[JoinTotalReq], &JoinStage) -> bool,
    right_here: &impl Fn(&HashSet<String>, &JoinStage) -> bool,
) -> (Vec<JoinApplied<'a>>, Option<StageStop>) {
    let mut applied = Vec::new();
    for (si, stage) in stages.iter().enumerate() {
        for (ci, c) in stage.conjuncts.iter().enumerate() {
            if c.field_only && owned_here(&c.fields, stage) {
                applied.push(JoinApplied {
                    conjunct: c,
                    stage,
                    right: false,
                });
                continue;
            }
            if c.field_only && right_here(&c.fields, stage) {
                applied.push(JoinApplied {
                    conjunct: c,
                    stage,
                    right: true,
                });
                continue;
            }
            if c.has_total && total_here(&c.total, stage) {
                continue;
            }
            return (applied, Some(StageStop { stage: si, conjunct: ci }));
        }
    }
    (applied, None)
}

pub fn join_truncate_stages(stages: &[JoinStage], stop: Option<&StageStop>) -> Vec<JoinStage> {
    match stop {
        None => stages.to_vec(),
        Some(s) => {
            let mut out = Vec::with_capacity(s.stage + 1);
            out.extend_from_slice(&stages[..s.stage]);
            if s.conjunct > 0 {
                let partial = JoinStage {
                    binder: stages[s.stage].binder.clone(),
                    above: stages[s.stage].above,
                    conjuncts: stages[s.stage].conjuncts[..s.conjunct].to_vec(),
                };
                out.push(partial);
            }
            out
        }
    }
}
