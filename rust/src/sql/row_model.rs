use std::collections::HashSet;

use crate::sql::binding::{ColumnSpec, RelationSpec};
use crate::sql::errors::refuse;
use crate::sql::relational_plan::RelationalPlan;

#[derive(Clone, Debug)]
pub struct RowField {
    pub spec: ColumnSpec,
    pub table: String,
    pub qualify: bool,
    pub optional: bool,
}

#[derive(Clone, Debug, Default)]
pub struct RowModel {
    pub side: bool,
    pub relation: Option<RelationSpec>,
    pub table: String,
    pub qualify: bool,
    pub names: Vec<String>,
    pub nested: Vec<(String, RowModel)>,
    pub self_names: Vec<String>,
    pub promoted: Vec<(String, RowField)>,
    pub dropped: HashSet<String>,
}

#[derive(Clone, Debug)]
pub struct JoinRowsStep {
    pub left: RowModel,
    pub right: RowModel,
}

#[derive(Clone, Debug, Default)]
pub struct JoinRows {
    pub row: RowModel,
    pub steps: Vec<JoinRowsStep>,
}

fn ordered_set_nested(list: &mut Vec<(String, RowModel)>, k: String, v: RowModel) {
    for item in list.iter_mut() {
        if item.0 == k {
            item.1 = v;
            return;
        }
    }
    list.push((k, v));
}

fn ordered_set_promoted(list: &mut Vec<(String, RowField)>, k: String, v: RowField) {
    for item in list.iter_mut() {
        if item.0 == k {
            item.1 = v;
            return;
        }
    }
    list.push((k, v));
}

fn binder_keys(names: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for name in names {
        let lower = name.to_lowercase();
        for k in [name.clone(), lower] {
            if !out.contains(&k) {
                out.push(k);
            }
        }
    }
    out
}

impl RowModel {
    pub fn nested_of(&self, key: &str) -> Option<&RowModel> {
        if self.side {
            if self.names.iter().any(|n| n == key) {
                return Some(self);
            }
            return None;
        }
        if self.self_names.iter().any(|n| n == key) {
            return Some(self);
        }
        for (k, v) in &self.nested {
            if k == key {
                return Some(v);
            }
        }
        None
    }

    pub fn row_field_spec(&self, key: &str) -> Option<RowField> {
        let u = key.to_ascii_uppercase();
        if self.side {
            if let Some(ref rel) = self.relation {
                if let Some(spec) = rel.field(&u) {
                    return Some(RowField {
                        spec: spec.clone(),
                        table: self.table.clone(),
                        qualify: self.qualify,
                        optional: false,
                    });
                }
            }
            return None;
        }
        for (k, v) in &self.promoted {
            if k == &u {
                return Some(v.clone());
            }
        }
        None
    }

    pub fn with_names(&self, names: &[String]) -> Self {
        let mut out = self.clone();
        if self.side {
            out.names = names.to_vec();
        } else {
            for k in names {
                out.nested.retain(|item| &item.0 != k);
                if !out.self_names.contains(k) {
                    out.self_names.push(k.clone());
                }
            }
        }
        out
    }

    pub fn row_keys(&self) -> Vec<String> {
        let mut out = Vec::new();
        if self.side {
            if let Some(ref rel) = self.relation {
                for k in rel.fields.keys() {
                    out.push(k.clone());
                }
            }
            out.extend(self.names.clone());
        } else {
            for (k, _) in &self.nested {
                out.push(k.clone());
            }
            out.extend(self.self_names.clone());
            for (k, _) in &self.promoted {
                out.push(k.clone());
            }
        }
        out
    }

    pub fn scalar_fields(&self) -> Vec<(String, RowField)> {
        if !self.side {
            return self.promoted.clone();
        }
        let mut out = Vec::new();
        if let Some(ref rel) = self.relation {
            if !rel.field_order.is_empty() {
                for u in &rel.field_order {
                    if let Some(spec) = rel.fields.get(u) {
                        out.push((
                            u.clone(),
                            RowField {
                                spec: spec.clone(),
                                table: self.table.clone(),
                                qualify: self.qualify,
                                optional: false,
                            },
                        ));
                    }
                }
            } else {
                for (u, spec) in &rel.fields {
                    out.push((
                        u.clone(),
                        RowField {
                            spec: spec.clone(),
                            table: self.table.clone(),
                            qualify: self.qualify,
                            optional: false,
                        },
                    ));
                }
            }
        }
        out
    }
}

