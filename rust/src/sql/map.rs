use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock, OnceLock, RwLock};
use regex::Regex;

use crate::builtins::host_arity;
use crate::utf8::Pos;
use crate::sql::emit::Emit;
use crate::sql::errors::{refuse, SqlError};
use crate::sql::map_data::{SHIPPED_DIALECTS_JSON, SHIPPED_RULES_JSON};
use crate::sql::types::Fragment;

pub const SECTIONS: &[&str] = &["ops", "funcs", "skel"];

pub type BuilderFn = Arc<dyn Fn(&Emit, &[Fragment], Pos) -> Result<Fragment, SqlError> + Send + Sync>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EntryKind {
    Refusal,
    Template,
    Builder,
}

#[derive(Clone, Debug, PartialEq)]
pub enum TemplateValue {
    Single(String),
    Map(HashMap<String, String>),
}

#[derive(Clone)]
pub struct EntryRecord {
    pub key: String,
    pub kind: EntryKind,
    pub reason: String,
    pub tpl: Option<TemplateValue>,
    pub variants: HashMap<String, String>,
    pub ret: String,
    pub caveat: String,
    pub since: String,
    pub arity: Option<[usize; 2]>,
    pub args: Vec<String>,
    pub builder: Option<BuilderFn>,
}

impl std::fmt::Debug for EntryRecord {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EntryRecord")
            .field("key", &self.key)
            .field("kind", &self.kind)
            .field("reason", &self.reason)
            .field("tpl", &self.tpl)
            .field("variants", &self.variants)
            .field("ret", &self.ret)
            .field("caveat", &self.caveat)
            .field("since", &self.since)
            .field("arity", &self.arity)
            .field("args", &self.args)
            .field("has_builder", &self.builder.is_some())
            .finish()
    }
}

impl PartialEq for EntryRecord {
    fn eq(&self, other: &Self) -> bool {
        self.key == other.key
            && self.kind == other.kind
            && self.reason == other.reason
            && self.tpl == other.tpl
            && self.variants == other.variants
            && self.ret == other.ret
            && self.caveat == other.caveat
            && self.since == other.since
            && self.arity == other.arity
            && self.args == other.args
            && self.builder.is_some() == other.builder.is_some()
    }
}

#[derive(Clone, Debug)]
pub struct DialectRecord {
    pub name: String,
    pub extends: Option<String>,
    pub version: String,
    pub target: bool,
    pub lexical: HashMap<String, serde_json::Value>,
    pub ops: HashMap<String, EntryRecord>,
    pub funcs: HashMap<String, EntryRecord>,
    pub skel: HashMap<String, EntryRecord>,
}

#[derive(Clone, Debug, Default)]
pub struct RulesData {
    pub op_arity: HashMap<String, [usize; 2]>,
    pub func_arity: HashMap<String, [Option<usize>; 2]>,
    pub skel_slots: HashMap<String, Vec<String>>,
    pub variants: HashMap<String, Vec<String>>,
    pub caveats: Vec<String>,
    pub ret_kinds: Vec<String>,
    pub arg_kinds: Vec<String>,
    pub lexical_types: HashMap<String, String>,
    pub template_keys: Vec<String>,
}

/// The shipped map and its rules, parsed once (both from the one pair of
/// JSON blobs, so one initialiser).
static SHIPPED: OnceLock<(HashMap<String, DialectRecord>, RulesData)> = OnceLock::new();

static DOTTED_VERSION: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[0-9]+(\.[0-9]+)*$").unwrap());
static TEMPLATE_SLOT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{([^}]*)\}").unwrap());
static UNIFY_RET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^@unify:[0-9]+(,[0-9]+)*$").unwrap());
static ARITY_TEMPLATE_KEY: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(0|[1-9][0-9]{0,2})$").unwrap());

static EXTRA: OnceLock<RwLock<HashMap<String, DialectRecord>>> = OnceLock::new();
static OVERLAY: OnceLock<RwLock<HashMap<String, HashMap<String, HashMap<String, EntryRecord>>>>> = OnceLock::new();
static GUARD_CHECKED: OnceLock<RwLock<HashSet<String>>> = OnceLock::new();
static HOST_ARITIES: OnceLock<RwLock<HashMap<String, HashMap<String, [usize; 2]>>>> = OnceLock::new();

fn extra_store() -> &'static RwLock<HashMap<String, DialectRecord>> {
    EXTRA.get_or_init(|| RwLock::new(HashMap::new()))
}

fn overlay_store() -> &'static RwLock<HashMap<String, HashMap<String, HashMap<String, EntryRecord>>>> {
    OVERLAY.get_or_init(|| RwLock::new(HashMap::new()))
}

fn guard_checked_store() -> &'static RwLock<HashSet<String>> {
    GUARD_CHECKED.get_or_init(|| RwLock::new(HashSet::new()))
}

fn host_arities_store() -> &'static RwLock<HashMap<String, HashMap<String, [usize; 2]>>> {
    HOST_ARITIES.get_or_init(|| RwLock::new(HashMap::new()))
}

