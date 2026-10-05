use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

static NEXT_SHAPE_ID: AtomicU64 = AtomicU64::new(1);

const SHAPE_CACHE_ENTRIES: usize = 256;
const SHAPE_CACHE_MAX_KEYS: usize = 256;
const SHAPE_CACHE_MAX_BYTES: usize = 16384;

#[derive(Clone, Debug)]
pub struct RecordShape {
    pub id: u64,
    pub keys: Arc<[String]>,
    pub key_map: HashMap<String, usize>,
    pub size: usize,
}

impl RecordShape {
    pub fn new(keys: &[String]) -> Self {
        let id = NEXT_SHAPE_ID.fetch_add(1, Ordering::Relaxed);
        let mut key_map = HashMap::with_capacity(keys.len());
        for (i, k) in keys.iter().enumerate() {
            key_map.insert(k.clone(), i);
        }
        Self {
            id,
            keys: keys.to_vec().into(),
            key_map,
            size: keys.len(),
        }
    }
}

fn shape_cache() -> &'static Mutex<HashMap<String, Arc<RecordShape>>> {
    static CACHE: OnceLock<Mutex<HashMap<String, Arc<RecordShape>>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

pub fn new_record_shape(keys: &[String]) -> Arc<RecordShape> {
    Arc::new(RecordShape::new(keys))
}

pub fn intern_record_shape(keys: &[String]) -> Arc<RecordShape> {
    let mut signature = String::new();
    for key in keys {
        signature.push_str(&key.len().to_string());
        signature.push(':');
        signature.push_str(key);
    }
    let sig = signature;
    let cache = shape_cache();
    {
        let guard = cache.lock().unwrap();
        if let Some(s) = guard.get(&sig) {
            return s.clone();
        }
    }
    let s = new_record_shape(keys);
    let total_bytes: usize = keys.iter().map(|k| k.len()).sum();
    if keys.len() <= SHAPE_CACHE_MAX_KEYS && total_bytes <= SHAPE_CACHE_MAX_BYTES {
        let mut guard = cache.lock().unwrap();
        if guard.len() >= SHAPE_CACHE_ENTRIES {
            guard.clear();
        }
        guard.insert(sig, s.clone());
    }
    s
}

pub fn unique_record_shape(keys: &[String]) -> Option<Arc<RecordShape>> {
    let mut seen = HashSet::with_capacity(keys.len());
    for k in keys {
        if !seen.insert(k) {
            return None;
        }
    }
    Some(intern_record_shape(keys))
}

pub fn parse_list_slot(key: &str, len: usize) -> Option<usize> {
    crate::utf8::canonical_index(key, 9).filter(|&val| val <= len).map(|val| val - 1)
}
