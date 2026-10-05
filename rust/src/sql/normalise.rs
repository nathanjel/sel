use std::collections::{HashMap, HashSet};

use crate::ast::{Node, NodeType};
use crate::limits::{MAX_DEPTH, MAX_SQL_NODES};
use crate::manifest::builtins::Scope;
use crate::manifest::binding_form;
use crate::utf8::Pos;
use crate::value::Value;
use crate::context::Context;
use crate::program::Program;
use crate::sql::constants::{is_constant, refuse_as_sel};
use crate::sql::errors::{refuse, SqlError};
use crate::sql::node::{SNode, SNodeType};

pub fn normalise(
    ast: &Node,
    const_names: Option<&HashSet<String>>,
    root: Option<&Value>,
) -> Result<SNode, SqlError> {
    // Alpha-rename explicit binders before helper substitution. Names that were
    // free at a helper's definition must stay free at every use site.
    let mut captures = Vec::new();
    if ast.t == NodeType::Seq {
        let mut seen = std::collections::HashSet::new();
        for stmt in ast.items.iter().take(ast.items.len().saturating_sub(1)) {
            if let Some(ref rhs) = stmt.r {
                crate::sql::hybrid::free_names_seen(rhs, &[], &mut captures, &mut seen);
            }
        }
    }
    if let Some(names) = const_names { captures.extend(names.iter().cloned()); }
    let hygienic;
    let ast = if captures.is_empty() { ast } else {
        hygienic = rename_binders(ast, &captures, &HashMap::new(), &mut 0);
        &hygienic
    };
    let mut stmts: Vec<&Node> = Vec::new();
    if ast.t == NodeType::Seq {
        for item in &ast.items {
            stmts.push(item);
        }
    } else {
        stmts.push(ast);
    }

    let result = *stmts.last().unwrap();
    stmts.pop();

    let mut defs: HashMap<String, SNode> = HashMap::new();
    let mut sizes: HashMap<String, Meas> = HashMap::new();
    let base = if ast.t == NodeType::Seq { 1 } else { 0 };

    // The names a statement may read as constants (the value bindings, then each
    // whole definition recorded as constant), the context constant statements
    // run in (a copy of the value bindings), and the definitions too large to
    // build, which are charged only where they are read.
    let mut run = Run {
        constant: const_names.cloned().unwrap_or_default(),
        scratch: Context::new(match root {
            Some(r) => r.deep_copy(0, Pos::default()).unwrap_or_else(|_| r.clone()),
            None => Value::none(),
        }),
        oversized: HashSet::new(),
    };
    for s in stmts {
        record_stmt(s, &mut defs, &mut sizes, &mut run, base + 1)?;
    }

    // The result expression is not measured here: the translator charges what it
    // walks (E_SQL_SIZE at the node it reached). Only the memory the inlined
    // copies take is capped (see St::charge).
    let mut st = St { measure: false, build: true, total: 0, sizes: &sizes, oversized: &run.oversized };
    Ok(substitute_node(result, &defs, &[], base, &mut st)?.0)
}

// The size of a subtree counted as a tree, its height, and the first refusal
// expandedSize meets in it (docs/internals/sql-translation.md §7.4). A helper's
// text is shared where it is read, so `X1 = X0 + X0; X2 = X1 + X1; ...` is small
// as a graph and exponential as a tree: every definition is refused, at the
// assignment, once its expanded size passes the translator's budget (E_SQL_SIZE)
// or its height four times the evaluator's depth (E_SQL_DEPTH). The first refusal
// is the first node, children right to left and each before its parent, that
// passes either -- the order the other hosts' iterative walk visits them in.
#[derive(Clone, Copy, Debug)]
struct Meas {
    size: usize,
    height: usize,
    fail: Option<&'static str>,
}

impl Meas {
    const LEAF: Meas = Meas { size: 1, height: 1, fail: None };

    fn over(kids: &[Meas]) -> Meas {
        let mut size = 1usize;
        let mut height = 0usize;
        let mut fail = None;
        for k in kids.iter().rev() {
            size = size.saturating_add(k.size);
            height = height.max(k.height);
            if fail.is_none() {
                fail = k.fail;
            }
        }
        height += 1;
        if fail.is_none() {
            if height > 4 * MAX_DEPTH {
                fail = Some("E_SQL_DEPTH");
            } else if size > MAX_SQL_NODES {
                fail = Some("E_SQL_SIZE");
            }
        }
        Meas { size, height, fail }
    }
}

