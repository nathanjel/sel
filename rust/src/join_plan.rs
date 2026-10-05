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
            let item = &storage[i];
            let item_inner = item.0.borrow();
            if item_inner.kind == Kind::None && !item_inner.is_list {
                return false;
            }
        }
        true
    }
}

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

const JOIN_SCALAR: usize = 0;
const JOIN_NULL: usize = 1;
const JOIN_NESTED: usize = 2;

fn join_category(v: &Value) -> usize {
    let inner = v.0.borrow();
    if inner.kind != Kind::None || inner.is_list {
        return JOIN_SCALAR;
    }
    if inner.size() > 0 {
        return JOIN_NESTED;
    }
    JOIN_NULL
}

pub fn make_joined_row(
    left: &Value,
    right: Option<&Value>,
    b1: &str,
    b2: &str,
    null_right: Option<&Value>,
) -> Value {
    let mut entries = Vec::new();
    let mut slot: HashMap<String, usize> = HashMap::new();

    let left_entries = left.entries();
    for e in &left_entries {
        if join_category(&e.val) == JOIN_NESTED
            && !slot.contains_key(&e.key) {
                slot.insert(e.key.clone(), entries.len());
                entries.push(Entry { key: e.key.clone(), val: e.val.clone() });
            }
    }

    for name in binder_keys(b1, "_1") {
        if let Some(&idx) = slot.get(&name) {
            entries[idx] = Entry { key: name, val: left.clone() };
        } else {
            slot.insert(name.clone(), entries.len());
            entries.push(Entry { key: name, val: left.clone() });
        }
    }

    let default_none = Value::none();
    let rside = if let Some(r) = right {
        r
    } else if let Some(nr) = null_right {
        nr
    } else {
        &default_none
    };

    for name in binder_keys(b2, "_2") {
        if let Some(&idx) = slot.get(&name) {
            entries[idx] = Entry { key: name, val: rside.clone() };
        } else {
            slot.insert(name.clone(), entries.len());
            entries.push(Entry { key: name, val: rside.clone() });
        }
    }

    let right_entries = if rside.size() > 0 && !rside.is_list() {
        rside.entries()
    } else {
        Vec::new()
    };
    let mut right_names = HashSet::new();
    for e in &right_entries {
        right_names.insert(e.key.to_ascii_uppercase());
    }

    for e in &left_entries {
        if join_category(&e.val) != JOIN_NESTED && !right_names.contains(&e.key.to_ascii_uppercase())
            && !slot.contains_key(&e.key) {
                slot.insert(e.key.clone(), entries.len());
                entries.push(Entry { key: e.key.clone(), val: e.val.clone() });
            }
    }

    if right.is_some() {
        let mut left_names = HashSet::new();
        for e in &left_entries {
            left_names.insert(e.key.to_ascii_uppercase());
        }
        for e in &right_entries {
            if join_category(&e.val) == JOIN_SCALAR && !left_names.contains(&e.key.to_ascii_uppercase())
                && !slot.contains_key(&e.key) {
                    slot.insert(e.key.clone(), entries.len());
                    entries.push(Entry { key: e.key.clone(), val: e.val.clone() });
                }
        }
    }

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

    let mut keys = Vec::new();
    let mut slots = Vec::new();
    let mut slot_map = HashMap::new();

    let l_keys = &left_shape.keys;
    let l_storage = left_inner.storage.as_ref().unwrap();

    for (i, k) in l_keys.iter().enumerate() {
        if is_left_nested(&l_storage[i])
            && !slot_map.contains_key(k) {
                slot_map.insert(k.clone(), keys.len());
                keys.push(k.clone());
                slots.push(JoinPlanSlot {
                    op: JoinPlanOp::LeftNested,
                    slot: i,
                });
            }
    }

    for name in binder_keys(b1, "_1") {
        if let Some(&idx) = slot_map.get(&name) {
            slots[idx] = JoinPlanSlot {
                op: JoinPlanOp::Left,
                slot: 0,
            };
        } else {
            slot_map.insert(name.clone(), keys.len());
            keys.push(name);
            slots.push(JoinPlanSlot {
                op: JoinPlanOp::Left,
                slot: 0,
            });
        }
    }

    for name in binder_keys(b2, "_2") {
        if let Some(&idx) = slot_map.get(&name) {
            slots[idx] = JoinPlanSlot {
                op: JoinPlanOp::Right,
                slot: 0,
            };
        } else {
            slot_map.insert(name.clone(), keys.len());
            keys.push(name);
            slots.push(JoinPlanSlot {
                op: JoinPlanOp::Right,
                slot: 0,
            });
        }
    }

    let r_keys = &rside_shape.keys;
    let mut right_names = HashSet::new();
    for k in r_keys.iter() {
        right_names.insert(k.to_ascii_uppercase());
    }

    for (i, k) in l_keys.iter().enumerate() {
        if !is_left_nested(&l_storage[i]) && !right_names.contains(&k.to_ascii_uppercase())
            && !slot_map.contains_key(k) {
                slot_map.insert(k.clone(), keys.len());
                keys.push(k.clone());
                slots.push(JoinPlanSlot {
                    op: JoinPlanOp::LeftSlot,
                    slot: i,
                });
            }
    }

    if matched {
        let mut left_names = HashSet::new();
        for k in l_keys.iter() {
            left_names.insert(k.to_ascii_uppercase());
        }
        let r_storage = rside_inner.storage.as_ref().unwrap();
        for (j, k) in r_keys.iter().enumerate() {
            let item_inner = r_storage[j].0.borrow();
            if (item_inner.kind != Kind::None || item_inner.is_list)
                && !left_names.contains(&k.to_ascii_uppercase())
                && !slot_map.contains_key(k) {
                    slot_map.insert(k.clone(), keys.len());
                    keys.push(k.clone());
                    slots.push(JoinPlanSlot {
                        op: JoinPlanOp::RightSlot,
                        slot: j,
                    });
                }
        }
    }

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
        let r_storage = rside_inner.storage.as_ref().unwrap();
        for (j, k) in r_keys.iter().enumerate() {
            let item_inner = r_storage[j].0.borrow();
            if !left_names.contains(&k.to_ascii_uppercase())
                && !binder_names.contains(k)
                && (item_inner.kind == Kind::None && !item_inner.is_list)
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
            let item_inner = rs[rk].0.borrow();
            if item_inner.kind != Kind::None || item_inner.is_list {
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
                if check_right {
                    let item_inner = v.0.borrow();
                    if item_inner.kind == Kind::None && !item_inner.is_list {
                        return None;
                    }
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
