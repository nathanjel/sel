use std::collections::HashSet;

use crate::ast::{Node, NodeType};
use crate::builtins::host_arity;
use crate::limits::{MAX_DEPTH, MAX_SQL_NODES};
use crate::regex::validate_pattern;
use crate::utf8::Pos;
use crate::value::{Kind, Value};
use crate::sql::binder::{Binder, BinderShape};
use crate::sql::binding::{Binding, BindingKind, Bindings, ColumnSpec, RelationSpec};
use crate::sql::constants::{
    constant_scale, is_binder_name, is_constant, require_numeric, validate,
};
use crate::sql::emit::Emit;
use crate::sql::errors::{refuse, SqlError};
use crate::sql::map::{
    chain, entry, host_spelling_arity, require_target, version, version_at_least, EntryKind,
    EntryRecord, TemplateValue,
};
use crate::sql::node::{SNode, SNodeType};
use crate::sql::normalise::normalise;
use crate::sql::relational_plan::{
    RelationalGroup, RelationalJoin, RelationalPlan, RelationalProjection,
};
use crate::sql::row_model::{build_join_rows, relation_alias, RowModel};
use crate::sql::types::{Fragment, Part, SqlKind};

#[derive(Clone, Copy, Debug, Default)]
pub struct Options {
    pub strict: bool,
}

pub struct Begun {
    pub norm: SNode,
    pub plan: Option<RelationalPlan>,
}

#[derive(Clone, Debug)]
pub enum Slot {
    Str(String),
    Frag(Fragment),
}

pub type SlotMap = Vec<(String, Vec<Slot>)>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum SourceShape {
    #[default]
    Static,
    Columns,
    Relation,
}

#[derive(Clone, Debug)]
pub struct SourceFilter {
    pub binder: String,
    pub node: SNode,
}

#[derive(Clone, Debug, Default)]
pub struct Source {
    pub shape: SourceShape,
    pub elements: Vec<(String, Binder)>,
    pub relation: Option<RelationSpec>,
    pub filters: Vec<SourceFilter>,
    pub scalar_rule: bool,
}

pub struct Translator {
    pub(crate) dialect: String,
    pub(crate) emit: Emit,
    pub(crate) bindings: Bindings,
    pub(crate) strict: bool,
    pub(crate) params: Vec<Value>,
    pub(crate) param_kinds: Vec<SqlKind>,
    pub(crate) caveats: Vec<String>,
    pub(crate) frames: Vec<Vec<(String, Binder)>>,
    pub(crate) const_names: HashSet<String>,
    pub(crate) const_root: Value,
    pub(crate) depth: usize,
    pub(crate) dispatched_nodes: usize,
    pub(crate) statement_plan: Option<RelationalPlan>,
    pub(crate) in_where: bool,
    pub(crate) subquery_counter: usize,
}

impl Translator {
    pub fn new(dialect: impl Into<String>, bindings: Option<Bindings>, options: Options) -> Self {
        let d = dialect.into();
        Self {
            emit: Emit::new(&d),
            dialect: d,
            bindings: bindings.unwrap_or_default(),
            strict: options.strict,
            params: Vec::new(),
            param_kinds: Vec::new(),
            caveats: Vec::new(),
            frames: Vec::new(),
            const_names: HashSet::new(),
            const_root: Value::null(),
            depth: 0,
            dispatched_nodes: 0,
            statement_plan: None,
            in_where: false,
            subquery_counter: 0,
        }
    }

    pub fn begin(&mut self, ast: &Node) -> Result<Begun, SqlError> {
        require_target(&self.dialect, ast.pos)?;
        self.bindings.check_aliases(ast.pos)?;

        self.params.clear();
        self.param_kinds.clear();
        self.caveats.clear();
        self.frames.clear();
        self.depth = 0;
        self.dispatched_nodes = 0;
        self.subquery_counter = 0;

        let (const_names, const_root) = crate::sql::constants::scope(Some(&self.bindings));
        self.const_names = const_names;
        self.const_root = const_root;

        let normalised = normalise(ast, Some(&self.const_names), Some(&self.const_root))?;
        let plan = self.analyze_pipeline(&normalised)?;
        Ok(Begun {
            norm: normalised,
            plan,
        })
    }

    pub fn translate(&mut self, ast: &Node) -> Result<Fragment, SqlError> {
        let b = self.begin(ast)?;
        if let Some(plan) = b.plan {
            return self.compile_statement(&plan);
        }
        let f = self.node(&b.norm)?;
        let mut out = Fragment::new(
            f.parts,
            f.kind,
            &self.dialect,
            self.params.clone(),
            self.param_kinds.clone(),
            self.caveats.clone(),
        );
        out.canonical = f.canonical;
        Ok(out)
    }

    pub fn translate_statement(&mut self, ast: &Node) -> Result<Fragment, SqlError> {
        let b = self.begin(ast)?;
        let plan = match b.plan {
            Some(p) => p,
            None => return refuse("E_SQL_SHAPE", "expected a relational query or pipeline", Pos::default()),
        };
        self.compile_statement(&plan)
    }

    pub fn add_caveat(&mut self, name: &str) {
        if !self.caveats.iter().any(|c| c == name) {
            self.caveats.push(name.to_string());
        }
    }

    pub fn node(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        self.dispatched_nodes += 1;
        if self.dispatched_nodes > MAX_SQL_NODES {
            return refuse(
                "E_SQL_SIZE",
                format!("this program expands to more than {} nodes once every helper read and every unrolled element is counted, and the translation stops there", MAX_SQL_NODES),
                n.pos,
            );
        }
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            self.depth -= 1;
            return refuse(
                "E_SQL_DEPTH",
                format!(
                    "this expression nests deeper than SEL will evaluate ({}), so there is nothing to translate; the evaluator answers E_DEPTH for it",
                    MAX_DEPTH
                ),
                n.pos,
            );
        }

        let compound = n.t == SNodeType::Bin || n.t == SNodeType::Un || n.t == SNodeType::Call;
        if !compound || !self.is_constant_here(n) {
            let res = self.dispatch(n);
            self.depth -= 1;
            return res;
        }

