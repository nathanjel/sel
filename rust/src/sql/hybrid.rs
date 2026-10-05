use std::collections::{HashMap, HashSet};

use crate::ast::{Node, NodeType};
use crate::manifest::{binding_form, builtins::Scope};
use crate::optimizer::{build_pipeline, optimize_ast_logical, unwind_pipeline};
use crate::program::Program;
use crate::sql::binding::{Binding, BindingKind, Bindings};
use crate::sql::constants::scope as const_scope;
use crate::sql::emit::Emit;
use crate::sql::map::{entry, require_target, EntryKind};
use crate::sql::normalise::normalise;
use crate::sql::translator::{Options, Translator};
use crate::sql::types::{Fragment, Mode, Part, SqlKind};
use crate::utf8::{Pos, SelError};
use crate::value::Value;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectedMember {
    pub partition_key: String,
    pub revision_key: String,
}

#[derive(Clone, Debug)]
pub struct HybridPlan {
    pub selected_member: Option<SelectedMember>,
    pub dialect: String,
    pub sql_statement: Option<Fragment>,
    pub sql_prefix_ast: Option<Node>,
    pub continuation_ast: Option<Node>,
    pub continuation_program: Option<Program>,
    pub continuation_source_var: String,
    pub is_hybrid: bool,
    pub pure_sql: bool,
    pub pure_memory: bool,
    pub source_tables: Vec<String>,
}

impl HybridPlan {
    pub fn sql_query(&self) -> Option<&Fragment> {
        self.sql_statement.as_ref()
    }
}

pub fn try_translate_statement(
    program: &Program,
    dialect: &str,
    bindings: &Bindings,
    options: Options,
) -> Option<Fragment> {
    let mut t = Translator::new(dialect, Some(bindings.clone()), options);
    t.translate_statement(program.ast()).ok()
}

fn var_node(name: &str, pos: Pos) -> Node {
    let mut n = Node::new(NodeType::Var, pos);
    n.s = name.to_string();
    n
}

fn text_node(val: &str, pos: Pos) -> Node {
    let mut n = Node::new(NodeType::Text, pos);
    n.s = val.to_string();
    n
}

fn index_node(obj: Node, idx: Node, pos: Pos) -> Node {
    let mut n = Node::new(NodeType::Index, pos);
    n.l = Some(Box::new(obj));
    n.r = Some(Box::new(idx));
    n
}

fn sql_special_calls(name: &str) -> bool {
    matches!(
        name,
        "IF" | "COND" | "COALESCE" | "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" | "RECORD" | "LIST"
    )
}

/// Whether `node` needs something the dialect cannot express. A call's
/// arguments are walked once, by the call branch; a helper's verdict is
/// remembered in `memo`, so `H1 = H0 + H0` does not walk `H0` twice per
/// level. Both walks were otherwise exponential in nesting.
fn contains_unsupported_sql(
    node: &Node,
    dialect: &str,
    defs: &HashMap<String, Node>,
    seen: &mut HashSet<String>,
    memo: &mut HashMap<String, bool>,
) -> bool {
    if node.t == NodeType::Var {
        if let Some(def) = defs.get(&node.s) {
            if !seen.contains(&node.s) {
                if let Some(&verdict) = memo.get(&node.s) {
                    return verdict;
                }
                seen.insert(node.s.clone());
                let res = contains_unsupported_sql(def, dialect, defs, seen, memo);
                seen.remove(&node.s);
                memo.insert(node.s.clone(), res);
                return res;
            }
        }
    }
    if node.t == NodeType::Call {
        if !sql_special_calls(&node.s) {
            let upper = node.s.to_ascii_uppercase();
            let ent = entry(dialect, "funcs", &upper);
            match ent {
                None => return true,
                Some(rec) => {
                    if rec.kind == EntryKind::Refusal {
                        return true;
                    }
                }
            }
        }
        // A call's operands are its items and nothing else: walking on into
        // the generic walk below would visit them again at every level.
        return node.items.iter().any(|item| contains_unsupported_sql(item, dialect, defs, seen, memo));
    }
    if let Some(ref l) = node.l {
        if contains_unsupported_sql(l, dialect, defs, seen, memo) {
            return true;
        }
    }
    if let Some(ref r) = node.r {
        if contains_unsupported_sql(r, dialect, defs, seen, memo) {
            return true;
        }
    }
    node.items.iter().any(|item| contains_unsupported_sql(item, dialect, defs, seen, memo))
}

fn bucket_rows_are_keys(steps: &[Node], count: usize) -> bool {
    let mut open = false;
    for i in 0..count.min(steps.len()) {
        let step = &steps[i];
        if step.s == "BUCKET" {
            if open {
                return true;
            }
            open = step.items.len() == 2;
        } else if open && step.s == "MAP" {
            open = false;
        } else if open && step.s != "FILTER" {
            return true;
        }
    }
    open
}

fn join_rows_lack_binders(steps: &[Node], count: usize) -> bool {
    let mut joined = false;
    for i in 0..count.min(steps.len()) {
        let step = &steps[i];
        if step.s == "LINK" || step.s == "LINK_LEFT" {
            joined = true;
        } else if step.s == "MAP" || step.s == "SELECT_COLS" || step.s == "BUCKET" {
            joined = false;
        }
    }
    joined
}