// A tree already built, measured by walking it (the kids of a list spliced into
// its parent, which have no measurement of their own).
fn measure_tree(node: &SNode) -> Meas {
    let kids: Vec<Meas> = node.kids.iter().map(measure_tree).collect();
    if kids.is_empty() { Meas::LEAF } else { Meas::over(&kids) }
}

// Substitution state. `total` counts the nodes the inlined copies add, which is
// capped well above the budget so a program cannot make this layer copy an
// exponential tree: the other hosts share a helper's text where it is read and
// never copy it.
struct St<'a> {
    measure: bool,
    // False for the dry run that measures a definition and makes its refusals
    // without copying any helper it reads: a read stays the bare name.
    build: bool,
    total: usize,
    sizes: &'a HashMap<String, Meas>,
    // Definitions past the budget, never built: reading one is E_SQL_SIZE.
    oversized: &'a HashSet<String>,
}

// Stage 1's state across the leading statements.
struct Run {
    constant: HashSet<String>,
    scratch: Context,
    oversized: HashSet<String>,
}

const COPY_CAP: usize = 4 * MAX_SQL_NODES;

impl St<'_> {
    fn charge(&mut self, n: usize) -> Result<(), SqlError> {
        self.total = self.total.saturating_add(n);
        if self.total > COPY_CAP {
            return refuse(
                "E_SQL_SIZE",
                format!("this expression expands to more than {} nodes once every read of a helper is counted", MAX_SQL_NODES),
                // No position: it blames the whole rule (sql/errors.md).
                Pos::default(),
            );
        }
        Ok(())
    }
}

fn rename_binders(node: &Node, captures: &[String], renames: &HashMap<String, String>, counter: &mut usize) -> Node {
    let mut out = node.clone();
    out.math_plan = None;
    if node.t == NodeType::Var {
        if !node.sql_binding {
            if let Some(name) = renames.get(&node.s) { out.s = name.clone(); }
        }
        return out;
    }
    if node.t == NodeType::Call {
        let binds = crate::builtins::lookup_spec(&node.s).is_some_and(|s| s.binds);
        let form = binding_form(&node.s, &node.items, binds);
        let mut inner = renames.clone();
        if let Some(ref f) = form {
            for name in &f.binds { inner.remove(name); }
            for (i, arg) in node.items.iter().enumerate() {
                if f.scopes.get(i) == Some(&Scope::Binder) && arg.t == NodeType::Var && captures.contains(&arg.s) {
                    *counter += 1;
                    // A control character cannot occur in a parsed identifier.
                    inner.insert(arg.s.clone(), format!("{}\u{1}{}", arg.s, counter));
                }
            }
        }
        out.items = node.items.iter().enumerate().map(|(i, arg)| {
            match form.as_ref().and_then(|f| f.scopes.get(i)).copied().unwrap_or(Scope::Outer) {
                Scope::Binder => {
                    let mut binder = arg.clone();
                    if let Some(name) = inner.get(&arg.s) { binder.s = name.clone(); }
                    binder
                },
                Scope::Inner => rename_binders(arg, captures, &inner, counter),
                Scope::Outer => rename_binders(arg, captures, renames, counter),
            }
        }).collect();
        return out;
    }
    out.l = node.l.as_ref().map(|n| Box::new(rename_binders(n, captures, renames, counter)));
    out.r = node.r.as_ref().map(|n| Box::new(rename_binders(n, captures, renames, counter)));
    out.items = node.items.iter().map(|n| rename_binders(n, captures, renames, counter)).collect();
    out
}

