use std::collections::HashMap;
use crate::utf8::Pos;
use crate::value::{Kind, Value};
use crate::dec::{dec_format, dec_parse};
use crate::sql::errors::{refuse, SqlError};
use crate::sql::types::SqlKind;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BindingKind {
    Column,
    Columns,
    Relation,
    Value,
}

#[derive(Clone, Debug, Default)]
pub struct ColumnSpec {
    pub unavailable: bool,
    pub is_raw: bool,
    pub raw: String,
    pub column: String,
    pub table: String,
    pub sql_type: SqlKind,
    pub exact: bool,
    pub sargable: bool,
    pub guard: bool,
    pub collation: String,
    pub prefilter: String,
    pub split_sargable: bool,
    pub canonical: bool,
}

#[derive(Clone, Debug, Default)]
pub struct RelationFrom {
    pub is_raw: bool,
    pub raw: String,
    pub table: String,
}

#[derive(Clone, Debug)]
pub struct FieldEntry {
    pub name: String,
    pub binding: Binding,
}

impl FieldEntry {
    pub fn new(name: impl Into<String>, binding: Binding) -> Self {
        Self {
            name: name.into(),
            binding,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct RelationSpec {
    pub unique_key: String,
    pub from: RelationFrom,
    pub alias: String,
    pub fields: HashMap<String, ColumnSpec>,
    pub field_order: Vec<String>,
    pub scalar: String,
    pub correlate: String,
    pub prefilter: String,
}

impl RelationSpec {
    pub fn field(&self, name: &str) -> Option<&ColumnSpec> {
        let key = name.to_ascii_uppercase();
        self.fields.get(&key)
    }
}

#[derive(Clone, Debug)]
/// Where a SEL name lives in the database: a column, a list of columns, a
/// relation, or a value known before the query runs. Built only by the
/// constructors below, which validate what they are given; the fields are the
/// crate's, so no binding can skip that.
pub struct Binding {
    pub(crate) kind: BindingKind,
    pub(crate) column: Option<ColumnSpec>,
    pub(crate) columns: Option<Vec<ColumnSpec>>,
    pub(crate) relation: Option<RelationSpec>,
    pub(crate) val: Option<Value>,
    pub(crate) value_type: Option<SqlKind>,
}

/// A malformed binding: the constructors panic with an `E_SQL_BINDING`
/// SqlError rather than return one (the README says so), as plan_hybrid does
/// for a binding set it cannot use.
fn binding_error(message: impl Into<String>) -> ! {
    std::panic::panic_any(SqlError::new("E_SQL_BINDING", message, Pos::default()))
}

fn check_name(what: &str, v: &str) {
    if v.is_empty() {
        binding_error(format!("a binding has an empty {} name", what));
    }
    if v.contains('\0') {
        binding_error(format!("a binding has a {} name containing a NUL, which no dialect can quote", what));
    }
}

// The kinds a binding may DECLARE: what a column holds. LIST and STATEMENT are
// kinds of fragment a translation can produce, not of a column.
fn check_column_type(typ: SqlKind) {
    if typ == SqlKind::List || typ == SqlKind::Statement {
        binding_error(format!("a binding has type {}; use one of UNKNOWN, NUM, TEXT, BOOL, BIN", typ.as_str()));
    }
}

// A collation spelling folded into the exact/sargable flags; an unknown one is
// refused, as the other hosts' bindings do (GO-C19).
fn check_collation(c: &str) -> (bool, bool) {
    if c.is_empty() {
        return (false, false);
    }
    match c.to_ascii_lowercase().as_str() {
        "binary" | "exact" => (true, false),
        "sargable" | "prefilter" => (false, true),
        "default" | "none" => (false, false),
        _ => binding_error(format!("unknown collation '{}'; use 'binary', 'exact', 'sargable', or 'default'", c)),
    }
}

// The spellings of a prefilter strategy, normalised; the rest refused.
fn check_prefilter(p: &str) -> String {
    if p.is_empty() {
        return String::new();
    }
    match p.to_ascii_lowercase().as_str() {
        "separate" | "splitsargable" | "split_sargable" => "separate".to_string(),
        "inline" => "inline".to_string(),
        _ => binding_error(format!("unknown prefilter '{}'; use 'separate' or 'inline'", p)),
    }
}

// The flags column() and raw() share: a collation spelling folded into
// exact/sargable, then the prefilter (split_sargable asks for `separate`).
fn column_flags(
    exact: bool,
    sargable: bool,
    collation: &str,
    prefilter: &str,
    split_sargable: bool,
) -> (bool, bool, String) {
    let (c_exact, c_sargable) = check_collation(collation);
    let prefilter = if split_sargable && prefilter.is_empty() { "separate" } else { prefilter };
    (exact || c_exact, sargable || c_sargable, check_prefilter(prefilter))
}

fn check_numeric(where_str: &str, v: &Value) {
    if v.size() > 0 {
        for e in v.entries() {
            check_numeric(&format!("{}[{:?}]", where_str, e.key), &e.val);
        }
        return;
    }
    if v.is_none() {
        return;
    }
    if v.kind() != Kind::Text || !v.looks_numeric() {
        let shown = if v.kind() == Kind::Bool {
            if v.as_bool(Pos::default()).unwrap_or(false) { "TRUE" } else { "FALSE" }
        } else {
            &v.scalar()
        };
        binding_error(format!("{} declares type NUM, which asks for it to be emitted unquoted, but {:?} is not a number", where_str, shown));
    }
    let text = v.scalar();
    let d = dec_parse(&text, Pos::default()).ok();
    if d.is_none() || dec_format(d.as_ref().unwrap()) != text {
        binding_error(format!("{} declares type NUM and is {:?}, which is not how SEL canonicalises it; write canonical decimals or omit type: NUM", where_str, text));
    }
}

impl Binding {
    pub fn column(
        column: &str,
        table: &str,
        typ: SqlKind,
        exact: bool,
        sargable: bool,
        guard: bool,
        collation: &str,
        prefilter: &str,
        split_sargable: bool,
    ) -> Self {
        check_name("column", column);
        if !table.is_empty() {
            check_name("table", table);
        }
        check_column_type(typ);
        let (exact, sargable, prefilter) =
            column_flags(exact, sargable, collation, prefilter, split_sargable);
        let prefilter = prefilter.as_str();
        Self {
            kind: BindingKind::Column,
            column: Some(ColumnSpec {
                unavailable: false,
                is_raw: false,
                raw: String::new(),
                column: column.to_string(),
                table: table.to_string(),
                sql_type: typ,
                exact,
                sargable,
                guard,
                collation: collation.to_string(),
                prefilter: prefilter.to_string(),
                split_sargable,
                canonical: false,
            }),
            columns: None,
            relation: None,
            val: None,
            value_type: None,
        }
    }

    pub fn raw(
        raw: &str,
        typ: SqlKind,
        exact: bool,
        sargable: bool,
        guard: bool,
        collation: &str,
        prefilter: &str,
        split_sargable: bool,
    ) -> Self {
        if raw.is_empty() {
            binding_error("a raw column binding cannot be empty");
        }
        check_column_type(typ);
        let (exact, sargable, prefilter) =
            column_flags(exact, sargable, collation, prefilter, split_sargable);
        let prefilter = prefilter.as_str();
        Self {
            kind: BindingKind::Column,
            column: Some(ColumnSpec {
                unavailable: false,
                is_raw: true,
                raw: raw.to_string(),
                column: String::new(),
                table: String::new(),
                sql_type: typ,
                exact,
                sargable,
                guard,
                collation: collation.to_string(),
                prefilter: prefilter.to_string(),
                split_sargable,
                canonical: false,
            }),
            columns: None,
            relation: None,
            val: None,
            value_type: None,
        }
    }

    pub fn columns(items: Vec<Binding>) -> Self {
        if items.is_empty() {
            binding_error("a columns binding needs at least one column");
        }
        let mut specs = Vec::with_capacity(items.len());
        for (i, item) in items.into_iter().enumerate() {
            if item.kind != BindingKind::Column || item.column.is_none() {
                binding_error(format!("a columns binding takes column bindings, and item {} is not a column", i + 1));
            }
            specs.push(item.column.unwrap());
        }
        Self {
            kind: BindingKind::Columns,
            column: None,
            columns: Some(specs),
            relation: None,
            val: None,
            value_type: None,
        }
    }

    pub fn relation(
        from: &str,
        alias: &str,
        fields: Vec<FieldEntry>,
        scalar: &str,
        correlate: &str,
        prefilter: &str,
        split_sargable: bool,
    ) -> Self {
        check_name("from", from);
        if !alias.is_empty() {
            check_name("alias", alias);
        }
        let pref = if split_sargable && prefilter.is_empty() {
            "separate"
        } else {
            prefilter
        };
        make_relation(
            RelationFrom {
                is_raw: false,
                raw: String::new(),
                table: from.to_string(),
            },
            alias,
            fields,
            scalar,
            correlate,
            pref,
        )
    }

    pub fn relation_query(
        query: &str,
        alias: &str,
        fields: Vec<FieldEntry>,
        scalar: &str,
        correlate: &str,
        prefilter: &str,
        split_sargable: bool,
    ) -> Self {
        if query.is_empty() {
            binding_error("a relation query cannot be empty");
        }
        if !alias.is_empty() {
            check_name("alias", alias);
        }
        let pref = if split_sargable && prefilter.is_empty() {
            "separate"
        } else {
            prefilter
        };
        make_relation(
            RelationFrom {
                is_raw: true,
                raw: query.to_string(),
                table: String::new(),
            },
            alias,
            fields,
            scalar,
            correlate,
            pref,
        )
    }

    pub fn value(val: Value, typ: Option<SqlKind>) -> Self {
        if let Some(t) = typ {
            if t == SqlKind::Num {
                check_numeric("this value binding", &val);
            }
        }
        Self {
            kind: BindingKind::Value,
            column: None,
            columns: None,
            relation: None,
            val: Some(val),
            value_type: typ,
        }
    }

    pub fn with_unique_key(mut self, key: &str) -> Self {
        check_name("unique key", key);
        if self.kind != BindingKind::Relation || self.relation.is_none() {
            binding_error("a unique key must name a declared relation field");
        }
        let rel = self.relation.as_mut().unwrap();
        if !rel.fields.contains_key(&key.to_ascii_uppercase()) {
            binding_error("a unique key must name a declared relation field");
        }
        rel.unique_key = key.to_string();
        self
    }
}

fn make_relation(
    from: RelationFrom,
    alias: &str,
    fields: Vec<FieldEntry>,
    scalar: &str,
    correlate: &str,
    prefilter: &str,
) -> Binding {
    let prefilter = check_prefilter(prefilter);
    let prefilter = prefilter.as_str();
    let mut field_specs = HashMap::new();
    let mut field_order = Vec::new();

    for fe in fields {
        if fe.binding.kind != BindingKind::Column || fe.binding.column.is_none() {
            binding_error(format!("the field {} of a relation binding must be a column binding", fe.name));
        }
        let uc = fe.name.to_ascii_uppercase();
        if field_specs.contains_key(&uc) {
            binding_error("relation fields collide ignoring ASCII case");
        }
        field_specs.insert(uc.clone(), fe.binding.column.unwrap());
        field_order.push(uc);
    }

    if !scalar.is_empty() && !field_specs.contains_key(&scalar.to_ascii_uppercase()) {
        binding_error(format!("a relation binding names {} as its scalar, which is not one of its fields", scalar));
    }

    Binding {
        kind: BindingKind::Relation,
        column: None,
        columns: None,
        relation: Some(RelationSpec {
            unique_key: String::new(),
            from,
            alias: alias.to_string(),
            fields: field_specs,
            field_order,
            scalar: scalar.to_string(),
            correlate: correlate.to_string(),
            prefilter: prefilter.to_string(),
        }),
        val: None,
        value_type: None,
    }
}

#[derive(Clone, Debug, Default)]
pub struct Bindings {
    pub(crate) map: HashMap<String, Binding>,
}

impl Bindings {
    pub fn new(map: Option<HashMap<String, Binding>>) -> Self {
        let mut m = HashMap::new();
        if let Some(items) = map {
            for (k, v) in items {
                let key = k.to_ascii_uppercase();
                if m.contains_key(&key) {
                    binding_error("binding names collide ignoring ASCII case");
                }
                m.insert(key, v);
            }
        }
        Self { map: m }
    }

    pub fn names(&self) -> Vec<String> {
        let mut keys: Vec<String> = self.map.keys().cloned().collect();
        keys.sort();
        keys
    }

    pub fn get(&self, name: &str, pos: Pos) -> Result<&Binding, SqlError> {
        let key = name.to_ascii_uppercase();
        if let Some(b) = self.map.get(&key) {
            return Ok(b);
        }
        let known = self.names();
        let tail = if known.is_empty() {
            "; no bindings were given".to_string()
        } else {
            format!("; bound names are {}", known.join(", "))
        };
        refuse(
            "E_SQL_UNBOUND",
            format!("{} is read by this rule but no binding says where it lives{}", key, tail),
            pos,
        )
    }

    pub fn has(&self, name: &str) -> bool {
        self.map.contains_key(&name.to_ascii_uppercase())
    }

    pub fn set(&mut self, name: impl Into<String>, b: Binding) {
        self.map.insert(name.into().to_ascii_uppercase(), b);
    }

    pub fn check_aliases(&self, pos: Pos) -> Result<(), SqlError> {
        let mut seen: HashMap<String, String> = HashMap::new();
        for name in self.names() {
            if let Some(bind) = self.map.get(&name) {
                if bind.kind != BindingKind::Relation {
                    continue;
                }
                if let Some(ref rel) = bind.relation {
                    let mut alias = rel.alias.clone();
                    if alias.is_empty() {
                        if rel.from.is_raw {
                            alias = rel.from.raw.clone();
                        } else {
                            alias = rel.from.table.clone();
                        }
                    }
                    // ASCII case-insensitively: SQLite (and, by platform, the
                    // MySQL family) reads `o` and `O` as one alias.
                    let alias_key = alias.to_ascii_uppercase();
                    if let Some(prev) = seen.get(&alias_key) {
                        return refuse(
                            "E_SQL_BINDING",
                            format!(
                                "relations {} and {} share the alias {}; give each one its own",
                                prev, name, alias
                            ),
                            pos,
                        );
                    }
                    seen.insert(alias_key, name);
                }
            }
        }
        Ok(())
    }
}