// Whether an explicit sort's order would not survive a later step in SQL.
// SEL's result is in the order the sort gave it, and a database promises
// nothing about the order of rows once they pass through a derived table
// into a join, a group or a second sort's tie-break: a later sort keeps the
// earlier sort's order among its ties, which is gone once a projection hid
// the earlier key. A LIMIT beside the earlier ORDER BY decides which rows
// survive, not any of this. A prefix that ends before that step is exact
// (docs/internals/sql-translation.md 12.1, "Order"; PHP-C35).
fn order_is_lost(steps: &[Node], count: usize) -> bool {
    const ORDER_SORTS: [&str; 6] = ["SORT", "SORT_DESC", "SORT_BY", "TOP", "TOP_DESC", "TOP_BY"];
    let mut sorted = false;
    let mut projected = false;
    for step in &steps[..count.min(steps.len())] {
        let name = step.s.as_str();
        if ORDER_SORTS.contains(&name) {
            if sorted && projected {
                return true;
            }
            sorted = true;
            projected = false;
        } else if sorted && (name == "MAP" || name == "SELECT_COLS") {
            projected = true;
        } else if sorted && (name == "BUCKET" || name == "LINK" || name == "LINK_LEFT") {
            return true;
        }
    }
    false
}

fn rows_are_not_the_value(steps: &[Node], count: usize) -> bool {
    bucket_rows_are_keys(steps, count) || join_rows_lack_binders(steps, count) || order_is_lost(steps, count)
}

fn physical_source(b: &Binding) -> String {
    let rel = b.relation.as_ref().unwrap();
    if rel.from.is_raw {
        rel.from.raw.clone()
    } else {
        rel.from.table.clone()
    }
}

pub(crate) fn free_names(ast: &Node, bound: &[String], out: &mut Vec<String>) {
    let mut found: HashSet<String> = out.iter().cloned().collect();
    free_names_seen(ast, bound, out, &mut found);
}

// `free_names` for a caller collecting over many trees: `found` mirrors `out`
// and persists between calls, so n helpers cost O(n), not O(n^2).
pub(crate) fn free_names_seen(ast: &Node, bound: &[String], out: &mut Vec<String>, found: &mut HashSet<String>) {
    enum Task<'a> { Visit(&'a Node), Bind(Vec<String>), Restore(usize) }
    let mut scope = bound.to_vec();
    let mut counts = HashMap::<String, usize>::new();
    for name in &scope { *counts.entry(name.clone()).or_default() += 1; }
    let mut pending = vec![Task::Visit(ast)];
    while let Some(task) = pending.pop() {
        let node = match task {
            Task::Bind(names) => {
                for name in names {
                    *counts.entry(name.clone()).or_default() += 1;
                    scope.push(name);
                }
                continue;
            }
            Task::Restore(length) => {
                while scope.len() > length {
                    let name = scope.pop().unwrap();
                    let count = counts.get_mut(&name).unwrap();
                    *count -= 1;
                    if *count == 0 { counts.remove(&name); }
                }
                continue;
            }
            Task::Visit(node) => node,
        };
        if node.t == NodeType::Var {
            if !counts.contains_key(&node.s) && found.insert(node.s.clone()) {
                out.push(node.s.clone());
            }
        } else if node.t == NodeType::Assign {
            if let Some(rhs) = node.r.as_deref() { pending.push(Task::Visit(rhs)); }
        } else if node.t == NodeType::Seq {
            pending.push(Task::Restore(scope.len()));
            for item in node.items.iter().rev() {
                if item.t == NodeType::Assign {
                    if let Some(target) = &item.l {
                        if target.t == NodeType::Var {
                            pending.push(Task::Bind(vec![target.s.clone()]));
                        }
                    }
                }
                pending.push(Task::Visit(item));
            }
        } else if node.t == NodeType::Call {
            let binds = crate::builtins::lookup_spec(&node.s).is_some_and(|s| s.binds);
            let form = binding_form(&node.s, &node.items, binds);
            for (i, item) in node.items.iter().enumerate().rev() {
                match form.as_ref().and_then(|f| f.scopes.get(i)).copied().unwrap_or(Scope::Outer) {
                    Scope::Binder => {},
                    Scope::Inner => {
                        pending.push(Task::Restore(scope.len()));
                        pending.push(Task::Visit(item));
                        if let Some(form) = &form { pending.push(Task::Bind(form.binds.clone())); }
                    }
                    Scope::Outer => pending.push(Task::Visit(item)),
                }
            }
        } else {
            pending.extend(node.items.iter().rev().map(Task::Visit));
            if let Some(rhs) = node.r.as_deref() { pending.push(Task::Visit(rhs)); }
            if let Some(lhs) = node.l.as_deref() { pending.push(Task::Visit(lhs)); }
        }
    }
}

fn source_tables(ast: &Node, bindings: &Bindings) -> Vec<String> {
    let mut names = Vec::new();
    free_names(ast, &[], &mut names);
    let mut tables = Vec::new();
    for name in names {
        if let Ok(b) = bindings.get(&name, ast.pos) {
            if b.kind == BindingKind::Relation {
                let table = physical_source(b);
                if !tables.contains(&table) { tables.push(table); }
            }
        }
    }
    tables
}

fn key_safe_boundary(steps: &[Node], count: usize) -> bool {
    if count == 0 || steps[count - 1].s != "FILTER" { return true; }
    for step in &steps[count..] {
        // Any `_K` in the step's own arguments counts, whatever binds it there: the
        // other hosts' test is this raw scan, and the plan is the same in every host.
        if step.items.iter().skip(1).any(mentions_key) { return false; }
        if step.s != "FILTER" { return true; }
    }
    false
}