fn init_shipped() -> (HashMap<String, DialectRecord>, RulesData) {
    let raw_dialects: HashMap<String, serde_json::Value> =
        serde_json::from_str(SHIPPED_DIALECTS_JSON).expect("failed to parse ShippedDialectsJSON");

    let raw_rules: serde_json::Value =
        serde_json::from_str(SHIPPED_RULES_JSON).expect("failed to parse ShippedRulesJSON");

    let mut rules = RulesData::default();
    if let Some(op_arity) = raw_rules.get("opArity").and_then(|v| v.as_object()) {
        for (k, arr) in op_arity {
            if let Some(a) = arr.as_array() {
                if a.len() == 2 {
                    let lo = a[0].as_u64().unwrap_or(0) as usize;
                    let hi = a[1].as_u64().unwrap_or(0) as usize;
                    rules.op_arity.insert(k.clone(), [lo, hi]);
                }
            }
        }
    }

    if let Some(func_arity) = raw_rules.get("funcArity").and_then(|v| v.as_object()) {
        for (k, arr) in func_arity {
            if let Some(a) = arr.as_array() {
                let lo = a.first().and_then(|v| v.as_u64()).map(|n| n as usize);
                let hi = a.get(1).and_then(|v| v.as_u64()).map(|n| n as usize);
                rules.func_arity.insert(k.clone(), [lo, hi]);
            }
        }
    }

    if let Some(skel_slots) = raw_rules.get("skelSlots").and_then(|v| v.as_object()) {
        for (k, arr) in skel_slots {
            if let Some(a) = arr.as_array() {
                rules.skel_slots.insert(
                    k.clone(),
                    a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
                );
            }
        }
    }

    if let Some(variants) = raw_rules.get("variants").and_then(|v| v.as_object()) {
        for (k, arr) in variants {
            if let Some(a) = arr.as_array() {
                rules.variants.insert(
                    k.clone(),
                    a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
                );
            }
        }
    }

    if let Some(caveats) = raw_rules.get("caveats").and_then(|v| v.as_array()) {
        rules.caveats = caveats.iter().filter_map(|v| v.as_str().map(String::from)).collect();
    }
    if let Some(ret_kinds) = raw_rules.get("retKinds").and_then(|v| v.as_array()) {
        rules.ret_kinds = ret_kinds.iter().filter_map(|v| v.as_str().map(String::from)).collect();
    }
    if let Some(arg_kinds) = raw_rules.get("argKinds").and_then(|v| v.as_array()) {
        rules.arg_kinds = arg_kinds.iter().filter_map(|v| v.as_str().map(String::from)).collect();
    }
    if let Some(lex_types) = raw_rules.get("lexicalTypes").and_then(|v| v.as_object()) {
        for (k, v) in lex_types {
            if let Some(s) = v.as_str() {
                rules.lexical_types.insert(k.clone(), s.to_string());
            }
        }
    }
    if let Some(template_keys) = raw_rules.get("templateKeys").and_then(|v| v.as_array()) {
        rules.template_keys = template_keys.iter().filter_map(|v| v.as_str().map(String::from)).collect();
    }

    let mut dialects = HashMap::new();
    for (name, val) in raw_dialects {
        let d = val.as_object().unwrap();
        let dialect_name = d.get("dialect").and_then(|v| v.as_str()).unwrap_or(&name).to_string();
        let extends = d.get("extends").and_then(|v| v.as_str()).map(String::from);
        let version = d.get("version").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let target = d.get("target").and_then(|v| v.as_bool()).unwrap_or(true);

        let mut lexical = HashMap::new();
        if let Some(lex) = d.get("lexical").and_then(|v| v.as_object()) {
            for (lk, lv) in lex {
                lexical.insert(lk.clone(), lv.clone());
            }
        }

        let ops = parse_section_entries(d.get("ops"));
        let funcs = parse_section_entries(d.get("funcs"));
        let skel = parse_section_entries(d.get("skel"));

        dialects.insert(
            dialect_name.clone(),
            DialectRecord {
                name: dialect_name,
                extends,
                version,
                target,
                lexical,
                ops,
                funcs,
                skel,
            },
        );
    }

    (dialects, rules)
}

fn ensure_init() -> (&'static HashMap<String, DialectRecord>, &'static RulesData) {
    let (d, r) = SHIPPED.get_or_init(init_shipped);
    (d, r)
}

fn parse_section_entries(val: Option<&serde_json::Value>) -> HashMap<String, EntryRecord> {
    let mut out = HashMap::new();
    if let Some(obj) = val.and_then(|v| v.as_object()) {
        for (k, v) in obj {
            out.insert(k.clone(), to_entry_record(k, v));
        }
    }
    out
}