        let result = self.dispatch(n).and_then(|f| {
            validate(n, Some(&self.const_root))?;
            Ok(f)
        });
        self.depth -= 1;
        result
    }

    fn dispatch(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        match n.t {
            SNodeType::Num => {
                let dec_exact = n.origin.as_ref().and_then(|o| o.dec.clone());
                let val = if let Some(d) = dec_exact {
                    Value::num_exact(n.str.clone(), d)
                } else if let Ok(d) = crate::dec::dec_parse(&n.str, n.pos) {
                    Value::num_exact(n.str.clone(), d)
                } else {
                    Value::text_owned(n.str.clone())
                };
                Ok(self.literal(val, SqlKind::Num))
            }
            SNodeType::Text => self.checked_literal(Value::text_owned(n.str.clone()), SqlKind::Text, n.pos),
            SNodeType::Bool => Ok(self.literal(Value::bool(n.bool_val), SqlKind::Bool)),
            SNodeType::Var => self.variable(n),
            SNodeType::Index => self.index(n),
            SNodeType::Un => self.unary(n),
            SNodeType::Bin => self.binary(n),
            SNodeType::List | SNodeType::CList => refuse(
                "E_SQL_SHAPE",
                "a list is not a SQL value; a list can only be the thing an aggregate iterates",
                n.pos,
            ),
            SNodeType::Call => self.call(n),
            _ => refuse("E_SQL_SHAPE", format!("cannot translate a {:?} node", n.t), n.pos),
        }
    }

    fn checked_literal(&mut self, v: Value, kind: SqlKind, pos: Pos) -> Result<Fragment, SqlError> {
        if v.kind() == Kind::Text && v.scalar().contains('\0') {
            return refuse("E_SQL_UNSUPPORTED", "SQL text cannot contain NUL", pos);
        }
        Ok(self.literal(v, kind))
    }

    pub fn literal(&mut self, v: Value, kind: SqlKind) -> Fragment {
        self.params.push(v);
        let pk = if kind == SqlKind::Unknown || kind == SqlKind::List {
            SqlKind::Text
        } else {
            kind
        };
        self.param_kinds.push(pk);
        let p = Part::Slot(self.params.len());
        Fragment::new(vec![p], kind, &self.dialect, Vec::new(), Vec::new(), Vec::new())
    }

    pub fn binder(&self, name: &str) -> Option<Binder> {
        for frame in self.frames.iter().rev() {
            for kv in frame {
                if kv.0 == name {
                    return Some(kv.1.clone());
                }
            }
        }
        None
    }

    fn variable(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        if let Some(bound) = self.binder(&n.str) {
            return self.from_binder(&bound, n);
        }

        let b = self.bindings.get(&n.str, n.pos)?;
        match b.kind {
            BindingKind::Column => {
                let col = b.column.as_ref().unwrap();
                Ok(self.column_ref(col))
            }
            BindingKind::Value => {
                let v = b.val.as_ref().unwrap();
                if v.size() > 0 {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!(
                            "{} is bound to a list, and a list is not a SQL value; it can only be the thing an aggregate iterates",
                            n.str
                        ),
                        n.pos,
                    );
                }
                if v.is_none() {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!(
                            "{} is bound to an empty value, which is not a SQL value; only an aggregate can be given an empty binding",
                            n.str
                        ),
                        n.pos,
                    );
                }
                let kind = declared_kind(b, v);
                self.checked_literal(v.clone(), kind, n.pos)
            }
            BindingKind::Columns | BindingKind::Relation => {
                let kind_str = if b.kind == BindingKind::Columns {
                    "columns"
                } else {
                    "relation"
                };
                refuse(
                    "E_SQL_SHAPE",
                    format!(
                        "{} is bound as a {}, which names a set of values rather than one; use it as the first argument of an aggregate, not as a value on its own",
                        n.str, kind_str
                    ),
                    n.pos,
                )
            }
        }
    }

    pub fn column_ref(&self, c: &ColumnSpec) -> Fragment {
        let sql_str = if c.is_raw {
            c.raw.clone()
        } else {
            self.emit.column(&c.table, &c.column)
        };
        let mut f = Fragment::new(
            vec![Part::Sql(sql_str)],
            c.sql_type,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        );
        f.exact = c.exact;
        f.sargable = c.sargable;
        f.guard = c.guard;
        f.separate_prefilter = c.prefilter == "separate";
        f.canonical = c.canonical;
        f
    }

    pub fn constant_index(&self, idx: &SNode) -> Result<String, SqlError> {
        if idx.t == SNodeType::Num || idx.t == SNodeType::Text {
            return Ok(idx.str.clone());
        }
        refuse(
            "E_SQL_SHAPE",
            "an index must be a constant here: the column it names has to be known before the query runs",
            idx.pos,
        )
    }

    fn index(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let obj = n.l().unwrap();
        if self.statement_plan.is_some() && obj.t == SNodeType::Index {
            if let Some(row) = self.row_path(obj, n)? {
                let idx_str = self.constant_index(n.r().unwrap())?;
                return self.row_field(&row, "the row", &idx_str, n);
            }
            self.node(n.l().unwrap())?;
            return refuse(
                "E_SQL_SHAPE",
                "only a bound name can be indexed here; SQL has no way to index into the result of an expression",
                n.pos,
            );
        }
        if obj.t != SNodeType::Var {
            return refuse(
                "E_SQL_SHAPE",
                "only a bound name can be indexed here; SQL has no way to index into the result of an expression",
                n.pos,
            );
        }
        if let Some(bound) = self.binder(&obj.str) {
            let idx_str = self.constant_index(n.r().unwrap())?;
            return self.index_binder(&bound, &obj.str, &idx_str, n);
        }
        let b = self.bindings.get(&obj.str, obj.pos)?;
        let key = self.constant_index(n.r().unwrap())?;

        match b.kind {
            BindingKind::Relation => refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} is a relation, which is a list of rows; indexing it names no value SEL can produce, so use an aggregate and index the row its binder gives you",
                    obj.str
                ),
                n.pos,
            ),
            BindingKind::Columns => {
                let i = list_key(&key);
                let cols = b.columns.as_ref().unwrap();
                let count = cols.len();
                if i.is_none_or(|idx| idx == 0 || idx > count) {
                    return refuse(
                        "E_SQL_BINDING",
                        format!("{}[{}] is outside that binding's {} column(s)", obj.str, key, count),
                        n.pos,
                    );
                }
                Ok(self.column_ref(&cols[i.unwrap() - 1]))
            }
            BindingKind::Value => {
                let v = b.val.as_ref().unwrap();
                let child = v.get(&key);
                if child.is_none() {
                    return refuse(
                        "E_SQL_BINDING",
                        format!("{}[{:?}] is not a key of that value", obj.str, key),
                        n.pos,
                    );
                }
                let c = child.unwrap();
                if c.size() > 0 {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!("{}[{:?}] is a list, not a SQL value", obj.str, key),
                        n.pos,
                    );
                }
                self.checked_literal(c.clone(), declared_kind(b, &c), n.pos)
            }
            BindingKind::Column => refuse(
                "E_SQL_SHAPE",
                format!("{} is bound as a column, which has no parts to index", obj.str),
                n.pos,
            ),
        }
    }

    pub fn group_key(
        &mut self,
        src: &Source,
        gb: &RelationalGroup,
        projected: bool,
    ) -> Result<Fragment, SqlError> {
        let key = self.with_row(src, &gb.binder, |t| t.node(&gb.node))?;
        let identity = self.identity_group_key(&gb.node, &key)?;
        if projected && key.kind == SqlKind::Num {
            let mut parts = vec![Part::Sql("MIN(".to_string())];
            parts.extend(key.parts);
            parts.push(Part::Sql(")".to_string()));
            return Ok(Fragment::new(
                parts,
                SqlKind::Num,
                &self.dialect,
                key.params,
                key.param_kinds,
                key.caveats,
            ));
        }
        Ok(identity)
    }

    pub fn identity_group_key(&self, n: &SNode, f: &Fragment) -> Result<Fragment, SqlError> {
        if f.canonical && f.kind == SqlKind::Num {
            return Ok(f.clone());
        }
        if f.kind == SqlKind::Unknown {
            return refuse("E_SQL_SHAPE", "group keys require proven scalar identity", n.pos);
        }
        if f.kind == SqlKind::Num {
            if n.t != SNodeType::Var && n.t != SNodeType::Index && n.t != SNodeType::Num {
                return refuse(
                    "E_SQL_SHAPE",
                    "computed numeric group keys do not preserve SEL identity",
                    n.pos,
                );
            }
            let numeric = Fragment::new(
                f.parts.clone(),
                SqlKind::Num,
                &self.dialect,
                f.params.clone(),
                f.param_kinds.clone(),
                f.caveats.clone(),
            );
            let w = self.emit.text_operand(&numeric)?;
            let mut out = Fragment::new(
                w.parts,
                SqlKind::Text,
                &self.dialect,
                w.params,
                w.param_kinds,
                w.caveats,
            );
            out.exact = true;
            return Ok(out);
        }
        self.collated_key(f.clone())
    }

    pub fn order_key(&mut self, f: Fragment, pos: Pos) -> Result<Fragment, SqlError> {
        if f.kind == SqlKind::Num {
            return Ok(f);
        }
        if f.canonical {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "CANON is text on {}, which SQL sorts by its bytes, and SEL sorts it as the number it is; sort it in memory",
                    self.dialect
                ),
                pos,
            );
        }
        if f.kind == SqlKind::Text || f.kind == SqlKind::Unknown {
            if self.strict {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    "a text key sorts by its bytes in SQL, where SEL sorts number-shaped text as numbers (text-order); strict mode refuses that",
                    pos,
                );
            }
            self.add_caveat("text-order");
        }
        self.collated_key(f)
    }

    pub fn collated_key(&self, f: Fragment) -> Result<Fragment, SqlError> {
        if f.kind != SqlKind::Text || f.exact {
            return Ok(f);
        }
        let wrapped = self.emit.text_operand(&f)?;
        let mut out = Fragment::new(
            wrapped.parts,
            SqlKind::Text,
            &self.dialect,
            wrapped.params,
            wrapped.param_kinds,
            wrapped.caveats,
        );
        out.exact = true;
        Ok(out)
    }

    pub fn row_path(&self, node: &SNode, outer: &SNode) -> Result<Option<RowModel>, SqlError> {
        if node.t == SNodeType::Var {
            if let Some(b) = self.binder(&node.str) {
                if b.shape == BinderShape::Row {
                    return Ok(b.model);
                }
            }
            return Ok(None);
        }
        if node.t != SNodeType::Index {
            return Ok(None);
        }
        let inner = self.row_path(node.l().unwrap(), node)?;
        let row = match inner {
            Some(r) => r,
            None => return Ok(None),
        };
        let key = self.constant_index(node.r().unwrap())?;
        self.row_nested(&row, &key, node, outer).map(Some)
    }

    pub fn row_nested(
        &self,
        row: &RowModel,
        key: &str,
        n: &SNode,
        outer: &SNode,
    ) -> Result<RowModel, SqlError> {
        if let Some(nested) = row.nested_of(key) {
            return Ok(nested.clone());
        }
        if list_key(key).is_some() {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "[{}] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply",
                    key
                ),
                n.pos,
            );
        }
        if row.row_field_spec(key).is_some() {
            return refuse(
                "E_SQL_SHAPE",
                format!("[{:?}] is a field, which has no parts to index", key),
                outer.pos,
            );
        }
        refuse(
            "E_SQL_SHAPE",
            format!(
                "only a bound name can be indexed here; SQL has no way to index into the result of an expression ({} names no record this row carries)",
                key
            ),
            n.pos,
        )
    }

    pub fn row_field(&self, row: &RowModel, label: &str, key: &str, n: &SNode) -> Result<Fragment, SqlError> {
        if list_key(key).is_some() {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "{}[{}] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply",
                    label, key
                ),
                n.pos,
            );
        }
        if row.nested_of(key).is_some() {
            return refuse(
                "E_SQL_SHAPE",
                format!("{}[{:?}] is a record, which is a map in SEL and not one value; name the field you mean", label, key),
                n.pos,
            );
        }
        let f = match row.row_field_spec(key) {
            Some(f) => f,
            None => {
                if !row.side && row.dropped.contains(&key.to_ascii_uppercase()) {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!("field {:?} is ambiguous across joined relations", key),
                        n.pos,
                    );
                }
                let mut known = Vec::new();
                if row.side {
                    if let Some(ref rel) = row.relation {
                        for k in rel.fields.keys() {
                            known.push(k.clone());
                        }
                    }
                } else {
                    for field in &row.promoted {
                        known.push(field.0.clone());
                    }
                }
                let rel_str = if row.side { "relation" } else { "joined row" };
                let tail = if !known.is_empty() {
                    known.sort();
                    format!("; it has {}", known.join(", "))
                } else {
                    "; it declares none".to_string()
                };
                return refuse(
                    "E_SQL_BINDING",
                    format!("{}[{:?}] is not a field of that {}{}", label, key, rel_str, tail),
                    n.pos,
                );
            }
        };

        if f.spec.unavailable {
            return refuse("E_SQL_SHAPE", "a raw field cannot be read across a derived table", n.pos);
        }
        if f.optional {
            return refuse(
                "E_SQL_SHAPE",
                format!("{}[{:?}] is a field of the right side of a LINK_LEFT, which a row with no match does not have; read it through the right binder", label, key),
                n.pos,
            );
        }

        if f.spec.is_raw || !f.qualify {
            return Ok(self.column_ref(&f.spec));
        }
        let mut qualified = f.spec;
        qualified.table = f.table;
        Ok(self.column_ref(&qualified))
    }

    fn scoped_node(&self, node: SNode) -> Binder {
        let mut binder = Binder::node(node);
        binder.scope = Some(std::rc::Rc::new(self.frames.clone()));
        binder
    }

    fn in_binder_scope<T>(&mut self, binder: &Binder, f: impl FnOnce(&mut Self) -> Result<T, SqlError>) -> Result<T, SqlError> {
        let Some(scope) = &binder.scope else { return f(self) };
        let saved = std::mem::replace(&mut self.frames, scope.as_ref().clone());
        let result = f(self);
        self.frames = saved;
        result
    }

    pub fn from_binder(&mut self, b: &Binder, n: &SNode) -> Result<Fragment, SqlError> {
        match b.shape {
            BinderShape::Node => self.in_binder_scope(b, |t| t.node(b.node.as_ref().unwrap())),
            BinderShape::Key => {
                let mut frame = Vec::new();
                frame_set(
                    &mut frame,
                    &b.group_binder,
                    Binder::row(b.relation.clone()),
                );
                self.frames.push(frame);
                let key_res = self.node(b.group_node.as_ref().unwrap());
                self.frames.pop();
                let key = key_res?;

                let wrapped = key.kind == SqlKind::Num || (key.kind == SqlKind::Text && !key.exact);
                let collated = self.identity_group_key(b.group_node.as_ref().unwrap(), &key)?;

                if key.kind == SqlKind::Num {
                    let mut parts = vec![Part::Sql("MIN(".to_string())];
                    parts.extend(key.parts);
                    parts.push(Part::Sql(")".to_string()));
                    let mut out = Fragment::new(
                        parts,
                        SqlKind::Num,
                        &self.dialect,
                        key.params,
                        key.param_kinds,
                        key.caveats,
                    );
                    out.canonical = key.canonical;
                    return Ok(out);
                }
                if wrapped {
                    let mut parts = vec![Part::Sql("MIN(".to_string())];
                    parts.extend(collated.parts);
                    parts.push(Part::Sql(")".to_string()));
                    let mut out = Fragment::new(
                        parts,
                        SqlKind::Text,
                        &self.dialect,
                        collated.params,
                        collated.param_kinds,
                        collated.caveats,
                    );
                    out.exact = true;
                    out.canonical = key.canonical;
                    return Ok(out);
                }
                Ok(collated)
            }
            BinderShape::Column => Ok(self.column_ref(b.column.as_ref().unwrap())),
            BinderShape::Group => refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} is the list of a bucket's members, which is not a value SQL has; count it (COUNT), sum over it (SUM), or name the group key (_K)",
                    n.str
                ),
                n.pos,
            ),
            BinderShape::Projected => refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} is the record the projection built, which is a map in SEL and not one value; name the field you mean",
                    n.str
                ),
                n.pos,
            ),
            BinderShape::Row => {
                let rel = b.relation.as_ref().unwrap();
                if let Some(ref m) = b.model {
                    if !m.side {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "{} is a joined row, which is a map in SEL and not one value; name the field you mean",
                                n.str
                            ),
                            n.pos,
                        );
                    }
                }
                if rel.fields.len() > 1 {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!(
                            "{} is a row of a relation with {} fields, which is a map in SEL and not one value; name the field you mean",
                            n.str,
                            rel.fields.len()
                        ),
                        n.pos,
                    );
                }
                let mut field: Option<&ColumnSpec> = None;
                if !rel.scalar.is_empty() {
                    field = rel.field(&rel.scalar.to_ascii_uppercase());
                }
                let f = match field {
                    Some(f) => f,
                    None => {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "{} names a row, and the relation does not say which of its fields a bare reference means; give the binding a \"scalar\", or index the field you want",
                                n.str
                            ),
                            n.pos,
                        )
                    }
                };

                if f.unavailable {
                    return refuse("E_SQL_SHAPE", "a raw field cannot be read across a derived table", n.pos);
                }
                if let Some(ref m) = b.model {
                    if !f.is_raw && self.statement_plan.is_some() {
                        if !m.qualify {
                            return Ok(self.column_ref(f));
                        }
                        let mut qualified = f.clone();
                        qualified.table = m.table.clone();
                        return Ok(self.column_ref(&qualified));
                    }
                }
                Ok(self.relation_column(rel, f))
            }
            BinderShape::None => refuse("E_SQL_SHAPE", &b.reason, n.pos),
        }
    }

    pub fn index_binder(&mut self, b: &Binder, name: &str, key: &str, n: &SNode) -> Result<Fragment, SqlError> {
        if b.shape == BinderShape::Group {
            return refuse(
                "E_SQL_SHAPE",
                format!("{}[{:?}] indexes the list of a bucket's members, which SEL refuses (E_NO_KEY); read a member's field inside an aggregate over the group, SUM({}, _[{:?}])", name, key, name, key),
                n.pos,
            );
        }
        if b.shape == BinderShape::Projected {
            let projections = b.projections.as_ref().unwrap();
            let mut proj: Option<&RelationalProjection> = None;
            for p in projections {
                if p.alias.as_deref() == Some(key) {
                    proj = Some(p);
                    break;
                }
            }
            if proj.is_none() {
                let key_upper = key.to_ascii_uppercase();
                for p in projections {
                    if p.alias.as_ref().is_some_and(|a| a.to_ascii_uppercase() == key_upper) {
                        proj = Some(p);
                        break;
                    }
                }
            }
            let p = match proj {
                Some(p) => p,
                None => {
                    let mut known = Vec::new();
                    for cand in projections {
                        if let Some(ref a) = cand.alias {
                            known.push(a.clone());
                        }
                    }
                    known.sort();
                    let tail = if !known.is_empty() {
                        format!("; it has {}", known.join(", "))
                    } else {
                        String::new()
                    };
                    return refuse(
                        "E_SQL_SHAPE",
                        format!("{}[{:?}] is not a field of the projection{}", name, key, tail),
                        n.pos,
                    );
                }
            };

            let src = Source {
                shape: SourceShape::Relation,
                elements: Vec::new(),
                relation: b.relation.clone(),
                filters: Vec::new(),
                scalar_rule: false,
            };
            if let Some(ref gk) = p.group_key {
                let kb = Binder::key(&gk.binder, Some(gk.node.clone()), b.relation.clone());
                return self.from_binder(&kb, n);
            }
            return self.with_group(&src, &p.binder, |t| t.node(p.node.as_ref().unwrap()));
        }

        if b.shape == BinderShape::Row {
            if list_key(key).is_some() {
                return refuse(
                    "E_SQL_SHAPE",
                    format!(
                        "{}[{}] asks for a row by position, and a relation has no first row without an ORDER BY that nothing here can supply",
                        name, key
                    ),
                    n.pos,
                );
            }
            if let Some(ref m) = b.model {
                return self.row_field(m, name, key, n);
            }
            let rel = b.relation.as_ref().unwrap();
            let field = key.to_ascii_uppercase();
            if let Some(f) = rel.field(&field) {
                if f.unavailable {
                    return refuse("E_SQL_SHAPE", "a raw field cannot be read across a derived table", n.pos);
                }
                return Ok(self.relation_column(rel, f));
            }
            let mut known: Vec<String> = rel.fields.keys().cloned().collect();
            let tail = if !known.is_empty() {
                known.sort();
                format!("; it has {}", known.join(", "))
            } else {
                "; it declares none".to_string()
            };
            return refuse(
                "E_SQL_BINDING",
                format!("{}[{:?}] is not a field of that relation{}", name, key, tail),
                n.pos,
            );
        }

        if b.shape == BinderShape::Node {
            let elem = child_of(b.node.as_ref().unwrap(), key);
            let e = match elem {
                Some(e) => e,
                None => {
                    return refuse(
                        "E_SQL_BINDING",
                        format!("{}[{:?}] is not a key of that element", name, key),
                        n.pos,
                    )
                }
            };
            return self.in_binder_scope(b, |t| t.node(e));
        }

        refuse(
            "E_SQL_SHAPE",
            format!("{} names a single column, which has no parts to index", name),
            n.pos,
        )
    }

    pub fn relation_table_alias(&self, rel: Option<&RelationSpec>, def: &str) -> String {
        let r = match rel {
            Some(r) => r,
            None => return def.to_string(),
        };
        if let Some(ref plan) = self.statement_plan {
            if let Some(ref sp_rel) = plan.source_relation {
                if let Some(ref s_rel) = sp_rel.relation {
                    if same_relation(r, s_rel) {
                        if !plan.source_alias.is_empty() {
                            return plan.source_alias.clone();
                        }
                        return relation_alias(Some(r));
                    }
                }
            }
            for join in &plan.joins {
                if let Some(ref j_rel) = join.source_relation {
                    if let Some(ref js_rel) = j_rel.relation {
                        if same_relation(r, js_rel) {
                            if !join.source_alias.is_empty() {
                                return join.source_alias.clone();
                            }
                            return relation_alias(Some(r));
                        }
                    }
                }
            }
        }
        relation_alias(Some(r))
    }

    pub fn relation_column(&self, rel: &RelationSpec, c: &ColumnSpec) -> Fragment {
        if c.is_raw
            || self.statement_plan.is_none()
            || (self.statement_plan.as_ref().unwrap().joins.is_empty()
                && self.statement_plan.as_ref().unwrap().source_subquery.is_none())
        {
            return self.column_ref(c);
        }
        let mut qualified = c.clone();
        qualified.table = self.relation_table_alias(Some(rel), "");
        self.column_ref(&qualified)
    }

    fn arithmetic_operand(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let mark = self.params.len();
        let fragment = self.node(n)?;
        if fragment.kind != SqlKind::Text || !self.is_constant_here(n) {
            return Ok(fragment);
        }
        let Some(node) = n.to_node() else { return Ok(fragment); };
        let Ok(value) = crate::program::Program::new("", node).run(Some(self.const_root.clone())) else {
            return Ok(fragment);
        };
        if value.kind() != Kind::Text {
            return Ok(fragment);
        }
        let Ok(decimal) = value.as_decimal(n.pos) else { return Ok(fragment); };
        // Replacing a folded text expression must also reclaim its parameter
        // slots, so params mode does not expose unused bindings.
        self.params.truncate(mark);
        self.param_kinds.truncate(mark);
        Ok(self.literal(Value::num_trusted(decimal), SqlKind::Num))
    }

    fn unary(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let mut x = if n.str == "NOT" {
            self.node(n.l().unwrap())?
        } else {
            self.arithmetic_operand(n.l().unwrap())?
        };
        if n.str == "NOT" {
            x = self.require_bool(x, n.l().unwrap().pos, "NOT")?;
        } else {
            self.require_not_bool(&x, n.l().unwrap().pos, &n.str)?;
            x = self.guard_numeric(x, n.l().unwrap())?;
        }
        self.apply("ops", &n.str, &[&x], n.pos, None)
    }

    fn binary(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let op = &n.str;
        if op == "IN" {
            return self.in_operator(n);
        }

        let mut l = if is_arithmetic_op(op) {
            self.arithmetic_operand(n.l().unwrap())?
        } else {
            self.node(n.l().unwrap())?
        };
        let mut r = if is_arithmetic_op(op) {
            self.arithmetic_operand(n.r().unwrap())?
        } else {
            self.node(n.r().unwrap())?
        };

        if crate::ops::is_logic(op) {
            l = self.require_bool(l, n.l().unwrap().pos, op)?;
            r = self.require_bool(r, n.r().unwrap().pos, op)?;
        }
        if is_arithmetic_op(op) || is_numeric_op(op) {
            self.require_not_bool(&l, n.l().unwrap().pos, op)?;
            self.require_not_bool(&r, n.r().unwrap().pos, op)?;
            self.require_numeric_constant(n.l().unwrap())?;
            self.require_numeric_constant(n.r().unwrap())?;
            l = self.guard_numeric(l, n.l().unwrap())?;
            r = self.guard_numeric(r, n.r().unwrap())?;
        }
        if crate::ops::is_concat(op) || crate::ops::is_text_comparison(op) {
            self.require_not_bool_operand(&l, n.l().unwrap().pos, op)?;
            self.require_not_bool_operand(&r, n.r().unwrap().pos, op)?;
        }

        let variant = self.variant_for(op, &[&l, &r]);
        if variant == Some("coerce") {
            self.coerce_scale_limits(&[n.l().unwrap(), n.r().unwrap()])?;
        }

        if is_byte_comparison(op) {
            require_comparable_kinds(&l, &r, op, n.pos)?;
            let l_exact = l.exact;
            let r_exact = r.exact;
            let l_lit = n.l().is_some_and(|k| k.t == SNodeType::Text);
            let r_lit = n.r().is_some_and(|k| k.t == SNodeType::Text);
            let sargable_prefilter = self
                .emit
                .lex("sargablePrefilter")
                .and_then(|v| v.as_str().map(|s| s == "true"))
                .unwrap_or(false);

            if (l_exact && (r_exact || r_lit)) || (r_exact && l_lit) {
                // bare comparison
            } else if op == "$==" && ((l.sargable && r_lit) || (r.sargable && l_lit)) {
                if sargable_prefilter {
                    let coarse = self.apply("ops", "$==", &[&l, &r], n.pos, variant)?;
                    let l_text = self.emit.text_operand(&l)?;
                    let r_text = self.emit.text_operand(&r)?;
                    let residual = self.apply("ops", "$==", &[&l_text, &r_text], n.pos, variant)?;
                    let mut res = self.apply("ops", "AND", &[&coarse, &residual], n.pos, None)?;
                    res.prefilter = Some(Box::new(coarse));
                    res.separate_prefilter = l.separate_prefilter || r.separate_prefilter;
                    return Ok(res);
                }
            } else if l.kind != SqlKind::Bin || r.kind != SqlKind::Bin {
                l = self.emit.text_operand(&l)?;
                r = self.emit.text_operand(&r)?;
            }
        }

        let mut res = self.apply("ops", op, &[&l, &r], n.pos, variant)?;
        if op == "AND" {
            if l.prefilter.is_some() && r.prefilter.is_some() {
                let lp = l.prefilter.as_ref().unwrap();
                let rp = r.prefilter.as_ref().unwrap();
                res.prefilter = Some(Box::new(self.apply("ops", "AND", &[lp, rp], n.pos, None)?));
            } else if l.prefilter.is_some() {
                let lp = l.prefilter.as_ref().unwrap();
                res.prefilter = Some(Box::new(self.apply("ops", "AND", &[lp, &r], n.pos, None)?));
            } else if r.prefilter.is_some() {
                let rp = r.prefilter.as_ref().unwrap();
                res.prefilter = Some(Box::new(self.apply("ops", "AND", &[&l, rp], n.pos, None)?));
            }
            if l.separate_prefilter || r.separate_prefilter {
                res.separate_prefilter = true;
            }
        }
        Ok(res)
    }

    fn in_operator(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let rhs = n.r().unwrap();
        let rhs_is_free_var = rhs.t == SNodeType::Var && self.binder(&rhs.str).is_none();

        if rhs_is_free_var && self.bindings.has(&rhs.str) {
            let b = self.bindings.get(&rhs.str, rhs.pos)?.clone();
            if b.kind == BindingKind::Relation {
                let rel = b.relation.as_ref().unwrap();
                let mut scalar: Option<ColumnSpec> = None;
                if !rel.scalar.is_empty() {
                    scalar = rel.field(&rel.scalar.to_ascii_uppercase()).cloned();
                }
                let sc = match scalar {
                    Some(ref s) => s.clone(),
                    None => {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "IN over {} needs the binding to name a \"scalar\" field: that is the column the subquery projects",
                                rhs.str
                            ),
                            rhs.pos,
                        )
                    }
                };
                if rel.fields.len() != 1 {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!(
                            "IN over {} is refused: the relation declares {} fields, so SEL reads its rows as maps and a scalar can never equal one. Bind the projected column as a relation with that one field.",
                            rhs.str,
                            rel.fields.len()
                        ),
                        rhs.pos,
                    );
                }
                // The skeleton first: a dialect gap is named before whatever the
                // operands happen to be wrong about. Then compared as `==` compares:
                // a BOOL or BIN needle (or column) has no portable spelling against
                // text, so it is refused, at that operand, not cast and matched.
                let skel = self.skeleton("inRelation", n.pos)?;
                let needle_f = self.node(n.l().unwrap())?;
                let body_f = self.column_ref(&sc);
                for (f, at) in [(&needle_f, n.l().unwrap().pos), (&body_f, rhs.pos)] {
                    if f.kind == SqlKind::Bool || f.kind == SqlKind::Bin {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "IN over {} compares {} with text, which SQL would coerce and SEL never equates",
                                rhs.str,
                                if f.kind == SqlKind::Bool { "a boolean" } else { "binary data" }
                            ),
                            at,
                        );
                    }
                }
                let needle_text = self.emit.text_operand(&needle_f)?;
                let body_text = self.emit.text_operand(&body_f)?;

                let mut slots = self.relation_slots(rel);
                merge_slots(
                    &mut slots,
                    vec![
                        ("needle".to_string(), vec![Slot::Frag(needle_text)]),
                        ("body".to_string(), vec![Slot::Frag(body_text)]),
                    ],
                );
                let parts = self.fill_named(&skel, &slots, n.pos)?;
                return Ok(Fragment::new(
                    parts,
                    SqlKind::Bool,
                    &self.dialect,
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                ));
            }
        }

        let mut elements: Vec<SNode> = Vec::new();
        let mut has_elements = false;
        if rhs.t == SNodeType::List || rhs.t == SNodeType::CList {
            elements = rhs.kids.clone();
            has_elements = true;
        } else if rhs_is_free_var && self.bindings.has(&rhs.str) {
            let b = self.bindings.get(&rhs.str, rhs.pos)?;
            if b.kind == BindingKind::Value && b.val.as_ref().is_some_and(|v| v.size() > 0) {
                let elems = self.value_elements(b, rhs.pos)?;
                elements = elems.into_iter().map(|e| e.1.node.unwrap()).collect();
                has_elements = true;
            }
        }

        if !has_elements {
            let r = self.node(n.r().unwrap())?;
            let l = self.node(n.l().unwrap())?;
            require_comparable_kinds(&l, &r, "IN", n.pos)?;
            let l_text = self.emit.text_operand(&l)?;
            let r_text = self.emit.text_operand(&r)?;
            return self.apply("ops", "IN", &[&l_text, &r_text], n.pos, Some("scalar"));
        }

        if elements.is_empty() {
            return Ok(self.literal(Value::bool(false), SqlKind::Bool));
        }

        let mut tests = Vec::new();
        for e in &elements {
            let raw = self.node(n.l().unwrap())?;
            let f = self.node(e)?;
            require_comparable_kinds(&raw, &f, "IN", e.pos)?;
            let is_exact = raw.exact;
            let needle = if !is_exact {
                self.emit.text_operand(&raw)?
            } else {
                raw
            };
            if f.kind == SqlKind::List {
                return refuse(
                    "E_SQL_SHAPE",
                    "IN over a list of lists is structural in SEL and has no SQL counterpart",
                    e.pos,
                );
            }
            let item = if !is_exact || f.kind == SqlKind::Num {
                self.emit.text_operand(&f)?
            } else {
                f
            };
            tests.push(self.apply("ops", "EQL", &[&needle, &item], e.pos, Some("text"))?);
        }
        self.fold_pairwise("OR", tests, n.pos)
    }

    fn conditional(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let name = &n.str;
        let mut args: Vec<SNode> = n.kids.clone();
        if name == "IF" && args.len() == 2 {
            let null_node = Node::new(NodeType::Text, n.pos);
            args.push(SNode::leaf(&null_node));
        }
        let branch_tpl = self.skeleton("caseBranch", n.pos)?;
        let case_tpl = self.skeleton("case", n.pos)?;

        let last = args.len() - 1;
        let mut results = Vec::new();
        let mut joined = Vec::new();

        let mut i = 0;
        while i < last {
            let cond_f = self.node(&args[i])?;
            let cond = self.require_bool(cond_f, args[i].pos, name)?;
            let then = self.node(&args[i + 1])?;
            results.push(then.clone());

            let slots = vec![
                ("cond".to_string(), vec![Slot::Frag(cond)]),
                ("then".to_string(), vec![Slot::Frag(then)]),
            ];
            let branch = Fragment::new(
                self.fill_named(&branch_tpl, &slots, n.pos)?,
                SqlKind::Unknown,
                &self.dialect,
                Vec::new(),
                Vec::new(),
                Vec::new(),
            );
            if !joined.is_empty() {
                joined.push(Slot::Str(" ".to_string()));
            }
            joined.push(Slot::Frag(branch));
            i += 2;
        }

        let els = self.node(&args[last])?;
        results.push(els.clone());

        let slots = vec![
            ("branches".to_string(), joined),
            ("else".to_string(), vec![Slot::Frag(els)]),
        ];
        let unified_kind = unify(&results, n.pos)?;
        Ok(Fragment::new(
            self.fill_named(&case_tpl, &slots, n.pos)?,
            unified_kind,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
    }

    pub fn case_when(
        &mut self,
        cond: &Fragment,
        then: &Fragment,
        els: &Fragment,
        pos: Pos,
    ) -> Result<Fragment, SqlError> {
        let branch_slots = vec![
            ("cond".to_string(), vec![Slot::Frag(cond.clone())]),
            ("then".to_string(), vec![Slot::Frag(then.clone())]),
        ];
        // The skeletons through this translator, so a caveat a dialect attaches
        // to `case`/`caseBranch` is recorded on the translation.
        let branch_tpl = self.skeleton("caseBranch", pos)?;
        let branch = Fragment::new(
            self.fill_named(&branch_tpl, &branch_slots, pos)?,
            SqlKind::Unknown,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        );

        let case_tpl = self.skeleton("case", pos)?;
        let slots = vec![
            ("branches".to_string(), vec![Slot::Frag(branch)]),
            ("else".to_string(), vec![Slot::Frag(els.clone())]),
        ];
        let mut res_kind = SqlKind::Unknown;
        if then.kind == els.kind {
            res_kind = then.kind;
        }
        Ok(Fragment::new(
            self.fill_named(&case_tpl, &slots, pos)?,
            res_kind,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
    }

    // A binder position holds a name (the manifest's 'binder' scope). Anything else
    // is refused here, at that expression, whatever the call is later refused for.
    fn require_named_binders(&self, n: &SNode) -> Result<(), SqlError> {
        let shapes: Vec<Node> = n
            .kids
            .iter()
            .map(|k| {
                let mut shape = Node::new(k.t.to_node_type().unwrap_or(NodeType::List), k.pos);
                shape.s = k.str.clone();
                shape.grouped = k.grouped;
                shape
            })
            .collect();
        let spec_binds = n.spec.as_ref().is_some_and(|s| s.binds);
        if let Some(form) = crate::manifest::binding_form(&n.str, &shapes, spec_binds) {
            for (i, scope) in form.scopes.iter().enumerate() {
                if *scope == crate::manifest::builtins::Scope::Binder && !is_binder_name(n.kids.get(i)) {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!("the binder of {} must be a bare name, not an expression", n.str),
                        n.kids[i].pos,
                    );
                }
            }
        }
        Ok(())
    }

    fn call(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let name = &n.str;

        if self.statement_plan.is_some() && !n.kids.is_empty() && n.kids[0].t == SNodeType::Var {
            if let Some(group) = self.binder(&n.kids[0].str) {
                if group.shape == BinderShape::Group {
                    if name == "COUNT" && n.kids.len() == 1 {
                        return Ok(Fragment::new(
                            vec![Part::Sql("COUNT(*)".to_string())],
                            SqlKind::Num,
                            &self.dialect,
                            Vec::new(),
                            Vec::new(),
                            Vec::new(),
                        ));
                    }
                    if name == "SUM" && n.kids.len() >= 2 {
                        // A binder slot that is not a bare name is refused, as
                        // everywhere: SEL raises E_EXPECT_SYMBOL for it.
                        let (binder_name, body_node) = agg_shape(n)?;
                        let src = Source {
                            shape: SourceShape::Relation,
                            elements: Vec::new(),
                            relation: group.relation.clone(),
                            filters: Vec::new(),
                            scalar_rule: false,
                        };
                        let inner = self.with_row(&src, binder_name, |t| {
                            t.require_numeric_constant(body_node)?;
                            let rendered = t.node(body_node)?;
                            let rendered = t.require_num(rendered, body_node.pos, "SUM")?;
                            t.guard_sum(rendered, body_node, true)
                        })?;
                        if inner.whole_sum { return Ok(inner); }
                        let mut parts = vec![Part::Sql("COALESCE(SUM(".into())];
                        parts.extend(inner.parts);
                        parts.push(Part::Sql("), 0)".into()));
                        return Ok(Fragment::new(parts, SqlKind::Num, &self.dialect,
                            Vec::new(), Vec::new(), Vec::new()));
                    }
                }
            }
        }

        self.require_named_binders(n)?;

        if is_aggregate(name) {
            return self.aggregate(n);
        }
        if name == "COUNT" {
            return self.count(n);
        }
        if name == "HAS" {
            return self.has(n);
        }
        if name == "INDEXES" {
            return refuse(
                "E_SQL_SHAPE",
                "INDEXES yields a list of keys, and a SQL expression is a scalar",
                n.pos,
            );
        }
        if name == "ABORT" {
            return refuse(
                "E_SQL_UNSUPPORTED",
                "ABORT raises an error, which is a control-flow effect and not a value a SQL expression can be",
                n.pos,
            );
        }
        if name == "IF" || name == "COND" {
            return self.conditional(n);
        }
        if host_arity(name).is_some() {
            return self.host_call(n);
        }

        let rewritten = self.rewrite_regex(n)?;
        let mut args = Vec::new();
        for (i, arg) in rewritten.kids.iter().enumerate() {
            let mut f = if name == "MIN" || name == "MAX" {
                self.arithmetic_operand(arg)?
            } else {
                self.node(arg)?
            };
            if f.kind == SqlKind::List {
                return refuse(
                    "E_SQL_SHAPE",
                    format!("argument to {} is a list, and a SQL expression is a scalar", name),
                    arg.pos,
                );
            }
            self.require_argument_kind(name, &f, arg.pos)?;
            if is_numeric_argument(name, i) {
                self.require_numeric_constant(arg)?;
                f = self.guard_numeric(f, arg)?;

            }
            args.push(f);
        }

        let arg_refs: Vec<&Fragment> = args.iter().collect();
        let mut out = self.apply("funcs", name, &arg_refs, n.pos, None)?;
        if name == "CANON" {
            out.canonical = true;
        }
        Ok(out)
    }

    fn host_call(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let name = &n.str;
        let raw = entry(&self.dialect, "funcs", name);
        let entry_rec = match raw {
            Some(ref e) if e.kind != EntryKind::Refusal || !e.reason.is_empty() => e,
            _ => {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "{} is a host function with no SQL spelling in dialect {}; register one with the map, or evaluate it here",
                        name, self.dialect
                    ),
                    n.pos,
                )
            }
        };

        if entry_rec.kind == EntryKind::Refusal {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!("{} has no mapping in dialect {} — {}", name, self.dialect, entry_rec.reason),
                n.pos,
            );
        }

        let recorded = host_spelling_arity(&self.dialect, name);
        let current_arity = host_arity(name);
        if let (Some(rec_ar), Some(curr_ar)) = (recorded, current_arity) {
            if rec_ar[0] != curr_ar.0 || rec_ar[1] != curr_ar.1 {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "{} was registered again with the arity [{}, {}] after its SQL spelling was defined for [{}, {}]; define the spelling again",
                        name, curr_ar.0, curr_ar.1, rec_ar[0], rec_ar[1]
                    ),
                    n.pos,
                );
            }
        }

        if self.strict {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "{} is spelled by the application, which SEL cannot check (host-function), and strict mode refuses that",
                    name
                ),
                n.pos,
            );
        }
        self.add_caveat("host-function");

        let mut args = Vec::new();
        for (i, arg) in n.kids.iter().enumerate() {
            let mut kind = "ANY";
            if i < entry_rec.args.len() {
                kind = &entry_rec.args[i];
            }
            if kind == "LIST" {
                args.push(self.host_list_argument(name, arg)?);
                continue;
            }
            let mut f = self.node(arg)?;
            if f.kind == SqlKind::List {
                return refuse(
                    "E_SQL_SHAPE",
                    format!("argument to {} is a list, and its spelling does not declare a LIST there", name),
                    arg.pos,
                );
            }
            let got = f.kind.as_str();
            if (kind == "NUM" || kind == "TEXT") && (f.kind == SqlKind::Bool || f.kind == SqlKind::Bin) {
                return refuse(
                    "E_SQL_SHAPE",
                    format!("{} declares this argument {}, and this is a {}", name, kind, got),
                    arg.pos,
                );
            }
            if (kind == "BOOL" || kind == "BIN") && got != kind {
                let val_desc = if f.kind == SqlKind::Unknown {
                    "not one this layer can prove".to_string()
                } else {
                    format!("a {}", got)
                };
                return refuse(
                    "E_SQL_SHAPE",
                    format!("{} declares this argument {}, and this is {}", name, kind, val_desc),
                    arg.pos,
                );
            }
            if kind == "NUM" {
                self.require_numeric_constant(arg)?;
                if f.kind != SqlKind::Num {
                    f = self.guard_numeric(f, arg)?;
                }
            }
            args.push(f);
        }

        let arg_refs: Vec<&Fragment> = args.iter().collect();
        self.apply("funcs", name, &arg_refs, n.pos, None)
    }

    fn host_list_argument(&mut self, name: &str, arg: &SNode) -> Result<Fragment, SqlError> {
        let src = self.classify(arg)?;
        if src.shape == SourceShape::Relation {
            return refuse(
                "E_SQL_SHAPE",
                format!("argument to {} is a relation, rows the query has not read yet; a LIST argument is a list known when translating", name),
                arg.pos,
            );
        }
        if !src.filters.is_empty() {
            return refuse(
                "E_SQL_SHAPE",
                format!("argument to {} is a filtered list, whose elements are decided when it is evaluated; a template cannot express that", name),
                arg.pos,
            );
        }
        if src.elements.is_empty() {
            return refuse(
                "E_SQL_SHAPE",
                format!("argument to {} is an empty list, which has nothing for the template to hold", name),
                arg.pos,
            );
        }
        let mut parts = Vec::new();
        for (i, elem) in src.elements.iter().enumerate() {
            let f = self.from_binder(&elem.1, arg)?;
            if f.kind == SqlKind::List {
                return refuse(
                    "E_SQL_SHAPE",
                    format!("argument to {} has a list as an element, which has no scalar rendering", name),
                    arg.pos,
                );
            }
            if i > 0 {
                parts.push(Part::Sql(", ".to_string()));
            }
            parts.extend(f.parts);
        }
        Ok(Fragment::new(
            parts,
            SqlKind::Unknown,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
    }

    pub fn classify(&mut self, src: &SNode) -> Result<Source, SqlError> {
        let mut out = Source::default();
        if src.t == SNodeType::Call {
            let name = &src.str;
            if name == "FILTER" {
                let (binder_name, body) = agg_shape(src)?;
                let mut inner = self.classify(&src.kids[0])?;
                inner.filters.push(SourceFilter {
                    binder: binder_name.to_string(),
                    node: body.clone(),
                });
                return Ok(inner);
            }
            if name == "MAP" {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    "MAP as the thing an aggregate iterates is not translated: unlike FILTER, which only decides whether an element takes part, MAP changes what the element is, so the two binders mean different things and binding both to one element is not enough. See docs/internals/sql-translation.md 7.5",
                    src.pos,
                );
            }
        }

        if src.t == SNodeType::List {
            for (i, kid) in src.kids.iter().enumerate() {
                out.elements.push((
                    (i + 1).to_string(),
                    self.scoped_node(kid.clone()),
                ));
            }
            return Ok(out);
        }
        if src.t == SNodeType::CList {
            for (i, kid) in src.kids.iter().enumerate() {
                out.elements.push((
                    src.keys[i].clone(),
                    self.scoped_node(kid.clone()),
                ));
            }
            return Ok(out);
        }
        if src.t == SNodeType::Var {
            if let Some(bound) = self.binder(&src.str) {
                match bound.shape {
                    BinderShape::Node => return self.in_binder_scope(&bound, |t| t.classify(bound.node.as_ref().unwrap())),
                    BinderShape::None => return refuse("E_SQL_SHAPE", &bound.reason, src.pos),
                    BinderShape::Group => {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "{} is the list of a bucket's members, over which only COUNT and SUM are translated",
                                src.str
                            ),
                            src.pos,
                        );
                    }
                    BinderShape::Projected => {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "{} is the record the projection built, a map with one child per field; SQL has no way to iterate or count that",
                                src.str
                            ),
                            src.pos,
                        );
                    }
                    BinderShape::Row => {
                        if bound.relation.as_ref().is_some_and(|r| r.fields.len() > 1) {
                            return refuse(
                                "E_SQL_SHAPE",
                                format!(
                                    "{} is a row of a multi-field relation, which is a map with one child per field; SQL has no way to iterate or count that",
                                    src.str
                                ),
                                src.pos,
                            );
                        }
                    }
                    BinderShape::Column | BinderShape::Key => {}
                }
                out.elements.push(("1".to_string(), bound));
                out.scalar_rule = true;
                return Ok(out);
            }

            let b = self.bindings.get(&src.str, src.pos)?;
            if b.kind == BindingKind::Relation {
                out.shape = SourceShape::Relation;
                out.relation = b.relation.clone();
                return Ok(out);
            }
            if b.kind == BindingKind::Columns {
                out.shape = SourceShape::Columns;
                for (i, col) in b.columns.as_ref().unwrap().iter().enumerate() {
                    out.elements.push((
                        (i + 1).to_string(),
                        Binder::column(col.clone()),
                    ));
                }
                return Ok(out);
            }
            if b.kind == BindingKind::Value {
                let v = b.val.as_ref().unwrap();
                out.elements = self.value_elements(b, src.pos)?;
                out.scalar_rule = v.size() == 0 && !v.is_none();
                return Ok(out);
            }
        }

        if src.t == SNodeType::Call
            && yields_list(&src.str)
        {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} yields a list, and the scalar rule does not apply to it; SQL has no way to count or index what it produces",
                    src.str
                ),
                src.pos,
            );
        }

        if src.t == SNodeType::Call {
            self.node(src)?;
        }
        out.elements.push(("1".to_string(), self.scoped_node(src.clone())));
        out.scalar_rule = true;
        Ok(out)
    }

    pub fn with_element<F>(
        &mut self,
        binder_name: &str,
        elem: Binder,
        key: &str,
        n: &SNode,
        render: F,
    ) -> Result<Fragment, SqlError>
    where
        F: FnOnce(&mut Translator) -> Result<Fragment, SqlError>,
    {
        let mut frame = Vec::new();
        frame_set(&mut frame, binder_name, elem.clone());
        frame_set(
            &mut frame,
            "_K",
            {
                let mut key_node = Node::new(NodeType::Text, n.pos);
                key_node.s = key.to_string();
                Binder::node(SNode::leaf(&key_node))
            },
        );
        self.frames.push(frame);
        let res = render(self);
        self.frames.pop();
        res
    }

    pub fn with_row<F>(
        &mut self,
        src: &Source,
        binder_name: &str,
        render: F,
    ) -> Result<Fragment, SqlError>
    where
        F: FnOnce(&mut Translator) -> Result<Fragment, SqlError>,
    {
        let alias = crate::sql::row_model::relation_alias(src.relation.as_ref());
        for frame in &self.frames {
            for kv in frame {
                if kv.1.shape == BinderShape::Row {
                    let other_alias = crate::sql::row_model::relation_alias(kv.1.relation.as_ref());
                    if other_alias == alias {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!(
                                "this relation is already open as {} further out, and a subquery reusing its own alias shadows the outer row rather than comparing against it; the correlation names the alias, so it cannot be renamed here",
                                alias
                            ),
                            Pos::default(),
                        );
                    }
                }
            }
        }

        let mut row = Binder::row(src.relation.clone());
        if let Some(ref plan) = self.statement_plan {
            if let Some(ref sp_rel) = plan.source_relation {
                if let Some(ref r_plan) = sp_rel.relation {
                    if let Some(ref r_src) = src.relation {
                        if same_relation(r_src, r_plan) {
                            row.model = Some(build_join_rows(plan)?.row);
                        }
                    }
                }
            }
        }

        let mut frame = Vec::new();
        frame_set(&mut frame, binder_name, row.clone());
        frame_set(
            &mut frame,
            "_K",
            Binder::none("a row of a relation has no key: SQL rows are unordered and unkeyed unless the schema says otherwise, and guessing which column is the key is not something this layer does"),
        );
        self.frames.push(frame);
        let res = render(self);
        self.frames.pop();
        res
    }

    pub fn with_group<F>(
        &mut self,
        src: &Source,
        binder_name: &str,
        render: F,
    ) -> Result<Fragment, SqlError>
    where
        F: FnOnce(&mut Translator) -> Result<Fragment, SqlError>,
    {
        let mut frame = Vec::new();
        frame_set(&mut frame, binder_name, Binder::group(src.relation.clone()));
        if let Some(ref plan) = self.statement_plan {
            if let Some(ref gb_list) = plan.group_by {
                if gb_list.len() == 1 {
                    let gb = &gb_list[0];
                    frame_set(
                        &mut frame,
                        "_K",
                        Binder::key(&gb.binder, Some(gb.node.clone()), src.relation.clone()),
                    );
                } else {
                    frame_set(
                        &mut frame,
                        "_K",
                        Binder::none("the key of a bucket over several keys is a list, which SQL has no value for; name one key"),
                    );
                }
            } else {
                frame_set(
                    &mut frame,
                    "_K",
                    Binder::none("the key of a bucket over several keys is a list, which SQL has no value for; name one key"),
                );
            }
        }
        self.frames.push(frame);
        let res = render(self);
        self.frames.pop();
        res
    }

    pub fn with_projected<F>(
        &mut self,
        src: &Source,
        binder_name: &str,
        render: F,
    ) -> Result<Fragment, SqlError>
    where
        F: FnOnce(&mut Translator) -> Result<Fragment, SqlError>,
    {
        let projections = self
            .statement_plan
            .as_ref()
            .and_then(|p| p.projections.clone())
            .unwrap_or_default();
        let mut frame = Vec::new();
        frame_set(
            &mut frame,
            binder_name,
            Binder::projected(src.relation.clone(), projections),
        );
        frame_set(
            &mut frame,
            "_K",
            Binder::none("after a projection the rows are a list renumbered from \"1\", and SQL has no row position to compare against"),
        );
        self.frames.push(frame);
        let res = render(self);
        self.frames.pop();
        res
    }

    pub fn with_join_binders<F>(
        &mut self,
        plan: &RelationalPlan,
        join: &RelationalJoin,
        render: F,
    ) -> Result<Fragment, SqlError>
    where
        F: FnOnce(&mut Translator) -> Result<Fragment, SqlError>,
    {
        let jr = build_join_rows(plan)?;
        let mut join_idx = None;
        for (i, j) in plan.joins.iter().enumerate() {
            if std::ptr::eq(j, join) {
                join_idx = Some(i);
                break;
            }
        }
        if join_idx.is_none() {
            for (i, j) in plan.joins.iter().enumerate() {
                if j.source_name == join.source_name
                    && j.source_table == join.source_table
                    && j.source_alias == join.source_alias
                {
                    join_idx = Some(i);
                    break;
                }
            }
        }
        let step = &jr.steps[join_idx.unwrap()];
        let mut frame = Vec::new();
        let mut left = Binder::row(plan.source_relation.as_ref().and_then(|b| b.relation.clone()));
        left.model = Some(step.left.clone());
        let mut right = Binder::row(join.source_relation.as_ref().and_then(|b| b.relation.clone()));
        right.model = Some(step.right.clone());

        frame_set(&mut frame, "_", left.clone());
        frame_set(&mut frame, "_1", left.clone());
        frame_set(&mut frame, "_2", right.clone());
        for name in &join.left_names {
            frame_set(&mut frame, name, left.clone());
        }
        for name in &join.right_names {
            frame_set(&mut frame, name, right.clone());
        }
        self.frames.push(frame);
        let res = render(self);
        self.frames.pop();
        res
    }

    fn agg_body(
        &mut self,
        name: &str,
        body: &SNode,
        src: &Source,
        n: &SNode,
    ) -> Result<Fragment, SqlError> {
        let mut q = self.node(body)?;
        if name == "SUM" {
            q = self.require_num(q, body.pos, name)?;
        } else {
            q = self.require_bool(q, body.pos, name)?;
        }

        for f in &src.filters {
            // A FILTER predicate has its own binder. Neither the consuming
            // aggregate's binder nor another FILTER's binder is in its scope.
            let consumer = self.frames.pop().expect("aggregate scope");
            let mut predicate = Vec::new();
            frame_set(&mut predicate, &f.binder, consumer[0].1.clone());
            if let Some((_, key)) = consumer.iter().find(|(name, _)| name == "_K") {
                frame_set(&mut predicate, "_K", key.clone());
            }
            self.frames.push(predicate);
            let rendered = self.node(&f.node);
            self.frames.pop();
            self.frames.push(consumer);
            let p_f = rendered?;
            let p = self.require_bool(p_f, f.node.pos, "FILTER")?;
            if name == "SUM" {
                let zero = self.literal(Value::text_owned("0".to_string()), SqlKind::Num);
                q = self.case_when(&p, &q, &zero, n.pos)?;
            } else if name == "ALL" {
                let not_p = self.apply("ops", "NOT", &[&p], n.pos, None)?;
                q = self.apply("ops", "OR", &[&not_p, &q], n.pos, None)?;
            } else {
                q = self.apply("ops", "AND", &[&p, &q], n.pos, None)?;
            }
        }
        if name == "SUM" {
            q = self.guard_sum(q, body, src.shape == SourceShape::Relation)?;
        }
        Ok(q)
    }

    fn guard_sum(&mut self, q: Fragment, body: &SNode, whole: bool) -> Result<Fragment, SqlError> {
        if q.kind != SqlKind::Unknown || self.is_constant_here(body) {
            return Ok(q);
        }
        if !whole { return self.guard_numeric(q, body); }
        crate::sql::map::check_numeric_guard(&self.dialect);
        let guard = self.emit.lex("numericGuard").and_then(|v| v.as_str().map(str::to_owned))
            .ok_or_else(|| SqlError::new("E_SQL_UNSUPPORTED", "dialect cannot guard an undeclared SUM body", body.pos))?;
        let halves = guard.strip_prefix("CASE WHEN ").and_then(|s| s.strip_suffix(" ELSE NULL END"))
            .and_then(|s| s.split_once(" THEN "));
        let Some((test_tpl, cast_tpl)) = halves else {
            return refuse("E_SQL_UNSUPPORTED", "numericGuard cannot be split for SUM", body.pos);
        };
        self.scale_limited(body.pos, "this operand is read as a number")?;
        let test = self.emit.fill(test_tpl, &[&q], body.pos, None)?;
        // PostgreSQL evaluates aggregate inputs before the outer CASE, so
        // invalid text must be protected at the cast as well as at the sum.
        let cast_template = if chain(&self.dialect).iter().any(|d| d == "postgresql") {
            guard.as_str()
        } else {
            cast_tpl
        };
        let cast = self.emit.fill(cast_template, &[&q], body.pos, None)?;
        let mut parts = vec![Part::Sql("CASE WHEN COUNT(*) = COUNT(CASE WHEN ".into())];
        parts.extend(test);
        parts.push(Part::Sql(" THEN 1 END) THEN COALESCE(SUM(".into()));
        parts.extend(cast);
        parts.push(Part::Sql("), 0) ELSE NULL END".into()));
        let mut out = Fragment::new(parts, SqlKind::Num, &self.dialect, Vec::new(), Vec::new(), Vec::new());
        out.whole_sum = true;
        Ok(out)
    }

    fn relation_aggregate(
        &mut self,
        name: &str,
        rel: &RelationSpec,
        body: Fragment,
        n: &SNode,
    ) -> Result<Fragment, SqlError> {
        let is_separate =
            (rel.prefilter == "separate") || (rel.prefilter.is_empty() && body.separate_prefilter);
        if name == "ANY" && body.prefilter.is_some() && is_separate {
            let mut pre_slots = self.relation_slots(rel);
            let bp = body.prefilter.as_ref().unwrap();
            merge_slots(
                &mut pre_slots,
                vec![("body".to_string(), vec![Slot::Frag((**bp).clone())])],
            );
            let skel_pre = self.skeleton("prefilter", n.pos)?;
            let pre = Fragment::new(
                self.fill_named(&skel_pre, &pre_slots, n.pos)?,
                agg_returns(name),
                &self.dialect,
                Vec::new(),
                Vec::new(),
                Vec::new(),
            );

            let mut main_slots = self.relation_slots(rel);
            merge_slots(
                &mut main_slots,
                vec![("body".to_string(), vec![Slot::Frag(body)])],
            );
            let skel_main = self.skeleton(&agg_skeleton(name), n.pos)?;
            let main = Fragment::new(
                self.fill_named(&skel_main, &main_slots, n.pos)?,
                agg_returns(name),
                &self.dialect,
                Vec::new(),
                Vec::new(),
                Vec::new(),
            );
            return self.apply("ops", "AND", &[&pre, &main], n.pos, None);
        }

        let mut skel = self.skeleton(&agg_skeleton(name), n.pos)?;
        if body.whole_sum {
            let marker = "COALESCE(SUM({body}), 0)";
            if !skel.contains(marker) {
                return refuse("E_SQL_UNSUPPORTED", "SUM skeleton cannot contain the whole-sum guard", n.pos);
            }
            skel = skel.replace(marker, "{body}");
        }
        let mut slots = self.relation_slots(rel);
        merge_slots(
            &mut slots,
            vec![("body".to_string(), vec![Slot::Frag(body)])],
        );
        Ok(Fragment::new(
            self.fill_named(&skel, &slots, n.pos)?,
            agg_returns(name),
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
    }

    fn aggregate(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let name = &n.str;
        if name == "MAP" || name == "FILTER" {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} yields a list, and a SQL expression is a scalar; it can only be the thing another aggregate iterates",
                    name
                ),
                n.pos,
            );
        }
        if name == "JOIN" {
            return self.join_aggregate(n);
        }

        let (binder_name, body_node) = agg_shape(n)?;
        let src = self.classify(&n.kids[0])?;

        if src.shape == SourceShape::Relation {
            let rel = src.relation.clone().unwrap();
            let rendered = self.with_row(&src, binder_name, |t| {
                t.agg_body(name, body_node, &src, n)
            })?;
            return self.relation_aggregate(name, &rel, rendered, n);
        }

        let mut parts = Vec::new();
        for kv in &src.elements {
            let key = &kv.0;
            let elem = kv.1.clone();
            parts.push(self.with_element(binder_name, elem, key, n, |t| {
                t.agg_body(name, body_node, &src, n)
            })?);
        }

        if parts.is_empty() {
            if name == "ALL" {
                return Ok(self.literal(Value::bool(true), SqlKind::Bool));
            }
            if name == "ANY" {
                return Ok(self.literal(Value::bool(false), SqlKind::Bool));
            }
            return Ok(self.literal(Value::text_owned("0".to_string()), SqlKind::Num));
        }
        if parts.len() == 1 {
            return Ok(parts.remove(0));
        }
        self.fold_pairwise(agg_fold(name), parts, n.pos)
    }

    fn count(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let src = self.classify(&n.kids[0])?;

        if src.shape != SourceShape::Relation && src.scalar_rule && src.filters.is_empty() {
            return Ok(self.literal(Value::text_owned("0".to_string()), SqlKind::Num));
        }

        if !src.filters.is_empty() {
            let mut num_node = Node::new(NodeType::Num, n.pos);
            num_node.s = "1".to_string();
            let body = SNode::leaf(&num_node);
            if src.shape == SourceShape::Relation {
                let rel = src.relation.clone().unwrap();
                let rendered = self.with_row(&src, "_", |t| {
                    t.agg_body("SUM", &body, &src, n)
                })?;
                return self.relation_aggregate("SUM", &rel, rendered, n);
            }
            let mut parts = Vec::new();
            for kv in &src.elements {
                let key = &kv.0;
                let elem = kv.1.clone();
                parts.push(self.with_element("_", elem, key, n, |t| {
                    t.agg_body("SUM", &body, &src, n)
                })?);
            }
            if parts.is_empty() {
                return Ok(self.literal(Value::text_owned("0".to_string()), SqlKind::Num));
            }
            if parts.len() == 1 {
                return Ok(parts.remove(0));
            }
            return self.fold_pairwise("+", parts, n.pos);
        }

        if src.shape == SourceShape::Relation {
            let rel = src.relation.as_ref().unwrap();
            let skel = self.skeleton("count", n.pos)?;
            let slots = self.relation_slots(rel);
            return Ok(Fragment::new(
                self.fill_named(&skel, &slots, n.pos)?,
                SqlKind::Num,
                &self.dialect,
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ));
        }
        Ok(self.literal(Value::text_owned(src.elements.len().to_string()), SqlKind::Num))
    }

    fn has(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let key_node = &n.kids[1];
        if key_node.t != SNodeType::Text && key_node.t != SNodeType::Num {
            return refuse(
                "E_SQL_SHAPE",
                "HAS needs a constant key here: which column it asks about has to be known before the query runs",
                key_node.pos,
            );
        }
        let key = &key_node.str;
        let src = self.classify(&n.kids[0])?;
        if !src.filters.is_empty() {
            return refuse(
                "E_SQL_SHAPE",
                "HAS over a FILTER would have to know at translation time which elements the filter kept",
                n.pos,
            );
        }
        if src.shape == SourceShape::Relation {
            return refuse(
                "E_SQL_SHAPE",
                "HAS over a relation asks whether it has a key, and a relation is a list of rows whose keys are positions; the answer needs the row count, which no expression here knows",
                n.pos,
            );
        }
        let mut found = false;
        if !src.scalar_rule {
            for elem in &src.elements {
                if &elem.0 == key {
                    found = true;
                    break;
                }
            }
        }
        Ok(self.literal(Value::bool(found), SqlKind::Bool))
    }

    fn join_aggregate(&mut self, n: &SNode) -> Result<Fragment, SqlError> {
        let src = self.classify(&n.kids[0])?;
        if !src.filters.is_empty() {
            return refuse("E_SQL_SHAPE", "JOIN cannot absorb a FILTER", n.kids[0].pos);
        }

        if src.shape == SourceShape::Relation {
            let rel = src.relation.as_ref().unwrap();
            let mut scalar: Option<&ColumnSpec> = None;
            if !rel.scalar.is_empty() {
                scalar = rel.field(&rel.scalar.to_ascii_uppercase());
            }
            let sc = match scalar {
                Some(s) => s,
                None => {
                    return refuse(
                        "E_SQL_SHAPE",
                        "JOIN over a relation needs the binding to name a \"scalar\" field",
                        n.pos,
                    )
                }
            };
            let body = self.column_ref(sc);
            self.require_join_text(&body, n.kids[0].pos)?;
            let sep_frag = self.node(&n.kids[1])?;
            self.require_join_text(&sep_frag, n.kids[1].pos)?;
            let skel = self.skeleton("join", n.pos)?;
            let mut slots = self.relation_slots(rel);
            merge_slots(
                &mut slots,
                vec![
                    ("body".to_string(), vec![Slot::Frag(body)]),
                    ("sep".to_string(), vec![Slot::Frag(sep_frag)]),
                ],
            );
            return Ok(Fragment::new(
                self.fill_named(&skel, &slots, n.pos)?,
                SqlKind::Text,
                &self.dialect,
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ));
        }

        let mut parts = Vec::new();
        for kv in &src.elements {
            let key = &kv.0;
            let elem = kv.1.clone();
            if !parts.is_empty() {
                let sep = self.node(&n.kids[1])?;
                self.require_join_text(&sep, n.kids[1].pos)?;
                parts.push(sep);
            }
            let held = elem.clone();
            let element = self.with_element("_", held.clone(), key, n, |t| {
                t.from_binder(&held, n)
            })?;
            self.require_join_text(&element, n.kids[0].pos)?;
            parts.push(element);
        }
        if src.elements.len() < 2 {
            let separator = self.node(&n.kids[1])?;
            self.require_join_text(&separator, n.kids[1].pos)?;
        }
        if parts.is_empty() {
            return Ok(self.literal(Value::text_owned("".to_string()), SqlKind::Text));
        }
        if parts.len() == 1 {
            return Ok(parts.remove(0));
        }
        self.fold_pairwise("&", parts, n.pos)
    }

    fn require_join_text(&self, value: &Fragment, pos: Pos) -> Result<(), SqlError> {
        if matches!(value.kind, SqlKind::Bool | SqlKind::Bin) {
            return refuse("E_SQL_SHAPE", "JOIN requires text-compatible elements and separator", pos);
        }
        Ok(())

    }

    pub fn skeleton(&mut self, name: &str, pos: Pos) -> Result<String, SqlError> {
        let raw = entry(&self.dialect, "skel", name);
        let entry_rec = match raw {
            Some(ref e) => e,
            None => {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!("dialect {} has no {} skeleton", self.dialect, name),
                    pos,
                )
            }
        };

        if entry_rec.kind == EntryKind::Refusal {
            if entry_rec.reason.is_empty() {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!("dialect {} has no {} skeleton", self.dialect, name),
                    pos,
                );
            }
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!("dialect {} cannot express {} — {}", self.dialect, name, entry_rec.reason),
                pos,
            );
        }
        if entry_rec.kind == EntryKind::Builder {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!("the {} skeleton for {} is a builder, and a skeleton is a template", name, self.dialect),
                pos,
            );
        }
        if !entry_rec.caveat.is_empty() {
            if self.strict {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "the {} skeleton for {} is not exactly equivalent ({}), and strict mode refuses those",
                        name, self.dialect, entry_rec.caveat
                    ),
                    pos,
                );
            }
            self.add_caveat(&entry_rec.caveat);
        }
        if let Some(TemplateValue::Single(ref s)) = entry_rec.tpl {
            return Ok(s.clone());
        }
        refuse(
            "E_SQL_UNSUPPORTED",
            format!("dialect {} has no {} skeleton", self.dialect, name),
            pos,
        )
    }

    pub fn fill_named(&self, tpl: &str, slots: &SlotMap, pos: Pos) -> Result<Vec<Part>, SqlError> {
        let mut parts = Vec::new();
        let push = |parts: &mut Vec<Part>, s: &str| {
            if s.is_empty() {
                return;
            }
            if let Some(last) = parts.last_mut() {
                if let Part::Sql(ref mut sql) = last {
                    sql.push_str(s);
                    return;
                }
            }
            parts.push(Part::Sql(s.to_string()));
        };

        let bytes = tpl.as_bytes();
        let mut i = 0;
        while i < bytes.len() {
            if bytes[i] != b'{' {
                let width = tpl[i..].chars().next().unwrap().len_utf8();
                push(&mut parts, &tpl[i..i + width]);
                i += width;
                continue;
            }
            let end_rel = match bytes[i..].iter().position(|&b| b == b'}') {
                Some(p) => p,
                None => {
                    push(&mut parts, std::str::from_utf8(&bytes[i..]).unwrap());
                    break;
                }
            };
            let end = i + end_rel;
            let name = std::str::from_utf8(&bytes[i + 1..end]).unwrap();
            i = end + 1;

            let mut items: Option<&Vec<Slot>> = None;
            for kv in slots {
                if kv.0 == name {
                    items = Some(&kv.1);
                    break;
                }
            }
            let it = match items {
                Some(it) => it,
                None => {
                    return refuse(
                        "E_SQL_UNSUPPORTED",
                        format!(
                            "a skeleton in dialect {} uses {{{}}}, which is not one of its slots",
                            self.dialect, name
                        ),
                        pos,
                    )
                }
            };

            for item in it {
                match item {
                    Slot::Str(s) => push(&mut parts, s),
                    Slot::Frag(f) => {
                        for p in &f.parts {
                            match p {
                                Part::Sql(ref s) => push(&mut parts, s),
                                Part::Slot(_) => parts.push(p.clone()),
                            }
                        }
                    }
                }
            }
        }
        Ok(parts)
    }

    pub fn apply(
        &mut self,
        section: &str,
        key: &str,
        args: &[&Fragment],
        pos: Pos,
        variant: Option<&str>,
    ) -> Result<Fragment, SqlError> {
        let raw = entry(&self.dialect, section, key);
        let what = if section == "ops" {
            format!("the {} operator", key)
        } else {
            key.to_string()
        };
        let entry_rec = match raw {
            Some(ref e) => e,
            None => {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!("{} has no mapping in dialect {}", what, self.dialect),
                    pos,
                )
            }
        };

        if entry_rec.kind == EntryKind::Refusal {
            let reason = if !entry_rec.reason.is_empty() {
                format!(" — {}", entry_rec.reason)
            } else {
                String::new()
            };
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!("{} has no mapping in dialect {}{}", what, self.dialect, reason),
                pos,
            );
        }

        if entry_rec.kind == EntryKind::Builder {
            let b = entry_rec.builder.as_ref().unwrap();
            let owned_args: Vec<Fragment> = args.iter().map(|&f| f.clone()).collect();
            return b(&self.emit, &owned_args, pos);
        }

        if let Some(ar) = entry_rec.arity {
            let n = args.len();
            if n < ar[0] || n > ar[1] {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!("{} has no mapping in dialect {} for {} argument(s)", what, self.dialect, n),
                    pos,
                );
            }
        }

        if !entry_rec.since.is_empty() && !version_at_least(&version(&self.dialect), &entry_rec.since) {
            return refuse(
                "E_SQL_DIALECT",
                format!(
                    "{} needs {} {} or newer, and this map says {}",
                    what,
                    self.dialect,
                    entry_rec.since,
                    version(&self.dialect)
                ),
                pos,
            );
        }

        if !entry_rec.caveat.is_empty() {
            if self.strict {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "{} translates only approximately in dialect {} ({}), and strict mode refuses those",
                        what, self.dialect, entry_rec.caveat
                    ),
                    pos,
                );
            }
            self.add_caveat(&entry_rec.caveat);
        }

        let tpl = self.template_of(entry_rec, args, variant, &what, pos)?;
        let parts = self.emit.fill(&tpl, args, pos, None)?;
        let rk = ret_kind(entry_rec, args, pos)?;
        Ok(Fragment::new(
            parts,
            rk,
            &self.dialect,
            Vec::new(),
            Vec::new(),
            Vec::new(),
        ))
    }

    pub fn fold_pairwise(
        &mut self,
        op: &str,
        parts: Vec<Fragment>,
        pos: Pos,
    ) -> Result<Fragment, SqlError> {
        self.dispatched_nodes = self.dispatched_nodes.saturating_add(parts.len().saturating_sub(1));
        if self.dispatched_nodes > MAX_SQL_NODES {
            return refuse(
                "E_SQL_SIZE",
                format!("this program expands to more than {} nodes once every helper read and every unrolled element is counted, and the translation stops there", MAX_SQL_NODES),
                pos,
            );
        }
        self.fold_parts(op, parts, pos)
    }

    fn fold_parts(&mut self, op: &str, mut parts: Vec<Fragment>, pos: Pos) -> Result<Fragment, SqlError> {
        if parts.len() > 256 {
            let right = parts.split_off(parts.len().div_ceil(2));
            let left = self.fold_parts(op, parts, pos)?;
            let right = self.fold_parts(op, right, pos)?;
            let variant = self.variant_for(op, &[&left, &right]);
            return self.apply("ops", op, &[&left, &right], pos, variant);
        }
        let mut acc = parts.remove(0);
        for part in parts {
            let variant = self.variant_for(op, &[&acc, &part]);
            acc = self.apply("ops", op, &[&acc, &part], pos, variant)?;
        }
        Ok(acc)
    }

    pub fn variant_for(&self, op: &str, args: &[&Fragment]) -> Option<&'static str> {
        if is_numeric_op(op) {
            if args[0].kind == SqlKind::Num && args[1].kind == SqlKind::Num {
                return Some("num");
            }
            return Some("coerce");
        }
        if is_textual_op(op) {
            return Some("text");
        }
        if op == "&" {
            if args[0].kind == SqlKind::Bin || args[1].kind == SqlKind::Bin {
                return Some("bin");
            }
            return Some("text");
        }
        None
    }

    fn template_of(
        &self,
        entry: &EntryRecord,
        args: &[&Fragment],
        variant: Option<&str>,
        what: &str,
        pos: Pos,
    ) -> Result<String, SqlError> {
        if !entry.variants.is_empty() {
            let arm = variant.and_then(|v| entry.variants.get(v)).filter(|s| !s.is_empty());
            if arm.is_none() {
                let var_desc = variant
                    .map(|v| format!("{} operands", v))
                    .unwrap_or_else(|| "this shape".to_string());
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!("{} has no mapping in dialect {} for {}", what, self.dialect, var_desc),
                    pos,
                );
            }
            return Ok(arm.unwrap().clone());
        }

        match entry.tpl {
            Some(TemplateValue::Single(ref s)) => Ok(s.clone()),
            Some(TemplateValue::Map(ref m)) => {
                let n = args.len().to_string();
                let chosen = if let Some(arm) = m.get(&n) {
                    if !arm.is_empty() {
                        Some(arm)
                    } else {
                        None
                    }
                } else { m.get("*").filter(|&star| !star.is_empty()) };
                if let Some(c) = chosen {
                    return Ok(c.clone());
                }
                let mut keys: Vec<String> = m.keys().filter(|k| !m[*k].is_empty()).cloned().collect();
                keys.sort();
                refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "{} has no mapping in dialect {} for {} argument(s); it maps {}",
                        what,
                        self.dialect,
                        n,
                        keys.join(", ")
                    ),
                    pos,
                )
            }
            None => refuse(
                "E_SQL_UNSUPPORTED",
                format!("{} has no template in dialect {}", what, self.dialect),
                pos,
            ),
        }
    }

    pub fn relation_slots(&self, rel: &RelationSpec) -> SlotMap {
        let mut from = if rel.from.is_raw {
            rel.from.raw.clone()
        } else {
            self.emit.ident(&rel.from.table)
        };
        if !rel.alias.is_empty() {
            from.push(' ');
            from.push_str(&self.emit.ident(&rel.alias));
        }
        let mut corr = self
            .emit
            .lex("true")
            .and_then(|v| v.as_str().map(String::from))
            .unwrap_or_else(|| "true".to_string());
        if !rel.correlate.is_empty() {
            corr = format!("({})", rel.correlate);
        }
        vec![
            ("from".to_string(), vec![Slot::Str(from)]),
            ("corr".to_string(), vec![Slot::Str(corr)]),
        ]
    }

    fn value_node(&self, v: &Value, b: &Binding, pos: Pos) -> Result<SNode, SqlError> {
        if v.size() > 0 {
            let mut entries = Vec::new();
            for e in v.entries() {
                entries.push((e.key.clone(), self.value_node(&e.val, b, pos)?));
            }
            return Ok(SNode::clist(pos, entries));
        }
        if v.is_bool() {
            let mut bool_node = Node::new(NodeType::Bool, pos);
            bool_node.b = v.as_bool(pos).unwrap_or(false);
            return Ok(SNode::leaf(&bool_node));
        }
        if v.is_bin() {
            return refuse(
                "E_SQL_SHAPE",
                "a BIN element of a value binding has no literal node to become; bind it as a column, or convert it before translating",
                pos,
            );
        }
        if v.is_none() {
            // A NULL element: SEL's as_text would raise E_NULL. It is a binding
            // problem, and a refusal.
            return refuse(
                "E_SQL_BINDING",
                "a value binding holds a NULL element, which has no SQL literal",
                pos,
            );
        }
        let is_num = b.value_type == Some(SqlKind::Num);
        let node_type = if is_num { NodeType::Num } else { NodeType::Text };
        let mut txt_node = Node::new(node_type, pos);
        txt_node.s = v.as_text(pos).unwrap_or_default();
        Ok(SNode::leaf(&txt_node))
    }

    pub fn value_elements(
        &self,
        b: &Binding,
        pos: Pos,
    ) -> Result<Vec<(String, Binder)>, SqlError> {
        let v = b.val.as_ref().unwrap();
        if v.size() == 0 {
            if v.is_none() {
                return Ok(Vec::new());
            }
            return Ok(vec![(
                "1".to_string(),
                Binder::node(self.value_node(v, b, pos)?),
            )]);
        }
        let mut out = Vec::new();
        for e in v.entries() {
            out.push((
                e.key.clone(),
                Binder::node(self.value_node(&e.val, b, pos)?),
            ));
        }
        Ok(out)
    }

    fn rewrite_regex(&self, n: &SNode) -> Result<SNode, SqlError> {
        let (pat_at, flag_at) = match regex_call(&n.str) {
            Some(rx) => (rx.pattern, rx.flags),
            None => return Ok(n.clone()),
        };
        let mut args = n.kids.clone();
        let pat = args.get(pat_at);
        if pat.is_none_or(|p| p.t != SNodeType::Text) {
            let p_pos = pat.map_or(n.pos, |p| p.pos);
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "{} needs a literal pattern here: SEL rewrites \\d, \\w and \\s into explicit ASCII classes before matching, and a pattern that is not known until the query runs cannot be rewritten",
                    n.str
                ),
                p_pos,
            );
        }

        let pat_str = &pat.unwrap().str;
        let pat_pos = pat.unwrap().pos;
        let source = match validate_pattern(pat_str, pat_pos) {
            Ok(s) => s,
            Err(se) => {
                return refuse(
                    "E_SQL_UNSUPPORTED",
                    format!(
                        "{}'s pattern is not in SEL's portable subset, so there is nothing to translate: {}",
                        n.str, se.message
                    ),
                    pat_pos,
                );
            }
        };

        let mut inline_flags = "(?s)";
        if flag_at >= args.len() {
            let mut pat_node = Node::new(NodeType::Text, pat_pos);
            pat_node.s = format!("{}{}", inline_flags, source);
            args[pat_at] = SNode::leaf(&pat_node);
            let mut res = n.clone();
            res.kids = args;
            return Ok(res);
        }

        let flags = &args[flag_at];
        if flags.t != SNodeType::Text {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "{} needs literal flags here: their content selects the mapping, so they have to be known before the query runs",
                    n.str
                ),
                flags.pos,
            );
        }
        let text = &flags.str;
        if !text.is_empty() && text != "i" {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "{} accepts only the i flag here, and SEL accepts only i at all; {:?} is not it",
                    n.str, text
                ),
                flags.pos,
            );
        }
        if !text.is_empty() {
            for b in source.bytes() {
                if b > 0x7f {
                    return refuse(
                        "E_SQL_UNSUPPORTED",
                        "the i flag needs an ASCII-only pattern, which SEL requires for the same reason and refuses here too",
                        flags.pos,
                    );
                }
            }
            inline_flags = "(?si)";
            crate::regex::validate_pattern_with_case(pat_str, pat_pos, true)
                .map_err(|e| SqlError::new("E_SQL_UNSUPPORTED", e.message, pat_pos))?;
        }
        let mut pat_node = Node::new(NodeType::Text, pat_pos);
        pat_node.s = format!("{}{}", inline_flags, source);
        args[pat_at] = SNode::leaf(&pat_node);
        args.remove(flag_at);
        let mut res = n.clone();
        res.kids = args;
        Ok(res)
    }

    pub fn require_bool(&self, f: Fragment, pos: Pos, where_str: &str) -> Result<Fragment, SqlError> {
        if f.kind == SqlKind::Bool {
            return Ok(f);
        }
        refuse(
            "E_SQL_SHAPE",
            format!(
                "{} needs a BOOL here and this is {}; SEL has no truthiness, so neither does its translation",
                where_str, f.kind
            ),
            pos,
        )
    }

    pub fn require_num(&self, f: Fragment, pos: Pos, where_str: &str) -> Result<Fragment, SqlError> {
        if f.kind == SqlKind::Num || f.kind == SqlKind::Unknown {
            return Ok(f);
        }
        refuse(
            "E_SQL_SHAPE",
            format!("{} adds its body up, so it needs a number here and this is {}", where_str, f.kind),
            pos,
        )
    }

    pub fn require_not_bool(&self, f: &Fragment, pos: Pos, where_str: &str) -> Result<(), SqlError> {
        if f.kind != SqlKind::Bool && f.kind != SqlKind::Bin {
            return Ok(());
        }
        let what = if f.kind == SqlKind::Bool { "a BOOL" } else { "a BIN" };
        refuse(
            "E_SQL_SHAPE",
            format!(
                "{} reads its operands as numbers, and {} is not one; SEL answers E_NOT_NUM here rather than coercing it",
                where_str, what
            ),
            pos,
        )
    }

    pub fn require_not_bool_operand(&self, f: &Fragment, pos: Pos, where_str: &str) -> Result<(), SqlError> {
        if f.kind != SqlKind::Bool {
            return Ok(());
        }
        refuse(
            "E_SQL_SHAPE",
            format!(
                "{} reads its operands as text or bytes, and a BOOL is neither; SEL answers E_NOT_TEXT here rather than spelling it 1 or true",
                where_str
            ),
            pos,
        )
    }

    fn is_constant_here(&self, node: &SNode) -> bool {
        if self.frames.is_empty() {
            return is_constant(Some(node), Some(&self.const_names));
        }
        // Aggregate binders shadow value bindings, including ones that happen
        // to have a literal value in the application's root context.
        let mut names = self.const_names.clone();
        for frame in &self.frames {
            for (name, _) in frame { names.remove(name); }
        }
        is_constant(Some(node), Some(&names))
    }

    pub fn require_numeric_constant(&self, n: &SNode) -> Result<(), SqlError> {
        if self.is_constant_here(n) {
            require_numeric(n, Some(&self.const_root))?;
        }
        Ok(())
    }

    pub fn guard_numeric(&mut self, f: Fragment, n: &SNode) -> Result<Fragment, SqlError> {
        if self.is_constant_here(n) {
            return Ok(f);
        }
        let wraps = f.kind != SqlKind::Num || f.guard;
        let guarded = self.emit.numeric_operand(&f, n.pos)?;
        if wraps {
            self.scale_limited(n.pos, "this operand is read as a number")?;
        }
        Ok(guarded)
    }

    pub fn numeric_cast_scale(&self) -> Option<i32> {
        let cap_val = self.emit.lex("numericCastScale");
        let cap_str = cap_val.as_ref().and_then(|v| v.as_str())?;
        cap_str.parse::<i32>().ok()
    }

    pub fn scale_limited(&mut self, pos: Pos, what: &str) -> Result<(), SqlError> {
        let cap = match self.numeric_cast_scale() {
            Some(c) => c,
            None => return Ok(()),
        };
        if self.strict {
            return refuse(
                "E_SQL_UNSUPPORTED",
                format!(
                    "{} through a DECIMAL that keeps {} fractional digits, and a value with more loses them on {} (scale-limit); strict mode refuses that",
                    what, cap, self.dialect
                ),
                pos,
            );
        }
        self.add_caveat("scale-limit");
        Ok(())
    }

    pub fn coerce_scale_limits(&mut self, operands: &[&SNode]) -> Result<(), SqlError> {
        let cap = match self.numeric_cast_scale() {
            Some(c) => c,
            None => return Ok(()),
        };
        for operand in operands {
            if self.is_constant_here(operand) {
                if constant_scale(operand, Some(&self.const_root))? > cap as usize {
                    self.scale_limited(operand.pos, "this constant is read as a number")?;
                }
            } else {
                self.scale_limited(operand.pos, "this operand is read as a number")?;
            }
        }
        Ok(())
    }

    fn require_argument_kind(&self, name: &str, f: &Fragment, pos: Pos) -> Result<(), SqlError> {
        if f.kind == SqlKind::Bool && !is_bool_argument_ok(name) {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} does not take a BOOL argument; SEL raises here rather than reading a boolean as text or as 1",
                    name
                ),
                pos,
            );
        }
        if f.kind == SqlKind::Bin && !is_bin_argument_ok(name) {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "{} reads its argument as text, and this is BIN; SEL raises here rather than reinterpreting bytes as characters",
                    name
                ),
                pos,
            );
        }
        Ok(())
    }
}

