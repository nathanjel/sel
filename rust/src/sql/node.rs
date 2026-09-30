use std::sync::Arc;

use crate::ast::{Node, NodeType};
use crate::builtins::Spec;
use crate::utf8::Pos;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SNodeType {
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
    CList,
}

impl SNodeType {
    pub fn as_str(&self) -> &'static str {
        match self {
            SNodeType::Num => "num",
            SNodeType::Text => "text",
            SNodeType::Bool => "bool",
            SNodeType::Null => "null",
            SNodeType::Var => "var",
            SNodeType::Index => "index",
            SNodeType::Seq => "seq",
            SNodeType::List => "list",
            SNodeType::Un => "un",
            SNodeType::Bin => "bin",
            SNodeType::Assign => "assign",
            SNodeType::Call => "call",
            SNodeType::CList => "clist",
        }
    }

    pub fn from_node_type(t: NodeType) -> Self {
        match t {
            NodeType::Num => SNodeType::Num,
            NodeType::Text => SNodeType::Text,
            NodeType::Bool => SNodeType::Bool,
            NodeType::Null => SNodeType::Null,
            NodeType::Var => SNodeType::Var,
            NodeType::Index => SNodeType::Index,
            NodeType::Seq => SNodeType::Seq,
            NodeType::List => SNodeType::List,
            NodeType::Un => SNodeType::Un,
            NodeType::Bin => SNodeType::Bin,
            NodeType::Assign => SNodeType::Assign,
            NodeType::Call => SNodeType::Call,
        }
    }

    pub fn to_node_type(&self) -> Option<NodeType> {
        match self {
            SNodeType::Num => Some(NodeType::Num),
            SNodeType::Text => Some(NodeType::Text),
            SNodeType::Bool => Some(NodeType::Bool),
            SNodeType::Null => Some(NodeType::Null),
            SNodeType::Var => Some(NodeType::Var),
            SNodeType::Index => Some(NodeType::Index),
            SNodeType::Seq => Some(NodeType::Seq),
            SNodeType::List => Some(NodeType::List),
            SNodeType::Un => Some(NodeType::Un),
            SNodeType::Bin => Some(NodeType::Bin),
            SNodeType::Assign => Some(NodeType::Assign),
            SNodeType::Call => Some(NodeType::Call),
            SNodeType::CList => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct CListEntry {
    pub key: String,
    pub val: Box<SNode>,
}

#[derive(Clone, Debug)]
pub struct SNode {
    pub t: SNodeType,
    pub pos: Pos,
    pub origin: Option<Arc<Node>>,
    pub str: String,
    pub bool_val: bool,
    pub grouped: bool,
    pub spec: Option<Arc<Spec>>,
    pub kids: Vec<SNode>,
    pub keys: Vec<String>,
    pub entries: Vec<CListEntry>,
}

impl SNode {
    pub fn leaf(n: &Node) -> Self {
        let spec = if n.t == NodeType::Call {
            crate::builtins::lookup_spec(&n.s)
        } else {
            None
        };
        Self {
            t: SNodeType::from_node_type(n.t),
            pos: n.pos,
            origin: Some(Arc::new(n.clone())),
            str: n.s.clone(),
            bool_val: n.b,
            grouped: n.grouped,
            spec,
            kids: Vec::new(),
            keys: Vec::new(),
            entries: Vec::new(),
        }
    }

    pub fn rewritten(shape: &Node, kids: Vec<SNode>) -> Self {
        let spec = if shape.t == NodeType::Call {
            crate::builtins::lookup_spec(&shape.s)
        } else {
            None
        };
        Self {
            t: SNodeType::from_node_type(shape.t),
            pos: shape.pos,
            origin: Some(Arc::new(shape.clone())),
            str: shape.s.clone(),
            bool_val: shape.b,
            grouped: shape.grouped,
            spec,
            kids,
            keys: Vec::new(),
            entries: Vec::new(),
        }
    }

    pub fn new_clist(pos: Pos) -> Self {
        Self {
            t: SNodeType::CList,
            pos,
            origin: None,
            str: String::new(),
            bool_val: false,
            grouped: false,
            spec: None,
            kids: Vec::new(),
            keys: Vec::new(),
            entries: Vec::new(),
        }
    }

    pub fn append(&mut self, key: impl Into<String>, val: SNode) {
        let k = key.into();
        self.keys.push(k.clone());
        self.kids.push(val.clone());
        self.entries.push(CListEntry {
            key: k,
            val: Box::new(val),
        });
    }

    pub fn clist(pos: Pos, entries: Vec<CListEntry>) -> Self {
        let mut keys = Vec::with_capacity(entries.len());
        let mut kids = Vec::with_capacity(entries.len());
        for e in &entries {
            keys.push(e.key.clone());
            kids.push((*e.val).clone());
        }
        Self {
            t: SNodeType::CList,
            pos,
            origin: None,
            str: String::new(),
            bool_val: false,
            grouped: false,
            spec: None,
            kids,
            keys,
            entries,
        }
    }

    pub fn l(&self) -> Option<&SNode> {
        self.kids.get(0)
    }

    pub fn r(&self) -> Option<&SNode> {
        self.kids.get(1)
    }

    pub fn obj(&self) -> Option<&SNode> {
        self.l()
    }

    pub fn idx(&self) -> Option<&SNode> {
        self.r()
    }

    pub fn target(&self) -> Option<&SNode> {
        self.l()
    }

    pub fn val(&self) -> Option<&SNode> {
        self.r()
    }

    pub fn args(&self) -> &[SNode] {
        &self.kids
    }

    pub fn items(&self) -> &[SNode] {
        &self.kids
    }

    pub fn to_node(&self) -> Option<Node> {
        if self.t == SNodeType::CList {
            return None;
        }
        if self.kids.is_empty() {
            if let Some(ref o) = self.origin {
                return Some((**o).clone());
            }
        }

        let node_type = self.t.to_node_type()?;
        let mut copy_node = Node::new(node_type, self.pos);
        copy_node.s = self.str.clone();
        copy_node.b = self.bool_val;
        copy_node.grouped = self.grouped;
        if let Some(ref o) = self.origin {
            copy_node.dec = o.dec.clone();
        }

        match self.t {
            SNodeType::Un => {
                let child = self.kids.get(0)?.to_node()?;
                copy_node.l = Some(Box::new(child));
            }
            SNodeType::Bin | SNodeType::Index | SNodeType::Assign => {
                let l = self.kids.get(0)?.to_node()?;
                let r = self.kids.get(1)?.to_node()?;
                copy_node.l = Some(Box::new(l));
                copy_node.r = Some(Box::new(r));
            }
            _ => {
                for kid in &self.kids {
                    let child = kid.to_node()?;
                    copy_node.items.push(child);
                }
            }
        }

        Some(copy_node)
    }
}