pub fn to_entry_record(key: &str, v: &serde_json::Value) -> EntryRecord {
    if v.is_null() {
        return EntryRecord {
            key: key.to_string(),
            kind: EntryKind::Refusal,
            reason: String::new(),
            tpl: None,
            variants: HashMap::new(),
            ret: String::new(),
            caveat: String::new(),
            since: String::new(),
            arity: None,
            args: Vec::new(),
            builder: None,
        };
    }
    if let Some(s) = v.as_str() {
        return EntryRecord {
            key: key.to_string(),
            kind: EntryKind::Refusal,
            reason: s.to_string(),
            tpl: None,
            variants: HashMap::new(),
            ret: String::new(),
            caveat: String::new(),
            since: String::new(),
            arity: None,
            args: Vec::new(),
            builder: None,
        };
    }
    if let Some(m) = v.as_object() {
        let mut rec = EntryRecord {
            key: key.to_string(),
            kind: EntryKind::Template,
            reason: String::new(),
            tpl: None,
            variants: HashMap::new(),
            ret: String::new(),
            caveat: String::new(),
            since: String::new(),
            arity: None,
            args: Vec::new(),
            builder: None,
        };
        if let Some(tpl_val) = m.get("tpl") {
            if let Some(s) = tpl_val.as_str() {
                rec.tpl = Some(TemplateValue::Single(s.to_string()));
            } else if let Some(tm) = tpl_val.as_object() {
                let mut map = HashMap::new();
                for (tk, tv) in tm {
                    map.insert(tk.clone(), tv.as_str().unwrap_or("").to_string());
                }
                rec.tpl = Some(TemplateValue::Map(map));
            } else if let Some(arr) = tpl_val.as_array() {
                let mut map = HashMap::new();
                for (idx, tv) in arr.iter().enumerate() {
                    map.insert(idx.to_string(), tv.as_str().unwrap_or("").to_string());
                }
                rec.tpl = Some(TemplateValue::Map(map));
            }
        }
        if let Some(vs) = m.get("variants").and_then(|v| v.as_object()) {
            for (vk, vv) in vs {
                rec.variants.insert(vk.clone(), vv.as_str().unwrap_or("").to_string());
            }
        }
        if let Some(r) = m.get("ret").and_then(|v| v.as_str()) {
            rec.ret = r.to_string();
        }
        if let Some(c) = m.get("caveat").and_then(|v| v.as_str()) {
            rec.caveat = c.to_string();
        }
        if let Some(s) = m.get("since").and_then(|v| v.as_str()) {
            rec.since = s.to_string();
        }
        if let Some(a) = m.get("arity").and_then(|v| v.as_array()) {
            if a.len() == 2 {
                let lo = a[0].as_u64().unwrap_or(0) as usize;
                let hi = a[1].as_u64().unwrap_or(0) as usize;
                rec.arity = Some([lo, hi]);
            }
        }
        if let Some(args) = m.get("args").and_then(|v| v.as_array()) {
            for arg in args {
                if let Some(s) = arg.as_str() {
                    rec.args.push(s.to_string());
                }
            }
        }
        return rec;
    }

    EntryRecord {
        key: key.to_string(),
        kind: EntryKind::Refusal,
        reason: String::new(),
        tpl: None,
        variants: HashMap::new(),
        ret: String::new(),
        caveat: String::new(),
        since: String::new(),
        arity: None,
        args: Vec::new(),
        builder: None,
    }
}

pub fn reset() {
    ensure_init();
    extra_store().write().unwrap().clear();
    overlay_store().write().unwrap().clear();
    guard_checked_store().write().unwrap().clear();
    host_arities_store().write().unwrap().clear();
}

pub fn exists(dialect: &str) -> bool {
    let (shipped, _) = ensure_init();
    extra_store().read().unwrap().contains_key(dialect) || shipped.contains_key(dialect)
}

fn get_record(dialect: &str) -> Option<DialectRecord> {
    if let Some(r) = extra_store().read().unwrap().get(dialect) {
        return Some(r.clone());
    }
    let (shipped, _) = ensure_init();
    shipped.get(dialect).cloned()
}

// Read only the requested metadata. Cloning a whole dialect for every parent
// lookup copied all templates once per rendered operator.
fn with_record<T>(dialect: &str, read: impl FnOnce(&DialectRecord) -> T) -> Option<T> {
    let extra = extra_store().read().unwrap();
    if let Some(record) = extra.get(dialect) {
        return Some(read(record));
    }
    let (shipped, _) = ensure_init();
    shipped.get(dialect).map(read)
}

pub fn shipped_dialect_names() -> Vec<String> {
    let (shipped, _) = ensure_init();
    let mut names: Vec<String> = shipped.keys().cloned().collect();
    names.sort();
    names
}

pub fn shipped_lexical_keys(dialect: &str) -> Vec<String> {
    let (shipped, _) = ensure_init();
    let mut keys = Vec::new();
    if let Some(d) = shipped.get(dialect) {
        for k in d.lexical.keys() {
            keys.push(k.clone());
        }
    }
    keys.sort();
    keys
}

