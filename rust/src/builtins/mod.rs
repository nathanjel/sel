use std::collections::HashMap;
use std::sync::{Arc, OnceLock, RwLock};

use crate::args::Args;
use crate::manifest::lookup_builtin;
use crate::utf8::SelError;
use crate::value::Value;

pub mod binary;
pub mod core;
pub mod math;
pub mod regex_ops;
pub mod structure;
pub mod text;

pub type BuiltinFn = fn(&mut Args) -> Result<Value, SelError>;

#[derive(Clone)]
pub enum SpecFn {
    Native(BuiltinFn),
    Host(Arc<dyn Fn(&mut Args) -> Result<Value, SelError> + Send + Sync>),
}

#[derive(Clone)]
pub struct Spec {
    pub name: String,
    pub min: usize,
    pub max: Option<usize>,
    pub lazy: bool,
    pub binds: bool,
    pub func: SpecFn,
}

impl std::fmt::Debug for Spec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Spec")
            .field("name", &self.name)
            .field("min", &self.min)
            .field("max", &self.max)
            .field("lazy", &self.lazy)
            .field("binds", &self.binds)
            .finish()
    }
}

impl Spec {
    pub fn call(&self, args: &mut Args) -> Result<Value, SelError> {
        match self.func {
            SpecFn::Native(f) => f(args),
            SpecFn::Host(ref f) => f(args),
        }
    }
}

fn register_native(m: &mut HashMap<String, Arc<Spec>>, name: &'static str, f: BuiltinFn) {
    let entry = lookup_builtin(name).unwrap_or_else(|| panic!("builtin {} not in manifest", name));
    m.insert(
        name.to_string(),
        Arc::new(Spec {
            name: name.to_string(),
            min: entry.min,
            max: entry.max,
            lazy: entry.lazy,
            binds: entry.binds,
            func: SpecFn::Native(f),
        }),
    );
}

fn registry() -> &'static RwLock<HashMap<String, Arc<Spec>>> {
    static REGISTRY: OnceLock<RwLock<HashMap<String, Arc<Spec>>>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let mut m = HashMap::with_capacity(64);

        // Core
        register_native(&mut m, "IF", core::fn_if);
        register_native(&mut m, "COND", core::fn_cond);
        register_native(&mut m, "ABORT", core::fn_abort);
        register_native(&mut m, "IS_NULL", core::fn_is_null);
        register_native(&mut m, "IS_NOT_NULL", core::fn_is_not_null);
        register_native(&mut m, "COALESCE", core::fn_coalesce);
        register_native(&mut m, "GET", core::fn_get);
        register_native(&mut m, "PATH", core::fn_path);
        register_native(&mut m, "IS_BLANK", core::fn_is_blank);
        register_native(&mut m, "IS_PRESENT", core::fn_is_present);

        // Math
        register_native(&mut m, "ABS", math::fn_abs);
        register_native(&mut m, "SIGN", math::fn_sign);
        register_native(&mut m, "CEIL", math::fn_ceil);
        register_native(&mut m, "FLOOR", math::fn_floor);
        register_native(&mut m, "TRUNC", math::fn_trunc);
        register_native(&mut m, "CANON", math::fn_canon);
        register_native(&mut m, "ROUND", math::fn_round);
        register_native(&mut m, "POWER", math::fn_power);
        register_native(&mut m, "MIN", math::fn_min);
        register_native(&mut m, "MAX", math::fn_max);
        register_native(&mut m, "ISNUM", math::fn_isnum);

        // Binary
        register_native(&mut m, "BLEN", binary::fn_blen);
        register_native(&mut m, "TO_UTF8", binary::fn_to_utf8);
        register_native(&mut m, "FROM_UTF8", binary::fn_from_utf8);
        register_native(&mut m, "TO_HEX", binary::fn_to_hex);
        register_native(&mut m, "FROM_HEX", binary::fn_from_hex);
        register_native(&mut m, "ENCODE_BASE64", binary::fn_encode_base64);
        register_native(&mut m, "DECODE_BASE64", binary::fn_decode_base64);
        register_native(&mut m, "CRC32", binary::fn_crc32);
        register_native(&mut m, "BTL", binary::fn_btl);
        register_native(&mut m, "LTB", binary::fn_ltb);

        // Text
        register_native(&mut m, "LEN", text::fn_len);
        register_native(&mut m, "LEFT", text::fn_left);
        register_native(&mut m, "RIGHT", text::fn_right);
        register_native(&mut m, "SUBSTR", text::fn_substr);
        register_native(&mut m, "FIND", text::fn_find);
        register_native(&mut m, "REPLACE", text::fn_replace);
        register_native(&mut m, "SPLIT", text::fn_split);
        register_native(&mut m, "TRIM", text::fn_trim);
        register_native(&mut m, "LTRIM", text::fn_ltrim);
        register_native(&mut m, "RTRIM", text::fn_rtrim);
        register_native(&mut m, "UPPER", text::fn_upper);
        register_native(&mut m, "LOWER", text::fn_lower);
        register_native(&mut m, "BACKWARDS", text::fn_backwards);
        register_native(&mut m, "REPEAT", text::fn_repeat);
        register_native(&mut m, "PADL", text::fn_padl);
        register_native(&mut m, "PADR", text::fn_padr);
        register_native(&mut m, "CHAR", text::fn_char);
        register_native(&mut m, "CODE", text::fn_code);

        // Regex
        register_native(&mut m, "RMATCH", regex_ops::fn_rmatch);
        register_native(&mut m, "RFIND", regex_ops::fn_rfind);
        register_native(&mut m, "RGROUPS", regex_ops::fn_rgroups);
        register_native(&mut m, "RREPLACE", regex_ops::fn_rreplace);

        // Structure & Relational
        register_native(&mut m, "COUNT", structure::fn_count);
        register_native(&mut m, "INDEXES", structure::fn_indexes);
        register_native(&mut m, "HAS", structure::fn_has);
        register_native(&mut m, "LIST", structure::fn_list);
        register_native(&mut m, "RECORD", structure::fn_record);
        register_native(&mut m, "TAKE", structure::fn_take);
        register_native(&mut m, "DROP", structure::fn_drop);
        register_native(&mut m, "SELECT_COLS", structure::fn_select_cols);
        register_native(&mut m, "DEDUPE", structure::fn_dedupe);
        register_native(&mut m, "DISTINCT", structure::fn_distinct);
        register_native(&mut m, "MAP", structure::fn_map);
        register_native(&mut m, "FILTER", structure::fn_filter);
        register_native(&mut m, "ALL", structure::fn_all);
        register_native(&mut m, "ANY", structure::fn_any);
        register_native(&mut m, "SUM", structure::fn_sum);
        register_native(&mut m, "JOIN", structure::fn_join);
        register_native(&mut m, "SORT", structure::fn_sort);
        register_native(&mut m, "SORT_DESC", structure::fn_sort_desc);
        register_native(&mut m, "SORT_BY", structure::fn_sort_by);
        register_native(&mut m, "TOP", structure::fn_top);
        register_native(&mut m, "TOP_DESC", structure::fn_top_desc);
        register_native(&mut m, "TOP_BY", structure::fn_top_by);
        register_native(&mut m, "BUCKET", structure::fn_bucket);
        register_native(&mut m, "LINK", structure::fn_link);
        register_native(&mut m, "LINK_LEFT", structure::fn_link_left);

        RwLock::new(m)
    })
}

