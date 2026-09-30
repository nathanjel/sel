use crate::sql::binding::{ColumnSpec, RelationSpec};
use crate::sql::node::SNode;
use crate::sql::relational_plan::RelationalProjection;
use crate::sql::row_model::RowModel;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BinderShape {
    Node,
    Column,
    Row,
    None,
    Key,
    Group,
    Projected,
}

#[derive(Clone, Debug)]
pub struct Binder {
    pub scope: Option<std::sync::Arc<Vec<Vec<(String, Binder)>>>>,
    pub shape: BinderShape,
    pub node: Option<SNode>,
    pub column: Option<ColumnSpec>,
    pub relation: Option<RelationSpec>,
    pub reason: String,
    pub group_binder: String,
    pub group_node: Option<SNode>,
    pub projections: Option<Vec<RelationalProjection>>,
    pub model: Option<RowModel>,
}

impl Binder {
    pub fn node(n: SNode) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Node,
            node: Some(n),
            column: None,
            relation: None,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn column(c: ColumnSpec) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Column,
            node: None,
            column: Some(c),
            relation: None,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn row(r: Option<RelationSpec>) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Row,
            node: None,
            column: None,
            relation: r,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn none(reason: impl Into<String>) -> Self {
        Self {
            scope: None,
            shape: BinderShape::None,
            node: None,
            column: None,
            relation: None,
            reason: reason.into(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn key(group_binder: impl Into<String>, group_node: Option<SNode>, r: Option<RelationSpec>) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Key,
            node: None,
            column: None,
            relation: r,
            reason: String::new(),
            group_binder: group_binder.into(),
            group_node,
            projections: None,
            model: None,
        }
    }

    pub fn group(r: Option<RelationSpec>) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Group,
            node: None,
            column: None,
            relation: r,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn projected(r: Option<RelationSpec>, projections: Vec<RelationalProjection>) -> Self {
        Self {
            scope: None,
            shape: BinderShape::Projected,
            node: None,
            column: None,
            relation: r,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: Some(projections),
            model: None,
        }
    }
}
