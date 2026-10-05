use std::collections::HashSet;

use crate::ast::{Node, NodeType};
use crate::optimizer::is_pipeline_op;
use crate::program::Program;
use crate::sql::binding::{Binding, ColumnSpec, FieldEntry};
use crate::sql::constants::{identity_loss_before_grouping, is_binder_name, NeededFields};
use crate::sql::errors::{refuse, SqlError};
use crate::sql::map::{chain, entry, EntryKind};
use crate::sql::node::{SNode, SNodeType};
use crate::sql::relational_plan::{
    BucketState, JoinType, RelationalFilter, RelationalGroup, RelationalJoin, RelationalOrder,
    RelationalPlan, RelationalProjection, SortDirection,
};
use crate::sql::row_model::{build_join_rows, relation_alias};
use crate::sql::translator::{Source, SourceFilter, SourceShape, Translator};
use crate::sql::types::{Fragment, Part, SqlKind};
use crate::utf8::Pos;

#[derive(Clone, Debug)]
pub struct JoinedRowField {
    pub name: String,
    pub spec: ColumnSpec,
    pub table: String,
}

pub fn joined_row_fields(plan: &RelationalPlan) -> Result<Vec<JoinedRowField>, SqlError> {
    let jr = build_join_rows(plan)?;
    let mut out = Vec::new();
    for (key, val) in jr.row.promoted {
        if !val.optional {
            out.push(JoinedRowField {
                name: key,
                spec: val.spec,
                table: val.table,
            });
        }
    }
    Ok(out)
}

pub fn record_fields(node: &SNode, dialect: &str) -> Result<Vec<(String, SNode)>, SqlError> {
    let args = &node.kids;
    if args.len() % 2 != 0 {
        return refuse("E_ARITY", "RECORD takes an even number of arguments", node.pos);
    }
    let mut fields = Vec::with_capacity(args.len() / 2);
    let postgres = chain(dialect).iter().any(|d| d == "postgresql");
    let mut prefixes = std::collections::HashMap::new();
    for i in (0..args.len()).step_by(2) {
        if args[i].t != SNodeType::Text {
            return refuse("E_BAD_ARG", "RECORD field names must be string literals", args[i].pos);
        }
        check_program_identifier(&args[i])?;
        if postgres {
            let key = args[i].str.as_bytes();
            let mut end = key.len().min(63);
            while !args[i].str.is_char_boundary(end) { end -= 1; }
            let prefix = key[..end].to_vec();
            if prefixes.insert(prefix, key.len() > 63).is_some_and(|was_long| was_long || key.len() > 63) {
                return refuse("E_SQL_UNSUPPORTED", "record aliases collide after PostgreSQL identifier truncation", args[i].pos);
            }
        }
        fields.push((args[i].str.clone(), args[i + 1].clone()));
    }
    Ok(fields)
}

fn check_program_identifier(node: &SNode) -> Result<(), SqlError> {
    if node.str.is_empty() || node.str.contains('\0') {
        return refuse("E_SQL_UNSUPPORTED", "SQL identifiers must be nonempty and contain no NUL", node.pos);
    }
    Ok(())
}

impl Translator {
    pub fn plan_needs_wrap_before_map(&self, plan: &RelationalPlan) -> bool {
        plan.projections.is_some()
            || plan.select_cols.is_some()
            || plan.group_by.is_some()
            || plan.distinct
            || plan.limit.is_some()
            || plan.offset.is_some()
    }

    pub fn plan_has_rows_above(&self, plan: &RelationalPlan) -> bool {
        plan.projections.is_some()
            || plan.select_cols.is_some()
            || plan.group_by.is_some()
            || plan.distinct
            || plan.limit.is_some()
            || plan.offset.is_some()
            || !plan.order_by.is_empty()
    }

    pub fn output_field_names(&self, plan: &RelationalPlan) -> Result<Vec<String>, SqlError> {
        let mut names = Vec::new();
        if let Some(ref projections) = plan.projections {
            for (i, projection) in projections.iter().enumerate() {
                if let Some(ref alias) = projection.alias {
                    names.push(alias.clone());
                } else if let Some(ref node) = projection.node {
                    if node.t == SNodeType::Index
                        && node.idx().map(|idx| idx.t == SNodeType::Text).unwrap_or(false)
                    {
                        names.push(node.idx().unwrap().str.clone());
                    } else {
                        names.push(format!("expr{}", i + 1));
                    }
                } else {
                    names.push(format!("expr{}", i + 1));
                }
            }
        } else if let Some(ref select_cols) = plan.select_cols {
            names.extend(select_cols.clone());
        } else if !plan.joins.is_empty() {
            for f in joined_row_fields(plan)? {
                names.push(f.name);
            }
        } else if let Some(ref src_binding) = plan.source_relation {
            if let Some(ref rel) = src_binding.relation {
                if !rel.field_order.is_empty() {
                    for name in &rel.field_order {
                        names.push(name.clone());
                    }
                } else {
                    let mut sorted_keys: Vec<_> = rel.fields.keys().cloned().collect();
                    sorted_keys.sort();
                    names.extend(sorted_keys);
                }
            }
        }
        let mut unique = Vec::new();
        let mut seen = HashSet::new();
        for name in names {
            let upper = name.to_ascii_uppercase();
            if !seen.contains(&upper) {
                seen.insert(upper);
                unique.push(name);
            }
        }
        Ok(unique)
    }

    pub fn output_field_type(&self, plan: &RelationalPlan, name: &str) -> SqlKind {
        let mut target_name = name.to_string();
        if let Some(ref projections) = plan.projections {
            let mut projection = None;
            for p in projections {
                if p.alias.as_deref() == Some(name) {
                    projection = Some(p);
                    break;
                }
            }
            let n = projection.and_then(|p| p.node.as_ref());
            let matches_index = n
                .map(|node| {
                    node.t == SNodeType::Index
                        && node
                            .obj()
                            .map(|o| o.t == SNodeType::Var && o.str == projection.unwrap().binder)
                            .unwrap_or(false)
                        && node.idx().map(|i| i.t == SNodeType::Text).unwrap_or(false)
                })
                .unwrap_or(false);
            if !matches_index {
                return SqlKind::Unknown;
            }
            target_name = n.unwrap().idx().unwrap().str.clone();
        }
        let mut match_spec: Option<&ColumnSpec> = None;
        if let Some(ref src_b) = plan.source_relation {
            if let Some(ref rel) = src_b.relation {
                match_spec = rel.field(&target_name);
            }
        }
        for join in &plan.joins {
            if let Some(ref j_b) = join.source_relation {
                if let Some(ref rel) = j_b.relation {
                    if let Some(field) = rel.field(&target_name) {
                        if match_spec.is_some() {
                            return SqlKind::Unknown;
                        }
                        match_spec = Some(field);
                    }
                }
            }
        }
        if let Some(m) = match_spec {
            if !m.guard && !m.is_raw {
                return m.sql_type;
            }
        }
        SqlKind::Unknown
    }