pub fn shipped_section_keys(dialect: &str, section: &str) -> Vec<String> {
    let (shipped, _) = ensure_init();
    let mut keys = Vec::new();
    if let Some(d) = shipped.get(dialect) {
        let sec = match section {
            "ops" => &d.ops,
            "funcs" => &d.funcs,
            "skel" => &d.skel,
            _ => return keys,
        };
        for k in sec.keys() {
            keys.push(k.clone());
        }
    }
    keys.sort();
    keys
}

pub fn targets() -> Vec<String> {
    let (shipped, _) = ensure_init();
    let mut set = HashSet::new();
    for (d, r) in shipped {
        if r.target {
            set.insert(d.clone());
        }
    }
    for (d, r) in extra_store().read().unwrap().iter() {
        if r.target {
            set.insert(d.clone());
        }
    }
    let mut out: Vec<String> = set.into_iter().collect();
    out.sort();
    out
}

pub fn require_target(dialect: &str, pos: Pos) -> Result<(), SqlError> {
    if !exists(dialect) {
        return refuse(
            "E_SQL_DIALECT",
            format!("there is no SQL dialect {}; known targets are {}", dialect, targets().join(", ")),
            pos,
        );
    }
    let rec = get_record(dialect).unwrap();
    if !rec.target {
        return refuse(
            "E_SQL_DIALECT",
            format!(
                "{} is a base other dialects inherit from, not a server anyone runs; translate to one of {}",
                dialect,
                targets().join(", ")
            ),
            pos,
        );
    }
    Ok(())
}

pub fn chain(dialect: &str) -> Vec<String> {
    ensure_init();
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    let mut cur = dialect.to_string();

    while !cur.is_empty() && exists(&cur) && !seen.contains(&cur) {
        seen.insert(cur.clone());
        out.push(cur.clone());
        match with_record(&cur, |r| r.extends.clone()).flatten() {
            Some(ext) => cur = ext,
            None => break,
        }
    }
    out
}

pub fn version(dialect: &str) -> String {
    with_record(dialect, |r| r.version.clone()).unwrap_or_default()
}

pub fn lexical(dialect: &str, key: &str) -> Option<serde_json::Value> {
    let ch = chain(dialect);
    for d in ch {
        if let Some(v) = with_record(&d, |r| r.lexical.get(key).cloned()).flatten() {
            return Some(v);
        }
    }
    None
}

pub fn entry(dialect: &str, section: &str, key: &str) -> Option<EntryRecord> {
    check_section(section);
    let ch = chain(dialect);

    let overlay = overlay_store().read().unwrap();
    for d in &ch {
        if let Some(sec_map) = overlay.get(d) {
            if let Some(by_sec) = sec_map.get(section) {
                if let Some(e) = by_sec.get(key) {
                    return Some(e.clone());
                }
            }
        }
    }
    drop(overlay);

    let (shipped, _) = ensure_init();
    for d in ch {
        if let Some(d_rec) = shipped.get(&d) {
            let sec_map = match section {
                "ops" => &d_rec.ops,
                "funcs" => &d_rec.funcs,
                "skel" => &d_rec.skel,
                _ => return None,
            };
            if let Some(e) = sec_map.get(key) {
                return Some(e.clone());
            }
        }
    }

    None
}