fn mentions_key(node: &Node) -> bool {
    if node.t == NodeType::Var {
        return node.s == "_K";
    }
    node.l.as_deref().is_some_and(mentions_key)
        || node.r.as_deref().is_some_and(mentions_key)
        || node.items.iter().any(mentions_key)
}

fn statements(ast: &Node) -> (Vec<&Node>, &Node) {
    if ast.t != NodeType::Seq || ast.items.is_empty() {
        (Vec::new(), ast)
    } else {
        (
            ast.items[..ast.items.len() - 1].iter().collect(),
            &ast.items[ast.items.len() - 1],
        )
    }
}

fn assigned_name(statement: &Node) -> String {
    let mut target = statement.l.as_deref();
    while let Some(t) = target {
        if t.t == NodeType::Index {
            target = t.l.as_deref();
        } else {
            break;
        }
    }
    target.map(|t| t.s.clone()).unwrap_or_default()
}

fn definitions(leading: &[&Node]) -> HashMap<String, Node> {
    let mut defs = HashMap::new();
    for s in leading {
        if s.t == NodeType::Assign {
            if let Some(ref l) = s.l {
                if l.t == NodeType::Var {
                    if let Some(ref r) = s.r {
                        defs.insert(l.s.clone(), (**r).clone());
                    }
                }
            }
        }
    }
    defs
}

fn is_literal_type(t: NodeType) -> bool {
    matches!(
        t,
        NodeType::Num | NodeType::Text | NodeType::Bool | NodeType::Null
    )
}

fn inline_literals(node: &Node, literals: &HashMap<String, Node>, bound: &[String]) -> Node {
    let t = node.t;
    if t == NodeType::Var {
        if bound.contains(&node.s) || !literals.contains_key(&node.s) {
            return node.clone();
        }
        let mut cp = literals[&node.s].clone();
        cp.pos = node.pos;
        return cp;
    }
    if is_literal_type(t) {
        return node.clone();
    }
    if t == NodeType::Un {
        let mut cp = node.clone();
        if let Some(ref l) = node.l {
            cp.l = Some(Box::new(inline_literals(l, literals, bound)));
        }
        return cp;
    }
    if t == NodeType::Bin || t == NodeType::Index {
        let mut cp = node.clone();
        if let Some(ref l) = node.l {
            cp.l = Some(Box::new(inline_literals(l, literals, bound)));
        }
        if let Some(ref r) = node.r {
            cp.r = Some(Box::new(inline_literals(r, literals, bound)));
        }
        return cp;
    }
    if t == NodeType::List || t == NodeType::Seq {
        let mut cp = node.clone();
        cp.items = node
            .items
            .iter()
            .map(|item| inline_literals(item, literals, bound))
            .collect();
        return cp;
    }
    if t == NodeType::Assign {
        let mut cp = node.clone();
        if let Some(ref r) = node.r {
            cp.r = Some(Box::new(inline_literals(r, literals, bound)));
        }
        return cp;
    }
    if t == NodeType::Call {
        let spec_binds = crate::builtins::lookup_spec(&node.s).map_or(false, |s| s.binds);
        let form = binding_form(&node.s, &node.items, spec_binds);
        let mut inner = bound.to_vec();
        if let Some(ref f) = form { inner.extend(f.binds.iter().cloned()); }
        let mut cp = node.clone();
        cp.items = node.items.iter().enumerate().map(|(i, item)| {
            match form.as_ref().and_then(|f| f.scopes.get(i)).copied().unwrap_or(Scope::Outer) {
                Scope::Binder => item.clone(),
                Scope::Inner => inline_literals(item, literals, &inner),
                Scope::Outer => inline_literals(item, literals, bound),
            }
        }).collect();
        return cp;
    }
    node.clone()
}

fn literal_helpers(leading: &[&Node]) -> HashMap<String, Node> {
    let mut literals = HashMap::new();
    for s in leading {
        if s.t != NodeType::Assign {
            continue;
        }
        let l = match s.l.as_deref() {
            Some(l) => l,
            None => continue,
        };
        if l.t != NodeType::Var {
            continue;
        }
        let r = match s.r.as_deref() {
            Some(r) => r,
            None => continue,
        };
        let folded = optimize_ast_logical(&inline_literals(r, &literals, &[]));
        if is_literal_type(folded.t) {
            literals.insert(l.s.clone(), folded);
        }
    }
    literals
}

fn unwind_through_helpers(
    result: &Node,
    defs: &HashMap<String, Node>,
    literals: &HashMap<String, Node>,
) -> (Node, Vec<Node>) {
    let inlined = inline_literals(result, literals, &[]);
    let (mut source, mut steps) = {
        let (s, st) = unwind_pipeline(&inlined);
        (s.clone(), st.into_iter().cloned().collect::<Vec<_>>())
    };
    let mut seen = HashSet::new();
    while source.t == NodeType::Var && defs.contains_key(&source.s) && !seen.contains(&source.s) {
        seen.insert(source.s.clone());
        let inner_inlined = inline_literals(&defs[&source.s], literals, &[]);
        let (inner_src, inner_steps) = unwind_pipeline(&inner_inlined);
        source = inner_src.clone();
        let mut combined: Vec<Node> = inner_steps.into_iter().cloned().collect();
        combined.extend(steps);
        steps = combined;
    }
    if source.t == NodeType::Var && defs.contains_key(&source.s) {
        source.sql_binding = true;
    }
    (source, steps)
}

