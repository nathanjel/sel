use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::sync::Arc;

use crate::dec::{dec_cmp, dec_format, dec_parse, Dec};
use crate::text::SelStr;
use std::borrow::Cow;
use crate::limits::MAX_DEPTH;
use crate::shape::{parse_list_slot, unique_record_shape, RecordShape};
use crate::utf8::{validate_text, Pos, SelError};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    None,
    Text,
    Bin,
    Bool,
}

impl Kind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Kind::None => "NONE",
            Kind::Text => "TEXT",
            Kind::Bin => "BIN",
            Kind::Bool => "BOOL",
        }
    }
}

#[derive(Clone, Debug)]
pub struct Entry {
    pub key: String,
    pub val: Value,
}

/// The keys of a list that is not numbered 1..n (what FILTER keeps).
#[derive(Clone, Debug)]
pub enum ListKeys {
    /// Ascending 1-based positions: the keys "3", "7", ... held as numbers.
    Index(Rc<[u32]>),
    /// Arbitrary text keys.
    Text(Rc<[String]>),
}

impl ListKeys {
    pub fn len(&self) -> usize {
        match self {
            ListKeys::Index(ix) => ix.len(),
            ListKeys::Text(t) => t.len(),
        }
    }

    pub fn key(&self, i: usize) -> String {
        match self {
            ListKeys::Index(ix) => ix[i].to_string(),
            ListKeys::Text(t) => t[i].clone(),
        }
    }

    /// The slot holding `key`, if any.
    pub fn position(&self, key: &str) -> Option<usize> {
        match self {
            ListKeys::Index(ix) => {
                // Only a canonical positive integer names a position.
                let b = key.as_bytes();
                if b.is_empty() || b.len() > 10 || b[0] == b'0' || !b.iter().all(|c| c.is_ascii_digit()) {
                    return None;
                }
                let n: u64 = key.parse().ok()?;
                let n = u32::try_from(n).ok()?;
                ix.binary_search(&n).ok()
            }
            ListKeys::Text(t) => t.iter().position(|k| k == key),
        }
    }

    pub fn to_strings(&self) -> Vec<String> {
        (0..self.len()).map(|i| self.key(i)).collect()
    }
}

/// A snapshot of a collection's children, taken before an aggregate walks it:
/// the values, and their keys rendered only when a caller asks for one.
pub struct Elems {
    pub vals: Vec<Value>,
    keys: ElemKeys,
}

enum ElemKeys {
    /// "1", "2", ...
    Position,
    Shape(Arc<[String]>),
    List(ListKeys),
    Owned(Vec<String>),
}

impl Elems {
    pub fn len(&self) -> usize {
        self.vals.len()
    }

    pub fn is_empty(&self) -> bool {
        self.vals.is_empty()
    }

    pub fn key(&self, i: usize) -> String {
        match &self.keys {
            ElemKeys::Position => (i + 1).to_string(),
            ElemKeys::Shape(k) => k[i].clone(),
            ElemKeys::List(k) => k.key(i),
            ElemKeys::Owned(k) => k[i].clone(),
        }
    }

    /// Whether child `i` is keyed by its own 1-based position.
    pub fn keyed_by_position(&self, i: usize) -> bool {
        match &self.keys {
            ElemKeys::Position => true,
            ElemKeys::List(ListKeys::Index(ix)) => ix[i] as usize == i + 1,
            _ => self.key(i) == (i + 1).to_string(),
        }
    }

    /// The source position of child `i` when the keys are positions (plain or
    /// FILTER-kept), so a filter can keep them as numbers.
    pub fn index_key(&self, i: usize) -> Option<u32> {
        match &self.keys {
            ElemKeys::Position => u32::try_from(i + 1).ok(),
            ElemKeys::List(ListKeys::Index(ix)) => Some(ix[i]),
            _ => None,
        }
    }

    pub fn into_entries(self) -> Vec<Entry> {
        let keys = self.keys;
        self.vals
            .into_iter()
            .enumerate()
            .map(|(i, val)| Entry {
                key: match &keys {
                    ElemKeys::Position => (i + 1).to_string(),
                    ElemKeys::Shape(k) => k[i].clone(),
                    ElemKeys::List(k) => k.key(i),
                    ElemKeys::Owned(k) => k[i].clone(),
                },
                val,
            })
            .collect()
    }
}

