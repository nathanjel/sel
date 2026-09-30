use std::collections::HashMap;

use crate::manifest::lookup_builtin;
use crate::program::Program;
use crate::utf8::{Pos, SelError};
use crate::value::Value;
use crate::sql::binding::{BindingKind, Bindings};
use crate::sql::errors::{refuse, SqlError};
use crate::sql::node::{SNode, SNodeType};

pub fn scope(bindings: Option<&Bindings>) -> (HashMap<String, bool>, Value) {
    let mut names = HashMap::new();
    let root = Value::null();
    if let Some(b_list) = bindings {
        for name in b_list.names() {
            if let Ok(b) = b_list.get(&name, Pos::default()) {
                if b.kind != BindingKind::Value {
                    continue;
                }
                if let Some(ref v) = b.val {
                    if !v.is_none() && v.size() == 0 {
                        names.insert(name.clone(), true);
                        let _ = root.set(&name, v.clone(), Pos::default());
                    }
                }
            }
        }
    }
    (names, root)
}

pub fn is_binder_name(node: Option<&SNode>) -> bool {
    matches!(node, Some(n) if n.t == SNodeType::Var && !n.grouped)
}

#[derive(Clone, Debug, Default)]
pub struct NeededFields {
    pub all: bool,
    pub fields: HashMap<String, bool>,
}

impl NeededFields {
    pub fn new() -> Self {
        Self {
            all: false,
            fields: HashMap::new(),
        }
    }

    pub fn is_needed(&self) -> bool {
        self.all || !self.fields.is_empty()
    }
}

pub fn text_literal_results(node: Option<&SNode>, depth: usize) -> bool {
    let n = match node {
        Some(n) => n,
        None => return false,
    };
    if depth >= 180 || n.t != SNodeType::Call || (n.str != "IF" && n.str != "COND") {
        return false;
    }
    let args = &n.kids;
    let mut results: Vec<&SNode> = Vec::new();
    if n.str == "IF" {
        if args.len() < 2 {
            return false;
        }
        for a in &args[1..] {
            results.push(a);
        }
    } else {
        if args.len() < 3 || args.len() % 2 == 0 {
            return false;
        }
        let mut i = 1;
        while i < args.len() {
            results.push(&args[i]);
            i += 2;
        }
        results.push(&args[args.len() - 1]);
    }

    for r in results {
        if r.t != SNodeType::Text && !text_literal_results(Some(r), depth + 1) {
            return false;
        }
    }
    true
}

pub fn identity_projection(node: Option<&SNode>, depth: usize) -> bool {
    let n = match node {
        Some(n) => n,
        None => return false,
    };
    if depth >= 180 {
        return false;
    }
    match n.t {
        SNodeType::Var | SNodeType::Num | SNodeType::Text | SNodeType::Bool | SNodeType::Null => true,
        SNodeType::Index => {
            identity_projection(n.obj(), depth + 1) && identity_projection(n.idx(), depth + 1)
        }
        SNodeType::Call => {
            let name = n.str.as_str();
            if name == "COUNT" || name == "LEN" || name == "BLEN" || name == "CANON" {
                return true;
            }
            if text_literal_results(Some(n), depth) {
                return true;
            }
            if name == "RECORD" {
                if n.kids.len() % 2 != 0 {
                    return false;
                }
                let mut i = 1;
                while i < n.kids.len() {
                    if !identity_projection(n.kids.get(i), depth + 1) {
                        return false;
                    }
                    i += 2;
                }
                return true;
            }
            false
        }
        _ => false,
    }
}

