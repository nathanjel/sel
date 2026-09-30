use std::sync::Arc;

use crate::ast::{Node, NodeType};
use crate::context::Context;
use crate::dec::Dec;
use crate::eval::eval_node;
use crate::shape::RecordShape;
use crate::utf8::{Pos, SelError};
use crate::value::Value;

// Argument values are cached in a Vec as each is first evaluated. (An inline
// cache for up to six arguments was tried -- rust-performance-plan.md, phase 1:
// 2.4% fewer allocations but no measurable time, and a larger `Args` on the
// evaluator's recursive path -- so it was not kept.)
pub struct Args<'a> {
    pub nodes: &'a [Node],
    pub record_shape: Option<Arc<RecordShape>>,
    pub name: &'a str,
    pub pos: Pos,
    pub ctx: &'a mut Context,
    vals: Vec<Option<Value>>,
    pub borrowed_filter: bool,
}

impl<'a> Args<'a> {
    pub fn new(node: &'a Node, ctx: &'a mut Context) -> Self {
        let count = node.items.len();
        Self {
            nodes: &node.items,
            record_shape: node.shape.clone(),
            name: &node.s,
            pos: node.pos,
            ctx,
            vals: vec![None; count],
            borrowed_filter: node.borrowed_filter,
        }
    }

    /// Supplies argument `i` already evaluated (a pipeline stage's source).
    pub(crate) fn preset(&mut self, i: usize, value: Value) {
        self.vals[i] = Some(value);
    }

    pub fn count(&self) -> usize {
        self.nodes.len()
    }

    pub fn name(&self) -> &str {
        self.name
    }

    pub fn pos(&self) -> Pos {
        self.pos
    }

    fn missing(&self, i: usize) -> SelError {
        SelError::new(
            "E_BAD_ARG",
            format!("no argument {} (the call has {})", i.saturating_add(1), self.nodes.len()),
            self.pos,
        )
    }

    /// The node of argument `i`; E_BAD_ARG past the call's argument count.
    pub fn node(&self, i: usize) -> Result<&Node, SelError> {
        self.nodes.get(i).ok_or_else(|| self.missing(i))
    }

    /// The source position of argument `i`; E_BAD_ARG past the argument count.
    pub fn pos_of(&self, i: usize) -> Result<Pos, SelError> {
        self.node(i).map(|node| node.pos)
    }

    // Built-ins index only arguments their arity check has already admitted.
    pub(crate) fn node_at(&self, i: usize) -> &Node {
        &self.nodes[i]
    }

    pub(crate) fn pos_at(&self, i: usize) -> Pos {
        self.nodes.get(i).map_or(self.pos, |node| node.pos)
    }

    pub fn val(&mut self, i: usize) -> Result<Value, SelError> {
        if i >= self.nodes.len() {
            return Err(SelError::new(
                "E_BAD_ARG",
                format!(
                    "argument index {} is out of range for {} arguments",
                    i,
                    self.nodes.len()
                ),
                self.pos,
            ));
        }
        if let Some(ref v) = self.vals[i] {
            return Ok(v.clone());
        }
        let v = eval_node(&self.nodes[i], self.ctx)?;
        self.vals[i] = Some(v.clone());
        Ok(v)
    }

    pub fn eval_node(&mut self, n: &Node) -> Result<Value, SelError> {
        eval_node(n, self.ctx)
    }

    pub fn text(&mut self, i: usize) -> Result<String, SelError> {
        let pos = self.pos_at(i);
        self.val(i)?.as_text(pos)
    }

    pub fn bytes(&mut self, i: usize) -> Result<Vec<u8>, SelError> {
        let pos = self.pos_at(i);
        self.val(i)?.as_bytes(pos)
    }

    pub fn bool(&mut self, i: usize) -> Result<bool, SelError> {
        let pos = self.pos_at(i);
        self.val(i)?.as_bool(pos)
    }

    pub fn dec(&mut self, i: usize) -> Result<Dec, SelError> {
        let pos = self.pos_at(i);
        self.val(i)?.as_decimal(pos)
    }

    pub fn int(&mut self, i: usize) -> Result<i64, SelError> {
        let pos = self.pos_at(i);
        let d = self.dec(i)?;
        if !d.is_integer() {
            return Err(SelError::not_int(
                format!("{} argument {} must be a whole number", self.name, i + 1),
                pos,
            ));
        }
        Ok(d.to_safe_i64())
    }

    pub fn non_neg_int(&mut self, i: usize) -> Result<i64, SelError> {
        let pos = self.pos_at(i);
        let n = self.int(i)?;
        if n < 0 {
            return Err(SelError::range(
                format!("{} argument {} must not be negative", self.name, i + 1),
                pos,
            ));
        }
        Ok(n)
    }

    pub fn symbol(&self, i: usize) -> Result<String, SelError> {
        let n = self.nodes.get(i).ok_or_else(|| {
            SelError::new(
                "E_BAD_ARG",
                format!(
                    "argument index {} is out of range for {} arguments",
                    i,
                    self.nodes.len()
                ),
                self.pos,
            )
        })?;
        if n.t != NodeType::Var || n.grouped {
            return Err(SelError::new(
                "E_EXPECT_SYMBOL",
                format!("{} argument {} must be a plain name", self.name, i + 1),
                n.pos,
            ));
        }
        Ok(n.s.clone())
    }

    /// Whether argument `i` is a plain name; E_BAD_ARG past the argument count.
    pub fn is_symbol(&self, i: usize) -> Result<bool, SelError> {
        self.node(i).map(|n| n.t == NodeType::Var && !n.grouped)
    }

    pub(crate) fn is_symbol_at(&self, i: usize) -> bool {
        self.nodes
            .get(i)
            .is_some_and(|n| n.t == NodeType::Var && !n.grouped)
    }

    pub fn record_shape(&self) -> Option<Arc<RecordShape>> {
        self.record_shape.clone()
    }
}
