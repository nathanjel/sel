use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use crate::shape::{intern_record_shape, RecordShape};
use crate::value::{Entry, Kind, Value};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JoinPlanOp {
    LeftSlot,
    LeftNested,
    RightSlot,
    Left,
    Right,
}

#[derive(Clone, Debug)]
pub struct JoinPlanSlot {
    pub op: JoinPlanOp,
    pub slot: usize,
}

#[derive(Clone, Debug)]
pub struct JoinRestCheck {
    pub slot: usize,
    pub nested: bool,
}

#[derive(Clone, Debug)]
pub struct JoinPlan {
    pub shape: Arc<RecordShape>,
    pub slots: Vec<JoinPlanSlot>,
    pub lrest: Vec<JoinRestCheck>,
    pub rkept: Vec<usize>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct JoinPlanKey {
    pub left_shape_id: u64,
    pub right_shape_id: u64,
    pub matched: bool,
}

pub struct JoinFlatTest {
    pub binder_names: HashSet<String>,
    pub last_shape_id: Option<u64>,
    pub slots: Vec<usize>,
}

impl JoinFlatTest {
    pub fn new(b1: &str, b2: &str) -> Self {
        let mut bm = HashSet::new();
        for k in binder_keys(b1, "_1") {
            bm.insert(k);
        }
        for k in binder_keys(b2, "_2") {
            bm.insert(k);
        }
        Self {
            binder_names: bm,
            last_shape_id: None,
            slots: Vec::new(),
        }
    }

    pub fn is_flat(&mut self, row: &Value) -> bool {
        let inner = row.0.borrow();
        let shape = match inner.shape {
            Some(ref sh) => sh,
            None => return false,
        };
        if self.last_shape_id != Some(shape.id) {
            self.last_shape_id = Some(shape.id);
            self.slots.clear();
            for (i, k) in shape.keys.iter().enumerate() {
                if !self.binder_names.contains(k) {
                    self.slots.push(i);
                }
            }
        }
        let storage = inner.storage.as_ref().unwrap();
        for &i in &self.slots {
            if !is_scalar_field(&storage[i]) {
                return false;
            }
        }
        true
    }
}

/// A non-empty record: a side of an inner join, nested in the row an outer
/// join builds over it.
pub fn is_left_nested(v: &Value) -> bool {
    let inner = v.0.borrow();
    inner.kind == Kind::None && !inner.is_list && inner.size() > 0
}

pub fn binder_keys(name: &str, positional: &str) -> Vec<String> {
    let mut keys = vec![name.to_string()];
    let lower = name.to_ascii_lowercase();
    if lower != name {
        keys.push(lower);
    }
    if !keys.iter().any(|k| k == positional) {
        keys.push(positional.to_string());
    }
    keys
}

/// Whether a field holds a scalar or a list: anything but a record (or the
/// none value, which a missing right side is).
pub fn is_scalar_field(v: &Value) -> bool {
    let inner = v.0.borrow();
    inner.kind != Kind::None || inner.is_list
}

/// The joined row's layout (spec §7.4), from the two sides' keys and what
/// each field holds: the left side's nested records (a join under a join),
/// the binder names of both sides (`b1`/`_1`, `b2`/`_2`), the left side's
/// other fields that the right side does not also have, and -- for a matched
/// row -- the right side's scalar fields the left side does not have. A key
/// already placed keeps its first place. `make_joined_row` (unshaped rows)
/// and `compile_join_plan` (shaped rows, compiled once per shape pair) both
/// lay a row out by it.
fn joined_layout<K: AsRef<str>>(
    left_keys: &[K],
    left_nested: impl Fn(usize) -> bool,
    right_keys: &[K],
    right_scalar: impl Fn(usize) -> bool,
    b1: &str,
    b2: &str,
    matched: bool,
) -> (Vec<String>, Vec<JoinPlanSlot>) {
    let mut keys: Vec<String> = Vec::new();
    let mut slots: Vec<JoinPlanSlot> = Vec::new();
    let mut slot_map: HashMap<String, usize> = HashMap::new();
    let mut place = |key: String, slot: JoinPlanSlot, replace: bool, keys: &mut Vec<String>, slots: &mut Vec<JoinPlanSlot>| {
        if let Some(&idx) = slot_map.get(&key) {
            if replace {
                slots[idx] = slot;
            }
        } else {
            slot_map.insert(key.clone(), keys.len());
            keys.push(key);
            slots.push(slot);
        }
    };

    for (i, k) in left_keys.iter().enumerate() {
        if left_nested(i) {
            place(k.as_ref().to_string(), JoinPlanSlot { op: JoinPlanOp::LeftNested, slot: i }, false, &mut keys, &mut slots);
        }
    }
    for name in binder_keys(b1, "_1") {
        place(name, JoinPlanSlot { op: JoinPlanOp::Left, slot: 0 }, true, &mut keys, &mut slots);
    }
    for name in binder_keys(b2, "_2") {
        place(name, JoinPlanSlot { op: JoinPlanOp::Right, slot: 0 }, true, &mut keys, &mut slots);
    }
    let right_names: HashSet<String> = right_keys.iter().map(|k| k.as_ref().to_ascii_uppercase()).collect();
    for (i, k) in left_keys.iter().enumerate() {
        if !left_nested(i) && !right_names.contains(&k.as_ref().to_ascii_uppercase()) {
            place(k.as_ref().to_string(), JoinPlanSlot { op: JoinPlanOp::LeftSlot, slot: i }, false, &mut keys, &mut slots);
        }
    }
    if matched {
        let left_names: HashSet<String> = left_keys.iter().map(|k| k.as_ref().to_ascii_uppercase()).collect();
        for (j, k) in right_keys.iter().enumerate() {
            if right_scalar(j) && !left_names.contains(&k.as_ref().to_ascii_uppercase()) {
                place(k.as_ref().to_string(), JoinPlanSlot { op: JoinPlanOp::RightSlot, slot: j }, false, &mut keys, &mut slots);
            }
        }
    }
    (keys, slots)
}

pub fn make_joined_row(
    left: &Value,
    right: Option<&Value>,
    b1: &str,
    b2: &str,
    null_right: Option<&Value>,
) -> Value {
    let default_none = Value::none();
    let rside = right.or(null_right).unwrap_or(&default_none);
    let left_entries = left.entries();
    let right_entries = if rside.size() > 0 && !rside.is_list() {
        rside.entries()
    } else {
        Vec::new()
    };
    let left_keys: Vec<&str> = left_entries.iter().map(|e| e.key.as_str()).collect();
    let right_keys: Vec<&str> = right_entries.iter().map(|e| e.key.as_str()).collect();
    let (keys, slots) = joined_layout(
        &left_keys,
        |i| is_left_nested(&left_entries[i].val),
        &right_keys,
        |j| is_scalar_field(&right_entries[j].val),
        b1,
        b2,
        right.is_some(),
    );
    let entries = keys
        .into_iter()
        .zip(slots)
        .map(|(key, s)| {
            let val = match s.op {
                JoinPlanOp::LeftSlot | JoinPlanOp::LeftNested => left_entries[s.slot].val.clone(),
                JoinPlanOp::RightSlot => right_entries[s.slot].val.clone(),
                JoinPlanOp::Left => left.clone(),
                JoinPlanOp::Right => rside.clone(),
            };
            Entry { key, val }
        })
        .collect();
    Value::record_from_entries(entries)
}

pub struct JoinProjector {
    pub b1: String,
    pub b2: String,
    pub null_right: Option<Value>,
    pub right_flat: bool,

