use std::collections::HashMap;

use crate::join_prefilter::{JoinPrefilter, JoinReport};
use crate::value::Value;

/// One scope of binder names (`_`, `_K`, a named binder, a LINK's table
/// aliases). A frame holds a handful of names, so it is a short vector searched
/// linearly: no hashing on every variable read, and rebinding a name per row
/// (`set`) allocates nothing.
#[derive(Clone, Debug, Default)]
pub struct Frame(Vec<(String, Value)>);

impl Frame {
    pub fn new() -> Self {
        Self(Vec::new())
    }

    pub fn get(&self, name: &str) -> Option<&Value> {
        self.0.iter().find(|(k, _)| k == name).map(|(_, v)| v)
    }

    pub fn contains_key(&self, name: &str) -> bool {
        self.0.iter().any(|(k, _)| k == name)
    }

    /// Binds `name`, replacing an existing binding of the same name.
    pub fn set(&mut self, name: &str, value: Value) {
        if let Some(slot) = self.0.iter_mut().find(|(k, _)| k == name) {
            slot.1 = value;
        } else {
            self.0.push((name.to_owned(), value));
        }
    }

    /// `HashMap::insert`-shaped binding, for code that owns the name already.
    pub fn insert(&mut self, name: String, value: Value) {
        if let Some(slot) = self.0.iter_mut().find(|(k, _)| *k == name) {
            slot.1 = value;
        } else {
            self.0.push((name, value));
        }
    }
}

impl From<HashMap<String, Value>> for Frame {
    fn from(mapping: HashMap<String, Value>) -> Self {
        Self(mapping.into_iter().collect())
    }
}

pub struct Context {
    pub root: Value,
    pub frames: Vec<Frame>,
    pub depth: usize,
    pub join_prefilter: Option<JoinPrefilter>,
    pub join_prefilter_report: Option<JoinReport>,
}

impl Context {
    pub fn new(root: Value) -> Self {
        Self {
            root,
            frames: Vec::new(),
            depth: 0,
            join_prefilter: None,
            join_prefilter_report: None,
        }
    }

    pub fn lookup(&self, name: &str) -> Option<Value> {
        for frame in self.frames.iter().rev() {
            if let Some(v) = frame.get(name) {
                return Some(v.clone());
            }
        }
        self.root.get(name)
    }

    pub fn is_bound(&self, name: &str) -> bool {
        self.frames.iter().any(|frame| frame.contains_key(name))
    }

    pub fn push_frame(&mut self, mapping: impl Into<Frame>) {
        self.frames.push(mapping.into());
    }

    pub fn pop_frame(&mut self) {
        self.frames.pop();
    }

    /// Rebinds `name` in the innermost frame (the per-row step of an aggregate).
    pub fn bind(&mut self, name: &str, value: Value) {
        if let Some(frame) = self.frames.last_mut() {
            frame.set(name, value);
        }
    }
}