fn read_names(node: &Node, out: &mut HashSet<String>) {
    if node.t == NodeType::Var {
        if !node.sql_binding { out.insert(node.s.clone()); }
        return;
    }
    if let Some(ref l) = node.l {
        read_names(l, out);
    }
    if let Some(ref r) = node.r {
        read_names(r, out);
    }
    for item in &node.items {
        read_names(item, out);
    }
}

fn referenced_assignments(leading: &[&Node], node: &Node) -> Vec<Node> {
    let mut needed = HashSet::new();
    read_names(node, &mut needed);
    let mut grew = true;
    while grew {
        grew = false;
        for s in leading {
            let target_name = assigned_name(s);
            if !needed.contains(&target_name) {
                continue;
            }
            if let Some(ref r) = s.r {
                let mut reads = HashSet::new();
                read_names(r, &mut reads);
                for name in reads {
                    if !needed.contains(&name) {
                        needed.insert(name);
                        grew = true;
                    }
                }
            }
        }
    }
    let mut kept = Vec::new();
    for s in leading {
        if needed.contains(&assigned_name(s)) {
            kept.push((*s).clone());
        }
    }
    kept
}

fn with_helpers(leading: &[&Node], node: &Node) -> Node {
    let kept = referenced_assignments(leading, node);
    if kept.is_empty() {
        return node.clone();
    }
    let mut seq = Node::new(NodeType::Seq, kept[0].pos);
    seq.items = kept;
    seq.items.push(node.clone());
    seq
}

struct HelpersContext<'a> {
    leading: &'a [&'a Node],
    defs: &'a HashMap<String, Node>,
    bindings: &'a Bindings,
    names: &'a HashMap<String, bool>,
    const_root: &'a Value,
}

impl<'a> HelpersContext<'a> {
    fn wrap(&self, node: &Node) -> Node {
        with_helpers(self.leading, node)
    }

    fn tables(&self, wrapped: &Node) -> Vec<String> {
        let norm_node = normalise(wrapped, Some(self.names), Some(self.const_root))
            .ok()
            .and_then(|sn| sn.to_node())
            .unwrap_or_else(|| wrapped.clone());
        source_tables(&norm_node, self.bindings)
    }
}

fn pure_memory_plan(program: &Program, dialect: &str, bindings: &Bindings) -> HybridPlan {
    HybridPlan {
        selected_member: None,
        dialect: dialect.to_string(),
        sql_statement: None,
        sql_prefix_ast: None,
        continuation_ast: Some(program.ast().clone()),
        continuation_program: Some(program.clone()),
        continuation_source_var: "_INPUT".to_string(),
        is_hybrid: false,
        pure_sql: false,
        pure_memory: true,
        source_tables: source_tables(program.ast(), bindings),
    }
}

fn latest_field_name(n: &Node) -> Option<String> {
    if n.t == NodeType::Index {
        if let (Some(ref l), Some(ref r)) = (&n.l, &n.r) {
            if l.t == NodeType::Var && l.s == "_" && r.t == NodeType::Text {
                return Some(r.s.clone());
            }
        }
    }
    None
}