fn record_stmt(
    s: &Node,
    defs: &mut HashMap<String, SNode>,
    sizes: &mut HashMap<String, Meas>,
    run: &mut Run,
    depth: usize,
) -> Result<(), SqlError> {
    if s.t != NodeType::Assign {
        return refuse(
            "E_SQL_ASSIGN",
            "only assignments may come before the result expression; this computes a value nothing reads, which SQL has nowhere to put",
            s.pos,
        );
    }
    if s.s != "=" {
        return refuse(
            "E_SQL_ASSIGN",
            format!(
                "{} reads its own target before writing it, and SQL has nowhere to put the write; use = and a fresh name",
                s.s
            ),
            s.pos,
        );
    }

    let mut keys: Vec<String> = Vec::new();
    let mut t = s.l.as_deref().unwrap();
    while t.t == NodeType::Index {
        let Some(k) = constant_key(t.r.as_deref().unwrap()) else {
            return refuse(
                "E_SQL_ASSIGN",
                "an assignment target may only be indexed by a constant here, because the shape has to be known before the query runs",
                t.r.as_deref().unwrap().pos,
            );
        };
        keys.insert(0, k);
        t = t.l.as_deref().unwrap();
    }

    if t.t != NodeType::Var {
        return refuse("E_SQL_ASSIGN", "assignment target is not a variable", s.pos);
    }
    let name = t.s.clone();

    // The dry run first: the statement as written, every refusal substitution
    // makes, and the size and height its expansion would have, without copying
    // a helper. A definition's SIZE is charged only where it is read
    // (docs/internals/sql-translation.md §7.4): one past the budget is not built
    // and is free unless something reads it. Its height is refused here, as the
    // other hosts do, before anything walks it.
    let (written, meas) = {
        let mut st = St { measure: true, build: false, total: 0, sizes, oversized: &run.oversized };
        substitute_node(s.r.as_deref().unwrap(), defs, &[], depth, &mut st)?
    };
    if meas.fail == Some("E_SQL_DEPTH") {
        return refuse(
            "E_SQL_DEPTH",
            format!("this expression nests deeper than SEL will evaluate ({}), so there is nothing to translate; the evaluator answers E_DEPTH for it", MAX_DEPTH),
            s.pos,
        );
    }
    let oversized = meas.size > MAX_SQL_NODES;

    // Validated here, and only here, because after this the subtree may be gone:
    // a definition nothing reads is dropped, so `A = 1 / 0; TRUE` would translate
    // to TRUE where SEL raises E_DIV_ZERO. Every constant definition, whatever its
    // size (§11.4): the statement is asked AS WRITTEN, run in scratch, where every
    // earlier constant definition already holds its value -- SEL reads a helper,
    // it does not re-expand it, so this is linear where evaluating the inlined
    // tree is exponential in a doubling chain
    // (norm.size.unread-definition-past-the-budget-*).
    let is_const = is_constant(Some(&written), Some(&run.constant));
    if is_const {
        if let Err(err) = Program::new("", s.clone()).run_with_context(&mut run.scratch) {
            return refuse_as_sel(&err, &written);
        }
    }
    if keys.is_empty() && is_const {
        run.constant.insert(name.clone());
    } else {
        run.constant.remove(&name);
    }

    let value = if oversized {
        // An indexed entry this large marks its whole list: reading the list
        // would expand it.
        run.oversized.insert(name.clone());
        SNode::leaf(t)
    } else {
        let mut st = St { measure: true, build: true, total: 0, sizes, oversized: &run.oversized };
        substitute_node(s.r.as_deref().unwrap(), defs, &[], depth, &mut st)?.0
    };

    if keys.is_empty() {
        if defs.contains_key(&name) {
            return refuse(
                "E_SQL_ASSIGN",
                format!(
                    "{} is assigned more than once; SQL has no notion of a variable changing, so each name may be written once",
                    name
                ),
                s.pos,
            );
        }
        sizes.insert(name.clone(), meas);
        defs.insert(name, value);
        return Ok(());
    }

    if keys.len() > 1 {
        return refuse(
            "E_SQL_ASSIGN",
            "only one level of indexed assignment can be folded into a list here",
            s.pos,
        );
    }
    let key = keys.remove(0);
    if !defs.contains_key(&name) {
        defs.insert(name.clone(), SNode::new_clist(s.pos));
        sizes.insert(name.clone(), Meas::LEAF);
    }
    let clist = defs.get_mut(&name).unwrap();
    if clist.t != SNodeType::CList {
        return refuse(
            "E_SQL_ASSIGN",
            format!("{} is assigned both as a whole and by index; use one or the other", name),
            s.pos,
        );
    }
    for existing in &clist.keys {
        if *existing == key {
            return refuse(
                "E_SQL_ASSIGN",
                format!("{}[{}] is assigned more than once", name, key),
                s.pos,
            );
        }
    }
    clist.append(key, value);
    if let Some(m) = sizes.get_mut(&name) {
        m.size = m.size.saturating_add(meas.size);
        m.height = m.height.max(meas.height + 1);
    }
    Ok(())
}