pub fn define_dialect(name: &str, spec: &serde_json::Value) {
    let (shipped, _) = ensure_init();
    if shipped.contains_key(name) {
        panic!("SQL dialect {} is already defined; a name means one dialect", name);
    }

    let spec_obj = spec.as_object().unwrap_or_else(|| panic!("spec must be an object"));
    let mut unknown = Vec::new();
    let allowed: HashMap<&str, bool> = [
        ("extends", true),
        ("version", true),
        ("target", true),
        ("lexical", true),
    ]
    .into_iter()
    .collect();

    for k in spec_obj.keys() {
        if !allowed.contains_key(k.as_str()) {
            unknown.push(k.clone());
        }
    }
    if !unknown.is_empty() {
        unknown.sort();
        panic!(
            "SQL dialect {} declares {}, which a dialect declaration does not carry; ops, funcs and skel entries are defined one at a time with define()",
            name,
            unknown.join(", ")
        );
    }

    if !spec_obj.contains_key("extends") {
        panic!(
            "SQL dialect {} must say what it extends; write extends: null for a dialect with no parent, as ansi has",
            name
        );
    }

    let ext = match spec_obj.get("extends") {
        Some(v) if v.is_null() => None,
        Some(v) if v.is_string() => {
            let s = v.as_str().unwrap().to_string();
            if !exists(&s) {
                panic!("SQL dialect {} extends {}, which does not exist", name, s);
            }
            Some(s)
        }
        _ => panic!("SQL dialect {} extends must be a string or null", name),
    };

    let old_parent = with_record(name, |r| r.extends.clone());
    if old_parent.is_some_and(|parent| parent != ext) {
        panic!("SQL dialect {} cannot change its parent", name);
    }

    let version = match &ext {
        None => {
            let v_str = match spec_obj.get("version") {
                Some(v) if v.is_string() => v.as_str().unwrap().to_string(),
                Some(v) => panic!(
                    "SQL dialect {} has version {}, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)",
                    name, v
                ),
                None => panic!(
                    "SQL dialect {} extends nothing, so it must declare a version; there is none to inherit",
                    name
                ),
            };
            v_str
        }
        Some(ext_name) => {
            if let Some(v) = spec_obj.get("version") {
                if let Some(s) = v.as_str() {
                    s.to_string()
                } else {
                    panic!(
                        "SQL dialect {} has version {}, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)",
                        name, v
                    );
                }
            } else {
                get_record(ext_name).unwrap().version
            }
        }
    };
    if !DOTTED_VERSION.is_match(&version) {
        panic!(
            "SQL dialect {} has version {:?}, which is not dotted-numeric; strip any suffix a server reports (11.8.8-MariaDB is 11.8.8)",
            name, version
        );
    }

    let target = match spec_obj.get("target") {
        Some(v) => match v.as_bool() {
            Some(b) => b,
            None => panic!(
                "SQL dialect {} has a target that is not a boolean; truthiness differs between hosts and must not decide this",
                name
            ),
        },
        None => true,
    };

    let mut lexical = HashMap::new();
    if let Some(lex) = spec_obj.get("lexical") {
        if let Some(obj) = lex.as_object() {
            for (k, v) in obj {
                check_lexical(k, v, &format!("SQL dialect {}", name));
                lexical.insert(k.clone(), v.clone());
            }
        } else {
            panic!("SQL dialect {} has a lexical that is not a map", name);
        }
    }

    let effective = |key: &str| lexical.get(key).cloned().or_else(|| ext.as_deref().and_then(|parent| self::lexical(parent, key)));
    // The quote and the escape that goes with it (sql/MAP.md §3.1): a text literal is
    // the one place a value becomes SQL, so a pairing that does not neutralise the
    // quote turns every text value into an injection.
    let fail = |why: &str| -> ! { panic!("SQL dialect {}: {} (sql/MAP.md §3.1)", name, why) };
    let quote = effective("textQuote");
    let quote = match quote.as_ref().and_then(|v| v.as_str()) {
        Some(q) if q.chars().count() == 1 => q.to_string(),
        _ => fail("textQuote must be exactly one character"),
    };
    let ident = effective("identQuote");
    if ident.as_ref().and_then(|v| v.as_str()) == Some(quote.as_str()) {
        fail("textQuote must differ from identQuote");
    }
    let escapes = effective("textEscape");
    let escapes = match escapes.as_ref().and_then(|v| v.as_object()) {
        Some(m) if !m.is_empty() => m.clone(),
        _ => fail("textEscape must be a non-empty map that escapes the quote"),
    };
    if escapes.keys().any(|k| k.is_empty()) {
        fail("textEscape has an empty key");
    }
    let replacement = escapes.get(&quote).and_then(|v| v.as_str());
    let doubled = format!("{}{}", quote, quote);
    let backslashed = format!("\\{}", quote);
    if replacement != Some(doubled.as_str()) && replacement != Some(backslashed.as_str()) {
        fail("textEscape must escape the quote, by doubling it or with a backslash");
    }
    let uses_backslash = escapes.values().any(|v| v.as_str().is_some_and(|v| v.starts_with('\\')));
    if uses_backslash && escapes.get("\\").and_then(|v| v.as_str()) != Some("\\\\") {
        fail("textEscape uses a backslash escape but does not double the backslash itself");
    }

    extra_store().write().unwrap().insert(
        name.to_string(),
        DialectRecord {
            name: name.to_string(),
            extends: ext,
            version,
            target,
            lexical,
            ops: HashMap::new(),
            funcs: HashMap::new(),
            skel: HashMap::new(),
        },
    );
    // A replacement can change an inherited guard for any descendant.
    guard_checked_store().write().unwrap().clear();
}

pub fn define(dialect: &str, section: &str, key: &str, entry_val: &serde_json::Value) {
    check_section(section);
    if !exists(dialect) {
        panic!("SQL dialect {} does not exist", dialect);
    }
    check_key(section, key);
    check_entry(section, key, entry_val);

    let k = if section == "funcs" {
        key.to_ascii_uppercase()
    } else {
        key.to_string()
    };

    let rec = to_entry_record(&k, entry_val);

    let mut overlay = overlay_store().write().unwrap();
    let d_map = overlay.entry(dialect.to_string()).or_default();
    let sec_map = d_map.entry(section.to_string()).or_default();
    sec_map.insert(k.clone(), rec);
    drop(overlay);

    let arity = if section == "funcs" {
        host_arity(&k).map(|(lo, hi)| [lo, hi])
    } else {
        None
    };

    let mut ha = host_arities_store().write().unwrap();
    if let Some(a) = arity {
        let d_ha = ha.entry(dialect.to_string()).or_default();
        d_ha.insert(k.clone(), a);
    } else if let Some(d_ha) = ha.get_mut(dialect) {
        d_ha.remove(&k);
    }
    drop(ha);

    if section == "funcs" && k == "ISNUM" {
        guard_checked_store().write().unwrap().clear();
    }
}