    pub fn output_canon_kind(&self, plan: &RelationalPlan, name: &str) -> Option<SqlKind> {
        let projections = plan.projections.as_ref()?;
        let projection = projections.iter().find(|p| p.alias.as_deref() == Some(name))?;
        let n = projection.node.as_ref()?;
        if n.t != SNodeType::Call || n.str != "CANON" {
            return None;
        }
        if let Some(rec) = entry(&self.dialect, "funcs", "CANON") {
            if rec.kind == EntryKind::Template {
                return Some(SqlKind::from_name(&rec.ret));
            }
        }
        None
    }

    pub fn ensure_derived(
        &mut self,
        plan: RelationalPlan,
        needed: bool,
    ) -> Result<RelationalPlan, SqlError> {
        if !needed {
            return Ok(plan);
        }
        self.subquery_counter += 1;
        let alias = format!("_sub{}", self.subquery_counter);
        let inner = plan;

        let mut derived = RelationalPlan::new();
        derived.source_name = alias.clone();
        derived.source_table = String::new();
        derived.source_alias = alias.clone();

        let root_name = if inner.joins.is_empty() {
            inner.root_name.clone()
        } else {
            None
        };
        derived.root_name = root_name;
        if inner.bucket != BucketState::None {
            derived.bucket = BucketState::Sealed;
        }

        let mut field_entries = Vec::new();
        let field_names = self.output_field_names(&inner)?;
        for name in field_names {
            let mut source_field = None;
            if inner.projections.is_none() && inner.select_cols.is_none() {
                if let Some(ref src_rel) = inner.source_relation {
                    if let Some(ref rel) = src_rel.relation {
                        source_field = rel.field(&name);
                    }
                }
                if source_field.is_none() {
                    for join in &inner.joins {
                        if let Some(ref j_rel) = join.source_relation {
                            if let Some(ref rel) = j_rel.relation {
                                source_field = rel.field(&name);
                                if source_field.is_some() {
                                    break;
                                }
                            }
                        }
                    }
                }
            }
            let mut col_name = name.clone();
            if let Some(sf) = source_field {
                if !sf.column.is_empty() {
                    col_name = sf.column.clone();
                }
            }
            let mut col_type = self.output_field_type(&inner, &name);
            let mut canonical = false;
            if let Some(canon_kind) = self.output_canon_kind(&inner, &name) {
                col_type = canon_kind;
                canonical = true;
            }
            let mut b = Binding::column_with(&col_name, &alias, col_type, Default::default());
            if let Some(ref mut c) = b.column {
                c.canonical = canonical;
                c.unavailable = source_field.is_some_and(|f| f.is_raw || f.unavailable);
            }
            field_entries.push(FieldEntry::new(name, b));
        }

        derived.source_relation = Some(Binding::relation(
            &alias,
            &alias,
            field_entries,
            "",
            "",
            "",
            false,
        ));
        // A derived table with no LIMIT beside its ORDER BY does not keep the order: a
        // step that needs the rows in that order (a BUCKET's groups, a LINK's rows, a
        // later sort's ties) cannot be built on it.
        derived.order_dropped = inner.order_dropped
            || (!inner.order_by.is_empty() && inner.limit.is_none() && inner.offset.is_none());
        derived.source_subquery = Some(Box::new(inner));
        Ok(derived)
    }

    pub fn bucket_projection(
        &self,
        plan: &mut RelationalPlan,
        binder: &str,
        agg_node: Option<&SNode>,
    ) -> Result<(), SqlError> {
        if let Some(agg) = agg_node {
            if agg.t == SNodeType::Call && agg.str == "RECORD" {
                let mut projections = Vec::new();
                for (key, val) in record_fields(agg, &self.dialect)? {
                    let mut actual_node = val;
                    let mut node_binder = binder.to_string();
                    let mut group_key = None;
                    if actual_node.t == SNodeType::Var
                        && actual_node.str == "_K"
                        && plan.group_by.as_ref().map(|g| g.len() == 1).unwrap_or(false)
                    {
                        let gb0 = &plan.group_by.as_ref().unwrap()[0];
                        actual_node = gb0.node.clone();
                        node_binder = gb0.binder.clone();
                        group_key = Some(gb0.clone());
                    }
                    projections.push(RelationalProjection {
                        alias: Some(key),
                        binder: node_binder,
                        node: Some(actual_node),
                        group_key,
                    });
                }
                plan.projections = Some(projections);
            } else {
                plan.projections = Some(vec![RelationalProjection {
                    alias: None,
                    binder: binder.to_string(),
                    node: Some(agg.clone()),
                    group_key: None,
                }]);
            }
        } else {
            let mut projections = Vec::new();
            if let Some(ref group_by) = plan.group_by {
                for gb in group_by {
                    projections.push(RelationalProjection {
                        alias: gb.alias.clone(),
                        binder: gb.binder.clone(),
                        node: Some(gb.node.clone()),
                        group_key: Some(gb.clone()),
                    });
                }
            }
            plan.projections = Some(projections);
        }
        plan.select_cols = None;
        Ok(())
    }

    pub fn eval_int_param(&self, n: &SNode, op: &str) -> Result<i64, SqlError> {
        let node = match n.to_node() {
            Some(node) => node,
            None => {
                return refuse(
                    "E_SQL_SHAPE",
                    format!("{} count cannot contain dynamic lists", op),
                    n.pos,
                )
            }
        };
        let mut prog = Program::new("", node);
        let root = self.const_root.clone();
        let val = match prog.run(Some(root)) {
            Ok(v) => v,
            Err(e) => return crate::sql::constants::refuse_as_sel(&e, n),
        };
        let number = val.as_decimal(n.pos)
            .map_err(|e| SqlError::new(e.code, e.message, e.pos))?;
        if !number.is_integer() {
            return refuse("E_NOT_INT", format!("{} count must be an integer", op), n.pos);
        }
        if number.neg {
            return refuse("E_RANGE", format!("{} count cannot be negative", op), n.pos);
        }
        Ok(number.to_safe_i64())
    }