pub fn frame_set(frame: &mut Vec<(String, Binder)>, name: &str, b: Binder) {
    for item in frame.iter_mut() {
        if item.0 == name {
            item.1 = b;
            return;
        }
    }
    frame.push((name.to_string(), b));
}

pub fn merge_slots(a: &mut SlotMap, b: SlotMap) {
    for kv_b in b {
        for kv_a in a.iter() {
            if kv_a.0 == kv_b.0 {
                // An invariant of the translator (its sources never share a
                // slot), not something a program or dialect can do: a bug.
                panic!("two sources both supply the skeleton slot {{{}}}; one would silently shadow the other", kv_b.0);
            }
        }
        a.push(kv_b);
    }
}

fn same_relation(a: &RelationSpec, b: &RelationSpec) -> bool {
    a.from.table == b.from.table
        && a.from.raw == b.from.raw
        && a.from.is_raw == b.from.is_raw
        && a.alias == b.alias
}

pub fn list_key(k: &str) -> Option<usize> {
    crate::utf8::canonical_index(k, 9)
}

pub fn child_of<'a>(n: &'a SNode, key: &str) -> Option<&'a SNode> {
    if n.t == SNodeType::List {
        let i = list_key(key)?;
        if i == 0 || i > n.kids.len() {
            return None;
        }
        return n.kids.get(i - 1);
    }
    if n.t == SNodeType::CList {
        for (i, k) in n.keys.iter().enumerate() {
            if k == key {
                return n.kids.get(i);
            }
        }
    }
    None
}