// A literal index names a constant key -- including "": an empty text
// literal is a key like any other.
fn constant_key(idx: &Node) -> Option<String> {
    if idx.t == NodeType::Num || idx.t == NodeType::Text {
        Some(idx.s.clone())
    } else {
        None
    }
}

fn substitute_node(
    node: &Node,
    defs: &HashMap<String, SNode>,
    bound: &[String],
    depth: usize,
    st: &mut St,
) -> Result<(SNode, Meas), SqlError> {
    let d = depth + 1;
    if d > MAX_DEPTH {
        return refuse(
            "E_SQL_DEPTH",
            format!(
                "this expression nests deeper than SEL will evaluate ({}), so there is nothing to translate; the evaluator answers E_DEPTH for it",
                MAX_DEPTH
            ),
            node.pos,
        );
    }

    match node.t {
        NodeType::Var => {
            if node.sql_binding || bound.iter().any(|b| b == &node.s) {
                return Ok((SNode::leaf(node), Meas::LEAF));
            }
            if let Some(def) = defs.get(&node.s) {
                let meas = st.sizes.get(&node.s).copied().unwrap_or(Meas::LEAF);
                // A list is spliced where it is read, so the dry run needs its kids.
                if !st.build && def.t != SNodeType::List && def.t != SNodeType::CList {
                    return Ok((SNode::leaf(node), Meas { fail: None, ..meas }));
                }
                if st.oversized.contains(&node.s) {
                    return refuse(
                        "E_SQL_SIZE",
                        format!("{} expands to more than {} nodes once every read of a helper is counted", node.s, MAX_SQL_NODES),
                        Pos::default(),
                    );
                }
                // The variable is replaced, not wrapped; the copy is charged before
                // it is made.
                st.charge(meas.size)?;
                let mut copy = def.clone();
                copy.inlined = true;
                return Ok((copy, Meas { fail: None, ..meas }));
            }
            Ok((SNode::leaf(node), Meas::LEAF))
        }
        NodeType::Num | NodeType::Text | NodeType::Bool | NodeType::Null => {
            Ok((SNode::leaf(node), Meas::LEAF))
        }
        NodeType::Assign => refuse(
            "E_SQL_ASSIGN",
            "an assignment here would have to happen while the query runs, and a SQL expression cannot assign",
            node.pos,
        ),
        NodeType::Seq => refuse(
            "E_SQL_ASSIGN",
            "a sequence here would evaluate and discard a value, which a SQL expression cannot do",
            node.pos,
        ),
        NodeType::Un => {
            let (child, m) = substitute_node(node.l.as_deref().unwrap(), defs, bound, d, st)?;
            Ok((SNode::rewritten(node, vec![child]), Meas::over(&[m])))
        }
        NodeType::Bin | NodeType::Index => {
            let (l, lm) = substitute_node(node.l.as_deref().unwrap(), defs, bound, d, st)?;
            let (r, rm) = substitute_node(node.r.as_deref().unwrap(), defs, bound, d, st)?;
            Ok((SNode::rewritten(node, vec![l, r]), Meas::over(&[lm, rm])))
        }
        NodeType::List => {
            let (items, metas) = flatten_nodes(&node.items, defs, bound, d, st)?;
            let m = if metas.is_empty() { Meas::LEAF } else { Meas::over(&metas) };
            Ok((SNode::rewritten(node, items), m))
        }
        NodeType::Call => {
            let spec_binds = crate::builtins::lookup_spec(&node.s).is_some_and(|s| s.binds);
            let form = binding_form(&node.s, &node.items, spec_binds);
            let mut inner = bound.to_vec();
            if let Some(ref f) = form {
                for b in &f.binds {
                    inner.push(b.clone());
                }
            }

            let mut args = Vec::with_capacity(node.items.len());
            let mut metas = Vec::with_capacity(node.items.len());
            for (i, arg) in node.items.iter().enumerate() {
                let scope = if let Some(ref f) = form {
                    if i < f.scopes.len() {
                        f.scopes[i]
                    } else {
                        Scope::Outer
                    }
                } else {
                    Scope::Outer
                };

                // A binder position is a name, not a read, and stays as written;
                // one that is not a name is refused by the translator, at the call.
                let (a, m) = match scope {
                    Scope::Binder => (SNode::leaf(arg), measure_source(arg)),
                    Scope::Inner => substitute_node(arg, defs, &inner, d, st)?,
                    Scope::Outer => substitute_node(arg, defs, bound, d, st)?,
                };
                args.push(a);
                metas.push(m);
            }
            let m = if metas.is_empty() { Meas::LEAF } else { Meas::over(&metas) };
            Ok((SNode::rewritten(node, args), m))
        }
    }
}