    pub fn analyze_sort_step(
        &mut self,
        step: &SNode,
        plan: &mut RelationalPlan,
    ) -> Result<(), SqlError> {
        let name = &step.str;
        let args = &step.kids;
        let top = name == "TOP" || name == "TOP_DESC" || name == "TOP_BY";
        if top && args.is_empty() {
            return refuse(
                "E_ARITY",
                format!("{} has an invalid sort form", name),
                step.pos,
            );
        }
        let count = if top { args.len() - 1 } else { args.len() };

        if top {
            let lim = self.eval_int_param(&args[args.len() - 1], name)?;
            if plan.limit.is_none() || lim < plan.limit.unwrap() {
                plan.limit = Some(lim);
            }
        }

        if name == "SORT" || name == "SORT_DESC" || name == "TOP" || name == "TOP_DESC" {
            let dir = if name == "SORT_DESC" || name == "TOP_DESC" {
                SortDirection::Desc
            } else {
                SortDirection::Asc
            };
            if count == 1 {
                if let Some(ref src_rel) = plan.source_relation {
                    if let Some(ref rel) = src_rel.relation {
                        if !rel.scalar.is_empty() {
                            let mut var_node = Node::new(NodeType::Var, step.pos);
                            var_node.s = "_".to_string();
                            let var_snode = SNode::leaf(&var_node);
                            let mut idx_node = Node::new(NodeType::Text, step.pos);
                            idx_node.s = rel.scalar.clone();
                            let idx_snode = SNode::leaf(&idx_node);
                            let index_shape = Node::new(NodeType::Index, step.pos);
                            let index_snode =
                                SNode::rewritten(&index_shape, vec![var_snode, idx_snode]);
                            plan.order_by.push(RelationalOrder {
                                binder: "_".to_string(),
                                node: index_snode,
                                dir,
                                pos: step.pos,
                                over_groups: false,
                            });
                            return Ok(());
                        }
                        if rel.fields.len() == 1 {
                            let field_name = rel.fields.keys().next().unwrap().clone();
                            let mut var_node = Node::new(NodeType::Var, step.pos);
                            var_node.s = "_".to_string();
                            let var_snode = SNode::leaf(&var_node);
                            let mut idx_node = Node::new(NodeType::Text, step.pos);
                            idx_node.s = field_name;
                            let idx_snode = SNode::leaf(&idx_node);
                            let index_shape = Node::new(NodeType::Index, step.pos);
                            let index_snode =
                                SNode::rewritten(&index_shape, vec![var_snode, idx_snode]);
                            plan.order_by.push(RelationalOrder {
                                binder: "_".to_string(),
                                node: index_snode,
                                dir,
                                pos: step.pos,
                                over_groups: false,
                            });
                            return Ok(());
                        }
                    }
                }
                return refuse(
                    "E_SQL_SHAPE",
                    "SORT on a multi-field relation requires a key expression; use SORT_BY",
                    step.pos,
                );
            } else if count == 2 {
                plan.order_by.push(RelationalOrder {
                    binder: "_".to_string(),
                    node: args[1].clone(),
                    dir,
                    pos: step.pos,
                    over_groups: false,
                });
            } else if count == 3 {
                if !is_binder_name(Some(&args[1])) {
                    return refuse(
                        "E_SQL_SHAPE",
                        format!("the binder of {} must be a bare name", name),
                        args[1].pos,
                    );
                }
                plan.order_by.push(RelationalOrder {
                    binder: args[1].str.clone(),
                    node: args[2].clone(),
                    dir,
                    pos: step.pos,
                    over_groups: false,
                });
            } else {
                return refuse(
                    "E_ARITY",
                    format!("{} takes 1 to 3 arguments", name),
                    step.pos,
                );
            }
            return Ok(());
        }

        // SORT_BY or TOP_BY. A text literal in the third place is a direction
        // and wins over a bare name in the second (`text-direction-wins-over-
        // bare-name`); otherwise a bare name there is the binder.
        let binder;
        let key;
        let dir;

        if count == 2 {
            binder = "_";
            key = &args[1];
            dir = SortDirection::Asc;
        } else if count == 3 {
            // Decided off the call as written: a helper inlined into the third
            // slot is that slot's name, so the second is the binder and the
            // helper the key (stmt.order-by.helper-in-the-key-slot-...).
            if args[2].is_written_text() {
                binder = "_";
                key = &args[1];
                if args[2].str.eq_ignore_ascii_case("ASC") {
                    dir = SortDirection::Asc;
                } else if args[2].str.eq_ignore_ascii_case("DESC") {
                    dir = SortDirection::Desc;
                } else {
                    return refuse("E_BAD_ARG", "sort direction must be 'ASC' or 'DESC'", args[2].pos);
                }
            } else if is_binder_name(Some(&args[1])) {
                binder = &args[1].str;
                key = &args[2];
                dir = SortDirection::Asc;
            } else {
                return refuse(
                    "E_BAD_ARG",
                    "sort direction must be 'ASC' or 'DESC'",
                    args[2].pos,
                );
            }
        } else if count == 4 {
            if !is_binder_name(Some(&args[1])) {
                return refuse(
                    "E_SQL_SHAPE",
                    "the binder of SORT_BY must be a bare name",
                    args[1].pos,
                );
            }
            binder = &args[1].str;
            key = &args[2];
            if !args[3].is_written_text() {
                return refuse(
                    "E_BAD_ARG",
                    "sort direction must be 'ASC' or 'DESC'",
                    args[3].pos,
                );
            }
            if args[3].str.eq_ignore_ascii_case("ASC") {
                dir = SortDirection::Asc;
            } else if args[3].str.eq_ignore_ascii_case("DESC") {
                dir = SortDirection::Desc;
            } else {
                return refuse(
                    "E_BAD_ARG",
                    "sort direction must be 'ASC' or 'DESC'",
                    args[3].pos,
                );
            }
        } else {
            return refuse("E_ARITY", "SORT_BY takes 2 to 4 arguments", step.pos);
        }

        plan.order_by.push(RelationalOrder {
            binder: binder.to_string(),
            node: key.clone(),
            dir,
            pos: step.pos,
            over_groups: false,
        });
        Ok(())
    }

