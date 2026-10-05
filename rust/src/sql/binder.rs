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
    pub scope: Option<std::rc::Rc<Vec<Vec<(String, Binder)>>>>,
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
    /// A binder of `shape` with nothing else set: each constructor names
    /// only what its shape carries.
    fn blank(shape: BinderShape) -> Self {
        Self {
            scope: None,
            shape,
            node: None,
            column: None,
            relation: None,
            reason: String::new(),
            group_binder: String::new(),
            group_node: None,
            projections: None,
            model: None,
        }
    }

    pub fn node(n: SNode) -> Self {
        Self { node: Some(n), ..Self::blank(BinderShape::Node) }
    }

    pub fn column(c: ColumnSpec) -> Self {
        Self { column: Some(c), ..Self::blank(BinderShape::Column) }
    }

    pub fn row(r: Option<RelationSpec>) -> Self {
        Self { relation: r, ..Self::blank(BinderShape::Row) }
    }

    pub fn none(reason: impl Into<String>) -> Self {
        Self { reason: reason.into(), ..Self::blank(BinderShape::None) }
    }

    pub fn key(group_binder: impl Into<String>, group_node: Option<SNode>, r: Option<RelationSpec>) -> Self {
        Self { relation: r, group_binder: group_binder.into(), group_node, ..Self::blank(BinderShape::Key) }
    }

    pub fn group(r: Option<RelationSpec>) -> Self {
        Self { relation: r, ..Self::blank(BinderShape::Group) }
    }

    pub fn projected(r: Option<RelationSpec>, projections: Vec<RelationalProjection>) -> Self {
        Self { relation: r, projections: Some(projections), ..Self::blank(BinderShape::Projected) }
    }
}