fn declared_kind(b: &Binding, v: &Value) -> SqlKind {
    if v.is_bool() {
        return SqlKind::Bool;
    }
    if v.is_bin() {
        return SqlKind::Bin;
    }
    if v.is_none() {
        return SqlKind::List;
    }
    if b.value_type == Some(SqlKind::Num) {
        return SqlKind::Num;
    }
    SqlKind::Text
}


fn agg_shape(n: &SNode) -> Result<(&str, &SNode), SqlError> {
    if n.kids.len() == 3 {
        if !is_binder_name(n.kids.get(1)) {
            return refuse(
                "E_SQL_SHAPE",
                format!("the binder of {} must be a bare name", n.str),
                n.kids[1].pos,
            );
        }
        return Ok((&n.kids[1].str, &n.kids[2]));
    }
    Ok(("_", &n.kids[1]))
}

fn agg_fold(name: &str) -> &'static str {
    match name {
        "ALL" => "AND",
        "ANY" => "OR",
        "SUM" => "+",
        _ => "",
    }
}

fn agg_skeleton(name: &str) -> String {
    name.to_ascii_lowercase()
}

fn agg_returns(name: &str) -> SqlKind {
    match name {
        "ALL" | "ANY" => SqlKind::Bool,
        "SUM" => SqlKind::Num,
        "JOIN" => SqlKind::Text,
        _ => SqlKind::List,
    }
}