    plans: HashMap<JoinPlanKey, Vec<JoinPlan>>,
    last_pair: Option<JoinPlanKey>,
    last_plan: Option<JoinPlan>,
    last_left_ptr: usize,
}

impl JoinProjector {
    pub fn new(b1: String, b2: String, null_right: Option<Value>) -> Self {
        Self {
            b1,
            b2,
            null_right,
            right_flat: false,
            plans: HashMap::new(),
            last_pair: None,
            last_plan: None,
            last_left_ptr: 0,
        }
    }

    pub fn project(&mut self, left: &Value, right: Option<&Value>) -> Value {
        // Allocated only for an unmatched row with no null record to hand.
        let default_none;
        let rside = if let Some(r) = right {
            r
        } else if let Some(ref nr) = self.null_right {
            nr
        } else {
            default_none = Value::none();
            &default_none
        };

        let left_inner = left.0.borrow();
        let rside_inner = rside.0.borrow();

        let (left_shape, rside_shape) = match (&left_inner.shape, &rside_inner.shape) {
            (Some(ls), Some(rs)) => (ls.clone(), rs.clone()),
            _ => {
                drop(left_inner);
                drop(rside_inner);
                return make_joined_row(left, right, &self.b1, &self.b2, self.null_right.as_ref());
            }
        };
        drop(left_inner);
        drop(rside_inner);

        let matched = right.is_some();
        let key = JoinPlanKey {
            left_shape_id: left_shape.id,
            right_shape_id: rside_shape.id,
            matched,
        };

        let cur_left_ptr = left.0.as_ptr() as usize;
        let same_left = self.last_left_ptr == cur_left_ptr;
        let check_right = !self.right_flat;

        if let Some(ref plan) = self.last_plan {
            if self.last_pair == Some(key) {
                let check_left = !same_left;
                if let Some(out) = build_join_plan(plan, left, rside, check_left, check_right) {
                    if !same_left {
                        self.last_left_ptr = cur_left_ptr;
                    }
                    return out;
                }
            }
        }

        if let Some(list) = self.plans.get(&key) {
            for plan in list {
                if let Some(out) = build_join_plan(plan, left, rside, true, check_right) {
                    self.last_pair = Some(key);
                    self.last_plan = Some(plan.clone());
                    self.last_left_ptr = cur_left_ptr;
                    return out;
                }
            }
        }

        let plan = match compile_join_plan(left, rside, &self.b1, &self.b2, matched) {
            Some(p) => p,
            None => {
                return make_joined_row(left, right, &self.b1, &self.b2, self.null_right.as_ref());
            }
        };

        let out = build_join_plan(&plan, left, rside, false, false).unwrap();
        self.plans.entry(key).or_default().push(plan.clone());
        self.last_pair = Some(key);
        self.last_plan = Some(plan);
        self.last_left_ptr = cur_left_ptr;

        out
    }
}

pub fn compile_join_plan(
    left: &Value,
    rside: &Value,
    b1: &str,
    b2: &str,
    matched: bool,
) -> Option<JoinPlan> {
    let left_inner = left.0.borrow();
    let rside_inner = rside.0.borrow();

    let left_shape = left_inner.shape.as_ref()?.clone();
    let rside_shape = rside_inner.shape.as_ref()?.clone();

    let l_keys: &[String] = &left_shape.keys;
    let l_storage = left_inner.storage.as_ref().unwrap();
    let r_keys: &[String] = &rside_shape.keys;
    let r_storage: &[Value] = rside_inner.storage.as_deref().unwrap_or(&[]);
    let (keys, slots) = joined_layout(
        l_keys,
        |i| is_left_nested(&l_storage[i]),
        r_keys,
        |j| is_scalar_field(&r_storage[j]),
        b1,
        b2,
        matched,
    );

    let result_shape = intern_record_shape(&keys);

    let mut lrest = Vec::new();
    let mut copied = vec![false; l_storage.len()];
    for s in &slots {
        if s.op == JoinPlanOp::LeftSlot || s.op == JoinPlanOp::LeftNested {
            copied[s.slot] = true;
        }
    }
    for i in 0..l_storage.len() {
        if !copied[i] {
            lrest.push(JoinRestCheck {
                slot: i,
                nested: is_left_nested(&l_storage[i]),
            });
        }
    }

    let mut rkept = Vec::new();
    if matched {
        let mut left_names = HashSet::new();
        for k in l_keys.iter() {
            left_names.insert(k.to_ascii_uppercase());
        }
        let mut binder_names = HashSet::new();
        for k in binder_keys(b1, "_1") {
            binder_names.insert(k);
        }
        for k in binder_keys(b2, "_2") {
            binder_names.insert(k);
        }
        for (j, k) in r_keys.iter().enumerate() {
            if !left_names.contains(&k.to_ascii_uppercase())
                && !binder_names.contains(k)
                && !is_scalar_field(&r_storage[j])
            {
                rkept.push(j);
            }
        }
    }

    Some(JoinPlan {
        shape: result_shape,
        slots,
        lrest,
        rkept,
    })
}

pub fn build_join_plan(
    plan: &JoinPlan,
    left: &Value,
    rside: &Value,
    check_left: bool,
    check_right: bool,
) -> Option<Value> {
    let left_inner = left.0.borrow();
    let rside_inner = rside.0.borrow();

    let ls = left_inner.storage.as_ref()?;
    let rs = rside_inner.storage.as_ref()?;

    if check_left {
        for lr in &plan.lrest {
            if is_left_nested(&ls[lr.slot]) != lr.nested {
                return None;
            }
        }
    }

    if check_right {
        for &rk in &plan.rkept {
            if is_scalar_field(&rs[rk]) {
                return None;
            }
        }
    }

    let mut storage = Vec::with_capacity(plan.slots.len());
    for s in &plan.slots {
        match s.op {
            JoinPlanOp::LeftSlot => {
                let v = &ls[s.slot];
                if check_left && is_left_nested(v) {
                    return None;
                }
                storage.push(v.clone());
            }
            JoinPlanOp::LeftNested => {
                let v = &ls[s.slot];
                if check_left && !is_left_nested(v) {
                    return None;
                }
                storage.push(v.clone());
            }
            JoinPlanOp::RightSlot => {
                let v = &rs[s.slot];
                if check_right && !is_scalar_field(v) {
                    return None;
                }
                storage.push(v.clone());
            }
            JoinPlanOp::Left => {
                storage.push(left.clone());
            }
            JoinPlanOp::Right => {
                storage.push(rside.clone());
            }
        }
    }

    Some(Value::shaped_record(plan.shape.clone(), storage))
}