#[derive(Clone, Debug)]
pub struct Value(pub Rc<RefCell<ValueInner>>);

// One value. Scalars and shaped records -- nearly every value a program
// handles -- use only the inline fields; what few values need (BIN bytes, an
// irregular record's entries and index, FILTER's preserved keys) sits behind
// one pointer, so a value cell stays small.
#[derive(Clone, Debug)]
pub struct ValueInner {
    pub kind: Kind,
    pub bool_val: bool,
    pub is_list: bool,
    pub str_val: SelStr,
    pub dec_val: Option<Dec>,
    pub shape: Option<Arc<RecordShape>>,
    pub storage: Option<Vec<Value>>,
    ext: Option<Box<Rare>>,
}

#[derive(Clone, Debug, Default)]
struct Rare {
    bin_val: Vec<u8>,
    list_keys: Option<ListKeys>,
    entries: Vec<Entry>,
    index: Option<HashMap<String, usize>>,
}

impl ValueInner {
    fn blank(kind: Kind) -> Self {
        ValueInner {
            kind,
            bool_val: false,
            is_list: false,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: None,
            storage: None,
            ext: None,
        }
    }

    fn rare_mut(&mut self) -> &mut Rare {
        self.ext.get_or_insert_with(Default::default)
    }

    /// An irregular record's entries, in order (empty for anything else).
    #[inline]
    pub fn entries(&self) -> &[Entry] {
        self.ext.as_deref().map_or(&[], |r| &r.entries)
    }

    pub fn entries_mut(&mut self) -> &mut Vec<Entry> {
        &mut self.rare_mut().entries
    }

    /// A BIN value's bytes (empty for anything else).
    #[inline]
    pub fn bin(&self) -> &[u8] {
        self.ext.as_deref().map_or(&[], |r| &r.bin_val)
    }

    /// The keys a FILTER preserved, for a list not numbered 1..n.
    #[inline]
    pub fn list_keys(&self) -> Option<&ListKeys> {
        self.ext.as_deref().and_then(|r| r.list_keys.as_ref())
    }

    fn take_list_keys(&mut self) -> Option<ListKeys> {
        self.ext.as_deref_mut().and_then(|r| r.list_keys.take())
    }

    #[inline]
    fn index(&self) -> Option<&HashMap<String, usize>> {
        self.ext.as_deref().and_then(|r| r.index.as_ref())
    }

    pub fn size(&self) -> usize {
        if let Some(ref st) = self.storage {
            st.len()
        } else {
            self.entries().len()
        }
    }

    pub fn is_null(&self) -> bool {
        self.kind == Kind::None && self.size() == 0 && !self.is_list
    }

    /// The scalar text of a TEXT value: its stored text, or its number
    /// formatted when it was computed and never rendered.
    pub fn text_cow(&self) -> Cow<'_, str> {
        match (&self.dec_val, self.str_val.is_empty()) {
            (Some(d), true) => Cow::Owned(dec_format(d)),
            _ => Cow::Borrowed(self.str_val.as_str()),
        }
    }

    pub fn rebuild_index(&mut self) {
        let mut idx = HashMap::with_capacity(self.entries().len());
        for (i, e) in self.entries().iter().enumerate() {
            idx.insert(e.key.clone(), i);
        }
        self.rare_mut().index = Some(idx);
    }
}