pub fn identity_inputs(node: Option<&SNode>, depth: usize) -> NeededFields {
    let n = match node {
        Some(n) => n,
        None => return NeededFields { all: true, fields: HashMap::new() },
    };
    if depth >= 180 {
        return NeededFields { all: true, fields: HashMap::new() };
    }
    match n.t {
        SNodeType::Num | SNodeType::Text | SNodeType::Bool | SNodeType::Null => NeededFields::new(),
        SNodeType::Var => {
            if n.str == "_K" {
                NeededFields::new()
            } else {
                NeededFields { all: true, fields: HashMap::new() }
            }
        }
        SNodeType::Index => {
            let idx = n.idx();
            if idx.map_or(true, |i| i.t != SNodeType::Text) {
                return NeededFields { all: true, fields: HashMap::new() };
            }
            if let Some(obj) = n.obj() {
                if obj.t == SNodeType::Var {
                    let mut nf = NeededFields::new();
                    nf.fields.insert(idx.unwrap().str.clone(), true);
                    return nf;
                }
            }
            identity_inputs(n.obj(), depth + 1)
        }
        SNodeType::Call => {
            let name = n.str.as_str();
            if name == "COUNT" || name == "LEN" || name == "BLEN" || name == "CANON" {
                return NeededFields::new();
            }
            if text_literal_results(Some(n), depth) {
                return NeededFields::new();
            }
            if name == "RECORD" || name == "LIST" {
                let items: Vec<&SNode> = if name == "RECORD" {
                    let mut v = Vec::new();
                    let mut i = 1;
                    while i < n.kids.len() {
                        v.push(&n.kids[i]);
                        i += 2;
                    }
                    v
                } else {
                    n.kids.iter().collect()
                };

                let mut out = NeededFields::new();
                for item in items {
                    let f = identity_inputs(Some(item), depth + 1);
                    if f.all {
                        return NeededFields { all: true, fields: HashMap::new() };
                    }
                    for k in f.fields.into_keys() {
                        out.fields.insert(k, true);
                    }
                }
                return out;
            }
            NeededFields { all: true, fields: HashMap::new() }
        }
        SNodeType::List => {
            let mut out = NeededFields::new();
            for item in &n.kids {
                let f = identity_inputs(Some(item), depth + 1);
                if f.all {
                    return NeededFields { all: true, fields: HashMap::new() };
                }
                for k in f.fields.into_keys() {
                    out.fields.insert(k, true);
                }
            }
            out
        }
        _ => NeededFields { all: true, fields: HashMap::new() },
    }
}

pub fn identity_loss_before_grouping(mut node: Option<&SNode>, mut needed: NeededFields) -> bool {
    while let Some(n) = node {
        if n.t != SNodeType::Call || n.kids.is_empty() {
            break;
        }
        let name = n.str.as_str();
        if needed.is_needed() && (name == "MAP" || (name == "BUCKET" && n.kids.len() > 2)) {
            let body = &n.kids[n.kids.len() - 1];
            let mut values: Vec<&SNode> = vec![body];
            if !needed.all && body.t == SNodeType::Call && body.str == "RECORD" {
                let mut found = HashMap::new();
                values.clear();
                let mut i = 0;
                while i + 1 < body.kids.len() {
                    let k = &body.kids[i];
                    if k.t == SNodeType::Text && needed.fields.contains_key(&k.str) {
                        found.insert(k.str.clone(), true);
                        values.push(&body.kids[i + 1]);
                    }
                    i += 2;
                }
                for k in needed.fields.keys() {
                    if !found.contains_key(k) {
                        return true;
                    }
                }
            }
            for v in &values {
                if !identity_projection(Some(v), 0) {
                    return true;
                }
            }
            let mut next_needed = NeededFields::new();
            for v in &values {
                let f = identity_inputs(Some(v), 0);
                if f.all {
                    next_needed.all = true;
                } else if !next_needed.all {
                    for k in f.fields.into_keys() {
                        next_needed.fields.insert(k, true);
                    }
                }
            }
            needed = next_needed;
        }

        if name == "BUCKET" {
            let arg_idx = if n.kids.len() == 4 { 2 } else { 1 };
            needed = identity_inputs(n.kids.get(arg_idx), 0);
        }
        if name == "DISTINCT" || name == "DEDUPE" {
            needed = NeededFields { all: true, fields: HashMap::new() };
        }
        if needed.is_needed()
            && !needed.all
            && (name == "LINK" || name == "LINK_LEFT")
            && n.kids.len() > 1
            && n.kids[1].t == SNodeType::Var
        {
            let mut right = n.kids[1].str.clone();
            if n.kids.len() == 5 {
                right = n.kids[3].str.clone();
            }
            let right_upper = right.to_ascii_uppercase();
            let mut filtered = HashMap::new();
            for k in needed.fields.into_keys() {
                if k.to_ascii_uppercase() != right_upper {
                    filtered.insert(k, true);
                }
            }
            needed = NeededFields { all: false, fields: filtered };
        }

        node = n.kids.get(0);
    }
    false
}

