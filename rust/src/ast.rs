use std::cell::Cell;
use std::sync::Arc;

use crate::dec::Dec;
use crate::shape::RecordShape;
use crate::utf8::Pos;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NodeType {
    Num,
    Text,
    Bool,
    Null,
    Var,
    Index,
    Seq,
    List,
    Un,
    Bin,
    Assign,
    Call,
}

#[derive(Clone, Copy, Debug)]
pub struct SlotCache {
    pub shape_id: u64,
    pub slot: usize,
}

/// A math-plan operation, resolved once when the plan is compiled from the
/// symbolic names of spec/math-ops.json (the interpreter matches on this,
/// never on a string).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OpCode {
    LoadVar,
    LoadConst,
    LoadLeaf,
    Add,
    Sub,
    Mul,
    Div,
    Mod,
    Neg,
    Abs,
    Sign,
    Ceil,
    Floor,
    Trunc,
    Round,
    Power,
    Min,
    Max,
}

impl OpCode {
    /// The code for one of the manifest's operations (`math_ops::OPS`).
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "ADD" => Self::Add,
            "SUB" => Self::Sub,
            "MUL" => Self::Mul,
            "DIV" => Self::Div,
            "MOD" => Self::Mod,
            "NEG" => Self::Neg,
            "ABS" => Self::Abs,
            "SIGN" => Self::Sign,
            "CEIL" => Self::Ceil,
            "FLOOR" => Self::Floor,
            "TRUNC" => Self::Trunc,
            "ROUND" => Self::Round,
            "POWER" => Self::Power,
            "MIN" => Self::Min,
            "MAX" => Self::Max,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug)]
pub struct MathStep {
    pub op: OpCode,
    pub dst: u16,
    pub src1: u16,
    pub src2: u16,
    pub pos: Pos,
    pub aux_pos: Pos,
    pub name: String,
    pub const_val: Option<Dec>,
    pub leaf_node: Option<Box<Node>>,
}

impl MathStep {
    /// A step with only its operation, destination and position set: the
    /// emitter fills in what each operation reads.
    pub(crate) fn new(op: OpCode, dst: u16, pos: Pos) -> Self {
        Self {
            op,
            dst,
            src1: 0,
            src2: 0,
            pos,
            aux_pos: Pos::default(),
            name: String::new(),
            const_val: None,
            leaf_node: None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct MathPlan {
    pub steps: Vec<MathStep>,
    pub output_slot: u16,
    pub scratchpad_size: u16,
}

#[derive(Debug)]
pub struct Node {
    pub t: NodeType,
    pub pos: Pos,
    pub s: String,
    pub b: bool,
    pub grouped: bool,
    /// SQL planning: this source reads the catalogue, not a same-named helper.
    pub sql_binding: bool,

    pub l: Option<Box<Node>>,
    pub r: Option<Box<Node>>,
    pub items: Vec<Node>,

    pub dec: Option<Dec>,
    pub shape: Option<Arc<RecordShape>>,
    pub slot_cache: Cell<Option<SlotCache>>,

    pub math_plan: Option<MathPlan>,
    pub keys_unobserved: bool,
    pub borrowed_filter: bool,
    /// Optimiser only: where a pipeline step stands in the tree as written (the
    /// outermost step is the call itself), for rewrites that would deepen a
    /// subtree -- FILTER fusion. 0 when unknown.
    pub step_depth: u16,
    pub spec: Option<Arc<crate::builtins::Spec>>,
}

impl Node {
    pub fn new(t: NodeType, pos: Pos) -> Self {
        Self {
            t,
            pos,
            s: String::new(),
            b: false,
            grouped: false,
            sql_binding: false,
            l: None,
            r: None,
            items: Vec::new(),
            dec: None,
            shape: None,
            slot_cache: Cell::new(None),
            math_plan: None,
            keys_unobserved: false,
            borrowed_filter: false,
            step_depth: 0,
            spec: None,
        }
    }
}

impl Node {
    /// This node's own fields, without its children (`l`, `r`, `items`).
    pub(crate) fn head(&self) -> Node {
        let n = self;
        Node {
                t: n.t,
                pos: n.pos,
                s: n.s.clone(),
                b: n.b,
                grouped: n.grouped,
                sql_binding: n.sql_binding,
                l: None,
                r: None,
                items: Vec::new(),
                dec: n.dec.clone(),
                shape: n.shape.clone(),
                slot_cache: n.slot_cache.clone(),
                math_plan: n.math_plan.clone(),
                keys_unobserved: n.keys_unobserved,
                borrowed_filter: n.borrowed_filter,
                step_depth: n.step_depth,
                spec: n.spec.clone(),
            }
    }
}

impl Clone for Node {
    fn clone(&self) -> Self {
        fn head(n: &Node) -> Node {
            n.head()
        }
        if self.l.is_none() && self.r.is_none() && self.items.is_empty() {
            return head(self);
        }
        let mut pending = vec![(self, false)];
        let mut completed = Vec::new();
        while let Some((node, visited)) = pending.pop() {
            if !visited {
                pending.push((node, true));
                pending.extend(node.items.iter().rev().map(|child| (child, false)));
                if let Some(child) = node.r.as_deref() {
                    pending.push((child, false));
                }
                if let Some(child) = node.l.as_deref() {
                    pending.push((child, false));
                }
            } else {
                let mut copy = head(node);
                copy.items = completed.split_off(completed.len() - node.items.len());
                if node.r.is_some() {
                    copy.r = Some(Box::new(completed.pop().unwrap()));
                }
                if node.l.is_some() {
                    copy.l = Some(Box::new(completed.pop().unwrap()));
                }
                completed.push(copy);
            }
        }
        completed.pop().unwrap()
    }
}

impl Drop for Node {
    fn drop(&mut self) {
        fn detach(node: &mut Node, pending: &mut Vec<Node>) {
            if let Some(child) = node.l.take() {
                pending.push(*child);
            }
            if let Some(child) = node.r.take() {
                pending.push(*child);
            }
            pending.append(&mut node.items);
            if let Some(mut plan) = node.math_plan.take() {
                for step in &mut plan.steps {
                    if let Some(child) = step.leaf_node.take() {
                        pending.push(*child);
                    }
                }
            }
        }
        if self.l.is_none() && self.r.is_none() && self.items.is_empty() && self.math_plan.is_none()
        {
            return;
        }
        let mut pending = Vec::new();
        detach(self, &mut pending);
        while let Some(mut child) = pending.pop() {
            detach(&mut child, &mut pending);
        }
    }
}