pub fn lookup_spec(name: &str) -> Option<Arc<Spec>> {
    let key = name.to_ascii_uppercase();
    registry().read().unwrap().get(&key).cloned()
}

pub fn is_reserved(name: &str) -> bool {
    matches!(
        name,
        "TRUE" | "FALSE" | "NULL" | "AND" | "OR" | "NOT" | "XOR" | "EQL" | "IN" | "BAND" | "BOR" | "BXOR"
    )
}

pub fn function_names() -> Vec<String> {
    let reg = registry().read().unwrap();
    let mut names: Vec<String> = reg.keys().cloned().collect();
    names.sort();
    names
}

pub fn register_function<F>(name: &str, min: i64, max: i64, f: F) -> Result<(), SelError>
where
    F: Fn(&mut Args) -> Result<Value, SelError> + Send + Sync + 'static,
{
    let bytes = name.as_bytes();
    let is_ascii_letter = |b: u8| (b'a'..=b'z').contains(&b) || (b'A'..=b'Z').contains(&b);
    let is_ident = |b: u8| is_ascii_letter(b) || (b'0'..=b'9').contains(&b) || b == b'_';
    if bytes.is_empty() || !is_ascii_letter(bytes[0]) || !bytes.iter().all(|&b| is_ident(b)) {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("SEL function name must be ASCII letters, digits and _, starting with a letter: {:?}", name),
            crate::utf8::Pos::default(),
        ));
    }
    let key = name.to_ascii_uppercase();
    if is_reserved(&key) {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("{} is a reserved word", key),
            crate::utf8::Pos::default(),
        ));
    }
    if lookup_builtin(&key).is_some() {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("{} is a builtin; a host function cannot replace it", key),
            crate::utf8::Pos::default(),
        ));
    }
    if min < 0 || max < min {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("SEL function {}: arity must be whole numbers with 0 <= min <= max", key),
            crate::utf8::Pos::default(),
        ));
    }
    let spec = Spec {
        name: key.clone(),
        min: min as usize,
        max: Some(max as usize),
        lazy: false,
        binds: false,
        func: SpecFn::Host(Arc::new(f)),
    };
    registry().write().unwrap().insert(key, Arc::new(spec));
    Ok(())
}

pub fn host_arity(name: &str) -> Option<(usize, usize)> {
    let key = name.to_ascii_uppercase();
    if lookup_builtin(&key).is_some() {
        return None;
    }
    let spec = lookup_spec(&key)?;
    Some((spec.min, spec.max.unwrap_or(usize::MAX)))
}

pub fn reset_host_functions() {
    let mut reg = registry().write().unwrap();
    reg.retain(|k, _| lookup_builtin(k).is_some());
}