    pub fn analyze_pipeline(&mut self, ast: &SNode) -> Result<Option<RelationalPlan>, SqlError> {
        if identity_loss_before_grouping(Some(ast), NeededFields::new()) {
            return refuse(
                "E_SQL_SHAPE",
                "grouping depends on a computed projection without identity preservation",
                ast.pos,
            );
        }
        let mut steps = Vec::new();
        let mut curr = ast;

        while curr.t == SNodeType::Call && is_pipeline_op(&curr.str) && !curr.kids.is_empty() {
            steps.push(curr.clone());
            curr = &curr.kids[0];
        }

        if curr.t != SNodeType::Var {
            return Ok(None);
        }

        if !self.bindings.has(&curr.str) {
            return Ok(None);
        }

        let b = self.bindings.get(&curr.str, curr.pos)?;
        if b.kind != crate::sql::binding::BindingKind::Relation {
            return Ok(None);
        }

        let mut plan = RelationalPlan::new();
        plan.source_name = curr.str.clone();
        let root = curr.str.clone();
        plan.root_name = Some(root);
        let rel_spec = b.relation.as_ref().unwrap();
        plan.source_from_raw = rel_spec.from.is_raw;
        if rel_spec.from.is_raw {
            plan.source_table = rel_spec.from.raw.clone();
        } else {
            plan.source_table = rel_spec.from.table.clone();
        }
        plan.source_alias = rel_spec.alias.clone();
        plan.correlate = rel_spec.correlate.clone();
        plan.source_relation = Some(b.clone());

        // reverse steps to process from root to tip
        steps.reverse();
        let last_step_pos = steps.last().map(|step| step.pos);

        for step in steps {
            let name = step.str.as_str();
            let args = &step.kids;

            let over_groups = plan.bucket == BucketState::Open;
            if plan.bucket == BucketState::Open && name != "FILTER" && name != "MAP" {
                plan.bucket = BucketState::Sealed;
            }

            match name {
                "FILTER" => {
                    if plan.bucket == BucketState::Sealed {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a FILTER over buckets must follow the BUCKET directly: SQL keeps a bucket's members only for the projection that ends the grouping",
                            step.pos,
                        );
                    }
                    let need_derived = plan.limit.is_some()
                        || plan.offset.is_some()
                        || (plan.group_by.is_none()
                            && (plan.projections.is_some()
                                || plan.select_cols.is_some()
                                || plan.distinct));
                    // A sort does not force the wrap: the WHERE goes beside the
                    // ORDER BY in the same SELECT, because a derived table does
                    // not keep an ORDER BY that has no LIMIT beside it (a filter
                    // commutes with a stable sort).
                    plan = self.ensure_derived(plan, need_derived)?;

                    let binder;
                    let pred;
                    if args.len() == 2 {
                        binder = "_";
                        pred = &args[1];
                    } else if args.len() == 3 {
                        if !is_binder_name(Some(&args[1])) {
                            return refuse(
                                "E_SQL_SHAPE",
                                "the binder of FILTER must be a bare name",
                                args[1].pos,
                            );
                        }
                        binder = &args[1].str;
                        pred = &args[2];
                    } else {
                        return refuse("E_ARITY", "FILTER takes 2 or 3 arguments", step.pos);
                    }

                    if plan.group_by.is_some() {
                        plan.having.push(RelationalFilter {
                            binder: binder.to_string(),
                            node: pred.clone(),
                            pos: step.pos,
                            over_groups,
                        });
                    } else {
                        plan.filters.push(RelationalFilter {
                            binder: binder.to_string(),
                            node: pred.clone(),
                            pos: step.pos,
                            over_groups: false,
                        });
                    }
                }
                "BUCKET" => {
                    if plan.bucket != BucketState::None {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a BUCKET over buckets: SQL keeps a bucket's members only for the projection that ends the grouping",
                            step.pos,
                        );
                    }
                    // Groups appear in order of their first member, and the members were
                    // sorted: a GROUP BY returns its groups in no order.
                    if !plan.order_by.is_empty() || plan.order_dropped {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a BUCKET over sorted rows would return its groups in no order, where SEL has them in the order of their first member in the sorted list",
                            step.pos,
                        );
                    }
                    let need_derived = self.plan_has_rows_above(&plan);
                    plan = self.ensure_derived(plan, need_derived)?;

                    let binder;
                    let key_node;
                    let agg_node;
                    if args.len() == 2 {
                        binder = "_";
                        key_node = &args[1];
                        agg_node = None;
                    } else if args.len() == 3 {
                        binder = "_";
                        key_node = &args[1];
                        agg_node = Some(&args[2]);
                    } else if args.len() == 4 {
                        if !is_binder_name(Some(&args[1])) {
                            return refuse(
                                "E_SQL_SHAPE",
                                "the binder of BUCKET must be a bare name",
                                args[1].pos,
                            );
                        }
                        binder = &args[1].str;
                        key_node = &args[2];
                        agg_node = Some(&args[3]);
                    } else {
                        return refuse("E_ARITY", "BUCKET takes 2 to 4 arguments", step.pos);
                    }

                    let several_keys = (key_node.t == SNodeType::Call
                        && (key_node.str == "LIST" || key_node.str == "RECORD"))
                        || key_node.t == SNodeType::List;
                    if agg_node.is_none() && several_keys {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a bare BUCKET groups by one text or number key, as an index does; BUCKET(src, key, proj) groups by several",
                            key_node.pos,
                        );
                    }

                    let mut group_by = Vec::new();
                    if (key_node.t == SNodeType::Call && key_node.str == "LIST")
                        || key_node.t == SNodeType::List
                    {
                        for k_arg in &key_node.kids {
                            group_by.push(RelationalGroup {
                                alias: None,
                                binder: binder.to_string(),
                                node: k_arg.clone(),
                                pos: k_arg.pos,
                            });
                        }
                    } else if key_node.t == SNodeType::Call && key_node.str == "RECORD" {
                        for (k, v) in record_fields(key_node, &self.dialect)? {
                            group_by.push(RelationalGroup {
                                alias: Some(k),
                                binder: binder.to_string(),
                                node: v.clone(),
                                pos: v.pos,
                            });
                        }
                    } else {
                        group_by.push(RelationalGroup {
                            alias: None,
                            binder: binder.to_string(),
                            node: key_node.clone(),
                            pos: key_node.pos,
                        });
                    }