pub fn define_builder(dialect: &str, section: &str, key: &str, builder: BuilderFn) {
    check_section(section);
    if !exists(dialect) {
        panic!("SQL dialect {} does not exist", dialect);
    }
    check_key(section, key);

    let k = if section == "funcs" {
        key.to_ascii_uppercase()
    } else {
        key.to_string()
    };

    let rec = EntryRecord {
        key: k.clone(),
        kind: EntryKind::Builder,
        reason: String::new(),
        tpl: None,
        variants: HashMap::new(),
        ret: String::new(),
        caveat: String::new(),
        since: String::new(),
        arity: None,
        args: Vec::new(),
        builder: Some(builder),
    };

    let mut overlay = overlay_store().write().unwrap();
    let d_map = overlay.entry(dialect.to_string()).or_default();
    let sec_map = d_map.entry(section.to_string()).or_default();
    sec_map.insert(k, rec);
}

pub fn host_spelling_arity(dialect: &str, key: &str) -> Option<[usize; 2]> {
    let ch = chain(dialect);
    let overlay = overlay_store().read().unwrap();
    let ha = host_arities_store().read().unwrap();
    for d in ch {
        if let Some(sec_map) = overlay.get(&d) {
            if let Some(f_map) = sec_map.get("funcs") {
                if f_map.contains_key(key) {
                    if let Some(d_ha) = ha.get(&d) {
                        if let Some(a) = d_ha.get(key) {
                            return Some(*a);
                        }
                    }
                    return None;
                }
            }
        }
    }
    None
}

fn quoted_runs(tpl: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut start = None;
    for (i, b) in tpl.bytes().enumerate() {
        if b == b'\'' {
            if let Some(s) = start {
                out.push(tpl[s..i].to_string());
                start = None;
            } else {
                start = Some(i + 1);
            }
        }
    }
    out
}

/// Holds a dialect's numericGuard to its funcs.ISNUM, once, at its first use.
/// A disagreement is a mistake in the application's dialect, not a rule that
/// cannot be translated: it panics (the start-up error other hosts raise as a
/// LogicException/Error, `register.guard.*` in 50-rendering-and-
/// registration.sqlt), on every use until fixed, rather than return a
/// SqlError that try_translate would swallow.
pub fn check_numeric_guard(dialect: &str) {
    {
        let gc = guard_checked_store().read().unwrap();
        if gc.contains(dialect) {
            return;
        }
    }

    let guard = match lexical(dialect, "numericGuard") {
        Some(v) => match v.as_str() {
            Some(s) => s.to_string(),
            None => return,
        },
        None => return,
    };

    let isnum = entry(dialect, "funcs", "ISNUM");
    let tpl_str = match isnum {
        Some(rec) => match rec.tpl {
            Some(TemplateValue::Single(s)) => s,
            _ => String::new(),
        },
        None => String::new(),
    };

    if tpl_str.is_empty() {
        panic!(
            "SQL dialect {} declares a numericGuard but maps no funcs.ISNUM with a template for it to agree with; the two ask the same question and sql/MAP.md §7 rule 10 is that one place defines a thing",
            dialect
        );
    }

    let want = quoted_runs(&tpl_str);
    if want.is_empty() {
        panic!(
            "SQL dialect {} maps a funcs.ISNUM that carries no quoted pattern, so its numericGuard has nothing to agree with",
            dialect
        );
    }

    let got_runs = quoted_runs(&guard);
    let mut got_set = HashSet::new();
    for g in got_runs {
        got_set.insert(g);
    }

    let mut missing = Vec::new();
    for w in want {
        if !got_set.contains(&w) {
            missing.push(format!("'{}'", w));
        }
    }
    if !missing.is_empty() {
        panic!(
            "SQL dialect {} declares a numericGuard that does not carry {}, which its funcs.ISNUM tests; they ask the same question, and a guard that asks a different one answers for rows SEL refuses",
            dialect,
            missing.join(", ")
        );
    }
    guard_checked_store().write().unwrap().insert(dialect.to_string());
}

fn check_lexical(key: &str, v: &serde_json::Value, where_str: &str) {
    let (_, rules) = ensure_init();
    let expected_type = match rules.lexical_types.get(key) {
        Some(t) => t,
        None => {
            let mut known: Vec<String> = rules.lexical_types.keys().cloned().collect();
            known.sort();
            panic!(
                "{} sets the unknown lexical key {}; known keys are {}",
                where_str,
                key,
                known.join(", ")
            );
        }
    };
    if v.is_null() {
        return;
    }
    if expected_type == "map" {
        if let Some(m) = v.as_object() {
            for (from, to) in m {
                if from.is_empty() || to.is_null() || !to.is_string() {
                    panic!("{}'s {} maps {:?} to something that is not a string", where_str, key, from);
                }
            }
            return;
        }
        panic!("{} sets {} to a {}; it must be a map of character to replacement", where_str, key, type_name(v));
    }

    if let Some(s) = v.as_str() {
        if s.is_empty() && (key == "identQuote" || key == "textQuote") {
            panic!("{} sets {} to the empty string; a quote character that is not a character cannot quote", where_str, key);
        }
        return;
    }
    panic!("{} sets {} to a {}; it must be a string", where_str, key, type_name(v));
}

