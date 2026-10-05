use std::collections::{BTreeSet, HashSet};

use crate::ast::Node;
use crate::context::Context;
use crate::eval::eval_node;
use crate::limits::MAX_DEPTH;
use crate::optimizer::optimize_ast;
use crate::parser::parse;
use crate::utf8::SelError;
use crate::value::Value;

#[derive(Clone, Debug)]
pub struct Program {
    source: String,
    ast: Node,
    physical_ast: Option<Node>,
}

impl Program {
    pub fn new(source: impl Into<String>, ast: Node) -> Self {
        Self {
            source: source.into(),
            ast,
            physical_ast: None,
        }
    }

    pub fn compile(source: &str) -> Result<Self, SelError> {
        let ast = parse(source)?;
        Ok(Self::new(source.to_string(), ast))
    }

    pub fn source(&self) -> &str {
        &self.source
    }

    pub fn ast(&self) -> &Node {
        &self.ast
    }

    pub fn physical_ast(&mut self) -> &Node {
        if self.physical_ast.is_none() {
            self.physical_ast = Some(optimize_ast(&self.ast));
        }
        self.physical_ast.as_ref().unwrap()
    }

    pub fn run(&mut self, root: Option<Value>) -> Result<Value, SelError> {
        let mut ctx = Context::new(root.unwrap_or_else(Value::none));
        self.run_with_context(&mut ctx)
    }

    pub fn run_with_context(&mut self, ctx: &mut Context) -> Result<Value, SelError> {
        // Evaluate the retained tree so its shape-checked inline caches survive
        // between runs. Evaluation mutates cache cells, not the logical AST.
        eval_node(self.physical_ast(), ctx)
    }

    pub fn dependencies(&self) -> Result<Vec<String>, SelError> {
        let mut reads = BTreeSet::new();
        collect_dependencies(
            &self.ast,
            &HashSet::new(),
            &mut reads,
            &mut HashSet::new(),
            1,
        )?;
        Ok(reads.into_iter().collect())
    }
}

pub fn compile(source: &str) -> Result<Program, SelError> {
    Program::compile(source)
}