                    plan.group_by = Some(group_by);
                    if agg_node.is_none() {
                        plan.bucket = BucketState::Open;
                        plan.bare_key = true;
                    } else {
                        plan.bucket = BucketState::None;
                        plan.bare_key = false;
                    }
                    self.bucket_projection(&mut plan, binder, agg_node)?;
                }
                "SELECT_COLS" => {
                    let need_derived = self.plan_needs_wrap_before_map(&plan);
                    plan = self.ensure_derived(plan, need_derived)?;

                    let items: Vec<&SNode> = if args.len() == 2 && args[1].t == SNodeType::List {
                        args[1].kids.iter().collect()
                    } else {
                        args[1..].iter().collect()
                    };

                    let mut cols = Vec::new();
                    for item in items {
                        if item.t != SNodeType::Text {
                            return refuse(
                                "E_BAD_ARG",
                                "SELECT_COLS column names must be string literals",
                                item.pos,
                            );
                        }
                        let col = &item.str;
                        check_program_identifier(item)?;
                        let uc = col.to_ascii_uppercase();
                        let mut matches = 0;
                        if let Some(ref src_rel) = plan.source_relation {
                            if let Some(ref rel) = src_rel.relation {
                                if let Some(field) = rel.field(&uc) {
                                    if field.is_raw || field.unavailable {
                                        return refuse("E_SQL_SHAPE", "a raw field cannot be selected by column name", item.pos);
                                    }
                                    matches += 1;
                                }
                            }
                        }
                        for join in &plan.joins {
                            if let Some(ref j_rel) = join.source_relation {
                                if let Some(ref rel) = j_rel.relation {
                                    if let Some(field) = rel.field(&uc) {
                                        if field.is_raw || field.unavailable {
                                            return refuse("E_SQL_SHAPE", "a raw field cannot be selected by column name", item.pos);
                                        }
                                        matches += 1;
                                    }
                                }
                            }
                        }
                        if matches > 1 {
                            return refuse(
                                "E_SQL_SHAPE",
                                format!(
                                    "column '{}' is ambiguous across joined tables; qualify with a table alias",
                                    col
                                ),
                                item.pos,
                            );
                        }
                        if let Some(ref src_rel) = plan.source_relation {
                            if let Some(ref rel) = src_rel.relation {
                                if !rel.fields.is_empty() && matches == 0 {
                                    let mut declared: Vec<_> = rel.fields.keys().cloned().collect();
                                    declared.sort();
                                    return refuse(
                                        "E_SQL_SHAPE",
                                        format!(
                                            "relation {} has no field '{}'; the relation declares {}",
                                            plan.source_name,
                                            col,
                                            declared.join(", ")
                                        ),
                                        item.pos,
                                    );
                                }
                            }
                        }
                        cols.push(col.clone());
                    }
                    plan.select_cols = Some(cols);
                    plan.projections = None;
                }
                "MAP" => {
                    if plan.bucket == BucketState::Sealed {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a MAP over buckets must follow the BUCKET, with at most a FILTER between: SQL keeps a bucket's members only for the projection that ends the grouping",
                            step.pos,
                        );
                    }
                    let binder;
                    let expr;
                    if args.len() == 2 {
                        binder = "_";
                        expr = &args[1];
                    } else if args.len() == 3 {
                        if !is_binder_name(Some(&args[1])) {
                            return refuse(
                                "E_SQL_SHAPE",
                                "the binder of MAP must be a bare name",
                                args[1].pos,
                            );
                        }
                        binder = &args[1].str;
                        expr = &args[2];
                    } else {
                        return refuse("E_ARITY", "MAP takes 2 or 3 arguments", step.pos);
                    }

                    if plan.bucket == BucketState::Open {
                        plan.bucket = BucketState::None;
                        self.bucket_projection(&mut plan, binder, Some(expr))?;
                        continue;
                    }

                    let need_derived = self.plan_needs_wrap_before_map(&plan);
                    plan = self.ensure_derived(plan, need_derived)?;

                    if expr.t == SNodeType::Call && expr.str == "RECORD" {
                        let mut projections = Vec::new();
                        for (k, v) in record_fields(expr, &self.dialect)? {
                            projections.push(RelationalProjection {
                                alias: Some(k),
                                binder: binder.to_string(),
                                node: Some(v),
                                group_key: None,
                            });
                        }
                        plan.projections = Some(projections);
                    } else {
                        plan.projections = Some(vec![RelationalProjection {
                            alias: None,
                            binder: binder.to_string(),
                            node: Some(expr.clone()),
                            group_key: None,
                        }]);
                    }
                    plan.select_cols = None;
                }
                "DISTINCT" | "DEDUPE" => {
                    let need_derived = plan.limit.is_some() || plan.offset.is_some();
                    plan = self.ensure_derived(plan, need_derived)?;
                    if plan.projections.is_none() && plan.select_cols.is_none() {
                        return refuse(
                            "E_SQL_SHAPE",
                            "DISTINCT requires an explicit typed projection",
                            step.pos,
                        );
                    }
                    // DISTINCT keeps the FIRST element of each run in sorted order; SQL's
                    // `SELECT DISTINCT proj ... ORDER BY <column not in proj>` is refused by
                    // PostgreSQL and MySQL 8 and answers an unspecified representative row on
                    // MariaDB, so the step stays in memory.
                    if !plan.order_by.is_empty() {
                        return refuse(
                            "E_SQL_SHAPE",
                            "DISTINCT after a sort keeps the first of each run in sorted order, which SELECT DISTINCT ... ORDER BY does not promise; run the DISTINCT in memory",
                            step.pos,
                        );
                    }
                    plan.distinct = true;
                }
                "TAKE" => {
                    if args.len() != 2 {
                        return refuse("E_ARITY", "TAKE takes 2 arguments", step.pos);
                    }
                    let lim = self.eval_int_param(&args[1], "TAKE")?;
                    if plan.limit.is_none() || lim < plan.limit.unwrap() {
                        plan.limit = Some(lim);
                    }
                }
                "DROP" => {
                    if args.len() != 2 {
                        return refuse("E_ARITY", "DROP takes 2 arguments", step.pos);
                    }
                    let off = self.eval_int_param(&args[1], "DROP")?;
                    let mut skipped = off;
                    if let Some(l) = plan.limit {
                        if l < skipped {
                            skipped = l;
                        }
                    }
                    let curr_off = plan.offset.unwrap_or(0);
                    if let Some(ref mut limit) = plan.limit {
                        *limit -= skipped;
                    }
                    plan.offset = Some(curr_off.saturating_add(skipped));
                }
                "SORT" | "SORT_DESC" | "SORT_BY" | "TOP" | "TOP_DESC" | "TOP_BY" => {
                    let need_derived = plan.limit.is_some()
                        || plan.offset.is_some()
                        || (plan.group_by.is_none()
                            && (plan.projections.is_some()
                                || plan.select_cols.is_some()
                                || plan.distinct));
                    // Sorts are stable, so an earlier sort is the later one's tie-break; a
                    // derived table with no LIMIT beside its ORDER BY does not keep it.
                    if (need_derived
                        && !plan.order_by.is_empty()
                        && plan.limit.is_none()
                        && plan.offset.is_none())
                        || plan.order_dropped
                    {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a sort over a projection of sorted rows loses the earlier sort, which is its tie-break: a derived table does not keep an ORDER BY",
                            step.pos,
                        );
                    }
                    plan = self.ensure_derived(plan, need_derived)?;
                    let before = plan.order_by.len();
                    self.analyze_sort_step(&step, &mut plan)?;
                    let mut added = plan.order_by.split_off(before);
                    for item in &mut added {
                        item.over_groups = over_groups;
                    }
                    added.extend(plan.order_by);
                    plan.order_by = added;
                }
                "LINK" | "LINK_LEFT" => {
                    if !plan.order_by.is_empty()
                        || plan.projections.is_some()
                        || plan.select_cols.is_some()
                        || plan.group_by.is_some()
                    {
                        let params_len = self.params.len();
                        let kinds_len = self.param_kinds.len();
                        let node_count = self.dispatched_nodes;
                        let checked = self.compile_statement(&plan);
                        self.params.truncate(params_len);
                        self.param_kinds.truncate(kinds_len);
                        self.dispatched_nodes = node_count;
                        checked?;
                    }
                    // A join returns its rows in no order, and SEL's are the left list's.
                    if plan.order_dropped
                        || (!plan.order_by.is_empty() && plan.limit.is_none() && plan.offset.is_none())
                    {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a LINK over sorted rows would return them in no order, where SEL has the left list's order",
                            step.pos,
                        );
                    }
                    let need_derived = self.plan_has_rows_above(&plan);
                    plan = self.ensure_derived(plan, need_derived)?;

                    if args.len() != 3 && args.len() != 5 {
                        return refuse(
                            "E_ARITY",
                            format!("{} takes 3 or 5 arguments", name),
                            step.pos,
                        );
                    }
                    let right_node = &args[1];
                    if right_node.t != SNodeType::Var || !self.bindings.has(&right_node.str) {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!("{} requires a bound relation as its right side", name),
                            right_node.pos,
                        );
                    }
                    let right_binding = self.bindings.get(&right_node.str, right_node.pos)?;
                    if right_binding.kind != crate::sql::binding::BindingKind::Relation {
                        return refuse(
                            "E_SQL_SHAPE",
                            format!("{} is not bound as a relation", right_node.str),
                            right_node.pos,
                        );
                    }

                    let join_type = if name == "LINK_LEFT" { JoinType::Left } else { JoinType::Inner };
                    let rel_spec = right_binding.relation.as_ref().unwrap();
                    let from_raw = rel_spec.from.is_raw;
                    let table = if from_raw {
                        rel_spec.from.raw.clone()
                    } else {
                        rel_spec.from.table.clone()
                    };

                    let mut join = RelationalJoin {
                        join_type,
                        source_name: right_node.str.clone(),
                        source_relation: Some(right_binding.clone()),
                        source_from_raw: from_raw,
                        source_table: table,
                        source_alias: rel_spec.alias.clone(),
                        left_names: Vec::new(),
                        right_names: Vec::new(),
                        on_pred: None,
                        pos: step.pos,
                    };

                    if args.len() == 5 {
                        if !is_binder_name(Some(&args[2])) || !is_binder_name(Some(&args[3])) {
                            return refuse(
                                "E_SQL_SHAPE",
                                "join binders must be bare names",
                                args[2].pos,
                            );
                        }
                        join.left_names = vec![args[2].str.clone()];
                        join.right_names = vec![args[3].str.clone()];
                        join.on_pred = Some(args[4].clone());
                    } else {
                        if plan.joins.is_empty() && plan.root_name.is_some() {
                            join.left_names = vec![plan.root_name.as_ref().unwrap().clone()];
                        }
                        join.right_names = vec![right_node.str.clone()];
                        join.on_pred = Some(args[2].clone());
                    }

                    if join.source_alias.is_empty() {
                        if args.len() == 5 {
                            join.source_alias = join.right_names[0].clone();
                        } else {
                            join.source_alias = "_2".to_string();
                        }
                    }

                    let mut open_aliases = vec![plan.source_alias.clone()];
                    if open_aliases[0].is_empty() && plan.source_relation.is_some() {
                        open_aliases[0] = relation_alias(
                            plan.source_relation
                                .as_ref()
                                .and_then(|b| b.relation.as_ref()),
                        );
                    }
                    for j in &plan.joins {
                        if !j.source_alias.is_empty() {
                            open_aliases.push(j.source_alias.clone());
                        }
                    }
                    let mine = join.source_alias.to_ascii_uppercase();
                    for a in open_aliases {
                        if a.to_ascii_uppercase() == mine {
                            return refuse(
                                "E_SQL_SHAPE",
                                format!(
                                    "{} would be joined under the table alias {}, which this statement already uses; bind the relation a second time under another alias",
                                    right_node.str, join.source_alias
                                ),
                                right_node.pos,
                            );
                        }
                    }

                    plan.joins.push(join);
                }
                // Every manifest pipeline step has an arm above (the steps
                // come from is_pipeline_op, and tests/pipeline_vocabulary.rs
                // plans one of each): a step added to the manifest without
                // one is refused here rather than silently dropped.
                _ => {
                    return refuse(
                        "E_SQL_UNSUPPORTED",
                        format!("{} is a pipeline step the statement planner does not handle", name),
                        step.pos,
                    );
                }
            }
        }

        // A derived table with no LIMIT beside its ORDER BY does not keep the
        // order, and this statement has no other ORDER BY: the rows would come
        // back in no order, where SEL's are the sorted list's. Refused at the
        // last step (sql-translation 12.1).
        if plan.order_dropped {
            if let Some(pos) = last_step_pos {
                return refuse(
                    "E_SQL_SHAPE",
                    "these rows come from a sorted derived table, which does not keep its order, and nothing after it sorts them again",
                    pos,
                );
            }
        }
        Ok(Some(plan))
    }

    pub fn compile_statement(&mut self, plan: &RelationalPlan) -> Result<Fragment, SqlError> {
        let check_aliases_proj = |entries: &[RelationalProjection]| -> Result<(), SqlError> {
            let mut seen = HashSet::new();
            for entry in entries {
                if let Some(ref alias) = entry.alias {
                    let upper = alias.to_ascii_uppercase();
                    if seen.contains(&upper) {
                        let pos = entry.node.as_ref().map(|n| n.pos).unwrap_or_default();
                        return refuse(
                            "E_SQL_SHAPE",
                            "duplicate or case-colliding RECORD fields require local evaluation",
                            pos,
                        );
                    }
                    seen.insert(upper);
                }
            }
            Ok(())
        };

        let check_aliases_group = |entries: &[RelationalGroup]| -> Result<(), SqlError> {
            let mut seen = HashSet::new();
            for entry in entries {
                if let Some(ref alias) = entry.alias {
                    let upper = alias.to_ascii_uppercase();
                    if seen.contains(&upper) {
                        return refuse(
                            "E_SQL_SHAPE",
                            "duplicate or case-colliding RECORD fields require local evaluation",
                            entry.pos,
                        );
                    }
                    seen.insert(upper);
                }
            }
            Ok(())
        };

        if let Some(ref projections) = plan.projections {
            check_aliases_proj(projections)?;
        }
        if let Some(ref group_by) = plan.group_by {
            check_aliases_group(group_by)?;
        }

        let prev_plan = self.statement_plan.clone();
        self.statement_plan = Some(plan.clone());

        let res = self.compile_statement_inner(plan);
        self.statement_plan = prev_plan;
        res
    }

    fn compile_statement_inner(&mut self, plan: &RelationalPlan) -> Result<Fragment, SqlError> {
        let mut parts = Vec::new();

        if plan.distinct {
            parts.push(Part::Sql("SELECT DISTINCT ".to_string()));
        } else {
            parts.push(Part::Sql("SELECT ".to_string()));
        }

        let src_rel = plan
            .source_relation
            .as_ref()
            .and_then(|b| b.relation.clone());
        let mut src = Source {
            shape: SourceShape::Relation,
            relation: src_rel.clone(),
            filters: Vec::new(),
            elements: Vec::new(),
            scalar_rule: false,
        };
        for f in &plan.filters {
            src.filters.push(SourceFilter {
                binder: f.binder.clone(),
                node: f.node.clone(),
            });
        }

        // 1. SELECT list (Projections)
        if let Some(ref projections) = plan.projections {
            let mut first = true;
            for proj in projections {
                if !first {
                    parts.push(Part::Sql(", ".to_string()));
                }
                first = false;
                let mut p_frag = if let Some(ref gk) = proj.group_key {
                    self.group_key(&src, gk, true)?
                } else if plan.group_by.is_some() {
                    self.with_group(&src, &proj.binder, |t| {
                        t.node(proj.node.as_ref().unwrap())
                    })?
                } else {
                    self.with_row(&src, &proj.binder, |t| {
                        t.node(proj.node.as_ref().unwrap())
                    })?
                };
                if plan.distinct {
                    if (p_frag.kind == SqlKind::Unknown || p_frag.kind == SqlKind::Num)
                        && !p_frag.canonical
                    {
                        return refuse(
                            "E_SQL_SHAPE",
                            "DISTINCT requires proven structural output identity",
                            proj.node.as_ref().unwrap().pos,
                        );
                    }
                    p_frag = self.identity_group_key(proj.node.as_ref().unwrap(), &p_frag)?;
                }
                parts.extend(p_frag.parts);
                if let Some(ref alias) = proj.alias {
                    parts.push(Part::Sql(format!(" AS {}", self.emit.ident(alias))));
                }
            }
        } else if let Some(ref select_cols) = plan.select_cols {
            let mut first = true;
            for col in select_cols {
                if !first {
                    parts.push(Part::Sql(", ".to_string()));
                }
                first = false;
                let mut owner = src_rel.as_ref();
                let mut f_spec = None;
                if let Some(ref rel) = src_rel {
                    f_spec = rel.field(col);
                }
                for join in &plan.joins {
                    if f_spec.is_none() {
                        if let Some(ref j_rel) = join.source_relation {
                            if let Some(ref rel) = j_rel.relation {
                                if let Some(f) = rel.field(col) {
                                    f_spec = Some(f);
                                    owner = Some(rel);
                                }
                            }
                        }
                    }
                }
                let mut column = col.as_str();
                if let Some(fs) = f_spec {
                    if !fs.column.is_empty() {
                        column = &fs.column;
                    }
                }
                if plan.distinct
                    && (f_spec.is_none()
                        || f_spec.unwrap().sql_type == SqlKind::Unknown
                        || f_spec.unwrap().sql_type == SqlKind::Num)
                {
                    return refuse(
                        "E_SQL_SHAPE",
                        "DISTINCT requires known output kinds",
                        Pos::default(),
                    );
                }
                let sql_str = if plan.joins.is_empty() && plan.source_subquery.is_none() {
                    let mut table = "";
                    if let Some(fs) = f_spec {
                        if !fs.table.is_empty() {
                            table = &fs.table;
                        } else if !plan.source_alias.is_empty() {
                            table = &plan.source_alias;
                        }
                    } else if !plan.source_alias.is_empty() {
                        table = &plan.source_alias;
                    }
                    self.emit.column(table, column)
                } else {
                    self.emit
                        .column(&self.relation_table_alias(owner, ""), column)
                };
                if plan.distinct
                    && f_spec
                        .map(|s| s.sql_type == SqlKind::Text || s.sql_type == SqlKind::Num)
                        .unwrap_or(false)
                {
                    let frag = self.emit.text_operand(&Fragment::new(
                        vec![Part::Sql(sql_str)],
                        f_spec.unwrap().sql_type,
                        &self.dialect,
                        Vec::new(),
                        Vec::new(),
                        Vec::new(),
                    ))?;
                    parts.extend(frag.parts);
                    parts.push(Part::Sql(format!(" AS {}", self.emit.ident(column))));
                } else {
                    parts.push(Part::Sql(sql_str));
                }
            }
        } else if !plan.joins.is_empty() {
            let fields = joined_row_fields(plan)?;
            if fields.is_empty() {
                let last = plan.joins.last().unwrap();
                return refuse(
                    "E_SQL_SHAPE",
                    "the joined row has no field SQL can carry: every field is on both sides, and the binders are nested records",
                    last.pos,
                );
            }
            let mut first = true;
            for f in fields {
                if !first {
                    parts.push(Part::Sql(", ".to_string()));
                }
                first = false;
                let mut col = f.name.as_str();
                if !f.spec.column.is_empty() {
                    col = &f.spec.column;
                }
                parts.push(Part::Sql(self.emit.column(&f.table, col)));
            }
        } else if !plan.source_alias.is_empty() {
            parts.push(Part::Sql(format!("{}.*", self.emit.ident(&plan.source_alias))));
        } else {
            parts.push(Part::Sql("*".to_string()));
        }

        // 2. FROM clause
        parts.push(Part::Sql(" FROM ".to_string()));
        if let Some(ref subquery) = plan.source_subquery {
            let sub_frag = self.compile_statement(subquery)?;
            parts.push(Part::Sql("(".to_string()));
            parts.extend(sub_frag.parts);
            parts.push(Part::Sql(")".to_string()));
            if !plan.source_alias.is_empty() {
                parts.push(Part::Sql(format!(" {}", self.emit.ident(&plan.source_alias))));
            }
        } else {
            let mut from = if plan.source_from_raw {
                plan.source_table.clone()
            } else {
                self.emit.ident(&plan.source_table)
            };
            if !plan.source_alias.is_empty() {
                from.push(' ');
                from.push_str(&self.emit.ident(&plan.source_alias));
            }
            parts.push(Part::Sql(from));
        }

        // Joins
        for join in &plan.joins {
            if join.join_type == JoinType::Left {
                parts.push(Part::Sql(" LEFT JOIN ".to_string()));
            } else {
                parts.push(Part::Sql(" INNER JOIN ".to_string()));
            }
            let mut right = if join.source_from_raw {
                join.source_table.clone()
            } else {
                self.emit.ident(&join.source_table)
            };
            if !join.source_alias.is_empty() {
                right.push(' ');
                right.push_str(&self.emit.ident(&join.source_alias));
            }
            parts.push(Part::Sql(format!("{} ON ", right)));
            let on = self.with_join_binders(plan, join, |t| {
                let n = t.node(join.on_pred.as_ref().unwrap())?;
                t.require_bool(n, join.pos, "LINK")
            })?;
            parts.extend(on.parts);
        }

        // 3. WHERE clause
        let mut cond_parts = Vec::new();
        if !plan.correlate.is_empty() {
            cond_parts.push(vec![Part::Sql(format!("({})", plan.correlate))]);
        }
        self.in_where = true;
        for filter in &plan.filters {
            let c_frag = self.with_row(&src, &filter.binder, |t| {
                let n = t.node(&filter.node)?;
                t.require_bool(n, filter.pos, "FILTER")
            })?;
            cond_parts.push(c_frag.parts);
        }
        self.in_where = false;

        if !cond_parts.is_empty() {
            parts.push(Part::Sql(" WHERE ".to_string()));
            for (i, cp) in cond_parts.into_iter().enumerate() {
                if i > 0 {
                    parts.push(Part::Sql(" AND ".to_string()));
                }
                parts.extend(cp);
            }
        }

        // 4. GROUP BY clause
        if let Some(ref group_by) = plan.group_by {
            if !group_by.is_empty() {
                parts.push(Part::Sql(" GROUP BY ".to_string()));
                let mut first = true;
                for gb in group_by {
                    if !first {
                        parts.push(Part::Sql(", ".to_string()));
                    }
                    first = false;
                    let g_frag = self.group_key(&src, gb, false)?;
                    if plan.bare_key && (g_frag.kind == SqlKind::Bool || g_frag.kind == SqlKind::Bin) {
                        return refuse(
                            "E_SQL_SHAPE",
                            "a bare BUCKET groups by one text or number key, as an index does; SEL refuses a boolean or binary key (E_NOT_TEXT)",
                            gb.pos,
                        );
                    }
                    parts.extend(g_frag.parts);
                }
            }
        }

        // 5. HAVING clause
        if !plan.having.is_empty() {
            parts.push(Part::Sql(" HAVING ".to_string()));
            let mut h_parts = Vec::new();
            for hav in &plan.having {
                let h_frag = if hav.over_groups {
                    self.with_group(&src, &hav.binder, |t| {
                        let n = t.node(&hav.node)?;
                        t.require_bool(n, hav.pos, "FILTER")
                    })?
                } else {
                    self.with_projected(&src, &hav.binder, |t| {
                        let n = t.node(&hav.node)?;
                        t.require_bool(n, hav.pos, "FILTER")
                    })?
                };
                h_parts.push(h_frag.parts);
            }
            for (i, hp) in h_parts.into_iter().enumerate() {
                if i > 0 {
                    parts.push(Part::Sql(" AND ".to_string()));
                }
                parts.extend(hp);
            }
        }

        // 6. ORDER BY clause
        if !plan.order_by.is_empty() {
            parts.push(Part::Sql(" ORDER BY ".to_string()));
            let mut first = true;
            for ord in &plan.order_by {
                if !first {
                    parts.push(Part::Sql(", ".to_string()));
                }
                first = false;
                let pos = ord.node.pos;
                let raw_frag = if ord.over_groups {
                    self.with_group(&src, &ord.binder, |t| t.node(&ord.node))?
                } else if plan.group_by.is_some() {
                    self.with_projected(&src, &ord.binder, |t| t.node(&ord.node))?
                } else {
                    self.with_row(&src, &ord.binder, |t| t.node(&ord.node))?
                };
                let o_frag = self.order_key(raw_frag, pos)?;
                parts.extend(o_frag.parts);
                parts.push(Part::Sql(format!(" {}", ord.dir.as_sql())));
            }
        }

        // 7. LIMIT / OFFSET clause
        if let (Some(limit), Some(offset)) = (plan.limit, plan.offset) {
            parts.push(Part::Sql(format!(" LIMIT {} OFFSET {}", limit, offset)));
        } else if let Some(limit) = plan.limit {
            parts.push(Part::Sql(format!(" LIMIT {}", limit)));
        } else if let Some(offset) = plan.offset {
            let ch = chain(&self.dialect);
            let has_target = |target: &str| ch.iter().any(|c| c == target);
            if has_target("mariadb") || has_target("mysql") || has_target("mysql-family") {
                parts.push(Part::Sql(format!(
                    " LIMIT 18446744073709551615 OFFSET {}",
                    offset
                )));
            } else if has_target("sqlite") {
                parts.push(Part::Sql(format!(" LIMIT -1 OFFSET {}", offset)));
            } else {
                parts.push(Part::Sql(format!(" OFFSET {}", offset)));
            }
        }

        Ok(Fragment::new(
            parts,
            SqlKind::Statement,
            &self.dialect,
            self.params.clone(),
            self.param_kinds.clone(),
            self.caveats.clone(),
        ))
    }
}