// An argument kept as written, measured as the tree it is.
fn measure_source(node: &Node) -> Meas {
    let mut kids = Vec::new();
    if let Some(ref l) = node.l { kids.push(measure_source(l)); }
    if let Some(ref r) = node.r { kids.push(measure_source(r)); }
    for item in &node.items { kids.push(measure_source(item)); }
    if kids.is_empty() { Meas::LEAF } else { Meas::over(&kids) }
}

fn flatten_nodes(
    items: &[Node],
    defs: &HashMap<String, SNode>,
    bound: &[String],
    depth: usize,
    st: &mut St,
) -> Result<(Vec<SNode>, Vec<Meas>), SqlError> {
    let mut out = Vec::new();
    let mut metas = Vec::new();
    for item in items {
        let (s, m) = substitute_node(item, defs, bound, depth, st)?;
        if s.t == SNodeType::List || s.t == SNodeType::CList {
            // Spliced: the kids become the parent's own, each measured as itself.
            for kid in s.kids {
                if st.measure {
                    metas.push(measure_tree(&kid));
                }
                out.push(kid);
            }
        } else {
            out.push(s);
            metas.push(m);
        }
    }
    Ok((out, metas))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sized(nodes: usize) -> String {
        let bits: Vec<_> = (0..18).filter(|i| ((nodes - 1) / 2) & (1 << i) != 0).collect();
        let mut source = String::from("X0 = N; ");
        for i in 1..=*bits.last().unwrap() {
            source.push_str(&format!("X{i} = X{} + X{}; ", i-1, i-1));
        }
        source.push_str(&bits.iter().map(|i| format!("X{i}")).collect::<Vec<_>>().join(" + "));
        source.push_str(" > 0");
        source
    }

    #[test]
    fn expanded_helpers_obey_size_boundary() {
        let accepted = crate::parser::parse(&sized(200_001)).unwrap();
        let tree = normalise(&accepted, None, None).unwrap();
        assert_eq!(measure_tree(&tree).size, 200_001);
        let refused = crate::parser::parse(&sized(300_001)).unwrap();
        assert_eq!(normalise(&refused, None, None).unwrap_err().code, "E_SQL_SIZE");
    }

    #[test]
    fn expanded_depth_is_checked_before_constant_evaluation() {
        // A chain past the evaluator's depth, constant or not, is left to the
        // translator's own depth guard: a constant definition is validated as
        // written, which SEL evaluates without nesting it. A definition past four
        // times that depth is refused at the assignment, whatever it reads.
        for (base, links, stage1) in [("1", 250, false), ("N", 250, false), ("N", 900, true)] {
            let mut source = format!("X0 = {base}; ");
            for i in 1..links {
                source.push_str(&format!("X{i} = X{} + 1; ", i-1));
            }
            source.push_str(&format!("X{} > 0", links - 1));
            let ast = crate::parser::parse(&source).unwrap();
            match normalise(&ast, None, None) {
                Err(e) => { assert!(stage1); assert_eq!(e.code, "E_SQL_DEPTH"); }
                Ok(_) => assert!(!stage1),
            }
        }
    }
}