fn collect_dependencies(
    node: &Node,
    bound: &HashSet<String>,
    reads: &mut BTreeSet<String>,
    defined: &mut HashSet<String>,
    depth: usize,
) -> Result<(), SelError> {
    use crate::ast::NodeType as N;
    use crate::manifest::builtins::Scope;
    if depth > MAX_DEPTH {
        return Err(SelError::depth("expression nested too deeply", node.pos));
    }
    let read = |name: &str, reads: &mut BTreeSet<String>, defined: &HashSet<String>| {
        if !bound.contains(name) && !defined.contains(name) {
            reads.insert(name.to_owned());
        }
    };
    match node.t {
        N::Var => read(&node.s, reads, defined),
        N::Assign => {
            let mut target = node.l.as_ref().unwrap().as_ref();
            let mut keys = Vec::new();
            while target.t == N::Index {
                keys.push(target.r.as_ref().unwrap().as_ref());
                if keys.len() >= MAX_DEPTH {
                    return Err(SelError::depth(
                        "value nested too deeply",
                        node.l.as_ref().unwrap().pos,
                    ));
                }
                target = target.l.as_ref().unwrap();
            }
            // Index expressions run before the store, so a key reading the
            // root (`A[A] = 1`, `A["k"] = A`) still needs the caller's value.
            for key in keys.into_iter().rev() {
                collect_dependencies(key, bound, reads, defined, depth + 1)?;
            }
            if node.s != "=" {
                read(&target.s, reads, defined);
            }
            collect_dependencies(node.r.as_ref().unwrap(), bound, reads, defined, depth + 1)?;
            if !bound.contains(&target.s) {
                defined.insert(target.s.clone());
            }
        }
        N::Seq | N::List => {
            for item in &node.items {
                collect_dependencies(item, bound, reads, defined, depth + 1)?;
            }
        }
        N::Index | N::Bin => {
            collect_dependencies(node.l.as_ref().unwrap(), bound, reads, defined, depth + 1)?;
            if node.t == N::Bin && crate::ops::is_short_circuit(&node.s) {
                // The right operand may never run. Its reads still count.
                let mut branch = defined.clone();
                collect_dependencies(
                    node.r.as_ref().unwrap(),
                    bound,
                    reads,
                    &mut branch,
                    depth + 1,
                )?;
            } else {
                collect_dependencies(node.r.as_ref().unwrap(), bound, reads, defined, depth + 1)?;
            }
        }
        N::Un => collect_dependencies(node.l.as_ref().unwrap(), bound, reads, defined, depth + 1)?,
        N::Call if node.s == "IF" => {
            collect_dependencies(&node.items[0], bound, reads, defined, depth + 1)?;
            let mut yes = defined.clone();
            let mut no = defined.clone();
            collect_dependencies(&node.items[1], bound, reads, &mut yes, depth + 1)?;
            if let Some(otherwise) = node.items.get(2) {
                collect_dependencies(otherwise, bound, reads, &mut no, depth + 1)?;
            }
            yes.retain(|name| no.contains(name));
            *defined = yes;
        }
        N::Call if node.s == "COND" => {
            let mut fallthrough = defined.clone();
            let mut exits: Option<HashSet<String>> = None;
            for pair in node.items[..node.items.len() - 1].chunks_exact(2) {
                collect_dependencies(&pair[0], bound, reads, &mut fallthrough, depth + 1)?;
                let mut branch = fallthrough.clone();
                collect_dependencies(&pair[1], bound, reads, &mut branch, depth + 1)?;
                if let Some(common) = &mut exits {
                    common.retain(|name| branch.contains(name));
                } else {
                    exits = Some(branch);
                }
            }
            collect_dependencies(
                node.items.last().unwrap(),
                bound,
                reads,
                &mut fallthrough,
                depth + 1,
            )?;
            if let Some(mut common) = exits {
                common.retain(|name| fallthrough.contains(name));
                *defined = common;
            } else {
                *defined = fallthrough;
            }
        }
        N::Call if node.s == "COALESCE" => {
            if let Some(first) = node.items.first() {
                collect_dependencies(first, bound, reads, defined, depth + 1)?;
            }
            let mut continuation = defined.clone();
            for item in node.items.iter().skip(1) {
                collect_dependencies(item, bound, reads, &mut continuation, depth + 1)?;
            }
        }
        N::Call => {
            let spec_binds = node.spec.as_ref().is_some_and(|s| s.binds);
            if let Some(form) = crate::manifest::binding_form(&node.s, &node.items, spec_binds) {
                let mut inner_bound = bound.clone();
                inner_bound.extend(form.binds);
                // Arguments are read in the order they are written, as every
                // other host reads them. A body may run once per element or
                // never, so what it assigns is not definite afterwards -- not
                // even for a later body of the same call. (Evaluation reads
                // TOP's limit before its keys, so this can name a variable the
                // run turns out not to need; over-reporting is allowed, and the
                // hosts agree on it.)
                for (i, arg) in node.items.iter().enumerate() {
                    match form.scopes[i] {
                        Scope::Binder => {}
                        Scope::Inner => {
                            let mut body = defined.clone();
                            collect_dependencies(arg, &inner_bound, reads, &mut body, depth + 1)?;
                        }
                        Scope::Outer => {
                            collect_dependencies(arg, bound, reads, defined, depth + 1)?;
                        }
                    }
                }
            } else {
                for (i, arg) in node.items.iter().enumerate() {
                    if matches!(node.s.as_str(), "GET" | "PATH") && i == 2 {
                        let mut fallback = defined.clone();
                        collect_dependencies(arg, bound, reads, &mut fallback, depth + 1)?;
                    } else {
                        collect_dependencies(arg, bound, reads, defined, depth + 1)?;
                    }
                }
            }
        }
        _ => {}
    }
    Ok(())
}

pub fn evaluate(source: &str, root: Option<Value>) -> Result<Value, SelError> {
    let mut prog = Program::compile(source)?;
    prog.run(root)
}
