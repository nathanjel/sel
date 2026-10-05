use crate::utf8::Pos;
use crate::sql::binding::Binding;
use crate::sql::node::SNode;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum BucketState {
    #[default]
    None,
    Open,
    Sealed,
}

#[derive(Clone, Debug)]
pub struct RelationalProjection {
    pub alias: Option<String>,
    pub binder: String,
    pub node: Option<SNode>,
    pub group_key: Option<RelationalGroup>,
}

#[derive(Clone, Debug)]
pub struct RelationalFilter {
    pub binder: String,
    pub node: SNode,
    pub pos: Pos,
    pub over_groups: bool,
}

/// An ORDER BY direction.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SortDirection {
    Asc,
    Desc,
}

impl SortDirection {
    pub fn as_sql(self) -> &'static str {
        match self {
            Self::Asc => "ASC",
            Self::Desc => "DESC",
        }
    }
}

/// LINK is an inner join, LINK_LEFT a left one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JoinType {
    Inner,
    Left,
}

#[derive(Clone, Debug)]
pub struct RelationalOrder {
    pub binder: String,
    pub node: SNode,
    pub dir: SortDirection,
    pub pos: Pos,
    pub over_groups: bool,
}

#[derive(Clone, Debug)]
pub struct RelationalGroup {
    pub alias: Option<String>,
    pub binder: String,
    pub node: SNode,
    pub pos: Pos,
}

#[derive(Clone, Debug)]
pub struct RelationalJoin {
    pub join_type: JoinType,
    pub source_name: String,
    pub source_relation: Option<Binding>,
    pub source_from_raw: bool,
    pub source_table: String,
    pub source_alias: String,
    pub left_names: Vec<String>,
    pub right_names: Vec<String>,
    pub on_pred: Option<SNode>,
    pub pos: Pos,
}

#[derive(Clone, Debug, Default)]
pub struct RelationalPlan {
    pub source_name: String,
    pub root_name: Option<String>,
    pub source_relation: Option<Binding>,
    pub source_from_raw: bool,
    pub source_table: String,
    pub source_alias: String,
    pub source_subquery: Option<Box<RelationalPlan>>,
    pub joins: Vec<RelationalJoin>,
    pub correlate: String,
    pub distinct: bool,
    pub select_cols: Option<Vec<String>>,
    pub projections: Option<Vec<RelationalProjection>>,
    pub filters: Vec<RelationalFilter>,
    pub group_by: Option<Vec<RelationalGroup>>,
    pub bucket: BucketState,
    pub bare_key: bool,
    pub having: Vec<RelationalFilter>,
    pub order_by: Vec<RelationalOrder>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
    // Set on a derived table built over sorted rows with no LIMIT: its ORDER BY is gone.
    pub order_dropped: bool,
}

impl RelationalPlan {
    pub fn new() -> Self {
        Self::default()
    }
}