impl Value {
    pub fn none() -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::None,
            bool_val: false,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: None,
            storage: None,
            is_list: false,
            ext: None,
        })))
    }

    pub fn null() -> Self {
        Self::none()
    }

    pub fn text(s: &str, pos: Pos) -> Result<Self, SelError> {
        validate_text(s, pos)?;
        Ok(Self::text_owned(s.to_string()))
    }

    pub fn text_owned(s: String) -> Self {
        Self::text_sel(SelStr::from(s))
    }

    /// A TEXT value from text already validated (a literal, a copy).
    pub fn text_sel(s: SelStr) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::Text,
            bool_val: false,
            str_val: s,
            dec_val: None,
            shape: None,
            storage: None,
            is_list: false,
            ext: None,
        })))
    }

    pub fn bin(b: &[u8]) -> Self {
        Self::bin_owned(b.to_vec())
    }

    pub fn bin_owned(b: Vec<u8>) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::Bin,
            bool_val: false,
            str_val: SelStr::EMPTY,
            ext: Some(Box::new(Rare { bin_val: b, ..Default::default() })),
            dec_val: None,
            shape: None,
            storage: None,
            is_list: false,
        })))
    }

    pub fn bool(b: bool) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::Bool,
            bool_val: b,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: None,
            storage: None,
            is_list: false,
            ext: None,
        })))
    }

    /// A number from a host-built decimal. The sign lives in `neg` and a
    /// zero is never negative, whatever the fields said; the digit caps apply
    /// as they do to the same digits in source (E_RANGE).
    pub fn num(d: Dec) -> Result<Self, SelError> {
        let d = match d.repr {
            crate::dec::DecRepr::Small(m) => Dec::from_small(d.neg, m, d.scale),
            crate::dec::DecRepr::Large(b) => Dec::from_large(d.neg, *b, d.scale),
        };
        let d = crate::dec::dec_guard(d, Pos::default())?;
        Ok(Self::num_trusted(d))
    }

    /// A number the evaluator produced: already canonical and within caps.
    pub(crate) fn num_trusted(d: Dec) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::Text,
            bool_val: false,
            str_val: SelStr::EMPTY,
            dec_val: Some(d),
            shape: None,
            storage: None,
            is_list: false,
            ext: None,
        })))
    }

    pub fn num_exact(s: String, d: Dec) -> Self {
        Self::num_exact_sel(SelStr::from(s), d)
    }

    /// A number literal: its source spelling and its parsed value.
    pub fn num_exact_sel(s: SelStr, d: Dec) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::Text,
            bool_val: false,
            str_val: s,
            dec_val: Some(d),
            shape: None,
            storage: None,
            is_list: false,
            ext: None,
        })))
    }

    pub fn int(n: i64) -> Self {
        Self::num_trusted(Dec::from_i64(n))
    }

    pub fn list(items: Vec<Value>) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::None,
            bool_val: false,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: None,
            storage: Some(items),
            is_list: true,
            ext: None,
        })))
    }

    pub fn list_owned(items: Vec<Value>) -> Self {
        Self::list(items)
    }

    pub fn list_with_keys(items: Vec<Value>, keys: Vec<String>) -> Self {
        Self::list_with_list_keys(items, ListKeys::Text(keys.into()))
    }

    pub fn list_with_list_keys(items: Vec<Value>, keys: ListKeys) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::None,
            bool_val: false,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: None,
            storage: Some(items),
            is_list: true,
            ext: Some(Box::new(Rare { list_keys: Some(keys), ..Default::default() })),
        })))
    }

    pub fn shaped_record(shape: Arc<RecordShape>, values: Vec<Value>) -> Self {
        Self(Rc::new(RefCell::new(ValueInner {
            kind: Kind::None,
            bool_val: false,
            str_val: SelStr::EMPTY,
            dec_val: None,
            shape: Some(shape),
            storage: Some(values),
            is_list: false,
            ext: None,
        })))
    }

    pub fn record_from_entries(entries: Vec<Entry>) -> Self {
        if !entries.is_empty() {
            let keys: Vec<String> = entries.iter().map(|e| e.key.clone()).collect();
            if let Some(shape) = unique_record_shape(&keys) {
                let vals = entries.into_iter().map(|e| e.val).collect();
                return Self::shaped_record(shape, vals);
            }
        }
        let v = Self::none();
        for e in entries {
            let _ = v.set(&e.key, e.val, Pos::default());
        }
        v
    }

    // Inspection
    pub fn kind(&self) -> Kind {
        self.0.borrow().kind
    }

    pub fn is_none(&self) -> bool {
        self.0.borrow().kind == Kind::None
    }

    pub fn is_null(&self) -> bool {
        self.0.borrow().is_null()
    }

    pub fn is_vacuous(&self) -> bool {
        let inner = self.0.borrow();
        if inner.kind == Kind::None && inner.size() == 0 {
            return true;
        }
        if inner.kind == Kind::Text && inner.size() == 0 {
            drop(inner);
            return self.scalar_str().trim().is_empty();
        }
        false
    }

    pub fn is_text(&self) -> bool {
        self.0.borrow().kind == Kind::Text
    }

    pub fn is_bin(&self) -> bool {
        self.0.borrow().kind == Kind::Bin
    }

    pub fn is_bool(&self) -> bool {
        self.0.borrow().kind == Kind::Bool
    }

    pub fn is_list(&self) -> bool {
        self.0.borrow().is_list
    }

    pub fn size(&self) -> usize {
        self.0.borrow().size()
    }

    pub fn scalar(&self) -> String {
        self.scalar_str().to_string()
    }

    /// The scalar text without copying it (a short text is inline, a long
    /// one shared). A computed number is formatted once and kept.
    pub fn scalar_str(&self) -> SelStr {
        let mut inner = self.0.borrow_mut();
        if inner.kind == Kind::Text && inner.str_val.is_empty() {
            if let Some(ref d) = inner.dec_val {
                let formatted = SelStr::from(dec_format(d));
                inner.str_val = formatted;
            }
        }
        inner.str_val.clone()
    }

    pub fn has(&self, key: &str) -> bool {
        let inner = self.0.borrow();
        if let Some(ref shape) = inner.shape {
            return shape.key_map.contains_key(key);
        }
        if inner.is_list && inner.storage.is_some() {
            if let Some(lk) = inner.list_keys() {
                return lk.position(key).is_some();
            }
            let len = inner.storage.as_ref().unwrap().len();
            return parse_list_slot(key, len).is_some();
        }
        if let Some(idx) = inner.index() {
            return idx.contains_key(key);
        }
        inner.entries().iter().any(|e| e.key == key)
    }

    pub fn get(&self, key: &str) -> Option<Value> {
        let inner = self.0.borrow();
        if let Some(ref shape) = inner.shape {
            if let Some(&idx) = shape.key_map.get(key) {
                return inner.storage.as_ref().map(|st| st[idx].clone());
            }
            return None;
        }
        if inner.is_list && inner.storage.is_some() {
            let st = inner.storage.as_ref().unwrap();
            if let Some(lk) = inner.list_keys() {
                return lk.position(key).map(|i| st[i].clone());
            }
            if let Some(idx) = parse_list_slot(key, st.len()) {
                return Some(st[idx].clone());
            }
            return None;
        }
        if let Some(idx) = inner.index() {
            if let Some(&i) = idx.get(key) {
                return Some(inner.entries()[i].val.clone());
            }
            return None;
        }
        for e in inner.entries() {
            if e.key == key {
                return Some(e.val.clone());
            }
        }
        None
    }

    pub fn set(&self, key: &str, val: Value, pos: Pos) -> Result<(), SelError> {
        validate_text(key, pos)?;
        let mut inner = self.0.borrow_mut();

        if inner.shape.is_some() {
            let idx_opt = inner.shape.as_ref().unwrap().key_map.get(key).copied();
            if let Some(idx) = idx_opt {
                inner.storage.as_mut().unwrap()[idx] = val;
                return Ok(());
            }
            // Transition from shaped to entries
            let shape = inner.shape.take().unwrap();
            let storage = inner.storage.take().unwrap();
            let mut entries = Vec::with_capacity(shape.keys.len() + 1);
            for (k, v) in shape.keys.iter().zip(storage.into_iter()) {
                entries.push(Entry {
                    key: k.clone(),
                    val: v,
                });
            }
            *inner.entries_mut() = entries;
            inner.rebuild_index();
        } else if inner.is_list && inner.storage.is_some() {
            let st_len = inner.storage.as_ref().unwrap().len();
            let found_idx = if let Some(lk) = inner.list_keys() {
                lk.position(key)
            } else {
                parse_list_slot(key, st_len)
            };
            if let Some(idx) = found_idx {
                inner.storage.as_mut().unwrap()[idx] = val;
                return Ok(());
            }
            // Transition from list to entries
            let storage = inner.storage.take().unwrap();
            let list_keys = inner.take_list_keys();
            let mut entries = Vec::with_capacity(storage.len() + 1);
            if let Some(lk) = list_keys {
                for (k, v) in lk.to_strings().into_iter().zip(storage.into_iter()) {
                    entries.push(Entry { key: k, val: v });
                }
            } else {
                for (i, v) in storage.into_iter().enumerate() {
                    entries.push(Entry {
                        key: (i + 1).to_string(),
                        val: v,
                    });
                }
            }
            *inner.entries_mut() = entries;
            inner.rebuild_index();
        }

        if let Some(found) = inner.index().map(|idx| idx.get(key).copied()) {
            let rare = inner.rare_mut();
            if let Some(i) = found {
                rare.entries[i].val = val;
                return Ok(());
            }
            let new_idx = rare.entries.len();
            rare.entries.push(Entry {
                key: key.to_string(),
                val,
            });
            rare.index.as_mut().unwrap().insert(key.to_string(), new_idx);
            return Ok(());
        }

        for e in inner.entries_mut() {
            if e.key == key {
                e.val = val;
                return Ok(());
            }
        }
        inner.entries_mut().push(Entry {
            key: key.to_string(),
            val,
        });
        if inner.entries().len() >= 16 {
            inner.rebuild_index();
        }
        Ok(())
    }

    pub fn keys(&self) -> Vec<String> {
        let inner = self.0.borrow();
        if let Some(ref shape) = inner.shape {
            return shape.keys.to_vec();
        }
        if inner.is_list && inner.storage.is_some() {
            if let Some(lk) = inner.list_keys() {
                return lk.to_strings();
            }
            let len = inner.storage.as_ref().unwrap().len();
            return (1..=len).map(|i| i.to_string()).collect();
        }
        inner.entries().iter().map(|e| e.key.clone()).collect()
    }

    pub fn values(&self) -> Vec<Value> {
        let inner = self.0.borrow();
        if let Some(ref storage) = inner.storage {
            return storage.clone();
        }
        inner.entries().iter().map(|e| e.val.clone()).collect()
    }

    pub fn entries(&self) -> Vec<Entry> {
        let inner = self.0.borrow();
        if let Some(ref shape) = inner.shape {
            let storage = inner.storage.as_ref().unwrap();
            return shape
                .keys
                .iter()
                .zip(storage.iter())
                .map(|(k, v)| Entry {
                    key: k.clone(),
                    val: v.clone(),
                })
                .collect();
        }
        if inner.is_list && inner.storage.is_some() {
            let storage = inner.storage.as_ref().unwrap();
            if let Some(lk) = inner.list_keys() {
                return storage
                    .iter()
                    .enumerate()
                    .map(|(i, v)| Entry {
                        key: lk.key(i),
                        val: v.clone(),
                    })
                    .collect();
            }
            return storage
                .iter()
                .enumerate()
                .map(|(i, v)| Entry {
                    key: (i + 1).to_string(),
                    val: v.clone(),
                })
                .collect();
        }
        inner.entries().to_vec()
    }

    /// The children an aggregate walks, as `elements()` defines them, without
    /// rendering a key per child: a scalar is its own single child "1".
    pub fn elems(&self) -> Elems {
        let inner = self.0.borrow();
        if inner.size() == 0 {
            if inner.kind != Kind::None {
                drop(inner);
                return Elems { vals: vec![self.clone()], keys: ElemKeys::Position };
            }
            return Elems { vals: Vec::new(), keys: ElemKeys::Position };
        }
        if let Some(ref storage) = inner.storage {
            let keys = if let Some(ref shape) = inner.shape {
                ElemKeys::Shape(shape.keys.clone())
            } else if let Some(lk) = inner.list_keys() {
                ElemKeys::List(lk.clone())
            } else {
                ElemKeys::Position
            };
            return Elems { vals: storage.clone(), keys };
        }
        Elems {
            vals: inner.entries().iter().map(|e| e.val.clone()).collect(),
            keys: ElemKeys::Owned(inner.entries().iter().map(|e| e.key.clone()).collect()),
        }
    }

    pub fn elements(&self) -> Vec<Entry> {
        let inner = self.0.borrow();
        if inner.size() > 0 {
            drop(inner);
            return self.entries();
        }
        if inner.kind != Kind::None {
            drop(inner);
            return vec![Entry {
                key: "1".to_string(),
                val: self.clone(),
            }];
        }
        Vec::new()
    }

    // Scalar Context
    pub fn scalar_source(&self, pos: Pos) -> Result<Value, SelError> {
        let inner = self.0.borrow();
        if inner.kind != Kind::None {
            drop(inner);
            return Ok(self.clone());
        }
        let mut cur = self.clone();
        let mut guard = 0;
        loop {
            let cur_inner = cur.0.borrow();
            if cur_inner.kind != Kind::None {
                drop(cur_inner);
                return Ok(cur);
            }
            if cur_inner.is_null() {
                return Err(SelError::null("value is NULL", pos));
            }
            if cur_inner.size() == 0 {
                return Err(SelError::new(
                    "E_NO_SCALAR",
                    "value has no scalar and no children",
                    pos,
                ));
            }
            let next = if let Some(ref st) = cur_inner.storage {
                st[0].clone()
            } else {
                cur_inner.entries()[0].val.clone()
            };
            drop(cur_inner);
            cur = next;
            guard += 1;
            if guard > MAX_DEPTH {
                return Err(SelError::depth("scalar context nested too deeply", pos));
            }
        }
    }

    pub fn as_text(&self, pos: Pos) -> Result<String, SelError> {
        let s = self.scalar_source(pos)?;
        let kind = s.kind();
        if kind == Kind::Text {
            return Ok(s.scalar());
        }
        if kind == Kind::Bin {
            return Err(SelError::not_text(
                "expected text, got binary (use FROM_UTF8)",
                pos,
            ));
        }
        Err(SelError::not_text("expected text, got boolean", pos))
    }

    /// `as_text` without copying the text.
    pub(crate) fn as_text_str(&self, pos: Pos) -> Result<SelStr, SelError> {
        let s = self.scalar_source(pos)?;
        let kind = s.kind();
        if kind == Kind::Text {
            return Ok(s.scalar_str());
        }
        if kind == Kind::Bin {
            return Err(SelError::not_text(
                "expected text, got binary (use FROM_UTF8)",
                pos,
            ));
        }
        Err(SelError::not_text("expected text, got boolean", pos))
    }

    pub fn as_bytes(&self, pos: Pos) -> Result<Vec<u8>, SelError> {
        let s = self.scalar_source(pos)?;
        let kind = s.kind();
        if kind == Kind::Bin {
            return Ok(s.0.borrow().bin().to_vec());
        }
        if kind == Kind::Text {
            return Ok(s.scalar_str().as_bytes().to_vec());
        }
        Err(SelError::not_bin(
            "expected binary or text, got boolean",
            pos,
        ))
    }

    pub fn as_bool(&self, pos: Pos) -> Result<bool, SelError> {
        let s = self.scalar_source(pos)?;
        if s.kind() == Kind::Bool {
            return Ok(s.0.borrow().bool_val);
        }
        Err(SelError::not_bool(
            "expected a boolean \u{2014} SEL has no truthiness",
            pos,
        ))
    }

    pub fn as_decimal(&self, pos: Pos) -> Result<Dec, SelError> {
        let s = self.scalar_source(pos)?;
        if s.kind() != Kind::Text {
            return Err(SelError::not_num(
                format!("expected a number, got {}", s.kind().as_str().to_lowercase()),
                pos,
            ));
        }
        if let Some(ref d) = s.0.borrow().dec_val {
            return Ok(d.clone());
        }
        let str_val = s.scalar_str();
        match dec_parse(&str_val, pos) {
            Ok(d) => {
                s.0.borrow_mut().dec_val = Some(d.clone());
                Ok(d)
            }
            Err(e) => {
                if e.code == "E_RANGE" {
                    return Err(e);
                }
                Err(SelError::not_num(
                    format!("not a number: {:?}", str_val),
                    pos,
                ))
            }
        }
    }

    pub fn looks_numeric(&self) -> bool {
        let inner = self.0.borrow();
        if inner.kind == Kind::None && inner.size() == 0 {
            return false;
        }
        drop(inner);
        let s = match self.scalar_source(Pos::default()) {
            Ok(s) => s,
            Err(_) => return false,
        };
        if s.kind() != Kind::Text {
            return false;
        }
        if s.0.borrow().dec_val.is_some() {
            return true;
        }
        let str_val = s.scalar_str();
        if let Ok(d) = dec_parse(&str_val, Pos::default()) {
            s.0.borrow_mut().dec_val = Some(d);
            return true;
        }
        false
    }

    // Cloning
    /// Check the same depth boundary as a copy when an internal, unobservable
    /// collector copy has been elided by the physical optimizer.
    pub fn check_copy_depth(&self, depth: usize, pos: Pos) -> Result<(), SelError> {
        if depth > MAX_DEPTH { return Err(SelError::depth("value nested too deeply", pos)); }
        let inner = self.0.borrow();
        if let Some(ref storage) = inner.storage {
            for child in storage { child.check_copy_depth(depth + 1, pos)?; }
        } else {
            for entry in inner.entries() { entry.val.check_copy_depth(depth + 1, pos)?; }
        }
        Ok(())
    }

    pub fn deep_copy(&self, depth: usize, pos: Pos) -> Result<Value, SelError> {
        if depth > MAX_DEPTH {
            return Err(SelError::depth("value nested too deeply", pos));
        }
        let inner = self.0.borrow();
        let mut out = ValueInner {
            kind: inner.kind,
            bool_val: inner.bool_val,
            is_list: inner.is_list,
            str_val: inner.str_val.clone(),
            dec_val: inner.dec_val.clone(),
            shape: inner.shape.clone(),
            storage: None,
            ext: None,
        };
        if !inner.bin().is_empty() || inner.list_keys().is_some() {
            let rare = out.rare_mut();
            rare.bin_val = inner.bin().to_vec();
            rare.list_keys = inner.list_keys().cloned();
        }
        if let Some(ref storage) = inner.storage {
            let mut new_storage = Vec::with_capacity(storage.len());
            for child in storage {
                new_storage.push(child.deep_copy(depth + 1, pos)?);
            }
            out.storage = Some(new_storage);
        } else if !inner.entries().is_empty() {
            let mut new_entries = Vec::with_capacity(inner.entries().len());
            for e in inner.entries() {
                new_entries.push(Entry {
                    key: e.key.clone(),
                    val: e.val.deep_copy(depth + 1, pos)?,
                });
            }
            let indexed = inner.index().is_some();
            *out.entries_mut() = new_entries;
            if indexed {
                out.rebuild_index();
            }
        }
        Ok(Value(Rc::new(RefCell::new(out))))
    }

    // Equality
    pub fn eql(&self, other: &Value, depth: usize, pos: Pos) -> Result<bool, SelError> {
        if depth > MAX_DEPTH {
            return Err(SelError::depth("value nested too deeply", pos));
        }
        let a = self.0.borrow();
        let b = other.0.borrow();
        if a.kind != b.kind {
            return Ok(false);
        }
        match a.kind {
            Kind::Text => {
                if a.str_val.is_empty()
                    && b.str_val.is_empty()
                    && a.dec_val.is_some()
                    && b.dec_val.is_some()
                {
                    let d1 = a.dec_val.as_ref().unwrap();
                    let d2 = b.dec_val.as_ref().unwrap();
                    if d1.neg != d2.neg || d1.scale != d2.scale || !dec_cmp(d1, d2).is_eq() {
                        return Ok(false);
                    }
                } else {
                    if a.text_cow() != b.text_cow() {
                        return Ok(false);
                    }
                }
            }
            Kind::Bin => {
                if a.bin() != b.bin() {
                    return Ok(false);
                }
            }
            Kind::Bool => {
                if a.bool_val != b.bool_val {
                    return Ok(false);
                }
            }
            Kind::None => {}
        }
        if a.size() != b.size() {
            return Ok(false);
        }
        if a.size() == 0 {
            return Ok(true);
        }
        if a.is_list
            && b.is_list
            && a.storage.is_some()
            && b.storage.is_some()
            && a.list_keys().is_none()
            && b.list_keys().is_none()
        {
            let st1 = a.storage.as_ref().unwrap().clone();
            let st2 = b.storage.as_ref().unwrap().clone();
            drop(a);
            drop(b);
            for i in 0..st1.len() {
                if !st1[i].eql(&st2[i], depth + 1, pos)? {
                    return Ok(false);
                }
            }
            return Ok(true);
        }
        drop(a);
        drop(b);
        let ae = self.entries();
        let be = other.entries();
        for i in 0..ae.len() {
            if ae[i].key != be[i].key {
                return Ok(false);
            }
            if !ae[i].val.eql(&be[i].val, depth + 1, pos)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    // Dump
    pub fn dump(&self) -> Result<String, SelError> {
        self.dump_at(1)
    }

    pub fn dump_at(&self, depth: usize) -> Result<String, SelError> {
        if depth > MAX_DEPTH {
            return Err(SelError::depth("value nested too deeply", Pos::default()));
        }
        let inner = self.0.borrow();
        let mut s = match inner.kind {
            Kind::None => "-".to_string(),
            Kind::Text => {
                format!("t{}", quote_dump(&inner.text_cow()))
            }
            Kind::Bin => {
                let mut hex_str = String::with_capacity(inner.bin().len() * 2);
                for &b in inner.bin() {
                    use std::fmt::Write;
                    let _ = write!(hex_str, "{:02x}", b);
                }
                format!("b{}", hex_str)
            }
            Kind::Bool => {
                if inner.bool_val {
                    "TRUE".to_string()
                } else {
                    "FALSE".to_string()
                }
            }
        };
        if inner.size() == 0 {
            return Ok(s);
        }
        drop(inner);
        let entries = self.entries();
        s.push('{');
        for (i, e) in entries.iter().enumerate() {
            if i > 0 {
                s.push_str(", ");
            }
            s.push_str(&quote_dump(&e.key));
            s.push('=');
            s.push_str(&e.val.dump_at(depth + 1)?);
        }
        s.push('}');
        Ok(s)
    }

    // Structural Hash
    pub fn structural_hash(&self) -> Result<u64, SelError> {
        self.structural_hash_at(1)
    }

    pub fn structural_hash_at(&self, depth: usize) -> Result<u64, SelError> {
        if depth > MAX_DEPTH {
            return Err(SelError::depth("value nested too deeply", Pos::default()));
        }
        let inner = self.0.borrow();
        let mut h = match inner.kind {
            Kind::Text => {
                fnv_hash(&inner.text_cow()) ^ 1000003
            }
            Kind::Bool => {
                if inner.bool_val {
                    12345
                } else {
                    67890
                }
            }
            Kind::Bin => fnv_hash_bytes(inner.bin()) ^ 2000003,
            Kind::None => 0,
        };
        if inner.size() == 0 {
            return Ok(h);
        }
        drop(inner);
        for e in self.entries() {
            let kh = fnv_hash(&e.key);
            let ch = e.val.structural_hash_at(depth + 1)?;
            h = (h.wrapping_mul(1000003)) ^ kh ^ ch;
        }
        Ok(h)
    }
}

pub fn quote_dump(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            _ => {
                let cp = ch as u32;
                if cp < 0x20 {
                    use std::fmt::Write;
                    let _ = write!(out, "\\u{:04x}", cp);
                } else {
                    out.push(ch);
                }
            }
        }
    }
    out.push('"');
    out
}

pub fn fnv_hash(s: &str) -> u64 {
    fnv_hash_bytes(s.as_bytes())
}

pub fn fnv_hash_bytes(b: &[u8]) -> u64 {
    let mut h: u64 = 14695981039346656037;
    for &byte in b {
        h ^= byte as u64;
        h = h.wrapping_mul(1099511628211);
    }
    h
}