fn check_key(section: &str, key: &str) {
    let (_, rules) = ensure_init();
    if section == "ops" {
        if !rules.op_arity.contains_key(key) {
            panic!("{} is not a SEL operator, so an ops entry for it would never be looked up", key);
        }
    } else if section == "funcs" {
        let upper = key.to_ascii_uppercase();
        let is_sel_func = rules.func_arity.contains_key(&upper);
        let is_host_func = host_arity(key).is_some();
        if !is_sel_func && !is_host_func {
            panic!(
                "{} is neither a SEL function this layer maps nor a registered host function. A host function is registered (registerFunction) before it is given a SQL spelling; the aggregates and IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by stage 2 and never reach the funcs table",
                key
            );
        }
    } else if section == "skel"
        && !rules.skel_slots.contains_key(key) {
            let mut known: Vec<String> = rules.skel_slots.keys().cloned().collect();
            known.sort();
            panic!("{} is not a skeleton; known ones are {}", key, known.join(", "));
        }
}

fn check_entry(section: &str, key: &str, e: &serde_json::Value) {
    let where_str = format!("the {} entry for {}", section, key);
    if e.is_null() || e.is_string() {
        return;
    }
    let m = match e.as_object() {
        Some(obj) => obj,
        None => panic!("{} must be a map, a string or null, and is {}", where_str, type_name(e)),
    };

    let host = if section == "funcs" {
        host_arity(key)
    } else {
        None
    };

    if let Some(args_val) = m.get("args") {
        if !args_val.is_null() {
            check_args(key, args_val, host, &where_str);
        }
    }

    if let Some(b_val) = m.get("builder") {
        if !b_val.is_null() {
            return;
        }
    }

    let (_, rules) = ensure_init();

    if section == "skel" {
        let tpl_val = match m.get("tpl") {
            Some(v) if v.is_string() => v.as_str().unwrap(),
            _ => panic!("{} needs a tpl that is a string", where_str),
        };
        let allowed = match rules.skel_slots.get(key) {
            Some(slots) => slots,
            None => panic!("unknown skel: {}", key),
        };
        for cap in TEMPLATE_SLOT.captures_iter(tpl_val) {
            let slot_name = &cap[1];
            if !allowed.iter().any(|a| a == slot_name) {
                panic!(
                    "{} uses the slot {{{}}}; {} has {} — a typo would survive as literal text in every query",
                    where_str,
                    slot_name,
                    key,
                    allowed.join(", ")
                );
            }
        }
        if let Some(cav) = m.get("caveat").and_then(|v| v.as_str()) {
            if !rules.caveats.iter().any(|c| c == cav) {
                panic!("{} declares the caveat {:?}, which is not on the closed list in sql/MAP.md §4.6", where_str, cav);
            }
        }
        return;
    }

    let has_tpl = m.contains_key("tpl") && !m["tpl"].is_null();
    let has_variants = m.contains_key("variants") && !m["variants"].is_null();
    if has_tpl == has_variants {
        panic!("{} needs exactly one of tpl and variants", where_str);
    }

    let ret_val = match m.get("ret").and_then(|v| v.as_str()) {
        Some(s) => s,
        None => panic!("{} has ret null; use one of {}, @concat or @unify:<n>[,<n>...]", where_str, rules.ret_kinds.join(", ")),
    };
    if !rules.ret_kinds.iter().any(|r| r == ret_val) && ret_val != "@concat" && !UNIFY_RET.is_match(ret_val) {
        panic!("{} has ret {:?}; use one of {}, @concat or @unify:<n>[,<n>...]", where_str, ret_val, rules.ret_kinds.join(", "));
    }

    if let Some(cav) = m.get("caveat").and_then(|v| v.as_str()) {
        if !rules.caveats.iter().any(|c| c == cav) {
            panic!(
                "{} declares the caveat {:?}, which is not on the closed list in sql/MAP.md §4.6; a caveat an application cannot branch on is prose",
                where_str, cav
            );
        }
    }
    if let Some(since) = m.get("since").and_then(|v| v.as_str()) {
        if !DOTTED_VERSION.is_match(since) {
            panic!("{} has a since that is not dotted-numeric", where_str);
        }
    }

    let mut entry_arity: Option<[usize; 2]> = None;
    if let Some(arity_val) = m.get("arity") {
        if !arity_val.is_null() {
            if let Some(arr) = arity_val.as_array() {
                if arr.len() == 2 {
                    if let (Some(n1), Some(n2)) = (arr[0].as_u64(), arr[1].as_u64()) {
                        let lo = n1 as usize;
                        let hi = n2 as usize;
                        if hi >= lo {
                            entry_arity = Some([lo, hi]);
                        }
                    }
                }
            }
            if entry_arity.is_none() {
                panic!("{} has an arity that is not [min, max] of two integers", where_str);
            }
        }
    }

    if let (Some(ea), Some(h)) = (entry_arity, host) {
        if ea[0] < h.0 || ea[1] > h.1 {
            panic!(
                "{} has the arity [{}, {}], which is wider than {}'s registered [{}, {}]; an entry may only narrow it",
                where_str, ea[0], ea[1], key, h.0, h.1
            );
        }
    }

    if has_variants {
        let vs_map = match m.get("variants").and_then(|v| v.as_object()) {
            Some(obj) if !obj.is_empty() => obj,
            _ => panic!("{} has variants that are not a map", where_str),
        };
        let allowed = match rules.variants.get(key) {
            Some(v) => v,
            None => panic!("{} uses variants, and {} is not a variant family", where_str, key),
        };
        for name in vs_map.keys() {
            if !allowed.iter().any(|a| a == name) {
                panic!("{} declares the variant {}; {} has {}", where_str, name, key, allowed.join(", "));
            }
        }
    }

    if has_tpl {
        let tpl_val = m.get("tpl").unwrap();
        let mut tpl_map = HashMap::new();
        if let Some(obj) = tpl_val.as_object() {
            for (k, v) in obj {
                tpl_map.insert(k.clone(), v.as_str().unwrap_or("").to_string());
            }
        } else if let Some(arr) = tpl_val.as_array() {
            for (idx, v) in arr.iter().enumerate() {
                tpl_map.insert(idx.to_string(), v.as_str().unwrap_or("").to_string());
            }
        }

        if !tpl_map.is_empty() {
            let (base_lo, base_hi) = if section == "ops" {
                let ar = rules.op_arity[key];
                (ar[0], Some(ar[1]))
            } else if let Some(h) = host {
                (h.0, Some(h.1))
            } else {
                let ar = rules.func_arity[&key.to_ascii_uppercase()];
                (ar[0].unwrap_or(0), ar[1])
            };

            let mut lo = base_lo;
            let mut hi = base_hi;
            if let Some(ea) = entry_arity {
                if ea[0] > lo {
                    lo = ea[0];
                }
                if hi.is_none_or(|h| ea[1] < h) {
                    hi = Some(ea[1]);
                }
            }
            for n in tpl_map.keys() {
                if n == "*" {
                    continue;
                }
                if !ARITY_TEMPLATE_KEY.is_match(n) {
                    panic!("{} keys a template by {:?}; an arity-keyed template uses a count or *", where_str, n);
                }
                let c: usize = n.parse().unwrap();
                if c < lo || hi.is_some_and(|h| c > h) {
                    let hi_str = hi.map(|h| h.to_string()).unwrap_or_else(|| "any".to_string());
                    panic!(
                        "{} keys a template by {}, and {} takes {} to {} argument(s), so that template could never be chosen",
                        where_str, c, key, lo, hi_str
                    );
                }
            }
        }
    }
}