// The argument facts below are the manifest's (spec/builtins.json: `regex`,
// `sql`, `yieldsList`), measured against SEL and re-measured by sql/oracle/.

fn regex_call(name: &str) -> Option<crate::manifest::builtins::RegexCall> {
    crate::manifest::builtins::regex_call(name)
}

fn is_numeric_argument(name: &str, i: usize) -> bool {
    use crate::manifest::builtins::NumericArgs;
    crate::manifest::builtins::sql_args(name).is_some_and(|a| match a.numeric {
        NumericArgs::All => true,
        NumericArgs::At(at) => at.contains(&i),
        NumericArgs::None => false,
    })
}

fn is_bool_argument_ok(name: &str) -> bool {
    crate::manifest::builtins::sql_args(name).is_some_and(|a| a.bool_arg)
}

fn is_bin_argument_ok(name: &str) -> bool {
    crate::manifest::builtins::sql_args(name).is_some_and(|a| a.bin_arg)
}

fn is_aggregate(name: &str) -> bool {
    matches!(name, "ALL" | "ANY" | "MAP" | "FILTER" | "SUM" | "JOIN")
}

// LIST, RECORD and every pipeline step are in the manifest's list too.
fn yields_list(name: &str) -> bool {
    crate::manifest::builtins::yields_list(name)
}