pub fn is_constant(n: Option<&SNode>, bound: Option<&HashMap<String, bool>>) -> bool {
    let node = match n {
        Some(node) => node,
        None => return false,
    };
    match node.t {
        SNodeType::Num | SNodeType::Text | SNodeType::Bool => true,
        SNodeType::Var => bound.map_or(false, |b| *b.get(&node.str).unwrap_or(&false)),
        SNodeType::Un => is_constant(node.l(), bound),
        SNodeType::Bin => is_constant(node.l(), bound) && is_constant(node.r(), bound),
        SNodeType::Index => is_constant(node.obj(), bound) && is_constant(node.idx(), bound),
        SNodeType::CList => false,
        SNodeType::List => {
            for item in &node.kids {
                if !is_constant(Some(item), bound) {
                    return false;
                }
            }
            true
        }
        SNodeType::Call => constant_call(node, bound),
        _ => false,
    }
}

fn constant_call(n: &SNode, bound: Option<&HashMap<String, bool>>) -> bool {
    let args = &n.kids;
    let binds = n.spec.as_ref().map_or(false, |s| s.binds)
        || lookup_builtin(&n.str).map_or(false, |e| e.binds);

    if !binds {
        for a in args {
            if !is_constant(Some(a), bound) {
                return false;
            }
        }
        return true;
    }

    if args.is_empty() || !is_constant(args.get(0), bound) {
        return false;
    }

    let mut inner = bound.cloned().unwrap_or_default();
    let body = if args.len() >= 3 {
        if !is_binder_name(args.get(1)) {
            return false;
        }
        inner.insert(args[1].str.clone(), true);
        2
    } else {
        inner.insert("_".to_string(), true);
        1
    };

    for a in &args[body..] {
        if !is_constant(Some(a), Some(&inner)) {
            return false;
        }
    }
    true
}

pub fn validate(n: &SNode, root: Option<&Value>) -> Result<(), SqlError> {
    let node = match n.to_node() {
        Some(node) => node,
        None => return Ok(()),
    };
    let mut prog = Program::new("", node);
    let null_val = Value::null();
    let r = root.unwrap_or(&null_val);
    if let Err(err) = prog.run(Some(r.clone())) {
        return refuse_as_sel(&err, n);
    }
    Ok(())
}

pub fn require_numeric(n: &SNode, root: Option<&Value>) -> Result<(), SqlError> {
    let node = match n.to_node() {
        Some(node) => node,
        None => return Ok(()),
    };
    let mut prog = Program::new("", node);
    let null_val = Value::null();
    let r = root.unwrap_or(&null_val);
    let val = match prog.run(Some(r.clone())) {
        Ok(v) => v,
        Err(err) => return refuse_as_sel(&err, n),
    };
    if let Err(err) = val.as_decimal(n.pos) {
        return refuse_as_sel(&err, n);
    }
    Ok(())
}

pub fn constant_scale(n: &SNode, root: Option<&Value>) -> Result<usize, SqlError> {
    let node = match n.to_node() {
        Some(node) => node,
        None => return Ok(0),
    };
    let mut prog = Program::new("", node);
    let null_val = Value::null();
    let r = root.unwrap_or(&null_val);
    let val = match prog.run(Some(r.clone())) {
        Ok(v) => v,
        Err(err) => return refuse_as_sel(&err, n),
    };
    match val.as_decimal(n.pos) {
        Ok(dec) => Ok(dec.scale as usize),
        Err(err) => refuse_as_sel(&err, n),
    }
}

pub fn refuse_as_sel<T>(err: &SelError, n: &SNode) -> Result<T, SqlError> {
    let mut pos = n.pos;
    if err.pos.line > 0 {
        pos = err.pos;
    }
    // Depth is the translation's limit, not SEL judging the expression invalid: a
    // constant chain past the evaluator's cap is the same E_SQL_DEPTH a column
    // chain gets (errors.md).
    if err.code == "E_DEPTH" {
        return refuse(
            "E_SQL_DEPTH",
            format!("this expression nests deeper than SEL will evaluate ({}), so there is nothing to translate", err.message),
            pos,
        );
    }
    refuse(
        "E_SQL_INVALID",
        format!(
            "SEL rejects this expression ({}: {}), so there is nothing to translate; a database would answer something rather than fail",
            err.code, err.message
        ),
        pos,
    )
}