fn check_args(key: &str, args_val: &serde_json::Value, host: Option<(usize, usize)>, where_str: &str) {
    let host = match host {
        Some(h) => h,
        None => panic!("{} declares args, and {} is not a host function: a builtin's argument rules are SEL's own", where_str, key),
    };
    let arr = match args_val.as_array() {
        Some(a) => a,
        None => panic!("{} has args that are not a list of kinds", where_str),
    };

    let mut args = Vec::new();
    for a in arr {
        match a.as_str() {
            Some(s) => args.push(s.to_string()),
            None => panic!("{} has args that are not a list of kinds", where_str),
        }
    }

    let (_, rules) = ensure_init();
    for a in &args {
        if !rules.arg_kinds.iter().any(|k| k == a) {
            panic!("{} declares the argument kind {:?}; use one of {}", where_str, a, rules.arg_kinds.join(", "));
        }
    }
    if args.len() > host.1 {
        panic!("{} declares {} argument kinds, and {} takes at most {}", where_str, args.len(), key, host.1);
    }
}

fn check_section(section: &str) {
    if !SECTIONS.contains(&section) {
        panic!("unknown map section {}; use {}", section, SECTIONS.join(", "));
    }
}

fn type_name(v: &serde_json::Value) -> &'static str {
    match v {
        serde_json::Value::Null => "null",
        serde_json::Value::Bool(_) => "boolean",
        serde_json::Value::Number(_) => "number",
        serde_json::Value::String(_) => "string",
        serde_json::Value::Array(_) => "list",
        serde_json::Value::Object(_) => "map",
    }
}

pub fn version_at_least(have: &str, want: &str) -> bool {
    let parse = |s: &str| -> Vec<usize> {
        s.split('.')
            .map(|p| p.parse::<usize>().unwrap_or(0))
            .collect()
    };
    let a = parse(have);
    let b = parse(want);
    let max_len = a.len().max(b.len());
    for i in 0..max_len {
        let x = *a.get(i).unwrap_or(&0);
        let y = *b.get(i).unwrap_or(&0);
        if x != y {
            return x > y;
        }
    }
    true
}