// The operator families, from the lexicon (crate::ops).
fn is_numeric_op(op: &str) -> bool {
    crate::ops::is_numeric_comparison(op)
}

// The comparisons that take the dialect's "text" variant: the byte
// comparisons and EQL (IN goes through in_operator, which applies EQL).
fn is_textual_op(op: &str) -> bool {
    crate::ops::is_text_comparison(op) || op == "EQL"
}

// The comparisons that read their operands as bytes (§5.3, §5.4).
fn is_byte_comparison(op: &str) -> bool {
    crate::ops::is_text_comparison(op) || crate::ops::is_deep_comparison(op)
}

fn is_arithmetic_op(op: &str) -> bool {
    crate::ops::is_arithmetic(op)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum EqlClass {
    Text,
    Bool,
    Bin,
}

fn get_eql_class(k: SqlKind) -> Option<EqlClass> {
    match k {
        SqlKind::Num | SqlKind::Text => Some(EqlClass::Text),
        SqlKind::Bool => Some(EqlClass::Bool),
        SqlKind::Bin => Some(EqlClass::Bin),
        _ => None,
    }
}

fn require_comparable_kinds(l: &Fragment, r: &Fragment, op: &str, pos: Pos) -> Result<(), SqlError> {
    let cl = get_eql_class(l.kind);
    let cr = get_eql_class(r.kind);
    if cl.is_none() || cr.is_none() || cl == cr {
        return Ok(());
    }
    // Both kinds as they are: a BIN and a TEXT reach here too, and were
    // reported as "a BOOL with a BIN".
    let what = format!("{} compares a {} with a {}", op, l.kind, r.kind);
    let message = if op.starts_with('$') {
        // SEL reads a BIN and a TEXT here as bytes, and a BOOL is no operand
        // of the byte comparisons (E_NOT_BIN); SQL would cast both sides to
        // characters, which says neither.
        format!("{what}, which SEL compares as bytes (or refuses, for a BOOL); SQL has no way to say that: both sides cast to the same characters")
    } else {
        format!("{what}, which SEL answers FALSE for every value because the kinds differ. SQL has no way to say that: both sides cast to the same characters")
    };
    refuse("E_SQL_SHAPE", message, pos)
}

pub fn unify(fs: &[Fragment], pos: Pos) -> Result<SqlKind, SqlError> {
    let mut kind: Option<SqlKind> = None;
    let mut unknown = false;
    for f in fs {
        if f.kind == SqlKind::Unknown {
            unknown = true;
            continue;
        }
        if kind.is_none() {
            kind = Some(f.kind);
            continue;
        }
        if kind != Some(f.kind) {
            return refuse(
                "E_SQL_SHAPE",
                format!(
                    "these branches produce different kinds — {} and {} — and SQL gives the whole expression one type, which cannot match SEL's for both",
                    kind.unwrap(),
                    f.kind
                ),
                pos,
            );
        }
    }
    Ok(if unknown { SqlKind::Unknown } else { kind.unwrap_or(SqlKind::Unknown) })
}

fn ret_kind(entry: &EntryRecord, args: &[&Fragment], pos: Pos) -> Result<SqlKind, SqlError> {
    let ret = &entry.ret;
    if ret == "@concat" {
        for a in args {
            if a.kind == SqlKind::Bin {
                return Ok(SqlKind::Bin);
            }
        }
        return Ok(SqlKind::Text);
    }
    if let Some(rest) = ret.strip_prefix("@unify:") {
        let mut pick = Vec::new();
        for piece in rest.split(',') {
            if let Ok(idx) = piece.parse::<usize>() {
                if idx < args.len() {
                    pick.push((*args[idx]).clone());
                }
            }
        }
        return unify(&pick, pos);
    }
    Ok(SqlKind::from_name(ret))
}