pub fn relation_alias(rel: Option<&RelationSpec>) -> String {
    let r = match rel {
        Some(r) => r,
        None => return String::new(),
    };
    if !r.alias.is_empty() {
        return r.alias.clone();
    }
    if r.from.is_raw {
        return r.from.raw.clone();
    }
    r.from.table.clone()
}

pub fn build_join_rows(plan: &RelationalPlan) -> Result<JoinRows, crate::sql::errors::SqlError> {
    let qualify = !plan.joins.is_empty() || plan.source_subquery.is_some();
    let side = |rel: Option<&RelationSpec>, alias: &str, names: Vec<String>| -> RowModel {
        let mut t = alias.to_string();
        if t.is_empty() {
            t = relation_alias(rel);
        }
        RowModel {
            side: true,
            relation: rel.cloned(),
            table: t,
            qualify,
            names,
            nested: Vec::new(),
            self_names: Vec::new(),
            promoted: Vec::new(),
            dropped: HashSet::new(),
        }
    };

    let mut result = JoinRows::default();
    let src_rel = plan.source_relation.as_ref().and_then(|b| b.relation.as_ref());
    let mut left = side(src_rel, &plan.source_alias, Vec::new());

    for join in &plan.joins {
        let left_names = binder_keys(&join.left_names);
        let right_names = binder_keys(&join.right_names);
        let join_rel = join.source_relation.as_ref().and_then(|b| b.relation.as_ref());
        let right = side(join_rel, &join.source_alias, right_names.clone());
        let left_el = left.with_names(&left_names);

        for check in [(&left_el, &join.left_names), (&right, &join.right_names)] {
            if !check.0.side || check.0.relation.is_none() {
                continue;
            }
            let rel = check.0.relation.as_ref().unwrap();
            for name in check.1 {
                if rel.field(&name.to_ascii_uppercase()).is_some() {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!(
                            "{} names a LINK side that has a field of that name too, which SEL binds instead of the row; rename the binder",
                            name
                        ),
                        join.pos,
                    );
                }
            }
        }

        let mut row = RowModel {
            side: false,
            relation: None,
            table: String::new(),
            qualify: false,
            names: Vec::new(),
            nested: Vec::new(),
            self_names: Vec::new(),
            promoted: Vec::new(),
            dropped: HashSet::new(),
        };

        if !left_el.side {
            row.nested.extend(left_el.nested.clone());
            for k in &left_el.self_names {
                ordered_set_nested(&mut row.nested, k.clone(), left_el.clone());
            }
        }
        ordered_set_nested(&mut row.nested, "_1".to_string(), left_el.clone());
        for k in &left_names {
            ordered_set_nested(&mut row.nested, k.clone(), left_el.clone());
        }
        ordered_set_nested(&mut row.nested, "_2".to_string(), right.clone());
        for k in &right_names {
            ordered_set_nested(&mut row.nested, k.clone(), right.clone());
        }

        let mut left_keys = HashSet::new();
        for k in left_el.row_keys() {
            left_keys.insert(k.to_ascii_uppercase());
        }
        let mut right_keys = HashSet::new();
        for k in right.row_keys() {
            right_keys.insert(k.to_ascii_uppercase());
        }

        for (k, v) in left_el.scalar_fields() {
            if right_keys.contains(&k) {
                row.dropped.insert(k);
            } else {
                ordered_set_promoted(&mut row.promoted, k, v);
            }
        }

        for (k, mut v) in right.scalar_fields() {
            if left_keys.contains(&k) {
                row.dropped.insert(k);
            } else {
                if join.join_type == crate::sql::relational_plan::JoinType::Left {
                    v.optional = true;
                }
                ordered_set_promoted(&mut row.promoted, k, v);
            }
        }

        result.steps.push(JoinRowsStep {
            left: left_el,
            right,
        });
        left = row;
    }

    result.row = left;
    Ok(result)
}