fn try_latest_member(
    source: &Node,
    steps: &[Node],
    dialect: &str,
    catalog: &Bindings,
    opts: Options,
    helpers: &HelpersContext,
) -> Option<HybridPlan> {
    if dialect != "mariadb" && dialect != "mysql" && dialect != "postgresql" && dialect != "sqlite"
    {
        return None;
    }
    if !catalog.has(&source.s) {
        return None;
    }
    let binding = match catalog.get(&source.s, source.pos) {
        Ok(b) => b,
        Err(_) => return None,
    };
    if binding.kind != BindingKind::Relation {
        return None;
    }
    let rel = binding.relation.as_ref()?;
    if rel.unique_key.is_empty() || rel.from.is_raw || !rel.correlate.is_empty() {
        return None;
    }
    let at = steps.iter().position(|s| s.s == "BUCKET")?;
    let revision = &rel.unique_key;
    let ba = &steps[at].items;
    let partition = if ba.len() == 2 || ba.len() == 3 {
        latest_field_name(&ba[1])?
    } else {
        return None;
    };

    let mut body = if ba.len() == 3 {
        Some(&ba[2])
    } else {
        None
    };
    if body.is_none()
        && at + 1 < steps.len()
        && steps[at + 1].s == "MAP"
        && steps[at + 1].items.len() == 2
    {
        body = Some(&steps[at + 1].items[1]);
    }
    let body = body?;
    let pf = rel.field(&partition)?;
    let rf = rel.field(revision)?;
    if body.t != NodeType::Call
        || body.s != "RECORD"
        || body.items.len() != 4
        || (pf.sql_type != SqlKind::Num && pf.sql_type != SqlKind::Text)
        || rf.sql_type != SqlKind::Num
        || pf.column != partition
        || rf.column != *revision
        || pf.is_raw
        || rf.is_raw
        || rf.guard
    {
        return None;
    }
    let ra = &body.items;
    if ra[0].t != NodeType::Text || ra[2].t != NodeType::Text || ra[0].s == ra[2].s {
        return None;
    }

    let mut top = None;
    let mut has_key = false;
    for v in [&ra[1], &ra[3]] {
        if v.t == NodeType::Call && v.s == "TOP_BY" {
            top = Some(v);
        }
        if v.t == NodeType::Var && v.s == "_K" {
            has_key = true;
        }
    }
    let top = top?;
    if !has_key {
        return None;
    }
    let ta = &top.items;
    if ta.len() != 4 || ta[0].t != NodeType::Var || ta[0].s != "_" {
        return None;
    }
    let lfn = latest_field_name(&ta[1])?;
    if lfn != *revision
        || ta[2].t != NodeType::Text
        || ta[2].s != "DESC"
        || ta[3].t != NodeType::Num
        || ta[3].s != "1"
    {
        return None;
    }

    for i in 0..at {
        let s = &steps[i];
        if s.s == "FILTER" {
            continue;
        }
        if s.s != "SORT_BY" || (s.items.len() != 2 && s.items.len() != 3) {
            return None;
        }
        let slfn = latest_field_name(&s.items[1])?;
        if slfn != *revision {
            return None;
        }
        if s.items.len() == 3 && (s.items[2].t != NodeType::Text || s.items[2].s != "ASC") {
            return None;
        }
    }

    let mut input_steps: Vec<Node> = steps[..at].to_vec();
    if input_steps.is_empty() {
        let mut truth = Node::new(NodeType::Bool, source.pos);
        truth.b = true;
        let mut dummy = Node::new(NodeType::Call, source.pos);
        dummy.s = "FILTER".to_string();
        dummy.items = vec![source.clone(), truth];
        input_steps = vec![dummy];
    }
    let prefix = helpers.wrap(&build_pipeline(source, &input_steps));
    let prefix_prog = Program::new("", prefix.clone());
    let sql = try_translate_statement(&prefix_prog, dialect, catalog, opts)?;

    let emit = Emit::new(dialect);
    let mut input = "_sel_input".to_string();
    let mut groups = "_sel_latest".to_string();
    let from_table = physical_source(binding);
    while input.eq_ignore_ascii_case(&from_table) {
        input.push('_');
    }
    while groups.eq_ignore_ascii_case(&from_table) || groups.eq_ignore_ascii_case(&input) {
        groups.push('_');
    }
    let qi = emit.ident(&input);
    let qg = emit.ident(&groups);
    let qr = emit.ident(revision);
    let qmax = emit.ident("_sel_revision");
    let qfirst = emit.ident("_sel_first");
    let key_frag = emit
        .text_operand(&Fragment::new(
            vec![Part::Sql(emit.ident(&partition))],
            pf.sql_type,
            dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
        .ok()?;
    let key_str = key_frag.as_value(Mode::Inline).ok()?;

    let mut parts = vec![Part::Sql(format!("WITH {} AS (", qi))];
    parts.extend(sql.parts.clone());
    parts.push(Part::Sql(format!(
        "), {} AS (SELECT MAX({}) AS {}, MIN({}) AS {} FROM {} GROUP BY {}) SELECT {}.* FROM {} JOIN {} ON {}.{} = {}.{} ORDER BY {}.{} ASC",
        qg, qr, qmax, qr, qfirst, qi, key_str, qi, qi, qg, qi, qr, qg, qmax, qg, qfirst
    )));

    let remaining = &steps[at..];
    let continuation = helpers.wrap(&build_pipeline(&var_node("_INPUT", steps[at].pos), remaining));
    let continuation_prog = Program::new("", continuation.clone());

    Some(HybridPlan {
        dialect: dialect.to_string(),
        is_hybrid: true,
        pure_sql: false,
        pure_memory: false,
        sql_statement: Some(Fragment::new(
            parts,
            SqlKind::Statement,
            dialect,
            sql.params,
            sql.param_kinds,
            sql.caveats,
        )),
        sql_prefix_ast: Some(prefix),
        continuation_ast: Some(continuation),
        continuation_program: Some(continuation_prog),
        continuation_source_var: "_INPUT".to_string(),
        source_tables: vec![from_table],
        selected_member: Some(SelectedMember {
            partition_key: partition,
            revision_key: revision.clone(),
        }),
    })
}

fn collect_field_references(node: &Node, binder: &str, out: &mut Vec<String>) {
    if node.t == NodeType::Index {
        if let (Some(ref l), Some(ref r)) = (&node.l, &node.r) {
            if l.t == NodeType::Var && r.t == NodeType::Text {
                let object = l.s.to_ascii_uppercase();
                if binder.is_empty()
                    || object == binder.to_ascii_uppercase()
                    || object == "_"
                    || object == "_1"
                    || object == "_2"
                {
                    let key = &r.s;
                    if !out.contains(key) {
                        out.push(key.clone());
                    }
                }
            }
        }
    }
    if let Some(ref l) = node.l {
        collect_field_references(l, binder, out);
    }
    if let Some(ref r) = node.r {
        collect_field_references(r, binder, out);
    }
    for item in &node.items {
        collect_field_references(item, binder, out);
    }
}

const FALLTHROUGH_DOWNSTREAM_OPS: &[&str] = &["SORT_BY", "TOP_BY", "TAKE", "DROP"];

fn reads_whole_row(node: &Node, binder: &str) -> bool {
    if node.t == NodeType::Var {
        let name = node.s.to_ascii_uppercase();
        return name == binder.to_ascii_uppercase()
            || name == "_"
            || name == "_1"
            || name == "_2";
    }
    if node.t == NodeType::Index {
        if let (Some(ref l), Some(ref r)) = (&node.l, &node.r) {
            if l.t == NodeType::Var && r.t == NodeType::Text {
                return reads_whole_row(r, binder);
            }
        }
    }
    if let Some(ref l) = node.l {
        if reads_whole_row(l, binder) {
            return true;
        }
    }
    if let Some(ref r) = node.r {
        if reads_whole_row(r, binder) {
            return true;
        }
    }
    for item in &node.items {
        if reads_whole_row(item, binder) {
            return true;
        }
    }
    false
}

fn is_own_field_read(key: &Node, val: &Node, binder: &str) -> bool {
    if val.t == NodeType::Index {
        if let (Some(ref l), Some(ref r)) = (&val.l, &val.r) {
            return l.t == NodeType::Var
                && r.t == NodeType::Text
                && l.s.eq_ignore_ascii_case(binder)
                && r.s == key.s;
        }
    }
    false
}

struct MapRecordDetails<'a> {
    explicit: bool,
    binder: String,
    body: &'a Node,
    pairs: Vec<(&'a Node, &'a Node)>,
}

fn get_map_record_details(step: &Node) -> Option<MapRecordDetails> {
    if step.t != NodeType::Call || step.s != "MAP" {
        return None;
    }
    let args = &step.items;
    let explicit = args.len() == 3 && args[1].t == NodeType::Var && !args[1].grouped;
    let binder = if explicit {
        args[1].s.clone()
    } else {
        "_".to_string()
    };
    let body = if explicit {
        &args[2]
    } else if args.len() == 2 {
        &args[1]
    } else {
        return None;
    };
    if body.t != NodeType::Call || body.s != "RECORD" || body.items.len() % 2 != 0 {
        return None;
    }
    let mut seen = HashSet::new();
    let mut pairs = Vec::new();
    for i in (0..body.items.len()).step_by(2) {
        let k = &body.items[i];
        if k.t != NodeType::Text || seen.contains(&k.s) {
            return None;
        }
        seen.insert(k.s.clone());
        pairs.push((k, &body.items[i + 1]));
    }
    Some(MapRecordDetails {
        explicit,
        binder,
        body,
        pairs,
    })
}

fn try_plan_fallthrough(
    source: &Node,
    steps: &[Node],
    dialect: &str,
    catalog: &Bindings,
    options: Options,
    helpers: &HelpersContext,
) -> Option<HybridPlan> {
    let map_index = steps.iter().position(|s| s.s == "MAP")?;
    if bucket_rows_are_keys(steps, map_index) || !key_safe_boundary(steps, map_index) {
        return None;
    }
    let map_step = &steps[map_index];
    let details = get_map_record_details(map_step)?;

    let mut pushable = Vec::new();
    let mut custom = Vec::new();
    let mut memo = HashMap::new();
    for pair in &details.pairs {
        let mut seen = HashSet::new();
        if contains_unsupported_sql(pair.1, dialect, helpers.defs, &mut seen, &mut memo) {
            custom.push(*pair);
        } else {
            pushable.push(*pair);
        }
    }
    if pushable.is_empty() || custom.is_empty() {
        return None;
    }
    for pair in &custom {
        if reads_whole_row(pair.1, &details.binder) {
            return None;
        }
    }

    let mut projected: Vec<String> = pushable.iter().map(|p| p.0.s.clone()).collect();

    for i in (map_index + 1)..steps.len() {
        if !FALLTHROUGH_DOWNSTREAM_OPS.contains(&steps[i].s.as_str()) {
            return None;
        }
        let mut refs = Vec::new();
        for a in 1..steps[i].items.len() {
            collect_field_references(&steps[i].items[a], "", &mut refs);
        }
        for ref_item in refs {
            if !projected.contains(&ref_item) {
                return None;
            }
        }
    }

    let mut own = Vec::new();
    for pair in &pushable {
        if is_own_field_read(pair.0, pair.1, &details.binder) {
            own.push(pair.0.s.clone());
        }
    }

    let mut dependencies: Vec<String> = Vec::new();
    let mut dependencies_folded: Vec<String> = Vec::new();
    for pair in &custom {
        let mut refs = Vec::new();
        collect_field_references(pair.1, &details.binder, &mut refs);
        for ref_item in refs {
            if projected.contains(&ref_item) {
                if !own.contains(&ref_item) {
                    return None;
                }
            } else {
                let ref_upper = ref_item.to_ascii_uppercase();
                for proj in &projected {
                    if proj.to_ascii_uppercase() == ref_upper {
                        return None;
                    }
                }
                if !dependencies.contains(&ref_item) {
                    if dependencies_folded.contains(&ref_upper) {
                        return None;
                    }
                    dependencies_folded.push(ref_upper);
                    dependencies.push(ref_item);
                }
            }
        }
    }

    let mut rewritten_record = details.body.clone();
    rewritten_record.items.clear();
    for pair in &pushable {
        rewritten_record.items.push(pair.0.clone());
        rewritten_record.items.push(pair.1.clone());
    }
    for dep in &dependencies {
        let k = text_node(dep, map_step.pos);
        let idx = index_node(
            var_node(&details.binder, map_step.pos),
            k.clone(),
            map_step.pos,
        );
        rewritten_record.items.push(k);
        rewritten_record.items.push(idx);
    }

    let mut rewritten_map = map_step.clone();
    rewritten_map.items = vec![map_step.items[0].clone()];
    if details.explicit {
        rewritten_map.items.push(map_step.items[1].clone());
    }
    rewritten_map.items.push(rewritten_record);

    let mut rewritten_steps: Vec<Node> = Vec::with_capacity(steps.len());
    rewritten_steps.extend_from_slice(&steps[..map_index]);
    rewritten_steps.push(rewritten_map);
    // Later operations must run after every local MAP pair has been evaluated.

    let rewritten_ast = helpers.wrap(&build_pipeline(source, &rewritten_steps));
    let rewritten_prog = Program::new("", rewritten_ast.clone());
    let sql = try_translate_statement(&rewritten_prog, dialect, catalog, options)?;

    let mut continuation_record = details.body.clone();
    continuation_record.items.clear();
    for pair in &details.pairs {
        continuation_record.items.push(pair.0.clone());
        let is_pushable = pushable.iter().any(|p| p.0.s == pair.0.s);
        if is_pushable {
            continuation_record.items.push(index_node(
                var_node(&details.binder, pair.1.pos),
                pair.0.clone(),
                pair.1.pos,
            ));
        } else {
            continuation_record.items.push(pair.1.clone());
        }
    }

    let mut continuation_map = map_step.clone();
    continuation_map.items = vec![var_node("_INPUT", map_step.pos)];
    if details.explicit {
        continuation_map.items.push(map_step.items[1].clone());
    }
    continuation_map.items.push(continuation_record);

    let continuation_ast = helpers.wrap(&build_pipeline(&continuation_map, &steps[map_index + 1..]));
    let continuation_prog = Program::new("", continuation_ast.clone());

    Some(HybridPlan {
        dialect: dialect.to_string(),
        is_hybrid: true,
        pure_sql: false,
        pure_memory: false,
        sql_statement: Some(sql),
        sql_prefix_ast: Some(rewritten_ast.clone()),
        continuation_ast: Some(continuation_ast),
        continuation_program: Some(continuation_prog),
        continuation_source_var: "_INPUT".to_string(),
        source_tables: helpers.tables(&rewritten_ast),
        selected_member: None,
    })
}

pub fn plan_hybrid(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> HybridPlan {
    if let Err(e) = require_target(dialect, Pos::default()) {
        std::panic::panic_any(e);
    }
    let default_bindings = Bindings::default();
    let checked = bindings.unwrap_or(&default_bindings);
    if let Err(e) = checked.check_aliases(Pos::default()) {
        std::panic::panic_any(e);
    }

    let (const_names, const_root) = const_scope(Some(checked));
    let mut identity_barrier = false;
    let mut early_pure_memory = false;

    match normalise(program.ast(), Some(&const_names), Some(&const_root)) {
        Ok(normalized) => {
            identity_barrier = crate::sql::constants::identity_loss_before_grouping(
                Some(&normalized),
                crate::sql::constants::NeededFields::new(),
            );
        }
        Err(_) => {
            early_pure_memory = true;
        }
    }

    if early_pure_memory {
        return pure_memory_plan(program, dialect, checked);
    }

    let (parts_leading, parts_result) = statements(program.ast());
    let literals = literal_helpers(&parts_leading);
    let defs = definitions(&parts_leading);

    let is_relation = |node: &Node| {
        if node.t == NodeType::Var && checked.has(&node.s) {
            if let Ok(b) = checked.get(&node.s, node.pos) {
                return b.kind == BindingKind::Relation;
            }
        }
        false
    };

    let (unwound_source, unwound_steps) =
        unwind_through_helpers(parts_result, &defs, &literals);
    if unwound_steps.is_empty() || !is_relation(&unwound_source) {
        return pure_memory_plan(program, dialect, checked);
    }

    let optimized = optimize_ast_logical(&build_pipeline(&unwound_source, &unwound_steps));
    let (source_ref, steps_ref) = unwind_pipeline(&optimized);
    let source = source_ref.clone();
    let steps: Vec<Node> = steps_ref.into_iter().cloned().collect();

    if steps.is_empty() || !is_relation(&source) {
        return pure_memory_plan(program, dialect, checked);
    }

    let helpers = HelpersContext {
        leading: &parts_leading,
        defs: &defs,
        bindings: checked,
        names: &const_names,
        const_root: &const_root,
    };

    let full_ast = helpers.wrap(&build_pipeline(&source, &steps));
    let full_prog = Program::new("", full_ast.clone());
    let mut full_sql = None;
    if !identity_barrier && !rows_are_not_the_value(&steps, steps.len()) {
        full_sql = try_translate_statement(&full_prog, dialect, checked, options);
    }
    if let Some(sql) = full_sql {
        return HybridPlan {
            dialect: dialect.to_string(),
            sql_statement: Some(sql),
            sql_prefix_ast: Some(full_ast.clone()),
            continuation_ast: None,
            continuation_program: None,
            pure_sql: true,
            is_hybrid: false,
            pure_memory: false,
            continuation_source_var: "_INPUT".to_string(),
            source_tables: helpers.tables(&full_ast),
            selected_member: None,
        };
    }

    if let Some(latest) = try_latest_member(&source, &steps, dialect, checked, options, &helpers) {
        return latest;
    }

    if !identity_barrier {
        if let Some(ft) = try_plan_fallthrough(&source, &steps, dialect, checked, options, &helpers) {
            return ft;
        }
    }

    for count in (1..steps.len()).rev() {
        if rows_are_not_the_value(&steps, count) || !key_safe_boundary(&steps, count) {
            continue;
        }
        let prefix_ast = helpers.wrap(&build_pipeline(&source, &steps[..count]));
        if identity_barrier {
            let mut skip = false;
            if let Ok(norm) = normalise(&prefix_ast, Some(&const_names), Some(&const_root)) {
                let mut needed = crate::sql::constants::NeededFields::new();
                needed.all = true;
                if crate::sql::constants::identity_loss_before_grouping(
                    Some(&norm),
                    needed,
                ) {
                    skip = true;
                }
            } else {
                skip = true;
            }
            if skip {
                continue;
            }
        }
        let prefix_prog = Program::new("", prefix_ast.clone());
        let sql = match try_translate_statement(&prefix_prog, dialect, checked, options) {
            Some(s) => s,
            None => continue,
        };

        let remaining = &steps[count..];
        // A 3-argument LINK names the two sides of the row it builds after the
        // variables it joined: the left side is the pipeline's own source
        // variable, wherever in the continuation the LINK falls (spec 7.4;
        // PHP-C9, GO-C22). The rows are fed to the continuation under that name,
        // or the joined row would carry the left side under `_INPUT`. A step that
        // also READS that name (a self-join) would find the truncated rows where
        // run() finds the whole relation: that split is not made. A LINK in the
        // prefix has already named its sides.
        let is_link = |step: &Node| step.s == "LINK" || step.s == "LINK_LEFT";
        let needs_rebind = !steps[..count].iter().any(|step| is_link(step))
            && remaining.iter().any(|step| is_link(step) && step.items.len() == 3);
        if needs_rebind {
            let reads_source = source.t != NodeType::Var
                || remaining.iter().any(|step| {
                    step.items.iter().skip(1).any(|arg| {
                        let mut names = HashSet::new();
                        read_names(arg, &mut names);
                        names.contains(&source.s)
                    })
                });
            if reads_source {
                continue;
            }
        }
        let feed = if needs_rebind { source.s.clone() } else { "_INPUT".to_string() };
        let continuation_ast =
            helpers.wrap(&build_pipeline(&var_node(&feed, remaining[0].pos), remaining));
        let continuation_prog = Program::new("", continuation_ast.clone());

        return HybridPlan {
            dialect: dialect.to_string(),
            sql_statement: Some(sql),
            sql_prefix_ast: Some(prefix_ast.clone()),
            continuation_ast: Some(continuation_ast),
            continuation_program: Some(continuation_prog),
            continuation_source_var: feed,
            is_hybrid: true,
            pure_sql: false,
            pure_memory: false,
            source_tables: helpers.tables(&prefix_ast),
            selected_member: None,
        };
    }

    pure_memory_plan(program, dialect, checked)
}

pub fn execute_hybrid<F>(
    plan: &HybridPlan,
    mut db_runner: F,
    context: Option<&Value>,
) -> Result<Value, SelError>
where
    F: FnMut(&str, &[Value]) -> Result<Value, SelError>,
{
    if plan.pure_memory {
        let mut prog = plan
            .continuation_program
            .clone()
            .ok_or_else(|| SelError::new("E_BAD_ARG", "pure-memory hybrid plan has no continuation program", Pos::default()))?;
        let ctx = match context {
            Some(c) => c.deep_copy(1, Pos::default())?,
            None => Value::null(),
        };
        return prog.run(Some(ctx));
    }
    if !plan.pure_sql && plan.continuation_program.is_none() {
        return Err(SelError::new(
            "E_BAD_ARG", "hybrid plan has no continuation program", Pos::default(),
        ));
    }
    let sql = plan
        .sql_statement
        .as_ref()
        .ok_or_else(|| SelError::new("E_BAD_ARG", "SQL hybrid plan has no SQL statement", Pos::default()))?;
    let sql_stmt = sql
        .as_statement(Mode::Params)
        .map_err(|e| {
            // SelError stores static identifiers; recover the catalogue entry
            // instead of replacing a rendering refusal with a wrapper code.
            const SQL_CODES: &[&str] = &[
                "E_SQL_DIALECT", "E_SQL_UNSUPPORTED", "E_SQL_UNBOUND",
                "E_SQL_BINDING", "E_SQL_ASSIGN", "E_SQL_INVALID",
                "E_SQL_DEPTH", "E_SQL_SIZE", "E_SQL_SHAPE",
            ];
            let code = SQL_CODES.iter().copied()
                .chain(crate::limits::ERROR_CODES.iter().map(|entry| entry.0))
                .find(|code| *code == e.code);
            match code {
                Some(code) => SelError::new(code, e.message, e.pos),
                None => SelError::new("E_BAD_ARG", format!("uncatalogued SQL error {}: {}", e.code, e.message), e.pos),
            }
        })?;
    let rows = db_runner(&sql_stmt, &sql.bindings())?;
    if plan.pure_sql {
        return Ok(rows);
    }
    let mut prog = plan
        .continuation_program
        .clone()
        .ok_or_else(|| SelError::new("E_BAD_ARG", "hybrid plan has no continuation program", Pos::default()))?;
    let cont_ctx = match context {
        // is_none() is true of every list and record (their kind is None): the test
        // for "no context" is is_null(), or the caller's whole context is dropped (GO-C5).
        Some(c) if !c.is_null() => c.deep_copy(1, Pos::default())?,
        _ => Value::record_from_entries(Vec::new()),
    };
    cont_ctx.set(&plan.continuation_source_var, rows, Pos::default())?;
    prog.run(Some(cont_ctx))
}
